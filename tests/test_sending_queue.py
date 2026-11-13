import dataclasses
import random
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from outreach.sending import accounts as _accounts
from outreach.sending import human, queue, ramp
from outreach.sending.config import load_config
from outreach.sending.accounts import in_play as _in_play
from tests import SHIPPED_SEND_CONFIG
from tests.sendtest import MON, UTC, bounce, put_day, seed_email_person, seed_message, seed_sent, with_ramp, solo
from tests.storetest import fresh_store, seed_company

# A floor of 1 so a test can set a day's limit as low as it likes: the floor lifts any lower row (#9, 2026-11-06).
CFG = dataclasses.replace(with_ramp(solo(load_config()), ramp_floor=1), followup_business_days=(3, 7, 14, 21))  # the 4-step order mechanism; shipped config is one follow-up (0018)
NY = ZoneInfo("America/New_York")
LA = ZoneInfo("America/Los_Angeles")
MON_DAY = date(2026, 11, 2)


def ny(day: int, hour: int, minute: int = 0) -> datetime:
    """A New York wall-clock moment on 2026-11-`day`, as aware UTC."""
    return datetime(2026, 11, day, hour, minute, tzinfo=NY).astimezone(UTC)


def stamp(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Base(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()
        self.company = seed_company(self.db)
        self.n = 0

    def person(self, *, company=None, role="engineer", tz="America/New_York"):
        self.n += 1
        return seed_email_person(self.db, company or self.company, f"P{self.n}", f"p{self.n}@acme.co",
                                 role_kind=role, tz=tz)

    def approve(self, *, company=None, role="engineer", tz="America/New_York", approved_at=None):
        """An approved first email to a new person. Returns (person id, message id)."""
        person = self.person(company=company, role=role, tz=tz)
        return person, seed_message(self.db, person, approved_at=approved_at)

    def approve_followup(self, due_on, *, company=None):
        person = self.person(company=company)
        seed_sent(self.db, person, at=ny(2, 8) - timedelta(days=7))
        return person, seed_message(self.db, person, seq=1, due_on=due_on)

    def sent_at(self, company, at, n=1):
        for _ in range(n):
            seed_sent(self.db, self.person(company=company), at=at)

    def touch_of(self, person):
        return self.db.execute("SELECT touch_id FROM touch WHERE person_id = ?", (person,)).fetchone()[0]

    def merge(self, duplicate, survivor):
        self.db.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                        "WHERE company_id = ?", (survivor, duplicate))

    def verdicts_at(self, now=MON, *, gate=None):
        """The verdicts at `now` by message id."""
        day = ramp.open_day(self.db, _accounts.primary(self.db, CFG), now, CFG)
        return {v.message_id: v for v in queue.verdicts(self.db, now, CFG, _in_play(self.db, CFG), day=day, gate=gate)}

    def reason(self, message, now=MON, **kw) -> str:
        return self.verdicts_at(now, **kw)[message].reason


ONE = CFG.accounts[0].address  # the one account of CFG and HUMAN
HUMAN = solo(load_config(SHIPPED_SEND_CONFIG))  # the shipped config: jittered day start and company gaps, a pick pool


class HumanPacingTest(Base):
    def verdicts_with_human(self, now):
        day = ramp.open_day(self.db, _accounts.primary(self.db, HUMAN), now, HUMAN)
        return {v.message_id: v for v in queue.verdicts(self.db, now, HUMAN, _in_play(self.db, HUMAN), day=day, gate=None)}

    def test_the_first_send_of_the_day_starts_a_little_after_the_window_opens(self):
        _, message = self.approve()
        opens = ny(2, 8, 0)
        offset = human.day_start_offset(MON_DAY, HUMAN)
        self.assertGreater(offset, timedelta(0))
        verdict = self.verdicts_with_human(opens)[message]
        self.assertEqual((verdict.reason, verdict.earliest_at), ("spacing", opens + offset))
        self.assertEqual(self.verdicts_with_human(opens + offset)[message].reason, "ready")

    def test_the_day_start_wait_ends_once_something_has_been_sent_today(self):
        _, message = self.approve()
        self.sent_at(seed_company(self.db, "Other"), ny(2, 8, 31))
        self.assertEqual(self.verdicts_with_human(ny(2, 8, 32))[message].reason, "ready")

    def test_a_company_waits_its_own_jittered_gap_not_a_flat_hour(self):
        _, message = self.approve()
        last = MON - timedelta(minutes=60)
        self.sent_at(self.company, last)
        gap = human.company_gap(self.company, last, HUMAN)
        self.assertGreater(gap, timedelta(minutes=60))
        verdict = self.verdicts_with_human(MON)[message]
        self.assertEqual((verdict.reason, verdict.earliest_at), ("per_company_gap", last + gap))
        self.assertEqual(self.verdicts_with_human(last + gap)[message].reason, "ready")

    def test_the_company_gap_is_never_under_an_hour(self):
        for company in range(1, 40):
            for minute in range(0, 600, 37):
                at = MON + timedelta(minutes=minute)
                self.assertGreaterEqual(human.company_gap(company, at, HUMAN), timedelta(minutes=60))

    def test_pick_takes_the_first_ready_without_an_rng(self):
        ids = [self.approve(company=seed_company(self.db, f"C{i}"))[1] for i in range(4)]
        found = list(self.verdicts_at().values())
        self.assertEqual(queue.pick(found, queue.load_queue(self.db), account=ONE).message_id, ids[0])

    def test_pick_takes_the_earliest_window_close_first(self):
        # Approved first, a Los Angeles recipient; second, a New York one. At 14:50 ET both are ready, and New York
        # closes at 15:00 ET while Los Angeles closes at 18:00 ET, so the New York one goes with a pool of one.
        _, la = self.approve(company=seed_company(self.db, "LA"), tz="America/Los_Angeles")
        _, ny_id = self.approve(company=seed_company(self.db, "NYC"), tz="America/New_York")
        now = ny(2, 14, 50)
        verdicts = self.verdicts_at(now)
        self.assertEqual({v.reason for v in verdicts.values()}, {"ready"})
        items = queue.load_queue(self.db)
        self.assertEqual(queue.pick(list(verdicts.values()), items, account=ONE).message_id, la)  # no order given: queue order
        picked = queue.pick(list(verdicts.values()), items, random.Random(1), 1, now=now, cfg=CFG, account=ONE)
        self.assertEqual(picked.message_id, ny_id)

    def test_pick_draws_only_among_the_earliest_close(self):
        # Two New York recipients and four Los Angeles ones, a pool of 5: every draw is a New York one.
        for i in range(4):
            self.approve(company=seed_company(self.db, f"LA{i}"), tz="America/Los_Angeles")
        nyc = [self.approve(company=seed_company(self.db, f"NYC{i}"), tz="America/New_York")[1] for i in range(2)]
        now = ny(2, 14, 50)
        verdicts = list(self.verdicts_at(now).values())
        items = queue.load_queue(self.db)
        picked = {queue.pick(verdicts, items, random.Random(s), 5, now=now, cfg=CFG, account=ONE).message_id for s in range(80)}
        self.assertEqual(picked, set(nyc))

    def test_pick_with_an_rng_varies_inside_the_pool(self):
        import random
        ids = [self.approve(company=seed_company(self.db, f"C{i}"))[1] for i in range(8)]
        found = list(self.verdicts_at().values())
        picked = {queue.pick(found, queue.load_queue(self.db), random.Random(s), 3, account=ONE).message_id for s in range(60)}
        self.assertEqual(picked, set(ids[:3]))


class MinutesLeftTest(Base):
    def left(self, now=MON) -> float:
        return queue.minutes_left(self.db, now, CFG, ONE, _in_play(self.db, CFG))

    def test_only_recipients_that_can_go_today_set_the_close(self):
        # NY-only ready mail; one LA follow-up due next week and one LA company that is paused do not move the close.
        self.approve()
        la = self.person(tz="America/Los_Angeles")
        seed_sent(self.db, la, at=ny(2, 8) - timedelta(days=7))
        seed_message(self.db, la, seq=1, due_on="2026-11-09")
        paused = seed_company(self.db, "Paused")
        self.db.execute("UPDATE company SET status = 'working', paused_at = '2026-11-01T12:00:00Z' "
                        "WHERE company_id = ?", (paused,))
        self.approve(company=paused, tz="America/Los_Angeles")
        self.assertEqual(self.left(MON), 360.0)  # 09:00 to 15:00 in New York

    def test_a_recipient_that_can_go_today_sets_the_close(self):
        self.approve()
        self.approve(company=seed_company(self.db, "West"), tz="America/Los_Angeles")
        self.assertEqual(self.left(MON), 540.0)  # to 18:00 New York, the Los Angeles close

    def test_a_company_at_its_daily_limit_does_not_count(self):
        limited = seed_company(self.db, "Limited")
        self.sent_at(limited, ny(2, 8, 30), n=CFG.per_company_daily)
        self.approve(company=limited, tz="America/Los_Angeles")
        self.approve()
        self.assertEqual(self.left(MON), 360.0)

    def test_a_tokyo_recipient_closes_on_their_own_date(self):
        # 19:00 Monday in New York is 09:00 Tuesday in Tokyo: ready, and the window closes Tuesday 15:00 Tokyo time.
        self.approve(tz="Asia/Tokyo")
        now = ny(2, 19)
        self.assertEqual(self.reason(self.db.execute("SELECT message_id FROM message").fetchone()[0], now), "ready")
        self.assertEqual(self.left(now), 360.0)

    def test_pick_uses_the_recipients_own_date_for_the_close(self):
        _, tokyo = self.approve(tz="Asia/Tokyo")
        _, la = self.approve(tz="America/Los_Angeles")
        now = ny(2, 19)  # Tokyo closes in 6 hours, Los Angeles (16:00 Monday there) in 2
        verdicts = list(self.verdicts_at(now).values())
        picked = queue.pick(verdicts, queue.load_queue(self.db), random.Random(1), 5, now=now, cfg=CFG, account=ONE)
        self.assertEqual({v.message_id: v.reason for v in verdicts}, {tokyo: "ready", la: "window"})
        self.assertEqual(picked.message_id, tokyo)


class PrepareTest(Base):
    def test_decide_all_equals_verdicts_for_the_whole_queue(self):
        other = seed_company(self.db, "Other")
        for _ in range(3):
            self.approve()
        for _ in range(3):
            self.approve(company=other)
        self.sent_at(self.company, MON - timedelta(minutes=30))
        day = ramp.open_day(self.db, _accounts.primary(self.db, CFG), MON, CFG)
        expected = queue.verdicts(self.db, MON, CFG, _in_play(self.db, CFG), day=day, gate=None)
        p = queue.prepare(self.db, MON, CFG, _in_play(self.db, CFG), day=day)
        got = queue.decide_all(p, MON, CFG, gate=None, pending=p.queue, sends=p.sends,
                               sent_by_day={ONE: {queue.cal.ny_date(MON, CFG): p.sent_today}},
                               next_send={ONE: p.next_send})
        self.assertEqual(got, expected)
        self.assertEqual(len(got), 6)


class QueueOrderTest(Base):
    def test_queue_is_first_approved_first(self):
        _, late = self.approve(approved_at="2026-11-02T14:00:00Z")
        _, early = self.approve(approved_at="2026-11-02T13:00:00Z")
        _, tie_a = self.approve(approved_at="2026-11-02T15:00:00Z")
        _, tie_b = self.approve(approved_at="2026-11-02T15:00:00Z")
        order = [q.message_id for q in queue.load_queue(self.db)]
        self.assertEqual(order, [early, late, tie_a, tie_b])
        verdicts = list(self.verdicts_at().values())
        self.assertEqual(queue.pick(verdicts, queue.load_queue(self.db), account=ONE).message_id, early)

    def test_a_follow_up_sorts_by_its_due_date_not_its_approval(self):
        _, followup = self.approve_followup("2026-11-06")
        _, fresh = self.approve(approved_at="2026-11-03T13:00:00Z")
        order = [q.message_id for q in queue.load_queue(self.db)]
        self.assertEqual(order, [fresh, followup])

    def test_only_approved_messages_are_queued(self):
        person = self.person()
        seed_message(self.db, person, state="drafted")
        _, approved = self.approve()
        self.assertEqual([q.message_id for q in queue.load_queue(self.db)], [approved])

    def test_queued_carries_what_the_sender_needs(self):
        person, message = self.approve(role="founder", tz="America/Los_Angeles")
        (item,) = queue.load_queue(self.db)
        self.assertEqual((item.message_id, item.person_id, item.to, item.tz, item.role_kind, item.company_id,
                          item.seq, item.due_on, item.approved_at),
                         (message, person, "p1@acme.co", "America/Los_Angeles", "founder", self.company, 0, None,
                          "2026-11-02T13:00:00Z"))
        self.assertEqual(item.touch_id, self.touch_of(person))

    def test_pick_skips_waiting_messages_and_returns_none_when_nothing_is_ready(self):
        paused = seed_company(self.db, "Paused")
        self.db.execute("UPDATE company SET status = 'working', paused_at = '2026-11-01T12:00:00Z' "
                        "WHERE company_id = ?", (paused,))
        _, first = self.approve(company=paused, approved_at="2026-11-02T10:00:00Z")
        _, second = self.approve(approved_at="2026-11-02T11:00:00Z")
        items = queue.load_queue(self.db)
        self.assertEqual(queue.pick(list(self.verdicts_at().values()), items, account=ONE).message_id, second)
        self.assertIsNone(queue.pick([v for v in self.verdicts_at().values() if v.message_id == first], items, account=ONE))


class LimitsTest(Base):
    def test_daily_limit_counts_first_emails_and_follow_ups(self):
        put_day(self.db, MON_DAY, 3)
        _, message = self.approve()
        self.sent_at(seed_company(self.db, "Other"), ny(2, 7))
        follower = self.person(company=seed_company(self.db, "Third"))
        seed_sent(self.db, follower, at=ny(2, 7) - timedelta(days=7))
        seed_sent(self.db, follower, at=ny(2, 8), seq=1)
        self.assertEqual(self.reason(message), "ready")
        self.sent_at(seed_company(self.db, "Fourth"), ny(2, 8, 30))
        verdict = self.verdicts_at()[message]
        self.assertEqual(verdict.reason, "daily_limit")
        self.assertEqual(verdict.earliest_at, ny(3, 8, 0))

    def claim(self, company, at):
        """A message claimed at `at` that may or may not have gone out."""
        message = seed_message(self.db, self.person(company=company))
        self.db.execute("UPDATE message SET state = 'sending', pending_rfc_id = '<x@y.z>', claimed_at = ? "
                        "WHERE message_id = ?", (stamp(at), message))
        return message

    def test_an_in_flight_send_counts_against_the_daily_limit(self):
        put_day(self.db, MON_DAY, 2)
        _, message = self.approve()
        self.sent_at(seed_company(self.db, "Other"), ny(2, 8))
        self.assertEqual(self.reason(message), "ready")
        self.claim(seed_company(self.db, "InFlight"), ny(2, 8, 45))
        self.assertEqual(self.reason(message), "daily_limit")

    def test_an_in_flight_send_from_yesterday_does_not_count_today(self):
        put_day(self.db, MON_DAY, 1)
        _, message = self.approve()
        self.claim(seed_company(self.db, "Old"), ny(1, 10))
        self.assertEqual(self.reason(message), "ready")

    def test_a_warning_lowers_the_daily_limit_at_once(self):
        put_day(self.db, MON_DAY, 30)
        _, message = self.approve()
        for i in range(20):
            self.sent_at(seed_company(self.db, f"Other{i}"), ny(2, 8))
        self.assertEqual(self.reason(message), "ready")
        self.db.execute("UPDATE send_day SET outcome = 'warning' WHERE day = ?", (MON_DAY.isoformat(),))
        self.assertEqual(self.reason(message), "daily_limit")

    def test_second_send_at_one_company_waits_60_minutes(self):
        _, message = self.approve()
        self.sent_at(self.company, MON - timedelta(minutes=59))
        verdict = self.verdicts_at()[message]
        self.assertEqual(verdict.reason, "per_company_gap")
        self.assertEqual(verdict.earliest_at, MON + timedelta(minutes=1))
        self.assertEqual(self.reason(message, MON + timedelta(minutes=1)), "ready")

    def test_exactly_60_minutes_after_the_last_send_is_ready(self):
        _, message = self.approve()
        self.sent_at(self.company, MON - timedelta(minutes=60))
        self.assertEqual(self.reason(message), "ready")

    def test_the_gap_reaches_back_across_midnight_in_new_york(self):
        now = ny(3, 0, 20)
        put_day(self.db, date(2026, 11, 3), 20)
        _, message = self.approve(tz="Asia/Tokyo")
        self.sent_at(self.company, ny(2, 23, 40))
        self.assertEqual(self.verdicts_at(now)[message].reason, "per_company_gap")

    def test_fourth_send_at_one_company_waits_for_tomorrow(self):
        _, message = self.approve()
        for hour in (5, 6, 7):
            self.sent_at(self.company, ny(2, hour))
        verdict = self.verdicts_at()[message]
        self.assertEqual(verdict.reason, "per_company_limit")
        self.assertEqual(verdict.earliest_at, ny(3, 8, 0))

    def test_a_follow_up_never_waits_for_the_company_limit(self):
        # #9, 2026-10-31: the per-company limit counts first emails only.
        for hour in (5, 6, 7):
            self.sent_at(self.company, ny(2, hour))
        _, followup = self.approve_followup("2026-11-02")
        self.assertEqual(self.reason(followup), "ready")

    def test_follow_ups_sent_today_do_not_count_toward_the_company_limit(self):
        for hour in (5, 6, 7):
            person = self.person()
            seed_sent(self.db, person, at=ny(2, 8) - timedelta(days=7))
            seed_sent(self.db, person, at=ny(2, hour), seq=1)
        _, message = self.approve()
        self.assertEqual(self.reason(message), "ready")

    def test_a_follow_up_still_keeps_the_60_minute_company_gap(self):
        self.sent_at(self.company, MON - timedelta(minutes=30))
        _, followup = self.approve_followup("2026-11-02")
        self.assertEqual(self.reason(followup), "per_company_gap")

    def test_third_send_at_one_company_is_still_ready(self):
        _, message = self.approve()
        self.sent_at(self.company, ny(2, 5))
        self.sent_at(self.company, ny(2, 7))
        self.assertEqual(self.reason(message), "ready")

    def test_yesterdays_sends_do_not_count_against_the_company_today(self):
        _, message = self.approve()
        for hour in (9, 10, 11):
            self.sent_at(self.company, ny(2, hour) - timedelta(days=1))
        self.assertEqual(self.reason(message), "ready")

    def test_per_company_counts_use_the_surviving_company(self):
        survivor = seed_company(self.db, "Survivor")
        duplicate = seed_company(self.db, "Duplicate")
        self.merge(duplicate, survivor)
        _, at_survivor = self.approve(company=survivor)
        _, at_duplicate = self.approve(company=duplicate)
        self.sent_at(survivor, ny(2, 5))
        self.sent_at(duplicate, ny(2, 6))
        self.sent_at(duplicate, ny(2, 7))
        self.assertEqual(self.reason(at_survivor), "per_company_limit")
        self.assertEqual(self.reason(at_duplicate), "per_company_limit")

    def test_person_at_merged_company_sends_under_the_survivor(self):
        survivor = seed_company(self.db, "Survivor")
        duplicate = seed_company(self.db, "Duplicate")
        self.merge(duplicate, survivor)
        _, message = self.approve(company=duplicate)
        self.assertEqual(self.reason(message), "ready")
        (item,) = queue.load_queue(self.db)
        self.assertEqual(item.company_id, survivor)

    def test_a_claimed_message_counts_as_a_send_at_its_company(self):
        _, message = self.approve()
        other = seed_message(self.db, self.person())
        self.db.execute("UPDATE message SET state = 'sending', pending_rfc_id = '<x@y.z>', claimed_at = ? "
                        "WHERE message_id = ?", (stamp(MON - timedelta(minutes=5)), other))
        self.assertEqual(self.reason(message), "per_company_gap")


class RecruiterTest(Base):
    def test_recruiter_waits_for_the_engineer_at_the_same_company(self):
        _, recruiter = self.approve(role="recruiter", approved_at="2026-11-02T10:00:00Z")
        _, engineer = self.approve(role="engineer", approved_at="2026-11-02T11:00:00Z")
        verdicts = self.verdicts_at()
        self.assertEqual(verdicts[recruiter].reason, "recruiter_waits")
        self.assertEqual(verdicts[engineer].reason, "ready")
        self.assertEqual(queue.pick(list(verdicts.values()), queue.load_queue(self.db), account=ONE).message_id, engineer)

    def test_recruiter_alone_is_ready(self):
        _, recruiter = self.approve(role="recruiter")
        self.assertEqual(self.reason(recruiter), "ready")

    def test_a_recruiter_is_not_held_back_by_another_company(self):
        _, recruiter = self.approve(role="recruiter")
        self.approve(role="engineer", company=seed_company(self.db, "Other"))
        self.assertEqual(self.reason(recruiter), "ready")

    def test_a_stopped_engineer_message_does_not_hold_the_recruiter(self):
        _, recruiter = self.approve(role="recruiter")
        engineer, message = self.approve(role="engineer")
        self.db.execute("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'said_no', 'reply', "
                        "'2026-11-01T12:00:00Z')", (engineer,))
        verdicts = self.verdicts_at()
        self.assertEqual(verdicts[message].reason, "no_go")
        self.assertEqual(verdicts[recruiter].reason, "ready")

    def test_a_waiting_engineer_still_holds_the_recruiter_back(self):
        _, recruiter = self.approve(role="recruiter")
        _, engineer = self.approve(role="engineer", tz="Asia/Tokyo")
        verdicts = self.verdicts_at()
        self.assertEqual(verdicts[engineer].reason, "window")
        self.assertEqual(verdicts[recruiter].reason, "recruiter_waits")

    def test_recruiter_waits_for_an_engineer_at_the_merged_duplicate(self):
        survivor = seed_company(self.db, "Survivor")
        duplicate = seed_company(self.db, "Duplicate")
        self.merge(duplicate, survivor)
        _, recruiter = self.approve(role="recruiter", company=survivor)
        self.approve(role="manager", company=duplicate)
        self.assertEqual(self.reason(recruiter), "recruiter_waits")

    def test_recruiter_counts_inside_the_company_limit(self):
        _, recruiter = self.approve(role="recruiter")
        for hour in (5, 6, 7):
            self.sent_at(self.company, ny(2, hour))
        self.assertEqual(self.reason(recruiter), "per_company_limit")


class TimingTest(Base):
    def test_follow_up_waits_for_its_due_date(self):
        _, message = self.approve_followup("2026-11-03")
        verdict = self.verdicts_at(ny(2, 16))[message]
        self.assertEqual(verdict.reason, "followup_not_due")
        self.assertEqual(verdict.earliest_at, ny(3, 8, 0))
        self.assertEqual(self.reason(message, ny(3, 8, 0)), "ready")

    def test_follow_up_due_on_a_weekend_waits_for_monday(self):
        _, message = self.approve_followup("2026-11-07")
        self.assertEqual(self.verdicts_at(ny(6, 12))[message].earliest_at, ny(9, 8, 0))

    def test_window_is_per_recipient(self):
        now = ny(2, 16, 45)
        _, east = self.approve(company=seed_company(self.db, "East"), tz="America/New_York")
        _, west = self.approve(company=seed_company(self.db, "West"), tz="America/Los_Angeles")
        verdicts = self.verdicts_at(now)
        self.assertEqual(verdicts[east].reason, "window")
        self.assertEqual(verdicts[east].earliest_at, ny(3, 8, 0))
        self.assertEqual(verdicts[west].reason, "ready")
        self.assertEqual(now.astimezone(LA).hour, 13)

    def test_before_the_window_opens_the_earliest_time_is_today(self):
        _, message = self.approve()
        verdict = self.verdicts_at(ny(2, 7, 0))[message]
        self.assertEqual((verdict.reason, verdict.earliest_at), ("window", ny(2, 8, 0)))

    def test_weekend_is_not_a_business_day(self):
        _, message = self.approve()
        verdict = self.verdicts_at(ny(7, 10))[message]
        self.assertEqual(verdict.reason, "not_business_day")
        self.assertEqual(verdict.earliest_at, ny(9, 8, 0))

    def test_bad_timezone_waits_and_says_so(self):
        _, message = self.approve(tz="Mars/Olympus")
        verdict = self.verdicts_at()[message]
        self.assertEqual((verdict.reason, verdict.earliest_at), ("bad_timezone", None))

    def test_spacing_uses_next_send_at(self):
        _, message = self.approve()
        self.db.execute("UPDATE sender_account SET next_send_at = ? WHERE account_id = 1", (stamp(MON + timedelta(minutes=5)),))
        verdict = self.verdicts_at()[message]
        self.assertEqual((verdict.reason, verdict.earliest_at), ("spacing", MON + timedelta(minutes=5)))
        self.assertEqual(self.reason(message, MON + timedelta(minutes=5)), "ready")


class HoldsAndStopsTest(Base):
    def hold(self, company, touch_id):
        self.db.execute("UPDATE company SET held_by_touch = ?, held_at = '2026-11-02T12:00:00Z' "
                        "WHERE company_id = ?", (touch_id, company))

    def test_held_and_paused_companies_wait_and_say_so(self):
        held = seed_company(self.db, "Held")
        paused = seed_company(self.db, "Paused")
        replier = self.person(company=held)
        seed_sent(self.db, replier, at=ny(1, 9))
        self.hold(held, self.touch_of(replier))
        self.db.execute("UPDATE company SET status = 'working', paused_at = '2026-11-01T12:00:00Z' "
                        "WHERE company_id = ?", (paused,))
        _, at_held = self.approve(company=held)
        _, at_paused = self.approve(company=paused)
        verdicts = self.verdicts_at()
        self.assertEqual((verdicts[at_held].reason, verdicts[at_held].earliest_at), ("company_held", None))
        self.assertEqual((verdicts[at_paused].reason, verdicts[at_paused].earliest_at), ("company_paused", None))
        self.assertIn("held", queue.REASON_TEXT["company_held"])
        self.assertIn("paused", queue.REASON_TEXT["company_paused"])

    def test_a_hold_on_the_survivor_waits_the_duplicates_people(self):
        survivor = seed_company(self.db, "Survivor")
        duplicate = seed_company(self.db, "Duplicate")
        self.merge(duplicate, survivor)
        replier = self.person(company=survivor)
        seed_sent(self.db, replier, at=ny(1, 9))
        self.hold(survivor, self.touch_of(replier))
        _, message = self.approve(company=duplicate)
        self.assertEqual(self.reason(message), "company_held")

    def test_no_go_person_and_company_are_stop_reasons(self):
        person, on_person = self.approve()
        other = seed_company(self.db, "Other")
        lifted_company = seed_company(self.db, "Lifted")
        _, on_company = self.approve(company=other)
        _, lifted = self.approve(company=lifted_company)
        self.db.execute("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'said_no', 'reply', "
                        "'2026-11-01T12:00:00Z')", (person,))
        self.db.execute("INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'agency', 'rules', "
                        "'2026-11-01T12:00:00Z')", (other,))
        self.db.execute("INSERT INTO no_go(company_id, reason, source, created_at, lifted_at, lift_reason) "
                        "VALUES (?, 'agency', 'rules', '2026-11-01T12:00:00Z', '2026-11-02T01:00:00Z', 'wrong')",
                        (lifted_company,))
        verdicts = self.verdicts_at()
        self.assertEqual(verdicts[on_person].reason, "no_go")
        self.assertEqual(verdicts[on_company].reason, "no_go")
        self.assertEqual(verdicts[lifted].reason, "ready")
        self.assertIsNone(verdicts[on_person].earliest_at)

    def test_a_no_go_on_the_survivor_stops_the_duplicates_people(self):
        survivor = seed_company(self.db, "Survivor")
        duplicate = seed_company(self.db, "Duplicate")
        self.merge(duplicate, survivor)
        self.db.execute("INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'agency', 'rules', "
                        "'2026-11-01T12:00:00Z')", (survivor,))
        _, message = self.approve(company=duplicate)
        self.assertEqual(self.reason(message), "no_go")

    def test_a_no_go_on_a_merged_away_duplicate_stops_the_survivors_people(self):
        survivor = seed_company(self.db, "Survivor")
        duplicate = seed_company(self.db, "Duplicate")
        self.db.execute("INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'agency', 'rules', "
                        "'2026-11-01T12:00:00Z')", (duplicate,))
        self.merge(duplicate, survivor)
        _, at_survivor = self.approve(company=survivor)
        _, at_duplicate = self.approve(company=duplicate)
        verdicts = self.verdicts_at()
        self.assertEqual(verdicts[at_survivor].reason, "no_go")
        self.assertEqual(verdicts[at_duplicate].reason, "no_go")

    def test_closed_company_is_a_stop_reason(self):
        closed = seed_company(self.db, "Closed")
        removed = seed_company(self.db, "Removed")
        self.db.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?", (closed,))
        self.db.execute("UPDATE company SET status = 'removed', remove_reason = 'gone' WHERE company_id = ?",
                        (removed,))
        _, at_closed = self.approve(company=closed)
        _, at_removed = self.approve(company=removed)
        verdicts = self.verdicts_at()
        self.assertEqual(verdicts[at_closed].reason, "company_closed")
        self.assertEqual(verdicts[at_removed].reason, "company_closed")

    def test_a_closed_survivor_stops_the_duplicates_people(self):
        survivor = seed_company(self.db, "Survivor")
        duplicate = seed_company(self.db, "Duplicate")
        self.merge(duplicate, survivor)
        self.db.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?",
                        (survivor,))
        _, message = self.approve(company=duplicate)
        self.assertEqual(self.reason(message), "company_closed")

    def test_said_no_tag_is_a_stop_reason(self):
        cases = [(self.approve(company=seed_company(self.db, kind)), kind)
                 for kind in ("said_no", "asked_to_stop", "interested")]
        for (person, _), kind in cases:
            self.db.execute("UPDATE touch SET state = 'replied', replied_at = '2026-11-02T12:00:00Z', "
                            "reply_kind = ? WHERE touch_id = ?", (kind, self.touch_of(person)))
        verdicts = self.verdicts_at()
        for (_, message), _kind in cases:
            self.assertEqual(verdicts[message].reason, "reply_stop")
            self.assertIsNone(verdicts[message].earliest_at)

    def test_bounced_and_closed_touches_are_stop_reasons(self):
        bounced = self.approve(company=seed_company(self.db, "B"))
        closed = self.approve(company=seed_company(self.db, "C"))
        bounce(self.db, self.touch_of(bounced[0]), ny(1, 10))
        self.db.execute("UPDATE touch SET state = 'closed', close_reason = 'no_reply', closed_at = ? "
                        "WHERE touch_id = ?", (stamp(ny(1, 10)), self.touch_of(closed[0])))
        verdicts = self.verdicts_at()
        self.assertEqual(verdicts[bounced[1]].reason, "touch_done")
        self.assertEqual(verdicts[closed[1]].reason, "touch_done")

    def test_a_stop_reason_beats_a_gate(self):
        closed = seed_company(self.db, "Closed")
        self.db.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?", (closed,))
        _, message = self.approve(company=closed)
        self.assertEqual(self.reason(message, gate="signed_out"), "company_closed")

    def test_gate_applies_to_every_message(self):
        self.approve()
        self.approve(company=seed_company(self.db, "B"), tz="Asia/Tokyo")
        self.approve_followup("2026-11-20", company=seed_company(self.db, "C"))
        for gate in ("sender_paused", "signed_out", "old_sender_running", "message_id_unproven"):
            verdicts = self.verdicts_at(gate=gate)
            self.assertEqual(len(verdicts), 3)
            for message, verdict in verdicts.items():
                self.assertEqual(verdict.reason, gate, message)
                self.assertIsNone(verdict.earliest_at)


