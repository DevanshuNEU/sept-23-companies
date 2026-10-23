"""Needs you: the open review items with their evidence, and the fixes he makes from them.

Reads are one query per table, joined in Python; every item answers as its company's survivor (a merged id follows
`merged_into` to its end). Items for companies in play come first, the rest are "Later".

The fixes run inside `run_write`'s one transaction: `ok`; `edit` (a locked hand fact, or a new board); `no_go` (a
`no_go` row, source `devanshu`); `merge` (only for a `merge_candidate`). A merge writes no `no_go` row and moves no
other row: holds, no-gos, people and touches of the company that goes away count for the survivor because every
reader follows `merged_into` (the sender's `held_survivors` and `no_go_sets` included), and an unmerge gives each
company back exactly its own.

REPLACE with #16: `outreach.review` (its Tasks 4, 8, 9, 11 and 13) takes over `in_play_ids`, the edits, the merge and
the unmerge; this module then keeps only the HTTP-facing reads.
"""
import json
import re
import sqlite3
from datetime import date, datetime

from outreach.api.errors import ApiProblem
from outreach.api.idempotency import WriteCtx
from outreach.api.models.company import Fact, ReviewCompany, ReviewItem, ReviewQuote, ReviewRead, ReviewReopened
from outreach.api.models.health import JobRunSummary
from outreach.api.models.needs_you import NeedsYouView, ResolvedReview, ResolveReviewBody, ReviewResolved
from outreach.api.queries import jobs
from outreach.api.queries.company import (
    ATS_LABELS, EMAIL_HOLDING_KINDS, READS_SHOWN, fact_rank, holding_touches, merge_parents, read_detail, survivor_of,
    to_fact,
)
from outreach.api.queries.outcomes import NOT_SAVED, by_ids
from outreach.api.revisions import bump
from outreach.drafts.followups import company_map
from outreach.identity import follow_merged
from outreach.outcomes.windows import day_bounds_utc, ny_day, week_bounds
from outreach.sources.ats_detect import detect_ats
from outreach.sources.boards import add_board
from outreach.sources.keys import classify_domain, registrable_domain
from outreach.store import add_alias, append_event, is_shared_key
from outreach.store.timefmt import ISO_FMT

LIVE_STATUSES = ("candidate", "backlog", "working")
PLAN_DAYS = 4  # the latest daily list puts a company in play for this many days (#16 ruling)
SIZE_KINDS = ("size_unknown", "size_guess", "size_check")
BOARD_KINDS = ("board_unreadable", "board_empty")
HIS_RESOLUTIONS = ("marked_ok", "marked_no_go", "field_edited")
# The one field an edit sets for each kind; a kind not here has nothing to edit.
EDIT_FIELD = {"domain_missing": "domain", "size_unknown": "size", "size_guess": "size", "size_check": "size",
              "sponsorship_unclear": "sponsorship", "board_unreadable": "board_url", "board_empty": "board_url"}
FIELD_WORDS = {"domain": "domain", "size": "size", "sponsorship": "sponsorship", "board_url": "board address"}
# A posting item's quote: the sponsorship wording for sponsorship_unclear, else the first decision quote there is.
QUOTE_COLUMNS = ("sponsorship_quote", "citizenship_quote", "eligibility_quote", "years_quote")
RESOLVED_WORDS = {"marked_ok": "you marked it ok", "marked_no_go": "you marked it no-go",
                  "field_edited": "you fixed it", "cleared": "it cleared by itself"}
CHANGED_WORDS = {
    "board_unreadable": "the board failed again", "board_empty": "the board went empty again",
    "sponsorship_unclear": "the sponsorship wording changed", "size_unknown": "the size evidence changed",
    "size_guess": "the size evidence changed", "size_check": "the size evidence changed",
    "domain_missing": "the domain is missing again", "careers_changed": "the careers page changed again",
    "reader_disagrees": "the reader read it again", "flagged": "it was flagged again",
}
REASON_WORDS = {
    "no_sponsorship": "does not sponsor", "citizenship": "citizenship or clearance", "over_300": "over 300 people",
    "defence_contractor": "defence contractor", "agency": "agency", "said_no": "said no", "stop_reply": "asked to stop",
    "not_a_fit": "not a fit", "other": "other",
}
DOMAIN_WORDS = {"free": "a free mail provider", "peo": "a payroll provider", "platform": "a platform or job board host"}
MERGED_ELSEWHERE = ("One of these was already merged into another company. Keep them separate here, then merge from "
                    "that company's page.")
