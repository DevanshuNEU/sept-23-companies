"""The first-send proof: at most one email is ever claimed before Gmail is shown to keep the Message-ID the sender
sets, and that email is read back from Gmail by its Gmail id (`outreach.sending.proof`).

Every test uses FakeGmail and a scratch store. The tick's own tests (`test_sending_tick.FirstSendTest`) run the
whole path; these pin the pieces: the phases, the claim's own check, the read back and `queue.first_only`.
"""
import json
import random
import unittest
from datetime import timedelta
from unittest import mock

from outreach.sending import claim, proof, queue
from outreach.sending.config import load_config
from outreach.sending.fakegmail import FakeClock, FakeGmail
from outreach.sending.mail import GmailError
from outreach.sending.queue import Verdict
from outreach.store import transaction
from outreach.store.writes import append_event
from tests.sendtest import MON, iso, seed_email_person, seed_message, seed_sent
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
        return claim.claim(self.db, q, rfc_id=f"<first.{q.message_id}@gmail.com>", now=iso(self.clock()))

    def send(self, q: queue.Queued) -> str:
        return claim.send_one(self.db, self.gmail, q, CFG, now=iso(self.clock()), clock=self.clock, rng=self.rng,
                              account=CFG.from_address)

    def event(self, kind: str, entity_id: int | None = None) -> None:
        append_event(self.db, entity="message", entity_id=entity_id, kind=kind, actor="sender", at=iso(MON),
                     idempotency_key=f"test:{kind}:{entity_id}")

    def events(self, kind: str) -> list:
        return self.db.execute("SELECT * FROM event WHERE kind = ? ORDER BY event_id", (kind,)).fetchall()

    def state(self, mid: int) -> str:
        return self.db.execute("SELECT state FROM message WHERE message_id = ?", (mid,)).fetchone()[0]


class PhaseTest(Base):
    def test_a_fresh_store_may_send_its_first_email(self):
        self.assertEqual(proof.phase(self.db), proof.FIRST_SEND)
        self.assertTrue(proof.may_claim(self.db))

    def test_sent_mail_the_sender_never_claimed_does_not_count(self):
        company = seed_company(self.db, "Old")
        seed_sent(self.db, seed_email_person(self.db, company, "Old", "old@old.example"), at=MON - timedelta(days=1))
        self.assertEqual(proof.phase(self.db), proof.FIRST_SEND)

    def test_each_kind_of_proof(self):
        for kind in ("import_verified", "send_verified"):
            with self.subTest(kind):
                self.setUp()
                self.approve()
                self.event(kind)
                self.assertEqual(proof.phase(self.db), proof.PROVEN)

    def test_a_sent_event_under_its_claimed_id_is_not_proof_on_its_own(self):
        """The real client reports a send under the claimed id even when its read back failed, and a Sent scan hit
        is only by the id: neither reads the email back by its Gmail id, so only `send_verified` proves it."""
        first = self.approve()
        self.assertEqual(self.send(first), "sent")
        [sent] = [e for e in self.events("sent") if e["entity"] == "message"]
        detail = json.loads(sent["detail"])
        self.assertEqual(detail["rfc_message_id"], detail["claimed_rfc_id"])
        self.assertEqual(proof.phase(self.db), proof.CHECKING)

    def test_the_phases_follow_the_first_email(self):
        first = self.approve()
        self.assertTrue(self.claim(first))
        self.assertEqual(proof.phase(self.db), proof.IN_DOUBT)  # sending
        claim.leftover_claims(self.db, iso(self.clock()))
        self.assertEqual(proof.phase(self.db), proof.IN_DOUBT)  # send_unknown
        self.clock.advance(minutes=CFG.reconcile_after_minutes + 1)
        [u] = claim.unclear(self.db)
        with transaction(self.db):
            claim.reconcile(self.db, {u.message_id: (None, True, u.rfc_id)}, now=iso(self.clock()), cfg=CFG,
                            rng=self.rng)
        self.assertEqual(self.state(first.message_id), "failed")
        self.assertEqual(proof.phase(self.db), proof.STOPPED)
        with transaction(self.db):
            self.assertEqual(claim.requeue_failed(self.db, now=iso(self.clock()), cfg=CFG), 1)
        self.assertEqual(self.state(first.message_id), "approved")
        self.assertEqual(proof.phase(self.db), proof.STOPPED)  # back in the queue, but never claimed again

    def test_a_mismatch_beats_any_proof(self):
        self.event("send_id_mismatch", 1)
        self.event("import_verified")
        self.event("send_verified", 2)
        self.assertEqual(proof.phase(self.db), proof.MISMATCH)
        self.assertFalse(proof.may_claim(self.db))

    def test_every_phase_has_its_words(self):
        for phase in proof.PHASES:
            if phase != proof.PROVEN:
                text = proof.PHASE_TEXT[phase]
                self.assertNotIn("\u2014", text)
                self.assertNotIn("\u2013", text)
        self.assertEqual(proof.PHASE_TEXT[proof.FIRST_SEND], queue.REASON_TEXT["message_id_unproven"])


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
        [u] = claim.unclear(self.db)
        with transaction(self.db):
            claim.reconcile(self.db, {u.message_id: (None, True, u.rfc_id)}, now=iso(self.clock()), cfg=CFG,
                            rng=self.rng)
            claim.requeue_failed(self.db, now=iso(self.clock()), cfg=CFG)
        again = next(q for q in queue.load_queue(self.db, CFG) if q.message_id == first.message_id)
        with self.assertRaises(claim.Unproven):
            claim.claim(self.db, again, rfc_id="<again@gmail.com>", now=iso(self.clock()))
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
        self.assertEqual(proof.verify_sent(self.db, self.gmail, now=self.clock())["verified"], 1)
        self.clock.advance(minutes=10)
        self.assertEqual(self.send(second), "sent")
        self.assertEqual(len(self.gmail.sent), 2)

    def test_a_mismatch_refuses_every_claim_even_with_proof(self):
        first = self.approve()
        self.event("import_verified")
        self.event("send_id_mismatch", 99)
        with self.assertRaises(claim.Unproven):
            self.claim(first)
        self.assertEqual(self.state(first.message_id), "approved")


