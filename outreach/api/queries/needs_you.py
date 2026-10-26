"""Needs you: the open review items with their evidence, and the HTTP side of the fixes he makes from them.

Reads are one query per table, joined in Python; every item answers as its company's survivor (a merged id follows
`merged_into` to its end). Items for companies in play come first, the rest are "Later".

Every write is `outreach.review` (#16): `apply_fix` (ok, a field with a source, no-go, merge), `reopen_item` (take an
ok, a no-go or a merge back) and `unmerge_company`, each called inside `run_write`'s one transaction (the review
package's own unit is a savepoint in it). This module holds no SQL that writes: it turns the body into a `Fix`, a
`ReviewError` into the `ApiProblem` with the same code and message, and the result into the answer.
"""
import contextlib
import logging
import sqlite3
from collections.abc import Iterator
from datetime import datetime

from outreach.api.errors import ApiProblem
from outreach.api.idempotency import WriteCtx
from outreach.api.models.company import (
    Fact, MergeCompare, MergeSide, ReviewCompany, ReviewItem, ReviewQuote, ReviewRead, ReviewReopened,
)
from outreach.api.models.health import JobRunSummary
from outreach.api.models.needs_you import NeedsYouView, ResolvedReview, ResolveReviewBody, ReviewResolved
from outreach.api.queries import jobs
from outreach.api.queries.company import (
    ATS_LABELS, READS_SHOWN, company_holds, fact_rank, merge_parents, read_detail, survivor_of, to_fact,
)
from outreach.api.queries.outcomes import by_ids
from outreach.outcomes.windows import day_bounds_utc, ny_day, week_bounds
from outreach.ranking.common import MergeLoop, canonical_ids
from outreach.review import queries as review_queries
from outreach.review.config import load_review_config
from outreach.review.errors import ReviewError
from outreach.review.fixes import Fix, apply_fix, can_undo, reopen_item
from outreach.review.kinds import spec_of
from outreach.review.merge import merge_compare
from outreach.review.merge import unmerge_company as review_unmerge
from outreach.review.play import emailing, in_play
from outreach.store.timefmt import ISO_FMT

log = logging.getLogger("outreach.api")

SIZE_KINDS = ("size_unknown", "size_guess", "size_check")
HIS_RESOLUTIONS = ("marked_ok", "marked_no_go", "field_edited")
# A posting item's quote: the sponsorship wording for sponsorship_unclear, else the first decision quote there is.
QUOTE_COLUMNS = ("sponsorship_quote", "citizenship_quote", "eligibility_quote", "years_quote")


@contextlib.contextmanager
def review_errors() -> Iterator[None]:
    """A `ReviewError` from `outreach.review` becomes the `ApiProblem` with the same code and message, so the page
    gets one envelope. `run_write` then rolls the whole write back."""
    try:
        yield
    except ReviewError as err:
        raise ApiProblem(err.code, err.message) from None


# ---------------------------------------------------------------------------------------------- reads

def _int(text: str) -> int | None:
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _merge(conn: sqlite3.Connection, review_id: int) -> MergeCompare:
    found = merge_compare(conn, review_id)
    side = lambda s: MergeSide(  # noqa: E731
        company_id=s.company_id, name=s.name, domain=s.domain, size=s.size, stage=s.stage, aliases=list(s.aliases),
        boards=list(s.boards), open_postings=s.open_postings, people=s.people, touches=s.touches, status=s.status)
    return MergeCompare(a=side(found.a), b=side(found.b), why=found.why, suggested_keep=found.suggested_keep,
                        warning=found.warning)


