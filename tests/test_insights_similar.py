"""Similarity to the interview companies (#81, Task 7): four traits, the nearest interview company, never below zero,
never like itself, unknown never matches unknown. Pure: rows in, matches out."""
import dataclasses
import itertools
import unittest

from outreach.insights.bands import FEATURE_NAMES, Features
from outreach.insights.dataset import OutcomeRow
from outreach.insights.similar import (InterviewProfile, Match, ProfileEntry, interview_profile, reason_text,
                                       similarity)

MIN = dict(min_matches=2, min_companies=3)
_ids = itertools.count(100)
BASE = OutcomeRow(
    company_id=0, name="Co", status="working", merged_ids=(0,), best_outcome="interview", outcome_source="touch",
    touches=1, channels=("linkedin_request",), best_channel="linkedin_request", best_role_kind="engineer",
    best_lane="level_role", best_person=None, first_touch_on="2026-10-01", first_reply_on=None, days_to_reply=None,
    interview_on="2026-10-10", offer_on=None, size_low=None, size_high=None, size_confidence=None, stage=None,
    hq=None, everify=None, sponsorship=None, board_system=None, level_tier=None, level_basis="", intern=False,
    features=Features(), score=None, rank=None)

FULL = Features(size_band="51 to 300", stage_band="Series A or B", level_band="entry level", board_system="ashby")
OTHER = Features(size_band="over 1,000", stage_band="public", level_band="senior", board_system="workday")
REST = Features(size_band="1 to 50", stage_band="seed or earlier", level_band="intern", board_system="lever")


def row(name: str | None = None, *, outcome: str = "interview", on: str | None = "2026-10-10",
        features: Features = Features(), company_id: int | None = None, offer_on: str | None = None) -> OutcomeRow:
    cid = next(_ids) if company_id is None else company_id
    return dataclasses.replace(BASE, company_id=cid, name=name or f"Co {cid}", merged_ids=(cid,),
                               best_outcome=outcome, interview_on=on, offer_on=offer_on, features=features)


def profile(*rows: OutcomeRow, confident_at: int = 20) -> InterviewProfile:
    return interview_profile(rows, confident_at=confident_at)


def sharing(n: int) -> Features:
    """FULL's first `n` traits; the rest are REST's, known and unlike both FULL and OTHER."""
    return Features(**{name: getattr(FULL if i < n else REST, name) for i, name in enumerate(FEATURE_NAMES)})


