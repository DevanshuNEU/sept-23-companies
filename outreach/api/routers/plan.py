"""The people plan (#15): `GET /api/plan`. See `outreach.api.queries.plan` for how each row is derived."""
from fastapi import APIRouter, Depends, Request

from outreach.api.clock import Clock, get_clock
from outreach.api.db import read_conn
from outreach.api.models.plan import PlanView
from outreach.api.queries import plan as queries
from outreach.plan.config import default_plan_config

router = APIRouter(tags=["plan"])


@router.get("/plan", response_model=PlanView)
def plan(request: Request, clock: Clock = Depends(get_clock)) -> PlanView:
    now = clock()
    with read_conn(request) as conn:
        return queries.plan_view(conn, now, default_plan_config())
