"""Needs you: `GET /api/needs-you`, `POST /api/review/{id}/resolve` (ok, edit, no-go or merge) and
`POST /api/companies/{id}/unmerge`."""
from fastapi import APIRouter, Depends, Path, Request, Response

from outreach.api.clock import Clock, get_clock
from outreach.api.db import read_conn
from outreach.api.idempotency import WriteCtx, run_write
from outreach.api.models.needs_you import NeedsYouView, ResolveReviewBody, ReviewResolved, UnmergeBody, Unmerged
from outreach.api.queries import needs_you as queries

router = APIRouter(tags=["needs-you"])
MAX_ID = 2**62  # a longer number cannot name a row, and SQLite would refuse it


@router.get("/needs-you", response_model=NeedsYouView)
def needs_you(request: Request, clock: Clock = Depends(get_clock)) -> NeedsYouView:
    now = clock()
    with read_conn(request) as conn:
        return queries.needs_you_view(conn, now)


@router.post("/review/{review_id}/resolve", response_model=ReviewResolved)
def resolve_review(body: ResolveReviewBody, request: Request, review_id: int = Path(ge=1, le=MAX_ID)) -> Response:
    def op(ctx: WriteCtx) -> ReviewResolved:
        return queries.resolve_review(ctx, review_id, body)

    return run_write(request, op)


@router.post("/companies/{company_id}/unmerge", response_model=Unmerged)
def unmerge_company(body: UnmergeBody, request: Request, company_id: int = Path(ge=1, le=MAX_ID)) -> Response:
    def op(ctx: WriteCtx) -> Unmerged:
        return Unmerged(revision=queries.unmerge_company(ctx, company_id, body.revision))

    return run_write(request, op)
