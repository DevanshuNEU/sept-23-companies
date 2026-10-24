"""Linking: which records are one company, and which mentions are one person.

When unsure, never merge. Two worst outcomes guide every rule here: one real person or company split in two (a second
cold email to someone already reached), and two different people or companies made one (a stop or a hold on the wrong
one, or a company hidden behind another). So the rules join only on an exact key; anything close is kept apart and
raised as an issue for his overrides (`same_company`, `not_same_company`, `person_name`), and his overrides win.

Companies (`CompanyIndex`). The plan's company key is `company_key(name)`: the store's `name_key` (the key #31's
identity and `company_name_key` use), with one guard: a name whose letters do not all fold to ASCII keeps its own
key, so two names in another script never collapse into one. data.js companies are the base; every other record
links by that exact key. Near names ("Tidewater" and "Tidewater AI") stay two companies and come back from
`near_names()` for one merge item each; the writer resolves each company through `identity.resolve_candidate`, which
is where store-side matching (domain, FEIN, name and state) happens.

People (`PersonIndex`), matched the way MCP `add_people` matches (`outreach.sources.keys`): by address anywhere,
else by LinkedIn profile anywhere (one normal form), else by name at the company. Then, for a mention with no
address and no profile (a `contacted.txt` line), `finalize()` folds it into the one person at the company with the
same name, or whose first name it is; with two such people it stays its own person and raises an issue. Folding at
the end makes the result the same whatever order the sources come in. A same-name person with a different address
is someone else (MCP's `same_name_as`), and a stop on one reaches the other (`carry_stops`, MCP's same-name refusal).
"""
from __future__ import annotations

import bisect
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field

from outreach.importer.manifest import Overrides
from outreach.importer.model import IssueIn, TouchIn
from outreach.importer.records import SOURCES
from outreach.importer.vocab import STOP_KINDS
from outreach.sources.keys import email_key, linkedin_key, person_name_key
from outreach.store import name_key

__all__ = ["company_key", "CompanyIndex", "NEAR_WORDS", "MIN_PREFIX", "ROLE_WORDS", "parse_person_name",
           "PersonId", "PersonIndex", "placeholder_key", "is_placeholder", "carry_stops"]

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
        a parenthetical, or one key starts with the other and the shorter has at least MIN_PREFIX characters.
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
        return sorted(pairs)


# ---- people ------------------------------------------------------------------------------------------------------

ROLE_WORDS = frozenset({
    "founder", "founders", "cofounder", "cofounders", "co-founder", "co-founders", "cto", "ceo", "coo", "cfo", "vp",
    "em", "pm", "hr", "recruiter", "recruiters", "recruiting", "talent", "team", "hiring", "manager", "engineer",
    "engineers", "someone", "unknown",
})


def parse_person_name(text: str | None) -> str | None:
    """The name when the text is 1 to 4 words, each starting with a letter, and no word is a role word
    ("founders", "the CTO", "hiring team"); else None, and the planner makes a placeholder person."""
    words = (text or "").split()
    if not 1 <= len(words) <= 4:
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
    """The profile key for a link written loosely (no scheme, http), as add_people would store it."""
    url = text.strip()
    if re.match(r"^http://", url, re.I):
        url = "https://" + url[7:]
    elif not re.match(r"^https://", url, re.I):
        url = "https://" + url
    return linkedin_key(url)


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
        if found is None:
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
        namesakes = [p for p in self._people.values() if p.company_key == company_key_ and p.anchored
                     and not p.placeholder and p.name_key == person_name_key(name)]
        fits = [p for p in namesakes if p.compatible(email, linkedin)]
        if len(fits) == 1:
            return self._join(fits[0], company_key_, email, linkedin, record)
        key = self._new(f"{company_key_}/{email or linkedin}", company_key_, name, email, linkedin, record)
        if namesakes:
            self._issue("same_name", company_key_, record,
                        f"{name} ({email or linkedin}) has the same name as "
                        f"{', '.join(f'{p.name} ({p.email or p.linkedin})' for p in namesakes)} at {company_key_} "
                        f"and another address or profile, so they are kept as two people.")
        return key

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
        loose = [p for p in self._people.values() if not p.anchored and not p.placeholder]
        live = [p for p in self._people.values() if not p.placeholder]
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
        return dict(self._folded)

    def _ambiguous(self, person: _Person, candidates: list[_Person]) -> None:
        self._ambiguous_keys.add(person.key)
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

    def namesakes(self, key: str) -> list[str]:
        """The other people at the same company with the same name (kept apart by another address or profile)."""
        me = self._people[self.resolve(key)]
        return sorted(p.key for p in self._people.values()
                      if p.key != me.key and p.key not in self._folded and not p.placeholder
                      and p.company_key == me.company_key and p.name_key == me.name_key)


def carry_stops(people: PersonIndex, touches: Iterable[TouchIn]
                ) -> tuple[dict[str, tuple[str, str]], list[IssueIn]]:
    """The people who must never be emailed again, as (no_go reason, record id) by resolved person key: everyone
    whose reply said no or asked to stop in any source, and each same-name person at their company (add_people
    refuses a same-name person who said no, so a second address never brings them back). Each namesake raises
    `same_name_stopped`."""
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
                                  f"{them.company_key}, so they are stopped too. Lift it if they are someone else.",
                                  False))
    return stops, issues

