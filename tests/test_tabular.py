"""The CSV and streaming xlsx reader (#31 Task 3). Fixture workbooks are built by tests/xlsxbuild.py."""
import csv
import tempfile
import unittest
import zipfile
from pathlib import Path

from outreach.sources.tabular import EmptyTable, MissingColumns, TabularError, normalize_header, read_rows
from tests.xlsxbuild import MAIN, RichText, col_letters, write_xlsx

HEADER = ["CASE_NUMBER", "EMPLOYER_NAME", "SOC_CODE", "DECISION_DATE"]
ROWS = [["I-200-1", "Acme Inc", "15-1252", "45930"], ["I-200-2", "Beta, LLC", "15-1211", "45931"]]
ALL = ["CASE_NUMBER", "EMPLOYER_NAME", "SOC_CODE", "DECISION_DATE"]


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return Path(path)


class TabularTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def xlsx(self, header=HEADER, rows=ROWS, name="f.xlsx", **kw):
        return write_xlsx(self.dir / name, header, rows, **kw)

    def read(self, path, wanted=ALL):
        return list(read_rows(path, wanted))

    def test_xlsx_roundtrip(self):
        got = self.read(self.xlsx())
        self.assertEqual(got, [dict(zip(HEADER, r)) for r in ROWS])

    def test_xlsx_sparse_rows_place_cells_by_reference(self):
        path = self.xlsx(["A_COL", "B_COL", "C_COL"], [["a1", None, "c1"], [None, "b2", None]], sparse=True)
        self.assertEqual(self.read(path, ["A_COL", "B_COL", "C_COL"]),
                         [{"A_COL": "a1", "B_COL": "", "C_COL": "c1"}, {"A_COL": "", "B_COL": "b2", "C_COL": ""}])

    def test_sparse_columns_past_z_use_two_letter_references(self):
        header = [f"H{i}" for i in range(30)]
        row = [None] * 30
        row[27], row[0] = "far", "near"
        self.assertEqual(col_letters(27), "AB")
        path = self.xlsx(header, [row], sparse=True)
        self.assertEqual(self.read(path, ["H0", "H27", "H28"]), [{"H0": "near", "H27": "far", "H28": ""}])

    def test_inline_strings(self):
        self.assertEqual(self.read(self.xlsx(inline=True)), [dict(zip(HEADER, r)) for r in ROWS])

    def test_rich_text_shared_string(self):
        path = self.xlsx(["NAME"], [[RichText("Acme", " & ", "Sons")]])
        self.assertEqual(self.read(path, ["NAME"]), [{"NAME": "Acme & Sons"}])

    def test_phonetic_runs_are_not_part_of_the_text(self):
        path = self.xlsx(["NAME"], [[RichText("Acme", phonetic="ACMEKANA")]])
        self.assertEqual(self.read(path, ["NAME"]), [{"NAME": "Acme"}])

    def test_serial_date_is_returned_as_text(self):
        path = self.xlsx(["DECISION_DATE", "WAGE"], [[45930, 99000.5]])
        self.assertEqual(self.read(path, ["DECISION_DATE", "WAGE"]), [{"DECISION_DATE": "45930", "WAGE": "99000.5"}])

    def test_fein_with_leading_zeros_keeps_them(self):
        path = self.xlsx(["FEIN"], [["000000000"], ["012345678"]])
        self.assertEqual(self.read(path, ["FEIN"]), [{"FEIN": "000000000"}, {"FEIN": "012345678"}])

    def test_header_variants_are_one_column(self):
        self.assertEqual(normalize_header("  h-1b_dependent "), "H_1B_DEPENDENT")
        for variant in ("H-1B_DEPENDENT", "H_1B_DEPENDENT", " h-1b_dependent"):
            path = self.xlsx([variant], [["Y"]], name="v.xlsx")
            self.assertEqual(self.read(path, ["H_1B_DEPENDENT"]), [{"H_1B_DEPENDENT": "Y"}])
        path = write_csv(self.dir / "v.csv", ["H-1B_DEPENDENT"], [["N"]])
        self.assertEqual(self.read(path, ["H_1B_DEPENDENT"]), [{"H_1B_DEPENDENT": "N"}])

    def test_csv_and_xlsx_agree(self):
        rows = ROWS + [["I-200-3", "", "15-1252", ""], ["I-200-4", 'Say "hi"', "", "45932"]]
        from_xlsx = self.read(self.xlsx(HEADER, rows, name="a.xlsx"))
        from_csv = self.read(write_csv(self.dir / "a.csv", HEADER, rows))
        self.assertEqual(from_xlsx, from_csv)
        self.assertEqual(len(from_csv), 4)

    def test_only_wanted_columns_returned(self):
        for path in (self.xlsx(name="w.xlsx"), write_csv(self.dir / "w.csv", HEADER, ROWS)):
            got = self.read(path, ["SOC_CODE", "CASE_NUMBER"])
            self.assertEqual(got[0], {"SOC_CODE": "15-1252", "CASE_NUMBER": "I-200-1"})
            self.assertEqual(set(got[1]), {"SOC_CODE", "CASE_NUMBER"})

    def test_missing_wanted_column_raises_naming_it(self):
        for path in (self.xlsx(["CASE_NUMBER"], [["x"]], name="m.xlsx"), write_csv(self.dir / "m.csv", ["CASE_NUMBER"], [["x"]])):
            with self.assertRaises(MissingColumns) as ctx:
                self.read(path, ["CASE_NUMBER", "SOC_CODE"])
            self.assertIn("SOC_CODE", str(ctx.exception))
            self.assertIsInstance(ctx.exception, TabularError)

    def test_zero_data_rows_raises_empty_table(self):
        for path in (self.xlsx(HEADER, [], name="e.xlsx"), write_csv(self.dir / "e.csv", HEADER, [])):
            with self.assertRaises(EmptyTable):
                self.read(path)

    def test_blank_trailing_rows_are_not_data(self):
        path = self.xlsx(HEADER, [[None] * 4, [None] * 4], name="b.xlsx", sparse=True)
        with self.assertRaises(EmptyTable):
            self.read(path)
        path = write_csv(self.dir / "b.csv", HEADER, [["", "", "", ""]])
        with self.assertRaises(EmptyTable):
            self.read(path)

    def test_blank_trailing_rows_after_data_are_skipped(self):
        path = self.xlsx(HEADER, ROWS + [[None] * 4], sparse=True)
        self.assertEqual(len(self.read(path)), 2)

    def test_truncated_zip_raises_tabular_error(self):
        whole = self.xlsx().read_bytes()
        cut = self.dir / "cut.xlsx"
        cut.write_bytes(whole[:100])
        with self.assertRaises(TabularError):
            self.read(cut)

    def test_truncated_midway_raises_tabular_error(self):
        rows = [[f"I-{i}", f"Employer {i}", "15-1252", "45930"] for i in range(2000)]
        whole = self.xlsx(HEADER, rows).read_bytes()
        cut = self.dir / "half.xlsx"
        cut.write_bytes(whole[: len(whole) // 2])
        with self.assertRaises(TabularError):
            self.read(cut)

    def test_sheet_xml_cut_off_raises_tabular_error(self):
        xml = (f'<?xml version="1.0"?><worksheet xmlns="{MAIN}"><sheetData><row r="1">'
               '<c r="A1" t="inlineStr"><is><t>CASE_NUMBER</t></is></c></row><row r="2"><c r="A2" t="inl')
        path = write_xlsx(self.dir / "x.xlsx", ["CASE_NUMBER"], [], sheet_xml=xml)
        with self.assertRaises(TabularError):
            self.read(path, ["CASE_NUMBER"])

    def test_damage_inside_a_valid_zip_raises_after_the_good_rows(self):
        rows = [[f"I-{i}", f"Employer number {i}"] for i in range(5000)]
        whole = bytearray(write_xlsx(self.dir / "w.xlsx", ["CASE_NUMBER", "NAME"], rows, inline=True).read_bytes())
        mid = len(whole) // 3
        whole[mid:mid + 300] = bytes(300)  # the central directory at the end stays intact
        bad = self.dir / "bad.xlsx"
        bad.write_bytes(bytes(whole))
        seen = []
        with self.assertRaises(TabularError):
            for row in read_rows(bad, ["CASE_NUMBER"]):
                seen.append(row)
        self.assertLess(len(seen), 5000)

    def test_not_a_zip_raises_tabular_error(self):
        junk = self.dir / "junk.xlsx"
        junk.write_bytes(b"<html>503 Service Unavailable</html>")
        with self.assertRaises(TabularError):
            self.read(junk)

    def test_zip_without_a_sheet_raises_tabular_error(self):
        path = self.dir / "nosheet.xlsx"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("hello.txt", "hi")
        with self.assertRaises(TabularError):
            self.read(path)

    def test_unsupported_suffix_raises(self):
        path = self.dir / "f.xls"
        path.write_bytes(b"x")
        with self.assertRaises(TabularError):
            self.read(path)

    def test_csv_with_bom_and_short_rows(self):
        path = self.dir / "bom.csv"
        path.write_bytes("﻿CASE_NUMBER,SOC_CODE\nI-1\nI-2,15-1252\n".encode("utf-8"))
        self.assertEqual(self.read(path, ["CASE_NUMBER", "SOC_CODE"]),
                         [{"CASE_NUMBER": "I-1", "SOC_CODE": ""}, {"CASE_NUMBER": "I-2", "SOC_CODE": "15-1252"}])

    def test_csv_bad_encoding_raises_tabular_error(self):
        path = self.dir / "latin.csv"
        path.write_bytes(b"NAME\nCaf\xe9\n")
        with self.assertRaises(TabularError):
            self.read(path, ["NAME"])

    def test_empty_file_raises(self):
        path = self.dir / "empty.csv"
        path.write_bytes(b"")
        with self.assertRaises(TabularError):
            self.read(path, ["NAME"])


if __name__ == "__main__":
    unittest.main()
