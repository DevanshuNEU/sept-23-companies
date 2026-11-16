"""Company size pacing (#229, #9 company size pacing ruling, 2026-11-12): how many first emails a company may get a
day and how far apart two emails to it must be, by its size band.

A company's size is its size fact (`company_fact`, the high figure, else the low one), read across the company and every
company merged into it: the survivor's own fact first, else the largest of its duplicates'. With no size on file, a
company the job list would guess is big (an active board on an enterprise ATS, or more than `BIG_BY_POSTINGS` open
postings read as US under the current ruleset, counted the same way) takes `size_guessed_big_band`; any other takes
the default band (`per_company_daily`, `per_company_gap_minutes`, `per_company_gap_jitter_minutes`).

The guess uses the job list's own constants (`outreach.pipeline.joblist`), so a company the list tags big is paced as
big here.
"""
from __future__ import annotations

import sqlite3
from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import dataclass

from outreach.pipeline.sizes import company_sizes
from outreach.sending.config import SendConfig

__all__ = ["CompanyPace", "default_pace", "pace_for", "company_sizes", "company_paces"]


@dataclass(frozen=True)
class CompanyPace:
    daily: int               # first emails a day (a follow-up takes no slot)
    gap_minutes: int         # the floor between two emails to the company, follow-ups too
    gap_jitter_minutes: int  # up to this many minutes more, a different amount after each send


def default_pace(cfg: SendConfig) -> CompanyPace:
    """The band for a company with no size on file and no sign of being big."""
    return CompanyPace(cfg.per_company_daily, cfg.per_company_gap_minutes, cfg.per_company_gap_jitter_minutes)


def _band(index: int, cfg: SendConfig) -> CompanyPace:
    return CompanyPace(cfg.size_band_daily[index], cfg.size_band_gap_minutes[index],
                       cfg.size_band_gap_jitter_minutes[index])


def pace_for(size: int | None, guessed_big: bool, cfg: SendConfig) -> CompanyPace:
    """The band for a company of `size` people (None when not on file); `guessed_big` counts only with no size."""
    if size is not None:
        return _band(bisect_left(cfg.size_band_up_to, size), cfg)
    if guessed_big:
        return _band(cfg.size_guessed_big_band - 1, cfg)
    return default_pace(cfg)


def company_paces(conn: sqlite3.Connection, company_ids: Iterable[int], cfg: SendConfig) -> dict[int, CompanyPace]:
    """Each surviving company's band, from its size (`company_sizes`)."""
    return {cid: pace_for(size, big, cfg) for cid, (size, big) in company_sizes(conn, company_ids).items()}