def review_items(conn: sqlite3.Connection, rows: list[sqlite3.Row], parents: dict[int, int],
                 now: str) -> list[ReviewItem]:
    """A `ReviewItem` for each `review_item` row, in the rows' order, answered as its company's survivor. A merge
    question whose two companies are one company now (or whose second company cannot be read) is left out: there is
    nothing to decide. The company page and Needs you both build their items here.

    The words come from `outreach.review`: a board item's reason is built from the board now
    (`display_reason`), `reopened` is `reopened_note`, `inPlay` is `play.in_play`, and an item holds an email when
    its kind does (`KindSpec.holds_email`) and the company is one he is emailing (`play.emailing`)."""
    if not rows:
        return []
    today = ny_day(now)
    survivor = {r["review_id"]: survivor_of(parents, r["company_id"]) for r in rows}
    other = {r["review_id"]: survivor_of(parents, o) for r in rows
             if r["kind"] == "merge_candidate" and (o := _int(r["evidence_key"])) is not None}
    companies = {c["company_id"]: c for c in by_ids(
        conn, "SELECT company_id, name, status, paused_at, held_by_touch, merged_into FROM company "
              "WHERE company_id IN ({ids})", set(survivor.values()) | set(other.values()))}
    rows = [r for r in rows if r["kind"] != "merge_candidate"
            or (r["review_id"] in other and other[r["review_id"]] != survivor[r["review_id"]]
                and other[r["review_id"]] in companies)]
    if not rows:
        return []

    board_ids = {r["board_id"] for r in rows if r["board_id"] is not None}
    reads: dict[int, list[sqlite3.Row]] = {}
    for x in by_ids(conn, "SELECT * FROM board_read WHERE board_id IN ({ids})", board_ids):
        reads.setdefault(x["board_id"], []).append(x)
    ats = {b["board_id"]: ATS_LABELS.get(b["ats"], b["ats"])
           for b in by_ids(conn, "SELECT board_id, ats FROM board WHERE board_id IN ({ids})", board_ids)}
    open_postings = {b: n for b, n in by_ids(
        conn, "SELECT board_id, count(*) FROM posting WHERE closed_at IS NULL AND board_id IN ({ids}) GROUP BY board_id",
        board_ids)}
    posting_ids = {r["posting_id"] for r in rows if r["posting_id"] is not None}
    postings = {p["posting_id"]: p for p in by_ids(
        conn, "SELECT posting_id, title, url, first_seen_at FROM posting WHERE posting_id IN ({ids})", posting_ids)}
    decisions = {d["posting_id"]: d for d in by_ids(
        conn, "SELECT * FROM posting_decision WHERE posting_id IN ({ids})", posting_ids)}
    sized = {survivor[r["review_id"]] for r in rows if r["kind"] in SIZE_KINDS}
    groups = sized | {c for c in parents if survivor_of(parents, c) in sized}
    size_rows: dict[int, list[sqlite3.Row]] = {}
    for e in by_ids(conn, "SELECT * FROM evidence WHERE fact = 'size' AND company_id IN ({ids})", groups):
        size_rows.setdefault(survivor_of(parents, e["company_id"]), []).append(e)
    picked = {survivor_of(parents, r[0]) for r in conn.execute(
        "SELECT company_id FROM daily_list_entry WHERE list_date = ? AND state = 'picked'", (today,))}
    planned_days = load_review_config().planned_days
    try:
        canon = canonical_ids(conn)
    except MergeLoop:
        # A `merged_into` loop is store corruption. Follow no merge for this request (each company is its own
        # survivor) rather than fail the list or read it as "nobody in play": a company on the latest plan, or one
        # with a queued email, still counts. Any other error (a bad list date) surfaces as before.
        log.warning("Needs you: a merged_into loop in the store; merges are not followed for in-play and emailing "
                    "on this request.")
        canon = {}
    playing = in_play(conn, today=today, planned_days=planned_days, canon=canon)
    mailing = emailing(conn, today=today, planned_days=planned_days, canon=canon)
    holds = company_holds(conn)  # a hold left on a merged-away duplicate holds the survivor; so does any reply

    moment = datetime.strptime(now, ISO_FMT)
    items = []
    for r in rows:
        cid = survivor[r["review_id"]]
        company = companies[cid]
        board = r["board_id"]
        shown = list(reversed(sorted(reads.get(board, []), key=lambda x: (x["at"], x["run_id"]), reverse=True)[:READS_SHOWN]))
        ok = [x["at"] for x in shown if x["result"] in ("ok", "unchanged")]
        note = review_queries.reopened_note(conn, r)
        items.append(ReviewItem(
            id=r["review_id"], kind=r["kind"],
            company=ReviewCompany(id=cid, name=company["name"], status=company["status"],
                                  paused_at=company["paused_at"], **holds.fields(cid),
                                  picked=cid in picked),
            holds_email=spec_of(r["kind"]).holds_email and cid in mailing,
            reason=review_queries.display_reason(conn, r),
            opened_at=r["created_at"],
            age_days=max(0, (moment - datetime.strptime(r["created_at"], ISO_FMT)).days),
            reopened=ReviewReopened(**note) if note is not None else None,
            quote=_quote(r, postings, decisions),
            reads=[ReviewRead(at=x["at"], result="ok" if x["result"] == "unchanged" else x["result"],
                              detail=read_detail(x, ats.get(board, "Board"))) for x in shown],
            last_ok_at=ok[-1] if ok else None,
            open_postings=open_postings.get(board, 0) if board is not None else None,
            size_facts=_size_facts(size_rows.get(cid, [])) if r["kind"] in SIZE_KINDS else [],
            revision=r["revision"],
            in_play=cid in playing,
            merge=_merge(conn, r["review_id"]) if r["kind"] == "merge_candidate" else None,
        ))
    return items


def _quote(r: sqlite3.Row, postings: dict, decisions: dict) -> ReviewQuote | None:
    p, d = postings.get(r["posting_id"]), decisions.get(r["posting_id"])
    if p is None or d is None:
        return None
    columns = ("sponsorship_quote",) if r["kind"] == "sponsorship_unclear" else QUOTE_COLUMNS
    text = next((d[c] for c in columns if d[c]), None)
    if text is None:
        return None
    return ReviewQuote(text=text, posting_id=p["posting_id"], posting_title=p["title"], url=p["url"],
                       first_seen_at=p["first_seen_at"])


