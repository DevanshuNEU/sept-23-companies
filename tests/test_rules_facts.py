"""Company facts from the old dashboard: size, stage and E-Verify evidence (issue #4, Task 13).

Every sizeWhy, head and funding string here is a real value from dashboard/data.js.
"""
import unittest

from outreach.rules.config import default_config, load_config
from outreach.rules.facts import (EvidenceDraft, OldSignal, everify_status, from_old_dashboard,
                                  old_dashboard_signals, parse_size, parse_stage)
from outreach.store.timefmt import utcnow
from tests.storetest import fresh_store, seed_company

EN = chr(0x2013)  # an en dash, built from its code point so this file has no literal one

SIZE = [  # (text, value, low, high, confidence)
    ("no filing record: most likely an early startup; source estimate 11-50 employees (Built In)",
     "11-50", 11, 50, "estimate"),
    ("about 50 people", "50", 50, 50, "estimate"),
    ("~50-150", "50-150", 50, 150, "estimate"),
    ("~1,000", "1000", 1000, 1000, "estimate"),
    ("size genuinely uncertain, somewhere around 300 to 700", "300-700", 300, 700, "guess"),
    ("no filing record: most likely an early startup; source estimate ~100 (guess)", "100", 100, 100, "guess"),
    ("no filing record: most likely an early startup; source estimate under 50", "1-49", 1, 49, "estimate"),
    ("known to be well over 500 people", "501+", 501, None, "estimate"),
    ("unknown", None, None, None, None),
    ("early", None, None, None, None),
]

# More real wording: what each reads as, or None.
MORE_SIZE = [
    ("no filing record: most likely an early startup; source estimate est. <25", "1-24", 1, 24, "guess"),
    ("no filing record: most likely an early startup; source estimate est. 10-20 (with Medal)",
     "10-20", 10, 20, "guess"),
    ("no filing record: most likely an early startup; source estimate <20 (guess)", "1-19", 1, 19, "guess"),
    ("no filing record: most likely an early startup; source estimate under 30 (6-month-old startup)",
     "1-29", 1, 29, "estimate"),
    ("no filing record: most likely an early startup; source estimate ~40 plans 100 by end of next year",
     "40", 40, 40, "estimate"),
    ("2 lifetime visa filings, consistent with under 500; source estimate ~25-40 (estimate)",
     "25-40", 25, 40, "estimate"),
    ("around 300 to 500 people after its 2024-2025 raises", "300-500", 300, 500, "estimate"),
    ("large contractor workforce, core staff near 300", "300", 300, 300, "estimate"),
    ("now Grammarly's company name, well over 500 people", "501+", 501, None, "estimate"),
    ("~1,000+ eng", "1000+", 1000, None, "estimate"),
    ("~1,300" + EN + "2,100", "1300-2100", 1300, 2100, "estimate"),
    ("no filing record: most likely an early startup; source estimate small (Series A, 100+ practices served)",
     None, None, None, None),
    ("no filing record: most likely an early startup; source estimate unknown (small)", None, None, None, None),
    ("no filing record: most likely an early startup", None, None, None, None),
    ("0 lifetime visa filings, consistent with under 500", None, None, None, None),
    ("1430 lifetime visa filings; every known under-500 company has 26 or fewer", None, None, None, None),
    ("the visa-data name match pointed at an unrelated employer, so size is unknown", None, None, None, None),
    ("a Constellation Software group, size unclear", None, None, None, None),
    # A bare number (or N+) is a size only after a cue, before a headcount noun, or first after "source estimate";
    # a year never counts without a headcount noun.
    ("founded in 2015, about 50 people", "50", 50, 50, "estimate"),
    ("Est. 2015, about 40 people", "40", 40, 40, "guess"),  # "est." is a guess word wherever it sits
    ("source estimate 2024 headcount ~60", "60", 60, 60, "estimate"),
    ("500 companies use us", None, None, None, None),
    ("Series A, 400+ practices served", None, None, None, None),
    ("10 years old", None, None, None, None),
    ("raised 20 million", None, None, None, None),
    ("2 offices, 40 engineers", "40", 40, 40, "estimate"),
    ("12/2025 about 30", "30", 30, 30, "estimate"),
    ("no filing record: most likely an early startup; source estimate 7", "7", 7, 7, "estimate"),
    ("no filing record: most likely an early startup; source estimate 78 per Consider board", "78", 78, 78,
     "estimate"),
    ("~500+", "500+", 500, None, "estimate"),
    ("2000 employees", "2000", 2000, 2000, "estimate"),
    ("government or defence contractor, clearance-heavy", None, None, None, None),
]

