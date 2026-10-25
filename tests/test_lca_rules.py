"""LCA classification (#31 Task 5): staffing risk, likely cap-exempt and the work tier, from Employer values built
directly (no files). Nothing here drops an employer: an excluded one is classified, tier 3 and says why."""
import dataclasses
import unittest
from pathlib import Path

from outreach.sources.lca import Employer, FyStats, load_employers, norm_employer
from outreach.sources.lca_rules import (Classification, LcaConfig, classify, load_lca_config, qualifies,
                                        source_fy)

CFG = load_lca_config()
FIXTURES = Path(__file__).parent / "fixtures" / "lca"

STATS = dict(total=6, software=5, jr=4, level1=2, newemp_jr=2, last_decision="2025-03-01", computer_share=0.5,
             jr_share_soft=0.5, newemp_share_soft=0.0, multi_worksite_share=0.0, n_worksite_states=1,
             h1b_dependent=False, willful=False, secondary=0, median_jr_wage=None)


def stats(fy=2025, **over) -> FyStats:
    return FyStats(fy=fy, **{**STATS, **over})


def employer(name="Nimbus Robotics, Inc.", naics="541715", years=None, **over) -> Employer:
    """One employer; `years` maps a fiscal year to FyStats overrides (default: 2025 with the plain stats)."""
    years = years if years is not None else {2025: {}}
    return Employer(key=norm_employer(name), legal_name=name, dba="", fein="11-1111111", city="Reno", state="NV",
                    naics=naics, domain=None, domain_kind="none",
                    fy={y: stats(fy=y, **o) for y, o in years.items()}, **over)


def classify25(name="Nimbus Robotics, Inc.", naics="541715", **stat_over) -> Classification:
    return classify(employer(name, naics, years={2025: stat_over}), 2025, CFG)


# Stats that fire exactly the body-shop profile for "Quill Data Services Inc" (541512, generic name).
BODY = dict(computer_share=1.0, jr_share_soft=1.0, multi_worksite_share=0.0, total=5)


class ExclusionTest(unittest.TestCase):
    def test_each_hard_exclusion_names_its_rule(self):
        cases = [
            ("Plainco Inc", "561320", {}, "excluded: NAICS 5613"),
            ("Plainco Inc", "541715", {"h1b_dependent": True}, "excluded: H-1B dependent"),
            ("Plainco Inc", "541715", {"willful": True}, "excluded: willful violator"),
            ("Plainco Inc", "541715", {"secondary": 1}, "excluded: secondary entity"),
            ("Bluepeak Infotech Inc", "541715", {}, "excluded: staffing word in name"),
            ("Quill Data Services Inc", "541512", BODY, "excluded: body-shop profile"),
        ]
        for name, naics, over, reason in cases:
            with self.subTest(reason=reason):
                c = classify25(name, naics, **over)
                self.assertTrue(c.excluded)
                self.assertEqual((c.staffing_risk, c.staffing_reason, c.work_tier), ("high", reason, 3))

    def test_first_hit_wins_in_analyze_order(self):
        c = classify25("Acme Staffing Solutions", "561320", h1b_dependent=True, willful=True, secondary=3)
        self.assertEqual(c.staffing_reason, "excluded: NAICS 5613")
        c = classify25("Acme Staffing Solutions", "541715", h1b_dependent=True, willful=True, secondary=3)
        self.assertEqual(c.staffing_reason, "excluded: H-1B dependent")
        c = classify25("Acme Staffing Solutions", "541715", willful=True, secondary=3)
        self.assertEqual(c.staffing_reason, "excluded: willful violator")
        c = classify25("Acme Staffing Solutions", "541715", secondary=3)
        self.assertEqual(c.staffing_reason, "excluded: secondary entity")

    def test_staffing_word_is_read_from_the_legal_name_and_the_key(self):
        self.assertTrue(classify25("Acme Staffing Solutions").excluded)
        self.assertTrue(classify25("Northwind Consulting LLC").excluded)
        self.assertFalse(classify25("Nimbus Robotics, Inc.").excluded)

    def test_excluded_secondary_and_body_shop_say_they_may_be_product_companies(self):
        # the research's known false positives (EliseAI, UWorld; Codapt) stay stored and flagged for a look
        for c in (classify25("EliseAI Inc", secondary=2), classify25("Codapt Technologies Inc", "541512", **BODY)):
            self.assertTrue(c.excluded)
            self.assertIn("real product company", c.work_reason)
        self.assertIn("real product company", classify25("Talent Robotics Inc").work_reason)
        self.assertNotIn("real product company", classify25(naics="561320").work_reason)
        self.assertNotIn("real product company", classify25(h1b_dependent=True).work_reason)

    def test_body_shop_needs_every_condition(self):
        name, naics = "Quill Data Services Inc", "541512"
        self.assertTrue(classify25(name, naics, **BODY).excluded)
        dropped = [
            ("naics not IT services", dict(name=name, naics="541715", over=BODY)),
            ("name not generic", dict(name="Quill Robotics Inc", naics=naics, over=BODY)),
            ("computer_share below 0.9", dict(name=name, naics=naics, over={**BODY, "computer_share": 0.89})),
            ("jr_share_soft below 0.8", dict(name=name, naics=naics, over={**BODY, "jr_share_soft": 0.79})),
            ("neither roaming nor 5 filings", dict(name=name, naics=naics, over={**BODY, "total": 4})),
        ]
        for label, d in dropped:
            with self.subTest(dropped=label):
                self.assertFalse(classify25(d["name"], d["naics"], **d["over"]).excluded)
        boundary = [
            ("computer_share 0.9 exactly", {**BODY, "computer_share": 0.9}, True),
            ("jr_share_soft 0.8 exactly", {**BODY, "jr_share_soft": 0.8}, True),
            ("multi_worksite_share 0.49 with 4 filings", {**BODY, "total": 4, "multi_worksite_share": 0.49}, False),
            ("multi_worksite_share 0.5 with 4 filings", {**BODY, "total": 4, "multi_worksite_share": 0.5}, True),
            ("5 filings exactly", {**BODY, "total": 5, "multi_worksite_share": 0.0}, True),
        ]
        for label, over, excluded in boundary:
            with self.subTest(boundary=label):
                self.assertEqual(classify25(name, naics, **over).excluded, excluded)
        # either roaming or 5 filings is enough
        self.assertTrue(classify25(name, naics, **{**BODY, "total": 4, "multi_worksite_share": 0.5}).excluded)

    def test_it_services_naics_is_an_exact_prefix_list(self):
        for naics in ("541511", "541512", "541513", "541519"):
            self.assertTrue(classify25("Quill Data Services Inc", naics, **BODY).excluded, naics)
        self.assertFalse(classify25("Quill Data Services Inc", "541714", **BODY).excluded)


