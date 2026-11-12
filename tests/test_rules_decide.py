"""decide() and passes() (issue #4, Task 14)."""
import ast
import dataclasses
import json
import re
import time
import unittest
from pathlib import Path

from outreach.rules import Decision, PostingInput, decide, load_config, passes, ruleset_version
from tests.storetest import fresh_store

ROOT = Path(__file__).resolve().parent.parent

# #172: a body under title.min_body_chars counts as no body; this neutral text pads a short body past it.
PAD = (" Our team works from a bright office with snacks, a library and a standing desk for everyone. "
       "We care about kind colleagues, clear writing and steady growth, and we celebrate small wins together. "
       "Lunch is shared on Fridays, mentors meet new hires weekly and the whole group plans its year in the spring.")

OLD_FIXTURE = [  # (title, lane, level_ok) of each tests/fixtures/ranking/postings.json row, by index
    ("Research Engineer (New Grad)", "engineering", 1),
    ("Research Scientist (post-training)", "not_engineering", 0),
    ("Research Scientist (diffusion)", "not_engineering", 0),
    ("GPU Performance Engineer", "engineering", 0),
    ("Analytics Engineer", "engineering", 1),
    ("Growth Data Engineer", "engineering", 1),
    ("Sales Account Executive", "not_engineering", 0),
    ("Research Engineer, Climate Modeling", "engineering", 1),
    ("Lead Product Designer, AI for the Planet", "not_engineering", 0),
    ("Lead Security Engineer", "engineering", 0),
    ("Research Engineer, Climate Modeling", "engineering", 1),
    ("Lead Product Designer, AI for the Planet", "not_engineering", 0),
    ("Lead Security Engineer", "engineering", 0),
    ("Research Engineer, Climate Modeling", "engineering", 1),
    ("Postdoctoral Associate-Systems Engineering", "off_lane", 0),
    ("Assistant Director of Graduate Student Life", "not_engineering", 0),
    ("Campus Police Dispatcher", "not_engineering", 0),
    ("Coordinator, Schaefer School of Engineering and Science", "engineering", 0),
    ("Implementation Engineer", "engineering", 1),
    ("Full Stack Engineer", "engineering", 1),
    ("Forward Deployed Engineer", "engineering", 1),
    ("AI Account Executive, Revenue Growth", "not_engineering", 0),
    ("Customer Success Engineer - Remote in the US", "off_lane", 0),
    ("Director of Accounting", "not_engineering", 0),
    ("Space Systems Engineer, Space Operations", "engineering", 1),
    ("Analyst, Sales Strategy & GTM Planning", "not_engineering", 0),
    ("Senior Camera Systems Engineer", "engineering", 0),
    ("Software Engineer - Platform & Application", "engineering", 1),
    ("Developer Relations Manager", "off_lane", 0),
    ("Software Engineer", "engineering", 1),
    ("Enterprise Account Executive", "not_engineering", 0),
    ("Lead Full-Stack Software Engineer", "engineering", 0),
    ("Software Development Engineer in Test (SDET)", "engineering", 1),
    ("Software Engineer - Platform & Application", "engineering", 1),
    ("Software Development Engineer in Test (SDET)", "engineering", 1),
    ("Analog IC Design and Verification Engineer", "off_lane", 0),
    ("Forward Deployed Engineer (Training)", "engineering", 1),
    ("Software Engineer", "engineering", 1),
    ("Software Engineer, Full Stack - Emerging Talent", "engineering", 1),
    ("Data Analyst", "not_engineering", 0),
    ("Scientific Data Engineer", "engineering", 1),
    ("Junior Account Executive", "not_engineering", 0),
    ("Scientific Data Engineer", "engineering", 1),
    ("Junior Account Executive", "not_engineering", 0),
]

OLD_SYNTHETIC = [  # (title, body, level_ok), one case per way level_ok is reached
    ("Software Engineer I", "5+ years of experience building services.", 0), ("Software Engineer I", "", 1),
    ("Software Engineer II", "", 1), ("Software Engineer II", "3 years of experience.", 1),
    ("Software Engineer II", "4+ years of experience.", 0), ("Software Engineer", "2 years of experience.", 1),
    ("Software Engineer", "3+ years of experience in industry.", 1),
    ("Software Engineer", "4+ years of experience in industry.", 0), ("Software Engineer", "0-1 years of experience.", 1),
    ("Software Engineer", "This is an entry-level role.", 1),
    ("Software Engineer", "BS and 5 years of experience, or MS and 0 years of experience.", 0),
    ("Software Engineer, New Grad", "5+ years of experience.", 0), ("Senior Software Engineer", "1 year of experience.", 0),
    ("Member of Technical Staff", "", 1), ("Software Engineer", "", 0), ("Accountant", "entry-level", 0),
    ("Software Engineer Intern", "", 0), ("Junior Software Engineer", "6 years of experience.", 0),
]


