"""The funnel, base rates, the quiet flag and findings, counted from `outreach_touch` rows.

Pure: rows go in, plain dataclasses come out, no clock. `rows` are `outreach_touch` rows (sqlite rows or dicts);
an undone touch is no touch, so every function drops rows with `undone_at` set even if the caller forgot to.

Counting (the plan's Decisions): by event date in the period, not by cohort. Requests are `linkedin_request` touches
with `sent_at` in the period. Accepts, replies (any channel, any reply kind), referrals and interviews are touches
whose own date falls in the period. Accepts and replies are counted against requests; referrals and interviews
against replies. A period is `(first_date, last_date)`, both inclusive New York dates.
"""
import hashlib
import json
import statistics
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from outreach.outcomes.windows import ny_day
from outreach.store.timefmt import ISO_FMT, iso_utc

STAGES = ("requests", "accepts", "replies", "referrals", "interviews")
_STAGE_DATE = {"accepts": "accepted_at", "replies": "replied_at", "referrals": "referred_at",
               "interviews": "interview_at"}
_STAGE_FROM = {"accepts": "requests", "replies": "requests", "referrals": "replies", "interviews": "replies"}
MAX_FINDINGS = 2


class ReportConfig(Protocol):
    min_requests: int
    finding_group_min: int


@dataclass(frozen=True)
class FunnelCount:
    stage: str
    count: int
    of_stage: str | None   # the stage this one is counted against; None for requests
    of: int | None         # that stage's count in the same period


@dataclass(frozen=True)
class BaseRates:
    """All time, over `linkedin_request` touches. A rate is None under `min_requests` requests."""
    requests: int
    accepts: int
    replies: int
    accept_rate: float | None
    reply_rate: float | None
    median_days_to_reply: float | None
    enough: bool


@dataclass(frozen=True)
class FindingDraft:
    """One contrast: the `high_group` of a dimension replies at least twice as often as its `low_group`.
    `ratio` is None when the low group never replied (no infinity on the wire). Counts are touches and replies."""
    id: str
    key: str
    dimension: str
    high_group: str
    low_group: str
    high_n: int
    high_replies: int
    low_n: int
    low_replies: int
    high_rate: float
    low_rate: float
    ratio: float | None


def _live(rows: Iterable[Mapping]) -> list[Mapping]:
    return [r for r in rows if r["undone_at"] is None]


def in_period(stamp: str | None, period: tuple[str, str]) -> bool:
    return stamp is not None and period[0] <= ny_day(stamp) <= period[1]


def funnel(rows: Iterable[Mapping], period: tuple[str, str]) -> list[FunnelCount]:
    """The five stages for `period`, each with the stage and count it is measured against."""
    live = _live(rows)
    counts = {"requests": sum(1 for r in live if r["channel"] == "linkedin_request"
                              and in_period(r["sent_at"], period))}
    for stage, column in _STAGE_DATE.items():
        counts[stage] = sum(1 for r in live if in_period(r[column], period))
    return [FunnelCount(s, counts[s], _STAGE_FROM.get(s), counts[_STAGE_FROM[s]] if s in _STAGE_FROM else None)
            for s in STAGES]


def rate(count: int, of: int, min_n: int) -> float | None:
    """`count / of`, or None when there is nothing to divide by or fewer than `min_n` to go on."""
    if of <= 0 or of < min_n:
        return None
    return count / of


def _days(sent_at: str, replied_at: str) -> float:
    def parse(s: str) -> datetime:
        return datetime.strptime(iso_utc(s), ISO_FMT).replace(tzinfo=timezone.utc)
    return (parse(replied_at) - parse(sent_at)).total_seconds() / 86400


def base_rates(rows: Iterable[Mapping], cfg: ReportConfig) -> BaseRates:
    requests = [r for r in _live(rows) if r["channel"] == "linkedin_request"]
    accepts = sum(1 for r in requests if r["accepted_at"] is not None)
    replied = [r for r in requests if r["replied_at"] is not None]
    median = (round(statistics.median(_days(r["sent_at"], r["replied_at"]) for r in replied), 1)
              if replied else None)
    return BaseRates(
        requests=len(requests), accepts=accepts, replies=len(replied),
        accept_rate=rate(accepts, len(requests), cfg.min_requests),
        reply_rate=rate(len(replied), len(requests), cfg.min_requests),
        median_days_to_reply=median,
        enough=len(requests) >= cfg.min_requests,
    )


