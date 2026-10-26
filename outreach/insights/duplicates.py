"""Possible duplicates among the companies he reached (#81): listed, never merged here.

Two dataset companies (both survivors) may be one company when they share, strongest first:

- a non-guess `domain` fact (basis `insights_domain`, `strong`);
- a `company`-class registrable domain in their people's email addresses (`insights_email_domain`, `likely`);
- a `company_name_key` (always 4 or more characters; `insights_name_key`, `likely`);
- one name key that starts with another's, the shorter 4 or more characters, "crusoe" and "crusoeenergy"
  (`insights_name_prefix`, `weak`);
- one name of 2 to 5 letters that is the initials of the other's words, "gm" and "General Motors"
  (`insights_initials`, `weak`).

A `free`, `peo` or `platform` domain never pairs, and neither does one in `shared_key` (many employers use it). Each pair is listed once, with its strongest basis. A pair he
already settled as two companies (a resolved `merge_candidate` item) is not listed again. Listing reads only;
`raise_duplicate_items` (the `duplicates --raise` command) turns pairs into `merge_candidate` items he settles in
Needs you, and nothing else here writes.
"""
from __future__ import annotations

import itertools
import json
import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from outreach.identity import ITEM_CONFIDENCES, raise_merge_candidate
from outreach.insights.dataset import OutcomeRow
from outreach.ranking.common import canonical_ids
from outreach.sources.keys import classify_domain, registrable_domain
from outreach.store import transaction

__all__ = ["BASES", "DuplicatePair", "possible_duplicates", "raise_duplicate_items"]

# Strongest first; a pair keeps the first basis it has.
BASES = (("insights_domain", "strong"), ("insights_email_domain", "likely"), ("insights_name_key", "likely"),
         ("insights_name_prefix", "weak"), ("insights_initials", "weak"))
_BASIS_ORDER = {basis: i for i, (basis, _conf) in enumerate(BASES)}
_CONFIDENCE_ORDER = {conf: i for i, conf in enumerate(ITEM_CONFIDENCES)}
# Words a company name may end with that are not part of its initials ("General Motors Company" is GM).
_SUFFIXES = frozenset({"inc", "llc", "ltd", "co", "corp", "corporation", "company", "plc", "gmbh", "lp", "llp"})
_SHORT = re.compile(r"[A-Za-z]{2,5}")
_PREFIX_MIN = 4


@dataclass(frozen=True)
class DuplicatePair:
    a_id: int  # a_id < b_id
    a_name: str
    b_id: int
    b_name: str
    basis: str
    confidence: str
    detail: str  # one plain line, such as "both have people at gm.com"


def _company_domain(value: str | None, shared: frozenset[str] = frozenset()) -> str | None:
    domain = registrable_domain(value)
    return domain if domain and domain not in shared and classify_domain(domain) == "company" else None


def _words(name: str) -> list[str]:
    """The words of a name without a trailing legal suffix ("General Motors Company" gives two)."""
    words = re.findall(r"[A-Za-z0-9]+", name)
    while len(words) > 1 and words[-1].lower() in _SUFFIXES:
        words.pop()
    return words


def _initials(name: str) -> str | None:
    words = _words(name)
    return "".join(w[0] for w in words).lower() if len(words) >= 2 else None


def _short_name(name: str) -> str | None:
    compact = re.sub(r"[\s.]", "", "".join(_words(name)))
    return compact.lower() if _SHORT.fullmatch(compact) else None


def _by_key(found: Iterable[tuple[int, str]]) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for cid, key in found:
        out.setdefault(key, set()).add(cid)
    return out


