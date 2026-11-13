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

__all__ = ["tier_wait_s", "head_read_due", "next_interval_s", "error_backoff_s", "add_seconds", "last_new_age_s", "low_yield", "plan_company_ids", "due_boards",
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


def low_yield(conn: sqlite3.Connection, board_id: int, now: str, cfg: CrawlConfig) -> bool:
    """True for a big board that yields little (#152): at least `big_board.min_open` open postings, fewer than
    `big_board.max_share` of them US engineering (lane `engineering`, `us` not `no`, in the current decision), and none
    of those at his level or with no stated level first seen in the last `big_board.quiet_days` days. One query over the
    board's open postings through `posting_board_open`; the decision is joined by its key, as `ingest._undecided` does,
    never through the `current_decision` view. A posting with no current decision is open but not US engineering."""
    big = cfg.big_board
    fresh_since = add_seconds(now, -big.quiet_days * 86400)
    us_eng = "d.lane = 'engineering' AND d.us != 'no'"
    row = conn.execute(
        f"SELECT COUNT(*), COALESCE(SUM({us_eng}), 0), "
        f"COALESCE(SUM({us_eng} AND (d.level_ok = 1 OR d.level_tier IN ('not_stated', 'open')) "
        "AND p.first_seen_at >= :fresh), 0) "
        "FROM posting p LEFT JOIN decision d ON d.posting_id = p.posting_id "
        "AND d.ruleset_version = (SELECT ruleset_version FROM ruleset WHERE is_current = 1) "
        "WHERE p.board_id = :board AND p.closed_at IS NULL", {"board": board_id, "fresh": fresh_since}).fetchone()
    open_n, us_eng_n, fresh_n = row
    return open_n >= big.min_open and us_eng_n / open_n < big.max_share and fresh_n == 0


# A job at his level first seen on the board since :since (#251): engineering, a software role, not outside the US, and
# fit or level not stated, under the current ruleset. Read through posting_board_open, by board.
_HOT_SQL = ("SELECT 1 FROM posting p JOIN decision d ON d.posting_id = p.posting_id "
            "AND d.ruleset_version = (SELECT ruleset_version FROM ruleset WHERE is_current = 1) "
            "WHERE p.board_id = :board AND p.first_seen_at >= :since AND d.lane = 'engineering' "
            "AND COALESCE(d.role, 'software') = 'software' AND d.us <> 'no' "
            "AND COALESCE(d.level_state, CASE WHEN d.level_ok = 1 THEN 'fit' "
            "WHEN d.level_tier IN ('not_stated', 'open') THEN 'uncertain' ELSE 'reject' END) IN ('fit', 'uncertain') "
            "LIMIT 1")


def head_read_due(board: BoardRow, now: str, cfg: CrawlConfig) -> bool:
    """True when the board is read with one newest-first request instead of a full read (#251 part 2): its ATS has a
    head read, its last read was no error, and its last full read is younger than its normal interval."""
    if board.ats not in cfg.tiers.head_ats or board.last_full_read_at is None or board.last_read == "error":
        return False
    return add_seconds(board.last_full_read_at, board.change_interval_s or cfg.floor_s) > now


def tier_wait_s(conn: sqlite3.Connection, board: BoardRow, now: str, cfg: CrawlConfig) -> int | None:
    """Seconds a board waits after a good read under the speed tiers (#251), or None when its ATS is not tiered.
    Hot (`cfg.tiers.hot_s`): a job at his level first seen there in the last `hot_days`. Dead (`dead_s`): nothing new
    for `dead_days`. Warm (`warm_s`): the rest."""
    t = cfg.tiers
    if board.ats not in t.ats and board.ats not in t.head_ats:
        return None
    since = add_seconds(now, -t.hot_days * 86400)
    if conn.execute(_HOT_SQL, {"board": board.board_id, "since": since}).fetchone():
        return t.hot_s
    if board.ats in t.head_ats:
        return None  # a head-read board that is not hot keeps the floor-and-ceiling rhythm
    if last_new_age_s(conn, board, now) > t.dead_days * 86400:
        return t.dead_s
    return t.warm_s


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
