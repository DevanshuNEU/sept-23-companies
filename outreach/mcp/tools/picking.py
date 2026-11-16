"""pick_people (#290): code, not the model, decides who at a company gets a cold email.

#9's reach out ruling (2026-11-14) says who: US only, the job's city first, then its state, then the US; by size,
under 50 the founder or CTO first, then a founding engineer, then a recruiter; 50 to 200 the engineering manager and
a technical recruiter first, then the CTO or CEO, then an engineer; above 200 a university or technical recruiter
first, then the engineering manager, then an engineer. Never executives at companies over 200, agency or
non-engineering recruiters, interns, advisors, people outside the US, or anyone Knock says is done (emailed, no-go,
said no). The full Reach out feature (tables, arms, a scheduled job with an Apollo key) is still only a design
(`docs/superpowers/specs/2026-11-14-reach-out-design.md` on the reach-out branch); until it lands, `/outreach` hands
this tool the people an Apollo search returned (search is free) and reveals emails for the picked ones only.

The size is Knock's own (the sender's `company_sizes`), else the employee count Apollo gave, else unknown, which
uses the 50 to 200 order (Claude's call: manager and recruiter first is the safe middle). Roles are taken in turn
(the best of the first role, then the second, ...), so five picks are not five recruiters. Reads only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from outreach.drafts.followups import no_go_sets
from outreach.mcp.tools import Ctx, Input, ToolError, ToolSpec
from outreach.mcp.tools.drafts import posting_key
from outreach.mcp.tools.reads import MAX_ID, UNTRUSTED, Family, company_row, email_status, marks, open_postings
from outreach.rules.config import default_config
from outreach.pipeline.sizes import company_sizes

__all__ = ["TOOLS"]

MAX_CANDIDATES = 50
DONE_STATUSES = {"replied": "already replied", "bounced": "their address bounced", "closed": "already emailed"}


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    title: str = Field("", max_length=200)
    city: str | None = Field(None, max_length=100)
    region: str | None = Field(None, max_length=100, description="The state Apollo gives (New York or NY).")
    country: str | None = Field(None, max_length=100)
    linkedin_url: str | None = Field(None, max_length=300)
    apollo_id: str | None = Field(None, max_length=24, pattern=r"^[a-f0-9]{24}$", description="The id apollo_mixed_people_api_search "
                                                                               "returned, for the reveal.")


class PickInput(Input):
    company_id: int = Field(ge=1, le=MAX_ID)
    candidates: list[Candidate] = Field(min_length=1, max_length=MAX_CANDIDATES,
                                        description="The people an Apollo search returned at this company (data).")
    posting_url: str | None = Field(None, max_length=500, pattern=r"^https://\S+$",
                                    description="The job he pasted: its city and title words rank the people.")
    company_size: int | None = Field(None, ge=1, le=10_000_000,
                                     description="Apollo's employee count; used only when Knock has no size.")
    max_people: int = Field(5, ge=1, le=10, description="How many to pick (5 fits the 5-credit budget).")


# ------------------------------------------------------------------------------------------------ title words

def _has(title: str, *words: str) -> bool:
    return any(re.search(rf"\b{w}\b", title) for w in words)


@dataclass(frozen=True)
class Role:
    group: str       # founder, cto, founding_engineer, recruiter, manager, engineer
    role_kind: str   # what intake and add_people store
    sub: int         # rank inside the group (0 best)
    executive: bool  # an executive at a company over 200 is never picked


def role_of(title: str) -> Role | str:
    """The title's role, or the reason it is never picked."""
    t = title.lower()
    if _has(t, "intern", "internship", "co-op"):
        return "an intern"
    if _has(t, "advisor", "adviser", "board member", "investor", "board of directors"):
        return "an advisor"
    if _has(t, "recruit\\w*", "talent", "sourcer", "sourcing"):
        if _has(t, "agency", "staffing", "executive search"):
            return "an agency recruiter"
        if _has(t, "sales", "gtm", "go-to-market", "marketing", "finance", "non-technical", "business"):
            return "not an engineering recruiter"
        early = _has(t, "university", "campus", "early career", "new grad", "technical", "engineering", "tech")
        return Role("recruiter", "recruiter", 0 if early else 1, False)
    founder = _has(t, "founder", "co-founder", "cofounder")
    if founder or _has(t, "ceo", "chief executive", "cto", "chief technology", "president"):
        cto = _has(t, "cto", "chief technology")
        return Role("cto" if cto else "founder", "founder", 0, True)
    if _has(t, "founding engineer", "founding software engineer", "founding member of technical staff"):
        return Role("founding_engineer", "engineer", 0, False)
    eng = _has(t, "engineering", "engineer", "software", "technical", "developer", "platform", "infrastructure",
               "machine learning", "ml", "ai", "data")
    if _has(t, "chief", "vp", "vice president", "svp", "evp"):
        return Role("manager", "manager", 2, True) if eng else "an executive outside engineering"
    if _has(t, "head", "director", "manager", "lead") and eng:
        return Role("manager", "manager", 0 if _has(t, "manager") else 1, False)
    if _has(t, "sales engineer", "solutions engineer", "support engineer", "customer engineer", "field engineer"):
        return "not on the engineering team"
    if _has(t, "engineer", "developer", "programmer", "member of technical staff", "mts", "architect", "scientist"):
        return Role("engineer", "engineer", 0, False)
    return "not on the engineering team"


