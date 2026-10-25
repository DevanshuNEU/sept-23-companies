"""Company contact state and the 14-day no-answer rule (#15, Task 5).

One person per company at a time: a company is `waiting` while any outbound touch (LinkedIn or email, either
channel) has an anchor date under `linkedin.rotate_days` old with no reply, or an email is still queued (a
follow-up to go, or a first email in the Outbox); `engaged` when anyone replied or a
referral, interview or offer exists (never auto-rotated); `stale` when it has outbound touches and none is waiting or
engaged; `free` with none. Only `free` and `stale` companies are eligible, and the caller decides how far `stale`
rotates. Pure over `Outbound` rows; `load_contact_states` adds the store read.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date
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


def _state(company_id: int, rows: list[Outbound], today: date, rotate_days: int) -> ContactState:
    if not rows:
        return ContactState(company_id, "free", 0, 0, None, None, "No outreach yet")
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
    return ContactState(company_id, status, len(people), len(email_people), newest.person_id, newest.anchor_on,
                        reason)


def contact_states(outbound: Iterable[Outbound], company_ids: Iterable[int], today: date,
                   cfg: PlanConfig) -> dict[int, ContactState]:
    """A state for every id in `company_ids` (a company with no rows is `free`). `company_id` on the rows is the
    survivor of any merge, so `company_ids` are survivor ids too. Rows for other companies are ignored."""
    wanted = list(dict.fromkeys(company_ids))
    by_company: dict[int, list[Outbound]] = {c: [] for c in wanted}
    for o in outbound:
        if o.company_id in by_company:
            by_company[o.company_id].append(o)
    rotate_days = cfg.get("linkedin", "rotate_days")
    return {c: _state(c, rows, today, rotate_days) for c, rows in by_company.items()}


def load_contact_states(conn: sqlite3.Connection, company_ids: Collection[int], canon: dict[int, int], today: date,
                        cfg: PlanConfig) -> dict[int, ContactState]:
    ids = list(dict.fromkeys(company_ids))
    rows = load_outbound(conn, canon, tz=cfg.get("plan", "timezone"), company_ids=ids)
    return contact_states(rows, ids, today, cfg)
