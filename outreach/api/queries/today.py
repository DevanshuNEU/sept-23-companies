"""The lean Today: the day's list, built from the ranked list, stored, and written by pick, unpick, backlog, remove
and restore.

THIS IS LEAN. It stands in until #15's daily plan exists, and #15's plan replaces the building, the capacity and the
`stillEmailing` rows here (the shapes stay). Left out on purpose: find-someone rows, notes, and the prepare step.

How the list is made (only with `[today] lean_build` on; off as shipped, so the plan job builds the day and this
page shows `not_built` until it has). The first read of a weekday (New York) builds that day's list in one short write
transaction, so the same people stay in the same places all day and a pick sticks:

- who: `outreach.ranking.scores.ranked_list(laned_only=True)` of the newest ranker version, as
  `outreach.mcp.tools.reads.ranked_companies` filters it: no hide, a lane, not a merged duplicate, no live touch
  (an outreach touch not undone, or a first email sent), no active no-go (through the merged family), no reply hold.
  Only a candidate or backlog company is proposed (a working one is already picked; closed and removed are done);
- how many: `proposals_per_slot` times the day's capacity, best score first, ranks 1 up;
- capacity: `linkedin_per_day` (7, #9) plus the sender's effective limit for the day (`outreach.sending.status`; not
  reduced by what went out), none while it is paused, signed out or gated (Message-ID unproven), at most 60 (the
  table's CHECK). The shell uses the same rule (`capacity_from`);
- the backlog: a company whose newest earlier row is `backlog` and whose status is still `backlog` comes back as a
  backlog row. An unpicked proposal does not carry: the ranking proposes it again if it still ranks.

Weekends have no list; the backlog of the newest list still shows. A write on a row checks the company's revision,
which is the row's `revision` on the wire (a pick, unpick, backlog, remove and restore each change the company's
status, so each moves it).
"""
import json
import sqlite3
from datetime import date

from outreach.api.config import ApiConfig
from outreach.api.db import busy_problem, connect_for, is_busy
from outreach.api.errors import ApiProblem
from outreach.api.idempotency import WriteCtx
from outreach.api.models.base import Empty
from outreach.api.models.company import Fact, RoleToName
from outreach.api.models.health import JobRunSummary
from outreach.api.models.today import (
    DailyCompany, DailyEntry, NextStep, NotReady, OtherRole, StillEmailing, TodayView,
)
from outreach.api.queries import jobs
from outreach.api.queries.company import (
    DAYS_OF_EMAIL, FACT_ORDER, best_facts, company_holds, merge_parents, sponsorship_shown, survivor_of, to_fact,
)
from outreach.api.queries.outbox import stored_checks
from outreach.api.queries.outcomes import by_ids, company_refs
from outreach.api.queries.sending import SenderRead, read_sender
from outreach.api.revisions import bump
from outreach.drafts.followups import canonical_company, company_map, held_survivors, no_go_sets, resolve_company
from outreach.outcomes.windows import ny_day
from outreach.ranking.scores import latest_version, ranked_list
from outreach.sending import calendar
from outreach.sending.config import load_config as load_send_config
from outreach.store import transaction
from outreach.store.writes import append_event

NOT_SAVED = "Nothing was saved."
MAX_CAPACITY = 60  # daily_list.capacity's CHECK
STOPPED_STATES = ("paused_by_you", "paused_hard_block", "signed_out")
PROPOSABLE = ("candidate", "backlog")
LIVE_MAIL = ("approved", "sending", "send_unknown", "sent")
OTHER_ROLES = 5
LANE_WORDS = {"level_role": "a role at your level", "intern_junior": "an internship or junior role",
              "any_us_role": "a US role", "reason_no_role": "a reason to reach out and no open role"}
WEEKEND_TEXT = "No list on weekends. The backlog is still here."
NOT_BUILT_TEXT = "Today's list is not ready. No ranking has run yet, so there is nothing to propose."


# ---------------------------------------------------------------------------------------------- capacity

def capacity_from(read: SenderRead, cfg: ApiConfig) -> int:
    """The one capacity rule, for Today and the shell: LinkedIn per day plus the sender's effective limit for the day
    (not what it has sent so far, so the number never depends on when Today is first opened). The sender adds no
    email slots when it is paused or signed out, when the tick's gate is shut (an old sender still running, a
    Message-ID not yet proven), or when its config cannot be loaded."""
    snap = read.snapshot
    email = 0 if snap is None or read.gate is not None or snap.state in STOPPED_STATES else snap.limit_today
    return min(MAX_CAPACITY, cfg.linkedin_per_day + email)


