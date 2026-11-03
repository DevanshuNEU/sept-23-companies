"""Title lane and title level (issue #4, Task 8)."""
import dataclasses
import unittest

from outreach.rules.config import load_config
from outreach.rules.title import TitleResult, classify_title

CASES = [  # (title, lane, tier)
    ("Member of Technical Staff", "engineering", "open"),
    ("Member of Technical Staff, Applied AI", "engineering", "open"),
    ("Member of Technical Staff 2", "engineering", "swe2"),
    ("Senior Member of Technical Staff", "engineering", "senior"),
    ("Forward Deployed Engineer", "engineering", "open"),
    ("Software Engineer II", "engineering", "swe2"),
    ("Software Engineer 2", "engineering", "swe2"),
    ("SWE II", "engineering", "swe2"),
    ("Software Engineer Front-End 2", "engineering", "swe2"),
    ("Software Engineer I", "engineering", "swe1"),
    ("Software Engineer I/II", "engineering", "swe1"),
    ("Staff Engineer II", "engineering", "senior"),
    ("Staff Engineer I", "engineering", "senior"),
    ("Staff Software Engineer, Agent Platform", "engineering", "senior"),
    ("Software Engineer III", "engineering", "swe2"),  # #154: III is read from the body (Task 4), tier swe2
    ("Software Engineer IV", "engineering", "senior"),
    ("Software Engineer 3", "engineering", "swe2"),
    ("Full Stack Engineer 4 (Go, AWS)", "engineering", "senior"),
    ("Backend Engineer 5", "engineering", "senior"),
    ("Machine Learning Engineer Graduate - Lead Ads", "engineering", "new_grad"),
    ("Forward Deployed Engineer Fellow", "engineering", "early_career"),
    ("AI Fellow - Member of Technical Staff", "engineering", "early_career"),
    ("Backend Engineer, New Grad to Staff Level", "engineering", "new_grad"),
    ("Engineering Manager, New Grad Hiring", "engineering", "senior"),
    ("Associate Software Engineer", "engineering", "early_career"),
    ("Software Engineer", "engineering", None),
    ("Cellular Layer-1 Control Software Engineer", "engineering", None),
    ("Founding Engineer", "engineering", None),
    ("Software Engineering Intern", "intern", None),
    ("Solutions Engineer", "off_lane", None),
    ("Help Desk Engineer - Linux Level II", "off_lane", None),
    ("Scientist I/II", "not_engineering", None),
    ("Recruiter", "not_engineering", None),
    ("General Application - Engineering", "general_application", None),
]

# Beyond the brief's cases: the senior words carried from refresh/crawl_boards.py:59, numerals written onto the
# noun, numerals that are not levels, and "level N".
MORE_CASES = [
    ("AVP, Software Engineering", "engineering", "senior"),
    ("Mid-Level Software Engineer", "engineering", "senior"),
    ("Mid Level Backend Engineer", "engineering", "senior"),
    ("Software Engineer (SDE2)", "engineering", "swe2"),
    ("SDE-1", "engineering", "swe1"),
    ("Software Engineer, Level 1", "engineering", "swe1"),
    ("Software Engineer V", "engineering", "senior"),
    ("Software Engineer, Payments 2.0", "engineering", None),
    ("Software Engineer, Tier 1 Support Tools", "engineering", None),
    ("Software Engineer, Infrastructure", "engineering", None),
    ("Software Engineer 10x", "engineering", None),
    ("Sr. Software Engineer", "engineering", "senior"),
    ("New Grad Software Engineer III", "engineering", "new_grad"),  # entry wording is read before the numeral now
    ("FDE", "engineering", "open"),
    ("Software Engineer, I/O Platform", "engineering", None),
    ("Software Engineer II/III", "engineering", "swe2"),
    ("Software Engineer (Backend) II", "engineering", "swe2"),
    ("Senior Engineer, Graduate Program", "engineering", "senior"),
    ("Graduate Software Engineer", "engineering", "new_grad"),
    ("Principal MTS", "engineering", "senior"),
    # role qualifiers between the noun and the numeral (title.level_qualifiers)
    ("Software Engineer, Backend II", "engineering", "swe2"),
    ("Software Engineer - Full Stack 3", "engineering", "swe2"),
    ("Software Engineer, Mobile 1", "engineering", "swe1"),
    ("Backend Engineer, Back-End 2 (Remote)", "engineering", "swe2"),
    # any other word in between: the numeral is not a level
    ("Software Engineer, Web 3", "engineering", None),
    ("Software Engineer - Python 3", "engineering", None),
    ("Backend Engineer (Python 3)", "engineering", None),
    ("Software Engineer, Product 4", "engineering", None),
    ("Software Engineer, Growth 3", "engineering", None),
    ("Software Engineer, Search 2", "engineering", None),
    ("Software Engineer, AI Agents 2", "engineering", None),
    ("Software Engineer, Series 1", "engineering", None),
    ("Software Engineer, Layer 2", "engineering", None),
]

