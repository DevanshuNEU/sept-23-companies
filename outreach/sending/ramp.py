"""The adaptive ramp and the hard block (#9): 20 a day to start, 10 more each clean sending day up to 100.

`send_day.send_limit` is the day's starting limit and is never rewritten. A warning (or a hard block) makes the
day not clean: its effective limit is one level lower at once, and the next day starts from that lower level. A day
closes on the first tick of a later New York date; only then is its outcome frozen, as `clean` when it had sends.
A day with no sends keeps the limit, so a Mac that slept through days does not ramp twice.

Every function takes the caller's connection and runs inside its transaction; none opens one.
"""
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone

from outreach.sending import calendar as cal
from outreach.sending import signals
from outreach.sending.config import SendConfig
from outreach.sending.notify import Notifier, notify_once
from outreach.store.timefmt import ISO_FMT
from outreach.store.writes import append_event

__all__ = ["DayState", "limit_for", "effective_limit", "open_day", "today_state", "note_signal"]


@dataclass(frozen=True)
class DayState:
    day: date
    start_limit: int
    effective_limit: int
    sent: int
    outcome: str | None
    closed: bool


def _row(conn: sqlite3.Connection, d: date) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM send_day WHERE day = ?", (d.isoformat(),)).fetchone()


def _step_up(level: int, cfg: SendConfig) -> int:
    return min(level + cfg.ramp_step, cfg.ramp_ceiling)


def _step_down(level: int, cfg: SendConfig) -> int:
    return min(level, max(level - cfg.ramp_step, cfg.ramp_floor))


def _close_outcome(conn: sqlite3.Connection, day: date, cfg: SendConfig) -> tuple[str | None, str]:
    """How a day with no outcome yet closes: `warning` (note `bounce rate`) when it had sends and the 3-day bounce
    rate warns, `clean` when it had sends and does not, else no outcome and the note `no sends`."""
    if signals.sends_on(conn, day, cfg) == 0:
        return None, "no sends"
    if signals.bounce_rate_warns(signals.bounce_rate_3d(conn, day, cfg), cfg):
        return "warning", "bounce rate"
    return "clean", ""


def limit_for(conn: sqlite3.Connection, d: date, cfg: SendConfig) -> int:
    """Read only: the start limit day `d` gets from the latest `send_day` row before it. 20 with no row. After a
    clean day it is one level up (to the ceiling); after a warning or hard block, one level down (to the floor);
    after a day with no sends, the same. A day still open is judged as it would close (`_close_outcome`)."""
    prev = conn.execute("SELECT * FROM send_day WHERE day < ? ORDER BY day DESC LIMIT 1",
                        (d.isoformat(),)).fetchone()
    if prev is None:
        return cfg.ramp_start
    outcome = prev["outcome"]
    if outcome is None and prev["closed_at"] is None:
        outcome = _close_outcome(conn, date.fromisoformat(prev["day"]), cfg)[0]
    if outcome == "clean":
        return _step_up(prev["send_limit"], cfg)
    if outcome in ("warning", "hard_block"):
        return _step_down(prev["send_limit"], cfg)
    return prev["send_limit"]


def effective_limit(conn: sqlite3.Connection, d: date, cfg: SendConfig) -> int:
    """Read only: how many `d` may send. Its start limit (its row, else `limit_for`), one level lower (floor 20)
    when a warning or hard-block signal belongs to `d`. #15's `current_send_limit` is this."""
    row = _row(conn, d)
    start = row["send_limit"] if row is not None else limit_for(conn, d, cfg)
    lowered = row is not None and row["outcome"] in ("warning", "hard_block")
    if not lowered:
        kinds = signals.WARNING_KINDS + signals.HARD_KINDS
        lowered = conn.execute(
            f"SELECT 1 FROM event WHERE entity = 'sender' AND kind IN ({','.join('?' * len(kinds))}) "
            "AND json_extract(detail, '$.day') = ? LIMIT 1", (*kinds, d.isoformat())).fetchone() is not None
    return _step_down(start, cfg) if lowered else start


def _state(conn: sqlite3.Connection, row: sqlite3.Row, cfg: SendConfig) -> DayState:
    day = date.fromisoformat(row["day"])
    return DayState(day=day, start_limit=row["send_limit"], effective_limit=effective_limit(conn, day, cfg),
                    sent=signals.sends_on(conn, day, cfg), outcome=row["outcome"],
                    closed=row["closed_at"] is not None)


