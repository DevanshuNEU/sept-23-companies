"""The fresh lane of the top picks (the owner's spec, 2026-10-26): new openings, the visa gate, Not now and applied.

The best moment to reach a company is the first days after a role is posted, and an F-1 student on OPT needs an
employer that can hire him. So up to `queue.fresh_slots` of the day's companies are kept for a NEW opening:

- an `Opening` is a visible engineering posting at his level (the ranker's `level_role` and `intern_junior` lanes,
  `outreach.ranking.roles`) on a trusted board, not stale, that opened within `queue.fresh_hours`. It opened at its
  first sighting when it has no posted date or was posted the day it was first seen (a time we know), else at the New
  York start of its posted date (a posting the crawler found days after it went up is not new);
- the fresh lane takes only an early career fit (`early_career_fit`): new grad, early career, SWE I, or an open level
  that asks 2 years or fewer. SWE II, senior and an open level with no years can still be picked by rank;
- the visa gate (`visa_clear`): the posting does not refuse sponsorship and the company's sponsorship fact is not a
  no, and the company is E-Verify enrolled, or the posting says it sponsors or welcomes OPT, or it is silent. A no-go
  never reaches here (`load_prospects` drops it), and the ranker's hide already keeps a refusing company off the list;
- Not now (a `pick_passed` event) keeps the company out of the picks for `queue.pass_cooldown_days`, unless an early
  career opening that clears the gate was first seen after the pass: that is the case he wants, and it is fresh again;
- a company he applied to stays out of the picks until such an opening was first seen on a day after the application.

Pure functions first, then the reads (one query per table). The reads take `now`; nothing here writes.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from outreach.crawl.stale import load_stale
from outreach.plan.config import PlanConfig
from outreach.plan.dates import ny_date
from outreach.ranking.common import clean_text, doubtful_board_ids
from outreach.ranking.hide import posting_visible
from outreach.rules.rebuild import stored_decision
from outreach.ranking.roles import EARLY_CAREER_MAX_YEARS, early_career_fit as _shared_fit
from outreach.store.timefmt import ISO_FMT, iso_utc

__all__ = ["PASSED", "PASS_REASONS", "EARLY_TIERS", "Opening", "early_career_fit", "level_words", "opened",
           "hours_old", "is_fresh", "visa_clear", "age_words", "fresh_tag", "plan_moment", "as_utc", "load_openings",
           "load_level_roles", "load_visa_facts", "load_passes", "load_applied"]

PASSED = "pick_passed"  # event kind: Not now on a pick (entity company, actor devanshu)
# The reasons of Not now, in the order of the chips (keys 1 to 8). None of them is a no-go.
PASS_REASONS = ("visa", "too_big", "too_early", "role", "product", "know_it", "not_a_fit", "other")
EARLY_TIERS = ("new_grad", "early_career", "swe1")
EARLY_MAX_YEARS = EARLY_CAREER_MAX_YEARS
_LEVEL_WORDS = {"new_grad": "New grad role", "early_career": "Early career role", "swe1": "SWE I role",
                "swe2": "SWE II role", "senior": "Senior role"}
_GATE_SPONSORS = ("yes", "opt_only", "silent")


def early_career_fit(level_tier: str | None, years_asked: int | None) -> bool:
    """New grad, early career or SWE I, or an open level that asks `EARLY_CAREER_MAX_YEARS` years or fewer. The rule
    is the ranking's own (`outreach.ranking.roles.early_career_fit`), so the score and the fresh lane read one rule."""
    return _shared_fit(level_tier, "", years_asked)


def level_words(level_tier: str | None, years_asked: int | None, *, intern: bool = False) -> str | None:
    """The role's level in plain words ("New grad role", "SWE II role"); None when the posting does not say."""
    if intern:
        return "Intern role"
    if level_tier in _LEVEL_WORDS:
        return _LEVEL_WORDS[level_tier]
    if level_tier == "open":
        if years_asked is None:
            return "Open level role"
        return f"Open level, asks {years_asked} {'year' if years_asked == 1 else 'years'}"
    return None


@dataclass(frozen=True)
class Opening:
    posting_id: int
    company_id: int  # the survivor of any merge
    title: str
    url: str
    location: str
    lane: str  # level_role or intern_junior
    level_tier: str
    years_asked: int | None
    stance: str  # the posting's sponsorship: yes, opt_only, unclear or silent (a no is never visible)
    posted_on: date | None
    first_seen_at: datetime  # aware, UTC
    first_seen_on: date  # its New York date
    opened_at: datetime  # aware, UTC: see `opened`
    timed: bool  # opened_at is the first sighting (a time), not the start of a posted date

    @property
    def early_fit(self) -> bool:
        return self.lane == "level_role" and early_career_fit(self.level_tier, self.years_asked)

    @property
    def level(self) -> str | None:
        return level_words(self.level_tier, self.years_asked, intern=self.lane == "intern_junior")


def as_utc(ts: str) -> datetime:
    """A stored timestamp as an aware UTC datetime."""
    return datetime.strptime(iso_utc(ts), ISO_FMT).replace(tzinfo=timezone.utc)