# #4 follow-up 2026-10-22: postdoctoral roles need a PhD, so they are off lane (he is a May 2026 MS new grad).
# The controls keep the results they had before the change.
POSTDOC_CASES = [
    ("Postdoctoral Associate-Systems Engineering", "off_lane", None),
    ("Postdoctoral Researcher - Civil, Environmental, and Ocean Engineering", "off_lane", None),
    ("Postdoc Software Engineer", "off_lane", None),
    ("Post-Doctoral Fellow, Machine Learning Engineering", "off_lane", None),
    ("Post Doc Developer", "off_lane", None),
    ("Software Engineer, New Grad", "engineering", "new_grad"),
    ("Graduate Software Engineer", "engineering", "new_grad"),
    ("Engineering Fellow", "engineering", "early_career"),
]

# #4 follow-up 2026-10-23: support, field, metrology and academic titles are off lane. Contract roles stay in
# (he wants contract-to-hire), so the contract controls keep their results.
OFF_LANE_CASES = [
    ("Mandarin-speaking: Customer Success Engineer (Contract)", "off_lane", None),
    ("Customer Success Engineer", "off_lane", None),
    ("Robotics Field Engineer", "off_lane", None),
    ("Field Engineers, Hardware Rollout", "off_lane", None),
    ("Field Application Engineer", "off_lane", None),
    ("Field Applications Engineer", "off_lane", None),
    ("CMM Programmer", "off_lane", None),
    ("Metrology Engineer", "off_lane", None),
    ("Assistant Professor of Professional Practice, Clinical Practicum & Internship Coordinator, "
     "Counselor Education", "off_lane", None),
    ("Adjunct Faculty - Engineering", "off_lane", None),
    ("Lecturer, Software Engineering", "off_lane", None),
    ("Faculty Software Engineer", "off_lane", None),
    ("Customer Success Engineers", "off_lane", None),
    ("Customer Success Manager, Engineering", "off_lane", None),
    ("Customer Success Specialist, Developer Tools", "off_lane", None),
    ("Customer Success Architect, Software Engineering", "off_lane", None),
    # narrowed 2026-10-23 (second commit): these software roles stay in
    ("Forward Deployed Engineer | AI Implementation & Customer Success", "engineering", "open"),
    ("AI Field Engineer - Enterprise", "engineering", None),
    ("MLOps Field Engineer", "engineering", None),
    ("Cloud Field Engineer", "engineering", None),
    ("Field Engineer", "engineering", None),  # a plain Field Engineer passes, accepted
    # already off lane before this change (field service and field technician stay as they were)
    ("Field Service Engineer", "off_lane", None),
    ("Field Technician", "not_engineering", None),
    # unchanged controls
    ("Software Engineer (Contract)", "engineering", None),
    ("Software Engineer, Contract to Hire", "engineering", None),
    ("Software Engineer, Field Systems", "engineering", None),
    ("Software Engineering Intern", "intern", None),
    ("RA-II/Sr. Research Associate Protein Engineering", "engineering", "senior"),
    ("Research Associate, Software Engineering", "engineering", "early_career"),
]


class ClassifyTitleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()

    def _check(self, cases):
        for title, lane, tier in cases:
            with self.subTest(title=title):
                got = classify_title(title, self.cfg)
                self.assertEqual(got.lane, lane)
                if lane == "engineering":
                    self.assertEqual(got.tier, tier)

    def test_cases(self):
        self._check(CASES)

    def test_more_cases(self):
        self._check(MORE_CASES)

    def test_postdoc_cases(self):
        self._check(POSTDOC_CASES)

    def test_postdoc_titles_are_off_lane_with_no_tier(self):
        for title, lane, _ in POSTDOC_CASES:
            if lane == "off_lane":
                with self.subTest(title=title):
                    got = classify_title(title, self.cfg)
                    self.assertEqual(got.lane, "off_lane")
                    self.assertIsNone(got.tier)

    def test_off_lane_cases(self):
        self._check(OFF_LANE_CASES)

    def test_basis_is_never_empty(self):
        for title, _, _ in CASES + MORE_CASES:
            with self.subTest(title=title):
                basis = classify_title(title, self.cfg).basis
                self.assertIsInstance(basis, str)
                self.assertTrue(basis.strip())

    def test_basis_names_what_the_title_says(self):
        expect = {
            "Software Engineer II": "Title says Engineer II",
            "Machine Learning Engineer Graduate - Lead Ads": "Title says new grad",
            "Associate Software Engineer": "Title says early career",
            "Member of Technical Staff": "MTS role, no level in the title",
            "Software Engineer": "Title states no level",
            "Software Engineer, Level 1": "Title says Level 1",
        }
        for title, basis in expect.items():
            with self.subTest(title=title):
                self.assertEqual(classify_title(title, self.cfg).basis, basis)

    def test_non_engineering_lanes_have_no_tier(self):
        for title, lane, _ in CASES:
            if lane != "engineering":
                with self.subTest(title=title):
                    self.assertIsNone(classify_title(title, self.cfg).tier)

    def test_result_is_frozen(self):
        got = classify_title("Software Engineer", self.cfg)
        self.assertIsInstance(got, TitleResult)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            got.lane = "intern"

    def test_empty_title(self):
        got = classify_title("", self.cfg)
        self.assertEqual(got.lane, "not_engineering")
        self.assertIsNone(got.tier)
        self.assertTrue(got.basis)


