"""The review items the ranker raises: size_guess, size_check and the sponsorship conflict (#6, Task 10).

An item asks Devanshu to look at something; it never changes a score, a hide or the list. Each is raised with
`ON CONFLICT DO NOTHING` on the review item's unique index, so a resolved item stays resolved until its evidence key
changes. An open item whose condition has gone away is resolved `cleared`; a `cleared` item whose condition returns
opens again (an item he resolved himself never does). Items of other raisers are never touched.
"""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Mapping

from outreach.ranking.config import RankerConfig
from outreach.ranking.confidence import scrub_text
from outreach.ranking.facts import SHARED_DOMAIN_FLAG, RankFacts
from outreach.ranking.hide import HideInputs, SizeEvidence
from outreach.ranking.score import Scored, list_order
from outreach.store import atomic, utcnow

__all__ = ["CONFLICT_PREFIX", "sync_size_items", "sync_conflict_items"]

CONFLICT_PREFIX = "sponsorship-conflict:"
_SIZE_KINDS = ("size_guess", "size_check")
# The weak-flag texts facts.py writes carry live counts ("90 open roles"), so an item is keyed on the kind of flag.
_FLAG_KINDS = ((re.compile(r"\d+ open roles\Z"), "many_open_roles"),
               (re.compile(r"\d+ software H-1B filings in one fiscal year\Z"), "many_software_lca"),
               (re.compile(r"later stage, size unknown\Z"), "later_stage_no_size"))


def flag_kind(text: str) -> str:
    """The stable name of a weak flag. A text no rule knows keeps itself, digits as N, so it still keys one item."""
    for pattern, kind in _FLAG_KINDS:
        if pattern.match(text):
            return kind
    return "other:" + scrub_text(re.sub(r"\d+", "N", text))
# (kind, company_id, evidence_key) -> (posting_id, reason)
_Wanted = dict[tuple[str, int, str], tuple[int | None, str]]


def _sync(conn: sqlite3.Connection, scope_sql: str, scope_args: tuple, wanted: _Wanted, now: str) -> tuple[int, int]:
    """Makes the ranker's open items in scope match `wanted`; returns (raised, cleared)."""
    raised = cleared = 0
    with atomic(conn, "rank_review_items"):
        stale = [r["review_id"] for r in conn.execute(
            "SELECT review_id, kind, company_id, evidence_key FROM review_item "
            f"WHERE raised_by = 'ranker' AND state = 'open' AND {scope_sql}", scope_args)
            if (r["kind"], r["company_id"], r["evidence_key"]) not in wanted]
        for review_id in stale:
            conn.execute("UPDATE review_item SET state = 'resolved', resolution = 'cleared', resolved_at = ?, "
                         "revision = revision + 1 WHERE review_id = ?", (now, review_id))
            cleared += 1
        for (kind, company_id, key), (posting_id, reason) in sorted(wanted.items()):
            made = conn.execute(
                "INSERT INTO review_item(kind, company_id, posting_id, reason, evidence_key, raised_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'ranker', ?) ON CONFLICT DO NOTHING",
                (kind, company_id, posting_id, reason, key, now)).rowcount
            if made:
                raised += 1
                continue
            where = "kind = ? AND company_id = ? AND evidence_key = ? AND raised_by = 'ranker'"
            reopened = conn.execute(
                "UPDATE review_item SET state = 'open', resolution = NULL, resolved_at = NULL, reason = ?, "
                f"revision = revision + 1 WHERE {where} AND state = 'resolved' AND resolution = 'cleared'",
                (reason, kind, company_id, key)).rowcount
            raised += reopened
            if not reopened:  # still open: keep its reason current
                conn.execute("UPDATE review_item SET reason = ?, revision = revision + 1 "
                             f"WHERE {where} AND state = 'open' AND reason <> ?", (reason, kind, company_id, key, reason))
    return raised, cleared


def _span(s: SizeEvidence) -> str:
    return f"{s.low} to {s.high}" if s.high is not None else f"{s.low} or more"


