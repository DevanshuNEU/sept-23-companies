"""The pipeline: `GET /api/pipeline/scorecard` (the newest stored card) and its one write, `POST /api/pipeline/misses`,
a job link Devanshu pasted because Knock did not show it."""
from fastapi import APIRouter, Request, Response

from outreach.api import views
from outreach.api.db import read_conn
from outreach.api.idempotency import run_write
from outreach.api.models.pipeline import MissBody, MissResult, ScorecardView
from outreach.api.queries import pipeline as pipeline_queries
from outreach.api.services import pipeline

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.post("/misses", response_model=MissResult)
def log_miss(body: MissBody, request: Request) -> Response:
    return run_write(request, lambda ctx: pipeline.log_miss(ctx, body))


@router.get("/scorecard", response_model=ScorecardView)
async def scorecard(request: Request) -> Response:
    def compute(now: str, _) -> ScorecardView:
        with read_conn(request) as conn:
            return pipeline_queries.scorecard_view(conn)

    return await views.serve(request, ScorecardView, compute)
