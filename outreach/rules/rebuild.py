"""Where stored decisions come from: rebuild() re-decides every open posting under the current ruleset, and
decide_and_store() decides one posting for the crawler. record_evidence() is the one writer of rule-made evidence.

The mail sender and the API share this SQLite file with a 5 second busy timeout, so no step here holds the write lock
for longer than one batch. A rebuild never deletes a decision before it writes the new one (the old Job Radar
rebuild_decisions.py did, and lost data); it upserts under the new version, switches the current ruleset in one short
transaction, then prunes versions nobody reads. The crawler must not run at the same time: #21's job runs rebuild
first, then crawls.

`python -m outreach.rules.rebuild` runs a rebuild on the store at OUTREACH_DB (the live path only in the live
checkout) and prints the flip report.
"""
import argparse
import contextlib
import sqlite3
from dataclasses import dataclass

from outreach.store import append_event, connect, finish_job, start_job, transaction, utcnow

from .config import Config, load_config
from .decide import Decision, PostingInput, decide, passes
from .facts import EvidenceDraft
from .version import ruleset_version

__all__ = ["RulesetNotCurrent", "Flip", "FlipReport", "BATCH_SIZE", "rebuild", "decide_and_store",
           "record_evidence", "main"]

BATCH_SIZE = 500

_COLUMNS = ("lane", "level_tier", "level_ok", "level_basis", "years_asked", "years_quote", "us", "sponsorship",
            "sponsorship_quote", "citizenship", "citizenship_quote", "eligibility", "eligibility_reason",
            "eligibility_quote", "start_2027")
_UPSERT = (
    "INSERT INTO decision(posting_id, ruleset_version, decided_at, " + ", ".join(_COLUMNS) + ") VALUES (?, ?, ?, "
    + ", ".join("?" for _ in _COLUMNS) + ") ON CONFLICT(posting_id, ruleset_version) DO UPDATE SET decided_at = "
    "excluded.decided_at, " + ", ".join(f"{c} = excluded.{c}" for c in _COLUMNS)
)
# The fields passes() reads: what a shown-to-hidden flip is explained by.
_HIDING_FIELDS = ("lane", "us", "sponsorship", "citizenship", "eligibility")


class RulesetNotCurrent(Exception):
    """The store's current ruleset is not the one this code hashes to."""


@dataclass(frozen=True)
class Flip:
    posting_id: int
    company: str
    title: str
    change: str  # for example "sponsorship silent -> no"


@dataclass(frozen=True)
class FlipReport:
    version: str
    previous: str | None
    decided: int
    to_hidden: list[Flip]
    to_shown: list[Flip]
    level_lost: list[Flip]

    def lines(self) -> list[str]:
        out = [f"Ruleset {self.version} (previous {self.previous or 'none'}): {self.decided} postings decided."]
        for heading, flips in (("Postings that went from shown to hidden", self.to_hidden),
                               ("Postings that went from hidden to shown", self.to_shown),
                               ("Postings that lost the level check", self.level_lost)):
            out.append(f"{heading} ({len(flips)}):")
            out.extend(f"  #{f.posting_id} {f.company}: {f.title} ({f.change})" for f in flips)
            if not flips:
                out.append("None.")
        return out


def _stored(row: sqlite3.Row) -> Decision:
    """A Decision from a decision-table row."""
    values = {c: row[c] for c in _COLUMNS}
    for flag in ("level_ok", "citizenship", "start_2027"):
        values[flag] = bool(values[flag])
    return Decision(**values)


def _word(value) -> str:
    return {True: "yes", False: "no", None: "none"}.get(value, str(value))


def _change(old: Decision, new: Decision, fields) -> str:
    return "; ".join(f"{f} {_word(getattr(old, f))} -> {_word(getattr(new, f))}"
                     for f in fields if getattr(old, f) != getattr(new, f))


def _upsert(conn: sqlite3.Connection, posting_id: int, version: str, d: Decision) -> None:
    row = d.as_row()
    conn.execute(_UPSERT, (posting_id, version, utcnow(), *(row[c] for c in _COLUMNS)))


