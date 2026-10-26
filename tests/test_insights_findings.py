"""What the interview companies share (#81, Task 5): counts, never a rate below the minimum sample, and "too few to
tell" when a comparison cannot be drawn. Pure: rows in, findings out."""
import dataclasses
import itertools
import unittest

from outreach.insights.bands import Features
from outreach.insights.config import InsightsConfig, default_insights_config
from outreach.insights.dataset import OutcomeRow
from outreach.insights.findings import DIMENSIONS, Shared, shared_findings

CFG = default_insights_config()
_ids = itertools.count(1)
BASE = OutcomeRow(
    company_id=0, name="Co", status="working", merged_ids=(0,), best_outcome="interview", outcome_source="touch",
    touches=1, channels=("linkedin_request",), best_channel="linkedin_request", best_role_kind="engineer",
    best_lane="level_role", best_person=None, first_touch_on="2026-10-01", first_reply_on=None, days_to_reply=None,
    interview_on="2026-10-10", offer_on=None, size_low=None, size_high=None, size_confidence=None, stage=None,
    hq=None, everify=None, sponsorship=None, board_system=None, level_tier=None, level_basis="", intern=False,
    features=Features(), score=None, rank=None)


def row(outcome: str = "interview", *, size: str | None = None, stage: str | None = None, **over) -> OutcomeRow:
    cid = next(_ids)
    feats = Features(size_band=size, stage_band=stage, board_system=over.get("board_system"))
    return dataclasses.replace(BASE, company_id=cid, name=f"Co {cid}", merged_ids=(cid,), best_outcome=outcome,
                               features=feats, **over)


def thirteen() -> list[OutcomeRow]:
    """13 interview companies: 9 of 51 to 300 people, 1 of 1 to 50, 1 over 1,000 and 2 unknown; 2 known stages."""
    sizes = ["51 to 300"] * 9 + ["1 to 50", "over 1,000", None, None]
    stages = ["Series A or B", "seed or earlier"] + [None] * 11
    return [row(size=s, stage=st) for s, st in zip(sizes, stages)]


def by_dimension(found: list[Shared]) -> dict[str, Shared]:
    return {s.dimension: s for s in found}