STAGE = [("Series B era", "series_b"), ("YC Winter 2024", "yc"), ("Late-stage private", "late_private"),
         ("Early stage", "early"), ("not researched", None)]

MORE_STAGE = [  # (text, value, confidence)
    ("Series A/B era", "series_a", "guess"),
    ("Seed / Series A", "seed", "guess"),
    ("Late-stage, high valuation", "late_private", "estimate"),
    ("Public (NYSE: AXP)", "public", "estimate"),
    ("Series D $250M at $3.5B (2025-06-10 Coatue + Index)", "series_d", "estimate"),
    ("$20M Series A, Sept 2025 led by Updata Partners ($30M total)", "series_a", "estimate"),
    ("Unverified", None, None),
    ("Private, profitable", None, None),
]

RETELL = {
    "id": "retell", "sizeClass": "small", "sizeWhy": "about 50 people", "head": "~50-150",
    "yc": {"slug": "retell-ai", "batch": "Winter 2024", "team": 50},
    "funding": "Series B era",
    "everify": {"status": "enrolled", "entity": "Retell AI, Inc", "enrolled": "2025-05-05", "sites": 1,
                "plus": True, "note": "California, 10 to 19 employees.", "checked": "2026-09-27"},
}


class SizeTest(unittest.TestCase):
    def test_small_default_is_not_evidence(self):
        for why in [
            "no filing record: most likely an early startup",
            "no filing record and not a name I recognise as large: most likely an early startup",
            "1 lifetime visa filings, consistent with under 500",
            "no filing record: most likely an early startup; source estimate unknown (small)",
        ]:
            with self.subTest(why=why):
                self.assertEqual(from_old_dashboard({"sizeClass": "small", "sizeWhy": why}), [])
        self.assertEqual(
            from_old_dashboard({"sizeClass": "cleared", "sizeWhy": "government or defence contractor, clearance-heavy"}),
            [])
        self.assertEqual(from_old_dashboard({"sizeClass": "small"}), [])
        self.assertEqual(from_old_dashboard({"sizeClass": "big"}), [])

    def _check_size(self, text, value, low, high, confidence):
        got = parse_size(text, "old_dashboard")
        if value is None:
            self.assertIsNone(got)
            return
        self.assertIsNotNone(got)
        self.assertEqual((got.fact, got.value, got.num_low, got.num_high, got.confidence),
                         ("size", value, low, high, confidence))
        self.assertEqual(got.quote, text)

    def test_parse_size(self):
        for case in SIZE:
            with self.subTest(text=case[0]):
                self._check_size(*case)
        builtin = parse_size(SIZE[0][0], "old_dashboard")
        self.assertEqual(builtin.source_kind, "builtin")
        self.assertEqual(parse_size("about 50 people", "old_dashboard").source_kind, "old_dashboard")

    def test_more_real_wording(self):
        for case in MORE_SIZE:
            with self.subTest(text=case[0]):
                self._check_size(*case)

    def test_yc_team_is_yc_evidence(self):
        drafts = from_old_dashboard({"yc": {"team": 50}})
        self.assertEqual([(d.fact, d.value, d.num_low, d.num_high, d.source_kind) for d in drafts],
                         [("size", "50", 50, 50, "yc")])
        self.assertEqual(from_old_dashboard({"yc": {"team": None}}), [])
        self.assertEqual(from_old_dashboard({"yc": {"team": 0}}), [])

    def test_none_cfg_loads_once(self):
        self.assertIs(default_config(), default_config())
        cfg = load_config()
        self.assertEqual(parse_size("about 50 people", "old_dashboard", cfg),
                         parse_size("about 50 people", "old_dashboard"))


class StageTest(unittest.TestCase):
    def test_parse_stage(self):
        for text, value in STAGE:
            with self.subTest(text=text):
                got = parse_stage(text, "old_dashboard")
                if value is None:
                    self.assertIsNone(got)
                else:
                    self.assertEqual((got.fact, got.value, got.source_kind), ("stage", value, "old_dashboard"))
                    self.assertIsNone(got.num_low)

    def test_more_stage_wording(self):
        for text, value, confidence in MORE_STAGE:
            with self.subTest(text=text):
                got = parse_stage(text, "old_dashboard")
                if value is None:
                    self.assertIsNone(got)
                else:
                    self.assertEqual((got.value, got.confidence, got.quote), (value, confidence, text))


