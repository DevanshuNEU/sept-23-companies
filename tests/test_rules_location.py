"""US location (issue #4, Task 10)."""
import time
import unittest

from outreach.rules.config import load_config
from outreach.rules.location import us_status

CASES = [  # (location, country, expected)
    ("Indianapolis, IN", None, "yes"), ("Columbus, Indiana", None, "yes"),
    ("Albuquerque, New Mexico", None, "yes"), ("Lima, OH", None, "yes"),
    ("Paris, TX", None, "yes"), ("Athens, GA", None, "yes"),
    ("San Francisco, CA | London, UK", None, "yes"), ("Remote (US)", None, "yes"), ("New York", None, "yes"),
    ("Paris, France", None, "no"), ("London, UK", None, "no"), ("Toronto, ON, Canada", None, "no"),
    ("Remote - EMEA", None, "no"), ("Bengaluru, India", None, "no"),
    ("Remote", None, "unknown"), ("", None, "unknown"),
    ("Remote", "United States", "yes"), ("Austin, TX", "Canada", "no"),
    ("Tel Aviv - IL", None, "no"), ("Madrid, MD, Spain", None, "no"), ("Munich, DE", None, "no"),
    ("Toronto, CA", None, "no"), ("Tbilisi, Georgia", None, "no"), ("Atlanta, Georgia", None, "yes"),
    ("Paris", None, "no"),
]


class UsStatusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()

    def test_cases(self):
        for loc, country, expected in CASES:
            with self.subTest(location=loc, country=country):
                self.assertEqual(us_status(loc, country, self.cfg), expected)

    def test_lowercase_state_words_are_not_codes(self):
        self.assertEqual(us_status("Remote in Berlin", None, self.cfg), "no")

    def test_structured_country_spellings(self):
        for country in ("US", "USA", "United States", "United States of America", " usa "):
            with self.subTest(country=country):
                self.assertEqual(us_status("Toronto, Canada", country, self.cfg), "yes")
        self.assertEqual(us_status("Austin, TX", "", self.cfg), "yes")  # empty country: read the location
        self.assertEqual(us_status("Austin, TX", "  ", self.cfg), "yes")
        self.assertEqual(us_status("Remote", "Germany", self.cfg), "no")

    def test_or_splits_only_lower_case(self):
        # "OR" is Oregon; only a lower-case " or " separates places.
        self.assertEqual(us_status("Portland, OR", None, self.cfg), "yes")
        self.assertEqual(us_status("Portland, OR | Toronto", None, self.cfg), "yes")
        self.assertEqual(us_status("Toronto or Austin, TX", None, self.cfg), "yes")
        self.assertEqual(us_status("Toronto; Paris", None, self.cfg), "no")

    def test_state_code_only_after_a_comma_or_at_the_end(self):
        self.assertEqual(us_status("Austin TX", None, self.cfg), "yes")
        self.assertEqual(us_status("Austin, TX 78701", None, self.cfg), "yes")
        self.assertEqual(us_status("IN office, hybrid", None, self.cfg), "unknown")

    def test_long_location_is_fast(self):
        loc = ("Paris, " * 20000) + "New " * 20000 + "x" + " " * 50000 + "CA" + " " * 50000 + "y"
        start = time.perf_counter()
        us_status(loc, None, self.cfg)
        self.assertLess(time.perf_counter() - start, 2.0)


if __name__ == "__main__":
    unittest.main()
