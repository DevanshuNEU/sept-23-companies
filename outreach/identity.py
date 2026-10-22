"""Identity (#31, design 5.2): which stored company a source's candidate is, or a new one.

What becomes one company:

1. The same source record seen before (`company_source` holds `(source_name, source_ref)`): that company, followed
   through `merged_into`. Basis `source_ref`, confidence `certain`. No matching runs.
2. Otherwise the strong keys, in this order: `fein` (the 9 digits, an alias with source `fein`; confidence
   `certain`), `domain` (a `company`-class domain held in non-`guess` `domain` evidence; `strong`) and `name_state`
   (a name key of at least 4 characters from the name, `legal_name` or `dba`, held in `company_name_key` by a company
   whose US state is the candidate's; `likely`). A name with a letter outside ASCII loses letters in its key, so it
   is a strong key on neither side: `name_state` needs both the candidate's name and the stored one to be strong. A
   key recorded with `mark_shared_key` never matches (a subdomain of a shared domain is shared too). A leading `www.`
   is dropped from the candidate's domain for matching and for its stored evidence. The first hit is the company;
   every other company found raises a `merge_candidate` item.
3. Two valid non-shared FEINs that differ (the hit's own, or one of a company merged into it) veto every non-FEIN
   strong match: LCA splits a same-name group by FEIN so two employers' counts never blend, and the name or the
   domain must not join them again. A vetoed company is treated as a weak match.
4. Two or more `name_state` hits and no FEIN or domain hit is ambiguous: no merge, one item against each hit
   (basis `ambiguous_name_state`), never a guess at the lower id. The company made for it writes its name keys
   weak, so it never becomes one more hit. A later ambiguous candidate with no usable FEIN attaches to the
   lowest-id such company with its name key and state (basis `ambiguous_repeat`, `likely`, no new item): the
   ambiguity adds one company, not one per candidate.
5. Weak matches never merge. With `creates` the candidate becomes a new company and one item is raised per weak
   match; without, the result is None. A name key raises at most one name item, against its lowest-id live owner,
   so the sixth company with one name raises one item, not five.

Every match states its basis and confidence: in the `Resolution`, in the company event (`created`, `matched` or
`aliases_added`, which also lists the aliases written) and in every item's reason. Nothing is copied between
companies: a candidate's evidence goes only to the company it resolved to, so a subsidiary never inherits a parent's
size. Everything for one candidate is one transaction (a savepoint inside a caller's): an error rolls it all back.
"""
from __future__ import annotations

import contextlib
import dataclasses
import sqlite3
from dataclasses import dataclass

from outreach.rules.rebuild import record_evidence
from outreach.sources.base import Candidate
from outreach.sources.keys import classify_domain, normalize_fein, registrable_domain
from outreach.store import add_alias, append_event, is_shared_key, name_key, transaction, utcnow

__all__ = ["BASES", "CONFIDENCE", "ITEM_CONFIDENCES", "MIN_NAME_KEY", "Resolution", "Match", "follow_merged",
           "company_state", "find_company", "resolve_candidate", "raise_merge_candidate"]

BASES = ("source_ref", "fein", "domain", "name_state", "ambiguous_repeat", "created")
CONFIDENCE = {"source_ref": "certain", "fein": "certain", "domain": "strong", "name_state": "likely",
              "ambiguous_repeat": "likely", "created": "new"}
# How sure an item is that its two companies are one, most sure first.
ITEM_CONFIDENCES = ("certain", "strong", "likely", "weak")
_ITEM_CONFIDENCE = {"source_ref": "certain", "fein": "certain", "domain": "strong", "name_state": "likely",
                    "name": "weak", "same_board": "strong", "ambiguous_name_state": "likely"}
_ALIAS_CONFIDENCE = {"fein": "certain", "ats": "strong", "linkedin": "strong"}  # any name-like alias: weak
MIN_NAME_KEY = 4  # shorter name keys ("ace", "ibm") never match by name

