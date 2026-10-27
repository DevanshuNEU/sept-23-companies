"""The sender's status and the queue's schedule, for the API (#11) and the daily plan (#15) to read.

Read only: nothing here writes, opens a transaction or calls Gmail. The snapshot is what the Outbox's sending strip
shows; `queue_schedule` is the same `verdicts` the tick uses, with the same gate (paused, signed out, old sender
loaded, Message-ID not proven) and the same one first email while it is not proven (`queue.first_only`), so "when
it will go" on the Outbox is the sender's own answer and never a second copy of the rules. A message waiting on the
proof reads the line for where the first email stands (`reason_text`).

The store cannot say whether the old mailer's launchd job is loaded (only a `launchctl` call can), so a caller that
knows passes `old_sender_loaded=True`; the snapshot then shows `paused_by_you` with a reason, and every message
waits as `old_sender_running`, exactly as the tick treats it.
"""
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from outreach.sending import calendar as cal
from outreach.sending import proof, queue, ramp, signals
from outreach.sending.config import SendConfig
from outreach.sending.queue import REASON_TEXT, STOP_REASONS, Verdict
from outreach.store.timefmt import ISO_FMT

__all__ = ["SendingSnapshot", "sending_snapshot", "queue_schedule", "reason_text", "OLD_SENDER_TEXT",
           "NOTHING_APPROVED"]

OLD_SENDER_TEXT = "The old mail sender is still running, so the new one waits until it is stopped."
NOTHING_APPROVED = "Nothing is approved and waiting to send."
ALL_STOPPED = "Every email waiting is stopped and will be removed."
_GMAIL_TEXT = {"gmail_limit": "Gmail said the account hit a sending limit",
               "gmail_policy": "Gmail said this account may not send"}


@dataclass(frozen=True)
class SendingSnapshot:
    state: str  # the web SenderState values
    paused_reason: str
    paused_at: str | None
    gmail_message: str | None
    sent_today: int
    limit_today: int
    start_limit: int
    ramp_level: int
    ramp_levels: tuple[int, ...]
    next_limit_if_clean: int
    last_step_down: tuple[str, str] | None
    bounce_rate_3d: float
    gmail_errors_today: int
    stop_replies_today: int
    imported_unverified: int
    next_send: tuple[int, str, str] | None
    none_reason: str | None
    revision: int


def _stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime(ISO_FMT)


def _gate(conn: sqlite3.Connection, old_sender_loaded: bool) -> str | None:
    """The reason the whole tick would give for sending nothing, in the tick's own order. A fresh store's
    `unknown` sign-in is not a block: the first tick finds out."""
    row = conn.execute("SELECT paused, auth_state FROM sender WHERE sender_id = 1").fetchone()
    if row["paused"]:
        return "sender_paused"
    if row["auth_state"] in ("signed_out", "wrong_account"):
        return "signed_out"
    if old_sender_loaded:
        return "old_sender_running"
    if proof.phase(conn) not in proof.OPEN:
        return "message_id_unproven"
    return None


def reason_text(reason: str, stage: str) -> str:
    """The sentence for a queue reason; `message_id_unproven` says where the first email stands (`stage`, a
    `proof` phase)."""
    if reason == "message_id_unproven":
        return proof.PHASE_TEXT.get(stage, REASON_TEXT[reason])
    return REASON_TEXT[reason]


def queue_schedule(conn: sqlite3.Connection, now: datetime, cfg: SendConfig, *,
                   old_sender_loaded: bool = False) -> dict[int, Verdict]:
    """The tick's verdict for every approved message, by message id, in queue order: the reason it waits (or
    `ready`) and, for a wait that ends by itself, when. While Gmail is unproven and no email is claimed yet, only
    the first email keeps its own verdict, as in the tick."""
    day = ramp.today_state(conn, now, cfg)
    gate = _gate(conn, old_sender_loaded)
    found = queue.verdicts(conn, now, cfg, day=day, gate=gate)
    if gate is None and proof.phase(conn) == proof.FIRST_SEND:
        found = queue.first_only(found)
    return {v.message_id: v for v in found}


def _state(row: sqlite3.Row, now: datetime, cfg: SendConfig, old_sender_loaded: bool) -> tuple[str, str]:
    """The state a person should be told, and the reason line that goes with it. A pause or a sign-in problem wins
    (it needs an action), then the old sender, then a day off, then the hours (read on the New York clock)."""
    if row["paused"] and row["pause_kind"] == "hard_block":
        return "paused_hard_block", row["paused_reason"]
    if row["paused"]:
        return "paused_by_you", row["paused_reason"]
    if row["auth_state"] in ("signed_out", "wrong_account"):
        return "signed_out", row["auth_error"]
    if old_sender_loaded:
        return "paused_by_you", OLD_SENDER_TEXT
    local = now.astimezone(ZoneInfo(cfg.day_tz))
    if not cal.is_business_day(local.date(), cfg):
        return "weekend", ""
    if not cfg.window_start <= local.time() <= cfg.window_end:
        return "outside_window", ""
    return "running", ""


def _levels(cfg: SendConfig) -> tuple[int, ...]:
    return tuple(range(cfg.ramp_floor, cfg.ramp_ceiling + 1, cfg.ramp_step))


