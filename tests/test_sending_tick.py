"""Task 13: the tick, the one run that does everything in order.

Every test uses FakeGmail and a fake clock. Its probe runs at the start of every client call: it checks that the
tick holds no transaction and that another connection could write at that moment, then runs the test's hook for
that call, if any. Each store starts with one imported email that Gmail has under the Message-ID the store holds
(the import check runs on it) and, unless `proven` is False, a `marker_verified` event: Gmail is shown to keep the
sender's marker, so the whole queue may go. `FirstSendTest`, `OldProofTest` and `LiveShapeTest` start unproven.
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
from outreach.sending import accounts, claim, proof, queue, tick
from outreach.sending.config import load_config
from outreach.sending.fakegmail import FakeClock, FakeGmail
from outreach.sending.mail import GmailError, Sent
from outreach.sending.tick import LOCK_NAME, TickDeps, run_tick
from outreach.store import connect
from outreach.store.writes import append_event
from tests.sendtest import MON, iso, prove, seed_email_person, seed_message, seed_sent, solo
from tests.storetest import fresh_store, seed_company
from tests.test_drafts_checks import FIXTURE_BANNED
from tests.test_sending_precheck import body

# The shipped shape: account 1 (devanshurajesh) rests and is only read, account 2 (devanshuchi9) sends the general
# lane, account 3 (the Northeastern Mail.app account) does not send and has no client in Phase A.
SHIPPED = load_config()
REST, GENERAL, NEU = (a.address for a in SHIPPED.accounts)
# Most tests here run the one-account shape: account 1 sends every company.
CFG = solo(SHIPPED)
COUNTS = ("sent", "reconciled", "replies", "autoReplies", "bounces", "skipped", "planned", "lapsed", "closed")
IMPORTED_RFC = "<old.1@devanshu.local>"


class Base(unittest.TestCase):
    with_import = True  # seed an imported email (history the import check reads; it proves nothing)
    proven = True       # Gmail is shown to keep the sender's marker (`marker_verified`)
    cfg = CFG           # the accounts in play; `self.gmail` is the first one's mailbox

    def setUp(self):
        self.db = fresh_store()
        self.db.execute("PRAGMA fullfsync = ON")  # the sender's connection: the tick refuses any other
        self.path = self.db.execute("PRAGMA database_list").fetchone()["file"]
        self.clock = FakeClock(MON)
        self.problems: list[str] = []
        self.hooks: dict[str, object] = {}
        self.gmail = FakeGmail(clock=self.clock, account=self.cfg.accounts[0].address, probe=self.probe,
                               reconcile_after_minutes=CFG.reconcile_after_minutes)
        self.clients = {self.cfg.accounts[0].address: self.gmail}
        self.rng = random.Random(11)
        self.notices: list[tuple[str, str]] = []
        self.old_loaded = False
        self.banned, ok = load_banned(FIXTURE_BANNED)
        self.assertTrue(ok)
        self.emails: dict[int, str] = {}
        self.n = 0
        if self.with_import:
            self.seed_imported(IMPORTED_RFC)
        if self.proven:
            prove(self.db)

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
        base = TickDeps(clients=self.clients, clock=self.clock, rng=self.rng, notify=self.notify,
                        old_sender_loaded=lambda: self.old_loaded, banned=self.banned)
        return replace(base, **changes)

    def tick(self, **kwargs):
        return run_tick(self.db, self.deps(), self.cfg, **kwargs)

    def hold_lock(self) -> None:
        fd = os.open(Path(self.path).with_name(LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    # -- reads --------------------------------------------------------------------------------------------------

    def message(self, mid: int) -> sqlite3.Row:
        return self.db.execute("SELECT * FROM message WHERE message_id = ?", (mid,)).fetchone()

    def sender(self) -> sqlite3.Row:
        return self.db.execute("SELECT * FROM sender WHERE sender_id = 1").fetchone()

    def account_row(self) -> sqlite3.Row:
        """Account 1's own row: where a hard block lands (sender row 1 stays his button)."""
        return self.db.execute("SELECT * FROM sender_account WHERE account_id = 1").fetchone()

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
        self.db.execute("UPDATE sender_account SET paused = 0, pause_kind = NULL, paused_reason = '', "
                        "paused_at = NULL")


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
        self.assertEqual(self.notices, [(tick.WAIT_TITLE, tick.BANNED_TEXT)])
        self.clock.advance(minutes=2)
        run_tick(self.db, self.deps(banned=[]), CFG)
        self.assertEqual(len(self.notices), 1)  # once a day

    def test_a_long_gmail_read_cannot_push_a_send_past_the_window(self):
        self.clock.set(datetime(2026, 11, 2, 21, 25, tzinfo=timezone.utc))  # 16:25 in New York
        mid = self.approve()
        self.hooks["thread"] = lambda: self.clock.advance(minutes=10)  # the read ends at 16:35
        report = self.tick()
        self.assertEqual(report.sent, ())
        self.assertEqual(report.waiting, {"window": 1})
        self.assertEqual(self.message(mid)["state"], "approved")
        self.assertEqual(self.gmail.sent, [])

    def test_the_claim_is_dated_when_the_pick_was_made(self):
        mid = self.approve()
        self.hooks["thread"] = lambda: self.clock.advance(minutes=3)
        self.tick()
        at = self.db.execute("SELECT at FROM event WHERE kind = 'claimed' AND entity_id = ?", (mid,)).fetchone()[0]
        self.assertEqual(at, iso(MON + timedelta(minutes=3)))

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

    def test_a_hard_block_with_no_account_row_still_pauses_after_the_ticks_sync(self):
        self.db.execute("DELETE FROM sender_account")  # a store that signed in after migration 0017 has none
        self.approve()
        self.gmail.fail_next_send(GmailError("rate_limit", 429, "Too many requests"))
        self.tick()
        row = self.account_row()
        self.assertIsNotNone(row, "the tick's sync created the row")
        self.assertEqual((row["paused"], row["pause_kind"]), (1, "hard_block"))
        self.assertEqual(len(self.notices), 1)

    def test_hard_block_on_429_pauses_and_the_message_is_retried_after_resume(self):
        mid = self.approve()
        self.gmail.fail_next_send(GmailError("rate_limit", 429, "Too many requests"))
        self.tick()
        row = self.account_row()
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

    def test_a_failed_read_waits_as_inbound_unread(self):
        self.tick()  # takes a history cursor
        mid = self.approve()
        self.hooks["history"] = lambda: (_ for _ in ()).throw(GmailError("network", None, "offline"))
        report = self.tick()
        self.assertEqual(report.sent, ())
        self.assertEqual(report.waiting, {"inbound_unread": 1})
        self.assertTrue(any("devanshurajesh@gmail.com was not fully read" in line for line in report.lines))
        self.clock.advance(minutes=2)
        self.assertEqual(self.tick().sent, (mid,))

    def test_inbound_that_fails_to_apply_waits_as_inbound_unread(self):
        self.tick()
        mid = self.approve()
        with mock.patch("outreach.sending.inbound.apply_inbound", side_effect=RuntimeError("a bad inbound row")):
            report = self.tick()
        self.assertEqual((report.sent, report.waiting), ((), {"inbound_unread": 1}))
        self.assertEqual(self.message(mid)["state"], "approved")

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


