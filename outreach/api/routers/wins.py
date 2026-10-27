"""`GET /api/wins` and `POST /api/wins/seen`: the win log, and marking wins seen (a win is never lost until then)."""
from fastapi import APIRouter, Request, Response

from outreach.api import views
from outreach.api.db import read_conn
from outreach.api.idempotency import WriteCtx, run_write
from outreach.api.models.progress import WinsSeen, WinsSeenBody, WinsView
from outreach.api.queries import progress

router = APIRouter(prefix="/wins", tags=["wins"])


@router.get("", response_model=WinsView)
async def wins_view(request: Request) -> Response:
    def compute(now: str, _) -> WinsView:
        with read_conn(request) as conn:
            return progress.wins_view(conn, now)

    return await views.serve(request, WinsView, compute)


@router.post("/seen", response_model=WinsSeen)
def mark_seen(body: WinsSeenBody, request: Request) -> Response:
    def op(ctx: WriteCtx) -> WinsSeen:
        return progress.mark_seen(ctx, body.win_ids)

    return run_write(request, op)
