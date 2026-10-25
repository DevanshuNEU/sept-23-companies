"""One normalized read of outbound history for the plan (#15, Task 3).

Outreach to a person sits in three places: `outreach_touch` (the one outcome record per touch, every channel),
legacy email `touch` rows from before the switch, and `linkedin_log` entries that were imported without a touch.
`load_outbound` merges them into one list of `Outbound` rows, counting each real touch once. Applications are
touches with no person: they are never outreach to a person and never appear here. Reads only; no clock.

Two kinds of email state count beyond sent mail: a thread's anchor is its latest sent message (a follow-up keeps a
thread alive), and a thread with a follow-up still queued, or a first email still in the Outbox, is `queued`, which
the contact rule reads as waiting however old it is.
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
_QUEUED_FOLLOWUP = ("planned", "drafted", "approved", "sending", "send_unknown")
_QUEUED_FIRST = ("planned", "drafted", "approved", "sending", "send_unknown", "failed")


@dataclass(frozen=True)
class Outbound:
    person_id: int | None
    company_id: int
    channel: str
    sent_on: date
    anchor_on: date  # the later of sent_on and the accepted date (an email thread: its latest sent message)
    accepted: bool
    replied: bool
    progressed: bool  # referred, interview or offer
    lane: str | None
    context_source: Literal["plan", "own"]
    source: Literal["touch", "email_touch", "linkedin_log", "queued_email"]
    queued: bool = False  # an email still to go out (a follow-up or a first email): waiting whatever its age


def _context(text: str) -> dict:
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


@dataclass
class _Log:
    accepted: dict[int, list[date]]
    replied: dict[int, list[date]]
    entries: list[tuple[int, str, date]]  # request and message entries: (person_id, channel, day)


def _log_index(conn: sqlite3.Connection, tz: str) -> _Log:
    """The live `linkedin_log` entries (none that a later `undo` entry takes back), by kind."""
    undone = {r[0] for r in conn.execute("SELECT undoes FROM linkedin_log WHERE undoes IS NOT NULL")}
    log = _Log({}, {}, [])
    for r in conn.execute("SELECT entry_id, person_id, kind, at FROM linkedin_log WHERE kind <> 'undo' "
                          "ORDER BY entry_id"):
        if r["entry_id"] in undone:
            continue
        day = ny_date(r["at"], tz)
        if r["kind"] == "accepted":
            log.accepted.setdefault(r["person_id"], []).append(day)
        elif r["kind"] == "replied":
            log.replied.setdefault(r["person_id"], []).append(day)
        elif r["kind"] in _LOG_CHANNEL:
            log.entries.append((r["person_id"], _LOG_CHANNEL[r["kind"]], day))
    return log


@dataclass
class _Threads:
    last_sent: dict[int, date]  # email touch id -> New York date of its latest sent message
    followup_queued: set[int]  # open email touches with a follow-up still to go out
    first_queued: list[tuple[int, int, date]]  # (person_id, company_id, queued on) of unsent first emails
    live_outreach: set[int]  # email touches that have a live outreach_touch (the sender wrote it at the first send)


def _threads(conn: sqlite3.Connection, tz: str) -> _Threads:
    live = {r[0] for r in conn.execute(
        "SELECT email_touch_id FROM outreach_touch WHERE email_touch_id IS NOT NULL AND undone_at IS NULL")}
    out = _Threads({}, set(), [], live)
    for r in conn.execute(
            "SELECT m.touch_id, m.seq, m.state, m.sent_at, m.created_at, t.state AS touch_state, "
            "t.first_sent_at, t.person_id, p.company_id FROM message m JOIN touch t ON t.touch_id = m.touch_id "
            "JOIN person p ON p.person_id = t.person_id ORDER BY m.message_id"):
        touch_id = r["touch_id"]
        if r["state"] == "sent" and r["sent_at"]:
            day = ny_date(r["sent_at"], tz)
            out.last_sent[touch_id] = max(day, out.last_sent.get(touch_id, day))
        if r["touch_state"] != "open":
            continue
        if r["seq"] >= 1 and r["state"] in _QUEUED_FOLLOWUP:
            out.followup_queued.add(touch_id)
        elif (r["seq"] == 0 and r["state"] in _QUEUED_FIRST and r["first_sent_at"] is None
              and touch_id not in live):
            out.first_queued.append((r["person_id"], r["company_id"], ny_date(r["created_at"], tz)))
    return out


def _touch_rows(conn: sqlite3.Connection, canon: dict[int, int], tz: str, log: _Log,
                threads: _Threads) -> list[Outbound]:
    out = []
    for r in conn.execute(
            "SELECT person_id, company_id, channel, email_touch_id, sent_at, accepted_at, replied_at, referred_at, "
            "interview_at, offer_at, context FROM outreach_touch WHERE channel <> 'application' "
            "AND undone_at IS NULL ORDER BY touch_id"):
        sent_on = ny_date(r["sent_at"], tz)
        accepted_on = ny_date(r["accepted_at"], tz) if r["accepted_at"] else None
        replied = r["replied_at"] is not None
        anchor = sent_on
        queued = False
        if r["channel"].startswith("linkedin"):
            # An accept or a reply that reached only the log still belongs to this person's live request.
            if accepted_on is None:
                accepted_on = min((d for d in log.accepted.get(r["person_id"], ()) if d >= sent_on), default=None)
            replied = replied or any(d >= sent_on for d in log.replied.get(r["person_id"], ()))
        elif r["channel"] == "email" and r["email_touch_id"] is not None:
            anchor = max(anchor, threads.last_sent.get(r["email_touch_id"], anchor))
            queued = r["email_touch_id"] in threads.followup_queued
        ctx = _context(r["context"])
        lane = ctx.get("lane")
        out.append(Outbound(
            person_id=r["person_id"], company_id=canon.get(r["company_id"], r["company_id"]),
            channel=r["channel"], sent_on=sent_on, anchor_on=max(anchor, accepted_on or sent_on),
            accepted=accepted_on is not None, replied=replied,
            progressed=any(r[c] is not None for c in ("referred_at", "interview_at", "offer_at")),
            lane=lane if isinstance(lane, str) else None,
            context_source="plan" if ctx.get("source") == "plan" else "own", source="touch", queued=queued))
    return out


def _email_rows(conn: sqlite3.Connection, canon: dict[int, int], tz: str, threads: _Threads) -> list[Outbound]:
    out = []
    # A legacy touch is covered only by a LIVE outreach_touch: an undone one is a mistake, not the record.
    for r in conn.execute(
            "SELECT t.touch_id, t.person_id, p.company_id, t.first_sent_at, t.state FROM touch t "
            "JOIN person p ON p.person_id = t.person_id WHERE t.first_sent_at IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM outreach_touch o WHERE o.email_touch_id = t.touch_id "
            "AND o.undone_at IS NULL) ORDER BY t.touch_id"):
        sent_on = ny_date(r["first_sent_at"], tz)
        out.append(Outbound(
            person_id=r["person_id"], company_id=canon.get(r["company_id"], r["company_id"]), channel="email",
            sent_on=sent_on, anchor_on=max(sent_on, threads.last_sent.get(r["touch_id"], sent_on)), accepted=False,
            replied=r["state"] == "replied", progressed=False, lane=None, context_source="own",
            source="email_touch", queued=r["touch_id"] in threads.followup_queued and r["state"] == "open"))
    return out


def _queued_rows(canon: dict[int, int], threads: _Threads) -> list[Outbound]:
    """A first email in the Outbox that has not gone out: no touch row exists yet, but the company is in play."""
    return [Outbound(person_id=person_id, company_id=canon.get(company_id, company_id), channel="email",
                     sent_on=day, anchor_on=day, accepted=False, replied=False, progressed=False, lane=None,
                     context_source="own", source="queued_email", queued=True)
            for person_id, company_id, day in threads.first_queued]


def _log_rows(conn: sqlite3.Connection, canon: dict[int, int], tz: str, log: _Log) -> list[Outbound]:
    # A log entry's twin is a LIVE touch of the same person, channel and New York day. An undone touch is a mistake
    # and takes no entry with it: an entry still alive in the log counts on its own.
    twins = {(r["person_id"], r["channel"], ny_date(r["sent_at"], tz)) for r in conn.execute(
        "SELECT person_id, channel, sent_at FROM outreach_touch WHERE person_id IS NOT NULL AND undone_at IS NULL")}
    company = {r["person_id"]: r["company_id"] for r in conn.execute("SELECT person_id, company_id FROM person")}
    out = []
    for person_id, channel, day in log.entries:
        if (person_id, channel, day) in twins:
            continue
        accepted_on = min((d for d in log.accepted.get(person_id, ()) if d >= day), default=None)
        out.append(Outbound(
            person_id=person_id, company_id=canon.get(company[person_id], company[person_id]), channel=channel,
            sent_on=day, anchor_on=max(day, accepted_on or day), accepted=accepted_on is not None,
            replied=any(d >= day for d in log.replied.get(person_id, ())), progressed=False, lane=None,
            context_source="own", source="linkedin_log"))
    return out


def load_outbound(conn: sqlite3.Connection, canon: dict[int, int], *, tz: str, since: date | None = None,
                  company_ids: Collection[int] | None = None) -> list[Outbound]:
    """Every outbound touch to a person, once, from the three tables (in that order), then the first emails still
    in the Outbox (`queued`). `company_id` follows `canon` (the survivor of a merge). `since` keeps rows with
    `sent_on >= since`; `company_ids` keeps those companies (survivor ids). A handful of bulk queries and
    Python-side dedupe, never a query per person."""
    log = _log_index(conn, tz)
    threads = _threads(conn, tz)
    rows = (_touch_rows(conn, canon, tz, log, threads) + _email_rows(conn, canon, tz, threads)
            + _queued_rows(canon, threads) + _log_rows(conn, canon, tz, log))
    wanted = None if company_ids is None else set(company_ids)
    return [o for o in rows if (since is None or o.sent_on >= since) and (wanted is None or o.company_id in wanted)]
