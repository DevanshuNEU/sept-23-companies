"""Scoring and the stored shape (issue #6, Task 8).

score = sum of points; a positive component counts by its confidence and a warning counts in full; a staffing or
large later-stage company sorts below every other and stays listed; unknown size and stage score exactly neutral."""
import dataclasses
import json
import unittest

from outreach.ranking.config import default_ranker_config
from outreach.ranking.confidence import LEVELS, Assessment, Confidence
from outreach.ranking.facts import RankFacts
from outreach.ranking.hide import Hide
from outreach.ranking.roles import Best
from outreach.ranking.score import Points, Scored, list_order, score_company, to_columns
from outreach.ranking.signals import build_signals
from tests.ranking_helpers import neutral_facts

CFG = default_ranker_config()
SIGNALS = build_signals(CFG)
NAMES = [s.name for s in SIGNALS]
EN, EM = chr(0x2013), chr(0x2014)
STALE_REASON = "posted 2016-03-02, over 12 months"


def graded(level="high", **facts) -> RankFacts:
    grades = {n: Confidence(level, f"test {level}") for n in NAMES}
    grades.update(facts.pop("grades", {}))
    return neutral_facts(grades=grades, **facts)


def score(facts, hide=None, **kw) -> Scored:
    return score_company(facts, hide, SIGNALS, CFG, **kw)


def fresh_role(**facts):
    base = dict(lanes=("level_role",), best_role_age_days=3, best_title="New Grad Engineer",
                best_level_tier="new_grad", best_sponsorship="yes", best_posting_id=7)
    return {**base, **facts}


def stale_best(posting_id=9) -> Best:
    return Best(posting_id, "level_role", f"Old {EM} role", "new_grad", "silent", None, "", "", "2016-03-02",
                "2016-03-02", "read_ok", True, stale_reason=STALE_REASON)


