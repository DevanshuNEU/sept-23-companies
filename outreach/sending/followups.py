"""Follow-ups (#9: one, at about 7 business days), their lapse, closing quiet touches and stopping messages.

`plan_followups` runs when a first email goes out; `lapse_followups`, `close_quiet_touches` and `sweep_stopped` run
on every tick; `stop_touch_messages` is what a reply or a bounce does to the touch's queue. Every function runs
inside the caller's transaction and opens none.

A `planned` row holds no text, and the `message` CHECK refuses an empty subject or body in every state but
`planned`, so a planned row that will never be drafted is deleted (with an event on its touch), never moved to
`skipped`.
"""
import sqlite3
from datetime import date

from outreach.drafts.followups import canonical_company
from outreach.sending import calendar as cal
from outreach.sending.config import SendConfig
from outreach.sending.queue import STOP_REASONS, Verdict
from outreach.sending.signals import as_datetime
from outreach.store.timefmt import iso_utc, utcnow
from outreach.store.writes import append_event

__all__ = ["plan_followups", "stop_planned", "stop_touch_messages", "lapse_followups", "close_quiet_touches",
           "sweep_stopped", "STOPPABLE"]

# The states of a message that has not gone out and is not in flight: what a stop removes from the queue. A
# `sending` or `send_unknown` row may already be out, so the sender settles it by Message-ID instead.
STOPPABLE = ("planned", "drafted", "approved", "failed")


def _company_of_touch(conn: sqlite3.Connection, touch_id: int) -> int:
    row = conn.execute("SELECT p.company_id FROM touch t JOIN person p ON p.person_id = t.person_id "
                       "WHERE t.touch_id = ?", (touch_id,)).fetchone()
    return canonical_company(conn, row["company_id"])


def plan_followups(conn: sqlite3.Connection, touch_id: int, first_sent_on: date, cfg: SendConfig, *,
                   now: str | None = None) -> int:
    """One `planned` follow-up per entry of `cfg.followup_business_days` (seq 1, 2, ...), each due that many
    business days after `first_sent_on` (the New York date of the first send). A seq that already has a row is
    left alone. Returns how many rows were added; each gets a `followup_planned` event. `now` stamps the rows
    (default the real clock; the sender passes the send time)."""
    stamp = iso_utc(now) if now else utcnow()
    company_id = _company_of_touch(conn, touch_id)
    added = 0
    for seq, days in enumerate(cfg.followup_business_days, start=1):
        due_on = cal.add_business_days(first_sent_on, days, cfg).isoformat()
        cur = conn.execute(
            "INSERT INTO message(touch_id, seq, state, due_on, saved_by, created_at, updated_at) "
            "VALUES (?, ?, 'planned', ?, 'sender', ?, ?) ON CONFLICT(touch_id, seq) DO NOTHING",
            (touch_id, seq, due_on, stamp, stamp))
        if cur.rowcount:
            added += 1
            append_event(conn, entity="message", entity_id=cur.lastrowid, company_id=company_id,
                         kind="followup_planned", actor="sender", at=stamp,
                         detail={"touch_id": touch_id, "seq": seq, "due_on": due_on},
                         idempotency_key=f"followup-planned:{touch_id}:{seq}:{due_on}")
    return added


def stop_planned(conn: sqlite3.Connection, message_id: int, *, reason: str, now: str, actor: str = "sender") -> None:
    """Delete one `planned` follow-up and record it on its touch: event `followup_lapsed` when `reason` is
    `lapsed`, else `skipped_by_sender`, detail `{seq, due_on, reason}`. ValueError for any other state (a drafted
    or approved message is skipped, never deleted). Every path that stops a planned row calls this: the sender's
    reply, bounce, lapse and sweep, and the API's stop reply (`actor` is the event's actor, `devanshu` there)."""
    row = conn.execute("SELECT touch_id, seq, state, due_on FROM message WHERE message_id = ?",
                       (message_id,)).fetchone()
    if row is None:
        raise ValueError(f"no message {message_id}")
    if row["state"] != "planned":
        raise ValueError(f"message {message_id} is {row['state']}, not planned; only a planned row is deleted")
    conn.execute("DELETE FROM message WHERE message_id = ? AND state = 'planned'", (message_id,))
    append_event(conn, entity="touch", entity_id=row["touch_id"], company_id=_company_of_touch(conn, row["touch_id"]),
                 kind="followup_lapsed" if reason == "lapsed" else "skipped_by_sender", actor=actor,
                 at=now, detail={"seq": row["seq"], "due_on": row["due_on"], "reason": reason},
                 idempotency_key=f"followup-stopped:{row['touch_id']}:{row['seq']}:{row['due_on']}")


