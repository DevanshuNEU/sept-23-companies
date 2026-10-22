"""A company Devanshu adds by hand, with the reason he gives (#31 Task 13).

`ByHandSource` turns entries into candidates; `add_by_hand` runs one entry through identity and writes what only a
person can state: the company's domain and state as locked hand evidence, the reason as an event (where the company
page reads it), and a work tier that no source can lower.

Identity refuses hand evidence drafts, and a by-hand candidate carries no state at resolve time. So a company is
returned (`created=False`) only on its domain or the same hand record (the name key is the `source_ref`, so "Acme" and
"Acme, Inc." are one record). A name already known from another source is a weak match: a new company and one
`merge_candidate` item, never a merge. Devanshu merges the two with one click if they are the same.
"""
from __future__ import annotations

import contextlib
import hashlib
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal

from outreach.identity import US_STATES, resolve_candidate
from outreach.sources.base import Candidate
from outreach.sources.boards import BoardRef
from outreach.sources.tiers import promote, set_work_tier
from outreach.store import append_event, name_key, transaction, utcnow

__all__ = ["HandEntry", "ByHandSource", "HandAdd", "add_by_hand"]

SOURCE_NAME = "hand"
VERSION = "hand-1"
_ACTORS = ("devanshu", "claude")


def _ref_key(name: str) -> str:
    """The name key, or a hash of the name when it has no ASCII letter or digit ("日本"), so such names do not all
    share one source_ref."""
    return name_key(name) or hashlib.sha256(name.strip().lower().encode("utf-8")).hexdigest()[:12]


def _parse_ats(ats: str) -> BoardRef:
    """`greenhouse:acme` as a `BoardRef`; `ValueError` for no colon, an unknown ATS or a bad token."""
    kind, colon, token = ats.partition(":")
    if not colon:
        raise ValueError(f"ats {ats!r} is not '<ats>:<token>', such as greenhouse:acme")
    return BoardRef(kind.strip().lower(), token)


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
            yield Candidate(name=e.name.strip(), domain=e.domain, keys={"ats": e.ats} if e.ats else {},
                            source_ref=f"{SOURCE_NAME}:{_ref_key(e.name)}")


@dataclass(frozen=True)
class HandAdd:
    company_id: int
    created: bool
    basis: str
    items_raised: int


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
    """Write one locked hand evidence row unless the company already has that fact with that value locked (the
    `evidence_once` index covers unlocked rows only, so hand rows are de-duplicated here). True when written."""
    if conn.execute("SELECT 1 FROM evidence WHERE company_id = ? AND fact = ? AND value = ? AND locked = 1",
                    (company_id, fact, value)).fetchone():
        return False
    cur = conn.execute(
        "INSERT INTO evidence(company_id, fact, value, confidence, locked, source_kind, source_ref, quote, "
        "recorded_at) VALUES (?, ?, ?, 'confirmed', 1, 'hand', ?, ?, ?) ON CONFLICT DO NOTHING",
        (company_id, fact, value, source_ref, quote, utcnow()))
    return cur.rowcount == 1


def add_by_hand(conn: sqlite3.Connection, *, name: str, reason: str, domain: str | None = None,
                state: str | None = None, ats: str | None = None,
                actor: Literal["devanshu", "claude"] = "devanshu") -> HandAdd:
    """Add a company Devanshu named, or find the one he means.

    Everything is validated before any write (`ValueError` for a blank name or reason, a bad domain, a state that is
    not a US state, DC or a territory, a bad `ats`, or an actor other than devanshu): the entry is built, its
    candidate made. Then, as one unit (a transaction, or a savepoint inside the caller's), the candidate goes through
    `resolve_candidate(source_name="hand")`, the domain and `hq` (the state, bare) are written as locked hand
    evidence (`confirmed`, the reason as the domain row's quote; a fact already locked with that value is not
    written again), one `company` / `added_by_hand` event holds the reason (key `hand:<name key>:<sha256(reason)[:12]>`,
    so the same name with the same reason adds nothing), and the work tier is set to 1 (`by hand`) and promoted, so a
    later source run cannot lower it. An `ats` becomes an alias; `find_boards` makes the board.

    `actor="claude"` raises `ValueError`: locked hand facts are Devanshu's own, and the schema has no source kind
    for Claude's research, so a Claude add has nowhere correct to put its domain and state yet.
    """
    if actor not in _ACTORS:
        raise ValueError(f"actor {actor!r} is not one of {_ACTORS}")
    if actor == "claude":
        raise ValueError("a company added by Claude cannot be written as locked hand evidence, and the schema has "
                         "no source kind for Claude's research yet")
    entry = HandEntry(name=name, reason=reason, domain=domain, state=state, ats=ats)
    (cand,) = ByHandSource([entry]).discover()  # Candidate validates the domain
    key = _ref_key(entry.name)
    bare = cand.domain[4:] if cand.domain and cand.domain.startswith("www.") and "." in cand.domain[4:] \
        else cand.domain  # identity strips www. from a domain it matches and stores
    reason_text = entry.reason.strip()
    with _atomic(conn):
        conn.execute("INSERT INTO source(source_name, version) VALUES (?, ?) "
                     "ON CONFLICT(source_name) DO UPDATE SET version = excluded.version", (SOURCE_NAME, VERSION))
        res = resolve_candidate(conn, cand, source_name=SOURCE_NAME, actor=actor)
        company_id = res.company_id
        if bare:
            _locked_fact(conn, company_id, "domain", bare, cand.source_ref, reason_text)
        if entry.state:
            _locked_fact(conn, company_id, "hq", entry.state, cand.source_ref, "")
        append_event(conn, entity="company", kind="added_by_hand", actor=actor, entity_id=company_id,
                     company_id=company_id, detail={"reason": reason_text},
                     idempotency_key=f"hand:{key}:{hashlib.sha256(reason_text.encode('utf-8')).hexdigest()[:12]}")
        set_work_tier(conn, company_id, 1, "by hand", VERSION)
        promote(conn, company_id, actor)
    return HandAdd(company_id=company_id, created=res.created, basis=res.basis, items_raised=res.items_raised)
