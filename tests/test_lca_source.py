"""LcaSource (#31 Task 6): candidates, evidence, shared keys and work tiers from the fixture CSVs."""
import csv
import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from outreach.sources.base import Candidate
from outreach.sources.capabilities import SharedKey
from outreach.sources.lca import COLUMNS, LcaSource
from outreach.sources.lca_rules import load_lca_config
from outreach.sources.tabular import EmptyTable, MissingColumns
from tests.contracts import check_source
from tests.test_lca_aggregate import BASE

FIXTURES = Path(__file__).parent / "fixtures" / "lca"
FILES = [FIXTURES / "LCA_Disclosure_Data_FY2025_Q1.csv", FIXTURES / "LCA_Disclosure_Data_FY2025_Q2.csv"]
CFG = load_lca_config()


def by_ref(source: LcaSource) -> dict[str, Candidate]:
    return {c.source_ref: c for c in source.discover()}


def facts(c: Candidate) -> dict[str, list]:
    out: dict[str, list] = {}
    for e in c.evidence:
        out.setdefault(e.fact, []).append(e)
    return out


class FixtureTest(unittest.TestCase):
    def setUp(self):
        self.source = LcaSource(FILES)
        self.cands = by_ref(self.source)

    def test_source_has_name_version_schedule(self):
        self.assertEqual((self.source.name, self.source.schedule), ("lca", "quarterly"))
        self.assertRegex(self.source.version, r"^lca-1-[0-9a-f]{8}$")

    def test_yields_the_qualifying_employers(self):
        names = {c.name for c in self.cands.values()}
        self.assertEqual(len(self.cands), 14)
        self.assertIn("Orchard", names)
        self.assertIn("Bridgeway Staffing Solutions LLC", names)
        self.assertNotIn("Veltrix Corp", names)  # 30 filings: a large filer

    def test_nimbus_candidate_exactly(self):
        c = self.cands["lca:nimbus robotics"]
        self.assertEqual(c.name, "Nimbus Robotics, Inc.")
        self.assertEqual(c.domain, "nimbusrobotics.com")
        self.assertEqual(c.keys, {"legal_name": "Nimbus Robotics, Inc.", "fein": "111111111"})
        f = facts(c)
        self.assertEqual(set(f), {"lca", "staffing_risk", "hq", "domain"})
        self.assertEqual(f["hq"][0].value, "San Francisco, CA")
        self.assertEqual(f["hq"][0].confidence, "estimate")
        (lca,) = f["lca"]
        self.assertEqual(lca.value, '{"fy":2025,"jr":3,"level1":2,"median_jr_wage":120000,"newemp_jr":2,"software":5}')
        self.assertEqual((lca.confidence, lca.source_kind, lca.source_ref, lca.observed_on),
                         ("confirmed", "lca", "lca:FY25", "2025-04-02"))
        self.assertEqual((f["domain"][0].value, f["domain"][0].confidence), ("nimbusrobotics.com", "estimate"))
        self.assertEqual((f["staffing_risk"][0].value, f["staffing_risk"][0].confidence), ("low", "estimate"))

    def test_dba_is_the_name_and_a_key(self):
        c = self.cands["lca:orchard labs"]
        self.assertEqual(c.name, "Orchard")
        self.assertEqual(c.keys["dba"], "Orchard")
        self.assertEqual(c.keys["legal_name"], "Orchard Labs LLC dba Orchard")

    def test_excluded_employer_is_stored_with_high_risk(self):
        c = self.cands["lca:bridgeway staffing solutions"]
        (risk,) = facts(c)["staffing_risk"]
        self.assertEqual((risk.value, risk.quote), ("high", "excluded: NAICS 5613"))
        tier, reason = self.source.work_tier("lca:bridgeway staffing solutions")
        self.assertEqual(tier, 3)
        self.assertIn("excluded: NAICS 5613", reason)

    def test_work_tier_of_a_clean_employer_and_of_an_unknown_ref(self):
        self.assertEqual(self.source.work_tier("lca:nimbus robotics")[0], 1)
        self.assertIsNone(self.source.work_tier("lca:veltrix"))  # large filer, not a candidate
        self.assertIsNone(self.source.work_tier("lca:nobody"))

    def test_cap_exempt_tag(self):
        f = facts(self.cands["lca:riverside university"])
        (cap,) = f["cap_exempt"]
        self.assertEqual((cap.value, cap.confidence, cap.source_kind), ("likely", "estimate", "lca"))
        self.assertTrue(cap.quote)
        self.assertNotIn("cap_exempt", facts(self.cands["lca:nimbus robotics"]))

    def test_shared_fein_and_domain(self):
        shared = self.source.shared_keys()
        self.assertEqual([(k.kind, k.value) for k in shared], [("domain", "veltrix.com"), ("fein", "333333333")])
        domain, fein = shared
        self.assertEqual(domain.reason,
                         "3 employers: Veltrix Corp, Veltrix Diagnostics Inc, Veltrix Pharma Inc")
        self.assertEqual(fein.reason, "3 employers: Clearpath Inc, Greenfield AI Inc, Sundial Health Inc")
        for ref in ("lca:clearpath", "lca:greenfield ai", "lca:sundial health"):
            self.assertNotIn("fein", self.cands[ref].keys, ref)
        for ref in ("lca:veltrix diagnostics", "lca:veltrix pharma"):
            c = self.cands[ref]
            self.assertEqual(c.domain, "veltrix.com")
            (d,) = facts(c)["domain"]
            self.assertEqual((d.value, d.confidence), ("veltrix.com", "guess"))

    def test_free_mail_employer_has_no_domain(self):
        c = self.cands["lca:solo dev studio"]
        self.assertIsNone(c.domain)
        self.assertNotIn("domain", facts(c))

    def test_no_size_or_headcount_evidence_anywhere(self):
        for c in self.cands.values():
            self.assertFalse({"size", "headcount"} & set(facts(c)), c.source_ref)

    def test_every_lca_value_is_a_year_summary(self):
        for c in self.cands.values():
            for e in facts(c)["lca"]:
                obj = json.loads(e.value)
                self.assertEqual(e.source_ref, f"lca:FY{obj['fy'] % 100:02d}")

    def test_summary_numbers_on_the_fixture(self):
        self.assertEqual(self.source.summary(),
                         {"fy_mismatch": 0, "employers": 15, "qualifying": 14, "excluded": 4, "cap_exempt": 1})

    def test_lca_source_passes_check_source(self):
        self.assertEqual(len(check_source(self, LcaSource(FILES))), 14)

    def test_load_happens_once(self):
        source = LcaSource(FILES)
        source.shared_keys()
        source.work_tier("lca:nimbus robotics")
        first = list(source.discover())
        self.assertIs(source._employers(), source._employers())
        self.assertEqual(first, list(source.discover(since="2020-01-01")))  # since is ignored


