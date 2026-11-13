import dataclasses
import unittest
from collections import Counter
from datetime import date, datetime, timedelta

from outreach.sending import accounts as _accounts
from outreach.sending import calendar as cal
from outreach.sending import human, projection, queue, ramp
from outreach.sending.config import load_config
from tests import SHIPPED_SEND_CONFIG
from outreach.sending import status
from outreach.sending.accounts import in_play as _in_play
from tests.sendtest import prove, put_day, seed_message, seed_sent, with_ramp, solo
from tests.storetest import seed_company
from tests.test_sending_queue import LA, NY, Base, ny, stamp

# A floor of 1 so a test can set a day's limit as low as it likes: the floor lifts any lower row (#9, 2026-11-06).
CFG = with_ramp(solo(load_config()), ramp_floor=1)
FRI = ny(6, 11, 6)  # Friday 2026-11-06, 11:06 New York (three company slots 60 minutes apart fit before the 15:00 close)


def day_of(at: datetime) -> date:
    return at.astimezone(NY).date()


class ProjectionTest(Base):
    def project(self, now=FRI):
        day = ramp.open_day(self.db, _accounts.primary(self.db, CFG), now, CFG)
        return projection.project(queue.prepare(self.db, now, CFG, _in_play(self.db, CFG), day=day), now, CFG)

    def test_nothing_approved_projects_nothing(self):
        self.assertEqual(self.project(), {})

    def test_ten_at_one_company_spread_three_a_day(self):
        ids = [self.approve()[1] for _ in range(10)]
        got = self.project()
        self.assertEqual(len(got), 10)
        self.assertEqual(sorted(Counter(day_of(got[i]) for i in ids).items()),
                         [(date(2026, 11, 6), 3), (date(2026, 11, 9), 3), (date(2026, 11, 10), 3),
                          (date(2026, 11, 11), 1)])
        times = sorted(got.values())
        for a, b in zip(times, times[1:]):
            if day_of(a) == day_of(b):
                self.assertGreaterEqual(b - a, timedelta(minutes=CFG.per_company_gap_minutes))
        self.assertEqual(got[ids[0]], FRI)

    def test_after_the_window_the_first_goes_on_the_next_business_day_at_open(self):
        _, mid = self.approve()
        self.assertEqual(self.project(ny(6, 17))[mid], ny(9, 8, 0))

    def test_a_weekend_projects_to_monday_at_open(self):
        _, mid = self.approve()
        self.assertEqual(self.project(ny(7, 11))[mid], ny(9, 8, 0))

    def test_spacing_separates_different_companies_by_the_expected_spread_gap(self):
        mids = [self.approve(company=seed_company(self.db, f"Co{i}"))[1] for i in range(3)]
        got = self.project()
        # Each gap is the spread rule at factor 1.0: the minutes left until the window closes (15:00 New York, the
        # recipients are in New York) over the sends left today, clipped.
        limit = queue.prepare(self.db, FRI, CFG, _in_play(self.db, CFG), day=None).limit
        first = timedelta(minutes=human.expected_gap_minutes(
            queue.minutes_left_for([], [], FRI, CFG), limit - 1, CFG))
        self.assertEqual(got[mids[0]], FRI)
        self.assertEqual(got[mids[1]], FRI + first)
        second = timedelta(minutes=human.expected_gap_minutes(
            queue.minutes_left_for([], [], FRI + first, CFG), limit - 2, CFG))
        self.assertEqual(got[mids[2]], FRI + first + second)

    def test_daily_limit_holds_flat_for_later_days(self):
        put_day(self.db, date(2026, 11, 6), 4)
        self.sent_at(seed_company(self.db, "Earlier"), ny(6, 9))
        mids = [self.approve(company=seed_company(self.db, f"Co{i}"))[1] for i in range(9)]
        got = self.project()
        self.assertEqual(sorted(Counter(day_of(got[m]) for m in mids).items()),
                         [(date(2026, 11, 6), 3), (date(2026, 11, 9), 4), (date(2026, 11, 10), 2)])

    def test_a_pacific_recipient_projects_inside_their_own_window(self):
        _, mid = self.approve(tz="America/Los_Angeles")
        got = self.project(ny(6, 20))[mid]  # 17:00 in Los Angeles: closed
        self.assertEqual(got.astimezone(LA), datetime(2026, 11, 9, 8, 0, tzinfo=LA))

    def test_a_follow_up_waits_for_its_due_day(self):
        _, mid = self.approve_followup("2026-11-11")
        self.assertGreaterEqual(self.project()[mid], ny(11, 8, 0))

    def test_recruiter_goes_after_the_others_at_the_company(self):
        _, recruiter = self.approve(role="recruiter", approved_at="2026-11-06T10:00:00Z")
        _, one = self.approve(approved_at="2026-11-06T11:00:00Z")
        _, two = self.approve(approved_at="2026-11-06T12:00:00Z")
        got = self.project()
        self.assertGreater(got[recruiter], max(got[one], got[two]))

    def test_unsendable_messages_are_not_projected(self):
        paused, closed = seed_company(self.db, "Paused"), seed_company(self.db, "Closed")
        self.db.execute("UPDATE company SET status = 'working', paused_at = '2026-11-01T12:00:00Z' "
                        "WHERE company_id = ?", (paused,))
        self.db.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?", (closed,))
        _, a = self.approve(company=paused)
        _, b = self.approve(company=closed)
        _, ok = self.approve()
        self.assertEqual(set(self.project()), {ok})

    def test_first_projection_matches_the_sender_verdict_now(self):
        _, mid = self.approve()
        self.assertEqual(self.project()[mid], FRI)
        soon = FRI + timedelta(minutes=3)
        self.db.execute("UPDATE sender_account SET next_send_at = ? WHERE account_id = 1", (stamp(soon),))
        verdict = queue.verdicts(self.db, FRI, CFG, _in_play(self.db, CFG), day=None, gate=None)[0]
        self.assertEqual((verdict.reason, verdict.earliest_at), ("spacing", soon))
        self.assertEqual(self.project()[mid], soon)

    def test_more_than_the_horizon_leaves_the_rest_unprojected(self):
        for _ in range(100):
            self.approve()
        got = self.project()
        self.assertTrue(0 < len(got) < 100)
        self.assertLessEqual(max(got.values()), FRI + timedelta(days=projection.MAX_DAYS))