def d(title, body="", location="Boston, MA", country=None):
    return decide(PostingInput(title, body, location, country))


class DecideTests(unittest.TestCase):
    def test_years_over_the_limit_keeps_title_tier_but_fails_level(self):
        r = d("Software Engineer II", "5+ years of experience with distributed systems.")
        self.assertEqual(r.level_tier, "swe2")
        self.assertFalse(r.level_ok)
        self.assertEqual(r.years_asked, 5)
        self.assertEqual(r.level_basis, "Asks 5 years")

    def test_entry_wording_makes_early_career(self):
        r = d("Software Engineer", "This is an entry-level role for recent graduates.")
        self.assertEqual(r.level_tier, "early_career")
        self.assertTrue(r.level_ok)
        self.assertEqual(r.level_basis, "Posting says entry level")

    def test_swe_iii_is_swe2_and_title_based(self):
        # #154: III used to be senior (level_ok false); it is swe2 now, and years in the body still fail it
        r = d("Software Engineer III", "")
        self.assertEqual((r.level_tier, r.level_ok, r.level_basis), ("swe2", True, "Title says III"))
        r = d("Software Engineer III", "6+ years of experience building backend systems.")
        self.assertEqual((r.level_tier, r.level_ok, r.level_basis), ("swe2", False, "Asks 6 years"))
        self.assertEqual(d("Software Engineer IV", "").level_tier, "senior")

    def test_ambiguous_title_reads_the_body(self):
        # #172: a body under 300 characters counts as no body, so both bodies are padded past it
        tech = d("Systems Engineer", "Build backend services in Python and Go on AWS with Docker." + PAD)
        self.assertEqual(tech.lane, "engineering")
        physical = d("Systems Engineer", "Model parts in SolidWorks and run FEA in ANSYS." + PAD)
        self.assertEqual(physical.lane, "off_lane")
        self.assertEqual(d("Systems Engineer", "").lane, "engineering")  # no body yet: the crawler fetches it

    def test_plain_title_is_not_stated(self):
        r = d("Software Engineer", "")
        self.assertEqual((r.level_tier, r.level_ok, r.lane), ("not_stated", False, "engineering"))
        self.assertEqual(r.level_basis, "Posting does not state a level")
        self.assertTrue(passes(r))

    def test_eligibility_excluded_fails_passes(self):
        r = d("Software Engineer, New Grad",
              "You must be currently enrolled in a Bachelor's or Master's program in Computer Science.")
        self.assertEqual(r.eligibility, "excluded")
        self.assertTrue(r.eligibility_reason)
        self.assertFalse(passes(r))

    def test_2027_start_still_passes(self):
        r = d("Software Engineer, New Grad (2027)", "")
        self.assertTrue(r.start_2027)
        self.assertTrue(passes(r))

    def test_opt_only_passes(self):
        r = d("Software Engineer, New Grad", "We support OPT and STEM OPT but cannot sponsor H-1B visas.")
        self.assertEqual(r.sponsorship, "opt_only")
        self.assertTrue(passes(r))

    def test_no_sponsorship_passes(self):
        """#154 (#9, 2026-11-01): "no sponsorship" is a flag on the posting, not a hide."""
        r = d("Software Engineer, New Grad", "We are unable to sponsor visas for this role.")
        self.assertEqual(r.sponsorship, "no")
        self.assertTrue(passes(r))

    def test_citizenship_fails_passes(self):
        r = d("Software Engineer, New Grad", "Applicants must be U.S. citizens.")
        self.assertTrue(r.citizenship)
        self.assertTrue(r.citizenship_quote)
        self.assertFalse(passes(r))

    def test_non_us_fails_passes(self):
        self.assertFalse(passes(d("Software Engineer, New Grad", "", "Paris, France")))

    def test_a_non_us_country_in_the_title_fails_passes_when_the_location_is_silent(self):
        r = d("Software Engineer II (Venezuela)", "", "Remote")
        self.assertEqual(r.us, "no")
        self.assertFalse(passes(r))
        self.assertEqual(d("Software Engineer II (Venezuela)", "", "").us, "no")
        # a US place in the location, or a US mark in the title, keeps it in
        self.assertEqual(d("Software Engineer II (Venezuela)", "", "Austin, TX").us, "yes")
        self.assertNotEqual(d("Software Engineer (US)", "", "Remote").us, "no")
        self.assertTrue(passes(d("Software Engineer, Remote (USA or Canada)", "", "Remote")))
        self.assertNotEqual(d("Engineer, Georgia Tech", "", "Remote").us, "no")

    def test_off_lane_fails_passes(self):
        self.assertFalse(passes(d("Solutions Engineer", "")))

    def test_empty_body_still_decides(self):
        r = decide(PostingInput("Software Engineer I", "", ""))
        self.assertEqual(r.sponsorship, "silent")
        self.assertEqual(r.us, "unknown")
        self.assertEqual((r.level_tier, r.level_ok), ("swe1", True))
        self.assertEqual((r.years_asked, r.years_quote), (None, ""))

    def test_non_engineering_lane_fails_level(self):
        r = d("Scientist I/II", "")
        self.assertEqual((r.lane, r.level_ok), ("not_engineering", False))
        self.assertTrue(r.level_basis)

    def test_senior_title_beats_low_years(self):
        r = d("Senior Software Engineer", "1+ years of experience.")
        self.assertEqual((r.level_tier, r.level_ok), ("senior", False))

    def test_low_years_make_early_career(self):
        r = d("Software Engineer", "1 year of professional experience.")
        self.assertEqual((r.level_tier, r.level_ok), ("early_career", True))
        self.assertEqual(r.level_basis, "Posting asks 0 to 1 years")

    def test_years_without_title_tier_are_swe2(self):
        r = d("Software Engineer", "3+ years of experience building backend systems in production.")
        self.assertEqual((r.level_tier, r.level_ok, r.level_basis), ("swe2", True, "Asks 3 years"))

    def test_open_title_without_years(self):
        r = d("Member of Technical Staff", "")
        self.assertEqual((r.level_tier, r.level_ok), ("open", True))

    def test_intern_lane_fails_level_and_passes(self):
        r = d("Software Engineering Intern", "")
        self.assertEqual((r.lane, r.level_ok), ("intern", False))
        self.assertFalse(passes(r))

    def test_decide_is_pure_and_frozen(self):
        p = PostingInput("Software Engineer, New Grad", "We will not sponsor H-1B petitions.")
        self.assertEqual(decide(p), decide(p))
        self.assertEqual(decide(p, load_config()), decide(p))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            p.title = "x"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            decide(p).lane = "intern"

    def test_nul_bytes_are_dropped_before_deciding(self):
        r = decide(PostingInput("Software\x00 Engineer", "\x00We do not sponsor visas.\x00", "Boston,\x00 MA", "United\x00 States"))
        self.assertEqual((r.lane, r.sponsorship, r.us), ("engineering", "no", "yes"))
        self.assertEqual(r.sponsorship_quote, "We do not sponsor visas.")
        self.assertEqual(r, decide(PostingInput("Software Engineer", "We do not sponsor visas.", "Boston, MA", "United States")))

    def test_invisible_characters_cannot_flip_a_refusal(self):
        # A zero-width joiner inside "not" used to read yes; a soft hyphen or zero-width space inside "sponsor" read
        # unclear (#112).
        for body in ("We do n‍ot sponsor visas.", "We do not spon­sor visas.", "We do not spon​sor visas.",
                     "We do not spon\U000E0041sor visas.", "﻿We do not sponsor️ visas.",
                     "We do ‮not‬ sponsor visas."):
            r = d("Software Engineer", body)
            self.assertEqual(r.sponsorship, "no", repr(body))
            self.assertEqual(r.sponsorship_quote, "We do not sponsor visas.", repr(body))

    def test_invisible_characters_cannot_hide_a_citizenship_line(self):
        r = d("Software Engineer", "Applicants must be U.S. citi​zens.")
        self.assertTrue(r.citizenship)
        self.assertEqual(r.citizenship_quote, "Applicants must be U.S. citizens.")
        self.assertEqual(r, d("Software Engineer", "Applicants must be U.S. citizens."))

    def test_a_title_and_location_drop_invisible_characters_too(self):
        r = d("Soft​ware Engi­neer, New Grad", "", "Bos⁠ton, MA")
        self.assertEqual((r.lane, r.level_tier, r.us), ("engineering", "new_grad", "yes"))

    def test_line_and_paragraph_separators_end_a_sentence(self):
        r = d("Software Engineer", "We sponsor H-1B visas. Relocation is not provided.")
        self.assertEqual((r.sponsorship, r.sponsorship_quote), ("yes", "We sponsor H-1B visas."))

    def test_posting_input_documents_its_plain_text_contract(self):
        doc = PostingInput.__doc__
        self.assertIn("body` is plain text", doc)
        self.assertIn("crawler strips HTML", doc)

    def test_ruleset_version_is_exported(self):
        self.assertRegex(ruleset_version(), r"^r-[0-9a-f]{12}$")

    def test_pathological_body_is_fast(self):
        body = ("5 years ago we graduated in 2027 or 2026; sponsorship maybe. é " * 15000)[:1_000_000]
        # CPU time, not wall time: scheduler wait on a loaded machine is not the code's cost.
        start = time.process_time()
        decide(PostingInput("Software Engineer, New Grad", body, "Boston, MA"))
        # Measured about 1.4 s CPU on a quiet machine; 3.0 s leaves room for a slower one. Catastrophic backtracking
        # would take minutes, so the bound still catches it.
        self.assertLess(time.process_time() - start, 3.0)

    def test_as_row_matches_the_decision_table(self):
        db = fresh_store()
        columns = {r["name"] for r in db.execute("PRAGMA table_info(decision)")}
        row = d("Software Engineer", "3+ years of experience.").as_row()
        # provisional and reader_version are the reader's (#32 writes them through its combiner), not decide()'s.
        self.assertEqual(set(row), columns - {"posting_id", "ruleset_version", "decided_at", "provisional",
                                              "reader_version"})
        self.assertTrue(all(not isinstance(v, bool) for v in row.values()))
        self.assertIn(row["level_ok"], (0, 1))

    def test_as_row_carries_level_state_and_years_ms(self):
        for title, body, state in (("Software Engineer, New Grad", "", "fit"),
                                   ("Senior Software Engineer", "", "reject"),
                                   ("Software Engineer", "", "uncertain"),
                                   ("Accountant", "", "reject")):
            row = d(title, body).as_row()
            self.assertEqual(row["level_state"], state, title)
            self.assertIsNone(row["years_ms"], title)

    def test_the_fallback_for_an_unrebuilt_row_is_the_job_list_rule(self):
        """One fallback: the Python rule and the job list's STATE_SQL agree on every row, and a stored state wins."""
        from outreach.pipeline.joblist import STATE_SQL
        from outreach.rules.decide import interim_level_state, row_level_state
        conn = fresh_store()
        self.addCleanup(conn.close)
        for ok in (0, 1):
            for tier in (None, "not_stated", "open", "new_grad", "swe2", "senior"):
                sql = conn.execute(f"SELECT {STATE_SQL} FROM (SELECT NULL AS level_state, ? AS level_ok, "
                                   f"? AS level_tier) d", (ok, tier)).fetchone()[0]
                with self.subTest(ok=ok, tier=tier):
                    self.assertEqual(interim_level_state(bool(ok), tier), sql)
                    self.assertEqual(row_level_state({"level_state": None, "level_ok": ok, "level_tier": tier}), sql)
        self.assertEqual(row_level_state({"level_state": "reject", "level_ok": 1, "level_tier": "swe1"}), "reject")

    def test_level_state_has_no_default(self):
        field = next(f for f in dataclasses.fields(Decision) if f.name == "level_state")
        self.assertIs(field.default, dataclasses.MISSING)
        self.assertIs(field.default_factory, dataclasses.MISSING)

    def test_decision_carries_the_masters_figure(self):
        r = d("Software Engineer", "BS and 2+ years of experience, or MS and 0+ years.")
        self.assertEqual((r.years_asked, r.years_ms), (2, 0))
        self.assertEqual(d("Software Engineer", "3+ years of experience.").years_ms, None)
        self.assertEqual(d("Software Engineer", "BS and 2+ years of experience, or MS and 0+ years.").as_row()["years_ms"], 0)

    def test_as_row_satisfies_the_table_checks(self):
        db = fresh_store()
        version = ruleset_version()
        # install registered this version
        company = db.execute("INSERT INTO company(name, status_at, created_at) VALUES "
                             "('Acme', '2026-10-20T00:00:00Z', '2026-10-20T00:00:00Z')").lastrowid
        cases = [
            ("Software Engineer, New Grad", "", "Boston, MA"),
            ("Software Engineer II", "5+ years of experience with distributed systems.", "Boston, MA"),
            ("Software Engineer, New Grad", "We will not sponsor H-1B petitions.", "Paris, France"),
            ("Software Engineer, New Grad", "Applicants must be U.S. citizens.", "Boston, MA"),
            ("Software Engineer, New Grad", "You must be currently enrolled in a Bachelor's program.", "Boston, MA"),
            ("Scientist I/II", "", ""),
            ("Senior Software Engineer", "", ""),
            ("Software Engineer", "", ""),
        ]
        for i, (title, body, loc) in enumerate(cases):
            posting = db.execute(
                "INSERT INTO posting(company_id, source_key, title, location, first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, '2026-10-20T00:00:00Z', '2026-10-20T00:00:00Z')",
                (company, f"url:{i}", title, loc)).lastrowid
            row = d(title, body, loc).as_row()
            row.update(posting_id=posting, ruleset_version=version, decided_at="2026-10-20T00:00:00Z")
            names = list(row)
            db.execute(f"INSERT INTO decision({','.join(names)}) VALUES ({','.join('?' * len(names))})",
                       [row[n] for n in names])

    def test_decision_fields(self):
        names = [f.name for f in dataclasses.fields(Decision)]
        self.assertEqual(names, ["lane", "level_tier", "level_ok", "level_basis", "years_asked", "years_quote", "us",
                                 "sponsorship", "sponsorship_quote", "citizenship", "citizenship_quote",
                                 "eligibility", "eligibility_reason", "eligibility_quote", "start_2027",
                                 "level_state", "years_ms"])

    def test_only_rules_classify(self):
        # Done-when line 2: nothing outside outreach/rules reads titles, locations or sponsorship wording.
        banned = ("grad", "senior", "staff", "sponsor", "visa", "citizen", "clearance", "years", "entry", "intern",
                  "remote")
        states = re.compile(r"\b(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE"
                            r"|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b.*"
                            r"\b(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE"
                            r"|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b")
        funcs = {"compile", "search", "match", "fullmatch", "findall", "finditer", "sub"}
        # allowed exceptions: none today. A path (relative to the repo root) maps to the reason it may differ.
        allowed = {
            # Pay is read only for a worksite in a listed metro, never for a remote role. This is a guard on the wage
            # lookup (the ruling of 2026-10-22), not a classification of the posting: the posting's own location
            # still goes through outreach/rules.
            "outreach/ranking/pay.py": "wage lookup refuses a remote or anywhere location",
            # The visa block reads his own email draft, never a posting: no email mentions visa, OPT or sponsorship
            # to anyone (#9, 2026-11-09; #191). Postings are still classified only in outreach/rules.
            "outreach/drafts/checks.py": "blocks visa words in his own email draft, not a posting",
        }
        found = []
        for path in sorted((ROOT / "outreach").rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith("outreach/rules/") or rel in allowed:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in funcs and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "re" and node.args):
                    continue
                arg = node.args[0]
                if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                    continue
                low = arg.value.lower()
                if any(w in low for w in banned) or states.search(arg.value):
                    found.append(f"{rel}:{node.lineno}: {arg.value!r}")
        self.assertEqual(found, [], "classification wording outside outreach/rules")


