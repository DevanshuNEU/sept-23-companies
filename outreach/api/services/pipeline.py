"""The miss log's write, inside the caller's `run_write` transaction (#153)."""
from outreach.api.errors import ApiProblem
from outreach.api.idempotency import WriteCtx
from outreach.api.models.pipeline import MissBody, MissResult
from outreach.pipeline.misses import log_miss as store_miss


def log_miss(ctx: WriteCtx, body: MissBody) -> MissResult:
    """Store the pasted link once with its first cause; the same link again answers with the first row."""
    if not body.url.strip():
        raise ApiProblem("bad_request", "Paste the job link. Nothing was saved.")
    miss = store_miss(ctx.conn, body.url, body.note, ctx.now)
    return MissResult(miss_id=miss.miss_id, cause=miss.cause, cause_text=miss.cause_text)
