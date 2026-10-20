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
    # a negation in a clause without the topic word still counts: the sentence is about sponsorship
    ("We would love to sponsor you, but we are unable at this time.", "unclear"),  # yes and no in one sentence
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

# Review round 2 (#4, Task 11): what the round 1 fix broke.
REVIEW_2_STANCE = [  # (body, stance)
    # a refusal shared by both sides of "and" stays a refusal
    ("Visa sponsorship and relocation are not available.", "no"),
    ("Sponsorship and relocation are not available for this role.", "no"),
    ("We do not offer relocation and visa sponsorship.", "no"),
    ("We cannot provide relocation assistance and visa sponsorship for this role.", "no"),
    ("We do not provide relocation and visa sponsorship.", "no"),
    ("We are unable to sponsor and transfer H-1B visas.", "no"),
    ("We do not sponsor and will not transfer H-1B visas.", "no"),
    # sponsorship, visas, H-1B and the verb sponsor are their own context; sponsors and sponsored are not
    ("Sponsorship is available for this role.", "yes"),
    ("Sponsorship is available.", "yes"),
    ("Sponsorship: Yes", "yes"),
    ("Sponsorship Available: Yes", "yes"),
    ("H-1B transfers welcome.", "yes"),
    ("Sponsorship provided for the right candidate.", "yes"),
    ("We can sponsor.", "yes"),
    ("You will report to the executive sponsor of the program.", "silent"),  # the business sense of "sponsor"
    # "employer-sponsored work authorization" is about sponsorship
    ("Candidates must not require employer-sponsored work authorization now or in the future.", "no"),
    ("This position is not eligible for employer-sponsored work authorization.", "no"),
    # a refusal is read before the equal opportunity marker
    ("We do not sponsor visas, regardless of seniority.", "no"),
    ("We are unable to sponsor visas regardless of experience level.", "no"),
]

# Review round 3 (#4, Task 11).
REVIEW_3_STANCE = [  # (body, stance)
    # " and <verb>" with OPT on one side: the refusal does not cover the OPT support
    ("We do not offer visa sponsorship and welcome OPT candidates.", "opt_only"),
    ("We cannot sponsor H-1B visas and are happy to support OPT.", "opt_only"),
    ("We don't sponsor visas and accept OPT and CPT candidates.", "opt_only"),
    ("We are unable to sponsor H-1B and accept candidates on STEM OPT.", "opt_only"),
    ("We do not sponsor H-1B visas and welcome candidates on OPT.", "opt_only"),
    ("We support OPT and do not sponsor H-1B.", "opt_only"),
    ("We welcome F-1 students on OPT and are unable to sponsor H-1B visas.", "opt_only"),
    # a verbless side or a verb the list lacked no longer shares a generic negation about relocation
    ("H-1B sponsorship available and relocation not provided.", "yes"),
    ("We sponsor visas and relocation remains unavailable.", "yes"),
    # business and perk uses of "sponsor" and "sponsorship"
    ("Secure executive sponsorship for key initiatives.", "silent"),
    ("Partner with the product sponsor.", "silent"),
    ("Our sponsor, the Department of Energy, funds this work.", "silent"),
    ("You will work with the sponsor of each clinical trial.", "silent"),
    ("We sponsor professional development and certifications.", "silent"),
    ("We offer tuition sponsorship.", "silent"),
    ("We sponsor conferences like PyCon.", "silent"),
    ("We sponsor hackathons and meetups.", "silent"),
    ("Sponsorship of conference attendance is available.", "silent"),
    ("We do not sponsor visas. We sponsor conference attendance and certifications.", "no"),
    ("Unfortunately we cannot sponsor H-1B visas. Perks: tuition sponsorship, gym stipend.", "no"),
]

# A refusal of relocation joined to a real yes by " and <verb>": yes is best, unclear is accepted, no is wrong.
REVIEW_3_RELOCATION = [
    "We sponsor H-1B visas and do not offer relocation.",
    "We sponsor visas and do not provide relocation assistance.",
    "We offer visa sponsorship and do not offer relocation.",
]

REVIEW_3_CITIZEN = [  # (body, required)
    # a negation about something else across " and " does not cancel the requirement
    ("Active Secret clearance required and travel not required.", True),
    ("US citizenship required and prior experience not necessary.", True),
    ("Must be a U.S. citizen and degree not required.", True),
    ("Active security clearance required and a degree is not necessary.", True),
    # header and intensifier forms of a preference
    ("Bonus points: active security clearance.", False),
    ("Bonus: active TS/SCI clearance", False),
    ("Plus: active security clearance.", False),
    ("Security clearance a big plus.", False),
    ("Having an active Secret security clearance would be a huge plus.", False),
    # one negation shared by a list of nouns: the cut at " and " must not leave the first noun bare
    ("Security clearance and travel are not required.", False),
    ("U.S. citizenship and security clearance are not required.", False),
    ("Security clearance and relocation are not necessary.", False),
    ("We do not require clearance and citizenship.", False),
]

