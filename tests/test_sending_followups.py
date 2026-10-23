"""Task 11: follow-up dates, lapses that delete the planned row, quiet closes and the stop sweep.

A `planned` row holds no text, so a follow-up that will never be drafted is deleted with an event on its touch,
never moved to `skipped` (the message CHECK refuses an empty subject or body there).
"""
import dataclasses
import json
import random
import unittest
from datetime import date, datetime

from outreach.sending import claim, queue
from outreach.sending.config import load_config
from outreach.sending.fakegmail import FakeClock, FakeGmail
from outreach.sending.followups import (close_quiet_touches, lapse_followups, plan_followups, stop_planned,
                                        sweep_stopped)
from outreach.store import transaction
from tests.sendtest import MON, UTC, iso, seed_email_person, seed_message, seed_sent
from tests.storetest import fresh_store, seed_company

CFG = load_config()
STAMP = "2026-11-20T15:00:00Z"


class Base(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()
        self.company = seed_company(self.db)
        self.n = 0

    def person(self, company=None):
        self.n += 1
        return seed_email_person(self.db, company or self.company, f"P{self.n}", f"p{self.n}@acme.co")

    def touch_of(self, person):
        return self.db.execute("SELECT touch_id FROM touch WHERE person_id = ?", (person,)).fetchone()[0]

    def touch(self, touch_id):
        return self.db.execute("SELECT * FROM touch WHERE touch_id = ?", (touch_id,)).fetchone()

    def planned(self, touch_id):
        return [tuple(r) for r in self.db.execute(
            "SELECT seq, due_on FROM message WHERE touch_id = ? AND state = 'planned' ORDER BY seq", (touch_id,))]

    def state(self, message_id):
        row = self.db.execute("SELECT state FROM message WHERE message_id = ?", (message_id,)).fetchone()
        return row[0] if row else None

    def touch_events(self, touch_id, kind):
        return [json.loads(r["detail"]) for r in self.db.execute(
            "SELECT detail FROM event WHERE entity = 'touch' AND entity_id = ? AND kind = ? ORDER BY event_id",
            (touch_id, kind))]

    def sent_touch(self, at=MON, company=None):
        """A touch whose first email went out at `at` and that has no follow-up planned yet."""
        person = self.person(company)
        seed_sent(self.db, person, at=at)
        return person, self.touch_of(person)

    def send_at(self, at: datetime):
        """A first email sent through the sender at `at`; returns its touch's planned follow-ups."""
        clock = FakeClock(at)
        gmail = FakeGmail(clock=clock, reconcile_after_minutes=CFG.reconcile_after_minutes)
        person = self.person()
        mid = seed_message(self.db, person, subject="Hello")
        q = next(q for q in queue.load_queue(self.db, CFG) if q.message_id == mid)
        claim.send_one(self.db, gmail, q, CFG, now=iso(at), clock=clock, rng=random.Random(1),
                       account=CFG.from_address)
        return self.planned(self.touch_of(person))


class PlanTest(Base):
    def test_followup_due_seven_business_days_later(self):
        self.assertEqual(self.send_at(MON), [(1, "2026-11-11")])

    def test_friday_send_follow_up_lands_seven_business_days_later(self):
        self.assertEqual(self.send_at(datetime(2026, 11, 6, 14, 0, tzinfo=UTC)), [(1, "2026-11-17")])

    def test_evening_utc_send_uses_the_new_york_date(self):
        # 02:00 UTC on Tuesday is 21:00 on Monday in New York.
        self.assertEqual(self.send_at(datetime(2026, 11, 3, 2, 0, tzinfo=UTC)), [(1, "2026-11-11")])

    def test_three_configured_follow_ups_make_seq_1_to_3(self):
        cfg = dataclasses.replace(CFG, followup_business_days=(3, 7, 15))
        _, touch_id = self.sent_touch()
        with transaction(self.db):
            self.assertEqual(plan_followups(self.db, touch_id, date(2026, 11, 2), cfg, now=iso(MON)), 3)
        self.assertEqual(self.planned(touch_id), [(1, "2026-11-05"), (2, "2026-11-11"), (3, "2026-11-23")])

    def test_planning_twice_adds_nothing(self):
        _, touch_id = self.sent_touch()
        with transaction(self.db):
            self.assertEqual(plan_followups(self.db, touch_id, date(2026, 11, 2), CFG, now=iso(MON)), 1)
            self.assertEqual(plan_followups(self.db, touch_id, date(2026, 11, 2), CFG, now=iso(MON)), 0)
        self.assertEqual(self.planned(touch_id), [(1, "2026-11-11")])
        planned_events = self.db.execute("SELECT COUNT(*) FROM event WHERE kind = 'followup_planned'").fetchone()[0]
        self.assertEqual(planned_events, 1)


class LapseTest(Base):
    def planned_touch(self):
        _, touch_id = self.sent_touch()
        with transaction(self.db):
            plan_followups(self.db, touch_id, date(2026, 11, 2), CFG, now=iso(MON))  # due Wednesday 2026-11-11
        return touch_id

    def lapse(self, today):
        with transaction(self.db):
            return lapse_followups(self.db, today, CFG, now=STAMP)

    def test_undrafted_follow_up_lapses_after_five_business_days(self):
        touch_id = self.planned_touch()
        self.assertEqual(self.lapse(date(2026, 11, 17)), 0)  # four business days after it was due
        self.assertEqual(self.planned(touch_id), [(1, "2026-11-11")])
        self.assertEqual(self.lapse(date(2026, 11, 18)), 1)  # five
        self.assertEqual(self.planned(touch_id), [])

    def test_a_weekend_does_not_count_toward_the_lapse(self):
        touch_id = self.planned_touch()
        self.assertEqual(self.lapse(date(2026, 11, 15)), 0)  # Sunday: two business days after
        self.assertEqual(self.planned(touch_id), [(1, "2026-11-11")])

    def test_lapse_deletes_the_planned_row_and_records_it_on_the_touch(self):
        touch_id = self.planned_touch()
        self.lapse(date(2026, 11, 20))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM message WHERE touch_id = ? AND seq = 1",
                                         (touch_id,)).fetchone()[0], 0)
        self.assertEqual(self.touch_events(touch_id, "followup_lapsed"),
                         [{"seq": 1, "due_on": "2026-11-11", "reason": "lapsed"}])
        self.assertEqual(self.lapse(date(2026, 11, 23)), 0)
        self.assertEqual(len(self.touch_events(touch_id, "followup_lapsed")), 1)
        self.assertEqual(self.touch_events(touch_id, "skipped_by_sender"), [])

    def test_a_drafted_follow_up_never_lapses(self):
        person, touch_id = self.sent_touch()
        drafted = seed_message(self.db, person, state="drafted", seq=1, due_on="2026-11-11")
        self.assertEqual(self.lapse(date(2026, 11, 30)), 0)
        self.assertEqual(self.state(drafted), "drafted")

    def test_stop_planned_refuses_a_drafted_row(self):
        person, _ = self.sent_touch()
        drafted = seed_message(self.db, person, state="drafted", seq=1, due_on="2026-11-11")
        with self.assertRaises(ValueError):
            stop_planned(self.db, drafted, reason="lapsed", now=STAMP)
        self.assertEqual(self.state(drafted), "drafted")

    def test_a_re_plan_after_a_lapse_is_a_new_planned_row(self):
        touch_id = self.planned_touch()
        self.lapse(date(2026, 11, 20))
        with transaction(self.db):
            self.assertEqual(plan_followups(self.db, touch_id, date(2026, 11, 20), CFG, now=STAMP), 1)
        row = self.db.execute("SELECT message_id, state, due_on FROM message WHERE touch_id = ? AND seq = 1",
                              (touch_id,)).fetchone()
        self.assertEqual((row["state"], row["due_on"]), ("planned", "2026-12-01"))
        self.assertEqual(self.touch_events(touch_id, "followup_lapsed"),
                         [{"seq": 1, "due_on": "2026-11-11", "reason": "lapsed"}])  # the lapse stays on record
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM event WHERE kind = 'followup_planned'").fetchone()[0],
                         2)


