"""Seeders for the daily people plan tests (#15). Every plan task adds to this file.

All of them write through the real tables and the real writers (`log_touch`, `record_outcome`), so a test states only
what it is about.
"""
from __future__ import annotations

import itertools
import json
import sqlite3
from datetime import date

from outreach.store.timefmt import utcnow
from outreach.store.writes import log_touch, record_outcome

__all__ = ["TODAY", "seed_scored_company", "seed_person", "seed_source", "seed_outbound", "seed_log_only",
           "seed_send_day",
           "seed_email_touch", "seed_message", "seed_daily_entry"]

TODAY = date(2026, 11, 10)
_counter = itertools.count(1)


def _at(day: date, hour: int = 15) -> str:
    """A UTC timestamp that falls on `day` in New York all year (15:00Z is 10:00 or 11:00 there)."""
    return f"{day.isoformat()}T{hour:02d}:00:00Z"


def seed_scored_company(conn: sqlite3.Connection, name: str, score: float, lanes, *, status: str = "candidate",
                        with_posting: bool = True, size_high: int | None = None, version: str = "rk-test") -> int:
    now = utcnow()
    close_reason = "other" if status == "closed" else None
    remove_reason = "seeded as removed" if status == "removed" else None
    company_id = conn.execute(
        "INSERT INTO company(name, status, close_reason, remove_reason, status_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)", (name, status, close_reason, remove_reason, now, now)).lastrowid
    posting_id = None
    if with_posting:
        n = next(_counter)
        posting_id = conn.execute(
            "INSERT INTO posting(company_id, source_key, title, location, country, url, posted_on, first_seen_at, "
            "last_seen_at) VALUES (?, ?, 'Software Engineer', 'Remote', 'US', ?, ?, ?, ?)",
            (company_id, f"url:plan-seed-{n}", f"https://jobs.example.com/{n}", TODAY.isoformat(), now, now)
        ).lastrowid
    if size_high is not None:
        conn.execute(
            "INSERT INTO evidence(company_id, fact, value, num_low, num_high, confidence, source_kind, observed_on, "
            "recorded_at) VALUES (?, 'size', ?, 1, ?, 'confirmed', 'website', ?, ?)",
            (company_id, f"1-{size_high}", size_high, TODAY.isoformat(), now))
    signals = {"values": {}, "reasons": ["Open new grad role: Software Engineer, posted 3 days ago"], "hide": None,
               "flags": [], "best_posting_id": posting_id, "ignored_postings": 0}
    conn.execute(
        "INSERT INTO company_score(company_id, ranker_version, score, signals, lanes, scored_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (company_id, version, score, json.dumps(signals, sort_keys=True), json.dumps(list(lanes)), now))
    return company_id


def seed_person(conn: sqlite3.Connection, company_id: int, name: str, role_kind: str = "engineer", *,
                email: str | None = None, linkedin_url: str | None = None, sources=(),
                added_by: str = "claude") -> int:
    """A person at the company. `sources` are `(kind, url, quote)` tuples observed today; an `email` source gets the
    basis `published`. The channel is a hint for the CHECK only: email with an address, else LinkedIn."""
    now = utcnow()
    channel = "email" if email else "linkedin"
    person_id = conn.execute(
        "INSERT INTO person(company_id, name, role_kind, email, linkedin_url, channel, linkedin_reason, added_by, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (company_id, name, role_kind, email, linkedin_url, channel, None if email else "no_email", added_by, now, now)
    ).lastrowid
    for kind, url, quote in sources:
        conn.execute(
            "INSERT INTO person_source(person_id, kind, url, quote, email_basis, observed_on, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (person_id, kind, url, quote, "published" if kind == "email" else None, TODAY.isoformat(), now))
    return person_id


def seed_source(conn: sqlite3.Connection, person_id: int, kind: str, url: str, *, observed_on: date | None = TODAY,
                quote: str = "", email_basis: str | None = None, label: str = "") -> int:
    """One `person_source` row with a chosen observation date (an `email` source needs a basis)."""
    return conn.execute(
        "INSERT INTO person_source(person_id, kind, url, label, quote, email_basis, observed_on, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (person_id, kind, url, label, quote, email_basis, observed_on.isoformat() if observed_on else None,
         utcnow())).lastrowid


def seed_outbound(conn: sqlite3.Connection, person_id: int, company_id: int, channel: str, sent_on: date, *,
                  accepted_on: date | None = None, replied_on: date | None = None, lane: str | None = None,
                  context_source: str = "plan", key: str | None = None) -> int:
    """An `outreach_touch` through `log_touch` and `record_outcome`; the context carries `source` and `lane`."""
    context = {"source": context_source, "lane": lane, "roleKind": None, "roleAgeDays": None,
               "rankerVersion": None, "signals": {}, "roleNamed": False}
    touch_id = log_touch(conn, person_id=person_id, company_id=company_id, channel=channel,
                         sent_at=_at(sent_on), context=context,
                         idempotency_key=key or f"plan-seed-{next(_counter)}")
    revision = 1
    if accepted_on is not None:
        revision = record_outcome(conn, touch_id, kind="accepted", at=_at(accepted_on, 16), revision=revision)
    if replied_on is not None:
        record_outcome(conn, touch_id, kind="replied", at=_at(replied_on, 17), revision=revision)
    return touch_id


def seed_log_only(conn: sqlite3.Connection, person_id: int, kind: str, on: date, *,
                  undoes: int | None = None) -> int:
    """A `linkedin_log` row with no touch behind it (imported history). `undoes` is for `kind='undo'`."""
    at = _at(on)
    return conn.execute(
        "INSERT INTO linkedin_log(person_id, kind, undoes, at, recorded_at) VALUES (?, ?, ?, ?, ?)",
        (person_id, kind, undoes, at, at)).lastrowid


def seed_send_day(conn: sqlite3.Connection, day: date, limit: int, outcome: str | None = None) -> None:
    conn.execute("INSERT INTO send_day(day, send_limit, outcome, created_at) VALUES (?, ?, ?, ?)",
                 (day.isoformat(), limit, outcome, utcnow()))


def seed_email_touch(conn: sqlite3.Connection, person_id: int, first_sent_on: date | None, *, state: str = "open",
                     company_id: int | None = None, outreach: bool = False) -> int:
    """A legacy email `touch` for the person, its first email sent on `first_sent_on` (None: not sent yet). With
    `outreach` and a `company_id` it also gets the `outreach_touch` the sender writes at the first send."""
    now = utcnow()
    first = _at(first_sent_on) if first_sent_on else None
    later = _at(first_sent_on, 17) if first_sent_on else now
    touch_id = conn.execute(
        "INSERT INTO touch(person_id, origin, state, first_sent_at, replied_at, reply_kind, closed_at, close_reason, "
        "created_at, updated_at) VALUES (?, 'import', ?, ?, ?, ?, ?, ?, ?, ?)",
        (person_id, state, first, later if state == "replied" else None, "interested" if state == "replied" else None,
         later if state == "closed" else None, "no_reply" if state == "closed" else None, now, now)).lastrowid
    if outreach:
        context = {"source": "own", "lane": None, "roleKind": None, "roleAgeDays": None, "rankerVersion": None,
                   "signals": {}, "roleNamed": False}
        log_touch(conn, person_id=person_id, company_id=company_id, channel="email", sent_at=first, context=context,
                  email_touch_id=touch_id, idempotency_key=f"plan-seed-email-{next(_counter)}")
    return touch_id


def seed_message(conn: sqlite3.Connection, touch_id: int, seq: int, state: str, *, sent_on: date | None = None,
                 due_on: date | None = None) -> int:
    """A message in `state` (planned, drafted, approved, sending, send_unknown, failed, skipped or sent). The store
    makes a message born planned, drafted or sent and moves it by UPDATE, so this does the same."""
    now = utcnow()
    n = next(_counter)
    born = "sent" if state == "sent" else ("planned" if state == "planned" else "drafted")
    if seq >= 1 and due_on is None:
        due_on = date(2026, 11, 12)
    full = born != "planned"
    message_id = conn.execute(
        "INSERT INTO message(touch_id, seq, state, subject, body, due_on, sent_at, rfc_message_id, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (touch_id, seq, born, "s" if full else "", "b" if full else "", due_on.isoformat() if due_on else None,
         _at(sent_on) if state == "sent" else None, f"<plan-seed-{n}@x>" if state == "sent" else None, now,
         now)).lastrowid
    steps = {"approved": ("approved",), "sending": ("approved", "sending"),
             "send_unknown": ("approved", "sending", "send_unknown"),
             "failed": ("approved", "sending", "send_unknown", "failed")}.get(state, ())
    for step in steps:
        held = step in ("sending", "send_unknown")
        conn.execute("UPDATE message SET state = ?, approved_at = ?, pending_rfc_id = ?, claimed_at = ? "
                     "WHERE message_id = ?", (step, now, f"<plan-seed-p{n}@x>" if held else None,
                                              now if held else None, message_id))
    if state == "skipped":
        conn.execute("UPDATE message SET state = 'skipped' WHERE message_id = ?", (message_id,))
    return message_id


def seed_daily_entry(conn: sqlite3.Connection, list_date: date, company_id: int, *, rank: int = 1,
                     person_id: int | None = None, channel: str | None = None, state: str = "proposed",
                     list_group: str = "level_role", why: dict | None = None, capacity: int = 7) -> None:
    """A `daily_list` for the date (made once) and one entry on it. A person row needs a channel; a decided state
    (anything but proposed) gets a `decided_at`."""
    now = utcnow()
    conn.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES (?, ?, ?) "
                 "ON CONFLICT(list_date) DO NOTHING", (list_date.isoformat(), now, capacity))
    if person_id is not None and channel is None:
        channel = "linkedin_request"
    conn.execute(
        "INSERT INTO daily_list_entry(list_date, company_id, list_group, rank, state, why, proposed_at, decided_at, "
        "person_id, channel) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (list_date.isoformat(), company_id, list_group, rank, state, json.dumps(why or {}), now,
         None if state == "proposed" else now, person_id, channel))