def capacity_for(conn: sqlite3.Connection, now: str, cfg: ApiConfig, *, old_sender_loaded: bool) -> int:
    """`capacity_from` for a page that does not need the sender's line too."""
    return capacity_from(read_sender(conn, now, old_sender_loaded=old_sender_loaded), cfg)


def _weekday(day: str) -> bool:
    return date.fromisoformat(day).weekday() < 5


# ---------------------------------------------------------------------------------------------- the build

def _touched_survivors(conn: sqlite3.Connection, companies: dict) -> set[int]:
    """Survivors with a live touch: any outreach touch not undone, or a first email that went out."""
    ids = {r[0] for r in conn.execute("SELECT DISTINCT company_id FROM outreach_touch WHERE undone_at IS NULL")}
    ids |= {r[0] for r in conn.execute("SELECT DISTINCT p.company_id FROM touch t JOIN person p "
                                       "ON p.person_id = t.person_id WHERE t.first_sent_at IS NOT NULL")}
    return {resolve_company(companies, c)[-1] for c in ids if c in companies}


def build_list(conn: sqlite3.Connection, now: str, day: str, capacity: int, cfg: ApiConfig) -> bool:
    """Store `day`'s list unless it exists or no ranking has run. Run inside a write transaction."""
    if conn.execute("SELECT 1 FROM daily_list WHERE list_date = ?", (day,)).fetchone() is not None:
        return False
    version = latest_version(conn)
    if version is None:
        return False
    companies = company_map(conn)
    held = held_survivors(companies)
    barred = no_go_sets(conn, companies)[1] | held | _touched_survivors(conn, companies)

    def open_to_him(company_id: int) -> bool:
        row = companies.get(company_id)
        return row is not None and row["merged_into"] is None and company_id not in barred

    carried = []
    for r in conn.execute(
            "SELECT e.company_id, e.list_group, e.posting_id, e.why FROM daily_list_entry e WHERE e.state = 'backlog' "
            "AND e.list_date = (SELECT max(x.list_date) FROM daily_list_entry x WHERE x.company_id = e.company_id "
            "AND x.list_date < ?) ORDER BY e.list_date DESC, e.rank", (day,)):
        if open_to_him(r["company_id"]) and companies[r["company_id"]]["status"] == "backlog":
            carried.append(r)
    carried_ids = {r["company_id"] for r in carried}
    wanted = max(1, capacity * cfg.proposals_per_slot)
    proposals = [s for s in ranked_list(conn, version, laned_only=True)
                 if s.company_id not in carried_ids and open_to_him(s.company_id)
                 and companies[s.company_id]["status"] in PROPOSABLE][:wanted]
    conn.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES (?, ?, ?)", (day, now, capacity))
    rank = 0
    for s in proposals:
        rank += 1
        reason = (s.signals.get("rank_reason") or {}).get("line") or ""
        conn.execute(
            "INSERT INTO daily_list_entry(list_date, company_id, list_group, rank, state, posting_id, why, "
            "proposed_at) VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?)",
            (day, s.company_id, s.lanes[0], rank, s.signals.get("best_posting_id"),
             json.dumps({"rankReason": reason, "rankerVersion": version, "score": s.score}, sort_keys=True), now))
    for r in carried:
        rank += 1
        conn.execute(
            "INSERT INTO daily_list_entry(list_date, company_id, list_group, rank, state, posting_id, why, "
            "proposed_at, decided_at) VALUES (?, ?, ?, ?, 'backlog', ?, ?, ?, ?)",
            (day, r["company_id"], r["list_group"], rank, r["posting_id"], r["why"], now, now))
    return True


def ensure_list(settings, now: str, cfg: ApiConfig, *, old_sender_loaded: bool) -> None:
    """Build today's list on the first read of a weekday: one short write transaction of its own (a read of the
    page, not a write of his, so it takes no idempotency key). The row's primary key makes a race build it once.

    Only with `[today] lean_build` on. It is off as shipped: the plan job (#15) builds the day, and a list built here
    first would make that job stop with `already_built` and leave no people plan for the date."""
    if not cfg.lean_build:
        return
    day = ny_day(now)
    if not _weekday(day):
        return
    conn = connect_for(settings)
    try:
        if conn.execute("SELECT 1 FROM daily_list WHERE list_date = ?", (day,)).fetchone() is not None:
            return
        with transaction(conn):
            if conn.execute("SELECT 1 FROM daily_list WHERE list_date = ?", (day,)).fetchone() is None:
                build_list(conn, now, day, capacity_for(conn, now, cfg, old_sender_loaded=old_sender_loaded), cfg)
    except sqlite3.OperationalError as err:
        if is_busy(err):
            raise busy_problem() from err
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------- the view

