"""The prompt, outcome answers and corrections, a touch's history, and touches he logs himself."""
from fastapi import APIRouter, Depends, Request, Response

from outreach.api.clock import Clock, get_clock
from outreach.api.db import read_conn
from outreach.api.errors import ApiProblem
from outreach.api.idempotency import WriteCtx, run_write
from outreach.api.models.outcomes import (
    CorrectionBody, LogTouchBody, OutcomeBody, OutcomeKind, OutcomePromptView, TouchHistory, TouchLogged,
    TouchRevision,
)
from outreach.api.queries import outcomes

router = APIRouter(tags=["outcomes"])


@router.get("/outcomes/prompt", response_model=OutcomePromptView)
def outcome_prompt(request: Request, clock: Clock = Depends(get_clock)) -> OutcomePromptView:
    now = clock()
    with read_conn(request) as conn:
        return outcomes.prompt_view(conn, now, request.app.state.config)


@router.post("/touches/{touch_id}/outcomes", response_model=TouchRevision)
def log_outcome(touch_id: int, body: OutcomeBody, request: Request) -> Response:
    cfg = request.app.state.config

    def op(ctx: WriteCtx) -> TouchRevision:
        return TouchRevision(revision=outcomes.answer(ctx, touch_id, body, cfg))

    return run_write(request, op)


@router.put("/touches/{touch_id}/outcomes/{kind}", response_model=TouchRevision)
def correct_outcome(touch_id: int, kind: OutcomeKind, body: CorrectionBody, request: Request) -> Response:
    def op(ctx: WriteCtx) -> TouchRevision:
        return TouchRevision(revision=outcomes.correct(ctx, touch_id, kind, body))

    return run_write(request, op)


@router.get("/touches/{touch_id}/history", response_model=TouchHistory)
def touch_history(touch_id: int, request: Request) -> TouchHistory:
    with read_conn(request) as conn:
        history = outcomes.touch_history(conn, touch_id)
    if history is None:
        raise ApiProblem("not_found", f"There is no touch {touch_id}.")
    return history


@router.post("/touches", response_model=TouchLogged)
def log_touch(body: LogTouchBody, request: Request) -> Response:
    def op(ctx: WriteCtx) -> TouchLogged:
        return outcomes.log_own_touch(ctx, body)

    return run_write(request, op)
