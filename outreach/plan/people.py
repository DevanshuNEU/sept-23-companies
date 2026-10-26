"""Candidate people for the daily plan (#15, Task 6): who to reach at a company, in what order, by which channel,
and the one door that stores a new person (`add_candidate`).

Safety rules held here and tested:
- a candidate's source is a public page and never LinkedIn (code refuses it before the table CHECK does);
- an email address is stored only with a source page whose quote contains it, never from GitHub or a noreply
  address, and the basis of an address is read only from a source whose quote holds that address;
- everything is read in bulk: one query per table, never one per company or person.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal
from urllib.parse import parse_qs, quote, urlsplit

from outreach.plan.config import PlanConfig, default_plan_config
from outreach.plan.dates import ny_date
from outreach.ranking.common import canonical_ids, size_order_key
from outreach.store.atomic import atomic
from outreach.store.timefmt import utcnow
from outreach.store.writes import append_event, name_key

__all__ = ["SourceRef", "SourceInput", "Candidate", "AddResult", "ROLE_KINDS", "SOURCE_KINDS", "load_candidates",
           "tier", "rank_people", "pick_channel", "people_search_url", "linkedin_search_url", "linkedin_company_id",
           "SEARCH_PREFIX", "US_GEO", "add_candidate", "small_company_ids",
           "holds_email", "is_linkedin_url", "is_github_host", "linkedin_profile_url"]

ROLE_KINDS = ("engineer", "manager", "founder", "recruiter", "alum", "other")
SOURCE_KINDS = ("profile", "team", "context", "email", "post")
EMAIL_BASES = ("published", "verified", "pattern")
_EMPLOYER_KINDS = ("profile", "team", "post")
_FIT_TIER = {"team": 1, "context": 2, "founder_small": 3, "other": 4, "recruiter": 5}
_EMPLOYER_RANK = {"ok": 0, "unknown": 1, "stale": 2}
_BASIS_RANK = {"published": 0, "verified": 1, "pattern": 2}
SEARCH_PREFIX = "https://www.linkedin.com/search/results/people/"
US_GEO = "103644278"  # the United States on LinkedIn's people search
_FACETS = f"geoUrn=%5B%22{US_GEO}%22%5D&origin=FACETED_SEARCH"
_SEARCH_HOSTS = ("www.linkedin.com", "linkedin.com")
_SEARCH_MAX = 2000
_LINKEDIN_IN = re.compile(r"https://(?:www\.)?linkedin\.com/in/([^/?#\s]+)/?(?:[?#].*)?\Z", re.I)
_EMAIL_SHAPE = re.compile(r"[^@\s]+@[^@\s.]+\.[^@\s]+\Z")
_CHUNK = 500


@dataclass(frozen=True)
class SourceRef:
    kind: str
    url: str
    label: str
    quote: str
    observed_on: date | None
    email_basis: str | None


@dataclass(frozen=True)
class SourceInput:
    kind: str
    url: str
    label: str = ""
    quote: str = ""
    observed_on: str | None = None
    email_basis: str | None = None


@dataclass(frozen=True)
class Candidate:
    person_id: int
    company_id: int  # the surviving company
    name: str
    title: str
    role_kind: str
    linkedin_url: str | None
    email: str | None
    email_basis: str | None  # the strongest basis among email sources whose quote holds the address
    fit: Literal["team", "context", "founder_small", "other", "recruiter"]
    employer_check: Literal["ok", "stale", "unknown"]
    sources: tuple[SourceRef, ...]
    found_on: SourceRef | None
    post_contact: bool
    draft_id: int | None


@dataclass(frozen=True)
class AddResult:
    person_id: int | None
    created: bool
    refused: str | None


# Loading

def _marks(n: int) -> str:
    return ", ".join("?" for _ in range(n))


def _chunks(ids: Sequence[int]):
    for i in range(0, len(ids), _CHUNK):
        yield ids[i:i + _CHUNK]


def _holds_email(quote_text: str, email: str | None) -> bool:
    """The address stands on the page as a whole address: no letter, digit or one of `._%+-` just before it, and
    none just after it (a full stop that starts another label, as in `.com.au`, counts as more address). So the
    quote `joanna@acme.com` does not back `anna@acme.com`, nor `pat@acme.com` back `pat@acme.co`."""
    if not email:
        return False
    pattern = r"(?<![A-Za-z0-9._%+-])" + re.escape(email) + r"(?![A-Za-z0-9_%+-]|\.[A-Za-z0-9])"
    return re.search(pattern, quote_text or "", re.I) is not None


def _generic_mailbox(email: str | None, cfg: PlanConfig) -> bool:
    """A shared mailbox (info@, hello@, jobs@ and the rest of `people.generic_mailboxes`), `+tag` removed."""
    if not email:
        return False
    local = email.lower().split("@", 1)[0].split("+", 1)[0]
    return local in {m.lower() for m in cfg.get("people", "generic_mailboxes")}


def small_company_ids(conn: sqlite3.Connection, company_ids: Collection[int], canon: dict[int, int],
                      cfg: PlanConfig) -> set[int]:
    """The surviving companies among `company_ids` (and their merged-away duplicates) whose winning `size` or
    `headcount` evidence has `num_high` at or under `people.founder_max_headcount`. Unknown size is never small."""
    small_max = cfg.get("people", "founder_max_headcount")
    rows: dict[int, list[sqlite3.Row]] = {}
    wanted = {canon.get(c, c) for c in company_ids}
    family = sorted(c for c in {*canon, *wanted} if canon.get(c, c) in wanted)
    for part in _chunks(family):
        for r in conn.execute(
                f"SELECT company_id, fact, num_high, locked, recorded_at, confidence, evidence_id FROM evidence "
                f"WHERE fact IN ('size', 'headcount') AND company_id IN ({_marks(len(part))})", part):
            rows.setdefault(canon.get(r["company_id"], r["company_id"]), []).append(r)
    out = set()
    for company, found in rows.items():
        best = max(found, key=size_order_key)
        if best["num_high"] is not None and best["num_high"] <= small_max:
            out.add(company)
    return out


def load_candidates(conn: sqlite3.Connection, company_ids: Collection[int], canon: dict[int, int],
                    contacted: Collection[int], today: date, cfg: PlanConfig) -> dict[int, list[Candidate]]:
    """People at `company_ids` (and at their merged-away duplicates) who are not in `contacted` and have no active
    `no_go`, per surviving company, each list in `rank_people` order. Every requested company has a key (maybe an
    empty list). Recruiters are dropped when the company has any engineer, manager, founder or alum on record,
    contacted or not."""
    wanted = {canon.get(c, c) for c in company_ids}
    family = sorted(c for c in {*canon, *wanted} if canon.get(c, c) in wanted)
    out: dict[int, list[Candidate]] = {c: [] for c in sorted(wanted)}
    if not wanted:
        return out
    fresh_days = cfg.get("people", "employer_fresh_days")
    post_days = cfg.get("email", "hn_post_days")
    tz = cfg.get("plan", "timezone")
    contacted = set(contacted)

    people = []
    for part in _chunks(family):
        people += conn.execute(
            f"SELECT person_id, company_id, name, title, role_kind, email, linkedin_url FROM person "
            f"WHERE company_id IN ({_marks(len(part))}) ORDER BY person_id", part).fetchall()
    if not people:
        return out
    no_go = {r[0] for r in conn.execute(
        "SELECT person_id FROM no_go WHERE person_id IS NOT NULL AND lifted_at IS NULL")}
    person_ids = [p["person_id"] for p in people]

    sources: dict[int, list[SourceRef]] = {}
    for part in _chunks(person_ids):
        for r in conn.execute(
                f"SELECT person_id, kind, url, label, quote, email_basis, observed_on FROM person_source "
                f"WHERE person_id IN ({_marks(len(part))}) ORDER BY source_id", part):
            sources.setdefault(r["person_id"], []).append(SourceRef(
                kind=r["kind"], url=r["url"], label=r["label"], quote=r["quote"],
                observed_on=date.fromisoformat(r["observed_on"]) if r["observed_on"] else None,
                email_basis=r["email_basis"]))
    drafts: dict[int, int] = {}
    for part in _chunks(person_ids):
        drafts.update({r[0]: r[1] for r in conn.execute(
            f"SELECT person_id, draft_id FROM linkedin_draft WHERE person_id IN ({_marks(len(part))})", part)})

    small_ids = small_company_ids(conn, family, canon, cfg)
    asks_posts: dict[int, dict[str, date | None]] = {}
    for part in _chunks(family):
        for r in conn.execute(
                f"SELECT company_id, url, posted_at FROM company_post WHERE asks_email = 1 "
                f"AND company_id IN ({_marks(len(part))})", part):
            posted = ny_date(r["posted_at"], tz) if r["posted_at"] else None
            asks_posts.setdefault(canon.get(r["company_id"], r["company_id"]), {})[r["url"]] = posted

    by_company: dict[int, list[sqlite3.Row]] = {}
    for p in people:
        by_company.setdefault(canon.get(p["company_id"], p["company_id"]), []).append(p)

    for company, rows in by_company.items():
        has_non_recruiter = any(p["role_kind"] in ("engineer", "manager", "founder", "alum") for p in rows)
        company_small = company in small_ids
        cands = []
        for p in rows:
            pid = p["person_id"]
            if pid in contacted or pid in no_go:
                continue
            if p["role_kind"] == "recruiter" and has_non_recruiter:
                continue
            srcs = tuple(sources.get(pid, ()))
            employer_srcs = [s for s in srcs if s.kind in _EMPLOYER_KINDS]
            dated = [s for s in employer_srcs if s.observed_on is not None]
            if any((today - s.observed_on).days <= fresh_days for s in dated):
                employer = "ok"
            elif dated:
                employer = "stale"
            else:
                employer = "unknown"
            found_on = max(employer_srcs, key=lambda s: s.observed_on or date.min) if employer_srcs else None
            role = p["role_kind"]
            if role == "recruiter":
                fit = "recruiter"
            elif role in ("engineer", "manager") and any(s.kind == "team" for s in srcs):
                fit = "team"
            elif role == "alum" or any(s.kind == "context" for s in srcs):
                fit = "context"
            elif role == "founder" and company_small:
                fit = "founder_small"
            else:
                fit = "other"
            bases = [s.email_basis for s in srcs
                     if s.kind == "email" and s.email_basis and _holds_email(s.quote, p["email"])]
            asks = asks_posts.get(company, {})
            post_contact = any(
                s.kind == "post" and s.url in asks and asks[s.url] is not None
                and 0 <= (today - asks[s.url]).days <= post_days for s in srcs)
            cands.append(Candidate(
                person_id=pid, company_id=company, name=p["name"], title=p["title"], role_kind=role,
                linkedin_url=p["linkedin_url"], email=p["email"],
                email_basis=min(bases, key=_BASIS_RANK.get) if bases else None, fit=fit, employer_check=employer,
                sources=srcs, found_on=found_on, post_contact=post_contact, draft_id=drafts.get(pid)))
        out[company] = rank_people(cands)
    return out


def tier(c: Candidate) -> int:
    """1 team engineer or manager, 2 shared context, 3 founder at a very small company, 4 anyone else, 5 recruiter."""
    return _FIT_TIER[c.fit]


def rank_people(cands: Sequence[Candidate]) -> list[Candidate]:
    """Best first: tier, then employer check (ok, unknown, stale), then newest source date, then lowest id."""
    def key(c: Candidate):
        seen = c.found_on.observed_on if c.found_on and c.found_on.observed_on else date.min
        return tier(c), _EMPLOYER_RANK[c.employer_check], -seen.toordinal(), c.person_id
    return sorted(cands, key=key)


# Channel

def _tokens(name: str) -> list[str]:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    return re.findall(r"[a-z0-9]+", ascii_name)


def _shapes(name: str, email: str) -> tuple[frozenset[str], str]:
    """The local-part patterns `email` follows for `name` (first.last, flast and so on), and its domain."""
    local, _, domain = email.lower().partition("@")
    toks = _tokens(name)
    if not toks:
        return frozenset(), domain
    first, last = toks[0], toks[-1]
    options = {"first": first, "last": last}
    if len(toks) > 1:
        options |= {"first.last": f"{first}.{last}", "firstlast": f"{first}{last}", "flast": f"{first[0]}{last}",
                    "f.last": f"{first[0]}.{last}", "firstl": f"{first}{last[0]}", "first_last": f"{first}_{last}",
                    "first-last": f"{first}-{last}", "last.first": f"{last}.{first}"}
    return frozenset(shape for shape, value in options.items() if value == local), domain


def _email_usable(c: Candidate, siblings: Sequence[Candidate], cfg: PlanConfig) -> bool:
    if not c.email:
        return False
    if _generic_mailbox(c.email, cfg) and not c.post_contact:
        return False  # only a post that asks for email makes a shared mailbox the way in
    if c.email_basis in ("published", "verified"):
        return True
    if c.email_basis != "pattern":
        return False
    mine, domain = _shapes(c.name, c.email)
    if not mine:
        return False
    for s in siblings:
        if s.person_id == c.person_id or not s.email or s.email_basis not in ("published", "verified"):
            continue
        theirs, their_domain = _shapes(s.name, s.email)
        if their_domain == domain and mine & theirs:
            return True
    return False


def pick_channel(c: Candidate, siblings: Sequence[Candidate], cfg: PlanConfig) -> tuple[
        Literal["linkedin_request", "email"], str]:
    """The channel and why. Email needs an address backed by a published or verified basis (a pattern address only
    when a colleague's proven address has the same shape) that is not a shared mailbox (info@, hello@) unless an HN
    post asked for email there. Then email is the channel for an HN post that asks for it (outranks the preference
    order), for a founder, and for a company with no LinkedIn-capable non-recruiter: every candidate, this one
    included, is a recruiter. Anyone else with a name can be asked on LinkedIn (the plan builds a people search for
    them), so a lone engineer with an address is still a LinkedIn row. `siblings` are the other candidates at the
    same company."""
    if _email_usable(c, siblings, cfg):
        if c.post_contact:
            return "email", "hn_post_asks_email"
        if c.role_kind == "founder":
            return "email", "founder_published_email"
        if c.role_kind == "recruiter" and all(s.role_kind == "recruiter" for s in siblings):
            return "email", "no_linkedin_person"
    return "linkedin_request", "linkedin"


def linkedin_search_url(url) -> str | None:
    """The URL, stripped, when it is a LinkedIn people search: https, host www.linkedin.com or linkedin.com, no
    credentials, no port, no fragment, no whitespace, path /search/results/people, at most 2000 characters. Else
    None. It is only ever opened in his browser; the app never fetches it."""
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url or len(url) > _SEARCH_MAX or re.search(r"\s", url):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or (parts.hostname or "") not in _SEARCH_HOSTS or parts.username is not None \
            or parts.password is not None or port is not None or parts.fragment or "@" in parts.netloc:
        return None
    return url if parts.path.rstrip("/") == "/search/results/people" else None


def linkedin_company_id(url) -> str | None:
    """The one LinkedIn company id in a people search's `currentCompany=["<id>"]` filter, or None (no filter, more
    than one company, or not a people search)."""
    url = linkedin_search_url(url)
    if url is None:
        return None
    values = parse_qs(urlsplit(url).query).get("currentCompany", [])
    if len(values) != 1:
        return None
    ids = re.fullmatch(r'\[\s*"(\d{1,20})"\s*\]', values[0].strip())
    return ids.group(1) if ids else None


def people_search_url(company_name: str, *, saved_url: str | None = None, linkedin_id: str | None = None) -> str:
    """The LinkedIn people search a queue row opens (design company-first, section 1): his saved search for the
    company as he pasted it; else, with a stored LinkedIn company id, that company's people in the United States;
    else the company's name as keywords in the United States."""
    saved = linkedin_search_url(saved_url)
    if saved is not None:
        return saved
    if isinstance(linkedin_id, str) and re.fullmatch(r"\d{1,20}", linkedin_id):
        return f"{SEARCH_PREFIX}?currentCompany=%5B%22{linkedin_id}%22%5D&{_FACETS}"
    return f"{SEARCH_PREFIX}?keywords={quote(company_name or '', safe='')}&{_FACETS}"