class IIITests(unittest.TestCase):
    """#154: III and 3 are swe2 with a title basis; the body decides fit or reject later (Task 4)."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()

    def test_swe_iii_is_swe2_with_title_basis(self):
        for title in ("Software Engineer III", "Software Engineer 3", "SDE III", "Backend Engineer, Backend 3",
                      "Software Engineer (III)"):
            with self.subTest(title=title):
                got = classify_title(title, self.cfg)
                self.assertEqual((got.lane, got.tier, got.basis), ("engineering", "swe2", "Title says III"))

    def test_iv_and_v_stay_senior(self):
        for title in ("Software Engineer IV", "Software Engineer V", "Software Engineer 4", "Software Engineer 5",
                      "SDE IV"):
            with self.subTest(title=title):
                got = classify_title(title, self.cfg)
                self.assertEqual(got.tier, "senior")
                self.assertIn("above entry level", got.basis)

    def test_senior_words_still_beat_iii(self):
        for title in ("Senior Software Engineer III", "Staff Engineer III", "Engineering Manager III"):
            with self.subTest(title=title):
                self.assertEqual(classify_title(title, self.cfg).tier, "senior")

    def test_iii_that_is_not_a_level_is_still_none(self):
        self.assertIsNone(classify_title("Software Engineer, Web 3", self.cfg).tier)


# #154: tech words added to [title] engineering: each makes a title with no other engineering word an engineering one.
TECH_NOUN_TITLES = {
    "ml": "ML Researcher",
    "machine learning": "Machine Learning Scientist",
    "ai engineer": "AI Engineer",
    "applied ai": "Applied AI Lead Researcher",
    "data engineer": "Data Engineer",
    "analytics engineer": "Analytics Engineer",
    "applied scientist": "Applied Scientist",
    "research engineer": "Research Engineer",
    "data scientist": "Data Scientist",
    "research scientist": "Research Scientist",
    "sre": "SRE",
    "site reliability": "Site Reliability Specialist",
}

# #154: words added to [title] off_lane: each makes an engineering title off lane.
NON_TECH_TITLES = {
    "supply chain": "Supply Chain Engineer",
    "procurement": "Procurement Engineer",
    "logistics": "Logistics Engineer",
    "sourcing": "Sourcing Engineer",
    "buyer": "Buyer, Engineering Components",
    "planner": "Planner, Engineering Programs",
    "thermal": "Thermal Engineer",
    "optical": "Optical Engineer",
    "photonics": "Photonics Engineer",
    "laser": "Laser Engineer",
    "process engineer": "Process Engineer",
    "hvac": "HVAC Engineer",
    "chemical": "Chemical Engineer",
    "materials": "Materials Engineer",
    "data analyst": "Data Analyst, Engineering Metrics",
    "data annotation": "Data Annotation Engineer",
}


class TechWordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()

    def test_each_added_tech_noun_is_engineering(self):
        for word, title in TECH_NOUN_TITLES.items():
            with self.subTest(word=word):
                self.assertIn(word, self.cfg.title["engineering"])
                self.assertEqual(classify_title(title, self.cfg).lane, "engineering")

    def test_each_added_non_tech_word_is_off_lane(self):
        for word, title in NON_TECH_TITLES.items():
            with self.subTest(word=word):
                self.assertIn(word, self.cfg.title["off_lane_unless_software"])
                self.assertEqual(classify_title(title, self.cfg).lane, "off_lane")

    def test_non_tech_words_do_not_hide_a_title_that_names_software_work(self):
        for title in ("Software Engineer, Logistics", "Backend Engineer, Supply Chain", "Platform Engineer, Procurement",
                      "Machine Learning Engineer, Materials Discovery", "Frontend Developer, Thermal Dashboards",
                      "Data Engineer, Planner", "SRE, Optical Networks", "DevOps Engineer, Laser Fabs",
                      "Full Stack Engineer, HVAC Controls", "Infrastructure Engineer, Sourcing"):
            with self.subTest(title=title):
                self.assertEqual(classify_title(title, self.cfg).lane, "engineering")

    def test_non_tech_titles_without_software_words_are_off_lane(self):
        for title in ("Logistics Engineer", "Thermal Systems Engineer", "Process Engineer", "Materials Engineer II"):
            with self.subTest(title=title):
                self.assertEqual(classify_title(title, self.cfg, SOFTWARE_BODY).lane, "off_lane")

    def test_older_off_lane_words_keep_today_s_behavior_beside_software_words(self):
        for title in ("Software Engineer, Hardware", "Backend Engineer, Mechanical", "Software Support Engineer",
                      "Embedded Software Engineer"):
            with self.subTest(title=title):
                self.assertEqual(classify_title(title, self.cfg).lane, "off_lane")

    def test_software_title_words_come_from_config(self):
        self.assertTrue(self.cfg.title["software_titles"])

    def test_data_analyst_bi_and_annotation_alone_are_not_tech(self):
        for title in ("Data Analyst", "Senior Data Analyst", "Business Intelligence Analyst", "Data Annotation Specialist"):
            with self.subTest(title=title):
                self.assertNotIn(classify_title(title, self.cfg).lane, ("engineering", "intern"))

    def test_scientist_is_engineering_lane(self):
        self.assertNotIn("scientist", self.cfg.title["off_lane"])
        for title in ("Data Scientist", "Research Scientist, Language Models", "Applied Scientist II",
                      "Senior Research Scientist"):
            with self.subTest(title=title):
                self.assertEqual(classify_title(title, self.cfg).lane, "engineering")

    def test_scientist_is_a_level_noun(self):
        for title, tier, basis in (("Applied Scientist II", "swe2", "Title says Scientist II"),
                                   ("Research Scientist III", "swe2", "Title says III"),
                                   ("Data Scientist I", "swe1", "Title says Scientist I"),
                                   ("Research Scientist IV", "senior", None),
                                   ("Staff Applied Scientist II", "senior", None)):
            with self.subTest(title=title):
                got = classify_title(title, self.cfg)
                self.assertEqual((got.lane, got.tier), ("engineering", tier))
                if basis:
                    self.assertEqual(got.basis, basis)

    def test_scientist_without_a_tech_word_names_no_engineering_role(self):
        self.assertEqual(classify_title("Scientist I/II", self.cfg).lane, "not_engineering")
        self.assertEqual(classify_title("Staff Scientist, Biology", self.cfg).lane, "not_engineering")

    def test_existing_off_lane_words_stay(self):
        for title in ("Mechanical Engineer", "Postdoc Software Engineer", "Recruiter", "Hardware Engineer"):
            with self.subTest(title=title):
                self.assertNotEqual(classify_title(title, self.cfg).lane, "engineering")


# #172: a body under title.min_body_chars counts as no body, so every body here is padded past it with a neutral
# filler that holds no software or physical word.
FILLER = (" Our team works from a bright office with snacks, a library and a standing desk for everyone. "
          "We care about kind colleagues, clear writing and steady growth, and we celebrate small wins together. "
          "Lunch is shared on Fridays, mentors meet new hires weekly and the whole group plans its year in the spring.")
SOFTWARE_BODY = ("You will build backend services in Python and TypeScript, deploy on AWS with Docker and "
                 "Kubernetes, and own our public API." + FILLER)
PHYSICAL_BODY = ("You will model parts in SolidWorks, run FEA and CFD in ANSYS, apply GD&T on drawings and "
                 "review the BOM with suppliers. Injection molding and sheet metal experience helps." + FILLER)
AMBIGUOUS_TITLES = ("Systems Engineer", "Test Engineer", "Design Engineer", "Product Engineer",
                    "Application Engineer", "Project Engineer")


class AmbiguousTitleTests(unittest.TestCase):
    """#154: Systems, Test, Design, Product, Application and Project Engineer: the body decides."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()

    def test_systems_engineer_python_body_is_tech(self):
        got = classify_title("Systems Engineer", self.cfg, SOFTWARE_BODY)
        self.assertEqual(got.lane, "engineering")
        self.assertIsNone(got.tier)

    def test_design_engineer_solidworks_body_is_off_lane(self):
        got = classify_title("Design Engineer", self.cfg, PHYSICAL_BODY)
        self.assertEqual(got.lane, "off_lane")
        self.assertIsNone(got.tier)
        self.assertTrue(got.basis.strip())

    def test_ambiguous_title_with_no_body_is_engineering_until_its_body_arrives(self):
        # the crawler fetches bodies only for engineering and intern lanes, so a bodiless ambiguous title stays in
        # the lane and the scorer decides once the body is there
        for title in AMBIGUOUS_TITLES:
            for body in (None, "", "   "):
                with self.subTest(title=title, body=body):
                    got = classify_title(title, self.cfg, body)
                    self.assertEqual((got.lane, got.tier), ("engineering", None))
        self.assertEqual(classify_title("Systems Engineer", self.cfg).lane, "engineering")

    def test_the_same_posting_with_a_physical_body_is_off_lane(self):
        for title in AMBIGUOUS_TITLES:
            with self.subTest(title=title):
                self.assertEqual(classify_title(title, self.cfg, "").lane, "engineering")
                self.assertEqual(classify_title(title, self.cfg, PHYSICAL_BODY).lane, "off_lane")

    def test_every_ambiguous_title_follows_its_body(self):
        for title in AMBIGUOUS_TITLES:
            with self.subTest(title=title):
                self.assertEqual(classify_title(title, self.cfg, SOFTWARE_BODY).lane, "engineering")
                self.assertEqual(classify_title(title, self.cfg, PHYSICAL_BODY).lane, "off_lane")

    def test_software_must_lead_by_two(self):
        lead_0 = "We use Python every day. We also use a PLC on the line." + FILLER  # 1 software, 1 physical
        lead_1 = "We use Python and SQL every day. We also use a PLC on the line." + FILLER  # 2 software, 1 physical
        lead_2 = "We use Python, SQL and Docker every day. We also use a PLC on the line." + FILLER  # 3 software, 1 physical
        self.assertEqual(classify_title("Systems Engineer", self.cfg, lead_0).lane, "off_lane")
        self.assertEqual(classify_title("Systems Engineer", self.cfg, lead_1).lane, "off_lane")
        self.assertEqual(classify_title("Systems Engineer", self.cfg, lead_2).lane, "engineering")

    def test_a_body_with_neither_kind_of_word_is_off_lane(self):
        self.assertEqual(classify_title("Systems Engineer", self.cfg, "You will join a friendly team." + FILLER).lane, "off_lane")

    def test_distinct_words_count_once(self):
        body = "Python Python Python Python Python. Python is our language. PLC." + FILLER
        self.assertEqual(classify_title("Systems Engineer", self.cfg, body).lane, "off_lane")
        self.assertEqual(classify_title("Systems Engineer", self.cfg, body + " Docker and SQL too.").lane,
                         "engineering")

    def test_simple_plurals_count(self):
        self.assertEqual(classify_title("Systems Engineer", self.cfg, "You will design APIs and microservices." + FILLER).lane,
                         "engineering")
        self.assertEqual(classify_title("Systems Engineer", self.cfg, "Read PCBs and PLCs; check BOMs and CFDs." + FILLER).lane,
                         "off_lane")

    def test_words_match_whole_words_only(self):
        # "javascript" is not "java"; "rustic" is not "rust"; "capital" holds no word of either list
        body = "A rustic office with a capital view, a javascript kata and a goal." + FILLER
        self.assertEqual(classify_title("Systems Engineer", self.cfg, body).lane, "off_lane")

    def test_software_word_forms_count(self):
        body = "Backend and frontend work in C++ and Go with CI/CD on Linux." + FILLER
        self.assertEqual(classify_title("Product Engineer", self.cfg, body).lane, "engineering")

    def test_ambiguous_title_keeps_its_level_when_tech(self):
        got = classify_title("Systems Engineer II", self.cfg, SOFTWARE_BODY)
        self.assertEqual((got.lane, got.tier), ("engineering", "swe2"))
        got = classify_title("Senior Test Engineer", self.cfg, SOFTWARE_BODY)
        self.assertEqual((got.lane, got.tier), ("engineering", "senior"))

    def test_a_software_title_skips_the_body_scorer(self):
        thin = "We use Python every day." + FILLER  # one software word: the scorer alone would say off lane
        for title in ("Software Systems Engineer", "Distributed Systems Engineer", "Software Design Engineer",
                      "Software Test Engineer", "Platform Systems Engineer"):
            with self.subTest(title=title):
                self.assertEqual(classify_title(title, self.cfg, thin).lane, "engineering")
                self.assertEqual(classify_title(title, self.cfg, PHYSICAL_BODY).lane, "engineering")
                self.assertEqual(classify_title(title, self.cfg, "").lane, "engineering")
        self.assertEqual(classify_title("Systems Engineer", self.cfg, thin).lane, "off_lane")  # control

    def test_scientist_titles_are_software_titles(self):
        for title in ("Data Scientist, Supply Chain", "Research Scientist, Materials", "Applied Scientist, Logistics"):
            with self.subTest(title=title):
                self.assertEqual(classify_title(title, self.cfg).lane, "engineering")

    def test_a_clear_title_ignores_the_body(self):
        self.assertEqual(classify_title("Software Engineer", self.cfg, PHYSICAL_BODY).lane, "engineering")
        self.assertEqual(classify_title("Mechanical Engineer", self.cfg, SOFTWARE_BODY).lane, "off_lane")

    def test_other_off_lane_words_beat_a_software_body(self):
        self.assertEqual(classify_title("Hardware Test Engineer", self.cfg, SOFTWARE_BODY).lane, "off_lane")
        self.assertEqual(classify_title("Hardware Test Engineer", self.cfg).lane, "off_lane")

    def test_existing_ambiguous_titles_in_other_lanes_are_unchanged(self):
        self.assertEqual(classify_title("Systems Engineering Intern", self.cfg, SOFTWARE_BODY).lane, "intern")
        self.assertEqual(classify_title("Systems Engineer, General Application", self.cfg, SOFTWARE_BODY).lane,
                         "general_application")

    def test_word_lists_come_from_config(self):
        for key in ("ambiguous", "software_words", "physical_words"):
            self.assertTrue(self.cfg.title[key])
        self.assertEqual(self.cfg.title["ambiguous_margin"], 2)


