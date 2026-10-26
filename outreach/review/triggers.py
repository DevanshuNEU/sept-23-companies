"""The sweep that raises the Needs-you items nothing else raises (#16).

Triggers 4 (size unknown), 5 (missing email domain) and 3 (unclear sponsorship wording) are here, with the
clearing of items whose subject went away and `sweep_needs_you`, which runs them all and writes the heartbeat.
Every sync reads stored data, so it is idempotent and cheap, and it can run for a few companies right after a pick.
A sync clears only the items of its own `(kind, raised_by)`, so another producer's item is never touched.
"""
from __future__ import annotations

import dataclasses
import hashlib
import re
import sqlite3
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from outreach.ranking.common import canonical_ids
from outreach.reader.version import is_side_sql
from outreach.review import words
from outreach.review.config import ReviewConfig, load_review_config
from outreach.review.items import clear_item, decided_before, raise_item, unit
from outreach.review.merge import follow_merge_questions
from outreach.review.play import emailing, in_play, live_company_ids, ny_date, recent_list_date
from outreach.review.scope import clear_out_of_scope, ranked_ids
from outreach.rules import Decision, passes, ruleset_version
from outreach.rules.rebuild import stored_decision
from outreach.sources.keys import registrable_domain
from outreach.store import append_event, is_shared_key
from outreach.store.timefmt import ISO_FMT, iso_utc, utcnow

__all__ = ["SyncResult", "sync_size_unknown", "sync_domain_missing", "sync_sponsorship", "clear_gone",
           "clear_stale", "SweepReport", "sweep_needs_you"]


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
    return set(ranked_ids(conn, top_n))


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
    sponsorship fact, and a company with another shown posting that reads `yes` or `opt_only`. A side session's queue
    rows (`is_side_sql`) do not count as the reader having a posting. Skipped, with the
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
    # Only the night reader's rows: a side session (#13) queues its own and reads a few, so its rows never mean
    # "the reader has it".
    pending = {r[0] for r in conn.execute("SELECT DISTINCT posting_id FROM read_queue "
                                          f"WHERE state IN ('queued', 'leased') AND NOT ({is_side_sql()})")}
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


# Why an item cleared itself, for the three clearing rules that are not a sync's.
_GONE_NOTES = {
    "merged": "This company was merged into another one.",
    "removed": "This company was removed.",
    "closed": "This company is closed.",
    "no_go": "This company is on your no-go list.",
    "board": "The crawler no longer reads this board.",
    "posting": "The posting is closed.",
    "merge": "One of these companies is gone.",
}
_GONE_QUERIES = (
    ("merged", "SELECT r.review_id, r.kind, r.company_id FROM review_item r JOIN company c "
               "ON c.company_id = r.company_id WHERE r.state = 'open' AND c.merged_into IS NOT NULL"),
    ("removed", "SELECT r.review_id, r.kind, r.company_id FROM review_item r JOIN company c "
                "ON c.company_id = r.company_id WHERE r.state = 'open' AND c.status = 'removed'"),
    ("closed", "SELECT r.review_id, r.kind, r.company_id FROM review_item r JOIN company c "
               "ON c.company_id = r.company_id WHERE r.state = 'open' AND c.status = 'closed'"),
    ("no_go", "SELECT r.review_id, r.kind, r.company_id FROM review_item r WHERE r.state = 'open' AND EXISTS ("
              "SELECT 1 FROM no_go n WHERE n.company_id = r.company_id AND n.lifted_at IS NULL)"),
    ("board", "SELECT r.review_id, r.kind, r.company_id FROM review_item r JOIN board b ON b.board_id = r.board_id "
              "WHERE r.state = 'open' AND r.kind IN ('board_unreadable', 'board_empty') AND b.active = 0"),
    ("posting", "SELECT r.review_id, r.kind, r.company_id FROM review_item r JOIN posting p "
                "ON p.posting_id = r.posting_id WHERE r.state = 'open' AND r.kind = 'reader_disagrees' "
                "AND p.closed_at IS NOT NULL"),
    ("merge", "SELECT r.review_id, r.kind, r.company_id FROM review_item r WHERE r.state = 'open' "
              "AND r.kind = 'merge_candidate' AND EXISTS (SELECT 1 FROM company o "
              "WHERE o.company_id = CAST(r.evidence_key AS INTEGER) AND (o.merged_into IS NOT NULL "
              "OR o.status = 'removed'))"),
)
# The producers this sweep answers for, and the kind each raises: only these go stale.
_STALE_PRODUCERS = (("size_unknown", "daily_list"), ("domain_missing", "sender"), ("sponsorship_unclear", "rules"))
_STALE_NOTE = "No longer in your plan."


