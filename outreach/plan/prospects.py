"""Prospects, carry-forward and the stored row explanation (#15, Task 9).

`load_prospects` turns the ranked list into `Prospect` values with one query per table over the scanned companies.
`load_carried` finds the unfinished rows of the latest earlier plan. `build_why` writes the `why` object of a row: the
keys in `WHY_KEYS` and no others (the Hand-off section of the plan lists them once; the API reads exactly these).
Reads only; `today` is a parameter.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

from outreach.plan.config import PlanConfig
from outreach.plan.contact import contact_states
from outreach.plan.dates import business_days_between, ny_date
from outreach.plan.outbound import Outbound
from outreach.plan.people import Candidate, load_candidates, people_search_url, small_company_ids
from outreach.plan.select import CarriedRow, Chosen, Prospect
from outreach.ranking.common import canonical_ids
from outreach.ranking.confidence import scrub_text
from outreach.ranking.scores import StoredScore

__all__ = ["WHY_KEYS", "HANDOFF", "PostingInfo", "load_prospects", "load_carried", "load_postings", "build_why",
           "explanation"]

KEPT_STATUSES = ("candidate", "backlog", "working")
CARRIED_STATES = ("proposed", "picked", "backlog")
HANDOFF = ("Open the search, pick someone on the team, and paste their profile link and name. "
           "Your side session drafts the note.")
WHY_KEYS = ("lane", "reason", "reason_sources", "signals", "role", "person", "channel_reason", "find_someone",
            "draft_id", "carried_from", "first_proposed", "rotation", "score", "ranker_version")
_CHUNK = 500


def _marks(n: int) -> str:
    return ", ".join("?" for _ in range(n))


def _chunks(ids: Sequence[int]):
    for i in range(0, len(ids), _CHUNK):
        yield ids[i:i + _CHUNK]


def _iso(text) -> date | None:
    try:
        return date.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None


# Carry-forward

def load_carried(conn: sqlite3.Connection, today: date, cfg: PlanConfig,
                 outbound: Sequence[Outbound]) -> dict[int, CarriedRow]:
    """The unfinished rows of the plans before `today`, by surviving company id. The plans are walked newest first,
    and a company is decided by the newest plan that has a row for it: a carryable row there is carried, any other row
    (removed, done, past the limit) means it is not carried, and an older plan is never read for it again. A row is
    carryable when it is still `proposed`, `picked` or `backlog`, not done, and not past the roll limit. So a row
    that a day had no room for (it was never written into that day's plan) is still carried from the plan that last
    proposed it, until the limit. A person row is done when `outbound` has a row for that person on or after the
    plan's date; a find-someone row when anyone at the company was asked by LinkedIn request or email since then. A
    row is dropped once more than `plan.roll_forward_days` weekdays passed since it was first proposed; plans too old
    for any row to survive are not read. `carried_from` is the date of the plan the row is read from."""
    limit = cfg.get("plan", "roll_forward_days")
    canon = canonical_ids(conn)
    by_person: dict[int, list[date]] = {}
    by_company: dict[int, list[date]] = {}
    for o in outbound:
        if o.person_id is not None:
            by_person.setdefault(o.person_id, []).append(o.sent_on)
        if o.channel in ("linkedin_request", "email"):
            by_company.setdefault(o.company_id, []).append(o.sent_on)
    out: dict[int, CarriedRow] = {}
    decided: set[int] = set()
    for (plan_day,) in conn.execute("SELECT list_date FROM daily_list WHERE list_date < ? ORDER BY list_date DESC",
                                    (today.isoformat(),)).fetchall():
        list_date = date.fromisoformat(plan_day)
        if business_days_between(list_date, today) > limit:
            break  # a row's first_proposed is never after its plan's date, so none here can survive
        _carryable(conn, plan_day, list_date, today, limit, canon, by_person, by_company, decided, out)
    return out


def _carryable(conn: sqlite3.Connection, plan_day: str, list_date: date, today: date, limit: int,
               canon: dict[int, int], by_person: dict[int, list[date]], by_company: dict[int, list[date]],
               decided: set[int], out: dict[int, CarriedRow]) -> None:
    """Add the carryable rows of one plan to `out`, skipping companies a newer plan already decided; every company
    this plan has a row for is decided from here on."""
    here: set[int] = set()
    for r in conn.execute("SELECT company_id, person_id, why, state FROM daily_list_entry WHERE list_date = ? "
                          "ORDER BY rank", (plan_day,)):
        company = canon.get(r["company_id"], r["company_id"])
        if company in decided or company in out:
            continue
        here.add(company)
        if r["state"] not in CARRIED_STATES:
            continue
        seen = by_person.get(r["person_id"], ()) if r["person_id"] is not None else by_company.get(company, ())
        if any(d >= list_date for d in seen):
            continue
        try:
            why = json.loads(r["why"])
        except ValueError:
            why = {}
        first = _iso(why.get("first_proposed") if isinstance(why, dict) else None) or list_date
        if business_days_between(first, today) > limit:
            continue
        out[company] = CarriedRow(company, r["person_id"], list_date, first)
    decided |= here


# Prospects

def load_prospects(conn: sqlite3.Connection, ranked: Sequence[StoredScore], canon: dict[int, int], today: date,
                   cfg: PlanConfig, outbound: Sequence[Outbound]) -> list[Prospect]:
    """A `Prospect` for each of the first `plan.scan_limit` ranked rows whose company may be reached: status
    `candidate`, `backlog` or `working`, not paused, not held by a reply, no active company `no_go`. A merged-away
    id counts as its survivor (the first ranked row wins). `position` is the 1-based place in `ranked`. A person
    with any `outbound` row (a queued first email included) is not a candidate again."""
    scanned: list[tuple[int, StoredScore, int]] = []
    seen: set[int] = set()
    for place, s in enumerate(ranked[:cfg.get("plan", "scan_limit")], 1):
        company = canon.get(s.company_id, s.company_id)
        if company not in seen:
            seen.add(company)
            scanned.append((company, s, place))
    ids = [c for c, _, _ in scanned]
    if not ids:
        return []
    company_rows: dict[int, sqlite3.Row] = {}
    for part in _chunks(ids):
        for r in conn.execute(f"SELECT company_id, name, status, paused_at, held_by_touch FROM company "
                              f"WHERE company_id IN ({_marks(len(part))})", part):
            company_rows[r["company_id"]] = r
    no_go = {canon.get(r[0], r[0]) for r in conn.execute(
        "SELECT company_id FROM no_go WHERE company_id IS NOT NULL AND lifted_at IS NULL")}
    keep = [(c, s, place) for c, s, place in scanned
            if c in company_rows and company_rows[c]["status"] in KEPT_STATUSES
            and company_rows[c]["paused_at"] is None and company_rows[c]["held_by_touch"] is None
            and c not in no_go]
    kept = [c for c, _, _ in keep]
    if not kept:
        return []
    contacted = {o.person_id for o in outbound if o.person_id is not None}
    states = contact_states(outbound, kept, today, cfg)
    candidates = load_candidates(conn, kept, canon, contacted, today, cfg)
    small = small_company_ids(conn, kept, canon, cfg)
    carried = load_carried(conn, today, cfg, outbound)
    return [Prospect(company_id=c, name=company_rows[c]["name"], score=s.score, position=place, lanes=s.lanes,
                     contact=states[c], candidates=tuple(candidates.get(c, ())), small=c in small,
                     carried=carried.get(c))
            for c, s, place in keep]


# The row explanation

@dataclass(frozen=True)
class PostingInfo:
    posting_id: int
    title: str
    url: str
    location: str
    posted_on: date | None
    first_seen_on: date


def load_postings(conn: sqlite3.Connection, posting_ids: Collection[int], cfg: PlanConfig) -> dict[int, PostingInfo]:
    """The role postings named by `signals.best_posting_id`, in one query per chunk."""
    tz = cfg.get("plan", "timezone")
    ids = sorted({p for p in posting_ids if p is not None})
    out: dict[int, PostingInfo] = {}
    for part in _chunks(ids):
        for r in conn.execute(f"SELECT posting_id, title, url, location, posted_on, first_seen_at FROM posting "
                              f"WHERE posting_id IN ({_marks(len(part))})", part):
            out[r["posting_id"]] = PostingInfo(r["posting_id"], r["title"], r["url"], r["location"],
                                               _iso(r["posted_on"]), ny_date(r["first_seen_at"], tz))
    return out


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or url
    except ValueError:
        return url


def _signals(stored: StoredScore) -> list[dict]:
    values = stored.signals.get("values") or {}
    rows = [(name, v) for name, v in values.items() if isinstance(v, dict) and v.get("points")]
    rows.sort(key=lambda x: (-abs(x[1]["points"]), x[0]))
    return [{"name": name, "reason": scrub_text(str(v.get("reason", ""))), "points": float(v["points"])}
            for name, v in rows[:3]]


def _person(c: Candidate | None) -> dict | None:
    if c is None:
        return None
    found = None
    if c.found_on is not None:
        found = {"label": scrub_text(c.found_on.label) or _host(c.found_on.url), "url": c.found_on.url}
    return {"person_id": c.person_id, "name": scrub_text(c.name), "title": scrub_text(c.title),
            "role_kind": c.role_kind, "fit": c.fit, "employer_check": c.employer_check, "found_on": found}


def build_why(prospect: Prospect, chosen: Chosen, stored: StoredScore, posting: PostingInfo | None, *, today: date,
              cfg: PlanConfig, ranker_version: str) -> dict:
    """The stored `why` of one row: exactly `WHY_KEYS`. `stored` is the company's score row, `posting` the row of its
    `signals.best_posting_id` (None when it has none or the posting is gone). The text goes through `scrub_text`,
    so no dash reaches the UI."""
    reasons = stored.signals.get("reasons") or []
    role = None
    if posting is not None:
        since = posting.posted_on or posting.first_seen_on
        role = {"posting_id": posting.posting_id, "title": scrub_text(posting.title), "url": posting.url,
                "age_days": max(0, (today - since).days), "location": scrub_text(posting.location)}
    person = None
    if chosen.person_id is not None:
        person = _person(next((c for c in prospect.candidates if c.person_id == chosen.person_id), None))
    draft = next((c.draft_id for c in prospect.candidates if c.person_id == chosen.person_id), None) \
        if chosen.person_id is not None else None
    find = None
    if chosen.person_id is None:
        find = {"search_url": people_search_url(prospect.name, cfg.get("people", "search_hint")), "handoff": HANDOFF}
    return {
        "lane": chosen.list_group,
        "reason": scrub_text(str(reasons[0])) if reasons else "",
        "reason_sources": [{"label": "Posting", "url": role["url"]}] if role and role["url"] else [],
        "signals": _signals(stored),
        "role": role,
        "person": person,
        "channel_reason": chosen.channel_reason,
        "find_someone": find,
        "draft_id": draft,
        "carried_from": chosen.carried_from.isoformat() if chosen.carried_from else None,
        "first_proposed": (chosen.first_proposed or today).isoformat(),
        "rotation": {"previous_person_id": chosen.rotation_from} if chosen.rotation_from is not None else None,
        "score": float(prospect.score),
        "ranker_version": ranker_version,
    }


def explanation(*args, **kwargs) -> str:
    """`build_why(...)` as the JSON text stored in `daily_list_entry.why` (sorted keys, one line)."""
    return json.dumps(build_why(*args, **kwargs), sort_keys=True)
