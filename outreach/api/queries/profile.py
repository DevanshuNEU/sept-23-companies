"""A company's profile (the Today redesign): what does not change about a pick, each fact with where it comes from.

`queue_facts` builds one `CompanyProfile` per company row from what the store already holds, one query per table
however many rows:

- what it builds, its domains, the judge's reason and risk: the newest fresh judgment (`outreach.ranking.judged`);
- stage, headcount, funding, HQ, E-Verify, sponsorship: the best fact of each (`company_fact`, merged duplicates folded
  into their survivor), with its confidence and source in small words;
- visa history: the `lca` evidence rows (one per fiscal year), the newest two fiscal years added up;
- open roles at his level: `outreach.plan.fresh.load_level_roles`, up to five;
- people on record, and when the company is free again; his application and his Not nows.

A fact nothing is known about has `text` None: the page says "Not known". Nothing here writes, and no line is guessed.
"""
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlsplit

from outreach.api.models.plan import (CompanyProfile, ProfileHistory, ProfileRole, ProfileValue, QueueOpening,
                                      QueueStanding)
from outreach.api.queries.company import best_facts
from outreach.api.queries.outcomes import by_ids
from outreach.plan.config import PlanConfig
from outreach.plan.dates import ny_date
from outreach.plan.fresh import PASSED, age_words, load_level_roles
from outreach.plan.route import day_text
from outreach.ranking.confidence import scrub_text
from outreach.ranking.judged import Judgment

__all__ = ["STAGES", "SPONSORSHIP", "EVERIFY", "ROLES_SHOWN", "ProfileData", "count_words", "plain_url", "load_profile_data",
           "company_profile"]

ROLES_SHOWN = 5
PASSES_SHOWN = 3
PLACES_SHOWN = 3
LCA_YEARS = 2
STAGES = {"pre_seed": "pre-seed", "seed": "seed", "series_a": "Series A", "series_b": "Series B",
          "series_c": "Series C", "series_d": "Series D", "series_e_plus": "Series E or later",
          "late_private": "late stage private", "public": "public", "yc": "YC", "early": "early stage"}
SPONSORSHIP = {"has_sponsored": "Has sponsored before", "says_yes": "Says it sponsors", "says_no": "Does not sponsor",
               "opt_only": "OPT only", "unclear": "Unclear on sponsorship", "not_stated": "Sponsorship not stated"}
SPONSORSHIP_SHORT = {"has_sponsored": "Has sponsored before", "says_yes": "Says it sponsors",
                     "says_no": "Does not sponsor", "opt_only": "OPT only", "unclear": "Unclear"}
EVERIFY = {"enrolled": "E-Verify enrolled", "not_enrolled": "Not in E-Verify"}
SOURCES = {"hand": "added by you", "posting": "a posting", "old_dashboard": "your old dashboard", "yc": "Y Combinator",
           "builtin": "Built In", "linkedin": "LinkedIn", "website": "its website", "formd": "SEC Form D",
           "lca": "DOL disclosure data", "everify": "the E-Verify list", "other": "another source",
           "stapply": "Stapply", "commoncrawl": "Common Crawl", "hn": "Hacker News", "simplify": "Simplify"}
DOMAINS = {"ai_agents": "AI agents", "llm_apps": "LLM apps", "devtools": "Dev tools", "mcp": "MCP",
           "search_retrieval": "Search and retrieval", "evals": "Evals", "voice": "Voice",
           "full_stack_web": "Full stack web", "infra": "Infrastructure", "data": "Data", "security": "Security",
           "robotics_hardware": "Robotics and hardware", "fintech": "Fintech", "healthcare": "Healthcare",
           "marketing_sales": "Marketing and sales", "recruiting": "Recruiting"}  # "other" says nothing: left out
FUNDING_KINDS = {"form_d": "Form D", "form_d_amendment": "Form D amendment"}
PASS_WORDS = {"visa": "visa worry", "too_big": "too big", "too_early": "too early", "role": "role not for me",
              "product": "not into the product", "know_it": "already know it", "not_a_fit": "not a fit right now",
              "other": "other"}
NOT_KNOWN = ProfileValue(text=None, note=None)


@dataclass(frozen=True)
class ProfileData:
    """The bulk reads for a set of companies, by surviving company id."""

    facts: dict[int, dict[str, sqlite3.Row]]  # hq, everify, funding, headcount, sponsorship: the best row of each
    lca: dict[int, list[sqlite3.Row]]
    roles: dict[int, tuple]  # `Opening`s at his level, early career fits first then newest
    passes: dict[int, list[str]]
    now: datetime
    tz: str