def _json(text: str) -> dict:
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def build_entries(conn: sqlite3.Connection, rows: list[sqlite3.Row], parents: dict[int, int],
                  holds=None) -> list[DailyEntry]:
    """A `DailyEntry` for each `daily_list_entry` row, in the rows' order, answered as the company's survivor.
    One query per table, however many rows."""
    if not rows:
        return []
    survivor = {r["company_id"]: survivor_of(parents, r["company_id"]) for r in rows}
    owners = set(survivor.values())
    family = owners | {c for c in parents if survivor_of(parents, c) in owners}
    owner_of = {c: survivor_of(parents, c) for c in family}
    companies = {c["company_id"]: c for c in by_ids(conn, "SELECT * FROM company WHERE company_id IN ({ids})", owners)}
    refs = company_refs(conn, owners, holds=holds if holds is not None else company_holds(conn))
    facts: dict[int, list[sqlite3.Row]] = {}
    for f in by_ids(conn, "SELECT * FROM company_fact WHERE company_id IN ({ids})", family):
        facts.setdefault(owner_of[f["company_id"]], []).append(f)
    lca: dict[int, list[sqlite3.Row]] = {}
    for e in by_ids(conn, "SELECT * FROM evidence WHERE fact = 'lca' AND company_id IN ({ids})", family):
        lca.setdefault(owner_of[e["company_id"]], []).append(e)
    postings: dict[int, list[sqlite3.Row]] = {}
    for p in by_ids(conn, "SELECT * FROM posting WHERE closed_at IS NULL AND company_id IN ({ids}) "
                          "ORDER BY first_seen_at DESC, posting_id DESC", family):
        postings.setdefault(owner_of[p["company_id"]], []).append(p)
    decisions = {d["posting_id"]: d for d in by_ids(
        conn, "SELECT d.* FROM posting_decision d JOIN posting p ON p.posting_id = d.posting_id "
              "WHERE p.closed_at IS NULL AND p.company_id IN ({ids})", family)}
    domains = {r["domain"]: r["status"] for r in conn.execute(
        "SELECT domain, status FROM mx_check WHERE domain IN (SELECT value FROM json_each(?))",
        (json.dumps(sorted({f["value"] for fs in facts.values() for f in fs if f["fact"] == "domain"})),))}
    people: dict[int, int] = {}
    for r in by_ids(conn, "SELECT company_id, count(*) AS n FROM person WHERE company_id IN ({ids}) "
                          "GROUP BY company_id", family):
        people[owner_of[r["company_id"]]] = people.get(owner_of[r["company_id"]], 0) + r["n"]
    drafts: dict[int, int] = {}
    for r in by_ids(conn, "SELECT p.company_id, count(*) AS n FROM message m JOIN touch t ON t.touch_id = m.touch_id "
                          "JOIN person p ON p.person_id = t.person_id WHERE m.state = 'drafted' "
                          "AND p.company_id IN ({ids}) GROUP BY p.company_id", family):
        drafts[owner_of[r["company_id"]]] = drafts.get(owner_of[r["company_id"]], 0) + r["n"]
    first_on: dict[int, str] = {}
    for r in by_ids(conn, "SELECT company_id, min(list_date) AS d FROM daily_list_entry WHERE company_id IN ({ids}) "
                          "GROUP BY company_id", family):
        owner = owner_of[r["company_id"]]
        first_on[owner] = min(r["d"], first_on.get(owner, r["d"]))
    out = []
    for r in rows:
        cid = survivor[r["company_id"]]
        best = best_facts(facts.get(cid, []))
        domain = best["domain"]["value"] if "domain" in best else None
        pick = next((p for p in postings.get(cid, []) if p["posting_id"] == r["posting_id"]), None)
        d = decisions.get(pick["posting_id"]) if pick is not None else None
        role = None
        if pick is not None:
            role = RoleToName(
                posting_id=pick["posting_id"], title=pick["title"], url=pick["url"], location=pick["location"],
                first_seen_at=pick["first_seen_at"], level_tier=d["level_tier"] if d else "not_stated",
                years_asked=d["years_asked"] if d else None, start2027=bool(d["start_2027"]) if d else False)
        others = [p for p in postings.get(cid, []) if pick is None or p["posting_id"] != pick["posting_id"]
                  if (decisions.get(p["posting_id"]) is None or decisions[p["posting_id"]]["lane"] == "engineering")]
        why = _json(r["why"]).get("rankReason") or f"Ranked for {LANE_WORDS[r['list_group']]}."
        mail = domains.get(domain) if domain else None
        out.append(DailyEntry(
            company=DailyCompany(**refs[cid].model_dump(), domain=domain,
                                 hq=best["hq"]["value"] if "hq" in best else ""),
            list_group=r["list_group"], rank=r["rank"], state=r["state"], revision=companies[cid]["revision"],
            role=role,
            size=to_fact(best["size"]) if "size" in best else None,
            stage=to_fact(best["stage"]) if "stage" in best else None,
            sponsorship=sponsorship_shown(best, {"sponsorship": d["sponsorship"] if d else "silent"} if pick else None,
                                          [], lca.get(cid, [])),
            sponsorship_fact=to_fact(best["sponsorship"]) if "sponsorship" in best else None,
            domain_fact=to_fact(best["domain"]) if "domain" in best else None,
            mail_server_ok=None if mail is None else mail == "ok",
            why=why, other_roles=[OtherRole(title=p["title"], first_seen_at=p["first_seen_at"])
                                  for p in others[:OTHER_ROLES]],
            people_count=people.get(cid, 0), draft_count=drafts.get(cid, 0),
            first_proposed_on=first_on.get(cid, r["list_date"])))
    return out


