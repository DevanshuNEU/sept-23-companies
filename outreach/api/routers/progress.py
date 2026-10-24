"""`GET /api/progress` and `GET /api/findings`: what he did, what came of it, and what the data suggests."""
from fastapi import APIRouter, Depends, Request

from outreach.api.clock import Clock, get_clock
from outreach.api.db import read_conn
from outreach.api.models.progress import FindingsView, ProgressView
from outreach.api.queries import progress

router = APIRouter(tags=["progress"])


@router.get("/progress", response_model=ProgressView)
def progress_view(request: Request, clock: Clock = Depends(get_clock)) -> ProgressView:
    now = clock()
    with read_conn(request) as conn:
        return progress.progress_view(conn, now, request.app.state.config)


@router.get("/findings", response_model=FindingsView)
def findings_view(request: Request, clock: Clock = Depends(get_clock)) -> FindingsView:
    now = clock()
    with read_conn(request) as conn:
        return progress.findings_view(conn, now, request.app.state.config)