class LevelStateTests(unittest.TestCase):
    """#154: fit, reject or uncertain for each posting. His years: the master's figure first, else the general one."""

    def state(self, title, body=""):
        return d(title, body).level_state

    def test_swe_iii_two_years_or_one_with_ms_is_fit(self):
        google = "2 years of experience in software development, or 1 year of experience with an advanced degree."
        r = d("Software Engineer III", google)
        self.assertEqual((r.years_asked, r.years_ms), (2, 1))
        self.assertEqual((r.level_tier, r.level_ok, r.level_state), ("swe2", True, "fit"))
        self.assertEqual(r.level_basis, "Title says III")
        # the master's figure decides: 6 years for a bachelor's, 2 for a master's is within his band
        r = d("Software Engineer III", "BS and 6 years of experience, or MS and 2 years of experience.")
        self.assertEqual((r.level_ok, r.level_state), (True, "fit"))
        # a general figure alone over the limit still fails
        r = d("Software Engineer III", "6+ years of experience building backend systems.")
        self.assertEqual((r.level_ok, r.level_state), (False, "reject"))
        # exactly 3 is above his level (#212, #9: 2 years or fewer); none: his level is not clear
        self.assertEqual(self.state("Software Engineer III", "3 years of experience in software development."),
                         "reject")
        self.assertEqual(self.state("Software Engineer III"), "uncertain")

    def test_plain_title_no_years_is_uncertain(self):
        self.assertEqual(self.state("Software Engineer"), "uncertain")
        self.assertEqual(self.state("Software Engineer", "Build backend services with a small team."), "uncertain")
        self.assertEqual(self.state("Member of Technical Staff"), "uncertain")  # open tier, no years
        self.assertEqual(self.state("Forward Deployed Engineer"), "uncertain")
        self.assertEqual(self.state("Software Engineer II"), "uncertain")  # swe2 with no years
        # #212 (2026-11-12): exactly 3 years is above his level (#9: 2 years or fewer), no longer "level not stated"
        self.assertEqual(self.state("Software Engineer", "3+ years of experience in industry."), "reject")
        self.assertEqual(self.state("Research Scientist", "Train and evaluate large models."), "uncertain")

    def test_fit_by_tier_years_or_entry_wording(self):
        for title, body in (("Software Engineer, New Grad", ""), ("Early Career Software Engineer", ""),
                            ("Junior Software Engineer", ""), ("Software Engineer I", ""),
                            ("Software Engineer", "2 years of experience."),
                            ("Software Engineer", "0-1 years of experience."),
                            ("Software Engineer", "This is an entry-level role for recent graduates."),
                            ("Software Engineer", "BS and 5 years of experience, or MS and 0 years of experience."),
                            ("Software Engineer II", "2+ years of experience.")):
            self.assertEqual(self.state(title, body), "fit", (title, body))

    def test_research_scientist_phd_required_is_reject(self):
        for body in ("PhD required.", "You must have a PhD in machine learning.",
                     "A PhD in computer science or a related field is required.",
                     "Ph.D. in Machine Learning required.", "Candidates must hold a doctorate in a related field."):
            self.assertEqual(self.state("Research Scientist", body), "reject", body)
            self.assertEqual(self.state("Applied Scientist", body), "reject", body)
        # a preference, or an alternative he meets, is not a requirement
        for body in ("A PhD is preferred.", "MS or PhD in computer science.", "PhD or equivalent experience.",
                     "A PhD is a plus.", "We require a BS, MS or PhD in a quantitative field.",
                     "Bachelor's degree required; PhD a bonus."):
            self.assertEqual(self.state("Research Scientist", body), "uncertain", body)
        # only a scientist or researcher title: an engineer is not turned away for a PhD line
        self.assertEqual(self.state("Software Engineer", "PhD required."), "uncertain")
        self.assertEqual(self.state("Research Engineer", "PhD required."), "uncertain")
        # senior tier and years still decide first
        self.assertEqual(self.state("Senior Research Scientist", "PhD preferred."), "reject")

    def test_more_than_three_years_is_reject(self):
        self.assertEqual(self.state("Software Engineer", "4+ years of experience in industry."), "reject")
        self.assertEqual(self.state("Software Engineer", "5 years of professional experience."), "reject")
        self.assertEqual(self.state("Software Engineer, New Grad", "5+ years of experience."), "reject")
        self.assertEqual(self.state("Software Engineer I", "5+ years of experience building services."), "reject")
        self.assertEqual(self.state("Senior Software Engineer", "1 year of experience."), "reject")  # senior tier
        self.assertEqual(self.state("Staff Engineer", ""), "reject")
        # the master's figure first: over the limit for a bachelor's, within it for a master's
        both = "BS and 5 years of experience, or MS and 4 years of experience."
        self.assertEqual(self.state("Software Engineer", both), "reject")

    def test_masters_figure_can_fit_where_level_ok_stays_as_before(self):
        r = d("Software Engineer", "BS and 5 years of experience, or MS and 0 years of experience.")
        self.assertEqual((r.years_asked, r.years_ms, r.level_state), (5, 0, "fit"))
        self.assertFalse(r.level_ok)  # level_ok keeps reading the general figure

    def test_non_tech_is_reject(self):
        for title, body in (("Accountant", "entry-level"), ("Software Engineer Intern", ""),
                            ("Solutions Engineer", ""), ("Data Analyst", "0-1 years of experience."),
                            ("Account Executive, New Grad", ""), ("Staff Scientist, Biology", ""),
                            ("Systems Engineer", "Model parts in SolidWorks and run FEA in ANSYS." + PAD)):
            self.assertEqual(self.state(title, body), "reject", title)

    def test_every_state_is_one_of_the_three(self):
        for title, body in (("Software Engineer", ""), ("Accountant", ""), ("Software Engineer, New Grad", ""),
                            ("Software Engineer III", "9 years of experience."), ("", "")):
            self.assertIn(self.state(title, body), ("fit", "reject", "uncertain"))


