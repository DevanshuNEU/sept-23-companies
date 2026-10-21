"""The evidence vocabulary: facts.py's tuples and checks against the evidence table (#30 Task 1).

EvidenceDraft refuses what the evidence table's CHECKs refuse, so a bad value fails where it is made. Every draft
these tests accept is also written through record_evidence, so the two cannot drift apart.
"""
import json
import re
import unittest

from outreach.rules import record_evidence
from outreach.rules.facts import CAP_EXEMPT, FACTS, SOURCE_KINDS, STAFFING_RISK, EvidenceDraft
from tests.storetest import fresh_store, seed_company

LCA = {"fy": 2025, "jr": 3, "level1": 2, "newemp_jr": 2, "software": 5}


def lca(**over) -> str:
    return json.dumps({**LCA, **over}, sort_keys=True, separators=(",", ":"))


def funding(**over) -> str:
    return json.dumps({"amount_usd": 4500000, "filed_on": "2026-08-14", "kind": "form_d", **over}, sort_keys=True)


class FactsVocabTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.company = seed_company(self.conn)

    def stored(self, *drafts):
        """Write the drafts through record_evidence: the store must take every draft EvidenceDraft accepts."""
        return record_evidence(self.conn, self.company, list(drafts))

    def evidence_sql(self):
        return self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'evidence'").fetchone()[0]

    def test_vocab_matches_schema(self):
        sql = self.evidence_sql()
        fact_list = re.search(r"fact\s+TEXT NOT NULL CHECK \(fact IN \(([^)]*)\)\)", sql).group(1)
        kind_list = re.search(r"source_kind\s+TEXT NOT NULL CHECK \(source_kind IN \(([^)]*)\)\)", sql).group(1)
        self.assertEqual(set(re.findall(r"'(\w+)'", fact_list)), set(FACTS))
        self.assertEqual(set(re.findall(r"'(\w+)'", kind_list)), set(SOURCE_KINDS))
        for value in FACTS + SOURCE_KINDS + CAP_EXEMPT + STAFFING_RISK:
            self.assertIn(f"'{value}'", sql)
        for fact in ("lca", "cap_exempt", "staffing_risk", "funding", "headcount"):
            self.assertIn(fact, FACTS)
        for kind in ("stapply", "commoncrawl", "hn", "simplify"):
            self.assertIn(kind, SOURCE_KINDS)

    def test_draft_accepts_lca_json_with_and_without_median_wage(self):
        plain = EvidenceDraft("lca", lca(), "confirmed", "lca", source_ref="lca:FY25", observed_on="2025-04-02")
        wage = EvidenceDraft("lca", lca(median_jr_wage=120000), "confirmed", "lca", source_ref="lca:FY26")
        self.assertEqual(self.stored(plain, wage), 2)
        bad = ["x", "[]", json.dumps({k: v for k, v in LCA.items() if k != "newemp_jr"}),
               lca(median_jr_wage=None), lca(median_jr_wage="120k"), lca(fy="2025"), lca(jr=True), lca(jr=-1),
               lca(jr=1.5), lca(extra=1)]
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    EvidenceDraft("lca", value, "confirmed", "lca")

    def test_draft_rejects_bad_cap_exempt_and_staffing_values(self):
        good = [EvidenceDraft("cap_exempt", v, "estimate", "lca", quote=f"basis {v}") for v in CAP_EXEMPT]
        good += [EvidenceDraft("staffing_risk", v, "estimate", "lca", quote=f"reason {v}") for v in STAFFING_RISK]
        self.assertEqual(set(CAP_EXEMPT), {"likely", "confirmed", "no"})
        self.assertEqual(set(STAFFING_RISK), {"low", "medium", "high"})
        self.assertEqual(self.stored(*good), len(good))
        for fact, value in (("cap_exempt", "maybe"), ("cap_exempt", "yes"), ("staffing_risk", "none"),
                            ("staffing_risk", "High")):
            with self.subTest(fact=fact, value=value):
                with self.assertRaises(ValueError):
                    EvidenceDraft(fact, value, "estimate", "lca")

    def test_draft_checks_funding_json(self):
        good = [EvidenceDraft("funding", funding(), "confirmed", "formd", source_ref="0001"),
                EvidenceDraft("funding", funding(amount_usd=None, kind="form_d_amendment"), "confirmed", "formd",
                              source_ref="0002")]
        self.assertEqual(self.stored(*good), 2)
        bad = ["4.5M", "[]", funding(filed_on="2026-08-14T00:00:00Z"), funding(filed_on="2026-13-01"),
               funding(amount_usd="4500000"), funding(amount_usd=True), funding(amount_usd=-1), funding(kind=""),
               funding(kind=3), funding(extra=1),
               json.dumps({"filed_on": "2026-08-14", "kind": "form_d"})]  # amount_usd is required, null allowed
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    EvidenceDraft("funding", value, "confirmed", "formd")

    def test_draft_accepts_hn_and_stapply_source_kinds(self):
        drafts = [EvidenceDraft("hq", "Boston, MA", "estimate", kind, source_ref=kind)
                  for kind in ("hn", "stapply", "commoncrawl", "simplify")]
        self.assertEqual(self.stored(*drafts), 4)
        with self.assertRaises(ValueError):
            EvidenceDraft("hq", "Boston, MA", "estimate", "twitter")

    def test_headcount_needs_num_low(self):
        with self.assertRaises(ValueError):
            EvidenceDraft("headcount", "40", "estimate", "linkedin")
        with self.assertRaises(ValueError):
            EvidenceDraft("headcount", "40-30", "estimate", "linkedin", num_low=40, num_high=30)
        with self.assertRaises(ValueError):
            EvidenceDraft("cap_exempt", "likely", "estimate", "lca", num_low=1)
        draft = EvidenceDraft("headcount", "40-60", "estimate", "linkedin", num_low=40, num_high=60)
        self.assertEqual(self.stored(draft), 1)


if __name__ == "__main__":
    unittest.main()
