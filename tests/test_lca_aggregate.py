"""LCA aggregation (#31 Task 4): one summary per employer per fiscal year from small hand-written fixtures."""
import csv
import tempfile
import unittest
from pathlib import Path

from outreach.sources.lca import (COLUMNS, FyStats, fiscal_year_of, load_employers, norm_employer,
                                  parse_decision_date)
from outreach.sources.tabular import EmptyTable, MissingColumns, TabularError

FIXTURES = Path(__file__).parent / "fixtures" / "lca"
Q1 = FIXTURES / "LCA_Disclosure_Data_FY2025_Q1.csv"
Q2 = FIXTURES / "LCA_Disclosure_Data_FY2025_Q2.csv"

BASE = {"CASE_NUMBER": "I-200-1", "CASE_STATUS": "Certified", "DECISION_DATE": "45930", "VISA_CLASS": "H-1B",
        "SOC_CODE": "15-1252", "PW_WAGE_LEVEL": "I", "NEW_EMPLOYMENT": "1", "EMPLOYER_NAME": "Acme Inc",
        "TRADE_NAME_DBA": "", "EMPLOYER_CITY": "Reno", "EMPLOYER_STATE": "NV", "EMPLOYER_FEIN": "",
        "NAICS_CODE": "541715", "SECONDARY_ENTITY": "No", "TOTAL_WORKSITE_LOCATIONS": "1", "WORKSITE_STATE": "NV",
        "H-1B_DEPENDENT": "No", "WILLFUL_VIOLATOR": "No", "WAGE_RATE_OF_PAY_FROM": "100000",
        "WAGE_UNIT_OF_PAY": "Year", "EMPLOYER_POC_EMAIL": "hr@acme.io", "AGENT_ATTORNEY_EMAIL_ADDRESS": "",
        "PREPARER_EMAIL": ""}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def write(self, rows, name="LCA_Disclosure_Data_FY2025_Q1.csv", header=None):
        """rows: dicts overriding BASE. A CASE_NUMBER of None numbers the row."""
        header = header or list(COLUMNS)
        path = self.dir / name
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header)
            for i, over in enumerate(rows):
                row = {**BASE, "CASE_NUMBER": f"I-200-{name}-{i}", **over}
                w.writerow([row.get(h, "") for h in header])
        return path