# add_candidate

def _host(url: str) -> str | None:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def _github_host(host: str | None) -> bool:
    """GitHub or its user-content host, any subdomain, any case, with or without one trailing dot (`github.com.` is
    the same host)."""
    host = (host or "").lower()
    host = host[:-1] if host.endswith(".") else host
    return bool(host) and any(host == d or host.endswith("." + d) for d in ("github.com", "githubusercontent.com"))


def _linkedin_url(url: str) -> bool:
    low = (url or "").lower()
    return "linkedin.com" in low or "lnkd.in" in low


def linkedin_profile_url(url: str | None) -> str | None:
    """The normalized profile link (`https://www.linkedin.com/in/<slug>`, slug lower case) of a URL with the profile
    shape, else None."""
    m = _LINKEDIN_IN.match(url.strip()) if isinstance(url, str) else None
    return f"https://www.linkedin.com/in/{m.group(1).lower()}" if m else None


# The rules above, by public name, for the prepare step (Task 13), which checks a reply before `add_candidate` does.
holds_email = _holds_email
is_linkedin_url = _linkedin_url
is_github_host = _github_host


def _refuse(code: str) -> AddResult:
    return AddResult(None, False, code)


def _source_problem(s: SourceInput) -> bool:
    if s.kind not in SOURCE_KINDS or not isinstance(s.url, str) or not s.url.startswith("https://"):
        return True
    try:
        parts = urlsplit(s.url)
    except ValueError:
        return True
    if not parts.hostname or parts.username or parts.password:
        return True
    if (s.kind == "email") != (s.email_basis is not None) or (s.email_basis and s.email_basis not in EMAIL_BASES):
        return True
    if s.observed_on is not None:
        try:
            date.fromisoformat(s.observed_on)
        except (TypeError, ValueError):
            return True
    return False


