"""Task 12: the approve checks run again on current data right before every claim.

A block found at send time moves the message back to `drafted` for Devanshu's review, with the fresh checks stored
and a `send_blocked` event, and nothing is sent. Warnings never stop a send, and a mail server the cache does not
know only warns. A banned phrases list that cannot be read blocks every claim and leaves the approvals alone.
Every test uses FakeGmail and the fixture banned list, never the DevVault file.
"""
import json
import random
import unittest

from outreach.drafts.checks import load_banned
from outreach.sending import claim, queue
from outreach.sending.config import load_config
from outreach.sending.fakegmail import FakeClock, FakeGmail
from outreach.sending.precheck import precheck
from tests.sendtest import MON, iso, seed_email_person, seed_message
from tests.storetest import fresh_store, seed_company
from tests.test_drafts_checks import FIXTURE_BANNED

CFG = load_config()
DASH = chr(0x2014)  # an em dash, written as a code point so this file has none


def body(n: int) -> str:
    """A first email body unlike any other test body (so near-identical never fires by accident)."""
    topics = ["queue rewrite", "billing service", "search ranking", "edge cache", "data pipeline"]
    return (f"Hi there,\n\nI read about the {topics[n % len(topics)]} work at your team and number {n} of the "
            f"posts on it. I build backend systems in Go and Python and would like to help. My work is at "
            f"https://www.devanshuchicholikar.com and a project is at https://github.com/devanshu/p{n}.\n\n"
            f"Would you refer me?\n\nThanks,\nDevanshu")


