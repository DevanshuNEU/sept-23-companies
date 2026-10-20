"""Sponsorship stance and citizenship (issue #4, Task 11)."""
import time
import unittest

from outreach.rules import config as config_mod
from outreach.rules import sponsorship
from outreach.rules.config import load_config
from outreach.rules.sponsorship import citizenship_required, sponsorship_stance

RQ = chr(0x2019)  # a curly apostrophe
EN = chr(0x2013)  # an en dash, built from its code point so this file has no literal one

CAPITAL_ONE = (
    "At this time, Capital One will not sponsor a new applicant for employment authorization, or offer any "
    "immigration related support for this position (i.e. H1B, F-1 OPT, F-1 STEM OPT, F-1 CPT, J-1, TN, E-2, E-3, "
    "L-1 and O-1, or any EADs or other forms of work authorization that require immigration support from an "
    "employer)."
)
EEO = (
    "All qualified applicants will receive consideration for employment without regard to race, color, religion, "
    "sex, national origin, disability, protected veteran status, citizenship, or immigration status."
)

STANCE = [  # (body, stance)
    ("We are unable to provide visa sponsorship for this role.", "no"),
    ("Visa sponsorship is not available for this position.", "no"),
    ("We will not sponsor H-1B petitions.", "no"),
    ("This role is not eligible for immigration sponsorship.", "no"),
    ("We do not sponsor.", "no"),
    ("Candidates must be authorized to work in the United States without sponsorship now or in the future.", "no"),
    ("We can sponsor visas to Germany.", "unclear"),
    ("Sponsorship may be considered for exceptional candidates.", "unclear"),
    ("We sponsor H-1B visas.", "yes"),
    ("Visa sponsorship is available for this role.", "yes"),
    ("Retell AI is open to sponsoring work authorization for qualified candidates, including H-1B, TN, L-1.", "yes"),
    ("We support candidates on OPT and STEM OPT, but we are unable to sponsor H-1B visas.", "opt_only"),
    (CAPITAL_ONE, "no"),
    (EEO, "silent"),
    ("Must be authorized to work in the U.S.", "silent"),
    ("We build voice agents for hospitals.", "silent"),
    ("", "silent"),
]

CITIZEN = [  # (body, required)
    ("U.S. citizenship is required for this role.", True),
    ("You must be a U.S. citizen.", True),
    ("An active Secret clearance is required.", True),
    ("This role requires access to ITAR-controlled data; applicants must be U.S. persons.", True),
    ("No security clearance required.", False),
    ("We welcome applicants regardless of citizenship.", False),
    (EEO, False),
]

# More stances: clause reading, OPT across sentences, wording the old list missed, and conflicts.
MORE_STANCE = [  # (body, stance)
    # a clause with no topic word and no signal adds nothing
    ("We are a fast-growing team, but we do not sponsor visas.", "no"),
    ("We sponsor H-1B visas, but you must apply by June 1.", "yes"),
    # a negation in a clause without the topic word still counts: the sentence is about sponsorship. Review round
    # 1 (R1) made "sponsor you" alone neutral (no context word), so this reads no, not the yes-and-no unclear of before.
    ("We would love to sponsor you, but we are unable at this time.", "no"),
    # a hedge or a non-US country in another clause makes the sentence unclear
    ("We sponsor H-1B visas, but only in limited cases.", "unclear"),
    ("We do not sponsor visas, but exceptions may be made for senior hires.", "unclear"),
    ("We sponsor visas, but this role is based in Canada.", "unclear"),
    # yes and no in one sentence conflict
    ("We sponsor H-1B transfers, but we cannot sponsor new petitions.", "unclear"),
    # OPT support in one sentence and no sponsorship in another is still OPT-only
    ("We support OPT and CPT. Unfortunately, we are unable to sponsor H-1B visas.", "opt_only"),
    ("We do not sponsor visas. Candidates on STEM OPT are welcome.", "opt_only"),
    ("We welcome candidates on OPT, and we sponsor H-1B visas.", "yes"),
    # OPT support with no sponsorship wording anywhere says nothing about sponsorship
    ("Candidates on OPT are welcome to apply.", "silent"),
    # OPT refused is not OPT support
    ("We do not accept OPT or visa candidates.", "no"),
    ("We do not sponsor visas. We do not accept OPT candidates.", "no"),
    # the word "opt" in a sentence about something else never decides anything
    ("Dental is not available in all states and you can opt out at any time.", "silent"),
    # wording the ported list missed
    ("We do not support visa sponsorship for this role.", "no"),
    ("We don" + RQ + "t offer visa sponsorship.", "no"),
    ("We don't offer visa sponsorship.", "no"),
    ("Visa sponsorship is unavailable for this role.", "no"),
    ("We are not currently sponsoring visas.", "no"),
    ("We do not currently sponsor H-1B visas.", "no"),
    ("We will not be able to offer visa sponsorship.", "no"),
    ("No H-1B sponsorship is offered.", "no"),
    ("Applicants must not require sponsorship now or in the future.", "no"),
    ("You must be authorized to work without the need for current or future visa sponsorship.", "no"),
    ("Must be authorized to work without requiring visa sponsorship.", "no"),
    ("Candidates who require visa sponsorship will not be considered.", "no"),
    ("Candidates requiring sponsorship are not eligible.", "no"),
    # dashes in the topic word
    ("We sponsor H" + EN + "1B visas.", "yes"),
    ("We sponsor H1B visas.", "yes"),
    # across sentences: no with an unclear sentence is still no; yes with an unclear one is still yes
    ("We do not sponsor visas. Sponsorship may be discussed later.", "no"),
    ("We sponsor H-1B visas. Sponsorship may take a few months.", "yes"),
    # across sentences: yes and no conflict
    ("We sponsor H-1B visas. We do not sponsor visas for contractors.", "unclear"),
]

