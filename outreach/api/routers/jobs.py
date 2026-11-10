"""The job list (#156): `GET /api/jobs` and its two writes.

`GET /api/jobs` is open engineering postings, newest first, one page at a time (`outreach.pipeline.joblist` decides
which and in which order). `POST /api/jobs/{postingId}/pass` hides one posting and never its company;
`POST /api/jobs/{postingId}/go-ahead` adds its company to today's list as his own pick. Each write is one `run_write`.

Check now (#194): `POST /api/jobs/check-now` records a request and, after the commit, starts the crawl job;
`GET /api/jobs/check` says how the check is going. The API never reads a job board itself.
"""
import logging

from fastapi import APIRouter, Query, Request, Response

from outreach.api import views
from outreach.api.clock import get_clock
from outreach.api.db import read_conn
from outreach.api.errors import ApiProblem
from outreach.api.idempotency import run_write
from outreach.api.models.jobs import (CheckNowResult, CheckStatus, GoAheadResult, JobSize, JobsView, JobView, JobWindow,
                                      JobWriteBody, PassJobResult)
from outreach.api.queries import jobs_list as queries
from outreach.api.routers import row_id
from outreach.api.services import check_now as check_service
from outreach.api.services import jobs as service
from outreach.pipeline.joblist import JobFilters

log = logging.getLogger("outreach.api")
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


@router.post("/jobs/check-now", response_model=CheckNowResult)
def check_now(body: JobWriteBody, request: Request) -> Response:
    """Check now: record the request, then start the crawl job. A request already waiting is not recorded again, but
    the job is started again; a check that is running is only reported. A replayed key starts nothing."""
    kick = []  # filled only when the write ran: a replay reads the stored answer and never reaches the op

    def op(ctx):
        answer, wanted = check_service.request_check(ctx)
        kick.append(wanted)
        return answer

    res = run_write(request, op)
    if not any(kick):
        return res
    try:
        state = request.app.state.kick_crawl()
    except Exception:  # the request is stored; the next crawl tick picks it up
        log.exception("check now: the crawl job could not be started")
        state = "not_live"
    answer = CheckNowResult.model_validate_json(res.body)
    if state == "started" or answer.state != "started":
        return res  # a request that was already waiting stays `running`, whatever the kick said
    answer.state = "not_live"  # recorded, but nothing was started
    return Response(content=answer.model_dump_json(by_alias=True), status_code=res.status_code,
                    media_type="application/json")


@router.get("/jobs/check", response_model=CheckStatus)
def check_status(request: Request) -> CheckStatus:
    """How the check is going: idle, queued, running (with the crawler's counts) or done. Never cached."""
    with read_conn(request) as conn:
        return check_service.read_status(conn, get_clock(request)())