class CloseTest(Base):
    def close(self, today):
        with transaction(self.db):
            return close_quiet_touches(self.db, today, CFG, now=STAMP)

    def test_touch_closes_seven_business_days_after_the_last_send(self):
        _, quiet = self.sent_touch()
        self.assertEqual(self.close(date(2026, 11, 10)), 0)  # six business days after Monday 2026-11-02
        self.assertEqual(self.touch(quiet)["state"], "open")
        self.assertEqual(self.close(date(2026, 11, 11)), 1)  # seven
        touch = self.touch(quiet)
        self.assertEqual((touch["state"], touch["close_reason"], touch["closed_at"]), ("closed", "no_reply", STAMP))
        self.assertEqual(len(self.touch_events(quiet, "closed")), 1)
        self.assertEqual(self.close(date(2026, 11, 12)), 0)

    def test_the_last_send_counts_not_the_first(self):
        person, touch_id = self.sent_touch()
        seed_sent(self.db, person, at="2026-11-11T15:00:00Z", seq=1)
        self.assertEqual(self.close(date(2026, 11, 19)), 0)  # six business days after the follow-up
        self.assertEqual(self.close(date(2026, 11, 20)), 1)

    def test_no_close_with_a_follow_up_waiting(self):
        for state in ("planned", "drafted", "approved"):
            with self.subTest(state=state):
                person, touch_id = self.sent_touch()
                seed_message(self.db, person, state=state, seq=1, due_on="2026-11-11")
                self.assertEqual(self.close(date(2026, 11, 30)), 0)
                self.assertEqual(self.touch(touch_id)["state"], "open")
                self.db.execute("DELETE FROM message WHERE touch_id = ? AND seq = 1", (touch_id,))
                self.close(date(2026, 11, 30))  # leave nothing open for the next case

    def test_a_replied_or_bounced_touch_is_not_closed(self):
        _, replied = self.sent_touch()
        self.db.execute("UPDATE touch SET state = 'replied', replied_at = ?, reply_kind = 'unsorted' "
                        "WHERE touch_id = ?", (iso(MON), replied))
        self.assertEqual(self.close(date(2026, 11, 30)), 0)
        self.assertEqual(self.touch(replied)["state"], "replied")


