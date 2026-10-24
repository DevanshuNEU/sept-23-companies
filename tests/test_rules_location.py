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
    ("Remote", "United States", "yes"), ("Austin, TX", "Canada", "yes"),  # ruling 2026-10-21: US text wins
    ("Tel Aviv - IL", None, "no"), ("Madrid, MD, Spain", None, "no"), ("Munich, DE", None, "no"),
    ("Toronto, CA", None, "no"), ("Tbilisi, Georgia", None, "no"), ("Atlanta, Georgia", None, "yes"),
    ("Paris", None, "no"),
]


# Final fix wave, item 7 (#4): a structured country that is neither a US mark nor a known non-US country is not a "no";
# the location text decides.
COUNTRY_CASES = [  # (location, country, expected)
    ("Remote", "United States (Remote)", "yes"), ("", "US-Remote", "yes"), ("Remote", "Puerto Rico", "yes"),
    ("San Juan", "Puerto Rico", "yes"), ("", "Guam", "yes"),
    ("Boston, MA", "North America", "yes"), ("", "North America", "unknown"), ("Remote", "North America", "unknown"),
    ("Toronto, Canada", "North America", "no"),
    ("Moscow", "Russia", "unknown"), ("Atlanta, Georgia", "Georgia", "yes"), ("Tbilisi, Georgia", "Georgia", "no"),
    ("Austin, TX", "Remote", "yes"),
    ("Remote", "Canada", "no"), ("Remote", "Germany", "no"), ("Boston, MA", "Germany", "yes"),
    ("Remote", "Canada (Remote)", "no"), ("Remote", "United Kingdom", "no"),
    ("Remote", "us", "yes"), ("Toronto", "United States", "yes"),
]

# Final fix wave 2, item 6 (#4), Devanshu's ruling 2026-10-21: a US location in the text wins over a structured non-US
# country (a multi-location posting carries one country, often the primary office). A wrong show is cheap.
TEXT_BEATS_COUNTRY = [  # (location, country, expected)
    ("Remote - US or Canada", "Canada", "yes"), ("New York, NY; Toronto, ON", "Canada", "yes"),
    ("Remote (US)", "India", "yes"), ("Bangalore or Remote US", "India", "yes"),
    # no US place in the text: the structured country still decides
    ("Toronto, ON", "Canada", "no"), ("Remote", "India", "no"), ("Bangalore", "India", "no"), ("", "Canada", "no"),
]

# #4 follow-up 2026-10-23: a title that names a non-US country as a whole segment and no US place reads "no", but
# only when the location text and country give nothing ("unknown"). Whole segments, so "Jordan Lake" is no country.
TITLE_CASES = [  # (title, location, country, expected)
    ("Software Engineer II (Venezuela)", "", None, "no"),
    ("Software Engineer II (Venezuela)", "Remote", None, "no"),
    ("Software Engineer II (Venezuela)", "Remote", "Remote", "no"),
    ("Software Engineer - Remote Canada", "", None, "no"),
    ("Backend Engineer, India", "", None, "no"),
    ("Backend Engineer (UK)", "", None, "no"),
    ("Backend Engineer | Mexico", "", None, "no"),
    # a US place anywhere in the title, or a location that says yes, keeps the posting in
    ("Software Engineer (US)", "", None, "unknown"),
    ("Software Engineer, Remote (USA or Canada)", "", None, "unknown"),
    ("Software Engineer, Remote (Canada or US)", "", None, "unknown"),
    ("Software Engineer (Mexico, New York)", "", None, "unknown"),
    ("Software Engineer (India) - Austin", "", None, "unknown"),
    ("Software Engineer II (Venezuela)", "Austin, TX", None, "yes"),
    ("Software Engineer II (Venezuela)", "", "United States", "yes"),
    # names that are also a country or a US place, never a whole segment on their own
    ("Engineer, Georgia Tech", "", None, "unknown"),
    ("Software Engineer, Georgia", "", None, "unknown"),
    ("Jordan Lake Software Engineer", "", None, "unknown"),
    ("Software Engineer - Jordan", "", None, "unknown"),
    ("Software Engineer, India Hicks Brand", "", None, "unknown"),
    ("Software Engineer, Chile Pepper Labs", "", None, "unknown"),
    ("Software Engineer (Peru, IN)", "", None, "unknown"),
    ("Software Engineer", "", None, "unknown"),
    # a known non-US location still says no, a US one still says yes, with or without a title
    ("Software Engineer", "Toronto, Canada", None, "no"),
    ("Software Engineer", "Austin, TX", None, "yes"),
]


class UsStatusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()

    def test_cases(self):
        for loc, country, expected in CASES:
            with self.subTest(location=loc, country=country):
                self.assertEqual(us_status(loc, country, self.cfg), expected)

    def test_title_cases(self):
        for title, loc, country, expected in TITLE_CASES:
            with self.subTest(title=title, location=loc, country=country):
                self.assertEqual(us_status(loc, country, self.cfg, title=title), expected)

    def test_without_a_title_nothing_changes(self):
        self.assertEqual(us_status("Remote", None, self.cfg), "unknown")
        self.assertEqual(us_status("Remote", None, self.cfg, title=None), "unknown")

    def test_an_unrecognised_country_falls_back_to_the_location(self):
        for loc, country, expected in COUNTRY_CASES:
            with self.subTest(location=loc, country=country):
                self.assertEqual(us_status(loc, country, self.cfg), expected)

    def test_a_us_location_in_the_text_beats_a_non_us_country(self):
        for loc, country, expected in TEXT_BEATS_COUNTRY:
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

    def test_a_us_mark_beats_a_non_us_country(self):
        # Ruling R12: naming the US outright reads yes next to another country.
        for loc in ("Remote - US & Canada", "US, Canada", "United States or Canada", "USA / Mexico",
                    "Remote (U.S. or Canada)", "Canada, United States of America"):
            with self.subTest(location=loc):
                self.assertEqual(us_status(loc, None, self.cfg), "yes")
        # A state code, state name or US city next to a non-US country still reads no.
        for loc in ("Toronto, CA", "Madrid, MD, Spain", "Tbilisi, Georgia", "Munich, DE", "Austin, Canada",
                    "Texas, Mexico"):
            with self.subTest(location=loc):
                self.assertEqual(us_status(loc, None, self.cfg), "no")

    def test_bare_us_mark_is_upper_case_only(self):
        self.assertEqual(us_status("Remote for us", None, self.cfg), "unknown")
        self.assertEqual(us_status("Join us in Paris", None, self.cfg), "no")
        self.assertEqual(us_status("Remote (US)", None, self.cfg), "yes")
        self.assertEqual(us_status("Remote, usa", None, self.cfg), "yes")  # only the two-letter mark is case-sensitive
        self.assertEqual(us_status("Remote (CA)", None, self.cfg), "yes")
        self.assertEqual(us_status("Remote", "us", self.cfg), "yes")  # a structured country is not prose

    def test_long_location_is_fast(self):
        loc = ("Paris, " * 20000) + "New " * 20000 + "x" + " " * 50000 + "CA" + " " * 50000 + "y"
        # CPU time, not wall time: scheduler wait on a loaded machine is not the code's cost.
        start = time.process_time()
        us_status(loc, None, self.cfg)
        self.assertLess(time.process_time() - start, 2.0)


if __name__ == "__main__":
    unittest.main()
