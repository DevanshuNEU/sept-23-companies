"""`GET /api/companies/{id}` (the page) and `/score` (its score breakdown). A merged company answers as its survivor."""
from fastapi import APIRouter, Depends, Request

from outreach.api.clock import Clock, get_clock
from outreach.api.db import read_conn
from outreach.api.errors import ApiProblem
from outreach.api.models.company import CompanyView
from outreach.api.models.score import ScoreView
from outreach.api.queries.company import company_view
from outreach.api.queries.score import score_view
from outreach.api.routers import row_id

router = APIRouter(prefix="/companies", tags=["company"])


@router.get("/{company_id}", response_model=CompanyView)
def company(request: Request, company_id: int = row_id(), clock: Clock = Depends(get_clock)) -> CompanyView:
    now = clock()
    with read_conn(request) as conn:
        view = company_view(conn, company_id, now)
    if view is None:
        raise ApiProblem("not_found", f"There is no company {company_id}.")
    return view


@router.get("/{company_id}/score", response_model=ScoreView)
def company_score(request: Request, company_id: int = row_id(), clock: Clock = Depends(get_clock)) -> ScoreView:
    now = clock()
    with read_conn(request) as conn:
        view = score_view(conn, company_id, now)
    if view is None:
        raise ApiProblem("not_found", f"There is no score for company {company_id}.")
    return view
