"""Linking: which records are one company, and which mentions are one person.

When unsure, never merge. Two worst outcomes guide every rule here: one real person or company split in two (a second
cold email to someone already reached), and two different people or companies made one (a stop or a hold on the wrong
one, or a company hidden behind another). So the rules join only on an exact key; anything close is kept apart and
raised as an issue for his overrides (`same_company`, `not_same_company`, `person_name`), and his overrides win.

Companies (`CompanyIndex`). The plan's company key is `company_key(name)`: the store's `name_key` (the key #31's
identity and `company_name_key` use) of the name with accents folded, with one guard: a name whose letters do not
all fold to ASCII keeps its own key, so two names in another script never collapse into one. The fold is the one
difference from the store: here "Café Labs" and "Cafe Labs" are one key (`cafelabs`), while the store's `name_key`
drops the accented letter (`caflabs`). So the writer registers every spelling in `CompanyIn.names` as an alias of
the company, and a later "Cafe Labs" from any source resolves to it instead of becoming a second company. data.js
companies are the base; every other record links by that exact key. Near names ("Tidewater" and "Tidewater AI") stay two companies and come back from
`near_names()` for one merge item each; the writer resolves each company through `identity.resolve_candidate`, which
is where store-side matching (domain, FEIN, name and state) happens.

People (`PersonIndex`), matched the way MCP `add_people` matches (`outreach.sources.keys`): by address anywhere,
else by LinkedIn profile anywhere (one normal form), else by name at the company. The name joins wait for
`finalize()`: same-name people whose anchors do not disagree join in pairs (an address and a profile, with a
`same_person` issue naming both), then a mention with no address and no profile (a `contacted.txt` line) folds
into the one person at the company with the same name, or whose first name it is; with two such people it stays
its own person and raises an issue. Deciding at the end makes the result the same whatever order the sources come
in. A same-name person with a different address
is someone else (MCP's `same_name_as`), and a stop on one reaches the other (`carry_stops`, MCP's same-name refusal).
"""
from __future__ import annotations

import bisect
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date

from outreach.importer.manifest import Overrides
from outreach.importer.model import ApplicationIn, IssueIn, OutcomeIn, PersonIn, RecordOutcome, TouchIn
from outreach.importer.records import SOURCES, SourceRecord
from outreach.importer.vocab import (STOP_KINDS, curated_channel, curated_outcome, followups_count, noon_ny, ny_date,
                                    req_named, role_kind)
from outreach.sources.keys import email_key, linkedin_key, person_name_key
from outreach.store import name_key

__all__ = ["company_key", "CompanyIndex", "NEAR_WORDS", "MIN_PREFIX", "ROLE_WORDS", "SOLO_ROLE_WORDS",
           "parse_person_name",
           "PersonId", "PersonIndex", "placeholder_key", "is_placeholder", "carry_stops", "touch_key", "merge_outcome",
           "join_curated", "origin_touch", "interview_touch", "interview_date"]

NEAR_WORDS = ("ai", "labs", "inc", "technologies", "systems", "hq", "app")
MIN_PREFIX = 5  # "Arc" and "Arcadia" are not near names; "Tidal" and "Tidalwave Bio" are
_RECORD_ID = re.compile(rf"^(?:{'|'.join(SOURCES)}):")
_KEY_TEXT = re.compile(r"^(?:[a-z0-9]+(?:~\d+)?|u:.+)$")
_PAREN = re.compile(r"\s*\([^)]*\)")
_WORD = re.compile(r"[^\W_]+")


def _empty(overrides: Overrides | None) -> Overrides:
    return overrides or Overrides({}, frozenset(), {}, frozenset(), frozenset(), {})


# ---- companies ---------------------------------------------------------------------------------------------------

def company_key(name: str | None) -> str:
    """The plan's key for a company name: `name_key` of the name with accents folded ("Café Labs" is "cafelabs",
    as "Cafe Labs" is). A name with a letter that does not fold to ASCII keeps its letters (`u:` plus the name in
    lower case), because `name_key` would drop them and make unrelated names one. ValueError for a name with no
    key at all."""
    text = name or ""
    folded = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    if any(c.isalpha() and not c.isascii() for c in folded):
        return "u:" + " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    key = name_key(folded)
    if not key:
        raise ValueError(f"company name {name!r} has no key")
    return key


def _key_text(text: str) -> str:
    """An override value as a key: a key as written stays (`tidewaterai`, `relay~2`), a name is keyed."""
    return text if _KEY_TEXT.match(text) else company_key(text)


def _stem(key: str) -> str:
    return key.split("~", 1)[0]


