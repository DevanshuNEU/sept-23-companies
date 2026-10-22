"""A company Devanshu adds by hand, with the reason he gives (#31 Task 13).

`ByHandSource` turns entries into candidates; `add_by_hand` runs one entry through identity and writes what only a
person can state: the company's domain and state as locked hand evidence, the reason as an event (where the company
page reads it), and a work tier that no source can lower.

Identity refuses hand evidence drafts, so the state goes to it through `Candidate.state`, for matching only. A
company is returned (`created=False`) on its domain, on the same hand record or on the same name in the same state.
The record is `hand:<name key>@<domain>`, else `hand:<name key>@<state>`, else `hand:<name key>`, so "Acme" in Texas
and "Acme" in California are two records, and "Acme" and "Acme, Inc." with the same domain are one. The same name in
another state, or with no state on one side, is a weak match: a new company and one `merge_candidate` item, never a
merge. Devanshu merges the two with one click if they are the same.
"""
from __future__ import annotations

import contextlib
import hashlib
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal

from outreach.identity import US_STATES, find_company, resolve_candidate
from outreach.rules.facts import EvidenceDraft
from outreach.rules.rebuild import record_evidence
from outreach.sources.base import Candidate
from outreach.sources.boards import BoardRef
from outreach.sources.hn import _ref_key
from outreach.sources.tiers import promote, set_work_tier
from outreach.store import append_event, transaction, utcnow

__all__ = ["HandEntry", "ByHandSource", "HandAdd", "add_by_hand"]

SOURCE_NAME = "hand"
VERSION = "hand-1"
_ACTORS = ("devanshu", "claude")


def _parse_ats(ats: str) -> BoardRef:
    """`greenhouse:acme` as a `BoardRef`; `ValueError` for no colon, an unknown ATS or a bad token."""
    kind, colon, token = ats.partition(":")
    if not colon:
        raise ValueError(f"ats {ats!r} is not '<ats>:<token>', such as greenhouse:acme")
    return BoardRef(kind.strip().lower(), token)


def _bare(domain: str | None) -> str | None:
    """The domain without a leading `www.`, as identity matches and stores it."""
    if domain and domain.startswith("www.") and "." in domain[4:]:
        return domain[4:]
    return domain


def _normalize_state(state: str) -> str:
    value = state.strip().upper()
    if value not in US_STATES:
        raise ValueError(f"state {state!r} is not a US state, DC or a territory (a bare two-letter code)")
    return value


@dataclass(frozen=True)
class HandEntry:
    """One company, as Devanshu states it. `ValueError` on construction for a blank name or reason, a state that is
    not a US state, DC or a territory, or an `ats` (`<ats>:<token>`) that fails `BoardRef` validation. The stored
    `state` is upper case and `ats` is normalized."""
    name: str
    reason: str
    domain: str | None = None
    state: str | None = None
    ats: str | None = None

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("a company added by hand needs a name")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("a company added by hand needs a reason")
        if self.state is not None:
            object.__setattr__(self, "state", _normalize_state(self.state))
        if self.ats is not None:
            ref = _parse_ats(self.ats)
            object.__setattr__(self, "ats", f"{ref.ats}:{ref.token}")


class ByHandSource:
    """The by-hand company source: `discover` yields one `Candidate` per entry, in order."""
    name = SOURCE_NAME
    schedule = "on_demand"
    version = VERSION

    def __init__(self, entries: Sequence[HandEntry]):
        self.entries = tuple(entries)

    def discover(self, since: str | None = None) -> Iterator[Candidate]:
        for e in self.entries:
            where = _bare(e.domain) or e.state  # the record is the name at its domain, else in its state
            ref = f"{SOURCE_NAME}:{_ref_key(e.name.strip())}" + (f"@{where}" if where else "")
            yield Candidate(name=e.name.strip(), domain=e.domain, keys={"ats": e.ats} if e.ats else {},
                            source_ref=ref, state=e.state)


@dataclass(frozen=True)
class HandAdd:
    company_id: int
    created: bool
    basis: str
    items_raised: int
    status: str  # the company's status; `removed` means nothing was written


@contextlib.contextmanager
def _atomic(conn: sqlite3.Connection):
    """Resolve, evidence, event, tier and alias land together: a transaction of their own, or a savepoint in the
    caller's."""
    if not conn.in_transaction:
        with transaction(conn):
            yield
        return
    conn.execute("SAVEPOINT add_by_hand")
    try:
        yield
    except BaseException:
        if conn.in_transaction:  # SQLite may have rolled back the whole transaction already
            conn.execute("ROLLBACK TO add_by_hand")
            conn.execute("RELEASE add_by_hand")
        raise
    conn.execute("RELEASE add_by_hand")


