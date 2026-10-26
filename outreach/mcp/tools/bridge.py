"""The terminal bridge (#9, company-first ruling 2026-10-26): the tools his terminal skills call. He finds the people
on LinkedIn and writes his own notes; Knock picks the companies and keeps the record.

- `knock_status` (read): whether Knock answers, this server's version, the 7-day LinkedIn windows, the companies in
  flight, today's queue and any lines of the frozen contacted.txt that the store lacks. A skill calls it first; if
  it fails, the skill says "Not logged in Knock" and prints a paste block (it never writes to disk).
- `contact_check` (read): one company by name or id (merges followed; a near name or a tie is a question, never a
  guess; an unknown one is "not in Knock: add it?" with the by-hand `sources add` command, never added here), its
  state, each person's touches, the week's count, one advice line per person (`outreach.plan.route`) and the
  contacted.txt lines about it the store lacks.
- `intake` (write: people only): the people he pasted, recorded through `add_people`'s own door
  (`record_people`: same matching, same no-go and said-no refusals) with the role kind read from the title, so
  the dashboard shows them as found, not contacted. Each gets a route. A second identical call writes nothing.
- `log_sent` (write: the one widening of the guard): after he says he sent them, each LinkedIn request through
  `outreach.outcomes.requests.record_request`, the write the plan row and the LinkedIn page use, so the 7-day
  windows, plan or own source and the company's state stay in one place. Key: person, New York day, note hash.

Nothing here sends, approves or skips an email, records a reply, accept, referral, interview, email touch or undo,
or adds a company. The guard refuses each of those on these connections whatever is asked.
"""
from __future__ import annotations

import hashlib
import os
import re
import shlex
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from difflib import get_close_matches
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from outreach.drafts.checks import DASHES, load_banned
from outreach.drafts.followups import held_survivors, no_go_sets
from outreach.importer.read_text import read_contacted_txt
from outreach.importer.records import SourceError
from outreach.mcp.guard import Grant
from outreach.mcp.tools import SERVER_VERSION, Ctx, Input, ToolError, ToolSpec
from outreach.mcp.tools.people import PersonIn, SourceIn, clean_people, record_people, said_no_ids
from outreach.mcp.tools.reads import MAX_ID, UNTRUSTED, Family, clip, company_row, marks, open_postings, role_to_name
from outreach.mcp.tools.research import HTTPS, is_linkedin
from outreach.outcomes.requests import own_context, plan_context, record_request
from outreach.outcomes.windows import ny_day
from outreach.plan.capacity import linkedin_capacity
from outreach.plan.config import PlanConfig, default_plan_config
from outreach.plan.contact import contact_states
from outreach.plan.outbound import Outbound, load_outbound
from outreach.plan.people import load_candidates, pick_channel
from outreach.plan.route import (Budget, CompanyFacts, PersonFacts, Route, classify_role, free_again, route_all,
                                 seniority)
from outreach.ranking.common import canonical_ids
from outreach.sources.keys import linkedin_key, person_name_key
from outreach.store import append_event, iso_utc, name_key, transaction
from outreach.store.writes import NAME_ALIAS_SOURCES

__all__ = ["TOOLS"]

CONTACTED_ENV = "OUTREACH_CONTACTED_TXT"  # a scratch copy for tests; else `[bridge] contacted_txt`
LIVE_CHECKOUT = "$HOME/Desktop/Sept 23 Companies"
CANDIDATES = 3
ON_RECORD = 40
LEGACY_LINES = 20
FUZZY_CUTOFF = 0.84
CHECKS_USED = ("note.max_chars (300 characters, newlines count; LinkedIn takes no more)",
               "banned phrases (banned_phrases.txt, note.banned_scopes, plus note.extra_banned)",
               "em and en dashes")
CHECK_NOTE_NOT_HERE = ("check_note(author='user') is not on this branch yet, so only the hard limits above were "
                       "checked; a note is never refused for them, because it already went out.")
OUTCOME_WORDS = (("progressed", "a referral or interview"), ("replied", "a reply"), ("accepted", "accepted"),
                 ("queued", "email queued"))


# ---------------------------------------------------------------------------------------------- shared reads

def _cfg() -> PlanConfig:
    return default_plan_config()


def _week(conn: sqlite3.Connection, canon: dict[int, int], today: date, cfg: PlanConfig) -> dict:
    cap = linkedin_capacity(conn, canon, today, cfg)
    return {"used": cap.used, "limit": cap.budget, "hard_used": cap.hard_used, "hard_limit": cap.hard_limit,
            "remaining": cap.remaining, "window": "the last 7 days, today included",
            "text": f"{cap.hard_used} of {cap.hard_limit} requests in the last 7 days ({cap.used} of {cap.budget} "
                    "from the plan)."}


def _queue(conn: sqlite3.Connection, day: date, canon: dict[int, int]) -> dict[int, sqlite3.Row]:
    """Today's plan rows that are not removed, by surviving company."""
    rows = conn.execute("SELECT * FROM daily_list_entry WHERE list_date = ? AND state <> 'removed' ORDER BY rank",
                        (day.isoformat(),)).fetchall()
    out: dict[int, sqlite3.Row] = {}
    for r in rows:
        out.setdefault(canon.get(r["company_id"], r["company_id"]), r)
    return out


