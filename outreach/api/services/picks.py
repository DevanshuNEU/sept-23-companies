"""Not now and Add to today (top picks, the owner's spec and his rulings of 2026-10-26), each inside the caller's
`run_write` transaction.

Not now (`pass_pick`) is a cooldown, never a ban and never a no-go: the company row is not touched (no status
change, no `no_go`), it stays on every other page, and it comes back to the picks after `queue.pass_cooldown_days`,
or sooner when a new early career opening is first seen after the pass (`outreach.plan.select.held_back`). In one
transaction:

- the pick leaves today's list: its `daily_list_entry` row goes to the state `removed` with `decided_at` and moves
  to the end of the ranks (on this table that state only means "off this day's list"; the company is not removed),
  so no reader shows it, counts it or carries it, and the MCP bridge does not offer it;
- the next best company of the bench (`picks.bench_picks`, the same order the page shows) takes its rank as a new
  company row with an id above every row there is;
- one `pick_passed` event (entity company, actor devanshu) is the record the cooldown reads, with the reason chip,
  the list date, the rank, the company that came in and a snapshot of the signals (stage, size band, fresh, the
  sponsorship line).

It refuses only a row he already reached (a request logged since the list date): that row stays on the list as done.
An application logged on the row never refuses (his ruling: Not now must work on such a row). The same key again
answers with the first answer and writes nothing.

Add to today (`add_from_bench`) appends a bench company after the last rank, while the 7-day limit has room for
another wave (`bench.room_today`); else it says so in a plain sentence. The same key again answers with the row.
"""
import json
import sqlite3
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from outreach.api.errors import ApiProblem
from outreach.api.idempotency import WriteCtx
from outreach.api.models.company import CompanyRef
from outreach.api.models.plan import PassResult, PlanRow
from outreach.api.models.plan_writes import AddFromBenchBody, PassPickBody
from outreach.api.queries import plan as queries
from outreach.api.queries.bench import pending_picks, room_today
from outreach.api.queries.company import merge_parents, survivor_of
from outreach.api.queries.outcomes import NOT_SAVED, company_refs
from outreach.api.revisions import bump
from outreach.insights.bands import default_bands, size_band
from outreach.outcomes.windows import ny_day
from outreach.plan.config import PlanConfig
from outreach.plan.fresh import PASSED, as_utc
from outreach.plan.outbound import load_outbound
from outreach.plan.picks import bench_picks, write_pick
from outreach.plan.prospects import load_prospects
from outreach.plan.route import day_text
from outreach.plan.select import eligibility
from outreach.ranking.common import canonical_ids
from outreach.ranking.scores import ranked_list
from outreach.store import append_event

ADDED = "pick_added"  # event kind: a bench company he added to today's list


def _refuse(text: str) -> ApiProblem:
    return ApiProblem("not_allowed", f"{text} {NOT_SAVED}")


