"""Task 8: claim with the Message-ID before Gmail, then record the send (or that it is unclear).

Every test uses FakeGmail. Its probe runs at the start of every client call and checks that the sender holds no
transaction and that another connection could write at that moment.
"""
import json
import random
import sqlite3
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from outreach.outcomes.context import CONTEXT_KEYS
from outreach.sending import claim, queue
from outreach.sending.config import load_config
from outreach.sending.fakegmail import FakeClock, FakeGmail
from outreach.sending.followups import plan_followups, stop_planned
from outreach.sending.mail import GmailError, MESSAGE_ID, Sent
from outreach.store import transaction
from tests.sendtest import MON, UTC, iso, seed_email_person, seed_message, seed_sent, settle_and_send
from tests.storetest import fresh_store, seed_company

CFG = load_config()


def parse(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


class Base(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()
        self.db.execute("PRAGMA fullfsync = ON")  # the sender's connection: send_one refuses any other
        self.path = self.db.execute("PRAGMA database_list").fetchone()["file"]
        self.company = seed_company(self.db)
        self.clock = FakeClock(MON)
        self.problems: list[str] = []
        self.inside_send = None  # (state, pending_rfc_id) read by another connection inside send
        self.gmail = FakeGmail(clock=self.clock, probe=self.probe,
                               reconcile_after_minutes=CFG.reconcile_after_minutes)
        self.rng = random.Random(7)
        self.n = 0

    def probe(self, name: str) -> None:
        if self.db.in_transaction:
            self.problems.append(f"{name} called inside a transaction")
        other = sqlite3.connect(self.path, timeout=0, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.execute("ROLLBACK")
            if name == "send":
                self.inside_send = other.execute(
                    "SELECT state, pending_rfc_id FROM message WHERE state = 'sending'").fetchall()
        except sqlite3.OperationalError as err:
            self.problems.append(f"{name}: another connection could not write: {err}")
        finally:
            other.close()

    def person(self, *, company=None, role="engineer"):
        self.n += 1
        return seed_email_person(self.db, company or self.company, f"P{self.n}", f"p{self.n}@acme.co",
                                 role_kind=role)

    def approve(self, *, company=None, subject=None):
        person = self.person(company=company)
        return person, seed_message(self.db, person, subject=subject or f"Hello {self.n}")

    def queued(self, message_id: int) -> queue.Queued:
        return next(q for q in queue.load_queue(self.db, CFG) if q.message_id == message_id)

    def send(self, message_id: int, q: queue.Queued | None = None) -> str:
        return claim.send_one(self.db, self.gmail, q or self.queued(message_id), CFG, now=iso(self.clock()),
                              clock=self.clock, rng=self.rng, account=CFG.from_address)

    def message(self, message_id: int) -> sqlite3.Row:
        return self.db.execute("SELECT * FROM message WHERE message_id = ?", (message_id,)).fetchone()

    def touch(self, person_id: int) -> sqlite3.Row:
        return self.db.execute("SELECT * FROM touch WHERE person_id = ?", (person_id,)).fetchone()

    def events(self, message_id: int, kind: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM event WHERE entity = 'message' AND entity_id = ?"
        args: tuple = (message_id,)
        if kind:
            sql += " AND kind = ?"
            args += (kind,)
        return self.db.execute(sql + " ORDER BY event_id", args).fetchall()

    def approve_planned(self, message_id: int, subject: str, body: str = "Following up on my note.") -> None:
        """Stands in for #13's draft and his click: a planned follow-up made drafted, then approved."""
        self.db.execute("UPDATE message SET state = 'drafted', subject = ?, body = ? WHERE message_id = ?",
                        (subject, body, message_id))
        self.db.execute("UPDATE message SET state = 'approved', approved_at = ?, due_on = ? WHERE message_id = ?",
                        (iso(self.clock()), iso(self.clock())[:10], message_id))


class ClaimBeforeTheCallTest(Base):
    def test_send_is_claimed_with_its_message_id_before_the_call(self):
        _, mid = self.approve()
        self.assertEqual(self.send(mid), "sent")
        outgoing = self.gmail.sent[0][0]
        self.assertEqual([tuple(r) for r in self.inside_send], [("sending", outgoing.message_id)])
        self.assertTrue(MESSAGE_ID.fullmatch(outgoing.message_id))
        claimed = self.events(mid, "claimed")
        self.assertEqual(len(claimed), 1)
        self.assertEqual(json.loads(claimed[0]["detail"])["pending_rfc_id"], outgoing.message_id)

    def test_no_gmail_call_inside_a_transaction(self):
        _, first = self.approve()
        self.send(first)
        self.gmail.fail_next_send(GmailError("server", 503, "backend error"))
        _, second = self.approve()
        with self.assertRaises(GmailError):
            self.send(second)
        self.assertIn("send", self.gmail.calls)
        self.assertEqual(self.problems, [])

    def test_claim_is_its_own_committed_transaction(self):
        _, mid = self.approve()
        self.assertTrue(claim.claim(self.db, self.queued(mid), rfc_id="<a.1@gmail.com>", now=iso(MON)))
        self.assertFalse(self.db.in_transaction)
        other = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(other.close)
        self.assertEqual(other.execute("SELECT state, pending_rfc_id, claimed_at FROM message WHERE message_id = ?",
                                       (mid,)).fetchone(), ("sending", "<a.1@gmail.com>", iso(MON)))

    def test_claim_refuses_an_id_that_is_not_one_message_id(self):
        _, mid = self.approve()
        with self.assertRaises(ValueError):
            claim.claim(self.db, self.queued(mid), rfc_id="not-an-id", now=iso(MON))
        self.assertEqual(self.message(mid)["state"], "approved")


class RecordSentTest(Base):
    def test_sent_message_records_ids_and_clears_the_claim(self):
        _, mid = self.approve()
        self.clock.advance(seconds=3)  # the call took a while: sent_at is when it returned
        self.send(mid)
        out, sent, _ = self.gmail.sent[0]
        row = self.message(mid)
        self.assertEqual(row["state"], "sent")
        self.assertEqual(row["rfc_message_id"], out.message_id)
        self.assertEqual(row["gmail_message_id"], sent.gmail_message_id)
        self.assertEqual(row["sent_at"], iso(self.clock()))
        self.assertIsNone(row["pending_rfc_id"])
        self.assertIsNone(row["claimed_at"])
        self.assertIsNone(row["reconciled_at"])
        self.assertEqual(row["error"], "")
        self.assertEqual(len(self.events(mid, "sent")), 1)
        self.assertEqual(self.events(mid, "sent")[0]["actor"], "sender")

    def test_first_send_sets_the_touch_thread_and_logs_one_outreach_touch(self):
        survivor = seed_company(self.db, "Survivor")
        self.db.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                        "WHERE company_id = ?", (survivor, self.company))
        person, mid = self.approve()
        self.send(mid)
        out, sent, _ = self.gmail.sent[0]
        touch = self.touch(person)
        self.assertEqual((touch["first_sent_at"], touch["first_rfc_id"], touch["gmail_thread_id"]),
                         (iso(MON), out.message_id, sent.thread_id))
        rows = self.db.execute("SELECT * FROM outreach_touch").fetchall()
        self.assertEqual(len(rows), 1)
        ot = rows[0]
        self.assertEqual((ot["channel"], ot["email_touch_id"], ot["person_id"], ot["company_id"], ot["sent_at"],
                          ot["idempotency_key"]),
                         ("email", touch["touch_id"], person, survivor, iso(MON), f"email:{touch['touch_id']}"))
        self.assertEqual(set(json.loads(ot["context"])), set(CONTEXT_KEYS))
        event = self.db.execute("SELECT actor FROM event WHERE entity = 'outreach_touch' AND kind = 'sent'").fetchall()
        self.assertEqual([e["actor"] for e in event], ["sender"])
        # Recording the same touch again writes no second outreach touch (and a sent message is final).
        with self.assertRaises(LookupError):
            claim.record_sent(self.db, mid, sent, at=iso(MON), cfg=CFG, rng=self.rng)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM outreach_touch").fetchone()[0], 1)

    def test_an_outreach_touch_already_logged_for_the_email_touch_is_not_logged_again(self):
        person, mid = self.approve()
        touch_id = self.touch(person)["touch_id"]
        ctx = claim.email_context(self.db, person, self.company, at=iso(MON))
        self.db.execute(
            "INSERT INTO outreach_touch(person_id, company_id, channel, email_touch_id, sent_at, context, "
            "idempotency_key, created_at) VALUES (?, ?, 'email', ?, ?, ?, 'import:1', ?)",
            (person, self.company, touch_id, iso(MON), json.dumps(ctx), iso(MON)))
        self.send(mid)
        self.assertEqual(self.message(mid)["state"], "sent")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM outreach_touch").fetchone()[0], 1)

    def test_first_send_plans_one_follow_up_seven_business_days_later(self):
        person, mid = self.approve()
        self.send(mid)
        rows = self.db.execute("SELECT seq, state, due_on, saved_by, subject FROM message WHERE touch_id = ? "
                               "AND seq > 0", (self.touch(person)["touch_id"],)).fetchall()
        self.assertEqual([tuple(r) for r in rows], [(1, "planned", "2026-11-11", "sender", "")])

    def test_next_send_at_is_four_to_nine_minutes_later(self):
        _, mid = self.approve()
        self.send(mid)
        gap = random.Random(7).uniform(*CFG.gap_minutes)
        expected = iso(MON + timedelta(minutes=gap))
        got = self.db.execute("SELECT next_send_at FROM sender").fetchone()[0]
        self.assertEqual(got, expected)
        self.assertTrue(MON + timedelta(minutes=4) <= parse(got) <= MON + timedelta(minutes=9))

    def test_a_reconciled_record_never_moves_next_send_at_earlier(self):
        _, mid = self.approve()
        self.gmail.fail_next_send(GmailError("server", 503))
        with self.assertRaises(GmailError):
            self.send(mid)
        # A reconcile hit dates the send to its claim, which can be long before a gap set since.
        self.db.execute("UPDATE sender SET next_send_at = '2026-11-02T20:00:00Z'")
        claim.record_sent(self.db, mid, Sent("m-x", "t-x", self.message(mid)["pending_rfc_id"]), at=iso(MON),
                          cfg=CFG, rng=self.rng, reconciled=True)
        self.assertEqual(self.db.execute("SELECT next_send_at FROM sender").fetchone()[0], "2026-11-02T20:00:00Z")


class ContextTest(Base):
    def plan_row(self, person: int, *, group="level_role", posting=None, channel="email"):
        self.db.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES ('2026-11-02', ?, 10) "
                        "ON CONFLICT DO NOTHING", (iso(MON),))
        self.db.execute(
            "INSERT INTO daily_list_entry(list_date, company_id, list_group, rank, posting_id, proposed_at, "
            "person_id, channel) VALUES ('2026-11-02', ?, ?, 1, ?, ?, ?, ?)",
            (self.company, group, posting, iso(MON), person, channel))

    def score(self, company: int, lanes: list, values: dict, *, version="v3", at="2026-11-01T00:00:00Z"):
        self.db.execute("INSERT INTO company_score(company_id, ranker_version, score, signals, lanes, scored_at) "
                        "VALUES (?, ?, 1.0, ?, ?, ?)",
                        (company, version, json.dumps({"values": values}), json.dumps(lanes), at))

    def test_context_source_is_plan_for_a_plan_row_person_and_own_otherwise(self):
        self.score(self.company, ["any_us_role"], {"live_role": {"value": 2}, "funding": {"value": 0.5}})
        posting = self.db.execute(
            "INSERT INTO posting(company_id, source_key, title, first_seen_at, last_seen_at) "
            "VALUES (?, 'url:p1', 'Engineer', '2026-10-28T12:00:00Z', ?)", (self.company, iso(MON))).lastrowid
        planned = self.person()
        self.plan_row(planned, posting=posting)
        own = self.person(role="founder")
        on_list_by_linkedin = self.person()
        self.db.execute("INSERT INTO daily_list_entry(list_date, company_id, list_group, rank, proposed_at, "
                        "person_id, channel) VALUES ('2026-11-02', ?, 'any_us_role', 2, ?, ?, 'linkedin_request')",
                        (seed_company(self.db, "Other"), iso(MON), on_list_by_linkedin))

        ctx = claim.email_context(self.db, planned, self.company, at=iso(MON))
        self.assertEqual(ctx, {"source": "plan", "lane": "level_role", "roleKind": "engineer", "roleAgeDays": 5,
                               "rankerVersion": "v3", "signals": {"live_role": 2.0, "funding": 0.5},
                               "roleNamed": True})
        ctx = claim.email_context(self.db, own, self.company, at=iso(MON))
        self.assertEqual(ctx, {"source": "own", "lane": "any_us_role", "roleKind": "founder", "roleAgeDays": None,
                               "rankerVersion": "v3", "signals": {"live_role": 2.0, "funding": 0.5},
                               "roleNamed": False})
        self.assertEqual(claim.email_context(self.db, on_list_by_linkedin, self.company, at=iso(MON))["source"],
                         "own")

    def test_context_with_no_score_uses_the_own_lane(self):
        # build_context refuses a None lane; the API's own touches use "own" for a company with no score.
        ctx = claim.email_context(self.db, self.person(), self.company, at=iso(MON))
        self.assertEqual((ctx["lane"], ctx["rankerVersion"], ctx["signals"]), ("own", None, {}))

    def test_a_sent_touch_stores_the_plan_context(self):
        person, mid = self.approve()
        self.plan_row(person, group="intern_junior")
        self.send(mid)
        ctx = json.loads(self.db.execute("SELECT context FROM outreach_touch").fetchone()[0])
        self.assertEqual((ctx["source"], ctx["lane"]), ("plan", "intern_junior"))


class FollowUpTest(Base):
    def test_follow_up_goes_in_the_thread_with_reply_headers(self):
        person, first = self.approve(subject="Quick question")
        self.send(first)
        first_out, first_sent, _ = self.gmail.sent[0]
        planned = self.db.execute("SELECT message_id FROM message WHERE touch_id = ? AND seq = 1",
                                  (self.touch(person)["touch_id"],)).fetchone()[0]
        self.clock.advance(days=9)
        self.approve_planned(planned, "Re: Quick question")
        self.assertEqual(self.send(planned), "sent")
        out, sent, _ = self.gmail.sent[1]
        self.assertEqual(out.thread_id, first_sent.thread_id)
        self.assertEqual(out.in_reply_to, first_out.message_id)
        self.assertEqual(out.references, (first_out.message_id,))
        self.assertEqual(sent.thread_id, first_sent.thread_id)
        self.assertNotEqual(out.message_id, first_out.message_id)
        # A follow-up writes no second outreach touch and plans nothing more.
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM outreach_touch").fetchone()[0], 1)
        self.assertEqual(self.touch(person)["first_rfc_id"], first_out.message_id)

    def test_references_are_oldest_first_and_reply_to_the_newest(self):
        person = self.person()
        seed_sent(self.db, person, at="2026-10-01T14:00:00Z", rfc="<one@gmail.com>", thread="t-9")
        seed_sent(self.db, person, at="2026-10-12T14:00:00Z", seq=1, rfc="<two@gmail.com>")
        mid = seed_message(self.db, person, seq=2, subject="Re: Hello", due_on="2026-11-02")
        out = claim.outgoing_for(self.db, self.queued(mid), CFG, "<three@gmail.com>")
        self.assertEqual((out.thread_id, out.in_reply_to, out.references),
                         ("t-9", "<two@gmail.com>", ("<one@gmail.com>", "<two@gmail.com>")))
        self.assertEqual((out.subject, out.body, out.to, out.message_id),
                         ("Re: Hello", "Hello there.", f"p{self.n}@acme.co", "<three@gmail.com>"))
        self.assertEqual((out.from_addr, out.from_name), (CFG.from_address, CFG.from_name))

    def test_placeholder_and_malformed_ids_are_left_out_but_the_thread_is_kept(self):
        person = self.person()
        seed_sent(self.db, person, at="2026-10-01T14:00:00Z", rfc="<imported.7@outreach.invalid>", thread="t-4")
        seed_sent(self.db, person, at="2026-10-12T14:00:00Z", seq=1, rfc="no angle brackets")
        mid = seed_message(self.db, person, seq=2, subject="Re: Hello", due_on="2026-11-02")
        out = claim.outgoing_for(self.db, self.queued(mid), CFG, "<three@gmail.com>")
        self.assertEqual((out.thread_id, out.in_reply_to, out.references), ("t-4", None, ()))

    def test_first_email_has_no_thread_or_reply_headers(self):
        _, mid = self.approve()
        out = claim.outgoing_for(self.db, self.queued(mid), CFG, "<x.1@gmail.com>")
        self.assertEqual((out.thread_id, out.in_reply_to, out.references), (None, None, ()))


class UnclearTest(Base):
    def test_failed_call_leaves_send_unknown_and_cannot_be_claimed_again(self):
        _, mid = self.approve()
        q = self.queued(mid)
        self.gmail.fail_next_send(GmailError("server", 503, "Backend Error"))
        with self.assertRaises(GmailError) as caught:
            self.send(mid, q)
        self.assertEqual(caught.exception.kind, "server")
        row = self.message(mid)
        self.assertEqual(row["state"], "send_unknown")
        self.assertIsNotNone(row["pending_rfc_id"])
        self.assertIn("server", row["error"])
        detail = json.loads(self.events(mid, "send_unknown")[0]["detail"])
        self.assertEqual((detail["error_kind"], detail["retryable"]), ("server", True))
        self.assertFalse(claim.claim(self.db, q, rfc_id="<again.1@gmail.com>", now=iso(MON)))
        self.assertEqual(self.message(mid)["pending_rfc_id"], row["pending_rfc_id"])
        self.assertEqual([q.message_id for q in queue.load_queue(self.db, CFG)], [])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM outreach_touch").fetchone()[0], 0)

    def test_a_raw_timeout_is_reported_as_a_gmail_timeout(self):
        _, mid = self.approve()
        self.gmail.fail_next_send(TimeoutError("timed out"))
        with self.assertRaises(GmailError) as caught:
            self.send(mid)
        self.assertEqual(caught.exception.kind, "timeout")
        self.assertEqual(self.message(mid)["state"], "send_unknown")

    def test_unclear_error_text_holds_no_secret_and_is_short(self):
        _, mid = self.approve()
        self.gmail.fail_next_send(RuntimeError("Bearer ya29.secret-token " + "x" * 600))
        with self.assertRaises(GmailError):
            self.send(mid)
        error = self.message(mid)["error"]
        self.assertNotIn("ya29.secret-token", error)
        self.assertLessEqual(len(error), 300)
        self.assertNotIn("ya29.secret-token", self.events(mid, "send_unknown")[0]["detail"])

    def test_an_unclear_send_also_spaces_the_next_one(self):
        _, mid = self.approve()
        self.gmail.fail_next_send(GmailError("network", None, "connection reset"))
        with self.assertRaises(GmailError):
            self.send(mid)
        got = parse(self.db.execute("SELECT next_send_at FROM sender").fetchone()[0])
        self.assertTrue(MON + timedelta(minutes=4) <= got <= MON + timedelta(minutes=9))

    def test_rewritten_message_id_is_recorded_sent_and_pauses(self):
        _, mid = self.approve()
        self.gmail.rewrite_message_ids = True
        with self.assertRaises(GmailError) as caught:
            self.send(mid)
        self.assertEqual(caught.exception.kind, "message_id_rewritten")
        row = self.message(mid)
        self.assertEqual(row["state"], "sent")
        self.assertEqual(row["rfc_message_id"], caught.exception.sent.rfc_message_id)
        self.assertEqual(row["gmail_message_id"], caught.exception.sent.gmail_message_id)
        sender = self.db.execute("SELECT paused, pause_kind, paused_reason FROM sender").fetchone()
        self.assertEqual((sender["paused"], sender["pause_kind"]), (1, "hard_block"))
        self.assertIn("Message-ID", sender["paused_reason"])
        hard = self.db.execute("SELECT detail FROM event WHERE entity = 'sender' AND kind = 'hard_block'").fetchall()
        self.assertEqual([json.loads(h[0])["kind"] for h in hard], ["message_id_rewritten"])
        self.assertEqual(len(self.gmail.sent), 1)
        # The pause and a never-retried send_unknown were committed first, so a kill mid-record could not resend.
        [unknown] = self.events(mid, "send_unknown")
        detail = json.loads(unknown["detail"])
        self.assertEqual((detail["error_kind"], detail["retryable"], detail["stored_rfc_id"]),
                         ("message_id_rewritten", False, caught.exception.sent.rfc_message_id))

    def test_a_kill_during_the_rewritten_record_sends_once_and_pauses(self):
        _, mid = self.approve()
        self.gmail.rewrite_message_ids = True
        with mock.patch.object(claim, "record_sent", side_effect=SystemExit("killed while recording")):
            with self.assertRaises(SystemExit):
                self.send(mid)
        self.assertFalse(self.db.in_transaction)
        sender = self.db.execute("SELECT paused, pause_kind FROM sender").fetchone()
        self.assertEqual((sender["paused"], sender["pause_kind"]), (1, "hard_block"))
        self.assertEqual(self.message(mid)["state"], "send_unknown")
        stored = self.gmail.sent[0][1]
        for _ in range(4):
            self.clock.advance(minutes=11)
            settle_and_send(self.db, self.gmail, self.clock, self.rng, CFG)
            self.db.execute("UPDATE sender SET paused = 0, pause_kind = NULL, paused_at = NULL")  # even resumed
        self.assertEqual(len(self.gmail.sent), 1)
        row = self.message(mid)
        self.assertEqual((row["state"], row["rfc_message_id"], row["gmail_message_id"]),
                         ("sent", stored.rfc_message_id, stored.gmail_message_id))
        self.assertEqual(self.events(mid, "claim_failed"), [])
        self.assertEqual(self.touch(self.db.execute("SELECT person_id FROM touch WHERE touch_id = ?",
                                                    (row["touch_id"],)).fetchone()[0])["gmail_thread_id"],
                         stored.thread_id)

    def test_a_rewritten_send_that_cannot_be_recorded_is_never_retried(self):
        _, mid = self.approve()
        self.gmail.rewrite_message_ids = True
        with mock.patch.object(claim, "log_touch", side_effect=sqlite3.OperationalError("disk I/O error")):
            with self.assertRaises(GmailError):
                self.send(mid)
        row = self.message(mid)
        self.assertEqual(row["state"], "send_unknown")
        detail = json.loads(self.events(mid, "send_unknown")[0]["detail"])
        self.assertEqual((detail["error_kind"], detail["retryable"]), ("message_id_rewritten", False))
        self.assertEqual(detail["stored_rfc_id"], self.gmail.sent[0][1].rfc_message_id)
        for _ in range(3):
            self.clock.advance(minutes=11)
            settle_and_send(self.db, self.gmail, self.clock, self.rng, CFG)
        # Recorded from the ids Gmail kept, never failed as "did not go out".
        self.assertEqual(self.message(mid)["state"], "sent")
        self.assertEqual(self.message(mid)["rfc_message_id"], self.gmail.sent[0][1].rfc_message_id)
        self.assertEqual(self.events(mid, "claim_failed"), [])
        self.assertEqual(len(self.gmail.sent), 1)


class CrashWindowTest(Base):
    def test_crash_after_claim_before_the_call_leaves_sending(self):
        _, mid = self.approve()

        def crash(name):
            self.probe(name)
            if name == "send":
                raise SystemExit("power cut")
        self.gmail.probe = crash
        with self.assertRaises(SystemExit):
            self.send(mid)
        row = self.message(mid)
        self.assertEqual(row["state"], "sending")
        self.assertIsNotNone(row["pending_rfc_id"])
        self.assertEqual(self.gmail.sent, [])
        self.assertFalse(self.db.in_transaction)

    def test_crash_after_gmail_accepted_is_recorded_not_resent(self):
        _, mid = self.approve()
        with mock.patch.object(claim, "record_sent", side_effect=SystemExit("killed while recording")):
            with self.assertRaises(SystemExit):
                self.send(mid)
        row = self.message(mid)
        self.assertEqual(row["state"], "sending")
        self.assertEqual(len(self.gmail.sent), 1)
        claimed = row["pending_rfc_id"]
        self.assertEqual(claimed, self.gmail.sent[0][0].message_id)
        # Three more ticks of reconcile and pick: the first finds it in Sent and records it; none sends again.
        for _ in range(3):
            self.clock.advance(minutes=2)
            self.assertIsNone(settle_and_send(self.db, self.gmail, self.clock, self.rng, CFG))
        self.assertEqual(len(self.gmail.sent), 1)
        row = self.message(mid)
        self.assertEqual((row["state"], row["rfc_message_id"]), ("sent", claimed))
        self.assertEqual(len(self.events(mid, "reconciled_sent")), 1)
        self.assertEqual(self.problems, [])

    def test_a_failed_record_after_accept_leaves_sending_not_unclear(self):
        _, mid = self.approve()
        with mock.patch.object(claim, "log_touch", side_effect=sqlite3.OperationalError("database is locked")):
            with self.assertRaises(sqlite3.OperationalError):
                self.send(mid)
        self.assertEqual(self.message(mid)["state"], "sending")
        self.assertEqual(self.events(mid, "send_unknown"), [])
        self.assertEqual(len(self.gmail.sent), 1)


class RaceTest(Base):
    def test_second_claim_while_one_is_sending_is_refused(self):
        _, first = self.approve()
        _, second = self.approve(company=seed_company(self.db, "Beta"))
        q1, q2 = self.queued(first), self.queued(second)
        self.assertTrue(claim.claim(self.db, q1, rfc_id="<a.1@gmail.com>", now=iso(MON)))
        with self.assertRaises(claim.AnotherSendInFlight):
            claim.claim(self.db, q2, rfc_id="<b.1@gmail.com>", now=iso(MON))
        self.assertEqual(self.message(second)["state"], "approved")
        self.assertFalse(self.db.in_transaction)

    def test_lost_race_on_revision_sends_nothing(self):
        _, mid = self.approve()
        q = self.queued(mid)
        self.db.execute("UPDATE message SET body = 'Edited.', revision = revision + 1 WHERE message_id = ?", (mid,))
        self.assertEqual(self.send(mid, q), "lost_race")
        self.assertEqual(self.gmail.sent, [])
        self.assertNotIn("send", self.gmail.calls)
        self.assertEqual(self.message(mid)["state"], "approved")
        self.assertEqual(self.events(mid, "claimed"), [])

    def test_a_no_go_after_the_pick_stops_the_claim(self):
        person, mid = self.approve()
        q = self.queued(mid)
        self.db.execute("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'said_no', 'reply', ?)",
                        (person, iso(MON)))
        self.assertEqual(self.send(mid, q), "lost_race")
        self.assertEqual(self.gmail.sent, [])
        self.assertEqual(self.message(mid)["state"], "approved")

    def test_a_hold_pause_or_stop_after_the_pick_stops_the_claim(self):
        survivor = seed_company(self.db, "Survivor")
        cases = {
            "company no-go on a merged duplicate": lambda p, c: self.db.execute(
                "INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'agency', 'devanshu', ?)",
                (c, iso(MON))),
            "held duplicate": lambda p, c: self.db.execute(
                "UPDATE company SET held_by_touch = (SELECT touch_id FROM touch WHERE person_id = ?), held_at = ? "
                "WHERE company_id = ?", (p, iso(MON), c)),
            "survivor closed": lambda p, c: self.db.execute(
                "UPDATE company SET status = 'closed', close_reason = 'no_referrals' WHERE company_id = ?",
                (survivor,)),
            "survivor paused": lambda p, c: self.db.execute(
                "UPDATE company SET status = 'working', paused_at = ? WHERE company_id = ?", (iso(MON), survivor)),
            "touch replied": lambda p, c: self.db.execute(
                "UPDATE touch SET state = 'replied', replied_at = ?, reply_kind = 'unsorted' WHERE person_id = ?",
                (iso(MON), p)),
            "sender paused": lambda p, c: self.db.execute(
                "UPDATE sender SET paused = 1, pause_kind = 'by_you', paused_at = ?", (iso(MON),)),
        }
        for name, change in cases.items():
            with self.subTest(name):
                duplicate = seed_company(self.db, f"Dup {name}")
                person, mid = self.approve(company=duplicate)
                self.db.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                                "WHERE company_id = ?", (survivor, duplicate))
                q = self.queued(mid)
                with transaction(self.db):
                    change(person, duplicate)
                self.assertEqual(self.send(mid, q), "lost_race")
                self.assertEqual(self.message(mid)["state"], "approved")
                self.assertEqual(self.gmail.sent, [])
                self.db.execute("UPDATE sender SET paused = 0, pause_kind = NULL, paused_at = NULL")
                self.db.execute("UPDATE company SET status = 'working', close_reason = NULL, paused_at = NULL "
                                "WHERE company_id = ?", (survivor,))


class FollowupsStubTest(Base):
    def test_planning_twice_adds_nothing_and_stop_planned_deletes_with_a_touch_event(self):
        person = self.person()
        seed_sent(self.db, person, at="2026-11-02T14:00:00Z")
        touch_id = self.touch(person)["touch_id"]
        with transaction(self.db):
            self.assertEqual(plan_followups(self.db, touch_id, date(2026, 11, 2), CFG, now=iso(MON)), 1)
            self.assertEqual(plan_followups(self.db, touch_id, date(2026, 11, 2), CFG, now=iso(MON)), 0)
        mid = self.db.execute("SELECT message_id FROM message WHERE touch_id = ? AND seq = 1",
                              (touch_id,)).fetchone()[0]
        with transaction(self.db):
            stop_planned(self.db, mid, reason="replied", now=iso(MON))
        self.assertIsNone(self.message(mid))
        ev = self.db.execute("SELECT kind, detail FROM event WHERE entity = 'touch' AND entity_id = ?",
                             (touch_id,)).fetchall()
        self.assertEqual([(e["kind"], json.loads(e["detail"])) for e in ev],
                         [("skipped_by_sender", {"seq": 1, "due_on": "2026-11-11", "reason": "replied"})])
        drafted = seed_message(self.db, person, state="drafted", seq=2, due_on="2026-11-20")
        with self.assertRaises(ValueError):
            stop_planned(self.db, drafted, reason="replied", now=iso(MON))


class NoThreadTest(Base):
    """A follow-up goes only in its thread: an imported touch can have a first send and no Gmail thread."""

    def imported_followup(self, thread):
        person = self.person()
        touch = self.db.execute("INSERT INTO touch(person_id, origin, first_sent_at, first_rfc_id, gmail_thread_id, "
                                "created_at, updated_at) VALUES (?, 'import', ?, ?, ?, ?, ?)",
                                (person, "2026-10-20T14:00:00Z", f"<imp.{self.n}@gmail.com>", thread,
                                 "2026-10-20T14:00:00Z", "2026-10-20T14:00:00Z")).lastrowid
        self.db.execute("INSERT INTO message(touch_id, seq, state, subject, body, sent_at, rfc_message_id, saved_by, "
                        "created_at, updated_at) VALUES (?, 0, 'sent', 'Hi', 'Hello.', ?, ?, 'import', ?, ?)",
                        (touch, "2026-10-20T14:00:00Z", f"<imp.{self.n}@gmail.com>", "2026-10-20T14:00:00Z",
                         "2026-10-20T14:00:00Z"))
        return seed_message(self.db, person, seq=1, subject="Re: Hi", due_on="2026-11-02")

    def test_a_follow_up_with_no_thread_waits_and_is_never_claimed(self):
        for thread in (None, ""):
            with self.subTest(thread=thread):
                mid = self.imported_followup(thread)
                reasons = {v.message_id: v.reason for v in queue.verdicts(self.db, MON, CFG, day=None, gate=None)}
                self.assertEqual(reasons[mid], "no_thread")
                self.assertIn(reasons[mid], queue.REASONS)
                self.assertFalse(claim.claim(self.db, self.queued(mid), rfc_id=f"<nt.{self.n}@gmail.com>",
                                             now=iso(MON)))
                self.assertEqual(self.message(mid)["state"], "approved")
                self.assertEqual(self.gmail.sent, [])

    def test_a_follow_up_with_no_thread_does_not_block_the_queue(self):
        self.imported_followup(None)
        _, ready = self.approve(company=seed_company(self.db, "Beta"))
        self.db.execute("UPDATE message SET approved_at = '2026-11-02T13:45:00Z' WHERE message_id = ?", (ready,))
        self.assertEqual(queue.load_queue(self.db, CFG)[-1].message_id, ready)  # the follow-up is first in line
        picked = queue.pick(queue.verdicts(self.db, MON, CFG, day=None, gate=None), queue.load_queue(self.db, CFG))
        self.assertEqual(picked.message_id, ready)

    def test_an_empty_thread_id_from_gmail_is_never_stored_as_empty_text(self):
        person, mid = self.approve()
        self.assertTrue(claim.claim(self.db, self.queued(mid), rfc_id="<empty.thread@gmail.com>", now=iso(MON)))
        claim.record_sent(self.db, mid, Sent("m-77", "", "<empty.thread@gmail.com>"), at=iso(MON), cfg=CFG,
                          rng=self.rng)
        # Gmail starts a new thread under its first message's id; never '' (a follow-up would leave the thread).
        self.assertEqual(self.touch(person)["gmail_thread_id"], "m-77")


class FullfsyncTest(Base):
    def test_send_one_refuses_a_connection_without_fullfsync(self):
        from outreach.store import connect
        _, mid = self.approve()
        loose = connect(self.path)
        self.addCleanup(loose.close)
        self.gmail.calls.clear()
        with self.assertRaises(RuntimeError):
            claim.send_one(loose, self.gmail, self.queued(mid), CFG, now=iso(MON), clock=self.clock, rng=self.rng,
                           account=CFG.from_address)
        self.assertEqual(self.gmail.calls, [])
        self.assertEqual(self.message(mid)["state"], "approved")


if __name__ == "__main__":
    unittest.main()
