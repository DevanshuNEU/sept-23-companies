"""The pure helpers of the real-data ranking check (#6, Task 15): band slicing and band statistics, on synthetic
rows, so the numbers the check asserts on cannot be miscomputed."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests.ranking_realcheck import band, band_stats, middle_start


def row(score, lanes=(), reasons=(), level="high", basis="evidence", stale=0):
    conf = SimpleNamespace(level=level, basis=basis)
    return SimpleNamespace(score=score, lanes=tuple(lanes), reasons=tuple(reasons), confidence=conf,
                           rank_reason=SimpleNamespace(confidence=conf, line="x"), stale_postings=(None,) * stale)


class BandTest(unittest.TestCase):
    def test_band_is_a_slice(self):
        rows = list(range(10))
        self.assertEqual(band(rows, 2, 3), [2, 3, 4])
        self.assertEqual(band(rows, 8, 5), [8, 9])
        self.assertEqual(band(rows, 20, 5), [])

    def test_middle_start_centres_on_the_median_rank(self):
        self.assertEqual(middle_start(1000, 50), 475)
        self.assertEqual(middle_start(101, 50), 25)
        self.assertEqual(middle_start(30, 50), 0)
        self.assertEqual(middle_start(0, 50), 0)


class BandStatsTest(unittest.TestCase):
    def test_empty_band(self):
        s = band_stats([])
        self.assertEqual(s["count"], 0)
        self.assertEqual(s["level_role_share"], 0.0)
        self.assertEqual(s["mean_score"], 0.0)
        self.assertEqual(s["median_reasons"], 0)
        self.assertEqual(s["low_share"], 0.0)

    def test_shares_mean_and_median(self):
        rows = [row(90, ("level_role",), ("a", "b", "c"), "high"),
                row(70, ("level_role", "intern_junior"), ("a", "b"), "medium"),
                row(40, ("any_us_role",), ("a",), "low"),
                row(20, (), (), "low")]
        s = band_stats(rows)
        self.assertEqual(s["count"], 4)
        self.assertEqual(s["level_role_share"], 0.5)
        self.assertEqual(s["mean_score"], 55.0)
        self.assertEqual(s["median_reasons"], 1.5)
        self.assertEqual(s["confidence"], {"high": 1, "medium": 1, "low": 2})
        self.assertEqual(s["low_share"], 0.5)

    def test_median_of_an_odd_count(self):
        s = band_stats([row(1, reasons=("a",) * n) for n in (5, 1, 3)])
        self.assertEqual(s["median_reasons"], 3)

    def test_risk_reasons_are_counted_by_company(self):
        rows = [row(1, reasons=("Staffing-firm risk high: x", "Small employer")),
                row(1, reasons=("Large later-stage company: ranked last, never hidden (x)",)),
                row(1, reasons=("Staffing-firm risk medium: x", "Large later-stage company: y")),
                row(1, reasons=("Small employer",))]
        self.assertEqual(band_stats(rows)["risk_rows"], 3)

    def test_rows_without_a_level_or_basis_are_counted(self):
        rows = [row(1), row(1, level="sure"), row(1, basis="  "), row(1, level="")]
        self.assertEqual(band_stats(rows)["bad_reason_rows"], 3)

    def test_stale_ignored_total(self):
        self.assertEqual(band_stats([row(1, stale=2), row(1, stale=1), row(1)])["stale_ignored"], 3)


if __name__ == "__main__":
    unittest.main()