def _event_by_key(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM event WHERE idempotency_key = ?", (key,)).fetchone()


def _ref(conn: sqlite3.Connection, company_id: int) -> CompanyRef:
    return company_refs(conn, {company_id})[company_id]


def _passed_text(name: str, added: PlanRow | None) -> str:
    return f"Passed. Added {added.company.name}." if added is not None else \
        f"Passed on {name}. Nothing else is free to add right now."


def _pass_answer(conn: sqlite3.Connection, detail: dict, cfg: PlanConfig, now: str) -> PassResult:
    added_id = detail.get("added_entry_id")
    added = queries.build_row(conn, added_id, cfg, now) if isinstance(added_id, int) else None
    ref = _ref(conn, detail["company_id"])
    return PassResult(passed_entry_id=detail["entry_id"], company=ref, added=added,
                      text=_passed_text(ref.name, added))


def _snapshot(conn: sqlite3.Connection, survivor: int, row: PlanRow) -> dict:
    """What the company looked like when he passed: stage, size band, a new opening, the sponsorship line."""
    facts = {r["fact"]: r for r in conn.execute(
        "SELECT fact, value, num_low, num_high FROM company_fact WHERE company_id = ? AND fact IN ('size', 'stage')",
        (survivor,))}
    size = facts.get("size")
    opening = row.queue.opening if row.queue is not None else None
    return {"stage": facts["stage"]["value"] if "stage" in facts else None,
            "size_band": size_band(size["num_low"], size["num_high"], default_bands()) if size is not None else None,
            "fresh": bool(opening is not None and opening.fresh),
            "sponsorship": row.queue.sponsorship_line if row.queue is not None else None}


def pass_pick(ctx: WriteCtx, entry_id: int, body: PassPickBody, cfg: PlanConfig) -> PassResult:
    conn = ctx.conn
    key = f"pick-passed:{body.idempotency_key}"
    done = _event_by_key(conn, key)
    if done is not None:  # this Not now landed before: the same answer, nothing written
        return _pass_answer(conn, json.loads(done["detail"]), cfg, ctx.now)
    entry = conn.execute(f"{queries.ENTRY_SQL} WHERE rowid = ?", (entry_id,)).fetchone()
    if entry is None:
        raise ApiProblem("not_found", f"There is no plan row {entry_id}.")
    name = conn.execute("SELECT name FROM company WHERE company_id = ?", (entry["company_id"],)).fetchone()["name"]
    if entry["state"] == "removed":
        raise _refuse(f"{name} is already off today's list.")
    today = ny_day(ctx.now)
    if entry["list_date"] != today:
        raise _refuse(f"That row is on the list of {entry['list_date']}. Not now works on today's list.")
    if entry["revision"] != body.revision:
        raise ApiProblem("conflict", f"The plan row changed since you loaded it. {NOT_SAVED}")
    row = queries.build_row(conn, entry_id, cfg, ctx.now)
    if row is not None and any(s.kind == "request" and s.at for s in row.steps):
        raise _refuse(f"You already reached {name} today, so it stays on the list as done.")
    survivor = survivor_of(merge_parents(conn), entry["company_id"])
    last = conn.execute("SELECT max(rank) FROM daily_list_entry WHERE list_date = ?", (today,)).fetchone()[0]
    bump(conn, "daily_list_entry", "rowid", entry_id, body.revision,
         {"state": "removed", "decided_at": ctx.now, "rank": last + 1}, at=ctx.now)
    picks, stored = bench_picks(conn, date.fromisoformat(today), as_utc(ctx.now), cfg, n=1)
    added_id = None
    if picks:
        added_id = write_pick(conn, picks[0], stored[picks[0].prospect.company_id], today=date.fromisoformat(today),
                              rank=entry["rank"], now_iso=ctx.now, cfg=cfg)
    detail = {"reason": body.reason, "list_date": today, "rank": entry["rank"], "entry_id": entry_id,
              "company_id": survivor, "added_entry_id": added_id,
              "added_company_id": picks[0].prospect.company_id if picks else None,
              "signals": _snapshot(conn, survivor, row) if row is not None else {}}
    append_event(conn, entity="company", entity_id=survivor, company_id=survivor, kind=PASSED, actor="devanshu",
                 at=ctx.now, detail=detail, idempotency_key=key)
    return _pass_answer(conn, detail, cfg, ctx.now)


def add_from_bench(ctx: WriteCtx, company_id: int, body: AddFromBenchBody, cfg: PlanConfig) -> PlanRow:
    conn = ctx.conn
    key = f"bench-added:{body.idempotency_key}"
    done = _event_by_key(conn, key)
    if done is not None:
        row = queries.build_row(conn, json.loads(done["detail"])["entry_id"], cfg, ctx.now)
        if row is not None:
            return row
        raise _refuse("That company was added and has left today's list since.")
    company = conn.execute("SELECT company_id, name FROM company WHERE company_id = ?", (company_id,)).fetchone()
    if company is None:
        raise ApiProblem("not_found", f"There is no company {company_id}.")
    survivor = survivor_of(merge_parents(conn), company_id)
    name = conn.execute("SELECT name FROM company WHERE company_id = ?", (survivor,)).fetchone()["name"]
    today = ny_day(ctx.now)
    if conn.execute("SELECT 1 FROM daily_list WHERE list_date = ?", (today,)).fetchone() is None \
            or not queries.from_plan_job(conn, today):
        raise _refuse("Today's plan is not built yet, so there is no list to add to.")
    listed = conn.execute("SELECT state FROM daily_list_entry WHERE list_date = ? AND company_id = ?",
                          (today, survivor)).fetchone()
    if listed is not None:
        raise _refuse(f"{name} is already on today's list." if listed["state"] != "removed"
                      else f"{name} was on today's list earlier. It can come back from tomorrow.")
    rows = queries.plan_rows(conn, today, cfg, ctx.now) or []
    if room_today(conn, today, cfg, pending_picks(rows)) <= 0:
        raise _refuse("No room to add more this week: the 7-day limit is taken by today's picks and the requests "
                      "already sent.")
    picks, stored = bench_picks(conn, date.fromisoformat(today), as_utc(ctx.now), cfg, n=10_000)
    pick = next((b for b in picks if b.prospect.company_id == survivor), None)
    if pick is None:
        raise _refuse(_not_on_bench(conn, survivor, name, today, ctx.now, cfg))
    last = conn.execute("SELECT max(rank) FROM daily_list_entry WHERE list_date = ?", (today,)).fetchone()[0] or 0
    entry_id = write_pick(conn, pick, stored[survivor], today=date.fromisoformat(today), rank=last + 1,
                          now_iso=ctx.now, cfg=cfg)
    append_event(conn, entity="company", entity_id=survivor, company_id=survivor, kind=ADDED, actor="devanshu",
                 at=ctx.now, idempotency_key=key,
                 detail={"list_date": today, "rank": last + 1, "entry_id": entry_id,
                         "fresh": pick.opening is not None, "early_fit": pick.early_fit})
    row = queries.build_row(conn, entry_id, cfg, ctx.now)
    if row is None:  # unreachable: the row was just written
        raise ApiProblem("not_found", f"There is no plan row {entry_id}.")
    return row


def _not_on_bench(conn: sqlite3.Connection, survivor: int, name: str, today: str, now: str, cfg: PlanConfig) -> str:
    """Why a company is not on the bench, in a plain sentence: his Not now, his application, a wave out, a no-go,
    or no lane today."""
    canon = canonical_ids(conn)
    outbound = load_outbound(conn, canon, tz=cfg.get("plan", "timezone"))
    found = next((p for p in load_prospects(conn, ranked_list(conn, laned_only=True), canon,
                                            date.fromisoformat(today), cfg, outbound, now=as_utc(now))
                  if p.company_id == survivor), None)
    if found is None:
        return f"{name} is not in today's ranked list (a no-go, a pause, a reply or no open lane), so it is not added."
    _, why = eligibility(found, cfg)
    if why == "passed":
        back = found.passed_at.astimezone(ZoneInfo(cfg.get("plan", "timezone"))).date()
        until = back + timedelta(days=cfg.get("queue", "pass_cooldown_days"))
        return (f"You said Not now to {name} on {day_text(back)}. It comes back on {day_text(until)}, or sooner "
                "with a new opening.")
    if why == "applied":
        return f"You applied to {name} on {day_text(found.applied_on)}. It comes back with a new role."
    if why in ("waiting", "engaged"):
        return f"{name} is in flight: {found.contact.reason}."
    return f"{name} is not free to add today."
