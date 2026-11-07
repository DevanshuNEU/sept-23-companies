"""How the sender paces itself so a day of sends looks like a person working through an Outbox (#9, 2026-11-06).

Pure: nothing here reads the clock or the store. Two kinds of randomness:

- Drawn once, when a send happens, from the sender's own `random.Random`: the gap before the next send
  (`next_gap_minutes`) and which of the first few ready messages goes next (`choose`).
- Fixed by a hash of what is being decided, so every read of the queue (each tick, the status page, the projection)
  gets the same answer: how late the first send of a day starts (`day_start_offset`) and how long a company waits
  between two of its emails (`company_gap`).

Gaps are log-normal, not uniform: mostly short, sometimes long, with the odd real break. Uniform gaps from 4 to 9
minutes have a flat, even look that no person produces.
"""
import hashlib
import math
import random
from datetime import date, datetime, timedelta, timezone
from typing import Sequence, TypeVar

from outreach.sending.config import SendConfig
from outreach.store.timefmt import ISO_FMT

__all__ = ["next_gap_minutes", "mean_gap_minutes", "choose", "day_start_offset", "company_gap", "max_company_gap"]

T = TypeVar("T")


def next_gap_minutes(rng: random.Random, cfg: SendConfig) -> float:
    """Minutes until the next send: now and then a break, otherwise a log-normal gap clipped to `gap_minutes`."""
    if rng.random() < cfg.break_chance:
        return rng.uniform(*cfg.break_minutes)
    low, high = cfg.gap_minutes
    return min(max(rng.lognormvariate(math.log(cfg.gap_median_minutes), cfg.gap_spread), low), high)


def mean_gap_minutes(cfg: SendConfig) -> float:
    """The expected gap, for the projection (it ignores the clipping, which only trims the long tail a little)."""
    plain = cfg.gap_median_minutes * math.exp(cfg.gap_spread ** 2 / 2)
    return (1 - cfg.break_chance) * plain + cfg.break_chance * sum(cfg.break_minutes) / 2


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


def company_gap(company_id: int, last_send: datetime, cfg: SendConfig) -> timedelta:
    """The wait after a send to this company before the next one: `per_company_gap_minutes` (#9's floor) plus a share
    of `per_company_gap_jitter_minutes` that depends on the company and that send, so each gap differs."""
    stamp = last_send.astimezone(timezone.utc).strftime(ISO_FMT)
    extra = _fraction("company-gap", company_id, stamp) * cfg.per_company_gap_jitter_minutes
    return timedelta(minutes=cfg.per_company_gap_minutes + extra)


def max_company_gap(cfg: SendConfig) -> timedelta:
    """The longest `company_gap` can be: how far back the queue must look for a company's last send."""
    return timedelta(minutes=cfg.per_company_gap_minutes + cfg.per_company_gap_jitter_minutes)
