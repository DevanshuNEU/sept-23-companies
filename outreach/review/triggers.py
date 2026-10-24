"""The sweep that raises the Needs-you items nothing else raises (#16).

Triggers 4 (size unknown) and 5 (missing email domain) are here; trigger 3 and the sweep that runs them all follow.
Every sync reads stored data, so it is idempotent and cheap, and it can run for a few companies right after a pick.
A sync clears only the items of its own `(kind, raised_by)`, so another producer's item is never touched.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Collection
from dataclasses import dataclass

from outreach.ranking.common import canonical_ids
from outreach.review import words
from outreach.review.config import ReviewConfig, load_review_config
from outreach.review.items import clear_item, decided_before, raise_item, unit
from outreach.review.play import emailing, in_play, ny_date

__all__ = ["SyncResult", "sync_size_unknown", "sync_domain_missing"]


@dataclass(frozen=True)
class SyncResult:
    raised: int
    cleared: int
    skipped: str | None = None


def _owners_with(conn: sqlite3.Connection, facts: tuple[str, ...]) -> set[int]:
    """Companies that have a stored row for any of `facts`. A row on a duplicate merged into a company counts for
    the company it ended up in. One query for the store, not one per company."""
    marks = ", ".join("?" for _ in facts)
    rows = conn.execute(f"SELECT DISTINCT company_id FROM company_fact WHERE fact IN ({marks})", facts).fetchall()
    if not rows:
        return set()
    canon = canonical_ids(conn)
    return {canon.get(r[0], r[0]) for r in rows}


def _names(conn: sqlite3.Connection, ids: Collection[int]) -> dict[int, str]:
    wanted = sorted(ids)
    if not wanted:
        return {}
    marks = ", ".join("?" for _ in wanted)
    return {r[0]: r[1] for r in conn.execute(
        f"SELECT company_id, name FROM company WHERE company_id IN ({marks})", wanted)}


def _sync(conn: sqlite3.Connection, *, now: str, scope: set[int] | None, kind: str, raised_by: str, key: str,
          want: dict[int, str], have: set[int], reason, cleared_note: str) -> SyncResult:
    """Raise `kind` for each company in `want` (company id to a basis) that is not in `have`, unless he settled it;
    clear this producer's open items whose company is in `have`. `scope` limits both."""
    raised = cleared = 0
    with unit(conn):
        names = _names(conn, [c for c in want if c not in have and (scope is None or c in scope)])
        for cid in sorted(want):
            if cid in have or (scope is not None and cid not in scope):
                continue
            if decided_before(conn, kind=kind, company_id=cid, evidence_key=key) is not None:
                continue
            got = raise_item(conn, kind=kind, company_id=cid, reason=reason(names[cid], want[cid]),
                             evidence_key=key, raised_by=raised_by, now=now)
            raised += got.created or got.reopened
        for row in conn.execute("SELECT review_id, company_id FROM review_item WHERE kind = ? AND raised_by = ? "
                                "AND state = 'open' ORDER BY review_id", (kind, raised_by)).fetchall():
            if row["company_id"] in have and (scope is None or row["company_id"] in scope):
                cleared += clear_item(conn, row["review_id"], cleared_note, now)
    return SyncResult(raised, cleared)


def _scope(company_ids: Collection[int] | None) -> set[int] | None:
    return None if company_ids is None else set(company_ids)


def sync_size_unknown(conn: sqlite3.Connection, *, now: str, company_ids: Collection[int] | None = None,
                      cfg: ReviewConfig | None = None) -> SyncResult:
    """Trigger 4. Each in-play company with no `size` and no `headcount` fact gets a `size_unknown` item
    (`raised_by 'daily_list'`, key `no-size`), unless he already settled it. An item whose company has a size now
    clears. A `size` row of any confidence counts, so a guess means the size is known here (the ranker's
    `size_guess` covers guesses)."""
    cfg = cfg or load_review_config()
    play = in_play(conn, today=ny_date(now), planned_days=cfg.planned_days)
    return _sync(conn, now=now, scope=_scope(company_ids), kind="size_unknown", raised_by="daily_list",
                 key="no-size", want=play, have=_owners_with(conn, ("size", "headcount")),
                 reason=words.size_reason, cleared_note="A size is on file now.")


def sync_domain_missing(conn: sqlite3.Connection, *, now: str, company_ids: Collection[int] | None = None,
                        cfg: ReviewConfig | None = None) -> SyncResult:
    """Trigger 5. Each company he is emailing with no `domain` fact gets a `domain_missing` item
    (`raised_by 'sender'`, key `no-domain`), unless he already settled it. Only this producer's open items clear
    when a domain arrives: the crawler's own `domain_missing` item (no usable domain) is never touched."""
    cfg = cfg or load_review_config()
    mailing = emailing(conn, today=ny_date(now), planned_days=cfg.planned_days)
    return _sync(conn, now=now, scope=_scope(company_ids), kind="domain_missing", raised_by="sender",
                 key="no-domain", want={cid: "" for cid in mailing}, have=_owners_with(conn, ("domain",)),
                 reason=lambda name, _basis: words.domain_reason(name),
                 cleared_note="A domain is on file now.")
