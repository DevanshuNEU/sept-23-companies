"""The watch list: open review items for a changed careers page or a possible duplicate, and new companies worth a
look. One query per table, joined in Python. Every company is answered as its survivor (a merged id follows
`merged_into` to its end).

An item's id is `ref_id * 4 + kind index`: the review item for the two review kinds, the company for a new one.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from outreach.api.errors import ApiProblem
from outreach.api.idempotency import WriteCtx
from outreach.api.models.company import CompanyRef
from outreach.api.models.watch import SourceLink, WatchItem, WatchView
from outreach.api.queries.company import holding_touches, merge_parents, survivor_of
from outreach.drafts.followups import company_map, no_go_sets
from outreach.store.timefmt import ISO_FMT
from outreach.store.writes import append_event

KIND_INDEX = {"careers_changed": 0, "new_reason_no_role": 1, "merge_candidate": 2}
KIND_OF_INDEX = {index: kind for kind, index in KIND_INDEX.items()}
NEW_WITHIN_DAYS = 14
NEW_LIMIT = 20
LANE = "reason_no_role"
CHUNK = 500

# A company made in the last 14 days that is still open to us, with no person, touch or dismissal, and every score
# row it has (a no-go is read in Python through the sender's no_go_sets, so a duplicate's no-go counts). The scores are read by key (company_id) from each company: `CROSS JOIN` keeps that order, so the
# planner never scans company_score. `hide_type` is the JSON type of `signals.hide` ('null' or NULL: not hidden).
NEW_COMPANIES_SQL = """
SELECT c.company_id, c.name, c.status, c.paused_at, c.held_by_touch, c.created_at,
       s.ranker_version, s.score, s.scored_at, s.lanes, json_type(s.signals, '$.hide') AS hide_type
FROM company c CROSS JOIN company_score s ON s.company_id = c.company_id
WHERE c.created_at >= ? AND c.merged_into IS NULL AND c.status IN ('candidate', 'backlog', 'working')
  AND NOT EXISTS (SELECT 1 FROM person p WHERE p.company_id = c.company_id)
  AND NOT EXISTS (SELECT 1 FROM outreach_touch t WHERE t.company_id = c.company_id AND t.undone_at IS NULL)
  AND NOT EXISTS (SELECT 1 FROM watch_dismissal d WHERE d.kind = 'new_reason_no_role' AND d.ref_id = c.company_id)
