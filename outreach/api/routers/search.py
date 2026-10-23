"""`GET /api/search/index`, `/api/search/postings?q=` and `/api/search/companies?q=`."""
from fastapi import APIRouter, Depends, Request

from outreach.api.clock import Clock, get_clock
from outreach.api.db import read_conn
from outreach.api.models.search import PostingHit, SearchCompany, SearchIndex
from outreach.api.queries import search

router = APIRouter(prefix="/search", tags=["search"])


@router.get("/index", response_model=SearchIndex)
def search_index(request: Request, clock: Clock = Depends(get_clock)) -> SearchIndex:
    now = clock()
    with read_conn(request) as conn:
        return search.index(conn, now)


@router.get("/postings", response_model=list[PostingHit])
def search_postings(request: Request, q: str = "") -> list[PostingHit]:
    with read_conn(request) as conn:
        return search.find_postings(conn, q)


@router.get("/companies", response_model=list[SearchCompany])
def search_companies(request: Request, q: str = "") -> list[SearchCompany]:
    with read_conn(request) as conn:
        return search.find_companies(conn, q)
