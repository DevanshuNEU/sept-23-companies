"""Not now, Add to today and Swap (top picks, the owner's spec and his rulings of 2026-10-26; Browse companies,
#120), each inside the caller's `run_write` transaction.

Not now (`pass_pick`) is a cooldown, never a ban and never a no-go: the company row is not touched (no status
change, no `no_go`), it stays on every other page, and it comes back to the picks after `queue.pass_cooldown_days`,
or sooner when a new early career opening is first seen after the pass (`outreach.plan.select.held_back`). In one
transaction:

- the pick leaves today's list: its `daily_list_entry` row goes to the state `removed` with `decided_at` and moves
  to the end of the ranks (on this table that state only means "off this day's list"; the company is not removed),
  so no reader shows it, counts it or carries it, and the MCP bridge does not offer it;
- the next best company of the bench (`picks.bench_picks`, the same order the page shows) takes its rank as a new
  company row with an id above every row there is;
- Wrong company and Wrong location are data errors, not opinions about the company: `apply_flag`
  (`outreach.api.services.pick_flags`) closes the posting or detaches the board, raises a Needs-you item, and the
  pass gives no cooldown (`load_passes` skips them). Role not for me keeps the posting out of every later pick;
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
from outreach.api.models.plan import PassResult, PlanRow, SwapResult
from outreach.api.models.plan_writes import AddFromBenchBody, PassPickBody, SwapPickBody
from outreach.api.queries import plan as queries
from outreach.api.queries.bench import pending_picks, room_today
from outreach.api.queries.company import merge_parents, survivor_of
from outreach.api.queries.outcomes import NOT_SAVED, company_refs
from outreach.api.services.pick_flags import apply_flag
from outreach.api.revisions import bump
from outreach.insights.bands import default_bands, size_band
from outreach.outcomes.windows import ny_day
from outreach.plan.browse import blocked_text
from outreach.plan.config import PlanConfig
from outreach.plan.fresh import PASSED, as_utc
from outreach.plan.outbound import load_outbound
from outreach.plan.picks import bench_picks, pool, write_pick
from outreach.plan.prospects import load_prospects
from outreach.plan.route import day_text
from outreach.plan.prospects import KEPT_STATUSES
from outreach.plan.select import bench_pick_of, eligibility
from outreach.ranking.common import canonical_ids
from outreach.ranking.scores import ranked_list
from outreach.store import append_event

ADDED = "pick_added"  # event kind: a bench company he added to today's list
SWAPPED = "pick_swapped"  # event kind, on the chosen company: it took a pick's slot
SWAPPED_OUT = "pick_swapped_out"  # event kind, on the replaced company: it went back to the bench (not a Not now)


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
    row = queries.build_row(conn, entry_id, cfg, ctx.now)
    if row is not None and any(s.kind == "request" and s.at for s in row.steps):
        raise _refuse(f"You already reached {name} today, so it stays on the list as done.")
    survivor = survivor_of(merge_parents(conn), entry["company_id"])
    last = conn.execute("SELECT max(rank) FROM daily_list_entry WHERE list_date = ?", (today,)).fetchone()[0]
    # The revision check: a stale one is a conflict and nothing is written.
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
    # Wrong company, Wrong location and a typed reason act on the pick's posting or board, never on the company.
    detail.update(apply_flag(conn, reason=body.reason, note=body.note, company_id=survivor, now=ctx.now,
                             opening=row.queue.opening if row is not None and row.queue is not None else None))
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


def _swap_answer(conn: sqlite3.Connection, detail: dict, cfg: PlanConfig, now: str) -> SwapResult:
    row = queries.build_row(conn, detail["entry_id"], cfg, now)
    if row is None:
        raise _refuse(f"{detail['company']} was swapped in and has left today's list since.")
    return SwapResult(replaced_entry_id=detail["replaced_entry_id"], replaced=_ref(conn, detail["replaced_company_id"]),
                      row=row, text=f"Swapped in {detail['company']} for {detail['replaced_company']}.")


def _swap_target_refusal(conn: sqlite3.Connection, survivor: int, name: str, today: str) -> str | None:
    """The refusal sentence for a company that cannot take a pick's slot (a no-go, already listed, closed, paused or
    holding a reply), or None. The pick rules (`blocked_text`) come after this, on the company's prospect."""
    if conn.execute("SELECT 1 FROM no_go WHERE company_id IN (SELECT company_id FROM company WHERE company_id = ? "
                    "OR merged_into = ?) AND lifted_at IS NULL", (survivor, survivor)).fetchone() is not None:
        return f"{name} is on your no-go list, so it is not swapped in."
    listed = conn.execute("SELECT state FROM daily_list_entry WHERE list_date = ? AND company_id = ?",
                          (today, survivor)).fetchone()
    if listed is not None:
        return f"{name} is already on today's list." if listed["state"] != "removed" \
            else f"{name} was on today's list earlier. It can come back from tomorrow."
    c = conn.execute("SELECT status, paused_at, held_by_touch FROM company WHERE company_id = ?", (survivor,)).fetchone()
    if c["status"] not in KEPT_STATUSES or c["paused_at"] is not None or c["held_by_touch"] is not None:
        return f"{name} is not free to swap in: it is closed, removed, paused or holding a reply."
    return None