class EverifyTest(unittest.TestCase):
    def test_everify(self):
        got = everify_status({"status": "enrolled", "entity": "Retell AI, Inc", "enrolled": "2025-05-05",
                              "checked": "2026-09-27"})
        self.assertEqual((got.fact, got.value, got.observed_on, got.source_kind, got.confidence),
                         ("everify", "enrolled", "2026-09-27", "everify", "estimate"))
        self.assertIn("Retell AI, Inc", got.quote)
        self.assertIn("2025-05-05", got.quote)
        none = everify_status({"status": "none", "checked": "2026-09-27"})
        self.assertEqual((none.value, none.observed_on), ("not_enrolled", "2026-09-27"))
        self.assertIsNone(everify_status({"status": "enrolled"}))

    def test_bad_records_say_nothing(self):
        self.assertIsNone(everify_status({"status": "maybe", "checked": "2026-09-27"}))
        self.assertIsNone(everify_status({"status": "enrolled", "checked": "27 Sept 2026"}))
        self.assertIsNone(everify_status({}))


class OldSignalTest(unittest.TestCase):
    def test_old_hide_signals(self):
        cases = [
            ({"sizeClass": "big", "sizeWhy": "1430 lifetime visa filings; every known under-500 company has 26 or fewer"},
             "big_by_filings"),
            ({"sizeClass": "big", "sizeWhy": "decades of SEC filings, an established company"}, "sec_filings"),
            ({"sizeClass": "cleared", "sizeWhy": "aerospace, export-controlled"}, "defence_contractor"),
            ({"sizeClass": "cleared", "sizeWhy": "government or defence contractor, clearance-heavy"},
             "defence_contractor"),
            ({"sizeClass": "agency", "sizeWhy": "a recruiting agency posting for other companies, not the employer"},
             "agency"),
            ({"sizeClass": "big", "sizeWhy": "known to be well over 500 people"}, "over_500"),
        ]
        for company, kind in cases:
            with self.subTest(company=company):
                self.assertEqual(old_dashboard_signals(company), [OldSignal(kind, company["sizeWhy"])])
        self.assertEqual(
            old_dashboard_signals({"sizeClass": "small", "sizeWhy": "no filing record: most likely an early startup"}),
            [])
        self.assertEqual(old_dashboard_signals({"sizeClass": "big", "sizeWhy": "about 2000 people"}), [])
        self.assertIsNone(
            parse_size("1430 lifetime visa filings; every known under-500 company has 26 or fewer", "old_dashboard"))

    def test_over_500_is_also_size_evidence(self):
        drafts = from_old_dashboard({"sizeClass": "big", "sizeWhy": "known to be well over 500 people"})
        self.assertEqual([(d.value, d.num_low, d.num_high, d.confidence) for d in drafts],
                         [("501+", 501, None, "estimate")])


class RetellTest(unittest.TestCase):
    def test_retell_record(self):
        drafts = from_old_dashboard(RETELL)
        sizes = [d for d in drafts if d.fact == "size"]
        stages = [d for d in drafts if d.fact == "stage"]
        everify = [d for d in drafts if d.fact == "everify"]
        self.assertEqual(len(drafts), 5)
        self.assertEqual([(d.value, d.source_kind) for d in sizes],
                         [("50", "old_dashboard"), ("50-150", "old_dashboard"), ("50", "yc")])
        self.assertEqual([d.value for d in stages], ["series_b"])
        self.assertEqual([(d.value, d.observed_on) for d in everify], [("enrolled", "2026-09-27")])


class SchemaTest(unittest.TestCase):
    def test_every_draft_fits_the_evidence_table(self):
        conn = fresh_store()
        company_id = seed_company(conn)
        drafts = list(from_old_dashboard(RETELL))
        drafts += [d for case in SIZE + MORE_SIZE if (d := parse_size(case[0], "old_dashboard"))]
        drafts += [d for case in MORE_STAGE if (d := parse_stage(case[0], "old_dashboard"))]
        drafts.append(everify_status({"status": "none", "checked": "2026-09-27"}))
        for d in drafts:
            with self.subTest(draft=d):
                conn.execute(
                    "INSERT INTO evidence(company_id, fact, value, num_low, num_high, confidence, source_kind, "
                    "source_ref, quote, observed_on, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT DO NOTHING",
                    (company_id, d.fact, d.value, d.num_low, d.num_high, d.confidence, d.source_kind,
                     d.source_ref, d.quote, d.observed_on, utcnow()))

    def test_a_bad_draft_fails_at_once(self):
        with self.assertRaises(ValueError):
            EvidenceDraft("size", "50", "estimate", "old_dashboard")  # a size needs num_low
        with self.assertRaises(ValueError):
            EvidenceDraft("stage", "series_z", "estimate", "old_dashboard")
        with self.assertRaises(ValueError):
            EvidenceDraft("everify", "enrolled", "estimate", "everify")  # no observed_on
        with self.assertRaises(ValueError):
            EvidenceDraft("stage", "seed", "sure", "old_dashboard")


if __name__ == "__main__":
    unittest.main()
