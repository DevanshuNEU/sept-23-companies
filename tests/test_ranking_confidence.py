"""Confidence grades, the rank confidence and the rank reason line (issue #6, Task 2a, ruling 2026-10-22)."""
import unittest
from datetime import date

from outreach.ranking.config import default_ranker_config
from outreach.ranking.confidence import (LEVELS, Assessment, Confidence, RankReason, build_rank_reason, down_one,
                                         factor, grade_evidence, grade_no_role, grade_pay, grade_reach, grade_role,
                                         grade_sponsor, rank_confidence, weaker)
from outreach.ranking.types import CompanyFacts, SignalValue
from tests.contracts import check_signal

CFG = default_ranker_config()
TODAY = date(2026, 10, 21)
HIGH, MEDIUM, LOW = (Confidence(level, f"{level} basis") for level in ("high", "medium", "low"))
DASHES = (chr(0x2013), chr(0x2014))


def evidence(**kw):
    args = dict(confidence="confirmed", locked=False, source_kind="careers_page", observed_on="2026-09-01",
                today=TODAY, old_days=365, label="size")
    args.update(kw)
    return grade_evidence(**args)


class ConfidenceValueTests(unittest.TestCase):
    def test_confidence_needs_a_known_level_a_basis_and_no_dash(self):
        self.assertEqual(LEVELS, ("low", "medium", "high"))
        for level, basis in (("sure", "x"), ("high", ""), ("high", "  "), ("high", "a " + chr(0x2013) + " b"),
                             ("high", "a " + chr(0x2014) + " b"), ("", "x"), (None, "x"), ("high", None)):
            with self.subTest(level=level, basis=basis):
                with self.assertRaises(ValueError):
                    Confidence(level, basis)
        self.assertEqual(Confidence("high", "x").level, "high")

    def test_weaker_takes_the_lower_level_with_its_basis(self):
        self.assertIs(weaker(HIGH, LOW), LOW)
        self.assertIs(weaker(LOW, HIGH), LOW)
        self.assertIs(weaker(MEDIUM, HIGH), MEDIUM)
        self.assertIs(weaker(HIGH, HIGH), HIGH)

    def test_down_one_steps_down_and_never_below_low(self):
        got = down_one(HIGH, "old")
        self.assertEqual((got.level, got.basis), ("medium", "high basis, old"))
        self.assertEqual(down_one(MEDIUM, "old").level, "low")
        self.assertEqual(down_one(LOW, "old").level, "low")

    def test_assessment_is_a_signal_value_and_keeps_its_bounds(self):
        a = Assessment(0.5, "x", HIGH)
        self.assertIsInstance(a, SignalValue)
        self.assertEqual((a.value, a.reason, a.confidence), (0.5, "x", HIGH))
        for bad in (1.5, -1.5, float("nan"), True):
            with self.assertRaises(ValueError):
                Assessment(bad, "x", HIGH)
        with self.assertRaises(Exception):
            a.value = 0.1  # frozen

    def test_assessment_passes_the_signal_contract_check(self):
        class Fake:
            name = "fake"

            def score(self, facts):
                return Assessment(0.25, "A plain reason", LOW)

        facts = CompanyFacts(company_id=1, lanes=(), best_posting_id=None, best_role_age_days=None, everify=None,
                             cap_exempt=None, staffing_risk=None, lca_jr_recent=0, lca_level1_recent=0,
                             pay_level=None, size_low=None, size_high=None, stage=None, funded_months_ago=None,
                             reachable_person=False)
        got = check_signal(self, Fake(), facts)
        self.assertEqual(got.confidence, LOW)