class CompanyIndex:
    """Which records are one company. `add` returns the canonical key."""

    def __init__(self, overrides: Overrides | None = None):
        self.overrides = _empty(overrides)
        self._by_record: dict[str, str] = {}  # his same_company ruling for one record
        self._by_key: dict[str, str] = {}
        for src, dst in self.overrides.same_company.items():
            if _RECORD_ID.match(src):
                self._by_record[src] = _key_text(dst)
            else:
                self._by_key[_key_text(src)] = _key_text(dst)
        self._not_same = {frozenset(self.canonical(_key_text(x)) for x in pair)
                          for pair in self.overrides.not_same_company}
        self._base_key: dict[str, str] = {}  # data.js record id to its raw key
        self._base_owner: dict[str, list[str]] = {}  # raw key to the data.js records holding it, in order
        self._mentions: dict[str, list[str]] = {}  # raw key to the other records naming it
        self._names: dict[str, list[str]] = {}
        self._base_name: dict[str, str] = {}
        self._records: dict[str, list[str]] = {}

    def canonical(self, key: str) -> str:
        seen = []
        while key in self._by_key:
            seen.append(key)
            key = self._by_key[key]
            if key in seen:
                raise ValueError(f"same_company overrides loop: {' to '.join(seen + [key])}")
        return key

    def add(self, name: str, record: str, *, base: bool = False) -> str:
        raw = company_key(name)
        if record in self._by_record:
            key = self.canonical(self._by_record[record])
        elif base:
            if record not in self._base_key:
                owners = self._base_owner.setdefault(raw, [])
                owners.append(record)
                self._base_key[record] = raw if len(owners) == 1 else f"{raw}~{len(owners)}"
            key = self.canonical(self._base_key[record])
        else:
            self._mentions.setdefault(raw, []).append(record)
            key = self.canonical(raw)
        names = self._names.setdefault(key, [])
        if name not in names:
            names.append(name)
        if base:
            self._base_name.setdefault(key, name)
        records = self._records.setdefault(key, [])
        if record not in records:
            records.append(record)
        return key

    def lookup(self, name: str) -> str | None:
        """The canonical key a name links to, when a record already named it; else None. Adds nothing."""
        key = self.canonical(company_key(name))
        return key if key in self._records else None

    def keys(self) -> list[str]:
        return sorted(self._records)

    def name(self, key: str) -> str:
        """The old dashboard's spelling when it has the company, else the first one seen."""
        return self._base_name.get(key) or self._names[key][0]

    def names(self, key: str) -> frozenset[str]:
        return frozenset(self._names[key])

    def records(self, key: str) -> tuple[str, ...]:
        return tuple(self._records[key])

    def issues(self) -> list[IssueIn]:
        """Two data.js companies with one name key are kept apart (`base_same_key`, one per later one), and a record
        from another source naming that key is linked to the first and raised (`company_ambiguous`)."""
        out = []
        for raw, owners in sorted(self._base_owner.items()):
            keys = [self.canonical(self._base_key[r]) for r in owners]
            if len(set(keys)) < 2:
                continue
            first = keys[0]
            for record, key in zip(owners[1:], keys[1:]):
                if key != first:
                    out.append(IssueIn("base_same_key", key, record,
                                       f"The old dashboard has two companies named {self.name(key)!r}; they stay "
                                       f"two ({first} and {key}). If they are one, rule [same_company] "
                                       f"{key!r} = {first!r}.", False))
            for record in self._mentions.get(raw, []):
                if record not in self._by_record:
                    out.append(IssueIn("company_ambiguous", first, record,
                                       f"{record} names {self.name(first)!r}, which could be any of "
                                       f"{', '.join(sorted(set(keys)))}; it was linked to {first}. Rule "
                                       f"[same_company] for this record if it is another one.", False))
        return out

    def near_names(self) -> list[tuple[str, str]]:
        """Pairs of keys that may be one company: one is the other plus or minus a trailing word from NEAR_WORDS or
        a parenthetical, one key starts with the other and the shorter has at least MIN_PREFIX characters, or one
        name's words are the first words of the other's at any length ("Acme" and "Acme Robotics"; never "Arc" and
        "Arcadia"). Only an item is raised.
        Never a pair he ruled `not_same_company`, and never two data.js twins (`base_same_key` covers those)."""
        keys = self.keys()
        known = set(keys)
        pairs: set[tuple[str, str]] = set()

        def offer(a: str, b: str) -> None:
            if a == b or _stem(a) == _stem(b) or frozenset((a, b)) in self._not_same:
                return
            pairs.add((min(a, b), max(a, b)))

        for key in keys:
            for name in self._names[key]:
                bare = _PAREN.sub("", name).strip()
                variants = [bare] if bare != name.strip() else []
                words = _WORD.findall(bare.lower())
                if len(words) >= 2 and words[-1] in NEAR_WORDS:
                    variants.append(" ".join(words[:-1]))
                for variant in variants:
                    try:
                        other = self.canonical(company_key(variant))
                    except ValueError:
                        continue
                    if other in known:
                        offer(key, other)
        for key in keys:
            if len(key) < MIN_PREFIX:
                continue
            i = bisect.bisect_right(keys, key)
            while i < len(keys) and keys[i].startswith(key):
                offer(key, keys[i])
                i += 1
        # one name's words are the first words of another's ("Acme", "Acme Robotics"), at any length
        spelled = sorted({(tuple(_WORD.findall(_PAREN.sub("", name).lower())), key)
                          for key in keys for name in self._names[key]})
        for words, key in spelled:
            if not words:
                continue
            i = bisect.bisect_right(spelled, (words, chr(0x10FFFF)))
            while i < len(spelled) and spelled[i][0][:len(words)] == words:
                offer(key, spelled[i][1])
                i += 1
        return sorted(pairs)