class VersionTest(unittest.TestCase):
    def test_version_changes_with_config(self):
        a = LcaSource(FILES)
        b = LcaSource(FILES, dataclasses.replace(CFG, max_small_filings=30))
        c = LcaSource(FILES, load_lca_config())
        self.assertNotEqual(a.version, b.version)
        self.assertEqual(a.version, c.version)


class ErrorTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def write(self, rows, name="LCA_Disclosure_Data_FY2025_Q1.csv", header=None):
        header = header or list(COLUMNS)
        path = self.dir / name
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header)
            for i, over in enumerate(rows):
                row = {**BASE, "CASE_NUMBER": f"I-200-{name}-{i}", **over}
                w.writerow([row.get(h, "") for h in header])
        return path

    def test_bad_file_raises_on_first_item(self):
        header = [h for h in COLUMNS if h != "SOC_CODE"]
        source = LcaSource([self.write([{}], header=header)])
        with self.assertRaises(MissingColumns):
            next(source.discover())

    def test_no_employers_at_all_is_an_error(self):
        source = LcaSource([self.write([{"VISA_CLASS": "E-3"}, {"CASE_STATUS": "Denied"}])])
        with self.assertRaises(EmptyTable) as ctx:
            list(source.discover())
        self.assertIn("no employers", str(ctx.exception))
        with self.assertRaises(EmptyTable):
            source.summary()

    def test_no_qualifying_employer_with_software_rows_is_an_error(self):
        source = LcaSource([self.write([{"PW_WAGE_LEVEL": "III"}, {"PW_WAGE_LEVEL": "IV"}])])
        with self.assertRaises(EmptyTable) as ctx:
            list(source.discover())
        self.assertIn("none qualifies", str(ctx.exception))
        self.assertIn("software", str(ctx.exception))

    def test_no_qualifying_employer_and_no_software_rows_is_also_an_error(self):
        source = LcaSource([self.write([{"SOC_CODE": "13-2011"}])])
        with self.assertRaises(EmptyTable) as ctx:
            list(source.discover())
        self.assertIn("none qualifies", str(ctx.exception))

    def test_a_failed_load_is_not_cached_as_empty(self):
        path = self.write([{"VISA_CLASS": "E-3"}])
        source = LcaSource([path])
        for _ in range(2):
            with self.assertRaises(EmptyTable):
                list(source.discover())

    def test_no_files_is_an_error(self):
        with self.assertRaises(ValueError):
            list(LcaSource([]).discover())


if __name__ == "__main__":
    unittest.main()
