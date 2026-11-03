"""The bench and a pick added to today's list (top picks, the owner's spec, 2026-10-26).

`bench_picks` reads the same prospects the plan job reads (`load_prospects` over the laned ranked list, at `now`)
and returns `select.bench`: the next best companies not on today's list in any state, fresh early career fits
first. `bench_and_lane` reads the same pool once for the bench and the email lane (`outreach.plan.email_fit`: up to
`email.email_companies_per_day` more companies that can be emailed, by rank). `write_pick` adds one of them to today's list as a new company row, with the plan's own `why`, an id above
every row there is (the page names a row by its rowid, so a stale tab never reaches a new row) and the rank asked
for. It runs inside the caller's transaction and writes only `daily_list_entry`. (`add_own_pick` also records its event.)

`add_own_pick` (job list, #156) is Go ahead: the company of a posting he chose becomes his own pick on today's list.
Only a no-go company, a company already on (or already asked for on) today's list, or one with someone contacted and
waiting stops it; the company's size, the 7 day room and a Not now cooldown never do. With today's list built it
writes the row at the next free rank; before the plan is built it records an `own_pick_requested` event and
`own_picks_for_plan` hands those companies to the plan job, which puts them first on the day's list.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Literal

from outreach.plan.config import PlanConfig, default_plan_config
from outreach.plan.contact import load_contact_states
from outreach.plan.dates import is_weekday
from outreach.plan.email_fit import email_fit, email_lane, load_email_facts
from outreach.plan.fresh import without_turned_down
from outreach.plan.outbound import load_outbound
from outreach.plan.prospects import build_why, load_postings, load_prospects
from outreach.plan.select import BenchPick, Chosen, Prospect, bench, eligibility, group_of
from outreach.ranking.common import canonical_ids
from outreach.ranking.scores import StoredScore, latest_version, ranked_list
from outreach.store import append_event

__all__ = ["listed_today", "pool", "bench_picks", "bench_and_lane", "write_pick", "OWN_REQUESTED", "OwnPick",
           "OwnPickRefused", "add_own_pick", "own_pick_requests", "own_picks_for_plan"]

OWN_REQUESTED = "own_pick_requested"  # event kind (entity company, actor devanshu): Go ahead on a job list posting
_LANES = ("level_role", "any_us_role", "intern_junior", "reason_no_role")
OWN_REASON = "You chose this posting on the job list."


def listed_today(conn: sqlite3.Connection, day: date, canon: dict[int, int]) -> set[int]:
    """Every company with a row on the day's list in any state (passed and removed ones too), as survivors and as
    stored: such a company cannot get a second row that day."""
    out: set[int] = set()
    for (cid,) in conn.execute("SELECT company_id FROM daily_list_entry WHERE list_date = ?", (day.isoformat(),)):
        out |= {cid, canon.get(cid, cid)}
    return out


def pool(conn: sqlite3.Connection, today: date, now: datetime, cfg: PlanConfig, only: Collection[int] | None = None
         ) -> tuple[dict[int, int], list[Prospect], set[int], dict[int, StoredScore]]:
    """The plan job's prospects at `now`, every company on today's list, and each survivor's score row (the first
    ranked row of its merge family). `only` scans just those survivors, past the scan limit (browse, #120)."""
    canon = canonical_ids(conn)
    ranked = ranked_list(conn, laned_only=True)
    outbound = load_outbound(conn, canon, tz=cfg.get("plan", "timezone"))
    prospects = load_prospects(conn, ranked, canon, today, cfg, outbound, now=now, only=only)
    stored: dict[int, StoredScore] = {}
    for s in ranked:
        stored.setdefault(canon.get(s.company_id, s.company_id), s)
    return canon, prospects, listed_today(conn, today, canon), stored


def bench_picks(conn: sqlite3.Connection, today: date, now: datetime, cfg: PlanConfig,
                n: int | None = None) -> tuple[list[BenchPick], dict[int, StoredScore]]:
    """The bench for `today` at `now`, and each survivor's score row (the first ranked row of its merge family)."""
    _, prospects, taken, stored = pool(conn, today, now, cfg)
    return bench(prospects, taken, cfg, n), stored


def bench_and_lane(conn: sqlite3.Connection, today: date, now: datetime, cfg: PlanConfig
                   ) -> tuple[list[BenchPick], list[BenchPick], dict[int, StoredScore]]:
    """The bench and the email lane from one read of the pool. The email facts are read only for the companies the
    plan could pick today that are not on today's list (the lane's candidates), with their contact states."""
    canon, prospects, taken, stored = pool(conn, today, now, cfg)
    open_ = {p.company_id: p.contact for p in prospects if p.company_id not in taken and eligibility(p, cfg)[0]}
    facts = load_email_facts(conn, canon, open_, today, cfg, states=open_) if open_ else {}
    fits = {cid: email_fit(f, cfg) for cid, f in facts.items()}
    return bench(prospects, taken, cfg), email_lane(prospects, taken, fits, cfg), stored


def write_pick(conn: sqlite3.Connection, pick: BenchPick, stored: StoredScore, *, today: date, rank: int,
               now_iso: str, cfg: PlanConfig) -> int:
    """Insert `pick` as a proposed company row of `today` at `rank` (the caller keeps ranks unique). Returns its
    rowid, one above every row of the table."""
    p = pick.prospect
    lane = pick.opening.lane if pick.early_fit and pick.opening is not None else p.lanes[0]
    opening = pick.opening if pick.early_fit else None
    chosen = Chosen(company_id=p.company_id, channel="linkedin_request", person_id=None, group=group_of((lane,), cfg),
                    list_group=lane, channel_reason="find_someone", carried_from=None, first_proposed=None,
                    rotation_from=p.contact.last_person_id if p.contact.status == "stale" else None, opening=opening)
    posting_id = pick.opening.posting_id if pick.opening is not None \
        else without_turned_down(conn, stored.signals.get("best_posting_id"))
    posting = load_postings(conn, [posting_id], cfg).get(posting_id) if posting_id is not None else None
    why = build_why(p, chosen, stored, posting, today=today, cfg=cfg, ranker_version=latest_version(conn) or "")
    rowid = conn.execute("SELECT coalesce(max(rowid), 0) + 1 FROM daily_list_entry").fetchone()[0]
    conn.execute(
        "INSERT INTO daily_list_entry(rowid, list_date, company_id, list_group, rank, state, posting_id, why, "
        "proposed_at, person_id, channel) VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?, ?, NULL, 'linkedin_request')",
        (rowid, today.isoformat(), p.company_id, lane, rank, posting.posting_id if posting else None,
         json.dumps(why, sort_keys=True), now_iso))
    return rowid


class OwnPickRefused(Exception):
    """Go ahead cannot add the company; the message is a plain sentence for him (the API turns it into a refusal)."""


@dataclass(frozen=True)
class OwnPick:
    state: Literal["added", "requested"]  # added: a row on today's list; requested: the plan job adds it
    rank: int | None  # the row's rank on today's list; None until the plan is built
    entry_id: int | None = None  # the row's id, None until the plan is built
    list_date: date | None = None  # the day's list it is on, or for: the next weekday when asked on a weekend


def own_pick_requests(conn: sqlite3.Connection, day: date, canon: dict[int, int]) -> list[tuple[int, int | None]]:
    """`(survivor, posting_id)` of each company he said Go ahead to for `day`'s list, in the order he asked, once each."""
    out: dict[int, int | None] = {}
    for r in conn.execute("SELECT company_id, json_extract(detail, '$.posting_id') AS posting_id FROM event "
                          "WHERE kind = ? AND entity = 'company' AND company_id IS NOT NULL "
                          "AND json_extract(detail, '$.list_date') = ? ORDER BY at, event_id",
                          (OWN_REQUESTED, day.isoformat())):
        out.setdefault(canon.get(r["company_id"], r["company_id"]), r["posting_id"])
    return list(out.items())


def _refusal(conn: sqlite3.Connection, survivor: int, name: str, day: date, canon: dict[int, int],
             cfg: PlanConfig, *, listed: bool = True) -> str | None:
    """Why Go ahead cannot add the company, in a plain sentence, or None. `listed` False skips the check for a row on
    the day's list (the plan job, which builds the list itself)."""
    nogo = {canon.get(r[0], r[0]) for r in conn.execute(
        "SELECT company_id FROM no_go WHERE company_id IS NOT NULL AND lifted_at IS NULL")}
    if survivor in nogo:
        return f"{name} is on your no-go list, so it is not added."
    if listed:
        row = conn.execute("SELECT state FROM daily_list_entry WHERE list_date = ? AND company_id IN "
                           "(SELECT value FROM json_each(?))",
                           (day.isoformat(), json.dumps([c for c in canon if canon[c] == survivor] + [survivor]))
                           ).fetchone()
        if row is not None:
            return f"{name} is already on today's list." if row["state"] != "removed" \
                else f"{name} was on today's list earlier. It can come back from tomorrow."
        if any(c == survivor for c, _ in own_pick_requests(conn, day, canon)):
            return f"{name} is already asked for. It goes on the list when the plan is built."
    state = load_contact_states(conn, [survivor], canon, day, cfg)[survivor]
    if state.status in ("waiting", "engaged"):
        return f"{name} is in flight: {state.reason}."
    return None


def _own_pick(conn: sqlite3.Connection, survivor: int, name: str, posting_id: int | None, day: date,
              canon: dict[int, int], cfg: PlanConfig) -> tuple[BenchPick, StoredScore]:
    """The company as a pick whatever its rank row says (a big company the ranker hides is still his to choose): its
    newest score row when it has one, else a bare one, led by the posting he chose."""
    family = [c for c in canon if canon[c] == survivor] + [survivor]
    version = latest_version(conn)
    found = conn.execute("SELECT company_id, score, lanes, signals, scored_at FROM company_score WHERE ranker_version = ? "
                         "AND company_id IN (SELECT value FROM json_each(?)) ORDER BY score DESC, company_id LIMIT 1",
                         (version, json.dumps(family))).fetchone() if version is not None else None
    stored = StoredScore(survivor, found["score"], tuple(json.loads(found["lanes"])), json.loads(found["signals"]),
                         found["scored_at"]) if found is not None else StoredScore(survivor, 0.0, (), {}, "")
    lane = stored.lanes[0] if stored.lanes and stored.lanes[0] in _LANES else None
    if lane is None:
        ok = conn.execute("SELECT 1 FROM decision d JOIN ruleset r ON r.ruleset_version = d.ruleset_version "
                          "AND r.is_current = 1 WHERE d.posting_id = ? AND d.level_ok = 1", (posting_id,)).fetchone() \
            if posting_id is not None else None
        lane = "level_role" if ok is not None else "any_us_role"
    signals = {**stored.signals, "reasons": [OWN_REASON]}
    if posting_id is not None:
        signals["best_posting_id"] = posting_id
    stored = replace(stored, lanes=(lane,), signals=signals)
    state = load_contact_states(conn, [survivor], canon, day, cfg)[survivor]
    prospect = Prospect(company_id=survivor, name=name, score=stored.score, position=0, lanes=(lane,), contact=state,
                        candidates=(), small=False, carried=None)
    return BenchPick(prospect, None, False), stored


def own_picks_for_plan(conn: sqlite3.Connection, day: date, cfg: PlanConfig, canon: dict[int, int]
                       ) -> list[tuple[BenchPick, StoredScore]]:
    """The picks the plan job puts first on `day`'s list: each company he said Go ahead to, unless it went no-go or
    into flight since (nothing else stops it)."""
    out = []
    for survivor, posting_id in own_pick_requests(conn, day, canon):
        c = conn.execute("SELECT name FROM company WHERE company_id = ?", (survivor,)).fetchone()
        if c is not None and _refusal(conn, survivor, c["name"], day, canon, cfg, listed=False) is None:
            out.append(_own_pick(conn, survivor, c["name"], posting_id, day, canon, cfg))
    return out


def add_own_pick(conn: sqlite3.Connection, company_id: int, today: date, now: str, *, posting_id: int | None = None,
                 key: str | None = None, cfg: PlanConfig | None = None) -> OwnPick:
    """Go ahead on a company (job list, #156), inside the caller's transaction. With today's list built it adds the
    company at the next free rank and answers `added`; without it, it records the request for the plan job and
    answers `requested`. The plan job never builds a weekend, so a request made on a Saturday or Sunday is kept for
    the next weekday's list (`OwnPick.list_date`). Raises `OwnPickRefused` for a no-go company, one on (or asked for
    on) the list, or one with someone contacted and waiting. `posting_id` is the posting he chose; `key` makes the event idempotent."""
    cfg = cfg or default_plan_config()
    canon = canonical_ids(conn)
    survivor = canon.get(company_id, company_id)
    name = conn.execute("SELECT name FROM company WHERE company_id = ?", (survivor,)).fetchone()["name"]
    built = conn.execute("SELECT 1 FROM daily_list WHERE list_date = ?", (today.isoformat(),)).fetchone() is not None
    day = today
    while not built and not is_weekday(day):  # no build reads a weekend date: keep it for the next weekday
        day += timedelta(days=1)
    why_not = _refusal(conn, survivor, name, day, canon, cfg)
    if why_not is not None:
        raise OwnPickRefused(why_not)
    detail: dict = {"list_date": day.isoformat(), "posting_id": posting_id}
    entry_id = rank = None
    if built:
        pick, stored = _own_pick(conn, survivor, name, posting_id, today, canon, cfg)
        rank = (conn.execute("SELECT max(rank) FROM daily_list_entry WHERE list_date = ?",
                             (today.isoformat(),)).fetchone()[0] or 0) + 1
        entry_id = write_pick(conn, pick, stored, today=today, rank=rank, now_iso=now, cfg=cfg)
        detail.update(entry_id=entry_id, rank=rank)
    append_event(conn, entity="company", entity_id=survivor, company_id=survivor, kind=OWN_REQUESTED,
                 actor="devanshu", at=now, detail={**detail, "state": "added" if built else "requested"},
                 idempotency_key=key)
    return OwnPick("added" if built else "requested", rank, entry_id, day)