def _entry_rows(conn: sqlite3.Connection, day: str, states: tuple[str, ...], order: str) -> list[sqlite3.Row]:
    marks = ",".join("?" * len(states))
    return conn.execute(f"SELECT * FROM daily_list_entry WHERE list_date = ? AND state IN ({marks}) ORDER BY {order}",
                        (day, *states)).fetchall()


def picked_count(conn: sqlite3.Connection, day: str) -> int:
    """Picked rows of `day` whose company (its survivor) is still being worked: a pick that was closed or removed
    since is not a pick any more, so it never counts against the day's capacity. The page, the shell and a pick's
    capacity check all count this way."""
    rows = conn.execute("SELECT company_id FROM daily_list_entry WHERE list_date = ? AND state = 'picked'",
                        (day,)).fetchall()
    if not rows:
        return 0
    parents = merge_parents(conn)
    owners = {survivor_of(parents, r[0]) for r in rows}
    working = {r[0] for r in by_ids(conn, "SELECT company_id FROM company WHERE status = 'working' "
                                         "AND company_id IN ({ids})", owners)}
    return sum(1 for r in rows if survivor_of(parents, r[0]) in working)


def _shown(conn: sqlite3.Connection, rows: list[sqlite3.Row], parents: dict[int, int]) -> list[sqlite3.Row]:
    """The rows still worth showing. Rows are stored once, so a company closed, removed, given a no-go or held by a
    reply after the build leaves the list here (judged as its survivor, through the sender's own sets). A picked
    row that is held stays: he picked it and it is being worked."""
    if not rows:
        return rows
    companies = company_map(conn)
    held = held_survivors(companies)
    no_go = no_go_sets(conn, companies)[1]
    shown = []
    for r in rows:
        survivor = survivor_of(parents, r["company_id"])
        company = companies.get(survivor)
        if company is None or company["status"] in ("closed", "removed") or survivor in no_go:
            continue
        if survivor in held and r["state"] != "picked":
            continue
        shown.append(r)
    return shown


