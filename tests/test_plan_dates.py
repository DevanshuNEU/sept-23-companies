import unittest
from datetime import date

from outreach.plan.dates import (business_days_between, is_weekday, month_range, ny_date, today_ny, utc_window,
                                 weekdays_left)

TZ = "America/New_York"


class PlanDatesTest(unittest.TestCase):
    def test_ny_date_month_boundary(self):
        self.assertEqual(ny_date("2026-11-01T03:30:00Z", TZ), date(2026, 10, 31))
        self.assertEqual(ny_date("2026-11-01T05:30:00Z", TZ), date(2026, 11, 1))
        self.assertEqual(ny_date("2026-11-02T04:59:00Z", TZ), date(2026, 11, 1))
        self.assertEqual(today_ny("2026-11-01T03:30:00Z", TZ), date(2026, 10, 31))

    def test_weekdays_left(self):
        for day, want in ((date(2026, 11, 2), 21), (date(2026, 11, 10), 15), (date(2026, 10, 30), 1),
                          (date(2026, 11, 8), 16), (date(2026, 10, 31), 0)):
            self.assertEqual(weekdays_left(day), want, day)

    def test_business_days_between(self):
        self.assertEqual(business_days_between(date(2026, 11, 6), date(2026, 11, 9)), 1)
        self.assertEqual(business_days_between(date(2026, 11, 2), date(2026, 11, 9)), 5)
        self.assertEqual(business_days_between(date(2026, 11, 9), date(2026, 11, 9)), 0)
        self.assertEqual(business_days_between(date(2026, 11, 9), date(2026, 11, 2)), 0)

    def test_utc_window_covers_the_new_york_month(self):
        self.assertEqual(utc_window(*month_range(date(2026, 10, 14)), TZ),
                         ("2026-10-01T04:00:00Z", "2026-11-01T04:00:00Z"))
        self.assertEqual(utc_window(*month_range(date(2026, 11, 14)), TZ),
                         ("2026-11-01T04:00:00Z", "2026-12-01T05:00:00Z"))

    def test_month_range_and_weekday(self):
        self.assertEqual(month_range(date(2026, 2, 10)), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertTrue(is_weekday(date(2026, 11, 6)))
        self.assertFalse(is_weekday(date(2026, 11, 7)))


if __name__ == "__main__":
    unittest.main()
