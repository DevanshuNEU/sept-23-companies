"""The job list (#156): `GET /api/jobs` and its two writes.

`GET /api/jobs` is open engineering postings, newest first, one page at a time (`outreach.pipeline.joblist` decides
which and in which order). `POST /api/jobs/{postingId}/pass` hides one posting and never its company;
`POST /api/jobs/{postingId}/go-ahead` adds its company to today's list as his own pick. Each write is one `run_write`.
"""
from fastapi import APIRouter, Query, Request, Response

from outreach.api import views
from outreach.api.db import read_conn
from outreach.api.errors import ApiProblem
from outreach.api.idempotency import run_write
from outreach.api.models.jobs import GoAheadResult, JobSize, JobsView, JobView, JobWindow, JobWriteBody, PassJobResult
from outreach.api.queries import jobs_list as queries
from outreach.api.routers import row_id
from outreach.api.services import jobs as service
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
                raise ApiProblem("bad_request", "That page link is out of date. Reload the list.") from None

    return await views.serve(request, JobsView, compute)


@router.post("/jobs/{posting_id}/pass", response_model=PassJobResult)
def pass_job(body: JobWriteBody, request: Request, posting_id: int = row_id()) -> Response:
    return run_write(request, lambda ctx: service.pass_job(ctx, posting_id))


@router.post("/jobs/{posting_id}/go-ahead", response_model=GoAheadResult)
def go_ahead(body: JobWriteBody, request: Request, posting_id: int = row_id()) -> Response:
    return run_write(request, lambda ctx: service.go_ahead(ctx, posting_id))