def count_words(low: int | None, high: int | None) -> str | None:
    """"40 to 60 people", "38 people", "300 or more people"; None with no count."""
    if low is None:
        return None
    if high is None:
        return f"{low} or more people"
    return f"{low} {'person' if low == 1 else 'people'}" if high == low else f"{low} to {high} people"


def _note(confidence: str, source_kind: str, locked: bool) -> str:
    """How sure and from where, in small words: "estimate, LinkedIn". A fact he set by hand says so."""
    return "set by you" if locked else f"{confidence}, {SOURCES.get(source_kind, 'another source')}"


def _value(text: str | None, row) -> ProfileValue:
    if text is None or row is None:
        return NOT_KNOWN
    return ProfileValue(text=text, note=_note(row["confidence"], row["source_kind"], bool(row["locked"])))


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _long_day(d: date) -> str:
    return f"{day_text(d)}, {d.year}"


# ---------------------------------------------------------------------------------------------- bulk reads

def load_profile_data(conn: sqlite3.Connection, canon: dict[int, int], survivors: set[int], family: set[int],
                      owner: dict[int, int], now: datetime, cfg: PlanConfig) -> ProfileData:
    """Every table once for the whole set: the facts and LCA rows, his open roles at his level and his Not nows."""
    tz = cfg.get("plan", "timezone")
    rows: dict[int, list[sqlite3.Row]] = {}
    for r in by_ids(conn, "SELECT * FROM company_fact WHERE fact IN ('hq', 'everify', 'funding', 'headcount', "
                          "'sponsorship') AND company_id IN ({ids})", family):
        rows.setdefault(owner[r["company_id"]], []).append(r)
    lca: dict[int, list[sqlite3.Row]] = {}
    for r in by_ids(conn, "SELECT * FROM evidence WHERE fact = 'lca' AND company_id IN ({ids})", family):
        lca.setdefault(owner[r["company_id"]], []).append(r)
    passes: dict[int, list[str]] = {}
    for r in by_ids(conn, f"SELECT company_id, at, detail FROM event WHERE kind = '{PASSED}' AND entity = 'company' "
                          "AND company_id IN ({ids}) ORDER BY at DESC, event_id DESC", family):
        mine = passes.setdefault(owner[r["company_id"]], [])
        if len(mine) < PASSES_SHOWN:
            mine.append(_pass_line(r["at"], r["detail"], tz))
    return ProfileData(facts={c: best_facts(rs) for c, rs in rows.items()}, lca=lca,
                       roles=load_level_roles(conn, canon, survivors, now, cfg), passes=passes, now=now, tz=tz)


def _pass_line(at: str, detail: str, tz: str) -> str:
    try:
        reason = json.loads(detail).get("reason")
    except (TypeError, ValueError, AttributeError):
        reason = None
    line = f"Not now on {day_text(ny_date(at, tz))}"
    return f"{line}: {PASS_WORDS[reason]}" if reason in PASS_WORDS else line


# ---------------------------------------------------------------------------------------------- the pieces

def _funding(row) -> ProfileValue:
    if row is None:
        return NOT_KNOWN
    try:
        d = json.loads(row["value"])
        filed = date.fromisoformat(d["filed_on"])
        kind = FUNDING_KINDS.get(d.get("kind"), "Form D")
        amount = d.get("amount_usd")
    except (TypeError, ValueError, KeyError, AttributeError):
        return NOT_KNOWN
    text = f"{kind} filed {_long_day(filed)}"
    return _value(f"${amount:,} raised, {text}" if isinstance(amount, int) and amount > 0 else text, row)


def _lca(rows: Sequence[sqlite3.Row]) -> ProfileValue:
    """The software H-1B filings of the newest `LCA_YEARS` fiscal years on record, added up (one row per year: the
    newest recorded wins when a year was stored twice)."""
    years: dict[int, tuple[tuple, int]] = {}
    for r in rows:
        try:
            d = json.loads(r["value"])
            fy, software = int(d["fy"]), int(d["software"])
        except (TypeError, ValueError, KeyError):
            continue
        key = (r["recorded_at"], r["evidence_id"])
        if fy not in years or key > years[fy][0]:
            years[fy] = (key, software)
    if not years:
        return NOT_KNOWN
    shown = sorted(years)[-LCA_YEARS:]
    n = sum(years[fy][1] for fy in shown)
    span = " and ".join(f"FY{fy}" for fy in shown)
    what = "No software LCA filings" if n == 0 else f"{n} software LCA {'filing' if n == 1 else 'filings'}"
    return ProfileValue(text=f"{what} in {span}", note="DOL disclosure data")


