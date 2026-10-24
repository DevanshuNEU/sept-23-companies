"""The sending strip and the global pause: `GET /api/sending`, `POST /api/sending/pause` and `/resume`.

Both writes check `SendingStatus.revision` (the `sender` row's) and answer with the status after the write. Whether
the old mailer is loaded is asked before any transaction opens (it may run `launchctl`).
"""
from fastapi import APIRouter, Depends, Request, Response

from outreach.api.clock import Clock, get_clock
from outreach.api.db import read_conn
from outreach.api.idempotency import WriteCtx, run_write
from outreach.api.models.outbox import PauseSendingBody, ResumeSendingBody, SendingStatus
from outreach.api.queries import sending as queries
from outreach.sending.config import load_config

router = APIRouter(prefix="/sending", tags=["sending"])


@router.get("", response_model=SendingStatus)
def sending_status(request: Request, clock: Clock = Depends(get_clock)) -> SendingStatus:
    now, cfg = clock(), load_config()
    old_sender = request.app.state.old_sender_loaded()
    with read_conn(request) as conn:
        return queries.sending_status(conn, now, cfg, old_sender_loaded=old_sender)


@router.post("/pause", response_model=SendingStatus)
def pause_sending(body: PauseSendingBody, request: Request) -> Response:
    cfg, old_sender = load_config(), request.app.state.old_sender_loaded()

    def op(ctx: WriteCtx) -> SendingStatus:
        return queries.pause_sending(ctx, body.revision, cfg, old_sender_loaded=old_sender)

    return run_write(request, op)


@router.post("/resume", response_model=SendingStatus)
def resume_sending(body: ResumeSendingBody, request: Request) -> Response:
    cfg, old_sender = load_config(), request.app.state.old_sender_loaded()

    def op(ctx: WriteCtx) -> SendingStatus:
        return queries.resume_sending(ctx, body.revision, body.acknowledge_block, cfg, old_sender_loaded=old_sender)

    return run_write(request, op)
