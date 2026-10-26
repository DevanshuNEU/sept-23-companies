"""Seed helpers for the insights tests (#81): an `outreach_touch` with a valid context, on top of `tests/apiseed.py`.

Plain SQL, so a change to the touch writers cannot hide a change to the stored shape.
"""
import itertools
import json
import sqlite3

from tests.apiseed import T0, _insert

_n = itertools.count(1)


def outcome_touch(conn: sqlite3.Connection, company_id: int, person_id: int | None, *,
                  channel: str = "linkedin_request", sent_at: str, accepted_at: str | None = None,
                  replied_at: str | None = None, referred_at: str | None = None, interview_at: str | None = None,
                  offer_at: str | None = None, posting_id: int | None = None, lane: str = "level_role",
                  role_kind: str | None = "engineer", undone_at: str | None = None) -> int:
    """One outreach touch and its outcome stamps. `person_id` may be None only for channel `application`."""
    context = {"source": "own", "lane": lane, "roleKind": role_kind, "roleAgeDays": None, "rankerVersion": None,
               "signals": {}, "roleNamed": False}
    return _insert(conn, "outreach_touch", {
        "person_id": person_id, "company_id": company_id, "channel": channel, "posting_id": posting_id,
        "sent_at": sent_at, "accepted_at": accepted_at, "replied_at": replied_at,
        "reply_kind": "interested" if replied_at else None, "referred_at": referred_at,
        "interview_at": interview_at, "offer_at": offer_at, "undone_at": undone_at,
        "context": json.dumps(context), "idempotency_key": f"insights-seed-{next(_n)}", "created_at": sent_at or T0,
    })


def application(conn: sqlite3.Connection, company_id: int, *, applied_on: str = "2026-10-01",
                outcome: str = "open", outcome_at: str | None = None, posting_id: int | None = None) -> int:
    if outcome != "open" and outcome_at is None:
        outcome_at = "2026-10-10T12:00:00Z"
    return _insert(conn, "application", {"company_id": company_id, "posting_id": posting_id,
                                         "applied_on": applied_on, "outcome": outcome, "outcome_at": outcome_at})


def merge(conn: sqlite3.Connection, loser: int, into: int) -> None:
    """Merge `loser` into `into` the way the store records it: removed, with `merged_into` set."""
    conn.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', close_reason = NULL, "
                 "merged_into = ? WHERE company_id = ?", (into, loser))