def is_quiet(rows: Iterable[Mapping], period: tuple[str, str]) -> bool:
    """No accepts and no replies in the period while requests or applications went out in it."""
    live = _live(rows)
    worked = any(r["channel"] in ("linkedin_request", "application") and in_period(r["sent_at"], period)
                 for r in live)
    heard = any(in_period(r["accepted_at"], period) or in_period(r["replied_at"], period) for r in live)
    return worked and not heard


def _context(row: Mapping) -> Mapping:
    raw = row["context"]
    return json.loads(raw) if isinstance(raw, str) else raw


def _bucket(value: float) -> str:
    return "above_zero" if value > 0 else "below_zero" if value < 0 else "zero"


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _named(context: Mapping) -> str | None:
    return ("named" if context["roleNamed"] else "not_named") if "roleNamed" in context else None


def _signal_bucket(context: Mapping, name: str) -> str | None:
    value = (context.get("signals") or {}).get(name)
    return _bucket(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# Each dimension maps a row and its parsed context to a group name, or None when the row is in no group for it.
_Grouper = Callable[[Mapping, Mapping], str | None]


def _dimensions(rows: list[Mapping]) -> dict[str, _Grouper]:
    dims: dict[str, _Grouper] = {
        "role_kind": lambda row, ctx: _text(ctx.get("roleKind")),
        "lane": lambda row, ctx: _text(ctx.get("lane")),
        "role_named": lambda row, ctx: _named(ctx),
        "channel": lambda row, ctx: row["channel"],
    }
    for name in sorted({n for r in rows for n in (_context(r).get("signals") or {})}):
        dims[f"signal:{name}"] = lambda row, ctx, name=name: _signal_bucket(ctx, name)
    return dims


def _draft(dimension: str, high: tuple[str, int, int], low: tuple[str, int, int]) -> FindingDraft:
    (high_group, high_n, high_replies), (low_group, low_n, low_replies) = high, low
    key = f"{dimension}:{high_group}>{low_group}"
    high_rate, low_rate = high_replies / high_n, low_replies / low_n
    return FindingDraft(
        id=hashlib.sha256(key.encode("utf-8")).hexdigest()[:12], key=key, dimension=dimension,
        high_group=high_group, low_group=low_group, high_n=high_n, high_replies=high_replies,
        low_n=low_n, low_replies=low_replies, high_rate=high_rate, low_rate=low_rate,
        ratio=None if low_rate == 0 else high_rate / low_rate,
    )


def findings(rows: Iterable[Mapping], cfg: ReportConfig, limit: int | None = MAX_FINDINGS) -> list[FindingDraft]:
    """The strongest contrasts, strongest first (ties by key), at most `limit` (None for all).

    Each dimension (role kind, lane, role named, channel and every signal's three buckets) gives at most one: its
    best group against its worst, among groups with at least `finding_group_min` touches, and only when the best
    reply rate is above zero and at least twice the worst. The ids come from the key, so the same data always gives
    the same ids in the same order.
    """
    live = _live(rows)
    found: list[tuple[float, FindingDraft]] = []
    for dimension, group_of in _dimensions(live).items():
        tally: dict[str, list[int]] = {}
        for row in live:
            group = group_of(row, _context(row))
            if group is None:
                continue
            counts = tally.setdefault(group, [0, 0])
            counts[0] += 1
            counts[1] += 1 if row["replied_at"] is not None else 0
        eligible = sorted((g, n, k) for g, (n, k) in tally.items() if n >= cfg.finding_group_min)
        if len(eligible) < 2:
            continue
        high = max(eligible, key=lambda t: (t[2] / t[1], t[0]))
        low = min((t for t in eligible if t[0] != high[0]), key=lambda t: (t[2] / t[1], t[0]))
        if high[2] == 0 or high[2] * low[1] < 2 * low[2] * high[1]:
            continue
        draft = _draft(dimension, high, low)
        found.append((float("inf") if draft.ratio is None else draft.ratio, draft))
    found.sort(key=lambda pair: (-pair[0], pair[1].key))
    drafts = [d for _, d in found]
    return drafts if limit is None else drafts[:limit]