class HoldFamilyTest(Base):
    def test_a_hold_on_a_merged_away_duplicate_holds_the_survivor_and_the_duplicate(self):
        survivor = seed_company(self.db, "Survivor")
        duplicate = seed_company(self.db, "Duplicate")
        replier = self.person(company=duplicate)
        seed_sent(self.db, replier, at=ny(1, 9))
        self.db.execute("UPDATE company SET held_by_touch = ?, held_at = '2026-11-02T12:00:00Z' "
                        "WHERE company_id = ?", (self.touch_of(replier), duplicate))
        self.merge(duplicate, survivor)
        _, at_survivor = self.approve(company=survivor)
        _, at_duplicate = self.approve(company=duplicate)
        verdicts = self.verdicts_at()
        self.assertEqual(verdicts[at_survivor].reason, "company_held")
        self.assertEqual(verdicts[at_duplicate].reason, "company_held")


class NoAddressTest(Base):
    def blank_email(self, person):
        self.db.execute("UPDATE person SET email = NULL, added_by = 'import' WHERE person_id = ?", (person,))

    def test_no_address_waits_and_says_so(self):
        person, message = self.approve()
        self.blank_email(person)
        verdict = self.verdicts_at()[message]
        self.assertEqual((verdict.reason, verdict.earliest_at), ("no_address", None))
        self.assertEqual(queue.REASON_TEXT["no_address"], "No email address for this person.")
        self.assertIsNone(queue.pick(list(self.verdicts_at().values()), queue.load_queue(self.db), account=ONE))

    def test_no_address_waits_even_with_nothing_else_in_the_way(self):
        person, message = self.approve(tz="Asia/Tokyo")
        self.blank_email(person)
        self.assertEqual(self.reason(message, ny(2, 8, 0)), "no_address")

    def test_a_message_with_no_address_does_not_hold_a_recruiter(self):
        _, recruiter = self.approve(role="recruiter")
        engineer, message = self.approve(role="engineer")
        self.blank_email(engineer)
        verdicts = self.verdicts_at()
        self.assertEqual(verdicts[message].reason, "no_address")
        self.assertEqual(verdicts[recruiter].reason, "ready")


