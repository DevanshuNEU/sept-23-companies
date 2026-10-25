"""The app's days, weeks and months. All of them are America/New_York; none reads the clock.

Dates are `YYYY-MM-DD` text, moments are canonical UTC `YYYY-MM-DDTHH:MM:SSZ`. A day is local midnight to local
midnight, so the Sunday the clocks go back is 25 hours long and the Sunday they go forward is 23.
"""
import calendar
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from outreach.store.timefmt import ISO_FMT, iso_utc

NY = ZoneInfo("America/New_York")


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{value!r} is not a YYYY-MM-DD date") from None


def ny_day(now: str) -> str:
    """The New York date a moment falls on. `now` is any ISO-8601 time (naive is taken as UTC)."""
    stamp = iso_utc(now)
    if stamp is None:
        raise ValueError("ny_day needs a time")
    moment = datetime.strptime(stamp, ISO_FMT).replace(tzinfo=timezone.utc)
    return moment.astimezone(NY).date().isoformat()


def week_bounds(now: str) -> tuple[str, str]:
    """Monday and Sunday (both inclusive) of the New York week that holds `now`."""
    today = _date(ny_day(now))
    monday = today - timedelta(days=today.weekday())
    return monday.isoformat(), (monday + timedelta(days=6)).isoformat()


def month_bounds(now: str) -> tuple[str, str]:
    """The first and last day (both inclusive) of the New York month that holds `now`."""
    today = _date(ny_day(now))
    last = calendar.monthrange(today.year, today.month)[1]
    return today.replace(day=1).isoformat(), today.replace(day=last).isoformat()


def _midnight_utc(day: date) -> str:
    # Local midnight never falls in a New York DST gap or overlap (the changes are at 02:00), so this is exact.
    return datetime(day.year, day.month, day.day, tzinfo=NY).astimezone(timezone.utc).strftime(ISO_FMT)


def day_bounds_utc(day: str) -> tuple[str, str]:
    """`(start, end)` of a New York date as UTC moments: start is included, end (the next midnight) is not."""
    d = _date(day)
    return _midnight_utc(d), _midnight_utc(d + timedelta(days=1))


def next_weekday(day: str) -> str:
    """The first Monday to Friday after `day` (a Friday gives Monday). Holidays are not known here."""
    d = _date(day) + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()
