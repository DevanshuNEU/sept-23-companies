"""How the sender paces itself so a day of sends looks like a person working through an Outbox (#9, 2026-11-06).

Pure: nothing here reads the clock or the store. Two kinds of randomness:

- Drawn once, when a send happens, from the sender's own `random.Random`: the gap before the next send
  (`spread_gap`) and which of the first few ready messages goes next (`choose`).
- Fixed by a hash of what is being decided, so every read of the queue (each tick, the status page, the projection)
  gets the same answer: how late the first send of a day starts (`day_start_offset`) and how long a company waits
  between two of its emails (`company_gap`).

Gaps spread the account's day across its window (`spread_gap`): the minutes left over the sends left, with a random
factor, and the odd real break. Uniform gaps have a flat, even look that no person produces.
"""
import hashlib
import random
from datetime import date, datetime, timedelta, timezone
from typing import Sequence, TypeVar

from outreach.sending.config import SendConfig
from outreach.sending.pace import CompanyPace, default_pace
from outreach.store.timefmt import ISO_FMT

__all__ = ["spread_gap", "expected_gap_minutes", "choose", "day_start_offset", "company_gap", "max_company_gap"]

T = TypeVar("T")


def spread_gap(minutes_left: float, sends_left: int, rng: random.Random, cfg: SendConfig) -> float:
    """Minutes until the account's next send: now and then a break, otherwise the minutes left in its window over the
    sends it has left today, times a random factor from `spread_jitter`, clipped to `spread_gap_minutes`, so a day's
    sends spread across the window. No sends left gives the high clip."""
    low, high = cfg.spread_gap_minutes
    if sends_left <= 0:
        return float(high)
    if rng.random() < cfg.break_chance:
        return rng.uniform(*cfg.break_minutes)
    return min(max(minutes_left / sends_left * rng.uniform(*cfg.spread_jitter), low), high)


def expected_gap_minutes(minutes_left: float, sends_left: int, cfg: SendConfig) -> float:
    """The gap `spread_gap` has on average, for the projection: the formula with its factor at 1.0 (the middle of
    `spread_jitter`), clipped. It leaves out the rare break."""
    low, high = cfg.spread_gap_minutes
    if sends_left <= 0:
        return float(high)
    return min(max(minutes_left / sends_left * sum(cfg.spread_jitter) / 2, low), high)


def choose(candidates: Sequence[T], rng: random.Random, pool: int) -> T | None:
    """One of the first `pool` candidates, uniformly: a person does not take an Outbox strictly top to bottom. None
    when there are none. The oldest is never more than `pool - 1` places from going, so nothing starves."""
    head = candidates[:max(pool, 1)]
    return rng.choice(head) if head else None


def _fraction(*parts: object) -> float:
    """A number in [0, 1) that depends only on `parts`."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2 ** 64


def day_start_offset(day: date, cfg: SendConfig) -> timedelta:
    """How long after the window opens the first send of `day` waits: 0 up to `day_start_jitter_minutes`."""
    return timedelta(minutes=_fraction("day-start", day.isoformat()) * cfg.day_start_jitter_minutes)


def company_gap(company_id: int, last_send: datetime, cfg: SendConfig, pace: CompanyPace | None = None) -> timedelta:
    """The wait after a send to this company before the next one: its band's floor (`pace`, else the default band:
    `per_company_gap_minutes`) plus a share of the band's jitter that depends on the company and that send, so each
    gap differs (#229)."""
    pace = pace or default_pace(cfg)
    stamp = last_send.astimezone(timezone.utc).strftime(ISO_FMT)
    extra = _fraction("company-gap", company_id, stamp) * pace.gap_jitter_minutes
    return timedelta(minutes=pace.gap_minutes + extra)


def max_company_gap(cfg: SendConfig) -> timedelta:
    """The longest `company_gap` can be in any band: how far back the queue must look for a company's last send."""
    floors = (cfg.per_company_gap_minutes, *cfg.size_band_gap_minutes)
    jitters = (cfg.per_company_gap_jitter_minutes, *cfg.size_band_gap_jitter_minutes)
    return timedelta(minutes=max(f + j for f, j in zip(floors, jitters)))
