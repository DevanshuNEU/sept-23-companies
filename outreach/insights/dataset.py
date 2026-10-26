"""The outcome dataset (#81): one row per canonical company he reached, with its best outcome and its facts.

The universe is every canonical company with a live touch (`undone_at IS NULL`), an `application` or
`close_reason = 'interview'`, whatever its status. Everything a merged-away duplicate holds counts for its survivor,
so "gm" merged into "General Motors" is one row that keeps both ids.

Best outcome, best first: offer, interview, referred, replied, accepted, none. The touch that carries it is the
earliest touch at that level (its channel, role kind and lane are the row's); an outcome from an application only has
channel `application`, and one from the company status only (closed as interviewed) has no channel. With no outcome
the earliest touch (else the first application) still names the way in that was tried.

Facts are as known now, not at the time: the winning `company_fact` row across the merged family, with its
confidence. The level of the role comes, in order, from the carrying touch's posting, an application's posting, then
the newest engineering or intern posting of the family that was open when the touch was sent.

Every table is read once for all companies (`json_each(?)` for id lists), never once per company. Nothing is written.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from outreach.insights.bands import Bands, Features, default_bands, features_of
from outreach.outcomes.windows import day_bounds_utc, ny_day
from outreach.ranking.common import canonical_ids, fact_order_key, size_order_key
from outreach.ranking.scores import latest_version, load_scores, ranked_list
from outreach.store.timefmt import ISO_FMT

__all__ = ["OUTCOME_ORDER", "INTERVIEW_OUTCOMES", "OutcomeRow", "load_board_systems", "load_outcome_rows",
           "interview_rows"]

OUTCOME_ORDER = ("none", "accepted", "replied", "referred", "interview", "offer")
INTERVIEW_OUTCOMES = ("interview", "offer")
_LEVEL = {name: i for i, name in enumerate(OUTCOME_ORDER)}
# A touch's outcome columns, best first.
_TOUCH_STAMPS = (("offer", "offer_at"), ("interview", "interview_at"), ("referred", "referred_at"),
                 ("replied", "replied_at"), ("accepted", "accepted_at"))
_APPLICATION_OUTCOME = {"interview": "interview", "offer": "offer"}
_SOURCE_ORDER = {"touch": 0, "application": 1, "company_status": 2}  # the first wins a tie at one level

_TOUCHES = """
SELECT t.touch_id, t.company_id, t.channel, t.sent_at, t.accepted_at, t.replied_at, t.reply_kind,
       t.referred_at, t.interview_at, t.offer_at, t.posting_id,
       json_extract(t.context, '$.lane') AS lane, json_extract(t.context, '$.roleKind') AS role_kind,
       p.name AS person_name, (p.email IS NOT NULL OR p.linkedin_url IS NOT NULL) AS person_known,
       d.lane AS posting_lane, d.level_tier AS posting_level
FROM outreach_touch t
LEFT JOIN person p ON p.person_id = t.person_id
LEFT JOIN posting_decision d ON d.posting_id = t.posting_id
WHERE t.undone_at IS NULL
ORDER BY t.sent_at, t.touch_id"""
_APPLICATIONS = """
SELECT a.application_id, a.company_id, a.posting_id, a.applied_on, a.outcome, a.outcome_at,
       d.lane AS posting_lane, d.level_tier AS posting_level
