"""The role signals: live_role, posting_sponsors and pay_level (issue #6, Task 6).

Each signal copies `facts.grade(name)` into its Assessment and never grades by itself. A stale posting gives no live
role; a title with a dash never reaches a reason."""
import unittest

from outreach.ranking.config import default_ranker_config
from outreach.ranking.confidence import Assessment, Confidence
from outreach.ranking.roles import Best
from outreach.ranking.signals.live_role import LiveRole
from outreach.ranking.signals.pay_level import PayLevel
from outreach.ranking.signals.posting_sponsors import PostingSponsors
from tests.ranking_helpers import check_assessment, neutral_facts

CFG = default_ranker_config()
EN, EM = chr(0x2013), chr(0x2014)


def live_role() -> LiveRole:
    return LiveRole(CFG.section("live_role"))


def stale(posting_id: int, reason: str, age=None) -> Best:
    return Best(posting_id, "level_role", "Old role", "new_grad", "silent", age, "", "", None, "2016-03-02",
                "read_ok", True, stale_reason=reason)


class LiveRoleTests(unittest.TestCase):
    def value(self, **facts) -> Assessment:
        return check_assessment(self, live_role(), neutral_facts(**facts))

    def test_live_role_values(self):
        self.assertAlmostEqual(self.value(lanes=("level_role",), best_role_age_days=3).value, 1.0)
        self.assertAlmostEqual(self.value(lanes=("level_role",), best_role_age_days=20).value, 0.85)
        self.assertAlmostEqual(self.value(lanes=("level_role",), best_role_age_days=90).value, 0.7)
        self.assertAlmostEqual(self.value(lanes=("intern_junior",), best_role_age_days=90).value, 0.4)
        self.assertAlmostEqual(self.value(lanes=("any_us_role",), best_role_age_days=90).value, 0.15)
        self.assertEqual(self.value().value, 0.0)

    def test_freshness_bonus_steps_and_the_cap(self):
        self.assertAlmostEqual(self.value(lanes=("level_role",), best_role_age_days=14).value, 1.0)
        self.assertAlmostEqual(self.value(lanes=("level_role",), best_role_age_days=15).value, 0.85)
        self.assertAlmostEqual(self.value(lanes=("level_role",), best_role_age_days=31).value, 0.75)
        self.assertAlmostEqual(self.value(lanes=("level_role",), best_role_age_days=61).value, 0.7)
        self.assertAlmostEqual(self.value(lanes=("level_role",), best_role_age_days=None).value, 0.7)
        self.assertEqual(self.value(lanes=("level_role",), best_role_age_days=0).value, 1.0)

    def test_the_value_is_capped_at_one(self):
        params = {**CFG.section("live_role"), "level_role": 0.9}
        got = check_assessment(self, LiveRole(params), neutral_facts(lanes=("level_role",), best_role_age_days=1))
        self.assertEqual(got.value, 1.0)

    def test_the_strongest_role_lane_sets_the_base_and_other_lanes_are_ignored(self):
        got = self.value(lanes=("reason_no_role", "any_us_role"), best_role_age_days=90)
        self.assertAlmostEqual(got.value, 0.15)
        got = self.value(lanes=("level_role", "intern_junior", "any_us_role"), best_role_age_days=90)
        self.assertAlmostEqual(got.value, 0.7)
        self.assertEqual(self.value(lanes=("reason_no_role",)).value, 0.0)

    def test_live_role_reason_names_the_level_and_the_title(self):
        got = self.value(lanes=("level_role",), best_role_age_days=3, best_title="Software Engineer, New Grad",
                         best_level_tier="new_grad")
        self.assertEqual(got.reason, "Open new grad role: Software Engineer, New Grad, posted 3 days ago")
        self.assertIn("new grad", got.reason)

    def test_live_role_reason_labels_and_ages(self):
        cases = [("early_career", "early career"), ("swe1", "SWE I"), ("swe2", "SWE II"), ("open", "open level"),
                 ("senior", "engineering"), (None, "engineering")]
        for tier, label in cases:
            got = self.value(lanes=("level_role",), best_role_age_days=3, best_title="T", best_level_tier=tier)
            self.assertIn(f"Open {label} role: T", got.reason)
        intern = self.value(lanes=("intern_junior",), best_role_age_days=3, best_title="Intern", best_level_tier="x")
        self.assertIn("Open intern role: Intern", intern.reason)
        self.assertTrue(self.value(lanes=("level_role",), best_role_age_days=0, best_title="T").reason.endswith("posted today"))
        self.assertTrue(self.value(lanes=("level_role",), best_role_age_days=1, best_title="T").reason.endswith("posted 1 day ago"))

    def test_live_role_reason_survives_a_dashed_title(self):
        got = self.value(lanes=("level_role",), best_role_age_days=3, best_level_tier="new_grad",
                         best_title=f"Software Engineer {EN} New Grad {EM} Boston")
        self.assertNotIn(EN, got.reason)
        self.assertNotIn(EM, got.reason)
        self.assertIn("Software Engineer", got.reason)

    def test_no_role_says_so(self):
        self.assertEqual(self.value().reason, "No open role yet")

    def test_live_role_with_only_stale_postings_is_zero_and_says_why(self):
        one = self.value(stale_postings=(stale(1, "posted 2016-03-02, over 12 months"),))
        self.assertEqual(one.value, 0.0)
        self.assertEqual(one.reason, "No live role: the only posting is stale (posted 2016-03-02, over 12 months)")
        two = self.value(stale_postings=(stale(1, "posted 2016-03-02, over 12 months", age=3500),
                                         stale(2, "posted 2019-01-05, over 12 months", age=2500)))
        self.assertEqual(two.value, 0.0)
        self.assertTrue(two.reason.startswith("No live role: 2 postings are stale ("), two.reason)
        self.assertIn("posted 2019-01-05, over 12 months", two.reason)
        self.assertNotIn("2016-03-02", two.reason)

    def test_a_stale_reason_with_a_dash_is_cleaned(self):
        got = self.value(stale_postings=(stale(1, f"posted 2016{EN}03{EM}02, over 12 months"),))
        self.assertNotIn(EN, got.reason)
        self.assertNotIn(EM, got.reason)

    def test_a_live_role_beats_stale_postings(self):
        got = self.value(lanes=("level_role",), best_role_age_days=3, best_title="T",
                         stale_postings=(stale(1, "posted 2016-03-02, over 12 months"),))
        self.assertAlmostEqual(got.value, 1.0)
        self.assertTrue(got.reason.startswith("Open"))

    def test_confidence_is_copied_from_the_facts_grades(self):
        medium = Confidence("medium", "x")
        got = check_assessment(self, live_role(), neutral_facts(grades={"live_role": medium}))
        self.assertIs(got.confidence, medium)
        bare = check_assessment(self, live_role(), neutral_facts())
        self.assertEqual(bare.confidence, Confidence("low", "no data for live role"))


