"""The export (#81, Task 6): company level by default, names only with --with-people, never an address or a profile
URL, never inside the repo, never a partial file, and no cell a spreadsheet would run."""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from outreach.insights import export as E
from outreach.insights.config import default_insights_config
from outreach.insights.dataset import load_outcome_rows
from outreach.insights.export import (COMPANY_FIELDS, PEOPLE_FIELDS, ExportError, resolve_out, safe_cell,
                                      to_records, write_csv, write_json)
from tests import apiseed as S
from tests.insights_seed import outcome_touch
from tests.storetest import fresh_store

SENT, LATER = "2026-10-01T15:00:00Z", "2026-10-05T15:00:00Z"


def scratch() -> Path:
    return Path(tempfile.mkdtemp(prefix="insights-export-test-"))


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        a = S.company(self.conn, "Retell AI")
        pat = S.person(self.conn, a, "Pat Lee", email="pat@retell.example",
                       linkedin_url="https://www.linkedin.com/in/patlee")
        outcome_touch(self.conn, a, pat, sent_at=SENT, interview_at=LATER)
        S.evidence(self.conn, a, "size", "80", low=80)
        b = S.company(self.conn, '=HYPERLINK("x")')
        kim = S.person(self.conn, b, "Kim Ray", email=None, channel="linkedin",
                       linkedin_url="https://www.linkedin.com/in/kimray")
        outcome_touch(self.conn, b, kim, sent_at=SENT, replied_at=LATER)
        self.rows = load_outcome_rows(self.conn)
        self.people_rows = load_outcome_rows(self.conn, with_people=True)
        self.folder = scratch()

    def csv_text(self, records, fields) -> str:
        path = self.folder / "out.csv"
        write_csv(records, fields, path)
        return path.read_text(encoding="utf-8")

    def test_fields(self):
        self.assertEqual(32, len(COMPANY_FIELDS))
        self.assertEqual(("best_person",), PEOPLE_FIELDS)
        self.assertEqual(list(COMPANY_FIELDS), list(to_records(self.rows, with_people=False)[0]))

    def test_export_has_no_people_by_default(self):
        records = to_records(self.people_rows, with_people=False)
        for rec in records:
            self.assertNotIn("best_person", rec)
        text = self.csv_text(records, COMPANY_FIELDS)
        self.assertNotIn("@", text)
        self.assertNotIn("linkedin.com", text)
        self.assertNotIn("Pat Lee", text)
        for cells in csv.reader(text.splitlines()):
            for cell in cells:
                self.assertNotIn("@", cell)

    def test_with_people_adds_names_only(self):
        records = to_records(self.people_rows, with_people=True)
        self.assertEqual(["Pat Lee", "Kim Ray"], [r["best_person"] for r in records])
        text = self.csv_text(records, COMPANY_FIELDS + PEOPLE_FIELDS)
        self.assertIn("Pat Lee", text)
        self.assertNotIn("@", text)
        self.assertNotIn("linkedin.com", text)

    def test_csv_cells_cannot_start_a_formula(self):
        text = self.csv_text(to_records(self.rows, with_people=False), COMPANY_FIELDS)
        companies = [r["company"] for r in csv.DictReader(text.splitlines())]
        self.assertEqual(["Retell AI", "'=HYPERLINK(\"x\")"], companies)
        for raw in ("+1", "-1", "@x", "\tx", "\rx", "=1"):
            self.assertEqual("'" + raw, safe_cell(raw))
        self.assertEqual(("", "-5", "3;7", "2.5", "plain"), (safe_cell(None), safe_cell(-5), safe_cell([3, 7]),
                                                            safe_cell(2.5), safe_cell("plain")))
        self.assertEqual("'=a;b", safe_cell(["=a", "b"]))

    def test_csv_round_trips_with_the_header_in_field_order(self):
        text = self.csv_text(to_records(self.rows, with_people=False), COMPANY_FIELDS)
        reader = csv.reader(text.splitlines())
        self.assertEqual(list(COMPANY_FIELDS), next(reader))
        first = dict(zip(COMPANY_FIELDS, next(reader)))
        self.assertEqual(("Retell AI", "interview", "80", "51 to 300", "linkedin_request"),
                         (first["company"], first["best_outcome"], first["size_low"], first["size_band"],
                          first["channels"]))
        self.assertEqual(str(self.rows[0].company_id), first["merged_ids"])

    def test_json_has_fields_rows_and_notes(self):
        path = self.folder / "out.json"
        write_json(to_records(self.rows, with_people=False), COMPANY_FIELDS, path,
                   {"generated_at": "2026-10-26T12:00:00Z", "ranker_version": "rk-1"})
        body = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual({"generated_at", "ranker_version", "fields", "rows", "notes"}, set(body))
        self.assertEqual(list(COMPANY_FIELDS), body["fields"])
        self.assertEqual(2, len(body["rows"]))
        self.assertEqual([self.rows[0].company_id], body["rows"][0]["merged_ids"])
        self.assertEqual('=HYPERLINK("x")', body["rows"][1]["company"])  # JSON is data, not a sheet
        notes = " ".join(body["notes"]).lower()
        self.assertIn("facts as known now", notes)
        self.assertIn("company level only", notes)
        write_json(to_records(self.people_rows, with_people=True), COMPANY_FIELDS + PEOPLE_FIELDS,
                   self.folder / "people.json", {"generated_at": None, "ranker_version": None})
        body = json.loads((self.folder / "people.json").read_text(encoding="utf-8"))
        self.assertIn("includes people's names", " ".join(body["notes"]).lower())
        self.assertNotIn("@", json.dumps(body))

    def test_resolve_out_paths(self):
        cfg = mock.Mock(export_dir=self.folder / "exports")
        default = resolve_out(None, "csv", "2026-10-25", cfg, force=False)
        self.assertEqual((self.folder / "exports" / "outcomes-2026-10-25.csv").resolve(), default)
        self.assertTrue(default.parent.is_dir())
        self.assertEqual((self.folder / "outcomes-2026-10-25.json").resolve(),
                         resolve_out(str(self.folder), "json", "2026-10-25", cfg, force=False))
        named = resolve_out(str(self.folder / "new" / "mine.csv"), "csv", "2026-10-25", cfg, force=False)
        self.assertEqual((self.folder / "new" / "mine.csv").resolve(), named)
        self.assertEqual(Path("~/Desktop/outreach-scratch/insights").expanduser(),
                         default_insights_config().export_dir)
        with self.assertRaises(ExportError):
            resolve_out(None, "xlsx", "2026-10-25", cfg, force=False)

    def test_export_never_writes_into_the_repo(self):
        cfg = mock.Mock(export_dir=E.REPO_ROOT / "exports")
        for out in (str(E.REPO_ROOT / "outcomes.csv"), str(E.REPO_ROOT / "docs"), str(E.REPO_ROOT), None,
                    str(E.REPO_ROOT / "a" / ".." / "b.csv")):
            with self.subTest(out=out), self.assertRaisesRegex(ExportError, "inside the repo"):
                resolve_out(out, "csv", "2026-10-25", cfg, force=True)
        self.assertFalse((E.REPO_ROOT / "outcomes.csv").exists())
        self.assertFalse((E.REPO_ROOT / "exports").exists())
        self.assertFalse((E.REPO_ROOT / "docs" / "outcomes-2026-10-25.csv").exists())

    def test_an_existing_file_needs_force(self):
        path = self.folder / "taken.csv"
        path.write_text("old", encoding="utf-8")
        cfg = mock.Mock(export_dir=self.folder)
        with self.assertRaisesRegex(ExportError, "--force"):
            resolve_out(str(path), "csv", "2026-10-25", cfg, force=False)
        self.assertEqual(path.resolve(), resolve_out(str(path), "csv", "2026-10-25", cfg, force=True))
        self.assertEqual("old", path.read_text(encoding="utf-8"))

    def test_the_write_is_atomic(self):
        path = self.folder / "atomic.csv"
        calls = {"n": 0}
        real = E.safe_cell

        def flaky(value):
            calls["n"] += 1
            if calls["n"] > 40:
                raise OSError("disk full")
            return real(value)

        with mock.patch.object(E, "safe_cell", flaky), self.assertRaises(OSError):
            write_csv(to_records(self.rows, with_people=False), COMPANY_FIELDS, path)
        self.assertEqual([], list(self.folder.iterdir()))
        path.write_text("kept", encoding="utf-8")
        calls["n"] = 0
        with mock.patch.object(E, "safe_cell", flaky), self.assertRaises(OSError):
            write_csv(to_records(self.rows, with_people=False), COMPANY_FIELDS, path)
        self.assertEqual("kept", path.read_text(encoding="utf-8"))
        self.assertEqual([path], list(self.folder.iterdir()))


if __name__ == "__main__":
    unittest.main()
