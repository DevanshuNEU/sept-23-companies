"""The Mac alert for a new job at his level (#156): one grouped notice, never a stream.

A posting alerts when it is in "your level" with `level_ok = 1` on its current decision (read from the decision, not
from the tag: an `open` tier posting with `level_ok = 1` is tagged "level not stated" but is at his level, while a
`not_stated` posting never has `level_ok = 1`), its date (posted, or seen when it has no posted date) is today in New
York, it is not passed, and it has no `alert:posting:<id>` event yet. Uncertain rows (`level_ok = 0`) never alert.

Quiet hours are 22:00 to 08:00 New York: nothing is sent or recorded then, so what came in waits for the first tick
after 08:00 (and only alerts if its date is still today). At most one notice goes out per 15 minutes: the time of the
last one is `pipeline_meta` `last_alert_at`. One notice names up to three postings and says how many more there are.
Each announced posting gets one `alerted` event, so it is never announced twice.

The clock comes in as `now`; the notifier is whatever the caller passes (tests pass a list's append).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from outreach.pipeline.joblist import JobFilters, JobRow, query
from outreach.sending.notify import Notifier
from outreach.store import append_event, atomic
from outreach.store.timefmt import ISO_FMT, iso_utc

__all__ = ["run", "LAST_ALERT_KEY", "TITLE", "QUIET_START", "QUIET_END", "MIN_GAP", "SHOWN"]

LAST_ALERT_KEY = "last_alert_at"
TITLE = "New jobs at your level"
QUIET_START, QUIET_END = 22, 8  # New York hours: quiet from 22:00 up to 08:00
MIN_GAP = timedelta(minutes=15)
SHOWN = 3  # postings named in one notice
_NY = ZoneInfo("America/New_York")
_LIMIT = 10_000  # one page holds a whole day: a day is never walked in pages


def _moment(stamp: str) -> datetime:
    iso = iso_utc(stamp)
    if iso is None:
        raise ValueError("an alert needs a time")
    return datetime.strptime(iso, ISO_FMT).replace(tzinfo=timezone.utc)


def _quiet(local: datetime) -> bool:
    return local.hour >= QUIET_START or local.hour < QUIET_END


def _too_soon(conn: sqlite3.Connection, moment: datetime) -> bool:
    row = conn.execute("SELECT value FROM pipeline_meta WHERE key = ?", (LAST_ALERT_KEY,)).fetchone()
    last = iso_utc(row[0]) if row else None
    return last is not None and moment - _moment(last) < MIN_GAP


def _todays(conn: sqlite3.Connection, now: str, today: str) -> list[JobRow]:
    """Today's rows of "your level", newest first, from one query. A row dated after today (an ATS date in UTC can be
    tomorrow in New York) is skipped, not a stop: it heads the list and must not hide today's rows. The list is
    ordered by date, so the walk stops at the first row dated before today."""
    out: list[JobRow] = []
    for row in query(conn, now, JobFilters(view="your_level", window_days=7), None, _LIMIT).rows:
        if row.date < today:
            break
        if row.date == today:
            out.append(row)
    return out


def _level_ok(conn: sqlite3.Connection, ids: list[int]) -> set[int]:
    """Which of these postings have `level_ok = 1` on the current decision."""
    return {r[0] for r in conn.execute(
        "SELECT posting_id FROM decision WHERE ruleset_version = (SELECT ruleset_version FROM ruleset "
        "WHERE is_current = 1) AND level_ok = 1 AND posting_id IN (SELECT value FROM json_each(?))",
        (json.dumps(ids),))}


def _announced(conn: sqlite3.Connection, ids: list[int]) -> set[int]:
    return {int(r[0].rsplit(":", 1)[1]) for r in conn.execute(
        "SELECT idempotency_key FROM event WHERE idempotency_key IN "
        "(SELECT 'alert:posting:' || value FROM json_each(?))", (json.dumps(ids),))}


def _text(rows: list[JobRow]) -> str:
    text = "; ".join(f"{r.title} at {r.company_name}" for r in rows[:SHOWN])
    return text + (f" and {len(rows) - SHOWN} more" if len(rows) > SHOWN else "")


def run(conn: sqlite3.Connection, notifier: Notifier, now: str) -> int:
    """Announce the new postings at his level in one notice. Returns how many it announced (0 in quiet hours, inside
    the 15 minutes, or when nothing is new). The notice goes first and the events after it: a failure in between
    announces the same postings again on the next tick, never loses them."""
    moment = _moment(now)
    local = moment.astimezone(_NY)
    if _quiet(local) or _too_soon(conn, moment):
        return 0
    rows = _todays(conn, now, local.date().isoformat())
    if not rows:
        return 0
    ids = [r.posting_id for r in rows]
    ok, done = _level_ok(conn, ids), _announced(conn, ids)
    fresh = [r for r in rows if r.posting_id in ok and r.posting_id not in done]
    if not fresh:
        return 0
    notifier(TITLE, _text(fresh))
    with atomic(conn, "job_alert"):
        for r in fresh:
            append_event(conn, entity="posting", entity_id=r.posting_id, company_id=r.company_id, kind="alerted",
                         actor="system", detail={"title": r.title, "company": r.company_name}, at=now,
                         idempotency_key=f"alert:posting:{r.posting_id}")
        conn.execute("INSERT INTO pipeline_meta (key, value) VALUES (?, ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (LAST_ALERT_KEY, iso_utc(now)))
    return len(fresh)
