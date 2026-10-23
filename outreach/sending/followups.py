"""Planning follow-ups when a first email goes out, and stopping a planned one that will never be drafted.

Task 8 lands `plan_followups` and `stop_planned` with Task 11's signatures; Task 11 adds the lapse, the quiet close
and the stop sweep. Every function runs inside the caller's transaction and opens none.

A `planned` row holds no text, and the `message` CHECK refuses an empty subject or body in every state but
`planned`, so a planned row that will never be drafted is deleted (with an event on its touch), never moved to
`skipped`.
"""
import sqlite3
from datetime import date

from outreach.drafts.followups import canonical_company
from outreach.sending import calendar as cal
from outreach.sending.config import SendConfig
from outreach.store.timefmt import iso_utc, utcnow
from outreach.store.writes import append_event

__all__ = ["plan_followups", "stop_planned"]


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


def stop_planned(conn: sqlite3.Connection, message_id: int, *, reason: str, now: str) -> None:
    """Delete one `planned` follow-up and record it on its touch: event `followup_lapsed` when `reason` is
    `lapsed`, else `skipped_by_sender`, detail `{seq, due_on, reason}`. ValueError for any other state (a drafted
    or approved message is skipped, never deleted). Every path that stops a planned row calls this."""
    row = conn.execute("SELECT touch_id, seq, state, due_on FROM message WHERE message_id = ?",
                       (message_id,)).fetchone()
    if row is None:
        raise ValueError(f"no message {message_id}")
    if row["state"] != "planned":
        raise ValueError(f"message {message_id} is {row['state']}, not planned; only a planned row is deleted")
    conn.execute("DELETE FROM message WHERE message_id = ? AND state = 'planned'", (message_id,))
    append_event(conn, entity="touch", entity_id=row["touch_id"], company_id=_company_of_touch(conn, row["touch_id"]),
                 kind="followup_lapsed" if reason == "lapsed" else "skipped_by_sender", actor="sender",
                 at=now, detail={"seq": row["seq"], "due_on": row["due_on"], "reason": reason},
                 idempotency_key=f"followup-stopped:{row['touch_id']}:{row['seq']}:{row['due_on']}")
