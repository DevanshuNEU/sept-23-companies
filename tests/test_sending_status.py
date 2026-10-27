"""Task 16: the snapshot and the queue schedule the API and the daily plan read.

Read only: every test that reads also checks that nothing was written. A store has no marker_verified event by
default here, so only the first email may go and every other one waits (`message_id_unproven`); each test that
wants the whole queue's time-based waits seeds a proof first (`Base.prove`).
"""
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from outreach.sending import proof, queue, ramp
from outreach.sending.config import load_config
from outreach.sending.status import SendingSnapshot, queue_schedule, sending_snapshot
from outreach.store.writes import append_event
from tests.sendtest import MON, UTC, bounce, iso, put_day, seed_email_person, seed_message, seed_sent
from tests.storetest import fresh_store, seed_company

CFG = load_config()
NY = ZoneInfo("America/New_York")
MON_DAY = date(2026, 11, 2)


def ny(day: int, hour: int, minute: int = 0) -> datetime:
    """A New York wall-clock moment on 2026-11-`day`, as aware UTC."""
    return datetime(2026, 11, day, hour, minute, tzinfo=NY).astimezone(UTC)


class Base(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()
        self.company = seed_company(self.db)
        self.n = 0
        self.keys = 0

    def prove(self) -> None:
        """What `proof.verify_marker` leaves once Gmail has been shown to keep the sender's marker."""
        append_event(self.db, entity="message", entity_id=1, kind="marker_verified", actor="sender",
                     at="2026-11-01T12:00:00Z", idempotency_key="proof")

    def first_claimed(self, kind: str | None = None) -> int:
        """The one first marked email the sender claimed and sent before any proof, with `kind`
        (`marker_verified` or `marker_missing`) when it was read back from Gmail."""
        message = seed_sent(self.db, self.person(), at=MON - timedelta(hours=1))
        append_event(self.db, entity="message", entity_id=message, kind="claimed", actor="sender",
                     at=iso(MON - timedelta(hours=1)), idempotency_key=f"claimed:{message}",
                     detail={"pending_rfc_id": f"<c{message}@gmail.com>", "marker": f"<c{message}@gmail.com>"})
        if kind is not None:
            append_event(self.db, entity="message", entity_id=message, kind=kind, actor="sender", at=iso(MON),
                         idempotency_key=f"{kind}:{message}")
        return message

    def person(self, *, company=None, role="engineer", tz="America/New_York") -> int:
        self.n += 1
        return seed_email_person(self.db, company or self.company, f"P{self.n}", f"p{self.n}@acme.co",
                                 role_kind=role, tz=tz)

    def approve(self, *, company=None, tz="America/New_York", approved_at=None) -> int:
        return seed_message(self.db, self.person(company=company, tz=tz), approved_at=approved_at)

    def sender(self, **columns) -> None:
        sets = ", ".join(f"{k} = ?" for k in columns)
        self.db.execute(f"UPDATE sender SET {sets} WHERE sender_id = 1", tuple(columns.values()))

    def signal(self, kind: str, at: datetime, *, detail=None) -> None:
        self.keys += 1
        ramp.note_signal(self.db, kind, at=iso(at), detail=detail or {}, key=f"{kind}:{self.keys}", cfg=CFG,
                         notify=lambda title, text: None, now=at)

    def snap(self, now=MON, **kw) -> SendingSnapshot:
        before = self.db.total_changes
        out = sending_snapshot(self.db, now, CFG, **kw)
        self.assertEqual(self.db.total_changes, before, "the snapshot wrote to the store")
        self.assertFalse(self.db.in_transaction)
        return out

    def schedule(self, now=MON, **kw) -> dict:
        before = self.db.total_changes
        out = queue_schedule(self.db, now, CFG, **kw)
        self.assertEqual(self.db.total_changes, before, "the schedule wrote to the store")
        return out


class EmptyStoreTest(Base):
    def test_snapshot_of_an_empty_store(self):
        s = self.snap()
        self.assertEqual(s.state, "running")
        self.assertEqual((s.paused_reason, s.paused_at, s.gmail_message), ("", None, None))
        self.assertEqual((s.sent_today, s.limit_today, s.start_limit), (0, 20, 20))
        self.assertEqual((s.ramp_level, s.next_limit_if_clean), (1, 30))
        self.assertEqual(s.ramp_levels, (20, 30, 40, 50, 60, 70, 80, 90, 100))
        self.assertIsNone(s.last_step_down)
        self.assertEqual(s.bounce_rate_3d, 0.0)
        self.assertEqual((s.gmail_errors_today, s.stop_replies_today, s.imported_unverified), (0, 0, 0))
        self.assertIsNone(s.next_send)
        self.assertEqual(s.none_reason, "Nothing is approved and waiting to send.")
        self.assertEqual(s.revision, 1)

    def test_the_state_follows_the_clock(self):
        self.assertEqual(self.snap(ny(2, 9)).state, "running")
        self.assertEqual(self.snap(ny(2, 6)).state, "outside_window")
        self.assertEqual(self.snap(ny(2, 17)).state, "outside_window")
        self.assertEqual(self.snap(ny(7, 10)).state, "weekend")

    def test_a_day_the_sender_does_not_run_has_a_limit_of_zero(self):
        s = self.snap(ny(7, 10))
        self.assertEqual((s.state, s.limit_today, s.sent_today), ("weekend", 0, 0))

    def test_snapshot_states_per_pause_kind_sign_in_and_clock(self):
        self.sender(paused=1, pause_kind="by_you", paused_reason="going on holiday", paused_at="2026-11-02T13:00:00Z")
        s = self.snap()
        self.assertEqual((s.state, s.paused_reason, s.paused_at),
                         ("paused_by_you", "going on holiday", "2026-11-02T13:00:00Z"))
        self.sender(pause_kind="hard_block", paused_reason="Sending is paused because Gmail said the limit")
        s = self.snap()
        self.assertEqual((s.state, s.paused_reason),
                         ("paused_hard_block", "Sending is paused because Gmail said the limit"))
        self.assertEqual(self.snap(ny(7, 10)).state, "paused_hard_block", "a pause beats the clock")
        self.sender(paused=0, pause_kind=None, paused_reason="", paused_at=None)
        self.sender(auth_state="signed_out", auth_error="invalid_grant")
        s = self.snap()
        self.assertEqual((s.state, s.paused_reason), ("signed_out", "invalid_grant"))
        self.sender(auth_state="wrong_account", auth_error="the token signs in as other@gmail.com, not me@gmail.com")
        s = self.snap()
        self.assertEqual(s.state, "signed_out")
        self.assertIn("other@gmail.com", s.paused_reason)
        self.sender(auth_state="ok", auth_error="")
        self.assertEqual(self.snap().state, "running")

    def test_a_loaded_old_sender_shows_as_paused_by_you_with_a_reason(self):
        s = self.snap(old_sender_loaded=True)
        self.assertEqual(s.state, "paused_by_you")
        self.assertIn("old mail sender", s.paused_reason)
        self.assertIsNone(s.paused_at)
        self.assertEqual(self.snap(old_sender_loaded=False).state, "running")


class RampTest(Base):
    def test_ramp_level_and_next_limit(self):
        put_day(self.db, MON_DAY, 40)
        s = self.snap()
        self.assertEqual((s.start_limit, s.ramp_level, s.next_limit_if_clean), (40, 3, 50))
        self.db.execute("UPDATE send_day SET send_limit = 100 WHERE day = ?", (MON_DAY.isoformat(),))
        s = self.snap()
        self.assertEqual((s.start_limit, s.ramp_level, s.next_limit_if_clean), (100, 9, 100))
        self.assertEqual(s.ramp_levels[s.ramp_level - 1], 100)

    def test_snapshot_limit_matches_the_ramp_before_the_first_tick(self):
        put_day(self.db, "2026-10-30", 40, outcome="clean", closed=True)  # Friday: today has no row yet
        s = self.snap()
        self.assertEqual((s.start_limit, s.limit_today), (50, 50))
        self.assertEqual(s.limit_today, ramp.effective_limit(self.db, MON_DAY, CFG))
        self.assertEqual(s.start_limit, ramp.limit_for(self.db, MON_DAY, CFG))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM send_day WHERE day = ?",
                                         (MON_DAY.isoformat(),)).fetchone()[0], 0)

    def test_a_warning_lowers_the_limit_today_and_is_the_last_step_down(self):
        put_day(self.db, MON_DAY, 50)
        self.signal("policy_rejection", ny(2, 11), detail={})
        s = self.snap(ny(2, 12))
        self.assertEqual((s.start_limit, s.limit_today), (50, 40))
        self.assertEqual(s.last_step_down, ("2026-11-02", "policy_rejection"))

    def test_the_newest_step_down_wins_and_an_outcome_note_is_used_when_there_is_one(self):
        put_day(self.db, "2026-10-29", 50, outcome="hard_block", closed=True)
        put_day(self.db, "2026-10-30", 40, outcome="warning", closed=True)
        self.db.execute("UPDATE send_day SET outcome_note = 'bounce rate' WHERE day = '2026-10-30'")
        put_day(self.db, "2026-10-28", 30, outcome="clean", closed=True)
        s = self.snap()
        self.assertEqual(s.last_step_down, ("2026-10-30", "bounce rate"))

    def test_the_sent_count_is_todays_new_york_date(self):
        company = seed_company(self.db, "Other")
        seed_sent(self.db, self.person(company=company), at=ny(2, 8, 45))
        seed_sent(self.db, self.person(company=company), at=ny(1, 15))
        self.assertEqual(self.snap().sent_today, 1)


