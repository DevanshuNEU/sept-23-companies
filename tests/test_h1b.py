"""The H-1B list (#252): USCIS approvals per company per fiscal year, a trend, and a plain markdown file."""
import tempfile
import unittest
from pathlib import Path

from outreach.h1b import build, parse_confirmed, read_uscis, trend
from tests.ranking_store import posting
from tests.storetest import fresh_store, seed_company

HEADER = '"Fiscal Year",Employer,"Initial Approval","Initial Denial","Continuing Approval","Continuing Denial",NAICS,"Tax ID",State,City,ZIP\n'


def csv_file(folder: Path, year: int, rows: list[tuple[str, int, int]]) -> Path:
    """One USCIS archive file: rows of (employer, initial approvals, continuing approvals)."""
    p = folder / f"h1b_datahubexport-{year}.csv"
    p.write_text(HEADER + "".join(f'{year},"{name}",{ia},0,{ca},0,51,1234,CA,SAN JOSE,95110\n' for name, ia, ca in rows))
    return p


HUB_HEADER = ("Line by line\tFiscal Year   \tEmployer (Petitioner) Name\tTax ID\tIndustry (NAICS) Code\tPetitioner City\t"
              "Petitioner State\tPetitioner Zip Code\tNew Employment Approval\tNew Employment Denial\t"
              "Continuation Approval\tContinuation Denial\tChange with Same Employer Approval\t"
              "Change with Same Employer Denial\tNew Concurrent Approval\tNew Concurrent Denial\t"
              "Change of Employer Approval\tChange of Employer Denial\tAmended Approval\tAmended Denial\n")


def hub_file(folder: Path, name: str, year: int, rows: list[tuple[str, int]]) -> Path:
    """One export of the hub's Crosstab view, as USCIS sends it (2026-11-13): UTF-16, tab separated, six approval
    columns. `rows`: (employer, approvals), spread over three of the approval columns."""
    p = folder / name
    body = "".join(f"{n}\t{year}\t{emp}\t1234\t54 - Professional\tSAN JOSE\tCA\t95110\t"
                   f"{a - 2}\t0\t1\t0\t0\t0\t0\t0\t1\t0\t0\t0\n" for n, (emp, a) in enumerate(rows, 1))
    p.write_text(HUB_HEADER + body, encoding="utf-16")
    return p


class TrendTest(unittest.TestCase):
    YEARS = [2022, 2023, 2024]

    def test_rising_falling_steady_new_stopped_none(self):
        self.assertEqual(trend({2022: 10, 2023: 15, 2024: 25}, self.YEARS), "rising")
        self.assertEqual(trend({2022: 50, 2023: 30, 2024: 10}, self.YEARS), "falling")
        self.assertEqual(trend({2022: 20, 2023: 22, 2024: 21}, self.YEARS), "steady")
        self.assertEqual(trend({2024: 3}, self.YEARS), "new")
        self.assertEqual(trend({2022: 8}, self.YEARS), "stopped")
        self.assertEqual(trend({}, self.YEARS), "none")


class ReadTest(unittest.TestCase):
    def test_reads_the_archive_format_and_joins_a_dba_name(self):
        with tempfile.TemporaryDirectory() as d:
            p = csv_file(Path(d), 2023, [("ACME ROBOTICS INC", 2, 3), ("0965688 BC LTD DBA PROCOGIA", 0, 1)])
            rows = list(read_uscis(p))
        self.assertEqual([(r.year, r.employer, r.approvals) for r in rows],
                         [(2023, "ACME ROBOTICS INC", 5), (2023, "0965688 BC LTD DBA PROCOGIA", 1)])
        self.assertEqual(rows[1].keys, ("0965688bc", "procogia"))


