"""The pipeline's one write: `POST /api/pipeline/misses`, a job link Devanshu pasted because Knock did not show it."""
from fastapi import APIRouter, Request, Response

from outreach.api.idempotency import run_write
from outreach.api.models.pipeline import MissBody, MissResult
from outreach.api.services import pipeline

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.post("/misses", response_model=MissResult)
def log_miss(body: MissBody, request: Request) -> Response:
    return run_write(request, lambda ctx: pipeline.log_miss(ctx, body))
