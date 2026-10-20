"""The 8 misfires of the old system (docs/research/2026-10-17-current-system-audit.md section 2) as regression tests.

Every case goes through decide(), the one function every posting goes through. Corpus counts are in each docstring.
"""
import unittest

from outreach.rules import PostingInput, decide, passes
from outreach.rules.facts import from_old_dashboard


def d(title, body="", location="Boston, MA", country=None):
    return decide(PostingInput(title, body, location, country))


class Misfire1MTSDroppedByStaffRule(unittest.TestCase):
    """27 of 32 MTS titles dropped (crawl_boards.py:59)."""

    def test_mts_is_open_level(self):
        for title in ("Member of Technical Staff", "Member of Technical Staff, Applied AI"):
            with self.subTest(title):
                r = d(title)
                self.assertEqual((r.lane, r.level_tier, r.level_ok), ("engineering", "open", True))

    def test_senior_mts_is_senior(self):
        r = d("Senior Member of Technical Staff")
        self.assertEqual((r.level_tier, r.level_ok), ("senior", False))


class Misfire2SWEIIUnderDiscovered(unittest.TestCase):
    """34 of 119 found (crawl_boards.py:26)."""

    def test_swe2_titles(self):
        for title in ("Software Engineer II", "Software Engineer 2", "Member of Technical Staff 2",
                      "Software Engineer Front-End 2"):
            with self.subTest(title):
                r = d(title)
                self.assertEqual((r.level_tier, r.level_ok), ("swe2", True))

    def test_two_years_makes_swe2(self):
        r = d("Software Engineer", "You have 2+ years of professional software engineering experience.")
        self.assertEqual((r.level_tier, r.level_ok, r.years_asked), ("swe2", True, 2))

    def test_three_years_is_ok(self):
        r = d("Software Engineer", "3+ years of experience building backend systems in production.")
        self.assertTrue(r.level_ok)
        self.assertEqual(r.years_asked, 3)

    def test_four_years_is_not(self):
        r = d("Software Engineer", "4+ years of experience building backend systems.")
        self.assertFalse(r.level_ok)


class Misfire3StaffEngineerII(unittest.TestCase):
    """finalcheck.py:26 exempted a Staff Engineer II from the staff rule."""

    def test_staff_is_senior(self):
        for title in ("Staff Engineer II", "Staff Software Engineer, Agent Platform"):
            with self.subTest(title):
                r = d(title)
                self.assertEqual((r.level_tier, r.level_ok), ("senior", False))


class Misfire4FalseSenior(unittest.TestCase):
    """21 corpus titles read as senior by a substring match."""

    def test_not_senior(self):
        for title in ("Machine Learning Engineer Graduate - Lead Ads", "Forward Deployed Engineer Fellow",
                      "AI Fellow - Member of Technical Staff", "Backend Engineer, New Grad to Staff Level"):
            with self.subTest(title):
                r = d(title)
                self.assertNotEqual(r.level_tier, "senior")
                self.assertTrue(r.level_ok)

    def test_senior_control(self):
        self.assertEqual(d("Senior Software Engineer").level_tier, "senior")


class Misfire5YearsFromDegreeAlternative(unittest.TestCase):
    """finalcheck.py:41 read years from a degree alternative and from company history."""

    def test_degree_alternative_is_no_years(self):
        r = d("Software Engineer, New Grad", "Bachelor's degree or 4 years of equivalent experience.")
        self.assertEqual((r.years_asked, r.level_tier, r.level_ok), (None, "new_grad", True))

    def test_degree_alternative_without_or_is_no_years(self):
        # No "or" before the number, so only the degree-alternative exclusion can drop it.
        r = d("Software Engineer, New Grad", "Bachelor's degree and 4 years of equivalent experience.")
        self.assertEqual((r.years_asked, r.level_tier, r.level_ok), (None, "new_grad", True))

    def test_company_history_is_no_years(self):
        r = d("Software Engineer I",
              "We were founded 5 years ago, our engineering team now ships to 2,000 customers.")
        self.assertEqual((r.years_asked, r.level_tier, r.level_ok), (None, "swe1", True))


class Misfire6USCitiesReadAsNonUS(unittest.TestCase):
    """6 corpus places (finalcheck.py:69-82)."""

    def test_us_places(self):
        for place in ("Indianapolis, IN", "Columbus, Indiana", "Albuquerque, New Mexico", "Lima, OH", "Paris, TX",
                      "Athens, GA"):
            with self.subTest(place):
                r = d("Software Engineer, New Grad", "", place)
                self.assertEqual(r.us, "yes")
                self.assertTrue(passes(r))

    def test_paris_france_control(self):
        r = d("Software Engineer, New Grad", "", "Paris, France")
        self.assertEqual(r.us, "no")
        self.assertFalse(passes(r))


class Misfire7SponsorshipNegation(unittest.TestCase):
    """finalcheck.py:64-68; LAST_RUN Beacon AI, Forage, n8n."""

    def test_refusals(self):
        for sentence in ("We are unable to provide visa sponsorship for this role.",
                         "Visa sponsorship is not available for this position.",
                         "We will not sponsor H-1B petitions.",
                         "This role is not eligible for immigration sponsorship.",
                         "We do not sponsor."):
            with self.subTest(sentence):
                r = d("Software Engineer, New Grad", sentence)
                self.assertEqual(r.sponsorship, "no")
                self.assertEqual(r.sponsorship_quote, sentence)
                self.assertFalse(passes(r))

    def test_sponsoring_elsewhere_is_not_yes(self):
        r = d("Software Engineer, New Grad", "We can sponsor visas to Germany.")
        self.assertNotEqual(r.sponsorship, "yes")

    def test_sponsors_control(self):
        r = d("Software Engineer, New Grad", "We sponsor H-1B visas.")
        self.assertEqual(r.sponsorship, "yes")
        self.assertTrue(passes(r))


class Misfire8DashboardJuniorRegex(unittest.TestCase):
    """19 req titles (app.js:15)."""

    def test_scientist_is_not_engineering(self):
        r = d("Scientist I/II")
        self.assertEqual((r.lane, r.level_ok), ("not_engineering", False))

    def test_layer_1_is_no_level(self):
        r = d("Cellular Layer-1 Control Software Engineer")
        self.assertEqual((r.level_tier, r.level_ok), ("not_stated", False))

    def test_help_desk_is_off_lane(self):
        r = d("Help Desk Engineer - Linux Level II")
        self.assertEqual((r.lane, r.level_ok), ("off_lane", False))


class SmallDefaultIsUnknown(unittest.TestCase):
    """671 "small", 290 with a firm estimate (audit section 1). The store-level half is in Task 15."""

    def test_small_class_gives_no_size(self):
        company = {"sizeClass": "small", "sizeWhy": "no filing record: most likely an early startup"}
        self.assertEqual(from_old_dashboard(company), [])

    def test_no_filing_record_text_never_gives_a_size(self):
        # Visa filing wording measures filings, not headcount, even when a headcount-looking number sits beside it.
        company = {"sizeClass": "small", "sizeWhy": "no filing record, 12 engineers on the team"}
        self.assertEqual(from_old_dashboard(company), [])


if __name__ == "__main__":
    unittest.main()