class OldProofTest(Base):
    """What proved the Message-ID before the marker proves nothing now: only the first marked email read back does."""
    with_import = False
    proven = False

    def test_a_verified_import_is_no_proof_of_the_marker_so_one_email_goes_alone(self):
        self.seed_imported(IMPORTED_RFC)
        first, second = self.approve(), self.approve()
        report = self.tick()
        self.assertEqual(len(self.events("import_verified")), 1)
        self.assertEqual(report.sent, (first,))
        self.assertEqual(report.waiting, {"message_id_unproven": 1})
        self.assertEqual(self.message(second)["state"], "approved")

    def test_gmail_that_rewrote_the_imported_message_id_still_lets_the_first_email_go_alone(self):
        self.seed_imported("<rewritten@mail.gmail.com>")  # Gmail has it under another Message-ID
        first, second = self.approve(), self.approve()
        report = self.tick()
        self.assertEqual(len(self.events("import_not_in_sent")), 1)
        self.assertEqual(report.sent, (first,))
        self.assertEqual(report.waiting, {"message_id_unproven": 1})
        self.assertEqual(self.message(second)["state"], "approved")
        self.clock.advance(minutes=10)
        self.assertEqual(self.tick().sent, (second,))  # its marker read back: proven


class FirstSendTest(Base):
    """Nothing proves Gmail keeps the sender's marker: one email may go out, alone, and the next tick reads it back
    from Gmail by its Gmail id before any other goes."""
    with_import = False
    proven = False

    def claims(self) -> list[sqlite3.Row]:
        return self.events("claimed")

    def test_the_first_email_goes_out_alone_and_the_rest_wait(self):
        first, second, third = self.approve(), self.approve(), self.approve()
        report = self.tick()
        self.assertEqual(report.sent, (first,))
        self.assertEqual(len(self.gmail.sent), 1)
        self.assertEqual(report.waiting, {"message_id_unproven": 2})
        self.assertTrue(any("first email goes out alone" in line for line in report.lines))
        self.assertEqual(self.runs()[-1]["status"], "ok")
        self.assertEqual(self.notices, [])
        self.assertEqual((self.message(second)["state"], self.message(third)["state"]), ("approved", "approved"))
        self.assertEqual(self.problems, [])

    def test_a_verified_first_email_releases_the_rest(self):
        first, second, third = self.approve(), self.approve(), self.approve()
        self.tick()
        sent = self.sent_of(first)
        self.clock.advance(minutes=10)
        report = self.tick()
        [verified] = self.events("marker_verified")
        detail = json.loads(verified["detail"])
        self.assertEqual((verified["entity_id"], verified["idempotency_key"]), (first, f"marker_verified:{first}"))
        self.assertEqual((detail["gmail_message_id"], detail["thread_id"]), (sent.gmail_message_id, sent.thread_id))
        claimed = json.loads(self.events("claimed")[0]["detail"])["marker"]
        self.assertEqual((detail["marker"], detail["found_marker"]), (claimed, claimed))
        self.assertTrue(any("rest may follow" in line for line in report.lines))
        self.assertEqual(report.sent, (second,))  # proven in step 4, so the second goes in the same tick
        self.assertEqual(json.loads(self.runs()[-1]["summary"])["verified"], 1)
        self.clock.advance(minutes=10)
        self.assertEqual(self.tick().sent, (third,))
        self.assertEqual([e["entity_id"] for e in self.events("marker_verified")], [first, second])  # each once
        self.assertEqual(len(self.gmail.sent), 3)

    def test_gmail_rewriting_the_first_emails_message_id_is_normal(self):
        """What Gmail did on the first live send: it stored the email under a Message-ID of its own. That is no
        longer a failure: recorded under Gmail's id, no pause, and the marker proves Gmail kept the email findable."""
        self.gmail.rewrite_message_ids = True
        first, second = self.approve(), self.approve()
        self.assertEqual(self.tick().sent, (first,))
        stored = self.sent_of(first).rfc_message_id
        self.assertEqual(self.message(first)["rfc_message_id"], stored)
        self.assertNotEqual(stored, json.loads(self.events("claimed")[0]["detail"])["marker"])
        self.assertEqual((self.sender()["paused"], self.events("hard_block"), self.notices), (0, [], []))
        self.clock.advance(minutes=10)
        self.assertEqual(self.tick().sent, (second,))
        self.assertEqual(len(self.events("marker_verified")), 1)
        self.assertEqual(self.runs()[-1]["status"], "ok")

    def test_a_first_email_gmail_kept_without_its_marker_stops_sending_for_good(self):
        self.gmail.drop_markers = True
        self.gmail.readback_fails = True  # the send cannot see it, so the next tick's read back does (see
        # DroppedAtSendTest for the send's own read back)
        first, second = self.approve(), self.approve()
        self.assertEqual(self.tick().sent, (first,))
        self.clock.advance(minutes=10)
        report = self.tick()
        self.assertEqual(report.sent, ())
        [missing] = self.events("marker_missing")
        self.assertEqual((missing["entity_id"], json.loads(missing["detail"])["reason"]), (first, "no_marker"))
        self.assertEqual(self.notices, [(tick.STOP_TITLE, f"{REST}: {proof.PHASE_TEXT[proof.MISSING]}")])
        self.gmail.drop_markers = False
        for _ in range(3):
            self.clock.advance(minutes=30)
            self.assertEqual(self.tick().sent, ())
        self.assertEqual((len(self.gmail.sent), self.message(second)["state"]), (1, "approved"))
        self.assertEqual(len(self.notices), 1)  # once a day

    def test_a_lost_answer_after_gmail_took_the_first_email_is_found_by_its_marker_and_not_resent(self):
        self.gmail.rewrite_message_ids = True  # as the real Gmail: the Message-ID set is never the one kept
        first, second = self.approve(), self.approve()
        self.gmail.fail_next_send(TimeoutError("read timed out"), accepted=True)
        self.tick()
        self.assertEqual(self.message(first)["state"], "send_unknown")
        self.clock.advance(minutes=11)
        self.assertEqual(self.tick().sent, ())
        row = self.message(first)
        self.assertEqual((row["state"], row["rfc_message_id"]), ("sent", self.gmail.sent[0][1].rfc_message_id))
        self.clock.advance(minutes=2)
        self.assertEqual(self.tick().sent, (second,))
        self.assertEqual([out.to for out, _, _ in self.gmail.sent], [self.emails[first], self.emails[second]])
        self.assertEqual(self.events("claim_failed"), [])

    def test_the_second_email_waits_while_the_first_is_not_yet_checked(self):
        first, second = self.approve(), self.approve()
        self.assertEqual(self.tick().sent, (first,))
        self.clock.advance(minutes=10)
        with mock.patch.object(self.gmail, "message", side_effect=GmailError("server", 500, "backend error")):
            report = self.tick()
            self.assertEqual(report.sent, ())
            self.assertEqual(report.waiting, {"message_id_unproven": 1})
            self.clock.advance(minutes=10)
            self.assertEqual(self.tick().sent, ())
        self.assertEqual(self.events("marker_verified") + self.events("marker_missing"), [])
        self.assertEqual(self.runs()[-1]["status"], "partial")
        self.assertTrue(any("could not be read back" in line for line in report.lines))
        self.assertEqual(self.notices, [])
        self.assertEqual((len(self.gmail.sent), len(self.claims())), (1, 1))
        self.assertEqual(self.message(second)["state"], "approved")
        self.clock.advance(minutes=2)
        self.assertEqual(self.tick().sent, (second,))  # Gmail answers again: checked, and the second goes

    def test_a_first_email_gmail_shows_with_another_marker_stops_sending_for_good(self):
        first, second = self.approve(), self.approve()
        self.tick()
        self.gmail.change_stored(self.sent_of(first).gmail_message_id, marker="<other@gmail.com>")
        self.clock.advance(minutes=10)
        report = self.tick()
        self.assertEqual(report.sent, ())
        [missing] = self.events("marker_missing")
        detail = json.loads(missing["detail"])
        self.assertEqual((missing["entity_id"], detail["reason"], detail["found_marker"]),
                         (first, "other_marker", "<other@gmail.com>"))
        self.assertEqual(self.notices, [(tick.STOP_TITLE, f"{REST}: {proof.PHASE_TEXT[proof.MISSING]}")])
        self.assertIn("Gmail did not keep the sender's marker", self.notices[0][1])
        self.assertEqual(self.runs()[-1]["status"], "partial")
        prove(self.db)  # a proof found later does not start it again
        for _ in range(3):
            self.clock.advance(minutes=30)
            report = self.tick()
            self.assertEqual(report.sent, ())
        self.assertEqual(report.waiting, {"message_id_unproven": 1})
        self.assertEqual(len(self.notices), 1)  # once a day
        self.assertEqual((len(self.gmail.sent), self.message(second)["state"]), (1, "approved"))
        self.clock.advance(days=1)
        self.tick()
        self.assertEqual(len(self.notices), 2)  # and again the next day
        self.assertEqual(len(self.gmail.sent), 1)

    def test_a_first_email_moved_out_of_sent_is_no_proof(self):
        first, second = self.approve(), self.approve()
        self.tick()
        self.gmail.change_stored(self.sent_of(first).gmail_message_id, labels=("TRASH",))
        self.clock.advance(minutes=10)
        self.assertEqual(self.tick().sent, ())
        [missing] = self.events("marker_missing")
        self.assertEqual(json.loads(missing["detail"])["reason"], "not_in_sent")
        self.assertEqual(self.events("marker_verified"), [])
        self.assertEqual(self.message(second)["state"], "approved")

    def test_a_first_email_in_doubt_is_never_retried_and_nothing_else_goes(self):
        first, second = self.approve(), self.approve()
        self.gmail.fail_next_send(GmailError("timeout", None, "the call timed out"))  # Gmail never took it
        self.tick()
        self.assertEqual(self.message(first)["state"], "send_unknown")
        self.clock.advance(minutes=4)
        report = self.tick()
        self.assertEqual(report.sent, ())
        self.assertTrue(any("in doubt" in line for line in report.lines))
        self.assertEqual(self.notices, [])
        # Sent proves the miss, so the message is failed and put back for its one retry; while Gmail is unproven
        # a miss proves nothing (Gmail may hold it under another id), so it is never retried and sending stops.
        self.clock.advance(minutes=10)
        report = self.tick()
        self.assertEqual(self.message(first)["state"], "approved")
        for _ in range(3):
            self.assertEqual(report.sent, ())
            self.clock.advance(minutes=10)
            report = self.tick()
        self.assertEqual(report.waiting, {"message_id_unproven": 2})
        self.assertEqual(self.gmail.sent, [])
        self.assertEqual(len(self.claims()), 1)
        self.assertEqual(self.notices, [(tick.STOP_TITLE, f"{REST}: {proof.PHASE_TEXT[proof.STOPPED]}")])
        self.assertEqual(self.runs()[-1]["status"], "partial")
        self.assertEqual(self.message(second)["state"], "approved")

    def test_a_first_email_found_in_sent_after_a_timeout_is_read_back_before_the_rest(self):
        first, second = self.approve(), self.approve()
        self.gmail.fail_next_send(GmailError("timeout", None, "the call timed out"), accepted=True)
        self.tick()
        self.clock.advance(minutes=11)
        report = self.tick()
        self.assertEqual(self.message(first)["state"], "sent")  # settled from Sent by its marker
        self.assertEqual(report.sent, ())  # but not yet read back by its Gmail id
        self.assertEqual(self.events("marker_verified"), [])
        self.clock.advance(minutes=2)
        self.assertEqual(self.tick().sent, (second,))
        self.assertEqual(self.events("marker_verified")[0]["entity_id"], first)
        self.assertEqual(len(self.gmail.sent), 2)

    def test_an_email_held_back_at_send_time_is_not_the_first_claim(self):
        first, second = self.approve(), self.approve()
        company = self.touch_of(first)["person_id"]
        company = self.db.execute("SELECT company_id FROM person WHERE person_id = ?", (company,)).fetchone()[0]
        colleague = seed_email_person(self.db, company, "Colleague", "colleague@co1.example")
        seed_message(self.db, colleague, state="drafted", subject="Something else", body=self.message(first)["body"])
        report = self.tick()  # the approve checks send the first back to review: nothing was claimed
        self.assertEqual((report.sent, self.message(first)["state"]), ((), "drafted"))
        self.assertEqual(self.claims(), [])
        self.clock.advance(minutes=2)
        self.assertEqual(self.tick().sent, (second,))

    def test_every_other_gate_still_blocks_the_first_email(self):
        cases = {
            "paused": lambda: self.pause(),
            "hard block": lambda: self.pause("hard_block"),
            "signed out": lambda: self.gmail.fail_profile(GmailError("auth", 401, "invalid_grant")),
            "old sender loaded": lambda: setattr(self, "old_loaded", True),
            "weekend": lambda: self.clock.set(datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)),
            "outside the window": lambda: self.clock.set(datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc)),
            "day limit used": self.fill_the_day,
            "held by a reply": self.hold_company,
            "the person replied": self.replied,
        }
        for name, close in cases.items():
            with self.subTest(name):
                self.setUp()
                mid = self.approve()
                close()
                report = self.tick()
                self.assertEqual(report.sent, ())
                self.assertEqual(self.gmail.sent, [])
                self.assertEqual(self.claims(), [])
                self.assertEqual(proof.phase(self.db, 1), proof.FIRST_SEND)
                self.assertIn(self.message(mid)["state"], ("approved", "skipped"))

    def fill_the_day(self) -> None:
        """Today's limit (20 on a fresh store) is used up by emails that went out some other way."""
        company = seed_company(self.db, "Busy Co")
        for i in range(CFG.accounts[0].ramp_start):
            seed_sent(self.db, seed_email_person(self.db, company, f"B{i}", f"b{i}@busy.example"),
                      at=self.clock() - timedelta(minutes=20))

    def hold_company(self) -> None:
        mid = max(self.emails)
        touch = self.touch_of(mid)["touch_id"]
        company = self.db.execute("SELECT company_id FROM person WHERE person_id = ?",
                                  (self.touch_of(mid)["person_id"],)).fetchone()[0]
        self.db.execute("UPDATE company SET held_by_touch = ?, held_at = ? WHERE company_id = ?",
                        (touch, iso(self.clock()), company))

    def replied(self) -> None:
        mid = max(self.emails)
        self.db.execute("UPDATE touch SET state = 'replied', replied_at = ?, reply_kind = 'unsorted' "
                        "WHERE touch_id = ?", (iso(self.clock()), self.touch_of(mid)["touch_id"]))

    def test_a_dry_run_names_the_first_email_and_writes_nothing(self):
        first, second = self.approve(), self.approve()
        before = self.dump()
        report = self.tick(dry_run=True)
        self.assertEqual(report.would_send, first)
        self.assertEqual(self.dump(), before)
        self.assertEqual(self.gmail.sent, [])
        self.tick()
        self.clock.advance(minutes=10)
        before, calls = self.dump(), len(self.gmail.calls)
        report = self.tick(dry_run=True)  # a dry run never reads the first email back, so nothing is proven
        self.assertIsNone(report.would_send)
        self.assertEqual(self.dump(), before)
        self.assertNotIn("message", self.gmail.calls[calls:])
        self.assertEqual((len(self.gmail.sent), self.message(second)["state"]), (1, "approved"))
        self.assertEqual(self.notices, [])

    def test_a_tick_never_claims_a_second_email_while_unproven(self):
        """Whatever the queue says, the claim itself refuses: the pick is forced past `first_only`."""
        first, second = self.approve(), self.approve()
        self.assertEqual(self.tick().sent, (first,))
        self.clock.advance(minutes=10)
        q = next(q for q in queue.load_queue(self.db, CFG) if q.message_id == second)
        row = accounts.in_play(self.db, CFG)[REST]
        with mock.patch.object(proof, "verify_marker", return_value={"verified": 0, "missing": 0, "errors": 0}), \
                mock.patch.object(tick._Tick, "_senders", return_value=[row]), \
                mock.patch.object(queue, "pick", return_value=q):
            report = self.tick()
        self.assertEqual(report.sent, ())
        self.assertTrue(any("already claimed" in line for line in report.lines))
        self.assertEqual((len(self.gmail.sent), self.message(second)["state"], len(self.claims())),
                         (1, "approved", 1))


