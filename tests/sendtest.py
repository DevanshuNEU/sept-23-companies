"""Seed helpers for the sending engine's tests (every sending task adds to this file).

`seed_message` stands in for #13 (a draft) and for Devanshu's click (approval): it is test seeding, never product
code. Every helper writes the smallest valid rows and takes moments as aware datetimes or canonical UTC text.
"""
import itertools
import sqlite3
from datetime import date, datetime, timezone

from outreach.store.timefmt import ISO_FMT, iso_utc

__all__ = ["MON", "UTC", "iso", "seed_email_person", "seed_message", "seed_sent", "bounce", "put_day",
           "settle_and_send"]

UTC = timezone.utc
# Monday 2026-11-02, 09:00 in New York (daylight saving ended on the 1st, so UTC-5).
MON = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)

_counter = itertools.count(1)
_NOW_SEED = "2026-11-02T12:00:00Z"
_APPROVED_AT = "2026-11-02T13:00:00Z"


def iso(at: datetime | str) -> str:
    """Canonical UTC text for an aware datetime or any ISO-8601 string."""
    if isinstance(at, datetime):
        if at.tzinfo is None:
            raise ValueError("a naive datetime is ambiguous; pass an aware one")
        return at.astimezone(UTC).strftime(ISO_FMT)
    return iso_utc(at)


def seed_email_person(conn: sqlite3.Connection, company_id: int, name: str, email: str, *,
                      role_kind: str = "engineer", tz: str = "America/New_York") -> int:
    """A person with an address at the company. Returns the person id."""
    return conn.execute(
        "INSERT INTO person(company_id, name, role_kind, email, tz, channel, added_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'email', 'devanshu', ?, ?)",
        (company_id, name, role_kind, email, tz, _NOW_SEED, _NOW_SEED)).lastrowid


def _touch_for(conn: sqlite3.Connection, person_id: int) -> int:
    row = conn.execute("SELECT touch_id FROM touch WHERE person_id = ?", (person_id,)).fetchone()
    if row:
        return row[0]
    return conn.execute("INSERT INTO touch(person_id, created_at, updated_at) VALUES (?, ?, ?)",
                        (person_id, _NOW_SEED, _NOW_SEED)).lastrowid


def seed_message(conn: sqlite3.Connection, person_id: int, *, state: str = "approved", seq: int = 0,
                 subject: str = "Hi", body: str = "Hello there.", approved_at: str | None = None,
                 due_on: str | None = None) -> int:
    """A message in `planned`, `drafted` or `approved` (the touch is created when missing). A message is born
    planned or drafted and approval is an UPDATE, so `approved` does both. Returns the message id. A follow-up
    (seq above 0) needs `due_on` (default 2026-11-02) and, past `planned`, a touch whose first email was sent."""
    if state not in ("planned", "drafted", "approved"):
        raise ValueError(f"seed_message makes planned, drafted or approved messages, not {state!r}; use seed_sent")
    touch_id = _touch_for(conn, person_id)
    if seq > 0 and due_on is None:
        due_on = "2026-11-02"
    born = "planned" if state == "planned" else "drafted"
    if state == "planned":
        subject = body = ""
    message_id = conn.execute(
        "INSERT INTO message(touch_id, seq, state, subject, body, due_on, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (touch_id, seq, born, subject, body, due_on, _NOW_SEED, _NOW_SEED)).lastrowid
    if state == "approved":
        conn.execute("UPDATE message SET state = 'approved', approved_at = ? WHERE message_id = ?",
                     (iso(approved_at) if approved_at else _APPROVED_AT, message_id))
    return message_id


def seed_sent(conn: sqlite3.Connection, person_id: int, *, at: datetime | str, seq: int = 0,
              rfc: str | None = None, thread: str | None = None) -> int:
    """A message already sent at `at` (the touch is created when missing; a first email also fills the touch's
    first_sent_at, first_rfc_id and gmail_thread_id). Returns the message id."""
    n = next(_counter)
    when = iso(at)
    touch_id = _touch_for(conn, person_id)
    rfc = rfc or f"<seed.{n}@sendtest.local>"
    row = conn.execute("SELECT first_sent_at, gmail_thread_id FROM touch WHERE touch_id = ?", (touch_id,)).fetchone()
    if seq == 0:
        thread = thread or f"thread-{touch_id}"
        conn.execute("UPDATE touch SET first_sent_at = ?, first_rfc_id = ?, gmail_thread_id = ?, updated_at = ? "
                     "WHERE touch_id = ?", (when, rfc, thread, when, touch_id))
        due_on = None
    else:
        if row["first_sent_at"] is None:
            raise ValueError("a follow-up is sent only after the first email")
        due_on = when[:10]
    return conn.execute(
        "INSERT INTO message(touch_id, seq, state, subject, body, due_on, sent_at, rfc_message_id, gmail_message_id, "
        "created_at, updated_at) VALUES (?, ?, 'sent', ?, 'Hello there.', ?, ?, ?, ?, ?, ?)",
        (touch_id, seq, f"Hello {n}", due_on, when, rfc, f"gmail-{n}", when, when)).lastrowid


def bounce(conn: sqlite3.Connection, touch_id: int, at: datetime | str) -> None:
    """The touch bounced at `at`."""
    when = iso(at)
    conn.execute("UPDATE touch SET state = 'bounced', bounced_at = ?, updated_at = ? WHERE touch_id = ?",
                 (when, when, touch_id))


def put_day(conn: sqlite3.Connection, day: date | str, limit: int, *, outcome: str | None = None,
            closed: bool = False) -> None:
    """A `send_day` row as the ramp would have left it (for tests that start part way up the ramp)."""
    day = day.isoformat() if isinstance(day, date) else day
    conn.execute("INSERT INTO send_day(day, send_limit, outcome, closed_at, created_at) VALUES (?, ?, ?, ?, ?)",
                 (day, limit, outcome, _NOW_SEED if closed else None, _NOW_SEED))


def settle_and_send(conn: sqlite3.Connection, client, clock, rng, cfg) -> str | None:
    """One tick's settle and send steps, in the order Task 13's tick runs them (a test stand-in until it lands):
    leftover claims; a Sent scan for each unclear send with no transaction open; reconcile and requeue in one
    transaction; then the queue's pick and one send. Returns what `send_one` returned, the kind of the GmailError
    it raised, or None when nothing was ready."""
    from outreach.sending import claim, queue, ramp
    from outreach.sending.mail import GmailError
    from outreach.store import transaction
    now = clock()
    claim.leftover_claims(conn, iso(now))
    found = {u.message_id: (*client.find_sent(u.rfc_id, u.not_before_ms, send_returned_ms=u.send_returned_ms),
                            u.rfc_id)
             for u in claim.unclear(conn)}
    with transaction(conn):
        claim.reconcile(conn, found, now=iso(now), cfg=cfg, rng=rng)
        claim.requeue_failed(conn, now=iso(now), cfg=cfg)
        day = ramp.open_day(conn, now, cfg)
    picked = queue.pick(queue.verdicts(conn, now, cfg, day=day, gate=None), queue.load_queue(conn, cfg))
    if picked is None:
        return None
    try:
        return claim.send_one(conn, client, picked, cfg, now=iso(now), clock=clock, rng=rng,
                              account=cfg.from_address)
    except GmailError as err:
        return err.kind