class RiskTest(unittest.TestCase):
    def test_risk_points_map_to_levels(self):
        # one point at a time on a plain name and a non-IT NAICS
        generic = dict(name="Quill Data Services Inc", naics="541715")  # 1 point: generic name
        self.assertEqual(classify25(**generic).staffing_risk, "low")
        zero = classify25("Nimbus Robotics, Inc.")
        self.assertEqual((zero.staffing_risk, zero.excluded), ("low", False))
        # 2 points: generic name + IT-services NAICS (total 2 so no body shop)
        two = classify25("Quill Data Services Inc", "541512", total=2)
        self.assertEqual((two.staffing_risk, two.excluded), ("medium", False))
        # 3 points: add 90% computer jobs with 3 or more filings
        three = classify25("Quill Data Services Inc", "541512", total=3, computer_share=0.95)
        self.assertEqual((three.staffing_risk, three.excluded), ("high", False))
        # 4 and more stay high
        four = classify25("Quill Data Services Inc", "541512", total=3, computer_share=0.95, n_worksite_states=3)
        self.assertEqual((four.staffing_risk, four.excluded), ("high", False))

    def test_each_signal_is_one_point_with_its_own_boundary(self):
        base = dict(name="Nimbus Robotics, Inc.", naics="541715")

        def pts(**over):
            c = classify25(**{**base, **over})
            return c.staffing_reason

        self.assertEqual(pts(), "no staffing signals")
        self.assertIn("IT-services NAICS", pts(naics="541511"))
        self.assertIn("generic tech name", pts(name="Nimbus Systems Inc"))
        self.assertIn("90%+ computer-job filings", pts(computer_share=0.9, total=3))
        self.assertEqual(pts(computer_share=0.9, total=2), "no staffing signals")
        self.assertIn("all junior with new hires", pts(jr_share_soft=1, newemp_share_soft=0.7, software=4))
        self.assertEqual(pts(jr_share_soft=1, newemp_share_soft=0.7, software=3), "no staffing signals")
        self.assertEqual(pts(jr_share_soft=0.99, newemp_share_soft=0.7, software=4), "no staffing signals")
        self.assertIn("3+ worksite states", pts(n_worksite_states=3, total=10))
        self.assertEqual(pts(n_worksite_states=3, total=11), "no staffing signals")
        self.assertIn("multi-worksite filings", pts(multi_worksite_share=0.5))
        self.assertEqual(pts(multi_worksite_share=0.49), "no staffing signals")
        self.assertEqual(pts(n_worksite_states=2, total=10), "no staffing signals")
        self.assertEqual(pts(jr_share_soft=1, newemp_share_soft=0.69, software=4), "no staffing signals")
        self.assertEqual(pts(computer_share=0.89, total=3), "no staffing signals")
        self.assertIn("3+ worksite states", pts(n_worksite_states=4, total=1))

    def test_reason_lists_the_fired_signals(self):
        c = classify25("Quill Data Services Inc", "541512", total=2)
        self.assertEqual(c.staffing_risk, "medium")
        self.assertEqual(c.staffing_reason, "signals: IT-services NAICS, generic tech name")