def _start_of(day: date, tz: str) -> datetime:
    return datetime.combine(day, time.min, tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)


def opened(posted_on: date | None, first_seen_at: datetime, tz: str) -> tuple[datetime, bool]:
    """When the opening went up, and whether that is a time we know. The first sighting when there is no posted date,
    or when it was posted the day it was first seen (or a later day: a date ahead of the sighting is not trusted to
    move it later); else the New York start of the posted date, which is never after the sighting."""
    if posted_on is None or posted_on >= first_seen_at.astimezone(ZoneInfo(tz)).date():
        return first_seen_at, True
    return _start_of(posted_on, tz), False


def hours_old(opened_at: datetime, now: datetime) -> float:
    """Hours since it opened; a sighting a little ahead of `now` counts as 0."""
    return max(0.0, (now - opened_at).total_seconds() / 3600)


def is_fresh(opened_at: datetime, now: datetime, cfg: PlanConfig) -> bool:
    """Opened within `queue.fresh_hours` of `now`, the end hour included."""
    return hours_old(opened_at, now) <= cfg.get("queue", "fresh_hours")


def visa_clear(stance: str | None, everify: str | None, company_sponsorship: str | None) -> bool:
    """The visa gate: neither the posting nor the company's sponsorship fact says no, and the company is E-Verify
    enrolled, or the posting sponsors, welcomes OPT or is silent. An unclear posting passes only with E-Verify."""
    if stance == "no" or company_sponsorship == "no":
        return False
    return everify == "enrolled" or stance in _GATE_SPONSORS


def _ago(o: Opening, now: datetime, tz: str) -> str:
    zone = ZoneInfo(tz)
    if o.timed:
        hours = int(hours_old(o.opened_at, now))
        if hours < 1:
            return "under an hour ago"
        if hours < 24:
            return f"{hours} {'hour' if hours == 1 else 'hours'} ago"
        days = (now.astimezone(zone).date() - o.opened_at.astimezone(zone).date()).days
    else:
        days = (now.astimezone(zone).date() - o.posted_on).days
    if days <= 0:
        return "today"
    return "yesterday" if days == 1 else f"{days} days ago"


def age_words(o: Opening, now: datetime, tz: str) -> str:
    """"first seen 6 hours ago", "posted yesterday", "posted 3 days ago". A time comes only from our first sighting,
    so hours are said as first seen; a posted date is said in days."""
    return f"{'first seen' if o.timed else 'posted'} {_ago(o, now, tz)}"


def fresh_tag(o: Opening, now: datetime, tz: str) -> str:
    """The rail's small tag: "New opening, 6 hours ago"."""
    return f"New opening, {_ago(o, now, tz)}"


def plan_moment(today: date, tz: str, real: datetime | None = None) -> datetime:
    """The moment a plan for `today` is chosen at: now when `today` is today in New York, else 08:00 there (the plan
    job's hour), so a draft for tomorrow and a test on a fixed date read the same openings every time."""
    real = real or datetime.now(timezone.utc)
    if real.astimezone(ZoneInfo(tz)).date() == today:
        return real
    return datetime.combine(today, time(8, 0), tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)


# ---------------------------------------------------------------------------------------------- reads

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(ISO_FMT)


def _ids(values: Collection[int]) -> str:
    return json.dumps(sorted(set(values)))


_OPENINGS = (
    "SELECT p.posting_id, p.company_id, p.board_id, p.title, p.url, p.location, p.posted_on, p.first_seen_at, d.* "
    "FROM posting p JOIN decision d ON d.posting_id = p.posting_id "
    "AND d.ruleset_version = (SELECT ruleset_version FROM ruleset WHERE is_current = 1) "
    "WHERE p.closed_at IS NULL AND {where} ORDER BY p.posting_id")


def _lane(d) -> str:
    """The ranker's lane of a visible posting (`outreach.ranking.roles._lane`)."""
    if d.lane == "intern":
        return "intern_junior"
    return "level_role" if d.level_ok else "any_us_role"


def _build(conn: sqlite3.Connection, rows: list[sqlite3.Row], canon: dict[int, int], now: datetime, cfg: PlanConfig,
           *, only_fresh: bool) -> dict[int, tuple[Opening, ...]]:
    """The openings among these posting rows: visible (`posting_visible` under the current ruleset), on a trusted
    board, not stale, in a role lane, and (with `only_fresh`) opened within `queue.fresh_hours` of `now`. By company:
    early career fits first, then the newest."""
    if not rows:
        return {}
    tz = cfg.get("plan", "timezone")
    role_lanes = set(cfg.get("lanes", "role"))
    doubtful = doubtful_board_ids(conn)
    stale = load_stale(conn, now.astimezone(ZoneInfo(tz)).date(), posting_ids=[r["posting_id"] for r in rows])
    out: dict[int, list[Opening]] = {}
    for r in rows:
        if r["board_id"] in doubtful or r["posting_id"] in stale:
            continue
        d = stored_decision(r)
        if not posting_visible(d):
            continue
        lane = _lane(d)
        if lane not in role_lanes:
            continue
        seen = as_utc(r["first_seen_at"])
        posted = date.fromisoformat(r["posted_on"]) if r["posted_on"] else None
        at, timed = opened(posted, seen, tz)
        if only_fresh and not is_fresh(at, now, cfg):
            continue
        cid = canon.get(r["company_id"], r["company_id"])
        out.setdefault(cid, []).append(Opening(
            posting_id=r["posting_id"], company_id=cid, title=clean_text(" ".join(r["title"].split())),
            url=r["url"], location=r["location"], lane=lane, level_tier=d.level_tier, years_asked=d.years_asked,
            stance=d.sponsorship, posted_on=posted, first_seen_at=seen, first_seen_on=ny_date(r["first_seen_at"], tz),
            opened_at=at, timed=timed))
    return {c: tuple(sorted(os, key=lambda o: (not o.early_fit, -o.opened_at.timestamp(), -o.posting_id)))
            for c, os in out.items()}