def _pairs(rows: Sequence[OutcomeRow], *, domains: Iterable[tuple[int, str]], emails: Iterable[tuple[int, str]],
           name_keys: Iterable[tuple[int, str]], kept_apart: set[tuple[int, int]]) -> list[DuplicatePair]:
    """The pure part: survivor ids with their keys in, the strongest pair per two companies out."""
    names = {r.company_id: r.name for r in rows}
    best: dict[tuple[int, int], tuple[str, str]] = {}

    def offer(a: int, b: int, basis: str, detail: str) -> None:
        if a == b or a not in names or b not in names:
            return
        pair = (min(a, b), max(a, b))
        if pair in kept_apart:
            return
        if pair not in best or _BASIS_ORDER[basis] < _BASIS_ORDER[best[pair][0]]:
            best[pair] = (basis, detail)

    for basis, found, words in (("insights_domain", domains, "both have the domain {}"),
                                ("insights_email_domain", emails, "both have people at {}"),
                                ("insights_name_key", name_keys, "both are known by the name {}")):
        for key, ids in sorted(_by_key(found).items()):
            for a, b in itertools.combinations(sorted(ids), 2):
                offer(a, b, basis, words.format(key))
    keyed = sorted({(key, cid) for cid, key in name_keys})
    for i, (short, cid) in enumerate(keyed):
        if len(short) < _PREFIX_MIN:
            continue
        for key, other in itertools.takewhile(lambda kc: kc[0].startswith(short), keyed[i + 1:]):
            if key != short:
                offer(cid, other, "insights_name_prefix", f"{short} is the start of {key}")
    by_initials = _by_key((r.company_id, i) for r in rows if (i := _initials(r.name)))
    for r in rows:
        short = _short_name(r.name)
        for other in sorted(by_initials.get(short, ()) if short else ()):
            offer(r.company_id, other, "insights_initials", f"{r.name} is the initials of {names[other]}")
    conf = dict(BASES)
    out = [DuplicatePair(a, names[a], b, names[b], basis, conf[basis], detail)
           for (a, b), (basis, detail) in best.items()]
    out.sort(key=lambda p: (_CONFIDENCE_ORDER[p.confidence], p.a_id, p.b_id))
    return out


def possible_duplicates(conn: sqlite3.Connection, rows: Sequence[OutcomeRow]) -> list[DuplicatePair]:
    """Pairs among `rows` (the dataset's survivors) that may be one company, strongest first, then by ids. Reads
    only: the merge chains, the families' domain facts, people's addresses and name keys, and settled items."""
    if len(rows) < 2:
        return []
    canon = canonical_ids(conn)
    survivors = {r.company_id for r in rows}
    family = json.dumps(sorted(cid for cid, end in canon.items() if end in survivors))

    def owner(cid: int) -> int:
        return canon.get(cid, cid)

    shared = frozenset(v for (v,) in conn.execute("SELECT value FROM shared_key WHERE kind = 'domain'"))
    domains = [(owner(r[0]), d) for r in conn.execute(
        "SELECT company_id, value FROM evidence WHERE fact = 'domain' AND confidence <> 'guess' "
        "AND company_id IN (SELECT value FROM json_each(?))", (family,)) if (d := _company_domain(r[1], shared))]
    emails = [(owner(r[0]), d) for r in conn.execute(
        "SELECT company_id, email FROM person WHERE email IS NOT NULL "
        "AND company_id IN (SELECT value FROM json_each(?))", (family,)) if (d := _company_domain(r[1], shared))]
    name_keys = [(owner(r[0]), r[1]) for r in conn.execute(
        "SELECT company_id, name_key FROM company_name_key WHERE company_id IN (SELECT value FROM json_each(?))",
        (family,))]
    kept_apart = set()
    for r in conn.execute("SELECT company_id, evidence_key FROM review_item WHERE kind = 'merge_candidate' "
                          "AND state = 'resolved' AND company_id IN (SELECT value FROM json_each(?))", (family,)):
        if r[1].isdigit():
            a, b = owner(r[0]), owner(int(r[1]))
            kept_apart.add((min(a, b), max(a, b)))
    return _pairs(rows, domains=domains, emails=emails, name_keys=name_keys, kept_apart=kept_apart)


def raise_duplicate_items(conn: sqlite3.Connection, pairs: Iterable[DuplicatePair]) -> int:
    """One `merge_candidate` item per pair, all in one transaction; returns how many were new. A pair that already
    has an item, open or resolved, gets no second one."""
    new = 0
    with transaction(conn):
        for p in pairs:
            new += raise_merge_candidate(conn, p.a_id, p.b_id, p.basis, p.detail, confidence=p.confidence)
    return new
