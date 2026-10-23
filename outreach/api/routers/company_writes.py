"""The company page's writes: `POST /api/companies/{id}/pause`, `/resume`, `/close` and `/reopen`.

Each runs in `run_write` (one transaction, one idempotency key) and checks the company revision the page loaded.
A merged-away company id writes on its survivor, the company the page shows.
"""
from fastapi import APIRouter, Request, Response

from outreach.api.idempotency import WriteCtx, run_write
from outreach.api.models.company_writes import CloseCompanyBody, CompanyResumed, CompanyRevisionBody, CompanyWritten
from outreach.api.queries import company_writes as writes
from outreach.api.routers import row_id

router = APIRouter(prefix="/companies", tags=["company"])


@router.post("/{company_id}/pause", response_model=CompanyWritten)
def pause_company(body: CompanyRevisionBody, request: Request, company_id: int = row_id()) -> Response:
    def op(ctx: WriteCtx) -> CompanyWritten:
        return writes.pause(ctx, company_id, body.revision)

    return run_write(request, op)


@router.post("/{company_id}/resume", response_model=CompanyResumed)
def resume_company(body: CompanyRevisionBody, request: Request, company_id: int = row_id()) -> Response:
    def op(ctx: WriteCtx) -> CompanyResumed:
        return writes.resume(ctx, company_id, body.revision)

    return run_write(request, op)


@router.post("/{company_id}/close", response_model=CompanyWritten)
def close_company(body: CloseCompanyBody, request: Request, company_id: int = row_id()) -> Response:
    def op(ctx: WriteCtx) -> CompanyWritten:
        return writes.close(ctx, company_id, body.reason, body.note, body.revision)

    return run_write(request, op)


@router.post("/{company_id}/reopen", response_model=CompanyWritten)
def reopen_company(body: CompanyRevisionBody, request: Request, company_id: int = row_id()) -> Response:
    def op(ctx: WriteCtx) -> CompanyWritten:
        return writes.reopen(ctx, company_id, body.revision)

    return run_write(request, op)