# ---- people ------------------------------------------------------------------------------------------------------

ROLE_WORDS = frozenset({
    "founder", "founders", "cofounder", "cofounders", "co-founder", "co-founders", "cto", "ceo", "coo", "cfo", "vp",
    "hr", "recruiter", "recruiters", "recruiting", "talent", "team", "hiring", "manager", "engineer",
    "engineers", "someone", "unknown",
})


# Role abbreviations that are also given names ("Em Watson"): a role only when they are the whole text.
SOLO_ROLE_WORDS = frozenset({"em", "pm"})


def parse_person_name(text: str | None) -> str | None:
    """The name when the text is 1 to 4 words, each starting with a letter, and no word is a role word
    ("founders", "the CTO", "hiring team") and the text is not a lone "EM" or "PM"; else None, and the planner
    makes a placeholder person."""
    words = (text or "").split()
    if not 1 <= len(words) <= 4:
        return None
    if len(words) == 1 and words[0].lower().strip(".,;:") in SOLO_ROLE_WORDS:
        return None
    if any(not w[0].isalpha() or w.lower().strip(".,;:") in ROLE_WORDS for w in words):
        return None
    return " ".join(words)


def placeholder_key(company_key_: str, record: str) -> str:
    return f"{company_key_}/placeholder:{record}"


def is_placeholder(person_key: str | None) -> bool:
    return bool(person_key) and "/placeholder:" in person_key


_BLANKS = frozenset({"", "-", "?", "n/a", "na", "none", "null", "unknown", "tbd"})
_URL = re.compile(r"^https?://\S+$", re.I)


def _lenient_linkedin(text: str) -> str | None:
    """The profile key for a link written loosely, as add_people would store it: no scheme or http, a one to three
    letter subdomain (`m.linkedin.com`, `uk.linkedin.com`), and a `/details/...` page of the profile."""
    url = text.strip()
    if re.match(r"^http://", url, re.I):
        url = "https://" + url[7:]
    elif not re.match(r"^https://", url, re.I):
        url = "https://" + url
    url = re.sub(r"^https://[a-z]{1,3}\.linkedin\.com/", "https://www.linkedin.com/", url, flags=re.I)
    url = re.sub(r"^(https://(?:www\.)?linkedin\.com/in/[^/?#\s]+)/details/\S*$", r"\1", url, flags=re.I)
    return linkedin_key(url)


def _name_words(name: str) -> list[str]:
    """A name's words, lower case with accents folded ("José García" gives jose, garcia)."""
    folded = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    return re.findall(r"[^\W\d_]+", folded.lower())


def _may_be(a: str, b: str) -> bool:
    """Whether two names could be one person's: two full names share the surname ("Dana R. Whitfield" and "Dana
    Whitfield"); a one-word name is the other's first or last word ("Jose" and "José García")."""
    x, y = _name_words(a), _name_words(b)
    if not x or not y:
        return False
    if len(x) > 1 and len(y) > 1:
        return x[-1] == y[-1]
    return bool({x[0], x[-1]} & {y[0], y[-1]})


@dataclass(frozen=True)
class PersonId:
    """Who a person is, as linked: the planner builds its `PersonIn` from this and the records' details."""
    key: str
    company_key: str
    name: str
    email: str | None
    linkedin: str | None
    placeholder: bool
    records: tuple[str, ...]


@dataclass
class _Person:
    key: str
    company_key: str
    name: str
    email: str | None = None
    linkedin: str | None = None
    placeholder: bool = False
    records: list[str] = field(default_factory=list)

    @property
    def anchored(self) -> bool:
        return bool(self.email or self.linkedin)

    @property
    def name_key(self) -> str:
        return person_name_key(self.name)

    def compatible(self, email: str | None, linkedin: str | None) -> bool:
        return not (email and self.email and email != self.email) and \
            not (linkedin and self.linkedin and linkedin != self.linkedin)


