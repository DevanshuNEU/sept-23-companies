"""Task 9: settle unclear sends by Message-ID, and retry a send proven not to have gone out, once.

The only path to a second attempt is send_unknown -> failed -> approved, and only after a complete Sent scan begun
10 minutes after the send returned misses the Message-ID. Every test counts the emails in FakeGmail.sent.
"""
import json
import random
import sqlite3
import unittest
from unittest import mock
from datetime import timedelta

from outreach.sending import claim, queue
from outreach.sending.config import load_config
from outreach.sending.fakegmail import FakeClock, FakeGmail
from outreach.sending.mail import GmailError, Sent
from outreach.store import transaction
from tests.sendtest import MON, iso, seed_email_person, seed_message, seed_sent, settle_and_send, prove
from tests.storetest import fresh_store, seed_company

CFG = load_config()
MS = 60_000


class Base(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()
        prove(self.db)  # these tests claim more than one email; the first-send rule is tested in test_sending_proof
        self.db.execute("PRAGMA fullfsync = ON")  # the sender's connection: send_one refuses any other
        self.company = seed_company(self.db)
        self.clock = FakeClock(MON)
        self.problems: list[str] = []
        self.gmail = FakeGmail(clock=self.clock, probe=self.probe,
                               reconcile_after_minutes=CFG.reconcile_after_minutes)
        self.rng = random.Random(7)
        self.n = 0

    def probe(self, name: str) -> None:
        if self.db.in_transaction:
            self.problems.append(f"{name} called inside a transaction")

    def tearDown(self):
        self.assertEqual(self.problems, [])

    def approve(self, *, company=None):
        self.n += 1
        person = seed_email_person(self.db, company or self.company, f"P{self.n}", f"p{self.n}@acme.co")
        return person, seed_message(self.db, person, subject=f"Hello {self.n}")

    def queued(self, message_id: int) -> queue.Queued:
        return next(q for q in queue.load_queue(self.db, CFG) if q.message_id == message_id)

    def send(self, message_id: int) -> str:
        try:
            return claim.send_one(self.db, self.gmail, self.queued(message_id), CFG, now=iso(self.clock()),
                                  clock=self.clock, rng=self.rng, account=CFG.from_address)
        except GmailError as err:
            return err.kind

    def tick(self, **advance) -> str | None:
        if advance:
            self.clock.advance(**advance)
        return settle_and_send(self.db, self.gmail, self.clock, self.rng, CFG)

    def message(self, message_id: int) -> sqlite3.Row:
        return self.db.execute("SELECT * FROM message WHERE message_id = ?", (message_id,)).fetchone()

    def events(self, message_id: int, kind: str) -> list[dict]:
        return [json.loads(r[0]) for r in self.db.execute(
            "SELECT detail FROM event WHERE entity = 'message' AND entity_id = ? AND kind = ? ORDER BY event_id",
            (message_id, kind))]

    def reconcile(self, found) -> dict:
        with transaction(self.db):
            return claim.reconcile(self.db, found, now=iso(self.clock()), cfg=CFG, rng=self.rng)

    def scan(self) -> dict:
        return {u.message_id: (*self.gmail.find_sent(u.rfc_id, u.not_before_ms, send_returned_ms=u.send_returned_ms),
                               u.rfc_id)
                for u in claim.unclear(self.db)}


class FoundInSentTest(Base):
    def test_unknown_send_found_in_sent_becomes_sent_without_resending(self):
        person, mid = self.approve()
        self.gmail.fail_next_send(GmailError("server", 500, "Backend Error"), accepted=True,
                                  lands_after=timedelta(minutes=3))
        self.assertEqual(self.send(mid), "server")
        pending = self.message(mid)["pending_rfc_id"]
        self.assertEqual(self.tick(minutes=2), None)  # not in Sent yet, and too soon to count a miss
        self.assertEqual(self.message(mid)["state"], "send_unknown")
        self.tick(minutes=2)
        row = self.message(mid)
        self.assertEqual(row["state"], "sent")
        self.assertEqual(row["rfc_message_id"], pending)
        self.assertEqual(row["gmail_message_id"], self.gmail.sent[0][1].gmail_message_id)
        self.assertEqual(row["sent_at"], iso(MON))  # dated to the claim
        self.assertEqual(row["reconciled_at"], iso(self.clock()))
        self.assertIsNone(row["pending_rfc_id"])
        self.assertEqual(len(self.events(mid, "reconciled_sent")), 1)
        self.assertEqual(self.events(mid, "sent"), [])
        touch = self.db.execute("SELECT * FROM touch WHERE person_id = ?", (person,)).fetchone()
        self.assertEqual((touch["first_rfc_id"], touch["gmail_thread_id"]), (pending, self.gmail.sent[0][1].thread_id))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM outreach_touch").fetchone()[0], 1)
        for _ in range(3):
            self.tick(minutes=10)
        self.assertEqual(len(self.gmail.sent), 1)

    def test_timeout_after_accept_is_found_in_sent(self):
        _, mid = self.approve()
        self.gmail.fail_next_send(TimeoutError(), accepted=True)
        self.assertEqual(self.send(mid), "timeout")
        self.assertEqual(self.message(mid)["state"], "send_unknown")
        self.tick(minutes=2)
        self.assertEqual(self.message(mid)["state"], "sent")
        for _ in range(3):
            self.tick(minutes=10)
        self.assertEqual(len(self.gmail.sent), 1)

    def test_crash_leftover_sending_is_reconciled_not_resent(self):
        _, mid = self.approve()
        self.gmail.fail_next_send(SystemExit("killed"), accepted=True)
        with self.assertRaises(SystemExit):
            claim.send_one(self.db, self.gmail, self.queued(mid), CFG, now=iso(MON), clock=self.clock,
                           rng=self.rng, account=CFG.from_address)
        self.assertEqual(self.message(mid)["state"], "sending")
        self.tick(minutes=2)
        row = self.message(mid)
        self.assertEqual(row["state"], "sent")
        self.assertEqual(row["rfc_message_id"], self.gmail.sent[0][0].message_id)
        unknown = self.events(mid, "send_unknown")
        self.assertEqual([(u["error"], u["error_kind"], u["retryable"]) for u in unknown],
                         [("the sender stopped mid-send", "interrupted", True)])
        for _ in range(3):
            self.tick(minutes=10)
        self.assertEqual(len(self.gmail.sent), 1)

    def test_reconcile_matches_by_message_id_only(self):
        _, mid = self.approve()
        self.gmail.fail_next_send(GmailError("network", None, "reset"))
        self.send(mid)
        # Same recipient and subject, another Message-ID: not this email.
        decoy = self.gmail.add_to_sent(f"p{self.n}@acme.co", f"Hello {self.n}", message_id="<other.1@gmail.com>",
                                       at=MON)
        self.clock.advance(minutes=11)
        found = self.scan()
        self.assertEqual(found[mid][:2], (None, True))
        self.reconcile(found)
        row = self.message(mid)
        self.assertEqual(row["state"], "failed")
        self.assertIsNone(row["gmail_message_id"])
        self.assertNotIn(decoy.gmail_message_id, json.dumps(self.events(mid, "claim_failed")))

    def test_a_hit_for_another_message_id_is_not_taken(self):
        _, mid = self.approve()
        self.gmail.fail_next_send(GmailError("network", None, "reset"))
        self.send(mid)
        pending = self.message(mid)["pending_rfc_id"]
        counts = self.reconcile({mid: (Sent("m-9", "t-9", "<someone.else@gmail.com>"), True, pending)})
        self.assertEqual(counts["sent"], 0)
        self.assertEqual(self.message(mid)["state"], "send_unknown")

    def test_follow_up_reconcile_does_not_take_another_follow_ups_id(self):
        followups = []
        for _ in range(2):
            self.n += 1
            person = seed_email_person(self.db, seed_company(self.db, f"Co {self.n}"), f"P{self.n}",
                                       f"p{self.n}@acme.co")
            first = self.gmail.add_to_sent(f"p{self.n}@acme.co", "Hello", message_id=f"<first.{self.n}@gmail.com>",
                                           at=MON - timedelta(days=9))
            seed_sent(self.db, person, at=MON - timedelta(days=9), rfc=f"<first.{self.n}@gmail.com>",
                      thread=first.thread_id)
            followups.append(seed_message(self.db, person, seq=1, subject="Re: Hello", due_on="2026-11-02"))
        a, b = followups
        self.gmail.fail_next_send(GmailError("server", 503), accepted=True)
        self.assertEqual(self.send(a), "server")
        self.gmail.fail_next_send(GmailError("server", 503))
        self.assertEqual(self.send(b), "server")
        self.clock.advance(minutes=11)
        found = self.scan()
        self.assertIsNotNone(found[a][0])
        self.assertEqual(found[b][:2], (None, True))
        self.reconcile(found)
        self.assertEqual(self.message(a)["state"], "sent")
        self.assertEqual(self.message(a)["gmail_message_id"], self.gmail.sent[0][1].gmail_message_id)
        self.assertEqual(self.message(b)["state"], "failed")
        self.assertIsNone(self.message(b)["gmail_message_id"])
        self.assertEqual(len(self.gmail.sent), 1)


class MissingFromSentTest(Base):
    def failed_send(self, error=None) -> int:
        _, mid = self.approve()
        self.gmail.fail_next_send(error or GmailError("server", 503, "Backend Error"))
        self.send(mid)
        self.assertEqual(self.message(mid)["state"], "send_unknown")
        return mid

    def test_unknown_send_missing_from_sent_becomes_failed_after_ten_minutes(self):
        mid = self.failed_send()
        pending = self.message(mid)["pending_rfc_id"]
        self.clock.advance(minutes=9)
        self.assertEqual(self.reconcile(self.scan()), {"sent": 0, "failed": 0, "waiting": 1, "errors": 0})
        self.assertEqual(self.message(mid)["state"], "send_unknown")
        self.clock.advance(minutes=1)
        self.assertEqual(self.reconcile(self.scan()), {"sent": 0, "failed": 1, "waiting": 0, "errors": 0})
        row = self.message(mid)
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["error"], "Not in Sent 10 minutes after the attempt, so it did not go out.")
        self.assertEqual(row["reconciled_at"], iso(self.clock()))
        self.assertIsNone(row["pending_rfc_id"])
        self.assertIsNone(row["claimed_at"])
        [failed] = self.events(mid, "claim_failed")
        self.assertEqual(failed, {"pending_rfc_id": pending, "error": failed["error"], "error_kind": "server",
                                  "retryable": True, "attempt": 1})
        self.assertIn("server", failed["error"])
        self.assertEqual(self.gmail.sent, [])

    def test_recent_unknown_send_waits(self):
        mid = self.failed_send()
        self.clock.advance(minutes=3)
        # Even a scan that calls itself complete does not fail a send younger than the wait.
        pending = self.message(mid)["pending_rfc_id"]
        self.assertEqual(self.reconcile({mid: (None, True, pending)}),
                         {"sent": 0, "failed": 0, "waiting": 1, "errors": 0})
        self.assertEqual(self.message(mid)["state"], "send_unknown")

    def test_not_found_needs_a_scan_back_to_the_claim(self):
        mid = self.failed_send()
        # Many newer messages in Sent: a scan capped at one short page never reaches the claim time.
        for i in range(6):
            self.gmail.add_to_sent("someone@else.com", f"Note {i}", message_id=f"<note.{i}@gmail.com>",
                                   at=MON + timedelta(minutes=20 + i))
        self.gmail.find_sent_page_cap = 1
        self.gmail.find_sent_page_size = 2
        self.clock.advance(minutes=30)
        found = self.scan()
        self.assertEqual(found[mid][:2], (None, False))
        self.reconcile(found)
        self.assertEqual(self.message(mid)["state"], "send_unknown")
        self.gmail.find_sent_page_cap = None
        self.reconcile(self.scan())
        self.assertEqual(self.message(mid)["state"], "failed")

    def test_the_wait_counts_from_when_a_leftover_claim_was_found_not_from_the_claim(self):
        _, mid = self.approve()

        def crash(name):
            if name == "send":
                raise SystemExit("power cut")
        self.gmail.probe = crash
        with self.assertRaises(SystemExit):
            self.send(mid)
        self.gmail.probe = self.probe
        self.clock.advance(hours=2)  # the Mac slept between the claim and the next tick
        claim.leftover_claims(self.db, iso(self.clock()))
        [u] = claim.unclear(self.db)
        self.assertEqual(u.claimed_at, iso(MON))
        self.assertEqual(u.not_before_ms, int(MON.timestamp() * 1000) - 5 * MS)
        self.assertEqual(u.send_returned_ms, int(self.clock().timestamp() * 1000))
        self.reconcile(self.scan())
        self.assertEqual(self.message(mid)["state"], "send_unknown")
        self.clock.advance(minutes=10)
        self.reconcile(self.scan())
        self.assertEqual(self.message(mid)["state"], "failed")

    def test_leftover_claims_turns_every_sending_row_unknown(self):
        _, mid = self.approve()
        self.assertTrue(claim.claim(self.db, self.queued(mid), rfc_id="<c.1@gmail.com>", now=iso(MON)))
        self.assertEqual(claim.leftover_claims(self.db, iso(MON + timedelta(minutes=2))), 1)
        row = self.message(mid)
        self.assertEqual((row["state"], row["error"], row["pending_rfc_id"]),
                         ("send_unknown", "the sender stopped mid-send", "<c.1@gmail.com>"))
        self.assertEqual(claim.leftover_claims(self.db, iso(MON + timedelta(minutes=4))), 0)

    def test_failed_attempt_message_id_is_kept_in_an_event(self):
        mid = self.failed_send()
        first = self.message(mid)["pending_rfc_id"]
        self.tick(minutes=11)  # failed, requeued and sent again under a new Message-ID
        row = self.message(mid)
        self.assertEqual(row["state"], "sent")
        self.assertNotEqual(row["rfc_message_id"], first)
        self.assertEqual(self.events(mid, "claim_failed")[0]["pending_rfc_id"], first)
        self.assertEqual(self.events(mid, "send_unknown")[0]["pending_rfc_id"], first)
        self.assertEqual([c["pending_rfc_id"] for c in self.events(mid, "claimed")], [first, row["rfc_message_id"]])
        self.assertEqual([c["attempt"] for c in self.events(mid, "claimed")], [1, 2])


