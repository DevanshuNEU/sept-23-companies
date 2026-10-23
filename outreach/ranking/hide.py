"""The hide policy: which companies Devanshu never sees, and the one no-go row ranking may write (#6, #9).

Hidden only with evidence. `outreach.rules.passes()` is the posting-level half and is called, never restated; this
module adds the company level, in the order of the plan's Decisions:

1. an active `no_go` row set after the latest lift of the company's group (merged duplicates included); else a
   lifted one cancels every hide below, whatever its reason (he put the company back on the list on purpose: it
   stays shown and ranked, and only a new active row hides it again);
2. size over `hide.size_over` with evidence of a trusted confidence (`size`, else `headcount`);
3. a sponsorship refusal: an open, live (not stale) engineering posting on a trusted board, not outside the US,
   whose decision says `sponsorship = 'no'`. It hides nothing when another open live trusted posting says `yes`
   or `opt_only` (ruling 9: the postings disagree). It is final when a refusal's decision is final
   (`provisional = 0`); else the company is "hidden, pending reader" and nothing is written.

A stale posting neither refuses nor sponsors (ruling 2026-10-22), and a posting on a doubtful board counts for
nothing (ruling 7). Unknown size is neutral: no evidence never hides.
"""
from __future__ import annotations

import dataclasses
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass

from outreach.crawl.stale import Stale
from outreach.ranking.common import clean_text
from outreach.ranking.config import RankerConfig
from outreach.rules import Decision, passes
from outreach.store import append_event, atomic, utcnow

__all__ = ["HIDE_KINDS", "posting_visible", "Refusal", "SizeEvidence", "HideInputs", "Hide", "load_hide_inputs",
           "decide_hide", "write_no_go"]

HIDE_KINDS = ("no_go", "over_size", "no_sponsorship")
_SPONSORS = ("yes", "opt_only")
_CONFIDENCE_RANK = {"confirmed": 0, "estimate": 1, "guess": 2}
_QUOTE_MAX = 200
# Decisions are joined by key under the current ruleset, not through the current_decision view, which is a scan.
_DECISION_QUERY = (
    "SELECT p.posting_id, p.company_id, p.board_id, d.lane, d.us, d.sponsorship, d.sponsorship_quote, "
    "d.provisional FROM posting p JOIN decision d ON d.posting_id = p.posting_id "
    "AND d.ruleset_version = (SELECT ruleset_version FROM ruleset WHERE is_current = 1) "
    "WHERE p.closed_at IS NULL AND d.sponsorship IN ('no', 'yes', 'opt_only') ORDER BY p.posting_id")


def posting_visible(d: Decision) -> bool:
    """Whether a posting can be shown: `passes()` for engineering; an intern posting goes through `passes()` as if
    it were engineering, because `passes()` drops interns and lane 2 shows them (design section 8)."""
    if d.lane == "engineering":
        return passes(d)
    if d.lane == "intern":
        return passes(dataclasses.replace(d, lane="engineering"))
    return False


@dataclass(frozen=True)
class Refusal:
    posting_id: int
    provisional: bool
    quote: str


@dataclass(frozen=True)
class SizeEvidence:
    low: int
    high: int | None
    confidence: str
    source_ref: str
    evidence_id: int
    locked: bool = False  # set by hand: a guess he locked is his own call, not a guess to review


@dataclass(frozen=True)
class HideInputs:
    company_id: int
    no_go: str | None  # 'active', 'lifted' or None
    no_go_reason: str
    refusals: tuple[Refusal, ...]
    sponsors: tuple[int, ...]  # open, live, trusted postings whose decision says yes or opt_only
    size: SizeEvidence | None

    @property
    def conflict(self) -> bool:
        """A refusal and a sponsoring posting at once: ruling 9 keeps the company shown and flags it."""
        return bool(self.refusals) and bool(self.sponsors)


@dataclass(frozen=True)
class Hide:
    kind: str
    reason: str
    provisional: bool
    posting_id: int | None = None
    quote: str = ""

    def __post_init__(self) -> None:
        if self.kind not in HIDE_KINDS:
            raise ValueError(f"hide kind must be one of {HIDE_KINDS}, got {self.kind!r}")

    def to_json(self) -> dict:
        return {"kind": self.kind, "reason": self.reason, "provisional": self.provisional}


def _one_line(text: str) -> str:
    line = clean_text(" ".join(text.split()))
    return line if len(line) <= _QUOTE_MAX else line[:_QUOTE_MAX - 3].rstrip() + "..."