def _contacted_path(cfg: PlanConfig) -> Path:
    return Path(os.environ.get(CONTACTED_ENV) or cfg.get("bridge", "contacted_txt")).expanduser()


@dataclass(frozen=True)
class _Legacy:
    readable: bool
    message: str
    gaps: list[dict]  # {line, date, company, people}


def _legacy(conn: sqlite3.Connection, family: Family, names: dict[str, set[int]], cfg: PlanConfig) -> _Legacy:
    """Touch lines of the frozen contacted.txt that the store lacks: not imported, and no person of that name with a
    touch at a company of that name. Read only; a missing file is fine and says so."""
    path = _contacted_path(cfg)
    try:
        records = [r.raw for r in read_contacted_txt(path) if r.raw.get("kind") == "touch"]
    except SourceError:
        return _Legacy(False, "contacted.txt is not readable here (frozen history; Knock is the record), so no gaps "
                              "were checked.", [])
    imported = {r[0] for r in conn.execute("SELECT json_extract(raw, '$.line') FROM import_record "
                                           "WHERE source = 'contacted_txt'")}
    reached = _reached_names(conn, family)
    gaps = []
    for raw in records:
        if raw["line"] in imported:
            continue
        survivors = names.get(name_key(raw["company"]), set())
        wanted = [person_name_key(p["name"]) for p in raw["people"]]
        if survivors and all(any((s, w) in reached for s in survivors) for w in wanted):
            continue
        gaps.append({"line": clip(raw["line"], 300), "date": raw["date"], "company": clip(raw["company"]),
                     "people": [clip(p["name"]) for p in raw["people"]], **UNTRUSTED})
    message = (f"{len(gaps)} line{'' if len(gaps) == 1 else 's'} of contacted.txt the store lacks."
               if gaps else "contacted.txt has no line the store lacks.")
    return _Legacy(True, message, gaps)


def _reached_names(conn: sqlite3.Connection, family: Family) -> set[tuple[int, str]]:
    """(surviving company, person name key) for everyone with any outbound record: a live outreach touch, a sent
    legacy email or a LinkedIn log entry."""
    rows = conn.execute(
        "SELECT p.company_id, p.name FROM person p WHERE EXISTS (SELECT 1 FROM outreach_touch o WHERE "
        "o.person_id = p.person_id AND o.undone_at IS NULL) OR EXISTS (SELECT 1 FROM touch t WHERE "
        "t.person_id = p.person_id AND t.first_sent_at IS NOT NULL) OR EXISTS (SELECT 1 FROM linkedin_log l "
        "WHERE l.person_id = p.person_id)")
    return {(family.survivor(r["company_id"]), person_name_key(r["name"])) for r in rows}


def _name_index(conn: sqlite3.Connection, family: Family) -> dict[str, set[int]]:
    """Name key to the surviving companies that hold it: the company's own name, its name aliases and its name
    keys."""
    out: dict[str, set[int]] = {}

    def add(key: str, cid: int) -> None:
        if key and cid in family.companies:
            out.setdefault(key, set()).add(family.survivor(cid))

    for r in conn.execute("SELECT company_id, name FROM company"):
        add(name_key(r["name"]), r["company_id"])
    for r in conn.execute("SELECT alias_key, company_id, source FROM company_alias"):
        if r["source"] in NAME_ALIAS_SOURCES:
            add(r["alias_key"], r["company_id"])
    for r in conn.execute("SELECT name_key, company_id FROM company_name_key"):
        add(r["name_key"], r["company_id"])
    return out


# ---------------------------------------------------------------------------------------------- the company

@dataclass(frozen=True)
class _Found:
    resolution: Literal["found", "ambiguous", "fuzzy", "unknown"]
    survivor: int | None
    candidates: list[dict]
    message: str
    add_command: str | None
    merged_note: str | None = None


def _add_command(name: str) -> str:
    return (f'uv run --directory "{LIVE_CHECKOUT}" python -m outreach.sources add --name {shlex.quote(name)} '
            '--reason "found on LinkedIn"')


def _candidate(conn: sqlite3.Connection, cid: int) -> dict:
    row = company_row(conn, cid)
    return {"company_id": cid, "name": clip(row["name"]), "status": row["status"], **UNTRUSTED}