class CapExemptTest(unittest.TestCase):
    def test_cap_exempt_by_naics_and_by_name(self):
        by_naics = classify25("Riverside Academy", "611310")
        self.assertEqual(by_naics.cap_exempt, "likely")
        self.assertIn("NAICS 611310", by_naics.cap_reason)
        by_name = classify25("Mercy Hospital", "541715")
        self.assertEqual(by_name.cap_exempt, "likely")
        self.assertIn("Hospital", by_name.cap_reason)
        none = classify25("Nimbus Robotics, Inc.")
        self.assertIsNone(none.cap_exempt)
        self.assertTrue(none.cap_reason)

    def test_every_naics_prefix_counts(self):
        for p in ("6111", "6112", "6113", "6221", "6222", "6223", "8132", "8133", "8134", "8139", "9211", "9261",
                  "9271"):
            self.assertEqual(classify25("Plain Name", p + "10").cap_exempt, "likely", p)
        self.assertIsNone(classify25("Plain Name", "611410").cap_exempt)  # business and computer schools

    def test_corporate_name_without_a_strong_word_is_not_likely_cap_exempt(self):
        # the research flagged Lightcast and PowerSchool-like companies; a corporation is a company
        for name in ("Lightcast Inc", "PowerSchool Group LLC", "Foundation Medicine, Inc.", "Acme School Corp",
                     "Brightside Research Co.", "Learnly L.L.C."):
            for naics in ("611710", "541511", "513210"):
                with self.subTest(name=name, naics=naics):
                    c = classify25(name, naics)
                    self.assertIsNone(c.cap_exempt)

    def test_naics_6113_and_622_override_the_corporate_name_veto(self):
        for name, naics in (("Georgia Tech Research Corporation", "611310"), ("Foo Health Inc", "622110"),
                            ("Lightcast Inc", "611310")):
            with self.subTest(name=name, naics=naics):
                c = classify25(name, naics)
                self.assertEqual(c.cap_exempt, "likely")
                self.assertIn(f"NAICS {naics}", c.cap_reason)
                self.assertIn("overrides the corporate-name veto", c.cap_reason)
        for name, naics in (("Riverside Academy", "611310"), ("Mercy Care", "622110")):
            with self.subTest(name=name, naics=naics):
                c = classify25(name, naics)
                self.assertEqual(c.cap_exempt, "likely")
                self.assertIn(f"NAICS {naics} (strong signal)", c.cap_reason)
                self.assertNotIn("veto", c.cap_reason)
        # the other cap-exempt prefixes stay vetoable
        for naics in ("611110", "611210", "813210", "921110"):
            with self.subTest(naics=naics):
                c = classify25("Foo Group Inc", naics)
                self.assertIsNone(c.cap_exempt)
                self.assertIn(f"NAICS {naics}", c.cap_reason)
                self.assertIn("corporation", c.cap_reason)
        self.assertIsNone(classify25("Foo Group Inc", "611710").cap_exempt)

    def test_more_company_forms_make_a_corporate_name(self):
        for name in ("Foo Learning PC", "Foo Learning, P.C.", "Foo Learning PBC", "Foo Learning Pty",
                     "Foo Learning GmbH"):
            with self.subTest(name=name):
                self.assertIsNone(classify25(name, "611210").cap_exempt)
        # only the regex decides below (a vetoable code, no strong or weak word)
        self.assertIsNone(classify25("Foo Learning Pc", "611210").cap_exempt)
        self.assertEqual(classify25("Foo Learning Epc", "611210").cap_exempt, "likely")  # not on a word boundary
        self.assertEqual(classify25("Pc Learning Group", "611210").cap_exempt, "likely")  # form word mid-name

    def test_strong_word_wins_over_a_corporate_suffix(self):
        for name in ("Riverside University", "Georgetown College Inc", "Mercy Hospital, LLC",
                     "Broad Institute Inc.", "Universities Space Research Association"):
            with self.subTest(name=name):
                self.assertEqual(classify25(name, "541715").cap_exempt, "likely")

    def test_weak_name_words_count_only_without_a_corporate_suffix(self):
        self.assertEqual(classify25("Linux Foundation", "541715").cap_exempt, "likely")
        self.assertEqual(classify25("County of Alameda", "541715").cap_exempt, "likely")
        self.assertIsNone(classify25("Foundation Medicine Inc", "541715").cap_exempt)

    def test_words_must_stand_alone(self):
        self.assertIsNone(classify25("Collegeboard Tech Labs", "541715").cap_exempt)
        self.assertIsNone(classify25("Hospitality Tech", "541715").cap_exempt)

    def test_dba_tail_and_parentheses_do_not_hide_the_suffix(self):
        self.assertIsNone(classify25("Orchard Labs LLC dba Orchard School", "541715").cap_exempt)
        self.assertIsNone(classify25("Orchard School Inc (Foundation Group)", "541715").cap_exempt)


