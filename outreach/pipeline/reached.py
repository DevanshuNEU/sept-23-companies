"""His own reached mark (#195, the first to see ruling in #9): Done on the job list.

Done records that he has reached a company, even when the outreach happened outside Knock: a `company_reached` event
(entity company, actor devanshu, detail `posting_id` and `company_id`). Its undo is a `company_reached_cleared` event.
Events are append-only, so a company's mark is its newest `company_reached` with no `company_reached_cleared`
written after it, and a mark after a clear counts again. Events are read in the order they were written (`event_id`),
so a clear in the same second as its mark still clears it. A merged-away company's events count for its survivor.

The job list reads the marks into `contacted_on` with every touch (`outreach.pipeline.joblist`). Reads only.
"""
from __future__ import annotations

import sqlite3

from outreach.outcomes.windows import ny_day
from outreach.ranking.common import canonical_ids

__all__ = ["REACHED", "CLEARED", "marks"]

REACHED = "company_reached"  # event kind: Done on the job list
CLEARED = "company_reached_cleared"  # event kind: Done taken back


def marks(conn: sqlite3.Connection, canon: dict[int, int] | None = None) -> dict[int, str]:
    """Each survivor company with a standing mark, to the New York date (YYYY-MM-DD) of its newest one. `canon`
    defaults to `canonical_ids(conn)`."""
    canon = canonical_ids(conn) if canon is None else canon
    out: dict[int, str] = {}
    for entity_id, kind, at in conn.execute(
            "SELECT entity_id, kind, at FROM event WHERE entity = 'company' AND actor = 'devanshu' "
            "AND kind IN (?, ?) AND entity_id IS NOT NULL ORDER BY event_id", (REACHED, CLEARED)):
        company = canon.get(entity_id, entity_id)
        if kind == REACHED:
            out[company] = ny_day(at)
        else:
            out.pop(company, None)
    return out
