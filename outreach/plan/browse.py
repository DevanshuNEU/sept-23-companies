"""Browse companies (#120): every company the plan could pick, in stored rank order, one page at a time.

The pool is the laned, non-hidden ranked list (`ranked_list(laned_only=True)`), with no scan limit. Before any filter
or page, a company drops out when it is a no-go, paused or held by a reply, not a `KEPT_STATUSES` company, on
today's list in any state (`listed_today`) or in flight (`waiting` or `engaged`). Nothing here hides a company or
changes a hide rule: it only lists what he may choose. A company held back by his Not now or an application, or one
the rotation rules keep out, IS listed, with `blocked_text` as the reason, and cannot be swapped in.

Paging is a keyset cursor: `after` is the stored position (the 1-based place in the ranked list) of the last row seen,
so a write between two pages never skips or repeats a row. Filters narrow the list and never add a company:
`q` (the store's name keys: `name_key`, matched against `company_name_key` and the company's own name), `level_role`,
`visa_ok` (the company's sponsorship fact is not `no`) and `unworked` (no outbound touch and no application).

Reads only. Only the page's companies become full `Prospect`s (`load_prospects(only=...)`).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from outreach.plan.config import PlanConfig
from outreach.plan.contact import contact_states
from outreach.plan.fresh import load_applied, load_visa_facts
from outreach.plan.outbound import load_outbound
from outreach.plan.picks import listed_today
from outreach.plan.prospects import KEPT_STATUSES, load_prospects
from outreach.plan.route import day_text
from outreach.plan.select import Prospect, eligibility
from outreach.ranking.common import canonical_ids
from outreach.ranking.scores import StoredScore, ranked_list
from outreach.store import name_key

__all__ = ["BrowseFilters", "BrowsePage", "browse_page", "blocked_text", "worked_ids"]


@dataclass(frozen=True)
class BrowseFilters:
    q: str = ""
    level_role: bool = False
    visa_ok: bool = False
    unworked: bool = False


@dataclass(frozen=True)
class BrowsePage:
    prospects: list[Prospect]  # this page, in stored order
    total: int  # companies that match the search and the filters
    next_after: int | None  # the cursor for the next page; None on the last one
    stored: dict[int, StoredScore]  # each survivor's score row
    worked: set[int]  # survivors he has reached or applied to (any of the page's, and every other)


def _in(ids) -> str:
    return json.dumps(sorted(ids))


def worked_ids(conn: sqlite3.Connection, canon: dict[int, int], outbound, tz: str) -> set[int]:
    """Survivors he has reached (any outbound touch, a queued email included) or applied to."""
    return {o.company_id for o in outbound} | set(load_applied(conn, canon, tz))


def _name_matches(conn: sqlite3.Connection, canon: dict[int, int], key: str, names: dict[int, str]) -> set[int]:
    """Survivors known by a name key that contains `key`: the stored keys (name, legal name, dba) and the name."""
    found = {canon.get(r[0], r[0]) for r in conn.execute(
        "SELECT company_id FROM company_name_key WHERE name_key LIKE ?", (f"%{key}%",))}
    return found | {c for c, name in names.items() if key in name_key(name)}


def browse_page(conn: sqlite3.Connection, today: date, now: datetime, cfg: PlanConfig, f: BrowseFilters, after: int,
                limit: int) -> BrowsePage:
    tz = cfg.get("plan", "timezone")
    canon = canonical_ids(conn)
    ranked = ranked_list(conn, laned_only=True)
    stored: dict[int, StoredScore] = {}
    position: dict[int, int] = {}
    for place, s in enumerate(ranked, 1):
        survivor = canon.get(s.company_id, s.company_id)
        if survivor not in stored:
            stored[survivor] = s
            position[survivor] = place
    outbound = load_outbound(conn, canon, tz=tz)
    worked = worked_ids(conn, canon, outbound, tz)

    # What drops out first: not a company he may pick (status, pause, a reply hold), a no-go, today's list, in flight.
    rows = {r["company_id"]: r for r in conn.execute(
        "SELECT company_id, name, status, paused_at, held_by_touch FROM company "
        "WHERE company_id IN (SELECT value FROM json_each(?))", (_in(stored),))}
    no_go = {canon.get(r[0], r[0]) for r in conn.execute(
        "SELECT company_id FROM no_go WHERE company_id IS NOT NULL AND lifted_at IS NULL")}
    taken = listed_today(conn, today, canon)
    live = [c for c in stored if c in rows and rows[c]["status"] in KEPT_STATUSES
            and rows[c]["paused_at"] is None and rows[c]["held_by_touch"] is None
            and c not in no_go and c not in taken]
    states = contact_states(outbound, live, today, cfg)
    keep = [c for c in live if states[c].status not in ("waiting", "engaged")]

    # The search and the filters only narrow.
    if f.q.strip():
        key = name_key(f.q)
        matches = _name_matches(conn, canon, key, {c: rows[c]["name"] for c in keep}) if key else set()
        keep = [c for c in keep if c in matches]
    if f.level_role:
        keep = [c for c in keep if "level_role" in stored[c].lanes]
    if f.visa_ok:
        visa = load_visa_facts(conn, canon, keep)
        keep = [c for c in keep if visa[c][1] != "no"]
    if f.unworked:
        keep = [c for c in keep if c not in worked]

    ahead = [c for c in keep if position[c] > after]
    page = ahead[:limit]
    next_after = position[page[-1]] if len(ahead) > len(page) else None
    prospects = load_prospects(conn, ranked, canon, today, cfg, outbound, now=now, only=set(page))
    return BrowsePage(prospects=prospects, total=len(keep), next_after=next_after, stored=stored, worked=worked)


def blocked_text(p: Prospect, cfg: PlanConfig) -> str | None:
    """Why this company cannot be added or swapped in today, in a plain sentence, or None when it may be. The same
    rules as a pick (`select.eligibility`)."""
    ok, why = eligibility(p, cfg)
    if ok:
        return None
    if why == "passed":
        back = p.passed_at.astimezone(ZoneInfo(cfg.get("plan", "timezone"))).date()
        until = back + timedelta(days=cfg.get("queue", "pass_cooldown_days"))
        return (f"You said Not now to {p.name} on {day_text(back)}. It comes back on {day_text(until)}, or sooner "
                "with a new opening.")
    if why == "applied":
        return f"You applied to {p.name} on {day_text(p.applied_on)}. It comes back with a new role."
    if why in ("waiting", "engaged"):
        return f"{p.name} is in flight: {p.contact.reason}."
    if why == "stale_below_rotation_cutoff":
        return (f"You reached {p.name} before and nobody answered. It sits below the top "
                f"{cfg.get('linkedin', 'rotate_top_n')} of the ranking, so it does not come back on its own.")
    if why == "max_people_contacted":
        return (f"You already reached {p.contact.people_contacted} people at {p.name}, the most for one company "
                f"({cfg.get('linkedin', 'max_people_per_company')}).")
    return f"{p.name} is not free to add today."
