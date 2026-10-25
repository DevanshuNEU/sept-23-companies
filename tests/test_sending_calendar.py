import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from outreach.sending import calendar as cal
from outreach.sending.config import load_config

NY = ZoneInfo("America/New_York")
LA = ZoneInfo("America/Los_Angeles")
CFG = load_config()


def utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def local(y, mo, d, h, mi, zone=NY) -> datetime:
    """A moment given in a zone's wall clock, as aware UTC."""
    return datetime(y, mo, d, h, mi, tzinfo=zone).astimezone(timezone.utc)


class BusinessDayTest(unittest.TestCase):
    def test_add_business_days_examples(self):
        # Thursday 2026-10-22 plus 7 business days is Monday 2026-11-02.
        self.assertEqual(cal.add_business_days(date(2026, 10, 22), 7, CFG), date(2026, 11, 2))
        self.assertEqual(cal.add_business_days(date(2026, 11, 2), 7, CFG), date(2026, 11, 11))
        self.assertEqual(cal.add_business_days(date(2026, 11, 6), 1, CFG), date(2026, 11, 9))

    def test_add_zero_business_days_is_the_day_itself_even_on_a_weekend(self):
        self.assertEqual(cal.add_business_days(date(2026, 11, 7), 0, CFG), date(2026, 11, 7))

    def test_a_weekend_start_counts_from_the_next_business_day(self):
        # Saturday plus 1 is Monday: the start day never counts.
        self.assertEqual(cal.add_business_days(date(2026, 11, 7), 1, CFG), date(2026, 11, 9))

    def test_negative_business_days_are_refused(self):
        with self.assertRaises(ValueError):
            cal.add_business_days(date(2026, 11, 2), -1, CFG)

    def test_holiday_is_skipped_when_configured(self):
        cfg = load_config_with_holidays(date(2026, 11, 26))
        self.assertEqual(cal.add_business_days(date(2026, 11, 25), 1, cfg), date(2026, 11, 27))
        self.assertFalse(cal.is_business_day(date(2026, 11, 26), cfg))
        self.assertTrue(cal.is_business_day(date(2026, 11, 26), CFG))

    def test_is_business_day_follows_the_weekdays(self):
        self.assertTrue(cal.is_business_day(date(2026, 11, 6), CFG))   # Friday
        self.assertFalse(cal.is_business_day(date(2026, 11, 7), CFG))  # Saturday
        self.assertFalse(cal.is_business_day(date(2026, 11, 8), CFG))  # Sunday

    def test_business_days_between_counts_after_a_up_to_b(self):
        thu, fri, sat, mon = date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 7), date(2026, 11, 9)
        self.assertEqual(cal.business_days_between(thu, fri, CFG), 1)
        self.assertEqual(cal.business_days_between(fri, mon, CFG), 1)
        self.assertEqual(cal.business_days_between(fri, sat, CFG), 0)
        self.assertEqual(cal.business_days_between(thu, mon, CFG), 2)
        self.assertEqual(cal.business_days_between(date(2026, 10, 22), date(2026, 11, 2), CFG), 7)

    def test_business_days_between_is_zero_when_b_is_not_after_a(self):
        d = date(2026, 11, 4)
        self.assertEqual(cal.business_days_between(d, d, CFG), 0)
        self.assertEqual(cal.business_days_between(date(2026, 11, 9), d, CFG), 0)

    def test_between_and_add_agree(self):
        start = date(2026, 10, 30)
        for n in range(0, 15):
            end = cal.add_business_days(start, n, CFG)
            self.assertEqual(cal.business_days_between(start, end, CFG), n)


def load_config_with_holidays(*days):
    import dataclasses
    return dataclasses.replace(CFG, holidays=tuple(days))