class DroppedAtSendTest(Base):
    """The reviewer's case: proven, then Gmail drops the marker. Email A goes out and its own read back shows no
    marker; the next tick's read back of A fails; email B's answer would be lost. B must never be claimed, so it can
    never be resent."""

    def test_a_marker_dropped_at_send_stops_the_next_email_even_when_the_read_back_fails(self):
        a, b = self.approve(), self.approve()
        self.gmail.drop_markers = True
        report = self.tick()
        self.assertEqual(report.sent, (a,))
        [missing] = self.events("marker_missing")
        self.assertEqual((missing["entity_id"], json.loads(missing["detail"])["reason"]), (a, "dropped_on_send"))
        self.assertEqual(self.notices, [(tick.STOP_TITLE, f"{REST}: {proof.PHASE_TEXT[proof.MISSING]}")])  # at once
        self.assertEqual(self.runs()[-1]["status"], "partial")
        self.gmail.fail_reads = GmailError("server", 500, "backend error")  # every later read of A fails
        self.gmail.fail_next_send(TimeoutError("read timed out"), accepted=True)  # B's answer would be lost
        for _ in range(6):
            self.clock.advance(minutes=11)
            self.assertEqual(self.tick().sent, ())
        self.gmail.fail_reads = None
        for _ in range(3):
            self.clock.advance(minutes=11)
            self.assertEqual(self.tick().sent, ())
        self.assertEqual([out.to for out, _, _ in self.gmail.sent], [self.emails[a]])
        self.assertEqual(self.message(b)["state"], "approved")
        self.assertEqual([e["entity_id"] for e in self.events("claimed")], [a])
        self.assertEqual(len(self.notices), 1)  # once a day


