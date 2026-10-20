"""Graduation eligibility and the 2027 start flag (issue #4, Task 12)."""
import dataclasses
import re
import tempfile
import time
import unittest
from pathlib import Path

from outreach.rules import config as config_mod
from outreach.rules.config import CONFIG_PATH, load_config
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

# Review round 2: the graduate route is read in the current-student sentence only, and company history rules out
# only the words and dates it covers, never a whole graduation sentence.
ROUND_2 = [  # (title, body, status, reason, start_2027)
    # graduate wording in another sentence does not cancel an enrollment rule
    ("Software Engineer", "You must be currently enrolled in a Bachelor's program. Interns and new grads join the same "
     "onboarding.", "excluded", "current_student", False),
    ("Software Engineer", "Must be currently enrolled in a university. Our interns and new graduates get mentorship.",
     "excluded", "current_student", False),
    ("Software Engineer", "You must be currently enrolled in a Bachelor's program and have taken or completed a course "
     "in data structures.", "excluded", "current_student", False),
    ("Software Engineer", "You must be currently enrolled in a degree program; students who have withdrawn or "
     "graduated are not eligible.", "excluded", "current_student", False),
    # a graduate route in the current-student sentence lets graduates in
    ("Software Engineer", "Currently pursuing or who have completed a BS in CS.", "ok", None, False),
    ("Software Engineer", "Current students or those who graduated in the last year are eligible.", "ok", None, False),
    ("Software Engineer", "Currently enrolled in or recently graduated from a BS program.", "ok", None, False),
    ("Software Engineer", "Currently pursuing (or completed) a BS in CS.", "ok", None, False),
    ("Software Engineer", "Current students, recent graduates and career changers are welcome.", "ok", None, False),
    ("Software Engineer", "Currently pursuing a BS, or a recent new grad.", "ok", None, False),
    # "graduate degree", "graduate school" and founding lines do not hide a real window
    ("Software Engineer", "Must graduate between December 2026 and June 2027 with a Bachelor's or graduate degree in "
     "CS.", "excluded", "grad_window", False),
    ("Software Engineer", "Candidates pursuing an undergraduate or graduate degree and graduating between December "
     "2026 and June 2027.", "excluded", "grad_window", False),
    ("Software Engineer", "Graduate degree holders who graduate between December 2026 and June 2027 are eligible.",
     "excluded", "grad_window", False),
    ("Software Engineer", "Founded in 2015, we hire engineers who graduate between December 2026 and June 2027.",
     "excluded", "grad_window", False),
    ("Software Engineer", "Founded by ex-Stripe engineers; we hire students who graduate between December 2026 and "
     "June 2027.", "excluded", "grad_window", False),
]