_NAME_SOURCES = ("name", "hand", "import", "crawl", "merge", "dba", "legal_name", "formd")
_RAW_SOURCES = ("fein", "ats", "linkedin")
_NAME_ALIAS_SOURCE = {"formd": "formd"}  # the alias source of the candidate's name, by source; default `name`
# The 50 states, DC and the territories. "Berlin, DE" cannot be told from Delaware, so DE stays.
US_STATES = frozenset("""
AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI
SC SD TN TX UT VT VA WA WV WI WY DC PR GU VI AS MP
""".split())


@dataclass(frozen=True)
class Resolution:
    company_id: int
    created: bool
    basis: str  # one of BASES
    confidence: str  # CONFIDENCE[basis]
    aliases_added: int
    evidence_added: int
    items_raised: int


@dataclass(frozen=True)
class Match:
    """Company ids and bases, in priority order, one entry per company. `strong[0]` is the candidate's company;
    `weak` never merges: basis `name`, `fein_conflict+<basis>` for a strong match the FEIN veto stopped, or
    `ambiguous_name_state` for each of two or more name and state hits with no FEIN or domain hit."""
    strong: tuple[tuple[int, str], ...]
    weak: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class _Hit:
    company_id: int
    basis: str
    key: str  # the FEIN, domain, name key or source_ref that matched
    why: str = ""  # for a weak hit: why it is not a merge


def follow_merged(conn: sqlite3.Connection, company_id: int) -> int:
    """The live company: `merged_into` followed to its end. LookupError for no such company, ValueError on a loop."""
    seen = set()
    current = company_id
    while True:
        row = conn.execute("SELECT merged_into FROM company WHERE company_id = ?", (current,)).fetchone()
        if row is None:
            raise LookupError(f"no company {current}")
        if row[0] is None:
            return current
        seen.add(current)
        if row[0] in seen:
            raise ValueError(f"merged_into loops back to company {row[0]} from {company_id}")
        current = row[0]


def _state_of(value: str) -> str | None:
    """The US state of an hq value, "City, ST" or a bare "ST" (upper case, one of US_STATES), else None."""
    part = value.rpartition(",")[2].strip()
    return part if part in US_STATES else None


def _one_state(values) -> str | None:
    states = {s for v in values if (s := _state_of(v))}
    return states.pop() if len(states) == 1 else None


def company_state(conn: sqlite3.Connection, company_id: int) -> str | None:
    """The state from the company's non-`guess` `hq` evidence; None when there is none or two states disagree."""
    return _one_state(v for (v,) in conn.execute(
        "SELECT value FROM evidence WHERE company_id = ? AND fact = 'hq' AND confidence <> 'guess'", (company_id,)))


def _candidate_state(cand: Candidate) -> str | None:
    return _one_state(d.value for d in cand.evidence if d.fact == "hq" and d.confidence != "guess")


def _usable_fein(conn: sqlite3.Connection, cand: Candidate) -> str | None:
    fein = normalize_fein(cand.keys.get("fein"))
    return fein if fein and not is_shared_key(conn, "fein", fein) else None


def _domain_shared(conn: sqlite3.Connection, domain: str) -> bool:
    root = registrable_domain(domain)
    return is_shared_key(conn, "domain", domain) or (root is not None and is_shared_key(conn, "domain", root))


def _bare(domain: str | None) -> str | None:
    """The domain without a leading `www.` (when what is left still has a dot)."""
    if domain and domain.startswith("www.") and "." in domain[4:]:
        return domain[4:]
    return domain


def _usable_domain(conn: sqlite3.Connection, cand: Candidate) -> str | None:
    d = _bare(cand.domain)
    return d if d and classify_domain(d) == "company" and not _domain_shared(conn, d) else None


def _has_non_ascii_letter(text: str) -> bool:
    return any(c.isalpha() and not c.isascii() for c in text)


def _name_keys(cand: Candidate) -> list[tuple[str, bool]]:
    """(name key, may match strongly) for the name, legal_name and dba keys of at least MIN_NAME_KEY characters. A
    value with a letter outside ASCII ("Café Labs", "東京 Robotics") loses letters in its key, so it matches weakly
    only."""
    out: dict[str, bool] = {}
    for value in (cand.name, cand.keys.get("legal_name"), cand.keys.get("dba")):
        key = name_key(value)
        if len(key) >= MIN_NAME_KEY:
            out[key] = out.get(key, False) or not _has_non_ascii_letter(value)
    return list(out.items())