class LiveShapeTest(Base):
    """The live store as #118 left it: message 5 went out alone before the marker existed (claimed with no marker),
    Gmail stored it under its own Message-ID, the sender recorded it sent under that id, raised the hard block pause
    (`message_id_rewritten`) and later read it back under the other id (`send_id_mismatch`). Four approved emails
    wait. Nothing goes out until he clicks Resume; then exactly one marked email goes, is read back, and the rest
    follow one at a time. Message 5 is never read back, retried or sent again."""
    with_import = False
    proven = False
    CLAIMED = "<179312908749.1656.7162976981692914863@gmail.com>"
    STORED = "<CABkiztq.live.5@mail.gmail.com>"
    REASON = ("Sending is paused because Gmail stored a sent email under another Message-ID, so a send in doubt "
              "could not be found in Sent. The email was recorded as sent. Check Gmail before resuming.")

    def setUp(self):
        super().setUp()
        self.five = self.approve()
        at = iso(self.clock() - timedelta(hours=2))
        # In Sent as Gmail keeps it: its own Message-ID, and no marker (the sender did not set one then).
        meta = self.gmail.add_to_sent(self.emails[self.five], self.message(self.five)["subject"],
                                      message_id=self.STORED, at=self.clock() - timedelta(hours=2))
        self.db.execute("UPDATE message SET state = 'sending', pending_rfc_id = ?, claimed_at = ? "
                        "WHERE message_id = ?", (self.CLAIMED, at, self.five))
        company = self.db.execute("SELECT p.company_id FROM touch t JOIN person p ON p.person_id = t.person_id "
                                  "WHERE t.touch_id = ?", (self.message(self.five)["touch_id"],)).fetchone()[0]
        old = {"entity": "message", "entity_id": self.five, "company_id": company, "actor": "sender", "at": at}
        append_event(self.db, kind="claimed", idempotency_key=f"claimed:{self.CLAIMED}",
                     detail={"pending_rfc_id": self.CLAIMED, "attempt": 1, "seq": 0}, **old)
        self.db.execute("UPDATE message SET state = 'send_unknown' WHERE message_id = ?", (self.five,))
        append_event(self.db, kind="send_unknown", idempotency_key=f"send_unknown:{self.CLAIMED}",
                     detail={"stored_rfc_id": self.STORED, "gmail_message_id": meta.gmail_message_id,
                             "thread_id": meta.thread_id, "pending_rfc_id": self.CLAIMED,
                             "error": "Gmail message_id_rewritten", "error_kind": "message_id_rewritten",
                             "retryable": False}, **old)
        real_append = claim.append_event

        def old_format(conn, **kw):
            """The sender before the marker wrote no `marker` key on its `sent` event."""
            if kw.get("kind") == "sent" and kw.get("entity") == "message":
                kw["detail"] = {k: v for k, v in kw["detail"].items() if k != "marker"}
            return real_append(conn, **kw)
        with mock.patch.object(claim, "append_event", side_effect=old_format):
            claim.record_sent(self.db, self.five, Sent(meta.gmail_message_id, meta.thread_id, self.STORED), at=at,
                              cfg=CFG, rng=self.rng)
        sent = json.loads(self.db.execute("SELECT detail FROM event WHERE kind = 'sent' AND entity = 'message' "
                                          "AND entity_id = ?", (self.five,)).fetchone()[0])
        assert "marker" not in sent
        append_event(self.db, kind="send_id_mismatch", idempotency_key=f"send_id_mismatch:{self.five}",
                     detail={"reason": "other_message_id", "found_rfc_id": self.STORED}, **old)
        append_event(self.db, entity="sender", entity_id=1, kind="hard_block", actor="sender", at=at,
                     idempotency_key=f"hard_block:message_id_rewritten:{self.five}",
                     detail={"kind": "message_id_rewritten", "message_id": self.five, "reason": self.REASON})
        self.db.execute("UPDATE sender SET paused = 1, pause_kind = 'hard_block', paused_reason = ?, paused_at = ? "
                        "WHERE sender_id = 1", (self.REASON, at))
        self.waiting = [self.approve() for _ in range(4)]

    def test_message_fives_follow_up_threads_on_the_message_id_gmail_stored(self):
        """Its id came from Gmail (the old read back), so it counts as confirmed: In-Reply-To names it."""
        row = self.db.execute("SELECT message_id FROM message WHERE touch_id = ? AND seq = 1",
                              (self.message(self.five)["touch_id"],)).fetchone()
        subject = "Re: " + self.message(self.five)["subject"]
        self.db.execute("UPDATE message SET state = 'drafted', subject = ?, body = 'Following up.' "
                        "WHERE message_id = ?", (subject, row[0]))
        self.db.execute("UPDATE message SET state = 'approved', approved_at = ?, due_on = ? WHERE message_id = ?",
                        (iso(self.clock()), iso(self.clock())[:10], row[0]))
        q = next(q for q in queue.load_queue(self.db, CFG) if q.message_id == row[0])
        out = claim.outgoing_for(self.db, q, CFG, "<follow.5@gmail.com>", accounts.primary(self.db, CFG))
        self.assertEqual((out.in_reply_to, out.references), (self.STORED, (self.STORED,)))

    def test_the_shape_reads_as_the_first_send_and_nothing_goes_before_resume(self):
        self.assertEqual(proof.phase(self.db, 1), proof.FIRST_SEND)
        before = len(self.gmail.calls)
        for _ in range(3):
            report = self.tick()
            self.assertEqual(report.sent, ())
            self.assertEqual(report.waiting, {"sender_paused": 4})
            self.clock.advance(minutes=10)
        self.assertNotIn("message", self.gmail.calls[before:])  # message 5 is never read back
        self.assertNotIn("send", self.gmail.calls)
        self.assertEqual((self.sender()["paused"], self.sender()["pause_kind"]), (1, "hard_block"))  # stays
        self.assertEqual(proof.phase(self.db, 1), proof.FIRST_SEND)

    def test_after_resume_exactly_one_marked_email_goes_then_the_rest_follow(self):
        self.tick()
        self.resume()  # his click in the Outbox
        self.clock.advance(minutes=10)
        report = self.tick()
        first = self.waiting[0]
        self.assertEqual(report.sent, (first,))
        self.assertEqual(report.waiting, {"message_id_unproven": 3})
        self.assertEqual(len(self.gmail.sent), 1)
        out = self.gmail.sent[0][0]
        self.assertEqual(out.marker, json.loads(self.events("claimed")[-1]["detail"])["marker"])
        self.assertEqual(proof.phase(self.db, 1), proof.CHECKING)
        q = next(q for q in queue.load_queue(self.db, CFG) if q.message_id == self.waiting[1])
        with self.assertRaises(claim.Unproven):  # no second claim, by any path
            claim.claim(self.db, q, accounts.primary(self.db, CFG), rfc_id="<forced@gmail.com>", now=iso(self.clock()))
        self.clock.advance(minutes=2)
        self.assertEqual(self.tick().sent, ())  # read back: proven, but the gap after a send holds the next
        self.assertEqual([e["entity_id"] for e in self.events("marker_verified")], [first])
        for _ in range(12):
            self.clock.advance(minutes=10)
            self.tick()
        self.assertEqual([out.to for out, _, _ in self.gmail.sent], [self.emails[m] for m in self.waiting])
        self.assertEqual([self.message(m)["state"] for m in self.waiting], ["sent"] * 4)
        self.assertNotIn(self.emails[self.five], [out.to for out, _, _ in self.gmail.sent])  # never again
        self.assertEqual(self.events("marker_missing"), [])
        self.assertNotIn(self.five, [e["entity_id"] for e in self.events("marker_verified")])
        five = self.message(self.five)
        self.assertEqual((five["state"], five["rfc_message_id"]), ("sent", self.STORED))
        self.assertEqual(self.events("claim_failed"), [])
        self.assertEqual(self.notices, [])


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

    def test_dry_run_runs_the_pre_claim_check_and_writes_nothing(self):
        self.tick()
        mid = self.approve()
        company = self.db.execute("SELECT p.company_id FROM message m JOIN touch t ON t.touch_id = m.touch_id "
                                  "JOIN person p ON p.person_id = t.person_id WHERE m.message_id = ?",
                                  (mid,)).fetchone()[0]
        colleague = seed_email_person(self.db, company, "Colleague", "colleague@co1.example")
        seed_message(self.db, colleague, state="drafted", subject="Something else", body=self.message(mid)["body"])
        before = self.dump()
        report = self.tick(dry_run=True)
        self.assertIsNone(report.would_send)
        self.assertTrue(any("would go back to review: near_identical" in line for line in report.lines))
        self.assertEqual(self.dump(), before)
        self.assertEqual(self.message(mid)["state"], "approved")
        self.assertIsNone(run_tick(self.db, self.deps(banned=[]), CFG, dry_run=True).would_send)
        self.assertEqual(self.dump(), before)

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
        # Two "asked to stop" replies in a week: a hard block the tick raises from the store alone.
        self.db.execute("UPDATE touch SET state = 'replied', replied_at = ?, reply_kind = 'asked_to_stop' "
                        "WHERE touch_id IN (SELECT touch_id FROM touch ORDER BY touch_id DESC LIMIT 2)",
                        (iso(MON - timedelta(hours=1)),))
        before = self.dump()
        self.tick(dry_run=True)  # a real tick would pause on these stop replies and tell him
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
        self.hooks["send"] = lambda: inner.append(run_tick(second_conn, self.deps(clients={REST: other_gmail}), CFG))
        report = self.tick()
        self.assertEqual(report.sent, (mid,))
        self.assertEqual(inner[0].lines, ("another tick is running",))
        self.assertEqual(other_gmail.calls, [])
        self.assertEqual(len(self.gmail.sent), 1)
        self.assertEqual(len(self.runs()), 1)

    def test_a_lock_file_that_cannot_be_opened_fails_the_run_in_one_line(self):
        self.approve()
        denied = PermissionError(13, "Permission denied", str(Path(self.path).with_name(LOCK_NAME)))
        with mock.patch.object(tick, "_open_lock", side_effect=denied):
            dry = self.tick(dry_run=True)
            self.assertEqual(self.runs(), [])  # a dry run writes nothing, even here
            report = self.tick()
        for r in (dry, report):
            self.assertEqual(len(r.lines), 1)
            self.assertIn("lock", r.lines[0])
            self.assertIn("Permission denied", r.lines[0])
            self.assertNotIn("Traceback", r.lines[0])
            self.assertEqual(r.sent, ())
        run = self.runs()[-1]
        self.assertEqual(run["status"], "failed")
        self.assertIn("lock", run["error"])
        self.assertEqual(self.gmail.calls, [])

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
    def test_a_high_bounce_rate_from_any_path_never_pauses(self):
        company = seed_company(self.db, "Many")
        touches = []
        for i in range(10):
            person = seed_email_person(self.db, company, f"B{i}", f"b{i}@many.example")
            seed_sent(self.db, person, at=MON - timedelta(days=1, minutes=i))
            touches.append(self.db.execute("SELECT touch_id FROM touch WHERE person_id = ?", (person,)).fetchone()[0])
        # A bounce recorded by another path (not Gmail inbound): 1 in 10 first emails, and the tick does not pause.
        self.db.execute("UPDATE touch SET state = 'bounced', bounced_at = ? WHERE touch_id = ?",
                        (iso(MON - timedelta(hours=1)), touches[0]))
        self.tick()
        self.assertEqual(self.sender()["paused"], 0)
        self.assertEqual(self.events("hard_block"), [])

    def test_a_notice_from_a_rolled_back_step_is_never_shown(self):
        company = seed_company(self.db, "Many")
        for i in range(10):
            person = seed_email_person(self.db, company, f"B{i}", f"b{i}@many.example")
            seed_sent(self.db, person, at=MON - timedelta(days=1, minutes=i))
        # Two "asked to stop" replies in a week: a hard block the tick raises from the store alone.
        self.db.execute("UPDATE touch SET state = 'replied', replied_at = ?, reply_kind = 'asked_to_stop' "
                        "WHERE touch_id IN (SELECT touch_id FROM touch ORDER BY touch_id DESC LIMIT 2)",
                        (iso(MON - timedelta(hours=1)),))
        # The pause and its notice are raised, then a later step of the same transaction fails: all rolled back.
        with mock.patch("outreach.sending.followups.lapse_followups", side_effect=RuntimeError("a later step")):
            for _ in range(2):
                self.tick()
                self.clock.advance(minutes=2)
        self.assertEqual(self.notices, [])
        self.assertEqual(self.account_row()["paused"], 0)
        self.tick()
        self.assertEqual(len(self.notices), 1)
        self.assertEqual(self.account_row()["paused"], 1)
        self.clock.advance(minutes=2)
        self.tick()
        self.assertEqual(len(self.notices), 1)

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
        # The second tick reads back the email the first sent (its marker), so it is no no-op.
        self.assertEqual([s["verified"] for s in summaries], [1, 1, 0])  # the import check, then the read back
        self.assertEqual([s["noop"] for s in summaries], [0, 0, 0])
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