def swap_pick(ctx: WriteCtx, entry_id: int, body: SwapPickBody, cfg: PlanConfig) -> SwapResult:
    conn = ctx.conn
    key = f"pick-swap:{body.idempotency_key}"
    done = _event_by_key(conn, key)
    if done is not None:  # this swap landed before: the same answer, nothing written
        return _swap_answer(conn, json.loads(done["detail"]), cfg, ctx.now)
    entry = conn.execute(f"{queries.ENTRY_SQL} WHERE rowid = ?", (entry_id,)).fetchone()
    if entry is None:
        raise ApiProblem("not_found", f"There is no plan row {entry_id}.")
    old = conn.execute("SELECT company_id, name FROM company WHERE company_id = ?", (entry["company_id"],)).fetchone()
    old_name = old["name"]
    today = ny_day(ctx.now)
    if entry["state"] == "removed":
        raise _refuse(f"{old_name} is already off today's list.")
    if entry["list_date"] != today:
        raise _refuse(f"That row is on the list of {entry['list_date']}. A swap works on today's list.")
    row = queries.build_row(conn, entry_id, cfg, ctx.now)
    if row is None:
        raise _refuse(f"{old_name} is already off today's list.")
    if row.state == "skipped":
        raise _refuse(f"{old_name} is carried to tomorrow, so it stays on the list.")
    if row.state == "done":
        raise _refuse(f"{old_name} is already done, so it stays on the list.")
    if any(s.kind == "request" and s.at for s in row.steps):
        raise _refuse(f"You already reached {old_name} today, so it stays on the list.")
    if any(s.at for s in row.steps):
        raise _refuse(f"You already worked {old_name} today, so it stays on the list.")
    if conn.execute("SELECT 1 FROM company WHERE company_id = ?", (body.company_id,)).fetchone() is None:
        raise ApiProblem("not_found", f"There is no company {body.company_id}.")
    parents = merge_parents(conn)
    survivor = survivor_of(parents, body.company_id)
    name = conn.execute("SELECT name FROM company WHERE company_id = ?", (survivor,)).fetchone()["name"]
    why_not = _swap_target_refusal(conn, survivor, name, today)
    if why_not is not None:
        raise _refuse(why_not)
    _, found, _, stored = pool(conn, date.fromisoformat(today), as_utc(ctx.now), cfg, only={survivor})
    prospect = next((p for p in found if p.company_id == survivor), None)
    if prospect is None:
        raise _refuse(f"{name} is not in the ranked list (no open lane, or the rules hide it), so it is not swapped in.")
    blocked = blocked_text(prospect, cfg)
    if blocked is not None:
        raise _refuse(blocked)
    # The revision check: a stale one is a conflict and nothing is written. The old row steps aside to the last rank
    # so the new one can take its rank, then it goes: the pick is back on the bench, with no Not now on record.
    last = conn.execute("SELECT max(rank) FROM daily_list_entry WHERE list_date = ?", (today,)).fetchone()[0]
    bump(conn, "daily_list_entry", "rowid", entry_id, body.revision,
         {"state": "removed", "decided_at": ctx.now, "rank": last + 1}, at=ctx.now)
    new_id = write_pick(conn, bench_pick_of(prospect, cfg), stored[survivor], today=date.fromisoformat(today),
                        rank=entry["rank"], now_iso=ctx.now, cfg=cfg)
    conn.execute("DELETE FROM daily_list_entry WHERE rowid = ?", (entry_id,))
    old_survivor = survivor_of(parents, old["company_id"])
    old_survivor_name = conn.execute("SELECT name FROM company WHERE company_id = ?", (old_survivor,)).fetchone()["name"]
    detail = {"list_date": today, "rank": entry["rank"], "entry_id": new_id, "replaced_entry_id": entry_id,
              "company": name, "company_id": survivor, "replaced_company_id": old_survivor,
              "replaced_company": old_survivor_name}
    append_event(conn, entity="company", entity_id=survivor, company_id=survivor, kind=SWAPPED, actor="devanshu",
                 at=ctx.now, idempotency_key=key,
                 detail={**detail, "text": f"Swapped in for {old_survivor_name} on today's list."})
    append_event(conn, entity="company", entity_id=old_survivor, company_id=old_survivor, kind=SWAPPED_OUT,
                 actor="devanshu", at=ctx.now,
                 detail={**detail, "text": f"Swapped out for {name} on today's list. It is back on the bench."})
    return _swap_answer(conn, detail, cfg, ctx.now)
