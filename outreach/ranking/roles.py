"""Visible postings, the best posting and the role lanes per company (#6, design section 8, ruling 2026-10-22).

A visible posting is open, decided under the current ruleset, on a trusted board (not doubtful, not inactive), not
stale, and `posting_visible` accepts it. It earns a lane: `level_role` (engineering, level ok), `intern_junior` (an
intern posting that is not student-only) or `any_us_role` (engineering, level not ok). Task 5 decides which earned
lanes a company holds.

A stale posting (from `outreach.crawl.stale.load_stale`, never re-derived here) that would be visible goes to
`Roles.stale` only: no lane, never the best posting, not in `visible` and not in `open_count`. A stale posting that
would be hidden is listed nowhere, and one on a doubtful board counts as ignored.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from outreach.crawl.stale import Stale
from outreach.ranking.common import age_days, clean_text
from outreach.ranking.confidence import Confidence, grade_no_role, grade_role
from outreach.ranking.hide import posting_visible
from outreach.rules.rebuild import stored_decision

__all__ = ["LANE_ORDER", "Best", "Roles", "role_sort_key", "newest_stale", "load_roles", "company_board_states"]

LANE_ORDER = ("level_role", "intern_junior", "any_us_role")
_LANE_RANK = {lane: i for i, lane in enumerate(LANE_ORDER)}
_SPONSOR_RANK = {"yes": 0, "opt_only": 1}


@dataclass(frozen=True)
class Best:
    posting_id: int
    lane: str           # level_role, intern_junior or any_us_role
    title: str          # dashes cleaned, so a reason can quote it
    level_tier: str
    sponsorship: str
    age_days: int | None
    pay: str
    location: str
    posted_on: str | None
    last_seen_on: str
    board_state: str    # read_ok, read_empty, read_failed, never_read or no_board
    provisional: bool   # the decision's flag
    stale_reason: str = ""  # empty for a live posting
    level_basis: str = ""   # the decision's level basis, dashes cleaned


@dataclass(frozen=True)
class Roles:
    company_id: int
    lanes: tuple[str, ...]      # the lanes the visible postings earn, strongest first
    best: Best | None
    visible: tuple[Best, ...]   # every visible live posting, best first
    stale: tuple[Best, ...]     # would be visible but stale, same order, shown last
    open_count: int             # trusted open postings that are not stale
    confidence: Confidence      # the live_role grade
    ignored: int                # open postings on untrusted boards: doubtful or inactive
    undecided: int              # trusted open postings with no current decision
    inactive: int = 0           # the part of `ignored` on inactive boards (the crawler no longer reads them)


def role_sort_key(b: Best) -> tuple:
    """Lane, then freshest (unknown age last), then a sponsorship of yes, opt_only, the rest, then the newest id."""
    return (_LANE_RANK[b.lane], b.age_days is None, b.age_days or 0, _SPONSOR_RANK.get(b.sponsorship, 2),
            -b.posting_id)


def newest_stale(stale: tuple[Best, ...]) -> Best | None:
    """The newest stale posting by age (unknown age last, then the newest id), the one a reason or flag names."""
    return min(stale, key=lambda b: (b.age_days is None, b.age_days or 0, -b.posting_id), default=None)


def _board_state(last_read: str | None, has_board: bool) -> str:
    """A board's state for the grades. An `empty` read is a good read that listed nothing (ruling 2026-10-22)."""
    if not has_board:
        return "no_board"
    if last_read in ("ok", "unchanged"):
        return "read_ok"
    if last_read == "empty":
        return "read_empty"
    if last_read == "error":
        return "read_failed"
    return "never_read"


def _lane(d) -> str:
    if d.lane == "intern":
        return "intern_junior"
    return "level_role" if d.level_ok else "any_us_role"


def company_board_states(conn: sqlite3.Connection, canon: dict[int, int], doubtful: frozenset[int]) -> dict[int, str]:
    """Per surviving company with an active board, from its trusted boards: `read_ok` when any last read ok or
    unchanged, else `read_empty` when any read empty (a good read that listed nothing), else `read_failed` when any
    errored, else `never_read`. A company whose active boards are all doubtful is `unconfirmed`; one with no active
    board is absent (`no_board`)."""
    rank = {"read_ok": 0, "read_empty": 1, "read_failed": 2, "never_read": 3, "unconfirmed": 4}
    out: dict[int, str] = {}
    for board_id, company_id, last_read in conn.execute(
            "SELECT board_id, company_id, last_read FROM board WHERE active = 1"):
        cid = canon.get(company_id, company_id)
        state = "unconfirmed" if board_id in doubtful else _board_state(last_read, True)
        if cid not in out or rank[state] < rank[out[cid]]:
            out[cid] = state
    return out


