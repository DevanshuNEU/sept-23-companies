"""Similarity to the interview companies (#81): the profile the `interview_like` ranking signal compares with.

The profile is built from outcomes only: one entry per company whose best outcome is an interview or an offer. Never
from scores, proposals, picks, plans or touches without an interview, so the ranking cannot feed itself.

A company is compared with each interview company other than itself on four traits (size band, stage band, role
level band, board system). A trait matches only when both values are known and equal: unknown never matches unknown.
The value is the best match count over 4 when it reaches `min_matches`, else 0, so it is 0, 0.5, 0.75 or 1.0 and
never below zero. It depends on the nearest interview company, not on how many there are, so more interviews at one
kind of company never raise it. Under `min_companies` interview companies it is 0 for everyone.

Confidence is never `high`: `low` under `confident_at` interview companies, else `medium`. Pure: no store, no clock.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from outreach.insights.bands import FEATURE_NAMES, Features, board_words
from outreach.insights.dataset import OutcomeRow, interview_rows
from outreach.ranking.confidence import Confidence

__all__ = ["ProfileEntry", "InterviewProfile", "interview_profile", "Match", "similarity", "reason_text"]

_NAMED = 2  # interview companies a reason names before "and N more"


@dataclass(frozen=True)
class ProfileEntry:
    company_id: int
    name: str
    first_interview_on: str | None  # New York date of the first interview (else the offer)
    features: Features


@dataclass(frozen=True)
class InterviewProfile:
    entries: tuple[ProfileEntry, ...]  # newest first interview first, then by name; unknown days last
    confidence: Confidence

    @property
    def count(self) -> int:
        return len(self.entries)


def _profile_confidence(count: int, confident_at: int) -> Confidence:
    if count == 0:
        return Confidence("low", "no interview companies yet")
    if count < confident_at:
        noun = "interview company" if count == 1 else "interview companies"
        return Confidence("low", f"{count} {noun} so far, under {confident_at} is a small sample")
    return Confidence("medium", f"{count} interview companies")


def interview_profile(rows: Iterable[OutcomeRow], *, confident_at: int) -> InterviewProfile:
    """One entry per interview or offer row, newest first interview first, then by name."""
    entries = [ProfileEntry(r.company_id, r.name, r.interview_on or r.offer_on, r.features)
               for r in interview_rows(rows)]
    entries.sort(key=lambda e: (e.name, e.company_id))
    entries.sort(key=lambda e: e.first_interview_on or "", reverse=True)  # stable: a tie keeps the name order
    return InterviewProfile(tuple(entries), _profile_confidence(len(entries), confident_at))


@dataclass(frozen=True)
class Match:
    value: float  # 0.0 to 1.0
    matches: int  # the best number of traits shared with one interview company
    like: tuple[ProfileEntry, ...]  # the interview companies that reach it, in profile order; () when value is 0
    shared: tuple[str, ...]  # trait names shared with like[0], in FEATURE_NAMES order


_NONE = Match(0.0, 0, (), ())


def _shared(a: Features, b: Features) -> tuple[str, ...]:
    """The traits both know and agree on. None is never equal to anything, not even None."""
    return tuple(n for n in FEATURE_NAMES
                 if getattr(a, n) is not None and getattr(b, n) is not None and getattr(a, n) == getattr(b, n))


def similarity(company_id: int, features: Features, profile: InterviewProfile, *, min_matches: int,
               min_companies: int) -> Match:
    """How like the interview companies one company is. The company's own entry (same id) is skipped."""
    if profile.count < min_companies:
        return _NONE
    scored = [(len(_shared(features, e.features)), e) for e in profile.entries if e.company_id != company_id]
    best = max((n for n, _e in scored), default=0)
    if best < min_matches:
        return Match(0.0, best, (), ())
    like = tuple(e for n, e in scored if n == best)
    return Match(best / len(FEATURE_NAMES), best, like, _shared(features, like[0].features))


def _trait_words(name: str, value: str) -> str:
    if name == "size_band":
        return f"{value} people"
    if name == "level_band":
        return f"{value} role"
    if name == "board_system":
        return f"{board_words(value)} board"
    return value


def reason_text(m: Match, profile: InterviewProfile, *, min_companies: int) -> str:
    """The plain line the ranking stores: who the company is like and the traits shared with the first of them."""
    if profile.count < min_companies:
        return f"Fewer than {min_companies} interview companies so far"
    if m.value <= 0 or not m.like:
        return "Not like your interview companies yet"
    names = [e.name for e in m.like[:_NAMED]]
    more = len(m.like) - len(names)
    if more:
        who = f"{', '.join(names)} and {more} more"
    else:
        who = " and ".join(names)
    first = m.like[0].features
    traits = ", ".join(_trait_words(n, getattr(first, n)) for n in m.shared)
    return f"Like {who}, where you interviewed: {traits}"
