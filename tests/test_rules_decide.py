"""decide() and passes() (issue #4, Task 14)."""
import ast
import dataclasses
import re
import time
import unittest
from pathlib import Path

from outreach.rules import Decision, PostingInput, decide, load_config, passes, ruleset_version
from tests.storetest import fresh_store

ROOT = Path(__file__).resolve().parent.parent


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

    def test_citizenship_fails_passes(self):
        r = d("Software Engineer, New Grad", "Applicants must be U.S. citizens.")
        self.assertTrue(r.citizenship)
        self.assertTrue(r.citizenship_quote)
        self.assertFalse(passes(r))

    def test_non_us_fails_passes(self):
        self.assertFalse(passes(d("Software Engineer, New Grad", "", "Paris, France")))

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
        self.assertEqual(set(row), columns - {"posting_id", "ruleset_version", "decided_at"})
        self.assertTrue(all(not isinstance(v, bool) for v in row.values()))
        self.assertIn(row["level_ok"], (0, 1))

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
                "INSERT INTO posting(company_id, source_key, title, location, body, first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, '2026-10-20T00:00:00Z', '2026-10-20T00:00:00Z')",
                (company, f"url:{i}", title, loc, body)).lastrowid
            row = d(title, body, loc).as_row()
            row.update(posting_id=posting, ruleset_version=version, decided_at="2026-10-20T00:00:00Z")
            names = list(row)
            db.execute(f"INSERT INTO decision({','.join(names)}) VALUES ({','.join('?' * len(names))})",
                       [row[n] for n in names])

    def test_decision_fields(self):
        names = [f.name for f in dataclasses.fields(Decision)]
        self.assertEqual(names, ["lane", "level_tier", "level_ok", "level_basis", "years_asked", "years_quote", "us",
                                 "sponsorship", "sponsorship_quote", "citizenship", "citizenship_quote",
                                 "eligibility", "eligibility_reason", "eligibility_quote", "start_2027"])

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
        allowed = {}
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


if __name__ == "__main__":
    unittest.main()
