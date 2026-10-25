"""Validate and store one company's prepare reply (#15, Task 13). The trust boundary of the prepare step.

The reply is model output written over public page text, so both are untrusted. Only the fields of the reply schema
below are read; every other key is ignored, and code, never the reply, sets `observed_on` (today), `added_by`
(`claude`), the email basis (`published`, and only for an address the page quotes), the draft's `saved_by`,
`reason_state` and `prepared_by_version`. Page text that issues instructions is just a quote: nothing here acts on
what a quote says, only on whether it names the person and the company.

Reply schema, version 1 (`SCHEMA_VERSION`), one element of `results` per company:

    {"company_id": int,
     "candidates": [{"name", "title", "role_kind", "linkedin_url": str | null, "email": str | null,
                     "sources": [{"kind", "url", "label", "quote"}],
                     "note": {"body", "reason_line", "facts": [{"text", "label", "url", "in_note"}]} | null}],
     "none_reason": str}

Rules, in order, per candidate (the first `prepare.max_candidates` that pass are kept):
- a name, a known `role_kind`, and not a person already on record at the company (`Target.known_people`);
- no LinkedIn page anywhere in its sources (the whole candidate is refused, whatever else it has);
- every source URL is scanned for LinkedIn before any cap is applied;
- sources that are not a plain https page with a non-empty quote are dropped (an `email` source on GitHub too);
- a `profile`, `team` or `post` page whose quote holds the full name (given name then surname, a middle name or
  initial allowed between them, a hyphenated or compound part whole, accents and case folded, a leading Dr. or a
  trailing Jr. dropped) and names the company
  (its name as whole words, or its domain as a whole host name), or that is on the company's domain or a subdomain of
  it; the pages that do not say so are dropped;
- an email is kept only when an `email` page quotes the whole address, and `add_candidate`'s rules pass (no GitHub,
  no noreply, no shared mailbox unless the page is an HN post of the company that asks for email). Otherwise the
  email and its pages are dropped, `problems` says why, and the person is still stored;
- a `linkedin_url` is kept only with the profile shape and when a quote stored with the person holds the profile
  itself (a LinkedIn host, then `/in/<slug>` as a whole path segment); a guessed link would point at a stranger and
  would make `add_candidate` match or refuse the wrong person;
- a note is saved by `claude` (always `proposed`) unless the stored person is a founder with an address, who is
  emailed. Note problems are checks, never refusals. `DraftLocked` (his draft, or a line he confirmed or wrote) is
  recorded in `problems` and writes nothing over the draft.

One transaction per company: any other error rolls back every person, source and draft of the company and the
attempt event, then is raised. Writes: `person`, `person_source`, `linkedin_draft`, `event`.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

from outreach.plan.config import PlanConfig
from outreach.plan.drafts import DraftLocked, save_linkedin_draft
from outreach.plan.notecheck import Fact
from outreach.plan.people import (ROLE_KINDS, SOURCE_KINDS, SourceInput, add_candidate, holds_email, is_github_host,
                                  is_linkedin_url, linkedin_profile_url)
from outreach.plan.prepare_targets import Target, record_attempt
from outreach.store.atomic import atomic, describe_error
from outreach.store.writes import name_key

__all__ = ["SCHEMA_VERSION", "Rejected", "StoreResult", "store_company_result"]

SCHEMA_VERSION = 1
_EMPLOYER_KINDS = ("profile", "team", "post")
_EMAIL_REFUSALS = ("bad_email", "email_source_not_allowed", "email_needs_source")
_LEGAL = {"inc", "llc", "ltd", "corp", "co", "gmbh"}
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "md", "mba"}
_HONORIFICS = {"dr", "prof", "mr", "mrs", "ms", "mx", "sir"}
_PARTICLES = {"van", "von", "de", "der", "den", "del", "della", "da", "di", "du", "la", "le", "st", "bin", "al"}
_HOSTS = re.compile(r"[a-z0-9][a-z0-9.-]*")
_SEEN_MAX = 10      # candidates looked at per company
_SOURCES_MAX = 8    # sources read per candidate
_FACTS_MAX = 10
_NAME_MAX, _TITLE_MAX, _LABEL_MAX, _QUOTE_MAX, _FACT_MAX = 120, 200, 120, 1000, 300


@dataclass(frozen=True)
class Rejected:
    name: str
    why: str


@dataclass(frozen=True)
class StoreResult:
    company_id: int
    added: int  # candidates stored (a new person, or new sources on one already there): the attempt's `found`
    drafts: int
    rejected: tuple[Rejected, ...]
    problems: tuple[str, ...]


def _tokens(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii").lower()
    return re.findall(r"[a-z0-9]+", plain)


def _text(value, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _public_page(url) -> bool:
    if not isinstance(url, str) or not url.startswith("https://") or re.search(r"\s", url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return bool(parts.hostname) and not parts.username and not parts.password


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _on_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _names_company(source: SourceInput, target: Target) -> bool:
    domain = (target.domain or "").lower()
    if domain:
        if _on_domain(_host(source.url), domain):
            return True
        # the domain as a whole host name in the text: `facme.example`, `my-acme.example` and `acme.example.org`
        # are other hosts; `www.acme.example` and a full stop that ends the sentence are fine
        if any(_on_domain(h.rstrip(".-"), domain) for h in _HOSTS.findall(source.quote.lower())):
            return True
    words = _tokens(target.name)
    while len(words) > 1 and words[-1] in _LEGAL:
        words.pop()
    if len("".join(words)) < 3:
        return False  # too short to find in prose safely; the domain must do it
    quote = _tokens(source.quote)
    return any(quote[i:i + len(words)] == words for i in range(len(quote) - len(words) + 1))


def _name_groups(name: str) -> tuple[list[str], set[str], list[str]] | None:
    """The given name, the middle words and the surname of `name`, each as folded tokens. A hyphenated part stays one
    group (`Smith-Jones` is `smith`, `jones`), and particles before the surname join it (`van Beethoven`). Leading
    honorifics (Dr, Prof, Mr, Mrs, Ms, Mx, Sir) and trailing suffixes (Jr, Sr, III, PhD) are dropped, but never down to
    a single word. None for a single-word name."""
    words = [t for t in (_tokens(w) for w in name.split()) if t]
    while len(words) > 2 and len(words[-1]) == 1 and words[-1][0] in _NAME_SUFFIXES:
        words.pop()
    while len(words) > 2 and len(words[0]) == 1 and words[0][0] in _HONORIFICS:
        words.pop(0)
    if len(words) < 2:
        return None
    last = len(words) - 1
    while last > 1 and len(words[last - 1]) == 1 and words[last - 1][0] in _PARTICLES:
        last -= 1
    surname = [t for w in words[last:] for t in w]
    middle = {t for w in words[1:last] for t in w}
    return words[0], middle, surname


def _group_at(quote: list[str], i: int, parts: list[str]) -> int | None:
    """Where `parts` ends when it starts at `quote[i]`: the same parts in order, or all of them run together
    (`smithjones`). Never one part alone."""
    if quote[i:i + len(parts)] == parts:
        return i + len(parts)
    if i < len(quote) and quote[i] == "".join(parts):
        return i + 1
    return None


def _names_person(source: SourceInput, name: str) -> bool:
    """The full name in the quote: the given name, then the surname, with nothing between them but the name's own
    middle words or single letters (a middle initial). A hyphenated or compound part must appear whole (hyphen, space
    or no separator; case and accents folded): "Mary Jones" does not name Mary Smith-Jones, "Bob Smith" does not name
    Alice Smith, nor "Adam" Ada."""
    quote = _tokens(source.quote)
    groups = _name_groups(name)
    if groups is None:
        words = _tokens(name)
        return bool(words) and any(_group_at(quote, i, words) is not None for i in range(len(quote)))
    first, middle, surname = groups
    room = len(middle) + 1
    for i in range(len(quote)):
        j = _group_at(quote, i, first)
        if j is None:
            continue
        skipped = 0
        while j <= len(quote):
            if _group_at(quote, j, surname) is not None:
                return True
            if j < len(quote) and skipped < room and (quote[j] in middle or len(quote[j]) == 1):
                j += 1
                skipped += 1
                continue
            break
    return False


def _backs_profile(url: str, sources: list[SourceInput]) -> bool:
    """A stored quote holds the profile itself: a LinkedIn host (any subdomain, scheme optional) then `/in/<slug>`
    with the slug a whole path segment (followed by the end, `/`, `?`, `#`, a space or closing punctuation; never a
    letter, digit, `.`, `-`, `_` or `%`). A bare `in/<slug>` or another host (`notes.in/jane`) never counts."""
    slug = url.rsplit("/in/", 1)[1]
    pattern = re.compile(r"(?<![a-z0-9.-])(?:https?://)?(?:[a-z0-9-]+\.)*linkedin\.com/in/" + re.escape(slug)
                         + r"(?![a-z0-9._%-])")
    return any(pattern.search(s.quote.lower()) for s in sources)


def _sources(raw, name: str, problems: list[str]) -> list[SourceInput] | None:
    """The usable sources, observed today by code; None when any of them is a LinkedIn page."""
    if not isinstance(raw, list):
        return []
    if any(isinstance(s, dict) and isinstance(s.get("url"), str) and is_linkedin_url(s["url"]) for s in raw):
        return None
    out: dict[tuple[str, str], SourceInput] = {}
    for s in raw[:_SOURCES_MAX]:
        if not isinstance(s, dict):
            continue
        kind, url, quote = s.get("kind"), s.get("url"), _text(s.get("quote"), _QUOTE_MAX)
        if kind not in SOURCE_KINDS or not _public_page(url) or not quote:
            problems.append(f"{name}: source {url!s:.200} dropped: not a public https page with a quote")
            continue
        if kind == "email" and is_github_host(urlsplit(url).hostname):
            problems.append(f"{name}: source {url:.200} dropped: an address from GitHub is never used")
            continue
        out.setdefault((kind, url), SourceInput(kind=kind, url=url, label=_text(s.get("label"), _LABEL_MAX),
                                                quote=quote, email_basis="published" if kind == "email" else None))
    return list(out.values())


def _facts(raw) -> list[Fact]:
    out = []
    for f in (raw if isinstance(raw, list) else [])[:_FACTS_MAX]:
        if isinstance(f, dict) and _text(f.get("text"), _FACT_MAX):
            out.append(Fact(text=_text(f["text"], _FACT_MAX), label=_text(f.get("label"), _LABEL_MAX),
                            url=f["url"] if isinstance(f.get("url"), str) else "", in_note=f.get("in_note") is True))
    return out


class _Company:
    """One company's reply, stored inside the caller's transaction."""

    def __init__(self, conn: sqlite3.Connection, target: Target, *, version: str, today: date, cfg: PlanConfig,
                 banned, unconfirmed):
        self.conn, self.target, self.version, self.today, self.cfg = conn, target, version, today, cfg
        self.banned, self.unconfirmed = banned, unconfirmed
        self.known = {name_key(n) for n in target.known_people}
        self.added = self.drafts = 0
        self.rejected: list[Rejected] = []
        self.problems: list[str] = []

    def candidate(self, c) -> None:
        if not isinstance(c, dict):
            self.rejected.append(Rejected("", "not_an_object"))
            return
        name = _text(c.get("name"), _NAME_MAX + 1)
        if not name:
            self.rejected.append(Rejected("", "no_name"))
            return
        if len(name) > _NAME_MAX:
            self.rejected.append(Rejected(name[:_NAME_MAX], "bad_name"))
            return
        why = self._refusal(c, name)
        if why is not None:
            self.rejected.append(Rejected(name, why))
            return
        sources = _sources(c.get("sources"), name, self.problems)
        if sources is None:
            self.rejected.append(Rejected(name, "linkedin_source"))
            return
        employer = [s for s in sources if s.kind in _EMPLOYER_KINDS]
        proven = [s for s in employer if _names_person(s, name) and _names_company(s, self.target)]
        if not proven:
            if not employer:
                why = "no_public_page"
            elif any(_names_person(s, name) for s in employer):
                why = "company_not_on_page"
            else:
                why = "name_not_on_page"
            self.rejected.append(Rejected(name, why))
            return
        for s in employer:
            if s not in proven:
                self.problems.append(f"{name}: source {s.url:.200} dropped: it does not name them at the company")
        context = [s for s in sources if s.kind == "context" and _names_person(s, name)]
        kept = proven + context
        email, email_sources = self._email(c.get("email"), name, [s for s in sources if s.kind == "email"])
        linkedin = None
        if c.get("linkedin_url") is not None:
            linkedin = linkedin_profile_url(c.get("linkedin_url"))
            if linkedin is None:
                self.problems.append(f"{name}: linkedin_url {c.get('linkedin_url')!s:.200} dropped: not a profile link")
        add = dict(company_id=self.target.company_id, name=name, title=_text(c.get("title"), _TITLE_MAX),
                   role_kind=c["role_kind"], added_by="claude", today=self.today, cfg=self.cfg)

        def store(stored: list[SourceInput], address: str | None):
            # the profile link only when a quote that is actually stored with this call holds it
            backed = linkedin if linkedin is not None and _backs_profile(linkedin, stored) else None
            return add_candidate(self.conn, sources=stored, email=address, linkedin_url=backed, **add), backed

        result, backed = store(kept + email_sources, email)
        if result.refused in _EMAIL_REFUSALS and email is not None:
            self.problems.append(f"{name}: email {email} dropped ({result.refused})")
            result, backed = store(kept, None)
        if linkedin is not None and backed is None and result.refused is None:
            self.problems.append(f"{name}: linkedin_url {linkedin:.200} dropped: no kept quote holds it")
        if result.refused is not None:
            self.rejected.append(Rejected(name, result.refused))
            return
        self.added += 1
        self._note(c.get("note"), name, result.person_id)

    def _refusal(self, c: dict, name: str) -> str | None:
        if c.get("role_kind") not in ROLE_KINDS:
            return "bad_role_kind"
        if name_key(name) in self.known:
            return "already_known"
        if self.added >= self.cfg.get("prepare", "max_candidates"):
            return "max_candidates"
        return None

    def _email(self, raw, name: str, sources: list[SourceInput]) -> tuple[str | None, list[SourceInput]]:
        if raw is None:
            return None, []
        email = raw.strip().lower() if isinstance(raw, str) else ""
        if not email:
            self.problems.append(f"{name}: email dropped: not an address")
            return None, []
        backing = [s for s in sources if holds_email(s.quote, email)]
        if not backing:
            self.problems.append(f"{name}: email {email:.200} dropped: no email source page quotes it")
            return None, []
        return email, backing

    def _note(self, note, name: str, person_id: int) -> None:
        if note is None:
            return
        person = self.conn.execute("SELECT role_kind, email FROM person WHERE person_id = ?", (person_id,)).fetchone()
        if person["role_kind"] == "founder" and person["email"]:
            return  # a founder with an address is emailed: the cold-email skill writes that, not a note
        body = note.get("body") if isinstance(note, dict) else None
        if not isinstance(body, str) or not body.strip():
            self.problems.append(f"{name}: note not saved: it has no body")
            return
        if len(body) > 10 * self.cfg.get("note", "max_chars"):
            self.problems.append(f"{name}: note not saved: {len(body)} characters is far over the limit")
            return
        reason = note.get("reason_line")
        role = self.target.role
        try:
            save_linkedin_draft(self.conn, person_id=person_id, body=body,
                                reason_line=reason if isinstance(reason, str) else "", facts=_facts(note.get("facts")),
                                saved_by="claude", posting_id=role["posting_id"] if role else None,
                                prepared_by_version=self.version, cfg=self.cfg, banned=self.banned,
                                unconfirmed=self.unconfirmed)
        except DraftLocked as err:
            self.problems.append(f"{name}: note not saved: {describe_error(err)}")
            return
        self.drafts += 1


def store_company_result(conn: sqlite3.Connection, target: Target, result: dict, *, run_id: int, version: str,
                         today: date, cfg: PlanConfig, banned, unconfirmed) -> StoreResult:
    """Store one company's reply in one transaction (see the module text for every rule), then record the attempt
    with `found` = the candidates stored. A reply for another company is ignored: nothing is written, not even an
    attempt. `ValueError` (nothing written) when `candidates` is not a list: the reply is broken, not empty."""
    cid = result.get("company_id") if isinstance(result, dict) else None
    if isinstance(cid, bool) or not isinstance(cid, int) or cid != target.company_id:
        return StoreResult(target.company_id, 0, 0, (), (f"reply for company {cid!s:.40} ignored: the target is "
                                                         f"{target.company_id}",))
    candidates = result.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("the reply's candidates is not a list")
    work = _Company(conn, target, version=version, today=today, cfg=cfg, banned=banned, unconfirmed=unconfirmed)
    with atomic(conn, "prepare_company"):
        for c in candidates[:_SEEN_MAX]:
            work.candidate(c)
        record_attempt(conn, target.company_id, found=work.added, run_id=run_id, version=version)
    return StoreResult(target.company_id, work.added, work.drafts, tuple(work.rejected), tuple(work.problems))