class NewYorkDateTest(unittest.TestCase):
    def test_ny_date_of_late_evening_utc(self):
        self.assertEqual(cal.ny_date(utc("2026-11-03T03:30:00Z"), CFG), date(2026, 11, 2))

    def test_ny_date_changes_at_local_midnight_on_both_sides_of_the_dst_change(self):
        # EDT (UTC-4) on 2026-10-31, EST (UTC-5) on 2026-11-02.
        self.assertEqual(cal.ny_date(utc("2026-11-01T03:59:59Z"), CFG), date(2026, 10, 31))
        self.assertEqual(cal.ny_date(utc("2026-11-01T04:00:00Z"), CFG), date(2026, 11, 1))
        self.assertEqual(cal.ny_date(utc("2026-11-02T04:59:59Z"), CFG), date(2026, 11, 1))
        self.assertEqual(cal.ny_date(utc("2026-11-02T05:00:00Z"), CFG), date(2026, 11, 2))

    def test_ny_date_needs_an_aware_time(self):
        with self.assertRaises(ValueError):
            cal.ny_date(datetime(2026, 11, 2, 12, 0), CFG)

    def test_ny_day_bounds_on_the_25_hour_day(self):
        self.assertEqual(cal.ny_day_bounds(date(2026, 11, 1), CFG),
                         ("2026-11-01T04:00:00Z", "2026-11-02T05:00:00Z"))

    def test_ny_day_bounds_on_a_normal_day_and_the_23_hour_day(self):
        self.assertEqual(cal.ny_day_bounds(date(2026, 11, 2), CFG),
                         ("2026-11-02T05:00:00Z", "2026-11-03T05:00:00Z"))
        # Spring forward: 2026-03-08 has 23 hours.
        self.assertEqual(cal.ny_day_bounds(date(2026, 3, 8), CFG),
                         ("2026-03-08T05:00:00Z", "2026-03-09T04:00:00Z"))