class MultiBase(Base):
    """The shipped shape: account 1 rests (read every tick, never sends), account 2 sends the general lane, the
    Northeastern account has no client. `self.gmail` is account 1's mailbox (it holds the imported email),
    `self.gmail2` account 2's."""
    cfg = SHIPPED
    proven_two = True  # Gmail is shown to keep the marker on account 2 too

    def setUp(self):
        super().setUp()
        self.rows = accounts.sync(self.db, self.cfg, iso(MON))
        self.two = self.rows[GENERAL]
        self.gmail2 = FakeGmail(clock=self.clock, account=GENERAL, probe=self.probe, id_prefix="b",
                                reconcile_after_minutes=CFG.reconcile_after_minutes)
        self.clients = {self.cfg.accounts[0].address: self.gmail, GENERAL: self.gmail2}
        if self.proven_two:
            prove(self.db, self.two.account_id)

    def company_of(self, mid: int) -> int:
        return self.db.execute("SELECT p.company_id FROM message m JOIN touch t ON t.touch_id = m.touch_id "
                               "JOIN person p ON p.person_id = t.person_id WHERE m.message_id = ?",
                               (mid,)).fetchone()[0]

    def sponsor(self, mid: int) -> None:
        """The message's company sponsors (an E-Verify fact): it is in the sponsor lane."""
        self.db.execute("INSERT INTO evidence(company_id, fact, value, confidence, source_kind, observed_on, "
                        "recorded_at) VALUES (?, 'everify', 'enrolled', 'estimate', 'linkedin', '2026-11-01', "
                        "'2026-11-01T12:00:00Z')", (self.company_of(mid),))

    def account_of(self, account_id: int) -> sqlite3.Row:
        return self.db.execute("SELECT * FROM sender_account WHERE account_id = ?", (account_id,)).fetchone()

    def rest_thread(self, company: int, name: str, email: str) -> int:
        """A person account 1 emailed last week (its thread in account 1's mailbox). Returns the touch id."""
        person = seed_email_person(self.db, company, name, email)
        meta = self.gmail.add_to_sent(email, "Last week", message_id=f"<{name}@rest.local>",
                                      at=MON - timedelta(days=7))
        mid = seed_sent(self.db, person, at=MON - timedelta(days=7), thread=meta.thread_id,
                        rfc=f"<{name}@rest.local>", account_id=1)
        self.db.execute("UPDATE message SET gmail_message_id = ? WHERE message_id = ?", (meta.gmail_message_id, mid))
        return self.db.execute("SELECT touch_id FROM touch WHERE person_id = ?", (person,)).fetchone()[0]


