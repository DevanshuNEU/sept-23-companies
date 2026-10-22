"""Scoring a company and the stored shape of its row (#6, Task 8).

Pure: no store, no clock, no network. Each signal's `Assessment` becomes a `Points` row. `raw_points` is the weight
times the value; `points` is the same, except a positive raw amount is multiplied by the factor of its confidence (a
boost counts by how sure we are, a warning always counts in full). The score is the rounded sum of `points`.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass

from outreach.ranking.common import clean_text
from outreach.ranking.config import RankerConfig
from outreach.ranking.confidence import (Confidence, RankReason, build_rank_reason, factor, rank_confidence,
                                         scrub_text)
from outreach.ranking.facts import RankFacts
from outreach.ranking.hide import Hide
from outreach.ranking.roles import Best
from outreach.ranking.types import Signal

__all__ = ["Points", "Scored", "score_company", "to_columns", "list_order"]


def _round(x: float) -> float:
    return round(x, 4) + 0.0  # + 0.0 turns -0.0 into 0.0, so no "-0.0" is stored


@dataclass(frozen=True)
class Points:
    value: float
    weight: float
    raw_points: float   # round(weight * value, 4), before the confidence discount
    points: float       # after the discount (positive raw points only)
    reason: str
    confidence: Confidence


@dataclass(frozen=True)
class Scored:
    company_id: int
    name: str
    score: float
    lanes: tuple[str, ...]
    values: dict[str, Points]
    reasons: tuple[str, ...]
    hide: Hide | None
    flags: tuple[str, ...]
    best_posting_id: int | None
    ignored_postings: int
    stale_postings: tuple[Best, ...]
    confidence: Confidence      # the rank confidence
    rank_reason: RankReason


def _stale_flag(stale: tuple[Best, ...]) -> str:
    newest = scrub_text(clean_text(stale[0].stale_reason))  # the first is the newest
    if len(stale) == 1:
        return f"1 stale posting not counted as live: {newest}"
    return f"{len(stale)} stale postings not counted as live: {newest}"


def score_company(facts: RankFacts, hide: Hide | None, signals: list[Signal], cfg: RankerConfig, *,
                  extra_flags: tuple[str, ...] = ()) -> Scored:
    """Scores one company. A signal with no `[weights]` entry raises KeyError. `extra_flags` are appended to the
    flags and change no points."""
    values: dict[str, Points] = {}
    for signal in signals:
        got = signal.score(facts)
        weight = float(cfg.weights[signal.name])
        raw = _round(weight * got.value)
        points = raw if raw <= 0 else _round(raw * factor(got.confidence, cfg))
        values[signal.name] = Points(got.value, weight, raw, points, got.reason, got.confidence)

    ranked = sorted(((n, p) for n, p in values.items() if p.raw_points != 0), key=lambda x: (-abs(x[1].points), x[0]))
    reasons = tuple(p.reason for _n, p in ranked)
    confidence = rank_confidence({n: (p.raw_points, p.confidence) for n, p in values.items()}, cfg)

    flags = list(facts.weak_flags)
    if facts.ignored_postings > 0:
        flags.append(f"{facts.ignored_postings} postings ignored: board match doubtful")
    if facts.stale_postings:
        flags.append(_stale_flag(facts.stale_postings))
    flags.extend(extra_flags)

    return Scored(
        company_id=facts.company_id, name=facts.name, score=_round(sum(p.points for p in values.values())),
        lanes=tuple(facts.lanes), values=values, reasons=reasons, hide=hide, flags=tuple(flags),
        best_posting_id=facts.best_posting_id, ignored_postings=facts.ignored_postings,
        stale_postings=tuple(facts.stale_postings), confidence=confidence,
        rank_reason=build_rank_reason(reasons, confidence, cfg))


def to_columns(s: Scored) -> tuple[str, str]:
    """The `signals` and `lanes` texts of a `company_score` row: valid JSON with sorted keys."""
    signals = {
        "values": {n: {"value": p.value, "weight": p.weight, "raw_points": p.raw_points, "points": p.points,
                       "reason": p.reason, "confidence": p.confidence.level, "basis": p.confidence.basis}
                   for n, p in s.values.items()},
        "reasons": list(s.reasons),
        "hide": s.hide.to_json() if s.hide is not None else None,
        "flags": list(s.flags),
        "best_posting_id": s.best_posting_id,
        "ignored_postings": s.ignored_postings,
        "stale_postings": [{"posting_id": b.posting_id, "title": scrub_text(clean_text(b.title)), "reason": scrub_text(b.stale_reason)}
                           for b in s.stale_postings],
        "confidence": {"level": s.confidence.level, "basis": s.confidence.basis},
        "rank_reason": {"text": s.rank_reason.text, "level": s.confidence.level, "basis": s.confidence.basis,
                        "line": s.rank_reason.line},
    }
    return json.dumps(signals, sort_keys=True), json.dumps(list(s.lanes))


def list_order(rows: Iterable[Scored]) -> list[Scored]:
    """The ranked list: rows with no hide, highest score first, then the lower company id."""
    return sorted((r for r in rows if r.hide is None), key=lambda r: (-r.score, r.company_id))
