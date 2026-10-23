"""The evidence signals and the signal registry (issue #6, Task 7).

Each signal copies `facts.grade(name)` into its Assessment and never grades by itself. Unknown size and unknown
stage are exactly neutral; only `large_later_stage` pushes a big company down."""
import dataclasses
import unittest

from outreach.ranking.config import default_ranker_config
from outreach.ranking.confidence import Confidence
from outreach.ranking.facts import SHARED_DOMAIN_FLAG
from outreach.ranking.signals import build_signals
from outreach.ranking.signals.cap_exempt import CapExempt
from outreach.ranking.signals.everify import EVerify
from outreach.ranking.signals.funding import Funding
from outreach.ranking.signals.large_later_stage import LargeLaterStage
from outreach.ranking.signals.lca_junior import LcaJunior
from outreach.ranking.signals.reachable_person import ReachablePerson
from outreach.ranking.signals.small import Small
from outreach.ranking.signals.staffing_risk import StaffingRisk
from tests.ranking_helpers import check_assessment, neutral_facts

CFG = default_ranker_config()
ORDER = ["live_role", "posting_sponsors", "everify", "cap_exempt", "pay_level", "lca_junior", "small",
         "reachable_person", "funding", "staffing_risk", "large_later_stage"]


def sig(cls, section=None):
    return cls(CFG.section(section or cls.name))


class EVerifyTests(unittest.TestCase):
    def test_everify_values_and_reasons(self):
        s = sig(EVerify)
        got = check_assessment(self, s, neutral_facts(everify="enrolled"))
        self.assertEqual((got.value, got.reason), (1.0, "E-Verify enrolled (STEM OPT needs it by July 2027)"))
        got = check_assessment(self, s, neutral_facts(everify="not_enrolled"))
        self.assertEqual((got.value, got.reason), (-0.5, "Not enrolled in E-Verify (STEM OPT needs it)"))
        got = check_assessment(self, s, neutral_facts())
        self.assertEqual(got.value, 0.0)


class CapExemptTests(unittest.TestCase):
    def test_cap_exempt_values(self):
        s = sig(CapExempt)
        for value, want in (("confirmed", 1.0), ("likely", 0.7), ("no", 0.0), (None, 0.0)):
            with self.subTest(value=value):
                got = check_assessment(self, s, neutral_facts(cap_exempt=value))
                self.assertEqual(got.value, want)
        got = check_assessment(self, s, neutral_facts(cap_exempt="confirmed"))
        self.assertEqual(got.reason, "Cap-exempt employer: no lottery")


class LcaJuniorTests(unittest.TestCase):
    def test_lca_junior_values_and_reason(self):
        s = sig(LcaJunior)
        got = check_assessment(self, s, neutral_facts(lca_jr_recent=4, lca_level1_recent=3))
        self.assertAlmostEqual(got.value, 0.4)
        self.assertEqual(got.reason, "4 junior H-1B filings in the last two fiscal years (3 at level I)")
        self.assertEqual(check_assessment(self, s, neutral_facts(lca_jr_recent=10)).value, 1.0)
        self.assertEqual(check_assessment(self, s, neutral_facts(lca_jr_recent=250)).value, 1.0)
        self.assertEqual(check_assessment(self, s, neutral_facts()).value, 0.0)