def find_company(conn: sqlite3.Connection, family: Family, names: dict[str, set[int]], *, name: str | None,
                 company_id: int | None) -> _Found:
    """The company by id (its survivor) or by name key. Two companies with the key, or no exact key and a near
    one, is a question with up to 3 candidates; no near name is "not in Knock". Never a guess, never an add."""
    if company_id is not None:
        if company_id not in family.companies:
            raise ToolError("not_found", f"No company {company_id}.", "Use a company id from contact_check.")
        survivor = family.survivor(company_id)
        note = None if survivor == company_id else (f"Company {company_id} was merged into {survivor}; this is "
                                                    f"{survivor}.")
        return _Found("found", survivor, [], "In Knock.", None, note)
    text = " ".join((name or "").split())
    key = name_key(text)
    hits = sorted(names.get(key, set()))
    if len(hits) == 1:
        return _Found("found", hits[0], [], "In Knock.", None)
    if len(hits) > 1:
        return _Found("ambiguous", None, [_candidate(conn, c) for c in hits[:CANDIDATES]],
                      f"More than one company in Knock is called {text!r}. Ask Devanshu which, then call again "
                      "with its company_id.", None)
    near = get_close_matches(key, list(names), n=CANDIDATES, cutoff=FUZZY_CUTOFF) if key else []
    close = sorted({c for k in near for c in names[k]})[:CANDIDATES]
    if close:
        return _Found("fuzzy", None, [_candidate(conn, c) for c in close],
                      f"No company in Knock is called {text!r}; these are close. Ask Devanshu whether one is it, "
                      "then call again with its company_id. If none is, ask whether to add it.",
                      _add_command(text) if text else None)
    return _Found("unknown", None, [], f"{text!r} is not in Knock. Add it? Ask Devanshu once; on his yes, run the "
                                       "add command in the live checkout (never guess a domain).",
                  _add_command(text) if text else None)


@dataclass
class _Company:
    survivor: int
    row: sqlite3.Row
    facts: CompanyFacts
    state: dict
    on_queue: sqlite3.Row | None


def _company(conn: sqlite3.Connection, family: Family, survivor: int, outbound: list[Outbound], queue: dict,
             no_go_companies: set[int], held: set[int], today: date, cfg: PlanConfig) -> _Company:
    row = company_row(conn, survivor)
    st = contact_states(outbound, [survivor], today, cfg)[survivor]
    rotate = cfg.get("linkedin", "rotate_days")
    mine = [o for o in outbound if o.company_id == survivor]
    wave = [o for o in mine if o.queued or (today - o.anchor_on).days < rotate]
    contact, reason = st.status, st.reason
    if survivor in held and contact != "engaged":
        contact, reason = "engaged", "Someone replied"
    free_on = free_again(max((o.anchor_on for o in wave), default=None), cfg) if wave else None
    facts = CompanyFacts(
        name=row["name"], status=row["status"], close_reason=row["close_reason"], no_go=survivor in no_go_companies,
        paused=row["paused_at"] is not None, contact=contact, contact_reason=reason,
        wave_people=len({o.person_id for o in wave if o.person_id is not None}), free_on=free_on,
        email_people=st.email_people, on_queue=survivor in queue)
    if facts.no_go:
        label = "no_go"
    elif row["status"] == "removed":
        label = "removed"
    elif row["status"] == "closed":
        label = "done"
    elif facts.paused:
        label = "paused"
    else:
        label = {"engaged": "engaged", "waiting": "in_flight", "stale": "asked_before", "free": "new"}[contact]
    state = {"label": label, "reason": reason, "free_again": free_on.isoformat() if free_on else None,
             "on_today_queue": facts.on_queue, "wave_people": facts.wave_people,
             "wave_size": cfg.route["wave_size"], "people_contacted": st.people_contacted,
             "email_people": st.email_people, "close_reason": row["close_reason"]}
    return _Company(survivor, row, facts, state, queue.get(survivor))


def _verdict(state: dict) -> str:
    """The company-level line: go, in_flight, asked_before or stop."""
    label = state["label"]
    if label in ("no_go", "removed", "done", "paused"):
        return "stop"
    if label in ("in_flight", "engaged"):
        return "in_flight"
    return "asked_before" if label == "asked_before" else "go"


def _budget(week: dict, on_queue: bool, used_plan: int = 0, used_hard: int = 0) -> Budget:
    hard_left = week["hard_limit"] - week["hard_used"] - used_hard
    if on_queue:
        return Budget(max(0, min(week["limit"] - week["used"] - used_plan, hard_left)), "linkedin.plan_quota")
    return Budget(max(0, hard_left), "linkedin.hard_total")


# ---------------------------------------------------------------------------------------------- people

def _touches(outbound: list[Outbound], person_id: int) -> list[dict]:
    out = []
    for o in sorted((o for o in outbound if o.person_id == person_id), key=lambda o: o.sent_on):
        flags = {"progressed": o.progressed, "replied": o.replied, "accepted": o.accepted, "queued": o.queued}
        outcome = next((words for k, words in OUTCOME_WORDS if flags[k]), "no answer yet")
        out.append({"channel": o.channel, "sent_on": o.sent_on.isoformat(), "outcome": outcome,
                    "source": o.context_source})
    return out


def _asked_on(outbound: list[Outbound], person_id: int) -> date | None:
    return max((o.sent_on for o in outbound if o.person_id == person_id), default=None)