class ShippedPacingTest(Base):
    def test_a_mixed_zone_queue_projects_the_earliest_close_first(self):
        _, la = self.approve(company=seed_company(self.db, "West"), tz="America/Los_Angeles")  # approved first
        _, east = self.approve(company=seed_company(self.db, "East"))
        got = self_project(self.db, FRI, CFG)
        self.assertEqual(got[east], FRI)
        self.assertGreater(got[la], FRI)

    def test_the_gap_after_a_placement_follows_the_ready_recipients_only(self):
        # One LA follow-up due next week does not stretch the New York day to the Los Angeles close.
        _, a = self.approve(company=seed_company(self.db, "A"))
        _, b = self.approve(company=seed_company(self.db, "B"))
        la = self.person(company=seed_company(self.db, "West"), tz="America/Los_Angeles")
        seed_sent(self.db, la, at=ny(2, 8) - timedelta(days=7))
        seed_message(self.db, la, seq=1, due_on="2026-11-13")
        cfg = dataclasses.replace(CFG, spread_gap_minutes=(2, 45))  # the shipped clip (the scratch one is [2, 3])
        got = self_project(self.db, FRI, cfg)
        limit = queue.prepare(self.db, FRI, cfg, _in_play(self.db, cfg), day=None).limit
        expected = 234 / (limit - 1 + 1)  # 11:06 to 15:00 is 234 minutes
        self.assertAlmostEqual((got[b] - got[a]).total_seconds() / 60, expected, places=3)

    def test_shipped_pacing_literal_gap_with_a_follow_up_sent_today(self):
        cfg = dataclasses.replace(with_ramp(solo(load_config(SHIPPED_SEND_CONFIG)), ramp_start=16, ramp_floor=15),
                                  day_start_jitter_minutes=0, pick_pool=1, per_company_gap_jitter_minutes=0)
        person = self.person(company=seed_company(self.db, "Done"))
        seed_sent(self.db, person, at=ny(6, 8) - timedelta(days=7))
        seed_sent(self.db, person, at=ny(6, 8, 30), seq=1)  # a follow-up already sent today
        _, a = self.approve(company=seed_company(self.db, "A"))
        _, b = self.approve(company=seed_company(self.db, "B"))
        got = self_project(self.db, FRI, cfg)
        # 16 a day, 1 sent, 1 placed: 14 left, 15 intervals; 11:06 to 15:00 in New York is 234 minutes: 15.6 each.
        self.assertEqual(got[a], FRI)
        self.assertAlmostEqual((got[b] - got[a]).total_seconds(), 15.6 * 60, places=3)


