"""Task 10: reading replies, auto-replies and bounces, and applying them to the store.

Every test uses FakeGmail. Its probe runs at the start of every client call and checks that the sender holds no
transaction and that another connection could write at that moment. A missed reply or bounce would send the next
follow-up to someone who already answered, so most tests end on what the queue would do next.
"""
import dataclasses
import json
import random
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from outreach.drafts.followups import company_map, due_followups, held_survivors
from outreach.sending import claim, inbound, queue
from outreach.sending.config import load_config
from outreach.sending.fakegmail import FakeClock, FakeGmail
from outreach.sending.mail import GmailError
from outreach.store import transaction
from tests.sendtest import MON, iso, seed_email_person, seed_message, seed_sent
from tests.storetest import fresh_store, seed_company

CFG = load_config()


class Base(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()
        self.db.execute("PRAGMA fullfsync = ON")  # the sender's connection: send_one refuses any other
        self.path = self.db.execute("PRAGMA database_list").fetchone()["file"]
        self.company = seed_company(self.db)
        self.clock = FakeClock(MON)
        self.problems: list[str] = []
        self.fail_on: dict[str, BaseException] = {}
        self.gmail = FakeGmail(clock=self.clock, probe=self.probe,
                               reconcile_after_minutes=CFG.reconcile_after_minutes)
        self.rng = random.Random(3)
        self.notices: list[tuple[str, str]] = []
        self.n = 0

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
        if name in self.fail_on:
            raise self.fail_on.pop(name)

    def notify(self, title: str, text: str) -> None:
        self.notices.append((title, text))

    def person(self, company=None, *, email=None):
        self.n += 1
        return seed_email_person(self.db, company or self.company, f"P{self.n}", email or f"p{self.n}@acme.co")

    def send_first(self, company=None, *, subject=None):
        """A first email sent through the fake Gmail, as the sender sends it. Returns (person, touch, Sent)."""
        person = self.person(company)
        mid = seed_message(self.db, person, subject=subject or f"Quick question about the platform team {self.n}")
        q = next(q for q in queue.load_queue(self.db, CFG) if q.message_id == mid)
        self.assertEqual(claim.send_one(self.db, self.gmail, q, CFG, now=iso(self.clock()), clock=self.clock,
                                        rng=self.rng, account=CFG.from_address), "sent")
        touch = self.db.execute("SELECT touch_id FROM touch WHERE person_id = ?", (person,)).fetchone()[0]
        return person, touch, self.gmail.sent[-1][1]

    def colleague(self, company=None):
        """Someone else at the company with an approved first email."""
        person = self.person(company)
        return seed_message(self.db, person, subject=f"Hello from Devanshu {self.n}")

    def sync(self, cfg=CFG):
        """One tick's inbound steps: read with no transaction open, then apply in one transaction."""
        snap = inbound.snapshot(self.db, now=self.clock(), cfg=cfg)
        read = inbound.read_inbound(self.gmail, snap, cfg=cfg, now=self.clock())
        with transaction(self.db):
            counts = inbound.apply_inbound(self.db, read, account=CFG.from_address, now=self.clock(), cfg=cfg,
                                           notify=self.notify)
        return read, counts

    def touch(self, touch_id):
        return self.db.execute("SELECT * FROM touch WHERE touch_id = ?", (touch_id,)).fetchone()

    def outreach(self, touch_id):
        return self.db.execute("SELECT * FROM outreach_touch WHERE email_touch_id = ?", (touch_id,)).fetchone()

    def planned(self, touch_id):
        return self.db.execute("SELECT * FROM message WHERE touch_id = ? AND state = 'planned'",
                               (touch_id,)).fetchall()

    def state(self, message_id):
        row = self.db.execute("SELECT state FROM message WHERE message_id = ?", (message_id,)).fetchone()
        return row[0] if row else None

    def verdict(self, message_id):
        found = {v.message_id: v.reason for v in queue.verdicts(self.db, self.clock(), CFG, day=None, gate=None)}
        return found.get(message_id)

    def events(self, entity, kind, entity_id=None):
        sql, args = "SELECT * FROM event WHERE entity = ? AND kind = ?", [entity, kind]
        if entity_id is not None:
            sql += " AND entity_id = ?"
            args.append(entity_id)
        return self.db.execute(sql + " ORDER BY event_id", args).fetchall()

    def inbound_rows(self):
        return self.db.execute("SELECT * FROM inbound_message ORDER BY inbound_id").fetchall()

    def sender(self):
        return self.db.execute("SELECT * FROM sender WHERE sender_id = 1").fetchone()

    def approve_followup(self, touch_id, subject):
        """Stands in for #13's draft and his click on the planned follow-up."""
        mid = self.planned(touch_id)[0]["message_id"]
        self.db.execute("UPDATE message SET state = 'drafted', subject = ?, body = 'Following up.' "
                        "WHERE message_id = ?", (subject, mid))
        self.db.execute("UPDATE message SET state = 'approved', approved_at = ? WHERE message_id = ?",
                        (iso(self.clock()), mid))
        return mid


class HumanReplyTest(Base):
    def test_human_reply_marks_the_touch_and_records_the_outcome(self):
        person, touch_id, sent = self.send_first()
        self.clock.advance(hours=2)
        reply = self.gmail.deliver_reply(sent, f"P{self.n} <p{self.n}@acme.co>", snippet="Happy to chat")
        read, counts = self.sync()
        self.assertEqual(counts["replies"], 1)
        touch = self.touch(touch_id)
        self.assertEqual((touch["state"], touch["replied_at"], touch["reply_kind"]),
                         ("replied", iso(self.clock()), "unsorted"))
        rows = self.inbound_rows()
        self.assertEqual([(r["touch_id"], r["gmail_message_id"], r["kind"], r["received_at"], r["body_text"],
                           r["dsn_class"]) for r in rows],
                         [(touch_id, reply.gmail_message_id, "human", iso(self.clock()), "Happy to chat", None)])
        ot = self.outreach(touch_id)
        self.assertEqual((ot["replied_at"], ot["reply_kind"]), (iso(self.clock()), "unsorted"))
        outcome = self.events("outreach_touch", "replied", ot["touch_id"])
        self.assertEqual([e["actor"] for e in outcome], ["sender"])
        self.assertEqual(len(self.events("touch", "replied", touch_id)), 1)
        self.assertEqual(self.problems, [])

    def test_reply_holds_every_queued_message_at_the_company(self):
        _, touch_id, sent = self.send_first()
        due_on = self.planned(touch_id)[0]["due_on"]
        first_colleague = self.colleague()
        second_colleague = self.colleague()
        self.clock.advance(hours=1)
        self.gmail.deliver_reply(sent, "someone@acme.co")
        self.sync()
        for mid in (first_colleague, second_colleague):
            self.assertEqual(self.state(mid), "approved")
            self.assertEqual(self.verdict(mid), "company_held")
        self.assertEqual(self.planned(touch_id), [])
        stopped = self.events("touch", "skipped_by_sender", touch_id)
        self.assertEqual([json.loads(e["detail"]) for e in stopped],
                         [{"seq": 1, "due_on": due_on, "reason": "replied"}])
        company = self.db.execute("SELECT held_by_touch, held_at, revision FROM company WHERE company_id = ?",
                                  (self.company,)).fetchone()
        self.assertEqual((company["held_by_touch"], company["held_at"]), (touch_id, iso(self.clock())))
        self.assertEqual(len(self.events("company", "held", self.company)), 1)

    def test_reply_stops_the_repliers_drafted_approved_and_failed_messages(self):
        person, touch_id, sent = self.send_first()
        approved = self.approve_followup(touch_id, "Re: " + self.gmail.sent[-1][0].subject)
        self.gmail.deliver_reply(sent, "p1@acme.co")
        self.sync()
        self.assertEqual(self.state(approved), "skipped")
        detail = json.loads(self.events("message", "skipped_by_sender", approved)[0]["detail"])
        self.assertEqual(detail["reason"], "replied")
        self.assertIsNone(self.verdict(approved))  # out of the queue for good

    def test_reply_with_a_planned_follow_up_does_not_fail_the_tick(self):
        _, touch_id, sent = self.send_first()
        self.assertEqual(len(self.planned(touch_id)), 1)
        self.gmail.deliver_reply(sent, "p1@acme.co")
        self.sync()  # an IntegrityError from the message CHECK would raise here and roll everything back
        other = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(other.close)
        self.assertEqual(other.execute("SELECT state FROM touch WHERE touch_id = ?", (touch_id,)).fetchone()[0],
                         "replied")
        self.assertEqual(other.execute("SELECT COUNT(*) FROM message WHERE touch_id = ? AND state = 'planned'",
                                       (touch_id,)).fetchone()[0], 0)

    def test_reply_outcome_passes_the_current_revision(self):
        _, touch_id, sent = self.send_first()
        self.db.execute("UPDATE outreach_touch SET revision = 3 WHERE email_touch_id = ?", (touch_id,))
        self.gmail.deliver_reply(sent, "p1@acme.co")
        self.sync()
        ot = self.outreach(touch_id)
        self.assertEqual(ot["revision"], 4)
        self.assertIsNotNone(ot["replied_at"])

    def test_reply_at_a_merged_away_company_holds_the_survivor(self):
        survivor = seed_company(self.db, "Survivor")
        duplicate = seed_company(self.db, "Survivor Inc")
        _, touch_id, sent = self.send_first(duplicate)
        self.db.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                        "WHERE company_id = ?", (survivor, duplicate))
        at_survivor = self.colleague(survivor)
        self.gmail.deliver_reply(sent, "p1@acme.co")
        self.sync()
        rows = {r["company_id"]: r["held_by_touch"] for r in
                self.db.execute("SELECT company_id, held_by_touch FROM company WHERE company_id IN (?, ?)",
                                (survivor, duplicate))}
        self.assertEqual(rows, {survivor: touch_id, duplicate: None})
        self.assertEqual(self.verdict(at_survivor), "company_held")

    def test_late_reply_on_a_closed_touch_holds_and_records_the_outcome(self):
        _, touch_id, sent = self.send_first()
        colleague = self.colleague()
        self.db.execute("DELETE FROM message WHERE touch_id = ? AND state = 'planned'", (touch_id,))
        self.db.execute("UPDATE touch SET state = 'closed', close_reason = 'no_reply', closed_at = ? "
                        "WHERE touch_id = ?", (iso(MON + timedelta(days=12)), touch_id))
        self.clock.advance(days=14)
        self.gmail.deliver_reply(sent, "p1@acme.co")
        self.sync()
        self.assertEqual(self.touch(touch_id)["state"], "replied")
        self.assertEqual(self.outreach(touch_id)["replied_at"], iso(self.clock()))
        self.assertEqual(self.verdict(colleague), "company_held")

    def test_reply_after_quiet_clears_quiet(self):
        _, touch_id, sent = self.send_first()
        self.db.execute("UPDATE outreach_touch SET quiet_at = ? WHERE email_touch_id = ?",
                        (iso(MON + timedelta(days=21)), touch_id))
        self.clock.advance(days=25)
        self.gmail.deliver_reply(sent, "p1@acme.co")
        self.sync()
        ot = self.outreach(touch_id)
        self.assertIsNone(ot["quiet_at"])
        self.assertEqual(ot["replied_at"], iso(self.clock()))

    def test_second_reply_writes_inbound_only(self):
        _, touch_id, sent = self.send_first()
        self.gmail.deliver_reply(sent, "p1@acme.co")
        self.sync()
        before = {t: [tuple(r) for r in self.db.execute(f"SELECT * FROM {t} ORDER BY 1")]
                  for t in ("touch", "outreach_touch", "company", "message", "event")}
        self.clock.advance(hours=3)
        second = self.gmail.deliver_reply(sent, "p1@acme.co", snippet="One more thing")
        _, counts = self.sync()
        after = {t: [tuple(r) for r in self.db.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in before}
        self.assertEqual(after, before)
        self.assertEqual([r["gmail_message_id"] for r in self.inbound_rows()][-1], second.gmail_message_id)
        self.assertEqual(len(self.inbound_rows()), 2)

    def test_a_reply_dated_before_the_send_by_gmails_clock_still_records(self):
        _, touch_id, sent = self.send_first()
        meta = self.gmail.deliver_reply(sent, "p1@acme.co")
        early = dataclasses.replace(meta, internal_ms=meta.internal_ms - 60_000)  # Gmail's clock runs behind
        read = inbound.InboundRead(metas=[early], new_history_id=None, complete=False)
        with transaction(self.db):
            inbound.apply_inbound(self.db, read, account=CFG.from_address, now=self.clock(), cfg=CFG,
                                  notify=self.notify)
        self.assertEqual(self.touch(touch_id)["state"], "replied")
        self.assertIsNotNone(self.outreach(touch_id)["replied_at"])

    def test_paused_sender_still_records_replies(self):
        _, touch_id, sent = self.send_first()
        colleague = self.colleague()
        self.db.execute("UPDATE sender SET paused = 1, pause_kind = 'by_you', paused_at = ?", (iso(MON),))
        self.gmail.deliver_reply(sent, "p1@acme.co")
        self.sync()
        self.assertEqual(self.touch(touch_id)["state"], "replied")
        self.assertIn(self.company, held_survivors(company_map(self.db)))
        self.db.execute("UPDATE sender SET paused = 0, pause_kind = NULL, paused_at = NULL")
        self.assertEqual(self.verdict(colleague), "company_held")


class AutoReplyTest(Base):
    def test_auto_reply_is_recorded_and_holds_nothing(self):
        _, touch_id, sent = self.send_first()
        colleague = self.colleague()
        self.clock.advance(minutes=5)
        self.gmail.deliver_auto_reply(sent, "p1@acme.co")
        _, counts = self.sync()
        self.assertEqual(counts["auto_replies"], 1)
        touch = self.touch(touch_id)
        self.assertEqual((touch["state"], touch["auto_reply_at"], touch["replied_at"]),
                         ("open", iso(self.clock()), None))
        self.assertEqual([r["kind"] for r in self.inbound_rows()], ["auto_reply"])
        self.assertIsNone(self.outreach(touch_id)["replied_at"])
        self.assertEqual(len(self.planned(touch_id)), 1)
        self.assertNotIn(self.company, held_survivors(company_map(self.db)))
        self.assertNotEqual(self.verdict(colleague), "company_held")

    def test_an_email_touch_tagged_auto_reply_holds_nothing(self):
        _, touch_id, _ = self.send_first()
        colleague = self.colleague()
        self.db.execute("UPDATE touch SET replied_at = ?, reply_kind = 'auto_reply' WHERE touch_id = ?",
                        (iso(MON), touch_id))
        self.assertNotIn(self.company, held_survivors(company_map(self.db)))
        self.assertNotEqual(self.verdict(colleague), "company_held")


class BounceTest(Base):
    def test_bounce_closes_the_touch_queue_and_counts(self):
        from outreach.sending import signals
        _, touch_id, sent = self.send_first()
        self.clock.advance(minutes=3)
        self.gmail.deliver_bounce(sent, dsn="hard")
        _, counts = self.sync()
        self.assertEqual(counts["bounces"], 1)
        touch = self.touch(touch_id)
        self.assertEqual((touch["state"], touch["bounced_at"]), ("bounced", iso(self.clock())))
        self.assertEqual([(r["kind"], r["dsn_class"]) for r in self.inbound_rows()], [("bounce", "hard")])
        self.assertEqual(self.planned(touch_id), [])
        stopped = self.events("touch", "skipped_by_sender", touch_id)
        self.assertEqual(json.loads(stopped[0]["detail"])["reason"], "bounced")
        self.assertEqual(signals.hard_bounce_rate(self.db, CFG), (1.0, 1))
        self.assertEqual(signals.bounce_rate_3d(self.db, self.clock().date(), CFG), 1.0)
        self.assertEqual(len(self.events("touch", "bounced", touch_id)), 1)

    def test_bounce_leaves_no_follow_up_sendable(self):
        _, touch_id, sent = self.send_first()
        approved = self.approve_followup(touch_id, "Re: " + self.gmail.sent[-1][0].subject)
        self.gmail.deliver_bounce(sent, dsn="hard")
        self.sync()
        self.assertEqual(self.state(approved), "skipped")
        self.assertNotIn(approved, {v.message_id for v in queue.verdicts(self.db, self.clock(), CFG, day=None,
                                                                         gate=None)})
        self.assertEqual(due_followups(self.db, self.clock().date(), days_ahead=30), [])

    def test_unthreaded_bounce_is_matched(self):
        _, touch_id, sent = self.send_first()
        self.sync()  # takes a cursor, so the next read is by history and sees INBOX mail outside the thread
        self.assertTrue(self.sender()["history_id"])
        self.gmail.deliver_bounce(sent, dsn="hard", threaded=False)
        _, counts = self.sync()
        self.assertEqual(counts["bounces"], 1)
        self.assertEqual(self.touch(touch_id)["state"], "bounced")
        self.assertEqual([r["touch_id"] for r in self.inbound_rows()], [touch_id])

    def test_policy_bounce_is_a_warning(self):
        _, touch_id, sent = self.send_first()
        self.gmail.deliver_bounce(sent, dsn="policy")
        self.sync()
        self.assertEqual([r["dsn_class"] for r in self.inbound_rows()], ["policy"])
        self.assertEqual(len(self.events("sender", "policy_rejection")), 1)
        day = self.db.execute("SELECT outcome FROM send_day WHERE day = '2026-11-02'").fetchone()
        self.assertEqual(day["outcome"], "warning")
        self.assertEqual(self.sender()["paused"], 0)  # one bounce in one first email is over 3%, but under 10 sends
        self.assertEqual(self.events("sender", "hard_bounce_rate"), [])

    def test_bounce_pushing_the_rate_to_two_percent_is_one_warning_for_the_day(self):
        for _ in range(97):
            seed_sent(self.db, self.person(), at=MON - timedelta(hours=1))
        sends = [self.send_first() for _ in range(3)]
        self.sync()
        self.gmail.deliver_bounce(sends[0][2])
        self.sync()
        self.assertEqual(self.events("sender", "bounce_warning"), [])  # 1 in 100 is under 2%
        self.gmail.deliver_bounce(sends[1][2])
        self.sync()
        self.assertEqual(len(self.events("sender", "bounce_warning")), 1)  # 2 in 100 is 2%: not clean
        self.gmail.deliver_bounce(sends[2][2])
        self.sync()
        warnings = self.events("sender", "bounce_warning")
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["idempotency_key"], "bounce_warning:2026-11-02")
        self.assertEqual(self.events("sender", "hard_bounce_rate"), [])  # 3 in 100 is not over 3%
        self.assertEqual(self.sender()["paused"], 0)

    def test_hard_bounce_rate_pauses(self):
        for _ in range(9):
            seed_sent(self.db, self.person(), at=MON - timedelta(hours=1))
        _, _, sent = self.send_first()
        self.gmail.deliver_bounce(sent)
        self.sync()
        self.assertEqual(len(self.events("sender", "hard_bounce_rate")), 1)
        sender = self.sender()
        self.assertEqual((sender["paused"], sender["pause_kind"]), (1, "hard_block"))
        self.assertEqual(len(self.notices), 1)

    def test_a_limit_notice_pauses_and_never_marks_the_touch_bounced(self):
        from outreach.sending import signals
        _, touch_id, sent = self.send_first()
        self.gmail.deliver_bounce(sent, dsn="limit")
        self.sync()
        self.assertEqual(self.touch(touch_id)["state"], "open")
        self.assertIsNone(self.touch(touch_id)["bounced_at"])
        self.assertEqual(len(self.planned(touch_id)), 1)
        self.assertEqual(len(self.events("sender", "gmail_limit")), 1)
        sender = self.sender()
        self.assertEqual((sender["paused"], sender["pause_kind"]), (1, "hard_block"))
        self.assertEqual(signals.hard_bounce_rate(self.db, CFG), (0.0, 1))
        self.assertEqual([(r["kind"], r["dsn_class"]) for r in self.inbound_rows()], [("bounce", "limit")])

    def test_an_unmatched_limit_notice_still_pauses(self):
        self.send_first()
        self.sync()
        meta = self.gmail.deliver_unrelated("Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
                                            "Delivery Status Notification (Failure)")
        limit = dataclasses.replace(meta, snippet="You have reached a limit for sending mail.")
        read = inbound.InboundRead(metas=[limit], new_history_id=None, complete=False)
        with transaction(self.db):
            inbound.apply_inbound(self.db, read, account=CFG.from_address, now=self.clock(), cfg=CFG,
                                  notify=self.notify)
        self.assertEqual(len(self.events("sender", "gmail_limit")), 1)
        self.assertEqual(self.sender()["pause_kind"], "hard_block")


