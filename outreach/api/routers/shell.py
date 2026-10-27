"""`GET /api/shell`: the sidebar's counts, the sender line, today's plan and the wins he has not seen. A heavy view
(`outreach.api.views`)."""
from fastapi import APIRouter, Request, Response

from outreach.api import views
from outreach.api.db import read_conn
from outreach.api.models.shell import ShellSummary
from outreach.api.queries import shell

router = APIRouter(tags=["shell"])


@router.get("/shell", response_model=ShellSummary)
async def shell_summary(request: Request) -> Response:
    def compute(now: str, old_sender: bool) -> ShellSummary:
        with read_conn(request) as conn:
            return shell.shell_summary(conn, now, request.app.state.config, old_sender_loaded=old_sender)

    # The old mailer check runs before the read opens: it may ask launchctl.
    return await views.serve(request, ShellSummary, compute, request.app.state.old_sender_loaded)