def _still_emailing(conn: sqlite3.Connection, now: str, day: str, parents: dict[int, int], holds) -> list[StillEmailing]:
    """Companies picked on an earlier day that are still being emailed (working), with where each stands."""
    picked = {survivor_of(parents, r["company_id"]): r["d"] for r in conn.execute(
        "SELECT company_id, max(list_date) AS d FROM daily_list_entry WHERE state = 'picked' AND list_date < ? "
        "GROUP BY company_id", (day,))}
    companies = {c["company_id"]: c for c in by_ids(
        conn, "SELECT * FROM company WHERE status = 'working' AND merged_into IS NULL AND company_id IN ({ids})",
        set(picked))}
    if not companies:
        return []
    family = set(companies) | {c for c in parents if survivor_of(parents, c) in companies}
    owner_of = {c: survivor_of(parents, c) for c in family}
    emailed: dict[int, int] = {}
    budget: dict[int, int] = {}
    drafts: dict[int, list[sqlite3.Row]] = {}
    for p in by_ids(conn, "SELECT person_id, company_id, channel FROM person WHERE company_id IN ({ids})", family):
        if p["channel"] == "email":
            budget[owner_of[p["company_id"]]] = budget.get(owner_of[p["company_id"]], 0) + 1
    for t in by_ids(conn, "SELECT p.company_id, t.first_sent_at FROM touch t JOIN person p ON p.person_id = t.person_id "
                          "WHERE t.first_sent_at IS NOT NULL AND p.company_id IN ({ids})", family):
        emailed[owner_of[t["company_id"]]] = emailed.get(owner_of[t["company_id"]], 0) + 1
    for m in by_ids(conn, "SELECT p.company_id, m.checks FROM message m JOIN touch t ON t.touch_id = m.touch_id "
                          "JOIN person p ON p.person_id = t.person_id WHERE m.state = 'drafted' AND t.state = 'open' "
                          "AND p.company_id IN ({ids})", family):
        drafts.setdefault(owner_of[m["company_id"]], []).append(m)
    refs = company_refs(conn, set(companies), holds=holds)
    today = date.fromisoformat(day)
    try:
        cfg = load_send_config()
    except (ValueError, OSError):
        cfg = None  # the day count needs the sender's calendar; without it every company reads as day 1
    out = []
    for cid, row in sorted(companies.items(), key=lambda kv: (picked[kv[0]], kv[0])):
        ref = refs[cid]
        waiting = drafts.get(cid, [])
        blocked = sum(1 for m in waiting if any(c.severity == "block" for c in stored_checks(m["checks"])))
        if ref.held:
            who = ref.held_by_reply.person.name if ref.held_by_reply and ref.held_by_reply.person else "the person who replied"
            state, step = "held", NextStep(kind="answer_reply", text=f"Answer {who} in your inbox")
        elif ref.paused_at:
            state, step = "paused", NextStep(kind="paused", text="Paused by you")
        elif blocked:
            state, step = "emailing", NextStep(kind="fix_review", text=f"{blocked} blocked draft{'' if blocked == 1 else 's'}: fix before approving")
        elif waiting:
            state, step = "emailing", NextStep(kind="review_drafts", text=f"{len(waiting)} draft{'' if len(waiting) == 1 else 's'} to review")
        else:
            state, step = "waiting", NextStep(kind="nothing", text="Nothing. Follow-ups are scheduled")
        gone = 0 if cfg is None else calendar.business_days_between(date.fromisoformat(picked[cid]), today, cfg)
        out.append(StillEmailing(
            company=ref, day=max(1, min(DAYS_OF_EMAIL, 1 + gone)), days=DAYS_OF_EMAIL, emailed=emailed.get(cid, 0),
            budget=max(budget.get(cid, 0), emailed.get(cid, 0)), next_step=step, state=state))
    return out


def today_view(conn: sqlite3.Connection, now: str, cfg: ApiConfig, *, old_sender_loaded: bool) -> TodayView:
    day = ny_day(now)
    parents = merge_parents(conn)
    holds = company_holds(conn)
    built = conn.execute("SELECT * FROM daily_list WHERE list_date = ?", (day,)).fetchone()
    if built is not None:
        shown, capacity = day, built["capacity"]
        proposed = build_entries(
            conn, _shown(conn, _entry_rows(conn, day, ("proposed", "picked"), "rank"), parents), parents, holds)
        picked = picked_count(conn, day)
        not_ready = None
    else:
        newest = conn.execute("SELECT max(list_date) FROM daily_list WHERE list_date < ?", (day,)).fetchone()[0]
        shown, proposed, picked = newest, [], 0
        capacity = capacity_for(conn, now, cfg, old_sender_loaded=old_sender_loaded)
        not_ready = NotReady(reason="weekend", text=WEEKEND_TEXT) if not _weekday(day) \
            else NotReady(reason="not_built", text=NOT_BUILT_TEXT)
    backlog = build_entries(
        conn, _shown(conn, _entry_rows(conn, shown, ("backlog",), "decided_at DESC, rank"), parents), parents, holds) \
        if shown else []
    last = conn.execute("SELECT * FROM job_run WHERE job = 'daily_list' ORDER BY started_at DESC, run_id DESC LIMIT 1").fetchone()
    return TodayView(
        as_of=now, date=day, built_at=built["built_at"] if built is not None else None, capacity=capacity, picked=picked,
        not_ready=not_ready, proposed=proposed, backlog=backlog,
        still_emailing=_still_emailing(conn, now, day, parents, holds),
        list_job=jobs.run_summary(last, now) if last is not None else None)


# ---------------------------------------------------------------------------------------------- the writes