LOST_STOP = {"paused": "This one is paused. Keep the paused company, or unpause it first.",
             "closed": "This one is closed. Keep the closed company, or reopen it first.",
             "removed": "This one is removed. Keep the removed company, or restore it first."}
SPONSORSHIP_VALUES = {"yes": "yes", "unclear": "unclear", "opt only": "opt_only", "opt": "opt_only"}


# ---------------------------------------------------------------------------------------------- reads

def _int(text: str) -> int | None:
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _day_words(moment: str) -> str:
    """"Nov 3": the New York date of a UTC moment."""
    d = date.fromisoformat(ny_day(moment))
    return f"{d:%b} {d.day}"


def in_play_ids(conn: sqlite3.Connection, parents: dict[int, int], companies: dict[int, sqlite3.Row],
                today: str) -> set[int]:
    """The survivors in `companies` that are in play: live (candidate, backlog or working, not merged, no active
    no-go on it or on a company merged into it) and either `working`, or on the latest daily list (when it is at most
    PLAN_DAYS old) as `picked`, or `proposed` with a named person. #16's `play.in_play` replaces this."""
    barred = {survivor_of(parents, r[0]) for r in conn.execute(
        "SELECT company_id FROM no_go WHERE lifted_at IS NULL AND company_id IS NOT NULL")}
    planned: set[int] = set()
    latest = conn.execute("SELECT max(list_date) FROM daily_list").fetchone()[0]
    if latest is not None and (date.fromisoformat(today) - date.fromisoformat(latest)).days <= PLAN_DAYS:
        for r in conn.execute("SELECT company_id, state, person_id FROM daily_list_entry WHERE list_date = ?", (latest,)):
            if r["state"] == "picked" or (r["state"] == "proposed" and r["person_id"] is not None):
                planned.add(survivor_of(parents, r["company_id"]))
    return {cid for cid, c in companies.items()
            if c["merged_into"] is None and c["status"] in LIVE_STATUSES and cid not in barred
            and (c["status"] == "working" or cid in planned)}


def _reopened(row: sqlite3.Row, resolved: dict[tuple, list[sqlite3.Row]]) -> ReviewReopened | None:
    """What changed, when an older item of the same kind on the same thing was resolved on other evidence."""
    earlier = [x for x in resolved.get(_item_key(row), [])
               if x["evidence_key"] != row["evidence_key"] and x["created_at"] <= row["created_at"]]
    if not earlier:
        return None
    last = max(earlier, key=lambda x: (x["resolved_at"], x["review_id"]))
    return ReviewReopened(previous=f"{RESOLVED_WORDS[last['resolution']]} on {_day_words(last['resolved_at'])}",
                          changed=CHANGED_WORDS.get(row["kind"], "new evidence came in"))


def _item_key(r: sqlite3.Row) -> tuple:
    return r["kind"], r["company_id"], r["board_id"] or 0, r["posting_id"] or 0, r["person_id"] or 0