class RecruiterRuleEdgeTest(Base):
    def test_a_follow_up_that_is_not_due_does_not_hold_a_recruiter(self):
        _, recruiter = self.approve(role="recruiter")
        self.approve_followup("2026-11-20")
        self.assertEqual(self.reason(recruiter), "ready")

    def test_a_due_follow_up_still_holds_a_recruiter(self):
        _, recruiter = self.approve(role="recruiter")
        self.approve_followup("2026-11-02")
        self.assertEqual(self.reason(recruiter), "recruiter_waits")


class WindowEdgeTest(Base):
    def test_tokyo_saturday_while_new_york_is_friday_waits_for_tokyo_monday(self):
        now = ny(6, 20)  # Friday 20:00 in New York, Saturday 10:00 in Tokyo
        _, message = self.approve(tz="Asia/Tokyo")
        verdict = self.verdicts_at(now)[message]
        self.assertEqual(verdict.reason, "window")
        self.assertEqual(verdict.earliest_at, datetime(2026, 11, 9, 8, 0, tzinfo=ZoneInfo("Asia/Tokyo")))

    def test_15_00_exactly_is_inside_the_window_and_15_01_is_not(self):
        _, message = self.approve()
        self.assertEqual(self.reason(message, ny(2, 15, 0)), "ready")
        self.assertEqual(self.reason(message, ny(2, 15, 1)), "window")


