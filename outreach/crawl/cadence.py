"""When a board is read next, and which boards a tick reads (#21, Task 10).

A board's interval grows with the time since its last new posting and shrinks when it changes; an erroring board backs
off 6, 12, 24 hours and so on up to the ceiling. `due_boards` picks the boards a tick reads, in a fixed order, so a Mac
that slept for days reads today's plan first and leaves the rest due.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
from collections.abc import Collection
from datetime import datetime, timedelta, timezone

from outreach.crawl.boardstate import BoardRow
from outreach.crawl.config import CrawlConfig
from outreach.store.timefmt import ISO_FMT

__all__ = ["next_interval_s", "error_backoff_s", "add_seconds", "last_new_age_s", "plan_company_ids", "due_boards",
           "switched_off_boards"]

# Ruling 5: an erroring board waits 6, 12, then 24 hours and stays at 24. The 7-day ceiling is for quiet boards only.
ERROR_BACKOFF_CAP_S = 86400
_MAX_DOUBLINGS = 40  # 2 ** 40 floors is far past any cap; this only keeps a huge streak from building a huge int


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, ISO_FMT).replace(tzinfo=timezone.utc)


def next_interval_s(age_since_new_s: float, prev_interval_s: int | None, changed: bool, cfg: CrawlConfig) -> int:
    """Seconds until the next read: the time since the last new posting divided by `age_divisor`, kept between the
    floor and the ceiling. On a change with a previous interval, at most half of it (never under the floor)."""
    base = int(min(max(age_since_new_s / cfg.age_divisor, cfg.floor_s), cfg.ceiling_s))
    if changed and prev_interval_s is not None:
        return max(cfg.floor_s, min(base, prev_interval_s // 2))
    return base


def error_backoff_s(error_streak: int, cfg: CrawlConfig) -> int:
    """Seconds to wait after the `error_streak`-th error in a row: the floor, doubled each time, capped at 24 hours (and
    never above the ceiling)."""
    doublings = min(max(error_streak, 1) - 1, _MAX_DOUBLINGS)
    return min(cfg.ceiling_s, max(cfg.floor_s, min(ERROR_BACKOFF_CAP_S, cfg.floor_s * 2 ** doublings)))


def add_seconds(ts: str, seconds: int) -> str:
    """`ts` (canonical UTC) moved by `seconds`, canonical UTC."""
    return (_parse(ts) + timedelta(seconds=seconds)).strftime(ISO_FMT)


def last_new_age_s(conn: sqlite3.Connection, board: BoardRow, now: str) -> float:
    """Seconds from the newest `first_seen_at` among the board's postings (open or closed) to `now`; from the board's
    `created_at` when it has none. Never negative."""
    row = conn.execute("SELECT max(first_seen_at) FROM posting WHERE board_id = ?", (board.board_id,)).fetchone()
    since = row[0] or board.created_at
    return max(0.0, (_parse(now) - _parse(since)).total_seconds())


def plan_company_ids(conn: sqlite3.Connection, today: str) -> frozenset[int]:
    """Companies on today's list with a `proposed` or `picked` entry. The one place that reads "today's plan": #15's
    people-plan rework changes only this function."""
    rows = conn.execute("SELECT company_id FROM daily_list_entry WHERE list_date = ? AND state IN ('proposed', 'picked')",
                        (today,))
    return frozenset(r[0] for r in rows)


def due_boards(conn: sqlite3.Connection, now: str, *, supported: Collection[str], plan_ids: Collection[int],
               cfg: CrawlConfig, limit: int, only_company_ids: Collection[int] | None = None,
               force: bool = False) -> list[BoardRow]:
    """The boards a tick reads, at most `limit`.

    Candidates: active boards of live companies (status not `closed` or `removed`, not merged) whose `ats` is in
    `supported`, and in `only_company_ids` when that is given. A board is due when its `next_read_at` is NULL or has
    passed. A board of a plan company is also due once its last read is `floor_s` old or older, unless that read was an
    `error` (an erroring board keeps its backoff). `force` ignores `next_read_at` but keeps the floor and the error
    backoff. Order: plan companies, then never-read boards (`last_read` NULL, so a never-read board the tick deferred
    keeps its place), then `next_read_at` ascending, then board id.
    """
    if limit < 1 or not supported or (only_company_ids is not None and not only_company_ids):
        return []
    floor_cutoff = add_seconds(now, -cfg.floor_s)
    columns = ", ".join(f"b.{f.name}" for f in dataclasses.fields(BoardRow))
    # The id lists travel as JSON text, so a long plan never meets SQLite's variable limit.
    plan_json = json.dumps(sorted(set(plan_ids)))
    due = "(b.next_read_at IS NULL OR b.next_read_at <= :now)"
    floor_ok = "(b.last_read_at IS NULL OR b.last_read_at <= :cutoff)"
    if force:
        eligible = f"{floor_ok} AND ({due} OR b.last_read IS NOT 'error')"
    else:
        eligible = f"({due} OR (b.company_id IN (SELECT value FROM json_each(:plan)) AND {floor_ok} " \
                   "AND b.last_read IS NOT 'error'))"
    only = ""
    params: dict[str, object] = {"now": now, "cutoff": floor_cutoff, "plan": plan_json, "limit": limit,
                                 "supported": json.dumps(sorted(set(supported)))}
    if only_company_ids is not None:
        only = "AND b.company_id IN (SELECT value FROM json_each(:only)) "
        params["only"] = json.dumps(sorted(set(only_company_ids)))
    rows = conn.execute(
        f"SELECT {columns} FROM board b JOIN company c ON c.company_id = b.company_id "
        "WHERE b.active = 1 AND c.status NOT IN ('closed', 'removed') AND c.merged_into IS NULL "
        f"AND b.ats IN (SELECT value FROM json_each(:supported)) {only}AND {eligible} "
        "ORDER BY b.company_id IN (SELECT value FROM json_each(:plan)) DESC, (b.last_read IS NULL) DESC, "
        "b.next_read_at, b.board_id LIMIT :limit", params).fetchall()
    return [BoardRow(*r) for r in rows]


def switched_off_boards(conn: sqlite3.Connection, off: Collection[str], *,
                        only_company_ids: Collection[int] | None = None) -> list[BoardRow]:
    """The boards `due_boards` leaves out because their ATS's reader is switched off (#46): active boards of live
    companies whose `ats` is in `off` (and in `only_company_ids` when given), due or not, by board id. Read only."""
    if not off or (only_company_ids is not None and not only_company_ids):
        return []
    columns = ", ".join(f"b.{f.name}" for f in dataclasses.fields(BoardRow))
    params: dict[str, object] = {"off": json.dumps(sorted(set(off)))}
    only = ""
    if only_company_ids is not None:
        only = "AND b.company_id IN (SELECT value FROM json_each(:only)) "
        params["only"] = json.dumps(sorted(set(only_company_ids)))
    rows = conn.execute(
        f"SELECT {columns} FROM board b JOIN company c ON c.company_id = b.company_id "
        "WHERE b.active = 1 AND c.status NOT IN ('closed', 'removed') AND c.merged_into IS NULL "
        f"AND b.ats IN (SELECT value FROM json_each(:off)) {only}ORDER BY b.board_id", params).fetchall()
    return [BoardRow(*r) for r in rows]
