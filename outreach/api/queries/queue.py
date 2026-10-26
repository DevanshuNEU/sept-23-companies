"""Today as a company queue (#9 company-first ruling, 2026-10-26): the lines of a company row and the In flight list.

He finds the people on LinkedIn and writes his own notes; Knock picks the companies and tracks them. A company row of
`GET /api/plan` (no person, not an email row) gets a `CompanyQueue`, every line derived here from what the store
holds, one query per table however many rows:

- size and stage: the company's best `size` and `stage` facts, with their confidence;
- the role: the row's posting and its age (posted, else first seen), New York days;
- hiring now: open engineering postings (`posting_decision.lane`) posted, else first seen, in the last 30 days;
- sponsorship: the shown word (as the lean Today shows it) and the E-Verify fact;
- why it ranks: up to 3 reasons of the stored `why.signals`, else the row's reason;
- where it stands: `outreach.plan.contact` (the wave, its room and the day it is free again); a reply that holds the
  company counts as a reply;
- the last touch: the newest outbound row (`load_outbound`), with the person and their role;
- the email hint: `pick_channel` over the people on record (read only: the plan proposes no email rows);
- the people on record, and how many were asked;
- the people search: his saved search (an `event` of kind `linkedin_search_saved`), else a stored LinkedIn company id
  (a `company_alias` of source `linkedin`, key `linkedin:<id>`), else the name (`people_search_url`);
- the careers link (`pick_careers_url`) and the one line he copies to his terminal.

`in_flight` lists every company still being worked with a wave out inside `linkedin.rotate_days` (a queued email
counts) or a reply, replies first, then by the day each is free again. Reads only; the app never fetches LinkedIn.
"""
import json
import sqlite3
from collections.abc import Sequence
from datetime import date, timedelta

from outreach.api.models.company import CompanyRef
from outreach.api.models.plan import CompanyQueue, InFlightCompany, QueueStanding
from outreach.api.models.today import DailyEntry
from outreach.api.queries.company import Holds
from outreach.api.queries.outcomes import by_ids, company_refs, people_by_ids
from outreach.plan.careers import pick_careers_url
from outreach.plan.config import PlanConfig
from outreach.plan.contact import ContactState, contact_states
from outreach.plan.dates import ny_date
from outreach.plan.outbound import Outbound, load_outbound
from outreach.plan.people import (linkedin_search_url, load_candidates, people_search_url, pick_channel,
                                  rank_people)
from outreach.plan.route import day_text
from outreach.ranking.confidence import scrub_text

__all__ = ["SAVED_SEARCH", "LINKEDIN_ALIAS", "HIRING_DAYS", "standing", "queue_facts", "in_flight",
           "saved_searches", "linkedin_ids"]

SAVED_SEARCH = "linkedin_search_saved"  # event kind: his saved people search for a company, detail {url}
LINKEDIN_ALIAS = "linkedin:"  # company_alias key prefix of a LinkedIn company id (source 'linkedin')
HIRING_DAYS = 30
REASONS = 3
CONFIDENCE = {"confirmed": None, "estimate": "estimate", "guess": "guess"}
STAGES = {"pre_seed": "pre-seed", "seed": "seed", "series_a": "Series A", "series_b": "Series B",
          "series_c": "Series C", "series_d": "Series D", "series_e_plus": "Series E or later",
          "late_private": "late stage private", "public": "public", "yc": "YC", "early": "early stage"}
SPONSORSHIP = {"has_sponsored": "Has sponsored before", "says_yes": "Says it sponsors", "says_no": "Does not sponsor",
               "opt_only": "OPT only", "unclear": "Unclear on sponsorship", "not_stated": "Sponsorship not stated"}
EVERIFY = {"enrolled": "E-Verify enrolled", "not_enrolled": "Not in E-Verify"}
ROLE_WORDS = {"engineer": "Engineer", "manager": "Manager", "founder": "Founder", "recruiter": "Recruiter",
              "alum": "Alum", "other": "Other"}
TOUCH_WORDS = {"linkedin_request": "Request to", "linkedin_message": "Message to", "email": "Email to"}
EMAIL_HINTS = {"founder_published_email": "Email works here: a founder has a published address.",
               "hn_post_asks_email": "Email works here: an HN post there asks for email.",
               "no_linkedin_person": "Email works here: everyone on record is a recruiter."}


def _ago(days: int) -> str:
    return "today" if days <= 0 else ("1 day ago" if days == 1 else f"{days} days ago")


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# ---------------------------------------------------------------------------------------------- where it stands

