"""The company page's writes: `POST /api/companies/{id}/pause`, `/resume`, `/release-hold`, `/close` and `/reopen`,
and `/reached/clear`, the undo of Done on the job list (#195).

Each runs in `run_write` (one transaction, one idempotency key). All but `/reached/clear` check the company revision
the page loaded: his reached mark is an event and never changes the company row. A merged-away company id writes on
its survivor, the company the page shows.
"""
from fastapi import APIRouter, Request, Response

from outreach.api.idempotency import WriteCtx, run_write
from outreach.api.models.company_writes import CloseCompanyBody, CompanyResumed, CompanyRevisionBody, CompanyWritten
from outreach.api.models.jobs import ClearDoneResult, JobWriteBody
from outreach.api.queries import company_writes as writes
from outreach.api.routers import row_id
from outreach.api.services import jobs as jobs_service

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


@router.post("/{company_id}/release-hold", response_model=CompanyResumed)
def release_hold(body: CompanyRevisionBody, request: Request, company_id: int = row_id()) -> Response:
    def op(ctx: WriteCtx) -> CompanyResumed:
        return writes.release_hold(ctx, company_id, body.revision)

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


@router.post("/{company_id}/reached/clear", response_model=ClearDoneResult)
def clear_reached(body: JobWriteBody, request: Request, company_id: int = row_id()) -> Response:
    """Take back his Done mark. A company with no mark answers `cleared: false` and writes nothing."""
    return run_write(request, lambda ctx: jobs_service.clear_done(ctx, company_id))