def _known_facts(conn: sqlite3.Connection, rows: Sequence[sqlite3.Row], c: _Company, outbound: list[Outbound],
                 canon: dict[int, int], no_go_people: set[int], today: date, cfg: PlanConfig,
                 tenure: dict[int, int | None]) -> dict[int, PersonFacts]:
    """PersonFacts for stored people at one company. The email code is `pick_channel`'s, over the plan's own
    candidates (`load_candidates`, nobody counted as contacted), so the address rules are never re-derived here."""
    cands = load_candidates(conn, [c.survivor], canon, set(), today, cfg).get(c.survivor, [])
    by_id = {x.person_id: x for x in cands}
    said_no = said_no_ids(conn, [r["person_id"] for r in rows])
    others = any(x.role_kind in ("engineer", "manager", "founder", "alum") for x in cands)
    out = {}
    for r in rows:
        cand = by_id.get(r["person_id"])
        code = None
        if cand is not None:
            channel, why = pick_channel(cand, cands, cfg)
            code = why if channel == "email" else None
        title = r["title"] or ""
        out[r["person_id"]] = PersonFacts(
            name=r["name"], title=title, role_kind=r["role_kind"], seniority=seniority(title, cfg),
            tenure_months=tenure.get(r["person_id"]), no_go=r["person_id"] in no_go_people,
            said_no=r["person_id"] in said_no, asked_on=_asked_on(outbound, r["person_id"]), email_code=code,
            has_email=r["email"] is not None, recruiter_blocked=r["role_kind"] == "recruiter" and others)
    return out


def _person_out(row: sqlite3.Row | None, name: str, outbound: list[Outbound], route, pref: dict) -> dict:
    return {"person_id": row["person_id"] if row is not None else None, "name": clip(name),
            "known": row is not None, "title": clip(row["title"]) if row is not None else None,
            "role_kind": row["role_kind"] if row is not None else None,
            "touches": _touches(outbound, row["person_id"]) if row is not None else [],
            "route": {"verdict": route.verdict, "reason": route.reason, "key": route.key}, "preference": pref,
            **UNTRUSTED}


def _people_at(conn: sqlite3.Connection, family: Family, survivor: int) -> list[sqlite3.Row]:
    ids = family.members(survivor)
    return conn.execute(f"SELECT * FROM person WHERE company_id IN ({marks(ids)}) ORDER BY person_id", ids).fetchall()


def _match_person(rows: Sequence[sqlite3.Row], name: str, linkedin_url: str | None) -> sqlite3.Row | None:
    page = linkedin_key(linkedin_url) if linkedin_url else None
    if page:
        hit = next((r for r in rows if r["linkedin_url"] and linkedin_key(r["linkedin_url"]) == page), None)
        if hit is not None:
            return hit
    key = person_name_key(name)
    return next((r for r in rows if person_name_key(r["name"]) == key), None)


def _unknown_facts(name: str, title: str, tenure: int | None, cfg: PlanConfig) -> PersonFacts:
    return PersonFacts(name=name, title=title, role_kind=classify_role(title, cfg), seniority=seniority(title, cfg),
                       tenure_months=tenure, no_go=False, said_no=False, asked_on=None, email_code=None,
                       has_email=False, recruiter_blocked=False)


# ---------------------------------------------------------------------------------------------- knock_status

class StatusInput(Input):
    pass


def knock_status(ctx: Ctx, args: StatusInput) -> dict:
    conn, cfg = ctx.conn, _cfg()
    canon = canonical_ids(conn)
    family = Family(conn)
    outbound = load_outbound(conn, canon, tz=cfg.get("plan", "timezone"))
    ids = sorted({o.company_id for o in outbound})
    flight = sum(1 for s in contact_states(outbound, ids, ctx.today, cfg).values() if s.status == "waiting")
    queue = _queue(conn, ctx.today, canon)
    reached = {o.company_id for o in outbound if o.channel in ("linkedin_request", "email")
               and o.sent_on >= ctx.today}
    legacy = _legacy(conn, family, _name_index(conn, family), cfg)
    return {
        "reachable": True, "server_version": SERVER_VERSION, "today": ctx.today.isoformat(),
        "linkedin_week": _week(conn, canon, ctx.today, cfg),
        "companies_in_flight": flight,
        "today_queue": {"date": ctx.today.isoformat(), "size": len(queue),
                        "reached": sum(1 for s in queue if s in reached)},
        "contacted_txt": {"path": str(_contacted_path(cfg)), "readable": legacy.readable,
                          "gaps": len(legacy.gaps), "message": legacy.message},
    }


# ---------------------------------------------------------------------------------------------- contact_check

class CheckPerson(Input):
    name: str = Field(min_length=1, max_length=200)
    title: str = Field("", max_length=200)
    linkedin_url: str | None = Field(None, max_length=300)


class ContactCheckInput(Input):
    company: str | None = Field(None, min_length=1, max_length=200, description="The company's name as he has it.")
    company_id: int | None = Field(None, ge=1, le=MAX_ID, description="A company id (wins over the name).")
    people: list[CheckPerson] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def one_company(self):
        if self.company is None and self.company_id is None:
            raise ValueError("give company or company_id")
        return self