FROM application a LEFT JOIN posting_decision d ON d.posting_id = a.posting_id
ORDER BY a.applied_on, a.application_id"""
_CLOSED = "SELECT company_id, status_at FROM company WHERE close_reason = 'interview' ORDER BY company_id"
_COMPANIES = "SELECT company_id, name, status FROM company WHERE company_id IN (SELECT value FROM json_each(?))"
_FACT_ROWS = ("SELECT * FROM company_fact WHERE fact IN ('size','headcount','stage','hq','everify','sponsorship') "
              "AND company_id IN (SELECT value FROM json_each(?))")
_POSTINGS = """
SELECT p.posting_id, p.company_id, p.first_seen_at, p.closed_at, d.lane, d.level_tier
FROM posting p JOIN posting_decision d ON d.posting_id = p.posting_id
WHERE p.company_id IN (SELECT value FROM json_each(?)) AND d.lane IN ('engineering','intern')"""
_BOARDS = """
SELECT b.board_id, b.company_id, b.ats, b.active, COUNT(p.posting_id) AS open_postings
FROM board b LEFT JOIN posting p ON p.board_id = b.board_id AND p.closed_at IS NULL
GROUP BY b.board_id"""


@dataclass(frozen=True)
class OutcomeRow:
    company_id: int  # the survivor
    name: str
    status: str
    merged_ids: tuple[int, ...]  # every id of the family with a touch, application or close here, survivor first
    best_outcome: str
    outcome_source: str  # touch, application, company_status, none
    touches: int
    channels: tuple[str, ...]  # sorted, distinct
    best_channel: str | None
    best_role_kind: str | None
    best_lane: str | None
    best_person: str | None  # only with with_people, and only a person with an address or a profile on file
    first_touch_on: str | None  # New York dates
    first_reply_on: str | None
    days_to_reply: float | None
    interview_on: str | None
    offer_on: str | None
    size_low: int | None
    size_high: int | None
    size_confidence: str | None
    stage: str | None
    hq: str | None
    everify: str | None
    sponsorship: str | None
    board_system: str | None
    level_tier: str | None
    level_basis: str  # touch posting, application posting, open at the time, or empty
    intern: bool
    features: Features
    score: float | None
    rank: int | None  # 1-based place in ranked_list; None when hidden or unscored


def interview_rows(rows: Iterable[OutcomeRow]) -> list[OutcomeRow]:
    """The rows whose best outcome is an interview or an offer."""
    return [r for r in rows if r.best_outcome in INTERVIEW_OUTCOMES]


def load_board_systems(conn: sqlite3.Connection, canon: Mapping[int, int]) -> dict[int, str]:
    """Survivor id to the ats of its board with the most open postings: active boards first, then the most open
    postings, then the lowest board id. A company with no board has no entry."""
    return _pick_boards(conn.execute(_BOARDS).fetchall(), canon)


def _pick_boards(rows: Iterable[Mapping], canon: Mapping[int, int]) -> dict[int, str]:
    best: dict[int, tuple[tuple, str]] = {}
    for r in rows:
        cid = canon.get(r["company_id"], r["company_id"])
        key = (-r["active"], -r["open_postings"], r["board_id"])
        if cid not in best or key < best[cid][0]:
            best[cid] = (key, r["ats"])
    return {cid: ats for cid, (_key, ats) in best.items()}


@dataclass
class _Family:
    """Everything one survivor's family holds, in the order it was read."""
    ids: set[int] = field(default_factory=set)
    touches: list = field(default_factory=list)
    apps: list = field(default_factory=list)
    closed: list = field(default_factory=list)


def _group(touches: Iterable[Mapping], apps: Iterable[Mapping], closed: Iterable[Mapping],
           canon: Mapping[int, int]) -> dict[int, _Family]:
    """Rows grouped by the survivor of their company."""
    out: dict[int, _Family] = {}
    for kind, rows in (("touches", touches), ("apps", apps), ("closed", closed)):
        for r in rows:
            fam = out.setdefault(canon.get(r["company_id"], r["company_id"]), _Family())
            fam.ids.add(r["company_id"])
            getattr(fam, kind).append(r)
    return out


def _touch_level(t: Mapping) -> str:
    return next((name for name, col in _TOUCH_STAMPS if t[col]), "none")


@dataclass(frozen=True)
class _Best:
    outcome: str
    source: str
    touch: Mapping | None  # the carrying touch
    app: Mapping | None  # the carrying application
    status_at: str | None  # the close time, when the company status carries it