def _name_owners(conn: sqlite3.Connection, key: str) -> list[tuple[int, bool]]:
    """(live company, strong) for every company known by the name key, by id: the company_name_key rows plus the
    alias owner (an alias written by another writer has no company_name_key row; it is strong unless its text has a
    letter outside ASCII). A company is strong when any of its rows is."""
    owners: dict[int, bool] = {}
    for cid, strong in conn.execute("SELECT company_id, strong FROM company_name_key WHERE name_key = ?", (key,)):
        live = follow_merged(conn, cid)
        owners[live] = owners.get(live, False) or bool(strong)
    marks = ", ".join("?" for _ in _NAME_SOURCES)
    for cid, alias in conn.execute(
            f"SELECT company_id, alias FROM company_alias WHERE alias_key = ? AND source IN ({marks})",
            (key, *_NAME_SOURCES)):
        live = follow_merged(conn, cid)
        owners[live] = owners.get(live, False) or not _has_non_ascii_letter(alias)
    return sorted(owners.items())


def _conflicting_fein(conn: sqlite3.Connection, company_id: int, fein: str) -> str | None:
    """A valid non-shared FEIN of the company, or of a company merged into it, that is not `fein`; None when there is
    none or when they hold `fein`."""
    other = None
    for (key,) in conn.execute(
            "WITH RECURSIVE family(id) AS (SELECT ? UNION SELECT c.company_id FROM company c "
            "JOIN family f ON c.merged_into = f.id) "
            "SELECT alias_key FROM company_alias WHERE source = 'fein' AND company_id IN (SELECT id FROM family) "
            "ORDER BY alias_key", (company_id,)):
        if key == fein:
            return None
        if other is None and normalize_fein(key) == key and not is_shared_key(conn, "fein", key):
            other = key
    return other


def _distinct(hits: list[_Hit], exclude: set[int] = frozenset()) -> list[_Hit]:
    seen, out = set(exclude), []
    for h in hits:
        if h.company_id not in seen:
            seen.add(h.company_id)
            out.append(h)
    return out


def _find(conn: sqlite3.Connection, cand: Candidate, source_name: str) -> tuple[list[_Hit], list[_Hit]]:
    """(strong, weak) hits, read only."""
    linked = [_Hit(follow_merged(conn, cid), "source_ref", cand.source_ref) for (cid,) in conn.execute(
        "SELECT company_id FROM company_source WHERE source_name = ? AND source_ref = ? ORDER BY company_id",
        (source_name, cand.source_ref))]
    if linked:
        return _distinct(linked), []
    strong: list[_Hit] = []
    fein = _usable_fein(conn, cand)
    if fein:
        row = conn.execute("SELECT company_id FROM company_alias WHERE alias_key = ? AND source = 'fein'",
                           (fein,)).fetchone()
        if row:
            strong.append(_Hit(follow_merged(conn, row[0]), "fein", fein))
    domain = _usable_domain(conn, cand)
    if domain:
        strong.extend(_Hit(follow_merged(conn, cid), "domain", domain) for (cid,) in conn.execute(
            "SELECT DISTINCT company_id FROM evidence WHERE fact = 'domain' AND value = ? AND confidence <> 'guess' "
            "ORDER BY company_id", (domain,)))
    state = _candidate_state(cand)
    weak: list[_Hit] = []
    for key, ours_strong in _name_keys(cand):
        owners = _name_owners(conn, key)
        matched = set()
        for cid, theirs_strong in owners:
            if ours_strong and theirs_strong and state is not None and state == company_state(conn, cid):
                strong.append(_Hit(cid, "name_state", key))
                matched.add(cid)
        # One name item per key: against the lowest-id owner in the candidate's state, else the lowest-id owner.
        in_state = [o for o in owners if state is not None and company_state(conn, o[0]) == state]
        target = (in_state or owners or [None])[0]
        if target is not None and target[0] not in matched:
            cid, theirs_strong = target
            if ours_strong and theirs_strong:
                why = (f"the same name ({key}), but the state is {state or 'unknown'} here and "
                       f"{company_state(conn, cid) or 'unknown'} there")
            else:
                why = f"the same name key ({key}), from a name with letters outside ASCII, which never matches strongly"
            weak.append(_Hit(cid, "name", key, why))
    kept, vetoed = [], []
    for hit in _distinct(strong):
        other = _conflicting_fein(conn, hit.company_id, fein) if fein and hit.basis != "fein" else None
        if other:
            vetoed.append(_Hit(hit.company_id, "fein_conflict+" + hit.basis, hit.key,
                               f"the same {hit.basis.replace('_', ' and ')} ({hit.key}), but FEIN {fein} here and "
                               f"{other} there, so they are kept apart"))
        else:
            kept.append(hit)
    ambiguous = []
    if len(kept) >= 2 and all(h.basis == "name_state" for h in kept):
        repeat = None if fein else _ambiguous_repeat(conn, [h.key for h in kept], state)
        if repeat is not None:
            kept = [repeat]
        else:
            ambiguous = [_Hit(h.company_id, "ambiguous_name_state", h.key,
                              f"the same name ({h.key}) and state ({state}) as {len(kept)} companies, so which one "
                              "it is is unclear") for h in kept]
            kept = []
    return kept, _distinct(vetoed + ambiguous + weak, {h.company_id for h in kept})


