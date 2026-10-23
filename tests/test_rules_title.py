"""Title lane and title level (issue #4, Task 8)."""
import dataclasses
import unittest

from outreach.rules.config import load_config
from outreach.rules.title import TitleResult, classify_title

CASES = [  # (title, lane, tier)
    ("Member of Technical Staff", "engineering", "open"),
    ("Member of Technical Staff, Applied AI", "engineering", "open"),
    ("Member of Technical Staff 2", "engineering", "swe2"),
    ("Senior Member of Technical Staff", "engineering", "senior"),
    ("Forward Deployed Engineer", "engineering", "open"),
    ("Software Engineer II", "engineering", "swe2"),
    ("Software Engineer 2", "engineering", "swe2"),
    ("SWE II", "engineering", "swe2"),
    ("Software Engineer Front-End 2", "engineering", "swe2"),
    ("Software Engineer I", "engineering", "swe1"),
    ("Software Engineer I/II", "engineering", "swe1"),
    ("Staff Engineer II", "engineering", "senior"),
    ("Staff Engineer I", "engineering", "senior"),
    ("Staff Software Engineer, Agent Platform", "engineering", "senior"),
    ("Software Engineer III", "engineering", "senior"),
    ("Software Engineer IV", "engineering", "senior"),
    ("Software Engineer 3", "engineering", "senior"),
    ("Full Stack Engineer 4 (Go, AWS)", "engineering", "senior"),
    ("Backend Engineer 5", "engineering", "senior"),
    ("Machine Learning Engineer Graduate - Lead Ads", "engineering", "new_grad"),
    ("Forward Deployed Engineer Fellow", "engineering", "early_career"),
    ("AI Fellow - Member of Technical Staff", "engineering", "early_career"),
    ("Backend Engineer, New Grad to Staff Level", "engineering", "new_grad"),
    ("Engineering Manager, New Grad Hiring", "engineering", "senior"),
    ("Associate Software Engineer", "engineering", "early_career"),
    ("Software Engineer", "engineering", None),
    ("Cellular Layer-1 Control Software Engineer", "engineering", None),
    ("Founding Engineer", "engineering", None),
    ("Software Engineering Intern", "intern", None),
    ("Solutions Engineer", "off_lane", None),
    ("Help Desk Engineer - Linux Level II", "off_lane", None),
    ("Scientist I/II", "not_engineering", None),
    ("Recruiter", "not_engineering", None),
    ("General Application - Engineering", "general_application", None),
]

# Beyond the brief's cases: the senior words carried from refresh/crawl_boards.py:59, numerals written onto the
# noun, numerals that are not levels, and "level N".
MORE_CASES = [
    ("AVP, Software Engineering", "engineering", "senior"),
    ("Mid-Level Software Engineer", "engineering", "senior"),
    ("Mid Level Backend Engineer", "engineering", "senior"),
    ("Software Engineer (SDE2)", "engineering", "swe2"),
    ("SDE-1", "engineering", "swe1"),
    ("Software Engineer, Level 1", "engineering", "swe1"),
    ("Software Engineer V", "engineering", "senior"),
    ("Software Engineer, Payments 2.0", "engineering", None),
    ("Software Engineer, Tier 1 Support Tools", "engineering", None),
    ("Software Engineer, Infrastructure", "engineering", None),
    ("Software Engineer 10x", "engineering", None),
    ("Sr. Software Engineer", "engineering", "senior"),
    ("New Grad Software Engineer III", "engineering", "senior"),
    ("FDE", "engineering", "open"),
    ("Software Engineer, I/O Platform", "engineering", None),
    ("Software Engineer II/III", "engineering", "swe2"),
    ("Software Engineer (Backend) II", "engineering", "swe2"),
    ("Senior Engineer, Graduate Program", "engineering", "senior"),
    ("Graduate Software Engineer", "engineering", "new_grad"),
    ("Principal MTS", "engineering", "senior"),
    # role qualifiers between the noun and the numeral (title.level_qualifiers)
    ("Software Engineer, Backend II", "engineering", "swe2"),
    ("Software Engineer - Full Stack 3", "engineering", "senior"),
    ("Software Engineer, Mobile 1", "engineering", "swe1"),
    ("Backend Engineer, Back-End 2 (Remote)", "engineering", "swe2"),
    # any other word in between: the numeral is not a level
    ("Software Engineer, Web 3", "engineering", None),
    ("Software Engineer - Python 3", "engineering", None),
    ("Backend Engineer (Python 3)", "engineering", None),
    ("Software Engineer, Product 4", "engineering", None),
    ("Software Engineer, Growth 3", "engineering", None),
    ("Software Engineer, Search 2", "engineering", None),
    ("Software Engineer, AI Agents 2", "engineering", None),
    ("Software Engineer, Series 1", "engineering", None),
    ("Software Engineer, Layer 2", "engineering", None),
]