# Who comes first, by size (#9 reach out ruling). Groups in one tuple share a turn.
ORDER = {
    "under_50": (("cto", "founder"), ("founding_engineer",), ("recruiter",), ("engineer", "manager")),
    "50_to_200": (("manager",), ("recruiter",), ("cto", "founder"), ("engineer", "founding_engineer")),
    "over_200": (("recruiter",), ("manager",), ("engineer", "founding_engineer")),
}
ORDER["unknown"] = ORDER["50_to_200"]
GROUP_WORDS = {"cto": "CTO", "founder": "founder", "founding_engineer": "founding engineer", "recruiter": "recruiter",
               "manager": "engineering manager", "engineer": "engineer"}


def band_of(size: int | None, guessed_big: bool) -> str:
    if size is None:
        return "over_200" if guessed_big else "unknown"
    return "under_50" if size < 50 else "50_to_200" if size <= 200 else "over_200"


# ------------------------------------------------------------------------------------------------ places

def _states() -> dict[str, str]:
    loc = default_config().location
    names = {n.lower(): c for n, c in zip(loc["us_state_names"], loc["us_state_codes"])}
    return {**names, **{c.lower(): c for c in loc["us_state_codes"]}}


US_WORDS = {"us", "usa", "u.s.", "u.s.a.", "united states", "united states of america", "america"}
STOP_WORDS = {"software", "engineer", "engineering", "senior", "staff", "junior", "new", "grad", "graduate", "the",
              "of", "and", "for", "i", "ii", "iii", "iv", "member", "technical", "mts", "developer", "sr", "jr", "lead",
              "principal", "early", "career", "level", "entry", "remote", "hybrid", "full", "time", "in", "a", "to"}


def job_place(location: str | None) -> tuple[str | None, str | None]:
    """(city, state code) of a posting's location, either None when it does not say (remote, a country)."""
    if not location:
        return None, None
    states = _states()
    parts = [p.strip() for p in re.split(r"[,;/|]", location.split(" or ")[0]) if p.strip()]
    # The first part is the city when more follow ("New York, NY"); alone, it is a city only if no state has its name.
    rest = parts[1:] if len(parts) > 1 else parts
    state = next((states[p.lower()] for p in rest if p.lower() in states), None)
    first = parts[0] if parts else None
    city = first if first and first.lower() not in US_WORDS and (len(parts) > 1 or first.lower() not in states) \
        else None
    if city and city.lower() in ("remote", "anywhere"):
        city = None
    return city, state