def _created_ambiguous(conn: sqlite3.Connection, company_id: int) -> bool:
    """Whether the company was made by an ambiguous resolve: its `created` event lists an ambiguous_name_state
    possible duplicate."""
    return conn.execute(
        "SELECT 1 FROM event e, json_each(e.detail, '$.possible_duplicates') d WHERE e.company_id = ? "
        "AND e.entity = 'company' AND e.kind = 'created' AND json_extract(d.value, '$[1]') = 'ambiguous_name_state' "
        "LIMIT 1", (company_id,)).fetchone() is not None


def _ambiguous_repeat(conn: sqlite3.Connection, keys: list[str], state: str) -> _Hit | None:
    """The lowest-id company made by an ambiguous resolve that holds one of the keys and has the state, or None."""
    found = []
    for key in dict.fromkeys(keys):
        found += [(cid, key) for cid, _ in _name_owners(conn, key)
                  if company_state(conn, cid) == state and _created_ambiguous(conn, cid)]
    if not found:
        return None
    cid, key = min(found)
    return _Hit(cid, "ambiguous_repeat", key)


def find_company(conn: sqlite3.Connection, cand: Candidate, *, source_name: str) -> Match:
    """What the candidate matches, without writing anything."""
    strong, weak = _find(conn, cand, source_name)
    return Match(tuple((h.company_id, h.basis) for h in strong), tuple((h.company_id, h.basis) for h in weak))


def _item_confidence(basis: str) -> str:
    if basis.startswith("fein_conflict"):
        return "weak"
    last = basis.rpartition("+")[2]
    if last.startswith("alias:"):
        return _ALIAS_CONFIDENCE.get(last[len("alias:"):], "weak")
    if last not in _ITEM_CONFIDENCE:
        raise ValueError(f"no confidence known for basis {basis!r}; pass confidence=")
    return _ITEM_CONFIDENCE[last]