# #4 follow-up 2026-10-22: postdoctoral roles need a PhD, so they are off lane (he is a May 2026 MS new grad).
# The controls keep the results they had before the change.
POSTDOC_CASES = [
    ("Postdoctoral Associate-Systems Engineering", "off_lane", None),
    ("Postdoctoral Researcher - Civil, Environmental, and Ocean Engineering", "off_lane", None),
    ("Postdoc Software Engineer", "off_lane", None),
    ("Post-Doctoral Fellow, Machine Learning Engineering", "off_lane", None),
    ("Post Doc Developer", "off_lane", None),
    ("Software Engineer, New Grad", "engineering", "new_grad"),
    ("Graduate Software Engineer", "engineering", "new_grad"),
    ("Engineering Fellow", "engineering", "early_career"),
]

# #4 follow-up 2026-10-23: support, field, metrology and academic titles are off lane. Contract roles stay in
# (he wants contract-to-hire), so the contract controls keep their results.
OFF_LANE_CASES = [
    ("Mandarin-speaking: Customer Success Engineer (Contract)", "off_lane", None),
    ("Customer Success Engineer", "off_lane", None),
    ("Robotics Field Engineer", "off_lane", None),
    ("Field Engineer", "off_lane", None),
    ("Field Engineers, Hardware Rollout", "off_lane", None),
    ("Field Application Engineer", "off_lane", None),
    ("Field Applications Engineer", "off_lane", None),
    ("CMM Programmer", "off_lane", None),
    ("Metrology Engineer", "off_lane", None),
    ("Assistant Professor of Professional Practice, Clinical Practicum & Internship Coordinator, "
     "Counselor Education", "off_lane", None),
    ("Adjunct Faculty - Engineering", "off_lane", None),
    ("Lecturer, Software Engineering", "off_lane", None),
    ("Faculty Software Engineer", "off_lane", None),
    # already off lane before this change (field service and field technician stay as they were)
    ("Field Service Engineer", "off_lane", None),
    ("Field Technician", "not_engineering", None),
    # unchanged controls
    ("Software Engineer (Contract)", "engineering", None),
    ("Software Engineer, Contract to Hire", "engineering", None),
    ("Software Engineer, Field Systems", "engineering", None),
    ("Software Engineering Intern", "intern", None),
    ("RA-II/Sr. Research Associate Protein Engineering", "engineering", "senior"),
    ("Research Associate, Software Engineering", "engineering", "early_career"),
]


class ClassifyTitleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()

    def _check(self, cases):
        for title, lane, tier in cases:
            with self.subTest(title=title):
                got = classify_title(title, self.cfg)
                self.assertEqual(got.lane, lane)
                if lane == "engineering":
                    self.assertEqual(got.tier, tier)

    def test_cases(self):
        self._check(CASES)

    def test_more_cases(self):
        self._check(MORE_CASES)

    def test_postdoc_cases(self):
        self._check(POSTDOC_CASES)

    def test_postdoc_titles_are_off_lane_with_no_tier(self):
        for title, lane, _ in POSTDOC_CASES:
            if lane == "off_lane":
                with self.subTest(title=title):
                    got = classify_title(title, self.cfg)
                    self.assertEqual(got.lane, "off_lane")
                    self.assertIsNone(got.tier)

    def test_off_lane_cases(self):
        self._check(OFF_LANE_CASES)

    def test_basis_is_never_empty(self):
        for title, _, _ in CASES + MORE_CASES:
            with self.subTest(title=title):
                basis = classify_title(title, self.cfg).basis
                self.assertIsInstance(basis, str)
                self.assertTrue(basis.strip())

    def test_basis_names_what_the_title_says(self):
        expect = {
            "Software Engineer II": "Title says Engineer II",
            "Machine Learning Engineer Graduate - Lead Ads": "Title says new grad",
            "Associate Software Engineer": "Title says early career",
            "Member of Technical Staff": "MTS role, no level in the title",
            "Software Engineer": "Title states no level",
            "Software Engineer, Level 1": "Title says Level 1",
        }
        for title, basis in expect.items():
            with self.subTest(title=title):
                self.assertEqual(classify_title(title, self.cfg).basis, basis)

    def test_non_engineering_lanes_have_no_tier(self):
        for title, lane, _ in CASES:
            if lane != "engineering":
                with self.subTest(title=title):
                    self.assertIsNone(classify_title(title, self.cfg).tier)

    def test_result_is_frozen(self):
        got = classify_title("Software Engineer", self.cfg)
        self.assertIsInstance(got, TitleResult)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            got.lane = "intern"

    def test_empty_title(self):
        got = classify_title("", self.cfg)
        self.assertEqual(got.lane, "not_engineering")
        self.assertIsNone(got.tier)
        self.assertTrue(got.basis)


if __name__ == "__main__":
    unittest.main()