class ScoreTests(unittest.TestCase):
    def test_score_is_the_weighted_sum_and_rounds_to_four_places(self):
        facts = graded(**fresh_role(everify="enrolled", cap_exempt="likely", lca_jr_recent=3, funded_months_ago=7,
                                    size_low=120, size_high=120, stage="seed"))
        got = score(facts)
        w = CFG.weights
        want = (w["live_role"] * 1.0 + w["posting_sponsors"] * 1.0 + w["everify"] * 1.0 + w["cap_exempt"] * 0.7
                + w["lca_junior"] * 0.3 + w["small"] * 1.0 + w["funding"] * round(1 - 7 / 18, 4))
        self.assertEqual(got.score, round(want, 4))
        self.assertEqual(round(got.score, 4), got.score)
        self.assertEqual(got.score, round(sum(p.points for p in got.values.values()), 4))

    def test_points_carry_value_weight_raw_and_discounted_points(self):
        got = score(graded(everify="enrolled"))
        p = got.values["everify"]
        self.assertIsInstance(p, Points)
        self.assertEqual((p.value, p.weight, p.raw_points, p.points), (1.0, 12.0, 12.0, 12.0))
        self.assertEqual(list(got.values), NAMES)

    def test_reasons_are_non_zero_signals_largest_first(self):
        self.assertEqual(score(neutral_facts()).reasons, ())
        got = score(graded(**fresh_role(everify="enrolled", lca_jr_recent=10, cap_exempt="likely")))
        self.assertEqual(len(got.reasons), 5)
        self.assertTrue(got.reasons[0].startswith("Open new grad role"))
        pts = [abs(got.values[n].points) for n in NAMES if got.values[n].raw_points != 0]
        order = sorted(((abs(p.points), n) for n, p in got.values.items() if p.raw_points != 0),
                       key=lambda x: (-x[0], x[1]))
        self.assertEqual(got.reasons, tuple(got.values[n].reason for _p, n in order))
        self.assertEqual(sorted(pts, reverse=True), [abs(got.values[n].points) for _p, n in order])
        for text in got.reasons:
            self.assertNotIn("Size unknown", text)
            self.assertNotIn("No open role yet", text)

    def test_reasons_rank_by_discounted_points_and_include_warnings(self):
        low = Confidence("low", "guess")
        facts = graded(everify="enrolled", reach_via="person", reach_note="Reachable person: Ada, engineer",
                       grades={"everify": low})  # everify 12 raw but 6 counted, the person 8 counted in full
        got = score(facts)
        self.assertEqual(got.reasons[0], "Reachable person: Ada, engineer")
        warned = score(graded(staffing_risk="high", everify="not_enrolled"))
        self.assertEqual(warned.reasons[0], warned.values["staffing_risk"].reason)
        self.assertIn(warned.values["everify"].reason, warned.reasons)

    def test_reason_ties_break_by_signal_name(self):
        got = score(graded(everify="enrolled", lca_jr_recent=10))  # 12.0 points each
        self.assertEqual(got.reasons[0], got.values["everify"].reason)
        self.assertEqual(got.reasons[1], got.values["lca_junior"].reason)

    def test_large_later_stage_with_a_live_new_grad_role_is_below_a_clean_company_with_nothing(self):
        big = neutral_facts(**fresh_role(everify="enrolled", reach_via="person", reach_note="Reachable person: A, founder",
                                         stage="series_d"))
        self.assertLess(score(big).score, score(neutral_facts()).score)
        self.assertLess(score(big).score, -100)

    def test_staffing_risk_high_is_below_a_clean_twin(self):
        base = dict(**fresh_role(everify="enrolled"), size_low=50, size_high=50)
        clean, flagged = neutral_facts(**base), neutral_facts(**base, staffing_risk="high")
        a, b = score(clean), score(flagged)
        self.assertGreaterEqual(a.score - b.score, 200)
        a_row, b_row = dataclasses.replace(a, company_id=1), dataclasses.replace(b, company_id=2)
        self.assertEqual([r.company_id for r in list_order([b_row, a_row])], [1, 2])

    def test_a_staffing_flag_sinks_a_company_below_the_best_of_everything_else(self):
        best = score(graded(**fresh_role(everify="enrolled", cap_exempt="confirmed", lca_jr_recent=50,
                                         funded_months_ago=0, size_low=10, size_high=10, reach_via="person",
                                         reach_note="x", pay_level=4)))
        worst_flagged = score(graded(staffing_risk="high", everify="enrolled"))
        self.assertLess(worst_flagged.score, 0)
        self.assertLess(worst_flagged.score, best.score - 200)
        self.assertLess(score(graded(stage="series_d", everify="enrolled")).score, 0)

    def test_unknown_size_company_outranks_a_known_large_later_stage_one_but_not_a_small_one(self):
        unknown = score(neutral_facts())
        large = score(neutral_facts(size_low=400, size_high=400))
        late = score(neutral_facts(stage="series_d"))
        small = score(neutral_facts(size_low=100, size_high=100, stage="seed"))
        self.assertEqual(unknown.score, 0.0)
        self.assertGreater(unknown.score, large.score)
        self.assertGreater(unknown.score, late.score)
        self.assertGreater(small.score, unknown.score)

    def test_unknown_size_and_stage_score_exactly_neutral(self):
        for facts in (neutral_facts(), neutral_facts(stage="seed"), neutral_facts(stage="series_b")):
            got = score(facts)
            self.assertEqual(got.score, 0.0)
            self.assertEqual(got.values["small"].points, 0.0)
            self.assertEqual(got.values["large_later_stage"].points, 0.0)

    def test_a_positive_component_counts_by_its_confidence_and_a_warning_counts_in_full(self):
        for level, want in (("high", 12.0), ("medium", 10.2), ("low", 6.0)):
            with self.subTest(level=level):
                p = score(graded(level, everify="enrolled")).values["everify"]
                self.assertEqual((p.raw_points, p.points), (12.0, want))
        p = score(graded("low", staffing_risk="high")).values["staffing_risk"]
        self.assertEqual((p.raw_points, p.points), (-200.0, -200.0))
        p = score(graded("low", everify="not_enrolled")).values["everify"]
        self.assertEqual((p.raw_points, p.points), (-6.0, -6.0))

    def test_a_guess_never_outranks_the_same_fact_confirmed(self):
        sure = score(graded(everify="enrolled", grades={"everify": Confidence("high", "everify from lca, confirmed")}))
        guess = score(graded(everify="enrolled", grades={"everify": Confidence("low", "everify from lca, guess")}))
        self.assertEqual(round(sure.score - guess.score, 4), 6.0)
        self.assertGreaterEqual(LEVELS.index(sure.confidence.level), LEVELS.index(guess.confidence.level))
        self.assertEqual((sure.confidence.level, guess.confidence.level), ("high", "low"))

    def test_every_component_has_a_confidence_and_basis_even_when_unknown(self):
        got = score(neutral_facts())
        self.assertEqual(len(got.values), 11)
        for name, p in got.values.items():
            with self.subTest(signal=name):
                self.assertIn(p.confidence.level, LEVELS)
                self.assertEqual(p.confidence.level, "low")
                self.assertTrue(p.confidence.basis.strip())

    def test_rank_reason_line_states_the_confidence_and_names_the_guesses(self):
        # Chosen so the high share is 52 of 76 points (68%), under the 70% bar: medium.
        low = Confidence("low", "guess")
        facts = graded(**fresh_role(best_sponsorship="silent", everify="enrolled", lca_jr_recent=10, size_low=100, size_high=100,
                                    grades={"small": low, "lca_junior": low}))
        got = score(facts)
        self.assertEqual(got.confidence.level, "medium")
        self.assertTrue(got.rank_reason.line.endswith(")"))
        self.assertIn("(medium confidence: ", got.rank_reason.line)
        self.assertIn("lca junior, small", got.rank_reason.line)
        self.assertEqual(got.rank_reason.text, "; ".join(got.reasons[:3]))
        self.assertEqual(got.rank_reason.line, f"{got.rank_reason.text} (medium confidence: {got.confidence.basis})")
        self.assertEqual(len(got.rank_reason.text.split("; ")), 3)

    def test_unknown_everything_company_has_low_rank_confidence(self):
        got = score(neutral_facts())
        self.assertEqual(got.confidence, Confidence("low", "no signal has evidence"))
        self.assertEqual(got.rank_reason.text, "No signal has evidence yet")

    def test_extra_flags_are_appended_to_flags_and_change_no_points(self):
        facts = graded(everify="enrolled", weak_flags=("81 open roles",), ignored_postings=2)
        plain = score(facts)
        extra = score(facts, extra_flags=("postings disagree on sponsorship",))
        self.assertEqual(extra.flags, plain.flags + ("postings disagree on sponsorship",))
        self.assertEqual(plain.flags, ("81 open roles", "2 postings ignored: board match doubtful"))
        self.assertEqual((extra.score, extra.values, extra.reasons), (plain.score, plain.values, plain.reasons))

    def test_an_ignored_posting_gets_its_flag_and_none_gets_none(self):
        self.assertEqual(score(neutral_facts(ignored_postings=1)).flags, ("1 posting ignored: board match doubtful",))
        self.assertEqual(score(neutral_facts()).flags, ())

    def test_a_company_with_only_a_stale_posting_scores_with_no_role_credit_and_keeps_its_flag(self):
        got = score(neutral_facts(stale_postings=(stale_best(),)))
        self.assertEqual(got.values["live_role"].points, 0.0)
        self.assertIn(f"1 stale posting not counted as live: {STALE_REASON}", got.flags)
        self.assertIsNone(got.hide)
        stored = json.loads(to_columns(got)[0])
        self.assertEqual(stored["stale_postings"], [{"posting_id": 9, "title": "Old - role", "reason": STALE_REASON}])

    def test_several_stale_postings_name_the_newest(self):
        newest = dataclasses.replace(stale_best(9), age_days=2500)
        older = dataclasses.replace(newest, posting_id=4, age_days=3500,
                                    stale_reason="posted 2015-01-01, over 12 months")
        got = score(neutral_facts(stale_postings=(older, newest)))  # the newest is not first: it is found by age
        self.assertIn(f"2 stale postings not counted as live: {STALE_REASON}", got.flags)

    def test_postings_on_an_inactive_board_get_their_own_flag(self):
        both = score(neutral_facts(ignored_postings=3, inactive_postings=1)).flags
        self.assertEqual(both, ("2 postings ignored: board match doubtful", "1 posting ignored: board no longer read"))
        only = score(neutral_facts(ignored_postings=2, inactive_postings=2)).flags
        self.assertEqual(only, ("2 postings ignored: board no longer read",))
        self.assertEqual(score(neutral_facts(ignored_postings=2, inactive_postings=2)).ignored_postings, 2)

    def test_every_reason_is_free_of_dashes(self):
        facts = graded(**fresh_role(everify="enrolled", cap_exempt="confirmed", lca_jr_recent=5, lca_level1_recent=2,
                                    funded_months_ago=4, size_low=80, size_high=80, stage="seed", pay_level=2,
                                    pay_note=f"{EM}$130k{EN}$150k", reach_via="person",
                                    reach_note=f"Reachable person: Ana {EM} Lopez, founder", staffing_risk="medium",
                                    stale_postings=(stale_best(),)))
        facts = dataclasses.replace(facts, best_title=f"Engineer {EM} New Grad {EN} 2026")
        got = score(facts)
        texts = [*got.reasons, got.rank_reason.line, *got.flags, got.confidence.basis]
        texts += [p.reason for p in got.values.values()] + [p.confidence.basis for p in got.values.values()]
        for text in texts:
            self.assertNotIn(EN, text)
            self.assertNotIn(EM, text)
        self.assertNotIn(EN, to_columns(got)[0])
        self.assertNotIn(EM, to_columns(got)[0])

    def test_a_rerun_gives_the_same_scored(self):
        facts = graded(**fresh_role(everify="enrolled"))
        self.assertEqual(score(facts), score(facts))
        self.assertEqual(to_columns(score(facts)), to_columns(score(facts)))

    def test_a_weight_missing_from_the_config_is_a_key_error(self):
        cfg = dataclasses.replace(CFG, weights={k: v for k, v in CFG.weights.items() if k != "funding"})
        with self.assertRaises(KeyError):
            score_company(neutral_facts(), None, SIGNALS, cfg)

    def test_no_negative_zero_reaches_the_stored_text(self):
        class NegZero:
            name = "live_role"

            def score(self, facts):
                return Assessment(-0.0, "Nothing", Confidence("low", "test"))

        got = score_company(neutral_facts(), None, [NegZero()], CFG)
        text = to_columns(got)[0]
        self.assertNotIn("-0.0", text)
        self.assertEqual(repr(got.score), "0.0")


