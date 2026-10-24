"""The No-go list: `GET /api/no-go` and `POST /api/no-go/{id}/lift`."""
from fastapi import APIRouter, Depends, Request, Response

from outreach.api.clock import Clock, get_clock
from outreach.api.db import read_conn
from outreach.api.idempotency import WriteCtx, run_write
from outreach.api.models.base import Empty
from outreach.api.models.no_go import LiftNoGoBody, NoGoView
from outreach.api.queries import no_go
from outreach.api.routers import row_id

router = APIRouter(prefix="/no-go", tags=["no-go"])


@router.get("", response_model=NoGoView)
def no_go_view(request: Request, clock: Clock = Depends(get_clock)) -> NoGoView:
    now = clock()
    with read_conn(request) as conn:
        return no_go.no_go_view(conn, now)


@router.post("/{no_go_id}/lift", response_model=Empty)
def lift_no_go(body: LiftNoGoBody, request: Request, no_go_id: int = row_id()) -> Response:
    def op(ctx: WriteCtx) -> Empty:
        return no_go.lift(ctx, no_go_id, body.reason)

    return run_write(request, op)
