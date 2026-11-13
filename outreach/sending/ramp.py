"""The adaptive ramp and the hard block, per account: each account starts at its `ramp_start`, adds `ramp_step`
on each clean sending day up to its `ramp_ceiling`, and steps down by `ramp_step` (not below `ramp_floor`).

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
from outreach.sending import accounts, signals
from outreach.sending.accounts import AccountRow
from outreach.sending.config import AccountConfig, SendConfig
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


def _row(conn: sqlite3.Connection, account_id: int, d: date) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM send_day WHERE account_id = ? AND day = ?",
                        (account_id, d.isoformat())).fetchone()


def _clamp(level: int, acct: AccountConfig) -> int:
    """A carried level held to the account's own range: a row written under another floor or ceiling (a stale 200
    on an account whose ceiling is 40) comes back to this account's numbers before it steps."""
    return min(max(level, acct.ramp_floor), acct.ramp_ceiling)


def _step_up(level: int, acct: AccountConfig) -> int:
    return min(level + acct.ramp_step, acct.ramp_ceiling)


def _step_down(level: int, acct: AccountConfig) -> int:
    return min(level, max(level - acct.ramp_step, acct.ramp_floor))


def _close_outcome(conn: sqlite3.Connection, account: AccountRow, day: date, cfg: SendConfig) -> tuple[str | None, str]:
    """How a day with no outcome yet closes: `warning` (note `bounce rate`) when it had sends and the 3-day bounce
    rate warns, `clean` when it had sends and does not, else no outcome and the note `no sends`."""
    if signals.sends_on(conn, account.account_id, day, cfg) == 0:
        return None, "no sends"
    if signals.bounce_rate_warns(signals.bounce_rate_3d(conn, account.account_id, day, cfg), cfg):
        return "warning", "bounce rate"
    return "clean", ""


def limit_for(conn: sqlite3.Connection, account: AccountRow, d: date, cfg: SendConfig) -> int:
    """Read only: the start limit day `d` gets from the account's latest `send_day` row before it. `ramp_start`
    with no row. After a clean day it is one level up (to the ceiling); after a warning or hard block, one level
    down (to the floor); after a day with no sends, the same. The carried level is first clamped into
    `[ramp_floor, ramp_ceiling]`. A day still open is judged as it would close (`_close_outcome`)."""
    acct = account.cfg
    prev = conn.execute("SELECT * FROM send_day WHERE account_id = ? AND day < ? ORDER BY day DESC LIMIT 1",
                        (account.account_id, d.isoformat())).fetchone()
    if prev is None:
        return acct.ramp_start
    outcome = prev["outcome"]
    if outcome is None and prev["closed_at"] is None:
        outcome = _close_outcome(conn, account, date.fromisoformat(prev["day"]), cfg)[0]
    level = _clamp(prev["send_limit"], acct)
    if outcome == "clean":
        return _step_up(level, acct)
    if outcome in ("warning", "hard_block"):
        return _step_down(level, acct)
    return level


def effective_limit(conn: sqlite3.Connection, account: AccountRow, d: date, cfg: SendConfig) -> int:
    """Read only: how many `d` may send from this account. Its start limit (its row, else `limit_for`), one level
    lower when a warning or hard-block signal of this account belongs to `d`."""
    acct = account.cfg
    row = _row(conn, account.account_id, d)
    start = _clamp(row["send_limit"], acct) if row is not None else limit_for(conn, account, d, cfg)
    lowered = row is not None and row["outcome"] in ("warning", "hard_block")
    if not lowered:
        kinds = signals.WARNING_KINDS + signals.HARD_KINDS
        lowered = conn.execute(
            f"SELECT 1 FROM event WHERE entity = 'sender' AND entity_id = ? AND kind IN ({','.join('?' * len(kinds))}) "
            "AND json_extract(detail, '$.day') = ? LIMIT 1",
            (account.account_id, *kinds, d.isoformat())).fetchone() is not None
    return _step_down(start, acct) if lowered else start


def _state(conn: sqlite3.Connection, account: AccountRow, row: sqlite3.Row, cfg: SendConfig) -> DayState:
    day = date.fromisoformat(row["day"])
    return DayState(day=day, start_limit=row["send_limit"],
                    effective_limit=effective_limit(conn, account, day, cfg),
                    sent=signals.sends_on(conn, account.account_id, day, cfg), outcome=row["outcome"],
                    closed=row["closed_at"] is not None)


def _ensure_row(conn: sqlite3.Connection, account: AccountRow, d: date, cfg: SendConfig, now: str) -> sqlite3.Row:
    conn.execute("INSERT INTO send_day(account_id, day, send_limit, created_at) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(account_id, day) DO NOTHING",
                 (account.account_id, d.isoformat(), limit_for(conn, account, d, cfg), now))
    return _row(conn, account.account_id, d)