REVIEW_2_CITIZEN = [  # (body, required): a preference word cancels only the requirement it sits next to
    ("Active Secret clearance required, TS/SCI preferred.", True),
    ("Must hold an active TS/SCI clearance, Kubernetes experience is a plus.", True),
    ("U.S. citizenship required, experience with Python preferred.", True),
    ("Active security clearance required, Python a plus.", True),
    ("Must be a U.S. citizen with 3+ years of experience, plus strong Python skills.", True),
    ("Must be a US citizen, bonus eligible.", True),
    ("TS/SCI preferred, active Secret clearance required.", True),
    ("Nice to have: an active security clearance.", False),
]

REVIEW_CITIZEN = [  # (body, required)
    ("Candidates must be U.S. citizens.", True),
    ("Applicants must be U.S. citizens due to government contract requirements.", True),
    ("Security clearance is a plus.", False),
    ("Ability to obtain a security clearance preferred.", False),
    ("Experience with ITAR compliance is nice to have.", False),
]

# Final fix wave, item 1 (#4): a clause with a topic word and a negator the lists do not know reads unclear, never yes.
FINAL_1_STANCE = [  # (body, stances accepted)
    ("We aren" + RQ + "t able to sponsor visas.", ("no",)),
    ("We aren't able to sponsor visas.", ("no",)),
    ("Visa sponsorship will not be available.", ("no",)),
    ("Visa sponsorship will not be provided.", ("no",)),
    ("We have no plans to sponsor.", ("no",)),
    ("We cannot hire candidates who require visa sponsorship.", ("no",)),
    ("We do not have visa sponsorship available.", ("no",)),
    ("Sponsorship is not part of this role.", ("no", "unclear")),
    ("Visa sponsorship is dependent on many factors and we can't always guarantee success.", ("unclear",)),
    ("Visa sponsorship is available, but we can't always guarantee it.", ("unclear",)),
    ("We cannot guarantee visa sponsorship.", ("unclear", "no")),
    ("Right to work in the US without requiring the company to sponsor an immigration case at any point in the "
     "future is required.", ("no",)),
    ("Sponsorship is not guaranteed.", ("unclear",)),
    ("Visa: not sponsored", ("no",)),
    # a plural topic word is a topic word
    ("Visa sponsorships are available.", ("yes",)),
    # a negator the lists do not name must not leave a yes
    ("Visa sponsorship is available for candidates with no gaps in employment.", ("unclear",)),
    ("Sponsorship is available, none of it guaranteed.", ("unclear", "no")),
    ("Visa sponsorship is not part of the package.", ("no", "unclear")),
    # yes rows that must stay yes
    ("We sponsor H-1B visas.", ("yes",)),
    ("Relocation: No, H-1B sponsorship: Yes", ("yes",)),
]



# Final fix wave, item 2 (#4): a negation counts only in a clause that is about sponsorship, and clauses also end at a
# comma before a new subject, a spaced dash and a parenthesis.
FINAL_2_STANCE = [  # (body, stances accepted)
    ("Visa sponsorship is available, relocation is not provided.", ("yes",)),
    ("We offer visa sponsorship, we do not provide relocation.", ("yes",)),
    ("We sponsor H-1B visas (we do not provide relocation assistance).", ("yes",)),
    ("Visa sponsorship is available - relocation is not provided.", ("yes",)),
    ("Visa sponsorship is available " + EN + " relocation is not provided.", ("yes",)),
    ("Visa sponsorship is available " + chr(0x2014) + " relocation is not provided.", ("yes",)),
    ("Sponsorship for this role is available now or in the future.", ("yes",)),
    ("We will provide visa sponsorship now or in the future.", ("yes",)),
    ("Visa sponsorship is not provided for interns, but is for full-time hires.", ("unclear", "yes")),
    ("We offer visa sponsorship, but we are unable to provide relocation.", ("unclear", "yes")),
    ("We sponsor visas; we do not provide relocation.", ("yes",)),
    ("We sponsor visas (e.g. H-1B, TN).", ("yes",)),
    # a refusal that must stay a refusal across the new cuts
    ("Candidates must be authorized to work in the United States without sponsorship now or in the future.", ("no",)),
    ("We cannot be the sponsor of your visa.", ("no",)),
    ("We can" + RQ + "t be the sponsor of your visa.", ("no",)),
    ("Sponsorship (H-1B, TN) is not available for this role.", ("no",)),
    ("Visa sponsorship - not available for this role.", ("no",)),
    ("Visa sponsorship, not available for this role.", ("no",)),
    ("Candidates requiring sponsorship, now or in the future, will not be considered.", ("no",)),
    ("Applicants who need visa sponsorship, they will not be considered.", ("no", "unclear")),
    ("We would love to sponsor you, but we are unable at this time.", ("unclear",)),
    ("We cannot sponsor visas (relocation is available).", ("no", "unclear")),
    ("We are unable to hire candidates who require sponsorship, except for OPT.", ("opt_only",)),
    ("We do not sponsor visas, except for OPT students.", ("opt_only", "no")),
]