def contact_check(ctx: Ctx, args: ContactCheckInput) -> dict:
    conn, cfg = ctx.conn, _cfg()
    canon = canonical_ids(conn)
    family = Family(conn)
    names = _name_index(conn, family)
    found = find_company(conn, family, names, name=args.company, company_id=args.company_id)
    week = _week(conn, canon, ctx.today, cfg)
    legacy = _legacy(conn, family, names, cfg)
    asked = {person_name_key(p.name) for p in args.people}
    company_key = name_key(args.company or "")
    base = {"company": {"resolution": found.resolution, "message": found.message, "candidates": found.candidates,
                        "add_command": found.add_command, "merged_note": found.merged_note},
            "linkedin_week": week,
            "contacted_txt": {"readable": legacy.readable, "message": legacy.message}}
    if found.survivor is None:
        base["legacy"] = [g for g in legacy.gaps if name_key(g["company"]) == company_key
                          or any(person_name_key(n) in asked for n in g["people"])][:LEGACY_LINES]
        base.update({"state": None, "verdict": "not_in_knock", "people": []})
        return base
    outbound = load_outbound(conn, canon, tz=cfg.get("plan", "timezone"), company_ids=[found.survivor])
    no_go_people, no_go_companies = no_go_sets(conn, family.companies)
    c = _company(conn, family, found.survivor, outbound, _queue(conn, ctx.today, canon), no_go_companies,
                 held_survivors(family.companies), ctx.today, cfg)
    rows = _people_at(conn, family, c.survivor)
    facts = _known_facts(conn, rows, c, outbound, canon, no_go_people, ctx.today, cfg, {})
    listed: list[tuple[sqlite3.Row | None, str, PersonFacts]] = []
    seen: set[int] = set()
    for p in args.people:
        row = _match_person(rows, p.name, p.linkedin_url)
        if row is not None and row["person_id"] not in seen:
            seen.add(row["person_id"])
            listed.append((row, row["name"], facts[row["person_id"]]))
        elif row is None:
            listed.append((None, p.name, _unknown_facts(p.name, p.title, None, cfg)))
    for row in rows:
        if row["person_id"] not in seen and len(listed) < ON_RECORD:
            listed.append((row, row["name"], facts[row["person_id"]]))
    routed = route_all([f for _r, _n, f in listed], c.facts, _budget(week, c.facts.on_queue), ctx.today, cfg)
    people = [_person_out(listed[i][0], listed[i][1], outbound, r, pref) for i, r, pref in routed]
    company_names = {name_key(c.row["name"])} | {k for k, v in names.items() if c.survivor in v}
    members = family.members(c.survivor)
    reviews = conn.execute(
        f"SELECT review_id, reason FROM review_item WHERE kind = 'merge_candidate' AND state = 'open' AND "
        f"(company_id IN ({marks(members)}) OR evidence_key IN ({marks(members)}))",
        [*members, *[str(m) for m in members]]).fetchall()
    base["company"].update({"id": c.survivor, "name": c.row["name"], "status": c.row["status"]})
    base.update({
        "state": c.state, "verdict": _verdict(c.state), "people": people, "on_record_total": len(rows),
        "legacy": [g for g in legacy.gaps if name_key(g["company"]) in company_names
                   or any(person_name_key(n) in asked for n in g["people"])][:LEGACY_LINES],
        "open_merge_items": [{"review_id": r["review_id"], "reason": clip(r["reason"], 300), **UNTRUSTED}
                             for r in reviews[:5]],
    })
    return base


# ---------------------------------------------------------------------------------------------- intake

class EmailSourceIn(Input):
    url: str = Field(max_length=300, pattern=HTTPS, description="The public page that shows the address.")
    basis: Literal["published", "verified"]
    quote: str = Field(min_length=3, max_length=500, description="The words on the page, holding the address.")

    @field_validator("url")
    @classmethod
    def not_linkedin(cls, url: str) -> str:
        if is_linkedin(url):
            raise ValueError("LinkedIn is never a source")
        return url


class IntakePerson(Input):
    name: str = Field(min_length=1, max_length=200)
    title: str = Field("", max_length=200)
    company: str | None = Field(None, min_length=1, max_length=200)
    company_id: int | None = Field(None, ge=1, le=MAX_ID, description="When contact_check named the company.")
    linkedin_url: str | None = Field(None, max_length=300)
    email: str | None = Field(None, max_length=254, description="Only an address he gave or a page publishes.")
    email_source: EmailSourceIn | None = Field(None, description="The page that publishes or verifies the email.")
    tenure_months: int | None = Field(None, ge=0, le=600)
    source_text: str | None = Field(None, max_length=8000, description="What he pasted about them (data).")

    @model_validator(mode="after")
    def company_named(self):
        if self.company is None and self.company_id is None:
            raise ValueError("give company or company_id")
        if self.email_source is not None and not self.email:
            raise ValueError("email_source goes with an email")
        return self


class IntakeInput(Input):
    people: list[IntakePerson] = Field(min_length=1, max_length=20)


def _intake_key(survivor: int, p: IntakePerson) -> str:
    """What makes a pasted person the same person again: the company, the name key and the LinkedIn profile."""
    page = linkedin_key(p.linkedin_url) if p.linkedin_url else None
    return f"{survivor}:{person_name_key(p.name)}:{page or ''}"


def _person_in(p: IntakePerson, cfg: PlanConfig) -> PersonIn:
    sources = []
    if p.email_source is not None:
        sources.append(SourceIn(kind="email", url=p.email_source.url, quote=p.email_source.quote,
                                email_basis=p.email_source.basis))
    return PersonIn(name=p.name, title=p.title, role_kind=classify_role(p.title, cfg), email=p.email,
                    linkedin_url=p.linkedin_url, profile_text=p.source_text, notes=None, sources=sources)


