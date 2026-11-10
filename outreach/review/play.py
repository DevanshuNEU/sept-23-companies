"""Which companies are in play (picked, planned, marked or reached), and which he is emailing (#16, Decisions).

A company is picked or planned only by the latest daily list, never by its status: the importer sets `working` on
every company he ever touched, so status says nothing about what he chose today. Since the first to see ruling (#9,
2026-11-10) no daily list is built, so a company is also in play when he marked it Done and has not cleared the mark
(`outreach.pipeline.reached`), or when he reached a person there in the last `reached_days` days (any channel, from
`outreach.plan.outbound.load_outbound`). The old lists still count while they are recent, for history.

Items for size and domain are raised only for these companies, so the list never floods: of 18,000 stored
companies a plan names at most 60. Each function is a few set-based queries, never one per company.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from outreach.pipeline import reached
from outreach.plan.outbound import load_outbound
from outreach.ranking.common import canonical_ids

__all__ = ["ny_date", "live_company_ids", "recent_list_date", "in_play", "emailing"]

_NEW_YORK = ZoneInfo("America/New_York")
REACHED_DAYS = 14  # the default of `[sweep] reached_days`: a company reached this many days ago is still in play
# A message in one of these states, on an open touch, is an email he is about to send or must look at.
_LIVE_MESSAGE_STATES = ("planned", "drafted", "approved", "sending", "send_unknown", "failed")
# A plan row he dropped or deferred is not a plan to email.
_ROW_STATES_KEPT = ("proposed", "picked")


def ny_date(now: str) -> str:
    """The New York date of a canonical UTC time: `2026-11-10T03:30:00Z` is `2026-11-09`."""
    moment = datetime.fromisoformat(now.replace("Z", "+00:00")).astimezone(_NEW_YORK)
    return moment.date().isoformat()


def live_company_ids(conn: sqlite3.Connection, *, canon: Mapping[int, int] | None = None) -> frozenset[int]:
    """Companies still in the running: status candidate, backlog or working, not merged away, and no active no-go on
    the company or on any company merged into it (a no-go he set on a duplicate bars the survivor too).

    `canon` is the company-to-survivor mapping (`canonical_ids`, read when not given); a company it does not name is
    its own survivor, so `{}` follows no merge."""
    canon = canonical_ids(conn) if canon is None else canon
    barred = {canon.get(r[0], r[0]) for r in conn.execute(
        "SELECT company_id FROM no_go WHERE company_id IS NOT NULL AND lifted_at IS NULL")}
    rows = conn.execute("SELECT company_id FROM company WHERE status IN ('candidate', 'backlog', 'working') "
                        "AND merged_into IS NULL")
    return frozenset(r[0] for r in rows if r[0] not in barred)


def recent_list_date(conn: sqlite3.Connection, today: str, planned_days: int) -> str | None:
    """The newest daily list's date, when it is no more than `planned_days` before `today`; else None.

    None means no recent plan: `in_play` and `emailing` then read no list rows (the stale sweep no longer needs one,
    first to see ruling, #9 2026-11-10)."""
    row = conn.execute("SELECT MAX(list_date) FROM daily_list").fetchone()
    if row is None or row[0] is None:
        return None
    age = (date.fromisoformat(today) - date.fromisoformat(row[0])).days
    return row[0] if age <= planned_days else None


def in_play(conn: sqlite3.Connection, *, today: str, planned_days: int,
            canon: Mapping[int, int] | None = None, reached_days: int = REACHED_DAYS) -> dict[int, str]:
    """Live company id to `'picked'`, `'planned'`, `'marked'` or `'reached'` (the first that holds, in that order).

    A plan row, mark or person on a company merged into another counts for the company it ended up in.

    Picked: the latest list has it as `picked`. Planned: the latest list has it `proposed`, with or without a named
    person (company first, #9 2026-10-26: a company row with nobody on it is what the plan proposes). The latest list
    counts only when its date is within `planned_days` of `today`. Marked: he marked it Done (first to see, #9
    2026-11-10) and has not cleared the mark. Reached: a person there was reached, on any channel, within
    `reached_days` days before `today` (an email still in the Outbox is not reached yet; `emailing` covers it).
    Status plays no part here beyond `live_company_ids`: a `working` company on no plan, with no mark and no recent
    touch, is not in play. `canon` as in `live_company_ids`.
    """
    canon = canonical_ids(conn) if canon is None else canon
    live = live_company_ids(conn, canon=canon)
    out: dict[int, str] = {}
    when = recent_list_date(conn, today, planned_days)
    if when is not None:
        for row in conn.execute("SELECT company_id, state FROM daily_list_entry WHERE list_date = ?", (when,)):
            cid = canon.get(row["company_id"], row["company_id"])  # a merge moves no plan row: follow it
            if cid not in live:
                continue
            if row["state"] == "picked":
                out[cid] = "picked"
            elif row["state"] in _ROW_STATES_KEPT:
                out.setdefault(cid, "planned")
    for cid in reached.marks(conn, dict(canon)):
        if cid in live:
            out.setdefault(cid, "marked")
    since = date.fromisoformat(today) - timedelta(days=reached_days)
    for o in load_outbound(conn, canon, tz="America/New_York"):
        if not o.queued and o.sent_on >= since and o.company_id in live:
            out.setdefault(o.company_id, "reached")
    return out


def emailing(conn: sqlite3.Connection, *, today: str, planned_days: int,
             canon: Mapping[int, int] | None = None) -> frozenset[int]:
    """Live companies he is emailing: a person with an address there has a message in `planned`, `drafted`,
    `approved`, `sending`, `send_unknown` or `failed` on an open touch, or the latest list has an email row for a
    person there. A person or plan row on a company merged into another counts for the company it ended up in.
    `canon` as in `live_company_ids`."""
    canon = canonical_ids(conn) if canon is None else canon
    live = live_company_ids(conn, canon=canon)
    marks = ", ".join("?" for _ in _LIVE_MESSAGE_STATES)
    found = {r[0] for r in conn.execute(
        "SELECT DISTINCT p.company_id FROM message m JOIN touch t ON t.touch_id = m.touch_id "
        "JOIN person p ON p.person_id = t.person_id "
        f"WHERE t.state = 'open' AND p.email IS NOT NULL AND m.state IN ({marks})", _LIVE_MESSAGE_STATES)}
    when = recent_list_date(conn, today, planned_days)
    if when is not None:
        found.update(r[0] for r in conn.execute(
            "SELECT company_id FROM daily_list_entry WHERE list_date = ? AND channel = 'email' "
            "AND person_id IS NOT NULL AND state IN ('proposed', 'picked')", (when,)))
    return frozenset({canon.get(cid, cid) for cid in found} & live)