def _survivor_row(conn: sqlite3.Connection, company_id: int) -> sqlite3.Row:
    if conn.execute("SELECT 1 FROM company WHERE company_id = ?", (company_id,)).fetchone() is None:
        raise ApiProblem("not_found", f"There is no company {company_id}.")
    return conn.execute("SELECT * FROM company WHERE company_id = ?", (canonical_company(conn, company_id),)).fetchone()


def _fresh(row: sqlite3.Row, revision: int) -> None:
    if row["revision"] != revision:
        raise ApiProblem("conflict", f"The company changed since you loaded it. {NOT_SAVED}")


def _refuse(text: str) -> ApiProblem:
    return ApiProblem("not_allowed", f"{text} {NOT_SAVED}")


PLAN_DAY = "Today is a plan of people now. Use the plan to skip a row."


def _not_on_a_plan_day(conn: sqlite3.Connection, now: str) -> None:
    """Pick, unpick, backlog and restore act on today's `daily_list_entry` rows. On a day #15's plan job built, those
    rows are the people plan's, so these writes are refused and write nothing (the plan's own skip carries a row).
    The test is `plan_view`'s: a stored list whose rows name a channel, or that a `plan` run built. A day with no list
    or with the lean Today's list keeps the old behaviour. Remove is not refused: it is the web's Not a fit."""
    from outreach.api.queries.plan import from_plan_job  # plan imports this module

    day = ny_day(now)
    if conn.execute("SELECT 1 FROM daily_list WHERE list_date = ?", (day,)).fetchone() is not None \
            and from_plan_job(conn, day):
        raise ApiProblem("not_allowed", PLAN_DAY)


def _family(conn: sqlite3.Connection, survivor: int) -> list[int]:
    companies = company_map(conn)
    return sorted(c for c in companies if resolve_company(companies, c)[-1] == survivor)


def _blocker(conn: sqlite3.Connection, family: list[int]) -> str | None:
    """What stops a company leaving the picks or being removed, in words: the email approved or sent, the LinkedIn
    request or message logged, or the application logged. None when nothing has gone out or been cleared for it."""
    marks = ",".join("?" * len(family))
    states = ",".join("?" * len(LIVE_MAIL))
    if conn.execute(
            f"SELECT 1 FROM message m JOIN touch t ON t.touch_id = m.touch_id JOIN person p ON p.person_id = t.person_id "
            f"WHERE p.company_id IN ({marks}) AND m.state IN ({states}) LIMIT 1", (*family, *LIVE_MAIL)).fetchone():
        return "email approved or sent"
    found = {r[0] for r in conn.execute(
        f"SELECT DISTINCT channel FROM outreach_touch WHERE company_id IN ({marks}) AND undone_at IS NULL", family)}
    if found & {"linkedin_request", "linkedin_message"}:
        return "a LinkedIn request or message logged"
    if "application" in found:
        return "an application logged"
    if found:
        return "a touch logged"
    return None


def _todays_entry(conn: sqlite3.Connection, now: str, survivor: int, name: str) -> sqlite3.Row:
    day = ny_day(now)
    if conn.execute("SELECT 1 FROM daily_list WHERE list_date = ?", (day,)).fetchone() is None:
        raise _refuse(f"There is no list today, so {name} cannot be moved on it. Lists start on the next weekday.")
    family = _family(conn, survivor)
    marks = ",".join("?" * len(family))
    row = conn.execute(f"SELECT * FROM daily_list_entry WHERE list_date = ? AND company_id IN ({marks})",
                       (day, *family)).fetchone()
    if row is None:
        raise _refuse(f"{name} is not on today's list.")
    return row


def _event(ctx: WriteCtx, company_id: int, kind: str, detail: dict) -> None:
    append_event(ctx.conn, entity="company", entity_id=company_id, company_id=company_id, kind=kind,
                 actor="devanshu", at=ctx.now, detail=detail, idempotency_key=ctx.event_key(0))


def _entry_answer(ctx: WriteCtx, company_id: int) -> DailyEntry:
    day = ny_day(ctx.now)
    row = ctx.conn.execute("SELECT * FROM daily_list_entry WHERE list_date = ? AND company_id = ?",
                           (day, company_id)).fetchone()
    return build_entries(ctx.conn, [row], merge_parents(ctx.conn))[0]


