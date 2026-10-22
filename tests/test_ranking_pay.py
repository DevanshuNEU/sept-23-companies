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
    def test_parse_pay_reads_one_clear_usd_annual_range(self):
        cases = {
            "$120,000 - $150,000": PayRange(120000, 150000),
            "$120,000-$150,000": PayRange(120000, 150000),
            "$120K to $150K": PayRange(120000, 150000),
            "$120k-$150k": PayRange(120000, 150000),
            "$120,000 to $150,000 a year": PayRange(120000, 150000),
            "$120,000 \u2013 $150,000": PayRange(120000, 150000),
            "$120,000 \u2014 $150,000": PayRange(120000, 150000),
            "$120,000 - 150,000": PayRange(120000, 150000),
            "$120-150K": PayRange(120000, 150000),
            "USD 90,000 - USD 110,000": PayRange(90000, 110000),
            "US$140,000 - US$160,000 USD": PayRange(140000, 160000),
            "$140,000 - $140,000": PayRange(140000, 140000),
            "$20,000 - $1,000,000": PayRange(20000, 1000000),
            "Base salary: $130,000.75 - $150,000.": PayRange(130001, 150000),
            "$120,000 - $150,000. Bonus eligible.": PayRange(120000, 150000),
            "Sign-on $5,000. Base $130,000 - $150,000": PayRange(130000, 150000),
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_pay(text), want)

    def test_one_number_or_up_to_is_none(self):
        for text in ("$150,000", "USD 90,000", "$120K", "Up to $150,000", "up to $150,000 a year",
                     "$120,000 - up to $150,000", "$120,000 to 3 openings", "From $120,000", "$120,000 +"):
            with self.subTest(text=text):
                self.assertIsNone(parse_pay(text))

    def test_more_than_one_range_is_none(self):
        for text in ("Zone 1: $120,000 - $140,000. Zone 2: $130,000 - $150,000",
                     "$120,000 - $140,000 (Boston) / $130,000 - $150,000 (NYC)",
                     "$120,000 - $140,000 or $130,000 - $150,000",
                     "$120,000 - $140,000\n$130,000 - $150,000"):
            with self.subTest(text=text):
                self.assertIsNone(parse_pay(text))

    def test_another_number_in_the_same_sentence_is_none(self):
        self.assertIsNone(parse_pay("$120,000 - $150,000 or $130,000"))

    def test_a_reversed_or_out_of_bounds_range_is_none(self):
        for text in ("$150,000 - $120,000", "$10,000 - $15,000", "$19,999 - $25,000", "$900,000 - $1,200,000",
                     "$1,200,000 - $1,500,000", "$50 - $60", "$150 - $200", "$0 - $0"):
            with self.subTest(text=text):
                self.assertIsNone(parse_pay(text))

    def test_hourly_monthly_weekly_daily_is_none(self):
        for text in ("$60/hr", "$50 - $60 per hour", "$50 - $60 an hour", "$50 - $60 /hr", "Hourly: $50-60",
                     "$50,000 - $60,000 hourly", "$10,000 - $12,000 per month", "$10,000 - $12,000 a month",
                     "$2,500 - $3,000 per week", "$800 - $900 per day", "$8,000 - $9,000 monthly",
                     "Hourly pay.\n$50,000 - $60,000"):
            with self.subTest(text=text):
                self.assertIsNone(parse_pay(text))

    def test_another_currency_is_none(self):
        for text in ("£80,000 - £100,000", "€90,000 - €110,000", "CAD 120,000 - CAD 150,000",
                     "CAD $120,000 - $150,000", "$120,000 - $150,000 CAD", "$120,000 - $150,000 AUD",
                     "$120,000 - $150,000 EUR", "$120,000 - $150,000 GBP", "CA$120,000 - CA$150,000",
                     "C$120,000 - C$150,000", "A$120,000 - A$150,000", "INR 2,000,000 - 2,500,000",
                     "₹2,000,000 - ₹2,500,000", "Paid in GBP. $120,000 - $150,000"):
            with self.subTest(text=text):
                self.assertIsNone(parse_pay(text))

    def test_a_money_word_in_the_same_sentence_is_none(self):
        for word in ("bonus", "sign-on bonus", "signing bonus", "signing", "relocation", "equity", "stock", "stipend",
                     "tuition", "raised", "funding", "Equity", "BONUS", "sign on"):
            with self.subTest(word=word):
                self.assertIsNone(parse_pay(f"$120,000 - $150,000 plus {word}"))
                self.assertIsNone(parse_pay(f"We {word}: $120,000 - $150,000"))
        self.assertIsNone(parse_pay("We raised $5,000,000 in funding and pay $120,000 - $150,000."))

    def test_a_money_word_in_another_sentence_does_not_block(self):
        self.assertEqual(parse_pay("$120,000 - $150,000. Equity offered."), PayRange(120000, 150000))
        self.assertEqual(parse_pay("Equity offered.\n$120,000 - $150,000"), PayRange(120000, 150000))

    def test_no_pay_is_none(self):
        for text in ("Competitive", "", "DOE", "120,000 - 150,000", "120000 - 150000", None, 120000, "$"):
            with self.subTest(text=text):
                self.assertIsNone(parse_pay(text))

    def test_text_over_the_cap_is_none_never_truncated(self):
        pay = "$120,000 - $150,000"
        exactly = pay + " " * (5000 - len(pay))
        self.assertEqual(len(exactly), 5000)
        self.assertEqual(parse_pay(exactly), PayRange(120000, 150000))
        self.assertIsNone(parse_pay(exactly + "x"))
        self.assertIsNone(parse_pay(pay + " " * 6000))
        self.assertIsNone(parse_pay("x" * 6000 + pay))

    def test_parse_pay_is_linear_on_hostile_text(self):
        for text in ("$1," * 1_600, "$" + "1" * 4_990, "$1 - " * 1_000, "$1,000 to " * 400, "$1 " * 1_600,
                     "$" * 4_999, "1,000" * 900):
            start = time.perf_counter()
            parse_pay(text)
            self.assertLess(time.perf_counter() - start, 1.0)

    def test_source_has_no_literal_dash(self):
        for name in ("pay.py", "wages.toml"):
            text = (Path(__file__).parent.parent / "outreach" / "ranking" / name).read_text(encoding="utf-8")
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

    def test_equal_ends_note(self):
        got = pay_level("$140,000 - $140,000", "Austin, TX", WAGES)
        self.assertEqual(got.note, "posted $140,000 in Austin: level 2 at the minimum, level 2 at the midpoint")

    def test_multiple_worksites_take_the_lowest_level(self):
        got = pay_level("$140,000 - $140,000", "New York, NY; Boston, MA", WAGES)
        self.assertEqual((got.level, got.at_min, got.at_mid), (2, 2, 2))
        self.assertEqual(set(got.areas), {"New York", "Boston"})
        # Boston level III starts at 162198 and NYC level III at 165589, so 164,000 is III in Boston but II in NYC.
        got = pay_level("$164,000 - $164,000", "Boston, MA or New York, NY", WAGES)
        self.assertEqual(got.level, 2)
        got = pay_level("$140,000 - $140,000", "New York, NY; Austin, TX", WAGES)
        self.assertEqual(got.level, min(level_at(140000, a) for a in WAGES if a.name in ("New York", "Austin")))

    def test_unparseable_pay_is_none(self):
        self.assertIsNone(pay_level("Competitive", "Boston, MA", WAGES))
        self.assertIsNone(pay_level("£80,000 - £90,000", "Boston, MA", WAGES))
        self.assertIsNone(pay_level("$140,000", "Boston, MA", WAGES))

    def test_the_location_table(self):
        pay = "$130,000 - $150,000"
        listed = {
            "Boston, MA": "Boston", "Boston, Massachusetts": "Boston", "Boston, MA, USA": "Boston",
            "Cambridge, MA": "Boston", "Lynn, MA": "Boston", "Salem, NH": None, "Greater Boston": "Boston",
            "San Jose, CA": "San Jose", "Campbell, CA": "San Jose", "Santa Clara, California": "San Jose",
            "San Francisco, CA": "San Francisco", "Alameda, CA": "San Francisco", "Oakland, CA": "San Francisco",
            "Bay Area": "San Francisco", "San Francisco Bay Area": "San Francisco",
            "Silicon Valley": "San Jose",
            "Seattle, WA": "Seattle", "Everett, WA": "Seattle", "Bellevue, Washington": "Seattle",
            "New York, NY": "New York", "New York, New York": "New York", "NYC": "New York",
            "New York City": "New York", "Newark, NJ": "New York", "Jersey City, NJ": "New York",
            "Brooklyn, NY": "New York",
            "Austin, TX": "Austin", "Austin, Texas": "Austin", "Kyle, TX": "Austin", "San Marcos, TX": "Austin",
        }
        for location, area in listed.items():
            with self.subTest(location=location):
                got = pay_level(pay, location, WAGES)
                self.assertEqual(None if got is None else got.areas[0], area)

    def test_remote_or_anywhere_is_none(self):
        for location in ("Remote", "Remote, US", "Boston, MA (Remote)", "Remote or Boston, MA", "Anywhere",
                         "Anywhere in the US", "Boston, MA or anywhere", "REMOTE - New York, NY", ""):
            with self.subTest(location=location):
                self.assertIsNone(pay_level("$130,000 - $150,000", location, WAGES))

    def test_a_city_without_its_state_is_unlisted(self):
        for location in ("Boston", "BOSTON", "Cambridge", "San Jose", "San Francisco", "SF", "Seattle", "Austin",
                         "New York", "Brooklyn", "Kyle", "Newark", "Everett", "Lynn", "Alameda"):
            with self.subTest(location=location):
                self.assertIsNone(pay_level("$130,000 - $150,000", location, WAGES))

    def test_the_wrong_state_or_a_non_us_place_is_unlisted(self):
        for location in ("Cambridge, UK", "Cambridgeshire, UK", "Cambridge, Ontario, Canada", "Everett, MA",
                         "Newark, DE", "Newark, CA", "Lynn, IN", "Campbell, OH", "San Marcos, CA", "Austin, MN",
                         "Kyle, SD", "Boston, UK", "Boston, Lincolnshire, England", "San Jose, Costa Rica",
                         "Spokane, WA", "Portland, ME", "Boston, TX", "Boston, MA, United Kingdom", "Bostonian Way",
                         "Boston, MA, Canada"):
            with self.subTest(location=location):
                self.assertIsNone(pay_level("$130,000 - $150,000", location, WAGES))

    def test_worksites_are_matched_one_by_one(self):
        got = pay_level("$130,000 - $150,000", "Boston, MA; Austin, TX", WAGES)
        self.assertEqual(set(got.areas), {"Boston", "Austin"})
        got = pay_level("$130,000 - $150,000", "Boston, MA | Cambridge, UK", WAGES)
        self.assertEqual(got.areas, ("Boston",))
        got = pay_level("$130,000 - $150,000", "New York, NY or Seattle", WAGES)
        self.assertEqual(got.areas, ("New York",))

    def test_every_area_has_states_and_the_state_is_not_an_alias_alone(self):
        for area in WAGES:
            self.assertTrue(area.states, area.name)
            for state in area.states:
                self.assertRegex(state, r"^[A-Z]{2}$")
        for ambiguous in ("everett", "newark", "kyle", "lynn", "campbell", "alameda", "san marcos", "sf"):
            for area in WAGES:
                self.assertNotIn(ambiguous, area.metro, area.name)

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