class StopTagTest(Base):
    def tag(self, touch_id, at, *, on="touch"):
        if on == "touch":
            self.db.execute("UPDATE touch SET state = 'replied', replied_at = ?, reply_kind = 'asked_to_stop' "
                            "WHERE touch_id = ?", (iso(at), touch_id))
        else:
            self.db.execute("UPDATE outreach_touch SET replied_at = ?, reply_kind = 'asked_to_stop', "
                            "revision = revision + 1 WHERE email_touch_id = ?", (iso(at), touch_id))

    def observe(self):
        with transaction(self.db):
            return inbound.observe_stop_tags(self.db, now=self.clock(), cfg=CFG, notify=self.notify)

    def test_one_stop_tag_is_a_warning_and_two_in_a_week_pause(self):
        _, first, _ = self.send_first()
        _, second, _ = self.send_first()
        self.clock.advance(hours=2)
        self.tag(first, self.clock())
        self.assertEqual(self.observe(), 1)
        self.assertEqual(len(self.events("sender", "stop_reply")), 1)
        self.assertEqual(self.sender()["paused"], 0)
        self.assertEqual(self.db.execute("SELECT outcome FROM send_day WHERE day = '2026-11-02'").fetchone()[0],
                         "warning")
        self.clock.advance(days=3)
        self.tag(second, self.clock(), on="outreach_touch")
        self.assertEqual(self.observe(), 1)
        self.assertEqual(len(self.events("sender", "stop_replies_hard")), 1)
        sender = self.sender()
        self.assertEqual((sender["paused"], sender["pause_kind"]), (1, "hard_block"))

    def test_stop_tag_seen_twice_counts_once(self):
        _, touch_id, _ = self.send_first()
        self.tag(touch_id, self.clock())
        self.tag(touch_id, self.clock(), on="outreach_touch")
        self.assertEqual(self.observe(), 1)
        self.assertEqual(self.observe(), 0)
        self.assertEqual(len(self.events("sender", "stop_reply")), 1)
        self.assertEqual(self.events("sender", "stop_replies_hard"), [])

    def test_two_stop_tags_more_than_a_week_apart_do_not_pause(self):
        _, first, _ = self.send_first()
        _, second, _ = self.send_first()
        self.tag(first, self.clock())
        self.observe()
        self.clock.advance(days=8)
        self.tag(second, self.clock())
        self.observe()
        self.assertEqual(self.events("sender", "stop_replies_hard"), [])