class PersonIndex:
    """Which mentions are one person. `add` returns a key; after the last add, `finalize()` folds name-only
    mentions into the person they name, and `resolve(key)` gives the person any key ended in."""

    def __init__(self, overrides: Overrides | None = None):
        self.overrides = _empty(overrides)
        self.issues: list[IssueIn] = []
        self._people: dict[str, _Person] = {}
        self._by_email: dict[str, str] = {}
        self._by_linkedin: dict[str, str] = {}
        self._folded: dict[str, str] = {}
        self._other_company: set[tuple[str, str]] = set()
        self._ambiguous_keys: set[str] = set()
        self._candidates: dict[str, list[str]] = {}  # an ambiguous loose person to the people it could be
        self._finalized = False

    # -- adding --

    def _issue(self, code: str, company: str | None, record: str, detail: str) -> None:
        self.issues.append(IssueIn(code, company, record, detail, False))

    def _clean_email(self, text, company: str, record: str) -> str | None:
        if text is None or str(text).strip().lower() in _BLANKS:
            return None
        email = email_key(str(text))
        if email is None:
            self._issue("email_unparsed", company, record, f"{record} gives {str(text).strip()!r} as an address; "
                                                           f"it is not one, so the person has no address from it.")
        return email

    def _clean_linkedin(self, text, company: str, record: str) -> str | None:
        if text is None or str(text).strip().lower() in _BLANKS:
            return None
        found = _lenient_linkedin(str(text))
        if found is None and _URL.match(str(text).strip()):
            found = str(text).strip()  # a link that is not a profile link is kept as written, matched exactly
            self._issue("linkedin_unparsed", company, record, f"{record} gives {found!r} as a LinkedIn link; it is "
                                                              f"not a profile link, so it is kept as written and "
                                                              f"matches only that exact link.")
        elif found is None:
            self._issue("linkedin_unparsed", company, record, f"{record} gives {str(text).strip()!r} as a LinkedIn "
                                                              f"link; it is not one, so it was left out.")
        return found

    def _new(self, key: str, company: str, name: str, email, linkedin, record: str, *, placeholder=False) -> str:
        person = self._people.get(key)
        if person is None:
            person = self._people[key] = _Person(key, company, name, email, linkedin, placeholder)
            if email:
                self._by_email[email] = key
            if linkedin:
                self._by_linkedin[linkedin] = key
        if record not in person.records:
            person.records.append(record)
        return key

    def _join(self, person: _Person, company: str, email, linkedin, record: str) -> str:
        if person.company_key != company and (person.key, company) not in self._other_company:
            self._other_company.add((person.key, company))
            self._issue("person_other_company", company, record,
                        f"{person.name} ({person.email or person.linkedin}) is known at {person.company_key} and "
                        f"{record} has them at {company}: one person, kept at {person.company_key}. The two "
                        f"companies may be one.")
        for attr, value, index in (("email", email, self._by_email), ("linkedin", linkedin, self._by_linkedin)):
            if not value:
                continue
            current = getattr(person, attr)
            if current is None and value not in index:
                setattr(person, attr, value)
                index[value] = person.key
            elif current is not None and current != value:
                self._issue("person_detail_conflict", company, record,
                            f"{record} gives {person.name} the {attr} {value!r}; they already have {current!r}, "
                            f"which is kept.")
        if record not in person.records:
            person.records.append(record)
        return person.key

    def add(self, company_key_: str, name: str, *, email=None, linkedin=None, record: str) -> str:
        """A mention of a person at a company. Joins, in order: the person with this address (at any company), the
        person with this LinkedIn profile (at any company), the one person at the company with this name and no
        different address or profile. A mention with neither address nor profile is kept until `finalize()`."""
        if self._finalized:
            raise RuntimeError("PersonIndex.add after finalize")
        name = " ".join((self.overrides.person_name.get(record) or name or "").split())
        if not name:
            raise ValueError(f"{record}: a person needs a name (make a placeholder instead)")
        email = self._clean_email(email, company_key_, record)
        linkedin = self._clean_linkedin(linkedin, company_key_, record)
        by_email = self._people[self._by_email[email]] if email in self._by_email else None
        by_page = self._people[self._by_linkedin[linkedin]] if linkedin in self._by_linkedin else None
        if by_email and by_page and by_email is not by_page:
            self._issue("two_people", company_key_, record,
                        f"{record}: the address {email} is {by_email.name}'s and the LinkedIn profile is "
                        f"{by_page.name}'s. Linked to {by_email.name} by the address; the profile was left out.")
            return self._join(by_email, company_key_, email, None, record)
        if by_email or by_page:
            return self._join(by_email or by_page, company_key_, email, linkedin, record)
        if not email and not linkedin:
            return self._new(f"{company_key_}/{person_name_key(name)}", company_key_, name, None, None, record)
        # A same-name person with an address or profile is joined (or not) in finalize(), on the whole set, so the
        # order the sources come in never changes who is one person.
        return self._new(f"{company_key_}/{email or linkedin}", company_key_, name, email, linkedin, record)

    def placeholder(self, company_key_: str, record: str, role_kind: str = "other") -> str:
        """Someone the record never named: their own person, never joined to anyone."""
        if self._finalized:
            raise RuntimeError("PersonIndex.placeholder after finalize")
        return self._new(placeholder_key(company_key_, record), company_key_, f"Unnamed {role_kind} (imported)",
                         None, None, record, placeholder=True)

    # -- folding name-only mentions --

    def _fold(self, loose: _Person, into: _Person) -> None:
        self._folded[loose.key] = into.key
        for record in loose.records:
            if record not in into.records:
                into.records.append(record)

    def finalize(self) -> dict[str, str]:
        """Fold each name-only person (no address, no profile) into the one person at the company with the same
        name and an address or profile; then a one-word name-only person into the one person there whose first
        name it is (issue `same_person`). With two or more candidates it stays its own person (issue
        `person_ambiguous`). Returns the folds (key to key). Calling it again changes nothing."""
        if self._finalized:
            return dict(self._folded)
        self._finalized = True
        self._join_namesakes()
        loose = [p for p in self._people.values() if not p.anchored and not p.placeholder]
        live = [p for p in self._people.values() if not p.placeholder and p.key not in self._folded]
        for person in loose:  # the same full name
            same = [p for p in live if p.anchored and p.company_key == person.company_key
                    and p.name_key == person.name_key]
            if len(same) == 1:
                self._fold(person, same[0])
            elif len(same) > 1:
                self._ambiguous(person, same)
        for person in loose:  # a first name
            if person.key in self._folded or person.key in self._ambiguous_keys:
                continue
            words = person.name_key.split(" ")
            if len(words) != 1:
                continue
            targets = {self.resolve(p.key) for p in live
                       if p is not person and p.company_key == person.company_key
                       and len(p.name_key.split(" ")) > 1 and p.name_key.split(" ")[0] == words[0]}
            if len(targets) == 1:
                into = self._people[targets.pop()]
                self._fold(person, into)
                self._issue("same_person", person.company_key, person.records[0],
                            f"{person.name!r} ({', '.join(person.records)}) is taken as {into.name}, the only "
                            f"{person.name} at {person.company_key}. If not, give the full name in [person_name].")
            elif len(targets) > 1:
                self._ambiguous(person, [self._people[k] for k in sorted(targets)])
        for person in loose:  # folded into no one: name the people it might have been, never merge
            if person.key in self._folded or person.key in self._ambiguous_keys:
                continue
            near = [p for p in live if p is not person and p.company_key == person.company_key
                    and p.key not in self._folded and p.key not in self._ambiguous_keys
                    and _may_be(person.name, p.name)]
            if near:
                self._issue("person_unmatched", person.company_key, person.records[0],
                            f"{person.name!r} ({', '.join(person.records)}) matched no one at {person.company_key}, "
                            f"so it is its own person; it shares a name with "
                            f"{', '.join(f'{p.name} ({p.email or p.linkedin or 'no address'})' for p in near)}. If it "
                            f"is one of them, give the full name in [person_name] for the record.")
        return dict(self._folded)

    def _join_namesakes(self) -> None:
        """Same-name people at one company, each with an address or a profile: two join when neither has an anchor
        that disagrees with the other and each is the only such fit of the other (an address and a profile, say),
        with a `same_person` issue naming both anchors, because a split is the worse mistake. A profile that fits
        two people with different addresses joins neither. Who stays apart raises `same_name`. Decided on the
        whole set, so the order of the sources does not matter."""
        groups: dict[tuple[str, str], list[_Person]] = {}
        for p in sorted(self._people.values(), key=lambda p: p.key):
            if p.anchored and not p.placeholder:
                groups.setdefault((p.company_key, p.name_key), []).append(p)
        for (company, _), members in groups.items():
            if len(members) < 2:
                continue
            fits = {p.key: [q for q in members if q is not p and p.compatible(q.email, q.linkedin)] for p in members}
            pairs = sorted({tuple(sorted((p.key, fits[p.key][0].key))) for p in members
                            if len(fits[p.key]) == 1 and [x.key for x in fits[fits[p.key][0].key]] == [p.key]})
            for a, b in pairs:
                into, other = self._people[a], self._people[b]
                if other.email and not into.email:
                    into, other = other, into
                was = f"{into.name} ({into.email or into.linkedin}, {', '.join(into.records)})"
                self._fold(other, into)
                for attr, index in (("email", self._by_email), ("linkedin", self._by_linkedin)):
                    value = getattr(other, attr)
                    if value and not getattr(into, attr):
                        setattr(into, attr, value)
                        index[value] = into.key
                self._issue("same_person", company, other.records[0],
                            f"{other.name} ({other.email or other.linkedin}, {', '.join(other.records)}) is taken as "
                            f"{was}: the same name at {company} and no address or profile that disagrees. If they "
                            f"are two people, give one record another name in [person_name].")
            apart = [p for p in members if p.key not in self._folded]
            for p in apart[1:]:
                self._issue("same_name", company, p.records[0],
                            f"{p.name} ({p.email or p.linkedin}) has the same name as "
                            f"{', '.join(f'{q.name} ({q.email or q.linkedin})' for q in apart if q is not p)} at "
                            f"{company} and another address or profile, so they are kept as separate people.")

    def _ambiguous(self, person: _Person, candidates: list[_Person]) -> None:
        self._ambiguous_keys.add(person.key)
        self._candidates[person.key] = [p.key for p in candidates]
        self._issue("person_ambiguous", person.company_key, person.records[0],
                    f"{person.name!r} ({', '.join(person.records)}) could be any of "
                    f"{', '.join(p.name for p in candidates)} at {person.company_key}, so it is kept as its own "
                    f"person. Give the full name in [person_name] for the record.")

    # -- reading --

    def resolve(self, key: str) -> str:
        while key in self._folded:
            key = self._folded[key]
        return key

    def person(self, key: str) -> PersonId:
        p = self._people[self.resolve(key)]
        return PersonId(p.key, p.company_key, p.name, p.email, p.linkedin, p.placeholder, tuple(p.records))

    def people(self) -> list[PersonId]:
        return [self.person(k) for k in sorted(self._people) if k not in self._folded]

    def candidates(self, key: str) -> list[str]:
        """The people an ambiguous name-only person could be (empty when it is not ambiguous)."""
        return sorted({self.resolve(k) for k in self._candidates.get(self.resolve(key), [])})

    def namesakes(self, key: str) -> list[str]:
        """The other people at the same company with the same name (kept apart by another address or profile)."""
        me = self._people[self.resolve(key)]
        return sorted(p.key for p in self._people.values()
                      if p.key != me.key and p.key not in self._folded and not p.placeholder
                      and p.company_key == me.company_key and p.name_key == me.name_key)


