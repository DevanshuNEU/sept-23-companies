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