# Review round 1 (#4, Task 11): every misreading the review saw. Each row read wrong before the fix.
REVIEW_STANCE = [  # (body, stance)
    # benefits and business uses of "sponsor" and "Visa" are not sponsorship
    ("We offer employer-sponsored health insurance.", "silent"),
    ("You will partner with executive sponsors across the business.", "silent"),
    ("Experience with Visa and Mastercard payment networks.", "silent"),
    ("We do not sponsor visas. We offer company-sponsored health insurance.", "no"),
    # refusals the list missed
    ("Visa sponsorship is not currently available for this role.", "no"),
    ("We won't be able to sponsor visas.", "no"),
    ("We won" + RQ + "t be able to sponsor visas.", "no"),
    ("This role is ineligible for visa sponsorship.", "no"),
    ("We never sponsor visas.", "no"),
    ("We are not in a position to sponsor visas.", "no"),
    ("This position does not qualify for visa sponsorship.", "no"),
    ("Sponsorship is not possible for this role.", "no"),
    ("We do not have the ability to sponsor visas.", "no"),
    ("We will not consider candidates who need visa sponsorship.", "no"),
    ("Must be authorized to work without the need of sponsorship.", "no"),
    ("Visa sponsorship: No", "no"),
    ("Sponsorship Available: No", "no"),
    ("We are not open to visa sponsorship for this role.", "no"),
    # the word "opt" in lower case is not OPT
    ("We do not sponsor visas. We support employees who opt in to our wellness program.", "no"),
    ("We do not sponsor visas. Employees can opt out at any time and we welcome feedback.", "no"),
    # EEO lines
    ("Equal opportunity employer. We do not discriminate based on immigration or visa status.", "silent"),
    ("All applicants are considered without regard to race, citizenship, immigration or visa status.", "silent"),
    # a generic negation about something else is not a refusal
    ("We offer visa sponsorship and relocation is not provided.", "yes"),
    ("We sponsor H-1B visas for this role, which is not eligible for remote work.", "yes"),
    ("Visa sponsorship is available for this role, though relocation is not available.", "yes"),
    ("We sponsor visas for strong candidates who need them now or in the future.", "yes"),
]

REVIEW_CITIZEN = [  # (body, required)
    ("Candidates must be U.S. citizens.", True),
    ("Applicants must be U.S. citizens due to government contract requirements.", True),
    ("Security clearance is a plus.", False),
    ("Ability to obtain a security clearance preferred.", False),
    ("Experience with ITAR compliance is nice to have.", False),
]


class SponsorshipStanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()

    def check(self, cases):
        for body, expected in cases:
            with self.subTest(body=body):
                stance, quote = sponsorship_stance(body, self.cfg)
                self.assertEqual(stance, expected)
                if expected == "silent":
                    self.assertEqual(quote, "")
                else:
                    self.assertNotEqual(quote, "")
                    self.assertIn(quote.strip("."), body)

    def test_stance(self):
        self.check(STANCE)

    def test_more_stances(self):
        self.check(MORE_STANCE)

    def test_review_stances(self):
        self.check(REVIEW_STANCE)

    def test_a_field_answer_counts_only_after_the_topic(self):
        # "Relocation: No" before a sponsorship field on the same line is not a refusal of sponsorship.
        self.assertEqual(sponsorship_stance("Relocation: No, H-1B sponsorship: Yes", self.cfg)[0], "yes")

    def test_patterns_are_built_once_per_config(self):
        self.assertIs(sponsorship._patterns(self.cfg), sponsorship._patterns(self.cfg))
        self.assertIsNot(sponsorship._patterns(self.cfg), sponsorship._patterns(load_config()))

    def test_no_word_list_has_a_curly_apostrophe(self):
        # Bodies have curly apostrophes read as straight ones before matching, so a curly entry can never match.
        for key, words in self.cfg.sponsorship.items():
            for word in words:
                with self.subTest(key=key, word=word):
                    self.assertNotIn(RQ, word)

    def test_work_authorization_alone_is_never_yes(self):
        self.assertNotEqual(sponsorship_stance("Must be authorized to work in the U.S.", self.cfg)[0], "yes")
        self.assertNotEqual(sponsorship_stance("Work authorization is required.", self.cfg)[0], "yes")

    def test_capital_one_quote_names_the_negation(self):
        stance, quote = sponsorship_stance("We build apps. " + CAPITAL_ONE + " Apply now.", self.cfg)
        self.assertEqual(stance, "no")
        self.assertIn("will not sponsor", quote)
        self.assertLessEqual(len(quote), 260)

    def test_quote_is_the_deciding_sentence(self):
        body = "We build voice agents. We sponsor H-1B visas. Apply now."
        self.assertEqual(sponsorship_stance(body, self.cfg), ("yes", "We sponsor H-1B visas."))
        body = "We build voice agents. Visa sponsorship is not available. Apply now."
        self.assertEqual(sponsorship_stance(body, self.cfg), ("no", "Visa sponsorship is not available."))

    def test_unclear_quotes_the_first_unclear_or_conflicting_sentence(self):
        body = "We build tools. We sponsor H-1B visas. We do not sponsor visas for contractors."
        self.assertEqual(sponsorship_stance(body, self.cfg), ("unclear", "We sponsor H-1B visas."))
        body = "We build tools. Sponsorship may be possible. Visa sponsorship depends on the role."
        self.assertEqual(sponsorship_stance(body, self.cfg), ("unclear", "Sponsorship may be possible."))

    def test_opt_only_across_sentences_quotes_both(self):
        body = "We support OPT and CPT. We build tools. We are unable to sponsor H-1B visas."
        stance, quote = sponsorship_stance(body, self.cfg)
        self.assertEqual(stance, "opt_only")
        self.assertIn("We support OPT and CPT.", quote)
        self.assertIn("We are unable to sponsor H-1B visas.", quote)

    def test_eeo_line_next_to_a_real_answer(self):
        self.assertEqual(sponsorship_stance("We sponsor H-1B visas. " + EEO, self.cfg)[0], "yes")
        self.assertEqual(sponsorship_stance(EEO + " We do not sponsor visas.", self.cfg)[0], "no")

    def test_new_keys_are_required(self):
        for key in ("opt_support", "opt_acronyms", "negation_near", "context", "benefit", "non_discrimination",
                    "field_no"):
            with self.subTest(key=key):
                self.assertIn(key, config_mod.REQUIRED["sponsorship"])

    def test_large_body_is_fast(self):
        # Every pattern is literals with bounded gaps, so a 1 MB body costs a few passes, not a blow-up.
        chunk = ("We may sponsor visas; however, we do not sponsor H-1B, but we support OPT \u00e9\u00e8 \u4e2d. "
                 "U.S. citizenship is not required, clearance required. sponsor sponsor visa visa ")
        bodies = [
            chunk * (1_000_000 // len(chunk)),
            "sponsor " * 125_000,  # one 1 MB sentence full of topic words
            "u.s. persons " * 80_000,  # one 1 MB sentence of near misses for the citizenship list
            ("visa, but " * 100_000) + "we do not sponsor.",  # one sentence of 100,000 clauses
            # the brief's pathological body: many short topic sentences, digits and non-ASCII
            ("5 years ago we graduated in 2027 or 2026; sponsorship maybe. \u00e9 " * 15000)[:1_000_000],
            "not available " * 72_000,  # one 1 MB sentence of window negations with no topic word near
            "visa sponsorship not available " * 33_000,  # window negations, each next to a topic word
        ]
        for body in bodies:
            with self.subTest(body=body[:30]):
                start = time.monotonic()
                sponsorship_stance(body, self.cfg)
                citizenship_required(body, self.cfg)
                self.assertLess(time.monotonic() - start, 1.5)


class CitizenshipTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()

    def test_citizenship(self):
        for body, expected in CITIZEN + REVIEW_CITIZEN:
            with self.subTest(body=body):
                required, quote = citizenship_required(body, self.cfg)
                self.assertIs(required, expected)
                if expected:
                    self.assertNotEqual(quote, "")
                else:
                    self.assertEqual(quote, "")

    def test_negation_is_read_per_clause(self):
        # "not required" about a degree does not cancel a citizenship requirement in the other clause.
        required, quote = citizenship_required("A degree is not required, but U.S. citizenship is required.", self.cfg)
        self.assertTrue(required)
        self.assertIn("citizenship is required", quote)
        self.assertFalse(citizenship_required("Security clearance is not required for this role.", self.cfg)[0])

    def test_quote_is_the_sentence(self):
        body = "We build tools. You must be a U.S. citizen. Apply now."
        self.assertEqual(citizenship_required(body, self.cfg), (True, "You must be a U.S. citizen."))

    def test_empty_body(self):
        self.assertEqual(citizenship_required("", self.cfg), (False, ""))


if __name__ == "__main__":
    unittest.main()