def carry_stops(people: PersonIndex, touches: Iterable[TouchIn]
                ) -> tuple[dict[str, tuple[str, str]], list[IssueIn]]:
    """The people who must never be emailed again, as (no_go reason, record id) by resolved person key: everyone
    whose reply said no or asked to stop in any source, each same-name person at their company (add_people
    refuses a same-name person who said no, so a second address never brings them back), and, for a name-only
    person who could be several people ("Dana" with two Danas there), every one of them, since the stop must reach
    the real one. Each namesake raises `same_name_stopped`, each candidate `ambiguous_stopped`."""
    stops: dict[str, tuple[str, str]] = {}
    for touch in touches:
        if not touch.person_key:
            continue
        for outcome in touch.outcomes:
            reason = STOP_KINDS.get(outcome.reply_kind or "") if outcome.kind == "replied" else None
            if reason is None:
                continue
            key = people.resolve(touch.person_key)
            if key not in stops or (stops[key][0] == "said_no" and reason == "stop_reply"):
                stops[key] = (reason, outcome.record)
    issues = []
    for key, (reason, record) in sorted(stops.copy().items()):
        for other in people.namesakes(key):
            if other in stops:
                continue
            stops[other] = (reason, record)
            them, me = people.person(other), people.person(key)
            issues.append(IssueIn("same_name_stopped", them.company_key, record,
                                  f"{me.name} ({me.email or me.linkedin}) said no or asked to stop ({record}); "
                                  f"{them.name} ({them.email or them.linkedin}) has the same name at "
                                  f"{them.company_key}, so they are stopped too. If they are someone else, give "
                                  f"the full name in [person_name] for the record, or lift the no-go in the app.",
                                  False))
    for key, (reason, record) in sorted(stops.copy().items()):
        me = people.person(key)
        for other in people.candidates(key):
            if other in stops:
                continue
            stops[other] = (reason, record)
            them = people.person(other)
            issues.append(IssueIn("ambiguous_stopped", them.company_key, record,
                                  f"{me.name!r} ({', '.join(me.records)}) said no or asked to stop and could be "
                                  f"{them.name} ({them.email or them.linkedin or 'no address'}), so they are stopped "
                                  f"too. If {me.name!r} is someone else, give the full name in [person_name] for the "
                                  f"record, or lift the no-go in the app.", False))
    return stops, issues