class SmallTests(unittest.TestCase):
    def value(self, **facts):
        return check_assessment(self, sig(Small), neutral_facts(**facts))

    def test_small_values(self):
        self.assertEqual(self.value(size_low=120, size_high=120, stage="seed").value, 1.0)
        self.assertEqual(self.value(size_low=120, size_high=120, stage="series_c").value, 0.6)
        self.assertEqual(self.value(size_low=120, size_high=120).value, 1.0)
        self.assertEqual(self.value(size_low=250, size_high=250).value, 1.0)
        self.assertEqual(self.value(size_low=251, size_high=251).value, 0.2)
        self.assertEqual(self.value(size_low=280, size_high=280).value, 0.2)
        self.assertEqual(self.value(size_low=300, size_high=300).value, 0.2)
        self.assertEqual(self.value(size_low=301, size_high=301).value, 0.0)
        self.assertEqual(self.value(size_low=400, size_high=400).value, 0.0)

    def test_a_range_is_read_at_its_high_end(self):
        self.assertEqual(self.value(size_low=200, size_high=280).value, 0.2)
        self.assertEqual(self.value(size_low=200, size_high=None).value, 1.0)

    def test_small_unknown_size_is_exactly_neutral(self):
        got = self.value()
        self.assertEqual(got.value, 0.0)
        self.assertEqual(got.reason, "Size unknown")
        got = self.value(stage="seed")
        self.assertEqual(got.value, 0.0)  # a stage alone never makes a company small
        self.assertEqual(got.reason, "Size unknown")
        self.assertEqual(self.value(stage="series_d").value, 0.0)

    def test_small_unknown_size_with_a_weak_flag_is_lowered(self):
        got = self.value(weak_flags=("81 open roles",))
        self.assertEqual(got.value, -0.3)
        self.assertIn("81 open roles", got.reason)
        for flag in ("12 software H-1B filings in one fiscal year", SHARED_DOMAIN_FLAG, "later stage, size unknown"):
            self.assertEqual(self.value(weak_flags=(flag,)).value, -0.3)
        self.assertEqual(self.value(weak_flags=("81 open roles", SHARED_DOMAIN_FLAG)).value, -0.3)

    def test_a_known_size_ignores_the_weak_flags(self):
        self.assertEqual(self.value(size_low=100, size_high=100, weak_flags=("81 open roles",)).value, 1.0)

    def test_the_reason_names_the_size(self):
        self.assertIn("120", self.value(size_low=120, size_high=120, stage="seed").reason)
        self.assertIn("280", self.value(size_low=280, size_high=280).reason)


class LargeLaterStageTests(unittest.TestCase):
    def value(self, **facts):
        return check_assessment(self, sig(LargeLaterStage, "small"), neutral_facts(**facts))

    def test_large_later_stage_is_the_only_bottom_push_for_size(self):
        got = self.value(stage="series_d")
        self.assertEqual(got.value, -1.0)
        self.assertTrue(got.reason.startswith("Large later-stage company: ranked last, never hidden"))
        self.assertIn("series d", got.reason)
        got = self.value(size_low=400, size_high=400)
        self.assertEqual(got.value, -1.0)
        self.assertIn("guess", got.reason)
        self.assertEqual(self.value().value, 0.0)

    def test_every_later_stage_pushes_and_every_early_stage_does_not(self):
        for stage in CFG.section("small")["later_stages"]:
            self.assertEqual(self.value(stage=stage).value, -1.0, stage)
        for stage in CFG.section("small")["early_stages"]:
            self.assertEqual(self.value(stage=stage).value, 0.0, stage)

    def test_the_size_boundary_is_over_the_borderline_headcount(self):
        self.assertEqual(self.value(size_low=300, size_high=300).value, 0.0)
        self.assertEqual(self.value(size_low=301, size_high=301).value, -1.0)

    def test_a_confirmed_size_names_its_evidence_not_a_guess(self):
        got = self.value(size_low=400, size_high=400, size_confidence="confirmed")
        self.assertEqual(got.value, -1.0)
        self.assertNotIn("guess", got.reason)

    def test_a_guess_grade_is_carried_not_upgraded(self):
        low = Confidence("low", "size from lca, guess")
        facts = neutral_facts(size_low=400, size_high=400, size_confidence="guess", grades={"large_later_stage": low})
        got = check_assessment(self, sig(LargeLaterStage, "small"), facts)
        self.assertEqual(got.value, -1.0)
        self.assertEqual(got.confidence, low)
        self.assertIn("guess", got.reason)


class ReachablePersonTests(unittest.TestCase):
    def test_reachable_person_values(self):
        s = sig(ReachablePerson)
        got = check_assessment(self, s, neutral_facts(reach_via="person", reach_note="Reachable person: Ada, engineer"))
        self.assertEqual((got.value, got.reason), (1.0, "Reachable person: Ada, engineer"))
        got = check_assessment(self, s, neutral_facts(reach_via="hn_post",
                                                      reach_note="An HN post asks for email, posted 3 days ago"))
        self.assertEqual((got.value, got.reason), (0.6, "An HN post asks for email, posted 3 days ago"))

    def test_no_one_to_reach_supplies_its_own_reason(self):
        got = check_assessment(self, sig(ReachablePerson), neutral_facts())
        self.assertEqual(got.value, 0.0)
        self.assertTrue(got.reason.strip())

    def test_a_route_with_an_empty_note_still_has_a_reason(self):
        got = check_assessment(self, sig(ReachablePerson), neutral_facts(reach_via="person"))
        self.assertEqual(got.value, 1.0)