def standing(st: ContactState, today: date, cfg: PlanConfig, *, held: bool = False) -> QueueStanding:
    """The row's "where it stands" from the company's contact state. A reply that holds the company (any channel)
    is a reply even when the touch behind it is not in the outbound read."""
    if st.status == "engaged" or held:
        reason = st.reason if st.status == "engaged" else "Someone replied"
        return QueueStanding(status="replied", text=reason, reason=reason, asked=st.wave_people, room=0, free_on=None)
    if st.status == "free":
        return QueueStanding(status="new", text="New", reason=st.reason, asked=0, room=st.wave_room, free_on=None)
    if st.status == "stale":
        return QueueStanding(status="asked_before", text=f"{st.people_contacted} asked before. {st.reason}",
                             reason=st.reason, asked=0, room=st.wave_room, free_on=None)
    free = st.free_on
    if st.wave_people == 0 or free is None or free <= today:  # a queued email keeps it waiting whatever its age
        text = st.reason
    else:
        since = free - timedelta(days=cfg.get("linkedin", "rotate_days"))
        parts = [f"{st.wave_people} asked {_ago((today - since).days)}"]
        if st.wave_room:
            parts.append(f"room for {st.wave_room} more")
        parts.append(f"free again {day_text(free)}")
        text = ", ".join(parts)
    return QueueStanding(status="in_flight", text=text, reason=st.reason, asked=st.wave_people, room=st.wave_room,
                         free_on=free.isoformat() if free is not None and free > today else None)


# ---------------------------------------------------------------------------------------------- the lines

def _fact_words(fact, words) -> str | None:
    if fact is None:
        return None
    note = CONFIDENCE.get(fact.confidence)
    return f"{words} ({note})" if note else words


def size_line(size, stage) -> str | None:
    """"40 to 60 people (estimate), seed (guess)", from the best size and stage facts; None when neither is known."""
    parts = []
    if size is not None and size.low is not None:
        if size.high is None:
            count = f"{size.low} or more people"
        elif size.high == size.low:
            count = _plural(size.low, "person", "people")
        else:
            count = f"{size.low} to {size.high} people"
        parts.append(_fact_words(size, count))
    if stage is not None and stage.value in STAGES:
        parts.append(_fact_words(stage, STAGES[stage.value]))
    return ", ".join(parts) if parts else None


def _posted_age(row: sqlite3.Row, today: date, tz: str) -> str:
    if row["posted_on"]:
        return f"posted {_ago((today - date.fromisoformat(row['posted_on'])).days)}"
    return f"first seen {_ago((today - ny_date(row['first_seen_at'], tz)).days)}"


def _reasons(why_text: str | None, fallback: str) -> list[str]:
    try:
        why = json.loads(why_text or "{}")
    except (TypeError, ValueError):
        why = {}
    signals = why.get("signals") if isinstance(why, dict) else None
    out = []
    for s in signals if isinstance(signals, list) else []:
        text = scrub_text(s.get("reason", "")).strip() if isinstance(s, dict) and isinstance(s.get("reason"), str) else ""
        if text and text not in out:
            out.append(text)
    return out[:REASONS] or [fallback]


def _last_touch(rows: Sequence[Outbound], people: dict[int, sqlite3.Row]) -> str | None:
    if not rows:
        return None
    newest = max(rows, key=lambda o: (o.sent_on, o.anchor_on, o.person_id or 0))
    person = people.get(newest.person_id) if newest.person_id is not None else None
    who = (f"{scrub_text(person['name'])} ({ROLE_WORDS.get(person['role_kind'], 'Other')})" if person is not None
           else "someone")
    what = TOUCH_WORDS.get(newest.channel, "Touch to")
    if newest.source == "queued_email":
        return f"{what} {who} waiting in the Outbox"
    return f"{what} {who} on {day_text(newest.sent_on)}"


def _people_line(on_record: int, asked: int) -> str:
    if on_record == 0:
        return "Nobody on record yet"
    return f"{_plural(on_record, 'person', 'people')} on record, {'none' if asked == 0 else asked} asked"


def _hiring_line(n: int) -> str:
    if n == 0:
        return f"No new engineering roles in the last {HIRING_DAYS} days"
    return f"{n} new engineering {'role' if n == 1 else 'roles'} in the last {HIRING_DAYS} days"


# ---------------------------------------------------------------------------------------------- bulk reads