def _gmail_message(conn: sqlite3.Connection, today: str) -> tuple[int, str | None]:
    """How many Gmail limit or policy signals belong to today, and a plain line for the newest."""
    rows = conn.execute(
        "SELECT kind, json_extract(detail, '$.error_kind') AS error_kind, json_extract(detail, '$.status') AS status "
        "FROM event WHERE entity = 'sender' AND kind IN ('gmail_limit', 'gmail_policy') "
        "AND json_extract(detail, '$.day') = ? ORDER BY event_id DESC", (today,)).fetchall()
    if not rows:
        return 0, None
    newest = rows[0]
    text = _GMAIL_TEXT[newest["kind"]]
    extra = ", ".join(part for part in (newest["error_kind"], f"HTTP {newest['status']}" if newest["status"] else "")
                      if part)
    return len(rows), f"{text} ({extra})" if extra else text


def _last_step_down(conn: sqlite3.Connection) -> tuple[str, str] | None:
    row = conn.execute("SELECT day, outcome, outcome_note FROM send_day WHERE outcome IN ('warning', 'hard_block') "
                       "ORDER BY day DESC LIMIT 1").fetchone()
    if row is None:
        return None
    if row["outcome_note"]:
        return row["day"], row["outcome_note"]
    kinds = signals.WARNING_KINDS + signals.HARD_KINDS
    event = conn.execute(
        f"SELECT kind FROM event WHERE entity = 'sender' AND kind IN ({','.join('?' * len(kinds))}) "
        "AND json_extract(detail, '$.day') = ? ORDER BY event_id DESC LIMIT 1", (*kinds, row["day"])).fetchone()
    return row["day"], event["kind"] if event is not None else row["outcome"]


def _stop_replies(conn: sqlite3.Connection, today, cfg: SendConfig) -> int:
    """Email touches tagged `asked_to_stop` whose reply is dated today (New York), from the touch or from its
    `outreach_touch` (an undone one does not count); a touch tagged in both counts once."""
    start, end = cal.ny_day_bounds(today, cfg)
    return conn.execute(
        "SELECT COUNT(*) FROM ("
        " SELECT touch_id FROM touch WHERE reply_kind = 'asked_to_stop' AND replied_at >= ? AND replied_at < ?"
        " UNION"
        " SELECT email_touch_id FROM outreach_touch WHERE email_touch_id IS NOT NULL"
        "  AND reply_kind = 'asked_to_stop' AND undone_at IS NULL AND replied_at >= ? AND replied_at < ?)",
        (start, end, start, end)).fetchone()[0]


def _next_send(schedule: dict[int, Verdict], queued: dict[int, queue.Queued], now: datetime, stage: str):
    """The message that goes first and when (`ready` is now), else why none does."""
    best = None
    for index, verdict in enumerate(schedule.values()):
        when = now if verdict.reason == "ready" else verdict.earliest_at
        if when is not None and (best is None or (when, index) < best[0]):
            best = ((when, index), verdict.message_id)
    if best is not None:
        when, message_id = best[0][0], best[1]
        return (message_id, _stamp(when), queued[message_id].tz), None
    if not schedule:
        return None, NOTHING_APPROVED
    waiting = next((v for v in schedule.values() if v.reason not in STOP_REASONS), None)
    return None, reason_text(waiting.reason, stage) if waiting is not None else ALL_STOPPED


def sending_snapshot(conn: sqlite3.Connection, now: datetime, cfg: SendConfig, *,
                     old_sender_loaded: bool = False) -> SendingSnapshot:
    """Everything the sending strip shows, read from the store. Today's limit is the day's row when it has one,
    else what `limit_for` will give it (the first tick has not run yet), and 0 on a day the sender does not run."""
    sender = conn.execute("SELECT * FROM sender WHERE sender_id = 1").fetchone()
    today = cal.ny_date(now, cfg)
    state, reason = _state(sender, now, cfg, old_sender_loaded)
    row = conn.execute("SELECT send_limit FROM send_day WHERE day = ?", (today.isoformat(),)).fetchone()
    start = row["send_limit"] if row is not None else ramp.limit_for(conn, today, cfg)
    levels = _levels(cfg)
    level = max((i for i, value in enumerate(levels, start=1) if value <= start), default=1)
    errors, gmail_message = _gmail_message(conn, today.isoformat())
    schedule = queue_schedule(conn, now, cfg, old_sender_loaded=old_sender_loaded)
    queued = {q.message_id: q for q in queue.load_queue(conn, cfg)}
    next_send, none_reason = _next_send(schedule, queued, now, proof.phase(conn))
    return SendingSnapshot(
        state=state, paused_reason=reason, paused_at=sender["paused_at"] if sender["paused"] else None,
        gmail_message=gmail_message, sent_today=signals.sends_on(conn, today, cfg),
        limit_today=ramp.effective_limit(conn, today, cfg) if cal.is_business_day(today, cfg) else 0,
        start_limit=start, ramp_level=level, ramp_levels=levels,
        next_limit_if_clean=min(start + cfg.ramp_step, cfg.ramp_ceiling), last_step_down=_last_step_down(conn),
        bounce_rate_3d=signals.bounce_rate_3d(conn, today, cfg), gmail_errors_today=errors,
        stop_replies_today=_stop_replies(conn, today, cfg),
        imported_unverified=conn.execute(
            "SELECT COUNT(*) FROM event WHERE entity = 'message' AND kind = 'import_not_in_sent'").fetchone()[0],
        next_send=next_send, none_reason=none_reason, revision=sender["revision"])
