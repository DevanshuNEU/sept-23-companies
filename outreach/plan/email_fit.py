"""Email fit and the email lane (2026-10-27, Devanshu: "flag me which company to send emails to").

He finds the people himself and brings their names to his terminal, where each one gets a cold email (a draft he
approves in the Outbox) or a LinkedIn note. This module says, for each company, whether email is worth it there. It
only flags and lists companies: it never names a person, drafts, approves or sends anything.

`email_fit` is pure: facts in, an `EmailFit` out (a verdict and short reasons in plain words). A company is `email`
when nothing below blocks it, else `linkedin_only` with every blocker, in this order:

1. it is on the no-go list, removed or closed;
2. he paused it;
3. a reply holds it (any channel, #9), or someone there replied or a referral or interview is in progress;
4. `email.people_per_company` people there were already emailed (#9: 1 or 2 people per company);
5. its wave is full (`queue.requests_per_company` people asked inside `linkedin.rotate_days`): wait until it is free;
6. no website domain on file: an address pattern needs one, so it is LinkedIn only until he sets it;
7. the cached mail server check (`mx_check`) says the domain takes no mail (#9's approve check blocks that too);
8. it is bigger than `email.email_max_headcount` by its best size fact (the lower end of the range, as the size gate
   reads it). No size on file is fine: a company on the ranked list already passed the size gate.

A domain is the company's best `domain` fact across its merged family (the `company_fact` order), leaving out shared
keys (a parent's portal, a payroll host) and free mail, payroll and platform hosts; a guess counts and says so.

`email_lane` is the second list (pure as well): up to `email.email_companies_per_day` companies a day beyond today's
picks, by the stored ranking, that the plan could pick today (`select.eligibility`: not held back by Not now or an
application, not in flight, the rotation rules) and that are `email`. `load_email_facts` is the store read, one query
per table however many companies.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from outreach.drafts.followups import company_map, held_survivors, no_go_sets
from outreach.plan.config import PlanConfig
from outreach.plan.contact import ContactState, load_contact_states
from outreach.plan.route import day_text
from outreach.plan.select import BenchPick, Prospect, eligibility, fresh_any, fresh_pick
from outreach.ranking.common import fact_order_key, size_order_key
from outreach.sources.keys import classify_domain

__all__ = ["VERDICTS", "EmailFacts", "EmailFit", "email_fit", "size_words", "plain_domain", "load_email_facts",
           "email_lane", "lane_text"]

VERDICTS = ("email", "linkedin_only")
LABELS = {"email": "Email", "linkedin_only": "LinkedIn only"}
_CHUNK = 500


@dataclass(frozen=True)
class EmailFacts:
    company_id: int  # the survivor of any merge
    status: str  # company.status
    no_go: bool
    paused: bool
    held: bool  # a reply holds the company (`held_survivors`)
    contact: ContactState
    domain: str | None  # the best usable domain, or None
    domain_confidence: str | None  # its confidence: confirmed, estimate or guess
    mail_server: str | None  # the cached `mx_check` status of the domain: ok, none, or None when not checked
    size_low: int | None  # the best size fact's range (`size` over `headcount`), None when not on file
    size_high: int | None
    domain_source: str | None = None  # the domain's evidence source_kind (hand, old_dashboard, hn...)
    domain_locked: bool = False  # he set the domain by hand


@dataclass(frozen=True)
class EmailFit:
    verdict: Literal["email", "linkedin_only"]
    reasons: tuple[str, ...]  # plain words, the most important first; the blockers when linkedin_only
    domain: str | None

    @property
    def label(self) -> str:
        return LABELS[self.verdict]


def size_words(low: int, high: int | None) -> str:
    """"About 50 people", "11 to 50 people", "300 or more people"."""
    if high is None:
        return f"{low} or more people"
    if high == low:
        return f"About {low} {'person' if low == 1 else 'people'}"
    return f"{low} to {high} people"


def _blockers(f: EmailFacts, cfg: PlanConfig) -> list[str]:
    out = []
    if f.no_go:
        out.append("On the no-go list")
    if f.status == "removed":
        out.append("Removed from Knock")
    elif f.status == "closed":
        out.append("Closed in Knock")
    if f.paused:
        out.append("You paused it")
    st = f.contact
    cap = cfg.get("email", "people_per_company")
    if f.held or st.status == "engaged":
        out.append("Someone there replied: work that thread first")
    elif st.email_people >= cap:
        out.append(f"{st.email_people} {'person' if st.email_people == 1 else 'people'} there already emailed "
                   f"(up to {cap})")
    elif st.status == "waiting" and st.wave_room <= 0:
        free = f", free again {day_text(st.free_on)}" if st.free_on is not None else ""
        out.append(f"Wave in flight: {st.wave_people} asked{free}")
    if f.domain is None:
        out.append("No website on file, so no address pattern")
    elif f.mail_server == "none":
        out.append(f"{f.domain} takes no mail (no mail server)")
    most = cfg.get("email", "email_max_headcount")
    if f.size_low is not None and f.size_low > most:
        out.append(f"{size_words(f.size_low, f.size_high)}: over {most}, a cold email gets lost there")
    return out


def email_fit(f: EmailFacts, cfg: PlanConfig) -> EmailFit:
    """The verdict for one company, in the module's rule order. `email` says why it works; `linkedin_only` lists
    every blocker."""
    blocked = _blockers(f, cfg)
    if blocked:
        return EmailFit("linkedin_only", tuple(blocked), f.domain)
    reasons = [f"Website known ({f.domain}{', a guess' if f.domain_confidence == 'guess' else ''})"]
    if f.size_low is None:
        reasons.append("Size not on file, no sign it is large")
    else:
        top = f.size_high if f.size_high is not None else f.size_low
        who = "a founder reads this" if top <= cfg.get("people", "founder_max_headcount") \
            else "a founder or engineer reads this"
        reasons.append(f"{size_words(f.size_low, f.size_high)}: {who}")
    st = f.contact
    cap = cfg.get("email", "people_per_company")
    if st.email_people > 0:
        reasons.append(f"{st.email_people} of {cap} emailed there so far")
    if st.status == "waiting" and st.wave_room > 0:
        reasons.append(f"{st.wave_people} asked in this wave, room for {st.wave_room} more")
    return EmailFit("email", tuple(reasons), f.domain)


# ---------------------------------------------------------------------------------------------- the lane

def email_lane(prospects: Sequence[Prospect], taken: Collection[int], fits: Mapping[int, EmailFit],
               cfg: PlanConfig) -> list[BenchPick]:
    """Up to `email.email_companies_per_day` companies not in `taken` (every company on today's list, in any state)
    that the plan could pick today (`eligibility`) and whose fit is `email`, by the stored ranking (position, then
    id). Each carries the new opening the bench would flag, if any, so its row names the same role."""
    n = cfg.get("email", "email_companies_per_day")
    found = [p for p in prospects
             if p.company_id not in taken and eligibility(p, cfg)[0]
             and (fit := fits.get(p.company_id)) is not None and fit.verdict == "email"]
    found.sort(key=lambda p: (p.position, p.company_id))
    out = []
    for p in found[:max(0, n)]:
        early = fresh_pick(p, cfg)
        out.append(BenchPick(p, early or fresh_any(p, cfg), early is not None))
    return out


def lane_text(n: int, cfg: PlanConfig) -> str:
    """The one line over the email lane."""
    if n == 0:
        return "No other company can be emailed today. Companies come back after their wave or with a website on file."
    per = cfg.get("email", "people_per_company")
    return (f"{n} more {'company' if n == 1 else 'companies'} you can email today, by rank. Find up to {per} people at "
            "each and bring the names to your terminal.")


# ---------------------------------------------------------------------------------------------- the read

def plain_domain(value: str | None) -> str | None:
    """A stored domain as a bare host (`www.` dropped), or None when it is not one or is a free mail, payroll or
    platform host (`classify_domain`)."""
    host = (value or "").strip().lower().removeprefix("www.")
    if not host or "." not in host or any(c in host for c in "/:?#@ \t"):
        return None
    return host if classify_domain(host) == "company" else None


def _marks(n: int) -> str:
    return ", ".join("?" for _ in range(n))


def _rows(conn: sqlite3.Connection, sql: str, ids: Sequence[int]) -> list[sqlite3.Row]:
    out: list[sqlite3.Row] = []
    for i in range(0, len(ids), _CHUNK):
        part = ids[i:i + _CHUNK]
        out += conn.execute(sql.format(marks=_marks(len(part))), part).fetchall()
    return out


def load_email_facts(conn: sqlite3.Connection, canon: dict[int, int], survivors: Collection[int], today: date,
                     cfg: PlanConfig, *, states: Mapping[int, ContactState] | None = None) -> dict[int, EmailFacts]:
    """`EmailFacts` for each survivor in `survivors`, its merged duplicates folded in. `states` are the contact states
    when the caller has them (else they are read here)."""
    wanted = sorted({canon.get(c, c) for c in survivors})
    if not wanted:
        return {}
    keep = set(wanted)
    family = sorted(c for c in {*canon, *wanted} if canon.get(c, c) in keep)
    companies = company_map(conn)
    held = held_survivors(companies)
    _, no_go = no_go_sets(conn, companies)
    if states is None or any(c not in states for c in wanted):
        states = load_contact_states(conn, wanted, canon, today, cfg)
    shared = {r[0] for r in conn.execute("SELECT value FROM shared_key WHERE kind = 'domain'")}
    domains: dict[int, tuple[tuple, str, sqlite3.Row]] = {}
    for r in _rows(conn, "SELECT evidence_id, company_id, value, confidence, locked, recorded_at, source_kind "
                         "FROM evidence WHERE fact = 'domain' AND company_id IN ({marks})", family):
        host = plain_domain(r["value"])
        if host is None or host in shared or r["value"] in shared:
            continue
        cid = canon.get(r["company_id"], r["company_id"])
        key = fact_order_key(r)
        if cid not in domains or key > domains[cid][0]:
            domains[cid] = (key, host, r)
    mx = {r["domain"]: r["status"] for r in conn.execute(
        "SELECT domain, status FROM mx_check WHERE domain IN (SELECT value FROM json_each(?))",
        (json.dumps(sorted({d[1] for d in domains.values()})),))}
    sizes: dict[int, sqlite3.Row] = {}
    for r in _rows(conn, "SELECT evidence_id, company_id, fact, num_low, num_high, confidence, locked, recorded_at "
                         "FROM evidence WHERE fact IN ('size', 'headcount') AND company_id IN ({marks})", family):
        cid = canon.get(r["company_id"], r["company_id"])
        if cid not in sizes or size_order_key(r) > size_order_key(sizes[cid]):
            sizes[cid] = r
    out = {}
    for cid in wanted:
        row = companies.get(cid)
        if row is None:
            continue
        domain = domains.get(cid)
        size = sizes.get(cid)
        out[cid] = EmailFacts(
            company_id=cid, status=row["status"], no_go=cid in no_go, paused=row["paused_at"] is not None,
            held=cid in held, contact=states[cid], domain=domain[1] if domain else None,
            domain_confidence=domain[2]["confidence"] if domain else None,
            domain_source=domain[2]["source_kind"] if domain else None,
            domain_locked=bool(domain[2]["locked"]) if domain else False,
            mail_server=mx.get(domain[1]) if domain else None,
            size_low=size["num_low"] if size else None, size_high=size["num_high"] if size else None)
    return out
