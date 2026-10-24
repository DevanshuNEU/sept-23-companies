"""The sweep that raises the Needs-you items nothing else raises (#16).

Triggers 4 (size unknown), 5 (missing email domain) and 3 (unclear sponsorship wording) are here.
Every sync reads stored data, so it is idempotent and cheap, and it can run for a few companies right after a pick.
A sync clears only the items of its own `(kind, raised_by)`, so another producer's item is never touched.
"""
from __future__ import annotations

import dataclasses
import hashlib
import sqlite3
from collections.abc import Collection
from dataclasses import dataclass

from outreach.ranking.common import canonical_ids
from outreach.review import words
from outreach.review.config import ReviewConfig, load_review_config
from outreach.review.items import clear_item, decided_before, raise_item, unit
from outreach.review.play import emailing, in_play, live_company_ids, ny_date
from outreach.rules import Decision, passes, ruleset_version
from outreach.rules.rebuild import stored_decision

__all__ = ["SyncResult", "sync_size_unknown", "sync_domain_missing", "sync_sponsorship"]


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


_SPONSORS = ("yes", "opt_only")
_UNCLEAR_NOTE = "The wording is no longer unclear here."


def _visible(d: Decision) -> bool:
    """Whether the rules show the posting: `passes()` for engineering, and the intern lane counted as engineering.
    Restates `outreach.ranking.hide.posting_visible` on purpose (a test pins the two together), so a change to what
    ranking shows never changes what this trigger asks about without a failing test."""
    if d.lane == "engineering":
        return passes(d)
    if d.lane == "intern":
        return passes(dataclasses.replace(d, lane="engineering"))
    return False


def wording_key(quote: str) -> str:
    """The evidence key of a sponsorship wording: sha256 of the lower-cased, whitespace-collapsed quote, 16 hex."""
    return hashlib.sha256(" ".join(quote.lower().split()).encode("utf-8")).hexdigest()[:16]


def _top_company_ids(conn: sqlite3.Connection, top_n: int) -> set[int]:
    """The top `top_n` of the ranked list, or nobody when the ranker is not there or has not run."""
    try:
        from outreach.ranking.scores import ranked_list
    except ImportError:
        return set()
    return {row.company_id for row in ranked_list(conn, limit=top_n)}


def _doubtful_boards(conn: sqlite3.Connection) -> frozenset[int]:
    try:
        from outreach.ranking.common import doubtful_board_ids
    except ImportError:
        return frozenset()
    return doubtful_board_ids(conn)


def sync_sponsorship(conn: sqlite3.Connection, *, now: str, company_ids: Collection[int] | None = None,
                     cfg: ReviewConfig | None = None) -> SyncResult:
    """Trigger 3. One `sponsorship_unclear` item per company whose rules decision on an open posting is neither a
    clear yes nor a clear no (`raised_by 'rules'`, key from the wording, on the company's newest such posting).

    In scope: a live company that is in play or in the top `sponsorship_top_n` of the ranked list, so the list never
    floods. Left out: a posting the reader has queued, a posting on a doubtful board, a company with a locked
    sponsorship fact, and a company with another shown posting that reads `yes` or `opt_only`. Skipped, with the
    reason in `skipped`, when the current ruleset is not the code's or holds no decisions: that is missing input, not
    "nothing is wrong", so nothing is raised or cleared.

    An item clears when its wording is no longer a candidate for the company. An item whose company is out of scope
    clears only when the ranked list is known (not empty); with no ranking, the stale sweep decides later.
    """
    cfg = cfg or load_review_config()
    version = ruleset_version()
    current = conn.execute("SELECT ruleset_version FROM ruleset WHERE is_current = 1").fetchone()
    if current is None or current[0] != version:
        return SyncResult(0, 0, "ruleset not current")
    if conn.execute("SELECT 1 FROM decision WHERE ruleset_version = ? LIMIT 1", (version,)).fetchone() is None:
        return SyncResult(0, 0, "no decisions under the current ruleset")
    scope = _scope(company_ids)
    play = in_play(conn, today=ny_date(now), planned_days=cfg.planned_days)
    top = _top_company_ids(conn, cfg.sponsorship_top_n)
    in_scope = live_company_ids(conn) & (set(play) | top)
    canon = canonical_ids(conn)
    doubtful = _doubtful_boards(conn)
    pending = {r[0] for r in conn.execute("SELECT DISTINCT posting_id FROM read_queue "
                                          "WHERE state IN ('queued', 'leased')")}
    locked = {canon.get(r[0], r[0]) for r in conn.execute(
        "SELECT DISTINCT company_id FROM evidence WHERE fact = 'sponsorship' AND locked = 1")}
    marks = ", ".join("?" for _ in ("unclear", *_SPONSORS))
    candidates: dict[int, list[tuple[str, int, str, str]]] = {}
    sponsors: set[int] = set()
    for row in conn.execute(
            "SELECT p.posting_id, p.company_id, p.board_id, p.title, p.first_seen_at, d.* FROM posting p "
            "JOIN decision d ON d.posting_id = p.posting_id AND d.ruleset_version = ? "
            f"WHERE p.closed_at IS NULL AND d.sponsorship IN ({marks}) ORDER BY p.posting_id",
            (version, "unclear", *_SPONSORS)):
        owner = canon.get(row["company_id"], row["company_id"])
        if owner not in in_scope or row["board_id"] in doubtful:
            continue
        decision = stored_decision(row)
        if not _visible(decision):
            continue
        if decision.sponsorship in _SPONSORS:
            sponsors.add(owner)
        elif row["posting_id"] not in pending:
            candidates.setdefault(owner, []).append(
                (row["first_seen_at"], row["posting_id"], row["title"], wording_key(decision.sponsorship_quote)))
    eligible = {owner: found for owner, found in candidates.items() if owner not in sponsors and owner not in locked}
    keys = {owner: {found[3] for found in rows} for owner, rows in eligible.items()}
    open_items: dict[int, list[sqlite3.Row]] = {}
    for item in conn.execute("SELECT review_id, company_id, evidence_key FROM review_item WHERE "
                             "kind = 'sponsorship_unclear' AND raised_by = 'rules' AND state = 'open' "
                             "ORDER BY review_id"):
        open_items.setdefault(item["company_id"], []).append(item)
    raised = cleared = 0
    with unit(conn):
        wanted = [o for o in sorted(eligible) if scope is None or o in scope]
        names = _names(conn, wanted)
        for owner in wanted:
            if any(item["evidence_key"] in keys[owner] for item in open_items.get(owner, ())):
                continue  # one item per company: it is already asked, on a wording that still stands
            _, posting_id, title, key = max(eligible[owner])
            if decided_before(conn, kind="sponsorship_unclear", company_id=owner, evidence_key=key) is not None:
                continue
            got = raise_item(conn, kind="sponsorship_unclear", company_id=owner, posting_id=posting_id,
                             reason=words.sponsorship_reason(names[owner], title), evidence_key=key,
                             raised_by="rules", now=now)
            raised += got.created or got.reopened
        for owner, items in open_items.items():
            if scope is not None and owner not in scope:
                continue
            if owner not in in_scope and not top:
                continue  # no ranked list to say the company left the top: leave it to the stale sweep
            for item in items:
                if item["evidence_key"] in keys.get(owner, ()):
                    continue
                cleared += clear_item(conn, item["review_id"], _UNCLEAR_NOTE, now)
    return SyncResult(raised, cleared)
