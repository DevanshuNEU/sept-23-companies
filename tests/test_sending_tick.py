"""Task 13: the tick, the one run that does everything in order.

Every test uses FakeGmail and a fake clock. Its probe runs at the start of every client call: it checks that the
tick holds no transaction and that another connection could write at that moment, then runs the test's hook for
that call, if any. Each store starts with one imported email that Gmail has under the Message-ID the store holds,
so `verify_imported` can prove Gmail keeps the Message-ID the sender sets (no send happens before that proof).
"""
import fcntl
import json
import os
import random
import sqlite3
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from outreach.drafts.checks import load_banned
from outreach.sending import claim, queue, tick
from outreach.sending.config import load_config
from outreach.sending.fakegmail import FakeClock, FakeGmail
from outreach.sending.mail import GmailError, Sent
from outreach.sending.tick import LOCK_NAME, TickDeps, run_tick
from outreach.store import connect
from tests.sendtest import MON, iso, seed_email_person, seed_message, seed_sent
from tests.storetest import fresh_store, seed_company
from tests.test_drafts_checks import FIXTURE_BANNED
from tests.test_sending_precheck import body

CFG = load_config()
COUNTS = ("sent", "reconciled", "replies", "autoReplies", "bounces", "skipped", "lapsed", "closed")
IMPORTED_RFC = "<old.1@devanshu.local>"