def outside_us(c: Candidate) -> bool:
    if c.country:
        return c.country.strip().lower() not in US_WORDS
    return bool(c.region) and c.region.strip().lower() not in _states()


def team_words(title: str | None) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9+#]+", (title or "").lower()) if w not in STOP_WORDS and len(w) > 1}


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _split(name: str) -> tuple[str, str]:
    parts = name.strip().split()
    return (parts[0], " ".join(parts[1:])) if parts else ("", "")


def match_on_record(name: str, people: list) -> tuple[object | None, list[str]]:
    """(the person on record this name is, the names it could be when that is not certain).

    Apollo's free search masks surnames ("Alec Co***y", #294): a masked name matches a person with the same first
    name whose surname starts with the visible part and ends with the visible tail. One such person is a match; two
    or more is never guessed."""
    exact = [r for r in people if _key(r["name"]) == _key(name)]
    if exact:
        return exact[0], []
    first, last = _split(name)
    if "*" not in last:
        return None, []
    head, _, tail = last.partition("*")
    head, tail = _key(head), _key(tail.strip("*"))
    hits = [r for r in people if _key(_split(r["name"])[0]) == _key(first)
            and _key(_split(r["name"])[1]).startswith(head) and _key(_split(r["name"])[1]).endswith(tail)]
    if len(hits) == 1:
        return hits[0], []
    return None, [r["name"] for r in hits]


# ------------------------------------------------------------------------------------------------ the tool

def _posting(ctx: Ctx, ids: list[int], url: str | None) -> dict | None:
    rows = ctx.conn.execute(f"SELECT posting_id, title, url, location, closed_at FROM posting "
                            f"WHERE company_id IN ({marks(ids)})", ids).fetchall()
    if url is not None:
        found = [r for r in rows if r["url"] and posting_key(r["url"]) == posting_key(url)]
        if not found:
            raise ToolError("bad_input", "That job link is not a posting of this company in Knock.",
                            "Check it against the company tool's postings, or leave posting_url out. Nothing was read.")
        r = found[0]
    else:
        _total, live = open_postings(ctx.conn, ids, ctx.today)
        best = next((p for p in live if p["state"] == "open"), None)
        if best is None:
            return None
        r = next(r for r in rows if r["posting_id"] == best["posting_id"])
    return {"posting_id": r["posting_id"], "title": r["title"], "location": r["location"], **UNTRUSTED}


def _stop(conn, survivor: int, main, family: Family) -> str | None:
    _people, companies = no_go_sets(conn, family.companies)
    if survivor in companies:
        return "The company is on the no-go list."
    if main["status"] in ("closed", "removed"):
        return f"The company is {main['status']} in Knock."
    return None


