"""The score breakdown: the stored `company_score` row of a company, read and shaped. Never recomputed.

The ranker (#6) stores each signal's value, weight, points, reason and confidence under `signals.values`, the rank
reason and the company's confidence beside them, and the lanes in their own column. This module reads those rows by
key, orders the signals, works out each share and finds the source link of each signal.
"""
import json
import sqlite3
from urllib.parse import urlsplit

from outreach.api.models.score import (
    PreviousScore, ScoreConfidence, ScoreLane, ScoreSignal, ScoreView,
)
from outreach.api.models.watch import SourceLink
from outreach.api.queries.company import best_facts, group_of, merge_parents, survivor_of
from outreach.ranking.confidence import scrub_text
from outreach.ranking.scores import latest_version

# The ranker's own signal order (`build_signals`), which breaks a tie in points. A test pins it to the ranker.
SIGNAL_ORDER = (
    "live_role", "posting_sponsors", "everify", "cap_exempt", "pay_level", "lca_junior", "small", "reachable_person",
    "funding", "hiring_velocity", "staffing_risk", "large_later_stage", "interview_like",
)
LABELS = {
    "everify": "E-Verify",
    "cap_exempt": "Cap-exempt employer",
    "lca_junior": "Junior visa filings",
    "posting_sponsors": "Posting offers sponsorship",
    "staffing_risk": "Staffing agency risk",
    "large_later_stage": "Large, later-stage company",
    "interview_like": "Like your interview companies",
    "hiring_velocity": "Hiring pace",
}
# Signals that rest on the best posting (its url is the source), and on evidence (the winning row's http link).
POSTING_SIGNALS = frozenset({"live_role", "posting_sponsors", "pay_level"})
EVIDENCE_FACTS = {
    "everify": ("everify",),
    "cap_exempt": ("cap_exempt",),
    "lca_junior": ("lca",),
    "small": ("size", "headcount"),
    "funding": ("funding",),
    "staffing_risk": ("staffing_risk",),
    "large_later_stage": ("stage", "size", "headcount"),
}

# The company's own rows, newest first: the first is the breakdown, the second the previous version.
ROWS_SQL = (
    "SELECT company_id, ranker_version, score, signals, lanes, scored_at FROM company_score WHERE company_id = ? "
    "ORDER BY scored_at DESC, ranker_version DESC LIMIT 2"
)
# One pass over one table: how many unhidden companies of the version there are, and how many come before this one
# (the list's own order: highest score first, then the lower company id).
RANK_SQL = (
    "SELECT COUNT(*) AS total, COALESCE(SUM(score > ? OR (score = ? AND company_id < ?)), 0) AS ahead "
    "FROM company_score WHERE ranker_version = ? AND json_extract(signals, '$.hide') IS NULL"
)
# Only the winner of each fact matters, so the rows are read with the `evidence_company` index and ranked in Python.
_FACTS = ",".join(repr(f) for f in sorted({f for facts in EVIDENCE_FACTS.values() for f in facts}))
EVIDENCE_SQL = (
    "SELECT evidence_id, company_id, fact, confidence, locked, source_kind, source_ref, recorded_at FROM evidence "
    "WHERE company_id IN ({marks}) AND fact IN (" + _FACTS + ")"
)
TOP_SQL = (
    "SELECT company_id FROM company_score WHERE ranker_version = ? AND json_extract(signals, '$.hide') IS NULL "
    "ORDER BY score DESC, company_id LIMIT ?"
)


def label_of(name: str) -> str:
    return LABELS.get(name) or name.replace("_", " ").capitalize()


def _is_url(text: str) -> bool:
    return text.strip().lower().startswith(("http://", "https://"))


def _order(item: tuple[str, dict]) -> tuple:
    name, v = item
    at = SIGNAL_ORDER.index(name) if name in SIGNAL_ORDER else len(SIGNAL_ORDER)
    return (-abs(v["points"]), at, name)