class Base(unittest.TestCase):
    with_import = True  # seed the imported email that proves Gmail keeps the Message-ID

    def setUp(self):
        self.db = fresh_store()
        self.db.execute("PRAGMA fullfsync = ON")  # the sender's connection: the tick refuses any other
        self.path = self.db.execute("PRAGMA database_list").fetchone()["file"]
        self.clock = FakeClock(MON)
        self.problems: list[str] = []
        self.hooks: dict[str, object] = {}
        self.gmail = FakeGmail(clock=self.clock, probe=self.probe,
                               reconcile_after_minutes=CFG.reconcile_after_minutes)
        self.rng = random.Random(11)
        self.notices: list[tuple[str, str]] = []
        self.old_loaded = False
        self.banned, ok = load_banned(FIXTURE_BANNED)
        self.assertTrue(ok)
        self.emails: dict[int, str] = {}
        self.n = 0
        if self.with_import:
            self.seed_imported(IMPORTED_RFC)

    # -- fakes and seeds ----------------------------------------------------------------------------------------

    def probe(self, name: str) -> None:
        if self.db.in_transaction:
            self.problems.append(f"{name} called inside a transaction")
        other = sqlite3.connect(self.path, timeout=0, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.execute("ROLLBACK")
        except sqlite3.OperationalError as err:
            self.problems.append(f"{name}: another connection could not write: {err}")
        finally:
            other.close()
        hook = self.hooks.pop(name, None)
        if hook is not None:
            hook()

    def notify(self, title: str, text: str) -> None:
        self.notices.append((title, text))

    def seed_imported(self, stored_rfc: str) -> int:
        """An email the old mailer sent yesterday (#17's import), in Gmail's Sent under `stored_rfc`."""
        company = seed_company(self.db, "Imported Co")
        person = seed_email_person(self.db, company, "Old Contact", "old@imported.example")
        at = MON - timedelta(days=1)
        meta = self.gmail.add_to_sent("old@imported.example", "An email from last week", message_id=stored_rfc, at=at)
        touch = self.db.execute(
            "INSERT INTO touch(person_id, origin, first_sent_at, first_rfc_id, gmail_thread_id, created_at, "
            "updated_at) VALUES (?, 'import', ?, ?, ?, ?, ?)",
            (person, iso(at), IMPORTED_RFC, meta.thread_id, iso(at), iso(at))).lastrowid
        return self.db.execute(
            "INSERT INTO message(touch_id, seq, state, subject, body, sent_at, rfc_message_id, gmail_message_id, "
            "saved_by, created_at, updated_at) VALUES (?, 0, 'sent', 'An email from last week', 'An old body.', ?, ?, "
            "?, 'import', ?, ?)", (touch, iso(at), IMPORTED_RFC, meta.gmail_message_id, iso(at), iso(at))).lastrowid

    def approve(self, *, tz: str = "America/New_York") -> int:
        self.n += 1
        company = seed_company(self.db, f"Co{self.n}")
        email = f"p{self.n}@co{self.n}.example"
        person = seed_email_person(self.db, company, f"P{self.n}", email, tz=tz)
        mid = seed_message(self.db, person, subject=f"Your team and the backend role {self.n}", body=body(self.n))
        self.emails[mid] = email
        return mid

    def deps(self, **changes) -> TickDeps:
        base = TickDeps(client=self.gmail, clock=self.clock, rng=self.rng, notify=self.notify,
                        old_sender_loaded=lambda: self.old_loaded, banned=self.banned)
        return replace(base, **changes)

    def tick(self, **kwargs):
        return run_tick(self.db, self.deps(), CFG, **kwargs)

    def hold_lock(self) -> None:
        fd = os.open(Path(self.path).with_name(LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    # -- reads --------------------------------------------------------------------------------------------------

    def message(self, mid: int) -> sqlite3.Row:
        return self.db.execute("SELECT * FROM message WHERE message_id = ?", (mid,)).fetchone()

    def sender(self) -> sqlite3.Row:
        return self.db.execute("SELECT * FROM sender WHERE sender_id = 1").fetchone()

    def touch_of(self, mid: int) -> sqlite3.Row:
        return self.db.execute("SELECT t.* FROM touch t JOIN message m ON m.touch_id = t.touch_id "
                               "WHERE m.message_id = ?", (mid,)).fetchone()

    def sent_of(self, mid: int) -> Sent:
        gmail_id = self.message(mid)["gmail_message_id"]
        return next(s for _, s, _ in self.gmail.sent if s.gmail_message_id == gmail_id)

    def runs(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM job_run WHERE job = 'sender' ORDER BY run_id").fetchall()

    def events(self, kind: str) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM event WHERE kind = ? ORDER BY event_id", (kind,)).fetchall()

    def dump(self) -> list[str]:
        return list(self.db.iterdump())

    def pause(self, kind: str = "by_you") -> None:
        stamp = iso(self.clock())
        self.db.execute("UPDATE sender SET paused = 1, pause_kind = ?, paused_reason = 'a test pause', paused_at = ?, "
                        "updated_at = ? WHERE sender_id = 1", (kind, stamp, stamp))

    def resume(self) -> None:
        """A test seed standing in for his resume click."""
        self.db.execute("UPDATE sender SET paused = 0, pause_kind = NULL, paused_reason = '', paused_at = NULL "
                        "WHERE sender_id = 1")


class SendingTest(Base):
    def test_one_tick_sends_at_most_one(self):
        mids = [self.approve() for _ in range(3)]
        report = self.tick()
        self.assertEqual(report.auth, "ok")
        self.assertEqual(report.sent, (mids[0],))
        self.assertEqual(len(self.gmail.sent), 1)
        again = self.tick()  # the same moment: the random gap holds the rest
        self.assertEqual(again.sent, ())
        self.assertEqual(again.waiting, {"spacing": 2})
        self.clock.advance(minutes=10)
        self.assertEqual(self.tick().sent, (mids[1],))
        self.assertEqual(len(self.gmail.sent), 2)
        self.assertEqual(self.problems, [])

    def test_a_block_found_at_send_time_returns_the_message_to_review(self):
        mid = self.approve()
        company = self.db.execute("SELECT p.company_id FROM message m JOIN touch t ON t.touch_id = m.touch_id "
                                  "JOIN person p ON p.person_id = t.person_id WHERE m.message_id = ?",
                                  (mid,)).fetchone()[0]
        colleague = seed_email_person(self.db, company, "Colleague", "colleague@co1.example")
        seed_message(self.db, colleague, state="drafted", subject="Something else", body=self.message(mid)["body"])
        report = self.tick()
        self.assertEqual(report.sent, ())
        self.assertEqual(self.message(mid)["state"], "drafted")
        self.assertIn("near_identical", [c["code"] for c in json.loads(self.message(mid)["checks"])])
        self.assertTrue(any("went back to review: near_identical" in line for line in report.lines))
        self.assertEqual(json.loads(self.runs()[-1]["summary"])["blocked"], 1)
        self.assertEqual(self.gmail.sent, [])

    def test_an_unreadable_banned_list_sends_nothing_and_keeps_the_approval(self):
        mid = self.approve()
        report = run_tick(self.db, self.deps(banned=[]), CFG)
        self.assertEqual(report.sent, ())
        self.assertEqual(self.message(mid)["state"], "approved")
        self.assertEqual(self.runs()[-1]["status"], "partial")
        self.assertTrue(any("banned phrases list" in line for line in report.lines))
        self.assertEqual(self.gmail.sent, [])

    def test_weekend_far_east_recipient_is_not_sent(self):
        # Sunday 20:00 in New York is Monday 10:00 in Tokyo: the New York date is not a sending day.
        self.clock.set(datetime(2026, 11, 9, 1, 0, tzinfo=timezone.utc))
        mid = self.approve(tz="Asia/Tokyo")
        report = self.tick()
        self.assertIsNone(report.day)
        self.assertEqual(report.waiting, {"not_business_day": 1})
        self.assertEqual(self.gmail.sent, [])
        self.assertEqual(self.message(mid)["state"], "approved")


class SignInTest(Base):
    def test_dead_token_sends_nothing_and_is_signed_out(self):
        mid = self.approve()
        self.gmail.fail_profile(GmailError("auth", 401, "invalid_grant: Token has been expired or revoked."))
        report = self.tick()
        self.assertEqual(report.auth, "signed_out")
        self.assertEqual(report.sent, ())
        self.assertEqual(self.gmail.calls, ["profile"])  # no reads, no sends
        row = self.sender()
        self.assertEqual(row["auth_state"], "signed_out")
        self.assertIn("auth", row["auth_error"])
        self.assertEqual(row["auth_checked_at"], iso(self.clock()))
        self.assertEqual(len(self.notices), 1)
        self.clock.advance(minutes=2)
        self.tick()
        self.assertEqual(len(self.notices), 1)  # one notice a day
        self.assertEqual(self.gmail.calls, ["profile", "profile"])
        self.assertEqual(self.message(mid)["state"], "approved")
        self.assertEqual(self.runs()[-1]["status"], "failed")

    def test_revoked_token_is_signed_out_not_a_hard_block(self):
        mid = self.approve()
        self.gmail.fail_next_send(GmailError("auth", 401, "Token has been revoked."))
        report = self.tick()
        self.assertEqual(report.auth, "signed_out")
        row = self.sender()
        self.assertEqual((row["auth_state"], row["paused"], row["pause_kind"]), ("signed_out", 0, None))
        self.assertEqual(self.events("hard_block"), [])
        self.assertEqual(self.events("gmail_limit"), [])
        self.assertIsNone(self.db.execute("SELECT 1 FROM send_day WHERE outcome = 'hard_block'").fetchone())
        self.assertEqual(self.message(mid)["state"], "send_unknown")
        # Signed in again: the miss is proven, the message is retried once and goes out.
        self.clock.advance(minutes=11)
        self.tick()
        self.assertEqual(self.sender()["auth_state"], "ok")
        self.assertEqual(self.message(mid)["state"], "sent")
        self.assertEqual(len(self.gmail.sent), 1)
        self.assertEqual(self.sender()["paused"], 0)

    def test_wrong_account_sends_nothing(self):
        self.approve()
        self.gmail.account = "someone@else.com"
        report = self.tick()
        self.assertEqual(report.auth, "wrong_account")
        row = self.sender()
        self.assertEqual((row["auth_state"], row["account"]), ("wrong_account", "someone@else.com"))
        self.assertEqual(self.gmail.calls, ["profile"])
        self.assertEqual(self.gmail.sent, [])
        self.assertEqual(len(self.notices), 1)


class GateTest(Base):
    def test_old_sender_loaded_sends_nothing_but_still_reads_replies(self):
        first = self.approve()
        self.tick()
        second = self.approve()
        self.gmail.deliver_reply(self.sent_of(first), self.emails[first])
        self.old_loaded = True
        self.clock.advance(minutes=10)
        report = self.tick()
        self.assertEqual(report.sent, ())
        self.assertEqual(report.inbound["replies"], 1)
        self.assertEqual(self.touch_of(first)["state"], "replied")
        self.assertEqual(report.waiting, {"old_sender_running": 1})
        self.assertEqual(self.message(second)["state"], "approved")
        self.assertEqual(len(self.gmail.sent), 1)

    def test_paused_by_you_sends_nothing_but_records_replies_and_settles_unclear_sends(self):
        first = self.approve()
        self.tick()
        second = self.approve()
        self.clock.advance(minutes=10)
        self.gmail.fail_next_send(GmailError("timeout", None, "the call timed out"), accepted=True)
        self.tick()
        self.assertEqual(self.message(second)["state"], "send_unknown")
        self.pause("by_you")
        third = self.approve()
        self.gmail.deliver_reply(self.sent_of(first), self.emails[first])
        self.clock.advance(minutes=11)
        report = self.tick()
        self.assertEqual(report.sent, ())
        self.assertEqual(self.message(second)["state"], "sent")  # found in Sent by its Message-ID
        self.assertEqual(report.reconciled["sent"], 1)
        self.assertEqual(self.touch_of(first)["state"], "replied")
        self.assertEqual(report.waiting, {"sender_paused": 1})
        self.assertEqual(self.message(third)["state"], "approved")
        self.assertEqual(len(self.gmail.sent), 2)
        self.assertEqual((self.sender()["paused"], self.sender()["pause_kind"]), (1, "by_you"))

    def test_hard_block_on_429_pauses_and_the_message_is_retried_after_resume(self):
        mid = self.approve()
        self.gmail.fail_next_send(GmailError("rate_limit", 429, "Too many requests"))
        self.tick()
        row = self.sender()
        self.assertEqual((row["paused"], row["pause_kind"]), (1, "hard_block"))
        self.assertEqual(len(self.events("gmail_limit")), 1)
        self.assertEqual(len(self.notices), 1)
        self.assertEqual(self.message(mid)["state"], "send_unknown")
        self.clock.advance(minutes=11)
        self.tick()  # still paused: the miss is proven and the message waits approved for its one retry
        self.assertEqual(self.message(mid)["state"], "approved")
        self.assertEqual(self.gmail.sent, [])
        self.resume()
        self.clock.advance(minutes=1)
        report = self.tick()
        self.assertEqual(report.sent, (mid,))
        self.assertEqual(self.message(mid)["state"], "sent")
        self.assertEqual(len(self.gmail.sent), 1)
        self.assertEqual(len(self.events("gmail_limit")), 1)

    def test_a_read_limit_is_not_a_hard_block_and_holds_the_send(self):
        self.tick()  # takes a history cursor
        mid = self.approve()
        self.hooks["history"] = lambda: (_ for _ in ()).throw(GmailError("rate_limit", 429, "Too many requests"))
        report = self.tick()
        self.assertEqual(report.sent, ())
        self.assertEqual(self.sender()["paused"], 0)
        self.assertEqual(self.events("gmail_limit"), [])
        self.assertEqual(self.runs()[-1]["status"], "partial")
        self.assertTrue(any("not fully read" in line for line in report.lines))
        self.clock.advance(minutes=2)
        self.assertEqual(self.tick().sent, (mid,))

    def test_inbound_that_fails_to_apply_sends_nothing_and_the_rest_still_settles(self):
        first = self.approve()
        self.gmail.fail_next_send(GmailError("timeout", None, "the call timed out"), accepted=True)
        self.tick()
        second = self.approve()
        self.clock.advance(minutes=11)
        with mock.patch("outreach.sending.inbound.apply_inbound", side_effect=RuntimeError("a bad inbound row")):
            report = self.tick()
        self.assertEqual(report.sent, ())
        self.assertEqual(self.message(first)["state"], "sent")  # reconcile still committed
        self.assertEqual(self.message(second)["state"], "approved")
        self.assertEqual(self.runs()[-1]["status"], "partial")
        self.assertEqual(self.tick().sent, (second,))

    def test_a_failed_sent_search_leaves_the_send_in_doubt(self):
        mid = self.approve()
        self.gmail.fail_next_send(GmailError("timeout", None, "the call timed out"), accepted=True)
        self.tick()
        self.clock.advance(minutes=11)
        self.hooks["find_sent"] = lambda: (_ for _ in ()).throw(GmailError("server", 500, "backend error"))
        report = self.tick()
        self.assertEqual(self.message(mid)["state"], "send_unknown")
        self.assertEqual(self.runs()[-1]["status"], "partial")
        self.assertEqual(self.sender()["paused"], 0)
        self.assertTrue(any("Sent could not be searched" in line for line in report.lines))
        self.clock.advance(minutes=2)
        self.tick()
        self.assertEqual(self.message(mid)["state"], "sent")
        self.assertEqual(len(self.gmail.sent), 1)


class ImportProofTest(Base):
    with_import = False

    def test_no_send_before_gmail_is_proven_to_keep_the_message_id(self):
        mid = self.approve()  # nothing imported, so nothing proves it
        report = self.tick()
        self.assertEqual(report.sent, ())
        self.assertTrue(any("Message-ID" in line for line in report.lines))
        self.assertEqual(report.waiting, {"message_id_unproven": 1})
        self.assertEqual(self.message(mid)["state"], "approved")
        run = self.runs()[-1]
        self.assertEqual(run["status"], "partial")
        self.assertIn("Message-ID", run["error"])
        self.assertEqual(self.notices, [(tick.WAIT_TITLE, "Sending waits: Gmail's Message-ID handling is not proven.")])
        self.clock.advance(minutes=2)
        self.tick()
        self.assertEqual(len(self.notices), 1)  # once a day
        self.assertIsNone(self.tick(dry_run=True).would_send)

    def test_a_send_read_back_under_its_claimed_id_is_proof(self):
        first = self.approve()
        q = next(q for q in queue.load_queue(self.db, CFG) if q.message_id == first)
        self.assertEqual(claim.send_one(self.db, self.gmail, q, CFG, now=iso(self.clock()), clock=self.clock,
                                        rng=self.rng, account=CFG.from_address), "sent")
        second = self.approve()
        self.clock.advance(minutes=10)
        self.assertEqual(self.tick().sent, (second,))

    def test_a_send_gmail_stored_under_another_id_is_not_proof(self):
        self.gmail.rewrite_message_ids = True
        first = self.approve()
        q = next(q for q in queue.load_queue(self.db, CFG) if q.message_id == first)
        with self.assertRaises(GmailError):
            claim.send_one(self.db, self.gmail, q, CFG, now=iso(self.clock()), clock=self.clock, rng=self.rng,
                           account=CFG.from_address)
        self.assertEqual(self.message(first)["state"], "sent")
        self.resume()
        self.gmail.rewrite_message_ids = False
        self.approve()
        self.clock.advance(minutes=10)
        report = self.tick()
        self.assertEqual(report.sent, ())
        self.assertEqual(report.waiting, {"message_id_unproven": 1})

    def test_gmail_that_rewrote_the_imported_message_id_blocks_every_send(self):
        self.seed_imported("<rewritten@mail.gmail.com>")  # Gmail has it under another Message-ID
        mid = self.approve()
        self.tick()
        self.assertEqual(len(self.events("import_not_in_sent")), 1)
        self.assertEqual(self.events("import_verified"), [])
        self.clock.advance(minutes=10)
        self.tick()
        self.assertEqual(self.gmail.sent, [])
        self.assertEqual(self.message(mid)["state"], "approved")

    def test_one_verified_import_lets_sending_start(self):
        self.seed_imported(IMPORTED_RFC)
        mid = self.approve()
        self.assertEqual(self.tick().sent, (mid,))
        self.assertEqual(len(self.events("import_verified")), 1)


class RewrittenTest(Base):
    def test_message_id_rewritten_pauses_and_notifies(self):
        self.gmail.rewrite_message_ids = True
        mid = self.approve()
        report = self.tick()
        self.assertEqual(report.sent, (mid,))
        self.assertEqual(self.message(mid)["state"], "sent")
        self.assertEqual((self.sender()["paused"], self.sender()["pause_kind"]), (1, "hard_block"))
        self.assertEqual(len(self.notices), 1)
        self.approve()
        self.clock.advance(minutes=10)
        self.tick()
        self.assertEqual(len(self.gmail.sent), 1)
        self.assertEqual(len(self.notices), 1)

    def test_the_tick_pauses_on_a_rewritten_id_even_when_the_send_did_not(self):
        self.gmail.rewrite_message_ids = True
        mid = self.approve()
        with mock.patch.object(claim, "_pause_rewritten"):
            self.tick()
        self.assertEqual((self.sender()["paused"], self.sender()["pause_kind"]), (1, "hard_block"))
        self.assertEqual(self.message(mid)["state"], "sent")
        self.assertEqual(len(self.events("hard_block")), 1)


class DryRunTest(Base):
    def test_dry_run_writes_nothing(self):
        first = self.approve()
        self.tick()
        self.gmail.deliver_reply(self.sent_of(first), self.emails[first])
        second = self.approve()
        self.clock.advance(minutes=10)
        self.notices.clear()
        before = self.dump()
        report = self.tick(dry_run=True)
        self.assertEqual(self.dump(), before)
        self.assertTrue(report.dry_run)
        self.assertEqual(report.would_send, second)
        self.assertEqual(report.inbound["replies"], 1)  # applied, then rolled back
        self.assertEqual(len(self.gmail.sent), 1)
        self.assertEqual(self.notices, [])
        self.assertFalse(self.db.in_transaction)

    def test_dry_run_reads_gmail(self):
        self.tick()  # takes a history cursor
        self.gmail.calls.clear()
        self.tick(dry_run=True)
        self.assertIn("profile", self.gmail.calls)
        self.assertIn("history", self.gmail.calls)
        self.assertNotIn("send", self.gmail.calls)

    def test_dry_run_tells_nobody(self):
        company = seed_company(self.db, "Many")
        for i in range(10):
            person = seed_email_person(self.db, company, f"B{i}", f"b{i}@many.example")
            seed_sent(self.db, person, at=MON - timedelta(days=1, minutes=i))
        self.db.execute("UPDATE touch SET state = 'bounced', bounced_at = ? WHERE touch_id = "
                        "(SELECT max(touch_id) FROM touch)", (iso(MON - timedelta(hours=1)),))
        before = self.dump()
        self.tick(dry_run=True)  # a real tick would pause on this bounce rate and tell him
        self.assertEqual(self.notices, [])
        self.assertEqual(self.dump(), before)
        self.tick()
        self.assertEqual(len(self.notices), 1)

    def test_dry_run_with_a_dead_token_reports_it_and_writes_nothing(self):
        self.gmail.fail_profile(GmailError("auth", 401, "invalid_grant"))
        before = self.dump()
        report = self.tick(dry_run=True)
        self.assertEqual(report.auth, "signed_out")
        self.assertEqual(self.dump(), before)
        self.assertEqual(self.notices, [])


class LockTest(Base):
    def test_second_tick_while_one_runs_does_nothing(self):
        self.approve()
        self.hold_lock()
        before = self.dump()
        for dry in (False, True):
            report = self.tick(dry_run=dry)
            self.assertEqual(report.lines, ("another tick is running",))
            self.assertEqual(report.sent, ())
        self.assertEqual(self.gmail.calls, [])
        self.assertEqual(self.dump(), before)

    def test_a_tick_started_during_a_tick_exits_on_the_lock(self):
        mid = self.approve()
        second_conn = connect(self.path, fullfsync=True)
        self.addCleanup(second_conn.close)
        other_gmail = FakeGmail(clock=self.clock)
        inner = []
        self.hooks["send"] = lambda: inner.append(run_tick(second_conn, self.deps(client=other_gmail), CFG))
        report = self.tick()
        self.assertEqual(report.sent, (mid,))
        self.assertEqual(inner[0].lines, ("another tick is running",))
        self.assertEqual(other_gmail.calls, [])
        self.assertEqual(len(self.gmail.sent), 1)
        self.assertEqual(len(self.runs()), 1)

    def test_locked_store_fails_the_tick_without_sending(self):
        mid = self.approve()
        self.db.execute("PRAGMA busy_timeout = 0")
        blocker = sqlite3.connect(self.path, timeout=0, isolation_level=None)
        self.addCleanup(blocker.close)
        self.hooks["profile"] = lambda: blocker.execute("BEGIN IMMEDIATE")
        report = self.tick()
        self.assertTrue(any("locked" in line for line in report.lines))
        self.assertNotIn("send", self.gmail.calls)
        self.assertFalse(self.db.in_transaction)
        blocker.execute("ROLLBACK")
        self.assertEqual(self.runs()[-1]["status"], "running")  # its failure could not be written either
        self.clock.advance(minutes=2)
        self.assertEqual(self.tick().sent, (mid,))
        first, second = self.runs()
        self.assertEqual((first["status"], first["error"]), ("failed", tick.STALE_RUN))
        self.assertEqual(second["status"], "ok")

    def test_a_locked_step_writes_a_failed_run(self):
        self.approve()
        with mock.patch("outreach.sending.ramp.open_day", side_effect=sqlite3.OperationalError("database is locked")):
            report = self.tick()
        self.assertEqual(report.sent, ())
        self.assertEqual(self.gmail.sent, [])
        run = self.runs()[-1]
        self.assertEqual(run["status"], "failed")
        self.assertIn("locked", run["error"])
        self.assertTrue(any("locked" in line for line in report.lines))


class CrashTest(Base):
    def test_a_tick_killed_before_gmail_took_the_email_is_sent_once_later(self):
        mid = self.approve()
        self.hooks["send"] = lambda: (_ for _ in ()).throw(SystemExit("killed"))
        with self.assertRaises(SystemExit):
            self.tick()
        self.assertEqual(self.message(mid)["state"], "sending")
        self.clock.advance(minutes=2)
        self.tick()  # the leftover claim is now in doubt; a miss proves nothing yet
        self.assertEqual(self.message(mid)["state"], "send_unknown")
        self.clock.advance(minutes=11)
        report = self.tick()
        self.assertEqual(report.sent, (mid,))
        self.assertEqual(len(self.gmail.sent), 1)
        self.assertEqual(self.runs()[0]["status"], "failed")

    def test_a_tick_killed_after_gmail_took_the_email_never_sends_it_again(self):
        mid = self.approve()
        self.gmail.fail_next_send(SystemExit("killed"), accepted=True)
        with self.assertRaises(SystemExit):
            self.tick()
        self.clock.advance(minutes=2)
        report = self.tick()
        self.assertEqual(report.reconciled["sent"], 1)
        self.assertEqual(self.message(mid)["state"], "sent")
        for _ in range(3):
            self.clock.advance(minutes=11)
            self.tick()
        self.assertEqual(len(self.gmail.sent), 1)


class SignalTest(Base):
    def test_hard_bounce_rate_from_any_path_pauses(self):
        company = seed_company(self.db, "Many")
        touches = []
        for i in range(10):
            person = seed_email_person(self.db, company, f"B{i}", f"b{i}@many.example")
            seed_sent(self.db, person, at=MON - timedelta(days=1, minutes=i))
            touches.append(self.db.execute("SELECT touch_id FROM touch WHERE person_id = ?", (person,)).fetchone()[0])
        # A bounce recorded by another path (not Gmail inbound): the tick still sees the rate.
        self.db.execute("UPDATE touch SET state = 'bounced', bounced_at = ? WHERE touch_id = ?",
                        (iso(MON - timedelta(hours=1)), touches[0]))
        self.tick()
        self.assertEqual((self.sender()["paused"], self.sender()["pause_kind"]), (1, "hard_block"))
        self.assertEqual([json.loads(e["detail"])["kind"] for e in self.events("hard_block")], ["hard_bounce_rate"])
        self.resume()
        mid = self.approve()
        self.clock.advance(minutes=2)
        self.assertEqual(self.tick().sent, (mid,))  # the same bounces never pause twice
        self.assertEqual(self.sender()["paused"], 0)

    def test_reconcile_errors_tell_him(self):
        mid = self.approve()
        self.gmail.fail_next_send(GmailError("timeout", None, "the call timed out"), accepted=True)
        self.tick()
        self.clock.advance(minutes=11)
        with mock.patch.object(claim, "record_sent", side_effect=RuntimeError("cannot record")):
            report = self.tick()
        self.assertEqual(report.reconciled["errors"], 1)
        self.assertEqual(len(self.notices), 1)
        self.assertEqual(self.message(mid)["state"], "send_unknown")
        self.clock.advance(minutes=2)
        self.tick()
        self.assertEqual(self.message(mid)["state"], "sent")
        self.assertEqual(len(self.gmail.sent), 1)

    def test_a_failed_send_waiting_for_its_retry_keeps_its_touch_open(self):
        company = seed_company(self.db, "Quiet")
        person = seed_email_person(self.db, company, "Quinn", "quinn@quiet.example")
        first_at = MON - timedelta(days=14)  # ten business days ago: the touch is due to close as quiet
        first = seed_sent(self.db, person, at=first_at, rfc="<first.q@devanshu.local>", thread="tq")
        subject = self.message(first)["subject"]
        self.gmail.add_to_sent("quinn@quiet.example", subject, message_id="<first.q@devanshu.local>", at=first_at,
                               thread_id="tq")
        followup = seed_message(self.db, person, seq=1, due_on="2026-10-28", subject=f"Re: {subject}",
                                body="Hi Quinn, one more thing: I shipped the runner's retry queue last week.")
        self.gmail.fail_next_send(GmailError("server", 500, "backend error"))
        self.tick()
        self.assertEqual(self.message(followup)["state"], "send_unknown")
        self.clock.advance(minutes=11)
        self.tick()  # the miss is proven, the follow-up is requeued and goes out; the touch never closed
        self.assertEqual(self.message(followup)["state"], "sent")
        self.assertEqual(self.touch_of(followup)["state"], "open")
        self.assertEqual(len(self.gmail.sent), 1)


class OutputTest(Base):
    def test_lines_carry_no_traceback_and_no_secret(self):
        self.approve()
        self.gmail.fail_next_send(OSError("connection reset; Bearer ya29.a0secret"))
        report = self.tick()
        self.gmail.fail_profile(GmailError("auth", 401, "access_token=ya29.othersecret"))
        self.clock.advance(minutes=2)
        report2 = self.tick()
        for line in report.lines + report2.lines:
            self.assertNotIn("Traceback", line)
            self.assertNotIn("During handling", line)
            self.assertNotIn("ya29", line)
            self.assertNotIn("\n", line)
        self.assertNotIn("ya29", self.sender()["auth_error"])
        for run in self.runs():
            self.assertNotIn("ya29", run["error"] + run["summary"])


class JobRunTest(Base):
    def run_row(self, started: datetime, status: str, noop: int, job: str = "sender") -> int:
        return self.db.execute(
            "INSERT INTO job_run(job, started_at, finished_at, status, summary) VALUES (?, ?, ?, ?, ?)",
            (job, iso(started), iso(started + timedelta(seconds=5)), status,
             json.dumps({"sent": 0 if noop else 1, "noop": noop}))).lastrowid

    def ids(self) -> set[int]:
        return {r[0] for r in self.db.execute("SELECT run_id FROM job_run")}

    def test_quiet_ticks_are_pruned_after_14_days_and_busy_ones_kept(self):
        old_quiet = self.run_row(MON - timedelta(days=15), "ok", 1)
        kept = {self.run_row(MON - timedelta(days=15), "ok", 0),
                self.run_row(MON - timedelta(days=13), "ok", 1),
                self.run_row(MON - timedelta(days=15), "failed", 0),
                self.run_row(MON - timedelta(days=15), "ok", 1, job="crawl")}
        self.tick()
        self.assertNotIn(old_quiet, self.ids())
        self.assertTrue(kept <= self.ids())
        late = self.run_row(MON - timedelta(days=20), "ok", 1)
        self.clock.advance(minutes=2)
        self.tick()
        self.assertIn(late, self.ids())  # once a New York day
        self.clock.advance(days=1)
        self.tick()
        self.assertNotIn(late, self.ids())

    def test_every_tick_writes_one_job_run_with_integer_counts(self):
        self.approve()
        self.tick()
        self.clock.advance(minutes=2)
        self.tick()
        self.gmail.fail_profile(GmailError("auth", 401, "invalid_grant"))
        self.clock.advance(minutes=2)
        self.tick()
        runs = self.runs()
        self.assertEqual(len(runs), 3)
        for run in runs:
            self.assertIsNotNone(run["finished_at"])
            summary = json.loads(run["summary"])
            for key in COUNTS + ("noop",):
                self.assertIs(type(summary[key]), int, key)
        summaries = [json.loads(r["summary"]) for r in runs]
        self.assertEqual([s["sent"] for s in summaries], [1, 0, 0])
        self.assertEqual([s["noop"] for s in summaries], [0, 1, 0])
        self.assertEqual([r["status"] for r in runs], ["ok", "ok", "failed"])


class TransactionTest(Base):
    def test_no_gmail_call_inside_a_transaction_across_a_whole_tick(self):
        first = self.approve()
        self.tick()
        second = self.approve()
        self.clock.advance(minutes=10)
        self.gmail.fail_next_send(GmailError("timeout", None, "the call timed out"), accepted=True)
        self.tick()
        third = self.approve()
        self.gmail.deliver_reply(self.sent_of(first), self.emails[first])
        self.clock.advance(minutes=11)
        report = self.tick()
        self.assertEqual(report.sent, (third,))
        self.assertEqual(self.message(second)["state"], "sent")
        self.assertEqual(self.problems, [])
        self.assertTrue({"profile", "thread", "message", "find_sent", "history", "send"} <= set(self.gmail.calls))

    def test_refuses_a_connection_without_fullfsync(self):
        plain = connect(self.path)
        self.addCleanup(plain.close)
        with self.assertRaises(RuntimeError):
            run_tick(plain, self.deps(), CFG)
        self.assertEqual(self.gmail.calls, [])


if __name__ == "__main__":
    unittest.main()
