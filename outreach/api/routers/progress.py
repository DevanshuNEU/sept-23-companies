"""`GET /api/progress` and `GET /api/findings`: what he did, what came of it, and what the data suggests."""
from fastapi import APIRouter, Depends, Request, Response

from outreach.api import views
from outreach.api.clock import Clock, get_clock
from outreach.api.db import read_conn
from outreach.api.models.progress import FindingsView, ProgressView
from outreach.api.queries import progress

router = APIRouter(tags=["progress"])


@router.get("/progress", response_model=ProgressView)
async def progress_view(request: Request) -> Response:
    def compute(now: str, _) -> ProgressView:
        with read_conn(request) as conn:
            return progress.progress_view(conn, now, request.app.state.config)

    return await views.serve(request, ProgressView, compute)


@router.get("/findings", response_model=FindingsView)
def findings_view(request: Request, clock: Clock = Depends(get_clock)) -> FindingsView:
    now = clock()
    with read_conn(request) as conn:
        return progress.findings_view(conn, now, request.app.state.config)
