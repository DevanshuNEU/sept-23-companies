import dataclasses
import random
import statistics
import unittest
from datetime import date, datetime, timedelta, timezone

from outreach.sending import human
from outreach.sending.config import load_config
from tests import SHIPPED_SEND_CONFIG

CFG = load_config(SHIPPED_SEND_CONFIG)
T0 = datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc)


class MiddleRng:
    """An rng whose every draw is the middle: no break (random 0.5 is above the break chance), a factor of 1.0."""

    def random(self):
        return 0.5

    def uniform(self, low, high):
        return (low + high) / 2


class SpreadGapTest(unittest.TestCase):
    def test_spread_gap_formula(self):
        self.assertEqual(human.spread_gap(420, 15, MiddleRng(), CFG), 26.25)

    def test_spread_gap_clipped(self):
        low, high = CFG.spread_gap_minutes
        self.assertEqual(human.spread_gap(10, 15, MiddleRng(), CFG), low)
        self.assertEqual(human.spread_gap(420, 2, MiddleRng(), CFG), high)

    def test_nothing_left_gives_the_high_clip(self):
        self.assertEqual(human.spread_gap(420, 0, MiddleRng(), CFG), CFG.spread_gap_minutes[1])
        self.assertEqual(human.spread_gap(420, -3, random.Random(1), CFG), CFG.spread_gap_minutes[1])

    def test_the_factor_runs_across_the_jitter_range(self):
        rng = random.Random(1)
        gaps = [human.spread_gap(420, 15, rng, dataclasses.replace(CFG, break_chance=0.0001)) for _ in range(3000)]
        low, high = CFG.spread_jitter
        self.assertTrue(all(26.25 * low - 1e-9 <= g <= 26.25 * high + 1e-9 for g in gaps if g < 40))
        self.assertGreater(len({round(g) for g in gaps}), 10)

    def test_same_seed_same_gaps(self):
        def draws(seed):
            rng = random.Random(seed)
            return [human.spread_gap(300, 10, rng, CFG) for _ in range(50)]
        self.assertEqual(draws(7), draws(7))
        self.assertNotEqual(draws(7), draws(8))

    def test_a_break_is_in_break_minutes(self):
        always = dataclasses.replace(CFG, break_chance=0.999999)
        low, high = CFG.break_minutes
        for seed in range(20):
            self.assertTrue(low <= human.spread_gap(420, 15, random.Random(seed), always) <= high)

    def test_the_expected_gap_is_the_formula_at_factor_one(self):
        self.assertEqual(human.expected_gap_minutes(420, 15, CFG), 26.25)
        self.assertEqual(human.expected_gap_minutes(10, 15, CFG), CFG.spread_gap_minutes[0])
        self.assertEqual(human.expected_gap_minutes(420, 0, CFG), CFG.spread_gap_minutes[1])

    def test_a_late_start_sends_fewer_not_faster(self):
        # 30 minutes left and 14 sends left would be 2 minute gaps; the full day's pace (a 420 minute window over a
        # limit of 15, plus one: 26.25) times the slack is the floor before the factor.
        self.assertEqual(CFG.spread_floor_slack, 0.75)
        floor = 0.75 * 420 / 16
        self.assertEqual(human.spread_gap(30, 14, MiddleRng(), CFG, day_minutes=420, limit=15), floor)
        self.assertEqual(human.expected_gap_minutes(30, 14, CFG, day_minutes=420, limit=15), floor)
        whole = dataclasses.replace(CFG, spread_floor_slack=0.9999)
        self.assertAlmostEqual(human.spread_gap(30, 14, MiddleRng(), whole, day_minutes=420, limit=15), 26.25, places=2)
        # Early in the day the minutes left over the sends left is the larger, and it still rules.
        self.assertEqual(human.spread_gap(300, 9, MiddleRng(), CFG, day_minutes=420, limit=15), 30.0)
        self.assertEqual(human.expected_gap_minutes(300, 9, CFG, day_minutes=420, limit=15), 30.0)
        # The factor and the clip still apply on top of the floor.
        rng = random.Random(3)
        no_breaks = dataclasses.replace(CFG, break_chance=0.0)
        gaps = [human.spread_gap(30, 14, rng, no_breaks, day_minutes=420, limit=15) for _ in range(2000)]
        self.assertTrue(all(floor * 0.6 - 1e-9 <= g <= floor * 1.4 + 1e-9 for g in gaps))

    def test_the_mean_draw_is_the_expected_gap(self):
        rng = random.Random(5)
        no_breaks = dataclasses.replace(CFG, break_chance=0.0001)
        gaps = [human.spread_gap(420, 15, rng, no_breaks) for _ in range(20000)]
        self.assertAlmostEqual(statistics.fmean(gaps), human.expected_gap_minutes(420, 15, CFG), delta=0.5)


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