def load_hide_inputs(conn: sqlite3.Connection, canon: dict[int, int], doubtful: frozenset[int],
                     stale: Mapping[int, Stale]) -> dict[int, HideInputs]:
    """The hide inputs of every surviving company, in three queries. Everything counts for `canon[company_id]`."""
    def owner(cid: int) -> int:
        return canon.get(cid, cid)

    refusals: dict[int, list[Refusal]] = {}
    sponsors: dict[int, list[int]] = {}
    for r in conn.execute(_DECISION_QUERY):
        if r["board_id"] in doubtful or r["posting_id"] in stale:
            continue  # an untrusted or stale posting is neither a refusal nor a sponsor
        cid = owner(r["company_id"])
        if r["sponsorship"] in _SPONSORS:
            sponsors.setdefault(cid, []).append(r["posting_id"])
        elif r["lane"] == "engineering" and r["us"] != "no":
            refusals.setdefault(cid, []).append(
                Refusal(r["posting_id"], bool(r["provisional"]), r["sponsorship_quote"]))

    # Per surviving company (its merged duplicates included): an active row counts only when it was set after the
    # group's latest lift, so a lift on one company is not undone by an older active row on a duplicate.
    lifted_at: dict[int, str] = {}
    actives: dict[int, list[tuple[str, int, str]]] = {}
    for r in conn.execute("SELECT no_go_id, company_id, reason, created_at, lifted_at FROM no_go "
                          "WHERE company_id IS NOT NULL"):
        cid = owner(r["company_id"])
        if r["lifted_at"] is not None:
            lifted_at[cid] = max(lifted_at.get(cid, ""), r["lifted_at"])
        else:
            actives.setdefault(cid, []).append((r["created_at"], r["no_go_id"], r["reason"]))
    no_go: dict[int, tuple[str, str]] = {}
    for cid in set(lifted_at) | set(actives):
        later = [a for a in actives.get(cid, ()) if cid not in lifted_at or a[0] > lifted_at[cid]]
        no_go[cid] = ("active", max(later)[2]) if later else ("lifted", "")

    size: dict[int, tuple[tuple, SizeEvidence]] = {}
    for r in conn.execute("SELECT evidence_id, company_id, fact, num_low, num_high, confidence, locked, source_ref, "
                          "recorded_at FROM company_fact WHERE fact IN ('size', 'headcount')"):
        cid = owner(r["company_id"])
        # size over headcount, then the company_fact order (hand-locked newest, confidence, newest) across the
        # companies merged into one
        key = (r["fact"] == "size", r["locked"], r["recorded_at"] if r["locked"] else "",
               -_CONFIDENCE_RANK.get(r["confidence"], 3), r["recorded_at"], r["evidence_id"])
        if cid not in size or key > size[cid][0]:
            size[cid] = (key, SizeEvidence(r["num_low"], r["num_high"], r["confidence"], r["source_ref"],
                                           r["evidence_id"], bool(r["locked"])))

    ids = set(canon.values()) | set(refusals) | set(sponsors) | set(no_go) | set(size)
    out: dict[int, HideInputs] = {}
    for cid in sorted(ids):
        state, reason = no_go.get(cid, (None, ""))
        out[cid] = HideInputs(cid, state, reason, tuple(refusals.get(cid, ())), tuple(sponsors.get(cid, ())),
                              size[cid][1] if cid in size else None)
    return out


def decide_hide(inputs: HideInputs, cfg: RankerConfig) -> Hide | None:
    """The company's hide, or None to show it. Pure."""
    if inputs.no_go == "active":
        return Hide("no_go", f"On the no-go list: {inputs.no_go_reason.replace('_', ' ')}", False)
    if inputs.no_go == "lifted":
        return None  # lifted by hand: never hidden by a computed rule (controller ruling)
    over = cfg.hide["size_over"]
    s = inputs.size
    if s is not None and s.confidence in cfg.hide["size_confidences"] and s.low > over:
        span = f"{s.low} to {s.high}" if s.high is not None else f"{s.low} or more"
        source = f", {s.source_ref}" if s.source_ref else ""
        return Hide("over_size", _one_line(f"Over {over} people: {span} ({s.confidence} evidence{source})"), False)
    if not inputs.refusals or inputs.sponsors:
        return None
    final = [r for r in inputs.refusals if not r.provisional]
    chosen = final[0] if final else inputs.refusals[0]
    quote = _one_line(chosen.quote)
    if final:
        reason = f'A US engineering posting says it does not sponsor visas: "{quote}"'
    else:
        reason = f'hidden, pending reader: a US engineering posting may not sponsor visas: "{quote}"'
    return Hide("no_sponsorship", reason, not final, posting_id=chosen.posting_id, quote=chosen.quote)


def write_no_go(conn: sqlite3.Connection, company_id: int, hide: Hide) -> bool:
    """Write the `no_go` row for a final sponsorship refusal and its event; return whether a row was written.

    Nothing is written for any other hide, or when the company (or a company merged into it) already has a no-go
    row of any kind, lifted or not: a lifted row means he took the company off the list, so it is never re-added.
    Joins the caller's transaction when there is one.
    """
    if hide.kind != "no_sponsorship" or hide.provisional or hide.posting_id is None:
        return False
    with atomic(conn, "rank_no_go"):
        exists = conn.execute(
            "WITH RECURSIVE grp(id) AS (SELECT ? UNION SELECT c.company_id FROM company c JOIN grp "
            "ON c.merged_into = grp.id) SELECT 1 FROM no_go WHERE company_id IN (SELECT id FROM grp) LIMIT 1",
            (company_id,)).fetchone()
        if exists is not None:
            return False
        cur = conn.execute(
            "INSERT INTO no_go(company_id, reason, quote, source, source_ref, created_at) "
            "VALUES (?, 'no_sponsorship', ?, 'rules', ?, ?) ON CONFLICT DO NOTHING",
            (company_id, hide.quote, str(hide.posting_id), utcnow()))
        if not cur.rowcount:
            return False
        append_event(conn, entity="no_go", kind="added", actor="rules", entity_id=cur.lastrowid,
                     company_id=company_id, detail={"reason": "no_sponsorship", "posting_id": hide.posting_id})
    return True
