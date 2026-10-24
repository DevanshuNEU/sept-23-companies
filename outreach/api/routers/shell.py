"""`GET /api/shell`: the sidebar's counts, the sender line, today's plan and the wins he has not seen."""
from fastapi import APIRouter, Depends, Request

from outreach.api.clock import Clock, get_clock
from outreach.api.db import read_conn
from outreach.api.models.shell import ShellSummary
from outreach.api.queries import shell

router = APIRouter(tags=["shell"])


@router.get("/shell", response_model=ShellSummary)
def shell_summary(request: Request, clock: Clock = Depends(get_clock)) -> ShellSummary:
    now = clock()
    old_sender = request.app.state.old_sender_loaded()  # outside the read: it may ask launchctl
    with read_conn(request) as conn:
        return shell.shell_summary(conn, now, old_sender_loaded=old_sender)