def _sending(cfg, index: int, **changes):
    """`cfg` with the account at `index` changed (`sends`, `lane`, ...)."""
    accts = list(cfg.accounts)
    accts[index] = replace(accts[index], **changes)
    return replace(cfg, accounts=tuple(accts))


# Two sending accounts: account 1 takes the sponsor lane, account 2 the general lane, Northeastern none.
TWO = _sending(_sending(SHIPPED, 0, sends=True, lane="sponsor"), 2, lane=None)
# Three Gmail accounts: account 1 rests, account 2 sends general, account 3 (Gmail here) sends sponsor.
THREE = _sending(SHIPPED, 2, kind="gmail", mailapp_account=None, sends=True)


class MultiAccountTest(MultiBase):
    def test_resting_account_read_not_sent(self):
        company = seed_company(self.db, "Old Co")
        touch = self.rest_thread(company, "Olga", "olga@old.example")
        follow = self.db.execute("INSERT INTO message(touch_id, seq, state, subject, body, due_on, created_at, "
                                 "updated_at) VALUES (?, 1, 'drafted', 'Re: Last week', ?, '2026-11-02', ?, ?)",
                                 (touch, body(90), iso(MON), iso(MON))).lastrowid
        self.db.execute("UPDATE message SET state = 'approved', approved_at = ? WHERE message_id = ?",
                        (iso(MON), follow))
        fresh = self.approve()
        report = self.tick()
        self.assertEqual(report.sent, (fresh,))
        self.assertEqual(len(self.gmail2.sent), 1)
        self.assertEqual(self.gmail.sent, [])  # account 1 never sends, not even in its own thread
        self.assertEqual(report.waiting, {"lane_waits": 1})
        self.assertIn("thread", self.gmail.calls)  # and it is read (no cursor yet: its threads)
        self.assertEqual(self.account_of(1)["auth_state"], "ok")
        self.assertEqual(self.touch_of(fresh)["account_id"], self.two.account_id)
        # A reply to account 1's old email is read and recorded.
        sent = Sent(gmail_message_id=self.db.execute("SELECT gmail_message_id FROM message WHERE touch_id = ? "
                                                     "AND seq = 0", (touch,)).fetchone()[0],
                    thread_id=self.db.execute("SELECT gmail_thread_id FROM touch WHERE touch_id = ?",
                                              (touch,)).fetchone()[0], rfc_message_id="<Olga@rest.local>")
        self.gmail.deliver_reply(sent, "olga@old.example")
        self.clock.advance(minutes=10)
        self.assertEqual(self.tick().inbound["replies"], 1)
        self.assertEqual(self.db.execute("SELECT state FROM touch WHERE touch_id = ?", (touch,)).fetchone()[0],
                         "replied")
        self.assertEqual(self.gmail.sent, [])
        self.assertEqual(self.problems, [])

    def test_a_resting_account_never_claims_even_its_own_thread(self):
        mid = self.approve()
        q = next(q for q in queue.load_queue(self.db, self.cfg) if q.message_id == mid)
        before = self.dump()
        with self.assertRaises(claim.WrongAccount):
            claim.claim(self.db, q, accounts.by_id(self.db, self.cfg, 1), rfc_id="<rest.1@gmail.com>",
                        now=iso(MON))
        self.assertEqual(self.dump(), before)

    def test_wrong_account_names_the_account(self):
        self.approve()
        self.gmail2.account = "someone@else.com"
        report = self.tick()
        self.assertEqual(report.auth, "wrong_account")
        self.assertEqual(report.accounts[GENERAL], "wrong_account")
        self.assertEqual(report.accounts[REST], "ok")
        row = self.account_of(self.two.account_id)
        self.assertEqual(row["auth_state"], "wrong_account")
        self.assertIn("someone@else.com", row["auth_error"])
        self.assertIn(GENERAL, row["auth_error"])
        self.assertEqual(self.account_of(1)["auth_state"], "ok")
        self.assertEqual(self.gmail2.calls, ["profile"])  # nothing read or sent with that token
        self.assertIn("thread", self.gmail.calls)
        self.assertEqual(self.gmail2.sent, [])
        self.assertEqual(len(self.notices), 1)
        self.assertIn(GENERAL, self.notices[0][1])
        self.assertIn("someone@else.com", self.notices[0][1])
        self.assertTrue(any(GENERAL in line and "someone@else.com" in line for line in report.lines))
        self.assertEqual(self.runs()[-1]["status"], "partial")
        self.assertEqual(report.waiting, {"signed_out": 1})

    def test_a_sending_account_with_no_token_is_signed_out_once_a_day_and_the_tick_goes_on(self):
        self.approve()
        self.clients = {REST: self.gmail}
        report = self.tick()
        self.assertEqual(report.accounts[GENERAL], "signed_out")
        self.assertEqual(self.account_of(self.two.account_id)["auth_state"], "signed_out")
        self.assertIn("thread", self.gmail.calls)  # account 1 is still read
        self.assertEqual(len(self.notices), 1)
        self.assertIn(GENERAL, self.notices[0][1])
        self.clock.advance(minutes=2)
        self.tick()
        self.assertEqual(len(self.notices), 1)  # one notice a day
        self.assertEqual(self.runs()[-1]["status"], "partial")

    def test_a_never_signed_in_account_is_asked_to_sign_in_not_to_sign_in_again(self):
        self.clients = {REST: self.gmail}  # no token file for account 2 yet (first deploy)
        self.tick()
        self.assertEqual(self.notices, [(tick.STOP_TITLE, f"{GENERAL} has no Gmail sign-in yet. Sign in to start "
                                         f"sending from {GENERAL}: python -m outreach.sending auth --account "
                                         f"{GENERAL}")])

    def test_a_dead_token_is_asked_to_sign_in_again(self):
        self.gmail2.fail_profile(GmailError("auth", 401, "invalid_grant"))
        self.tick()
        self.assertEqual(self.notices, [(tick.STOP_TITLE, f"Gmail is signed out on {GENERAL}, so nothing is sent "
                                         "from it and its replies are not read. Sign in again to resume: "
                                         f"python -m outreach.sending auth --account {GENERAL}")])

    def held_and_fresh(self) -> tuple[int, int]:
        """A company account 1 emailed, with a colleague's first email queued (it routes to account 2), and a fresh
        company's first email."""
        old = seed_company(self.db, "Old Co")
        self.rest_thread(old, "Olga", "olga@old.example")
        person = seed_email_person(self.db, old, "Nina", "nina@old.example")
        held = seed_message(self.db, person, subject="Your team and the backend role 80", body=body(80))
        return held, self.approve()

    def test_a_gmail_500_on_account_1s_sign_in_check_holds_only_the_companies_it_emailed(self):
        held, fresh = self.held_and_fresh()
        self.gmail.fail_profile(GmailError("server", 500, "backend error"))
        report = self.tick()
        self.assertEqual(report.accounts, {REST: "unknown", GENERAL: "ok", NEU: "not_read"})
        self.assertEqual(report.sent, (fresh,))
        self.assertEqual(self.message(held)["state"], "approved")
        self.assertEqual(report.waiting, {"inbound_unread": 1})
        self.assertEqual(self.runs()[-1]["status"], "partial")
        self.assertEqual(self.problems, [])

    def test_a_gmail_500_on_account_1s_inbound_read_holds_only_the_companies_it_emailed(self):
        held, fresh = self.held_and_fresh()
        def fail():
            raise GmailError("server", 500, "backend error")
        self.hooks["thread"] = fail  # account 1 is read first (config order): its first thread read fails
        report = self.tick()
        self.assertEqual(report.accounts[REST], "ok")
        self.assertTrue(any(f"{REST} was not fully read" in line for line in report.lines))
        self.assertEqual(report.sent, (fresh,))
        self.assertEqual(self.message(held)["state"], "approved")
        self.assertEqual(report.waiting, {"inbound_unread": 1})
        self.assertEqual(self.runs()[-1]["status"], "partial")

    def test_the_northeastern_account_has_no_client_and_sponsor_mail_waits(self):
        mid = self.approve()
        self.sponsor(mid)
        report = self.tick()
        self.assertEqual(report.sent, ())
        self.assertEqual(report.waiting, {"lane_waits": 1})
        self.assertEqual(report.accounts[NEU], "not_read")
        self.assertEqual(self.notices, [])
        self.assertEqual(self.runs()[-1]["status"], "ok")

    def test_an_unread_account_holds_only_the_companies_it_emailed(self):
        old = seed_company(self.db, "Old Co")
        self.rest_thread(old, "Olga", "olga@old.example")
        person = seed_email_person(self.db, old, "Nina", "nina@old.example")
        held = seed_message(self.db, person, subject="Your team and the backend role 80", body=body(80))
        fresh = self.approve()
        self.gmail.fail_profile(GmailError("auth", 401, "invalid_grant"))
        report = self.tick()
        self.assertEqual(report.accounts[REST], "signed_out")
        self.assertEqual(report.sent, (fresh,))
        self.assertEqual(self.message(held)["state"], "approved")
        verdict = {v.message_id: v for v in queue.verdicts(self.db, self.clock(), self.cfg,
                                                           accounts.in_play(self.db, self.cfg), day=None,
                                                           gate=None, unread=frozenset({1}))}[held]
        self.assertEqual(verdict.reason, "inbound_unread")
        self.assertEqual(report.waiting, {"inbound_unread": 1})

    def test_signed_out_account_2_does_not_stop_account_3(self):
        self.cfg = THREE
        three = accounts.sync(self.db, THREE, iso(MON))[NEU]
        prove(self.db, three.account_id)
        gmail3 = FakeGmail(clock=self.clock, account=NEU, probe=self.probe, id_prefix="c",
                           reconcile_after_minutes=CFG.reconcile_after_minutes)
        self.clients = {REST: self.gmail, GENERAL: self.gmail2, NEU: gmail3}
        general = self.approve()
        sponsor = self.approve()
        self.sponsor(sponsor)
        self.gmail2.fail_profile(GmailError("auth", 401, "invalid_grant"))
        report = self.tick()
        self.assertEqual(report.sent, (sponsor,))
        self.assertEqual(len(gmail3.sent), 1)
        self.assertEqual(self.gmail2.calls, ["profile"])
        self.assertEqual(self.message(general)["state"], "approved")
        self.assertEqual(report.waiting, {"signed_out": 1})
        self.assertEqual(self.account_of(self.two.account_id)["auth_state"], "signed_out")
        self.assertEqual(self.account_of(three.account_id)["auth_state"], "ok")
        self.assertEqual(len(self.notices), 1)
        self.assertIn(GENERAL, self.notices[0][1])
        self.assertEqual(self.problems, [])

    def test_one_signed_out_account_makes_the_run_partial_and_exit_1_while_the_others_work(self):
        from outreach.sending.__main__ import _exit_code
        self.cfg = THREE
        three = accounts.sync(self.db, THREE, iso(MON))[NEU]
        prove(self.db, three.account_id)
        gmail3 = FakeGmail(clock=self.clock, account=NEU, probe=self.probe, id_prefix="c",
                           reconcile_after_minutes=CFG.reconcile_after_minutes)
        self.clients = {REST: self.gmail, GENERAL: self.gmail2, NEU: gmail3}
        sponsor = self.approve()
        self.sponsor(sponsor)
        self.clients = {REST: self.gmail, NEU: gmail3}  # account 2 has no token
        report = self.tick()
        self.assertEqual(report.sent, (sponsor,))  # account 3 sends
        self.assertIn("thread", self.gmail.calls)  # account 1 is read
        self.assertEqual(report.accounts, {REST: "ok", GENERAL: "signed_out", NEU: "ok"})
        self.assertEqual(report.auth, "signed_out")  # the worst sign-in of the tick
        self.assertEqual(self.runs()[-1]["status"], "partial")
        self.assertEqual(_exit_code(self.db, report, False), 1)

    def test_a_send_in_doubt_on_account_2_is_scanned_only_by_account_2(self):
        mid = self.approve()
        self.gmail2.fail_next_send(GmailError("timeout", None, "the call timed out"), accepted=True)
        self.tick()
        self.assertEqual(self.message(mid)["state"], "send_unknown")
        self.clock.advance(minutes=CFG.reconcile_after_minutes + 5)
        self.gmail.calls.clear()
        report = self.tick()
        self.assertNotIn("find_sent", self.gmail.calls)
        self.assertIn("find_sent", self.gmail2.calls)
        self.assertEqual(report.reconciled["sent"], 1)
        self.assertEqual(self.message(mid)["state"], "sent")
        self.assertEqual(len(self.gmail2.sent), 1)

    def test_a_dry_run_over_every_account_writes_and_sends_nothing(self):
        self.approve()
        self.approve()
        before = self.dump()
        report = self.tick(dry_run=True)
        self.assertIsNotNone(report.would_send)
        self.assertEqual(self.dump(), before)
        self.assertEqual((self.gmail.sent, self.gmail2.sent), ([], []))
        self.assertEqual(self.notices, [])
        self.assertEqual(self.runs(), [])

    def test_no_gmail_call_inside_a_transaction_on_any_account(self):
        for _ in range(3):
            self.approve()
        for _ in range(4):
            self.tick()
            self.clock.advance(minutes=10)
        self.assertGreaterEqual(len(self.gmail2.sent), 2)
        self.assertEqual(self.problems, [])

    def test_stop_replies_pause_only_the_account_they_came_to(self):
        company = seed_company(self.db, "Stops")
        touches = [self.rest_thread(company, f"S{i}", f"s{i}@stops.example") for i in range(2)]
        self.db.execute("UPDATE touch SET state = 'replied', replied_at = ?, reply_kind = 'asked_to_stop' "
                        f"WHERE touch_id IN ({touches[0]}, {touches[1]})", (iso(MON - timedelta(hours=1)),))
        fresh = self.approve()
        report = self.tick()
        # The stops came to account 1: its signal is recorded there, never on account 2. Account 1 rests, so the
        # signal pauses nothing (a resting account cannot send; Task A11 review).
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM event WHERE entity = 'sender' AND entity_id = 1 "
                                         "AND kind = 'stop_replies_hard'").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM event WHERE entity = 'sender' AND entity_id = ? "
                                         "AND kind = 'stop_replies_hard'", (self.two.account_id,)).fetchone()[0], 0)
        self.assertEqual(self.account_of(1)["paused"], 0)
        self.assertEqual(self.account_of(self.two.account_id)["paused"], 0)
        self.assertEqual(report.sent, (fresh,))

    def test_one_stop_reply_on_each_account_pauses_neither(self):
        company = seed_company(self.db, "Stops")
        rest = self.rest_thread(company, "S0", "s0@stops.example")
        person = seed_email_person(self.db, seed_company(self.db, "Stops Two"), "S1", "s1@stops2.example")
        seed_sent(self.db, person, at=MON - timedelta(days=2), account_id=self.two.account_id)
        two = self.db.execute("SELECT touch_id FROM touch WHERE person_id = ?", (person,)).fetchone()[0]
        self.db.execute("UPDATE touch SET state = 'replied', replied_at = ?, reply_kind = 'asked_to_stop' "
                        f"WHERE touch_id IN ({rest}, {two})", (iso(MON - timedelta(hours=1)),))
        self.tick()
        self.assertEqual((self.account_of(1)["paused"], self.account_of(self.two.account_id)["paused"]), (0, 0))