class FundingTests(unittest.TestCase):
    def test_funding_values(self):
        s = build_signals(CFG)[ORDER.index("funding")]
        for months, want in ((0, 1.0), (9, 0.5), (18, 0.0), (30, 0.0), (None, 0.0)):
            with self.subTest(months=months):
                got = check_assessment(self, s, neutral_facts(funded_months_ago=months))
                self.assertEqual(got.value, want)
        got = check_assessment(self, s, neutral_facts(funded_months_ago=9))
        self.assertEqual(got.reason, "Raised money 9 months ago")

    def test_the_window_comes_from_the_ranker_section(self):
        s = Funding(CFG.section("funding"), window_months=24)
        got = check_assessment(self, s, neutral_facts(funded_months_ago=12))
        self.assertEqual(got.value, 0.5)


class StaffingRiskTests(unittest.TestCase):
    def value(self, **facts):
        return check_assessment(self, sig(StaffingRisk), neutral_facts(**facts))

    def test_staffing_risk_values(self):
        self.assertEqual(self.value(work_tier=3).value, -1.0)  # tier 3, not promoted, no evidence row
        self.assertEqual(self.value(staffing_risk="high").value, -1.0)
        self.assertEqual(self.value(staffing_risk="high", work_promoted=True).value, -0.5)
        self.assertEqual(self.value(staffing_risk="medium").value, -0.5)
        self.assertEqual(self.value(staffing_risk="low").value, 0.0)
        self.assertEqual(self.value().value, 0.0)

    def test_a_promoted_tier_3_with_no_evidence_is_clean(self):
        self.assertEqual(self.value(work_tier=3, work_promoted=True).value, 0.0)
        self.assertEqual(self.value(work_tier=2).value, 0.0)

    def test_tier_3_beats_a_medium_row_when_not_promoted(self):
        self.assertEqual(self.value(work_tier=3, staffing_risk="medium").value, -1.0)

    def test_reason_names_the_level(self):
        got = self.value(staffing_risk="medium")
        self.assertEqual(got.reason, "Staffing-firm risk medium: STEM OPT cannot be a third-party placement")
        got = self.value(staffing_risk="high")
        self.assertEqual(got.reason, "Staffing-firm risk high: STEM OPT cannot be a third-party placement")
        self.assertIn("promoted", self.value(staffing_risk="high", work_promoted=True).reason)


class RegistryTests(unittest.TestCase):
    def test_build_signals_returns_the_eleven_in_order(self):
        self.assertEqual([s.name for s in build_signals(CFG)], ORDER)

    def test_weights_have_one_entry_per_signal(self):
        self.assertEqual(set(CFG.weights), {s.name for s in build_signals(CFG)})

    def test_build_signals_rejects_a_weightless_signal(self):
        cfg = dataclasses.replace(CFG, weights={k: v for k, v in CFG.weights.items() if k != "everify"})
        with self.assertRaises(KeyError) as ctx:
            build_signals(cfg)
        self.assertIn("everify", str(ctx.exception))

    def test_build_signals_rejects_a_weight_with_no_signal(self):
        cfg = dataclasses.replace(CFG, weights={**CFG.weights, "mystery": 3.0})
        with self.assertRaises(ValueError) as ctx:
            build_signals(cfg)
        self.assertIn("mystery", str(ctx.exception))

    def test_unknown_evidence_is_neutral_and_low_confidence(self):
        facts = neutral_facts()
        for s in build_signals(CFG):
            with self.subTest(signal=s.name):
                got = check_assessment(self, s, facts)
                self.assertEqual(got.value, 0.0)
                self.assertEqual(got.confidence.level, "low")
        small = next(s for s in build_signals(CFG) if s.name == "small")
        self.assertEqual(small.score(facts).reason, "Size unknown")

    def test_every_signal_copies_its_own_grade(self):
        grades = {s.name: Confidence("high", f"basis of {s.name}") for s in build_signals(CFG)}
        facts = neutral_facts(grades=grades)
        for s in build_signals(CFG):
            self.assertEqual(s.score(facts).confidence, grades[s.name])


if __name__ == "__main__":
    unittest.main()
