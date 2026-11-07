import random
import statistics
import unittest
from datetime import date, datetime, timedelta, timezone

from outreach.sending import human
from outreach.sending.config import load_config
from tests import SHIPPED_SEND_CONFIG

CFG = load_config(SHIPPED_SEND_CONFIG)
T0 = datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc)


class GapTest(unittest.TestCase):
    def draws(self, n=20000, seed=1):
        rng = random.Random(seed)
        return [human.next_gap_minutes(rng, CFG) for _ in range(n)]

    def test_same_seed_same_gaps(self):
        self.assertEqual(self.draws(50, seed=7), self.draws(50, seed=7))
        self.assertNotEqual(self.draws(50, seed=7), self.draws(50, seed=8))

    def test_every_gap_is_a_clipped_gap_or_a_break(self):
        low, high = CFG.gap_minutes
        b_low, b_high = CFG.break_minutes
        for g in self.draws(5000):
            self.assertTrue(low <= g <= high or b_low <= g <= b_high, g)

    def test_gaps_are_not_flat(self):
        gaps = self.draws()
        self.assertGreater(statistics.pstdev(gaps), 2.0)  # uniform 4 to 9 minutes has a spread of 1.4
        self.assertGreater(len({round(g) for g in gaps}), 15)  # many different whole-minute gaps
        self.assertTrue(any(g < 2 for g in gaps) and any(g > 12 for g in gaps))  # quick ones and real pauses

    def test_the_mean_is_what_the_projection_uses(self):
        gaps = self.draws()
        self.assertAlmostEqual(statistics.fmean(gaps), human.mean_gap_minutes(CFG), delta=0.5)

    def test_a_day_of_sends_fits_the_window(self):
        # 100 sends in an 8 hour window need an average gap under 4.8 minutes, tick spacing included (up to 2 more).
        self.assertLess(human.mean_gap_minutes(CFG) + 1, 4.8)


class ChooseTest(unittest.TestCase):
    def test_picks_only_from_the_first_few(self):
        rng = random.Random(3)
        seen = {human.choose(list(range(20)), rng, 5) for _ in range(500)}
        self.assertEqual(seen, {0, 1, 2, 3, 4})

    def test_a_short_list_and_an_empty_one(self):
        self.assertEqual(human.choose([9], random.Random(1), 5), 9)
        self.assertIsNone(human.choose([], random.Random(1), 5))

    def test_a_pool_of_one_is_first_come_first_served(self):
        self.assertEqual({human.choose([4, 5, 6], random.Random(s), 1) for s in range(50)}, {4})


class FixedJitterTest(unittest.TestCase):
    def test_day_start_offset_is_stable_and_varies_by_day(self):
        offsets = {human.day_start_offset(date(2026, 11, 2) + timedelta(days=i), CFG) for i in range(20)}
        self.assertGreater(len(offsets), 10)
        for off in offsets:
            self.assertTrue(timedelta(0) <= off < timedelta(minutes=CFG.day_start_jitter_minutes), off)
        self.assertEqual(human.day_start_offset(date(2026, 11, 6), CFG), human.day_start_offset(date(2026, 11, 6), CFG))

    def test_company_gap_is_at_least_the_floor_and_never_over_the_max(self):
        floor = timedelta(minutes=CFG.per_company_gap_minutes)
        gaps = {human.company_gap(c, T0 + timedelta(minutes=m), CFG) for c in range(1, 6) for m in range(0, 50, 7)}
        self.assertGreater(len(gaps), 20)
        for gap in gaps:
            self.assertTrue(floor <= gap <= human.max_company_gap(CFG), gap)

    def test_company_gap_is_stable_for_one_send(self):
        self.assertEqual(human.company_gap(4, T0, CFG), human.company_gap(4, T0, CFG))
        self.assertNotEqual(human.company_gap(4, T0, CFG), human.company_gap(5, T0, CFG))


if __name__ == "__main__":
    unittest.main()
