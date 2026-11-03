"""The shell summary: the sidebar's counts and the sender line, in one read.

A dozen counts and no per-row query: every number reads the same rows the view it summarizes reads, through that
view's own count (`done_count`, `no_go_count`, `open_count`, `replied_count`, `plan_counts`, `wins_unseen`), so the
sidebar never disagrees with the page it links to. The ones with no view of their own are counted here:

- `draftsToReview`: the Outbox's `review` group: an open email thread whose current message (the newest that is not
  planned or skipped) is a draft, at a company that is neither closed, removed nor paused;
- `unhandledReplies`: email threads that replied and whose reply kind is still `unsorted`.
"""
import sqlite3

from outreach.api.config import ApiConfig
from outreach.api.models.pipeline import PipelineLine
from outreach.api.models.shell import PlanProgress, ShellSummary
from outreach.api.queries import done, jobs, linkedin, needs_you, no_go, progress, today
from outreach.api.queries.company import merge_parents, survivor_of
from outreach.api.queries.outcomes import by_ids
from outreach.api.queries.sending import read_sender, summary_of
from outreach.outcomes.windows import ny_day
from outreach.pipeline import alarms

_DRAFTS = (
    "SELECT p.company_id, p.person_id FROM touch t JOIN person p ON p.person_id = t.person_id JOIN ("
    "SELECT touch_id, state, ROW_NUMBER() OVER (PARTITION BY touch_id ORDER BY seq DESC) AS n FROM message "
    "WHERE state NOT IN ('planned', 'skipped')) m ON m.touch_id = t.touch_id AND m.n = 1 "
    "WHERE t.state = 'open' AND m.state = 'drafted'")


def drafts_to_review(conn: sqlite3.Connection) -> int:
    """Drafts the Outbox lists under Drafts to review: not at a closed, removed or paused company, and not to a
    person or company on the no-go list (those are Closed there, #149)."""
    drafts = [(r[0], r[1]) for r in conn.execute(_DRAFTS)]
    if not drafts:
        return 0
    parents = merge_parents(conn)
    owners = {survivor_of(parents, c) for c, _ in drafts}
    open_company = {r["company_id"] for r in by_ids(
        conn, "SELECT company_id FROM company WHERE status NOT IN ('closed', 'removed') AND paused_at IS NULL "
              "AND company_id IN ({ids})", owners)}
    no_go_people, no_go_companies = set(), set()
    for r in conn.execute("SELECT person_id, company_id FROM no_go WHERE lifted_at IS NULL"):
        if r["person_id"] is not None:
            no_go_people.add(r["person_id"])
        else:
            no_go_companies.add(survivor_of(parents, r["company_id"]))
    return sum(1 for c, p in drafts
               if survivor_of(parents, c) in open_company and survivor_of(parents, c) not in no_go_companies
               and p not in no_go_people)


def pipeline_line(conn: sqlite3.Connection, now: str) -> PipelineLine | None:
    """The sidebar's scorecard line: the newest stored card in words, and the alarms that hold now. Reads only, and a
    store that cannot answer leaves the line out rather than failing the whole summary."""
    try:
        found = alarms.check(conn, now)
        return PipelineLine(text=alarms.scorecard_line(conn), alarms=[a.text for a in found],
                            tone="bad" if found else "idle")
    except sqlite3.Error:
        return None


def shell_summary(conn: sqlite3.Connection, now: str, cfg: ApiConfig, *, old_sender_loaded: bool) -> ShellSummary:
    day = ny_day(now)
    built = conn.execute("SELECT capacity FROM daily_list WHERE list_date = ?", (day,)).fetchone()
    sender = read_sender(conn, now, old_sender_loaded=old_sender_loaded)  # one snapshot for the line and the capacity
    planned, finished = progress.plan_counts(conn, day)
    return ShellSummary(
        as_of=now, picked_today=today.picked_count(conn, day), capacity_today=built["capacity"] if built is not None else today.capacity_from(sender, cfg),
        unhandled_replies=conn.execute("SELECT count(*) FROM touch WHERE state = 'replied' "
                                       "AND reply_kind = 'unsorted'").fetchone()[0],
        drafts_to_review=drafts_to_review(conn), linkedin_replied=linkedin.replied_count(conn),
        open_review_items=needs_you.open_count(conn, now), no_go_count=no_go.no_go_count(conn),
        done_count=done.done_count(conn), sending=summary_of(sender),
        replies_this_month=done.replies_this_month(conn, now), jobs=jobs.last_runs(conn, now),
        plan=PlanProgress(done=finished, total=planned), wins_unseen=progress.wins_unseen(conn),
        pipeline=pipeline_line(conn, now))
