"""The people plan (#15): `GET /api/plan` and the writes on its rows and notes.

`POST /api/plan/{entryId}/request`, `/apply` and `/skip` answer with the row as the plan shows it; `PUT
/api/notes/{noteId}` and `POST /api/notes/{noteId}/confirm` answer with the note. Each is one `run_write`. See
`outreach.api.queries.plan` for how a row is derived and `outreach.api.services.plan` for the writes.
"""
from fastapi import APIRouter, Depends, Request, Response

from outreach.api.clock import Clock, get_clock
from outreach.api.db import read_conn
from outreach.api.idempotency import run_write
from outreach.api.models.plan import NoteDraft, PlanRow, PlanView
from outreach.api.models.plan_writes import (
    ConfirmReasonBody, LogAppliedBody, LogRequestBody, SaveNoteBody, SkipRowBody,
)
from outreach.api.queries import plan as queries
from outreach.api.routers import row_id
from outreach.api.services import plan as service
from outreach.drafts.checks import load_banned
from outreach.plan.config import default_plan_config

router = APIRouter(tags=["plan"])


@router.get("/plan", response_model=PlanView)
def plan(request: Request, clock: Clock = Depends(get_clock)) -> PlanView:
    now = clock()
    with read_conn(request) as conn:
        return queries.plan_view(conn, now, default_plan_config())


def _banned(request: Request, cfg) -> list:
    """The note scopes of the banned list the API is configured with (read outside the write's transaction). An
    unusable list comes back empty, and #15's `check_note` blocks every note on that."""
    return load_banned(request.app.state.settings.banned_phrases, scopes=tuple(cfg.get("note", "banned_scopes")))[0]


@router.post("/plan/{entry_id}/request", response_model=PlanRow)
def log_request(body: LogRequestBody, request: Request, entry_id: int = row_id()) -> Response:
    cfg = default_plan_config()
    return run_write(request, lambda ctx: service.log_request(ctx, entry_id, body, cfg))


@router.post("/plan/{entry_id}/apply", response_model=PlanRow)
def log_applied(body: LogAppliedBody, request: Request, entry_id: int = row_id()) -> Response:
    cfg = default_plan_config()
    return run_write(request, lambda ctx: service.log_applied(ctx, entry_id, body, cfg))


@router.post("/plan/{entry_id}/skip", response_model=PlanRow)
def skip_row(body: SkipRowBody, request: Request, entry_id: int = row_id()) -> Response:
    cfg = default_plan_config()
    return run_write(request, lambda ctx: service.skip_row(ctx, entry_id, body.revision, cfg))


@router.put("/notes/{note_id}", response_model=NoteDraft)
def save_note(body: SaveNoteBody, request: Request, note_id: int = row_id()) -> Response:
    cfg = default_plan_config()
    banned = _banned(request, cfg)
    return run_write(request, lambda ctx: service.save_note(ctx, note_id, body.text, body.reason_line, body.revision,
                                                            cfg, banned))


@router.post("/notes/{note_id}/confirm", response_model=NoteDraft)
def confirm_reason(body: ConfirmReasonBody, request: Request, note_id: int = row_id()) -> Response:
    cfg = default_plan_config()
    banned = _banned(request, cfg)
    return run_write(request, lambda ctx: service.confirm_note(ctx, note_id, body.revision, cfg, banned))
