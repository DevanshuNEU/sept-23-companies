"""A person's writes: `POST /api/people/{id}/role-kind`. (The people add and edit come with #15's `add_candidate`.)"""
from fastapi import APIRouter, Request, Response

from outreach.api.idempotency import WriteCtx, run_write
from outreach.api.models.company import PersonRef
from outreach.api.models.company_writes import RoleKindBody
from outreach.api.queries import company_writes as writes
from outreach.api.routers import row_id
from outreach.drafts.checks import load_banned

router = APIRouter(prefix="/people", tags=["company"])


@router.post("/{person_id}/role-kind", response_model=PersonRef)
def set_role_kind(body: RoleKindBody, request: Request, person_id: int = row_id()) -> Response:
    # Read before the transaction opens; an unusable list is [] and the checks block on it (fail closed).
    banned = load_banned(request.app.state.settings.banned_phrases)[0]

    def op(ctx: WriteCtx) -> PersonRef:
        return writes.set_role_kind(ctx, person_id, body.kind, body.revision, banned)

    return run_write(request, op)