class ReasonsTest(Base):
    def test_every_wait_has_an_earliest_time_or_is_a_stop(self):
        held = seed_company(self.db, "Held")
        replier = self.person(company=held)
        seed_sent(self.db, replier, at=ny(1, 9))
        self.db.execute("UPDATE company SET held_by_touch = ?, held_at = '2026-11-02T12:00:00Z' "
                        "WHERE company_id = ?", (self.touch_of(replier), held))
        paused = seed_company(self.db, "Paused")
        self.db.execute("UPDATE company SET status = 'working', paused_at = '2026-11-01T12:00:00Z' "
                        "WHERE company_id = ?", (paused,))
        closed = seed_company(self.db, "Closed")
        self.db.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?", (closed,))
        capped = seed_company(self.db, "Capped")
        for hour in (5, 6, 7):
            self.sent_at(capped, ny(2, hour))
        gapped = seed_company(self.db, "Gapped")
        self.sent_at(gapped, MON - timedelta(minutes=10))
        recruiter_co = seed_company(self.db, "Recruiters")
        self.approve(company=held)
        self.approve(company=paused)
        self.approve(company=closed)
        self.approve(company=capped)
        self.approve(company=gapped)
        self.approve(company=recruiter_co, role="recruiter")
        self.approve(company=recruiter_co, role="engineer", tz="Asia/Tokyo")
        self.approve(company=seed_company(self.db, "BadZone"), tz="Mars/Olympus")
        self.approve(company=seed_company(self.db, "Later"), tz="Asia/Tokyo")
        self.approve_followup("2026-11-05", company=seed_company(self.db, "Soon"))
        self.approve(company=seed_company(self.db, "Spaced"))
        self.approve(company=seed_company(self.db, "Fine"), tz="America/Los_Angeles")
        nobody, _ = self.approve(company=seed_company(self.db, "Nobody"))
        self.db.execute("UPDATE person SET email = NULL, added_by = 'import' WHERE person_id = ?", (nobody,))
        self.db.execute("UPDATE sender_account SET next_send_at = ? WHERE account_id = 1", (stamp(MON + timedelta(minutes=3)),))
        seen = set()
        for now in (MON, ny(2, 16, 45), ny(7, 10)):
            for verdict in self.verdicts_at(now).values():
                seen.add(verdict.reason)
                self.assertIn(verdict.reason, queue.REASONS)
                if verdict.reason in queue.TIME_REASONS:
                    self.assertIsNotNone(verdict.earliest_at, verdict.reason)
                    self.assertGreaterEqual(verdict.earliest_at, now, verdict.reason)
                else:
                    self.assertIsNone(verdict.earliest_at, verdict.reason)
        self.assertTrue({"company_held", "company_paused", "company_closed", "per_company_limit", "per_company_gap",
                         "recruiter_waits", "bad_timezone", "window", "no_address", "followup_not_due", "spacing",
                         "not_business_day", "ready"} <= seen, seen)

    def test_every_reason_is_one_plain_line(self):
        self.assertEqual(set(queue.REASON_TEXT), set(queue.REASONS))
        for reason, text in queue.REASON_TEXT.items():
            self.assertTrue(text and text == text.strip() and "\n" not in text, reason)
            self.assertFalse(any(c in text for c in (chr(0x2013), chr(0x2014))), reason)
        self.assertEqual(queue.REASONS[0], "ready")

    def test_verdicts_use_a_fixed_number_of_queries(self):
        def count(n):
            self.db = fresh_store()
            self.company = seed_company(self.db)
            for i in range(n):
                self.approve(company=seed_company(self.db, f"Co{i}"))
            statements = []
            self.db.set_trace_callback(statements.append)
            queue.load_queue(self.db)
            self.verdicts_at()
            self.db.set_trace_callback(None)
            return len(statements)
        self.assertEqual(count(3), count(40))


