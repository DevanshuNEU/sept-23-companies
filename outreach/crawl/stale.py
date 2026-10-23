"""Which open postings are stale, derived at read time (issue #21, Task 16a; ruling of 2026-10-22).

A posting is stale when it is open and either its posted date is older than `stale_after_months`, or its board's
latest successful read did not list it. Both come from columns that already exist, so nothing is stored and the age
rule moves with the calendar (`today` is a parameter).

Why `board.empty_streak >= 1` is the whole "not on the board" test: after an `ok` read the crawler closes every
posting the board no longer lists (`gone_from_board`), after the second empty read it closes them all
(`board_empty_twice`), and `unchanged` means the same ids. So the one state in which an open posting is absent from
the latest successful read is a first `empty` read. `last_seen_at` is touched only by an `ok` read that lists the
posting, so its date is the last day an `ok` read listed it; `unchanged` reads touch nothing, so the board may have
listed the posting on later reads. That is why the text says "last listed <date> or later". An `error` read leaves
`empty_streak` and `last_seen_at` alone, so an error never makes a posting stale and an error after an empty keeps it
stale.

This module only reads. Stale is never a hide and never a close, and it imports no adapter or HTTP code. Ranking,
the daily plan and the web app import `load_stale` and `Stale` from here and never re-derive the rule.
"""
from __future__ import annotations

import calendar
import json
import sqlite3
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date

from outreach.crawl.config import load_crawl_config

__all__ = ["STALE_OLD", "STALE_NOT_ON_BOARD", "Stale", "months_before", "stale_reason", "load_stale"]

STALE_OLD = "old"
STALE_NOT_ON_BOARD = "not_on_board"


@dataclass(frozen=True)
class Stale:
    kinds: tuple[str, ...]  # "old", "not_on_board" or both, in that order
    reason: str             # each kind's text, joined by "; "


def months_before(day: date, months: int) -> date:
    """`day` moved back by whole calendar months, the day clamped to the month's end (03-31 minus 1 is 02-28)."""
    index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def stale_reason(*, posted_on: str | None, last_seen_at: str, closed_at: str | None, board_empty_streak: int | None,
                 today: date, max_age_months: int) -> Stale | None:
    """Why an open posting is stale, or None. Pure. A closed posting is never stale: closed is its own state.

    `board_empty_streak` is None for a posting with no board (a `url:` posting). It and a board never read (0) have
    no absence signal, so only the age rule can apply.
    """
    if closed_at is not None:
        return None
    kinds: list[str] = []
    texts: list[str] = []
    if posted_on is not None and date.fromisoformat(posted_on) < months_before(today, max_age_months):
        kinds.append(STALE_OLD)
        unit = "month" if max_age_months == 1 else "months"
        texts.append(f"posted {posted_on}, over {max_age_months} {unit}")
    if board_empty_streak is not None and board_empty_streak >= 1:
        kinds.append(STALE_NOT_ON_BOARD)
        texts.append(f"not on the board at its latest read (last listed {last_seen_at[:10]} or later)")
    if not kinds:
        return None
    return Stale(tuple(kinds), "; ".join(texts))


def load_stale(conn: sqlite3.Connection, today: date, *, max_age_months: int | None = None,
               posting_ids: Collection[int] | None = None, company_ids: Collection[int] | None = None
               ) -> dict[int, Stale]:
    """The stale open postings, keyed by posting_id. One read-only SELECT: no write, no transaction.

    The two filters narrow the result. `max_age_months` defaults to the config's `stale_after_months`.
    """
    months = max_age_months if max_age_months is not None else load_crawl_config().stale_after_months
    # The SQL only drops rows that cannot be stale (a superset of the rule); stale_reason decides.
    sql = ("SELECT p.posting_id, p.posted_on, p.last_seen_at, p.closed_at, b.empty_streak "
           "FROM posting p LEFT JOIN board b ON b.board_id = p.board_id "
           "WHERE p.closed_at IS NULL AND (p.posted_on < ? OR b.empty_streak >= 1)")
    args: list[object] = [months_before(today, months).isoformat()]
    if posting_ids is not None:
        sql += " AND p.posting_id IN (SELECT value FROM json_each(?))"
        args.append(json.dumps(sorted(set(posting_ids))))
    if company_ids is not None:
        sql += " AND p.company_id IN (SELECT value FROM json_each(?))"
        args.append(json.dumps(sorted(set(company_ids))))
    out: dict[int, Stale] = {}
    for posting_id, posted_on, last_seen_at, closed_at, empty_streak in conn.execute(sql, args).fetchall():
        found = stale_reason(posted_on=posted_on, last_seen_at=last_seen_at, closed_at=closed_at,
                             board_empty_streak=empty_streak, today=today, max_age_months=months)
        if found is not None:
            out[posting_id] = found
    return out