class NimbusTest(Base):
    def test_nimbus_counts_with_q1_only(self):
        s = load_employers([Q1])["nimbus robotics"].fy[2025]
        self.assertEqual((s.total, s.software, s.jr, s.level1, s.newemp_jr, s.median_jr_wage),
                         (6, 5, 4, 2, 2, 125000))
        # N6 (certified-withdrawn) counts; N7 withdrawn, N8 E-3, N9 denied (later date) do not
        self.assertEqual(s.last_decision, "2024-11-09")

    def test_duplicate_case_keeps_latest_decision(self):
        s = load_employers([Q1, Q2])["nimbus robotics"].fy[2025]
        self.assertEqual((s.total, s.software, s.jr, s.level1, s.newemp_jr, s.median_jr_wage),
                         (6, 5, 3, 2, 2, 120000))
        self.assertEqual(s.last_decision, "2025-04-02")

    def test_file_order_does_not_decide_the_duplicate(self):
        s = load_employers([Q2, Q1])["nimbus robotics"].fy[2025]
        self.assertEqual((s.jr, s.median_jr_wage, s.last_decision), (3, 120000, "2025-04-02"))

    def test_a_later_withdrawal_removes_an_earlier_certification(self):
        a = self.write([{"CASE_NUMBER": "I-1", "DECISION_DATE": "45900"}, {"CASE_NUMBER": "I-2"}], "FY2025_Q1.csv")
        b = self.write([{"CASE_NUMBER": "I-1", "CASE_STATUS": "Withdrawn", "DECISION_DATE": "45920"}],
                       "FY2025_Q2.csv")
        self.assertEqual(load_employers([a, b])["acme"].fy[2025].total, 1)

    def test_same_date_duplicate_keeps_the_later_row(self):
        a = self.write([{"CASE_NUMBER": "I-1", "PW_WAGE_LEVEL": "I"}], "FY2025_Q1.csv")
        b = self.write([{"CASE_NUMBER": "I-1", "PW_WAGE_LEVEL": "III"}], "FY2025_Q2.csv")
        self.assertEqual(load_employers([a, b])["acme"].fy[2025].jr, 0)

    def test_dated_row_beats_an_undated_duplicate(self):
        a = self.write([{"CASE_NUMBER": "I-1", "PW_WAGE_LEVEL": "I", "DECISION_DATE": "45930"}], "FY2025_Q1.csv")
        b = self.write([{"CASE_NUMBER": "I-1", "PW_WAGE_LEVEL": "III", "DECISION_DATE": ""}], "FY2025_Q2.csv")
        self.assertEqual(load_employers([a, b])["acme"].fy[2025].jr, 1)

    def test_rows_without_a_case_number_are_all_kept(self):
        p = self.write([{"CASE_NUMBER": ""}, {"CASE_NUMBER": " "}])
        self.assertEqual(load_employers([p])["acme"].fy[2025].total, 2)

    def test_each_fiscal_year_is_its_own_summary(self):
        a = self.write([{}], "LCA_Disclosure_Data_FY2025_Q4.csv")
        b = self.write([{}, {}], "LCA_Disclosure_Data_FY2026_Q1.csv")
        fy = load_employers([a, b])["acme"].fy
        self.assertEqual({k: v.total for k, v in fy.items()}, {2025: 1, 2026: 2})

    def test_the_same_case_in_two_fiscal_years_is_two_filings(self):
        a = self.write([{"CASE_NUMBER": "I-1"}], "FY2025_Q4.csv")
        b = self.write([{"CASE_NUMBER": "I-1"}], "FY2026_Q1.csv")
        fy = load_employers([a, b])["acme"].fy
        self.assertEqual((fy[2025].total, fy[2026].total), (1, 1))


