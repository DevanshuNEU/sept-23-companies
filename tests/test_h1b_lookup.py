"""Reading the H-1B list back (#252): `companies.md` as a tag per company name. Temp folders only."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from outreach.h1b import lookup
from outreach.h1b.lookup import NO_RECORD, tag_for

FILE = """# H-1B approvals by company

Built 2026-11-14 from USCIS's H-1B Employer Data Hub.

| Company | FY2022 | FY2023 | FY2024 | FY2025 | FY2026 | Trend | USCIS name |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1X | 0 | 2 | 1 | 9 | 30 | rising | 1X TECHNOLOGIES INC |
| Accenture | 2233 | 2137 | 2157 | 1886 | 1696 | steady | ACCENTURE LLP |
| 1Password | 0 | 1 | 0 | 0 | 0 | stopped | AGILEBITS USA INC DBA 1PASSWORD |
| Zero Co | 0 | 0 | 0 | 0 | 0 | none | ZERO CO |
| Broken | x | y |
"""


class LookupTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="outreach-h1b-test-"))
        patch = mock.patch.dict(os.environ, {"OUTREACH_H1B_DIR": str(self.dir)})
        patch.start()
        self.addCleanup(patch.stop)
        lookup.forget()

    def write(self, text=FILE):
        (self.dir / "companies.md").write_text(text, encoding="utf-8")

    def test_a_listed_company_has_its_total_and_trend(self):
        self.write()
        tag = tag_for("1X")
        self.assertEqual((tag.label, tag.trend, tag.total), ("H-1B 42, rising", "rising", 42))
        self.assertEqual(tag_for("Accenture").label, "H-1B 10,109, steady")

    def test_the_detail_lists_each_fiscal_year(self):
        self.write()
        self.assertEqual(tag_for("1X").detail,
                         "USCIS approvals by fiscal year: FY2022 0, FY2023 2, FY2024 1, FY2025 9, FY2026 30")

    def test_an_unlisted_company_has_no_h1b_on_record(self):
        self.write()
        for name in ("Nobody Inc", "Zero Co"):
            tag = tag_for(name)
            self.assertEqual((tag.label, tag.total, tag.trend), (NO_RECORD, 0, "none"))

    def test_legal_words_and_case_do_not_stop_a_match(self):
        self.write()
        self.assertEqual(tag_for("1x technologies inc").label, "H-1B 42, rising")
        self.assertEqual(tag_for("ACCENTURE LLP").total, 10109)

    def test_no_file_means_no_tag(self):
        self.assertIsNone(tag_for("1X"))
        self.write("")
        self.assertIsNone(tag_for("1X"))

    def test_a_blank_name_has_no_tag(self):
        self.write()
        self.assertIsNone(tag_for(None))
        self.assertIsNone(tag_for("  "))

    def test_a_new_build_is_read_at_once(self):
        self.write()
        self.assertEqual(tag_for("1X").total, 42)
        self.write(FILE.replace("| 1X | 0 | 2 | 1 | 9 | 30 |", "| 1X | 0 | 2 | 1 | 9 | 40 |"))
        self.assertEqual(tag_for("1X").total, 52)

    def test_two_lines_of_one_key_keep_the_larger_total(self):
        self.write(FILE + "| 1X Inc | 1 | 1 | 1 | 1 | 1 | steady | OTHER |\n")
        self.assertEqual(tag_for("1X").total, 42)

    def test_the_folder_defaults_to_h1b_beside_the_store(self):
        with mock.patch.dict(os.environ, {"OUTREACH_DB": "/store/outreach.db"}):
            os.environ.pop("OUTREACH_H1B_DIR")
            self.assertEqual(lookup.h1b_dir(), Path("/store/h1b"))


if __name__ == "__main__":
    unittest.main()