def pick_people(ctx: Ctx, args: PickInput) -> dict:
    conn = ctx.conn
    family = Family(conn)
    if conn.execute("SELECT 1 FROM company WHERE company_id = ?", (args.company_id,)).fetchone() is None:
        raise ToolError("not_found", f"No company {args.company_id}.", "Use the id contact_check gave.")
    survivor = family.survivor(args.company_id)
    ids = family.members(survivor)
    main = company_row(conn, survivor)
    size, guessed_big = company_sizes(conn, [survivor])[survivor]
    basis = "knock" if size is not None else "apollo" if args.company_size is not None else "none"
    if size is None:
        size = args.company_size
    band = band_of(size, guessed_big)
    posting = _posting(ctx, ids, args.posting_url)
    head = {"company_id": survivor, "size": size, "size_band": band, "size_basis": basis, "posting": posting}
    stop = _stop(conn, survivor, main, family)
    if stop is not None:
        return {**head, "stop": stop, "picked": [],
                "not_picked": [{"name": c.name, "title": c.title, "reason": stop, **UNTRUSTED} for c in args.candidates]}

    city, state = job_place(posting["location"] if posting else None)
    words = team_words(posting["title"] if posting else None)
    states = _states()
    on_record = conn.execute(
        f"SELECT person_id, name, email FROM person WHERE company_id IN ({marks(ids)})", ids).fetchall()
    statuses = email_status(conn, [r["person_id"] for r in on_record])
    no_go_people, _companies = no_go_sets(conn, family.companies)

    groups: dict[str, list[tuple]] = {}
    not_picked = []
    seen = set()
    for n, c in enumerate(args.candidates):
        def skip(reason: str):
            not_picked.append({"name": c.name, "title": c.title, "reason": reason, **UNTRUSTED})
        if _key(c.name) in seen:
            skip("listed twice")
            continue
        seen.add(_key(c.name))
        if outside_us(c):
            skip("outside the US")
            continue
        role = role_of(c.title)
        if isinstance(role, str):
            skip(role)
            continue
        if role.executive and band == "over_200":
            skip("an executive at a company over 200 people")
            continue
        known, maybe = match_on_record(c.name, on_record)
        if known is not None:
            if known["person_id"] in no_go_people:
                skip("on the no-go list")
                continue
            status = statuses.get(known["person_id"], "not_started")
            if status in DONE_STATUSES or status in ("sending", "send_unknown", "sent"):
                skip(DONE_STATUSES.get(status, "already emailed"))
                continue
            if status != "not_started":
                skip(f"already has a {status} email in Knock")
                continue
        c_state = states.get((c.region or "").strip().lower())
        place = 0 if city and c.city and c.city.strip().lower() == city.lower() else 1 if state and c_state == state \
            else 2
        overlap = len(words & team_words(c.title))
        where = ("in " + city) if place == 0 else ("in " + state) if place == 1 else "in the US"
        why = f"{GROUP_WORDS[role.group]} {where}" + (", title shares the job's words" if overlap else "") + (
            f"; maybe {' or '.join(maybe)} in Knock (Apollo hides the surname): check after the reveal" if maybe else "")
        groups.setdefault(role.group, []).append(((role.sub, place, -overlap, n), c, role, known, why))

    for members in groups.values():
        members.sort(key=lambda m: m[0])
    picked = []
    turns = ORDER[band]
    while len(picked) < args.max_people and any(groups.get(g) for turn in turns for g in turn):
        for turn in turns:
            options = [groups[g][0] for g in turn if groups.get(g)]
            if not options or len(picked) >= args.max_people:
                continue
            best = min(options, key=lambda m: (m[0][1:], turn.index(m[2].group), m[0][0]))
            groups[best[2].group].pop(0)
            _k, c, role, known, why = best
            picked.append({"name": c.name, "title": c.title, "role_kind": role.role_kind, "why": why,
                           "apollo_id": c.apollo_id, "linkedin_url": c.linkedin_url,
                           "person_id": known["person_id"] if known else None,
                           "email_on_record": bool(known and known["email"]), **UNTRUSTED})
    for members in groups.values():
        for _k, c, _role, _known, _why in members:
            not_picked.append({"name": c.name, "title": c.title,
                               "reason": f"over the limit of {args.max_people} for this run", **UNTRUSTED})
    return {**head, "stop": None, "picked": picked, "not_picked": not_picked,
            "next": "Reveal emails for the picked people only (apollo_people_bulk_match with their apollo_id), "
                    "within the company's 5-credit budget. Anyone without a verified address is dropped."}


TOOLS = (
    ToolSpec("pick_people",
             "Who to cold email at a company, decided by code from #9's reach out ruling: give the people an Apollo "
             "search returned (free) and the job link; it returns the picks in order with why, and every other "
             "person with the reason they are not picked (outside the US, intern, advisor, agency or non-engineering "
             "recruiter, executive at a company over 200, already emailed, no-go, over the limit). Reveal emails "
             "for the picked people only. Reads only.", PickInput, pick_people),
)
