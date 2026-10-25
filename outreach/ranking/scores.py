"""Stored scores: the `company_score` reads and writes (#6, Task 9).

One row per company and ranker version. Every row of a run shares one `scored_at` and is written in one
transaction. A version is as new as its newest row; the three newest are kept (`prune_versions`). A hidden company
keeps its score, and the list reads filter on the `hide` key of the stored JSON.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from outreach.ranking.score import Scored, to_columns
from outreach.store import atomic

__all__ = ["StoredScore", "write_scores", "latest_version", "previous_version", "load_scores", "ranked_list",
           "hidden_list", "prune_versions"]

# Versions, newest first. Ties on scored_at break on the version text so the order is always the same.
_VERSIONS = ("SELECT ranker_version FROM company_score GROUP BY ranker_version "
             "ORDER BY MAX(scored_at) DESC, ranker_version DESC")
_COLUMNS = "company_id, score, lanes, signals, scored_at"


@dataclass(frozen=True)
class StoredScore:
    company_id: int
    score: float
    lanes: tuple[str, ...]
    signals: dict
    scored_at: str


def _stored(r: sqlite3.Row) -> StoredScore:
    return StoredScore(r["company_id"], r["score"], tuple(json.loads(r["lanes"])), json.loads(r["signals"]),
                       r["scored_at"])


def write_scores(conn: sqlite3.Connection, scored: Iterable[Scored], version: str, scored_at: str) -> int:
    """Upserts every row under `version` with the same `scored_at`; returns the count. All rows or none: this joins
    the caller's transaction, or makes one."""
    rows = []
    for s in scored:
        signals, lanes = to_columns(s)
        rows.append((s.company_id, version, s.score, signals, lanes, scored_at))
    with atomic(conn, "rank_write_scores"):
        conn.executemany(
            "INSERT INTO company_score(company_id, ranker_version, score, signals, lanes, scored_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (company_id, ranker_version) DO UPDATE SET "
            "score = excluded.score, signals = excluded.signals, lanes = excluded.lanes, "
            "scored_at = excluded.scored_at", rows)
    return len(rows)


def latest_version(conn: sqlite3.Connection) -> str | None:
    """The `ranker_version` with the newest `scored_at`, or None with no scores."""
    row = conn.execute(_VERSIONS + " LIMIT 1").fetchone()
    return row[0] if row else None


def previous_version(conn: sqlite3.Connection, version: str) -> str | None:
    """The next older version after `version`; None when it is the oldest or is not stored."""
    versions = [r[0] for r in conn.execute(_VERSIONS)]
    if version not in versions:
        return None
    at = versions.index(version) + 1
    return versions[at] if at < len(versions) else None


def load_scores(conn: sqlite3.Connection, version: str) -> dict[int, StoredScore]:
    """Every row of `version`, hidden ones included, by company id."""
    return {r["company_id"]: _stored(r) for r in conn.execute(
        f"SELECT {_COLUMNS} FROM company_score WHERE ranker_version = ? ORDER BY company_id", (version,))}


def _list(conn: sqlite3.Connection, version: str | None, hidden: bool, limit: int | None,
          laned_only: bool = False) -> list[StoredScore]:
    version = latest_version(conn) if version is None else version
    if version is None:
        return []
    where = "json_extract(signals, '$.hide') IS NOT NULL" if hidden else "json_extract(signals, '$.hide') IS NULL"
    if laned_only:
        where += " AND json_array_length(lanes) > 0"
    return [_stored(r) for r in conn.execute(
        f"SELECT {_COLUMNS} FROM company_score WHERE ranker_version = ? AND {where} "
        "ORDER BY score DESC, company_id LIMIT ?", (version, -1 if limit is None else limit))]


def ranked_list(conn: sqlite3.Connection, version: str | None = None, *, limit: int | None = None,
                laned_only: bool = False) -> list[StoredScore]:
    """Rows with no hide, highest score first, then the lower company id; `laned_only` also needs a lane. The
    surface #15 and #12 read. `version` None means the latest."""
    return _list(conn, version, False, limit, laned_only)


def hidden_list(conn: sqlite3.Connection, version: str | None = None) -> list[StoredScore]:
    """Rows with a hide (final or provisional), in the same order, so a view can say why."""
    return _list(conn, version, True, None)


def prune_versions(conn: sqlite3.Connection, keep: int) -> int:
    """Deletes the rows of every version but the `keep` newest; returns the rows deleted."""
    if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
        raise ValueError(f"keep must be a whole number of at least 1, got {keep!r}")
    with atomic(conn, "rank_prune_versions"):
        return conn.execute(
            f"DELETE FROM company_score WHERE ranker_version NOT IN ({_VERSIONS} LIMIT ?)", (keep,)).rowcount
