"""Graduation eligibility and the 2027 start flag (issue #4, Task 12)."""
import dataclasses
import time
import unittest

from outreach.rules.config import load_config
from outreach.rules.eligibility import eligibility, starts_2027

CASES = [  # (title, body, status, reason, start_2027)
  ("Software Engineer, New Grad", "Candidates must graduate between December 2026 and June 2027.", "excluded", "grad_window", False),
  ("Software Engineer, New Grad", "Class of 2027 graduates only.", "excluded", "grad_window", False),
  ("Software Engineer", "You must be currently enrolled in a Bachelor's or Master's program in Computer Science.", "excluded", "current_student", False),
  ("Software Engineer, New Grad", "Currently pursuing a Bachelor's degree or higher or a recent graduate studying Computer Science, Mathematics, or a related technical field.", "ok", None, False),
  ("Software Engineer, New Grad", "Bachelor's degree in Computer Science, earned or expected by Summer 2027.", "ok", None, False),
  ("Software Engineer, New Grad", "Graduated between May 2025 and August 2026 with a degree in Computer Science.", "ok", None, False),
  ("2027 University Graduate - Software Engineer", "", "excluded", "cohort_title", False),
  ("Software Engineer, New Grad (2027)", "", "ok", None, True),
  ("Software Engineer, New Grad", "This role starts in July 2027.", "ok", None, True),
  ("Software Engineer", "Our founders graduated from Y Combinator's Winter 2024 batch.", "ok", None, False),
  ("Software Engineer, New Grad", "Immediate start. Graduated in 2025 or 2026.", "ok", None, False),
]

# More readings: the order of the steps, upper bounds, soft wording, and what is and is not a 2027 start.
MORE = [  # (title, body, status, reason, start_2027)
    # an upper bound opens the window below, so someone who already graduated qualifies
    ("Software Engineer, New Grad", "You must graduate no later than June 2027.", "ok", None, False),
    # soft wording in the graduation sentence is a preference, not a rule
    ("Software Engineer, New Grad", "Graduation in Spring 2027 is preferred.", "ok", None, False),
    # a dated graduation sentence decides before a current-student phrase elsewhere in the body
    ("Software Engineer", "Graduated between May 2025 and August 2026. Currently enrolled students may also apply.",
     "ok", None, False),
    # graduates-welcome wording beats a current-student requirement
    ("Software Engineer", "Must be currently enrolled or recently graduated and ready to start.", "ok", None, False),
    # a graduation sentence without a date does not decide
    ("Software Engineer", "Graduates of any accredited program welcome. You must be a current student.",
     "excluded", "current_student", False),
    # cohort titles name graduates
    ("Software Engineer, Class of 2027", "", "excluded", "cohort_title", False),
    ("Software Engineer (College Grad 2027)", "", "excluded", "cohort_title", False),
    ("2027 Campus Hire, Backend", "", "excluded", "cohort_title", False),
    # a title that names 2027 without naming graduates is a start date
    ("Software Engineer, New Grad 2027", "", "ok", None, True),
    # start wording in the body
    ("Software Engineer, New Grad", "We are hiring for a Summer 2027 start.", "ok", None, True),
    ("Software Engineer, New Grad", "Come join our team in 2027.", "ok", None, True),
    # graduation wording is not a start date
    ("Software Engineer, New Grad", "Recent graduates are welcome, and so are students who graduate in 2027.",
     "ok", None, False),
    # an immediate start cancels a 2027 title
    ("Software Engineer, New Grad (2027)", "Immediate start for the right candidate.", "ok", None, False),
    # nothing to read
    ("Software Engineer", "", "ok", None, False),
]

# Review round 1: graduate routes beside a current-student phrase, joined windows, company history and glued years.
ROUND_1 = [  # (title, body, status, reason, start_2027)
    # a current-student phrase with a graduate route in the same sentence lets graduates in
    ("Software Engineer", "Currently pursuing or recently completed a Bachelor's degree in Computer Science.",
     "ok", None, False),
    ("Software Engineer", "Currently pursuing or have recently completed a BS in CS.", "ok", None, False),
    ("Software Engineer", "Currently pursuing or graduated with a BS in Computer Science.", "ok", None, False),
    ("Software Engineer", "We encourage current students and recent graduates to apply.", "ok", None, False),
    ("Software Engineer", "Current students and new grads are welcome.", "ok", None, False),
    ("Software Engineer", "Bachelor's degree in CS (completed or currently pursuing).", "ok", None, False),
    # a semicolon or a colon does not end the graduation sentence
    ("Software Engineer, New Grad",
     "Must graduate between December 2026 and June 2027; recent graduates are also welcome.", "ok", None, False),
    ("Software Engineer, New Grad", "Expected graduation date:\nDecember 2026 - June 2027",
     "excluded", "grad_window", False),
    # the company's own history and benefits are not eligibility rules
    ("Software Engineer", "Founded in 2023 by two MIT graduates.", "ok", None, False),
    ("Software Engineer", "We are a team of graduates from MIT, founded in 2024.", "ok", None, False),
    ("Software Engineer", "Benefits include graduate school tuition support starting 2025.", "ok", None, False),
    # a year glued to letters is not a date
    ("Software Engineer", "We are a YC W2024 graduate company.", "ok", None, False),
]

