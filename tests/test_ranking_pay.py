"""Pay text to a wage level (issue #6, Task 2)."""
import re
import tempfile
import time
import unittest
from pathlib import Path

from outreach.ranking.pay import (Area, PayLevel, PayRange, WAGES_PATH, level_at, load_wages, lca_pay_level, parse_pay,
                                  pay_level)

WAGES = load_wages()
BOSTON = next(a for a in WAGES if a.name == "Boston")


class ParsePayTests(unittest.TestCase):
    def test_parse_pay_forms(self):
        cases = {
            "$120,000 - $150,000": PayRange(120000, 150000),
            "$120K to $150K": PayRange(120000, 150000),
            "$120k-$150k": PayRange(120000, 150000),
            "$60/hr": PayRange(124800, 124800),
            "$50 - $60 per hour": PayRange(104000, 124800),
            "$62.50 an hour": PayRange(130000, 130000),
            "USD 90,000": PayRange(90000, 90000),
            "$120,000 \u2013 $150,000": PayRange(120000, 150000),
            "$120,000 \u2014 $150,000": PayRange(120000, 150000),
            "$150,000 - $120,000": PayRange(120000, 150000),
            "$120,000 - 150,000": PayRange(120000, 150000),
            "$120-150K": PayRange(120000, 150000),
            "Salary: US$140,000 a year": PayRange(140000, 140000),
            "Hourly: $50-60": PayRange(104000, 124800),
            "$120,000 plus a $5,000 sign-on bonus": PayRange(120000, 120000),
            "Sign-on $5,000. Base $130,000 - $150,000": PayRange(130000, 150000),
            "$120,000 to 3 openings": PayRange(120000, 120000),
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_pay(text), want)

    def test_parse_pay_refuses_junk(self):
        for text in ("Competitive", "", "$150", "$5,000 sign-on bonus", "£80,000", "€90,000", "CAD 120,000",
                     "CA$120,000", "INR 2,000,000", "$1,200,000", "DOE", "120,000", "120000 - 150000", "$0", "$19,999",
                     "$120,000 - $5,000 bonus", None):
            with self.subTest(text=text):
                self.assertIsNone(parse_pay(text))

    def test_parse_pay_is_linear_on_hostile_text(self):
        for text in ("$1," * 50_000, "$" + "1" * 100_000, "$1 - " * 50_000, "$1,000 to " * 20_000):
            start = time.perf_counter()
            parse_pay(text)
            self.assertLess(time.perf_counter() - start, 1.0)

    def test_source_has_no_literal_dash(self):
        text = (Path(__file__).parent.parent / "outreach" / "ranking" / "pay.py").read_text(encoding="utf-8")
        self.assertNotIn(chr(0x2013), text)
        self.assertNotIn(chr(0x2014), text)