def _ensure_row(conn: sqlite3.Connection, d: date, cfg: SendConfig, now: str) -> sqlite3.Row:
    conn.execute("INSERT INTO send_day(day, send_limit, created_at) VALUES (?, ?, ?) ON CONFLICT(day) DO NOTHING",
                 (d.isoformat(), limit_for(conn, d, cfg), now))
    return _row(conn, d)


def open_day(conn: sqlite3.Connection, now: datetime, cfg: SendConfig) -> DayState | None:
    """Close every earlier open day, then open today. A closed day keeps `warning` or `hard_block`, else becomes
    `clean` when it had sends and the 3-day bounce rate is under 2%, `warning` (note `bounce rate`) when it is not,
    else stays NULL with the note `no sends`. Today's row gets `limit_for` and is
    written only when today is a business day (None on a weekend or holiday). Safe to call on every tick."""
    today = cal.ny_date(now, cfg)
    stamp = now.astimezone(timezone.utc).strftime(ISO_FMT)
    for old in conn.execute("SELECT day, outcome FROM send_day WHERE day < ? AND closed_at IS NULL ORDER BY day",
                            (today.isoformat(),)).fetchall():
        if old["outcome"] in ("warning", "hard_block"):
            conn.execute("UPDATE send_day SET closed_at = ? WHERE day = ?", (stamp, old["day"]))
        else:
            outcome, note = _close_outcome(conn, date.fromisoformat(old["day"]), cfg)
            conn.execute("UPDATE send_day SET closed_at = ?, outcome = ?, outcome_note = ? WHERE day = ?",
                         (stamp, outcome, note, old["day"]))
    if not cal.is_business_day(today, cfg):
        return None
    return _state(conn, _ensure_row(conn, today, cfg, stamp), cfg)


def today_state(conn: sqlite3.Connection, now: datetime, cfg: SendConfig) -> DayState | None:
    """Read only: today's row as a `DayState`, or None when there is none."""
    row = _row(conn, cal.ny_date(now, cfg))
    return _state(conn, row, cfg) if row is not None else None


def _reason(kind: str, cfg: SendConfig) -> str:
    return {
        "gmail_limit": "Sending is paused because Gmail said the account hit its sending limit.",
        "gmail_policy": "Sending is paused because Gmail said this account may not send.",
        "stop_replies_hard": f"Sending is paused because {cfg.stop_replies_hard} people asked you to stop "
                             f"within {cfg.stop_replies_days} days.",
    }[kind]


def note_signal(conn: sqlite3.Connection, kind: str, *, at: str, detail: dict, key: str, cfg: SendConfig,
                notify: Notifier, now: datetime) -> None:
    """Record a signal and apply it. The signal lands on its own day (`signals.signal_day`), whose `outcome`
    becomes `warning`, or `hard_block` for a hard kind (never downgraded). A hard kind also pauses the sender with
    `pause_kind 'hard_block'` (over his own pause too), appends a `hard_block` event and tells him once per kind
    per day. A repeated `key` changes nothing. `now` is when the signal is processed (required: nothing here
    reads the real clock). Callers pass only send-path Gmail limits as `gmail_limit` or `gmail_policy`: a 429 on
    a read is not one."""
    if kind not in signals.WARNING_KINDS + signals.HARD_KINDS:
        raise ValueError(f"unknown signal kind {kind!r}")
    stamp = now.astimezone(timezone.utc).strftime(ISO_FMT)
    day = signals.signal_day(conn, at, cfg, now=now)
    if not signals.record_signal(conn, kind, at=at, detail={**detail, "day": day.isoformat()}, key=key):
        return
    row = _ensure_row(conn, day, cfg, stamp)
    hard = kind in signals.HARD_KINDS
    if hard or row["outcome"] != "hard_block":
        conn.execute("UPDATE send_day SET outcome = ? WHERE day = ?", ("hard_block" if hard else "warning",
                                                                      day.isoformat()))
    if not hard:
        return
    reason = _reason(kind, cfg)
    sender = conn.execute("SELECT paused, pause_kind FROM sender WHERE sender_id = 1").fetchone()
    if not (sender["paused"] and sender["pause_kind"] == "hard_block"):
        conn.execute("UPDATE sender SET paused = 1, pause_kind = 'hard_block', paused_reason = ?, paused_at = ?, "
                     "updated_at = ?, revision = revision + 1 WHERE sender_id = 1", (reason, stamp, stamp))
        append_event(conn, entity="sender", entity_id=1, kind="hard_block", actor="sender", at=at,
                     detail={"kind": kind, "day": day.isoformat(), "reason": reason},
                     idempotency_key=f"hard_block:{key}")
    notify_once(conn, notify, kind, day, "Outreach sending paused", reason, at=stamp)
