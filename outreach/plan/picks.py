"""The bench and a pick added to today's list (top picks, the owner's spec, 2026-10-26).

`bench_picks` reads the same prospects the plan job reads (`load_prospects` over the laned ranked list, at `now`)
and returns `select.bench`: the next best companies not on today's list in any state, fresh early career fits
first. `bench_and_lane` reads the same pool once for the bench and the email lane (`outreach.plan.email_fit`: up to
`email.email_companies_per_day` more companies that can be emailed, by rank). `write_pick` adds one of them to today's list as a new company row, with the plan's own `why`, an id above
every row there is (the page names a row by its rowid, so a stale tab never reaches a new row) and the rank asked
for. It runs inside the caller's transaction and writes only `daily_list_entry`.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Collection
from datetime import date, datetime

from outreach.plan.config import PlanConfig
from outreach.plan.email_fit import email_fit, email_lane, load_email_facts
from outreach.plan.fresh import without_turned_down
from outreach.plan.outbound import load_outbound
from outreach.plan.prospects import build_why, load_postings, load_prospects
from outreach.plan.select import BenchPick, Chosen, Prospect, bench, eligibility, group_of
from outreach.ranking.common import canonical_ids
from outreach.ranking.scores import StoredScore, latest_version, ranked_list

__all__ = ["listed_today", "pool", "bench_picks", "bench_and_lane", "write_pick"]


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
