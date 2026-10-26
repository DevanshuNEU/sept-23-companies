"""The interview_like signal (#81, Task 8): a one-way lift toward companies like the interview companies. It never
goes below zero, counts by the profile's confidence (never high), and weight 0 switches it off."""
import itertools
import re
import tempfile
import unittest
from pathlib import Path

from outreach.insights.bands import FEATURE_NAMES, Features
from outreach.insights.similar import InterviewProfile, ProfileEntry, interview_profile
from outreach.ranking.config import CONFIG_PATH, default_ranker_config, load_ranker_config
from outreach.ranking.confidence import Confidence
from outreach.ranking.score import score_company
from outreach.ranking.signals import build_signals
from outreach.ranking.signals.interview_like import InterviewLike
from tests.ranking_helpers import neutral_facts

CFG = default_ranker_config()
FULL = Features(size_band="51 to 300", stage_band="Series A or B", level_band="entry level", board_system="ashby")
OTHER = Features(size_band="over 1,000", stage_band="public", level_band="senior", board_system="workday")
_ids = itertools.count(1000)


def make_profile(n: int, features: Features = FULL) -> InterviewProfile:
    """`n` interview companies, the first with `features` and the rest unlike everything, graded as the shipped
    `confident_at` grades them."""
    entries = [ProfileEntry(next(_ids), f"Interview {i}", f"2026-10-{i % 28 + 1:02d}", features if i == 0 else OTHER)
               for i in range(n)]
    graded = interview_profile((), confident_at=CFG.section("interview_like")["confident_at"])
    level = "low" if n < CFG.section("interview_like")["confident_at"] else "medium"
    return InterviewProfile(tuple(entries), Confidence(level, f"{n} interview companies") if n else graded.confidence)


def with_weight(tc: unittest.TestCase, weight: str):
    text = CONFIG_PATH.read_text(encoding="utf-8")
    text, n = re.subn(r"(?m)^interview_like\s*=.*$", f"interview_like = {weight}", text, count=1)
    tc.assertEqual(n, 1)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.toml"
        p.write_text(text, encoding="utf-8")
        return load_ranker_config(p)


def facts_like(features: Features, company_id: int = 1):
    return neutral_facts(company_id=company_id, features=features)


class InterviewLikeTest(unittest.TestCase):
    def signal(self, profile: InterviewProfile) -> InterviewLike:
        return InterviewLike(CFG.section("interview_like"), profile)

    def test_interview_like_never_goes_below_zero(self):
        profile = make_profile(13)
        choices = [(None, FULL.as_dict()[n], OTHER.as_dict()[n]) for n in FEATURE_NAMES]
        sig = self.signal(profile)
        for combo in itertools.product(*choices):
            feats = Features(**dict(zip(FEATURE_NAMES, combo)))
            with self.subTest(features=combo):
                got = sig.score(facts_like(feats))
                self.assertGreaterEqual(got.value, 0.0)
                self.assertLessEqual(got.value, 1.0)
                p = score_company(facts_like(feats), None, [sig], CFG).values["interview_like"]
                self.assertGreaterEqual(p.raw_points, 0.0)
                self.assertGreaterEqual(p.points, 0.0)

    def test_the_lift_never_lowers_the_confidence_label_of_the_facts_below_it(self):
        high, low = Confidence("high", "confirmed"), Confidence("low", "a guess")
        facts = neutral_facts(company_id=1, features=FULL, everify="enrolled", reachable_person=True,
                              grades={"everify": high, "reachable_person": low})
        without = [s for s in build_signals(CFG, make_profile(13)) if s.name != "interview_like"]
        plain = score_company(facts, None, without, CFG)
        lifted = score_company(facts, None, build_signals(CFG, make_profile(13)), CFG)
        self.assertGreater(lifted.values["interview_like"].points, 0.0)
        self.assertEqual(plain.confidence, lifted.confidence)

    def test_a_lift_counts_by_its_confidence(self):
        self.assertEqual(6.0, CFG.weights["interview_like"])
        low = score_company(facts_like(FULL), None, [self.signal(make_profile(13))], CFG).values["interview_like"]
        self.assertEqual((1.0, 6.0, 3.0, "low"), (low.value, low.raw_points, low.points, low.confidence.level))
        medium = score_company(facts_like(FULL), None, [self.signal(make_profile(20))], CFG).values["interview_like"]
        self.assertEqual((6.0, 5.1, "medium"), (medium.raw_points, medium.points, medium.confidence.level))

    def test_confidence_is_never_high(self):
        for n in (0, 3, 13, 20, 500):
            p = interview_profile((), confident_at=20) if n == 0 else make_profile(n)
            self.assertNotEqual("high", self.signal(p).score(facts_like(FULL)).confidence.level)

    def test_weight_zero_switches_it_off(self):
        off = with_weight(self, "0.0")
        self.assertEqual(0.0, off.weights["interview_like"])
        profile = make_profile(13)
        signals = build_signals(off, profile)
        facts = neutral_facts(company_id=1, features=FULL, everify="enrolled",
                              grades={"everify": Confidence("high", "test")})
        got = score_company(facts, None, signals, off)
        p = got.values["interview_like"]
        self.assertEqual((1.0, 0.0, 0.0), (p.value, p.raw_points, p.points), "the value is kept, the points are 0")
        self.assertTrue(p.reason.startswith("Like Interview 0"))
        self.assertNotIn(p.reason, got.reasons)
        without = score_company(facts, None, [s for s in signals if s.name != "interview_like"], off)
        self.assertEqual(without.score, got.score)
        self.assertEqual(without.confidence, got.confidence)

    def test_no_profile_scores_zero_with_the_reason(self):
        signals = build_signals(CFG)
        self.assertEqual("interview_like", signals[-1].name)
        got = signals[-1].score(facts_like(FULL))
        self.assertEqual(0.0, got.value)
        self.assertEqual("Fewer than 3 interview companies so far", got.reason)
        self.assertEqual(("low", "no interview companies yet"), (got.confidence.level, got.confidence.basis))

    def test_build_signals_keeps_the_first_eleven_and_appends_the_tilt(self):
        names = [s.name for s in build_signals(CFG, make_profile(5))]
        self.assertEqual(12, len(names))
        self.assertEqual(["live_role", "posting_sponsors", "everify", "cap_exempt", "pay_level", "lca_junior", "small",
                          "reachable_person", "funding", "staffing_risk", "large_later_stage", "interview_like"], names)

    def test_a_company_is_not_lifted_by_its_own_interview(self):
        me = ProfileEntry(1, "Me", "2026-10-10", FULL)
        others = tuple(ProfileEntry(next(_ids), f"Other {i}", "2026-10-01", OTHER) for i in range(3))
        profile = InterviewProfile((me, *others), Confidence("low", "4 interview companies"))
        self.assertEqual(0.0, self.signal(profile).score(facts_like(FULL, company_id=1)).value)
        self.assertEqual(1.0, self.signal(profile).score(facts_like(FULL, company_id=2)).value)


if __name__ == "__main__":
    unittest.main()
