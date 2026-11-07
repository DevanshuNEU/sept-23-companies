import dataclasses
import unittest
from collections import Counter
from datetime import date, datetime, timedelta

from outreach.sending import calendar as cal
from outreach.sending import human, projection, queue, ramp
from outreach.sending.config import load_config
from outreach.sending import status
from tests.sendtest import prove, put_day
from tests.storetest import seed_company
from tests.test_sending_queue import LA, NY, Base, ny, stamp

# A floor of 1 so a test can set a day's limit as low as it likes: the floor lifts any lower row (#9, 2026-11-06).
CFG = dataclasses.replace(load_config(), ramp_floor=1)
FRI = ny(6, 13, 6)  # Friday 2026-11-06, 13:06 New York


def day_of(at: datetime) -> date:
    return at.astimezone(NY).date()


class ProjectionTest(Base):
    def project(self, now=FRI):
        day = ramp.open_day(self.db, now, CFG)
        return projection.project(queue.prepare(self.db, now, CFG, day=day), now, CFG)

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
        self.assertEqual(self.project(ny(6, 17))[mid], ny(9, 8, 30))

    def test_a_weekend_projects_to_monday_at_open(self):
        _, mid = self.approve()
        self.assertEqual(self.project(ny(7, 11))[mid], ny(9, 8, 30))

    def test_spacing_separates_different_companies_by_the_mean_gap(self):
        mids = [self.approve(company=seed_company(self.db, f"Co{i}"))[1] for i in range(3)]
        got = self.project()
        mean = timedelta(minutes=human.mean_gap_minutes(CFG))
        self.assertEqual([got[m] for m in mids], [FRI, FRI + mean, FRI + 2 * mean])

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
        self.assertEqual(got.astimezone(LA), datetime(2026, 11, 9, 8, 30, tzinfo=LA))

    def test_a_follow_up_waits_for_its_due_day(self):
        _, mid = self.approve_followup("2026-11-11")
        self.assertGreaterEqual(self.project()[mid], ny(11, 8, 30))

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
        self.db.execute("UPDATE sender SET next_send_at = ? WHERE sender_id = 1", (stamp(soon),))
        verdict = queue.verdicts(self.db, FRI, CFG, day=None, gate=None)[0]
        self.assertEqual((verdict.reason, verdict.earliest_at), ("spacing", soon))
        self.assertEqual(self.project()[mid], soon)

    def test_more_than_the_horizon_leaves_the_rest_unprojected(self):
        for _ in range(100):
            self.approve()
        got = self.project()
        self.assertTrue(0 < len(got) < 100)
        self.assertLessEqual(max(got.values()), FRI + timedelta(days=projection.MAX_DAYS))


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
        self.db.execute("UPDATE sender SET auth_state = 'signed_out' WHERE sender_id = 1")
        self.assertEqual(status.queue_projection(self.db, FRI, CFG), ({}, False))

    def test_the_old_sender_running_projects_nothing(self):
        self.assertEqual(status.queue_projection(self.db, FRI, CFG, old_sender_loaded=True), ({}, False))


if __name__ == "__main__":
    unittest.main()