# Final fix wave, item 3 (#4): citizenship phrasings the lists missed, and wrong hides.
FINAL_3_CITIZEN = [  # (body, required)
    ("Position requires US citizenship.", True),
    ("This role requires U.S. citizenship.", True),
    ("This role requires United States citizenship.", True),
    ("U.S. citizenship is a requirement.", True),
    ("You must have U.S. citizenship.", True),
    ("Applicants must hold US citizenship.", True),
    ("Only U.S. citizens will be considered.", True),
    ("Only US citizens may apply.", True),
    ("U.S. citizenship or permanent residency required.", True),
    ("US citizenship or permanent residency is required.", True),
    ("This role requires access to ITAR-controlled data.", True),
    ("ITAR compliance is required.", True),
    ("Must be eligible to work on ITAR programs.", True),
    ("Only ITAR eligible candidates will be considered.", True),
    ("You do not need a security clearance.", False),
    ("You don" + RQ + "t need a security clearance.", False),
    ("You don't need a security clearance.", False),
    ("A security clearance is not mandatory.", False),
    ("No need for a security clearance.", False),
    ("U.S. citizenship is not a must.", False),
    ("No ITAR restrictions on this role.", False),
    ("This role is not subject to ITAR.", False),
    ("Our product is ITAR compliant.", False),
    ("We build ITAR compliant software.", False),
    ("ITAR is not required for this role.", False),
    # a requirement next to a negation about something else stays a requirement
    ("A degree is not required, but this role requires U.S. citizenship.", True),
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

    def test_review_2_stances(self):
        self.check(REVIEW_2_STANCE)

    def test_review_3_stances(self):
        self.check(REVIEW_3_STANCE)

    def test_final_1_a_refusal_is_never_yes(self):
        for body, accepted in FINAL_1_STANCE:
            with self.subTest(body=body):
                self.assertIn(sponsorship_stance(body, self.cfg)[0], accepted)

    def test_final_2_a_negation_about_something_else_is_not_a_refusal(self):
        for body, accepted in FINAL_2_STANCE:
            with self.subTest(body=body):
                self.assertIn(sponsorship_stance(body, self.cfg)[0], accepted)

    def test_relocation_refusal_next_to_a_yes_is_never_no(self):
        for body in REVIEW_3_RELOCATION:
            with self.subTest(body=body):
                self.assertIn(sponsorship_stance(body, self.cfg)[0], ("yes", "unclear"))

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

    def test_immigration_alone_is_not_a_topic_word(self):
        # An EEO line that names immigration status is not about sponsorship, so a refusal about something else in
        # the same sentence ("we do not provide relocation") must not read as a sponsorship refusal.
        self.check([
            ("All qualified applicants are considered regardless of immigration status, but we do not provide "
             "relocation.", "silent"),
            ("We do not discriminate on immigration status, and we are unable to offer relocation.", "silent"),
        ])

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
        for key in ("opt_support", "opt_acronyms", "negation_near", "context", "self_context", "benefit",
                    "non_discrimination", "field_no", "clause_verbs", "citizenship_preference", "negators", "negation_elided", "not_subject", "continuation",
                    "elided_complete", "negation_cued", "negation_cue", "citizenship_us_forms",
                    "citizenship_templates", "itar", "itar_cue"):
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
            "sponsorship and relocation are " * 33_000,  # one 1 MB sentence of "and" cuts to weigh
            "security clearance is a plus " * 35_000,  # one 1 MB sentence of requirements, each with a preference
            ("visa and " * 111_112)[:1_000_000],  # one 1 MB sentence of "and" joins with no verb anywhere
            "we do not sponsor visas and welcome OPT " * 25_000,  # "and <verb>" joins, each with OPT and a refusal
            # final fix wave: comma and dash cuts before a new subject, parentheticals with verbs, ", except" cuts
            "visa sponsorship is available, relocation is not provided - " * 17_000,
            "visa sponsorship (we do not provide relocation), " * 10_000,  # half a MB: each clause is read twice
            ("(" + "a" * 299) * 3_300,  # parentheses that never close
            "we cannot hire people who need visas, except for OPT " * 19_000,
            "itar " * 200_000,  # bare ITAR with no cue anywhere: one cue search, not one per word
            "itar, " * 160_000,
        ]
        for body in bodies:
            with self.subTest(body=body[:30]):
                # CPU time, not wall time: scheduler wait on a loaded machine is not the code's cost.
                start = time.process_time()
                sponsorship_stance(body, self.cfg)
                citizenship_required(body, self.cfg)
                self.assertLess(time.process_time() - start, 1.5)


class CitizenshipTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()

    def test_citizenship(self):
        for body, expected in CITIZEN + REVIEW_CITIZEN + REVIEW_2_CITIZEN + REVIEW_3_CITIZEN + FINAL_3_CITIZEN:
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