class EmployersTest(Base):
    def test_employers_group_by_normalized_name_and_keep_all_sizes(self):
        emps = load_employers([Q1])
        self.assertEqual(emps["veltrix"].fy[2025].total, 30)
        self.assertEqual(emps["veltrix"].fy[2025].software, 3)
        self.assertEqual(emps["veltrix"].legal_name, "Veltrix Corp")
        self.assertEqual(emps["veltrix pharma"].fy[2025].total, 2)
        self.assertIn("bridgeway staffing solutions", emps)
        self.assertEqual(emps["orchard labs"].legal_name, "Orchard Labs LLC dba Orchard")
        self.assertEqual(emps["orchard labs"].dba, "Orchard")
        self.assertEqual(len(emps), 15)

    def test_name_variants_are_one_employer(self):
        p = self.write([{"EMPLOYER_NAME": "Acme, Inc."}, {"EMPLOYER_NAME": "ACME INC"},
                        {"EMPLOYER_NAME": "Acme Incorporated"}, {"EMPLOYER_NAME": "Acme Inc"}, {"EMPLOYER_NAME": "Acme Inc"}])
        emps = load_employers([p])
        self.assertEqual(list(emps), ["acme"])
        self.assertEqual(emps["acme"].legal_name, "Acme Inc")  # 2 of 5

    def test_most_common_value_ties_break_alphabetically(self):
        p = self.write([{"EMPLOYER_STATE": "NV", "EMPLOYER_CITY": "Reno"},
                        {"EMPLOYER_STATE": "CA", "EMPLOYER_CITY": "Fresno"}])
        e = load_employers([p])["acme"]
        self.assertEqual((e.state, e.city), ("CA", "Fresno"))

    def test_blank_values_never_win(self):
        p = self.write([{"EMPLOYER_FEIN": ""}, {"EMPLOYER_FEIN": ""}, {"EMPLOYER_FEIN": "12-3456789"}])
        self.assertEqual(load_employers([p])["acme"].fein, "123456789")

    def test_a_row_with_no_employer_name_is_not_an_employer(self):
        p = self.write([{"EMPLOYER_NAME": ""}, {}])
        self.assertEqual(list(load_employers([p])), ["acme"])

    def test_a_name_made_only_of_stop_words_still_has_a_key(self):
        p = self.write([{"EMPLOYER_NAME": "The Company"}])
        self.assertEqual(list(load_employers([p])), ["the company"])

    def test_domain_choice(self):
        emps = load_employers([Q1])
        self.assertEqual((emps["nimbus robotics"].domain, emps["nimbus robotics"].domain_kind),
                         ("nimbusrobotics.com", "company"))
        self.assertEqual((emps["solo dev studio"].domain, emps["solo dev studio"].domain_kind), (None, "free"))
        p = self.write([{"EMPLOYER_POC_EMAIL": "law@fraglaw.com", "AGENT_ATTORNEY_EMAIL_ADDRESS": "x@fraglaw.com"}])
        e = load_employers([p])["acme"]
        self.assertEqual((e.domain, e.domain_kind), (None, "attorney"))

    def test_preparer_domain_is_not_the_company(self):
        p = self.write([{"EMPLOYER_POC_EMAIL": "p@prepco.com", "PREPARER_EMAIL": "q@prepco.com"}])
        self.assertEqual(load_employers([p])["acme"].domain_kind, "attorney")

    def test_a_company_domain_wins_over_free_mail_rows(self):
        p = self.write([{"EMPLOYER_POC_EMAIL": "a@gmail.com"}, {"EMPLOYER_POC_EMAIL": "b@gmail.com"},
                        {"EMPLOYER_POC_EMAIL": "c@acme.io"}])
        e = load_employers([p])["acme"]
        self.assertEqual((e.domain, e.domain_kind), ("acme.io", "company"))

    def test_most_common_company_domain_wins(self):
        p = self.write([{"EMPLOYER_POC_EMAIL": "a@one.io"}, {"EMPLOYER_POC_EMAIL": "b@two.io"},
                        {"EMPLOYER_POC_EMAIL": "c@two.io"}])
        self.assertEqual(load_employers([p])["acme"].domain, "two.io")

    def test_peo_platform_and_missing_poc_kinds(self):
        for email, kind in (("a@trinet.com", "peo"), ("a@greenhouse.io", "none"), ("", "none"), ("nonsense", "none")):
            p = self.write([{"EMPLOYER_POC_EMAIL": email}])
            e = load_employers([p])["acme"]
            self.assertEqual((e.domain, e.domain_kind), (None, kind), email)

    def test_shared_parent_domain_is_visible_on_every_employer(self):
        emps = load_employers([Q1])
        self.assertEqual({emps[k].domain for k in ("veltrix pharma", "veltrix diagnostics", "veltrix")},
                         {"veltrix.com"})

    def test_fein_is_none_when_all_zero(self):
        emps = load_employers([Q1])
        self.assertIsNone(emps["lumen apps"].fein)
        self.assertIsNone(emps["bridgeway staffing solutions"].fein)
        self.assertEqual(emps["nimbus robotics"].fein, "111111111")

    def test_clearpath_greenfield_sundial_share_a_fein(self):
        emps = load_employers([Q1])
        self.assertEqual({emps[k].fein for k in ("clearpath", "greenfield ai", "sundial health")}, {"333333333"})