def raise_merge_candidate(conn: sqlite3.Connection, a: int, b: int, basis: str, detail: str, *,
                          confidence: str | None = None) -> bool:
    """One `merge_candidate` item per pair (whatever the order of `a` and `b`): `company_id` the lower id,
    `evidence_key` the higher id as text, raised by `source`. The reason names both companies, the basis, the
    confidence (from the basis unless given) and `detail`. Returns whether the row is new: a pair that already has an
    item, open or resolved, gets no second one. Both ids are followed through `merged_into` first: two that are one
    company already raise nothing (False)."""
    if a == b:
        raise ValueError(f"a merge item needs two companies, got {a} twice")
    a, b = follow_merged(conn, a), follow_merged(conn, b)
    if a == b:
        return False
    if not detail.strip():
        raise ValueError("a merge item needs a detail")
    conf = confidence or _item_confidence(basis)
    if conf not in ITEM_CONFIDENCES:
        raise ValueError(f"confidence {conf!r} is not one of {ITEM_CONFIDENCES}")
    low, high = sorted((a, b))
    names = dict(conn.execute("SELECT company_id, name FROM company WHERE company_id IN (?, ?)", (low, high)))
    if len(names) != 2:
        raise LookupError(f"no company {low if low not in names else high}")
    reason = (f"Maybe one company: {names[low]} (#{low}) and {names[high]} (#{high}). Basis {basis}, confidence "
              f"{conf}: {detail}")
    cur = conn.execute(
        "INSERT INTO review_item(kind, company_id, reason, evidence_key, raised_by, created_at) "
        "VALUES ('merge_candidate', ?, ?, ?, 'source', ?) ON CONFLICT DO NOTHING",
        (low, reason, str(high), utcnow()))
    return cur.rowcount == 1


@contextlib.contextmanager
def _atomic(conn: sqlite3.Connection):
    """One candidate's writes: its own transaction, or a savepoint inside the caller's, undone on any error."""
    if not conn.in_transaction:
        with transaction(conn):
            yield
        return
    conn.execute("SAVEPOINT identity_candidate")
    try:
        yield
    except BaseException:
        if conn.in_transaction:  # SQLite may have rolled back the whole transaction (a full disk, an interrupt)
            conn.execute("ROLLBACK TO identity_candidate")
            conn.execute("RELEASE identity_candidate")
        raise
    conn.execute("RELEASE identity_candidate")


def _alias_key(value: str, source: str) -> str:
    return value.lower() if source in _RAW_SOURCES else name_key(value)


def _write_aliases(conn, company_id: int, cand: Candidate, source_name: str, label: str) -> tuple[list, int]:
    """Add the candidate's aliases; an alias another company owns raises an item and the rest continue.
    Returns ([source, alias] for each alias added, items raised)."""
    wanted = [(cand.name, _NAME_ALIAS_SOURCE.get(source_name, "name")),
              (cand.keys.get("legal_name"), "legal_name"), (cand.keys.get("dba"), "dba")]
    fein = _usable_fein(conn, cand)
    if fein:
        wanted.append((fein, "fein"))
    wanted += [(cand.keys.get("ats"), "ats"), (cand.keys.get("linkedin"), "linkedin")]
    added, items = [], 0
    for value, source in wanted:
        if not value or not value.strip():
            continue
        value = value.strip()
        key = _alias_key(value, source)
        if not key:
            continue  # no letters or digits to key on ("日本"): the company stands without this alias
        try:
            if add_alias(conn, company_id, value, source):
                added.append([source, value])
            continue
        except ValueError:
            row = conn.execute("SELECT company_id FROM company_alias WHERE alias_key = ?", (key,)).fetchone()
            if row is None:
                raise
        owner = follow_merged(conn, row[0])
        if owner != company_id:
            items += raise_merge_candidate(
                conn, company_id, owner, f"alias:{source}",
                f"{label} has the {source} {value!r} (key {key}), which already belongs to #{owner}")
    return added, items


def _write_name_keys(conn, company_id: int, cand: Candidate, *, weak_only: bool) -> None:
    """One company_name_key row per name key; `weak_only` (a company made by an ambiguous resolve) writes every key
    with strong 0, so that company never counts as a name and state hit."""
    conn.executemany(
        "INSERT INTO company_name_key(name_key, company_id, strong) VALUES (?, ?, ?) "
        "ON CONFLICT(name_key, company_id) DO UPDATE SET strong = max(strong, excluded.strong) "
        "WHERE excluded.strong > company_name_key.strong",
        [(key, company_id, 0 if weak_only else int(strong)) for key, strong in _name_keys(cand)])


