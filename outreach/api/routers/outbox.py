"""The Outbox: its reads (`GET /api/outbox`, `/api/outbox/compare`, `/api/outbox/{touchId}`) and its writes
(`POST /api/messages/{id}/save`, `/approve`, `/unapprove`, `/skip`, `POST /api/touches/{id}/reply-kind`).

Every write runs in `run_write` and answers with the thread's `OutboxDetail`. Approve is the one route that makes a
message `approved` (#9 safety blocks enforced here, never only in the page): the mail server is looked up before any
transaction, the checks run on current data inside it, and a refusal stores the fresh checks in a second short
transaction. Whether the old mailer is loaded is asked before any transaction opens (it may run `launchctl`).
"""
import sqlite3

from fastapi import APIRouter, Depends, Query, Request, Response

from outreach.api import mx
from outreach.api.clock import Clock, get_clock
from outreach.api.db import busy_problem, connect_for, is_busy, read_conn
from outreach.api.errors import ApiProblem
from outreach.api.idempotency import WriteCtx, run_write
from outreach.api.models.outbox import DraftCompare, OutboxDetail, OutboxView
from outreach.api.models.outbox_writes import MessageRevisionBody, ReplyKindBody, SaveMessageBody
from outreach.api.queries import outbox as queries
from outreach.api.routers import MAX_ID, row_id
from outreach.api.services import outbox as service
from outreach.drafts.checks import load_banned
from outreach.sending.config import load_config
from outreach.store import transaction

router = APIRouter(tags=["outbox"])


@router.get("/outbox", response_model=OutboxView)
def outbox(request: Request, clock: Clock = Depends(get_clock)) -> OutboxView:
    now, cfg = clock(), load_config()
    old_sender = request.app.state.old_sender_loaded()
    with read_conn(request) as conn:
        return queries.outbox_view(conn, now, cfg, old_sender_loaded=old_sender)


# Declared before /outbox/{touch_id}, which would otherwise take "compare" as an id.
@router.get("/outbox/compare", response_model=DraftCompare)
def compare_drafts(request: Request, message_id: int = Query(alias="messageId", ge=1, le=MAX_ID),
                   other_message_id: int = Query(alias="otherMessageId", ge=1, le=MAX_ID)) -> DraftCompare:
    with read_conn(request) as conn:
        return queries.compare(conn, message_id, other_message_id)


@router.get("/outbox/{touch_id}", response_model=OutboxDetail)
def outbox_detail(request: Request, touch_id: int = row_id(), clock: Clock = Depends(get_clock)) -> OutboxDetail:
    now, cfg = clock(), load_config()
    old_sender = request.app.state.old_sender_loaded()
    with read_conn(request) as conn:
        detail = queries.outbox_detail(conn, touch_id, now, cfg, old_sender_loaded=old_sender)
    if detail is None:
        raise ApiProblem("not_found", f"There is no email thread {touch_id}.")
    return detail


# ---------------------------------------------------------------------------------------------- writes

def _write(request: Request, change) -> Response:
    """`run_write` around `change(ctx) -> touch id`, answering with that thread as the list now shows it."""
    cfg, old_sender = load_config(), request.app.state.old_sender_loaded()

    def op(ctx: WriteCtx) -> OutboxDetail:
        touch_id = change(ctx)
        detail = queries.outbox_detail(ctx.conn, touch_id, ctx.now, cfg, old_sender_loaded=old_sender)
        if detail is None:
            raise ApiProblem("not_found", f"There is no email thread {touch_id}.")
        return detail

    return run_write(request, op)


def _banned(request: Request) -> list:
    """Read before the transaction opens; an unusable list is [] and the checks block on it (fail closed)."""
    return load_banned(request.app.state.settings.banned_phrases)[0]


@router.post("/messages/{message_id}/save", response_model=OutboxDetail)
def save_message(body: SaveMessageBody, request: Request, message_id: int = row_id()) -> Response:
    banned = _banned(request)
    return _write(request, lambda ctx: service.save_message(ctx, message_id, body.subject, body.body, body.revision,
                                                            banned))


@router.post("/messages/{message_id}/approve", response_model=OutboxDetail)
def approve(body: MessageRevisionBody, request: Request, message_id: int = row_id(),
            clock: Clock = Depends(get_clock)) -> Response:
    state = request.app.state
    with read_conn(request) as conn:
        domain = service.mail_domain_of(conn, message_id)
    if domain is not None:  # outside any transaction: a slow lookup never holds the store
        mx.ensure(state.settings, connect_for, domain, clock(), resolvers=state.mx_resolvers)
    banned = _banned(request)
    try:
        return _write(request, lambda ctx: service.approve_message(ctx, message_id, body.revision, banned))
    except service.Blocked as blocked:
        _save_checks(request, blocked)
        raise ApiProblem("blocked", blocked.message, checks=blocked.checks) from None


def _save_checks(request: Request, blocked: service.Blocked) -> None:
    """The refused approve's second short transaction, so the next read shows what approve saw. A busy store is the
    `busy` envelope (try again); nothing else was written."""
    conn = connect_for(request.app.state.settings)
    try:
        with transaction(conn):
            service.save_blocked_checks(conn, blocked)
    except sqlite3.OperationalError as err:
        if is_busy(err):
            raise busy_problem() from err
        raise
    finally:
        conn.close()


@router.post("/messages/{message_id}/unapprove", response_model=OutboxDetail)
def unapprove(body: MessageRevisionBody, request: Request, message_id: int = row_id()) -> Response:
    return _write(request, lambda ctx: service.unapprove_message(ctx, message_id, body.revision))


@router.post("/messages/{message_id}/skip", response_model=OutboxDetail)
def skip(body: MessageRevisionBody, request: Request, message_id: int = row_id()) -> Response:
    return _write(request, lambda ctx: service.skip_message(ctx, message_id, body.revision))


@router.post("/touches/{touch_id}/reply-kind", response_model=OutboxDetail)
def set_reply_kind(body: ReplyKindBody, request: Request, touch_id: int = row_id()) -> Response:
    return _write(request, lambda ctx: service.set_reply_kind(ctx, touch_id, body.kind, body.revision))
