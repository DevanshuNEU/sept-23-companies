"""`GET /api/pulse`: one number that moves on whenever anything is written, so an open page knows to refetch.

The store already has the counter: `event` is append-only (its triggers refuse every update and delete), and every
write tool of the MCP server appends one (`intake` and `log_sent` included), as most of the page's own writes and the
jobs do. So the pulse is the newest event id, one read of the primary key. Read only; it says nothing beyond the
number.
"""
from fastapi import APIRouter, Request

from outreach.api.db import read_conn
from outreach.api.models.pulse import Pulse

router = APIRouter(tags=["pulse"])


@router.get("/pulse", response_model=Pulse)
def pulse(request: Request) -> Pulse:
    with read_conn(request) as conn:
        return Pulse(revision=conn.execute("SELECT coalesce(max(event_id), 0) FROM event").fetchone()[0])