class RequeueTest(Base):
    def test_requeue_retries_once_then_stops(self):
        _, mid = self.approve()
        self.gmail.fail_next_send(GmailError("server", 503))
        self.gmail.fail_next_send(GmailError("server", 503))
        self.assertEqual(self.send(mid), "server")
        self.assertEqual(self.tick(minutes=11), "server")  # failed, requeued once, tried again, unclear again
        self.assertEqual(self.events(mid, "requeued"), [{"attempts": 1, "previous_rfc_id":
                                                         self.events(mid, "claim_failed")[0]["pending_rfc_id"]}])
        self.assertIsNone(self.tick(minutes=11))
        row = self.message(mid)
        self.assertEqual(row["state"], "failed")
        self.assertEqual(len(self.events(mid, "claimed")), 2)
        self.assertEqual([f["attempt"] for f in self.events(mid, "claim_failed")], [1, 2])
        self.assertEqual(len(self.events(mid, "requeued")), 1)
        for _ in range(3):
            self.assertIsNone(self.tick(minutes=30))
        self.assertEqual(self.message(mid)["state"], "failed")
        self.assertEqual(self.gmail.sent, [])

    def test_bad_request_failure_is_not_requeued(self):
        _, mid = self.approve()
        self.gmail.fail_next_send(GmailError("bad_request", 400, "Invalid To header"))
        self.assertEqual(self.send(mid), "bad_request")
        self.tick(minutes=11)
        self.assertEqual(self.message(mid)["state"], "failed")
        self.assertEqual(self.events(mid, "claim_failed")[0]["retryable"], False)
        self.assertEqual(self.events(mid, "requeued"), [])
        with transaction(self.db):
            self.assertEqual(claim.requeue_failed(self.db, now=iso(self.clock()), cfg=CFG), 0)

    def test_requeue_needs_a_claim_failed_event(self):
        # A failed message that the sender did not fail (an import, a hand edit) is never requeued.
        _, mid = self.approve()
        self.db.execute("UPDATE message SET state = 'sending', pending_rfc_id = '<h.1@gmail.com>', "
                        "claimed_at = ? WHERE message_id = ?", (iso(MON), mid))
        self.db.execute("UPDATE message SET state = 'send_unknown' WHERE message_id = ?", (mid,))
        self.db.execute("UPDATE message SET state = 'failed', pending_rfc_id = NULL, claimed_at = NULL "
                        "WHERE message_id = ?", (mid,))
        with transaction(self.db):
            self.assertEqual(claim.requeue_failed(self.db, now=iso(MON), cfg=CFG), 0)
        self.assertEqual(self.message(mid)["state"], "failed")

    def test_one_email_total_after_a_crash_before_the_call(self):
        _, mid = self.approve()

        def crash(name):
            self.probe(name)
            if name == "send":
                raise SystemExit("power cut")
        self.gmail.probe = crash
        with self.assertRaises(SystemExit):
            self.send(mid)
        first = self.message(mid)["pending_rfc_id"]
        self.gmail.probe = self.probe
        self.assertIsNone(self.tick(minutes=2))      # leftover: send_unknown, too soon to count a miss
        self.assertEqual(self.message(mid)["state"], "send_unknown")
        self.assertEqual(self.tick(minutes=10), "sent")  # failed, requeued, sent once
        for _ in range(3):
            self.tick(minutes=10)
        self.assertEqual(len(self.gmail.sent), 1)
        row = self.message(mid)
        self.assertEqual(row["state"], "sent")
        self.assertEqual(row["rfc_message_id"], self.gmail.sent[0][0].message_id)
        self.assertNotEqual(row["rfc_message_id"], first)