# ---- touches -----------------------------------------------------------------------------------------------------

def touch_key(company_key_: str, person_key: str | None, channel: str, sent_at: str) -> str:
    """`<company key>|<person key or ->|<channel>|<New York date>`."""
    return f"{company_key_}|{person_key or '-'}|{channel}|{ny_date(sent_at)}"


# How strongly a reply kind speaks; a stop always wins, so no source can lose one.
_REPLY_RANK = {"unsorted": 0, "interested": 1, "not_now": 1, "said_no": 2, "asked_to_stop": 3}


def _better_date(a: OutcomeIn, b: OutcomeIn) -> OutcomeIn:
    """The outcome whose date to keep: a known date over an estimated one, else the earlier."""
    if a.estimated != b.estimated:
        return b if a.estimated else a
    return a if a.at <= b.at else b


def merge_outcome(outcomes: tuple[OutcomeIn, ...], new: OutcomeIn) -> tuple[tuple[OutcomeIn, ...], str | None]:
    """Add one outcome to a touch's outcomes. One outcome per kind: the same kind again keeps the known (else the
    earlier) date. Two replies: a sorted kind beats `unsorted`; two sorted kinds that differ are a conflict (the
    returned text), and a stop (`asked_to_stop`, then `said_no`) wins it, so a stop in any source is kept;
    otherwise the first stays."""
    for i, old in enumerate(outcomes):
        if old.kind != new.kind:
            continue
        dated = _better_date(old, new)
        conflict = None
        keep = old
        if new.kind == "replied" and old.reply_kind != new.reply_kind:
            r_old, r_new = _REPLY_RANK.get(old.reply_kind, 0), _REPLY_RANK.get(new.reply_kind, 0)
            if min(r_old, r_new) > 0:
                conflict = (f"{old.record} says the reply was {old.reply_kind} and {new.record} says {new.reply_kind}; "
                            f"kept {(new if r_new > r_old else old).reply_kind}.")
            if r_new > r_old:
                keep = new
        merged = OutcomeIn(keep.kind, dated.at, keep.reply_kind, dated.estimated, keep.record)
        return outcomes[:i] + (merged,) + outcomes[i + 1:], conflict
    return outcomes + (new,), None


def _parse_day(text) -> str | None:
    try:
        return date.fromisoformat(str(text or "").strip()).isoformat()
    except ValueError:
        return None