class FlagsTest(Base):
    def test_hard_exclusion_signals(self):
        emps = load_employers([Q1])
        self.assertTrue(emps["depco systems"].fy[2025].h1b_dependent)
        self.assertFalse(emps["nimbus robotics"].fy[2025].h1b_dependent)
        self.assertEqual(emps["lumen apps"].fy[2025].secondary, 1)
        self.assertEqual(emps["nimbus robotics"].fy[2025].secondary, 0)
        self.assertEqual(emps["bridgeway staffing solutions"].naics, "561320")
        self.assertFalse(emps["nimbus robotics"].fy[2025].willful)

    def test_willful_secondary_and_yes_spellings(self):
        p = self.write([{"WILLFUL_VIOLATOR": "Y", "SECONDARY_ENTITY": "yes", "H-1B_DEPENDENT": "YES"},
                        {"SECONDARY_ENTITY": "Y"}])
        s = load_employers([p])["acme"].fy[2025]
        self.assertEqual((s.willful, s.h1b_dependent, s.secondary), (True, True, 2))

    def test_shares(self):
        emps = load_employers([Q1])
        v = emps["veltrix"].fy[2025]
        self.assertAlmostEqual(v.computer_share, 3 / 30)
        self.assertAlmostEqual(v.newemp_share_soft, 1.0)
        self.assertAlmostEqual(v.jr_share_soft, 1.0)
        self.assertEqual(v.multi_worksite_share, 0.0)
        b = emps["bridgeway staffing solutions"].fy[2025]
        self.assertEqual(b.multi_worksite_share, 1.0)
        n = emps["nimbus robotics"].fy[2025]
        self.assertAlmostEqual(n.computer_share, 5 / 6)  # 11-2021 is the one non-computer filing
        self.assertAlmostEqual(n.jr_share_soft, 4 / 5)
        self.assertAlmostEqual(n.newemp_share_soft, 3 / 5)  # N4 is level III: all software filings, not only junior

    def test_computer_share_counts_the_two_engineering_socs(self):
        p = self.write([{"SOC_CODE": "17-2061"}, {"SOC_CODE": "17-2072"}, {"SOC_CODE": "15-1251"},
                        {"SOC_CODE": "17-2051"}])
        self.assertAlmostEqual(load_employers([p])["acme"].fy[2025].computer_share, 3 / 4)

    def test_shares_are_zero_without_software(self):
        p = self.write([{"SOC_CODE": "11-2021"}])
        s = load_employers([p])["acme"].fy[2025]
        self.assertEqual((s.software, s.jr_share_soft, s.newemp_share_soft), (0, 0.0, 0.0))

    def test_worksite_states_count_software_filings_only(self):
        p = self.write([{"WORKSITE_STATE": "NV"}, {"WORKSITE_STATE": "CA"}, {"WORKSITE_STATE": "CA"},
                        {"WORKSITE_STATE": ""}, {"WORKSITE_STATE": "TX", "SOC_CODE": "11-2021"}])
        self.assertEqual(load_employers([p])["acme"].fy[2025].n_worksite_states, 2)

    def test_multi_worksite_reads_numbers_written_as_floats(self):
        p = self.write([{"TOTAL_WORKSITE_LOCATIONS": "2.0"}, {"TOTAL_WORKSITE_LOCATIONS": "1.0"},
                        {"TOTAL_WORKSITE_LOCATIONS": ""}, {"TOTAL_WORKSITE_LOCATIONS": "n/a"}])
        self.assertEqual(load_employers([p])["acme"].fy[2025].multi_worksite_share, 0.25)

    def test_new_employment_zero_or_blank_is_not_new(self):
        p = self.write([{"NEW_EMPLOYMENT": "1.0"}, {"NEW_EMPLOYMENT": "0"}, {"NEW_EMPLOYMENT": ""},
                        {"NEW_EMPLOYMENT": "2"}])
        self.assertEqual(load_employers([p])["acme"].fy[2025].newemp_jr, 2)

    def test_status_spellings_and_visa_class(self):
        p = self.write([{"CASE_STATUS": "Certified-Withdrawn"}, {"CASE_STATUS": "certified - withdrawn"},
                        {"CASE_STATUS": "Denied"}, {"CASE_STATUS": "Withdrawn"}, {"VISA_CLASS": "H-1B1 Chile"},
                        {"VISA_CLASS": "E-3 Australian"}, {"VISA_CLASS": "h-1b"}])
        self.assertEqual(load_employers([p])["acme"].fy[2025].total, 3)

    def test_software_soc_list_and_levels(self):
        socs = ["15-1252", "15-1253", "15-1211", "15-1221", "15-1242", "15-1243", "15-1244", "15-1299", "15-2051"]
        rows = [{"SOC_CODE": s + ".00"} for s in socs] + [{"SOC_CODE": "15-1251"}, {"SOC_CODE": ""}]
        s = load_employers([self.write(rows)])["acme"].fy[2025]
        self.assertEqual((s.total, s.software), (11, 9))

    def test_levels_ignore_case_and_blank_is_not_junior(self):
        p = self.write([{"PW_WAGE_LEVEL": "i"}, {"PW_WAGE_LEVEL": " II "}, {"PW_WAGE_LEVEL": ""},
                        {"PW_WAGE_LEVEL": "IV"}, {"PW_WAGE_LEVEL": "Level I"}])
        s = load_employers([p])["acme"].fy[2025]
        self.assertEqual((s.jr, s.level1), (2, 1))

    def test_a_file_with_only_filtered_rows_gives_employers_with_no_year(self):
        p = self.write([{"CASE_STATUS": "Denied"}])
        self.assertEqual(load_employers([p]), {})