class FindingsTest(unittest.TestCase):
    def test_one_finding_per_dimension(self):
        found = shared_findings(thirteen(), CFG)
        self.assertEqual(sorted(DIMENSIONS), sorted(s.dimension for s in found))
        self.assertEqual(9, len(DIMENSIONS))

    def test_a_majority_value_is_shared_with_counts(self):
        size = by_dimension(shared_findings(thirteen(), CFG))["size_band"]
        self.assertEqual(("shared", "51 to 300", 9, 13, 11), (size.kind, size.value, size.count, size.of, size.known))
        self.assertEqual("9 of your 13 interview companies have 51 to 300 people (size known for 11).", size.text)
        self.assertEqual((("51 to 300", 9), ("1 to 50", 1), ("over 1,000", 1)), size.spread)

    def test_no_rate_below_the_group_minimum(self):
        rows = thirteen() + [row("replied", size="51 to 300") for _ in range(40)]
        found = shared_findings(rows, CFG)
        for s in found:
            self.assertFalse(s.enough, s.dimension)
            self.assertNotIn("%", s.text)
            self.assertNotIn("%", s.compare_text)
        size = by_dimension(found)["size_band"]
        self.assertEqual("Too few to tell whether this sets them apart: that takes 20 interview companies, and you "
                         "have 13.", size.compare_text)
        self.assertEqual((40, 40), (size.compare_of, size.compare_known))

    def test_at_the_minimum_the_comparison_is_drawn(self):
        rows = [row(size="51 to 300") for _ in range(15)] + [row(size="1 to 50") for _ in range(5)]
        rows += [row("replied", size="51 to 300") for _ in range(12)] + [row("none", size="over 1,000")
                                                                          for _ in range(18)]
        size = by_dimension(shared_findings(rows, CFG))["size_band"]
        self.assertTrue(size.enough)
        self.assertEqual((12, 30, 30), (size.compare_count, size.compare_of, size.compare_known))
        self.assertEqual("15 of your 20 interview companies have 51 to 300 people.", size.text)
        self.assertEqual("Of the 30 companies you reached without an interview, 12 of 30 with a known size do too.",
                         size.compare_text)

    def test_enough_interview_companies_but_too_few_known(self):
        rows = [row(size="51 to 300") for _ in range(12)] + [row() for _ in range(10)]
        rows += [row("none", size="1 to 50") for _ in range(30)]
        size = by_dimension(shared_findings(rows, CFG))["size_band"]
        self.assertFalse(size.enough)
        self.assertEqual("Too few to tell whether this sets them apart: that takes 20 with a known size, and you "
                         "have 12.", size.compare_text)

    def test_too_few_known_in_the_comparison_group(self):
        rows = [row(size="51 to 300") for _ in range(20)] + [row("none", size="1 to 50") for _ in range(5)]
        size = by_dimension(shared_findings(rows, CFG))["size_band"]
        self.assertFalse(size.enough)
        self.assertEqual("Too few to tell whether this sets them apart: that takes 20 companies reached without an "
                         "interview with a known size, and you have 5.", size.compare_text)

    def test_no_majority_is_mixed_and_lists_the_spread(self):
        boards = ["ashby"] * 3 + ["greenhouse"] * 3 + ["lever"] * 2 + ["workable", "icims"]
        rows = [row(board_system=b) for b in boards]
        board = by_dimension(shared_findings(rows, CFG))["board_system"]
        self.assertEqual(("mixed", None, 10), (board.kind, board.value, board.known))
        self.assertEqual("No one job board stands out: Ashby 3, Greenhouse 3, Lever 2 and 2 more.", board.text)
        self.assertEqual("", board.compare_text)
        self.assertFalse(board.enough)
        self.assertEqual((("ashby", 3), ("greenhouse", 3), ("lever", 2), ("icims", 1), ("workable", 1)),
                         board.spread)

    def test_unknown_is_counted_but_never_shared(self):
        stage = by_dimension(shared_findings(thirteen(), CFG))["stage_band"]
        self.assertEqual(("unknown", 13, 2), (stage.kind, stage.of, stage.known))
        self.assertEqual("Stage is known for only 2 of 13, too few to tell.", stage.text)
        self.assertEqual("", stage.compare_text)
        self.assertNotIn(None, [v for v, _c in stage.spread])
        # Eleven companies with no stage are not a shared stage.
        self.assertEqual(2, sum(c for _v, c in stage.spread))

    def test_no_recorded_way_in_is_unknown(self):
        rows = [row(best_channel="other", best_role_kind="other") for _ in range(9)]
        rows += [row(best_channel=None, best_role_kind=None) for _ in range(2)]
        rows += [row(best_channel="email", best_role_kind="founder") for _ in range(2)]
        found = by_dimension(shared_findings(rows, CFG))
        self.assertEqual(("unknown", 13, 2), (found["best_channel"].kind, found["best_channel"].of,
                                              found["best_channel"].known))
        self.assertEqual("Way in is known for only 2 of 13, too few to tell.", found["best_channel"].text)
        self.assertEqual(("unknown", 2), (found["best_role_kind"].kind, found["best_role_kind"].known))

    def test_three_is_the_smallest_shared_count(self):
        rows = [row(size="51 to 300"), row(size="51 to 300"), row(size="1 to 50")]
        size = by_dimension(shared_findings(rows, CFG))["size_band"]
        self.assertEqual("mixed", size.kind)
        rows.append(row(size="51 to 300"))
        self.assertEqual("shared", by_dimension(shared_findings(rows, CFG))["size_band"].kind)
        small = dataclasses.replace(CFG, share_min_count=2)
        self.assertEqual("shared", by_dimension(shared_findings(rows[:3], small))["size_band"].kind)

    def test_order_is_shared_by_count_then_mixed_then_unknown(self):
        rows = thirteen()
        rows = [dataclasses.replace(r, board_system="ashby", features=dataclasses.replace(r.features,
                board_system="ashby")) if i < 10 else r for i, r in enumerate(rows)]
        found = shared_findings(rows, CFG)
        kinds = [s.kind for s in found]
        self.assertEqual(sorted(kinds, key=["shared", "mixed", "unknown"].index), kinds)
        shared = [s for s in found if s.kind == "shared"]
        self.assertEqual(sorted((-s.count for s in shared)), [-s.count for s in shared])
        self.assertEqual("best_channel", shared[0].dimension)  # 13, and earlier in DIMENSIONS than role kind

    def test_every_phrase(self):
        rows = [row(best_channel="linkedin_request", best_role_kind="alum", sponsorship="opt_only",
                    everify="not_enrolled", hq="San Francisco, CA", board_system="smartrecruiters",
                    level_tier="new_grad") for _ in range(3)]
        rows = [dataclasses.replace(r, features=Features(level_band="entry level", board_system="smartrecruiters",
                                                         stage_band="Series A or B")) for r in rows]
        texts = {s.dimension: s.text for s in shared_findings(rows, CFG)}
        self.assertEqual("3 of your 3 interview companies started with LinkedIn requests.", texts["best_channel"])
        self.assertEqual("3 of your 3 interview companies started with a touch to alumni.", texts["best_role_kind"])
        self.assertEqual("3 of your 3 interview companies have sponsorship on file as OPT only.",
                         texts["sponsorship"])
        self.assertEqual("3 of your 3 interview companies are not E-Verify enrolled.", texts["everify"])
        self.assertEqual("3 of your 3 interview companies are based in CA.", texts["hq_state"])
        self.assertEqual("3 of your 3 interview companies post jobs on SmartRecruiters.", texts["board_system"])
        self.assertEqual("3 of your 3 interview companies were for entry level roles.", texts["level_band"])
        self.assertEqual("3 of your 3 interview companies are Series A or B.", texts["stage_band"])

    def test_no_interview_companies_gives_every_dimension_unknown(self):
        found = shared_findings([row("replied", size="1 to 50") for _ in range(5)], CFG)
        self.assertEqual(9, len(found))
        for s in found:
            self.assertEqual(("unknown", 0, 0, False), (s.kind, s.of, s.known, s.enough))
            self.assertEqual("No interview companies yet, too few to tell.", s.text)
        self.assertEqual(9, len(shared_findings([], CFG)))

    def test_copy_has_no_dash_and_no_guilt_words(self):
        cases = [thirteen(), [], [row(board_system=b) for b in ("ashby", "lever", "gem", "dover")],
                 [row(size="51 to 300") for _ in range(25)] + [row("none", size="1 to 50") for _ in range(25)]]
        for rows in cases:
            for s in shared_findings(rows, CFG):
                for text in (s.text, s.compare_text):
                    self.assertFalse(any(c in text for c in chr(0x2013) + chr(0x2014)), text)
                    for word in ("streak", "overdue", "behind", "keep it up", "%"):
                        self.assertNotIn(word, text.lower())

    def test_config_type(self):
        self.assertIsInstance(CFG, InsightsConfig)


if __name__ == "__main__":
    unittest.main()
