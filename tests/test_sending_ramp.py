import json
import unittest
from datetime import date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from outreach.sending import notify, ramp, signals
from outreach.sending.config import load_config
from outreach.store.writes import log_touch
from tests.sendtest import MON, UTC, bounce, iso, put_day, seed_email_person, seed_sent
from tests.storetest import fresh_store, seed_company

CFG = load_config()
NY = ZoneInfo("America/New_York")
BASE = date(2026, 11, 2)  # a Monday
STAMP = "2026-11-02T15:00:00Z"


def d(n: int) -> date:
    """The date n days after Monday 2026-11-02."""
    return BASE + timedelta(days=n)


def ny(n: int, hour: int = 9, minute: int = 0) -> datetime:
    """A New York wall-clock moment on day n (0 is Monday 2026-11-02), as aware UTC."""
    day = d(n)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=NY).astimezone(UTC)


class Base(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()
        self.company = seed_company(self.db)
        self.people = 0
        self.notices = []
        self.keys = 0

    def notifier(self, title, text):
        self.notices.append((title, text))

    def send(self, at, *, tz="America/New_York"):
        """One first email sent at `at` to a new person. Returns the touch id."""
        self.people += 1
        person = seed_email_person(self.db, self.company, f"P{self.people}", f"p{self.people}@acme.co", tz=tz)
        seed_sent(self.db, person, at=at)
        return self.db.execute("SELECT touch_id FROM touch WHERE person_id = ?", (person,)).fetchone()[0]

    def sends(self, at, n):
        return [self.send(at) for _ in range(n)]

    def note(self, kind, at, *, now=None, key=None):
        self.keys += 1
        ramp.note_signal(self.db, kind, at=iso(at), detail={}, key=key or f"{kind}:{self.keys}", cfg=CFG,
                         notify=self.notifier, now=now or at)

    def row(self, day):
        return self.db.execute("SELECT * FROM send_day WHERE day = ?", (day.isoformat(),)).fetchone()

    def sender(self):
        return self.db.execute("SELECT * FROM sender WHERE sender_id = 1").fetchone()

    def events(self, kind):
        return self.db.execute("SELECT * FROM event WHERE entity = 'sender' AND kind = ? ORDER BY event_id",
                               (kind,)).fetchall()


class RampTest(Base):
    def test_first_day_starts_at_20(self):
        state = ramp.open_day(self.db, MON, CFG)
        self.assertEqual(state, ramp.DayState(day=d(0), start_limit=20, effective_limit=20, sent=0, outcome=None,
                                              closed=False))
        self.assertEqual(self.row(d(0))["send_limit"], 20)

    def test_clean_days_rise_by_10_to_100_and_stop(self):
        starts = []
        for n in (0, 1, 2, 3, 4, 7, 8, 9, 10, 11):
            starts.append(ramp.open_day(self.db, ny(n), CFG).start_limit)
            self.send(ny(n, 10))
        self.assertEqual(starts, [20, 30, 40, 50, 60, 70, 80, 90, 100, 100])

    def test_ramp_stops_at_100(self):
        put_day(self.db, d(0), 100, outcome="clean", closed=True)
        self.assertEqual(ramp.open_day(self.db, ny(1), CFG).start_limit, 100)
        self.assertEqual(ramp.limit_for(self.db, d(2), CFG), 100)

    def test_warning_today_lowers_the_effective_limit_at_once(self):
        put_day(self.db, d(0), 50)
        self.note("policy_rejection", ny(0, 11))
        self.assertEqual(ramp.effective_limit(self.db, d(0), CFG), 40)
        self.assertEqual(self.row(d(0))["outcome"], "warning")
        self.assertEqual(self.row(d(0))["send_limit"], 50)
        state = ramp.today_state(self.db, ny(0, 12), CFG)
        self.assertEqual((state.start_limit, state.effective_limit, state.outcome), (50, 40, "warning"))

    def test_warning_day_does_not_rise(self):
        put_day(self.db, d(0), 50)
        self.send(ny(0, 10))
        self.note("policy_rejection", ny(0, 11))
        state = ramp.open_day(self.db, ny(1), CFG)
        self.assertEqual(state.start_limit, 40)
        closed = self.row(d(0))
        self.assertEqual(closed["outcome"], "warning")
        self.assertIsNotNone(closed["closed_at"])

    def test_two_warnings_in_one_day_step_down_once(self):
        put_day(self.db, d(0), 50)
        self.note("policy_rejection", ny(0, 11))
        self.note("stop_reply", ny(0, 12))
        self.assertEqual(ramp.effective_limit(self.db, d(0), CFG), 40)
        self.assertEqual(ramp.open_day(self.db, ny(1), CFG).start_limit, 40)

    def test_floor_is_20(self):
        put_day(self.db, d(0), 20)
        self.note("bounce_warning", ny(0, 11))
        self.assertEqual(ramp.effective_limit(self.db, d(0), CFG), 20)
        self.assertEqual(ramp.open_day(self.db, ny(1), CFG).start_limit, 20)

    def test_day_with_no_sends_keeps_the_limit(self):
        put_day(self.db, d(0), 40)
        state = ramp.open_day(self.db, ny(1), CFG)
        self.assertEqual(state.start_limit, 40)
        old = self.row(d(0))
        self.assertIsNone(old["outcome"])
        self.assertEqual(old["outcome_note"], "no sends")
        self.assertIsNotNone(old["closed_at"])

    def test_day_with_sends_closes_clean(self):
        put_day(self.db, d(0), 40)
        self.send(ny(0, 10))
        ramp.open_day(self.db, ny(1), CFG)
        self.assertEqual(self.row(d(0))["outcome"], "clean")

    def test_a_day_is_not_clean_when_the_three_day_bounce_rate_warns(self):
        put_day(self.db, d(0), 50)
        mon = self.sends(ny(0, 10), 20)
        bounce(self.db, mon[0], ny(0, 15))  # 1 in 20: 5%
        self.assertEqual(ramp.limit_for(self.db, d(1), CFG), 40)  # the open Monday is not clean
        self.assertEqual(ramp.open_day(self.db, ny(1), CFG).start_limit, 40)
        self.assertEqual(self.row(d(0))["outcome"], "warning")
        self.assertEqual(self.row(d(0))["outcome_note"], "bounce rate")
        self.sends(ny(1, 10), 20)  # Tuesday has no bounce, but the 3-day rate is 1 in 40: 2.5%
        self.assertEqual(ramp.limit_for(self.db, d(2), CFG), 30)
        self.assertEqual(ramp.open_day(self.db, ny(2), CFG).start_limit, 30)
        self.assertEqual(self.row(d(1))["outcome"], "warning")
        self.assertEqual(self.row(d(1))["outcome_note"], "bounce rate")

    def test_a_day_under_the_bounce_rate_still_closes_clean(self):
        put_day(self.db, d(0), 50)
        mon = self.sends(ny(0, 10), 51)
        bounce(self.db, mon[0], ny(0, 15))
        self.assertEqual(ramp.open_day(self.db, ny(1), CFG).start_limit, 60)
        self.assertEqual(self.row(d(0))["outcome"], "clean")

    def test_weekend_has_no_row(self):
        for n in (5, 6):
            self.assertIsNone(ramp.open_day(self.db, ny(n), CFG))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM send_day").fetchone()[0], 0)
        self.assertIsNone(ramp.today_state(self.db, ny(5), CFG))

    def test_sleeping_mac_does_not_ramp_twice(self):
        put_day(self.db, d(4), 30)  # Friday
        self.send(ny(4, 10))
        state = ramp.open_day(self.db, ny(9), CFG)  # the next tick is Wednesday 2026-11-11
        self.assertEqual((state.day, state.start_limit), (d(9), 40))
        days = [r[0] for r in self.db.execute("SELECT day FROM send_day ORDER BY day")]
        self.assertEqual(days, [d(4).isoformat(), d(9).isoformat()])
        self.assertEqual(self.row(d(4))["outcome"], "clean")

    def test_open_day_twice_in_a_day_changes_nothing(self):
        ramp.open_day(self.db, ny(0), CFG)
        before = self.db.total_changes
        again = ramp.open_day(self.db, ny(0, 15), CFG)
        self.assertEqual(again.start_limit, 20)
        self.assertEqual(self.db.total_changes, before)

    def test_a_late_signal_belongs_to_its_own_day_until_that_day_closes(self):
        put_day(self.db, d(0), 40)
        late, processed = ny(0, 23, 50), ny(1, 0, 5)
        self.assertEqual(signals.signal_day(self.db, iso(late), CFG, now=processed), d(0))
        self.note("bounce_warning", late, now=processed)
        self.assertEqual(self.row(d(0))["outcome"], "warning")
        self.assertIsNone(self.row(d(1)))
        # The next day opens and closes Monday; a signal for Monday that is read now lands on Tuesday.
        ramp.open_day(self.db, ny(1, 9), CFG)
        self.assertEqual(self.row(d(1))["send_limit"], 30)
        later = ny(1, 9, 30)
        self.assertEqual(signals.signal_day(self.db, iso(ny(0, 23, 55)), CFG, now=later), d(1))
        self.note("policy_rejection", ny(0, 23, 55), now=later)
        self.assertEqual(self.row(d(1))["outcome"], "warning")
        self.assertEqual(ramp.effective_limit(self.db, d(1), CFG), 20)
        day_of = [json.loads(e["detail"])["day"] for e in self.events("policy_rejection")]
        self.assertEqual(day_of, [d(1).isoformat()])

    def test_a_signal_for_a_day_that_never_opened_lands_on_today(self):
        # The Mac slept through Tuesday; Wednesday is open, so Tuesday can no longer take a signal.
        put_day(self.db, d(4), 30)
        self.send(ny(4, 10))
        ramp.open_day(self.db, ny(9), CFG)
        self.assertEqual(signals.signal_day(self.db, iso(ny(8, 12)), CFG, now=ny(9, 10)), d(9))

    def test_limit_for_matches_open_day(self):
        put_day(self.db, d(0), 40)
        self.send(ny(0, 10))
        before = self.db.total_changes
        predicted = ramp.limit_for(self.db, d(1), CFG)
        self.assertEqual(self.db.total_changes, before)
        self.assertEqual(predicted, 50)
        self.assertEqual(ramp.open_day(self.db, ny(1), CFG).start_limit, predicted)

    def test_limit_for_with_no_row_is_20(self):
        self.assertEqual(ramp.limit_for(self.db, d(0), CFG), 20)

    def test_effective_limit_is_one_level_lower_after_a_warning(self):
        put_day(self.db, d(0), 40)
        self.send(ny(0, 10))
        self.assertEqual(ramp.open_day(self.db, ny(1), CFG).start_limit, 50)
        self.assertEqual(ramp.effective_limit(self.db, d(1), CFG), 50)
        self.note("policy_rejection", ny(1, 11))
        before = self.db.total_changes
        self.assertEqual(ramp.effective_limit(self.db, d(1), CFG), 40)
        self.assertEqual(ramp.limit_for(self.db, d(1), CFG), 50)
        self.assertEqual(self.row(d(1))["send_limit"], 50)
        self.assertEqual(self.db.total_changes, before)

    def test_effective_limit_before_the_day_opens_uses_limit_for(self):
        put_day(self.db, d(0), 40)
        self.send(ny(0, 10))
        self.assertEqual(ramp.effective_limit(self.db, d(1), CFG), 50)


class WeekendSendTest(Base):
    """Monday morning in Tokyo is Sunday evening in New York: the sender's day is the New York date, whatever
    weekday that is."""
    TOKYO_MONDAY_8AM = datetime(2026, 11, 9, 8, 0, tzinfo=ZoneInfo("Asia/Tokyo"))

    def test_new_york_weekend_send_counts_in_the_rates(self):
        touch = self.send(self.TOKYO_MONDAY_8AM, tz="Asia/Tokyo")
        self.sends(ny(4, 10), 3)  # Friday
        self.assertEqual(signals.sends_on(self.db, d(6), CFG), 1)
        self.assertEqual(signals.sends_on(self.db, d(7), CFG), 0)
        bounce(self.db, touch, ny(6, 19))
        # Sending dates up to Sunday: Sunday and Friday. The weekend date is not skipped.
        self.assertEqual(signals.bounce_rate_3d(self.db, d(6), CFG), 1 / 4)

    def test_weekend_warning_steps_the_next_business_day_down(self):
        put_day(self.db, d(4), 50)
        self.send(ny(4, 10))
        self.note("policy_rejection", ny(6, 18))  # Sunday evening in New York
        self.assertEqual(ramp.effective_limit(self.db, d(6), CFG), 50)  # Friday clean made Sunday 60, one level lower
        state = ramp.open_day(self.db, ny(7, 9), CFG)
        self.assertEqual(state.start_limit, 50)
        self.assertEqual(self.row(d(6))["outcome"], "warning")
        self.assertEqual(self.row(d(4))["outcome"], "clean")

    def test_weekend_hard_block_pauses_and_is_not_lost(self):
        self.note("gmail_limit", ny(5, 12))
        self.assertEqual(self.sender()["pause_kind"], "hard_block")
        self.assertEqual(self.row(d(5))["outcome"], "hard_block")


class BounceRateTest(Base):
    def test_bounce_rate_of_exactly_two_percent_is_a_warning(self):
        touches = self.sends(ny(0, 10), 17) + self.sends(ny(1, 10), 17) + self.sends(ny(2, 10), 16)
        bounce(self.db, touches[-1], ny(2, 12))
        rate = signals.bounce_rate_3d(self.db, d(2), CFG)
        self.assertEqual(rate, 0.02)
        self.assertTrue(signals.bounce_rate_warns(rate, CFG))

    def test_just_under_two_percent_is_clean(self):
        touches = self.sends(ny(0, 10), 17) + self.sends(ny(1, 10), 17) + self.sends(ny(2, 10), 17)
        bounce(self.db, touches[-1], ny(2, 12))
        rate = signals.bounce_rate_3d(self.db, d(2), CFG)
        self.assertAlmostEqual(rate, 1 / 51)
        self.assertFalse(signals.bounce_rate_warns(rate, CFG))

    def test_bounce_rate_3d_uses_sending_days_and_is_zero_without_sends(self):
        self.assertEqual(signals.bounce_rate_3d(self.db, d(2), CFG), 0.0)
        monday = self.sends(ny(0, 10), 10)
        self.sends(ny(2, 10), 10)
        self.sends(ny(3, 10), 10)
        friday = self.sends(ny(4, 10), 10)
        # Wednesday's three sending days are Wednesday and Monday only (Tuesday sent nothing).
        bounce(self.db, monday[0], ny(0, 15))
        self.assertEqual(signals.bounce_rate_3d(self.db, d(2), CFG), 1 / 20)
        # By Friday the window is Friday, Thursday and Wednesday; Monday's bounce has aged out.
        self.assertEqual(signals.bounce_rate_3d(self.db, d(4), CFG), 0.0)
        bounce(self.db, friday[0], ny(4, 15))
        self.assertEqual(signals.bounce_rate_3d(self.db, d(4), CFG), 1 / 30)

    def test_a_bounce_arriving_on_a_day_with_no_sends_is_counted(self):
        # A delayed DSN on Saturday for a Friday send: the date had no sends, the bounce still counts.
        touches = self.sends(ny(4, 10), 10)
        bounce(self.db, touches[0], ny(5, 10))
        self.assertEqual(signals.bounce_rate_3d(self.db, d(5), CFG), 1 / 10)
        self.assertEqual(signals.bounce_rate_3d(self.db, d(4), CFG), 0.0)

    def test_a_bounce_before_the_window_is_not_counted(self):
        touches = self.sends(ny(0, 10), 10)
        self.sends(ny(4, 10), 10)
        self.sends(ny(3, 10), 10)
        self.sends(ny(2, 10), 10)
        bounce(self.db, touches[0], ny(1, 10))  # Tuesday: before Wednesday, the first window date
        self.assertEqual(signals.bounce_rate_3d(self.db, d(4), CFG), 0.0)

    def test_follow_ups_count_as_sends(self):
        person = seed_email_person(self.db, self.company, "Fo", "fo@acme.co")
        seed_sent(self.db, person, at=ny(0, 10))
        seed_sent(self.db, person, at=ny(1, 10), seq=1)
        self.assertEqual(signals.sends_on(self.db, d(1), CFG), 1)

    def test_a_send_at_midnight_new_york_belongs_to_the_new_day(self):
        self.send(ny(1, 0, 0))
        self.assertEqual(signals.sends_on(self.db, d(0), CFG), 0)
        self.assertEqual(signals.sends_on(self.db, d(1), CFG), 1)

    def test_hard_bounce_rate_needs_ten_sends_and_over_three_percent(self):
        def fresh(sent, bounced):
            self.setUp()
            touches = [self.send(ny(0, 10) + timedelta(minutes=i)) for i in range(sent)]
            for t in touches[:bounced]:
                bounce(self.db, t, ny(1, 10))

        fresh(9, 1)
        rate, count = signals.hard_bounce_rate(self.db, CFG)
        self.assertEqual((round(rate, 4), count), (round(1 / 9, 4), 9))
        self.assertFalse(signals.hard_bounce_tripped(self.db, CFG))  # under 10 sends: not judged
        fresh(34, 1)
        rate, count = signals.hard_bounce_rate(self.db, CFG)
        self.assertEqual((round(rate, 3), count), (0.029, 34))
        self.assertFalse(signals.hard_bounce_tripped(self.db, CFG))
        fresh(40, 2)
        rate, count = signals.hard_bounce_rate(self.db, CFG)
        self.assertEqual((rate, count), (0.05, 40))
        self.assertTrue(signals.hard_bounce_tripped(self.db, CFG))

    def test_hard_bounce_rate_looks_at_the_last_100_first_emails_only(self):
        old = [self.send(ny(0, 10) + timedelta(minutes=i)) for i in range(20)]
        for t in old:
            bounce(self.db, t, ny(1, 10))
        self.sends(ny(2, 10), 100)
        self.assertEqual(signals.hard_bounce_rate(self.db, CFG), (0.0, 100))

    def test_hard_bounce_rate_ignores_follow_ups(self):
        person = seed_email_person(self.db, self.company, "Fo", "fo@acme.co")
        seed_sent(self.db, person, at=ny(0, 10))
        seed_sent(self.db, person, at=ny(1, 10), seq=1)
        self.assertEqual(signals.hard_bounce_rate(self.db, CFG), (0.0, 1))


class HardBlockTest(Base):
    def test_hard_kind_pauses_with_a_reason_and_notifies_once(self):
        put_day(self.db, d(0), 50)
        self.note("gmail_limit", ny(0, 11))
        first = self.sender()
        self.assertEqual((first["paused"], first["pause_kind"]), (1, "hard_block"))
        self.assertTrue(first["paused_reason"].endswith("."))
        self.assertIsNotNone(first["paused_at"])
        self.note("gmail_limit", ny(0, 12))
        second = self.sender()
        self.assertEqual(second["revision"], first["revision"])
        self.assertEqual(second["paused_reason"], first["paused_reason"])
        self.assertEqual(len(self.events("hard_block")), 1)
        self.assertEqual(len(self.events("gmail_limit")), 2)
        self.assertEqual(len(self.notices), 1)
        self.assertEqual(self.row(d(0))["outcome"], "hard_block")
        self.assertEqual(ramp.effective_limit(self.db, d(0), CFG), 40)

    def test_each_hard_kind_pauses_with_its_own_reason(self):
        reasons = set()
        for kind in signals.HARD_KINDS:
            self.setUp()
            self.note(kind, ny(0, 11))
            self.assertEqual(self.sender()["pause_kind"], "hard_block", kind)
            reasons.add(self.sender()["paused_reason"])
        self.assertEqual(len(reasons), len(signals.HARD_KINDS))

    def test_the_same_signal_key_twice_changes_nothing(self):
        self.note("gmail_limit", ny(0, 11), key="k1")
        before = self.db.total_changes
        self.note("gmail_limit", ny(0, 11), key="k1")
        self.assertEqual(self.db.total_changes, before)

    def test_hard_block_over_his_own_pause_becomes_hard_block(self):
        self.db.execute("UPDATE sender SET paused = 1, pause_kind = 'by_you', paused_reason = 'away', "
                        "paused_at = ? WHERE sender_id = 1", (iso(MON),))
        self.note("gmail_policy", ny(0, 11))
        row = self.sender()
        self.assertEqual((row["paused"], row["pause_kind"]), (1, "hard_block"))
        self.assertNotEqual(row["paused_reason"], "away")

    def test_a_warning_does_not_pause_or_notify(self):
        self.note("policy_rejection", ny(0, 11))
        self.assertEqual(self.sender()["paused"], 0)
        self.assertEqual(self.notices, [])

    def test_a_warning_never_downgrades_a_hard_block(self):
        self.note("gmail_limit", ny(0, 11))
        self.note("policy_rejection", ny(0, 12))
        self.assertEqual(self.row(d(0))["outcome"], "hard_block")

    def test_a_hard_block_day_starts_the_next_day_one_level_lower(self):
        put_day(self.db, d(0), 50)
        self.note("gmail_limit", ny(0, 11))
        self.assertEqual(ramp.open_day(self.db, ny(1), CFG).start_limit, 40)

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            self.note("bogus", ny(0, 11))

    def test_a_failing_notice_does_not_undo_the_pause(self):
        def broken(title, text):
            raise OSError("no display")
        ramp.note_signal(self.db, "gmail_limit", at=iso(ny(0, 11)), detail={}, key="k", cfg=CFG, notify=broken,
                         now=ny(0, 11))
        self.assertEqual(self.sender()["pause_kind"], "hard_block")


class StopTagsTest(Base):
    def tag(self, touch_id, at):
        self.db.execute("UPDATE touch SET state = 'replied', replied_at = ?, reply_kind = 'asked_to_stop' "
                        "WHERE touch_id = ?", (iso(at), touch_id))

    def test_stop_tags_in_counts_the_last_days_only(self):
        a, b, c = self.sends(ny(0, 10), 3)
        self.tag(a, ny(1))
        self.tag(b, ny(6))
        self.tag(c, ny(-9))
        self.assertEqual(signals.stop_tags_in(self.db, ny(7), 7), 2)
        self.assertEqual(signals.stop_tags_in(self.db, iso(ny(7)), 7), 2)
        self.assertEqual(signals.stop_tags_in(self.db, ny(7), 1), 0)

    def test_other_reply_kinds_do_not_count(self):
        (a,) = self.sends(ny(0, 10), 1)
        self.db.execute("UPDATE touch SET state = 'replied', replied_at = ?, reply_kind = 'said_no' "
                        "WHERE touch_id = ?", (iso(ny(1)), a))
        self.assertEqual(signals.stop_tags_in(self.db, ny(2), 7), 0)

    def test_a_tag_on_the_outcome_record_counts_once(self):
        (a,) = self.sends(ny(0, 10), 1)
        company_id = self.company
        person_id = self.db.execute("SELECT person_id FROM touch WHERE touch_id = ?", (a,)).fetchone()[0]
        context = {"source": "own", "lane": None, "roleKind": "engineer", "roleAgeDays": None,
                   "rankerVersion": "v1", "signals": {}, "roleNamed": False}
        outcome = log_touch(self.db, person_id=person_id, company_id=company_id, channel="email",
                            sent_at=iso(ny(0, 10)), context=context, email_touch_id=a, idempotency_key="k-a",
                            actor="sender")
        self.db.execute("UPDATE outreach_touch SET replied_at = ?, reply_kind = 'asked_to_stop' WHERE touch_id = ?",
                        (iso(ny(1)), outcome))
        self.assertEqual(signals.stop_tags_in(self.db, ny(2), 7), 1)
        self.tag(a, ny(1))
        self.assertEqual(signals.stop_tags_in(self.db, ny(2), 7), 1)


class NowIsRequiredTest(Base):
    def test_signal_day_and_note_signal_need_the_moment_they_run(self):
        with self.assertRaises(TypeError):
            signals.signal_day(self.db, iso(ny(0, 11)), CFG)
        with self.assertRaises(TypeError):
            ramp.note_signal(self.db, "bounce_warning", at=iso(ny(0, 11)), detail={}, key="k", cfg=CFG,
                             notify=self.notifier)


class NotifyTest(Base):
    def test_the_notified_event_carries_the_moment_it_was_sent(self):
        notify.notify_once(self.db, self.notifier, "gmail_limit", d(0), "Paused", "Limit", at=STAMP)
        row = self.db.execute("SELECT at FROM event WHERE kind = 'notified'").fetchone()
        self.assertEqual(row["at"], STAMP)

    def test_notify_once_needs_the_moment(self):
        with self.assertRaises(TypeError):
            notify.notify_once(self.db, self.notifier, "gmail_limit", d(0), "Paused", "Limit")

    def test_notify_once_dedupes_by_kind_and_day(self):
        self.assertTrue(notify.notify_once(self.db, self.notifier, "gmail_limit", d(0), "Paused", "Limit", at=STAMP))
        self.assertFalse(notify.notify_once(self.db, self.notifier, "gmail_limit", d(0), "Paused", "Limit", at=STAMP))
        self.assertTrue(notify.notify_once(self.db, self.notifier, "gmail_limit", d(1), "Paused", "Limit", at=STAMP))
        self.assertTrue(notify.notify_once(self.db, self.notifier, "gmail_policy", d(0), "Paused", "Policy", at=STAMP))
        self.assertEqual(len(self.notices), 3)
        keys = [r[0] for r in self.db.execute("SELECT idempotency_key FROM event WHERE kind = 'notified' "
                                              "ORDER BY event_id")]
        self.assertEqual(keys, ["notify:gmail_limit:2026-11-02", "notify:gmail_limit:2026-11-03",
                                "notify:gmail_policy:2026-11-02"])

    def test_mac_notifier_does_nothing_outside_the_live_checkout(self):
        with mock.patch("outreach.sending.notify.subprocess.run") as run:
            notify.mac_notifier("Paused", "Gmail said stop")
        run.assert_not_called()

    def test_mac_notifier_runs_osascript_in_the_live_checkout_with_the_text_escaped(self):
        with mock.patch("outreach.sending.notify.live_checkout.is_live", return_value=True), \
                mock.patch("outreach.sending.notify.subprocess.run") as run:
            notify.mac_notifier('Say "hi"', 'back\\slash "quoted"')
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["osascript", "-e"])
        self.assertEqual(args[2], 'display notification "back\\\\slash \\"quoted\\"" with title "Say \\"hi\\""')

    def test_mac_notifier_survives_a_missing_osascript(self):
        with mock.patch("outreach.sending.notify.live_checkout.is_live", return_value=True), \
                mock.patch("outreach.sending.notify.subprocess.run", side_effect=FileNotFoundError):
            notify.mac_notifier("Paused", "text")


if __name__ == "__main__":
    unittest.main()