class WageTest(Base):
    def test_hourly_wage_is_annualized(self):
        s = load_employers([Q1])["orchard labs"].fy[2025]
        self.assertEqual(s.median_jr_wage, 124800)

    def test_median_wage_is_none_without_a_usable_wage(self):
        for wage, unit in (("", ""), ("", "Year"), ("5000", "Month"), ("x", "Year"), ("0", "Year"), ("-5", "Year"),
                           ("nan", "Year"), ("inf", "Hour"), ("100000", "")):
            p = self.write([{"WAGE_RATE_OF_PAY_FROM": wage, "WAGE_UNIT_OF_PAY": unit}])
            self.assertIsNone(load_employers([p])["acme"].fy[2025].median_jr_wage, (wage, unit))
        self.assertIsNone(load_employers([Q1])["riverside university"].fy[2025].median_jr_wage)

    def test_unusable_wages_are_skipped_not_counted_as_zero(self):
        p = self.write([{"WAGE_RATE_OF_PAY_FROM": "100000"}, {"WAGE_RATE_OF_PAY_FROM": "5000",
                                                              "WAGE_UNIT_OF_PAY": "Month"},
                        {"WAGE_RATE_OF_PAY_FROM": ""}])
        self.assertEqual(load_employers([p])["acme"].fy[2025].median_jr_wage, 100000)

    def test_wage_text_with_dollar_and_commas(self):
        p = self.write([{"WAGE_RATE_OF_PAY_FROM": "$120,000.00"}])
        self.assertEqual(load_employers([p])["acme"].fy[2025].median_jr_wage, 120000)

    def test_median_uses_only_junior_software_filings(self):
        p = self.write([{"WAGE_RATE_OF_PAY_FROM": "100000"},
                        {"WAGE_RATE_OF_PAY_FROM": "999999", "PW_WAGE_LEVEL": "III"},
                        {"WAGE_RATE_OF_PAY_FROM": "999999", "SOC_CODE": "11-2021"}])
        self.assertEqual(load_employers([p])["acme"].fy[2025].median_jr_wage, 100000)

    def test_even_count_takes_the_mean_of_the_middle_two_rounded(self):
        p = self.write([{"WAGE_RATE_OF_PAY_FROM": "100001"}, {"WAGE_RATE_OF_PAY_FROM": "100002"}])
        self.assertEqual(load_employers([p])["acme"].fy[2025].median_jr_wage, 100002)  # 100001.5 rounds up
        p = self.write([{"WAGE_RATE_OF_PAY_FROM": "10.5", "WAGE_UNIT_OF_PAY": "Hour"}])
        self.assertEqual(load_employers([p])["acme"].fy[2025].median_jr_wage, 21840)

    def test_evidence_has_the_ruled_keys_and_whole_numbers(self):
        s = load_employers([Q1])["nimbus robotics"].fy[2025]
        self.assertEqual(s.evidence(), {"fy": 2025, "software": 5, "jr": 4, "level1": 2, "newemp_jr": 2,
                                        "median_jr_wage": 125000})
        none = load_employers([Q1])["riverside university"].fy[2025].evidence()
        self.assertEqual(sorted(none), ["fy", "jr", "level1", "newemp_jr", "software"])
        for v in list(s.evidence().values()) + list(none.values()):
            self.assertIs(type(v), int)
            self.assertGreaterEqual(v, 0)


