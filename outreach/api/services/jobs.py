"""Pass, Applied and Done on the job list (#156, #218, #195), each inside the caller's `run_write` transaction.

Pass hides one posting: a `posting_passed` event (entity posting, actor devanshu, detail `posting_id` and
`company_id`), which `joblist.passed_posting_ids` and `fresh.load_passed_postings` read. It is never a Not now: the
company is untouched and `fresh.load_passes` (the cooldown) does not read it. Passing a posting twice writes once.

Applied (#218) records that he applied online to the posting: one `application` row (the posting's company after any
merge, the New York day, the posting's url) and one `application` touch with an `own` context, the shape the plan's
`log_applied` writes, so Progress counts it. The job list hides a posting with a live application touch
(`joblist.applied_posting_ids`). It is not Done: the company is untouched. A posting already applied to writes nothing
and says when. A closed posting is recorded all the same: he applied.

Done (the first to see ruling in #9) is his mark that the posting's company has been reached, even when the outreach
happened outside Knock: a `company_reached` event (`outreach.pipeline.reached`), which the job list reads into
`contacted_on`. No other check stops it: a no-go company, one in flight or one on an old daily list is marked all the
same. A company already marked keeps its one mark and Done says when. `clear_done` takes the mark back with a
`company_reached_cleared` event; a company with no mark writes nothing. The same key again answers with the first
answer and writes nothing (`run_write`).
"""
import sqlite3
from datetime import date

from outreach.api.errors import ApiProblem
from outreach.api.idempotency import WriteCtx
from outreach.api.models.jobs import AppliedResult, ClearDoneResult, DoneResult, PassJobResult
from outreach.api.queries.company import merge_parents, survivor_of
from outreach.outcomes.requests import own_context
from outreach.outcomes.windows import ny_day
from outreach.pipeline import reached
from outreach.pipeline.joblist import passed_posting_ids
from outreach.store import append_event
from outreach.store.writes import log_touch

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


def applied(ctx: WriteCtx, posting_id: int) -> AppliedResult:
    conn = ctx.conn
    company_id, _ = _posting(conn, posting_id)
    standing = conn.execute("SELECT min(sent_at) FROM outreach_touch WHERE channel = 'application' "
                            "AND posting_id = ? AND undone_at IS NULL", (posting_id,)).fetchone()[0]
    if standing is not None:
        on = date.fromisoformat(ny_day(standing))
        return AppliedResult(posting_id=posting_id, company_id=company_id, applied_on=on.isoformat(),
                             text=f"Already applied on {on:%b} {on.day}.")
    posting = conn.execute("SELECT url, first_seen_at FROM posting WHERE posting_id = ?", (posting_id,)).fetchone()
    # One application row per posting: a touch taken back and applied again keeps the row it already has.
    if conn.execute("SELECT 1 FROM application WHERE posting_id = ? LIMIT 1", (posting_id,)).fetchone() is None:
        conn.execute("INSERT INTO application(company_id, posting_id, applied_on, url) VALUES (?, ?, ?, ?)",
                     (company_id, posting_id, ny_day(ctx.now), posting["url"]))
    log_touch(conn, person_id=None, company_id=company_id, channel="application", sent_at=ctx.now,
              context=own_context(conn, company_id, None, posting["first_seen_at"], ctx.now), posting_id=posting_id,
              idempotency_key=ctx.event_key(0))
    return AppliedResult(posting_id=posting_id, company_id=company_id, applied_on=ny_day(ctx.now),
                         text="Saved to Applied.")


def done(ctx: WriteCtx, posting_id: int) -> DoneResult:
    conn = ctx.conn
    company_id, name = _posting(conn, posting_id)
    standing = reached.marks(conn).get(company_id)
    if standing is not None:  # one mark: Done again changes nothing and says when he marked it
        on = date.fromisoformat(standing)
        return DoneResult(posting_id=posting_id, company_id=company_id, reached_on=standing,
                          text=f"{name} was already marked reached on {on:%b} {on.day}.")
    append_event(conn, entity="company", entity_id=company_id, company_id=company_id, kind=reached.REACHED,
                 actor="devanshu", at=ctx.now, detail={"posting_id": posting_id, "company_id": company_id},
                 idempotency_key=ctx.event_key(0))
    return DoneResult(posting_id=posting_id, company_id=company_id, reached_on=ny_day(ctx.now),
                      text=f"Marked {name} reached.")


def clear_done(ctx: WriteCtx, company_id: int) -> ClearDoneResult:
    conn = ctx.conn
    if conn.execute("SELECT 1 FROM company WHERE company_id = ?", (company_id,)).fetchone() is None:
        raise ApiProblem("not_found", f"There is no company {company_id}.")
    survivor = survivor_of(merge_parents(conn), company_id)
    standing = reached.marks(conn).get(survivor)
    if standing is None:
        return ClearDoneResult(company_id=survivor, cleared=False)
    append_event(conn, entity="company", entity_id=survivor, company_id=survivor, kind=reached.CLEARED,
                 actor="devanshu", at=ctx.now, detail={"company_id": survivor, "reached_on": standing},
                 idempotency_key=ctx.event_key(0))
    return ClearDoneResult(company_id=survivor, cleared=True)