def open_day(conn: sqlite3.Connection, account: AccountRow, now: datetime, cfg: SendConfig) -> DayState | None:
    """Close the account's earlier open days, then open today. A closed day keeps `warning` or `hard_block`, else
    becomes `clean` when it had sends and the 3-day bounce rate is under 2%, `warning` (note `bounce rate`) when it
    is not, else stays NULL with the note `no sends`. Today's row gets `limit_for` and is
    written only when today is a business day (None on a weekend or holiday). Safe to call on every tick."""
    today = cal.ny_date(now, cfg)
    stamp = now.astimezone(timezone.utc).strftime(ISO_FMT)
    aid = account.account_id
    for old in conn.execute("SELECT day, outcome FROM send_day WHERE account_id = ? AND day < ? "
                            "AND closed_at IS NULL ORDER BY day", (aid, today.isoformat())).fetchall():
        if old["outcome"] in ("warning", "hard_block"):
            conn.execute("UPDATE send_day SET closed_at = ? WHERE account_id = ? AND day = ?",
                         (stamp, aid, old["day"]))
        else:
            outcome, note = _close_outcome(conn, account, date.fromisoformat(old["day"]), cfg)
            conn.execute("UPDATE send_day SET closed_at = ?, outcome = ?, outcome_note = ? "
                         "WHERE account_id = ? AND day = ?", (stamp, outcome, note, aid, old["day"]))
    if not cal.is_business_day(today, cfg):
        return None
    return _state(conn, account, _ensure_row(conn, account, today, cfg, stamp), cfg)


def today_state(conn: sqlite3.Connection, account: AccountRow, now: datetime, cfg: SendConfig) -> DayState | None:
    """Read only: today's row for the account as a `DayState`, or None when there is none."""
    row = _row(conn, account.account_id, cal.ny_date(now, cfg))
    return _state(conn, account, row, cfg) if row is not None else None


def _reason(kind: str, account: AccountRow, cfg: SendConfig) -> str:
    who = account.cfg.address
    return {
        "gmail_limit": f"Sending from {who} is paused because Gmail said the account hit its sending limit.",
        "gmail_policy": f"Sending from {who} is paused because Gmail said this account may not send.",
        "stop_replies_hard": f"Sending from {who} is paused because {cfg.stop_replies_hard} people asked you to "
                             f"stop within {cfg.stop_replies_days} days.",
        "bounce_rate_hard": f"Sending from {who} is paused because 2% or more of its last 100 first emails "
                            "bounced.",
    }[kind]


def note_signal(conn: sqlite3.Connection, kind: str, *, account: AccountRow, at: str, detail: dict, key: str,
                cfg: SendConfig, notify: Notifier, now: datetime) -> None:
    """Record a signal for the account and apply it. The signal lands on its own day (`signals.signal_day`), whose
    `outcome` becomes `warning`, or `hard_block` for a hard kind (never downgraded). A hard kind also pauses that
    account's `sender_account` row with `pause_kind 'hard_block'` (never `sender` row 1: that is his button, and it
    pauses every account), appends a `hard_block` event and tells him once per kind per account per day. A repeated
    `key` changes nothing. `now` is when the signal is processed (required: nothing here reads the real clock).
    An account that does not send (`sends = false`, account 1 resting) gets the signal and its day's outcome but
    never a pause or a notice: it sends nothing, and a block nobody can see would only hold his Resume.
    Callers pass only send-path Gmail limits as `gmail_limit` or `gmail_policy`: a 429 on a read is not one."""
    if kind not in signals.WARNING_KINDS + signals.HARD_KINDS:
        raise ValueError(f"unknown signal kind {kind!r}")
    stamp = now.astimezone(timezone.utc).strftime(ISO_FMT)
    aid = account.account_id
    if kind in signals.HARD_KINDS and conn.execute("SELECT 1 FROM sender_account WHERE account_id = ?",
                                                   (aid,)).fetchone() is None:
        # Nothing to pause and nobody to tell: `accounts.sync` (the tick's first step) creates the row.
        raise ValueError(f"account {aid} has no sender_account row, so {kind} cannot pause it")
    day = signals.signal_day(conn, aid, at, cfg, now=now)
    if not signals.record_signal(conn, kind, account_id=aid, at=at, detail={**detail, "day": day.isoformat()},
                                 key=key):
        return
    row = _ensure_row(conn, account, day, cfg, stamp)
    hard = kind in signals.HARD_KINDS
    if hard or row["outcome"] != "hard_block":
        conn.execute("UPDATE send_day SET outcome = ? WHERE account_id = ? AND day = ?",
                     ("hard_block" if hard else "warning", aid, day.isoformat()))
    if not hard or not account.cfg.sends:
        return  # a resting account (sends = false) cannot send, so a hard kind is recorded but pauses nothing
    reason = _reason(kind, account, cfg)
    if accounts.hard_pause(conn, aid, reason, stamp):
        append_event(conn, entity="sender", entity_id=aid, kind="hard_block", actor="sender", at=at,
                     detail={"kind": kind, "day": day.isoformat(), "reason": reason, "account_id": aid},
                     idempotency_key=f"hard_block:{key}")
    notify_once(conn, notify, kind, day, "Outreach sending paused", reason, at=stamp, account_id=aid)
