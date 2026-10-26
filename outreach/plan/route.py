"""How a person Devanshu found is reached: `route_person` (connect-integration.md section C, company-first P3).

He finds people on LinkedIn himself; the MCP bridge (`intake`, `contact_check`) asks this module what to do with each
one. Pure: facts in, a `Route` out (no store, no clock). Every verdict is one of `VERDICTS`, with one plain sentence
and the key behind it: a config key (`section.key` in outreach/plan/config.toml) or, for a rule #9 fixes and no
setting can change, `#9 <the rule>`. First match wins:

1. stop: the company is on the no-go list, removed or closed (done); the person is on the no-go list or said no.
2. wait: he paused the company; someone there replied or a referral or interview is in progress (that thread first);
   this person was asked under `linkedin.rotate_days` ago.
3. find_someone_else: this person was asked longer ago with no answer; their role is outside `route.reach_kinds`
   (advice only: the note is still his pick); a recruiter while anyone else at the company is known (#9).
4. wait: the company's wave is full (`route.wave_size` people inside `linkedin.rotate_days`), until it is free.
5. email: `outreach.plan.people.pick_channel` already said email (a published or verified address, for a founder,
   an HN post that asks, or a company of recruiters only) and the company has room (`email.people_per_company`).
   The sender then holds every other #9 email rule: the ramp, 3 a day per company, 60 minutes apart.
6. wait: no LinkedIn requests left in the 7-day windows (`linkedin.plan_quota` for a company on today's queue,
   else `linkedin.hard_total`).
7. linkedin_note.

`route.prefer_ic_in_role` (his call, not measured yet) only orders people inside one company (`order_people`); it
never hides a person or a company. Recruiters go last whatever it says.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Literal

from outreach.plan.config import PlanConfig

__all__ = ["VERDICTS", "CompanyFacts", "PersonFacts", "Budget", "Taken", "Route", "classify_role", "seniority",
           "route_person", "order_people", "route_all", "preference", "day_text", "free_again"]

VERDICTS = ("linkedin_note", "email", "wait", "find_someone_else", "stop")
_SEGMENT = re.compile(r",|\(|\||\s@\s|\sat\s|\s-\s", re.I)
_IC_RANK = {"ic": 0, "senior": 1, "lead": 2, "unknown": 3}
_EMAIL_WHY = {"founder_published_email": "a founder with a published or verified address",
              "hn_post_asks_email": "an HN post there asks for email",
              "no_linkedin_person": "everyone known there is a recruiter, so email is the way in"}


@dataclass(frozen=True)
class CompanyFacts:
    name: str
    status: str                 # company.status
    close_reason: str | None
    no_go: bool
    paused: bool
    contact: str                # outreach.plan.contact ContactState.status: free, waiting, engaged, stale
    contact_reason: str
    wave_people: int            # people reached there inside linkedin.rotate_days (a queued email counts)
    free_on: date | None        # the day the company is free again (newest anchor plus linkedin.rotate_days)
    email_people: int           # people emailed there (ContactState.email_people)
    on_queue: bool              # on today's plan


@dataclass(frozen=True)
class PersonFacts:
    name: str
    title: str
    role_kind: str
    seniority: str
    tenure_months: int | None
    no_go: bool
    said_no: bool
    asked_on: date | None       # their newest live request or email
    email_code: str | None      # pick_channel's code when it said email, else None
    has_email: bool             # an address is known, usable or not
    recruiter_blocked: bool     # a recruiter while someone else at the company is known


@dataclass(frozen=True)
class Budget:
    linkedin_left: int          # requests left in the 7-day window that counts this company
    key: str                    # linkedin.plan_quota or linkedin.hard_total


@dataclass(frozen=True)
class Taken:
    """What this call already gave to other people at the same company."""
    wave: int = 0
    email: int = 0
    linkedin: int = 0


@dataclass(frozen=True)
class Route:
    verdict: Literal["linkedin_note", "email", "wait", "find_someone_else", "stop"]
    reason: str
    key: str


# ---------------------------------------------------------------------------------------------- titles

def _has(text: str, words: Sequence[str]) -> bool:
    return any(re.search(r"(?<![a-z0-9])" + re.escape(w.lower()) + r"(?![a-z0-9])", text) for w in words)


def classify_role(title: str, cfg: PlanConfig) -> str:
    """engineer, manager, founder, recruiter or other, from the title's words (the title itself is kept as is)."""
    t = (title or "").lower()
    r = cfg.route
    if _has(t, r["recruiter_title_words"]):
        return "recruiter"
    if _has(t, r["founder_title_words"]):
        return "founder"
    if _has(t, r["outside_title_words"]):
        return "other"
    if _has(t, r["manager_title_words"]) and (_has(t, r["engineering_words"]) or _has(t, ["cto", "chief technology"])):
        return "manager"
    if _has(t, r["ic_title_words"]):
        return "engineer"
    return "other"