def _posting_input(row: sqlite3.Row) -> PostingInput:
    return PostingInput(row["title"], row["body"], row["location"], row["country"])


def _decide_batch(conn, ids, version, cfg, report: dict) -> None:
    """Decide one batch inside the caller's transaction and add its flips to `report`."""
    for posting_id in ids:
        posting = conn.execute(
            "SELECT p.title, p.body, p.location, p.country, p.closed_at, c.name FROM posting p "
            "JOIN company c ON c.company_id = p.company_id WHERE p.posting_id = ?", (posting_id,)).fetchone()
        new = decide(_posting_input(posting), cfg)
        old_row = None
        if posting["closed_at"] is None:
            old_row = conn.execute("SELECT * FROM current_decision WHERE posting_id = ?", (posting_id,)).fetchone()
        if old_row is not None:
            old = _stored(old_row)
            was, now = passes(old), passes(new)
            if was and not now:
                report["to_hidden"].append(
                    Flip(posting_id, posting["name"], posting["title"], _change(old, new, _HIDING_FIELDS)))
            elif now and not was:
                report["to_shown"].append(
                    Flip(posting_id, posting["name"], posting["title"], _change(old, new, _HIDING_FIELDS)))
            if old.level_ok and not new.level_ok:
                report["level_lost"].append(Flip(
                    posting_id, posting["name"], posting["title"],
                    f"level {old.level_tier} -> {new.level_tier}: {new.level_basis}"))
        _upsert(conn, posting_id, version, new)


def _prune(conn: sqlite3.Connection, keep: list[str], batch_size: int) -> int:
    """Delete decision rows under other versions, for postings that still have a row under a kept one."""
    marks = ", ".join("?" for _ in keep)
    pick = (f"SELECT d.rowid FROM decision d WHERE d.rowid > ? AND d.ruleset_version NOT IN ({marks}) AND EXISTS "
            f"(SELECT 1 FROM decision k WHERE k.posting_id = d.posting_id AND k.ruleset_version IN ({marks})) "
            "ORDER BY d.rowid LIMIT ?")
    pruned, last = 0, 0
    while True:
        with transaction(conn):
            rowids = [r[0] for r in conn.execute(pick, (last, *keep, *keep, batch_size))]
            if rowids:
                conn.execute(f"DELETE FROM decision WHERE rowid IN ({', '.join('?' for _ in rowids)})", rowids)
        if not rowids:
            return pruned
        pruned += len(rowids)
        last = rowids[-1]


def rebuild(conn: sqlite3.Connection, *, cfg: Config | None = None, version: str | None = None,
            batch_size: int = BATCH_SIZE, progress=None) -> FlipReport:
    """Re-decide every open posting (and any with no decision) under `version`, make it current, prune old rows.

    `cfg` and `version` default to the shipped config and the code's ruleset version; tests override them. Returns
    the flips against the ruleset that was current when the run started. A failed run leaves that ruleset current;
    rows already written under the new version are overwritten by the next run.
    """
    cfg = cfg if cfg is not None else load_config()
    version = version if version is not None else ruleset_version()
    with transaction(conn):
        row = conn.execute("SELECT ruleset_version FROM ruleset WHERE is_current = 1").fetchone()
        previous = row[0] if row else None
        conn.execute(
            "INSERT INTO ruleset(ruleset_version, config_toml, created_at, is_current) VALUES (?, ?, ?, 0) "
            "ON CONFLICT(ruleset_version) DO NOTHING", (version, cfg.raw.decode("utf-8"), utcnow()))
        run_id = start_job(conn, "rebuild", version)
    flips = {"to_hidden": [], "to_shown": [], "level_lost": []}
    try:
        ids = [r[0] for r in conn.execute(
            "SELECT posting_id FROM posting p WHERE closed_at IS NULL OR NOT EXISTS "
            "(SELECT 1 FROM decision d WHERE d.posting_id = p.posting_id) ORDER BY posting_id")]
        for start in range(0, len(ids), batch_size):
            with transaction(conn):
                _decide_batch(conn, ids[start:start + batch_size], version, cfg, flips)
            if progress is not None:
                progress(min(start + batch_size, len(ids)))
        with transaction(conn):
            conn.execute("UPDATE ruleset SET is_current = 0 WHERE is_current = 1")
            conn.execute("UPDATE ruleset SET is_current = 1 WHERE ruleset_version = ?", (version,))
        keep = [version] + ([previous] if previous and previous != version else [])
        pruned = _prune(conn, keep, batch_size)
        summary = {"decided": len(ids), "previous": previous, "pruned": pruned,
                   **{name: [f.posting_id for f in found] for name, found in flips.items()}}
        finish_job(conn, run_id, "ok", summary)
    except BaseException as err:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        finish_job(conn, run_id, "failed", error=f"{type(err).__name__}: {err}")
        raise
    return FlipReport(version, previous, len(ids), **flips)