def _sponsorship(word: str, fact_row) -> ProfileValue:
    if word == "not_stated":
        return NOT_KNOWN
    if fact_row is not None:
        note = _note(fact_row["confidence"], fact_row["source_kind"], bool(fact_row["locked"]))
    else:
        note = "from its LCA filings" if word == "has_sponsored" else "from a posting"
    return ProfileValue(text=SPONSORSHIP_SHORT[word], note=note)


def _people(on_record: int, contacted: int, standing: QueueStanding) -> ProfileValue:
    if on_record == 0:
        return ProfileValue(text="Nobody on record yet", note=None)
    if contacted == 0 and standing.asked == 0:
        asked = "none asked yet"
    elif standing.asked > 0:
        asked = f"{standing.asked} asked"
        if standing.free_on:
            asked += f", free again {day_text(date.fromisoformat(standing.free_on))}"
    else:
        asked = f"{contacted} asked before"
    return ProfileValue(text=f"{on_record} on record, {asked}", note=None)


def _places(roles: Sequence) -> ProfileValue:
    seen: list[str] = []
    for o in roles:
        place = scrub_text(" ".join(o.location.split()))
        if place and place not in seen:
            seen.append(place)
    return ProfileValue(text="; ".join(seen[:PLACES_SHOWN]), note=None) if seen else NOT_KNOWN


def plain_url(url: str | None) -> str | None:
    """The link as given when it is a plain http(s) URL with a host, else None."""
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return None
    return url if parts.scheme in ("http", "https") and parts.hostname else None


def company_profile(data: ProfileData, cid: int, d, judged: Judgment | None, standing: QueueStanding,
                    contacted: int, sponsorship_word: str, opening: QueueOpening | None,
                    applied_on: date | None) -> CompanyProfile:
    """The profile of one company row. `d` is the row's `DailyEntry`; `sponsorship_word` the word its page shows;
    `opening` the row's role; `applied_on` his newest application there."""
    facts = data.facts.get(cid, {})
    roles = data.roles.get(cid, ())
    size = d.size
    if size is not None and size.low is not None:
        headcount = ProfileValue(text=count_words(size.low, size.high),
                                 note=_note(size.confidence, size.source_kind, size.locked))
    elif "headcount" in facts:
        h = facts["headcount"]
        headcount = _value(count_words(h["num_low"], h["num_high"]), h)
    else:
        headcount = NOT_KNOWN
    stage = d.stage
    stage_value = (ProfileValue(text=_cap(STAGES[stage.value]), note=_note(stage.confidence, stage.source_kind, stage.locked))
                   if stage is not None and stage.value in STAGES else NOT_KNOWN)
    everify = facts.get("everify")
    everify_value = NOT_KNOWN
    if everify is not None and everify["value"] in EVERIFY:
        note = _note(everify["confidence"], everify["source_kind"], bool(everify["locked"]))
        if everify["observed_on"]:
            note += f", checked {day_text(date.fromisoformat(everify['observed_on']))}"
        everify_value = ProfileValue(text=EVERIFY[everify["value"]], note=note)
    hq = facts.get("hq")
    applied = (opening.applied_note if opening is not None and opening.applied_note
               else f"You applied on {day_text(applied_on)}." if applied_on is not None else None)
    return CompanyProfile(
        builds=ProfileValue(text=judged.builds, note=f"Judged {day_text(date.fromisoformat(judged.at[:10]))}"
                            + (", thin evidence" if judged.thin else "")) if judged is not None else NOT_KNOWN,
        domains=[DOMAINS[x] for x in judged.domains if x in DOMAINS] if judged is not None else [],
        verdict=ProfileValue(text=judged.why, note=None) if judged is not None else NOT_KNOWN,
        risk=judged.risk if judged is not None else None,
        stage=stage_value, funding=_funding(facts.get("funding")), headcount=headcount,
        hq=_value(hq["value"], hq) if hq is not None else NOT_KNOWN,
        role_places=_places(roles), everify=everify_value,
        sponsorship=_sponsorship(sponsorship_word, facts.get("sponsorship")), lca=_lca(data.lca.get(cid, ())),
        roles=[ProfileRole(posting_id=o.posting_id, title=o.title, url=plain_url(o.url), level=o.level,
                           age=age_words(o, data.now, data.tz), location=scrub_text(" ".join(o.location.split())),
                           early_fit=o.early_fit) for o in roles[:ROLES_SHOWN]],
        people=_people(d.people_count, contacted, standing),
        history=ProfileHistory(applied=applied, passes=data.passes.get(cid, [])))