class SimilarityTest(unittest.TestCase):
    def three_like(self, features: Features) -> InterviewProfile:
        """Three interview companies: one with `features`, two unlike everything."""
        return profile(row("Near", features=features), row("Far 1", features=OTHER), row("Far 2", features=OTHER))

    def test_four_shared_traits_is_one_and_two_is_a_half(self):
        p = self.three_like(FULL)
        for n, value in ((4, 1.0), (3, 0.75), (2, 0.5)):
            with self.subTest(shared=n):
                m = similarity(1, sharing(n), p, **MIN)
                self.assertEqual((value, n), (m.value, m.matches))
                self.assertEqual(("Near",), tuple(e.name for e in m.like))
                self.assertEqual(FEATURE_NAMES[:n], m.shared)

    def test_one_shared_trait_is_zero(self):
        p = self.three_like(FULL)
        m = similarity(1, sharing(1), p, **MIN)
        self.assertEqual((0.0, ()), (m.value, m.like))
        self.assertEqual("Not like your interview companies yet", reason_text(m, p, min_companies=3))

    def test_unknown_never_matches_unknown(self):
        p = profile(*(row(features=Features()) for _ in range(5)))
        m = similarity(1, Features(), p, **MIN)
        self.assertEqual((0.0, 0, ()), (m.value, m.matches, m.like))
        half = Features(size_band="51 to 300", stage_band="Series A or B")
        p = profile(*(row(features=half) for _ in range(3)))
        m = similarity(1, Features(size_band="51 to 300"), p, **MIN)
        self.assertEqual((0.0, 1), (m.value, m.matches), "the three unknown traits are not matches")

    def test_a_company_is_never_like_itself(self):
        me = row("Me", features=FULL, company_id=7)
        p = profile(me, row("Half", features=sharing(2)), row("Far", features=OTHER))
        m = similarity(7, FULL, p, **MIN)
        self.assertEqual((0.5, 2), (m.value, m.matches))
        self.assertEqual(("Half",), tuple(e.name for e in m.like))
        self.assertNotIn(7, [e.company_id for e in m.like])

    def test_value_does_not_grow_with_more_interview_companies(self):
        p = self.three_like(sharing(3))
        before = similarity(1, FULL, p, **MIN)
        more = profile(*p_rows(p), *(row(features=sharing(3)) for _ in range(10)))
        after = similarity(1, FULL, more, **MIN)
        self.assertEqual(0.75, before.value)
        self.assertEqual(before.value, after.value)
        self.assertLessEqual(after.value, 1.0)

    def test_fewer_than_three_interview_companies_lifts_nobody(self):
        p = profile(row("A", features=FULL), row("B", features=FULL))
        m = similarity(1, FULL, p, **MIN)
        self.assertEqual((0.0, ()), (m.value, m.like))
        self.assertEqual("Fewer than 3 interview companies so far", reason_text(m, p, min_companies=3))
        empty = profile()
        self.assertEqual(0.0, similarity(1, FULL, empty, **MIN).value)

    def test_value_is_never_negative_and_at_most_one(self):
        p = self.three_like(FULL)
        for n in range(len(FEATURE_NAMES) + 1):
            m = similarity(1, sharing(n), p, **MIN)
            self.assertTrue(0.0 <= m.value <= 1.0, (n, m.value))

    def test_confidence_is_low_under_twenty_and_medium_at_twenty_never_high(self):
        empty = profile()
        self.assertEqual(("low", "no interview companies yet"), (empty.confidence.level, empty.confidence.basis))
        self.assertEqual(0, empty.count)
        one = profile(row())
        self.assertEqual("1 interview company so far, under 20 is a small sample", one.confidence.basis)
        thirteen = profile(*(row() for _ in range(13)))
        self.assertEqual(("low", "13 interview companies so far, under 20 is a small sample"),
                         (thirteen.confidence.level, thirteen.confidence.basis))
        twenty = profile(*(row() for _ in range(20)))
        self.assertEqual(("medium", "20 interview companies"), (twenty.confidence.level, twenty.confidence.basis))
        many = profile(*(row() for _ in range(200)))
        self.assertEqual("medium", many.confidence.level)

    def test_reason_names_up_to_two_companies_and_the_shared_traits(self):
        p = profile(row("Distyl AI", on="2026-09-01", features=sharing(3)),
                    row("Retell AI", on="2026-10-01", features=sharing(3)),
                    row("Far", on="2026-10-20", features=OTHER))
        m = similarity(1, FULL, p, **MIN)
        self.assertEqual(("Retell AI", "Distyl AI"), tuple(e.name for e in m.like), "newest first interview first")
        self.assertEqual("Like Retell AI and Distyl AI, where you interviewed: 51 to 300 people, Series A or B, "
                         "entry level role", reason_text(m, p, min_companies=3))
        five = profile(*(row(f"Tie {c}", on="2026-10-01", features=FULL) for c in "EDCBA"))
        m = similarity(1, FULL, five, **MIN)
        self.assertEqual("Like Tie A, Tie B and 3 more, where you interviewed: 51 to 300 people, Series A or B, "
                         "entry level role, Ashby board", reason_text(m, five, min_companies=3))
        one = profile(row("Solo", features=sharing(2)), row(features=OTHER), row(features=OTHER))
        m = similarity(1, FULL, one, **MIN)
        self.assertEqual("Like Solo, where you interviewed: 51 to 300 people, Series A or B",
                         reason_text(m, one, min_companies=3))

    def test_profile_uses_outcomes_only(self):
        rows = [row("Replied", outcome="replied"), row("Referred", outcome="referred"),
                row("Accepted", outcome="accepted"), row("Nothing", outcome="none"),
                row("Interview", outcome="interview"), row("Offer", outcome="offer", on=None, offer_on="2026-10-12")]
        p = profile(*rows)
        self.assertEqual(["Offer", "Interview"], [e.name for e in p.entries])
        self.assertEqual("2026-10-12", p.entries[0].first_interview_on, "an offer with no interview day uses its day")
        self.assertEqual(2, p.count)

    def test_entries_are_newest_first_then_by_name_and_unknown_days_last(self):
        p = profile(row("B", on="2026-10-01"), row("A", on="2026-10-01"), row("C", on="2026-10-05"),
                    row("D", on=None))
        self.assertEqual(["C", "A", "B", "D"], [e.name for e in p.entries])
        self.assertIsInstance(p.entries[0], ProfileEntry)

    def test_match_is_frozen(self):
        m = similarity(1, FULL, self.three_like(FULL), **MIN)
        self.assertIsInstance(m, Match)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            m.value = 0.5  # type: ignore[misc]

    def test_copy_has_no_dash(self):
        p = self.three_like(FULL)
        for n in range(5):
            text = reason_text(similarity(1, sharing(n), p, **MIN), p, min_companies=3)
            self.assertNotIn(chr(0x2013), text)
            self.assertNotIn(chr(0x2014), text)


def p_rows(p: InterviewProfile) -> list[OutcomeRow]:
    """Rows that rebuild `p`'s entries."""
    return [row(e.name, on=e.first_interview_on, features=e.features, company_id=e.company_id) for e in p.entries]


if __name__ == "__main__":
    unittest.main()