class ColumnsTests(unittest.TestCase):
    def test_columns_are_valid_json_with_the_pinned_keys(self):
        facts = graded(**fresh_role(everify="enrolled", size_low=50, size_high=50), name="Acme", ignored_postings=1,
                       stale_postings=(stale_best(),))
        got = score(facts, Hide("no_sponsorship", "refused in a posting", True, posting_id=7))
        signals, lanes = to_columns(got)
        stored, lane_list = json.loads(signals), json.loads(lanes)
        self.assertEqual(lane_list, list(got.lanes))
        self.assertIsInstance(lane_list, list)
        self.assertEqual(set(stored), {"values", "reasons", "hide", "flags", "best_posting_id", "ignored_postings",
                                       "stale_postings", "confidence", "rank_reason"})
        self.assertEqual(list(stored["values"]), sorted(NAMES))
        for name, v in stored["values"].items():
            self.assertEqual(set(v), {"value", "weight", "raw_points", "points", "reason", "confidence", "basis"}, name)
            self.assertIn(v["confidence"], LEVELS)
            self.assertTrue(v["basis"])
        self.assertEqual(set(stored["hide"]), {"kind", "reason", "provisional"})
        self.assertEqual(stored["hide"]["kind"], "no_sponsorship")
        self.assertIs(stored["hide"]["provisional"], True)
        self.assertEqual(set(stored["confidence"]), {"level", "basis"})
        self.assertEqual(set(stored["rank_reason"]), {"text", "level", "basis", "line"})
        self.assertEqual(stored["rank_reason"]["line"], got.rank_reason.line)
        self.assertEqual(stored["best_posting_id"], 7)
        self.assertEqual(stored["ignored_postings"], 1)
        self.assertEqual(stored["reasons"], list(got.reasons))
        self.assertEqual(stored["flags"], list(got.flags))
        self.assertIsInstance(stored["stale_postings"], list)
        self.assertEqual(set(stored["stale_postings"][0]), {"posting_id", "title", "reason"})

    def test_a_company_with_no_hide_stores_null_and_empty_lists(self):
        stored = json.loads(to_columns(score(neutral_facts()))[0])
        self.assertIsNone(stored["hide"])
        self.assertIsNone(stored["best_posting_id"])
        self.assertEqual((stored["flags"], stored["reasons"], stored["stale_postings"]), ([], [], []))
        self.assertEqual(json.loads(to_columns(score(neutral_facts()))[1]), [])

    def test_the_text_has_sorted_keys(self):
        text = to_columns(score(graded(everify="enrolled")))[0]
        self.assertEqual(text, json.dumps(json.loads(text), sort_keys=True))


class ListOrderTests(unittest.TestCase):
    def scored(self, company_id, hide=None, **facts):
        return dataclasses.replace(score(neutral_facts(**facts), hide), company_id=company_id)

    def test_list_order_excludes_every_hide_and_breaks_ties_by_company_id(self):
        rows = [self.scored(5), self.scored(2, everify="enrolled"), self.scored(3),
                self.scored(4, Hide("no_sponsorship", "r", True)), self.scored(6, Hide("no_go", "r", False)),
                self.scored(1)]
        got = list_order(rows)
        self.assertEqual([r.company_id for r in got], [2, 1, 3, 5])

    def test_equal_scores_order_by_company_id_whatever_the_input_order(self):
        rows = [self.scored(9), self.scored(3), self.scored(7)]
        self.assertEqual([r.company_id for r in list_order(rows)], [3, 7, 9])
        self.assertEqual([r.company_id for r in list_order(reversed(rows))], [3, 7, 9])


if __name__ == "__main__":
    unittest.main()
