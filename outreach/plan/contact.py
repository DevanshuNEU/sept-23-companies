"""Company contact state, the wave and the 14-day no-answer rule (#15, Task 5; #9 company-first ruling, 2026-10-26).

One wave per company: he asks up to `queue.requests_per_company` people at a company, then the company waits. A
company is `waiting` while any outbound touch (LinkedIn or email, either channel) has an anchor date under
`linkedin.rotate_days` old with no reply, or an email is still queued (a follow-up to go, or a first email in the
Outbox); `engaged` when anyone replied or a referral, interview or offer exists (never auto-rotated); `stale` when it
has outbound touches and none is waiting or engaged; `free` with none. Only `free` and `stale` companies are eligible
for a new queue row, and the caller decides how far `stale` rotates.

The wave is the people reached inside that window (a queued email counts): `wave_people`. While it holds fewer than
`queue.requests_per_company`, `wave_room` says how many more he may ask (2 asked today leaves room for 2 more the
same day); a full wave has no room and the company is free again on `free_on` (its newest anchor plus
`linkedin.rotate_days`). A free or stale company has a whole wave of room; an engaged one has none (that thread
first). Pure over `Outbound` rows; `load_contact_states` adds the store read.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from outreach.plan.config import PlanConfig
from outreach.plan.outbound import Outbound, load_outbound

__all__ = ["ContactState", "contact_states", "load_contact_states"]


@dataclass(frozen=True)
class ContactState:
    company_id: int
    status: Literal["free", "waiting", "engaged", "stale"]
    people_contacted: int
    email_people: int
    last_person_id: int | None
    last_anchor: date | None
    reason: str  # a plain line for the UI, no dashes
    wave_people: int = 0  # people reached inside linkedin.rotate_days (a queued email counts)
    wave_room: int = 0  # how many more he may ask in this wave: queue.requests_per_company less wave_people
    free_on: date | None = None  # the newest anchor of the wave plus linkedin.rotate_days; None with no wave


def _days(n: int) -> str:
    return "today" if n <= 0 else ("1 day ago" if n == 1 else f"{n} days ago")


def _waiting_reason(row: Outbound, today: date) -> str:
    if row.queued:
        return "Email queued, no answer yet"
    when = _days((today - row.anchor_on).days)
    if row.channel == "email":
        what = "Email sent"
    elif row.accepted:
        what = "Request accepted"
    elif row.channel == "linkedin_message":
        what = "Message sent"
    else:
        what = "Request sent"
    return f"{what} {when}, no answer yet"


def _state(company_id: int, rows: list[Outbound], today: date, rotate_days: int, wave_size: int) -> ContactState:
    if not rows:
        return ContactState(company_id, "free", 0, 0, None, None, "No outreach yet", 0, wave_size, None)
    newest = max(rows, key=lambda o: (o.anchor_on, o.sent_on, o.person_id or 0))
    people = {o.person_id for o in rows if o.person_id is not None}
    email_people = {o.person_id for o in rows if o.person_id is not None and o.channel == "email"}
    # A queued email (a follow-up still to go, or a first email in the Outbox) is in play however old it is.
    waiting = [o for o in rows if o.queued or (today - o.anchor_on).days < rotate_days]
    if any(o.replied or o.progressed for o in rows):
        status, reason = "engaged", ("A referral or interview is in progress" if any(o.progressed for o in rows)
                                     else "Someone replied")
    elif waiting:
        status, reason = "waiting", _waiting_reason(max(waiting, key=lambda o: (o.anchor_on, o.sent_on, o.queued)), today)
    else:
        status, reason = "stale", f"No answer for {(today - newest.anchor_on).days} days"
    wave_people = len({o.person_id for o in waiting if o.person_id is not None})
    room = 0 if status == "engaged" else max(0, wave_size - wave_people)
    free_on = max(o.anchor_on for o in waiting) + timedelta(days=rotate_days) if waiting else None
    return ContactState(company_id, status, len(people), len(email_people), newest.person_id, newest.anchor_on,
                        reason, wave_people, room, free_on)


def contact_states(outbound: Iterable[Outbound], company_ids: Iterable[int], today: date,
                   cfg: PlanConfig) -> dict[int, ContactState]:
    """A state for every id in `company_ids` (a company with no rows is `free`). `company_id` on the rows is the
    survivor of any merge, so `company_ids` are survivor ids too. Rows for other companies are ignored."""
    wanted = list(dict.fromkeys(company_ids))
    by_company: dict[int, list[Outbound]] = {c: [] for c in wanted}
    for o in outbound:
        if o.company_id in by_company:
            by_company[o.company_id].append(o)
    rotate_days, wave_size = cfg.get("linkedin", "rotate_days"), cfg.get("queue", "requests_per_company")
    return {c: _state(c, rows, today, rotate_days, wave_size) for c, rows in by_company.items()}


def load_contact_states(conn: sqlite3.Connection, company_ids: Collection[int], canon: dict[int, int], today: date,
                        cfg: PlanConfig) -> dict[int, ContactState]:
    ids = list(dict.fromkeys(company_ids))
    rows = load_outbound(conn, canon, tz=cfg.get("plan", "timezone"), company_ids=ids)
    return contact_states(rows, ids, today, cfg)
