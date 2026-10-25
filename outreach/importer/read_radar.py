"""The radar.db reader: Job Radar's database, every table accounted for.

How it reads a live file: it opens radar.db with `mode=ro` (`open_radar`) and reads inside one deferred read
transaction, which is closed when the generator finishes or is closed. radar.db is in WAL mode, so that read never
blocks Job Radar's writers (a reader only keeps the WAL from being checkpointed past its snapshot). Nothing is
copied, and nothing is written: the connection cannot write, and the main file's digest is unchanged by a run (a test
holds the reader open part-way and writes to the file from another connection to prove the writer is not blocked).
Postings are streamed one by one from a cursor, so the version bodies (most of the 700 MB) are never all in memory.
"""
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from outreach.importer.records import SourceError, SourceRecord, open_radar

# One import record per row.
RADAR_ROW_TABLES = ("board", "posting", "app_event")

# Every other table in Job Radar's schema (store.py, migrations 1 to 5) and why it is one count, not rows.
RADAR_TABLE_REASONS: dict[str, str] = {
    "poll_run": "run_history",
    "board_fetch": "run_history",
    "posting_version": "older_version",
    "decision": "derived_decision",
    "outbox": "outbox_delivery",
    "label": "label_rubric",
    "body_attempt": "run_history",
    "meta": "schema_meta",
    "schema_migration": "schema_meta",
}


@dataclass(frozen=True)
class RadarRecord(SourceRecord):
    """A radar record. `body` is a posting's newest body text, kept out of `raw` (and so out of `import_record`)."""
    body: str | None = None


def radar_body(record: SourceRecord) -> str | None:
    """The body text read beside a posting record, or None (not a posting, no version, or a version with no body)."""
    return getattr(record, "body", None)


def _row(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def _check_schema(conn: sqlite3.Connection, path: Path) -> list[str]:
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name")]
    views = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'view'")}
    unknown = [t for t in tables if t not in RADAR_ROW_TABLES and t not in RADAR_TABLE_REASONS]
    if unknown:
        raise SourceError(
            f"radar_db: {path} has a table the importer has not decided about: {', '.join(unknown)}. "
            f"Add it to RADAR_ROW_TABLES or RADAR_TABLE_REASONS; it is never dropped silently")
    missing = [t for t in RADAR_ROW_TABLES if t not in tables] + (["posting_live (view)"] if "posting_live" not in views else [])
    if missing:
        raise SourceError(f"radar_db: {path} has no {', '.join(missing)}; it is not a Job Radar database")
    return tables


def read_radar(path: Path) -> Iterator[SourceRecord]:
    """Records, in order: `board:<id>`, `posting:<id>`, `app_event:<id>`, then `table:<name>` for every other table.

    A posting's raw is its row plus `closed` (from `posting_live`; None for a merged posting, which that view leaves
    out) and `latest`, the newest `posting_version` by `observed_at` then `version_id` as `{version_id, content_hash,
    observed_at}` (None with no version). The body text of that version travels beside the record: `radar_body`.
    `table:posting_version` counts the versions that are not read as a posting's latest.
    """
    conn = open_radar(path)
    try:
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN")
            tables = _check_schema(conn, Path(path))
            for row in conn.execute("SELECT * FROM board ORDER BY board_id").fetchall():
                yield SourceRecord("radar_db", f"board:{row['board_id']}", _row(row))
            # posting_live is a view SQLite rebuilds on every query that touches it, so it is read once, here: one
            # small int per posting. A posting the view leaves out (a merged one) has no entry, so its closed is None.
            closed_by_id = {pid: closed for pid, closed in conn.execute("SELECT posting_id, closed FROM posting_live")}
            for row in conn.execute("SELECT * FROM posting ORDER BY posting_id"):
                posting_id = row["posting_id"]
                latest = conn.execute(
                    "SELECT version_id, content_hash, observed_at, body_text FROM posting_version "
                    "WHERE posting_id = ? ORDER BY observed_at DESC, version_id DESC LIMIT 1", (posting_id,)).fetchone()
                raw = _row(row)
                raw["closed"] = closed_by_id.get(posting_id)
                raw["latest"] = None if latest is None else {
                    "version_id": latest["version_id"], "content_hash": latest["content_hash"],
                    "observed_at": latest["observed_at"]}
                yield RadarRecord("radar_db", f"posting:{posting_id}", raw,
                                  body=None if latest is None else latest["body_text"])
            event_ids = [r[0] for r in conn.execute("SELECT event_id FROM app_event ORDER BY event_id")]
            for event_id in event_ids:
                row = conn.execute("SELECT * FROM app_event WHERE event_id = ?", (event_id,)).fetchone()
                yield SourceRecord("radar_db", f"app_event:{event_id}", _row(row))
            for name in tables:
                if name in RADAR_ROW_TABLES:
                    continue
                if name == "posting_version":
                    rows = conn.execute(
                        "SELECT count(*) - count(DISTINCT posting_id) FROM posting_version").fetchone()[0]
                else:
                    rows = conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                yield SourceRecord("radar_db", f"table:{name}", {"rows": rows, "reason": RADAR_TABLE_REASONS[name]})
        except sqlite3.Error as e:
            raise SourceError(f"radar_db: cannot read {path}: {e}") from e
    finally:
        conn.close()