class VerifySentTest(Base):
    def sent_first(self) -> tuple[queue.Queued, str]:
        first = self.approve()
        self.assertEqual(self.send(first), "sent")
        gmail_id = self.db.execute("SELECT gmail_message_id FROM message WHERE message_id = ?",
                                   (first.message_id,)).fetchone()[0]
        return first, gmail_id

    def test_a_match_is_proof_and_is_written_once(self):
        first, gmail_id = self.sent_first()
        calls = len(self.gmail.calls)
        self.assertEqual(proof.verify_sent(self.db, self.gmail, now=self.clock()),
                         {"verified": 1, "mismatch": 0, "errors": 0})
        self.assertEqual(self.gmail.calls[calls:], ["message"])
        [event] = self.events("send_verified")
        self.assertEqual((event["entity_id"], event["actor"]), (first.message_id, "sender"))
        self.assertEqual(json.loads(event["detail"])["gmail_message_id"], gmail_id)
        self.assertEqual(proof.phase(self.db), proof.PROVEN)
        self.assertEqual(proof.verify_sent(self.db, self.gmail, now=self.clock())["verified"], 0)
        self.assertEqual(self.gmail.calls[calls:], ["message"])  # proven: nothing is read again

    def test_a_header_that_differs_only_in_folding_still_matches(self):
        _, gmail_id = self.sent_first()
        stored = self.gmail.message(gmail_id).headers["message-id"]
        self.gmail.change_stored(gmail_id, message_id=f" {stored}\r\n ")
        self.assertEqual(proof.verify_sent(self.db, self.gmail, now=self.clock())["verified"], 1)

    def test_another_id_no_id_no_sent_label_or_gone_is_a_mismatch(self):
        cases = [("other_message_id", {"message_id": "<x@mail.gmail.com>"}),
                 ("other_message_id", {"message_id": ""}),
                 ("not_in_sent", {"labels": ("INBOX",)})]
        for reason, change in cases:
            with self.subTest(reason=reason, change=change):
                self.setUp()
                first, gmail_id = self.sent_first()
                self.gmail.change_stored(gmail_id, **change)
                self.assertEqual(proof.verify_sent(self.db, self.gmail, now=self.clock())["mismatch"], 1)
                [event] = self.events("send_id_mismatch")
                self.assertEqual(json.loads(event["detail"])["reason"], reason)
                self.assertEqual(proof.phase(self.db), proof.MISMATCH)
        with self.subTest("gone"):
            self.setUp()
            self.sent_first()
            with mock.patch.object(self.gmail, "message",
                                            side_effect=GmailError("bad_request", 404, "Not Found")):
                self.assertEqual(proof.verify_sent(self.db, self.gmail, now=self.clock())["mismatch"], 1)
            self.assertEqual(json.loads(self.events("send_id_mismatch")[0]["detail"])["reason"], "not_found")

    def test_a_gmail_error_writes_nothing_and_is_tried_again(self):
        self.sent_first()
        before = self.db.total_changes
        with mock.patch.object(self.gmail, "message", side_effect=GmailError("timeout", None, "slow")):
            self.assertEqual(proof.verify_sent(self.db, self.gmail, now=self.clock()),
                             {"verified": 0, "mismatch": 0, "errors": 1})
        self.assertEqual(self.db.total_changes, before)
        self.assertEqual(proof.phase(self.db), proof.CHECKING)
        self.assertEqual(proof.verify_sent(self.db, self.gmail, now=self.clock())["verified"], 1)

    def test_it_reads_nothing_unless_a_first_email_waits_for_its_check(self):
        for setup in (lambda: None, lambda: self.event("import_verified"), lambda: self.claim(self.approve())):
            self.setUp()
            setup()
            calls = len(self.gmail.calls)
            self.assertEqual(proof.verify_sent(self.db, self.gmail, now=self.clock())["verified"], 0)
            self.assertEqual(self.gmail.calls[calls:], [])

    def test_a_rewritten_send_is_compared_with_the_id_it_was_claimed_with(self):
        self.gmail.rewrite_message_ids = True
        first = self.approve()
        with self.assertRaises(GmailError):
            self.send(first)  # recorded sent under Gmail's own id, and paused
        self.assertEqual(proof.verify_sent(self.db, self.gmail, now=self.clock())["mismatch"], 1)
        self.assertEqual(proof.phase(self.db), proof.MISMATCH)

    def test_it_never_runs_inside_a_transaction(self):
        with transaction(self.db):
            with self.assertRaises(RuntimeError):
                proof.verify_sent(self.db, self.gmail, now=self.clock())


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


if __name__ == "__main__":
    unittest.main()
