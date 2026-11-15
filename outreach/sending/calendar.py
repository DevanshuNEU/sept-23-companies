"""New York dates, business days and recipient windows.

Pure: every function takes the time or date it needs and none reads the clock. Times are aware datetimes; results
that are moments are aware UTC.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from outreach.sending.config import SendConfig
from outreach.store.timefmt import ISO_FMT


def _aware(at: datetime) -> datetime:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("a naive datetime is ambiguous; pass an aware one")
    return at


def ny_date(at: datetime, cfg: SendConfig) -> date:
    """The sender's day (`cfg.day_tz`, New York) that the moment `at` falls on."""
    return _aware(at).astimezone(ZoneInfo(cfg.day_tz)).date()


def is_business_day(d: date, cfg: SendConfig) -> bool:
    return d.weekday() in cfg.weekdays and d not in cfg.holidays


def add_business_days(d: date, n: int, cfg: SendConfig) -> date:
    """The date `n` business days after `d`. `d` itself never counts; `n` of 0 gives `d` back."""
    if n < 0:
        raise ValueError(f"cannot add {n} business days")
    while n > 0:
        d += timedelta(days=1)
        if is_business_day(d, cfg):
            n -= 1
    return d


def business_days_between(a: date, b: date, cfg: SendConfig) -> int:
    """Business days after `a` up to and including `b`; 0 when `b` is not after `a`."""
    count = 0
    d = a
    while d < b:
        d += timedelta(days=1)
        if is_business_day(d, cfg):
            count += 1
    return count


def recipient_zone(tz_name: str) -> ZoneInfo | None:
    """The zone named `tz_name`, or None when `zoneinfo` cannot load it."""
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def in_window(at: datetime, zone: ZoneInfo, cfg: SendConfig) -> bool:
    """True when the recipient's local date is a business day and the local time is inside the window (ends
    inclusive)."""
    local = _aware(at).astimezone(zone)
    return is_business_day(local.date(), cfg) and cfg.window_start <= local.time() <= cfg.window_end


def _at(d: date, t: time, zone: ZoneInfo) -> datetime:
    return datetime.combine(d, t, tzinfo=zone).astimezone(timezone.utc)


def next_window_start(at: datetime, zone: ZoneInfo, cfg: SendConfig) -> datetime:
    """The first moment at or after `at` that is inside the window: `at` itself when it already is, else the next
    opening (`window_start` on the next business day, or later today when today is a business day and not yet open)."""
    if in_window(at, zone, cfg):
        return at.astimezone(timezone.utc)
    local = at.astimezone(zone)
    day = local.date()
    if not (is_business_day(day, cfg) and local.time() < cfg.window_start):
        day = add_business_days(day, 1, cfg)
    return _at(day, cfg.window_start, zone)


def ny_day_bounds(d: date, cfg: SendConfig) -> tuple[str, str]:
    """Canonical UTC start and end of a New York date, as [start, end): the end is the next date's start."""
    zone = ZoneInfo(cfg.day_tz)
    start = _at(d, time(0, 0), zone)
    end = _at(d + timedelta(days=1), time(0, 0), zone)
    return start.strftime(ISO_FMT), end.strftime(ISO_FMT)
