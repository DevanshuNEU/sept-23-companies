"""`h1b_history` (#252): what the H-1B list says about one company, read only, through the official SDK client on a
scratch store."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from outreach.h1b import lookup
from tests import apiseed as seed
from tests.mcptest import McpCase, fingerprint

LIST = """# H-1B approvals by company

Built 2026-11-14 from USCIS's H-1B Employer Data Hub (initial plus continuing approvals, fiscal years 2022 to 2026).

| Company | FY2022 | FY2023 | FY2024 | FY2025 | FY2026 | Trend | USCIS name |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Acme Labs | 0 | 2 | 1 | 9 | 30 | rising | ACME LABS INC; ACME AI INC |
"""


class H1bHistoryCase(McpCase):
    def setUp(self):
        super().setUp()
        self.dir = Path(tempfile.mkdtemp(prefix="outreach-h1b-mcp-"))
        patcher = mock.patch.dict(os.environ, {"OUTREACH_H1B_DIR": str(self.dir)})
        patcher.start()
        self.addCleanup(patcher.stop)
        lookup.forget()
        self.addCleanup(lookup.forget)
        self.acme = seed.company(self.db, "Acme Labs", "working")

    def build(self, text=LIST):
        (self.dir / "companies.md").write_text(text, encoding="utf-8")


class HistoryTest(H1bHistoryCase):
    def test_a_listed_company_answers_with_its_years_trend_and_uscis_names(self):
        self.build()
        out = self.ok("h1b_history", {"company": "Acme Labs"})
        self.assertEqual(out["company"], {"resolution": "found", "id": self.acme, "name": "Acme Labs",
                                          "message": "In Knock.", "candidates": [], "merged_note": None})
        h1b = out["h1b"]
        self.assertEqual((h1b["on_record"], h1b["label"], h1b["total"], h1b["trend"]),
                         (True, "H-1B 42, rising", 42, "rising"))
        self.assertEqual(h1b["by_year"][-1], {"fiscal_year": 2026, "approvals": 30})
        self.assertEqual(len(h1b["by_year"]), 5)
        self.assertEqual(h1b["uscis_names"], ["ACME LABS INC", "ACME AI INC"])
        self.assertEqual(out["list"]["built_on"], "2026-11-14")
        self.assertIn("USCIS", out["list"]["source"])

    def test_the_id_works_too_and_a_merged_id_answers_as_its_survivor(self):
        self.build()
        old = seed.company(self.db, "Acme Old Name", "removed", merged_into=self.acme)
        for args in ({"company_id": self.acme}, {"company_id": old}):
            out = self.ok("h1b_history", args)
            self.assertEqual((out["company"]["id"], out["h1b"]["total"]), (self.acme, 42))
        self.assertIn("merged", self.ok("h1b_history", {"company_id": old})["company"]["merged_note"])

    def test_a_company_the_list_does_not_hold_has_no_h1b_on_record(self):
        self.build()
        other = seed.company(self.db, "Nobody Corp", "working")
        out = self.ok("h1b_history", {"company_id": other})
        self.assertEqual(out["h1b"]["on_record"], False)
        self.assertEqual((out["h1b"]["label"], out["h1b"]["total"], out["h1b"]["by_year"]), ("No H-1B on record", 0, []))
        self.assertIn("2026-11-14", out["message"])

    def test_an_alias_name_finds_the_line_when_the_name_is_not_listed(self):
        self.build()
        co = seed.company(self.db, "Acme Holdings Group Worldwide", "working")
        seed.alias(self.db, co, "Acme Labs")
        self.assertEqual(self.ok("h1b_history", {"company_id": co})["h1b"]["total"], 42)

    def test_no_list_is_a_plain_error_that_names_the_fix(self):
        err = self.error("h1b_history", {"company": "Acme Labs"})
        self.assertEqual(err["code"], "no_list")
        self.assertIn("python -m outreach.h1b build", err["fix"])

    def test_an_unknown_name_is_a_question_and_gives_no_h1b_answer(self):
        self.build()
        unknown = self.ok("h1b_history", {"company": "Zzyzx Quux"})
        self.assertEqual((unknown["company"]["resolution"], unknown["h1b"]), ("unknown", None))

    def test_it_needs_a_company_or_an_id_and_changes_nothing(self):
        self.build()
        before = fingerprint(self.db)
        self.assertEqual(self.error("h1b_history", {})["code"], "bad_input")
        self.ok("h1b_history", {"company": "Acme Labs"})
        self.assertEqual(fingerprint(self.db), before)


if __name__ == "__main__":
    unittest.main()