def seniority(title: str, cfg: PlanConfig) -> str:
    """ic, senior, lead or unknown, read from the title's first segment (before `,`, `(`, `|`, ` @ `, ` at `,
    ` - `), with `route.title_exceptions` read as a plain IC."""
    first = _SEGMENT.split(title or "", maxsplit=1)[0].lower()
    r = cfg.route
    for phrase in r["title_exceptions"]:
        first = first.replace(phrase.lower(), " engineer ")
    if _has(first, r["lead_title_words"]):
        return "lead"
    if _has(first, r["senior_title_words"]):
        return "senior"
    if _has(first, r["ic_title_words"]):
        return "ic"
    return "unknown"


# ---------------------------------------------------------------------------------------------- one person

def day_text(d: date) -> str:
    return f"{d:%b} {d.day}"


def _tenure(months: int | None) -> str:
    if months is None:
        return ""
    if months < 12:
        return f", {months} month{'' if months == 1 else 's'} in role"
    years = months // 12
    return f", {years} year{'' if years == 1 else 's'} in role"


def route_person(p: PersonFacts, c: CompanyFacts, budget: Budget, taken: Taken, today: date,
                 cfg: PlanConfig) -> Route:
    """The one verdict for `p` at `c`, in the module's rule order."""
    rotate = cfg.get("linkedin", "rotate_days")
    if c.no_go:
        return Route("stop", f"{c.name} is on the no-go list, so nobody there is reached.", "#9 no-go")
    if c.status == "removed":
        return Route("stop", f"{c.name} was removed from Knock; bring it back on its page first.", "company.status")
    if c.status == "closed":
        why = (c.close_reason or "other").replace("_", " ")
        return Route("stop", f"{c.name} is done (closed: {why}).", "company.status")
    if p.no_go:
        return Route("stop", f"{p.name} is on the no-go list.", "#9 no-go")
    if p.said_no:
        return Route("stop", f"{p.name} said no or asked to stop.", "#9 said no")
    if c.paused:
        return Route("wait", f"You paused {c.name}; resume it on its page first.", "company.paused")
    if c.contact == "engaged":
        return Route("wait", f"{c.contact_reason} at {c.name}: work that thread first.", "#9 a reply holds the company")
    if p.asked_on is not None:
        if (today - p.asked_on).days < rotate:
            return Route("wait", f"{p.name} was already asked on {day_text(p.asked_on)}; give it "
                                 f"{rotate} days.", "linkedin.rotate_days")
        return Route("find_someone_else", f"{p.name} was asked on {day_text(p.asked_on)} with no answer; reach "
                                          "someone new.", "linkedin.rotate_days")
    if p.role_kind not in cfg.route["reach_kinds"] and p.role_kind != "recruiter":
        return Route("find_someone_else", f"{p.title or 'No title'} is outside engineering; a note can still go "
                                          "if you want, it is your pick.", "route.reach_kinds")
    if p.role_kind == "recruiter" and p.recruiter_blocked:
        return Route("find_someone_else", f"{p.name} is a recruiter and {c.name} has someone else on record; "
                                          "recruiters come last.", "#9 recruiters last")
    wave_size = cfg.route["wave_size"]
    if c.wave_people + taken.wave >= wave_size:
        free = f"; free again {day_text(c.free_on)}" if c.free_on is not None else ""
        return Route("wait", f"{c.wave_people + taken.wave} people at {c.name} are in this wave (up to "
                             f"{wave_size}){free}.", "route.wave_size")
    room = cfg.get("email", "people_per_company") - c.email_people - taken.email
    if p.email_code is not None and room > 0:
        return Route("email", f"Email: {_EMAIL_WHY.get(p.email_code, 'the address is published or verified')}. "
                              "The sender sends it inside the ramp, at most 3 a day per company, 60 minutes apart.",
                     "email.people_per_company")
    if budget.linkedin_left - taken.linkedin <= 0:
        limit = cfg.get("linkedin", budget.key.split(".", 1)[1])
        return Route("wait", f"No LinkedIn requests left in the last 7 days ({limit} is the limit).", budget.key)
    if p.email_code is not None:
        held = f"; {c.name} already has {cfg.get('email', 'people_per_company')} people on email"
    elif p.has_email:
        held = "; email needs a published or verified address with its page"
    else:
        held = ""
    counts = "the plan's 150" if budget.key == "linkedin.plan_quota" else "the 200 a week"
    return Route("linkedin_note", f"LinkedIn note: {p.role_kind} ({p.title or 'no title'}{_tenure(p.tenure_months)})"
                                  f"{held}; it counts toward {counts}.", budget.key)


