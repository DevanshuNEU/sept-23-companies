import unittest
from datetime import date

from outreach.plan.dates import business_days_between, is_weekday, last_seven_days, ny_date, today_ny, utc_window

TZ = "America/New_York"


class PlanDatesTest(unittest.TestCase):
    def test_ny_date_month_boundary(self):
        self.assertEqual(ny_date("2026-11-01T03:30:00Z", TZ), date(2026, 10, 31))
        self.assertEqual(ny_date("2026-11-01T05:30:00Z", TZ), date(2026, 11, 1))
        self.assertEqual(ny_date("2026-11-02T04:59:00Z", TZ), date(2026, 11, 1))
        self.assertEqual(today_ny("2026-11-01T03:30:00Z", TZ), date(2026, 10, 31))

    def test_last_seven_days_is_today_and_the_six_before_it(self):
        self.assertEqual(last_seven_days(date(2026, 11, 10)), (date(2026, 11, 4), date(2026, 11, 10)))
        self.assertEqual(last_seven_days(date(2026, 11, 3)), (date(2026, 10, 28), date(2026, 11, 3)))  # across a month
        self.assertEqual(last_seven_days(date(2027, 1, 2)), (date(2026, 12, 27), date(2027, 1, 2)))

    def test_business_days_between(self):
        self.assertEqual(business_days_between(date(2026, 11, 6), date(2026, 11, 9)), 1)
        self.assertEqual(business_days_between(date(2026, 11, 2), date(2026, 11, 9)), 5)
        self.assertEqual(business_days_between(date(2026, 11, 9), date(2026, 11, 9)), 0)
        self.assertEqual(business_days_between(date(2026, 11, 9), date(2026, 11, 2)), 0)

    def test_utc_window_covers_the_new_york_days(self):
        self.assertEqual(utc_window(date(2026, 10, 1), date(2026, 10, 31), TZ),
                         ("2026-10-01T04:00:00Z", "2026-11-01T04:00:00Z"))
        # seven days across the clocks falling back (1 November): EDT at the start, EST at the end
        self.assertEqual(utc_window(*last_seven_days(date(2026, 11, 3)), TZ),
                         ("2026-10-28T04:00:00Z", "2026-11-04T05:00:00Z"))

    def test_weekday(self):
        self.assertTrue(is_weekday(date(2026, 11, 6)))
        self.assertFalse(is_weekday(date(2026, 11, 7)))


if __name__ == "__main__":
    unittest.main()
