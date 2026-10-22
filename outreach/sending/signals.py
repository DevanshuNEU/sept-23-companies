"""What happened on the sending days: signals, send counts and the bounce rates the ramp judges a day by.

Every function takes the caller's connection and runs inside its transaction; none opens one. A "day" is the
`America/New_York` date (`cfg.day_tz`), and it can be a weekend: Monday morning in Tokyo is Sunday evening in New
York. So no function here walks business days to find sends; they look at the dates that had sends.
"""
import sqlite3
from datetime import date, datetime, timedelta, timezone

from outreach.sending import calendar as cal
from outreach.sending.config import SendConfig
from outreach.store.timefmt import ISO_FMT
from outreach.store.writes import append_event

__all__ = ["WARNING_KINDS", "HARD_KINDS", "record_signal", "signal_day", "sends_on", "bounce_rate_3d",
           "bounce_rate_warns", "hard_bounce_rate", "hard_bounce_tripped", "stop_tags_in", "as_datetime"]

# A warning: the day is not clean, today's limit drops one level at once, the next day starts from there.
WARNING_KINDS = ("bounce_warning", "policy_rejection", "stop_reply")
# A hard block: the sender is paused and Devanshu is told.
HARD_KINDS = ("gmail_limit", "gmail_policy", "hard_bounce_rate", "stop_replies_hard")


def as_datetime(at: datetime | str) -> datetime:
    """An aware UTC datetime from an aware datetime or canonical UTC text."""
    if isinstance(at, datetime):
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("a naive datetime is ambiguous; pass an aware one")
        return at.astimezone(timezone.utc)
    return datetime.strptime(at, ISO_FMT).replace(tzinfo=timezone.utc)


def record_signal(conn: sqlite3.Connection, kind: str, *, at: str, detail: dict, key: str) -> bool:
    """Write the signal as a `sender` event. True when it is new, False when `key` was already recorded."""
    return append_event(conn, entity="sender", entity_id=1, kind=kind, actor="sender", detail=detail, at=at,
                        idempotency_key=key) is not None


def signal_day(conn: sqlite3.Connection, at: str, cfg: SendConfig, *, now: datetime) -> date:
    """The day a signal that happened at `at` belongs to: its New York date, or today when that day is already
    closed (or is gone: a later day has opened without it). `now` is the moment the signal is being processed;
    it is required, so no caller reads the real clock by accident."""
    day = cal.ny_date(as_datetime(at), cfg)
    today = cal.ny_date(now, cfg)
    row = conn.execute("SELECT closed_at FROM send_day WHERE day = ?", (day.isoformat(),)).fetchone()
    if row is not None:
        return today if row["closed_at"] is not None else day
    later = conn.execute("SELECT 1 FROM send_day WHERE day > ?", (day.isoformat(),)).fetchone()
    return today if later is not None else day


def sends_on(conn: sqlite3.Connection, d: date, cfg: SendConfig) -> int:
    """Messages sent on the New York date `d`, first emails and follow-ups."""
    start, end = cal.ny_day_bounds(d, cfg)
    return conn.execute("SELECT COUNT(*) FROM message WHERE sent_at >= ? AND sent_at < ?", (start, end)).fetchone()[0]


def bounce_rate_3d(conn: sqlite3.Connection, d: date, cfg: SendConfig) -> float:
    """Bounces over sends across the last `cfg.bounce_days` New York dates that had at least one send, up to and
    including `d` (weekend dates count when something was sent). Sends are every message sent on those dates.
    Bounces are touches with `bounced_at` anywhere from the start of the earliest of those dates to the end of
    `d`: a delayed notice that arrives on a date with no sends (Saturday, for a Friday send) still counts.
    0.0 when nothing was sent."""
    first = conn.execute("SELECT MIN(sent_at) FROM message WHERE sent_at IS NOT NULL").fetchone()[0]
    if first is None:
        return 0.0
    earliest = cal.ny_date(as_datetime(first), cfg)
    sent = found = 0
    day, window_start = d, d
    while day >= earliest and found < cfg.bounce_days:
        n = sends_on(conn, day, cfg)
        if n:
            found += 1
            sent += n
            window_start = day
        day -= timedelta(days=1)
    if not sent:
        return 0.0
    start = cal.ny_day_bounds(window_start, cfg)[0]
    end = cal.ny_day_bounds(d, cfg)[1]
    bounced = conn.execute("SELECT COUNT(*) FROM touch WHERE bounced_at >= ? AND bounced_at < ?",
                           (start, end)).fetchone()[0]
    return bounced / sent


def bounce_rate_warns(rate: float, cfg: SendConfig) -> bool:
    """A day is clean only when bounces are under `clean_bounce_rate`: exactly 2% is a warning."""
    return rate >= cfg.clean_bounce_rate - 1e-12


def hard_bounce_rate(conn: sqlite3.Connection, cfg: SendConfig) -> tuple[float, int]:
    """The share of the last `cfg.hard_bounce_last_n` sent first emails whose touch bounced, and how many first
    emails that is (0.0 and 0 when none)."""
    rows = conn.execute(
        "SELECT t.bounced_at IS NOT NULL FROM message m JOIN touch t ON t.touch_id = m.touch_id "
        "WHERE m.seq = 0 AND m.state = 'sent' ORDER BY m.sent_at DESC, m.message_id DESC LIMIT ?",
        (cfg.hard_bounce_last_n,)).fetchall()
    if not rows:
        return 0.0, 0
    return sum(r[0] for r in rows) / len(rows), len(rows)


def hard_bounce_tripped(conn: sqlite3.Connection, cfg: SendConfig) -> bool:
    """True when at least `hard_bounce_min_sends` first emails are in the window and more than
    `hard_bounce_rate` of them bounced."""
    rate, count = hard_bounce_rate(conn, cfg)
    return count >= cfg.hard_bounce_min_sends and rate > cfg.hard_bounce_rate


def stop_tags_in(conn: sqlite3.Connection, now: datetime | str, days: int) -> int:
    """Email touches tagged `asked_to_stop` whose reply came in the last `days` days (after `now - days`, up to
    `now`). The tag counts from `touch.reply_kind` or from its `outreach_touch` (an undone one does not); a touch
    tagged in both counts once."""
    end = as_datetime(now)
    start = end - timedelta(days=days)
    lo, hi = start.strftime(ISO_FMT), end.strftime(ISO_FMT)
    return conn.execute(
        "SELECT COUNT(*) FROM ("
        " SELECT touch_id FROM touch WHERE reply_kind = 'asked_to_stop' AND replied_at > ? AND replied_at <= ?"
        " UNION"
        " SELECT email_touch_id FROM outreach_touch WHERE email_touch_id IS NOT NULL"
        "  AND reply_kind = 'asked_to_stop' AND undone_at IS NULL AND replied_at > ? AND replied_at <= ?)",
        (lo, hi, lo, hi)).fetchone()[0]
