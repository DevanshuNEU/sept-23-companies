"""Seeders for the review package's tests: a valid review item with the rows its kind needs."""
import itertools
import sqlite3

from outreach.review.items import raise_item
from outreach.review.kinds import KINDS
from outreach.store.timefmt import utcnow
from tests.storetest import seed_board, seed_company, seed_posting

_keys = itertools.count(1)


def seed_person(conn: sqlite3.Connection, company_id: int, name: str = "Pat") -> int:
    now = utcnow()
    return conn.execute(
        "INSERT INTO person(company_id, name, role_kind, email, channel, added_by, created_at, updated_at) "
        "VALUES (?, ?, 'engineer', ?, 'email', 'devanshu', ?, ?)",
        (company_id, name, f"{name.lower()}@example.com", now, now)).lastrowid


def seed_item(conn: sqlite3.Connection, kind: str = "size_unknown", company_id: int | None = None, **kw) -> int:
    """A valid open item raised through `raise_item`. A kind that needs a board or a posting gets one; every call
    that does not pass `evidence_key` gets a new one, so two calls make two items."""
    if company_id is None:
        company_id = seed_company(conn)
    if kind in ("board_unreadable", "board_empty") and "board_id" not in kw:
        kw["board_id"] = seed_board(conn, company_id, token=f"seed-{next(_keys)}")
    if kind in ("sponsorship_unclear", "reader_disagrees") and "posting_id" not in kw:
        kw["posting_id"] = seed_posting(conn, company_id, "Software Engineer")
    kw.setdefault("reason", f"A seeded {kind} item.")
    kw.setdefault("evidence_key", f"seed-{next(_keys)}")
    kw.setdefault("raised_by", KINDS[kind].raised_by[0])
    return raise_item(conn, kind=kind, company_id=company_id, **kw).review_id


def seed_status(conn: sqlite3.Connection, company_id: int, status: str) -> None:
    """Move a company to a status, with the reason the schema demands for it."""
    now = utcnow()
    if status == "closed":
        conn.execute("UPDATE company SET status = 'closed', close_reason = 'other', status_at = ? WHERE company_id = ?",
                     (now, company_id))
    elif status == "removed":
        conn.execute("UPDATE company SET status = 'removed', remove_reason = 'seeded', status_at = ? "
                     "WHERE company_id = ?", (now, company_id))
    else:
        conn.execute("UPDATE company SET status = ?, status_at = ? WHERE company_id = ?", (status, now, company_id))



def seed_plan_row(conn: sqlite3.Connection, company_id: int, list_date: str, state: str = "proposed", *,
                  person_id: int | None = None, channel: str | None = None) -> int:
    """One row on the daily list of `list_date` (the list is made when it is missing). A row with a person needs a
    channel, so the default for one is `email`. Returns the rank."""
    now = utcnow()
    conn.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES (?, ?, 60) ON CONFLICT DO NOTHING",
                 (list_date, now))
    rank = conn.execute("SELECT COALESCE(MAX(rank), 0) + 1 FROM daily_list_entry WHERE list_date = ?",
                        (list_date,)).fetchone()[0]
    if person_id is not None and channel is None:
        channel = "email"
    decided = None if state == "proposed" else now
    conn.execute(
        "INSERT INTO daily_list_entry(list_date, company_id, list_group, rank, state, proposed_at, decided_at, "
        "person_id, channel) VALUES (?, ?, 'level_role', ?, ?, ?, ?, ?, ?)",
        (list_date, company_id, rank, state, now, decided, person_id, channel))
    return rank


def seed_email_person(conn: sqlite3.Connection, company_id: int, email: str | None = None) -> int:
    """A person at the company with an address of their own."""
    n = next(_keys)
    return seed_person(conn, company_id, f"Emailer{n}") if email is None else conn.execute(
        "INSERT INTO person(company_id, name, role_kind, email, channel, added_by, created_at, updated_at) "
        "VALUES (?, ?, 'engineer', ?, 'email', 'devanshu', ?, ?)",
        (company_id, email.split("@")[0], email, utcnow(), utcnow())).lastrowid


def seed_queued_message(conn: sqlite3.Connection, person_id: int, state: str = "drafted", *, seq: int = 0,
                        touch_state: str = "open") -> int:
    """A message in `state` on the person's touch (made when it is missing), with every column its state needs.
    `touch_state` is `open` or `closed`. Returns the message id."""
    now = utcnow()
    row = conn.execute("SELECT touch_id FROM touch WHERE person_id = ?", (person_id,)).fetchone()
    if row is None:
        touch_id = conn.execute("INSERT INTO touch(person_id, created_at, updated_at) VALUES (?, ?, ?)",
                                (person_id, now, now)).lastrowid
    else:
        touch_id = row["touch_id"]
    if touch_state == "closed":
        conn.execute("UPDATE touch SET state = 'closed', close_reason = 'skipped', closed_at = ? WHERE touch_id = ?",
                     (now, touch_id))
    n = next(_keys)
    first = state if state in ("planned", "drafted", "sent") else "drafted"
    sent = state == "sent"
    mid = conn.execute(
        "INSERT INTO message(touch_id, seq, state, subject, body, due_on, rfc_message_id, sent_at, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (touch_id, seq, first, "" if first == "planned" else "Hello", "" if first == "planned" else "A note.",
         None if seq == 0 else "2026-11-20", f"<sent-{n}@seed>" if sent else None, now if sent else None, now,
         now)).lastrowid
    # The store's triggers allow only legal moves, so a later state is reached one move at a time.
    steps = {"approved": ("approved",), "sending": ("approved", "sending"),
             "send_unknown": ("approved", "sending", "send_unknown"),
             "failed": ("approved", "sending", "send_unknown", "failed"), "skipped": ("skipped",)}.get(state, ())
    for step in steps:
        pending = (f"<pending-{n}@seed>", now) if step in ("sending", "send_unknown") else (None, None)
        conn.execute("UPDATE message SET state = ?, approved_at = COALESCE(approved_at, ?), pending_rfc_id = ?, "
                     "claimed_at = ? WHERE message_id = ?",
                     (step, now if step != "skipped" else None, pending[0], pending[1], mid))
    return mid