class LevelOkUnchangedTests(unittest.TestCase):
    """#154 changes level_state, not level_ok: the merge base's (092a34c) level_ok is kept for every tier except III."""

    def test_level_ok_unchanged_for_existing_tiers(self):
        postings = json.loads((ROOT / "tests" / "fixtures" / "ranking" / "postings.json").read_text(encoding="utf-8"))
        self.assertEqual([p["title"] for p in postings], [t for t, _, _ in OLD_FIXTURE])
        excluded = []
        for i, (p, (title, old_lane, old_ok)) in enumerate(zip(postings, OLD_FIXTURE)):
            self.assertNotRegex(title, r"\b(?:III|3)\b")  # the III case changes on purpose
            new = d(title, p.get("body", ""), p.get("location", ""), p.get("country"))
            if new.lane != old_lane:  # the lane moved (scientist titles, systems titles, #172 body rule): not a level change
                excluded.append(i)
                continue
            self.assertEqual(int(new.level_ok), old_ok, (i, title))
        self.assertEqual(excluded, [1, 2, 17, 24, 26])  # 17: #172, an admin body under a non-software title
        for title, body, old_ok in OLD_SYNTHETIC:
            self.assertEqual(int(d(title, body).level_ok), old_ok, (title, body))


if __name__ == "__main__":
    unittest.main()