def _pick_best(fam: _Family) -> _Best:
    """The best outcome and what carries it. At one level a touch wins over an application, which wins over the
    company status; among touches the earliest wins (they are read in sent order)."""
    found: list[tuple[int, int, _Best]] = []
    for t in fam.touches:
        level = _touch_level(t)
        found.append((_LEVEL[level], _SOURCE_ORDER["touch"], _Best(level, "touch", t, None, None)))
    for a in fam.apps:
        level = _APPLICATION_OUTCOME.get(a["outcome"], "none")
        found.append((_LEVEL[level], _SOURCE_ORDER["application"], _Best(level, "application", None, a, None)))
    for c in fam.closed:
        found.append((_LEVEL["interview"], _SOURCE_ORDER["company_status"],
                      _Best("interview", "company_status", None, None, c["status_at"])))
    top = max(level for level, _src, _b in found)
    _level, _src, best = min((f for f in found if f[0] == top), key=lambda f: f[1])  # min keeps the first on a tie
    if best.outcome == "none":
        best = _Best("none", "none", best.touch, best.app, None)
    return best


def _days_between(start: str, end: str) -> float:
    seconds = (datetime.strptime(end, ISO_FMT) - datetime.strptime(start, ISO_FMT)).total_seconds()
    return round(seconds / 86400, 1)


def _first_day(stamps: Iterable[str | None]) -> str | None:
    days = [ny_day(s) for s in stamps if s]
    return min(days) if days else None


def _pick_facts(rows: Iterable[Mapping], canon: Mapping[int, int]) -> dict[int, dict[str, Mapping]]:
    """Survivor id to fact to its winning `company_fact` row across the family; `size` holds the size evidence the
    company is judged by (a size row over a headcount row, `size_order_key`)."""
    won: dict[tuple[int, str], tuple[tuple, Mapping]] = {}
    for r in rows:
        slot = (canon.get(r["company_id"], r["company_id"]), r["fact"])
        key = fact_order_key(r)
        if slot not in won or key > won[slot][0]:
            won[slot] = (key, r)
    out: dict[int, dict[str, Mapping]] = {}
    for (cid, fact), (_key, r) in won.items():
        out.setdefault(cid, {})[fact] = r
    for facts in out.values():
        sizes = [facts.pop(f) for f in ("size", "headcount") if f in facts]
        if sizes:
            facts["size"] = max(sizes, key=size_order_key)
    return out


def _pick_level(best: _Best, fam: _Family, postings: Sequence[Mapping]) -> tuple[str | None, str, bool]:
    """(level tier, basis, intern) from the carrying touch's posting, an application's posting, else the newest
    engineering or intern posting open when the touch was sent; (None, "", False) with none of them."""
    t = best.touch
    if t is not None and t["posting_id"] is not None and t["posting_level"] is not None:
        return t["posting_level"], "touch posting", t["posting_lane"] == "intern"
    apps = ([best.app] if best.app is not None else []) + sorted(
        (a for a in fam.apps if a is not best.app),
        key=lambda a: (-_LEVEL[_APPLICATION_OUTCOME.get(a["outcome"], "none")], a["applied_on"]))
    for a in apps:
        if a["posting_id"] is not None and a["posting_level"] is not None:
            return a["posting_level"], "application posting", a["posting_lane"] == "intern"
    if t is not None:
        at = t["sent_at"]
    elif best.app is not None:
        # The last second of the New York day he applied, as a moment (the day ends at the next midnight).
        at = (datetime.strptime(day_bounds_utc(best.app["applied_on"])[1], ISO_FMT) - timedelta(seconds=1)
              ).strftime(ISO_FMT)
    else:
        at = best.status_at
    if at is None:
        return None, "", False
    open_then = [p for p in postings if p["first_seen_at"] <= at and (p["closed_at"] is None or p["closed_at"] > at)]
    if not open_then:
        return None, "", False
    newest = max(open_then, key=lambda p: (p["first_seen_at"], p["posting_id"]))
    return newest["level_tier"], "open at the time", newest["lane"] == "intern"