@contextlib.contextmanager
def _atomic(conn: sqlite3.Connection):
    """A write unit: the caller's transaction (under a savepoint, so a failure undoes only this unit) or its own."""
    if not conn.in_transaction:
        with transaction(conn):
            yield
        return
    conn.execute("SAVEPOINT rules_unit")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO rules_unit")
        conn.execute("RELEASE rules_unit")
        raise
    conn.execute("RELEASE rules_unit")


def decide_and_store(conn: sqlite3.Connection, posting_id: int, *, cfg: Config | None = None) -> Decision:
    """Decide one posting and upsert its row under the store's current ruleset. What the crawler calls per posting.

    Raises RulesetNotCurrent when the store's current version is not this code's. Joins the caller's transaction
    when there is one.
    """
    with _atomic(conn):
        row = conn.execute("SELECT ruleset_version FROM ruleset WHERE is_current = 1").fetchone()
        if row is None or row[0] != ruleset_version():
            raise RulesetNotCurrent("ruleset changed; run: uv run python -m outreach.rules.rebuild")
        posting = conn.execute("SELECT title, body, location, country FROM posting WHERE posting_id = ?",
                               (posting_id,)).fetchone()
        if posting is None:
            raise LookupError(f"no posting {posting_id}")
        decision = decide(_posting_input(posting), cfg)
        _upsert(conn, posting_id, row[0], decision)
    return decision


def record_evidence(conn: sqlite3.Connection, company_id: int, drafts: list[EvidenceDraft], *,
                    posting_id: int | None = None) -> int:
    """Insert rule-made evidence (never locked) and return how many rows were new.

    A repeat of an earlier unlocked row adds nothing (the `evidence_once` index). A hand draft is refused: hand values
    come from #16 edits and #17's locked import, which write `locked = 1` themselves. A draft the table rejects
    raises sqlite3.IntegrityError and nothing from this call is kept.
    """
    if any(d.source_kind == "hand" for d in drafts):
        raise ValueError("hand evidence is written locked by edits and the import, not by the rules")
    with _atomic(conn):
        added = []
        for d in drafts:
            cur = conn.execute(
                "INSERT INTO evidence(company_id, fact, value, num_low, num_high, confidence, locked, source_kind, "
                "source_ref, quote, posting_id, observed_on, recorded_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                (company_id, d.fact, d.value, d.num_low, d.num_high, d.confidence, d.source_kind, d.source_ref,
                 d.quote, posting_id, d.observed_on, utcnow()))
            if cur.rowcount:
                added.append(d.fact)
        if added:
            append_event(conn, entity="evidence", kind="recorded", actor="rules", company_id=company_id,
                         detail={"added": len(added), "facts": added, "posting_id": posting_id})
    return len(added)


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(
        prog="python -m outreach.rules.rebuild",
        description="Re-decide every open posting under the current ruleset and print what flipped.",
    ).parse_args(argv)
    conn = connect()
    try:
        print("\n".join(rebuild(conn).lines()))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