# ---------------------------------------------------------------------------------------------- one company

def order_people(people: Sequence[PersonFacts], cfg: PlanConfig) -> list[int]:
    """The indices of `people` in the order they are offered: with `route.prefer_ic_in_role`, plain IC engineers
    first, then senior ICs, then leads, then the rest; recruiters always last; ties keep the paste order."""
    prefer = cfg.route["prefer_ic_in_role"]

    def key(i: int):
        p = people[i]
        rank = (_IC_RANK.get(p.seniority, 3) if p.role_kind in ("engineer", "manager") else 3) if prefer else 0
        return (p.role_kind == "recruiter", rank, i)
    return sorted(range(len(people)), key=key)


def preference(p: PersonFacts, position: int, cfg: PlanConfig) -> dict:
    """The visible preference line for one person: what it is, its key, and that it is not measured."""
    on = cfg.route["prefer_ic_in_role"]
    plain = p.role_kind == "engineer" and p.seniority == "ic"
    text = (f"Your preference 'plain engineers in role first' puts {p.name} at {position}"
            f"{' (a plain engineer)' if plain else ''}. Not measured yet." if on
            else "Your preference 'plain engineers in role first' is off: the paste order stands.")
    return {"key": "route.prefer_ic_in_role", "on": on, "label": "unmeasured", "position": position, "text": text}


def route_all(people: Sequence[PersonFacts], c: CompanyFacts, budget: Budget, today: date,
              cfg: PlanConfig) -> list[tuple[int, Route, dict]]:
    """Every person at one company routed in `order_people` order, each counting against the wave, the email room
    and the LinkedIn window the ones before them took. `(index, route, preference)` for every person: nobody is
    dropped. A recruiter is blocked when anyone else in the list is not one."""
    others = any(p.role_kind != "recruiter" for p in people)
    taken = Taken()
    out = []
    for position, i in enumerate(order_people(people, cfg), 1):
        p = people[i]
        if p.role_kind == "recruiter" and others and not p.recruiter_blocked:
            p = replace(p, recruiter_blocked=True)
        r = route_person(p, c, budget, taken, today, cfg)
        if r.verdict == "linkedin_note":
            taken = Taken(taken.wave + 1, taken.email, taken.linkedin + 1)
        elif r.verdict == "email":
            taken = Taken(taken.wave + 1, taken.email + 1, taken.linkedin)
        out.append((i, r, preference(p, position, cfg)))
    return out


def free_again(newest_anchor: date | None, cfg: PlanConfig) -> date | None:
    """The day a waiting company is free again: its newest anchor plus `linkedin.rotate_days`."""
    return None if newest_anchor is None else newest_anchor + timedelta(days=cfg.get("linkedin", "rotate_days"))