class TierTest(unittest.TestCase):
    def test_naics_tier(self):
        self.assertEqual(classify25(naics="513210").naics_tier, "A")
        self.assertEqual(classify25(naics="541511").naics_tier, "B")
        self.assertEqual(classify25(naics="54151").naics_tier, "B")
        self.assertEqual(classify25(naics="722511").naics_tier, "C")
        self.assertEqual(classify25(naics="").naics_tier, "C")
        for p in ("5112", "5132", "5182", "5191", "5192", "5417", "3341", "3342", "3344", "3345"):
            self.assertEqual(classify25(naics=p + "10").naics_tier, "A", p)

    def test_work_tier_table(self):
        rows = [
            # name, naics, stats, tier
            ("Nimbus Robotics, Inc.", "541715", {}, 1),
            ("Orchard Labs LLC", "513210", {}, 1),
            ("Riverside University", "611310", {}, 1),
            ("Clearpath Inc", "513210", {}, 1),
            ("Veltrix Pharma Inc", "541715", {}, 1),
            ("Quill Data Services Inc", "541512", {"total": 2}, 2),  # medium risk
            ("Marlowe Bakery Inc", "722511", {}, 2),  # low risk, naics C
            ("Quill Data Services Inc", "541512", {"total": 3, "computer_share": 0.95}, 2),  # high risk, kept
            ("Mercy Hospital", "541512",  # cap-exempt, high risk, kept
             {"total": 3, "computer_share": 0.95, "n_worksite_states": 3, "multi_worksite_share": 0.5}, 2),
            ("Bridgeway Staffing Solutions LLC", "561320", {}, 3),
            ("Depco Systems LLC", "541511", {"secondary": 3}, 3),
            ("Lumen Apps Inc", "513210", {"h1b_dependent": True}, 3),
        ]
        for name, naics, over, tier in rows:
            with self.subTest(name=name):
                c = classify25(name, naics, **over)
                self.assertEqual(c.work_tier, tier, c)
                self.assertTrue(c.work_reason.startswith(f"tier {tier}"), c.work_reason)

    def test_cap_exempt_medium_risk_is_tier_1_and_naics_c_cap_exempt_too(self):
        c = classify25("Mercy Hospital", "722511", total=2)
        self.assertEqual((c.cap_exempt, c.work_tier), ("likely", 1))
        c = classify25("Mercy Data Hospital", "541512", total=2)  # 2 points: medium
        self.assertEqual((c.staffing_risk, c.work_tier), ("medium", 1))

    def test_excluded_cap_exempt_is_still_tier_3(self):
        c = classify25("Mercy Hospital", "541511", h1b_dependent=True)
        self.assertEqual((c.cap_exempt, c.work_tier), ("likely", 3))

    def test_work_reason_says_why(self):
        self.assertIn("NAICS tier A", classify25().work_reason)
        self.assertIn("likely cap-exempt", classify25("Riverside University", "611310").work_reason)
        self.assertIn("NAICS tier C", classify25(naics="722511").work_reason)
        self.assertIn("staffing risk medium", classify25("Quill Data Services Inc", "541512", total=2).work_reason)
        self.assertIn("excluded: H-1B dependent", classify25(h1b_dependent=True).work_reason)

    def test_classification_is_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            classify25().work_tier = 9

    def test_classifies_the_fiscal_year_it_is_given(self):
        e = employer(years={2024: {"h1b_dependent": True}, 2025: {}})
        self.assertTrue(classify(e, 2024, CFG).excluded)
        self.assertFalse(classify(e, 2025, CFG).excluded)
        with self.assertRaises(ValueError):
            classify(e, 2023, CFG)