class WindowTest(unittest.TestCase):
    def test_unknown_zone_name_is_none(self):
        self.assertIsNone(cal.recipient_zone("Mars/Olympus_Mons"))
        self.assertIsNone(cal.recipient_zone(""))
        self.assertIsNone(cal.recipient_zone("../etc/passwd"))
        self.assertEqual(cal.recipient_zone("America/Chicago"), ZoneInfo("America/Chicago"))

    def test_window_edges(self):
        monday = (2026, 11, 2)
        self.assertFalse(cal.in_window(local(*monday, 8, 29), NY, CFG))
        self.assertTrue(cal.in_window(local(*monday, 8, 30), NY, CFG))
        self.assertTrue(cal.in_window(local(*monday, 16, 30), NY, CFG))
        self.assertFalse(cal.in_window(local(*monday, 16, 31), NY, CFG))
        self.assertFalse(cal.in_window(local(2026, 11, 7, 10, 0), NY, CFG))  # Saturday
        self.assertFalse(cal.in_window(local(2026, 11, 8, 10, 0), NY, CFG))  # Sunday

    def test_window_uses_recipient_time_across_the_dst_change(self):
        # After 2026-11-01 New York is on EST (UTC-5) and Los Angeles on PST (UTC-8).
        at = utc("2026-11-02T21:00:00Z")  # 16:00 in New York, 13:00 in Los Angeles
        self.assertTrue(cal.in_window(at, NY, CFG))
        self.assertTrue(cal.in_window(at, LA, CFG))
        at = utc("2026-11-02T22:00:00Z")  # 17:00 in New York, 14:00 in Los Angeles
        self.assertFalse(cal.in_window(at, NY, CFG))
        self.assertTrue(cal.in_window(at, LA, CFG))

    def test_window_shifts_by_an_hour_in_the_week_daylight_saving_ends(self):
        # The same 20:30Z is 16:30 in New York on EDT (Friday 2026-10-30) and 15:30 on EST (Monday 2026-11-02).
        self.assertTrue(cal.in_window(utc("2026-10-30T20:30:00Z"), NY, CFG))
        self.assertFalse(cal.in_window(utc("2026-10-30T20:31:00Z"), NY, CFG))
        self.assertTrue(cal.in_window(utc("2026-11-02T21:30:00Z"), NY, CFG))
        self.assertFalse(cal.in_window(utc("2026-11-02T21:31:00Z"), NY, CFG))
        # 08:30 opens at 12:30Z on EDT and at 13:30Z on EST.
        self.assertTrue(cal.in_window(utc("2026-10-30T12:30:00Z"), NY, CFG))
        self.assertFalse(cal.in_window(utc("2026-11-02T12:30:00Z"), NY, CFG))
        self.assertTrue(cal.in_window(utc("2026-11-02T13:30:00Z"), NY, CFG))

    def test_the_local_date_decides_the_weekday_not_the_utc_date(self):
        # Friday 17:00 in Los Angeles is Saturday 01:00Z; Monday 00:30Z is Sunday evening in Los Angeles.
        self.assertFalse(cal.in_window(utc("2026-11-07T01:00:00Z"), LA, CFG))
        self.assertTrue(cal.in_window(utc("2026-11-06T20:00:00Z"), LA, CFG))  # Friday 12:00 in LA
        self.assertFalse(cal.in_window(utc("2026-11-09T00:30:00Z"), LA, CFG))

    def test_a_holiday_is_out_of_window(self):
        cfg = load_config_with_holidays(date(2026, 11, 26))
        self.assertFalse(cal.in_window(local(2026, 11, 26, 10, 0), NY, cfg))

    def test_next_window_start_inside_a_window_is_the_time_itself(self):
        at = local(2026, 11, 2, 10, 0)
        self.assertEqual(cal.next_window_start(at, NY, CFG), at)

    def test_next_window_start_from_friday_evening_is_monday_0830(self):
        at = local(2026, 11, 6, 18, 0)
        self.assertEqual(cal.next_window_start(at, NY, CFG), local(2026, 11, 9, 8, 30))
        self.assertEqual(cal.next_window_start(at, NY, CFG).tzinfo, timezone.utc)

    def test_next_window_start_before_opening_is_the_same_day(self):
        self.assertEqual(cal.next_window_start(local(2026, 11, 3, 7, 0), NY, CFG), local(2026, 11, 3, 8, 30))

    def test_next_window_start_on_a_weekend_is_monday(self):
        self.assertEqual(cal.next_window_start(local(2026, 11, 7, 9, 0), NY, CFG), local(2026, 11, 9, 8, 30))

    def test_next_window_start_after_close_is_the_next_business_day(self):
        self.assertEqual(cal.next_window_start(local(2026, 11, 3, 16, 31), NY, CFG), local(2026, 11, 4, 8, 30))

    def test_next_window_start_across_the_dst_change_uses_the_new_offset(self):
        # Saturday 2026-10-31 evening in New York is still EDT; Monday 08:30 is EST, so 13:30Z.
        got = cal.next_window_start(local(2026, 10, 31, 20, 0), NY, CFG)
        self.assertEqual(got, utc("2026-11-02T13:30:00Z"))

    def test_next_window_start_for_a_pacific_recipient_while_new_york_is_closed(self):
        at = utc("2026-11-02T22:00:00Z")  # New York 17:00: closed. Los Angeles 14:00: open.
        self.assertEqual(cal.next_window_start(at, LA, CFG), at)
        self.assertEqual(cal.next_window_start(at, NY, CFG), local(2026, 11, 3, 8, 30))

    def test_next_window_start_skips_a_holiday(self):
        cfg = load_config_with_holidays(date(2026, 11, 3))
        self.assertEqual(cal.next_window_start(local(2026, 11, 2, 17, 0), NY, cfg), local(2026, 11, 4, 8, 30))

    def test_the_result_is_always_in_window_and_never_earlier(self):
        start = local(2026, 10, 29, 0, 0)
        for hours in range(0, 24 * 10):
            at = start + timedelta(hours=hours, minutes=17)
            for zone in (NY, LA):
                nxt = cal.next_window_start(at, zone, CFG)
                self.assertGreaterEqual(nxt, at)
                self.assertTrue(cal.in_window(nxt, zone, CFG), (at, zone, nxt))


class WindowResultZoneTest(unittest.TestCase):
    def test_next_window_start_in_the_window_comes_back_as_utc(self):
        inside = datetime(2026, 11, 2, 10, 0, tzinfo=NY)
        result = cal.next_window_start(inside, NY, CFG)
        self.assertEqual(result, inside)
        self.assertIs(result.tzinfo, timezone.utc)


if __name__ == "__main__":
    unittest.main()