def sync_size_items(conn: sqlite3.Connection, scored: list[Scored], facts_by_company: Mapping[int, RankFacts],
                    sizes: Mapping[int, SizeEvidence], cfg: RankerConfig, *,
                    now: str | None = None) -> tuple[int, int]:
    """Raises `size_guess` and `size_check` items, clears the ones that no longer hold; returns (raised, cleared).

    `size_guess`: a listed company whose winning size evidence is a guess over the hide size (wherever it ranks; the
    guess hides nothing). `size_check`: a company in the top `size_check_top_n` of the ranked list with a weak
    "not small" flag other than the shared domain one (#31's `resolve` raises that one, ruling 11), and only while
    its size is unknown (a known size needs no check). Evidence keys: the evidence id, and the sorted flag kinds
    joined by `;`; the live counts stay in the reason, which is kept current while the item is open.
    """
    now = now or utcnow()
    wanted: _Wanted = {}
    over = cfg.hide["size_over"]
    for s in scored:
        size = sizes.get(s.company_id)
        if (s.hide is None and size is not None and size.confidence == "guess" and not size.locked
                and size.low > over):
            reason = scrub_text(f"Size is only a guess ({_span(size)} people, over {over}). Look it up. "
                                "The company stays visible.")
            wanted[("size_guess", s.company_id, str(size.evidence_id))] = (None, reason)
    for s in list_order(scored)[:cfg.ranker["size_check_top_n"]]:
        facts = facts_by_company.get(s.company_id)
        if facts is None or facts.size_low is not None:
            continue
        flags = [f for f in facts.weak_flags if f != SHARED_DOMAIN_FLAG]
        if flags:
            reason = scrub_text(f"Check the size: {'; '.join(flags)}. Ranking treats it as not small until you "
                                "confirm. The company stays visible.")
            key = ";".join(sorted({flag_kind(f) for f in flags}))
            wanted[("size_check", s.company_id, key)] = (None, reason)
    marks = ", ".join("?" * len(_SIZE_KINDS))
    return _sync(conn, f"kind IN ({marks})", _SIZE_KINDS, wanted, now)


def _postings(ids: list[int], one: str, many: str) -> str:
    if len(ids) == 1:
        return f"posting {ids[0]} {one}"
    return f"postings {', '.join(str(i) for i in ids[:-1])} and {ids[-1]} {many}"


def sync_conflict_items(conn: sqlite3.Connection, inputs: Mapping[int, HideInputs], scored: Iterable[Scored],
                        cfg: RankerConfig, *, now: str | None = None) -> tuple[int, int]:
    """One `flagged` item per scored, listed company whose postings disagree on sponsorship (ruling 9); returns
    (raised, cleared). A company that is not scored (closed, removed, merged away) or is hidden (a no-go, say)
    gets none, and an open item of one is cleared.

    The key is `sponsorship-conflict:` and the sorted refusal posting ids; the item points at the first refusal. It
    names every posting that disagrees and hides nothing.
    """
    now = now or utcnow()
    wanted: _Wanted = {}
    listed = {s.company_id for s in scored if s.hide is None}
    for company_id, hi in inputs.items():
        if company_id not in listed or not hi.conflict:
            continue
        refusals = sorted(r.posting_id for r in hi.refusals)
        sponsors = sorted(hi.sponsors)
        reason = (f"Postings disagree on sponsorship: {_postings(refusals, 'refuses', 'refuse')} and "
                  f"{_postings(sponsors, 'says it sponsors', 'say they sponsor')}. The company stays visible.")
        key = CONFLICT_PREFIX + ",".join(str(i) for i in refusals)
        wanted[("flagged", company_id, key)] = (refusals[0], scrub_text(reason))
    return _sync(conn, "kind = 'flagged' AND substr(evidence_key, 1, ?) = ?", (len(CONFLICT_PREFIX), CONFLICT_PREFIX),
                 wanted, now)