class KeyTest(unittest.TestCase):
    def test_legal_and_generic_words_drop(self):
        # 2026-11-13 preview: "MICROSOFT CORPORATION" kept "corporation" in Knock's name_key and missed Microsoft.
        from outreach.h1b import h1b_key
        for a, b in (("MICROSOFT CORPORATION", "Microsoft"), ("ACCENTURE LLP", "Accenture"), ("NVIDIA CORPORATION", "NVIDIA"),
                     ("ABBOTT LABORATORIES", "Abbott"), ("ACE HARDWARE CORPORATION", "Ace Hardware"),
                     ("Retell AI, Inc.", "Retell AI"), ("STRIPE INC", "Stripe"), ("SALESFORCE.COM INC", "Salesforce")):
            with self.subTest(a=a):
                self.assertEqual(h1b_key(a), h1b_key(b))
        self.assertNotEqual(h1b_key("ABRIDGE INFO SYSTEMS INC"), h1b_key("Abridge"))


class HubFormatTest(unittest.TestCase):
    def test_reads_the_hub_export_and_sums_every_approval_column(self):
        with tempfile.TemporaryDirectory() as d:
            p = hub_file(Path(d), "Employer Information.csv", 2026, [("MICROSOFT CORPORATION", 10)])
            (row,) = list(read_uscis(p))
        self.assertEqual((row.year, row.employer, row.approvals), (2026, "MICROSOFT CORPORATION", 10))


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.tmp = Path(tempfile.mkdtemp())
        self.src, self.out = self.tmp / "src", self.tmp / "out"
        self.src.mkdir()

    def software_job(self, company_id):
        from tests.ranking_store import decision
        decision(self.conn, posting(self.conn, company_id), role="software")

    def test_matched_companies_get_a_line_with_counts_and_a_trend(self):
        seed_company(self.conn, "Acme Robotics")
        seed_company(self.conn, "Quiet Co")
        csv_file(self.src, 2022, [("ACME ROBOTICS INC", 4, 6)])
        csv_file(self.src, 2023, [("ACME ROBOTICS, INC.", 10, 15)])
        report = build(self.conn, self.src, self.out, years=[2022, 2023])
        text = (self.out / "companies.md").read_text()
        self.assertIn("| Acme Robotics | 10 | 25 | rising |", text)
        self.assertNotIn("Quiet Co", text)  # no record: absent, which the list reads as "No H-1B on record"
        self.assertEqual(report.matched, 1)

    def test_a_near_name_goes_to_unsure_and_a_confirmed_pair_is_used(self):
        self.software_job(seed_company(self.conn, "Databricks"))  # only companies with an open software job
        csv_file(self.src, 2023, [("DATABRICKS ENGINEERING INC", 30, 40)])
        build(self.conn, self.src, self.out, years=[2023])
        unsure = (self.out / "unsure.md").read_text()
        self.assertIn("Databricks", unsure)
        self.assertIn("DATABRICKS ENGINEERING INC", unsure)
        self.assertNotIn("| Databricks |", (self.out / "companies.md").read_text())
        (self.out / "confirmed.md").write_text("- Databricks = DATABRICKS ENGINEERING INC\n")
        build(self.conn, self.src, self.out, years=[2023])
        self.assertIn("| Databricks | 70 |", (self.out / "companies.md").read_text())
        self.assertNotIn("Databricks", (self.out / "unsure.md").read_text())

    def test_a_pair_he_said_no_to_is_never_asked_again(self):
        self.software_job(seed_company(self.conn, "Databricks"))
        csv_file(self.src, 2023, [("DATABRICKS ENGINEERING INC", 30, 40)])
        self.out.mkdir()
        (self.out / "confirmed.md").write_text("- Databricks = none\n")
        build(self.conn, self.src, self.out, years=[2023])
        self.assertNotIn("Databricks", (self.out / "unsure.md").read_text())
        self.assertNotIn("| Databricks |", (self.out / "companies.md").read_text())

    def test_only_companies_with_an_open_software_posting_are_asked(self):
        from tests.ranking_store import decision
        sales = seed_company(self.conn, "Abridge")
        decision(self.conn, posting(self.conn, sales, "Account Executive"), lane="not_engineering", role="other")
        csv_file(self.src, 2023, [("ABRIDGE INFO SYSTEMS INC", 30, 25)])
        build(self.conn, self.src, self.out, years=[2023])
        self.assertNotIn("Abridge", (self.out / "unsure.md").read_text())

    def test_one_line_per_company_name(self):
        for _ in range(3):
            seed_company(self.conn, "Salesforce")
        csv_file(self.src, 2023, [("SALESFORCE INC", 100, 400)])
        build(self.conn, self.src, self.out, years=[2023])
        self.assertEqual((self.out / "companies.md").read_text().count("| Salesforce |"), 1)

    def test_a_tiny_near_name_or_a_job_above_his_level_is_not_asked(self):
        from tests.ranking_store import decision
        tiny, senior = seed_company(self.conn, "Abnormal AI"), seed_company(self.conn, "Abridge")
        self.software_job(tiny)
        decision(self.conn, posting(self.conn, senior, "Senior Software Engineer"), role="software",
                 level_tier="senior", level_state="reject")
        csv_file(self.src, 2023, [("ABNORMAL AI SECURITY CO", 1, 1), ("ABRIDGE INFO SYSTEMS INC", 30, 25)])
        build(self.conn, self.src, self.out, years=[2023])
        unsure = (self.out / "unsure.md").read_text()
        self.assertNotIn("Abnormal AI", unsure)  # 2 approvals: too few to ask about
        self.assertNotIn("Abridge", unsure)  # its open job is above his level

    def test_a_short_generic_start_is_not_a_near_name(self):
        # "ALPHA TECHNOLOGIES INC" (key "alpha") was offered for "Alpha Nexus Group": a USCIS key that is only the start
        # of the company's key must cover more than half of it.
        self.software_job(seed_company(self.conn, "Alpha Nexus Group"))
        csv_file(self.src, 2023, [("ALPHA TECHNOLOGIES INC", 5, 4)])
        build(self.conn, self.src, self.out, years=[2023])
        self.assertNotIn("Alpha Nexus", (self.out / "unsure.md").read_text())

    def test_a_hub_file_replaces_the_archive_file_of_its_year(self):
        # The archive's FY2023 file is partial and the hub's is whole: one year never counts twice.
        seed_company(self.conn, "Acme Robotics")
        csv_file(self.src, 2023, [("ACME ROBOTICS INC", 2, 3)])
        hub_file(self.src, "fy2023.csv", 2023, [("ACME ROBOTICS INC", 12)])
        build(self.conn, self.src, self.out, years=[2023])
        self.assertIn("| Acme Robotics | 12 |", (self.out / "companies.md").read_text())

    def test_parse_confirmed(self):
        self.assertEqual(parse_confirmed("# notes\n- Acme = ACME INC\n- Beta Co = none\n\nnot a line\n"),
                         {"Acme": "ACME INC", "Beta Co": None})


if __name__ == "__main__":
    unittest.main()


class CliTest(unittest.TestCase):
    def test_build_reads_the_store_read_only_and_says_what_it_wrote(self):
        import io
        from contextlib import redirect_stdout
        from outreach.h1b.__main__ import main
        tmp = Path(tempfile.mkdtemp())
        db = tmp / "outreach.db"
        from outreach.install import install
        install(str(db))
        conn = __import__("outreach.store", fromlist=["connect"]).connect(str(db))
        seed_company(conn, "Acme Robotics")
        conn.close()
        src = tmp / "src"
        src.mkdir()
        csv_file(src, 2023, [("ACME ROBOTICS INC", 1, 2)])
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["build", "--db", str(db), "--source", str(src), "--out", str(tmp / "h1b")])
        self.assertEqual(code, 0)
        self.assertIn("1 companies matched", out.getvalue())
        self.assertIn("| Acme Robotics | 3 |", (tmp / "h1b" / "companies.md").read_text())
