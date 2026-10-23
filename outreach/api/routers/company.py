"""`GET /api/companies/{id}`: the company page. A merged company answers as its survivor."""
from fastapi import APIRouter, Depends, Request

from outreach.api.clock import Clock, get_clock
from outreach.api.db import read_conn
from outreach.api.errors import ApiProblem
from outreach.api.models.company import CompanyView
from outreach.api.queries.company import company_view

router = APIRouter(prefix="/companies", tags=["company"])


@router.get("/{company_id}", response_model=CompanyView)
def company(company_id: int, request: Request, clock: Clock = Depends(get_clock)) -> CompanyView:
    now = clock()
    with read_conn(request) as conn:
        view = company_view(conn, company_id, now)
    if view is None:
        raise ApiProblem("not_found", f"There is no company {company_id}.")
    return view