# The reason #31's `raise_merge_candidate` writes for a pair joined only by a domain (basis `domain`, or
# `fein_conflict+domain`): "... Basis domain, confidence strong: both hold the domain acme.io".
_DOMAIN_BASIS = re.compile(r"Basis (?:fein_conflict\+)?domain, confidence \w+: both hold the domain (\S+?)\.?$")


def _shared_domain(conn: sqlite3.Connection, domain: str) -> bool:
    """The domain, or its registrable root, is a shared key (identity's own test)."""
    root = registrable_domain(domain)
    return is_shared_key(conn, "domain", domain) or (root is not None and is_shared_key(conn, "domain", root))


def _gone(conn: sqlite3.Connection, now: str, scope: set[int] | None, raised: Counter | None = None) -> Counter:
    """Clear every open item whose subject went away. Returns the count by kind; the merge questions it moved to a
    keeper are added to `raised`.

    A merge question with a merged side is answered first, by the same rule a merge uses
    (`follow_merge_questions`): one that still asks a real pair moves to the company it was merged into, so the
    `merged` and `merge` rules below only see questions that rule left alone."""
    by_kind: Counter = Counter()
    with unit(conn):
        moved_from, moved_to = follow_merge_questions(conn, at=now, scope=scope)
    by_kind["merge_candidate"] += len(moved_from)
    if raised is not None and moved_to:
        raised["merge_candidate"] += len(moved_to)
    found: dict[int, tuple[str, str]] = {}
    for why, sql in _GONE_QUERIES:
        for row in conn.execute(sql + " ORDER BY r.review_id"):
            if scope is None or row["company_id"] in scope:
                found.setdefault(row["review_id"], (row["kind"], _GONE_NOTES[why]))
    for row in conn.execute("SELECT review_id, company_id, reason FROM review_item WHERE state = 'open' "
                            "AND kind = 'merge_candidate' ORDER BY review_id"):
        m = _DOMAIN_BASIS.search(row["reason"])
        if m and (scope is None or row["company_id"] in scope) and _shared_domain(conn, m.group(1)):
            found.setdefault(row["review_id"], ("merge_candidate", words.shared_domain_note(m.group(1))))
    with unit(conn):
        for review_id in sorted(found):
            kind, note = found[review_id]
            if clear_item(conn, review_id, note, now):
                by_kind[kind] += 1
    return by_kind


def clear_gone(conn: sqlite3.Connection, *, now: str, company_ids: Collection[int] | None = None) -> int:
    """Clear (with a note saying why) every open item of any kind whose company is merged, removed, closed or on the
    no-go list; a board item whose board is inactive; a `reader_disagrees` item whose posting is closed; and a
    `merge_candidate` item where either company is removed (a question with a merged side that still asks a real
    pair moves to the company it was merged into instead), or whose only basis is a domain that has since
    become a shared key. A quiet item is never cleared for being quiet.
    Returns how many it cleared."""
    return sum(_gone(conn, now, _scope(company_ids)).values())


def _stale(conn: sqlite3.Connection, now: str, cfg: ReviewConfig,
           scope: set[int] | None) -> tuple[Counter | None, list[str]]:
    """Clear this plan's items that are old and out of play. Returns the counts by kind and the steps it held back.

    The counts are None when it did not run for want of a recent list. Old sponsorship items are held (nothing
    cleared for them, one note in the second value) when the ranked list is empty: the top-N is then unknown, and
    "not in the top" read from missing input is not a reason to clear."""
    today = ny_date(now)
    if recent_list_date(conn, today, cfg.planned_days) is None:
        return None, []
    cutoff = (datetime.fromisoformat(now.replace("Z", "+00:00")) - timedelta(days=cfg.stale_days)
              ).astimezone(timezone.utc).strftime(ISO_FMT)
    pairs = " OR ".join("(kind = ? AND raised_by = ?)" for _ in _STALE_PRODUCERS)
    args = [v for pair in _STALE_PRODUCERS for v in pair]
    old = conn.execute(f"SELECT review_id, kind, company_id FROM review_item WHERE state = 'open' "
                       f"AND created_at <= ? AND ({pairs}) ORDER BY review_id", (cutoff, *args)).fetchall()
    old = [r for r in old if scope is None or r["company_id"] in scope]
    by_kind: Counter = Counter()
    notes: list[str] = []
    if not old:
        return by_kind, notes
    play = in_play(conn, today=today, planned_days=cfg.planned_days)
    mailing = emailing(conn, today=today, planned_days=cfg.planned_days)
    top = _top_company_ids(conn, cfg.sponsorship_top_n) if any(
        r["kind"] == "sponsorship_unclear" for r in old) else set()
    no_ranking = not top and any(r["kind"] == "sponsorship_unclear" for r in old)
    with unit(conn):
        for row in old:
            cid, kind = row["company_id"], row["kind"]
            if kind == "sponsorship_unclear" and no_ranking:
                continue
            held = {"size_unknown": cid in play, "domain_missing": cid in mailing,
                    "sponsorship_unclear": cid in play or cid in top}[kind]
            if not held and clear_item(conn, row["review_id"], _STALE_NOTE, now):
                by_kind[kind] += 1
    if no_ranking:
        notes.append(f"sponsorship items: no ranked list, so none was cleared for leaving the top "
                     f"{cfg.sponsorship_top_n}")
    return by_kind, notes