def _family(canon: dict[int, int], survivors: set[int]) -> tuple[set[int], dict[int, int]]:
    family = set(survivors) | {c for c, s in canon.items() if s in survivors}
    return family, {c: canon.get(c, c) for c in family}


def saved_searches(conn: sqlite3.Connection, canon: dict[int, int], survivors: set[int]) -> dict[int, str]:
    """Each survivor's newest saved people search that is still a LinkedIn people search, across its family."""
    family, owner = _family(canon, survivors)
    out: dict[int, str] = {}
    for r in by_ids(conn, f"SELECT company_id, detail FROM event WHERE entity = 'company' AND kind = '{SAVED_SEARCH}' "
                          "AND company_id IN ({ids}) ORDER BY event_id", family):
        try:
            url = linkedin_search_url(json.loads(r["detail"]).get("url"))
        except (TypeError, ValueError, AttributeError):
            url = None
        if url is not None:
            out[owner[r["company_id"]]] = url
    return out


def linkedin_ids(conn: sqlite3.Connection, canon: dict[int, int], survivors: set[int]) -> dict[int, str]:
    """Each survivor's stored LinkedIn company id (`company_alias` source `linkedin`, key `linkedin:<id>`)."""
    family, owner = _family(canon, survivors)
    out: dict[int, str] = {}
    for r in by_ids(conn, "SELECT company_id, alias_key FROM company_alias WHERE source = 'linkedin' "
                          f"AND alias_key LIKE '{LINKEDIN_ALIAS}%' AND company_id IN ({{ids}}) "
                          "ORDER BY created_at, alias_key", family):
        value = r["alias_key"][len(LINKEDIN_ALIAS):]
        if value.isdigit() and len(value) <= 20:
            out.setdefault(owner[r["company_id"]], value)
    return out


def _hiring(conn: sqlite3.Connection, family: set[int], owner: dict[int, int], today: date, tz: str) -> dict[int, int]:
    since = today - timedelta(days=HIRING_DAYS - 1)
    out: dict[int, int] = {}
    for r in by_ids(conn, "SELECT p.company_id, p.posted_on, p.first_seen_at FROM posting p "
                          "JOIN posting_decision d ON d.posting_id = p.posting_id WHERE p.closed_at IS NULL "
                          "AND d.lane = 'engineering' AND p.company_id IN ({ids})", family):
        new_on = date.fromisoformat(r["posted_on"]) if r["posted_on"] else ny_date(r["first_seen_at"], tz)
        if since <= new_on <= today:
            out[owner[r["company_id"]]] = out.get(owner[r["company_id"]], 0) + 1
    return out


def _email_hints(conn: sqlite3.Connection, survivors: set[int], canon: dict[int, int], outbound: Sequence[Outbound],
                 states: dict[int, ContactState], today: date, cfg: PlanConfig) -> dict[int, str]:
    room = {c for c in survivors if states[c].status != "engaged"
            and states[c].email_people < cfg.get("email", "people_per_company")}
    if not room:
        return {}
    contacted = {o.person_id for o in outbound if o.person_id is not None}
    out = {}
    for company, cands in load_candidates(conn, room, canon, contacted, today, cfg).items():
        ranked = rank_people(cands)
        for c in ranked:
            channel, why = pick_channel(c, [s for s in ranked if s is not c], cfg)
            if channel == "email":
                out[company] = EMAIL_HINTS.get(why, "Email works here: the address is published or verified.")
                break
    return out


# ---------------------------------------------------------------------------------------------- the queue