def _link(conn, company_id: int, source_name: str, source_ref: str) -> bool:
    """Upsert the company_source row; True when it is new."""
    new = conn.execute("SELECT 1 FROM company_source WHERE company_id = ? AND source_name = ? AND source_ref = ?",
                       (company_id, source_name, source_ref)).fetchone() is None
    now = utcnow()
    conn.execute(
        "INSERT INTO company_source(company_id, source_name, source_ref, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(company_id, source_name, source_ref) DO UPDATE SET "
        "last_seen_at = max(last_seen_at, excluded.last_seen_at)",
        (company_id, source_name, source_ref, now, now))
    return new


def resolve_candidate(conn: sqlite3.Connection, cand: Candidate, *, source_name: str, creates: bool = True,
                      actor: str = "system") -> Resolution | None:
    """Attach the candidate to its company or make a new one (status `candidate`), then write its aliases, its
    evidence (`record_evidence`, actor `source`; a `domain` draft that is not a company domain is dropped) and its
    `company_source` row. None only when `creates` is false and nothing matched strongly; nothing is written then.

    `actor` labels the company event. ValueError, before any write, for a `hand` evidence draft (hand evidence is
    written locked by its own writer), an empty `source_ref` or a source not registered in `source`. Any error
    rolls back everything this call wrote.
    """
    if any(d.source_kind == "hand" for d in cand.evidence):
        raise ValueError("hand evidence is written locked by its own writer, not through identity")
    if not cand.source_ref:
        raise ValueError(f"candidate {cand.name!r} has no source_ref")
    with _atomic(conn):
        if conn.execute("SELECT 1 FROM source WHERE source_name = ?", (source_name,)).fetchone() is None:
            raise ValueError(f"source {source_name!r} is not registered")
        strong, weak = _find(conn, cand, source_name)
        label = f"{source_name} record {cand.source_ref} ({cand.name})"
        if strong:
            first = strong[0]
            company_id, basis, created = first.company_id, first.basis, False
        elif not creates:
            return None
        else:
            now = utcnow()
            company_id = conn.execute(
                "INSERT INTO company(name, status, status_at, created_at) VALUES (?, 'candidate', ?, ?)",
                (cand.name.strip(), now, now)).lastrowid
            first, basis, created = None, "created", True
        items = 0
        for hit in strong[1:]:
            pair = "source_ref" if basis == "source_ref" else f"{basis}+{hit.basis}"
            items += raise_merge_candidate(
                conn, company_id, hit.company_id, pair,
                f"{label} matched #{company_id} by {basis} ({first.key}) and #{hit.company_id} by {hit.basis} "
                f"({hit.key}); it was attached to #{company_id}")
        for hit in weak:
            items += raise_merge_candidate(conn, company_id, hit.company_id, hit.basis, f"{label}: {hit.why}")
        aliases, alias_items = _write_aliases(conn, company_id, cand, source_name, label)
        items += alias_items
        weak_only = (any(h.basis == "ambiguous_name_state" for h in weak) if created
                     else _created_ambiguous(conn, company_id))
        _write_name_keys(conn, company_id, cand, weak_only=weak_only)
        new_link = _link(conn, company_id, source_name, cand.source_ref)
        detail = {"source": source_name, "source_ref": cand.source_ref, "basis": basis,
                  "confidence": CONFIDENCE[basis], "aliases": aliases}
        if created:
            detail["possible_duplicates"] = [[h.company_id, h.basis] for h in weak]
            append_event(conn, entity="company", kind="created", actor=actor, entity_id=company_id,
                         company_id=company_id, detail=detail)
        elif new_link:
            append_event(conn, entity="company", kind="matched", actor=actor, entity_id=company_id,
                         company_id=company_id, detail={**detail, "key": first.key})
        elif aliases:
            append_event(conn, entity="company", kind="aliases_added", actor=actor, entity_id=company_id,
                         company_id=company_id, detail=detail)
        drafts = [dataclasses.replace(d, value=_bare(d.value)) if d.fact == "domain" else d for d in cand.evidence]
        drafts = [d for d in drafts if d.fact != "domain" or classify_domain(d.value) == "company"]
        evidence = record_evidence(conn, company_id, drafts, actor="source")
    return Resolution(company_id=company_id, created=created, basis=basis, confidence=CONFIDENCE[basis],
                      aliases_added=len(aliases), evidence_added=evidence, items_raised=items)