class UnprovenTwoTest(MultiBase):
    proven_two = False  # account 2 is new: its first email goes alone and is read back

    def test_account_2s_first_email_goes_alone_and_is_read_back_by_its_own_client(self):
        first, second = self.approve(), self.approve()
        report = self.tick()
        self.assertEqual(report.sent, (first,))
        self.assertEqual(proof.phase(self.db, self.two.account_id), proof.CHECKING)
        self.clock.advance(minutes=10)
        self.gmail.calls.clear()
        report = self.tick()
        self.assertEqual(proof.phase(self.db, self.two.account_id), proof.PROVEN)
        self.assertNotIn("message", self.gmail.calls)  # account 1's client never read account 2's mail back
        self.assertIn("message", self.gmail2.calls)
        self.assertEqual(report.sent, (second,))  # the rest follow at once
        self.assertEqual(proof.phase(self.db, 1), proof.PROVEN)  # account 1's own proof is untouched


# A colleague's email in other words, so the near-identical check never holds it.
OTHER_BODY = ("Hello Sam,\n\nYour talk on settlement retries stuck with me; I rebuilt a ledger reconciler last spring "
              "and hit the same ordering bugs. Portfolio: https://www.devanshuchicholikar.com, code: "
              "https://github.com/devanshu/ledger.\n\nCould we talk for ten minutes?\n\nDevanshu")