class SignalsTest(Base):
    def test_gmail_errors_and_the_gmail_message_come_from_todays_signals(self):
        put_day(self.db, MON_DAY, 40)
        self.signal("gmail_limit", ny(2, 10), detail={"error_kind": "rate_limit", "status": 429, "source": "send"})
        s = self.snap(ny(2, 11))
        self.assertEqual(s.gmail_errors_today, 1)
        self.assertIn("limit", s.gmail_message)
        self.assertIn("429", s.gmail_message)
        self.assertEqual(self.snap(ny(3, 11)).gmail_errors_today, 0)
        self.assertIsNone(self.snap(ny(3, 11)).gmail_message)

    def test_the_gmail_message_is_the_newest_limit_or_policy_event_today(self):
        put_day(self.db, MON_DAY, 40)
        self.signal("gmail_limit", ny(2, 9), detail={"error_kind": "daily_limit", "status": 429})
        self.signal("gmail_policy", ny(2, 10), detail={"error_kind": "account_blocked", "status": 403})
        s = self.snap(ny(2, 11))
        self.assertEqual(s.gmail_errors_today, 2)
        self.assertIn("may not send", s.gmail_message)

    def test_stop_replies_today_counts_asked_to_stop_tags_dated_today(self):
        for day, kind in ((2, "asked_to_stop"), (2, "asked_to_stop"), (2, "said_no"), (1, "asked_to_stop")):
            person = self.person()
            seed_sent(self.db, person, at=ny(day, 8, 30) - timedelta(days=7))
            self.db.execute("UPDATE touch SET state = 'replied', replied_at = ?, reply_kind = ? WHERE person_id = ?",
                            (iso(ny(day, 12)), kind, person))
        self.assertEqual(self.snap(ny(2, 15)).stop_replies_today, 2)

    def test_imported_unverified_counts_the_not_in_sent_events(self):
        for i in range(2):
            append_event(self.db, entity="message", entity_id=10 + i, kind="import_not_in_sent", actor="sender",
                         at="2026-11-02T12:00:00Z", idempotency_key=f"nis:{i}")
        self.assertEqual(self.snap().imported_unverified, 2)

    def test_bounce_rate_3d_is_the_rate_the_ramp_judges_by(self):
        company = seed_company(self.db, "Other")
        touches = []
        for i in range(50):
            person = self.person(company=company)
            seed_sent(self.db, person, at=ny(2, 8, 30))
            touches.append(self.db.execute("SELECT touch_id FROM touch WHERE person_id = ?", (person,)).fetchone()[0])
        bounce(self.db, touches[0], ny(2, 10))
        self.assertAlmostEqual(self.snap(ny(2, 12)).bounce_rate_3d, 0.02)


