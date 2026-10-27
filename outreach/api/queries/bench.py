"""The bench ("More options", top picks 2026-10-26): `GET /api/plan/bench`.

The next best companies not on today's list (`outreach.plan.picks.bench_picks`: eligible, not held back by his Not
now or an application, not in flight), fresh early career fits first, then other new openings at his level, then by
rank. Each comes in the compact shape of a pick row: the company, its role and the same `CompanyQueue` lines a pick
shows (`queue_facts` over the rows the bench would write), so the page draws both the same way. `room` is how many he
may still add today: the whole waves the 7-day limit leaves, less today's picks still to do.

The same answer carries the email lane (2026-10-27, `outreach.plan.picks.bench_and_lane`): up to
`email.email_companies_per_day` companies not on today's list whose email fit is `email`, by the stored ranking, in the
same compact shape. One read of the pool serves both, so the page asks for no extra heavy view. Reads only.
"""
import json
import sqlite3
from datetime import date

from outreach.api.models.plan import BenchCompany, BenchView, PlanCompany
from outreach.api.queries.company import company_holds, merge_parents
from outreach.api.queries.plan import plan_rows
from outreach.api.queries.queue import queue_facts
from outreach.api.queries.today import build_entries
from outreach.outcomes.windows import ny_day
from outreach.plan.capacity import linkedin_capacity
from outreach.plan.config import PlanConfig
from outreach.plan.fresh import as_utc
from outreach.plan.email_fit import lane_text
from outreach.plan.picks import bench_and_lane
from outreach.plan.prospects import _signals
from outreach.plan.select import BenchPick
from outreach.ranking.common import canonical_ids
from outreach.ranking.scores import StoredScore

__all__ = ["bench_rows", "room_today", "room_text", "pending_picks", "bench_view"]


def _row(i: int, day: str, pick: BenchPick, stored: StoredScore | None) -> dict:
    """The entry-shaped row `build_entries` and `queue_facts` read, for a company that is not on the list."""
    p = pick.prospect
    posting = pick.opening.posting_id if pick.opening is not None else (
        stored.signals.get("best_posting_id") if stored is not None else None)
    lane = pick.opening.lane if pick.early_fit and pick.opening is not None else p.lanes[0]
    why = {"signals": _signals(stored) if stored is not None else []}
    return {"rowid": -i, "list_date": day, "company_id": p.company_id, "list_group": lane, "rank": i,
            "state": "proposed", "posting_id": posting, "why": json.dumps(why), "person_id": None,
            "channel": "linkedin_request"}


def bench_rows(conn: sqlite3.Connection, picks: list[BenchPick], stored: dict[int, StoredScore], day: str,
               now: str, cfg: PlanConfig) -> list[BenchCompany]:
    """Each bench pick in the compact shape of a pick row, in the bench's order."""
    if not picks:
        return []
    rows = [_row(i, day, b, stored.get(b.prospect.company_id)) for i, b in enumerate(picks, 1)]
    daily = build_entries(conn, rows, merge_parents(conn), company_holds(conn))
    fallback = {r["rowid"]: (stored[b.prospect.company_id].signals.get("reasons") or ["Ranked for today."])[0]
                if b.prospect.company_id in stored else "Ranked for today." for r, b in zip(rows, picks)}
    facts = queue_facts(conn, rows, daily, canonical_ids(conn), cfg, date.fromisoformat(day), fallback,
                        now=as_utc(now))
    revisions = {r["company_id"]: r["revision"] for r in conn.execute(
        "SELECT company_id, revision FROM company WHERE company_id IN (SELECT value FROM json_each(?))",
        (json.dumps(sorted({d.company.id for d in daily})),))}
    out = []
    for i, (r, d, b) in enumerate(zip(rows, daily, picks), 1):
        q = facts[r["rowid"]]
        out.append(BenchCompany(
            company=PlanCompany(**d.company.model_dump(), revision=revisions.get(d.company.id, 1)), position=i,
            lane=r["list_group"], role=d.role, queue=q, fresh=q.opening is not None and q.opening.fresh,
            early_fit=b.early_fit))
    return out


def room_today(conn: sqlite3.Connection, day: str, cfg: PlanConfig, pending: int) -> int:
    """Companies he may still add today: the whole waves (`queue.requests_per_company`) the 7-day windows leave,
    less `pending`, today's picks still to do (each will take a wave)."""
    cap = linkedin_capacity(conn, canonical_ids(conn), date.fromisoformat(day), cfg)
    return max(0, cap.remaining // cfg.get("queue", "requests_per_company") - pending)


def room_text(room: int) -> str:
    if room == 0:
        return "No room to add more this week: the 7-day limit is taken by today's picks and the requests already sent."
    return f"Room to add {room} more {'company' if room == 1 else 'companies'} this week."


def pending_picks(rows) -> int:
    """Today's picks still to do: not carried, and nobody asked yet."""
    return sum(1 for r in rows if r.state == "todo" and not any(s.kind == "request" and s.at for s in r.steps))


def bench_view(conn: sqlite3.Connection, now: str, cfg: PlanConfig) -> BenchView:
    day = ny_day(now)
    picks, lane, stored = bench_and_lane(conn, date.fromisoformat(day), as_utc(now), cfg)
    rows = plan_rows(conn, day, cfg, now) or []
    room = room_today(conn, day, cfg, pending_picks(rows))
    return BenchView(as_of=now, date=day, companies=bench_rows(conn, picks, stored, day, now, cfg), room=room,
                     room_text=room_text(room), email_lane=bench_rows(conn, lane, stored, day, now, cfg),
                     email_lane_text=lane_text(len(lane), cfg))
