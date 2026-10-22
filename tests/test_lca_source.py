"""LcaSource (#31 Task 6): candidates, evidence, shared keys and work tiers from the fixture CSVs."""
import csv
import dataclasses
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from outreach.sources.base import Candidate
from outreach.sources.capabilities import SharedKey
from outreach.sources import lca as lca_module
from outreach.sources.lca import COLUMNS, LcaSource
from outreach.sources.lca_rules import CONFIG_PATH, load_lca_config
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
        with mock.patch.object(lca_module, "load_employers", wraps=lca_module.load_employers) as loader:
            source.shared_keys()
            source.work_tier("lca:nimbus robotics")
            first = list(source.discover())
            source.summary()
            self.assertEqual(first, list(source.discover(since="2020-01-01")))  # since is ignored
        self.assertEqual(loader.call_count, 1)


class VersionTest(unittest.TestCase):
    def test_version_changes_with_config(self):
        a = LcaSource(FILES)
        b = LcaSource(FILES, dataclasses.replace(CFG, max_small_filings=30))
        c = LcaSource(FILES, load_lca_config())
        self.assertNotEqual(a.version, b.version)
        self.assertEqual(a.version, c.version)

    def test_version_changes_with_a_regex_but_not_with_a_comment(self):
        a = LcaSource(FILES)
        regex = dataclasses.replace(CFG, staffing_name_regex=re.compile("consult|extra", re.I))
        self.assertNotEqual(a.version, LcaSource(FILES, regex).version)
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "lca.toml"
            copy.write_text("# a new comment\n" + CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            self.assertEqual(a.version, LcaSource(FILES, load_lca_config(copy)).version)


class TempFiles(unittest.TestCase):
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


class ErrorTest(TempFiles):
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


def rows_for(name, n, **over):
    """n rows of one employer (a junior software filing each), numbered by name."""
    return [{"CASE_NUMBER": f"{name}-{i}", "EMPLOYER_NAME": name, **over} for i in range(n)]


class MultiYearTest(TempFiles):
    """Files for FY2024 and FY2025: the qualifying year is the latest one in which the employer is small."""
    FY24 = {"DECISION_DATE": "45352"}  # 2024-03-01, inside FY2024

    def setUp(self):
        super().setUp()
        bigco = {"EMPLOYER_POC_EMAIL": "hr@bigco.io", "NAICS_CODE": "541715"}
        smallco = {"EMPLOYER_POC_EMAIL": "hr@smallco.io", "NAICS_CODE": "541715"}
        fy24 = (rows_for("Bigco Inc", 3, **bigco, **self.FY24)
                + rows_for("Smallco Inc", 2, **smallco, **self.FY24, **{"H-1B_DEPENDENT": "Yes"}))
        fy25 = (rows_for("Bigco Inc", 30, **bigco, **{"H-1B_DEPENDENT": "Yes"})
                + rows_for("Smallco Inc", 2, **smallco))
        self.source = LcaSource([self.write(fy24, "LCA_Disclosure_Data_FY2024_Q1.csv"),
                                 self.write(fy25, "LCA_Disclosure_Data_FY2025_Q1.csv")])
        self.cands = by_ref(self.source)

    def test_large_now_but_small_before_is_classified_on_the_small_year(self):
        c = self.cands["lca:bigco"]
        lca = facts(c)["lca"]
        self.assertEqual([e.source_ref for e in lca], ["lca:FY24", "lca:FY25"])
        self.assertEqual([json.loads(e.value)["fy"] for e in lca], [2024, 2025])
        (risk,) = facts(c)["staffing_risk"]
        self.assertEqual((risk.source_ref, risk.value), ("lca:FY24", "low"))  # FY25 says H-1B dependent
        self.assertEqual(self.source.work_tier("lca:bigco")[0], 1)

    def test_small_in_both_years_is_classified_on_the_latest(self):
        c = self.cands["lca:smallco"]
        self.assertEqual([e.source_ref for e in facts(c)["lca"]], ["lca:FY24", "lca:FY25"])
        (risk,) = facts(c)["staffing_risk"]
        self.assertEqual((risk.source_ref, risk.value), ("lca:FY25", "low"))  # FY24 says H-1B dependent
        self.assertEqual(self.source.work_tier("lca:smallco")[0], 1)


class SummaryTest(TempFiles):
    def test_a_row_dated_outside_its_fiscal_year_is_counted(self):
        rows = [{"CASE_NUMBER": "in"}, {"CASE_NUMBER": "out", "DECISION_DATE": "45352"}]  # 2024-03-01 in a FY2025 file
        source = LcaSource([self.write(rows)])
        self.assertEqual(source.summary()["fy_mismatch"], 1)


class SharedKeyTest(TempFiles):
    def test_the_reason_lists_five_sorted_names_and_counts_all(self):
        names = ["Golf Inc", "Echo Inc", "Alpha Inc", "Foxtrot Inc", "Bravo Inc", "Delta Inc", "Charlie Inc"]
        rows = [r for n in names for r in rows_for(n, 1, EMPLOYER_FEIN="99-9999999", EMPLOYER_POC_EMAIL="x@one.io")]
        shared = {k.kind: k for k in LcaSource([self.write(rows)]).shared_keys()}
        expected = "7 employers: Alpha Inc, Bravo Inc, Charlie Inc, Delta Inc, Echo Inc"
        self.assertEqual(shared["fein"].reason, expected)
        self.assertEqual(shared["domain"].reason, expected)

    def test_a_fein_split_pair_keeps_its_domain_shared_and_names_it_once(self):
        rows = (rows_for("Acme Inc", 2, EMPLOYER_FEIN="11-1111111", EMPLOYER_POC_EMAIL="x@acme.io")
                + rows_for("Acme Inc", 1, EMPLOYER_FEIN="22-2222222", EMPLOYER_POC_EMAIL="x@acme.io"))
        source = LcaSource([self.write(rows)])
        (shared,) = source.shared_keys()
        self.assertEqual((shared.kind, shared.value, shared.reason), ("domain", "acme.io", "2 employers: Acme Inc"))
        cands = by_ref(source)
        self.assertEqual(sorted(cands), ["lca:acme#111111111", "lca:acme#222222222"])
        for ref, fein in (("lca:acme#111111111", "111111111"), ("lca:acme#222222222", "222222222")):
            c = cands[ref]
            self.assertEqual(c.keys["fein"], fein)  # each FEIN is its own and not shared
            self.assertEqual(c.domain, "acme.io")
            self.assertEqual(facts(c)["domain"][0].confidence, "guess")


class HqTest(TempFiles):
    def test_hq_needs_a_state(self):
        rows = (rows_for("Withcity Inc", 1, EMPLOYER_CITY="reno", EMPLOYER_STATE="NV")
                + rows_for("Stateonly Inc", 1, EMPLOYER_CITY="", EMPLOYER_STATE="OR")
                + rows_for("Cityonly Inc", 1, EMPLOYER_CITY="Boise", EMPLOYER_STATE=""))
        cands = by_ref(LcaSource([self.write(rows)]))
        hq = {ref: [e.value for e in facts(c).get("hq", [])] for ref, c in cands.items()}
        self.assertEqual(hq, {"lca:withcity": ["Reno, NV"], "lca:stateonly": ["OR"], "lca:cityonly": []})


if __name__ == "__main__":
    unittest.main()
