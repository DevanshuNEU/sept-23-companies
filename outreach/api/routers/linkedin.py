"""The LinkedIn list, logging a step he took by hand there, and taking the newest one back."""
from fastapi import APIRouter, Request, Response

from outreach.api import views
from outreach.api.db import read_conn
from outreach.api.idempotency import WriteCtx, run_write
from outreach.api.models.linkedin import LinkedInPerson, LinkedInView, LogStepBody, UndoStepBody
from outreach.api.queries import linkedin
from outreach.api.routers import row_id

router = APIRouter(prefix="/linkedin", tags=["linkedin"])


@router.get("", response_model=LinkedInView)
async def linkedin_view(request: Request) -> Response:
    def compute(now: str, _) -> LinkedInView:
        with read_conn(request) as conn:
            return linkedin.linkedin_view(conn, now)

    return await views.serve(request, LinkedInView, compute)


@router.post("/{person_id}/log", response_model=LinkedInPerson)
def log_step(body: LogStepBody, request: Request, person_id: int = row_id()) -> Response:
    cfg = request.app.state.config

    def op(ctx: WriteCtx) -> LinkedInPerson:
        return linkedin.log_step(ctx, person_id, body, cfg)

    return run_write(request, op)


@router.post("/{person_id}/undo", response_model=LinkedInPerson)
def undo_step(body: UndoStepBody, request: Request, person_id: int = row_id()) -> Response:
    def op(ctx: WriteCtx) -> LinkedInPerson:
        return linkedin.undo_step(ctx, person_id, body)

    return run_write(request, op)