class ReviewFixTest(Base):
    def test_a_miss_for_an_earlier_attempt_never_fails_a_newer_claim(self):
        _, mid = self.approve()
        self.gmail.fail_next_send(GmailError("server", 503, "Backend Error"))
        self.send(mid)
        first_rfc = self.message(mid)["pending_rfc_id"]
        self.clock.advance(minutes=11)
        stale = self.scan()  # a complete miss for the first attempt
        self.assertEqual(stale[mid][:2], (None, True))
        self.reconcile(self.scan())
        with transaction(self.db):
            self.assertEqual(claim.requeue_failed(self.db, now=iso(self.clock()), cfg=CFG), 1)
        # The second attempt went out, but Gmail is slow to show it in Sent.
        self.gmail.fail_next_send(GmailError("timeout", None, "timed out"), accepted=True,
                                  lands_after=timedelta(minutes=30))
        self.assertEqual(self.send(mid), "timeout")
        self.assertNotEqual(self.message(mid)["pending_rfc_id"], first_rfc)
        self.clock.advance(minutes=11)
        self.assertEqual(self.reconcile(stale), {"sent": 0, "failed": 0, "waiting": 1, "errors": 0})
        self.assertEqual(self.message(mid)["state"], "send_unknown")

    def test_one_message_whose_record_always_fails_does_not_roll_back_the_others(self):
        _, broken = self.approve()
        _, fine = self.approve(company=seed_company(self.db, "Beta"))
        for mid in (broken, fine):
            self.gmail.fail_next_send(GmailError("server", 500), accepted=True)
            self.send(mid)
        self.clock.advance(minutes=2)
        found = self.scan()
        real = claim.record_sent

        def record(conn, message_id, *args, **kwargs):
            if message_id == broken:
                raise sqlite3.OperationalError("disk I/O error")
            return real(conn, message_id, *args, **kwargs)
        with mock.patch.object(claim, "record_sent", side_effect=record), \
                self.assertLogs("outreach.sending.claim", level="WARNING") as logged:
            counts = self.reconcile(found)
        self.assertIn(f"message {broken}", logged.output[0])
        self.assertEqual((counts["sent"], counts["errors"]), (1, 1))
        self.assertEqual(self.message(fine)["state"], "sent")
        self.assertEqual(self.message(broken)["state"], "send_unknown")
        self.reconcile(self.scan())
        self.assertEqual(self.message(broken)["state"], "sent")
        self.assertEqual(len(self.gmail.sent), 2)


if __name__ == "__main__":
    unittest.main()