def _set_entry(ctx: WriteCtx, entry: sqlite3.Row, state: str, why: dict | None = None) -> None:
    decided = None if state == "proposed" else ctx.now
    ctx.conn.execute("UPDATE daily_list_entry SET state = ?, decided_at = ?, why = ? WHERE list_date = ? "
                     "AND company_id = ?",
                     (state, decided, json.dumps(why if why is not None else _json(entry["why"]), sort_keys=True),
                      entry["list_date"], entry["company_id"]))


def pick(ctx: WriteCtx, company_id: int, revision: int) -> DailyEntry:
    conn = ctx.conn
    row = _survivor_row(conn, company_id)
    _not_on_a_plan_day(conn, ctx.now)
    survivor, name = row["company_id"], row["name"]
    entry = _todays_entry(conn, ctx.now, survivor, name)
    if entry["state"] == "picked":
        raise _refuse(f"{name} is already picked.")
    if row["status"] not in PROPOSABLE:
        raise _refuse(f"{name} is {row['status']}, so it cannot be picked.")
    _fresh(row, revision)
    companies = company_map(conn)
    if survivor in no_go_sets(conn, companies)[1]:
        raise _refuse(f"{name} is on the no-go list. Take it off the list first.")
    if survivor in held_survivors(companies):
        raise _refuse(f"{name} is held by a reply. Answer it first.")
    capacity = conn.execute("SELECT capacity FROM daily_list WHERE list_date = ?", (entry["list_date"],)).fetchone()[0]
    if picked_count(conn, entry["list_date"]) >= capacity:
        raise ApiProblem("capacity", f"Today's limit carries {capacity} companies. Unpick one first. {NOT_SAVED}")
    bump(conn, "company", "company_id", survivor, revision,
         {"status": "working", "status_at": ctx.now}, at=ctx.now)
    why = {**_json(entry["why"]), "pickedFrom": row["status"]}
    _set_entry(ctx, entry, "picked", why)
    _event(ctx, survivor, "picked", {"text": "Picked from Today", "list_date": entry["list_date"],
                                     "previous_status": row["status"]})
    return _entry_answer(ctx, entry["company_id"])


def _leave_picked(ctx: WriteCtx, row: sqlite3.Row, entry: sqlite3.Row, what: str) -> None:
    """A picked company can leave the picks only while nothing has gone out or been cleared for it."""
    blocker = _blocker(ctx.conn, _family(ctx.conn, row["company_id"]))
    if blocker is not None:
        raise _refuse(f"{row['name']} already has {blocker}, so it cannot be {what}. Pause or close it on its page "
                      f"instead.")
    if row["paused_at"] is not None:
        raise _refuse(f"{row['name']} is paused. Resume it first, then {what.replace('moved', 'move')}.")


def unpick(ctx: WriteCtx, company_id: int, revision: int) -> DailyEntry:
    conn = ctx.conn
    row = _survivor_row(conn, company_id)
    _not_on_a_plan_day(conn, ctx.now)
    survivor, name = row["company_id"], row["name"]
    entry = _todays_entry(conn, ctx.now, survivor, name)
    if entry["state"] != "picked":
        raise _refuse(f"{name} is not picked.")
    if row["status"] != "working":
        raise _refuse(f"{name} is {row['status']}, so it cannot be unpicked.")
    _fresh(row, revision)
    _leave_picked(ctx, row, entry, "unpicked")
    before = _json(entry["why"]).get("pickedFrom")
    status = before if before in PROPOSABLE else "candidate"
    bump(conn, "company", "company_id", survivor, revision, {"status": status, "status_at": ctx.now}, at=ctx.now)
    _set_entry(ctx, entry, "proposed")
    _event(ctx, survivor, "unpicked", {"text": "Taken off the picks", "list_date": entry["list_date"], "status": status})
    return _entry_answer(ctx, entry["company_id"])


def backlog(ctx: WriteCtx, company_id: int, revision: int) -> DailyEntry:
    conn = ctx.conn
    row = _survivor_row(conn, company_id)
    _not_on_a_plan_day(conn, ctx.now)
    survivor, name = row["company_id"], row["name"]
    entry = _todays_entry(conn, ctx.now, survivor, name)
    if entry["state"] == "backlog":
        raise _refuse(f"{name} is already in the backlog.")
    if entry["state"] == "removed" or row["status"] not in (*PROPOSABLE, "working"):
        raise _refuse(f"{name} is {row['status']}, so it cannot go to the backlog.")
    _fresh(row, revision)
    if entry["state"] == "picked":
        _leave_picked(ctx, row, entry, "moved to the backlog")
    bump(conn, "company", "company_id", survivor, revision, {"status": "backlog", "status_at": ctx.now}, at=ctx.now)
    _set_entry(ctx, entry, "backlog")
    _event(ctx, survivor, "moved_to_backlog", {"text": "Moved to the backlog", "list_date": entry["list_date"],
                                               "previous_status": row["status"]})
    return _entry_answer(ctx, entry["company_id"])