def stop_touch_messages(conn: sqlite3.Connection, touch_id: int, *, reason: str, now: str,
                        actor: str = "sender") -> int:
    """Stop every message on the touch that has not gone out (#14 Decisions, "Holds and stops"): a `planned` row
    through `stop_planned`; a `drafted`, `approved` or `failed` row moves to `skipped` (revision on by one) with
    a message event `skipped_by_sender` and detail `{seq, due_on, reason}`. A `sending` or `send_unknown` row is
    left for the sender to settle. Returns how many were stopped."""
    stamp = iso_utc(now)
    company_id = _company_of_touch(conn, touch_id)
    stopped = 0
    for row in conn.execute(
            f"SELECT message_id, seq, state, due_on, revision FROM message WHERE touch_id = ? "
            f"AND state IN ({','.join('?' * len(STOPPABLE))}) ORDER BY seq", (touch_id, *STOPPABLE)).fetchall():
        if row["state"] == "planned":
            stop_planned(conn, row["message_id"], reason=reason, now=stamp, actor=actor)
            stopped += 1
            continue
        cur = conn.execute("UPDATE message SET state = 'skipped', updated_at = ?, revision = revision + 1 "
                           "WHERE message_id = ? AND state = ?", (stamp, row["message_id"], row["state"]))
        if not cur.rowcount:
            continue
        append_event(conn, entity="message", entity_id=row["message_id"], company_id=company_id,
                     kind="skipped_by_sender", actor=actor, at=stamp,
                     detail={"seq": row["seq"], "due_on": row["due_on"], "reason": reason},
                     idempotency_key=f"skipped_by_sender:{row['message_id']}:{row['revision'] + 1}")
        stopped += 1
    return stopped


def lapse_followups(conn: sqlite3.Connection, today: date, cfg: SendConfig, *, now: str | None = None) -> int:
    """A follow-up still `planned` (never drafted) `followup_lapse_business_days` (5) business days after its due
    date lapses: the row is deleted with a `followup_lapsed` event on its touch (`stop_planned`). A lapsed follow-up
    is not revived; a re-plan is a new row. Returns how many lapsed. `now` stamps the events (default the clock)."""
    stamp = iso_utc(now) if now else utcnow()
    lapsed = 0
    for row in conn.execute("SELECT message_id, due_on FROM message WHERE state = 'planned' AND due_on IS NOT NULL "
                            "AND due_on < ? ORDER BY due_on, message_id", (today.isoformat(),)).fetchall():
        if cal.business_days_between(date.fromisoformat(row["due_on"]), today, cfg) >= cfg.followup_lapse_business_days:
            stop_planned(conn, row["message_id"], reason="lapsed", now=stamp)
            lapsed += 1
    return lapsed


_WAITING = ("planned", "drafted", "approved", "sending", "send_unknown")


def close_quiet_touches(conn: sqlite3.Connection, today: date, cfg: SendConfig, *, now: str | None = None) -> int:
    """An `open` touch whose email went out, with nothing waiting (no message `planned`, `drafted`, `approved`,
    `sending` or `send_unknown`), closes `no_reply` once `close_after_business_days` (7) business days have passed
    since its last sent message's New York date. Event `closed` on the touch. Returns how many closed."""
    stamp = iso_utc(now) if now else utcnow()
    closed = 0
    for row in conn.execute(
            "SELECT t.touch_id, max(m.sent_at) AS last_sent FROM touch t "
            "JOIN message m ON m.touch_id = t.touch_id AND m.state = 'sent' "
            "WHERE t.state = 'open' AND t.first_sent_at IS NOT NULL AND NOT EXISTS (SELECT 1 FROM message w "
            f"WHERE w.touch_id = t.touch_id AND w.state IN ({','.join('?' * len(_WAITING))})) "
            "GROUP BY t.touch_id ORDER BY t.touch_id", _WAITING).fetchall():
        last = cal.ny_date(as_datetime(row["last_sent"]), cfg)
        if cal.business_days_between(last, today, cfg) < cfg.close_after_business_days:
            continue
        if _close(conn, row["touch_id"], "no_reply", stamp, detail={"last_sent": row["last_sent"]}):
            closed += 1
    return closed


def _close(conn: sqlite3.Connection, touch_id: int, reason: str, stamp: str, *, detail: dict) -> bool:
    """An `open` touch becomes `closed` with `reason`, and event `closed`. False when it was not open."""
    if not conn.execute("UPDATE touch SET state = 'closed', close_reason = ?, closed_at = ?, updated_at = ?, "
                        "revision = revision + 1 WHERE touch_id = ? AND state = 'open'",
                        (reason, stamp, stamp, touch_id)).rowcount:
        return False
    append_event(conn, entity="touch", entity_id=touch_id, company_id=_company_of_touch(conn, touch_id),
                 kind="closed", actor="sender", at=stamp, detail={"reason": reason, **detail},
                 idempotency_key=f"closed:{touch_id}:{stamp}")
    return True


def sweep_stopped(conn: sqlite3.Connection, verdicts: list[Verdict], *, now: str) -> int:
    """For each verdict with a stop reason (the queue's `STOP_REASONS`), stop the whole touch's queue: the approved
    message and the touch's other `drafted`, `approved` and `failed` messages move to `skipped`, its `planned` ones
    are deleted through `stop_planned`, each with a `skipped_by_sender` event naming the reason. An `open` touch
    closes `company_closed` or `no_go`; a replied, bounced or closed touch keeps its state. Waits (held, paused and
    the rest) are left alone. Returns how many messages were stopped."""
    stamp = iso_utc(now)
    stopped = 0
    for verdict in verdicts:
        if verdict.reason not in STOP_REASONS:
            continue
        row = conn.execute("SELECT m.state, m.touch_id, t.state AS touch_state FROM message m "
                           "JOIN touch t ON t.touch_id = m.touch_id WHERE m.message_id = ?",
                           (verdict.message_id,)).fetchone()
        if row is None or row["state"] != "approved":
            continue  # moved on since the verdict was read
        stopped += stop_touch_messages(conn, row["touch_id"], reason=verdict.reason, now=stamp)
        if row["touch_state"] == "open" and verdict.reason in ("company_closed", "no_go"):
            _close(conn, row["touch_id"], verdict.reason, stamp, detail={"message_id": verdict.message_id})
    return stopped