def intake(ctx: Ctx, args: IntakeInput) -> dict:
    conn, cfg = ctx.conn, _cfg()
    wanted = clean_people([_person_in(p, cfg) for p in args.people])  # every person checked before any write
    groups: dict[tuple, list[int]] = {}
    for i, p in enumerate(args.people):
        groups.setdefault(("id", p.company_id) if p.company_id is not None else ("name", name_key(p.company)),
                          []).append(i)
    results: list[dict | None] = [None] * len(args.people)
    companies = []
    used_plan = used_hard = 0
    with transaction(conn):
        canon = canonical_ids(conn)
        family = Family(conn)
        names = _name_index(conn, family)
        week = _week(conn, canon, ctx.today, cfg)
        queue = _queue(conn, ctx.today, canon)
        no_go_people, no_go_companies = no_go_sets(conn, family.companies)
        held = held_survivors(family.companies)
        for (_, _key), idx in groups.items():
            first = args.people[idx[0]]
            found = find_company(conn, family, names, name=first.company, company_id=first.company_id)
            block = {"asked_as": clip(first.company) if first.company else None, "resolution": found.resolution,
                     "message": found.message, "candidates": found.candidates, "add_command": found.add_command,
                     "merged_note": found.merged_note, "people": idx}
            companies.append(block)
            if found.survivor is None:
                for i in idx:
                    results[i] = {"index": i, "name": clip(args.people[i].name), "recorded": False,
                                  "person_id": None, "route": None,
                                  "message": "Not recorded: the company is not settled in Knock yet."}
                continue
            outbound = load_outbound(conn, canon, tz=cfg.get("plan", "timezone"), company_ids=[found.survivor])
            c = _company(conn, family, found.survivor, outbound, queue, no_go_companies, held, ctx.today, cfg)
            block.update({"id": c.survivor, "name": c.row["name"], "state": c.state, "verdict": _verdict(c.state)})
            recorded: dict[int, dict] = {}
            if not (c.facts.no_go or c.row["status"] == "removed"):
                out = record_people(ctx, c.survivor, [wanted[i] for i in idx])
                recorded = {idx[r["index"]]: r for r in out["results"]}
            rows = {r["person_id"]: r for r in _people_at(conn, family, c.survivor)}
            tenure = {recorded[i]["person_id"]: args.people[i].tenure_months for i in recorded
                      if recorded[i]["person_id"] is not None}
            facts = _known_facts(conn, list(rows.values()), c, outbound, canon, no_go_people, ctx.today, cfg, tenure)
            listed: list[PersonFacts] = []
            for i in idx:
                rec = recorded.get(i)
                pid = rec["person_id"] if rec else None
                if pid is not None and pid in facts:
                    listed.append(facts[pid])
                else:
                    listed.append(_unknown_facts(args.people[i].name, args.people[i].title,
                                                 args.people[i].tenure_months, cfg))
            budget = _budget(week, c.facts.on_queue, used_plan, used_hard)
            for j, r, pref in route_all(listed, c.facts, budget, ctx.today, cfg):
                i = idx[j]
                rec = recorded.get(i)
                pid = rec["person_id"] if rec else None
                if rec is not None and rec["refused"] == "two_people":
                    r = Route("stop", rec["message"], "add_people.two_people")
                if r.verdict == "linkedin_note":
                    used_hard += 1
                    used_plan += 1 if c.facts.on_queue else 0
                results[i] = {
                    "index": i, "name": clip(args.people[i].name), "title": clip(args.people[i].title),
                    "role_kind": listed[j].role_kind, "person_id": pid,
                    "recorded": rec is not None and pid is not None and rec["refused"] is None,
                    "created": bool(rec and rec["created"]), "flags": rec["flags"] if rec else [],
                    "refused": rec["refused"] if rec else None,
                    "key": _intake_key(c.survivor, args.people[i]),
                    "route": {"verdict": r.verdict, "reason": r.reason, "key": r.key}, "preference": pref,
                    "message": rec["message"] if rec else None, **UNTRUSTED}
    return {"companies": companies, "people": results, "linkedin_week": week,
            "next": "People with route linkedin_note: he writes and sends the notes, then log_sent after he says "
                    "sent. Route email: draft with save_draft per the cold-email skill. Nothing was sent."}


# ---------------------------------------------------------------------------------------------- log_sent

class SentRequest(Input):
    person_id: int | None = Field(None, ge=1, le=MAX_ID)
    name: str | None = Field(None, min_length=1, max_length=200)
    company: str | None = Field(None, min_length=1, max_length=200)
    company_id: int | None = Field(None, ge=1, le=MAX_ID)
    channel: str = Field(pattern=r"^linkedin_request$", max_length=16, description="Only linkedin_request.")
    note_text: str = Field("", max_length=300, description="The exact note he sent (empty when he sent none). "
                                                           "300 characters at most, newlines count.")
    at: str | None = Field(None, max_length=40, description="When he sent it (ISO time). Default: now.")

    @model_validator(mode="after")
    def who(self):
        if self.person_id is None and (self.name is None or (self.company is None and self.company_id is None)):
            raise ValueError("give person_id, or name with company or company_id")
        return self