def _evidence_link(name: str, winners: dict[str, sqlite3.Row]) -> SourceLink | None:
    for fact in EVIDENCE_FACTS.get(name, ()):
        row = winners.get(fact)
        if row is not None and _is_url(row["source_ref"]):
            url = row["source_ref"].strip()
            return SourceLink(label=(urlsplit(url).hostname or url).removeprefix("www."), url=url)
    return None


def signal_sources(conn: sqlite3.Connection, group: list[int], names: list[str],
                   best_posting_id: int | None) -> dict[str, SourceLink | None]:
    """The source link of each signal: the best posting's url for the role and pay signals, the http `source_ref`
    of the winning evidence row for the evidence signals, else None. Two keyed reads cover every signal."""
    posting = None
    if best_posting_id is not None and POSTING_SIGNALS.intersection(names):
        posting = conn.execute("SELECT title, url FROM posting WHERE posting_id = ?", (best_posting_id,)).fetchone()
    winners: dict[str, sqlite3.Row] = {}
    if any(n in EVIDENCE_FACTS for n in names):
        sql = EVIDENCE_SQL.format(marks=",".join("?" * len(group)))
        winners = best_facts(conn.execute(sql, group).fetchall())
    out: dict[str, SourceLink | None] = {}
    for name in names:
        if name in POSTING_SIGNALS:
            ok = posting is not None and _is_url(posting["url"])
            # ranking's own scrub: a posting title is one plain line with no long dash, as in its reasons
            out[name] = SourceLink(label=scrub_text(posting["title"]), url=posting["url"].strip()) if ok else None
        else:
            out[name] = _evidence_link(name, winners)
    return out


def score_view(conn: sqlite3.Connection, company_id: int, now: str) -> ScoreView | None:
    """The breakdown of `company_id` (answered as its survivor), or None with no such company or no stored score."""
    if conn.execute("SELECT 1 FROM company WHERE company_id = ?", (company_id,)).fetchone() is None:
        return None
    parents = merge_parents(conn)
    survivor = survivor_of(parents, company_id)
    rows = conn.execute(ROWS_SQL, (survivor,)).fetchall()
    if not rows:
        return None
    row, older = rows[0], (rows[1] if len(rows) > 1 else None)
    stored = json.loads(row["signals"])

    ordered = sorted(stored["values"].items(), key=_order)
    total = sum(abs(v["points"]) for _n, v in ordered)
    sources = signal_sources(conn, group_of(parents, survivor), [n for n, _v in ordered], stored["best_posting_id"])
    signals = [
        ScoreSignal(
            name=name, label=label_of(name), value=v["value"], weight=v["weight"], points=v["points"],
            share=round(v["points"] / total, 4) + 0.0 if total else 0.0, neutral=v["value"] == 0,
            reason=v["reason"], confidence=v["confidence"], basis=v["basis"], source=sources[name],
        )
        for name, v in ordered
    ]

    counted = conn.execute(RANK_SQL, (row["score"], row["score"], survivor, row["ranker_version"])).fetchone()
    lanes = json.loads(row["lanes"])
    return ScoreView(
        as_of=now, company_id=survivor, ranker_version=row["ranker_version"], score=row["score"],
        rank=None if stored["hide"] is not None else counted["ahead"] + 1, of_companies=counted["total"],
        lanes=[ScoreLane(lane=lane, strongest=i == 0) for i, lane in enumerate(lanes)], signals=signals,
        rank_reason=stored["rank_reason"]["line"],
        confidence=ScoreConfidence(level=stored["confidence"]["level"], basis=stored["confidence"]["basis"]),
        scored_at=row["scored_at"],
        previous=PreviousScore(ranker_version=older["ranker_version"], score=older["score"]) if older else None,
    )


def top_scored_ids(conn: sqlite3.Connection, limit: int) -> list[int]:
    """The companies with no hide that score highest in the newest version, best first (the ranked list's order)."""
    version = latest_version(conn)
    if version is None or limit <= 0:
        return []
    return [r[0] for r in conn.execute(TOP_SQL, (version, limit))]