class SweepTest(Base):
    def verdicts(self):
        return queue.verdicts(self.db, MON, CFG, day=None, gate=None)

    def sweep(self):
        verdicts = self.verdicts()
        with transaction(self.db):
            return sweep_stopped(self.db, verdicts, now=STAMP)

    def queued_followup(self, company=None):
        """A touch whose first email went out, with an approved follow-up and a later one still planned."""
        person, touch_id = self.sent_touch(company=company)
        approved = seed_message(self.db, person, state="approved", seq=1, due_on="2026-11-02")
        planned = seed_message(self.db, person, state="planned", seq=2, due_on="2026-11-20")
        return touch_id, approved, planned

    def test_sweep_skips_messages_at_a_closed_company_and_closes_the_touch(self):
        touch_id, approved, planned = self.queued_followup()
        self.db.execute("UPDATE company SET status = 'closed', close_reason = 'no_referrals' WHERE company_id = ?",
                        (self.company,))
        self.assertEqual({v.message_id: v.reason for v in self.verdicts()}, {approved: "company_closed"})
        self.assertEqual(self.sweep(), 2)
        self.assertEqual(self.state(approved), "skipped")
        self.assertIsNone(self.state(planned))  # deleted, never moved to skipped
        self.assertEqual(self.touch_events(touch_id, "skipped_by_sender"),
                         [{"seq": 2, "due_on": "2026-11-20", "reason": "company_closed"}])
        message_event = self.db.execute("SELECT detail FROM event WHERE entity = 'message' AND entity_id = ? "
                                        "AND kind = 'skipped_by_sender'", (approved,)).fetchone()
        self.assertEqual(json.loads(message_event[0])["reason"], "company_closed")
        touch = self.touch(touch_id)
        self.assertEqual((touch["state"], touch["close_reason"], touch["closed_at"]),
                         ("closed", "company_closed", STAMP))
        self.assertEqual(self.sweep(), 0)

    def test_sweep_on_a_no_go_person(self):
        person = self.person()
        first = seed_message(self.db, person)
        colleague = seed_message(self.db, self.person())
        self.db.execute("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'said_no', 'reply', ?)",
                        (person, iso(MON)))
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(self.state(first), "skipped")
        self.assertEqual(self.state(colleague), "approved")
        touch = self.touch(self.touch_of(person))
        self.assertEqual((touch["state"], touch["close_reason"]), ("closed", "no_go"))

    def test_sweep_after_a_reply_or_bounce_keeps_the_touch_state(self):
        replied, approved_r, planned_r = self.queued_followup(seed_company(self.db, "Replied Co"))
        self.db.execute("UPDATE touch SET state = 'replied', replied_at = ?, reply_kind = 'unsorted' "
                        "WHERE touch_id = ?", (iso(MON), replied))
        bounced, approved_b, planned_b = self.queued_followup(seed_company(self.db, "Bounced Co"))
        self.db.execute("UPDATE touch SET state = 'bounced', bounced_at = ? WHERE touch_id = ?", (iso(MON), bounced))
        reasons = {v.message_id: v.reason for v in self.verdicts()}
        self.assertEqual((reasons[approved_r], reasons[approved_b]), ("reply_stop", "touch_done"))
        self.sweep()
        self.assertEqual([self.state(m) for m in (approved_r, planned_r, approved_b, planned_b)],
                         ["skipped", None, "skipped", None])
        self.assertEqual((self.touch(replied)["state"], self.touch(bounced)["state"]), ("replied", "bounced"))

    def test_sweep_leaves_held_and_paused_alone(self):
        held_co = seed_company(self.db, "Held")
        paused_co = seed_company(self.db, "Paused")
        held_touch, held_msg, held_planned = self.queued_followup(held_co)
        _, paused_msg, paused_planned = self.queued_followup(paused_co)
        self.db.execute("UPDATE company SET held_by_touch = ?, held_at = ? WHERE company_id = ?",
                        (held_touch, iso(MON), held_co))
        self.db.execute("UPDATE company SET status = 'working', paused_at = ? WHERE company_id = ?",
                        (iso(MON), paused_co))
        reasons = {v.message_id: v.reason for v in self.verdicts()}
        self.assertEqual((reasons[held_msg], reasons[paused_msg]), ("company_held", "company_paused"))
        before = [tuple(r) for r in self.db.execute("SELECT * FROM message ORDER BY message_id")]
        self.assertEqual(self.sweep(), 0)
        self.assertEqual([tuple(r) for r in self.db.execute("SELECT * FROM message ORDER BY message_id")], before)
        self.assertEqual(self.touch(held_touch)["state"], "open")


if __name__ == "__main__":
    unittest.main()