class LogSentInput(Input):
    requests: list[SentRequest] = Field(min_length=1, max_length=10)


def _note_warnings(note: str, cfg: PlanConfig) -> list[str]:
    """The hard limits that exist on this branch: banned phrases and dashes. Warnings only (the note went out)."""
    out = []
    banned = load_banned(scopes=tuple(cfg.get("note", "banned_scopes")))[0]
    if not banned:
        out.append("The banned phrases list could not be read, so the note was not checked for banned phrases.")
    rules = list(banned) + [(p, re.compile(p, re.I)) for p in cfg.get("note", "extra_banned")]
    hits = sorted({src for src, rx in rules if rx.search(note)})
    if hits:
        out.append("On the banned list: " + ", ".join(repr(h) for h in hits) + ".")
    if DASHES.search(note):
        out.append("The note has an em or en dash.")
    return out


def _sent_moment(raw: str | None, ctx: Ctx, cfg: PlanConfig) -> str:
    if raw is None:
        return ctx.now
    try:
        stamp = iso_utc(raw)
    except ValueError:
        stamp = None
    if stamp is None:
        raise ValueError(f"{raw!r} is not an ISO time such as 2026-11-10T15:14:00Z")
    if stamp > ctx.now:
        raise ValueError("that time is in the future")
    back = cfg.get("bridge", "log_back_days")
    if (ctx.today - date.fromisoformat(ny_day(stamp))).days > back:
        raise ValueError(f"that is more than {back} days ago; log it on the LinkedIn page instead")
    return stamp


def _own_role(conn: sqlite3.Connection, family: Family, survivor: int, today: date) -> sqlite3.Row | None:
    """The role the company names (the company view's rule order): its posting row, or None."""
    _, live = open_postings(conn, family.members(survivor), today)
    named = role_to_name(live)
    if named is None:
        return None
    return conn.execute("SELECT * FROM posting WHERE posting_id = ?", (named["posting_id"],)).fetchone()


def _bridge_key(conn: sqlite3.Connection, person_id: int, day: str, note: str) -> str:
    """`bridge:<person>:<New York day>:<note hash>`. A request he took back on the LinkedIn page keeps its key (an
    undone touch is never changed), so logging it again takes the next free `:again<n>` key."""
    key = f"bridge:{person_id}:{day}:{hashlib.sha256(note.encode('utf-8')).hexdigest()[:12]}"
    candidate, n = key, 1
    while True:
        row = conn.execute("SELECT undone_at FROM outreach_touch WHERE idempotency_key = ?", (candidate,)).fetchone()
        if row is None or row["undone_at"] is None:
            return candidate
        n += 1
        candidate = f"{key}:again{n}"


def _refused(i: int, why: str, message: str, **extra) -> dict:
    return {"index": i, "status": "refused", "refused": why, "message": message, **extra}