HVAC_BODY = ("You will run and maintain the building's HVAC plant, boilers, chillers and steam systems, log "
             "readings, keep pumps and compressors in service and handle night shift rounds." + FILLER)
NIX_BODY = ("You will write Python tooling for our Linux fleet, package services in Docker images and run them on "
            "AWS, and keep the build reproducible with Nix." + FILLER)
FOOTWEAR_BODY = ("You will develop new footwear materials, test rubber and foam compounds in the lab, work with "
                 "the Jordan Brand design team and approve samples from the factory." + FILLER)


class BodyDecidesNonSoftwareTitleTests(unittest.TestCase):
    """#172: only a title that names software work counts outright; any other engineering title follows its body."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()

    def test_bodies_are_long_enough_for_the_tests(self):
        for body in (HVAC_BODY, NIX_BODY, FOOTWEAR_BODY):
            self.assertGreaterEqual(len(body.strip()), self.cfg.title["min_body_chars"])
        self.assertEqual(self.cfg.title["min_body_chars"], 300)

    def test_non_software_title_with_physical_body_is_off_lane(self):
        for title in ("Stationary Engineer", "Fire Protection Engineer"):
            with self.subTest(title=title):
                got = classify_title(title, self.cfg, HVAC_BODY)
                self.assertEqual((got.lane, got.tier), ("off_lane", None))
                self.assertEqual(got.basis, "Title names no software work and the posting is not clearly software work")

    def test_non_software_title_with_software_body_stays(self):
        got = classify_title("Nix Engineer", self.cfg, NIX_BODY)
        self.assertEqual(got.lane, "engineering")

    def test_non_software_title_with_no_body_stays(self):
        for body in (None, "", "   \n "):
            with self.subTest(body=body):
                self.assertEqual(classify_title("Stationary Engineer", self.cfg, body).lane, "engineering")

    def test_thin_body_counts_as_no_body(self):
        thin = "You will run the HVAC plant and boilers."
        self.assertLess(len(thin), 300)
        self.assertEqual(classify_title("Stationary Engineer", self.cfg, thin).lane, "engineering")
        self.assertEqual(classify_title("Systems Engineer", self.cfg, thin).lane, "engineering")
        edge = ("x " * 149 + "y").strip()  # 299 characters
        self.assertEqual(len(edge), 299)
        self.assertEqual(classify_title("Stationary Engineer", self.cfg, edge).lane, "engineering")
        full = edge + "z"  # 300 characters, no software word: the body is read and software does not lead
        self.assertEqual(len(full), 300)
        self.assertEqual(classify_title("Stationary Engineer", self.cfg, full).lane, "off_lane")

    def test_ambiguous_title_keeps_its_basis(self):
        got = classify_title("Systems Engineer", self.cfg, HVAC_BODY)
        self.assertEqual(got.lane, "off_lane")
        self.assertEqual(got.basis, "Title says Systems Engineer and the posting is not clearly software work")

    def test_software_titles_skip_the_body(self):
        for title in ("Founding Engineer", "Forward Deployed Engineer", "Member of Technical Staff",
                      "Research Engineer", "Cloud Engineer", "Security Engineer", "Software Engineer, Logistics",
                      "AI Engineer", "Mobile Engineer", "iOS Engineer", "Web Engineer", "Database Engineer",
                      "MTS", "FDE", "Analytics Engineer", "Software Developer", "Full Stack Developer",
                      "Application Developer", "Platform Engineer", "Python Engineer"):
            with self.subTest(title=title):
                self.assertEqual(classify_title(title, self.cfg, HVAC_BODY).lane, "engineering")
                self.assertEqual(classify_title(title, self.cfg, "").lane, "engineering")

    def test_ai_does_not_match_inside_other_words(self):
        # "ai" is a software title word; it must match whole words only
        self.assertEqual(classify_title("Maintenance Engineer", self.cfg, HVAC_BODY).lane, "off_lane")
        self.assertEqual(classify_title("Aircraft Maintenance Engineer", self.cfg, HVAC_BODY).lane, "off_lane")

    def test_bare_developer_goes_to_the_body(self):
        title = "Materials Developer II, Jordan Footwear"
        self.assertEqual(classify_title(title, self.cfg, FOOTWEAR_BODY).lane, "off_lane")
        self.assertEqual(classify_title(title, self.cfg, "").lane, "off_lane")  # "materials" is off lane unless software
        self.assertEqual(classify_title("Developer", self.cfg, FOOTWEAR_BODY).lane, "off_lane")
        self.assertEqual(classify_title("Developer", self.cfg, NIX_BODY).lane, "engineering")
        self.assertEqual(classify_title("Developer", self.cfg, "").lane, "engineering")
        self.assertEqual(classify_title("Programmer", self.cfg, FOOTWEAR_BODY).lane, "off_lane")

    def test_programmer_analyst_and_apps_dev_titles_count_as_software(self):
        # Citi's software roles: "programmer" alone is not a software title, these phrases are
        for title in ("Apps Dev Intmd Programmer Analyst - C11 - TAMPA", "Apps Dev Intmd Programmer Analyst - Officer",
                      "Applications Development Engineer", "Application Development Engineer"):
            for body in (HVAC_BODY, FOOTWEAR_BODY):
                with self.subTest(title=title):
                    self.assertGreaterEqual(len(body.strip()), 300)
                    self.assertEqual(classify_title(title, self.cfg, body).lane, "engineering")
        self.assertEqual(classify_title("Programmer", self.cfg, FOOTWEAR_BODY).lane, "off_lane")  # control

    def test_bi_developer_is_off_lane(self):
        for title in ("BI Report Developer 1", "Business Intelligence Engineer", "BI Developer", "Power BI Developer",
                      "Tableau Developer", "Reporting Developer", "Software Engineer, Business Intelligence",
                      "Python BI Engineer"):
            with self.subTest(title=title):
                self.assertEqual(classify_title(title, self.cfg, NIX_BODY).lane, "off_lane")
                self.assertEqual(classify_title(title, self.cfg).lane, "off_lane")

    def test_data_center_engineer_is_off_lane(self):
        self.assertEqual(classify_title("Data Center Engineer", self.cfg).lane, "off_lane")
        self.assertEqual(classify_title("Data Center Engineer", self.cfg, NIX_BODY).lane, "off_lane")
        self.assertEqual(classify_title("Software Engineer, Data Center Automation", self.cfg).lane, "engineering")

    def test_config_lists_after_the_change(self):
        for key in ("software_titles", "off_lane", "off_lane_unless_software"):
            self.assertTrue(self.cfg.title[key])
        self.assertNotIn("developer", self.cfg.title["software_titles"])
        self.assertNotIn("programmer", self.cfg.title["software_titles"])
        self.assertNotIn("business intelligence", self.cfg.title["off_lane_unless_software"])
        self.assertIn("data center", self.cfg.title["off_lane_unless_software"])


if __name__ == "__main__":
    unittest.main()
