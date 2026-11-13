"""The marker proof: at most one email is ever claimed before Gmail is shown to keep the sender's marker
(`X-Outreach-Id`), and that email is read back from Gmail by its Gmail id (`outreach.sending.proof`).

Every test uses FakeGmail and a scratch store. The tick's own tests (`test_sending_tick.FirstSendTest`) run the
whole path; these pin the pieces: the phases, the claim's own check, the read back and `queue.first_only`.
"""
import json
import random
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest import mock

from outreach.sending import accounts, claim, proof, queue
from outreach.sending.config import load_config
from outreach.sending.fakegmail import FakeClock, FakeGmail
from outreach.sending.mail import GmailError
from outreach.sending.queue import Verdict
from outreach.store import transaction
from outreach.store.writes import append_event
from tests.sendtest import MON, iso, prove, seed_email_person, seed_message, seed_sent
from tests.storetest import fresh_store, seed_company

CFG = load_config()


class Base(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()
        self.db.execute("PRAGMA fullfsync = ON")
        self.clock = FakeClock(MON)
        self.gmail = FakeGmail(clock=self.clock, reconcile_after_minutes=CFG.reconcile_after_minutes)
        self.rng = random.Random(3)
        self.n = 0

    def approve(self) -> queue.Queued:
        self.n += 1
        company = seed_company(self.db, f"Co{self.n}")
        person = seed_email_person(self.db, company, f"P{self.n}", f"p{self.n}@co{self.n}.example")
        mid = seed_message(self.db, person, subject=f"Hello {self.n}", body=f"A short note number {self.n}.")
        return next(q for q in queue.load_queue(self.db, CFG) if q.message_id == mid)

    def claim(self, q: queue.Queued) -> bool:
        return claim.claim(self.db, q, accounts.primary(self.db, CFG), rfc_id=f"<first.{q.message_id}@gmail.com>",
                           now=iso(self.clock()))

    def send(self, q: queue.Queued) -> str:
        return claim.send_one(self.db, self.gmail, q, CFG, now=iso(self.clock()), clock=self.clock, rng=self.rng,
                              account=accounts.primary(self.db, CFG))

    def event(self, kind: str, entity_id: int | None = None) -> None:
        append_event(self.db, entity="message", entity_id=entity_id, kind=kind, actor="sender", at=iso(MON),
                     idempotency_key=f"test:{kind}:{entity_id}")

    def sent_before_the_marker(self) -> int:
        """An email the sender sent before it set the marker, as on the live store: claimed with no `marker`,
        recorded sent under the Message-ID Gmail made, read back under that other id (`send_id_mismatch`)."""
        company = seed_company(self.db, "Before")
        mid = seed_sent(self.db, seed_email_person(self.db, company, "Before", "before@before.example"),
                        at=MON - timedelta(hours=1), rfc="<CABkiztq.1@mail.gmail.com>")
        append_event(self.db, entity="message", entity_id=mid, kind="claimed", actor="sender",
                     at=iso(MON - timedelta(hours=1)), idempotency_key="claimed:<179312908749.1656@gmail.com>",
                     detail={"pending_rfc_id": "<179312908749.1656@gmail.com>", "attempt": 1, "seq": 0})
        self.event("send_id_mismatch", mid)
        return mid

    def events(self, kind: str) -> list:
        return self.db.execute("SELECT * FROM event WHERE kind = ? ORDER BY event_id", (kind,)).fetchall()

    def state(self, mid: int) -> str:
        return self.db.execute("SELECT state FROM message WHERE message_id = ?", (mid,)).fetchone()[0]


class PhaseTest(Base):
    def test_a_fresh_store_may_send_its_first_email(self):
        self.assertEqual(proof.phase(self.db, 1), proof.FIRST_SEND)
        self.assertTrue(proof.may_claim(self.db, 1))

    def test_sent_mail_the_sender_never_claimed_does_not_count(self):
        company = seed_company(self.db, "Old")
        seed_sent(self.db, seed_email_person(self.db, company, "Old", "old@old.example"), at=MON - timedelta(days=1))
        self.assertEqual(proof.phase(self.db, 1), proof.FIRST_SEND)

    def test_only_marker_verified_is_proof(self):
        self.approve()
        self.event("marker_verified")
        self.assertEqual(proof.phase(self.db, 1), proof.PROVEN)

    def test_the_message_id_events_are_no_proof_of_the_marker_and_stop_nothing(self):
        """`import_verified` and `send_verified` say Gmail kept a Message-ID, `send_id_mismatch` that it did not:
        none is about the marker."""
        for kinds in (("import_verified",), ("send_verified",), ("send_id_mismatch",),
                      ("import_verified", "send_verified", "send_id_mismatch")):
            with self.subTest(kinds=kinds):
                self.setUp()
                for n, kind in enumerate(kinds):
                    self.event(kind, n + 1)
                self.assertEqual(proof.phase(self.db, 1), proof.FIRST_SEND)
                self.assertTrue(proof.may_claim(self.db, 1))

    def test_an_email_sent_before_the_marker_neither_proves_nor_blocks(self):
        """The live store's shape: message 5 went out claimed without a marker and is `sent`. It is not a marked
        claim, so the phase is the first send, never `checking` or `stopped`, and exactly one marked email may go."""
        self.sent_before_the_marker()
        self.assertEqual(proof.phase(self.db, 1), proof.FIRST_SEND)
        first, second = self.approve(), self.approve()
        self.assertTrue(self.claim(first))
        self.assertEqual(proof.phase(self.db, 1), proof.IN_DOUBT)
        with self.assertRaises(claim.Unproven):
            self.claim(second)

    def test_every_claim_names_its_marker(self):
        first = self.approve()
        self.assertTrue(self.claim(first))
        [claimed] = self.events("claimed")
        detail = json.loads(claimed["detail"])
        self.assertEqual(detail["marker"], detail["pending_rfc_id"])
        self.assertEqual(detail["marker"], f"<first.{first.message_id}@gmail.com>")

    def test_a_sent_event_under_its_claimed_id_is_not_proof_on_its_own(self):
        """The send's own read back can fail and still report the send, and a Sent scan hit is a scan: only the
        next tick's read back by Gmail id (`marker_verified`) proves the marker."""
        first = self.approve()
        self.assertEqual(self.send(first), "sent")
        [sent] = [e for e in self.events("sent") if e["entity"] == "message"]
        self.assertEqual(json.loads(sent["detail"])["marker"], json.loads(sent["detail"])["claimed_rfc_id"])
        self.assertEqual(proof.phase(self.db, 1), proof.CHECKING)

    def test_the_phases_follow_the_first_email(self):
        first = self.approve()
        self.assertTrue(self.claim(first))
        self.assertEqual(proof.phase(self.db, 1), proof.IN_DOUBT)  # sending
        claim.leftover_claims(self.db, iso(self.clock()))
        self.assertEqual(proof.phase(self.db, 1), proof.IN_DOUBT)  # send_unknown
        self.clock.advance(minutes=CFG.reconcile_after_minutes + 1)
        [u] = claim.unclear(self.db, 1)
        with transaction(self.db):
            claim.reconcile(self.db, {u.message_id: (None, True, u.rfc_id)}, 1, now=iso(self.clock()), cfg=CFG,
                            rng=self.rng)
        self.assertEqual(self.state(first.message_id), "failed")
        self.assertEqual(proof.phase(self.db, 1), proof.STOPPED)
        with transaction(self.db):
            self.assertEqual(claim.requeue_failed(self.db, now=iso(self.clock()), cfg=CFG), 1)
        self.assertEqual(self.state(first.message_id), "approved")
        self.assertEqual(proof.phase(self.db, 1), proof.STOPPED)  # back in the queue, but never claimed again

    def test_a_missing_marker_beats_any_proof(self):
        self.event("marker_missing", 1)
        self.event("marker_verified", 2)
        self.assertEqual(proof.phase(self.db, 1), proof.MISSING)
        self.assertFalse(proof.may_claim(self.db, 1))

    def test_every_phase_has_its_words(self):
        for phase in proof.PHASES:
            if phase != proof.PROVEN:
                text = proof.PHASE_TEXT[phase]
                self.assertNotIn("\u2014", text)
                self.assertNotIn("\u2013", text)
        self.assertEqual(proof.PHASE_TEXT[proof.FIRST_SEND], queue.REASON_TEXT["message_id_unproven"])
        self.assertEqual(proof.PHASE_TEXT[proof.FIRST_SEND],
                         "The first email goes out alone, then Gmail is checked. The rest follow.")


class ClaimTest(Base):
    def test_a_second_email_is_never_claimed_while_unproven(self):
        first, second = self.approve(), self.approve()
        self.assertTrue(self.claim(first))
        with self.assertRaises(claim.Unproven):
            self.claim(second)
        self.assertEqual(self.state(second.message_id), "approved")
        self.assertEqual(len(self.events("claimed")), 1)
        self.assertFalse(self.db.in_transaction)

    def test_the_first_email_is_never_claimed_again_while_unproven(self):
        first = self.approve()
        self.assertTrue(self.claim(first))
        claim.leftover_claims(self.db, iso(self.clock()))
        self.clock.advance(minutes=CFG.reconcile_after_minutes + 1)
        [u] = claim.unclear(self.db, 1)
        with transaction(self.db):
            claim.reconcile(self.db, {u.message_id: (None, True, u.rfc_id)}, 1, now=iso(self.clock()), cfg=CFG,
                            rng=self.rng)
            claim.requeue_failed(self.db, now=iso(self.clock()), cfg=CFG)
        again = next(q for q in queue.load_queue(self.db, CFG) if q.message_id == first.message_id)
        with self.assertRaises(claim.Unproven):
            claim.claim(self.db, again, accounts.primary(self.db, CFG), rfc_id="<again@gmail.com>",
                        now=iso(self.clock()))
        self.assertEqual(self.gmail.sent, [])

    def test_a_sent_first_email_waiting_for_its_check_blocks_the_next(self):
        first, second = self.approve(), self.approve()
        self.assertEqual(self.send(first), "sent")
        with self.assertRaises(claim.Unproven):
            self.send(second)
        self.assertEqual(len(self.gmail.sent), 1)

    def test_once_proven_every_email_may_be_claimed(self):
        first, second = self.approve(), self.approve()
        self.assertEqual(self.send(first), "sent")
        self.assertEqual(proof.verify_marker(self.db, self.gmail, 1, now=self.clock())["verified"], 1)
        self.clock.advance(minutes=10)
        self.assertEqual(self.send(second), "sent")
        self.assertEqual(len(self.gmail.sent), 2)

    def test_a_missing_marker_refuses_every_claim_even_with_proof(self):
        first = self.approve()
        self.event("marker_verified")
        self.event("marker_missing", 99)
        with self.assertRaises(claim.Unproven):
            self.claim(first)
        self.assertEqual(self.state(first.message_id), "approved")


class VerifyMarkerTest(Base):
    def sent_first(self) -> tuple[queue.Queued, str]:
        first = self.approve()
        self.assertEqual(self.send(first), "sent")
        gmail_id = self.db.execute("SELECT gmail_message_id FROM message WHERE message_id = ?",
                                   (first.message_id,)).fetchone()[0]
        return first, gmail_id

    def test_every_email_carries_its_claimed_token_as_the_marker(self):
        first, gmail_id = self.sent_first()
        out, sent, _ = self.gmail.sent[0]
        pending = json.loads(self.events("claimed")[0]["detail"])["pending_rfc_id"]
        self.assertEqual((out.marker, out.message_id), (pending, pending))
        self.assertEqual(self.gmail.message(gmail_id).headers["x-outreach-id"], pending)
        self.assertEqual(sent.marker, pending)

    def test_a_kept_marker_is_proof_and_is_written_once(self):
        first, gmail_id = self.sent_first()
        calls = len(self.gmail.calls)
        self.assertEqual(proof.verify_marker(self.db, self.gmail, 1, now=self.clock()),
                         {"verified": 1, "missing": 0, "errors": 0})
        self.assertEqual(self.gmail.calls[calls:], ["message"])
        [event] = self.events("marker_verified")
        self.assertEqual((event["entity_id"], event["actor"], event["idempotency_key"]),
                         (first.message_id, "sender", f"marker_verified:{first.message_id}"))
        detail = json.loads(event["detail"])
        self.assertEqual(detail["gmail_message_id"], gmail_id)
        self.assertEqual(detail["found_marker"], detail["marker"])
        self.assertEqual(proof.phase(self.db, 1), proof.PROVEN)
        self.assertEqual(proof.verify_marker(self.db, self.gmail, 1, now=self.clock())["verified"], 0)
        self.assertEqual(self.gmail.calls[calls:], ["message"])  # read back once: never again

    def test_a_marker_that_differs_only_in_folding_still_matches(self):
        _, gmail_id = self.sent_first()
        stored = self.gmail.message(gmail_id).headers["x-outreach-id"]
        self.gmail.change_stored(gmail_id, marker=f" {stored}\r\n ")
        self.assertEqual(proof.verify_marker(self.db, self.gmail, 1, now=self.clock())["verified"], 1)

    def test_a_rewritten_message_id_is_normal_and_the_marker_proves_it(self):
        self.gmail.rewrite_message_ids = True
        first, gmail_id = self.sent_first()
        stored = self.gmail.message(gmail_id).headers["message-id"]
        claimed = json.loads(self.events("claimed")[0]["detail"])["marker"]
        self.assertNotEqual(stored, claimed)
        row = self.db.execute("SELECT state, rfc_message_id FROM message WHERE message_id = ?",
                              (first.message_id,)).fetchone()
        self.assertEqual(tuple(row), ("sent", stored))  # recorded under the id Gmail kept
        self.assertEqual(self.db.execute("SELECT paused FROM sender").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM event WHERE kind = 'hard_block'").fetchone()[0], 0)
        self.assertEqual(proof.verify_marker(self.db, self.gmail, 1, now=self.clock())["verified"], 1)
        self.assertEqual(proof.phase(self.db, 1), proof.PROVEN)

    def test_no_marker_another_marker_no_sent_label_or_gone_is_missing(self):
        cases = [("no_marker", {"marker": ""}),
                 ("other_marker", {"marker": "<someone.else@gmail.com>"}),
                 ("not_in_sent", {"labels": ("INBOX",)})]
        for reason, change in cases:
            with self.subTest(reason=reason):
                self.setUp()
                first, gmail_id = self.sent_first()
                self.gmail.change_stored(gmail_id, **change)
                self.assertEqual(proof.verify_marker(self.db, self.gmail, 1, now=self.clock())["missing"], 1)
                [event] = self.events("marker_missing")
                self.assertEqual(json.loads(event["detail"])["reason"], reason)
                self.assertEqual(proof.phase(self.db, 1), proof.MISSING)
        with self.subTest("the replaced id kept, the marker dropped: never proof"):
            self.setUp()
            self.gmail.drop_markers, self.gmail.keep_original_id = True, True
            self.gmail.readback_fails = True  # so the send itself cannot see it (that path has its own test)
            self.sent_first()
            self.assertEqual(proof.verify_marker(self.db, self.gmail, 1, now=self.clock())["missing"], 1)
            self.assertEqual(proof.phase(self.db, 1), proof.MISSING)
        with self.subTest("dropped by Gmail"):
            self.setUp()
            self.gmail.drop_markers = True
            self.gmail.readback_fails = True  # so the send itself cannot see it (that path has its own test)
            self.sent_first()
            self.assertEqual(proof.verify_marker(self.db, self.gmail, 1, now=self.clock())["missing"], 1)
            self.assertEqual(json.loads(self.events("marker_missing")[0]["detail"])["reason"], "no_marker")
        with self.subTest("gone"):
            self.setUp()
            self.sent_first()
            with mock.patch.object(self.gmail, "message", side_effect=GmailError("bad_request", 404, "Not Found")):
                self.assertEqual(proof.verify_marker(self.db, self.gmail, 1, now=self.clock())["missing"], 1)
            self.assertEqual(json.loads(self.events("marker_missing")[0]["detail"])["reason"], "not_found")

    def test_a_gmail_error_writes_nothing_and_is_tried_again(self):
        self.sent_first()
        before = self.db.total_changes
        with mock.patch.object(self.gmail, "message", side_effect=GmailError("timeout", None, "slow")):
            self.assertEqual(proof.verify_marker(self.db, self.gmail, 1, now=self.clock()),
                             {"verified": 0, "missing": 0, "errors": 1})
        self.assertEqual(self.db.total_changes, before)
        self.assertEqual(proof.phase(self.db, 1), proof.CHECKING)
        self.assertEqual(proof.verify_marker(self.db, self.gmail, 1, now=self.clock())["verified"], 1)

    def test_it_reads_nothing_unless_a_marked_sent_email_waits_for_its_read_back(self):
        for setup in (lambda: None, lambda: self.event("marker_verified"), lambda: self.claim(self.approve()),
                      self.sent_before_the_marker):
            self.setUp()
            setup()
            calls = len(self.gmail.calls)
            self.assertEqual(proof.verify_marker(self.db, self.gmail, 1, now=self.clock())["verified"], 0)
            self.assertEqual(self.gmail.calls[calls:], [])

    def test_the_read_back_records_the_message_id_gmail_kept_when_the_send_could_not(self):
        self.gmail.rewrite_message_ids = True
        self.gmail.readback_fails = True
        first, gmail_id = self.sent_first()
        claimed = json.loads(self.events("claimed")[0]["detail"])["marker"]
        row = self.db.execute("SELECT rfc_message_id, touch_id FROM message WHERE message_id = ?",
                              (first.message_id,)).fetchone()
        self.assertEqual(row["rfc_message_id"], claimed)  # all the send could say
        [sent] = [e for e in self.events("sent") if e["entity"] == "message"]
        self.assertIsNone(json.loads(sent["detail"])["marker"])
        self.assertEqual(proof.verify_marker(self.db, self.gmail, 1, now=self.clock())["verified"], 1)
        stored = self.gmail.message(gmail_id).headers["message-id"]
        self.assertEqual(self.db.execute("SELECT rfc_message_id FROM message WHERE message_id = ?",
                                         (first.message_id,)).fetchone()[0], stored)
        self.assertEqual(self.db.execute("SELECT first_rfc_id FROM touch WHERE touch_id = ?",
                                         (row["touch_id"],)).fetchone()[0], stored)

    def test_a_marker_the_sends_own_read_back_shows_dropped_stops_sending_at_once(self):
        """The send's read back already shows Gmail dropped (or changed) the marker: `marker_missing` is written
        with the send, so no second email is claimed even if the next tick's read back fails."""
        for name, change in (("dropped", {"drop_markers": True}), ("changed", None)):
            with self.subTest(name):
                self.setUp()
                self.event("marker_verified")
                first, second = self.approve(), self.approve()
                if change:
                    self.gmail.drop_markers = True
                    self.assertEqual(self.send(first), "sent")
                else:
                    real = self.gmail.send
                    self.gmail.send = lambda m: replace(real(m), marker="<other@gmail.com>")
                    self.assertEqual(self.send(first), "sent")
                    self.gmail.send = real
                [missing] = self.events("marker_missing")
                detail = json.loads(missing["detail"])
                self.assertEqual((missing["entity_id"], detail["reason"]), (first.message_id, "dropped_on_send"))
                self.assertEqual(proof.phase(self.db, 1), proof.MISSING)
                self.clock.advance(minutes=10)
                with self.assertRaises(claim.Unproven):
                    self.send(second)
                self.assertEqual(len(self.gmail.sent), 1)

    def test_an_unread_or_kept_marker_writes_nothing_at_send_time(self):
        self.event("marker_verified")
        first, second = self.approve(), self.approve()
        self.assertEqual(self.send(first), "sent")          # kept
        self.gmail.readback_fails = True
        self.clock.advance(minutes=10)
        self.assertEqual(self.send(second), "sent")         # not read: the next tick reads it
        self.assertEqual(self.events("marker_missing"), [])
        self.assertEqual(proof.phase(self.db, 1), proof.PROVEN)

    def test_once_proven_every_later_email_is_read_back_and_one_without_its_marker_stops_sending(self):
        first, second, third = self.approve(), self.approve(), self.approve()
        self.assertEqual(self.send(first), "sent")
        proof.verify_marker(self.db, self.gmail, 1, now=self.clock())
        self.clock.advance(minutes=10)
        self.gmail.drop_markers = True  # Gmail stops keeping it later on, and the send cannot read it back
        self.gmail.readback_fails = True
        self.assertEqual(self.send(second), "sent")
        self.assertEqual(proof.verify_marker(self.db, self.gmail, 1, now=self.clock()),
                         {"verified": 0, "missing": 1, "errors": 0})
        self.assertEqual(self.events("marker_missing")[0]["entity_id"], second.message_id)
        self.assertEqual(proof.phase(self.db, 1), proof.MISSING)
        self.clock.advance(minutes=10)
        with self.assertRaises(claim.Unproven):
            self.send(third)
        self.assertEqual(len(self.gmail.sent), 2)

    def test_it_never_runs_inside_a_transaction(self):
        with transaction(self.db):
            with self.assertRaises(RuntimeError):
                proof.verify_marker(self.db, self.gmail, 1, now=self.clock())


class FirstOnlyTest(unittest.TestCase):
    def v(self, mid, reason, minutes=None):
        return Verdict(mid, reason, MON + timedelta(minutes=minutes) if minutes is not None else None)

    def test_the_first_ready_goes_and_every_other_ready_or_timed_wait_waits(self):
        out = queue.first_only([self.v(1, "no_go"), self.v(2, "window", 30), self.v(3, "ready"), self.v(4, "ready"),
                                self.v(5, "company_paused"), self.v(6, "spacing", 5)])
        self.assertEqual([(v.message_id, v.reason) for v in out],
                         [(1, "no_go"), (2, "message_id_unproven"), (3, "ready"), (4, "message_id_unproven"),
                          (5, "company_paused"), (6, "message_id_unproven")])
        self.assertEqual(sum(v.reason == "ready" for v in out), 1)

    def test_with_none_ready_the_wait_that_ends_first_keeps_its_time(self):
        out = queue.first_only([self.v(1, "window", 30), self.v(2, "not_business_day", 10), self.v(3, "window", 10)])
        self.assertEqual([(v.message_id, v.reason) for v in out],
                         [(1, "message_id_unproven"), (2, "not_business_day"), (3, "message_id_unproven")])
        self.assertEqual(out[1].earliest_at, MON + timedelta(minutes=10))

    def test_nothing_movable_changes_nothing(self):
        verdicts = [self.v(1, "no_address"), self.v(2, "reply_stop")]
        self.assertEqual(queue.first_only(verdicts), verdicts)
        self.assertEqual(queue.first_only([]), [])


class PerAccountTest(Base):
    """The proof is per account (#9 multi-sender ruling, 2026-11-12): account 1's old proof counts for account 1
    only, a new account sends its own first email alone and is read back by its own client, and a missing marker on
    one account stops only that account."""

    def setUp(self):
        super().setUp()
        rows = accounts.sync(self.db, CFG, iso(MON))
        self.one, self.two = rows["devanshurajesh@gmail.com"], rows["devanshuchi9@gmail.com"]
        self.gmail2 = FakeGmail(clock=self.clock, account=self.two.cfg.address, id_prefix="b",
                                reconcile_after_minutes=CFG.reconcile_after_minutes)

    def send_from(self, q: queue.Queued, account, client) -> str:
        return claim.send_one(self.db, client, q, CFG, now=iso(self.clock()), clock=self.clock, rng=self.rng,
                              account=account)

    def test_account_1s_old_proof_counts_for_account_1_only(self):
        """The live store's proof: a `marker_verified` event on a message whose touch has no account (sent before
        accounts existed) is account 1's."""
        before = self.sent_before_the_marker()
        self.event("marker_verified", before)
        self.assertEqual(proof.phase(self.db, self.one.account_id), proof.PROVEN)
        self.assertEqual(proof.phase(self.db, self.two.account_id), proof.FIRST_SEND)
        self.assertTrue(proof.may_claim(self.db, self.two.account_id))

    def test_new_account_needs_its_own_proof(self):
        prove(self.db)
        first, second, third = self.approve(), self.approve(), self.approve()
        self.assertEqual(self.send_from(first, self.two, self.gmail2), "sent")
        self.assertEqual(proof.phase(self.db, self.two.account_id), proof.CHECKING)
        with self.assertRaises(claim.Unproven):
            self.send_from(second, self.two, self.gmail2)
        self.assertEqual(len(self.gmail2.sent), 1)
        self.assertEqual(self.send_from(third, self.one, self.gmail), "sent")  # account 1 is proven and goes on
        self.assertEqual(proof.phase(self.db, self.one.account_id), proof.PROVEN)
        self.assertEqual(proof.verify_marker(self.db, self.gmail2, self.two.account_id, now=self.clock()),
                         {"verified": 1, "missing": 0, "errors": 0})
        self.assertEqual(proof.phase(self.db, self.two.account_id), proof.PROVEN)
        second = next(q for q in queue.load_queue(self.db, CFG) if q.message_id == second.message_id)
        self.assertEqual(self.send_from(second, self.two, self.gmail2), "sent")

    def test_each_account_reads_back_only_its_own_mail(self):
        """Account 2's email is not in account 1's mailbox: reading it there would look like Gmail lost it."""
        prove(self.db)
        mine, theirs = self.approve(), self.approve()
        self.assertEqual(self.send_from(mine, self.one, self.gmail), "sent")
        self.assertEqual(self.send_from(theirs, self.two, self.gmail2), "sent")
        calls = len(self.gmail.calls)
        self.assertEqual(proof.verify_marker(self.db, self.gmail, self.one.account_id, now=self.clock()),
                         {"verified": 1, "missing": 0, "errors": 0})
        self.assertEqual(self.gmail.calls[calls:], ["message"])
        self.assertEqual([e["entity_id"] for e in self.events("marker_verified") if e["entity_id"] is not None],
                         [mine.message_id])  # prove()'s own event names no message
        self.assertEqual(self.events("marker_missing"), [])
        self.assertEqual(proof.phase(self.db, self.two.account_id), proof.CHECKING)

    def test_marker_missing_on_account_2_stops_only_account_2(self):
        prove(self.db)
        first, later, other = self.approve(), self.approve(), self.approve()
        self.assertEqual(self.send_from(first, self.two, self.gmail2), "sent")
        gmail_id = self.db.execute("SELECT gmail_message_id FROM message WHERE message_id = ?",
                                   (first.message_id,)).fetchone()[0]
        self.gmail2.change_stored(gmail_id, marker="")
        self.assertEqual(proof.verify_marker(self.db, self.gmail2, self.two.account_id, now=self.clock())["missing"],
                         1)
        self.assertEqual(proof.phase(self.db, self.two.account_id), proof.MISSING)
        self.assertFalse(proof.may_claim(self.db, self.two.account_id))
        with self.assertRaises(claim.Unproven):
            self.send_from(later, self.two, self.gmail2)
        self.assertEqual(proof.phase(self.db, self.one.account_id), proof.PROVEN)
        self.assertTrue(proof.may_claim(self.db, self.one.account_id))
        self.assertEqual(self.send_from(other, self.one, self.gmail), "sent")

    def test_a_missing_marker_on_account_1_leaves_account_2_alone(self):
        """The old account-wide stop: an event on a message with no account (or none at all) is account 1's."""
        self.event("marker_missing", None)
        self.assertEqual(proof.phase(self.db, self.one.account_id), proof.MISSING)
        self.assertEqual(proof.phase(self.db, self.two.account_id), proof.FIRST_SEND)


if __name__ == "__main__":
    unittest.main()