def remove(ctx: WriteCtx, company_id: int, reason: str, note: str, revision: int) -> Empty:
    """Take a company off for good: `status removed` with its reason (no `no_go` row is written), and it is never
    proposed again on its own. One with email, a LinkedIn request or an application out is closed on its page, not removed."""
    conn = ctx.conn
    row = _survivor_row(conn, company_id)
    survivor, name = row["company_id"], row["name"]
    if row["status"] in ("closed", "removed"):
        raise _refuse(f"{name} is already {row['status']}.")
    _fresh(row, revision)
    blocker = _blocker(conn, _family(conn, survivor))
    if blocker is not None:
        raise _refuse(f"{name} already has {blocker}. Close it on its page instead.")
    if row["paused_at"] is not None:
        raise _refuse(f"{name} is paused. Resume it, or close it on its page.")
    bump(conn, "company", "company_id", survivor, revision,
         {"status": "removed", "remove_reason": reason, "status_note": note, "status_at": ctx.now}, at=ctx.now)
    conn.execute("UPDATE daily_list_entry SET state = 'removed', decided_at = ? WHERE list_date = ? AND company_id = ?",
                 (ctx.now, ny_day(ctx.now), survivor))
    _event(ctx, survivor, "removed", {"text": f"Removed by you: {note}" if note else "Removed by you",
                                      "reason": reason, "note": note, "previous_status": row["status"]})
    return Empty()


def restore(ctx: WriteCtx, company_id: int, reason: str, revision: int) -> Empty:
    """Bring a removed company back to the backlog. It needs a reason. Its row on today's list becomes a backlog row
    (one is added when its newest row is from an older day), so it shows at once; on a weekend its newest row becomes
    the backlog row and the next build carries it. A merged-away duplicate is not restored here: unmerge it in
    Needs you."""
    conn = ctx.conn
    why = reason.strip()
    row = conn.execute("SELECT * FROM company WHERE company_id = ?", (company_id,)).fetchone()
    if row is None:
        raise ApiProblem("not_found", f"There is no company {company_id}.")
    _not_on_a_plan_day(conn, ctx.now)
    if row["merged_into"] is not None:
        raise _refuse(f"{row['name']} was merged into another company. Unmerge it in Needs you instead.")
    if row["status"] != "removed":
        raise _refuse(f"{row['name']} is not removed.")
    if not why:
        raise ApiProblem("bad_request", f"Say why you are bringing it back. {NOT_SAVED}")
    _fresh(row, revision)
    bump(conn, "company", "company_id", company_id, revision,
         {"status": "backlog", "remove_reason": None, "status_note": "", "status_at": ctx.now}, at=ctx.now)
    day = ny_day(ctx.now)
    newest = conn.execute("SELECT * FROM daily_list_entry WHERE company_id = ? ORDER BY list_date DESC LIMIT 1",
                          (company_id,)).fetchone()
    listed_today = conn.execute("SELECT 1 FROM daily_list WHERE list_date = ?", (day,)).fetchone() is not None
    if newest is not None and (newest["list_date"] == day or not listed_today):
        # Today's own row, or a weekend (no list): the newest row becomes the backlog row the next build carries.
        conn.execute("UPDATE daily_list_entry SET state = 'backlog', decided_at = ? WHERE list_date = ? "
                     "AND company_id = ?", (ctx.now, newest["list_date"], company_id))
    elif listed_today:
        # An older day's row (or none): the company joins today's list, so it shows now and not at the next build.
        rank = conn.execute("SELECT coalesce(max(rank), 0) + 1 FROM daily_list_entry WHERE list_date = ?",
                            (day,)).fetchone()[0]
        conn.execute(
            "INSERT INTO daily_list_entry(list_date, company_id, list_group, rank, state, posting_id, why, "
            "proposed_at, decided_at) VALUES (?, ?, ?, ?, 'backlog', ?, ?, ?, ?)",
            (day, company_id, newest["list_group"] if newest else "any_us_role", rank,
             newest["posting_id"] if newest else None,
             newest["why"] if newest else json.dumps({"rankReason": "Brought back from the removed list by you."}),
             ctx.now, ctx.now))
    _event(ctx, company_id, "restored", {"text": f"Restored to the backlog: {why}", "reason": why})
    return Empty()