class NextSendTest(Base):
    def test_the_next_send_is_the_earliest_message_and_its_zone(self):
        self.prove()
        first = self.approve(approved_at="2026-11-02T13:00:00Z")
        later_zone = self.approve(tz="America/Los_Angeles", approved_at="2026-11-02T13:30:00Z")
        s = self.snap(ny(2, 9))
        self.assertEqual(s.next_send, (first, iso(ny(2, 9)), "America/New_York"))
        self.assertIsNone(s.none_reason)
        # While the first waits out the spacing, the Pacific recipient is still outside their hours.
        self.sender(next_send_at=iso(ny(2, 9, 6)))
        self.assertEqual(self.snap(ny(2, 9)).next_send, (first, iso(ny(2, 9, 6)), "America/New_York"))
        self.assertNotEqual(later_zone, first)

    def test_a_wait_with_no_time_says_why_nothing_is_next(self):
        self.prove()
        self.approve()
        self.sender(paused=1, pause_kind="by_you", paused_reason="", paused_at="2026-11-02T13:00:00Z")
        s = self.snap()
        self.assertIsNone(s.next_send)
        self.assertEqual(s.none_reason, queue.REASON_TEXT["sender_paused"])

    def test_while_gmail_is_unproven_the_first_email_is_next_and_goes_alone(self):
        first = self.approve()
        second = self.approve(tz="America/Chicago")
        self.assertEqual(self.snap().next_send, (first, iso(MON), "America/New_York"))
        self.assertEqual(self.schedule()[second].reason, "message_id_unproven")

    def test_nothing_is_next_while_the_first_email_is_checked(self):
        self.first_claimed()
        self.approve()
        s = self.snap()
        self.assertIsNone(s.next_send)
        self.assertEqual(s.none_reason, "First email sent, checking it in Sent. The rest follow.")

    def test_a_first_email_gmail_kept_without_its_marker_stops_sending_for_good(self):
        self.first_claimed("marker_missing")
        self.approve()
        self.prove()  # even a later proof does not start it again
        s = self.snap()
        self.assertIsNone(s.next_send)
        self.assertEqual(s.none_reason, proof.PHASE_TEXT[proof.MISSING])
        self.assertIn("Gmail did not keep the sender's marker", s.none_reason)

    def test_the_old_message_id_events_neither_prove_nor_stop(self):
        """The live store before the marker: a sent email claimed without one, read back under another Message-ID
        (`send_id_mismatch`), and older `import_verified` and `send_verified` events. None of them is about the
        marker, so the first marked email goes alone."""
        old = seed_sent(self.db, self.person(), at=MON - timedelta(hours=1))
        append_event(self.db, entity="message", entity_id=old, kind="claimed", actor="sender",
                     at=iso(MON - timedelta(hours=1)), idempotency_key=f"claimed:{old}",
                     detail={"pending_rfc_id": "<old@gmail.com>", "attempt": 1, "seq": 0})
        for kind in ("send_id_mismatch", "import_verified", "send_verified"):
            append_event(self.db, entity="message", entity_id=old, kind=kind, actor="sender", at=iso(MON),
                         idempotency_key=f"{kind}:{old}")
        first, second = self.approve(), self.approve()
        self.assertEqual(self.snap().next_send, (first, iso(MON), "America/New_York"))
        self.assertEqual({m: v.reason for m, v in self.schedule().items()},
                         {first: "ready", second: "message_id_unproven"})

    def test_a_verified_first_email_opens_the_queue(self):
        self.first_claimed("marker_verified")
        first, second = self.approve(), self.approve()
        self.assertEqual({m: v.reason for m, v in self.schedule().items()}, {first: "ready", second: "ready"})