_DECISION_COLUMNS = ("lane", "level_tier", "level_ok", "level_basis", "years_asked", "years_quote", "us",
                     "sponsorship", "sponsorship_quote", "citizenship", "citizenship_quote", "eligibility",
                     "eligibility_reason", "eligibility_quote", "start_2027")
_QUERY = (
    "SELECT p.posting_id, p.company_id, p.board_id, p.title, p.location, p.pay, p.posted_on, p.first_seen_at, "
    "p.last_seen_at, b.board_id IS NOT NULL AS has_board, b.last_read, b.active, d.posting_id IS NOT NULL AS decided, "
    "d.provisional, " + ", ".join(f"d.{c}" for c in _DECISION_COLUMNS) + " "
    # Looked up by key under the current ruleset: a join through the current_decision view is scanned once per posting.
    "FROM posting p LEFT JOIN decision d ON d.posting_id = p.posting_id "
    "AND d.ruleset_version = (SELECT ruleset_version FROM ruleset WHERE is_current = 1) "
    "LEFT JOIN board b ON b.board_id = p.board_id WHERE p.closed_at IS NULL ORDER BY p.posting_id")


def load_roles(conn: sqlite3.Connection, canon: dict[int, int], doubtful: frozenset[int], today: date,
               stale: Mapping[int, Stale]) -> dict[int, Roles]:
    """Roles for every surviving company with at least one open posting, from one query over open postings (plus
    one over boards for the no-role grade). A company with no open posting has no entry."""
    groups: dict[int, dict] = {}
    for r in conn.execute(_QUERY):
        cid = canon.get(r["company_id"], r["company_id"])
        g = groups.setdefault(cid, {"visible": [], "stale": [], "open": 0, "ignored": 0, "inactive": 0,
                                    "undecided": 0})
        if r["board_id"] in doubtful:
            g["ignored"] += 1
            g["inactive"] += 0 if r["active"] else 1
            continue
        pid = r["posting_id"]
        is_stale = pid in stale
        if not is_stale:
            g["open"] += 1
        if not r["decided"]:
            g["undecided"] += 1
            continue
        d = stored_decision(r)
        if not posting_visible(d):
            continue
        best = Best(
            posting_id=pid, lane=_lane(d), title=clean_text(" ".join(r["title"].split())), level_tier=d.level_tier,
            sponsorship=d.sponsorship, age_days=age_days(r["posted_on"] or r["first_seen_at"], today),
            pay=r["pay"], location=r["location"], posted_on=r["posted_on"], last_seen_on=r["last_seen_at"][:10],
            board_state=_board_state(r["last_read"], bool(r["has_board"])), provisional=bool(r["provisional"]),
            stale_reason=stale[pid].reason if is_stale else "", level_basis=clean_text(d.level_basis or ""))
        g["stale" if is_stale else "visible"].append(best)

    states = company_board_states(conn, canon, doubtful) if groups else {}
    out: dict[int, Roles] = {}
    for cid, g in groups.items():
        visible = tuple(sorted(g["visible"], key=role_sort_key))
        stale_list = tuple(sorted(g["stale"], key=role_sort_key))
        best = visible[0] if visible else None
        if best is not None:
            grade = grade_role(board_state=best.board_state, posted_known=best.posted_on is not None,
                               last_seen_on=best.last_seen_on)
        elif g["undecided"]:  # an open posting nobody has decided might be a live role: roles are unknown
            n = g["undecided"]
            grade = Confidence("low", f"{n} open {'posting' if n == 1 else 'postings'} not decided yet, roles unknown")
        else:
            newest = newest_stale(stale_list)
            grade = grade_no_role(company_board_state=states.get(cid, "no_board"),
                                  stale_kinds=stale[newest.posting_id].kinds if newest else (),
                                  stale_reason=newest.stale_reason if newest else "")
        lanes = tuple(lane for lane in LANE_ORDER if any(b.lane == lane for b in visible))
        out[cid] = Roles(company_id=cid, lanes=lanes, best=best, visible=visible, stale=stale_list,
                         open_count=g["open"], confidence=grade, ignored=g["ignored"], undecided=g["undecided"],
                         inactive=g["inactive"])
    return out