class FileOddityTest(Base):
    def test_renamed_header_variants_load(self):
        with open(Q1, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        rows[0] = [("H_1B_DEPENDENT" if h == "H-1B_DEPENDENT" else h) for h in rows[0]]
        p = self.dir / "LCA_Disclosure_Data_FY2025_Q1.csv"
        with open(p, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
        self.assertTrue(load_employers([p])["depco systems"].fy[2025].h1b_dependent)

    def test_serial_and_iso_dates(self):
        self.assertEqual(parse_decision_date("45930"), "2025-09-30")
        self.assertEqual(parse_decision_date("45930.0"), "2025-09-30")
        self.assertEqual(parse_decision_date("45930.75"), "2025-09-30")
        self.assertEqual(parse_decision_date("2025-09-30 00:00:00"), "2025-09-30")
        self.assertEqual(parse_decision_date("2025-09-30T08:15:00Z"), "2025-09-30")
        self.assertEqual(parse_decision_date("2025-09-30"), "2025-09-30")
        for bad in ("", "  ", "soon", "2025-13-45", "9/30/2025", "0", "-5", "99999999999", None):
            self.assertIsNone(parse_decision_date(bad), bad)

    def test_both_date_forms_load_and_the_latest_wins(self):
        p = self.write([{"DECISION_DATE": "2025-01-15 00:00:00"}, {"DECISION_DATE": "45930"}])
        self.assertEqual(load_employers([p])["acme"].fy[2025].last_decision, "2025-09-30")

    def test_unparseable_dates_leave_last_decision_empty(self):
        p = self.write([{"DECISION_DATE": "soon"}])
        self.assertEqual(load_employers([p])["acme"].fy[2025].last_decision, "")

    def test_missing_required_column_raises(self):
        header = [h for h in COLUMNS if h != "PW_WAGE_LEVEL"]
        p = self.write([{}], header=header)
        with self.assertRaises(MissingColumns):
            load_employers([p])

    def test_empty_file_raises(self):
        p = self.dir / "LCA_Disclosure_Data_FY2025_Q1.csv"
        p.write_text("")
        with self.assertRaises(EmptyTable):
            load_employers([p])
        p.write_text(",".join(COLUMNS) + "\n")
        with self.assertRaises(EmptyTable):
            load_employers([p])

    def test_a_late_error_gives_no_partial_result(self):
        good = self.write([{}], "LCA_Disclosure_Data_FY2025_Q1.csv")
        bad = self.dir / "LCA_Disclosure_Data_FY2025_Q2.csv"
        bad.write_text(",".join(COLUMNS) + "\n")
        with self.assertRaises(TabularError):
            load_employers([good, bad])

    def test_no_files_is_an_error_not_no_employers(self):
        with self.assertRaises(ValueError):
            load_employers([])

    def test_a_file_without_a_fiscal_year_raises(self):
        p = self.write([{}], "data.csv")
        with self.assertRaises(ValueError):
            load_employers([p])

    def test_fiscal_year_from_name(self):
        self.assertEqual(fiscal_year_of(Path("LCA_Disclosure_Data_FY2025_Q4.xlsx")), 2025)
        self.assertEqual(fiscal_year_of(Path("/x/FY2026_Q3.xlsx")), 2026)
        with self.assertRaises(ValueError):
            fiscal_year_of(Path("data.csv"))


class NormEmployerTest(unittest.TestCase):
    def test_norm_employer(self):
        cases = {
            "Nimbus Robotics, Inc.": "nimbus robotics",
            "Orchard Labs LLC dba Orchard": "orchard labs",
            "Orchard Labs L.L.C. d/b/a Orchard": "orchard labs",
            "Acme & Sons (Holdings) Corp": "acme and sons",
            "The Acme Company of America": "acme of",
            "Mesh Systems Inc": "mesh systems",
            "  ": "",
        }
        for raw, want in cases.items():
            self.assertEqual(norm_employer(raw), want, raw)

    def test_fy_stats_is_frozen(self):
        s = load_employers([Q1])["nimbus robotics"].fy[2025]
        self.assertIsInstance(s, FyStats)
        with self.assertRaises(Exception):
            s.total = 1


if __name__ == "__main__":
    unittest.main()