class GradeEvidenceTests(unittest.TestCase):
    def test_grade_evidence_maps_the_column(self):
        for column, level in (("confirmed", "high"), ("estimate", "medium"), ("guess", "low")):
            with self.subTest(column=column):
                got = evidence(confidence=column)
                self.assertEqual(got.level, level)
                self.assertEqual(got.basis, f"size from careers_page, {column}")

    def test_a_locked_row_is_high_even_when_its_column_says_guess(self):
        got = evidence(confidence="guess", locked=True)
        self.assertEqual((got.level, got.basis), ("high", "size set by hand"))

    def test_a_guess_never_reads_high_unless_locked(self):
        for observed in ("2026-10-01", "2020-01-01", None):
            with self.subTest(observed=observed):
                self.assertEqual(evidence(confidence="guess", observed_on=observed).level, "low")

    def test_grade_evidence_with_no_row_is_low_and_says_so(self):
        got = evidence(confidence=None, source_kind=None, observed_on=None, label="size")
        self.assertEqual((got.level, got.basis), ("low", "no size evidence"))
        got = evidence(confidence=None, source_kind=None, observed_on=None, label="funding")
        self.assertEqual(got.basis, "no funding evidence")

    def test_an_unrecognised_column_is_low_never_high(self):
        self.assertEqual(evidence(confidence="certain").level, "low")

    def test_old_evidence_drops_one_level_and_says_how_old(self):
        got = evidence(observed_on="2024-01-02")
        self.assertEqual(got.level, "medium")
        self.assertTrue(got.basis.endswith("observed 2024-01-02, over 365 days old"), got.basis)
        self.assertEqual(evidence(confidence="guess", observed_on="2024-01-02").level, "low")
        self.assertEqual(evidence(confidence="estimate", observed_on="2024-01-02").level, "low")
        fresh = evidence(observed_on="2026-01-02")
        self.assertEqual((fresh.level, fresh.basis), ("high", "size from careers_page, confirmed"))

    def test_the_old_cutoff_is_strictly_more_than_old_days(self):
        exactly = date.fromordinal(TODAY.toordinal() - 365).isoformat()
        over = date.fromordinal(TODAY.toordinal() - 366).isoformat()
        self.assertEqual(evidence(observed_on=exactly).level, "high")
        self.assertEqual(evidence(observed_on=over).level, "medium")

    def test_a_timestamp_is_read_as_its_date(self):
        self.assertEqual(evidence(observed_on="2024-01-02T10:00:00Z").level, "medium")


class GradeRoleTests(unittest.TestCase):
    def test_grade_role_levels(self):
        cases = (
            (dict(board_state="read_ok", posted_known=True), "high",
             "listed at the board's last good read, posted date known"),
            (dict(board_state="read_ok", posted_known=False), "medium",
             "listed at the board's last good read, no posted date"),
            (dict(board_state="read_failed", posted_known=True), "medium",
             "last board read failed, last listed 2026-10-01"),
            (dict(board_state="never_read", posted_known=True), "low",
             "board not read by this system yet, listing is from an import dated 2026-10-01"),
            (dict(board_state="no_board", posted_known=True), "low", "posting is not from a board read"),
        )
        for kw, level, basis in cases:
            with self.subTest(**kw):
                got = grade_role(last_seen_on="2026-10-01", **kw)
                self.assertEqual((got.level, got.basis), (level, basis))

    def test_an_unknown_board_state_is_refused(self):
        with self.assertRaises(ValueError):
            grade_role(board_state="fine", posted_known=True, last_seen_on="2026-10-01")

    def test_grade_no_role_tells_no_board_from_no_roles(self):
        cases = (("read_ok", "high", "board read ok, no matching posting listed"),
                 ("read_failed", "medium", "board read failed, roles unknown"),
                 ("never_read", "low", "board not read yet, roles unknown"),
                 ("no_board", "low", "no board found yet: roles are unknown, not absent"))
        for state, level, basis in cases:
            with self.subTest(state=state):
                got = grade_no_role(company_board_state=state, stale_kinds=(), stale_reason="x")
                self.assertEqual((got.level, got.basis), (level, basis))
        old = grade_no_role(company_board_state="read_ok", stale_kinds=("old",), stale_reason="posted 2016-03-02")
        self.assertEqual((old.level, old.basis), ("high", "only stale postings: posted 2016-03-02"))
        both = grade_no_role(company_board_state="read_ok", stale_kinds=("not_on_board", "old"), stale_reason="r")
        self.assertEqual(both.level, "high")
        absent = grade_no_role(company_board_state="read_ok", stale_kinds=("not_on_board",), stale_reason="gone")
        self.assertEqual((absent.level, absent.basis),
                         ("medium", "only stale postings: gone; a first empty read can be a glitch"))

    def test_grade_sponsor_final_rules_only_and_none(self):
        none = grade_sponsor(provisional=None)
        self.assertEqual(none.level, "low")
        final = grade_sponsor(provisional=False)
        self.assertEqual((final.level, final.basis), ("high", "reader confirmed the wording with a quote"))
        rules = grade_sponsor(provisional=True)
        self.assertEqual((rules.level, rules.basis), ("medium", "phrase rules only, reader has not confirmed"))

    def test_grade_pay_levels(self):
        cases = (
            (dict(source="posted", straddles=False, filings=0), "high",
             "posted range, same level at the minimum and the midpoint"),
            (dict(source="posted", straddles=True, filings=0), "medium", "level changes inside the posted range"),
            (dict(source="lca", straddles=False, filings=12), "medium", "LCA history, 12 junior filings"),
            (dict(source="lca", straddles=False, filings=10), "medium", "LCA history, 10 junior filings"),
            (dict(source="lca", straddles=False, filings=5), "low", "LCA history, 5 junior filings"),
            (dict(source="", straddles=False, filings=0), "low", "no posted pay and too few LCA filings"),
        )
        for kw, level, basis in cases:
            with self.subTest(**kw):
                got = grade_pay(medium_filings=10, **kw)
                self.assertEqual((got.level, got.basis), (level, basis))

    def test_grade_reach_levels(self):
        cases = (
            (dict(via="person", has_team_source=True), "high", "works on the team, source on file"),
            (dict(via="person", has_team_source=False), "medium", "person on file, no source"),
            (dict(via="hn_post", has_team_source=False), "medium", "HN post asks for email"),
            (dict(via="", has_team_source=False), "low", "no person recorded yet"),
        )
        for kw, level, basis in cases:
            with self.subTest(**kw):
                got = grade_reach(**kw)
                self.assertEqual((got.level, got.basis), (level, basis))

    def test_unknown_inputs_are_refused_not_graded_high(self):
        with self.assertRaises(ValueError):
            grade_pay(source="guessed", straddles=False, filings=0, medium_filings=10)
        with self.assertRaises(ValueError):
            grade_reach(via="rumor", has_team_source=True)
        with self.assertRaises(ValueError):
            grade_no_role(company_board_state="odd", stale_kinds=(), stale_reason="x")