def _find_touch(target: str, touches: list[TouchIn]) -> int | None:
    """The touch a `touch_for` value names: its key, or a record id it came from."""
    for i, t in enumerate(touches):
        if t.key == target:
            return i
    for i, t in enumerate(touches):
        if target in t.records:
            return i
    return None


def _role_fit(row_role: str | None, person_role: str | None) -> int:
    """2 for the same role kind, 1 when either side is not known (`other` included), 0 for two different kinds."""
    if row_role is None or person_role in (None, "other"):
        return 1
    return 2 if row_role == person_role else 0


def join_curated(touches: list[TouchIn], rows: list[SourceRecord], companies: CompanyIndex, *,
                 people: Mapping[str, PersonIn] | None = None
                 ) -> tuple[list[TouchIn], list[IssueIn], dict[str, RecordOutcome]]:
    """Curated rows are annotations on touches. A row joins the touch at the same company and New York date whose
    person has the same role kind (a touch whose person's role is not known, or a row whose role is not known,
    fits any; an exact role is taken first); with several, rows and touches pair in record order, each pair a
    `curated_by_order` issue he can fix with [touch_for]. His `touch_for` (row record id to a touch key or a record
    id) wins. A joined row adds its outcome, `role_named` from `req` when the touch does not know it, and
    `followups_sent` from `fu`: record outcome `merged`, reason `annotation`. A row with no touch is its own touch
    with a placeholder person (issue `touch_unmatched`, outcome `imported`). A row with no company or no date is
    `held` (`unparsed_record`). Returns every touch (the given ones first, in order), the issues and one outcome
    per row."""
    out = list(touches)
    used: set[str] = set()
    issues: list[IssueIn] = []
    outcomes: dict[str, RecordOutcome] = {}
    roles = {k: p.role_kind for k, p in (people or {}).items()}
    touch_for = companies.overrides.touch_for

    for rec in rows:
        rid = f"{rec.source}:{rec.source_key}"
        raw = rec.raw if isinstance(rec.raw, dict) else {}
        day = _parse_day(raw.get("date"))
        name = str(raw.get("company") or "").strip()
        if day is None or not name:
            what = "no date it can read" if day is None else "no company"
            issues.append(IssueIn("unparsed_record", None, rid, f"{rid} has {what}, so it cannot be placed. Fix the "
                                                                f"row in the curated file.", True))
            outcomes[rid] = RecordOutcome("held", "unparsed_record", None, None)
            continue
        ck = companies.add(name, rid)
        row_role = role_kind(raw.get("role"))
        if row_role is None and str(raw.get("role") or "").strip():
            issues.append(IssueIn("role_unmapped", ck, rid, f"{rid} gives the role {raw.get('role')!r}, which is no "
                                                            f"role kind; the row fits a touch of any role.", False))
        found = None
        target = touch_for.get(rid)
        if target is not None:
            found = _find_touch(target, out)
            if found is None:
                issues.append(IssueIn("touch_for_unknown", ck, rid, f"[touch_for] sends {rid} to {target!r}, which "
                                                                    f"is no touch; the row was matched by its date.",
                                      False))
        if found is None:
            pool = [i for i, t in enumerate(out[:len(touches)])
                    if t.company_key == ck and ny_date(t.sent_at) == day
                    and _role_fit(row_role, roles.get(t.person_key)) > 0]
            fit = {i: _role_fit(row_role, roles.get(out[i].person_key)) for i in pool}
            free = [i for i in pool if out[i].key not in used]
            best = max((fit[i] for i in free), default=0)
            free = [i for i in free if fit[i] == best]
            tier = [i for i in pool if fit[i] == best]
            if free:
                found = free[0]
                if len(tier) > 1:
                    issues.append(IssueIn("curated_by_order", ck, rid,
                                          f"{rid} was paired by order with {out[found].key}, one of {len(tier)} "
                                          f"touches that day at {ck}. Rule [touch_for] if it is another one.", False))
        effect = curated_outcome(raw.get("outcome"))
        if effect is None:
            text = str(raw.get("outcome")).strip()
            issues.append(IssueIn("reply_text", ck, rid, f"{rid} says the outcome was {text!r}; kept as a reply to "
                                                         f"sort.", False))
            effect = (["replied"], "unsorted")
        kinds, reply_kind = effect
        fu = followups_count(raw.get("fu"))
        if fu is None:
            issues.append(IssueIn("fu_unmapped", ck, rid, f"{rid} gives follow-ups as {raw.get('fu')!r}; counted as "
                                                          f"none.", False))
            fu = 0
        named = req_named(raw.get("req"))

        if found is not None:
            t = out[found]
            used.add(t.key)
            at = t.sent_at
        else:
            channel = curated_channel(raw.get("channel"))
            if channel is None:
                issues.append(IssueIn("channel_unmapped", ck, rid, f"{rid} gives the channel as "
                                                                   f"{raw.get('channel')!r}; kept as other.", False))
                channel = "other"
            at = noon_ny(day)
            pk = placeholder_key(ck, rid)
            t = TouchIn(key=touch_key(ck, pk, channel, at), company_key=ck, person_key=pk, channel=channel,
                        sent_at=at, posting_key=None, role_named=False, role_named_known=False, outcomes=(),
                        email=None, followups_sent=0, records=())
            said = f" It says {', '.join(kinds)}." if kinds else ""
            issues.append(IssueIn("touch_unmatched", ck, rid, f"{rid} ({day}, {name}, {raw.get('role') or 'no role'}) "
                                                              f"matched no touch, so it is its own touch with an "
                                                              f"unnamed person.{said} Rule [touch_for] if it belongs "
                                                              f"to one.", False))
        merged = t.outcomes
        for kind in kinds:
            merged, conflict = merge_outcome(merged, OutcomeIn(kind, at, reply_kind if kind == "replied" else None,
                                                               True, rid))
            if conflict:
                issues.append(IssueIn("outcome_conflict", ck, rid, conflict, False))
        role_named, known = t.role_named, t.role_named_known
        if not known and named is not None:
            role_named, known = named, True
        t = replace(t, outcomes=merged, role_named=role_named, role_named_known=known,
                    followups_sent=max(t.followups_sent, fu), records=t.records + (rid,))
        if found is not None:
            out[found] = t
            outcomes[rid] = RecordOutcome("merged", "annotation", "outreach_touch", t.key)
        else:
            out.append(t)
            outcomes[rid] = RecordOutcome("imported", "", "outreach_touch", t.key)
    return out, issues, outcomes


