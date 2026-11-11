"""`GET /api/applied` (#218): every posting he applied to, newest first, with its job link."""
from fastapi import APIRouter, Request, Response

from outreach.api import views
from outreach.api.db import read_conn
from outreach.api.models.applied import AppliedView
from outreach.api.queries import applied

router = APIRouter(tags=["applied"])


@router.get("/applied", response_model=AppliedView)
async def applied_view(request: Request) -> Response:
    def compute(now: str, _) -> AppliedView:
        with read_conn(request) as conn:
            return applied.applied_view(conn, now)

    return await views.serve(request, AppliedView, compute)