class FactorTests(unittest.TestCase):
    def test_factor_never_boosts_and_orders_the_levels(self):
        self.assertEqual([factor(Confidence(l, "x"), CFG) for l in ("high", "medium", "low")], [1.0, 0.85, 0.5])
        for l in LEVELS:
            self.assertLessEqual(factor(Confidence(l, "x"), CFG), 1.0)


class RankConfidenceTests(unittest.TestCase):
    def test_rank_confidence_follows_the_points_not_the_count(self):
        got = rank_confidence({"live_role": (40, HIGH), "everify": (12, HIGH)}, CFG)
        self.assertEqual((got.level, got.basis), ("high", "100% of the points rest on high-confidence facts"))
        got = rank_confidence({"live_role": (30, HIGH), "everify": (10, MEDIUM), "small": (10, LOW)}, CFG)
        self.assertEqual((got.level, got.basis),
                         ("medium", "60% of the points rest on high-confidence facts, 20% on medium-confidence "
                                    "facts, 20% on guesses or missing data (small)"))
        got = rank_confidence({"small": (12, LOW), "funding": (6, HIGH)}, CFG)
        self.assertEqual((got.level, got.basis),
                         ("low", "33% of the points rest on high-confidence facts, 67% on guesses or missing data "
                                 "(small)"))

    def test_a_negative_part_counts_by_its_absolute_value(self):
        got = rank_confidence({"staffing_risk": (-200, HIGH), "small": (12, LOW)}, CFG)
        self.assertEqual(got.level, "high")
        got = rank_confidence({"staffing_risk": (-200, LOW), "live_role": (40, HIGH)}, CFG)
        self.assertEqual(got.level, "low")

    def test_no_non_zero_part_is_low(self):
        for parts in ({}, {"small": (0, HIGH), "funding": (0.0, HIGH)}):
            got = rank_confidence(parts, CFG)
            self.assertEqual((got.level, got.basis), ("low", "no signal has evidence"))

    def test_zero_parts_are_ignored(self):
        got = rank_confidence({"live_role": (40, HIGH), "funding": (0, LOW)}, CFG)
        self.assertEqual((got.level, got.basis), ("high", "100% of the points rest on high-confidence facts"))

    def test_the_low_share_alone_can_make_it_low(self):
        got = rank_confidence({"live_role": (20, HIGH), "small": (10, LOW), "funding": (10, LOW)}, CFG)
        self.assertEqual(got.level, "low")  # the low share is exactly 0.5
        self.assertIn("(funding, small)", got.basis)  # ties sort by name

    def test_a_high_largest_below_the_high_share_is_medium(self):
        got = rank_confidence({"live_role": (40, HIGH), "small": (12, MEDIUM), "everify": (12, MEDIUM),
                               "funding": (6, MEDIUM)}, CFG)
        self.assertEqual(got.level, "medium")

    def test_a_medium_largest_is_never_high(self):
        got = rank_confidence({"live_role": (40, MEDIUM), "everify": (12, HIGH)}, CFG)
        self.assertEqual(got.level, "medium")

    def test_the_basis_names_at_most_three_low_signals_largest_first(self):
        parts = {"reachable_person": (8, LOW), "pay_level": (10, LOW), "large_later_stage": (20, LOW),
                 "cap_exempt": (5, LOW), "live_role": (400, HIGH)}
        got = rank_confidence(parts, CFG)
        self.assertIn("(large later stage, pay level, reachable person)", got.basis)
        self.assertNotIn("cap exempt", got.basis)

    def test_float_noise_at_the_share_boundary_does_not_flip_the_level(self):
        parts = {"a": (0.1, HIGH), "b": (0.2, HIGH), "c": (0.1, HIGH), "d": (0.1, HIGH), "e": (0.1, HIGH),
                 "f": (0.1, HIGH), "g": (0.1, MEDIUM), "h": (0.1, MEDIUM), "i": (0.1, MEDIUM)}
        # high share is 0.7 of 1.0, give or take float noise: the boundary is inclusive
        self.assertEqual(rank_confidence(parts, CFG).level, "high")


