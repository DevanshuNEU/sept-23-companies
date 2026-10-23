"""The Outbox reads: `GET /api/outbox`, `GET /api/outbox/compare` and `GET /api/outbox/{touchId}`.

Its writes (save, approve, unapprove, skip, the reply kind) are #11 Task 19. Whether the old mailer is loaded is
asked before the read opens (it may run `launchctl`).
"""
from fastapi import APIRouter, Depends, Query, Request

from outreach.api.clock import Clock, get_clock
from outreach.api.db import read_conn
from outreach.api.errors import ApiProblem
from outreach.api.models.outbox import DraftCompare, OutboxDetail, OutboxView
from outreach.api.queries import outbox as queries
from outreach.api.routers import MAX_ID, row_id
from outreach.sending.config import load_config

router = APIRouter(prefix="/outbox", tags=["outbox"])


@router.get("", response_model=OutboxView)
def outbox(request: Request, clock: Clock = Depends(get_clock)) -> OutboxView:
    now, cfg = clock(), load_config()
    old_sender = request.app.state.old_sender_loaded()
    with read_conn(request) as conn:
        return queries.outbox_view(conn, now, cfg, old_sender_loaded=old_sender)


# Declared before /{touch_id}, which would otherwise take "compare" as an id.
@router.get("/compare", response_model=DraftCompare)
def compare_drafts(request: Request, message_id: int = Query(alias="messageId", ge=1, le=MAX_ID),
                   other_message_id: int = Query(alias="otherMessageId", ge=1, le=MAX_ID)) -> DraftCompare:
    with read_conn(request) as conn:
        return queries.compare(conn, message_id, other_message_id)


@router.get("/{touch_id}", response_model=OutboxDetail)
def outbox_detail(request: Request, touch_id: int = row_id(), clock: Clock = Depends(get_clock)) -> OutboxDetail:
    now, cfg = clock(), load_config()
    old_sender = request.app.state.old_sender_loaded()
    with read_conn(request) as conn:
        detail = queries.outbox_detail(conn, touch_id, now, cfg, old_sender_loaded=old_sender)
    if detail is None:
        raise ApiProblem("not_found", f"There is no email thread {touch_id}.")
    return detail
