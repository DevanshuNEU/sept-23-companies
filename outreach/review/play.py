"""Which companies he has picked or planned, and which he is emailing (#16, Decisions).

Items for size and domain are raised only for these companies, so the list never floods: of 18,000 stored
companies a plan names at most 60. Each function is a few set-based queries, never one per company.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo

__all__ = ["ny_date", "live_company_ids", "recent_list_date", "in_play", "emailing"]

_NEW_YORK = ZoneInfo("America/New_York")
# A message in one of these states, on an open touch, is an email he is about to send or must look at.
_LIVE_MESSAGE_STATES = ("planned", "drafted", "approved", "sending", "send_unknown", "failed")
# A plan row he dropped or deferred is not a plan to email.
_ROW_STATES_KEPT = ("proposed", "picked")


def ny_date(now: str) -> str:
    """The New York date of a canonical UTC time: `2026-11-10T03:30:00Z` is `2026-11-09`."""
    moment = datetime.fromisoformat(now.replace("Z", "+00:00")).astimezone(_NEW_YORK)
    return moment.date().isoformat()


def live_company_ids(conn: sqlite3.Connection) -> frozenset[int]:
    """Companies still in the running: status candidate, backlog or working, not merged away, no active no-go."""
    rows = conn.execute(
        "SELECT c.company_id FROM company c WHERE c.status IN ('candidate', 'backlog', 'working') "
        "AND c.merged_into IS NULL AND NOT EXISTS ("
        "SELECT 1 FROM no_go n WHERE n.company_id = c.company_id AND n.lifted_at IS NULL)")
    return frozenset(r[0] for r in rows)


def recent_list_date(conn: sqlite3.Connection, today: str, planned_days: int) -> str | None:
    """The newest daily list's date, when it is no more than `planned_days` before `today`; else None.

    None also means "no recent plan": a job that needs a plan to judge by (the stale sweep) skips on it."""
    row = conn.execute("SELECT MAX(list_date) FROM daily_list").fetchone()
    if row is None or row[0] is None:
        return None
    age = (date.fromisoformat(today) - date.fromisoformat(row[0])).days
    return row[0] if age <= planned_days else None


def in_play(conn: sqlite3.Connection, *, today: str, planned_days: int) -> dict[int, str]:
    """Live company id to `'picked'` or `'planned'` (picked wins).

    Picked: the company is `working`, or the latest list has it as `picked`. Planned: the latest list has it
    `proposed` or `picked` with a named person. The latest list counts only when its date is within `planned_days`
    of `today`.
    """
    live = live_company_ids(conn)
    out: dict[int, str] = {}
    for (cid,) in conn.execute("SELECT company_id FROM company WHERE status = 'working'"):
        if cid in live:
            out[cid] = "picked"
    when = recent_list_date(conn, today, planned_days)
    if when is not None:
        for row in conn.execute("SELECT company_id, state, person_id FROM daily_list_entry WHERE list_date = ?",
                                (when,)):
            cid = row["company_id"]
            if cid not in live:
                continue
            if row["state"] == "picked":
                out[cid] = "picked"
            elif row["person_id"] is not None and row["state"] in _ROW_STATES_KEPT:
                out.setdefault(cid, "planned")
    return out


def emailing(conn: sqlite3.Connection, *, today: str, planned_days: int) -> frozenset[int]:
    """Live companies he is emailing: a person with an address there has a message in `planned`, `drafted`,
    `approved`, `sending`, `send_unknown` or `failed` on an open touch, or the latest list has an email row for a
    person there."""
    live = live_company_ids(conn)
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
    return frozenset(found & live)
