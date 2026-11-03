"""The job list (#156): `GET /api/jobs`, open engineering postings, newest first, one page at a time
(`outreach.pipeline.joblist` decides which and in which order)."""
from fastapi import APIRouter, Query, Request, Response

from outreach.api import views
from outreach.api.db import read_conn
from outreach.api.errors import ApiProblem
from outreach.api.models.jobs import JobSize, JobsView, JobView, JobWindow
from outreach.api.queries import jobs_list as queries
from outreach.pipeline.joblist import JobFilters

router = APIRouter(tags=["jobs"])

CURSOR_MAX = 200


@router.get("/jobs", response_model=JobsView)
async def jobs(request: Request, view: JobView = "your_level", window: JobWindow = "30", size: JobSize = "any",
               not_contacted: bool = Query(False, alias="notContacted"), q: str = Query("", max_length=100),
               after: str | None = Query(None, max_length=CURSOR_MAX), limit: int = Query(50, ge=1, le=50)) -> Response:
    """The job list: open engineering postings at his level (or the higher levels), newest first, 50 a page."""
    f = JobFilters(view=view, window_days=0 if window == "any" else int(window), size=size,
                   not_contacted=not_contacted, q=q)

    def compute(now: str, _) -> JobsView:
        with read_conn(request) as conn:
            try:
                return queries.jobs_view(conn, now, f, after, limit)
            except ValueError as err:
                raise ApiProblem("bad_request", f"after: {err}") from None

    return await views.serve(request, JobsView, compute)