def review_items(conn: sqlite3.Connection, rows: list[sqlite3.Row], parents: dict[int, int],
                 now: str) -> list[ReviewItem]:
    """A `ReviewItem` for each `review_item` row, in the rows' order, answered as its company's survivor. A merge
    question whose two companies are one company now (or whose second company cannot be read) is left out: there is
    nothing to decide. The company page and Needs you both build their items here."""
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
    playing = in_play_ids(conn, parents, companies, today)
    holds = holding_touches(company_map(conn))  # a hold left on a merged-away duplicate holds the survivor
    resolved: dict[tuple, list[sqlite3.Row]] = {}
    for x in by_ids(conn, "SELECT * FROM review_item WHERE state = 'resolved' AND company_id IN ({ids})",
                    {r["company_id"] for r in rows}):
        resolved.setdefault(_item_key(x), []).append(x)

    moment = datetime.strptime(now, ISO_FMT)
    items = []
    for r in rows:
        cid = survivor[r["review_id"]]
        company = companies[cid]
        board = r["board_id"]
        shown = list(reversed(sorted(reads.get(board, []), key=lambda x: (x["at"], x["run_id"]), reverse=True)[:READS_SHOWN]))
        ok = [x["at"] for x in shown if x["result"] in ("ok", "unchanged")]
        items.append(ReviewItem(
            id=r["review_id"], kind=r["kind"],
            company=ReviewCompany(id=cid, name=company["name"], status=company["status"],
                                  paused_at=company["paused_at"], held_by_touch_id=holds.get(cid),
                                  picked=cid in picked),
            holds_email=cid in picked and r["kind"] in EMAIL_HOLDING_KINDS, reason=r["reason"],
            opened_at=r["created_at"],
            age_days=max(0, (moment - datetime.strptime(r["created_at"], ISO_FMT)).days),
            reopened=_reopened(r, resolved),
            quote=_quote(r, postings, decisions),
            reads=[ReviewRead(at=x["at"], result="ok" if x["result"] == "unchanged" else x["result"],
                              detail=read_detail(x, ats.get(board, "Board"))) for x in shown],
            last_ok_at=ok[-1] if ok else None,
            open_postings=open_postings.get(board, 0) if board is not None else None,
            size_facts=_size_facts(size_rows.get(cid, [])) if r["kind"] in SIZE_KINDS else [],
            revision=r["revision"],
            in_play=cid in playing,
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
    monday, sunday = week_bounds(now)
    start, end = day_bounds_utc(monday)[0], day_bounds_utc(sunday)[1]
    rows = conn.execute(
        "SELECT r.review_id, r.kind, r.resolution, r.resolution_note, r.resolved_at, c.name FROM review_item r "
        "JOIN company c ON c.company_id = r.company_id WHERE r.state = 'resolved' AND r.resolution IN (?, ?, ?) "
        "AND r.resolved_at >= ? AND r.resolved_at < ? ORDER BY r.resolved_at DESC, r.review_id DESC",
        (*HIS_RESOLUTIONS, start, end)).fetchall()
    return [ResolvedReview(
        id=r["review_id"], kind=r["kind"], company_name=r["name"], resolution=r["resolution"],
        note=r["resolution_note"], at=r["resolved_at"],
        # A merge question resolves field_edited only through a merge, which can be unmerged: not locked.
        locked=r["resolution"] == "field_edited" and r["kind"] != "merge_candidate",
    ) for r in rows]


def _boards_job(conn: sqlite3.Connection, now: str) -> JobRunSummary | None:
    for job in ("read_boards", "crawl"):
        row = conn.execute("SELECT * FROM job_run WHERE job = ? ORDER BY started_at DESC, run_id DESC LIMIT 1",
                           (job,)).fetchone()
        if row is not None:
            return jobs.run_summary(row, now)
    return None


def needs_you_view(conn: sqlite3.Connection, now: str) -> NeedsYouView:
    parents = merge_parents(conn)
    rows = conn.execute("SELECT * FROM review_item WHERE state = 'open' ORDER BY created_at, review_id").fetchall()
    items = review_items(conn, rows, parents, now)
    items.sort(key=lambda i: not i.in_play)  # stable: in play first, oldest first within each part
    return NeedsYouView(as_of=now, open=items, resolved_this_week=_resolved_this_week(conn, now),
                        boards_job=_boards_job(conn, now))


# ---------------------------------------------------------------------------------------------- hand values

def _bad(text: str) -> ApiProblem:
    return ApiProblem("bad_request", f"{text} {NOT_SAVED}")


_APPROX = re.compile(r"(?:about|around|roughly|approximately|approx\.?|~)\s*")
_PEOPLE = r"(?:\s*(?:people|employees|staff))?"


def hand_size(text: str) -> tuple[str, int, int | None, str]:
    """(value, low, high, confidence) from what he typed: "45" is confirmed; "about 45", "11 to 50", "11-50" and
    "50+" are estimates; a "guess" anywhere makes it a guess. Commas are dropped ("1,000")."""
    t = text.strip().lower().replace(",", "")
    confidence = "confirmed"
    if re.search(r"\bguess\b", t):
        confidence, t = "guess", re.sub(r"\bguess\b", "", t).strip()
    approx = _APPROX.match(t)
    if approx and approx.end():
        t = t[approx.end():].strip()
        confidence = "guess" if confidence == "guess" else "estimate"
    rough = "guess" if confidence == "guess" else "estimate"
    if m := re.fullmatch(r"(\d+)\s*(?:to|-)\s*(\d+)" + _PEOPLE, t):
        low, high = int(m[1]), int(m[2])
        if 1 <= low <= high:
            return f"{low}-{high}", low, high, rough
    elif m := re.fullmatch(r"(\d+)\s*\+" + _PEOPLE, t):
        if int(m[1]) >= 1:
            return f"{int(m[1])}+", int(m[1]), None, rough
    elif m := re.fullmatch(r"(\d+)" + _PEOPLE, t):
        if int(m[1]) >= 1:
            return str(int(m[1])), int(m[1]), int(m[1]), confidence
    raise _bad("A size is a number like 45, or a range like 11 to 50.")


_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+\Z")


def hand_domain(conn: sqlite3.Connection, text: str) -> str:
    """The bare domain from an address, a URL or a domain ("https://www.Acme.io/jobs", "ana@acme.io"). Refused when
    it is not a domain, is a free mail, payroll or platform host, or is a key shared by several employers."""
    t = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text.strip().lower()).rpartition("@")[2]
    t = re.split(r"[/?#:]", t, maxsplit=1)[0].strip(".")
    if t.startswith("www.") and "." in t[4:]:
        t = t[4:]
    if not _HOST.match(t):
        raise _bad("That is not a domain like acme.com.")
    kind = classify_domain(t)
    if kind != "company":
        raise _bad(f"{t} is {DOMAIN_WORDS.get(kind, 'not a company host')}, not the company's own domain.")
    root = registrable_domain(t)
    if is_shared_key(conn, "domain", t) or (root is not None and is_shared_key(conn, "domain", root)):
        raise _bad(f"{t} is shared by several employers, so it cannot name this company.")
    return t


def hand_sponsorship(text: str) -> str:
    t = " ".join(text.strip().lower().replace("_", " ").split())
    if t == "no":
        raise _bad("For a company that does not sponsor, mark it no-go.")
    if t not in SPONSORSHIP_VALUES:
        raise _bad("Sponsorship is Yes, OPT only or Unclear.")
    return SPONSORSHIP_VALUES[t]


# ---------------------------------------------------------------------------------------------- writes

class _Events:
    """Appends the write's events in order, each keyed `<request id>:<n>` (a second guard against a repeat)."""

    def __init__(self, ctx: WriteCtx):
        self.ctx, self.n = ctx, 0

    def add(self, **fields) -> None:
        append_event(self.ctx.conn, actor="devanshu", at=self.ctx.now, idempotency_key=self.ctx.event_key(self.n),
                     **fields)
        self.n += 1


def _company(conn: sqlite3.Connection, company_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM company WHERE company_id = ?", (company_id,)).fetchone()


def _check_fix(kind: str, fix) -> None:
    """A fix the item's kind allows, before anything is written."""
    if fix.kind == "merge" and kind != "merge_candidate":
        raise ApiProblem("not_allowed", "Only a possible duplicate can be merged.")
    if kind == "merge_candidate" and fix.kind in ("edit", "no_go"):
        raise ApiProblem("not_allowed", "A possible duplicate is merged or kept separate.")
    if fix.kind == "edit":
        field = EDIT_FIELD.get(kind)
        if field is None:
            raise ApiProblem("not_allowed", "This item has nothing to edit. Mark it ok or no-go.")
        if fix.field != field:
            raise _bad(f"This item is fixed by setting the {FIELD_WORDS[field]}.")
        if not fix.value.strip():
            raise _bad("An edit needs a value.")
    if fix.kind in ("edit", "no_go") and not fix.source.strip():
        raise _bad("Say where you saw it: a fix needs a source.")


def resolve_review(ctx: WriteCtx, review_id: int, body: ResolveReviewBody) -> ReviewResolved:
    """Apply one fix to an open review item and resolve it, inside the write's transaction."""
    conn = ctx.conn
    row = conn.execute("SELECT * FROM review_item WHERE review_id = ?", (review_id,)).fetchone()
    if row is None:
        raise ApiProblem("not_found", f"There is no review item {review_id}.")
    if row["state"] == "resolved":
        raise ApiProblem("not_allowed", "Already resolved.")
    if row["revision"] != body.revision:
        raise ApiProblem("conflict", f"The review item changed since you loaded it. {NOT_SAVED}")
    fix = body.fix
    _check_fix(row["kind"], fix)
    events = _Events(ctx)
    try:
        survivor = follow_merged(conn, row["company_id"])
    except ValueError:
        raise ApiProblem("conflict", f"The company's merges loop back on themselves. {NOT_SAVED}") from None
    detail: dict = {"fix": fix.kind}
    if fix.kind == "ok":
        resolution = "marked_ok"
        note = "Kept separate by you." if row["kind"] == "merge_candidate" else "Marked ok by you."
    elif fix.kind == "edit":
        resolution = "field_edited"
        note = _apply_edit(ctx, row, survivor, fix, events, detail)
    elif fix.kind == "no_go":
        resolution = "marked_no_go"
        source = fix.source.strip()
        # Onto what the item names: its person, else its own company (a merged-away one included, so the stop
        # covers the survivor while the merge stands and stays with that company after an unmerge).
        column, target = (("person_id", row["person_id"]) if row["person_id"] is not None
                          else ("company_id", row["company_id"]))
        cur = conn.execute(
            f"INSERT INTO no_go({column}, reason, source, source_ref, created_at) VALUES (?, ?, 'devanshu', ?, ?) "
            "ON CONFLICT DO NOTHING", (target, fix.reason, source, ctx.now))
        detail.update(no_go_created=cur.rowcount == 1, reason=fix.reason, source=source, **{column: target})
        note = (f"Added to the no-go list by you: {REASON_WORDS[fix.reason]}." if cur.rowcount
                else "Marked no-go by you; it was already on the no-go list.")
    else:
        resolution = "field_edited"
        other = _int(row["evidence_key"])
        if other is None:
            raise ApiProblem("not_allowed", "This question does not name a second company.")
        keep_id, lose_id = (row["company_id"], other) if fix.keep == "a" else (other, row["company_id"])
        merge_companies(ctx, keep_id=keep_id, lose_id=lose_id, review_id=review_id, events=events)
        detail.update(kept_id=keep_id, merged_id=lose_id)
        note = f"merged into {keep_id}"
    revision = bump(conn, "review_item", "review_id", review_id, body.revision, {
        "state": "resolved", "resolution": resolution, "resolved_at": ctx.now, "resolution_note": note}, at=ctx.now)
    events.add(entity="review_item", entity_id=review_id, company_id=row["company_id"], kind="review_resolved",
               detail={**detail, "resolution": resolution, "text": note})
    return ReviewResolved(id=review_id, revision=revision)


def _apply_edit(ctx: WriteCtx, row: sqlite3.Row, survivor: int, fix, events: _Events, detail: dict) -> str:
    """Write the value he set: a locked hand fact (source_ref his source) or a new board, on the company the item
    names. While it is merged away every fact read follows it to the survivor; an unmerge keeps it where it was."""
    conn, source, value = ctx.conn, fix.source.strip(), fix.value.strip()
    if fix.field == "board_url":
        return _replace_board(ctx, row, survivor, value, source, events, detail)
    low = high = None
    confidence = "confirmed"
    if fix.field == "size":
        value, low, high, confidence = hand_size(value)
    elif fix.field == "domain":
        value = hand_domain(conn, value)
    else:
        value = hand_sponsorship(value)
    cur = conn.execute(
        "INSERT INTO evidence(company_id, fact, value, num_low, num_high, confidence, locked, source_kind, source_ref, "
        "observed_on, recorded_at) VALUES (?, ?, ?, ?, ?, ?, 1, 'hand', ?, ?, ?)",
        (row["company_id"], fix.field, value, low, high, confidence, source, ny_day(ctx.now), ctx.now))
    detail.update(field=fix.field, value=value, source=source, evidence_id=cur.lastrowid)
    return f"{FIELD_WORDS[fix.field].capitalize()} set to {value} by you, from {source}."


def _replace_board(ctx: WriteCtx, row: sqlite3.Row, survivor: int, url: str, source: str, events: _Events,
                   detail: dict) -> str:
    """The board at `url` becomes the company's (found_by `hand`); the board the item is about stops being read and
    its still-open postings close (`hand`). Refused before any write when the address is not a board we read, is the
    same board, or is another company's board (no merge question is raised for it)."""
    conn = ctx.conn
    found = detect_ats(url)
    if not found:
        raise _bad("That address is not a job board we can read. Paste the board's own address, for example "
                   "https://jobs.ashbyhq.com/company.")
    ref = found[0]
    old = conn.execute("SELECT * FROM board WHERE board_id = ?", (row["board_id"],)).fetchone()
    if old is not None and follow_merged(conn, old["company_id"]) != survivor:
        raise ApiProblem("conflict", f"The board this item names belongs to another company. {NOT_SAVED}")
    if old is not None and (old["ats"], old["token"]) == (ref.ats, ref.token):
        raise _bad("That is the board we already read. If the board is right, mark ok.")
    owner = conn.execute("SELECT company_id FROM board WHERE ats = ? AND token = ?", (ref.ats, ref.token)).fetchone()
    owner = owner or conn.execute("SELECT company_id FROM company_alias WHERE alias_key = lower(?)",
                                  (f"{ref.ats}:{ref.token}",)).fetchone()
    if owner is not None and follow_merged(conn, owner[0]) != survivor:
        name = _company(conn, owner[0])["name"]
        raise ApiProblem("conflict", f"That board belongs to {name} (#{owner[0]}). {NOT_SAVED}")
    try:
        added = add_board(conn, row["company_id"], ref, via="hand", matched_on="needs_you", found_by="hand",
                          actor="devanshu", now=ctx.now)
    except ValueError as err:
        raise _bad(f"That board cannot be added: {err}.") from None
    if added == "conflict":  # another writer claimed it since the check: retire nothing
        raise ApiProblem("conflict", f"That board belongs to another company. {NOT_SAVED}")
    conn.execute("UPDATE board SET active = 1 WHERE ats = ? AND token = ?", (ref.ats, ref.token))
    new = f"{ref.ats}:{ref.token}"
    closed = 0
    if old is not None:
        conn.execute("UPDATE board SET active = 0 WHERE board_id = ?", (old["board_id"],))
        closed = conn.execute("UPDATE posting SET closed_at = ?, close_reason = 'hand' WHERE board_id = ? "
                              "AND closed_at IS NULL", (ctx.now, old["board_id"])).rowcount
        events.add(entity="board", entity_id=old["board_id"], company_id=survivor, kind="replaced",
                   detail={"old": f"{old['ats']}:{old['token']}", "new": new, "closed_postings": closed,
                           "source": source, "text": f"Board replaced by {new} by you."})
    detail.update(field="board_url", value=new, source=source, closed_postings=closed)
    return f"Board set to {new} by you, from {source}."


def merge_companies(ctx: WriteCtx, *, keep_id: int, lose_id: int, review_id: int | None, events: _Events) -> None:
    """Merge `lose_id` into `keep_id`: the loser gets `merged_into`, status `removed` and remove_reason `merged`
    (its close reason and pause are cleared, as the company CHECKs need, and kept in the event for an unmerge); the
    loser's name becomes an alias of the keeper (`add_alias`, source `merge`) unless its key is already taken; one
    `merged` event records what the loser was. Nothing else is written or moved: no `no_go` row (a lifted one stays
    lifted), and `held_by_touch` stays on the loser, where the sender's `held_survivors` reads it for the survivor.
    `conflict` when either company is already merged (a merge into a merged company, or one that would loop);
    `not_allowed` when the merge would release mail the loser stops (`_refuse_lost_stop`)."""
    conn = ctx.conn
    keep, lose = _company(conn, keep_id), _company(conn, lose_id)
    if keep is None or lose is None:
        raise ApiProblem("not_found", f"There is no company {keep_id if keep is None else lose_id}.")
    if keep_id == lose_id:
        raise ApiProblem("not_allowed", "They are already one company.")
    if keep["merged_into"] is not None or lose["merged_into"] is not None:
        try:
            one = follow_merged(conn, keep_id) == follow_merged(conn, lose_id)
        except ValueError:
            one = False
        raise ApiProblem("conflict", "These are already one company." if one else MERGED_ELSEWHERE)
    _refuse_lost_stop(keep, lose)
    conn.execute(
        "UPDATE company SET merged_into = ?, status = 'removed', remove_reason = 'merged', close_reason = NULL, "
        "paused_at = NULL, pause_note = '', status_at = ?, revision = revision + 1 WHERE company_id = ?",
        (keep_id, ctx.now, lose_id))
    try:
        follow_merged(conn, lose_id)  # identity's own walk: a loop is corruption, never a merge
    except ValueError:
        raise ApiProblem("conflict", f"That merge would make a loop. {NOT_SAVED}") from None
    try:
        alias_added = add_alias(conn, keep_id, lose["name"], "merge")
    except ValueError:
        # The key is taken: by the loser itself (it resolves to the keeper through merged_into) or by a third
        # company (identity raises its own question for that). No alias row moves either way.
        alias_added = False
    events.add(entity="company", entity_id=lose_id, company_id=lose_id, kind="merged", detail={
        "previous_status": lose["status"], "previous_close_reason": lose["close_reason"],
        "previous_remove_reason": lose["remove_reason"], "previous_paused_at": lose["paused_at"],
        "previous_pause_note": lose["pause_note"], "kept_id": keep_id, "review_id": review_id,
        "alias_added": alias_added, "text": f"Merged into {keep['name']} (#{keep_id}) by you."})


def _stops_mail(company: sqlite3.Row) -> bool:
    return company["paused_at"] is not None or company["status"] in ("closed", "removed")


def _refuse_lost_stop(keep: sqlite3.Row, lose: sqlite3.Row) -> None:
    """A merge must not release mail the loser stops. The sender reads only the survivor's pause and status, and a
    merge clears the loser's pause (the company CHECKs force it), so a paused loser needs a keeper that stops mail
    too; a closed or removed loser (closing a company leaves its touches and messages as they are) needs a closed
    or removed keeper. Keeping the stopped company is always fine."""
    if lose["status"] in ("closed", "removed") and keep["status"] not in ("closed", "removed"):
        raise ApiProblem("not_allowed", LOST_STOP[lose["status"]])
    if lose["paused_at"] is not None and not _stops_mail(keep):
        raise ApiProblem("not_allowed", LOST_STOP["paused"])


def unmerge_company(ctx: WriteCtx, company_id: int, revision: int) -> int:
    """Take a merged company back out: `merged_into` and the `merged` remove reason cleared, its status (with the
    close reason, removed reason and pause it had) restored from the latest `merged` event, an `unmerged` event.
    The alias the merge added stays with the survivor and nothing else moves back. Returns the new revision."""
    conn = ctx.conn
    row = _company(conn, company_id)
    if row is None:
        raise ApiProblem("not_found", f"There is no company {company_id}.")
    if row["merged_into"] is None:
        raise ApiProblem("not_allowed", "Not merged.")
    if row["revision"] != revision:
        raise ApiProblem("conflict", f"The company changed since you loaded it. {NOT_SAVED}")
    found = conn.execute("SELECT detail FROM event WHERE entity = 'company' AND entity_id = ? AND kind = 'merged' "
                         "ORDER BY event_id DESC LIMIT 1", (company_id,)).fetchone()
    was = json.loads(found["detail"]) if found else {}
    status = was.get("previous_status")
    if status not in ("candidate", "backlog", "working", "closed", "removed") or (
            status == "removed" and not was.get("previous_remove_reason")):
        status = "candidate"  # merged by some other writer, with no record of what it was
    sets = {
        "merged_into": None, "status": status,
        "remove_reason": was.get("previous_remove_reason") if status == "removed" else None,
        "close_reason": (was.get("previous_close_reason") or "other") if status == "closed" else None,
        "paused_at": was.get("previous_paused_at") if status == "working" else None,
        "pause_note": was.get("previous_pause_note") or "", "status_at": ctx.now,
    }
    new = bump(conn, "company", "company_id", company_id, revision, sets, at=ctx.now)
    _Events(ctx).add(entity="company", entity_id=company_id, company_id=company_id, kind="unmerged", detail={
        "kept_id": row["merged_into"], "restored_status": status, "text": "Unmerged by you."})
    return new