def _invited(conn: sqlite3.Connection, canon: dict[int, int], survivor: int, backed: Sequence[SourceInput],
             email: str) -> bool:
    """A source page that is a post of this company asking for email, and whose stored text (not only the caller's
    quote) holds the address: the one way a shared mailbox is allowed."""
    urls = {s.url for s in backed}
    return any(r["url"] in urls and canon.get(r["company_id"], r["company_id"]) == survivor
               and _holds_email(r["text"], email)
               for r in conn.execute("SELECT company_id, url, text FROM company_post WHERE asks_email = 1"))


def add_candidate(conn: sqlite3.Connection, *, company_id: int, name: str, title: str = "", role_kind: str,
                  sources: Sequence[SourceInput], linkedin_url: str | None = None, email: str | None = None,
                  added_by: Literal["claude", "devanshu"] = "claude", today: date,
                  cfg: PlanConfig | None = None) -> AddResult:
    """Store a person and their public source pages in one transaction, or refuse and write nothing.

    Refusal codes: `bad_role_kind`, `bad_name`, `company_removed`, `linkedin_source`, `bad_linkedin_url`, `bad_email`,
    `email_source_not_allowed` (GitHub, a noreply address, or a shared mailbox such as info@ unless the source page
    is an HN post that asks for email and its stored text holds the address), `email_needs_source`, `bad_source` (an unknown kind, a page that is not plain https,
    a basis on a non-email source or a missing one on an email source, a bad date), `person_elsewhere`. A person who
    is already at the company (same linkedin url, email or name) is returned with `created` False and only the new
    sources are added.
    """
    sources = list(sources)
    if role_kind not in ROLE_KINDS:
        return _refuse("bad_role_kind")
    name = (name or "").strip()
    if not name:
        return _refuse("bad_name")
    row = conn.execute("SELECT company_id FROM company WHERE company_id = ?", (company_id,)).fetchone()
    if row is None:
        raise LookupError(f"company {company_id} does not exist")
    canon = canonical_ids(conn)
    survivor = canon.get(company_id, company_id)
    if conn.execute("SELECT status FROM company WHERE company_id = ?", (survivor,)).fetchone()[0] == "removed":
        return _refuse("company_removed")
    if any(_linkedin_url(s.url) for s in sources):
        return _refuse("linkedin_source")
    norm_linkedin = None
    if linkedin_url is not None:
        norm_linkedin = linkedin_profile_url(linkedin_url)
        if norm_linkedin is None:
            return _refuse("bad_linkedin_url")
    if email is not None:
        email = email.strip().lower()
        if not _EMAIL_SHAPE.match(email):
            return _refuse("bad_email")
        if "noreply" in email or "no-reply" in email:
            return _refuse("email_source_not_allowed")
    email_sources = [s for s in sources if s.kind == "email"]
    if any(_github_host(_host(s.url)) for s in email_sources):
        return _refuse("email_source_not_allowed")
    if email is not None:
        backed = [s for s in email_sources if s.email_basis in EMAIL_BASES and _holds_email(s.quote, email)]
        if not backed:
            return _refuse("email_needs_source")
        if _generic_mailbox(email, cfg or default_plan_config()) and not _invited(conn, canon, survivor, backed, email):
            return _refuse("email_source_not_allowed")
    if any(_source_problem(s) for s in sources):
        return _refuse("bad_source")

    with atomic(conn, "add_candidate"):
        family = [c for c, end in canonical_ids(conn).items() if end == survivor]
        person_id = None
        for column, value in (("linkedin_url", norm_linkedin), ("email", email)):
            if value is None:
                continue
            found = conn.execute(f"SELECT person_id, company_id FROM person WHERE {column} = ?", (value,)).fetchone()
            if found is None:
                continue
            if found["company_id"] not in family:
                return _refuse("person_elsewhere")
            person_id = person_id or found["person_id"]
        if person_id is None:
            key = name_key(name)
            if key:
                for p in conn.execute(f"SELECT person_id, name FROM person WHERE company_id IN ({_marks(len(family))}) "
                                      f"ORDER BY person_id", family):
                    if name_key(p["name"]) == key:
                        person_id = p["person_id"]
                        break
        now = utcnow()
        created = person_id is None
        if created:
            reason = "alum_first" if role_kind == "alum" else (None if email else "no_email")
            person_id = conn.execute(
                "INSERT INTO person(company_id, name, title, role_kind, email, linkedin_url, channel, "
                "linkedin_reason, added_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (survivor, name, title or "", role_kind, email, norm_linkedin, "email" if email else "linkedin",
                 reason, added_by, now, now)).lastrowid
        for s in sources:
            conn.execute(
                "INSERT INTO person_source(person_id, kind, url, label, quote, email_basis, observed_on, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(person_id, kind, url) DO NOTHING",
                (person_id, s.kind, s.url, s.label, s.quote, s.email_basis, s.observed_on or today.isoformat(), now))
        if created:
            append_event(conn, entity="person", kind="added", actor=added_by, entity_id=person_id,
                         company_id=survivor, detail={"roleKind": role_kind, "sources": [s.url for s in sources]})
    return AddResult(person_id, created, None)
