"""The `send` stage (#14 owns it): only what was approved goes out, once.

The stage seeds its own fixture company, "Sendwell Labs", with a founder whose first email is approved (test seed
SQL standing in for his click in the app) and a second person whose draft is never approved, and puts the founder on
the 2026-11-10 daily list with channel `email`, so the touch the sender logs says its source is the plan. It also
seeds one email the old mailer sent, found in Gmail's Sent under its own Message-ID (imported history; it proves
nothing about the sender's marker, so the founder's email is the one first email that goes out alone). Then it
runs the real `run_tick` on a `FakeGmail` and the fake clock, six ticks two minutes apart from 09:00 New York on
2026-11-10. Nothing here reaches Gmail or the real clock.

`register` leaves the ids on the harness as `harness.sending_ids` (a `SendingIds`): the company, the people, their
messages, the founder's touch, and the `FakeGmail` that took the email.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from outreach.drafts.checks import load_banned
from outreach.sending.config import load_config
from outreach.sending.fakegmail import FakeClock, FakeGmail
from outreach.sending.tick import TickDeps, run_tick

__all__ = ["register", "SendingIds", "FOUNDER_EMAIL", "COMPANY"]

COMPANY = "Sendwell Labs"
FOUNDER_EMAIL = "founder@sendwell.example"
ENGINEER_EMAIL = "engineer@sendwell.example"
LIST_DATE = "2026-11-10"
START = datetime(2026, 11, 10, 9, 0, tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
TICKS, TICK_MINUTES = 6, 2
STAMP = "2026-11-09T12:00:00Z"
BANNED = Path(__file__).resolve().parent.parent / "fixtures" / "banned_phrases.txt"
BODY = ("Hi there,\n\nI read about the billing service work at Sendwell Labs and the post on how your team moved it "
        "off a nightly batch. I build backend systems in Go and Python, and last year I shipped a queue that "
        "handled thousands of requests a minute without a page. I would like to help your team keep that kind of "
        "work calm. My work is at https://www.devanshuchicholikar.com and one project is at "
        "https://github.com/devanshu/queue.\n\nWould you be open to referring me?\n\nThanks,\nDevanshu")
OTHER_BODY = ("Hello Eli,\n\nYour ingest rewrite caught my eye while I was reading the engineering blog. Over the "
              "summer I wrote a batch loader for a research lab, then spent weeks making its retries safe, which "
              "taught me more about failure than any course did. If your team has room for a new graduate who "
              "enjoys that kind of unglamorous work, I would be glad to hear how you got in. Some of it is "
              "at https://www.devanshuchicholikar.com and a small load tester is at "
              "https://github.com/devanshu/loadtest.\n\nCould you point me to the right person to ask?\n\n"
              "Regards,\nDevanshu")


@dataclass
class SendingIds:
    company: int
    founder: int
    founder_touch: int
    founder_message: int
    other: int
    other_message: int
    gmail: FakeGmail


def _person(conn, company: int, name: str, email: str, role: str) -> tuple[int, int]:
    person = conn.execute(
        "INSERT INTO person(company_id, name, role_kind, email, tz, channel, added_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'America/New_York', 'email', 'devanshu', ?, ?)",
        (company, name, role, email, STAMP, STAMP)).lastrowid
    touch = conn.execute("INSERT INTO touch(person_id, created_at, updated_at) VALUES (?, ?, ?)",
                         (person, STAMP, STAMP)).lastrowid
    return person, touch


def _draft(conn, touch: int, subject: str, body: str) -> int:
    return conn.execute("INSERT INTO message(touch_id, seq, state, subject, body, created_at, updated_at) "
                        "VALUES (?, 0, 'drafted', ?, ?, ?, ?)", (touch, subject, body, STAMP, STAMP)).lastrowid


def _seed_import(conn, gmail: FakeGmail) -> None:
    company = conn.execute("INSERT INTO company(name, status_at, created_at) VALUES ('Sendwell Archive', ?, ?)",
                           (STAMP, STAMP)).lastrowid
    _, touch = _person(conn, company, "Old Contact", "old@archive.example", "engineer")
    rfc, at = "<old.1@devanshu.local>", "2026-11-05T15:00:00Z"
    meta = gmail.add_to_sent("old@archive.example", "An email from last week", message_id=rfc,
                             at=datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc))
    conn.execute("UPDATE touch SET origin = 'import', first_sent_at = ?, first_rfc_id = ?, gmail_thread_id = ? "
                 "WHERE touch_id = ?", (at, rfc, meta.thread_id, touch))
    conn.execute(
        "INSERT INTO message(touch_id, seq, state, subject, body, sent_at, rfc_message_id, gmail_message_id, "
        "saved_by, created_at, updated_at) VALUES (?, 0, 'sent', 'An email from last week', 'An old body.', ?, ?, ?, "
        "'import', ?, ?)", (touch, at, rfc, meta.gmail_message_id, at, at))


def register(harness) -> None:
    conn = harness.conn

    def send() -> None:
        conn.execute("PRAGMA fullfsync = ON")  # the tick refuses a connection without it
        clock = FakeClock(START)
        cfg = load_config()
        gmail = FakeGmail(clock=clock, reconcile_after_minutes=cfg.reconcile_after_minutes)
        company = conn.execute("INSERT INTO company(name, status_at, created_at) VALUES (?, ?, ?)",
                               (COMPANY, STAMP, STAMP)).lastrowid
        founder, founder_touch = _person(conn, company, "Fay Founder", FOUNDER_EMAIL, "founder")
        other, other_touch = _person(conn, company, "Eli Engineer", ENGINEER_EMAIL, "engineer")
        founder_message = _draft(conn, founder_touch, "Your billing service work at Sendwell Labs", BODY)
        other_message = _draft(conn, other_touch, "A question on the data pipeline at Sendwell Labs", OTHER_BODY)
        # His click on the founder's first email.
        conn.execute("UPDATE message SET state = 'approved', approved_at = ? WHERE message_id = ?",
                     (START.strftime("%Y-%m-%dT%H:%M:%SZ"), founder_message))
        # The plan put the founder on that day's list.
        conn.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES (?, ?, 20) "
                     "ON CONFLICT(list_date) DO NOTHING", (LIST_DATE, STAMP))
        rank = conn.execute("SELECT COALESCE(MAX(rank), 0) + 1 FROM daily_list_entry WHERE list_date = ?",
                            (LIST_DATE,)).fetchone()[0]
        conn.execute("INSERT INTO daily_list_entry(list_date, company_id, list_group, rank, person_id, channel, "
                     "proposed_at) VALUES (?, ?, 'any_us_role', ?, ?, 'email', ?)",
                     (LIST_DATE, company, rank, founder, STAMP))
        _seed_import(conn, gmail)
        banned, usable = load_banned(BANNED)
        assert usable, "the fixture banned list must load"
        deps = TickDeps(client=gmail, clock=clock, rng=random.Random(3), notify=lambda title, text: None,
                        old_sender_loaded=lambda: False, banned=banned)
        for _ in range(TICKS):
            run_tick(conn, deps, cfg)
            clock.advance(minutes=TICK_MINUTES)
        harness.sending_ids = SendingIds(company=company, founder=founder, founder_touch=founder_touch,
                                         founder_message=founder_message, other=other, other_message=other_message,
                                         gmail=gmail)

    harness.stages.append(("send", send))