class ScheduleTest(Base):
    def only(self, message: int, now=MON, **kw):
        return self.schedule(now, **kw)[message]

    def test_schedule_is_the_verdicts_the_tick_uses(self):
        self.prove()
        self.approve()
        self.approve(tz="America/Los_Angeles")
        day = ramp.open_day(self.db, MON, CFG)
        theirs = {v.message_id: v for v in queue.verdicts(self.db, MON, CFG, day=day, gate=None)}
        self.assertEqual(self.schedule(), theirs)

    def test_schedule_reason_and_time_for_each_wait(self):
        self.prove()
        # daily_limit: today's limit is used up (earliest: the next sending day's window start).
        put_day(self.db, MON_DAY, 1)
        sent_company = seed_company(self.db, "Sent")
        seed_sent(self.db, self.person(company=sent_company), at=ny(2, 8))
        waiting = self.approve()
        verdict = self.only(waiting)
        self.assertEqual((verdict.reason, verdict.earliest_at), ("daily_limit", ny(3, 8, 30)))

    def test_not_business_day(self):
        self.prove()
        message = self.approve()
        verdict = self.only(message, ny(7, 11))
        self.assertEqual((verdict.reason, verdict.earliest_at), ("not_business_day", ny(9, 8, 30)))

    def test_spacing(self):
        self.prove()
        message = self.approve()
        self.sender(next_send_at=iso(ny(2, 9, 6)))
        verdict = self.only(message, ny(2, 9))
        self.assertEqual((verdict.reason, verdict.earliest_at), ("spacing", ny(2, 9, 6)))

    def test_window(self):
        self.prove()
        message = self.approve(tz="America/Los_Angeles")
        verdict = self.only(message, ny(2, 9))  # 06:00 in Los Angeles
        self.assertEqual((verdict.reason, verdict.earliest_at), ("window", ny(2, 11, 30)))

    def test_followup_not_due(self):
        self.prove()
        person = self.person()
        seed_sent(self.db, person, at=ny(2, 8, 30) - timedelta(days=7))
        message = seed_message(self.db, person, seq=1, due_on="2026-11-04")
        verdict = self.only(message, ny(2, 9))
        self.assertEqual((verdict.reason, verdict.earliest_at), ("followup_not_due", ny(4, 8, 30)))

    def test_per_company_limit(self):
        self.prove()
        company = seed_company(self.db, "Busy")
        for i in range(3):
            seed_sent(self.db, self.person(company=company), at=ny(2, 8, 30) + timedelta(hours=2 * i))
        message = self.approve(company=company)
        verdict = self.only(message, ny(2, 15))
        self.assertEqual((verdict.reason, verdict.earliest_at), ("per_company_limit", ny(3, 8, 30)))

    def test_per_company_gap(self):
        self.prove()
        company = seed_company(self.db, "Gap")
        seed_sent(self.db, self.person(company=company), at=ny(2, 9))
        message = self.approve(company=company)
        verdict = self.only(message, ny(2, 9, 20))
        self.assertEqual((verdict.reason, verdict.earliest_at), ("per_company_gap", ny(2, 10)))

    def test_a_gate_reason_has_no_time(self):
        self.prove()
        message = self.approve()
        self.sender(paused=1, pause_kind="hard_block", paused_reason="x", paused_at="2026-11-02T13:00:00Z")
        verdict = self.only(message)
        self.assertEqual((verdict.reason, verdict.earliest_at), ("sender_paused", None))

    def test_signed_out_old_sender_and_unproven_gates(self):
        message = self.approve()
        self.assertEqual(self.only(message).reason, "ready")  # the one first email may go before the proof
        self.first_claimed()
        self.assertEqual(self.only(message).reason, "message_id_unproven")
        self.prove()
        self.assertEqual(self.only(message).reason, "ready")
        self.assertEqual(self.only(message, old_sender_loaded=True).reason, "old_sender_running")
        self.sender(auth_state="signed_out")
        self.assertEqual(self.only(message, old_sender_loaded=True).reason, "signed_out")
        self.sender(auth_state="wrong_account")
        self.assertEqual(self.only(message).reason, "signed_out")

    def test_snapshot_never_writes(self):
        self.prove()
        put_day(self.db, MON_DAY, 30)
        self.approve()
        self.approve(tz="America/Los_Angeles")
        self.signal("gmail_limit", MON, detail={"error_kind": "rate_limit"})
        before = self.db.total_changes
        sending_snapshot(self.db, MON, CFG)
        sending_snapshot(self.db, ny(7, 10), CFG, old_sender_loaded=True)
        queue_schedule(self.db, MON, CFG)
        self.assertEqual(self.db.total_changes, before)
        self.assertFalse(self.db.in_transaction)


if __name__ == "__main__":
    unittest.main()