# Body start wording is a 2027 start even next to immediate-start wording; only the title reading is cancelled.
STARTS = [  # (title, body, start_2027)
    ("Software Engineer, New Grad", "Open to 2026 grads. This role starts in July 2027.", True),
    ("Software Engineer, New Grad", "We welcome December 2026 graduates. Start date: January 2027.", True),
    ("Software Engineer, New Grad", "THIS ROLE STARTS IN JULY 2027.", True),
    ("Software Engineer, New Grad (2027)", "Open to 2026 grads.", False),
    ("Software Engineer, New Grad", "Starts in July 2026.", False),
]


class EligibilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()

    def check(self, cases):
        for title, body, status, reason, start in cases:
            with self.subTest(title=title, body=body):
                got_status, got_reason, quote = eligibility(title, body, self.cfg)
                self.assertEqual((got_status, got_reason), (status, reason))
                self.assertIs(starts_2027(title, body, self.cfg), start)
                if status == "excluded":
                    self.assertNotEqual(quote, "")
                    self.assertIn(quote, title + " " + body)

    def test_cases(self):
        self.check(CASES)

    def test_more_cases(self):
        self.check(MORE)

    def test_review_round_1_cases(self):
        self.check(ROUND_1)

    def test_body_start_wording_is_not_cancelled(self):
        for title, body, start in STARTS:
            with self.subTest(title=title, body=body):
                self.assertIs(starts_2027(title, body, self.cfg), start)

    def test_colon_joined_quote_holds_the_dates(self):
        status, reason, quote = eligibility("Software Engineer", "Expected graduation date:\nDecember 2026 - June 2027",
                                            self.cfg)
        self.assertEqual((status, reason), ("excluded", "grad_window"))
        self.assertIn("June 2027", quote)

    def test_graduation_month_comes_from_config(self):
        title, body = CASES[0][0], CASES[0][1]
        self.assertEqual(eligibility(title, body, self.cfg)[0], "excluded")
        later = dataclasses.replace(self.cfg, candidate={**self.cfg.candidate, "graduated": "2027-05"})
        self.assertEqual(eligibility(title, body, later)[:2], ("ok", None))

    def test_quote_is_the_deciding_sentence(self):
        body = "We build tools. Candidates must graduate between December 2026 and June 2027. Apply now."
        self.assertEqual(eligibility("Software Engineer", body, self.cfg),
                         ("excluded", "grad_window", "Candidates must graduate between December 2026 and June 2027."))
        body = "We build tools. You must be a current student. Apply now."
        self.assertEqual(eligibility("Software Engineer", body, self.cfg),
                         ("excluded", "current_student", "You must be a current student."))

    def test_cohort_quote_is_the_title(self):
        title = "2027 University Graduate - Software Engineer"
        self.assertEqual(eligibility(title, "", self.cfg), ("excluded", "cohort_title", title))

    def test_ok_without_a_deciding_sentence_has_no_quote(self):
        self.assertEqual(eligibility("Software Engineer", "We build tools.", self.cfg), ("ok", None, ""))

    def test_large_body_is_fast(self):
        # Every pattern is literals with bounded gaps, and dates are found once per sentence, so 1 MB stays linear.
        # The limit leaves room for a slower machine; catastrophic backtracking would take minutes, not seconds.
        bodies = [
            ("We build voice agents for hospitals and clinics across the country. " * 15000)[:1_000_000],
            ("5 years ago we graduated in 2027 or 2026; sponsorship maybe. é " * 15000)[:1_000_000],
            "graduated " * 100_000,  # one 1 MB sentence of graduation words and no date
            "graduate " * 50_000 + "2027 " * 50_000,  # many graduation words, then many years, in one sentence
            "currently " * 100_000 + "start 2026 " * 10_000,  # near misses for the current-student and start lists
        ]
        for body in bodies:
            with self.subTest(body=body[:30]):
                start = time.monotonic()
                eligibility("Software Engineer, New Grad 2027", body, self.cfg)
                starts_2027("Software Engineer, New Grad 2027", body, self.cfg)
                self.assertLess(time.monotonic() - start, 5.0)


if __name__ == "__main__":
    unittest.main()