class PostingSponsorsTests(unittest.TestCase):
    def setUp(self):
        self.signal = PostingSponsors(CFG.section("posting_sponsors"))

    def test_posting_sponsors_values_and_reasons(self):
        yes = check_assessment(self, self.signal, neutral_facts(best_sponsorship="yes"))
        self.assertEqual((yes.value, yes.reason), (1.0, "Posting says the company sponsors visas"))
        opt = check_assessment(self, self.signal, neutral_facts(best_sponsorship="opt_only"))
        self.assertEqual((opt.value, opt.reason), (0.5, "Posting welcomes OPT"))
        for stance in ("silent", "unclear", None):
            got = check_assessment(self, self.signal, neutral_facts(best_sponsorship=stance))
            self.assertEqual(got.value, 0.0, stance)

    def test_posting_sponsors_without_a_live_posting_is_zero(self):
        got = check_assessment(self, self.signal, neutral_facts(best_sponsorship=None))
        self.assertEqual(got.value, 0.0)
        self.assertEqual(got.confidence, Confidence("low", "no data for posting sponsors"))
        graded = neutral_facts(grades={"posting_sponsors": Confidence("low", "no live posting to read sponsorship from")})
        self.assertEqual(check_assessment(self, self.signal, graded).confidence.level, "low")

    def test_the_grade_is_copied_not_computed(self):
        final = Confidence("high", "reader confirmed the wording with a quote")
        got = check_assessment(self, self.signal, neutral_facts(best_sponsorship="yes", grades={"posting_sponsors": final}))
        self.assertIs(got.confidence, final)


class PayLevelTests(unittest.TestCase):
    def setUp(self):
        self.signal = PayLevel(CFG.section("pay_level"))

    def test_pay_level_values(self):
        for level, value in ((1, -0.3), (2, 0.6), (3, 0.85), (4, 1.0)):
            got = check_assessment(self, self.signal, neutral_facts(pay_level=level, pay_note="posted $1 in Boston"))
            self.assertEqual(got.value, value, level)
        none = check_assessment(self, self.signal, neutral_facts())
        self.assertEqual((none.value, none.reason), (0.0, "Pay level unknown"))

    def test_the_reason_of_a_posted_level_carries_the_note(self):
        note = "posted $140,000 to $150,000 in Boston: level 2 at the minimum, level 2 at the midpoint"
        got = check_assessment(self, self.signal, neutral_facts(pay_level=2, pay_basis="posted", pay_note=note))
        self.assertEqual(got.reason, f"Wage level 2: {note}")

    def test_a_level_with_no_note_still_has_a_reason(self):
        got = check_assessment(self, self.signal, neutral_facts(pay_level=3))
        self.assertEqual(got.reason, "Wage level 3")

    def test_an_unknown_level_number_is_neutral(self):
        self.assertEqual(check_assessment(self, self.signal, neutral_facts(pay_level=9)).value, 0.0)

    def test_confidence_is_copied(self):
        c = Confidence("medium", "level changes inside the posted range")
        got = check_assessment(self, self.signal, neutral_facts(pay_level=2, grades={"pay_level": c}))
        self.assertIs(got.confidence, c)


class RegistryTests(unittest.TestCase):
    def test_signal_name_matches_its_weight_key(self):
        for cls in (LiveRole, PostingSponsors, PayLevel):
            self.assertIn(cls.name, CFG.weights)
        self.assertEqual((LiveRole.name, PostingSponsors.name, PayLevel.name),
                         ("live_role", "posting_sponsors", "pay_level"))

    def test_each_signal_takes_its_config_section(self):
        for cls in (LiveRole, PostingSponsors, PayLevel):
            cls(CFG.section(cls.name))


if __name__ == "__main__":
    unittest.main()