if __name__ == "__main__":
    unittest.main()


class FollowupOrderTest(Base):
    """Follow-ups in one thread go out in order (#141): a follow-up waits while an earlier one has not gone out."""

    def thread(self):
        """A person whose first email went out a week before Monday. Returns the person id."""
        person = self.person()
        seed_sent(self.db, person, at=ny(2, 8) - timedelta(days=7))
        return person

    def test_a_follow_up_waits_for_an_approved_earlier_one(self):
        person = self.thread()
        first = seed_message(self.db, person, seq=1, due_on="2026-10-28")
        second = seed_message(self.db, person, seq=2, due_on="2026-11-02")
        self.assertEqual(self.reason(first), "ready")
        self.assertEqual(self.reason(second), "earlier_followup")
        self.assertIsNone(self.verdicts_at()[second].earliest_at)

    def test_it_waits_while_the_earlier_one_waits_out_the_company_gap(self):
        person = self.thread()
        self.sent_at(self.company, MON - timedelta(minutes=30))
        first = seed_message(self.db, person, seq=1, due_on="2026-10-28")
        second = seed_message(self.db, person, seq=2, due_on="2026-11-02")
        self.assertEqual(self.reason(first), "per_company_gap")
        self.assertEqual(self.reason(second), "earlier_followup")

    def test_a_planned_earlier_one_holds_it_too(self):
        person = self.thread()
        seed_message(self.db, person, state="planned", seq=1, due_on="2026-10-28")
        second = seed_message(self.db, person, seq=2, due_on="2026-11-02")
        self.assertEqual(self.reason(second), "earlier_followup")

    def test_a_skipped_earlier_one_lets_it_go(self):
        person = self.thread()
        first = seed_message(self.db, person, state="drafted", seq=1, due_on="2026-10-28")
        self.db.execute("UPDATE message SET state = 'skipped' WHERE message_id = ?", (first,))
        second = seed_message(self.db, person, seq=2, due_on="2026-11-02")
        self.assertEqual(self.reason(second), "ready")

    def test_a_sent_earlier_one_lets_it_go(self):
        person = self.thread()
        seed_sent(self.db, person, at=ny(2, 8) - timedelta(days=6), seq=1)  # Tuesday: 4 business days ago
        second = seed_message(self.db, person, seq=2, due_on="2026-11-02")
        self.assertEqual(self.reason(second), "ready")

    def test_it_keeps_its_gap_after_the_earlier_one_went_out_late(self):
        # Follow-up 1 went out late, on Friday 2026-10-30; follow-up 2 is due by the first email's clock but waits
        # 4 business days (7 minus 3) after follow-up 1: Thursday 2026-11-05 at the window start.
        person = self.thread()
        seed_sent(self.db, person, at=ny(2, 8) - timedelta(days=3), seq=1)
        second = seed_message(self.db, person, seq=2, due_on="2026-11-02")
        verdict = self.verdicts_at()[second]
        self.assertEqual(verdict.reason, "followup_not_due")
        self.assertEqual(verdict.earliest_at, datetime(2026, 11, 5, 8, 0, tzinfo=ZoneInfo("America/New_York")))
        self.assertEqual(self.reason(second, now=datetime(2026, 11, 5, 9, 0, tzinfo=ZoneInfo("America/New_York"))),
                         "ready")

    def test_the_reason_has_words(self):
        self.assertIn("earlier_followup", queue.REASONS)
        self.assertNotIn("earlier_followup", queue.TIME_REASONS)
        self.assertEqual(queue.REASON_TEXT["earlier_followup"],
                         "An earlier follow-up in this thread has not gone out yet. This one goes after it.")


