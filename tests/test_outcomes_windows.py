import unittest

from outreach.outcomes import windows


class WindowsTest(unittest.TestCase):
    def test_week_is_monday_to_sunday_in_new_york(self):
        # Wednesday 2026-10-21 in New York.
        self.assertEqual(windows.week_bounds("2026-10-21T15:00:00Z"), ("2026-10-19", "2026-10-25"))
        # A Monday and a Sunday belong to their own week.
        self.assertEqual(windows.week_bounds("2026-10-19T12:00:00Z"), ("2026-10-19", "2026-10-25"))
        self.assertEqual(windows.week_bounds("2026-10-25T12:00:00Z"), ("2026-10-19", "2026-10-25"))
        # Sunday 23:30 in New York is already Monday in UTC: still last week here.
        self.assertEqual(windows.week_bounds("2026-10-26T03:30:00Z"), ("2026-10-19", "2026-10-25"))

    def test_month_bounds_cover_the_whole_new_york_month(self):
        self.assertEqual(windows.month_bounds("2026-10-21T15:00:00Z"), ("2026-10-01", "2026-10-31"))
        self.assertEqual(windows.month_bounds("2026-02-10T15:00:00Z"), ("2026-02-01", "2026-02-28"))
        self.assertEqual(windows.month_bounds("2028-02-10T15:00:00Z"), ("2028-02-01", "2028-02-29"))
        self.assertEqual(windows.month_bounds("2026-12-31T20:00:00Z"), ("2026-12-01", "2026-12-31"))
        # 2026-11-01 02:00Z is still 31 October evening in New York.
        self.assertEqual(windows.month_bounds("2026-11-01T02:00:00Z"), ("2026-10-01", "2026-10-31"))

    def test_a_late_evening_utc_time_is_still_the_previous_new_york_day(self):
        self.assertEqual(windows.ny_day("2026-10-22T02:30:00Z"), "2026-10-21")
        self.assertEqual(windows.ny_day("2026-10-22T04:00:00Z"), "2026-10-22")   # EDT: midnight is 04:00Z
        self.assertEqual(windows.ny_day("2026-12-22T04:59:59Z"), "2026-12-21")   # EST: midnight is 05:00Z
        self.assertEqual(windows.ny_day("2026-12-22T05:00:00Z"), "2026-12-22")

    def test_ny_day_reads_offsets_and_bare_dates(self):
        self.assertEqual(windows.ny_day("2026-10-21T23:30:00-04:00"), "2026-10-21")
        self.assertEqual(windows.ny_day("2026-10-21T23:30:00+00:00"), "2026-10-21")

    def test_day_bounds_are_local_midnight_to_local_midnight_in_utc(self):
        self.assertEqual(windows.day_bounds_utc("2026-10-21"),
                         ("2026-10-21T04:00:00Z", "2026-10-22T04:00:00Z"))
        self.assertEqual(windows.day_bounds_utc("2026-12-21"),
                         ("2026-12-21T05:00:00Z", "2026-12-22T05:00:00Z"))

    def test_week_across_the_november_dst_change_has_the_right_utc_bounds(self):
        # Clocks go back on Sunday 2026-11-01: that Sunday has 25 hours.
        first, last = windows.week_bounds("2026-10-28T15:00:00Z")
        self.assertEqual((first, last), ("2026-10-26", "2026-11-01"))
        start, _ = windows.day_bounds_utc(first)
        _, end = windows.day_bounds_utc(last)
        self.assertEqual((start, end), ("2026-10-26T04:00:00Z", "2026-11-02T05:00:00Z"))
        sunday = windows.day_bounds_utc("2026-11-01")
        self.assertEqual(sunday, ("2026-11-01T04:00:00Z", "2026-11-02T05:00:00Z"))
        # Spring forward has a 23 hour day.
        self.assertEqual(windows.day_bounds_utc("2026-03-08"),
                         ("2026-03-08T05:00:00Z", "2026-03-09T04:00:00Z"))

    def test_the_first_week_after_the_change_starts_at_the_new_offset(self):
        first, last = windows.week_bounds("2026-11-03T15:00:00Z")
        self.assertEqual((first, last), ("2026-11-02", "2026-11-08"))
        self.assertEqual(windows.day_bounds_utc(first)[0], "2026-11-02T05:00:00Z")

    def test_next_weekday_skips_the_weekend(self):
        self.assertEqual(windows.next_weekday("2026-10-23"), "2026-10-26")   # Friday gives Monday
        self.assertEqual(windows.next_weekday("2026-10-24"), "2026-10-26")   # Saturday
        self.assertEqual(windows.next_weekday("2026-10-25"), "2026-10-26")   # Sunday
        self.assertEqual(windows.next_weekday("2026-10-21"), "2026-10-22")   # Wednesday gives Thursday

    def test_a_bad_date_is_a_value_error(self):
        with self.assertRaises(ValueError):
            windows.day_bounds_utc("not a date")
        with self.assertRaises(ValueError):
            windows.next_weekday("2026-13-40")


if __name__ == "__main__":
    unittest.main()
