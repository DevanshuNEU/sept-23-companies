"""Check now (#194, design 4.3), inside the caller's `run_write` transaction.

Records one `check_now_requested` event (entity source, actor devanshu) unless a check is already queued or running,
in which case it records nothing and the answer is `running`. The API never reads a job board: the crawl job sees the
request and runs the check (`outreach.crawl.kick` starts it, `outreach.pipeline.check_state` reports it).
"""
from outreach.api.idempotency import WriteCtx
from outreach.api.models.jobs import CheckNowResult, CheckStatus
from outreach.pipeline import check_state
from outreach.store import append_event


def request_check(ctx: WriteCtx) -> tuple[CheckNowResult, bool]:
    """The answer and whether the caller starts the crawl job after the commit.

    Nothing queued or running: record the request, answer `started`, start the job. A request already waiting
    (queued: the job may not have started, say the kickstart failed): record nothing, answer `running`, start the job
    again. A check running: record nothing, start nothing, answer `running`.
    """
    conn = ctx.conn
    current = check_state.status(conn, ctx.now)
    if current.state == "running":
        return CheckNowResult(requested_at=current.started_at or ctx.now, state="running"), False
    if current.state == "queued":
        request = check_state.pending_request(conn)
        return CheckNowResult(requested_at=request.at if request else ctx.now, state="running"), True
    append_event(conn, entity="source", kind=check_state.REQUEST_KIND, actor="devanshu", at=ctx.now,
                 detail={}, idempotency_key=ctx.event_key(0))
    return CheckNowResult(requested_at=ctx.now, state="started"), True


def read_status(conn, now: str) -> CheckStatus:
    return CheckStatus(**vars(check_state.status(conn, now)))