GMAIL = "devanshurajesh@gmail.com"
GENERAL = "devanshuchi9@gmail.com"
SPONSOR = "chicholikar.d@northeastern.edu"


def lanes_cfg(*, sponsor_sends: bool = True, **changes):
    """The scratch config with the shipped accounts at 15 a day: account 1 rests, the general Gmail sends and, unless
    `sponsor_sends` is False, the sponsor account sends too."""
    cfg = with_ramp(load_config(), ramp_start=15, ramp_step=3, ramp_floor=15, ramp_ceiling=40)
    accts = tuple(dataclasses.replace(a, sends=sponsor_sends) if a.address == SPONSOR else a for a in cfg.accounts)
    return dataclasses.replace(cfg, accounts=accts, **changes)


def add_fact(conn, company_id, fact, value):
    conn.execute("INSERT INTO evidence(company_id, fact, value, confidence, source_kind, observed_on, recorded_at) "
                 "VALUES (?, ?, ?, 'estimate', 'linkedin', '2026-11-01', '2026-11-01T12:00:00Z')",
                 (company_id, fact, value))


class AccountsQueueTest(Base):
    """The queue with every account in play (`accounts` given): each message gets the account `accounts.route`
    picks, and the daily limit, spacing, pause and sign-in are that account's."""

    def setUp(self):
        super().setUp()
        self.cfg = lanes_cfg()
        self.rows = _accounts.sync(self.db, self.cfg, "2026-11-02T12:00:00Z")
        self.ids = {a: r.account_id for a, r in self.rows.items()}
        self.sponsor_co = seed_company(self.db, "Sponsor Co")
        add_fact(self.db, self.sponsor_co, "everify", "enrolled")

    def found(self, now=MON, cfg=None, rows=None):
        cfg = cfg or self.cfg
        return {v.message_id: v for v in queue.verdicts(self.db, now, cfg, rows or self.rows, day=None, gate=None)}

    def sent_by(self, address, company, at, n=1):
        for _ in range(n):
            seed_sent(self.db, self.person(company=company), at=at, account_id=self.ids[address])

    def test_each_message_gets_its_account(self):
        _, general = self.approve()
        _, sponsor = self.approve(company=self.sponsor_co)
        # A follow-up stays on the account that sent its first email, whatever its company's lane.
        thread = self.person(company=self.sponsor_co)
        seed_sent(self.db, thread, at=ny(2, 8) - timedelta(days=7), account_id=self.ids[GENERAL])
        followup = seed_message(self.db, thread, seq=1, due_on="2026-11-02")
        found = self.found()
        self.assertEqual({m: found[m].account for m in (general, sponsor, followup)},
                         {general: GENERAL, sponsor: SPONSOR, followup: GENERAL})

    def test_daily_limit_per_account(self):
        # The general account has sent its 15 today; the sponsor account has sent none.
        for i in range(15):
            self.sent_by(GENERAL, seed_company(self.db, f"Sent{i}"), ny(2, 8, 10))
        _, general = self.approve()
        _, sponsor = self.approve(company=self.sponsor_co)
        found = self.found()
        self.assertEqual((found[general].reason, found[general].earliest_at), ("daily_limit", ny(3, 8, 0)))
        self.assertEqual(found[sponsor].reason, "ready")
        items, verdicts = queue.load_queue(self.db, self.cfg), list(found.values())
        self.assertIsNone(queue.pick(verdicts, items, now=MON, cfg=self.cfg, account=GENERAL))
        self.assertEqual(queue.pick(verdicts, items, now=MON, cfg=self.cfg, account=SPONSOR).message_id, sponsor)

    def test_a_claim_in_doubt_counts_only_against_its_own_account(self):
        for i in range(14):
            self.sent_by(GENERAL, seed_company(self.db, f"Sent{i}"), ny(2, 8, 10))
        _, general = self.approve()
        _, sponsor = self.approve(company=self.sponsor_co)
        self.assertEqual(self.found()[general].reason, "ready")
        person = self.person(company=seed_company(self.db, "InFlight"))
        claimed = seed_message(self.db, person)
        self.db.execute("UPDATE touch SET account_id = ? WHERE person_id = ?", (self.ids[GENERAL], person))
        self.db.execute("UPDATE message SET state = 'sending', pending_rfc_id = '<x@y.z>', claimed_at = ? "
                        "WHERE message_id = ?", (stamp(ny(2, 8, 45)), claimed))
        found = self.found()
        self.assertEqual((found[general].reason, found[sponsor].reason), ("daily_limit", "ready"))

    def test_sponsor_waits_when_edu_not_sending(self):
        cfg = lanes_cfg(sponsor_sends=False)
        rows = _accounts.sync(self.db, cfg, "2026-11-02T12:00:00Z")
        _, sponsor = self.approve(company=self.sponsor_co)
        _, general = self.approve()
        closed = seed_company(self.db, "Closed Sponsor")
        add_fact(self.db, closed, "everify", "enrolled")
        _, stopped = self.approve(company=closed)
        self.db.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?",
                        (closed,))
        found = self.found(cfg=cfg, rows=rows)
        self.assertEqual((found[sponsor].reason, found[sponsor].account, found[sponsor].earliest_at),
                         ("lane_waits", None, None))
        self.assertEqual(found[sponsor].text, f"Waits for {SPONSOR}, which does not send yet.")
        self.assertEqual(found[general].reason, "ready")
        self.assertEqual(found[stopped].reason, "company_closed")  # a stop still says stopped
        self.assertIn("lane_waits", queue.REASONS)
        self.assertNotIn("lane_waits", queue.TIME_REASONS | set(queue.STOP_REASONS))
        self.assertIsNone(queue.pick(list(found.values()), queue.load_queue(self.db, cfg), account=SPONSOR))

    def test_a_thread_on_an_account_that_is_not_sending_waits_and_names_it(self):
        # Account 1 rests: a follow-up in one of its threads cannot move to another account.
        resting = self.person()
        seed_sent(self.db, resting, at=ny(2, 8) - timedelta(days=7), account_id=self.ids[GMAIL])
        on_resting = seed_message(self.db, resting, seq=1, due_on="2026-11-02")
        # A thread whose account is not in play at all.
        gone = self.person(company=seed_company(self.db, "Gone"))
        seed_sent(self.db, gone, at=ny(2, 8) - timedelta(days=7), account_id=self.ids[SPONSOR])
        on_gone = seed_message(self.db, gone, seq=1, due_on="2026-11-02")
        rows = {a: r for a, r in self.rows.items() if a != SPONSOR}
        found = self.found(rows=rows)
        self.assertEqual((found[on_resting].reason, found[on_resting].account), ("lane_waits", None))
        self.assertEqual(found[on_resting].text, f"Waits: its thread is on {GMAIL}, which only reads now.")
        self.assertEqual((found[on_gone].reason, found[on_gone].account), ("lane_waits", None))
        self.assertEqual(found[on_gone].text, f"Waits: its thread is on {SPONSOR}, which is not set up to send now.")

    def test_a_thread_with_no_account_is_account_1s(self):
        # A first email sent before touches carried an account (NULL) went from account 1: its follow-up stays
        # there and does not move to the general account by lane or pin.
        person = self.person()
        seed_sent(self.db, person, at=ny(2, 8) - timedelta(days=7))
        followup = seed_message(self.db, person, seq=1, due_on="2026-11-02")
        _accounts.pin(self.db, self.company, self.ids[GENERAL], "rule", "2026-11-02T12:00:00Z")
        touch = self.touch_of(person)
        row, why = _accounts.route(self.db, self.cfg, touch, self.rows)
        self.assertEqual((row.account_id, why), (self.ids[GMAIL], "thread"))
        verdict = self.found()[followup]
        self.assertEqual((verdict.reason, verdict.account), ("lane_waits", None))
        self.assertEqual(verdict.text, f"Waits: its thread is on {GMAIL}, which only reads now.")

    def test_company_band_counts_every_account(self):
        cfg = lanes_cfg(per_company_daily=2)
        _, queued = self.approve()  # a general company in the 2 a day band
        self.sent_by(GENERAL, self.company, ny(2, 8, 5))  # one first email today
        now = ny(2, 11)
        self.assertEqual(self.found(now, cfg=cfg)[queued].reason, "ready")  # one more may go
        self.sent_by(GENERAL, self.company, ny(2, 9, 30))
        verdict = self.found(now, cfg=cfg)[queued]
        self.assertEqual((verdict.reason, verdict.account), ("per_company_limit", GENERAL))  # not two

    def test_first_emails_from_two_accounts_never_on_one_day(self):
        # The moment the pin moves from the resting account: it sent a first email to the company today, so the
        # general account's first email there waits for the next business day, though the band has room.
        cfg = lanes_cfg(per_company_daily=2)
        _, queued = self.approve()
        self.sent_by(GMAIL, self.company, ny(2, 8, 5))
        verdict = self.found(ny(2, 11), cfg=cfg)[queued]
        self.assertEqual((verdict.reason, verdict.earliest_at, verdict.account),
                         ("per_company_limit", ny(3, 8, 0), GENERAL))
        self.assertEqual(self.found(ny(3, 9), cfg=cfg)[queued].reason, "ready")  # a new day
        # A follow-up never waits for it: it goes from its own thread's account.
        thread = self.person()
        seed_sent(self.db, thread, at=ny(2, 8) - timedelta(days=7), account_id=self.ids[GENERAL])
        followup = seed_message(self.db, thread, seq=1, due_on="2026-11-02")
        self.assertEqual(self.found(ny(2, 11), cfg=cfg)[followup].reason, "ready")

    def test_a_first_email_in_doubt_or_at_a_merged_company_counts_for_its_account(self):
        cfg = lanes_cfg(per_company_daily=3)
        survivor, duplicate = seed_company(self.db, "Survivor"), seed_company(self.db, "Duplicate")
        self.merge(duplicate, survivor)
        _, queued = self.approve(company=survivor)
        # A first email to the duplicate, claimed today from the resting account and not settled yet.
        person = self.person(company=duplicate)
        claimed = seed_message(self.db, person)
        self.db.execute("UPDATE touch SET account_id = ? WHERE person_id = ?", (self.ids[GMAIL], person))
        self.db.execute("UPDATE message SET state = 'sending', pending_rfc_id = '<x@y.z>', claimed_at = ? "
                        "WHERE message_id = ?", (stamp(ny(2, 8, 5)), claimed))
        self.db.execute("UPDATE message SET state = 'send_unknown' WHERE message_id = ?", (claimed,))
        self.assertEqual(self.found(ny(2, 11), cfg=cfg)[queued].reason, "per_company_limit")

    def test_window_0800_1500_recipient_time(self):
        _, la = self.approve(tz="America/Los_Angeles")
        pt = ZoneInfo("America/Los_Angeles")
        self.assertEqual(self.found(datetime(2026, 11, 2, 7, 59, tzinfo=pt))[la].reason, "window")
        self.assertEqual(self.found(datetime(2026, 11, 2, 8, 0, tzinfo=pt))[la].reason, "ready")
        self.assertEqual(self.found(datetime(2026, 11, 2, 15, 1, tzinfo=pt))[la].reason, "window")

    def test_holiday_2026_11_26_waits(self):
        cfg = lanes_cfg(holidays=load_config(SHIPPED_SEND_CONFIG).holidays)
        self.assertIn(date(2026, 11, 26), cfg.holidays)
        _, general = self.approve()
        verdict = self.found(ny(26, 10), cfg=cfg)[general]
        self.assertEqual(verdict.reason, "not_business_day")
        self.assertEqual(verdict.earliest_at, ny(30, 8, 0))  # the 27th is a holiday too

    def unread(self, *addresses, now=MON):
        """The verdicts while the reads of these accounts are incomplete or failed."""
        held = frozenset(self.ids[a] for a in addresses)
        return {v.message_id: v for v in queue.verdicts(self.db, now, self.cfg, self.rows, day=None, gate=None,
                                                        unread=held)}

    def test_failed_read_on_account_1_holds_only_its_companies(self):
        # Company A was emailed from account 1; its new first email goes from the general account.
        seed_sent(self.db, self.person(), at=ny(2, 8) - timedelta(days=7), account_id=self.ids[GMAIL])
        _, at_a = self.approve()
        # Company B has nothing on account 1 (its own queued touch has no account yet).
        b = seed_company(self.db, "B")
        _, at_b = self.approve(company=b)
        # Company C has a touch sent before touches carried an account: its thread is account 1's.
        c = seed_company(self.db, "C")
        seed_sent(self.db, self.person(company=c), at=ny(2, 8) - timedelta(days=7))
        _, at_c = self.approve(company=c)
        # Company E's duplicate D was emailed from account 1: the family counts.
        d, e = seed_company(self.db, "D"), seed_company(self.db, "E")
        seed_sent(self.db, self.person(company=d), at=ny(2, 8) - timedelta(days=7), account_id=self.ids[GMAIL])
        self.merge(d, e)
        _, at_e = self.approve(company=e)
        # Company F was emailed only from the general account.
        f = seed_company(self.db, "F")
        seed_sent(self.db, self.person(company=f), at=ny(2, 8) - timedelta(days=7), account_id=self.ids[GENERAL])
        _, at_f = self.approve(company=f)
        clear = self.found()
        self.assertEqual({m: clear[m].reason for m in (at_a, at_b, at_c, at_e, at_f)},
                         dict.fromkeys((at_a, at_b, at_c, at_e, at_f), "ready"))
        found = self.unread(GMAIL)
        for held in (at_a, at_c, at_e):
            self.assertEqual((found[held].reason, found[held].account, found[held].earliest_at),
                             ("inbound_unread", GENERAL, None), held)
            self.assertEqual(found[held].text, f"Waits: replies on {GMAIL} could not be read yet.")
        self.assertEqual((found[at_b].reason, found[at_f].reason), ("ready", "ready"))
        self.assertEqual(queue.pick(list(found.values()), queue.load_queue(self.db, self.cfg), now=MON,
                                    cfg=self.cfg, account=GENERAL).message_id, at_b)

    def test_an_unread_account_holds_its_own_messages(self):
        _, general = self.approve()
        _, sponsor = self.approve(company=self.sponsor_co)
        found = self.unread(GENERAL)
        self.assertEqual((found[general].reason, found[general].account), ("inbound_unread", GENERAL))
        self.assertEqual(found[general].text, f"Waits: replies on {GENERAL} could not be read yet.")
        self.assertEqual(found[sponsor].reason, "ready")
        self.assertIsNone(queue.pick(list(found.values()), queue.load_queue(self.db, self.cfg), account=GENERAL))
        # A stop still says stopped.
        self.db.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?",
                        (self.sponsor_co,))
        self.assertEqual(self.unread(GENERAL, SPONSOR)[sponsor].reason, "company_closed")
        self.assertIn("inbound_unread", queue.REASONS)
        self.assertNotIn("inbound_unread", queue.TIME_REASONS | set(queue.STOP_REASONS))
        self.assertTrue(queue.REASON_TEXT["inbound_unread"])

    def test_every_caller_names_the_accounts_and_the_picking_account(self):
        # There is no one-account path any more: the accounts in play and the account that picks are required.
        self.approve()
        with self.assertRaises(TypeError):
            queue.verdicts(self.db, MON, self.cfg, day=None, gate=None)
        found = queue.verdicts(self.db, MON, self.cfg, self.rows, day=None, gate=None)
        with self.assertRaises(TypeError):
            queue.pick(found, queue.load_queue(self.db, self.cfg))

    def test_spacing_per_account(self):
        later = ny(2, 9, 30)
        self.db.execute("UPDATE sender_account SET next_send_at = ? WHERE account_id = ?",
                        (stamp(later), self.ids[GENERAL]))
        _, general = self.approve()
        _, sponsor = self.approve(company=self.sponsor_co)
        found = self.found()
        self.assertEqual((found[general].reason, found[general].earliest_at), ("spacing", later))
        self.assertEqual(found[sponsor].reason, "ready")

    def test_the_day_start_is_seeded_by_account(self):
        cfg = lanes_cfg(day_start_jitter_minutes=25)
        # Account 1 keeps the offset it had before accounts; the others get their own.
        self.assertEqual(human.day_start_offset(MON_DAY, cfg, 1), human.day_start_offset(MON_DAY, cfg))
        days = [MON_DAY + timedelta(days=i) for i in range(10)]
        self.assertNotEqual([human.day_start_offset(d, cfg, self.ids[GENERAL]) for d in days],
                            [human.day_start_offset(d, cfg, self.ids[SPONSOR]) for d in days])
        _, general = self.approve()
        _, sponsor = self.approve(company=self.sponsor_co)
        opens = ny(2, 8)
        found = self.found(opens, cfg=cfg)
        self.assertEqual((found[general].reason, found[general].earliest_at),
                         ("spacing", opens + human.day_start_offset(MON_DAY, cfg, self.ids[GENERAL])))
        self.assertEqual((found[sponsor].reason, found[sponsor].earliest_at),
                         ("spacing", opens + human.day_start_offset(MON_DAY, cfg, self.ids[SPONSOR])))

    def test_an_account_pause_or_sign_out_holds_only_that_account(self):
        _, general = self.approve()
        _, sponsor = self.approve(company=self.sponsor_co)
        _accounts.hard_pause(self.db, self.ids[GENERAL], "Gmail said no", "2026-11-02T13:00:00Z")
        found = self.found()
        self.assertEqual((found[general].reason, found[sponsor].reason), ("sender_paused", "ready"))
        self.db.execute("UPDATE sender_account SET auth_state = 'signed_out' WHERE account_id = ?",
                        (self.ids[SPONSOR],))
        self.assertEqual(self.found()[sponsor].reason, "signed_out")

    def test_a_signed_out_wait_names_its_account(self):
        # One account signed out while another is fine: the row says which account, not that Gmail is signed out.
        _, general = self.approve()
        _, sponsor = self.approve(company=self.sponsor_co)
        self.db.execute("UPDATE sender_account SET auth_state = 'signed_out' WHERE account_id = ?",
                        (self.ids[GENERAL],))
        found = self.found()
        self.assertEqual((found[general].reason, found[general].text),
                         ("signed_out", f"Waits: {GENERAL} is not signed in."))
        self.assertEqual((found[sponsor].reason, found[sponsor].text), ("ready", None))
        # The tick's own sign-in (gate_accounts) names it the same way.
        self.db.execute("UPDATE sender_account SET auth_state = 'ok'")
        gated = {v.message_id: v for v in queue.gate_accounts(list(self.found().values()), {SPONSOR: "signed_out"})}
        self.assertEqual((gated[sponsor].reason, gated[sponsor].text),
                         ("signed_out", f"Waits: {SPONSOR} is not signed in."))
        self.assertEqual((gated[general].reason, gated[general].text), ("ready", None))

    def test_his_pause_holds_every_account_and_a_stop_still_says_stopped(self):
        _, general = self.approve()
        _, sponsor = self.approve(company=self.sponsor_co)
        closed = seed_company(self.db, "Closed")
        _, stopped = self.approve(company=closed)
        self.db.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?",
                        (closed,))
        self.db.execute("UPDATE sender SET paused = 1, pause_kind = 'by_you', paused_reason = 'by you', "
                        "paused_at = '2026-11-02T13:00:00Z' WHERE sender_id = 1")
        found = self.found()
        self.assertEqual({m: found[m].reason for m in (general, sponsor, stopped)},
                         {general: "sender_paused", sponsor: "sender_paused", stopped: "company_closed"})

    def test_minutes_left_counts_only_the_accounts_own_recipients(self):
        self.approve()  # New York, general account
        self.approve(company=self.sponsor_co, tz="America/Los_Angeles")
        self.assertEqual(queue.minutes_left(self.db, MON, self.cfg, GENERAL, self.rows), 360.0)  # to 15:00 NY
        self.assertEqual(queue.minutes_left(self.db, MON, self.cfg, SPONSOR, self.rows), 540.0)  # to 18:00 NY
        found = queue.verdicts(self.db, MON, self.cfg, self.rows, day=None, gate=None)
        items = queue.load_queue(self.db, self.cfg)
        self.assertEqual(queue.minutes_left_for(found, items, MON, self.cfg, account=GENERAL), 360.0)

    def test_window_minutes_gives_minutes_left_and_the_full_day_per_account(self):
        # The full day runs from 08:00 New York to the latest close among the account's recipients that can go today.
        self.approve()  # New York, general account
        self.approve(company=self.sponsor_co, tz="America/Los_Angeles")
        self.assertEqual(queue.window_minutes(self.db, MON, self.cfg, GENERAL, self.rows), (360.0, 420.0))
        self.assertEqual(queue.window_minutes(self.db, MON, self.cfg, SPONSOR, self.rows), (540.0, 600.0))
        found = queue.verdicts(self.db, MON, self.cfg, self.rows, day=None, gate=None)
        items = queue.load_queue(self.db, self.cfg)
        self.assertEqual(queue.day_minutes_for(found, items, MON, self.cfg, account=SPONSOR), 600.0)
        self.assertEqual(queue.day_minutes_for([], [], MON, self.cfg), 420.0)

    def test_first_only_keeps_the_account(self):
        _, general = self.approve()
        _, other = self.approve(company=seed_company(self.db, "Other"))
        found = {v.message_id: v for v in queue.first_only(list(self.found().values()))}
        self.assertEqual((found[general].reason, found[other].reason), ("ready", "message_id_unproven"))
        self.assertEqual((found[general].account, found[other].account), (GENERAL, GENERAL))
