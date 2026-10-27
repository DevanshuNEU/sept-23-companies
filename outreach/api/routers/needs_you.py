"""Needs you: `GET /api/needs-you`, `POST /api/review/{id}/resolve` (ok, edit, no-go or merge),
`POST /api/review/{id}/reopen` (take an ok, a no-go or a merge back) and `POST /api/companies/{id}/unmerge`. The writes
are `outreach.review`'s, inside `run_write`."""
from fastapi import APIRouter, Request, Response

from outreach.api import views
from outreach.api.db import read_conn
from outreach.api.idempotency import WriteCtx, run_write
from outreach.api.models.needs_you import (
    NeedsYouView, ReopenReviewBody, Reopened, ResolveReviewBody, ReviewResolved, UnmergeBody, Unmerged,
)
from outreach.api.queries import needs_you as queries
from outreach.api.routers import row_id

router = APIRouter(tags=["needs-you"])


@router.get("/needs-you", response_model=NeedsYouView)
async def needs_you(request: Request) -> Response:
    def compute(now: str, _) -> NeedsYouView:
        with read_conn(request) as conn:
            return queries.needs_you_view(conn, now)

    return await views.serve(request, NeedsYouView, compute)


@router.post("/review/{review_id}/resolve", response_model=ReviewResolved)
def resolve_review(body: ResolveReviewBody, request: Request, review_id: int = row_id()) -> Response:
    def op(ctx: WriteCtx) -> ReviewResolved:
        return queries.resolve_review(ctx, review_id, body)

    return run_write(request, op)


@router.post("/review/{review_id}/reopen", response_model=Reopened)
def reopen_review(body: ReopenReviewBody, request: Request, review_id: int = row_id()) -> Response:
    def op(ctx: WriteCtx) -> Reopened:
        return Reopened(revision=queries.reopen_review(ctx, review_id, body.revision))

    return run_write(request, op)


@router.post("/companies/{company_id}/unmerge", response_model=Unmerged)
def unmerge_company(body: UnmergeBody, request: Request, company_id: int = row_id()) -> Response:
    def op(ctx: WriteCtx) -> Unmerged:
        return Unmerged(revision=queries.unmerge_company(ctx, company_id, body.revision))

    return run_write(request, op)
