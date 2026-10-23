"""`GET /api/watch` and `POST /api/watch/{id}/dismiss`: the watch list."""
from fastapi import APIRouter, Depends, Request, Response

from outreach.api.clock import Clock, get_clock
from outreach.api.db import read_conn
from outreach.api.idempotency import WriteCtx, run_write
from outreach.api.models.watch import WatchDismissed, WatchView
from outreach.api.queries import watch
from outreach.api.routers import row_id

router = APIRouter(prefix="/watch", tags=["watch"])


@router.get("", response_model=WatchView)
def watch_list(request: Request, clock: Clock = Depends(get_clock)) -> WatchView:
    now = clock()
    with read_conn(request) as conn:
        return watch.watch_view(conn, now)


@router.post("/{item_id}/dismiss", response_model=WatchDismissed)
def dismiss(request: Request, item_id: int = row_id()) -> Response:
    def op(ctx: WriteCtx) -> WatchDismissed:
        watch.dismiss_item(ctx, item_id)
        return WatchDismissed(id=item_id)

    return run_write(request, op)