def _locked_fact(conn: sqlite3.Connection, company_id: int, fact: str, value: str, source_ref: str,
                 quote: str) -> bool:
    """Write one locked hand evidence row unless the company's winning locked row for that fact already holds the
    value (newest by `recorded_at`, then `evidence_id`, as `company_fact` picks it). The `evidence_once` index covers
    unlocked rows only, so hand rows are de-duplicated here; a value that was locked once but has been replaced is
    written again. True when written."""
    winner = conn.execute(
        "SELECT value FROM evidence WHERE company_id = ? AND fact = ? AND locked = 1 "
        "ORDER BY recorded_at DESC, evidence_id DESC LIMIT 1", (company_id, fact)).fetchone()
    if winner is not None and winner["value"] == value:
        return False
    cur = conn.execute(
        "INSERT INTO evidence(company_id, fact, value, confidence, locked, source_kind, source_ref, quote, "
        "recorded_at) VALUES (?, ?, ?, 'confirmed', 1, 'hand', ?, ?, ?) ON CONFLICT DO NOTHING",
        (company_id, fact, value, source_ref, quote, utcnow()))
    return cur.rowcount == 1


def add_by_hand(conn: sqlite3.Connection, *, name: str, reason: str, domain: str | None = None,
                state: str | None = None, ats: str | None = None,
                actor: Literal["devanshu", "claude"] = "devanshu") -> HandAdd:
    """Add a company Devanshu (or Claude) named, or find the one meant.

    Everything is validated before any write (`ValueError` for a blank name or reason, a bad domain, a state that is
    not a US state, DC or a territory, a bad `ats`, or an actor other than devanshu or claude): the entry is built,
    its candidate made. Then, as one unit (a transaction, or a savepoint inside the caller's), the candidate goes
    through `resolve_candidate(source_name="hand")` and one `company` / `added_by_hand` event holds the reason (actor
    as given; key `<source_ref>:<sha256(reason)[:12]>`, so the same record with the same reason adds nothing).
    An `ats` becomes an alias; `find_boards` makes the board.

    Devanshu's add writes the domain and `hq` (the state, bare) as locked hand evidence (`confirmed`, the reason as
    the domain row's quote; skipped only when the company's winning locked row for that fact already holds the
    value), and sets the work tier to 1 (`by hand`) and promotes it, so a later source run cannot lower it.

    Claude's add writes them through `record_evidence` as `guess`, unlocked, source kind `other`, and promotes
    nothing: a locked fact is only ever Devanshu's own.

    A company that is `removed` (the one the candidate resolves to) is returned with `status="removed"` and nothing
    is written or promoted.
    """
    if actor not in _ACTORS:
        raise ValueError(f"actor {actor!r} is not one of {_ACTORS}")
    entry = HandEntry(name=name, reason=reason, domain=domain, state=state, ats=ats)
    (cand,) = ByHandSource([entry]).discover()  # Candidate validates the domain
    bare = _bare(cand.domain)
    reason_text = entry.reason.strip()
    with _atomic(conn):
        match = find_company(conn, cand, source_name=SOURCE_NAME)
        if match.strong:
            hit_id, hit_basis = match.strong[0]
            status = conn.execute("SELECT status FROM company WHERE company_id = ?", (hit_id,)).fetchone()[0]
            if status == "removed":
                return HandAdd(company_id=hit_id, created=False, basis=hit_basis, items_raised=0, status=status)
        conn.execute("INSERT INTO source(source_name, version) VALUES (?, ?) "
                     "ON CONFLICT(source_name) DO UPDATE SET version = excluded.version", (SOURCE_NAME, VERSION))
        res = resolve_candidate(conn, cand, source_name=SOURCE_NAME, actor=actor)
        company_id = res.company_id
        if actor == "devanshu":
            if bare:
                _locked_fact(conn, company_id, "domain", bare, cand.source_ref, reason_text)
            if entry.state:
                _locked_fact(conn, company_id, "hq", entry.state, cand.source_ref, "")
        else:
            drafts = []
            if bare:
                drafts.append(EvidenceDraft("domain", bare, "guess", "other", cand.source_ref, reason_text))
            if entry.state:
                drafts.append(EvidenceDraft("hq", entry.state, "guess", "other", cand.source_ref))
            record_evidence(conn, company_id, drafts, actor="claude")
        append_event(conn, entity="company", kind="added_by_hand", actor=actor, entity_id=company_id,
                     company_id=company_id, detail={"reason": reason_text},
                     idempotency_key=f"{cand.source_ref}:{hashlib.sha256(reason_text.encode('utf-8')).hexdigest()[:12]}")
        if actor == "devanshu":
            set_work_tier(conn, company_id, 1, "by hand", VERSION)
            promote(conn, company_id, actor)
        status = conn.execute("SELECT status FROM company WHERE company_id = ?", (company_id,)).fetchone()[0]
    return HandAdd(company_id=company_id, created=res.created, basis=res.basis, items_raised=res.items_raised,
                   status=status)