def queue_facts(conn: sqlite3.Connection, entries: Sequence[sqlite3.Row], daily: Sequence[DailyEntry],
                canon: dict[int, int], cfg: PlanConfig, today: date,
                fallback: dict[int, str]) -> dict[int, CompanyQueue]:
    """A `CompanyQueue` for every company row (no person, not an email row), by the entry's rowid. `fallback` is
    each row's reason, the one reason shown when the stored why has no signal reasons."""
    rows = [(e, d) for e, d in zip(entries, daily) if e["person_id"] is None and e["channel"] != "email"]
    if not rows:
        return {}
    tz = cfg.get("plan", "timezone")
    survivors = {d.company.id for _, d in rows}
    family, owner = _family(canon, survivors)
    outbound = load_outbound(conn, canon, tz=tz, company_ids=survivors)
    states = contact_states(outbound, survivors, today, cfg)
    people = people_by_ids(conn, {o.person_id for o in outbound if o.person_id is not None})
    roles = {r["posting_id"]: r for r in by_ids(conn, "SELECT posting_id, posted_on, first_seen_at FROM posting "
                                                      "WHERE posting_id IN ({ids})",
                                                {d.role.posting_id for _, d in rows if d.role is not None})}
    boards: dict[int, list[tuple[str, str]]] = {}
    for r in by_ids(conn, "SELECT company_id, ats, token FROM board WHERE active = 1 AND company_id IN ({ids}) "
                          "ORDER BY board_id", family):
        boards.setdefault(owner[r["company_id"]], []).append((r["ats"], r["token"]))
    watched = {}
    for r in by_ids(conn, "SELECT company_id, url FROM watched_page WHERE company_id IN ({ids}) ORDER BY company_id",
                    family):
        watched.setdefault(owner[r["company_id"]], r["url"])
    everify = {}
    for r in by_ids(conn, "SELECT company_id, value FROM company_fact WHERE fact = 'everify' AND company_id IN ({ids}) "
                          "ORDER BY recorded_at DESC, evidence_id DESC", family):
        everify.setdefault(owner[r["company_id"]], r["value"])
    hiring = _hiring(conn, family, owner, today, tz)
    saved = saved_searches(conn, canon, survivors)
    ids = linkedin_ids(conn, canon, survivors)
    hints = _email_hints(conn, survivors, canon, outbound, states, today, cfg)
    by_company: dict[int, list[Outbound]] = {}
    for o in outbound:
        by_company.setdefault(o.company_id, []).append(o)

    out = {}
    for e, d in rows:
        cid = d.company.id
        st = states[cid]
        role = d.role
        posted = roles.get(role.posting_id) if role is not None else None
        word = d.sponsorship if role is not None or d.sponsorship != "not_stated" else "not_stated"
        sponsorship = ", ".join(w for w in (SPONSORSHIP[word], EVERIFY.get(everify.get(cid))) if w)
        n = hiring.get(cid, 0)
        out[e["rowid"]] = CompanyQueue(
            size_line=size_line(d.size, d.stage),
            role_line=f"{role.title}, {_posted_age(posted, today, tz)}" if role is not None and posted is not None
            else None,
            hiring=n, hiring_line=_hiring_line(n), sponsorship_line=sponsorship,
            reasons=_reasons(e["why"], fallback.get(e["rowid"], "Ranked for today.")),
            standing=standing(st, today, cfg, held=d.company.held),
            last_touch=_last_touch(by_company.get(cid, ()), people),
            email_hint=hints.get(cid),
            people_on_record=d.people_count, people_line=_people_line(d.people_count, st.people_contacted),
            people_search_url=people_search_url(d.company.name, saved_url=saved.get(cid), linkedin_id=ids.get(cid)),
            search_saved=cid in saved,
            careers_url=pick_careers_url(posting_url=role.url if role is not None else None,
                                         boards=boards.get(cid, ()), watched_url=watched.get(cid),
                                         domain=d.company.domain),
            terminal_line=f"Knock: {d.company.name} (company {cid})" + (f", role: {role.title}" if role else ""))
    return out


# ---------------------------------------------------------------------------------------------- in flight

def in_flight(conn: sqlite3.Connection, canon: dict[int, int], holds: Holds, cfg: PlanConfig,
              today: date) -> list[InFlightCompany]:
    """Every company still being worked (not closed or removed) with a wave out inside `linkedin.rotate_days` or a
    reply: replies first, then by the day each is free again, then by name."""
    outbound = load_outbound(conn, canon, tz=cfg.get("plan", "timezone"))
    ids = {o.company_id for o in outbound} | set(holds.held)
    if not ids:
        return []
    states = contact_states(outbound, ids, today, cfg)
    refs: dict[int, CompanyRef] = company_refs(conn, ids, holds=holds)
    out = []
    for cid, st in states.items():
        ref = refs.get(cid)
        held = cid in holds.held
        if ref is None or ref.status in ("closed", "removed") or (st.status not in ("waiting", "engaged") and not held):
            continue
        s = standing(st, today, cfg, held=held)
        out.append(InFlightCompany(company=ref, status="replied" if s.status == "replied" else "in_flight",
                                   text=s.text, reason=s.reason, free_on=s.free_on))
    return sorted(out, key=lambda f: (f.status != "replied", f.free_on or "9999-12-31", f.company.name.lower(),
                                      f.company.id))
