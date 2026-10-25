"""The lean Today (until #15's daily plan): `GET /api/today` and the writes on its rows.

`POST /api/companies/{id}/pick`, `/unpick`, `/backlog`, `/remove` and `/restore`, each in `run_write` against the
company's revision. See `outreach.api.queries.today` for how the list is made. On a day the people plan built,
pick, unpick and backlog are `not_allowed` (the rows are the plan's); remove stays, and restore stays without
touching the plan's rows.
"""
from fastapi import APIRouter, Depends, Request, Response

from outreach.api.clock import Clock, get_clock
from outreach.api.db import read_conn
from outreach.api.idempotency import WriteCtx, run_write
from outreach.api.models.base import Empty
from outreach.api.models.company_writes import CompanyRevisionBody
from outreach.api.models.today import DailyEntry, RemoveCompanyBody, RestoreCompanyBody, TodayView
from outreach.api.queries import today as queries
from outreach.api.routers import row_id

router = APIRouter(tags=["today"])


@router.get("/today", response_model=TodayView)
def today(request: Request, clock: Clock = Depends(get_clock)) -> TodayView:
    now = clock()
    old_sender = request.app.state.old_sender_loaded()  # outside any transaction: it may ask launchctl
    cfg = request.app.state.config
    queries.ensure_list(request.app.state.settings, now, cfg, old_sender_loaded=old_sender)
    with read_conn(request) as conn:
        return queries.today_view(conn, now, cfg, old_sender_loaded=old_sender)


@router.post("/companies/{company_id}/pick", response_model=DailyEntry)
def pick_company(body: CompanyRevisionBody, request: Request, company_id: int = row_id()) -> Response:
    def op(ctx: WriteCtx) -> DailyEntry:
        return queries.pick(ctx, company_id, body.revision)

    return run_write(request, op)


@router.post("/companies/{company_id}/unpick", response_model=DailyEntry)
def unpick_company(body: CompanyRevisionBody, request: Request, company_id: int = row_id()) -> Response:
    def op(ctx: WriteCtx) -> DailyEntry:
        return queries.unpick(ctx, company_id, body.revision)

    return run_write(request, op)


@router.post("/companies/{company_id}/backlog", response_model=DailyEntry)
def backlog_company(body: CompanyRevisionBody, request: Request, company_id: int = row_id()) -> Response:
    def op(ctx: WriteCtx) -> DailyEntry:
        return queries.backlog(ctx, company_id, body.revision)

    return run_write(request, op)


@router.post("/companies/{company_id}/remove", response_model=Empty)
def remove_company(body: RemoveCompanyBody, request: Request, company_id: int = row_id()) -> Response:
    def op(ctx: WriteCtx) -> Empty:
        return queries.remove(ctx, company_id, body.reason, body.note, body.revision)

    return run_write(request, op)


@router.post("/companies/{company_id}/restore", response_model=Empty)
def restore_company(body: RestoreCompanyBody, request: Request, company_id: int = row_id()) -> Response:
    def op(ctx: WriteCtx) -> Empty:
        return queries.restore(ctx, company_id, body.reason, body.revision)

    return run_write(request, op)