def log_sent(ctx: Ctx, args: LogSentInput) -> dict:
    conn, cfg = ctx.conn, _cfg()
    results = []
    touched: set[int] = set()
    with transaction(conn):
        canon = canonical_ids(conn)
        family = Family(conn)
        names = _name_index(conn, family)
        no_go_people, no_go_companies = no_go_sets(conn, family.companies)
        for i, req in enumerate(args.requests):
            try:
                sent_at = _sent_moment(req.at, ctx, cfg)
            except ValueError as err:
                results.append(_refused(i, "bad_time", f"at: {err}. Nothing was logged for this one."))
                continue
            person = None
            if req.person_id is not None:
                person = conn.execute("SELECT * FROM person WHERE person_id = ?", (req.person_id,)).fetchone()
                if person is None:
                    results.append(_refused(i, "not_found", f"No person {req.person_id}."))
                    continue
            else:
                found = find_company(conn, family, names, name=req.company, company_id=req.company_id)
                if found.survivor is None:
                    results.append(_refused(i, "company_" + found.resolution, found.message,
                                            candidates=found.candidates, add_command=found.add_command))
                    continue
                rows = _people_at(conn, family, found.survivor)
                hits = [r for r in rows if person_name_key(r["name"]) == person_name_key(req.name)]
                if len(hits) != 1:
                    results.append(_refused(
                        i, "person_unknown" if not hits else "person_ambiguous",
                        f"{req.name} is not in Knock at that company yet: run intake for them first." if not hits
                        else f"More than one {req.name} there: call again with person_id.",
                        candidates=[{"person_id": r["person_id"], "name": r["name"], "title": r["title"]}
                                    for r in hits[:CANDIDATES]]))
                    continue
                person = hits[0]
            survivor = family.survivor(person["company_id"])
            main = company_row(conn, survivor)
            who = {"person": {"id": person["person_id"], "name": person["name"]},
                   "company": {"id": survivor, "name": main["name"]}}
            if person["person_id"] in no_go_people or survivor in no_go_companies:
                results.append(_refused(i, "no_go", f"{person['name']} or {main['name']} is on the no-go list, so "
                                                    "nothing is logged. Lift it first if that is wrong.", **who))
                continue
            live = conn.execute("SELECT sent_at FROM outreach_touch WHERE person_id = ? AND channel = "
                                "'linkedin_request' AND undone_at IS NULL ORDER BY sent_at LIMIT 1",
                                (person["person_id"],)).fetchone()
            if live is not None:
                results.append({"index": i, "status": "already_logged", **who,
                                "message": f"Already logged: a request to {person['name']} on "
                                           f"{ny_day(live['sent_at'])}. Nothing was written."})
                continue
            day = ny_day(sent_at)
            key = _bridge_key(conn, person["person_id"], day, req.note_text)
            entry = conn.execute("SELECT * FROM daily_list_entry WHERE list_date = ? AND state <> 'removed' AND "
                                 f"company_id IN ({marks(family.members(survivor))})",
                                 [day, *family.members(survivor)]).fetchone()
            if entry is not None:
                posting = conn.execute("SELECT * FROM posting WHERE posting_id = ? AND closed_at IS NULL",
                                       (entry["posting_id"],)).fetchone() if entry["posting_id"] else None
                context = plan_context(conn, lane=entry["list_group"], survivor=survivor,
                                       role_kind=person["role_kind"], posting=posting, now=sent_at)
            else:
                posting = _own_role(conn, family, survivor, ctx.today)
                context = own_context(conn, survivor, person["role_kind"],
                                      posting["first_seen_at"] if posting is not None else None, sent_at)
            touch_id, created = record_request(
                conn, person_id=person["person_id"], company_id=survivor, sent_at=sent_at, recorded_at=ctx.now,
                context=context, idempotency_key=key, posting_id=posting["posting_id"] if posting else None,
                note=req.note_text, actor="claude")
            if created:
                append_event(conn, entity="outreach_touch", kind="request_logged", actor="claude", entity_id=touch_id,
                             company_id=survivor, at=sent_at, idempotency_key=key,
                             detail={"via": "terminal", "personId": person["person_id"],
                                     "noteChars": len(req.note_text), "source": context["source"]})
            touched.add(survivor)
            warnings = _note_warnings(req.note_text, cfg) if req.note_text else []
            if main["paused_at"] is not None:
                warnings.append(f"{main['name']} is paused in Knock; the request is logged as history.")
            results.append({"index": i, "status": "logged" if created else "already_logged", **who,
                            "touch_id": touch_id, "sent_on": day, "source": context["source"],
                            "note_chars": len(req.note_text), "warnings": warnings,
                            "message": "Logged." if created else "Already logged under this key; nothing written."})
        week = _week(conn, canon, ctx.today, cfg)
        if week["hard_used"] > week["hard_limit"]:
            for r in results:
                if r["status"] == "logged":
                    r["warnings"].append(f"Over {week['hard_limit']} requests in the last 7 days.")
        outbound = load_outbound(conn, canon, tz=cfg.get("plan", "timezone"), company_ids=sorted(touched))
        queue = _queue(conn, ctx.today, canon)
        held = held_survivors(family.companies)
        states = [{"company_id": s, "name": company_row(conn, s)["name"],
                   **_company(conn, family, s, outbound, queue, no_go_companies, held, ctx.today, cfg).state}
                  for s in sorted(touched)]
    return {"results": results, "linkedin_week": week, "companies": states,
            "checks_used": list(CHECKS_USED), "check_note": CHECK_NOTE_NOT_HERE}


# ---------------------------------------------------------------------------------------------- the tools

_PEOPLE_GRANTS = {"person": Grant(insert=True, update=frozenset({"profile_text", "notes", "updated_at", "revision"})),
                  "person_source": Grant(insert=True), "event": Grant(insert=True)}

TOOLS = (
    ToolSpec("knock_status",
             "Call first from a terminal skill: whether Knock answers, this server's version, LinkedIn requests in "
             "the last 7 days (plan 150, all 200), companies in flight, today's queue, and lines of the frozen "
             "contacted.txt the store lacks. If this fails, say 'Not logged in Knock' and print a paste block.",
             StatusInput, knock_status),
    ToolSpec("contact_check",
             "Before reaching anyone: one company by name or id (merges followed; a near name or a tie comes back "
             "as a question; an unknown one as 'not in Knock: add it?' with the by-hand command, never added "
             "here), its state, each person's touches, the week's count and one advice line per person.",
             ContactCheckInput, contact_check),
    ToolSpec("intake",
             "Record the people Devanshu pasted (1 to 20, any companies): name, title, company, and a LinkedIn "
             "link, email with its page, tenure and his paste when given. Each is stored as found, not contacted, "
             "with a route: linkedin_note, email, wait, find_someone_else or stop, each with a reason. Never "
             "sends. A second identical call writes nothing.",
             IntakeInput, intake, _PEOPLE_GRANTS),
    ToolSpec("log_sent",
             "Call only after Devanshu says he sent these LinkedIn requests. Records each request he sent (who, "
             "the exact note, when); never sends, never records an accept, reply or email. Counts toward the "
             "7-day windows and moves the company in flight. A second call logs nothing new.",
             LogSentInput, log_sent,
             {"linkedin_log": Grant(insert=True), "outreach_touch": Grant(insert=True), "event": Grant(insert=True)}),
)
