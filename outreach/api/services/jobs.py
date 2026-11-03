"""Pass and Go ahead on the job list (#156), each inside the caller's `run_write` transaction.

Pass hides one posting: a `posting_passed` event (entity posting, actor devanshu, detail `posting_id` and
`company_id`), which `joblist.passed_posting_ids` and `fresh.load_passed_postings` read. It is never a Not now: the
company is untouched and `fresh.load_passes` (the cooldown) does not read it. Passing a posting twice writes once.

Go ahead (`outreach.plan.picks.add_own_pick`) puts the posting's company on today's list as his own pick, or, before
the 08:00 plan is built, records the pick for the plan job. The company's size, the 7 day room and a Not now cooldown
never stop it; a no-go company, a company already on today's list and one with someone contacted and waiting do, in a
plain sentence. The same key again answers with the first answer and writes nothing (`run_write`).
"""
import sqlite3
from datetime import date

from outreach.api.errors import ApiProblem
from outreach.api.idempotency import WriteCtx
from outreach.api.models.jobs import GoAheadResult, PassJobResult
from outreach.api.queries.company import merge_parents, survivor_of
from outreach.api.queries.outcomes import NOT_SAVED
from outreach.outcomes.windows import ny_day
from outreach.pipeline.joblist import passed_posting_ids
from outreach.plan.picks import OwnPickRefused, add_own_pick
from outreach.store import append_event

PASSED = "posting_passed"  # event kind: Pass on the job list


def _posting(conn: sqlite3.Connection, posting_id: int) -> tuple[int, str]:
    """The posting's company as it is after any merge, and the company's name."""
    row = conn.execute("SELECT company_id FROM posting WHERE posting_id = ?", (posting_id,)).fetchone()
    if row is None:
        raise ApiProblem("not_found", f"There is no posting {posting_id}.")
    survivor = survivor_of(merge_parents(conn), row["company_id"])
    return survivor, conn.execute("SELECT name FROM company WHERE company_id = ?", (survivor,)).fetchone()["name"]


def pass_job(ctx: WriteCtx, posting_id: int) -> PassJobResult:
    conn = ctx.conn
    company_id, _ = _posting(conn, posting_id)
    if posting_id not in passed_posting_ids(conn):
        append_event(conn, entity="posting", entity_id=posting_id, company_id=company_id, kind=PASSED,
                     actor="devanshu", at=ctx.now, detail={"posting_id": posting_id, "company_id": company_id},
                     idempotency_key=ctx.event_key(0))
    return PassJobResult(posting_id=posting_id, company_id=company_id, passed=True)


def go_ahead(ctx: WriteCtx, posting_id: int) -> GoAheadResult:
    conn = ctx.conn
    company_id, name = _posting(conn, posting_id)
    try:
        own = add_own_pick(conn, company_id, date.fromisoformat(ny_day(ctx.now)), ctx.now, posting_id=posting_id,
                           key=ctx.event_key(0))
    except OwnPickRefused as err:
        raise ApiProblem("not_allowed", f"{err} {NOT_SAVED}") from None
    text = f"Added {name} to today's list." if own.state == "added" \
        else f"{name} is asked for. It goes first on today's list when the plan is built."
    return GoAheadResult(posting_id=posting_id, company_id=company_id, state=own.state, rank=own.rank,
                         entry_id=own.entry_id, text=text)