def load_outcome_rows(conn: sqlite3.Connection, *, canon: Mapping[int, int] | None = None,
                      bands: Bands | None = None, with_people: bool = False, scores: bool = True) -> list[OutcomeRow]:
    """One row per canonical company he reached, in `company_id` order. `scores` adds the latest stored score and
    the place in `ranked_list` (ranking itself passes False, so it never reads `company_score` through here)."""
    canon = canonical_ids(conn) if canon is None else canon
    bands = default_bands() if bands is None else bands
    families = _group(conn.execute(_TOUCHES).fetchall(), conn.execute(_APPLICATIONS).fetchall(),
                      conn.execute(_CLOSED).fetchall(), canon)
    if not families:
        return []
    survivors = sorted(families)
    every_id = sorted({cid for cid, end in canon.items() if end in families} | set(survivors))
    ids_json = json.dumps(every_id)
    companies = {r["company_id"]: r for r in conn.execute(_COMPANIES, (json.dumps(survivors),))}
    facts = _pick_facts(conn.execute(_FACT_ROWS, (ids_json,)).fetchall(), canon)
    postings: dict[int, list[Mapping]] = {}
    for p in conn.execute(_POSTINGS, (ids_json,)):
        postings.setdefault(canon.get(p["company_id"], p["company_id"]), []).append(p)
    boards = load_board_systems(conn, canon)
    places: dict[int, tuple[float, int | None]] = {}
    if scores:
        version = latest_version(conn)
        if version is not None:
            places = {s.company_id: (s.score, None) for s in load_scores(conn, version).values()}
            places.update({s.company_id: (s.score, n) for n, s in enumerate(ranked_list(conn, version), 1)})

    out = []
    for cid in survivors:
        fam, company = families[cid], companies[cid]
        best = _pick_best(fam)
        carrier = best.touch
        if best.source == "application" or (carrier is None and best.app is not None):
            channel, role_kind, lane = "application", None, None
        elif carrier is not None:
            channel, role_kind, lane = carrier["channel"], carrier["role_kind"], carrier["lane"]
        else:
            channel, role_kind, lane = None, None, None
        person = None
        if with_people and carrier is not None and carrier["person_known"] and carrier["person_name"]:
            person = carrier["person_name"]
        replied = [t for t in fam.touches if t["replied_at"]]
        first_reply = min(replied, key=lambda t: (t["replied_at"], t["touch_id"])) if replied else None
        interview_stamps = [t["interview_at"] for t in fam.touches]
        interview_stamps += [a["outcome_at"] for a in fam.apps if a["outcome"] == "interview"]
        interview_stamps += [c["status_at"] for c in fam.closed]
        offer_stamps = [t["offer_at"] for t in fam.touches]
        offer_stamps += [a["outcome_at"] for a in fam.apps if a["outcome"] == "offer"]
        first_days = [ny_day(t["sent_at"]) for t in fam.touches] + [a["applied_on"] for a in fam.apps]
        mine = facts.get(cid, {})
        size = mine.get("size")
        size_low = size["num_low"] if size else None
        size_high = size["num_high"] if size else None
        stage = mine["stage"]["value"] if "stage" in mine else None
        tier, basis, intern = _pick_level(best, fam, postings.get(cid, ()))
        board = boards.get(cid)
        score, rank = places.get(cid, (None, None))
        out.append(OutcomeRow(
            company_id=cid, name=company["name"], status=company["status"],
            merged_ids=(cid, *sorted(fam.ids - {cid})), best_outcome=best.outcome, outcome_source=best.source,
            touches=len(fam.touches), channels=tuple(sorted({t["channel"] for t in fam.touches})),
            best_channel=channel, best_role_kind=role_kind, best_lane=lane, best_person=person,
            first_touch_on=min(first_days) if first_days else None,
            first_reply_on=ny_day(first_reply["replied_at"]) if first_reply else None,
            days_to_reply=_days_between(first_reply["sent_at"], first_reply["replied_at"]) if first_reply else None,
            interview_on=_first_day(interview_stamps), offer_on=_first_day(offer_stamps),
            size_low=size_low, size_high=size_high, size_confidence=size["confidence"] if size else None,
            stage=stage, hq=mine["hq"]["value"] if "hq" in mine else None,
            everify=mine["everify"]["value"] if "everify" in mine else None,
            sponsorship=mine["sponsorship"]["value"] if "sponsorship" in mine else None,
            board_system=board, level_tier=tier, level_basis=basis, intern=intern,
            features=features_of(size_low=size_low, size_high=size_high, stage=stage, level_tier=tier,
                                 intern=intern, board_system=board, bands=bands),
            score=score, rank=rank))
    return out