"""

# Merged-away duplicates that carry a person or a touch: the survivor is not new either.
CONTACTED_DUPLICATES_SQL = """
SELECT c.merged_into FROM company c JOIN person p ON p.company_id = c.company_id WHERE c.merged_into IS NOT NULL
UNION
SELECT c.merged_into FROM company c JOIN outreach_touch t ON t.company_id = c.company_id AND t.undone_at IS NULL
WHERE c.merged_into IS NOT NULL
"""


def _ref(row: sqlite3.Row, holds: dict[int, int]) -> CompanyRef:
    """`holds` is `holding_touches`: a hold on a merged-away duplicate holds the survivor."""
    return CompanyRef(id=row["company_id"], name=row["name"], status=row["status"], paused_at=row["paused_at"],
                      held_by_touch_id=holds.get(row["company_id"]))


def _companies(conn: sqlite3.Connection, ids: set[int]) -> dict[int, sqlite3.Row]:
    found: dict[int, sqlite3.Row] = {}
    listed = sorted(ids)
    for start in range(0, len(listed), CHUNK):
        part = listed[start:start + CHUNK]
        for row in conn.execute(
            "SELECT company_id, name, status, paused_at, held_by_touch FROM company "
            f"WHERE company_id IN ({','.join('?' * len(part))})", part,
        ):
            found[row["company_id"]] = row
    return found


def _other_record(evidence_key: str) -> int | None:
    try:
        return int(evidence_key)
    except ValueError:
        return None


def _review_items(conn: sqlite3.Connection, parents: dict[int, int], holds: dict[int, int]) -> list[WatchItem]:
    rows = conn.execute(
        "SELECT review_id, kind, company_id, reason, evidence_key, created_at FROM review_item "
        "WHERE state = 'open' AND kind IN ('careers_changed', 'merge_candidate') ORDER BY created_at, review_id"
    ).fetchall()
    if not rows:
        return []
    pages = {r[0]: r[1] for r in conn.execute("SELECT company_id, url FROM watched_page")}
    needed: set[int] = set()
    others: dict[int, int | None] = {}
    for r in rows:
        needed.add(survivor_of(parents, r["company_id"]))
        if r["kind"] == "merge_candidate":
            other = _other_record(r["evidence_key"])
            others[r["review_id"]] = other
            if other is not None:
                needed.add(survivor_of(parents, other))
    companies = _companies(conn, needed)
    items = []
    for r in rows:
        mine = survivor_of(parents, r["company_id"])
        if mine not in companies:
            continue
        if r["kind"] == "careers_changed":
            url = pages.get(r["company_id"])
            items.append(WatchItem(
                id=r["review_id"] * 4 + KIND_INDEX["careers_changed"], kind="careers_changed",
                company=_ref(companies[mine], holds), text=r["reason"], at=r["created_at"],
                link=SourceLink(label="Careers page", url=url) if url else None, review_id=r["review_id"],
            ))
            continue
        other = others[r["review_id"]]
        theirs = survivor_of(parents, other) if other is not None else None
        if theirs is None or theirs == mine or theirs not in companies:
            continue  # unreadable, already one company, or a record that is gone: nothing to decide
        items.append(WatchItem(
            id=r["review_id"] * 4 + KIND_INDEX["merge_candidate"], kind="merge_candidate",
            company=_ref(companies[mine], holds),
            text=f"{companies[mine]['name']} and {companies[theirs]['name']} may be the same company.",
            at=r["created_at"], link=None, review_id=r["review_id"],
        ))
    return items


def _new_items(conn: sqlite3.Connection, parents: dict[int, int], holds: dict[int, int], no_go_survivors: set[int],
               now: str) -> list[WatchItem]:
    # The window is rolling: the last 14 times 24 hours before `now`, in UTC, not 14 New York calendar days.
    cutoff = (datetime.strptime(now, ISO_FMT).replace(tzinfo=timezone.utc) - timedelta(days=NEW_WITHIN_DAYS)
              ).strftime(ISO_FMT)
    contacted = {survivor_of(parents, r[0]) for r in conn.execute(CONTACTED_DUPLICATES_SQL)}
    newest: dict[int, sqlite3.Row] = {}
    for r in conn.execute(NEW_COMPANIES_SQL, (cutoff,)):
        if r["company_id"] in contacted or r["company_id"] in no_go_survivors:
            continue
        held = newest.get(r["company_id"])
        if held is None or (r["scored_at"], r["ranker_version"]) > (held["scored_at"], held["ranker_version"]):
            newest[r["company_id"]] = r
    chosen = []
    for r in newest.values():
        if r["hide_type"] not in (None, "null"):
            continue  # hidden with evidence: not a company to reach
        lanes = json.loads(r["lanes"])
        if isinstance(lanes, list) and LANE in lanes:
            chosen.append(r)
    chosen.sort(key=lambda r: (-r["score"], r["company_id"]))
    return [
        WatchItem(
            id=r["company_id"] * 4 + KIND_INDEX["new_reason_no_role"], kind="new_reason_no_role", company=_ref(r, holds),
            text="New company with a reason to reach out and no open role yet.", at=r["created_at"], link=None,
            review_id=None,
        )
        for r in chosen[:NEW_LIMIT]
    ]


def watch_view(conn: sqlite3.Connection, now: str) -> WatchView:
    """Every item on the list, newest first."""
    parents = merge_parents(conn)
    companies = company_map(conn)
    holds = holding_touches(companies)
    _, no_go_survivors = no_go_sets(conn, companies)
    items = _review_items(conn, parents, holds) + _new_items(conn, parents, holds, no_go_survivors, now)
    items.sort(key=lambda i: (i.at, i.id), reverse=True)
    return WatchView(as_of=now, items=items)


# ---------------------------------------------------------------------------------------------- dismiss

def _gone() -> ApiProblem:
    return ApiProblem("not_found", "That item is not on the watch list.")


def dismiss_item(ctx: WriteCtx, item_id: int) -> None:
    """Take one item off the list, inside the write's transaction.

    A careers item resolves its review item as `marked_ok`, so it leaves Needs you too. A new company is remembered
    in `watch_dismissal` under its survivor's id. A merge item is decided in Needs you, never here. An item already
    dismissed is quietly left as it is (no second event).
    """
    ref_id, index = divmod(item_id, 4)
    kind = KIND_OF_INDEX.get(index)
    if kind is None or ref_id < 1:
        raise _gone()
    conn = ctx.conn
    if kind == "new_reason_no_role":
        company = conn.execute("SELECT company_id FROM company WHERE company_id = ?", (ref_id,)).fetchone()
        if company is None:
            raise _gone()
        survivor = survivor_of(merge_parents(conn), ref_id)
        cur = conn.execute(
            "INSERT INTO watch_dismissal(kind, ref_id, dismissed_at) VALUES ('new_reason_no_role', ?, ?) "
            "ON CONFLICT(kind, ref_id) DO NOTHING", (survivor, ctx.now))
        if cur.rowcount:
            append_event(conn, entity="company", entity_id=survivor, company_id=survivor, kind="watch_dismissed",
                         actor="devanshu", at=ctx.now, idempotency_key=ctx.event_key(0),
                         detail={"text": "Dismissed from the watch list."})
        return
    review = conn.execute("SELECT kind, company_id FROM review_item WHERE review_id = ?", (ref_id,)).fetchone()
    if review is None or review["kind"] != kind:
        raise _gone()
    if kind == "merge_candidate":
        raise ApiProblem("not_allowed", "Decide it in Needs you.")
    cur = conn.execute(
        "UPDATE review_item SET state = 'resolved', resolution = 'marked_ok', resolved_at = ?, "
        "resolution_note = 'Marked fine from the watch list.', revision = revision + 1 "
        "WHERE review_id = ? AND state = 'open'", (ctx.now, ref_id))
    if cur.rowcount:
        append_event(conn, entity="review_item", entity_id=ref_id, company_id=review["company_id"],
                     kind="review_resolved", actor="devanshu", at=ctx.now, idempotency_key=ctx.event_key(0),
                     detail={"resolution": "marked_ok", "text": "Careers page change marked fine from the watch list."})