# Review round 3: a welcome anywhere in the body (same sentence, next sentence, next bullet) lets graduates in, and a
# founding line keeps its year out of the window.
ROUND_3 = [  # (title, body, status, reason, start_2027)
    ("Software Engineer", "Currently pursuing a degree in Computer Science. Recent graduates are also welcome to apply.",
     "ok", None, False),
    ("Software Engineer", "Currently pursuing a Bachelor's or Master's in CS; recent graduates are also encouraged to "
     "apply.", "ok", None, False),
    ("Software Engineer", "You must be currently enrolled in a degree program; recent graduates welcome.",
     "ok", None, False),
    ("Software Engineer", "Requirements:\n- Currently pursuing a BS or MS in Computer Science, or\n- Graduated within "
     "the last 12 months", "ok", None, False),
    ("Software Engineer", "Requirements:\n- Currently enrolled in a BS/MS program\n- Or a recent graduate of one",
     "ok", None, False),
    ("Software Engineer", "Currently enrolled in a Bachelor's or Master's program. Or have graduated within the past "
     "12 months.", "ok", None, False),
    ("Software Engineer", "Currently pursuing a BS in CS.\nRecent grads welcome.", "ok", None, False),
    ("Software Engineer", "Currently pursuing a BS in CS.\nOpen to recent graduates.", "ok", None, False),
    ("Software Engineer", "Currently pursuing a Bachelor's degree in CS. Already graduated? You can apply too.",
     "ok", None, False),
    # a founding year is not a window, whatever sits between the founders and the year
    ("Software Engineer", "Founded by two MIT graduates in 2024.", "ok", None, False),
    ("Software Engineer", "Founded by two MIT graduates in 2023.", "ok", None, False),
    ("Software Engineer", "Founded by a team of MIT graduates in 2024.", "ok", None, False),
    ("Software Engineer", "Founded by two Stanford PhD graduates in 2024, we build developer tools.", "ok", None, False),
    ("Software Engineer", "Founded by former Stripe engineers and MIT graduates in 2023.", "ok", None, False),
    ("Software Engineer", "Founded by two MIT graduates, the company launched in 2024.", "ok", None, False),
    ("Software Engineer", "Founded by Harvard graduates; launched in 2025.", "ok", None, False),
    ("Software Engineer", "Our company graduated from Techstars in 2024 and hires new graduates.", "ok", None, False),
    ("Software Engineer", "We graduated from Y Combinator in 2023 and are hiring recent graduates.", "ok", None, False),
    # a real window stays a window, next to founding wording or with no comma after it
    ("Software Engineer", "Founded in 2025, hiring December 2026 graduates.", "excluded", "grad_window", False),
    ("Software Engineer", "Founded in 2015 we hire engineers who graduate between December 2026 and June 2027.",
     "excluded", "grad_window", False),
    ("Software Engineer", "Founded by two MIT graduates in 2024, we hire students who graduate between December 2026 "
     "and June 2027.", "excluded", "grad_window", False),
    ("Software Engineer", "We graduated from Y Combinator in 2023 and hire students who graduate between December "
     "2026 and June 2027.", "excluded", "grad_window", False),
]

# Final fix wave, item 5 (#4): dated graduation sentences.
NG = "Software Engineer, New Grad"
FINAL_5 = [  # (title, body, status, reason, start_2027)
    # (a) a date inside a start phrase is a start date, never a graduation date
    (NG, "New graduates start in September 2027.", "ok", None, True),
    (NG, "Full-time role for recent graduates with a start date of June 2027.", "ok", None, True),
    (NG, "Graduates start date is September 2027.", "ok", None, True),
    # (b) lower bounds open the window upward; "or earlier" is an upper bound
    (NG, "Candidates must have graduated on or after January 2025.", "ok", None, False),
    (NG, "Candidates must have graduated after December 2024.", "ok", None, False),
    (NG, "Candidates must have graduated in June 2025 or later.", "ok", None, False),
    (NG, "Candidates must have graduated in or after 2025.", "ok", None, False),
    (NG, "Candidates must have graduated (since 2024).", "ok", None, False),
    (NG, "Candidates must graduate in 2027 or earlier.", "ok", None, False),
    (NG, "Candidates must have graduated in 2024 or earlier.", "excluded", "grad_window", False),
    (NG, "Candidates must be graduating after June 2027.", "excluded", "grad_window", False),
    # (c) a graduate route in the dated sentence lets graduates in
    (NG, "Recent graduates and students graduating in 2027 are encouraged to apply.", "ok", None, False),
    (NG, "New grads from the class of 2027 as well as earlier classes.", "ok", None, False),
    (NG, "Open to students graduating in 2027 as well as recent graduates.", "ok", None, False),
    (NG, "New graduates must graduate between December 2026 and June 2027.", "excluded", "grad_window", False),
    # (d) a refused cohort is not a rule
    (NG, "This role is not open to students graduating in 2027.", "ok", None, False),
    (NG, "We are unable to consider 2027 graduates.", "ok", None, False),
    # (e) a date acts as a rule only with a candidate cue next to the graduation word
    (NG, "Graduates apply by January 15, 2027.", "ok", None, False),
    (NG, "Help us hit our 2027 revenue targets with our graduates program.", "ok", None, False),
    (NG, "We're a team of MIT graduates who started the company in 2025.", "ok", None, False),
    (NG, "Founded in 2023 by two CMU graduates, we raised our Series A in 2025.", "ok", None, False),
    # and the windows that must stay windows
    (NG, "Expected graduation date: December 2026 - June 2027.", "excluded", "grad_window", False),
    (NG, "Applicants must be graduating between December 2026 and June 2027.", "excluded", "grad_window", False),
    (NG, "We are hiring candidates who will graduate in 2027.", "excluded", "grad_window", False),
    (NG, "Graduation expected in Spring 2027.", "excluded", "grad_window", False),
]