class TwoSendersTest(MultiBase):
    cfg = TWO

    def test_tick_sends_one_per_account(self):
        general = [self.approve() for _ in range(2)]
        sponsor = [self.approve() for _ in range(2)]
        for mid in sponsor:
            self.sponsor(mid)
        report = self.tick()
        self.assertEqual(len(report.sent), 2)
        self.assertEqual((len(self.gmail.sent), len(self.gmail2.sent)), (1, 1))
        self.assertEqual(sorted(report.sent), sorted([general[0], sponsor[0]]))
        self.assertEqual(self.tick().sent, ())  # the same moment: each account's gap holds the rest
        self.assertEqual(self.problems, [])

    def test_two_accounts_never_send_to_one_company_in_one_tick(self):
        for seed in range(6):
            with self.subTest(seed=seed):
                self.setUp()
                self.rng = random.Random(seed)
                company = seed_company(self.db, "Shared")
                mine = seed_message(self.db, seed_email_person(self.db, company, "A", "a@shared.example"),
                                    subject="Your team and the backend role 70", body=body(70))
                self.db.execute("UPDATE touch SET account_id = 1 WHERE touch_id = ?", (self.touch_of(mine)["touch_id"],))
                theirs = seed_message(self.db, seed_email_person(self.db, company, "B", "b@shared.example"),
                                      subject="A question about payments infrastructure", body=OTHER_BODY)
                found = {v.message_id: v for v in queue.verdicts(self.db, MON, self.cfg,
                                                                 accounts.in_play(self.db, self.cfg), day=None,
                                                                 gate=None)}
                self.assertEqual((found[mine].reason, found[theirs].reason), ("ready", "ready"))
                self.assertNotEqual(found[mine].account, found[theirs].account)
                report = self.tick()
                self.assertEqual(len(report.sent), 1)
                self.assertEqual(len(self.gmail.sent) + len(self.gmail2.sent), 1)


if __name__ == "__main__":
    unittest.main()