def clear_stale(conn: sqlite3.Connection, *, now: str, cfg: ReviewConfig | None = None,
                company_ids: Collection[int] | None = None) -> int:
    """Clear this plan's open items (`rules`, `daily_list`, `sender`) older than `stale_days` whose company is out of
    play (a domain item: not being emailed; a sponsorship item: also not in the top-N, and never while the ranked
    list is empty), with the note `No longer in your plan.` It runs only when a daily list exists within `planned_days`: with none, a failed plan job would clear
    everything, so it clears nothing and returns 0 (`sweep_needs_you` says so in its report). Returns how many it
    cleared."""
    got, _ = _stale(conn, iso_utc(now), cfg or load_review_config(), _scope(company_ids))
    return 0 if got is None else sum(got.values())


@dataclass(frozen=True)
class SweepReport:
    raised: dict[str, int] = field(default_factory=dict)  # item kind to how many were raised
    cleared: dict[str, int] = field(default_factory=dict)  # item kind to how many cleared themselves
    skipped: tuple[str, ...] = ()  # each a plain sentence for a step that could not run, and why

    @property
    def changed(self) -> bool:
        return any(self.raised.values()) or any(self.cleared.values())


def _heartbeat_key(at: str) -> str:
    """`sweep:<YYYY-MM-DDTHH>:<00 or 30>` for the UTC half hour holding `at`."""
    return f"sweep:{at[:13]}:{'00' if int(at[14:16]) < 30 else '30'}"


def sweep_needs_you(conn: sqlite3.Connection, *, now: str | None = None, company_ids: Collection[int] | None = None,
                    cfg: ReviewConfig | None = None) -> SweepReport:
    """Raise and clear every Needs-you item that has a sweep behind it: clear what went away, trigger 4, trigger 5,
    trigger 3, clear what is stale, then clear the crawler's out-of-scope gaps (`scope.clear_out_of_scope`), each in
    its own write unit. Idempotent and cheap. `company_ids` limits
    every step to those companies (right after a pick or a person is added).

    A full run (no `company_ids`) then appends one heartbeat event, `review_item` / `sweep_run` by `system`, whether
    or not anything changed, with the key `sweep:<YYYY-MM-DDTHH>:<00 or 30>` so there is at most one per half hour.
    Health shows "Needs-you sweep: last run" from the newest. A step that raises stops the sweep before the
    heartbeat, so a sweep that keeps failing shows as one that has not run.
    """
    cfg = cfg or load_review_config()
    at = iso_utc(now) if now else utcnow()
    raised: Counter = Counter()
    cleared: Counter = Counter()
    skipped: list[str] = []
    cleared.update(_gone(conn, at, _scope(company_ids), raised))
    for kind, sync in (("size_unknown", sync_size_unknown), ("domain_missing", sync_domain_missing),
                       ("sponsorship_unclear", sync_sponsorship)):
        result = sync(conn, now=at, company_ids=company_ids, cfg=cfg)
        raised[kind] += result.raised
        cleared[kind] += result.cleared
        if result.skipped:
            skipped.append(f"{kind}: {result.skipped}")
    stale, held = _stale(conn, at, cfg, _scope(company_ids))
    skipped.extend(held)
    if stale is None:
        skipped.append("stale items: no recent daily list, so nothing was cleared for being out of the plan")
    else:
        cleared.update(stale)
    gaps = clear_out_of_scope(conn, now=at, cfg=cfg, company_ids=company_ids)
    if gaps is None:
        skipped.append(f"crawler domain items: no ranked list, so none was cleared for leaving the top "
                       f"{cfg.crawler_scope_top}")
    else:
        cleared["domain_missing"] += gaps
    report = SweepReport({k: n for k, n in raised.items() if n}, {k: n for k, n in cleared.items() if n},
                         tuple(skipped))
    if company_ids is None:
        with unit(conn):
            append_event(conn, entity="review_item", kind="sweep_run", actor="system", at=at,
                         detail={"raised": report.raised, "cleared": report.cleared, "skipped": list(report.skipped)},
                         idempotency_key=_heartbeat_key(at))
    return report
