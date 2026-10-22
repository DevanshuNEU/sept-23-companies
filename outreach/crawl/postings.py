"""What one `ok` read changes in a board's postings, and the batched writes that apply it.

`plan_diff` is pure: it compares the stored postings of the board with the listed jobs. `write_postings` and
`close_postings` apply a plan in batches, one transaction per batch, never one across a request. A posting is never
deleted: a gone one gets `closed_at` and `close_reason`, and if it is listed again later it reopens, unless it was
closed for a reason the board cannot overrule (by hand, by its page, or on import).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from outreach.crawl.base import JobRef
from outreach.crawl.boardstate import BoardRow
from outreach.store import atomic

__all__ = ["source_key", "native_id_of", "Existing", "load_existing", "DiffPlan", "plan_diff", "write_postings",
           "close_postings", "REOPENABLE"]

# Closes the crawler made, which a later listing undoes. Any other close (hand, page_closed, imported_closed) stands.
REOPENABLE = ("gone_from_board", "board_empty_twice")
_CLOSE_REASONS = ("gone_from_board", "board_empty_twice", "page_closed", "hand", "imported_closed")


def source_key(ats: str, token: str, native_id: str) -> str:
    return f"{ats}:{token}:{native_id}"


def native_id_of(key: str, ats: str, token: str) -> str:
    prefix = f"{ats}:{token}:"
    if not key.startswith(prefix):
        raise ValueError(f"source key {key!r} is not on board {ats}:{token}")
    return key[len(prefix):]


@dataclass(frozen=True)
class Existing:
    posting_id: int
    title: str
    location: str
    country: str | None
    url: str
    closed_at: str | None
    close_reason: str | None


def load_existing(conn: sqlite3.Connection, board_id: int) -> dict[str, Existing]:
    """Every posting of the board, open and closed, by `source_key`. Opens no transaction."""
    rows = conn.execute(
        "SELECT source_key, posting_id, title, location, country, url, closed_at, close_reason "
        "FROM posting WHERE board_id = ?", (board_id,))
    return {r[0]: Existing(*r[1:]) for r in rows}


@dataclass(frozen=True)
class DiffPlan:
    new: tuple[JobRef, ...]
    changed: tuple[tuple[int, JobRef], ...]  # (posting_id, the job as listed now)
    present: tuple[int, ...]  # listed and open, or listed and reopening: last_seen_at moves
    reopen: tuple[int, ...]
    gone: tuple[int, ...]  # open and not listed


def plan_diff(existing: dict[str, Existing], jobs: Sequence[JobRef], ats: str, token: str) -> DiffPlan:
    """The changes one `ok` read makes. A job id listed twice counts once (the first listing)."""
    new: list[JobRef] = []
    changed: list[tuple[int, JobRef]] = []
    present: list[int] = []
    reopen: list[int] = []
    listed: set[str] = set()
    for job in jobs:
        key = source_key(ats, token, job.native_id)
        if key in listed:
            continue
        listed.add(key)
        e = existing.get(key)
        if e is None:
            new.append(job)
            continue
        if e.closed_at is not None:
            if e.close_reason not in REOPENABLE:
                continue  # closed by hand, by its page or on import: the board listing it does not overrule that
            reopen.append(e.posting_id)
        present.append(e.posting_id)
        if (e.title, e.location, e.country, e.url) != (job.title, job.location, job.country, job.url):
            changed.append((e.posting_id, job))
    gone = sorted(e.posting_id for key, e in existing.items() if e.closed_at is None and key not in listed)
    return DiffPlan(tuple(new), tuple(changed), tuple(present), tuple(reopen), tuple(gone))


def write_postings(conn: sqlite3.Connection, board: BoardRow, plan: DiffPlan, now: str, *,
                   batch_rows: int) -> dict[str, int]:
    """Apply the new, changed, present and reopen parts of `plan`. Returns native_id to posting_id for every job
    the plan names. One transaction per `batch_rows` postings (each posting is one row written); a re-run of the
    same plan inserts nothing twice (`ON CONFLICT(source_key) DO NOTHING`, then the ids are selected).

    `posted_on` is set only where it is NULL. `last_seen_at` never moves back and so never falls before
    `first_seen_at`. A reopen applies only while the posting is still closed for a reason the board can undo, so a
    hand close made between the diff and this write stands.
    """
    if batch_rows < 1:
        raise ValueError("batch_rows must be at least 1")
    changed = dict(plan.changed)
    present, reopen = set(plan.present), set(plan.reopen)
    units: list[tuple[str, object]] = [("new", job) for job in plan.new]
    seen: set[int] = set()
    for pid in (*plan.present, *plan.reopen, *changed):
        if pid not in seen:
            seen.add(pid)
            units.append(("old", pid))
    ids: dict[str, int] = {}
    for start in range(0, len(units), batch_rows):
        batch = units[start:start + batch_rows]
        with atomic(conn, "write_postings"):
            new_jobs = [u for kind, u in batch if kind == "new"]
            if new_jobs:
                conn.executemany(
                    "INSERT INTO posting(company_id, board_id, source_key, title, location, country, url, posted_on, "
                    "first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(source_key) DO NOTHING",
                    [(board.company_id, board.board_id, source_key(board.ats, board.token, j.native_id), j.title,
                      j.location, j.country, j.url, j.posted_on, now, now) for j in new_jobs])
                keys = [source_key(board.ats, board.token, j.native_id) for j in new_jobs]
                for key, pid in conn.execute(
                        f"SELECT source_key, posting_id FROM posting WHERE source_key IN ({_marks(keys)})", keys):
                    ids[native_id_of(key, board.ats, board.token)] = pid
            old = [u for kind, u in batch if kind == "old"]
            for pid in old:
                job = changed.get(pid)
                if job is not None:
                    conn.execute(
                        "UPDATE posting SET title = ?, location = ?, country = ?, url = ?, "
                        "posted_on = coalesce(posted_on, ?) WHERE posting_id = ?",
                        (job.title, job.location, job.country, job.url, job.posted_on, pid))
                if pid in present or pid in reopen:
                    conn.execute("UPDATE posting SET last_seen_at = max(last_seen_at, first_seen_at, ?) "
                                 "WHERE posting_id = ?", (now, pid))
                if pid in reopen:
                    conn.execute(
                        "UPDATE posting SET closed_at = NULL, close_reason = NULL WHERE posting_id = ? "
                        f"AND close_reason IN ({_marks(REOPENABLE)})", (pid, *REOPENABLE))
            if old:
                for key, pid in conn.execute(
                        f"SELECT source_key, posting_id FROM posting WHERE posting_id IN ({_marks(old)})", old):
                    ids[native_id_of(key, board.ats, board.token)] = pid
    return ids


def close_postings(conn: sqlite3.Connection, posting_ids: Sequence[int], reason: str, now: str, *,
                   batch_rows: int) -> int:
    """Close the postings that are still open, one transaction per `batch_rows`. Returns how many closed. A posting
    already closed keeps its first close. Nothing is ever deleted."""
    if reason not in _CLOSE_REASONS:
        raise ValueError(f"close reason {reason!r} is not one of {_CLOSE_REASONS}")
    if batch_rows < 1:
        raise ValueError("batch_rows must be at least 1")
    ids = list(dict.fromkeys(posting_ids))
    closed = 0
    for start in range(0, len(ids), batch_rows):
        batch = ids[start:start + batch_rows]
        with atomic(conn, "close_postings"):
            closed += conn.execute(
                f"UPDATE posting SET closed_at = ?, close_reason = ? WHERE posting_id IN ({_marks(batch)}) "
                "AND closed_at IS NULL", (now, reason, *batch)).rowcount
    return closed


def _marks(values: Sequence[object]) -> str:
    return ", ".join("?" * len(values))