def _size_facts(rows: list[sqlite3.Row]) -> list[Fact]:
    """Every size row of the company and its merged duplicates, the best (as `company_fact` picks) first."""
    return [to_fact(e) for e in sorted(rows, key=fact_rank)]


def _resolved_this_week(conn: sqlite3.Connection, now: str) -> list[ResolvedReview]:
    """His own settlings (ok, no-go, a field he set or a merge) this New York week, newest first. An item that
    cleared by itself is not his and is not listed."""
    monday, sunday = week_bounds(now)
    start, end = day_bounds_utc(monday)[0], day_bounds_utc(sunday)[1]
    rows = conn.execute(
        "SELECT r.review_id, r.kind, r.resolution, r.resolution_note, r.resolved_at, r.revision, c.name "
        "FROM review_item r JOIN company c ON c.company_id = r.company_id WHERE r.state = 'resolved' "
        "AND r.resolution IN (?, ?, ?) AND r.resolved_at >= ? AND r.resolved_at < ? "
        "ORDER BY r.resolved_at DESC, r.review_id DESC",
        (*HIS_RESOLUTIONS, start, end)).fetchall()
    return [ResolvedReview(
        id=r["review_id"], kind=r["kind"], company_name=r["name"], resolution=r["resolution"],
        note=r["resolution_note"], at=r["resolved_at"],
        # A merge question resolves field_edited only through a merge, which can be unmerged: not locked.
        locked=r["resolution"] == "field_edited" and r["kind"] != "merge_candidate",
        revision=r["revision"], can_undo=can_undo(conn, r["review_id"]),
    ) for r in rows]


def _boards_job(conn: sqlite3.Connection, now: str) -> JobRunSummary | None:
    for job in ("read_boards", "crawl"):
        row = conn.execute("SELECT * FROM job_run WHERE job = ? ORDER BY started_at DESC, run_id DESC LIMIT 1",
                           (job,)).fetchone()
        if row is not None:
            return jobs.run_summary(row, now)
    return None


def open_count(conn: sqlite3.Connection) -> int:
    """How many items `needs_you_view` lists as open: every open item, less a merge question that is settled (its two
    companies are one now, or the second cannot be read). The shell polls this."""
    plain = conn.execute("SELECT count(*) FROM review_item WHERE state = 'open' AND kind <> 'merge_candidate'").fetchone()[0]
    merges = conn.execute("SELECT company_id, evidence_key FROM review_item WHERE state = 'open' "
                          "AND kind = 'merge_candidate'").fetchall()
    if not merges:
        return plain
    parents = merge_parents(conn)
    known = {r[0] for r in conn.execute("SELECT company_id FROM company")}
    count = 0
    for r in merges:
        other = _int(r["evidence_key"])
        if other is not None and other in known and survivor_of(parents, other) != survivor_of(parents, r["company_id"]):
            count += 1
    return plain + count


def needs_you_view(conn: sqlite3.Connection, now: str) -> NeedsYouView:
    parents = merge_parents(conn)
    rows = conn.execute("SELECT * FROM review_item WHERE state = 'open' ORDER BY created_at, review_id").fetchall()
    items = review_items(conn, rows, parents, now)
    items.sort(key=lambda i: not i.in_play)  # stable: in play first, oldest first within each part
    return NeedsYouView(as_of=now, open=items, resolved_this_week=_resolved_this_week(conn, now),
                        boards_job=_boards_job(conn, now), settled_today=review_queries.settled_today(conn, now))


# ---------------------------------------------------------------------------------------------- writes

def resolve_review(ctx: WriteCtx, review_id: int, body: ResolveReviewBody) -> ReviewResolved:
    """Settle one open item with one fix: `apply_fix`, by `devanshu`, at the write's clock."""
    f = body.fix
    fix = Fix(kind=f.kind, field=getattr(f, "field", None), value=getattr(f, "value", None),
              source=getattr(f, "source", None), reason=getattr(f, "reason", None), keep=getattr(f, "keep", None))
    with review_errors():
        result = apply_fix(ctx.conn, review_id, fix, revision=body.revision, now=ctx.now, actor="devanshu")
    return ReviewResolved(id=review_id, revision=result.revision)


def reopen_review(ctx: WriteCtx, review_id: int, revision: int) -> int:
    """Take an ok, a no-go or a merge back (`reopen_item`). Returns the item's new revision."""
    with review_errors():
        return reopen_item(ctx.conn, review_id, revision=revision, now=ctx.now, actor="devanshu").revision


def unmerge_company(ctx: WriteCtx, company_id: int, revision: int) -> int:
    """Take a merged company back out (`outreach.review.merge.unmerge_company`). Returns its new revision."""
    with review_errors():
        return review_unmerge(ctx.conn, company_id, revision=revision, now=ctx.now, actor="devanshu")