class RankReasonTests(unittest.TestCase):
    def test_rank_reason_line_states_the_confidence_and_what_rests_on_guesses(self):
        conf = Confidence("high", "100% of the points rest on high-confidence facts")
        reasons = ["Open new grad role: Software Engineer, New Grad, posted 3 days ago",
                   "E-Verify enrolled (STEM OPT needs it by July 2027)"]
        got = build_rank_reason(reasons, conf, CFG)
        self.assertIsInstance(got, RankReason)
        self.assertEqual(got.line, "Open new grad role: Software Engineer, New Grad, posted 3 days ago; E-Verify "
                                   "enrolled (STEM OPT needs it by July 2027) (high confidence: 100% of the points "
                                   "rest on high-confidence facts)")
        self.assertEqual(got.confidence, conf)
        four = build_rank_reason(["a", "b", "c", "d"], conf, CFG)
        self.assertEqual(four.text, "a; b; c")
        none = build_rank_reason([], Confidence("low", "no signal has evidence"), CFG)
        self.assertEqual(none.line, "No signal has evidence yet (low confidence: no signal has evidence)")

    def test_no_generated_text_has_a_dash(self):
        texts = [
            evidence().basis, evidence(confidence=None).basis, evidence(observed_on="2020-01-01").basis,
            evidence(locked=True).basis,
            grade_role(board_state="read_failed", posted_known=True, last_seen_on="2026-10-01").basis,
            grade_role(board_state="never_read", posted_known=True, last_seen_on="2026-10-01").basis,
            grade_no_role(company_board_state="no_board", stale_kinds=(), stale_reason="x").basis,
            grade_no_role(company_board_state="read_ok", stale_kinds=("not_on_board",), stale_reason="x").basis,
            grade_sponsor(provisional=True).basis, grade_pay(source="lca", straddles=False, filings=4,
                                                            medium_filings=10).basis,
            grade_reach(via="hn_post", has_team_source=False).basis,
            rank_confidence({"small": (12, LOW), "funding": (6, HIGH)}, CFG).basis,
            rank_confidence({}, CFG).basis,
            build_rank_reason(["a", "b"], rank_confidence({"small": (12, LOW)}, CFG), CFG).line,
            build_rank_reason([], rank_confidence({}, CFG), CFG).line,
        ]
        for text in texts:
            for dash in DASHES:
                self.assertNotIn(dash, text)


if __name__ == "__main__":
    unittest.main()