class QualifiesTest(unittest.TestCase):
    def test_qualifies_boundaries(self):
        self.assertTrue(qualifies(stats(total=25, jr=1), CFG))
        self.assertFalse(qualifies(stats(total=26, jr=1), CFG))
        self.assertFalse(qualifies(stats(total=25, jr=0), CFG))
        self.assertFalse(qualifies(stats(total=0, jr=1), CFG))
        self.assertTrue(qualifies(stats(total=1, jr=1), CFG))

    def test_source_fy_picks_latest_qualifying_year(self):
        e = employer(years={2023: {"total": 5}, 2024: {"total": 8}, 2025: {"total": 40}})
        self.assertEqual(source_fy(e, CFG), 2024)
        e = employer(years={2023: {"total": 5}, 2024: {"jr": 0}, 2025: {"total": 26}})
        self.assertEqual(source_fy(e, CFG), 2023)
        self.assertIsNone(source_fy(employer(years={2025: {"total": 40}}), CFG))
        self.assertIsNone(source_fy(employer(years={}), CFG))


class ConfigTest(unittest.TestCase):
    def test_config_regexes_match_the_research_samples(self):
        self.assertTrue(CFG.staffing_name_regex.search("Acme Staffing Solutions"))
        self.assertTrue(CFG.staffing_name_regex.search("Bluepeak Infotech"))
        self.assertIsNone(CFG.staffing_name_regex.search("Nimbus Robotics"))
        self.assertTrue(CFG.generic_name_regex.search("Quill Data Services"))
        self.assertIsNone(CFG.generic_name_regex.search("Nimbus Robotics"))

    def test_config_values_from_the_brief(self):
        self.assertIsInstance(CFG, LcaConfig)
        self.assertEqual(CFG.max_small_filings, 25)
        self.assertEqual(CFG.it_services_naics, ("541511", "541512", "541513", "541519"))
        self.assertEqual(CFG.staffing_naics_prefix, "5613")
        self.assertEqual(CFG.naics_tier_b_prefix, "5415")

    def test_a_missing_key_fails_loudly(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "lca.toml"
            p.write_text("max_small_filings = 25\n", encoding="utf-8")
            with self.assertRaises(KeyError):
                load_lca_config(p)


class FixtureRosterTest(unittest.TestCase):
    def test_the_fixture_roster_classifies_as_the_design_expects(self):
        emps = load_employers([FIXTURES / "LCA_Disclosure_Data_FY2025_Q1.csv",
                               FIXTURES / "LCA_Disclosure_Data_FY2025_Q2.csv"])
        got = {}
        for key, e in emps.items():
            fy = source_fy(e, CFG)
            if fy is not None:
                got[e.legal_name.split(" dba ")[0]] = classify(e, fy, CFG)
        tiers = {name: c.work_tier for name, c in got.items()}
        for name, tier in (("Nimbus Robotics, Inc.", 1), ("Orchard Labs LLC", 1), ("Riverside University", 1),
                           ("Clearpath Inc", 1), ("Veltrix Pharma Inc", 1), ("Bridgeway Staffing Solutions LLC", 3),
                           ("Depco Systems LLC", 3), ("Lumen Apps Inc", 3)):
            self.assertEqual(tiers.get(name), tier, (name, tiers))
        # The fixture's Quill (541512, generic name, 2 of 2 filings multi-worksite, all computer, all junior) is the
        # body-shop profile by the written rule, so it is tier 3 here. Its medium-risk tier 2 is tested with stats
        # built directly (test_work_tier_table).
        self.assertEqual(got["Quill Data Services Inc"].staffing_reason, "excluded: body-shop profile")
        self.assertEqual(got["Quill Data Services Inc"].work_tier, 3)


if __name__ == "__main__":
    unittest.main()