class Base(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()
        self.db.execute("PRAGMA fullfsync = ON")  # the sender's connection: send_one refuses any other
        self.company = seed_company(self.db)
        self.clock = FakeClock(MON)
        self.gmail = FakeGmail(clock=self.clock, reconcile_after_minutes=CFG.reconcile_after_minutes)
        self.rng = random.Random(5)
        self.banned, ok = load_banned(FIXTURE_BANNED)
        self.assertTrue(ok)
        self.n = 0

    def approve(self, *, company=None, text=None) -> tuple[int, int]:
        self.n += 1
        person = seed_email_person(self.db, company or self.company, f"P{self.n}", f"p{self.n}@acme.co")
        return person, seed_message(self.db, person, subject=f"Your team and the backend role {self.n}",
                                    body=text or body(self.n))

    def queued(self, message_id: int) -> queue.Queued:
        return next(q for q in queue.load_queue(self.db, CFG) if q.message_id == message_id)

    def step(self, q: queue.Queued, *, banned=None):
        """The tick's step 7: the pre-claim check, then the send only when it found no block."""
        now = iso(self.clock())
        blocks = precheck(self.db, q, now=now, banned=self.banned if banned is None else banned)
        if blocks is not None:
            return blocks
        return claim.send_one(self.db, self.gmail, q, CFG, now=now, clock=self.clock, rng=self.rng,
                              account=CFG.from_address)

    def message(self, message_id: int):
        return self.db.execute("SELECT * FROM message WHERE message_id = ?", (message_id,)).fetchone()

    def events(self, message_id: int, kind: str) -> list[dict]:
        return [json.loads(r[0]) for r in self.db.execute(
            "SELECT detail FROM event WHERE entity = 'message' AND entity_id = ? AND kind = ? ORDER BY event_id",
            (message_id, kind))]


class PrecheckTest(Base):
    def test_no_go_added_after_approval_stops_the_send(self):
        person, mid = self.approve()
        q = self.queued(mid)  # picked before the no-go lands, as a tick would
        self.db.execute("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'said_no', 'devanshu', ?)",
                        (person, iso(self.clock())))
        blocks = self.step(q)
        self.assertIn("no_go_person", [b["code"] for b in blocks])
        self.assertTrue(all(b["severity"] == "block" for b in blocks))
        row = self.message(mid)
        self.assertEqual(row["state"], "drafted")
        self.assertEqual(row["revision"], q.revision + 1)
        self.assertEqual(row["updated_at"], iso(self.clock()))
        stored = json.loads(row["checks"])
        self.assertIn({"code": "no_go_person", "severity": "block"},
                      [{"code": c["code"], "severity": c["severity"]} for c in stored])
        self.assertEqual(self.events(mid, "send_blocked")[0]["codes"], ["no_go_person"])
        self.assertEqual(self.events(mid, "claimed"), [])
        self.assertEqual(self.gmail.sent, [])
        self.assertEqual(self.gmail.calls, [])

    def test_colleague_draft_saved_after_approval_blocks_as_near_identical(self):
        _, mid = self.approve()
        q = self.queued(mid)
        colleague = seed_email_person(self.db, self.company, "Colleague", "colleague@acme.co")
        seed_message(self.db, colleague, state="drafted", subject="Another subject", body=self.message(mid)["body"])
        blocks = self.step(q)
        self.assertEqual([b["code"] for b in blocks], ["near_identical"])
        self.assertEqual(self.message(mid)["state"], "drafted")
        self.assertEqual(self.events(mid, "send_blocked")[0]["codes"], ["near_identical"])
        self.assertEqual(self.gmail.sent, [])

    def test_warnings_never_stop_a_send(self):
        _, mid = self.approve(text=body(1).replace("Would you refer me?", f"Would you refer me {DASH} or not?"))
        found = self.step(self.queued(mid))
        self.assertEqual(found, "sent")
        self.assertEqual(len(self.gmail.sent), 1)
        self.assertEqual(self.message(mid)["state"], "sent")
        self.assertEqual(self.events(mid, "send_blocked"), [])

    def test_unknown_mail_server_does_not_block(self):
        _, mid = self.approve()
        self.assertIsNone(self.db.execute("SELECT 1 FROM mx_check").fetchone())  # nothing cached: unknown
        self.assertEqual(self.step(self.queued(mid)), "sent")
        self.assertEqual(len(self.gmail.sent), 1)

    def test_a_cached_missing_mail_server_blocks(self):
        _, mid = self.approve()
        self.db.execute("INSERT INTO mx_check(domain, status, checked_at) VALUES ('acme.co', 'none', ?)",
                        (iso(self.clock()),))
        blocks = self.step(self.queued(mid))
        self.assertEqual([b["code"] for b in blocks], ["no_mail_server"])
        self.assertEqual(self.gmail.sent, [])

    def test_blocked_message_returns_to_review_and_can_be_approved_again_after_the_fix(self):
        person, mid = self.approve()
        q = self.queued(mid)
        self.db.execute("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'other', 'devanshu', ?)",
                        (person, iso(self.clock())))
        self.assertIsNotNone(self.step(q))
        self.assertEqual(self.message(mid)["state"], "drafted")
        self.assertNotIn(mid, [m.message_id for m in queue.load_queue(self.db, CFG)])
        # The fix: the no-go is lifted, and Devanshu approves it again (a test seed stands in for his click).
        self.db.execute("UPDATE no_go SET lifted_at = ?, lift_reason = 'test' WHERE person_id = ?",
                        (iso(self.clock()), person))
        self.db.execute("UPDATE message SET state = 'approved', approved_at = ?, revision = revision + 1 "
                        "WHERE message_id = ?", (iso(self.clock()), mid))
        self.assertEqual(self.step(self.queued(mid)), "sent")
        self.assertEqual(self.message(mid)["state"], "sent")
        self.assertEqual(len(self.gmail.sent), 1)

    def test_missing_banned_list_blocks_every_claim_and_keeps_the_approvals(self):
        _, first = self.approve()
        _, second = self.approve(company=seed_company(self.db, "Beta"))
        for mid in (first, second):
            before = self.message(mid)
            blocks = self.step(self.queued(mid), banned=[])
            self.assertEqual([b["code"] for b in blocks], ["banned_list_missing"])
            after = self.message(mid)
            # Not the draft's fault: it stays approved, untouched, and waits for the list to be fixed.
            self.assertEqual(tuple(after), tuple(before))
            self.assertEqual(self.events(mid, "send_blocked"), [])
        self.assertEqual(self.gmail.sent, [])

    def test_a_message_no_longer_approved_is_left_to_the_claim(self):
        _, mid = self.approve()
        q = self.queued(mid)
        self.db.execute("UPDATE message SET state = 'skipped' WHERE message_id = ?", (mid,))
        self.assertIsNone(precheck(self.db, q, now=iso(self.clock()), banned=self.banned))
        self.assertEqual(self.message(mid)["state"], "skipped")
        self.assertEqual(self.events(mid, "send_blocked"), [])

    def test_runs_inside_the_callers_transaction_as_a_savepoint(self):
        person, mid = self.approve()
        q = self.queued(mid)
        self.db.execute("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'other', 'devanshu', ?)",
                        (person, iso(self.clock())))
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.assertIsNotNone(precheck(self.db, q, now=iso(self.clock()), banned=self.banned))
            self.assertTrue(self.db.in_transaction)
        finally:
            self.db.execute("ROLLBACK")
        self.assertEqual(self.message(mid)["state"], "approved")


if __name__ == "__main__":
    unittest.main()
