"""`GET /api/health` and `GET /api/health/jobs/{job}`."""
from typing import get_args

from fastapi import APIRouter, Depends, Request

from outreach.api.clock import Clock, get_clock
from outreach.api.db import read_conn
from outreach.api.errors import ApiProblem
from outreach.api.models.health import HealthView, JobKind, JobRunSummary
from outreach.api.queries import jobs
from outreach.api.queries.sending import sender_summary

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=HealthView)
def health(request: Request, clock: Clock = Depends(get_clock)) -> HealthView:
    now = clock()
    old_sender = request.app.state.old_sender_loaded()  # outside the read: it may ask launchctl
    with read_conn(request) as conn:
        return HealthView(
            as_of=now,
            store=jobs.store_info(conn),
            jobs=jobs.last_runs(conn, now),
            read_queue=jobs.read_queue_status(conn, now),
            boards=jobs.board_counts(conn, now),
            watched_pages=jobs.watched_page_counts(conn, now),
            sender=sender_summary(conn, now, old_sender_loaded=old_sender),
        )


@router.get("/jobs/{job}", response_model=list[JobRunSummary])
def health_job(job: str, request: Request, clock: Clock = Depends(get_clock)) -> list[JobRunSummary]:
    if job not in get_args(JobKind):
        raise ApiProblem("not_found", f"There is no job called {job}.")
    now = clock()
    with read_conn(request) as conn:
        return jobs.runs_of(conn, job, now)
