"""Helpers for the ranking signal tests (#6): a neutral company and the assessment check."""
from __future__ import annotations

import unittest

from outreach.ranking.confidence import LEVELS, Assessment
from outreach.ranking.facts import RankFacts
from outreach.ranking.types import Signal
from tests.contracts import check_signal

__all__ = ["neutral_facts", "check_assessment"]

_DASHES = (chr(0x2014), chr(0x2013))


def neutral_facts(**overrides) -> RankFacts:
    """A company with no lanes, every optional field None, no LCA filings, nobody to reach and no grades: the
    starting point for a signal test. `overrides` replace any field."""
    base = dict(company_id=1, lanes=(), best_posting_id=None, best_role_age_days=None, everify=None, cap_exempt=None,
                staffing_risk=None, lca_jr_recent=0, lca_level1_recent=0, pay_level=None, size_low=None,
                size_high=None, stage=None, funded_months_ago=None, reachable_person=False)
    base.update(overrides)
    return RankFacts(**base)


def check_assessment(tc: unittest.TestCase, signal: Signal, facts: RankFacts) -> Assessment:
    """Runs the foundation's `check_signal`, then asserts the result is an `Assessment` whose confidence is the
    facts' own grade for this signal (copied, never computed by the signal), with a plain basis and a known level."""
    got = check_signal(tc, signal, facts)
    tc.assertIsInstance(got, Assessment, f"{signal.name} returned {type(got).__name__}, not an Assessment")
    tc.assertEqual(got.confidence, facts.grade(signal.name))
    tc.assertTrue(got.confidence.basis.strip(), "a confidence needs a basis")
    for dash in _DASHES:
        tc.assertNotIn(dash, got.confidence.basis)
    tc.assertIn(got.confidence.level, LEVELS)
    return got
