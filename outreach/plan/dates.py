"""New York dates and month and weekday math for the plan (#15). Pure: no clock, no store.

Timestamps are canonical UTC `YYYY-MM-DDTHH:MM:SSZ`; a "day" is a date in the configured zone (`plan.timezone`).
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from outreach.store.timefmt import ISO_FMT, iso_utc

__all__ = ["ny_date", "is_weekday", "month_range", "utc_window", "weekdays_left", "business_days_between",
           "today_ny"]


def ny_date(ts: str, tz: str) -> date:
    """The date in `tz` of a canonical UTC timestamp."""
    when = datetime.strptime(iso_utc(ts), ISO_FMT).replace(tzinfo=timezone.utc)
    return when.astimezone(ZoneInfo(tz)).date()


def today_ny(now_utc: str, tz: str) -> date:
    return ny_date(now_utc, tz)


def is_weekday(d: date) -> bool:
    return d.weekday() < 5


def month_range(d: date) -> tuple[date, date]:
    """The first and last day of the month `d` is in."""
    return d.replace(day=1), d.replace(day=calendar.monthrange(d.year, d.month)[1])


def utc_window(first: date, last: date, tz: str) -> tuple[str, str]:
    """The UTC start of `first` and the UTC start of the day after `last`, as canonical timestamps: the half-open
    window `[start, end)` that holds exactly the days `first` to `last` in `tz`."""
    zone = ZoneInfo(tz)

    def start(day: date) -> str:
        return datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc).strftime(ISO_FMT)

    return start(first), start(last + timedelta(days=1))


def weekdays_left(today: date) -> int:
    """Monday to Friday days from `today` to the end of its month, both included."""
    _, end = month_range(today)
    return sum(1 for n in range((end - today).days + 1) if is_weekday(today + timedelta(days=n)))


def business_days_between(a: date, b: date) -> int:
    """Weekdays in the half-open range `(a, b]`; 0 when `b <= a`."""
    if b <= a:
        return 0
    return sum(1 for n in range(1, (b - a).days + 1) if is_weekday(a + timedelta(days=n)))