# ---- where an interview came from --------------------------------------------------------------------------------

_POSITIVE = frozenset({"replied", "referred", "accepted", "interview", "offer"})


def origin_touch(company_key_: str, interview_at: str | None, touches: list[TouchIn],
                 applications: list[ApplicationIn], *, record: str | None = None,
                 overrides: Overrides | None = None) -> tuple[str | None, tuple[str, ...]]:
    """(the touch or application key an interview is recorded on, the issue codes to raise). His `touch_for` for
    the record (a touch or application key, or a record id) wins. Else, at the company: (1) the only touch sent on
    or before the day with a reply or a positive outcome (with several, the latest, issue `interview_origin`); (2)
    the only touch before the day; (3) the latest touch before it, issue `interview_origin`; (4) the latest
    application before it; (5) none: (None, `interview_origin`), and the caller makes an `interview_touch`. A
    `touch_for` that names no touch gives `touch_for_unknown` and `interview_origin` whatever the fallback finds,
    since the place is then a guess. With no day known every touch counts as before it."""
    day = ny_date(interview_at) if interview_at else None
    code = None
    target = _empty(overrides).touch_for.get(record) if record else None
    if target is not None:
        for t in touches:
            if t.key == target or target in t.records:
                return t.key, ()
        for a in applications:
            if a.key == target or target in a.records:
                return a.key, ()
        code = "touch_for_unknown"

    def codes(found: str | None) -> tuple[str, ...]:
        if code:
            return (code, "interview_origin")
        return (found,) if found else ()

    def latest(items, when):
        return max(items, key=lambda x: (when(x), x.key)).key

    mine = [t for t in touches if t.company_key == company_key_]
    on_or_before = [t for t in mine if day is None or ny_date(t.sent_at) <= day]
    answered = [t for t in on_or_before if any(o.kind in _POSITIVE for o in t.outcomes)]
    if len(answered) == 1:
        return answered[0].key, codes(None)
    if answered:
        return latest(answered, lambda t: t.sent_at), codes("interview_origin")
    before = [t for t in mine if day is None or ny_date(t.sent_at) < day]
    if len(before) == 1:
        return before[0].key, codes(None)
    if before:
        return latest(before, lambda t: t.sent_at), codes("interview_origin")
    apps = [a for a in applications if a.company_key == company_key_ and (day is None or a.applied_on < day)]
    if apps:
        return latest(apps, lambda a: a.applied_on), codes(None)
    return None, codes("interview_origin")


def interview_touch(company_key_: str, day: str, record: str) -> TouchIn:
    """The touch for an interview with no recorded way in: channel `other`, a placeholder person, at noon New York
    on the interview day. The caller adds the interview outcome and the `interview_origin` issue."""
    at = noon_ny(day)
    pk = placeholder_key(company_key_, record)
    return TouchIn(key=touch_key(company_key_, pk, "other", at), company_key=company_key_, person_key=pk,
                   channel="other", sent_at=at, posting_key=None, role_named=False, role_named_known=False,
                   outcomes=(), email=None, followups_sent=0, records=(record,))


def interview_date(record: SourceRecord, origin_at: str | None = None) -> tuple[str | None, bool]:
    """(the interview's New York day, estimated): the earliest session date, else the brief's `verified_on`, else
    the origin touch's day (estimated; the caller raises `date_unknown`). (None, True) when none is known: the
    caller places it by `origin_touch` with no day, or holds it."""
    raw = record.raw if isinstance(record.raw, dict) else {}
    sessions = sorted(d for d in (_parse_day(x) for x in raw.get("session_dates") or []) if d)
    if sessions:
        return sessions[0], False
    brief = raw.get("brief") if isinstance(raw.get("brief"), dict) else {}
    verified = _parse_day(brief.get("verified_on"))
    if verified:
        return verified, False
    if origin_at:
        return ny_date(origin_at), True
    return None, True
