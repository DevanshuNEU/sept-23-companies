"""Years asked and entry wording (issue #4, Task 9)."""
import time
import unittest

from outreach.rules.config import load_config
from outreach.rules.years import entry_wording, years_asked

EN = chr(0x2013)  # an en dash, built from its code point so this file has no literal one

CASES = [  # (body, years)
    ("Bachelor's degree or 4 years of equivalent experience.", None),
    ("We were founded 5 years ago, our engineering team now ships to 2,000 customers.", None),
    ("You have 2+ years of professional software engineering experience.", 2),
    ("3+ years of experience building backend systems in production.", 3),
    ("5+ years of experience with distributed systems.", 5),
    ("1-3 years of professional experience.", 1),
    ("BS in Computer Science or equivalent practical experience. 2+ years of experience with Python.", 2),
    ("Our customers have trusted us for 10 years.", None),
    ("5+ years of server-side experience. 1+ years of professional experience with Go.", 5),
    ("BS or MS in Computer Science and 5+ years of professional experience.", 5),
    ("1" + EN + "3 years of professional experience.", 1),
    ("2+ years of experience with Python. 5+ years of experience with Rust is a plus.", 2),
    ("", None),
]

MORE_CASES = [  # (body, years)
    ("1 to 3 years of professional experience.", 1),
    ("1 " + EN + " 3 years of professional experience.", 1),
    ("3-5+ years of software engineering experience.", 3),
    ("2 or more years of industry experience.", 2),
    ("3 or 4 years of professional experience.", 3),
    ("0-2 years of experience.", 0),
    ("4 yrs of experience in backend work.", 4),
    ("35 years of experience.", None),  # above 30: not a requirement anyone means
    ("30 years of experience.", 30),
    ("2.5 years of experience.", None),  # a decimal is not read as its last digit
    ("100 years of experience.", None),
    ("4 years of relevant experience in lieu of a degree.", None),
    ("A degree, or 4+ years of professional experience.", None),
    ("5+ years of professional experience, with a degree in CS or equivalent experience.", 5),
    ("We have been in business for 15 years and our engineering team is growing.", None),
    ("A 10 year history of engineering excellence.", None),
    ("Experience with Python is ideally 3+ years of professional work.", None),
    ("We have 5 years of runway in the bank, so we can keep hiring slowly and with care through 2028, and we love "
     "experience design.", None),  # the experience word is too far away
    ("Fun team. 3+ years of experience.\nBenefits include 401k.", 3),
    ("- 4+ years of experience with React", 4),
]


class YearsAskedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()

    def test_cases(self):
        for body, years in CASES:
            with self.subTest(body=body):
                self.assertEqual(years_asked(body, self.cfg)[0], years)

    def test_more_cases(self):
        for body, years in MORE_CASES:
            with self.subTest(body=body):
                self.assertEqual(years_asked(body, self.cfg)[0], years)

    def test_quote_is_the_sentence(self):
        self.assertEqual(years_asked(CASES[2][0], self.cfg)[1], CASES[2][0])

    def test_quote_is_the_largest_requirement(self):
        body = CASES[8][0]
        self.assertEqual(years_asked(body, self.cfg), (5, "5+ years of server-side experience."))

    def test_no_number_is_none_and_empty(self):
        for body in ("", "We hire great engineers.", "Our customers have trusted us for 10 years."):
            with self.subTest(body=body):
                self.assertEqual(years_asked(body, self.cfg), (None, ""))

    def test_every_number_has_a_quote(self):
        for body, years in CASES + MORE_CASES:
            if years is None:
                continue
            with self.subTest(body=body):
                number, quote = years_asked(body, self.cfg)
                self.assertTrue(quote)
                self.assertIn(str(number), quote)

    def test_long_sentence_quote_is_cut(self):
        body = "We value " + "craft and care " * 60 + "and need 4+ years of professional experience " + "x " * 200
        number, quote = years_asked(body, self.cfg)
        self.assertEqual(number, 4)
        self.assertLessEqual(len(quote), 260)
        self.assertIn("4+ years", quote)

    def test_pathological_body_is_fast(self):
        bodies = [
            ("5 years " * 130_000) + "é" * 1000,  # one huge sentence, no experience word anywhere
            ("3 years experience. " * 50_000) + ("years graduat. " * 10_000),
            "1" * 1_000_000 + " years of experience",
            ("or 4 years founded ago " * 40_000),
            ("5 years of experience " * 50_000),  # one huge sentence with no comma, every number a requirement
        ]
        for body in bodies:
            start = time.monotonic()
            years_asked(body, self.cfg)
            entry_wording(body, self.cfg)
            self.assertLess(time.monotonic() - start, 2.0)


class EntryWordingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()

    def test_entry_wording(self):
        self.assertNotEqual(entry_wording("This is an entry-level role for recent graduates.", self.cfg), "")
        self.assertEqual(entry_wording("We hire great engineers.", self.cfg), "")

    def test_quote_is_the_sentence(self):
        body = "We build tools. This role suits new grads who like Go. Apply now."
        self.assertEqual(entry_wording(body, self.cfg), "This role suits new grads who like Go.")

    def test_forms(self):
        for body in (
            "Early-career engineers welcome.", "0 to 2 years of experience.", "0" + EN + "2 years of experience.",
            "No experience required.", "We hire university graduates.", "Open to university grads.",
            "Recent university graduate in CS.", "Recent graduation from a CS program.", "New Grad 2026.",
        ):
            with self.subTest(body=body):
                self.assertNotEqual(entry_wording(body, self.cfg), "")

    def test_only_the_first_6000_characters(self):
        self.assertEqual(entry_wording("x " * 3000 + "Great for new grads.", self.cfg), "")
        self.assertNotEqual(entry_wording("x " * 2000 + "Great for new grads.", self.cfg), "")

    def test_graduate_degree_is_not_entry_wording(self):
        self.assertEqual(entry_wording("A university graduate degree in CS is required.", self.cfg), "")


if __name__ == "__main__":
    unittest.main()
