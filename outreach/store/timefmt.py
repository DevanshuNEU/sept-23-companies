"""Canonical time shapes for the store.

Timestamps are UTC text `YYYY-MM-DDTHH:MM:SSZ`. Dates are `YYYY-MM-DD`.
Ported from Job Radar `store.py` (utcnow, iso_utc) and `refresh/finalcheck.py` (d10).
"""
import re
from datetime import datetime, timezone

ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"

_DATE_PREFIX = re.compile(r"(\d{4}-\d{2}-\d{2})")


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime(ISO_FMT)


def iso_utc(s: str | None) -> str | None:
    """Normalise an ISO-8601 timestamp to canonical UTC.

    Sources send four shapes: canonical, UTC spelled as an offset, a real local
    offset (22:26 at -04:00 is 02:26 the next day in UTC), and fractional seconds.
    A string sort on the raw values puts the local-offset one on the wrong day.
    Naive input is taken as UTC and a bare date becomes midnight.

    None and "" give None. Anything unparseable raises ValueError: the schema
    CHECKs would reject it anyway, and carrying it would hide the bad source.
    """
    if not s:
        return None
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime(ISO_FMT)


def iso_date(v: str | int | float | None) -> str | None:
    """A `YYYY-MM-DD` date from an ISO prefix, or epoch seconds or milliseconds.

    Values above 1e11 are milliseconds. Returns None when the value is unreadable.
    """
    if not v:
        return None
    s = str(v)
    m = _DATE_PREFIX.match(s)
    if m:
        return m.group(1)
    if isinstance(v, float):
        n = v
    elif s.isdigit():
        n = int(s)
    else:
        return None
    try:
        n = n / 1000 if n > 1e11 else n
        return datetime.fromtimestamp(n, tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OverflowError, OSError):
        return None
