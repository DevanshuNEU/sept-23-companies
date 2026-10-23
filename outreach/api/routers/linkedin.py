"""The LinkedIn list, logging a step he took by hand there, and taking the newest one back."""
from fastapi import APIRouter, Depends, Path, Request, Response

from outreach.api.clock import Clock, get_clock
from outreach.api.db import read_conn
from outreach.api.idempotency import WriteCtx, run_write
from outreach.api.models.linkedin import LinkedInPerson, LinkedInView, LogStepBody, UndoStepBody
from outreach.api.queries import linkedin

router = APIRouter(prefix="/linkedin", tags=["linkedin"])
PersonId = Path(ge=1, le=2**62)  # a longer number cannot name a row, and SQLite would refuse it


@router.get("", response_model=LinkedInView)
def linkedin_view(request: Request, clock: Clock = Depends(get_clock)) -> LinkedInView:
    now = clock()
    with read_conn(request) as conn:
        return linkedin.linkedin_view(conn, now)


@router.post("/{person_id}/log", response_model=LinkedInPerson)
def log_step(body: LogStepBody, request: Request, person_id: int = PersonId) -> Response:
    cfg = request.app.state.config

    def op(ctx: WriteCtx) -> LinkedInPerson:
        return linkedin.log_step(ctx, person_id, body, cfg)

    return run_write(request, op)


@router.post("/{person_id}/undo", response_model=LinkedInPerson)
def undo_step(body: UndoStepBody, request: Request, person_id: int = PersonId) -> Response:
    def op(ctx: WriteCtx) -> LinkedInPerson:
        return linkedin.undo_step(ctx, person_id, body)

    return run_write(request, op)
