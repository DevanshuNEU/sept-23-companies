"""The plan job (#15, Task 10): build the day's list once per date, in one transaction.

`build_plan` reads the ranked list, the people and the outbound history in bulk, optionally asks the crawler to read
the top companies' boards and the ranker to re-rank (both injected, both allowed to be absent or to fail: the plan
never waits on them), chooses the rows with the pure `select` module, and writes `daily_list`, `daily_list_entry`, one
`event` and the closing `job_run` together. A second run for the same date changes nothing and writes no `job_run`. A
failure writes nothing but a `failed` job run, and the next run builds. Writes: `daily_list`, `daily_list_entry`,
`event` and `job_run` only.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Literal

from outreach.plan.boards import BoardsReader, Ranker
from outreach.plan.capacity import (EmailCapacity, LinkedinCapacity, email_capacity, lane_quotas,
                                    linkedin_capacity)
from outreach.plan.config import PlanConfig, default_plan_config
from outreach.plan.dates import is_weekday
from outreach.plan.outbound import load_outbound
from outreach.plan.prospects import build_why, load_postings, load_prospects
from outreach.plan.select import Caps, Chosen, choose_rows
from outreach.ranking.common import canonical_ids
from outreach.ranking.scores import StoredScore, latest_version, ranked_list
from outreach.store import append_event, describe_error, finish_job, start_job, transaction
from outreach.store.timefmt import utcnow

__all__ = ["PlanResult", "build_plan"]


@dataclass(frozen=True)
class PlanResult:
    status: Literal["built", "already_built", "weekend", "failed"]
    list_date: date
    capacity: int
    linkedin: LinkedinCapacity | None
    email: EmailCapacity | None
    chosen: tuple[Chosen, ...]
    boards: Literal["read", "partial", "absent", "failed", "skipped"]
    run_id: int | None


def _survivor(canon: dict[int, int], company_id: int) -> int:
    return canon.get(company_id, company_id)


def _read_boards(conn: sqlite3.Connection, ranked: list[StoredScore], canon: dict[int, int], cfg: PlanConfig,
                 reader: BoardsReader | None, ranker: Ranker | None, notes: dict) -> str:
    """Ask the crawler to read the top laned companies' boards, then re-rank. The read is recorded (`read`, `partial`,
    `failed`, `absent`) and never decides whether rank runs: rank runs whenever its hook exists, so the plan uses
    fresh scores. Neither can fail the plan; a failed rank leaves the stored scores in use."""
    boards = "absent"
    if reader is not None:
        top = list(dict.fromkeys(_survivor(canon, s.company_id)
                                 for s in ranked[:cfg.get("plan", "board_read_top_n")]))
        boards = "read"
        try:
            if top:
                reader(top)
        except (Exception, SystemExit) as err:
            boards = "partial" if getattr(err, "partial", False) else "failed"
            notes["boards_error"] = describe_error(err)
    if ranker is None:
        notes["rank"] = "absent"
        return boards
    try:
        ranker(conn)
        notes["rank"] = "ok"
    except Exception as err:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        notes["rank"] = "failed"
        notes["rank_error"] = describe_error(err)
    return boards


def build_plan(conn: sqlite3.Connection, *, today: date, cfg: PlanConfig | None = None,
               boards_reader: BoardsReader | None = None, ranker: Ranker | None = None, read_boards: bool = True,
               force_weekend: bool = False, dry_run: bool = False) -> PlanResult:
    """Build the plan for `today`. A weekend returns `weekend` (unless forced); a date that already has a plan returns
    `already_built` before anything is written. `read_boards` False skips the crawler and the ranker; otherwise rank always runs when its hook is given; a dry run
    skips them too (both write) and writes nothing at all, not even a job run. A failed build re-raises."""
    cfg = cfg or default_plan_config()
    if not is_weekday(today) and not force_weekend:
        return PlanResult("weekend", today, 0, None, None, (), "skipped", None)
    existing = conn.execute("SELECT capacity FROM daily_list WHERE list_date = ?", (today.isoformat(),)).fetchone()
    if existing is not None:
        return PlanResult("already_built", today, existing[0], None, None, (), "skipped", None)

    run_id = None if dry_run else start_job(conn, "plan")
    try:
        return _build(conn, today, cfg, boards_reader, ranker, read_boards and not dry_run, dry_run, run_id)
    except BaseException as err:
        if run_id is not None:
            try:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                finish_job(conn, run_id, "failed", error=describe_error(err))
            except Exception as finish_err:  # the original error is the one to raise
                err.add_note(f"could not record the failed run: {describe_error(finish_err)}")
        raise


def _build(conn: sqlite3.Connection, today: date, cfg: PlanConfig, boards_reader: BoardsReader | None,
           ranker: Ranker | None, read_boards: bool, dry_run: bool, run_id: int | None) -> PlanResult:
    tz = cfg.get("plan", "timezone")
    canon = canonical_ids(conn)
    ranked = ranked_list(conn, laned_only=True)
    notes: dict = {}
    boards = "skipped"
    if read_boards:
        boards = _read_boards(conn, ranked, canon, cfg, boards_reader, ranker, notes)
        if notes.get("rank") == "ok":
            canon = canonical_ids(conn)
            ranked = ranked_list(conn, laned_only=True)
    else:
        notes["rank"] = "skipped"

    linkedin = linkedin_capacity(conn, canon, today, cfg)
    email = email_capacity(conn, today, cfg)
    role_n, reason_n = lane_quotas(linkedin.today, linkedin.used, linkedin.reason_used, cfg)
    outbound = load_outbound(conn, canon, tz=tz)
    prospects = load_prospects(conn, ranked, canon, today, cfg, outbound)
    chosen = choose_rows(prospects, Caps(linkedin.today, email.slots, role_n, reason_n), cfg)
    capacity = linkedin.today + email.slots
    if dry_run:
        return PlanResult("built", today, capacity, linkedin, email, tuple(chosen), boards, None)

    by_company = {p.company_id: p for p in prospects}
    stored: dict[int, StoredScore] = {}
    for s in ranked:
        stored.setdefault(_survivor(canon, s.company_id), s)
    postings = load_postings(conn, [stored[c.company_id].signals.get("best_posting_id") for c in chosen], cfg)
    version = latest_version(conn) or ""
    entries = []
    for rank, row in enumerate(chosen, 1):
        score = stored[row.company_id]
        posting = postings.get(score.signals.get("best_posting_id"))
        why = build_why(by_company[row.company_id], row, score, posting, today=today, cfg=cfg, ranker_version=version)
        entries.append((rank, row, posting.posting_id if posting else None, json.dumps(why, sort_keys=True)))

    now = utcnow()
    summary = {"date": today.isoformat(), "capacity": capacity, "linkedin": linkedin.today, "email": email.slots,
               "rows": len(chosen), "people_rows": sum(1 for r in chosen if r.person_id is not None),
               "find_someone_rows": sum(1 for r in chosen if r.person_id is None),
               "carried_rows": sum(1 for r in chosen if r.carried_from is not None),
               "email_rows": sum(1 for r in chosen if r.channel == "email"), "boards": boards, **notes}
    with transaction(conn):
        made = conn.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES (?, ?, ?) "
                            "ON CONFLICT(list_date) DO NOTHING", (today.isoformat(), now, capacity))
        if made.rowcount == 0:  # another run built this date between the check and now
            finish_job(conn, run_id, "ok", {"status": "already_built", "date": today.isoformat()})
            return PlanResult("already_built", today, capacity, None, None, (), boards, run_id)
        conn.execute("UPDATE daily_list_entry SET state = 'backlog', decided_at = ?, revision = revision + 1 "
                     "WHERE state = 'proposed' AND list_date < ?", (now, today.isoformat()))
        for rank, row, posting_id, why in entries:
            conn.execute(
                "INSERT INTO daily_list_entry(list_date, company_id, list_group, rank, state, posting_id, why, "
                "proposed_at, person_id, channel) VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?, ?, ?) "
                "ON CONFLICT(list_date, company_id) DO NOTHING",
                (today.isoformat(), row.company_id, row.list_group, rank, posting_id, why, now, row.person_id,
                 row.channel))
        append_event(conn, entity="daily_list", kind="built", actor="daily_list",
                     detail={k: summary[k] for k in ("date", "capacity", "linkedin", "email", "rows", "people_rows",
                                                      "find_someone_rows", "carried_rows", "email_rows")},
                     idempotency_key=f"plan-built:{today.isoformat()}")
        finish_job(conn, run_id, "ok", summary)
    return PlanResult("built", today, capacity, linkedin, email, tuple(chosen), boards, run_id)
