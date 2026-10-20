import re
import unittest

from outreach.store.timefmt import iso_date, iso_utc, utcnow

CASES = [
    ("2026-07-30T16:30:19Z", "2026-07-30T16:30:19Z"),
    ("2026-07-30T16:30:19.242+00:00", "2026-07-30T16:30:19Z"),
    ("2026-07-28T22:06:03.425Z", "2026-07-28T22:06:03Z"),
    ("2026-07-29T22:26:46-04:00", "2026-07-30T02:26:46Z"),
    ("2026-07-30T16:30:19", "2026-07-30T16:30:19Z"),
    ("2026-07-30", "2026-07-30T00:00:00Z"),
]


class IsoUtcTest(unittest.TestCase):
    def test_the_four_wire_formats(self):
        self.assertEqual(iso_utc("2026-07-30T16:30:19Z"), "2026-07-30T16:30:19Z")
        self.assertEqual(iso_utc("2026-07-30T16:30:19.242+00:00"), "2026-07-30T16:30:19Z")
        self.assertEqual(iso_utc("2026-07-28T22:06:03.425Z"), "2026-07-28T22:06:03Z")

    def test_local_offset_crosses_midnight(self):
        self.assertEqual(iso_utc("2026-07-29T22:26:46-04:00"), "2026-07-30T02:26:46Z")

    def test_naive_is_utc(self):
        self.assertEqual(iso_utc("2026-07-30T16:30:19"), "2026-07-30T16:30:19Z")

    def test_bare_date_is_midnight(self):
        self.assertEqual(iso_utc("2026-07-30"), "2026-07-30T00:00:00Z")

    def test_unparseable_raises(self):
        with self.assertRaises(ValueError):
            iso_utc("last Tuesday")

    def test_empty_is_none(self):
        self.assertIsNone(iso_utc(None))
        self.assertIsNone(iso_utc(""))

    def test_idempotent(self):
        for raw, want in CASES:
            with self.subTest(raw=raw):
                self.assertEqual(iso_utc(want), want)
                self.assertEqual(iso_utc(iso_utc(raw)), iso_utc(raw))

    def test_all_cases_match_canonical_shape(self):
        for raw, want in CASES:
            with self.subTest(raw=raw):
                self.assertEqual(iso_utc(raw), want)


class UtcNowTest(unittest.TestCase):
    def test_utcnow_shape(self):
        self.assertRegex(utcnow(), r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
        self.assertIsNotNone(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", utcnow()))


class IsoDateTest(unittest.TestCase):
    def test_iso_prefix(self):
        self.assertEqual(iso_date("2026-08-26T10:00:00Z"), "2026-08-26")
        self.assertEqual(iso_date("2026-08-26"), "2026-08-26")

    def test_epoch_milliseconds(self):
        self.assertEqual(iso_date(1756200000000), "2025-08-26")
        self.assertEqual(iso_date("1756200000000"), "2025-08-26")

    def test_epoch_seconds(self):
        self.assertEqual(iso_date("1756200000"), "2025-08-26")
        self.assertEqual(iso_date(1756200000), "2025-08-26")
        self.assertEqual(iso_date(1756200000.0), "2025-08-26")

    def test_unreadable_is_none(self):
        self.assertIsNone(iso_date("soon"))
        self.assertIsNone(iso_date(None))
        self.assertIsNone(iso_date(""))
        self.assertIsNone(iso_date(float("nan")))
        self.assertIsNone(iso_date(10**30))


if __name__ == "__main__":
    unittest.main()