class ReadTest(Base):
    def test_expired_cursor_reads_recent_threads_then_moves_on(self):
        _, old_touch, _ = self.send_first()
        self.clock.advance(days=50)
        _, touch_id, sent = self.send_first()
        self.sync()
        cursor = self.sender()["history_id"]
        self.gmail.deliver_reply(sent, "p2@acme.co")
        self.gmail.expire_history()
        self.gmail.calls.clear()
        read, _ = self.sync()
        self.assertTrue(read.complete)
        self.assertEqual(self.gmail.calls[:2], ["history", "profile"])  # the new cursor is taken before the threads
        self.assertEqual(self.gmail.calls.count("thread"), 1)  # the touch sent 50 days ago is not read again
        self.assertEqual(self.touch(touch_id)["state"], "replied")
        self.assertEqual(self.touch(old_touch)["state"], "open")
        self.assertNotEqual(self.sender()["history_id"], cursor)
        self.gmail.calls.clear()
        self.sync()
        self.assertEqual(self.gmail.calls, ["history"])  # back on the cursor

    def test_empty_cursor_reads_threads_and_takes_the_profile_cursor(self):
        _, touch_id, sent = self.send_first()
        self.gmail.deliver_reply(sent, "p1@acme.co")
        history_before_threads = self.gmail.profile().history_id
        self.gmail.calls.clear()
        read, _ = self.sync()
        self.assertEqual(self.gmail.calls, ["profile", "thread"])
        self.assertEqual(read.new_history_id, history_before_threads)
        self.assertEqual(self.sender()["history_id"], history_before_threads)
        self.assertEqual(self.touch(touch_id)["state"], "replied")

    def test_a_deleted_thread_does_not_stop_the_read(self):
        _, touch_id, sent = self.send_first()
        self.db.execute("UPDATE touch SET gmail_thread_id = 'gone' WHERE touch_id = ?", (touch_id,))
        read, _ = self.sync()
        self.assertTrue(read.complete)
        self.assertTrue(self.sender()["history_id"])

    def test_incomplete_read_keeps_the_cursor(self):
        _, touch_id, sent = self.send_first()
        self.sync()
        cursor = self.sender()["history_id"]
        self.gmail.deliver_reply(sent, "p1@acme.co")
        self.gmail.deliver_unrelated("news@shop.example", "Sale")
        read, _ = self.sync(dataclasses.replace(CFG, inbound_meta_cap=1))
        self.assertFalse(read.complete)
        self.assertEqual(self.sender()["history_id"], cursor)
        self.assertEqual(self.touch(touch_id)["state"], "replied")  # tracked threads are read first
        self.gmail.deliver_reply(sent, "p1@acme.co")
        self.fail_on["message"] = GmailError("server", 503, "backend error")
        read, _ = self.sync()
        self.assertFalse(read.complete)
        self.assertEqual(self.sender()["history_id"], cursor)
        read, _ = self.sync()
        self.assertTrue(read.complete)
        self.assertNotEqual(self.sender()["history_id"], cursor)

    def test_a_failed_history_call_is_an_incomplete_read_not_no_replies(self):
        self.send_first()
        self.sync()
        cursor = self.sender()["history_id"]
        self.fail_on["history"] = GmailError("network", None, "offline")
        read, counts = self.sync()
        self.assertFalse(read.complete)
        self.assertEqual(self.sender()["history_id"], cursor)

    def test_inbox_noise_writes_nothing(self):
        _, touch_id, sent = self.send_first()
        self.sync()
        before = {t: [tuple(r) for r in self.db.execute(f"SELECT * FROM {t} ORDER BY 1")]
                  for t in ("touch", "outreach_touch", "company", "message", "event", "inbound_message")}
        self.gmail.deliver_unrelated("news@shop.example", "Your weekly digest")
        self.gmail.deliver_unrelated("friend@example.org", "Dinner on Friday?")
        self.gmail.add_to_sent("friend@example.org", "Re: Dinner", message_id="<mine.1@gmail.com>")
        self.gmail.deliver_bounce(sent, dsn="delay")
        read, counts = self.sync()
        self.assertTrue(read.complete)
        after = {t: [tuple(r) for r in self.db.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in before}
        self.assertEqual(after, before)
        self.assertEqual(self.touch(touch_id)["state"], "open")

    def test_his_own_message_in_the_thread_is_not_a_reply(self):
        _, touch_id, sent = self.send_first()
        self.gmail.deliver_reply(sent, "Devanshu Chicholikar <devanshurajesh@gmail.com>")
        self.sync()
        self.assertEqual(self.touch(touch_id)["state"], "open")
        self.assertEqual(self.inbound_rows(), [])

    def test_read_inbound_runs_with_no_transaction(self):
        _, _, sent = self.send_first()
        self.gmail.deliver_reply(sent, "p1@acme.co")
        self.sync()
        self.gmail.deliver_reply(sent, "p1@acme.co")
        self.sync()
        self.assertIn("thread", self.gmail.calls)
        self.assertIn("history", self.gmail.calls)
        self.assertIn("message", self.gmail.calls)
        self.assertEqual(self.problems, [])


class VerifyImportedTest(Base):
    def imported(self, *, rfc, gmail_id):
        person = self.person()
        touch = self.db.execute("INSERT INTO touch(person_id, origin, state, first_sent_at, first_rfc_id, "
                                "created_at, updated_at) VALUES (?, 'import', 'open', ?, ?, ?, ?)",
                                (person, iso(MON), rfc, iso(MON), iso(MON))).lastrowid
        return self.db.execute(
            "INSERT INTO message(touch_id, seq, state, subject, body, sent_at, rfc_message_id, gmail_message_id, "
            "saved_by, created_at, updated_at) VALUES (?, 0, 'sent', 'Hi', 'Hello.', ?, ?, ?, 'import', ?, ?)",
            (touch, iso(MON), rfc, gmail_id, iso(MON), iso(MON))).lastrowid

    def verify(self):
        return inbound.verify_imported(self.db, self.gmail, now=self.clock())

    def row(self, mid):
        return tuple(self.db.execute("SELECT * FROM message WHERE message_id = ?", (mid,)).fetchone())

    def test_imported_sent_message_is_verified_by_its_gmail_id(self):
        meta = self.gmail.add_to_sent("p@acme.co", "Hi", message_id="<old.1@gmail.com>")
        mid = self.imported(rfc="<old.1@gmail.com>", gmail_id=meta.gmail_message_id)
        self.assertEqual(self.verify(), {"verified": 1, "not_in_sent": 0, "errors": 0})
        self.assertEqual(len(self.events("message", "import_verified", mid)), 1)
        self.gmail.calls.clear()
        self.assertEqual(self.verify(), {"verified": 0, "not_in_sent": 0, "errors": 0})
        self.assertEqual(self.gmail.calls, [])
        self.assertEqual(self.problems, [])

    def test_imported_message_missing_from_sent_is_reported_and_left_alone(self):
        missing = self.imported(rfc="<old.2@gmail.com>", gmail_id="m404")
        other = self.gmail.add_to_sent("p@acme.co", "Hi", message_id="<someone.else@gmail.com>")
        rewritten = self.imported(rfc="<old.3@gmail.com>", gmail_id=other.gmail_message_id)
        received = self.gmail.deliver_unrelated("p@acme.co", "Hi")  # the same id, but not in Sent
        not_sent = self.imported(rfc=received.headers["message-id"], gmail_id=received.gmail_message_id)
        before = {mid: self.row(mid) for mid in (missing, rewritten, not_sent)}
        self.assertEqual(self.verify(), {"verified": 0, "not_in_sent": 3, "errors": 0})
        for mid in (missing, rewritten, not_sent):
            self.assertEqual(len(self.events("message", "import_not_in_sent", mid)), 1)
            self.assertEqual(self.row(mid), before[mid])

    def test_placeholder_message_id_is_never_looked_up(self):
        self.imported(rfc="<contact.7@outreach.invalid>", gmail_id="m1")
        self.gmail.calls.clear()
        self.assertEqual(self.verify(), {"verified": 0, "not_in_sent": 0, "errors": 0})
        self.assertEqual(self.gmail.calls, [])

    def test_a_failed_lookup_is_tried_again_next_tick(self):
        meta = self.gmail.add_to_sent("p@acme.co", "Hi", message_id="<old.4@gmail.com>")
        mid = self.imported(rfc="<old.4@gmail.com>", gmail_id=meta.gmail_message_id)
        self.fail_on["message"] = GmailError("server", 503, "backend error")
        self.assertEqual(self.verify(), {"verified": 0, "not_in_sent": 0, "errors": 1})
        self.assertEqual(self.events("message", "import_not_in_sent", mid), [])
        self.assertEqual(self.verify()["verified"], 1)


if __name__ == "__main__":
    unittest.main()