# Final fix wave, item 4 (#4): a current-student phrase excludes only in a sentence that names a degree, program, school
# or enrollment, with no negation and no preference wording.
FINAL_4 = [  # (title, body, status, reason, start_2027)
    ("Software Engineer II", "Preferred Qualifications: Bachelor's Degree (or currently pursuing).", "ok", None, False),
    ("Software Engineer", "The ideal candidate is not a current student.", "ok", None, False),
    ("Software Engineer", "CPA or actively pursuing.", "ok", None, False),
    ("Software Engineer", "Start actively pursuing new business opportunities.", "ok", None, False),
    ("Software Engineer", "We're currently working toward our public launch.", "ok", None, False),
    ("Software Engineer", "We are currently pursuing Series B funding.", "ok", None, False),
    ("Software Engineer", "Learn from current senior engineers.", "ok", None, False),
    ("Software Engineer", "This position is not open to current students.", "ok", None, False),
    ("Software Engineer", "Applicants do not need to be currently enrolled.", "ok", None, False),
    ("Software Engineer", "Applicants don" + chr(0x2019) + "t need to be currently enrolled in a degree program.", "ok", None,
     False),
    ("Software Engineer", "Currently enrolled in a degree program is a plus.", "ok", None, False),
    ("Software Engineer", "Nice to have: currently pursuing a Master's degree.", "ok", None, False),
    # still excluded
    ("Software Engineer", "You must be currently enrolled in a Bachelor's or Master's program in Computer Science.",
     "excluded", "current_student", False),
    ("Software Engineer", "You are actively pursuing a Bachelor's degree in Computer Science.", "excluded",
     "current_student", False),
    ("Software Engineer", "Current seniors at a university are eligible to apply.", "excluded", "current_student", False),
    ("Software Engineer", "You must be a current student.", "excluded", "current_student", False),
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

    def test_review_round_2_cases(self):
        self.check(ROUND_2)

    def test_review_round_3_cases(self):
        self.check(ROUND_3)

    def test_final_5_dated_graduation_sentences(self):
        self.check(FINAL_5)

    def test_final_4_current_student_needs_a_program_cue(self):
        self.check(FINAL_4)

    def test_a_missing_route_list_fails_at_load(self):
        # graduate_route is a required key, so a config without it is refused at load and the error names it.
        self.assertIn("graduate_route", config_mod.REQUIRED["eligibility"])
        text = CONFIG_PATH.read_text(encoding="utf-8")
        broken = re.sub(r"(?ms)^graduate_route = \[.*?^\]\n", "", text)
        self.assertNotEqual(broken, text)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.toml"
            path.write_text(broken, encoding="utf-8")
            with self.assertRaisesRegex(KeyError, "graduate_route"):
                load_config(path)

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
            "graduate 2027 " * 70_000,  # every graduation word has a date, and none has a cue
            "must graduate 2027 not open to " * 30_000,  # a cue and a refusal at every graduation word
            "start in 2027 graduate " * 40_000,  # start wording and graduation words interleaved
            "currently enrolled students " * 35_000,  # a current-student phrase, a cue and no negation, one sentence
        ]
        for body in bodies:
            with self.subTest(body=body[:30]):
                # CPU time, not wall time: scheduler wait on a loaded machine is not the code's cost.
                start = time.process_time()
                eligibility("Software Engineer, New Grad 2027", body, self.cfg)
                starts_2027("Software Engineer, New Grad 2027", body, self.cfg)
                self.assertLess(time.process_time() - start, 5.0)


if __name__ == "__main__":
    unittest.main()