class LevelTests(unittest.TestCase):
    def test_level_at_boundaries(self):
        for annual, want in ((114275, 1), (138236, 1), (138237, 2), (162198, 3), (186160, 4), (90000, 1), (500000, 4)):
            with self.subTest(annual=annual):
                self.assertEqual(level_at(annual, BOSTON), want)

    def test_pay_level_uses_midpoint_by_default(self):
        got = pay_level("$130,000 - $150,000", "Boston, MA", WAGES)
        self.assertIsInstance(got, PayLevel)
        self.assertEqual((got.level, got.at_min, got.at_mid), (2, 1, 2))
        self.assertEqual(got.areas, ("Boston",))
        self.assertEqual(got.note, "posted $130,000 to $150,000 in Boston: level 1 at the minimum, level 2 at the "
                                   "midpoint")
        self.assertEqual(pay_level("$130,000 - $150,000", "Boston, MA", WAGES, basis="minimum").level, 1)
        with self.assertRaises(ValueError):
            pay_level("$130,000 - $150,000", "Boston, MA", WAGES, basis="best")

    def test_single_number_note(self):
        got = pay_level("$140,000", "Austin, TX", WAGES)
        self.assertEqual(got.note, "posted $140,000 in Austin: level 2 at the minimum, level 2 at the midpoint")

    def test_multiple_worksites_take_the_lowest_level(self):
        got = pay_level("$140,000 - $140,000", "New York, NY; Boston, MA", WAGES)
        self.assertEqual((got.level, got.at_min, got.at_mid), (2, 2, 2))
        self.assertEqual(set(got.areas), {"New York", "Boston"})
        # Boston level III starts at 162198 and NYC level III at 165589, so 164,000 is III in Boston but II in NYC.
        got = pay_level("$164,000", "Boston, MA or New York, NY", WAGES)
        self.assertEqual(got.level, 2)
        got = pay_level("$140,000", "New York, NY; Austin, TX", WAGES)
        self.assertEqual(got.level, min(level_at(140000, a) for a in WAGES if a.name in ("New York", "Austin")))

    def test_unlisted_or_remote_location_is_none(self):
        self.assertIsNone(pay_level("$130,000 - $150,000", "Remote, US", WAGES))
        self.assertIsNone(pay_level("$130,000 - $150,000", "Spokane, WA", WAGES))
        self.assertIsNone(pay_level("$130,000 - $150,000", "", WAGES))

    def test_unparseable_pay_is_none(self):
        self.assertIsNone(pay_level("Competitive", "Boston, MA", WAGES))
        self.assertIsNone(pay_level("£80,000", "Boston, MA", WAGES))

    def test_aliases_match_whole_words(self):
        self.assertIsNone(pay_level("$130,000", "Cambridgeshire, UK", WAGES))
        self.assertIsNone(pay_level("$130,000", "Bostonian Way", WAGES))
        self.assertEqual(pay_level("$130,000", "Cambridge, MA", WAGES).areas, ("Boston",))
        self.assertEqual(pay_level("$130,000", "BOSTON", WAGES).areas, ("Boston",))
        self.assertEqual(pay_level("$130,000", "San Jose, CA", WAGES).areas, ("San Jose",))

    def test_lca_pay_level(self):
        self.assertEqual(lca_pay_level(15, 2, min_filings=3), (2, "LCA history: 2 of 15 junior filings at level I"))
        self.assertEqual(lca_pay_level(15, 10, min_filings=3), (1, "LCA history: 10 of 15 junior filings at level I"))
        self.assertEqual(lca_pay_level(4, 2, min_filings=3)[0], 1)  # exactly half is level 1
        self.assertIsNone(lca_pay_level(2, 0, min_filings=3))
        self.assertIsNone(lca_pay_level(0, 0, min_filings=3))


class WageTableTests(unittest.TestCase):
    def test_wage_table_matches_the_research_and_rejects_a_bad_row(self):
        self.assertEqual(BOSTON.levels, (114275, 138237, 162198, 186160))
        self.assertIsInstance(BOSTON, Area)
        by_name = {a.name: a.levels for a in WAGES}
        self.assertEqual(by_name["San Jose"], (152797, 187075, 221374, 255653))
        self.assertEqual(by_name["San Francisco"], (137093, 165131, 193149, 221187))
        self.assertEqual(by_name["Seattle"], (111613, 143104, 174595, 206086))
        self.assertEqual(by_name["New York"], (109845, 137717, 165589, 193461))
        self.assertEqual(by_name["Austin"], (95888, 119642, 143395, 167149))
        self.assertEqual(len(WAGES), 6)
        text = WAGES_PATH.read_text(encoding="utf-8")
        bad = text.replace("level2 = 138237", "level2 = 100000")
        self.assertNotEqual(bad, text)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "wages.toml"
            p.write_text(bad, encoding="utf-8")
            with self.assertRaises(ValueError):
                load_wages(p)

    def test_aliases_are_lower_case_and_belong_to_one_area(self):
        seen: dict[str, str] = {}
        for area in WAGES:
            self.assertTrue(area.aliases, area.name)
            for alias in area.aliases:
                self.assertEqual(alias, alias.lower())
                self.assertNotIn(alias, seen, f"{alias} is in {seen.get(alias)} and {area.name}")
                seen[alias] = area.name

    def test_header_keys_and_note(self):
        text = WAGES_PATH.read_text(encoding="utf-8")
        self.assertRegex(text, r'(?m)^soc = "15-1252"$')
        self.assertRegex(text, r'(?m)^wage_year = "2026-27"$')
        self.assertRegex(text, r'(?m)^source = "OFLC_Wages_2026-27 ALC_Export.csv, hourly x 2080"$')
        self.assertIn("ALC_Export.csv", re.sub(r'(?m)^source.*$', "", text))  # the note on adding areas

    def test_a_missing_key_names_it(self):
        text = WAGES_PATH.read_text(encoding="utf-8").replace("level3 = 162198\n", "")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "wages.toml"
            p.write_text(text, encoding="utf-8")
            with self.assertRaises(KeyError) as cm:
                load_wages(p)
        self.assertIn("level3", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
