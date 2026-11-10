"""Check now (#194, design 4.3), inside the caller's `run_write` transaction.

Records one `check_now_requested` event (entity source, actor devanshu) unless a check is already queued or running,
in which case it records nothing and the answer is `running`. The API never reads a job board: the crawl job sees the
request and runs the check (`outreach.crawl.kick` starts it, `outreach.pipeline.check_state` reports it).
"""
from outreach.api.idempotency import WriteCtx
from outreach.api.models.jobs import CheckNowResult, CheckStatus
from outreach.pipeline import check_state
from outreach.store import append_event


def request_check(ctx: WriteCtx) -> CheckNowResult:
    """`started` when this call recorded the request (the caller then starts the crawl job after the commit)."""
    conn = ctx.conn
    current = check_state.status(conn)
    if current.state in ("queued", "running"):
        waiting = check_state.pending_request(conn) if current.state == "queued" else None
        return CheckNowResult(requested_at=waiting or current.started_at or ctx.now, state="running")
    append_event(conn, entity="source", kind=check_state.REQUEST_KIND, actor="devanshu", at=ctx.now,
                 detail={}, idempotency_key=ctx.event_key(0))
    return CheckNowResult(requested_at=ctx.now, state="started")


def read_status(conn) -> CheckStatus:
    return CheckStatus(**vars(check_state.status(conn)))
