"""`GET /api/done`: companies closed with an interview or a referral, and the reply metrics."""
from fastapi import APIRouter, Depends, Request

from outreach.api.clock import Clock, get_clock
from outreach.api.db import read_conn
from outreach.api.models.done import DoneView
from outreach.api.queries import done

router = APIRouter(tags=["done"])


@router.get("/done", response_model=DoneView)
def done_view(request: Request, clock: Clock = Depends(get_clock)) -> DoneView:
    now = clock()
    with read_conn(request) as conn:
        return done.done_view(conn, now)