def load_openings(conn: sqlite3.Connection, canon: dict[int, int], now: datetime,
                  cfg: PlanConfig) -> dict[int, tuple[Opening, ...]]:
    """Every surviving company's openings that opened within `queue.fresh_hours` of `now`, at his level, visible
    (`posting_visible` under the current ruleset), on a trusted board and not stale, by company: early career fits
    first, then the newest. The SQL keeps postings first seen inside the window (an opening never opens after its
    first sighting); `opened` and `is_fresh` decide."""
    since = now - timedelta(hours=cfg.get("queue", "fresh_hours"))
    rows = conn.execute(_OPENINGS.format(where="p.first_seen_at >= ?"), (_iso(since),)).fetchall()
    return _build(conn, rows, canon, now, cfg, only_fresh=True)


def load_level_roles(conn: sqlite3.Connection, canon: dict[int, int], company_ids: Collection[int], now: datetime,
                     cfg: PlanConfig) -> dict[int, tuple[Opening, ...]]:
    """Every open role at his level of these surviving companies (and their merged duplicates), new or old, by
    company: the same rules as `load_openings` without the freshness window. One query for the whole set."""
    wanted = set(company_ids)
    family = wanted | {c for c, s in canon.items() if s in wanted}
    rows = conn.execute(_OPENINGS.format(where="p.company_id IN (SELECT value FROM json_each(?))"),
                        (_ids(family),)).fetchall()
    return _build(conn, rows, canon, now, cfg, only_fresh=False)


def load_visa_facts(conn: sqlite3.Connection, canon: dict[int, int],
                    company_ids: Collection[int]) -> dict[int, tuple[str | None, str | None]]:
    """`(everify, sponsorship)` for each survivor: the newest `company_fact` of each across its merge family."""
    wanted = set(company_ids)
    family = wanted | {c for c, s in canon.items() if s in wanted}
    out: dict[int, dict[str, str]] = {}
    for r in conn.execute("SELECT company_id, fact, value FROM company_fact WHERE fact IN ('everify', 'sponsorship') "
                          "AND company_id IN (SELECT value FROM json_each(?)) "
                          "ORDER BY recorded_at DESC, evidence_id DESC", (_ids(family),)):
        out.setdefault(canon.get(r["company_id"], r["company_id"]), {}).setdefault(r["fact"], r["value"])
    return {c: (out.get(c, {}).get("everify"), out.get(c, {}).get("sponsorship")) for c in wanted}


def load_passes(conn: sqlite3.Connection, canon: dict[int, int], today: date, cfg: PlanConfig) -> dict[int, datetime]:
    """Each survivor's newest Not now (`pick_passed`) inside the cooldown: on `today` or the
    `queue.pass_cooldown_days - 1` days before it (New York days)."""
    tz = cfg.get("plan", "timezone")
    first = today - timedelta(days=cfg.get("queue", "pass_cooldown_days") - 1)
    out: dict[int, datetime] = {}
    for r in conn.execute("SELECT company_id, at FROM event WHERE kind = ? AND entity = 'company' "
                          "AND company_id IS NOT NULL AND at >= ? ORDER BY at, event_id",
                          (PASSED, _iso(_start_of(first, tz)))):
        if ny_date(r["at"], tz) <= today:
            out[canon.get(r["company_id"], r["company_id"])] = as_utc(r["at"])
    return out


def load_applied(conn: sqlite3.Connection, canon: dict[int, int], tz: str) -> dict[int, date]:
    """Each survivor's newest application day: an `application` row, or a live `application` touch (New York day)."""
    out: dict[int, date] = {}

    def keep(company_id: int, day: date) -> None:
        cid = canon.get(company_id, company_id)
        if cid not in out or day > out[cid]:
            out[cid] = day

    for r in conn.execute("SELECT company_id, max(applied_on) AS d FROM application GROUP BY company_id"):
        keep(r["company_id"], date.fromisoformat(r["d"]))
    for r in conn.execute("SELECT company_id, max(sent_at) AS at FROM outreach_touch WHERE channel = 'application' "
                          "AND undone_at IS NULL AND company_id IS NOT NULL GROUP BY company_id"):
        keep(r["company_id"], ny_date(r["at"], tz))
    return out