def self_project(db, now, cfg):
    day = ramp.open_day(db, _accounts.primary(db, cfg), now, cfg)
    return projection.project(queue.prepare(db, now, cfg, _in_play(db, cfg), day=day), now, cfg)


class StatusProjectionTest(Base):
    def setUp(self):
        super().setUp()
        prove(self.db)
        _, self.mid = self.approve()

    def test_a_pause_still_projects_and_says_so(self):
        self.db.execute("UPDATE sender SET paused = 1, pause_kind = 'by_you', paused_reason = 'by you', "
                        "paused_at = '2026-11-06T12:00:00Z' WHERE sender_id = 1")
        got, paused = status.queue_projection(self.db, FRI, CFG)
        self.assertEqual((got, paused), ({self.mid: FRI}, True))

    def test_signed_out_projects_nothing(self):
        self.db.execute("UPDATE sender_account SET auth_state = 'signed_out' WHERE account_id = 1")
        self.assertEqual(status.queue_projection(self.db, FRI, CFG), ({}, False))

    def test_the_old_sender_running_projects_nothing(self):
        self.assertEqual(status.queue_projection(self.db, FRI, CFG, old_sender_loaded=True), ({}, False))


class PerAccountProjectionTest(Base):
    """With every account in play, each sending account's queue is projected with its own limit and spacing."""

    def test_each_sending_account_projects_its_own_queue(self):
        from tests.test_sending_queue import GENERAL, SPONSOR, add_fact, lanes_cfg
        cfg = lanes_cfg()
        rows = _accounts.sync(self.db, cfg, "2026-11-06T12:00:00Z")
        sponsor_co = seed_company(self.db, "Sponsor Co")
        add_fact(self.db, sponsor_co, "everify", "enrolled")
        general = [self.approve(company=seed_company(self.db, f"G{i}"))[1] for i in range(3)]
        sponsor = [self.approve(company=sponsor_co)[1]]
        got = projection.project(queue.prepare(self.db, FRI, cfg, rows, day=None), FRI, cfg)
        # Both accounts send their first at once: the sponsor account does not wait out the general one's gap.
        self.assertEqual((got[general[0]], got[sponsor[0]]), (FRI, FRI))
        gap = timedelta(minutes=human.expected_gap_minutes(queue.minutes_left_for([], [], FRI, cfg), 14, cfg))
        self.assertEqual(got[general[1]], FRI + gap)
        # The general account's 15 do not use the sponsor account's day: at its limit, the sponsor one still goes.
        for i in range(15):
            seed_sent(self.db, self.person(company=seed_company(self.db, f"S{i}")), at=ny(6, 8, 10),
                      account_id=rows[GENERAL].account_id)
        got = projection.project(queue.prepare(self.db, FRI, cfg, rows, day=None), FRI, cfg)
        self.assertEqual(got[sponsor[0]], FRI)
        self.assertEqual(got[general[0]], ny(9, 8, 0))
        self.assertEqual(set(queue.prepare(self.db, FRI, cfg, rows, day=None).senders), {GENERAL, SPONSOR})
        # A hard block on the sponsor account still projects, as if he resumed now; a signed-out account does not.
        _accounts.hard_pause(self.db, rows[SPONSOR].account_id, "Gmail said no", "2026-11-06T15:00:00Z")
        self.assertEqual(projection.project(queue.prepare(self.db, FRI, cfg, rows, day=None), FRI, cfg)[sponsor[0]],
                         FRI)
        self.db.execute("UPDATE sender_account SET auth_state = 'signed_out' WHERE account_id = ?",
                        (rows[SPONSOR].account_id,))
        self.assertNotIn(sponsor[0], projection.project(queue.prepare(self.db, FRI, cfg, rows, day=None), FRI, cfg))


if __name__ == "__main__":
    unittest.main()
