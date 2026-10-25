"""One normalized read of outbound history for the plan (#15, Task 3).

Outreach to a person sits in three places: `outreach_touch` (the one outcome record per touch, every channel),
legacy email `touch` rows from before the switch, and `linkedin_log` entries that were imported without a touch.
`load_outbound` merges them into one list of `Outbound` rows, counting each real touch once. Applications are
touches with no person: they are never outreach to a person and never appear here. Reads only; no clock.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date
from typing import Literal

from outreach.plan.dates import ny_date

__all__ = ["Outbound", "load_outbound"]

_LOG_CHANNEL = {"request_sent": "linkedin_request", "message_sent": "linkedin_message"}


@dataclass(frozen=True)
class Outbound:
    person_id: int | None
    company_id: int
    channel: str
    sent_on: date
    anchor_on: date  # the later of sent_on and the accepted date
    accepted: bool
    replied: bool
    progressed: bool  # referred, interview or offer
    lane: str | None
    context_source: Literal["plan", "own"]
    source: Literal["touch", "email_touch", "linkedin_log"]


def _context(text: str) -> dict:
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _touch_rows(conn: sqlite3.Connection, canon: dict[int, int], tz: str) -> list[Outbound]:
    out = []
    for r in conn.execute(
            "SELECT person_id, company_id, channel, sent_at, accepted_at, replied_at, referred_at, interview_at, "
            "offer_at, context FROM outreach_touch WHERE channel <> 'application' AND undone_at IS NULL "
            "ORDER BY touch_id"):
        sent_on = ny_date(r["sent_at"], tz)
        accepted_on = ny_date(r["accepted_at"], tz) if r["accepted_at"] else None
        ctx = _context(r["context"])
        lane = ctx.get("lane")
        out.append(Outbound(
            person_id=r["person_id"], company_id=canon.get(r["company_id"], r["company_id"]),
            channel=r["channel"], sent_on=sent_on, anchor_on=max(sent_on, accepted_on or sent_on),
            accepted=accepted_on is not None, replied=r["replied_at"] is not None,
            progressed=any(r[c] is not None for c in ("referred_at", "interview_at", "offer_at")),
            lane=lane if isinstance(lane, str) else None,
            context_source="plan" if ctx.get("source") == "plan" else "own", source="touch"))
    return out


def _email_rows(conn: sqlite3.Connection, canon: dict[int, int], tz: str) -> list[Outbound]:
    out = []
    for r in conn.execute(
            "SELECT t.person_id, p.company_id, t.first_sent_at, t.state FROM touch t "
            "JOIN person p ON p.person_id = t.person_id WHERE t.first_sent_at IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM outreach_touch o WHERE o.email_touch_id = t.touch_id) "
            "ORDER BY t.touch_id"):
        sent_on = ny_date(r["first_sent_at"], tz)
        out.append(Outbound(
            person_id=r["person_id"], company_id=canon.get(r["company_id"], r["company_id"]), channel="email",
            sent_on=sent_on, anchor_on=sent_on, accepted=False, replied=r["state"] == "replied", progressed=False,
            lane=None, context_source="own", source="email_touch"))
    return out


def _log_rows(conn: sqlite3.Connection, canon: dict[int, int], tz: str) -> list[Outbound]:
    # Every touch the log's twin could be, undone ones too: an undone touch must not bring its log entry back.
    twins = {(r["person_id"], r["channel"], ny_date(r["sent_at"], tz)) for r in conn.execute(
        "SELECT person_id, channel, sent_at FROM outreach_touch WHERE person_id IS NOT NULL")}
    undone = {r[0] for r in conn.execute("SELECT undoes FROM linkedin_log WHERE undoes IS NOT NULL")}
    accepted: dict[int, list[date]] = {}
    replied: dict[int, list[date]] = {}
    entries = []
    for r in conn.execute("SELECT entry_id, person_id, kind, at FROM linkedin_log WHERE kind <> 'undo' "
                          "ORDER BY entry_id"):
        if r["entry_id"] in undone:
            continue
        day = ny_date(r["at"], tz)
        if r["kind"] == "accepted":
            accepted.setdefault(r["person_id"], []).append(day)
        elif r["kind"] == "replied":
            replied.setdefault(r["person_id"], []).append(day)
        elif r["kind"] in _LOG_CHANNEL:
            entries.append((r["person_id"], _LOG_CHANNEL[r["kind"]], day))
    company = {r["person_id"]: r["company_id"] for r in conn.execute("SELECT person_id, company_id FROM person")}
    out = []
    for person_id, channel, day in entries:
        if (person_id, channel, day) in twins:
            continue
        accepted_on = min((d for d in accepted.get(person_id, ()) if d >= day), default=None)
        out.append(Outbound(
            person_id=person_id, company_id=canon.get(company[person_id], company[person_id]), channel=channel,
            sent_on=day, anchor_on=max(day, accepted_on or day), accepted=accepted_on is not None,
            replied=any(d >= day for d in replied.get(person_id, ())), progressed=False, lane=None,
            context_source="own", source="linkedin_log"))
    return out


def load_outbound(conn: sqlite3.Connection, canon: dict[int, int], *, tz: str, since: date | None = None,
                  company_ids: Collection[int] | None = None) -> list[Outbound]:
    """Every outbound touch to a person, once, from the three tables (in that order). `company_id` follows `canon`
    (the survivor of a merge). `since` keeps rows with `sent_on >= since`; `company_ids` keeps those companies
    (survivor ids). Three bulk queries and Python-side dedupe, never a query per person."""
    rows = _touch_rows(conn, canon, tz) + _email_rows(conn, canon, tz) + _log_rows(conn, canon, tz)
    wanted = None if company_ids is None else set(company_ids)
    return [o for o in rows if (since is None or o.sent_on >= since) and (wanted is None or o.company_id in wanted)]