class ListNoise212Tests(unittest.TestCase):
    """#212 (2026-11-12): rows the job list audit and his passes found at his level that #9 says should not be there,
    and the rows #9 keeps."""

    def test_named_foreign_places_read_as_outside_the_us(self):
        for location in ("Home-based, Mauritius", "Cape Town", "Bräuhausgasse 37, 1050 Wien", "European Union",
                         "Bucharest, RO", "Malaga", "Vienna, Austria"):
            with self.subTest(location=location):
                self.assertEqual(d("Software Engineer", location=location).us, "no")

    def test_chip_fab_and_it_service_titles_leave_the_tech_lane(self):
        for title in ("Ocotillo Technology Fabrication Yield Device Engineer", "Experienced Module Integration Engineer",
                      "R&D Engineer, IC Design", "End User Services Engineer II", "Design Verification Engineer",
                      "Analyst I, Payments Engineering Operations", "Mobile Building Engineer",
                      "Physical & Facility Security Engineer", "Security Certification Engineer - Android"):
            with self.subTest(title=title):
                self.assertNotEqual(d(title).lane, "engineering")

    def test_a_title_that_names_software_work_stays(self):
        for title in ("Software Engineer, Yield Analytics", "Software Engineer - Design Verification Tools",
                      "Software Engineer, Operations Research",
                      "Developer - Atlassian Process Engineering", "Application Security Engineer",
                      "Data Scientist"):
            with self.subTest(title=title):
                self.assertEqual(d(title).lane, "engineering")

    def test_lead_titles_are_above_his_level(self):
        for title in ("Engineering Leader", "Team Leader, Engineering", "Software Engineering Technical Leader"):
            with self.subTest(title=title):
                self.assertEqual(d(title).level_tier, "senior")

    def test_a_leadership_program_is_an_entry_role(self):
        for title in ("TRAIL Rising Leaders Program - Software Engineering", "Future Leaders Program, Software Engineer"):
            with self.subTest(title=title):
                self.assertNotEqual(d(title).level_tier, "senior")

    def test_three_years_is_above_his_level(self):
        # #9 level ruling: his level is a role asking 2 years or fewer.
        two = d("Software Engineer", "We want 2+ years of professional experience building APIs.")
        three = d("Software Engineer", "We want 3+ years of professional experience building APIs.")
        self.assertEqual((two.level_state, three.level_state), ("fit", "reject"))
