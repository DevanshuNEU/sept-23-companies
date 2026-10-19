"""Rules config, text helpers and the ruleset version (issue #4, Task 7)."""
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from outreach.rules import config as config_mod
from outreach.rules.config import CONFIG_PATH, load_config, phrases
from outreach.rules.text import quote_around, sentences
from outreach.rules.version import NOT_RULES, RULE_FILES, RULES_DIR, ruleset_version

CAPITAL_ONE = (
    "At this time, Capital One will not sponsor a new applicant for employment authorization, or offer any "
    "immigration related support for this position (i.e. H1B, F-1 OPT, F-1 STEM OPT, F-1 CPT, J-1, TN, E-2, E-3, "
    "L-1 and O-1, or any EADs or other forms of work authorization that require immigration support from an "
    "employer)."
)


def _copy_rules_dir():
    tmp = Path(tempfile.mkdtemp(prefix="rules-copy-"))
    self_dir = tmp / "rules"
    shutil.copytree(RULES_DIR, self_dir, ignore=shutil.ignore_patterns("__pycache__"))
    return self_dir


class ConfigTests(unittest.TestCase):
    def test_config_loads_and_pins_the_decided_numbers(self):
        cfg = load_config()
        self.assertEqual(cfg.candidate["graduated"], "2026-05")
        self.assertEqual(cfg.candidate["max_years_asked"], 3)
        self.assertEqual(cfg.raw, CONFIG_PATH.read_bytes())

    def test_missing_key_is_named(self):
        text = CONFIG_PATH.read_text(encoding="utf-8")
        broken = re.sub(r'(?m)^graduated\s*=.*\n', "", text)
        self.assertNotEqual(broken, text)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text(broken, encoding="utf-8")
            with self.assertRaisesRegex(KeyError, "graduated"):
                load_config(p)

    def test_missing_section_is_named(self):
        text = CONFIG_PATH.read_text(encoding="utf-8")
        broken = text.replace("[sponsorship]", "[sponsorship_renamed]")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text(broken, encoding="utf-8")
            with self.assertRaisesRegex(KeyError, "sponsorship"):
                load_config(p)

    def test_every_decided_key_exists_and_every_word_list_compiles(self):
        cfg = load_config()
        for section, keys in config_mod.REQUIRED.items():
            table = getattr(cfg, section)
            for key in keys:
                self.assertIn(key, table, f"{section}.{key}")
                value = table[key]
                if section == "candidate":
                    continue
                lists = value.values() if isinstance(value, dict) else [value]
                for words in lists:
                    self.assertIsInstance(words, list, f"{section}.{key}")
                    self.assertTrue(words, f"{section}.{key} is empty")
                    phrases(words)  # raises on an empty or non-string entry

    def test_old_vocabulary_decisions(self):
        cfg = load_config()
        senior = phrases(cfg.title["senior"])
        strong = phrases(cfg.title["strong_entry"])
        open_level = phrases(cfg.title["open_level"])
        self.assertIsNone(senior.search("AI Fellow"))
        self.assertTrue(senior.search("Distinguished Engineer"))
        self.assertTrue(senior.search("Staff Engineer I"))
        self.assertTrue(strong.search("Fellowship"))
        self.assertTrue(strong.search("Forward Deployed Engineer Fellow"))
        self.assertTrue(open_level.search("Member of Technical Staff"))
        self.assertTrue(open_level.search("Forward-Deployed Engineer"))
        self.assertIsNone(open_level.search("Founding Engineer"))
        self.assertEqual(len(cfg.location["us_state_codes"]), 51)
        self.assertEqual(len(cfg.location["us_state_names"]), 51)

    def test_config_file_has_no_dashes(self):
        text = CONFIG_PATH.read_text(encoding="utf-8")
        self.assertNotIn("\u2014", text)
        self.assertNotIn("\u2013", text)


class PhraseTests(unittest.TestCase):
    def test_phrase_space_matches_hyphen_and_nothing(self):
        p = phrases(["early career"])
        self.assertTrue(p.search("Early-Career Engineer"))
        self.assertTrue(p.search("earlycareer"))
        self.assertTrue(p.search("early career"))
        self.assertIsNone(p.search("yearly careers"))

    def test_case_sensitive_phrases(self):
        p = phrases(["IN", "OH"], case=True)
        self.assertTrue(p.search("Indianapolis, IN"))
        self.assertIsNone(p.search("Remote in Ohio"))

    def test_default_is_case_insensitive(self):
        self.assertTrue(phrases(["new grad"]).search("NEW GRAD"))

    def test_whole_words_only(self):
        p = phrases(["intern"])
        self.assertTrue(p.search("Software Intern"))
        self.assertIsNone(p.search("Internal Tools Engineer"))
        self.assertIsNone(phrases(["grad"]).search("graduate"))

    def test_words_are_escaped_and_may_end_in_punctuation(self):
        self.assertTrue(phrases(["u.s."]).search("Must be a U.S. citizen"))
        self.assertIsNone(phrases(["u.s."]).search("uxsx"))
        self.assertTrue(phrases(["est."]).search("est. 50 people"))
        self.assertTrue(phrases(["1+ years"]).search("1+ years of Go"))
        self.assertTrue(phrases(["ts/sci"]).search("TS/SCI clearance"))

    def test_longest_alternative_wins(self):
        m = phrases(["series", "series a era"]).search("a Series A era startup")
        self.assertEqual(m.group(0), "Series A era")

    def test_empty_list_matches_nothing(self):
        self.assertIsNone(phrases([]).search("anything"))

    def test_bad_input_is_refused(self):
        with self.assertRaises(TypeError):
            phrases("early career")
        with self.assertRaises(ValueError):
            phrases([""])
        with self.assertRaises(ValueError):
            phrases(["   "])

    def test_long_runs_of_spaces_stay_fast(self):
        p = phrases(["early career"])
        self.assertIsNone(p.search("early" + " " * 200_000 + "x"))


class SentenceTests(unittest.TestCase):
    def test_sentences_keep_us_whole(self):
        self.assertEqual(sentences("Must be a U.S. citizen. We ship."), ["Must be a U.S. citizen.", "We ship."])

    def test_sentences_keep_abbreviations_and_decimals(self):
        self.assertEqual(sentences(CAPITAL_ONE), [CAPITAL_ONE])
        self.assertEqual(
            sentences("B.S. or M.S. in Computer Science. We ship."),
            ["B.S. or M.S. in Computer Science.", "We ship."],
        )
        self.assertEqual(sentences("Pay is $120.5k. Benefits."), ["Pay is $120.5k.", "Benefits."])
        self.assertEqual(sentences("Needs a 3.5 GPA. Apply."), ["Needs a 3.5 GPA.", "Apply."])
        self.assertEqual(sentences("A Ph.D. is a plus, etc. We ship."), ["A Ph.D. is a plus, etc.", "We ship."])
        self.assertEqual(sentences("Join Acme Inc. today. Go."), ["Join Acme Inc. today.", "Go."])
        self.assertEqual(sentences("See e.g. Python or Go."), ["See e.g. Python or Go."])

    def test_splits_on_every_stop_and_on_lines_and_bullets(self):
        self.assertEqual(sentences("One! Two? Three; four."), ["One!", "Two?", "Three;", "four."])
        self.assertEqual(sentences("Line one\nLine two"), ["Line one", "Line two"])
        self.assertEqual(sentences("Skills:\n- Python\n* Go\n• Rust"), ["Skills:", "Python", "Go", "Rust"])
        self.assertEqual(sentences("Python • Go • Rust"), ["Python", "Go", "Rust"])

    def test_dots_inside_words_do_not_split(self):
        self.assertEqual(sentences("We use node.js and example.com daily. Ok."), ["We use node.js and example.com daily.", "Ok."])

    def test_closing_quotes_stay_with_the_sentence(self):
        self.assertEqual(sentences('He said "no." Then left.'), ['He said "no."', "Then left."])

    def test_empty_and_blank(self):
        self.assertEqual(sentences(""), [])
        self.assertEqual(sentences("  \n \n"), [])

    def test_pathological_input_is_linear(self):
        text = ("years graduat. " * 70_000) + "é" * 1000
        self.assertGreater(len(sentences(text)), 1000)


class QuoteAroundTests(unittest.TestCase):
    def test_returns_the_sentence_holding_the_match(self):
        text = "We build things. You need 3+ years of Go. Apply today."
        i = text.index("3+")
        self.assertEqual(quote_around(text, i, i + 2), "You need 3+ years of Go.")

    def test_does_not_cut_at_us(self):
        text = "Hello. Must be a U.S. citizen. Bye."
        i = text.index("citizen")
        self.assertEqual(quote_around(text, i, i + 7), "Must be a U.S. citizen.")

    def test_match_across_a_boundary_takes_both_sentences(self):
        text = "Alpha one. Beta two. Gamma three."
        self.assertEqual(quote_around(text, text.index("one"), text.index("Beta") + 4), "Alpha one. Beta two.")

    def test_long_sentences_are_trimmed_to_whole_words_around_the_match(self):
        text = " ".join(f"word{n}" for n in range(200)) + " VISA " + " ".join(f"tail{n}" for n in range(200)) + "."
        i = text.index("VISA")
        q = quote_around(text, i, i + 4, limit=100)
        self.assertLessEqual(len(q), 100)
        self.assertIn("VISA", q)
        self.assertTrue(q.startswith("...") and q.endswith("..."))
        for w in q.strip(".").split():
            self.assertRegex(w, r"^(word\d+|tail\d+|VISA)$")

    def test_no_stop_at_all(self):
        text = "no stop here at all but a match"
        i = text.index("match")
        self.assertEqual(quote_around(text, i, i + 5), text)

    def test_empty_text(self):
        self.assertEqual(quote_around("", 0, 0), "")


class VersionTests(unittest.TestCase):
    def test_version_shape_and_stability(self):
        v = ruleset_version()
        self.assertRegex(v, r"^r-[0-9a-f]{12}$")
        self.assertEqual(v, ruleset_version())

    def test_version_changes_with_config(self):
        a = _copy_rules_dir()
        b = _copy_rules_dir()
        with open(b / "config.toml", "a", encoding="utf-8") as f:
            f.write("# x\n")
        self.assertNotEqual(ruleset_version(a), ruleset_version(b))

    def test_version_changes_with_a_rule_file(self):
        a = _copy_rules_dir()
        b = _copy_rules_dir()
        with open(b / "title.py", "a", encoding="utf-8") as f:  # created when absent
            f.write("# x\n")
        self.assertNotEqual(ruleset_version(a), ruleset_version(b))

    def test_version_changes_with_config_py_and_text_py(self):
        base = _copy_rules_dir()
        for name in ("config.py", "text.py"):
            other = _copy_rules_dir()
            with open(other / name, "a", encoding="utf-8") as f:
                f.write("# x\n")
            self.assertNotEqual(ruleset_version(base), ruleset_version(other), name)

    def test_files_outside_the_rule_list_do_not_change_it(self):
        a = _copy_rules_dir()
        b = _copy_rules_dir()
        for name in NOT_RULES:
            with open(b / name, "a", encoding="utf-8") as f:
                f.write("# x\n")
        self.assertEqual(ruleset_version(a), ruleset_version(b))

    def test_a_missing_rule_file_hashes_as_empty(self):
        a = _copy_rules_dir()
        b = _copy_rules_dir()
        (b / "title.py").write_bytes(b"")
        (a / "title.py").unlink(missing_ok=True)
        self.assertEqual(ruleset_version(a), ruleset_version(b))

    def test_every_rules_file_is_classified(self):
        found = {p.name for p in RULES_DIR.iterdir() if p.suffix in (".py", ".toml")}
        self.assertFalse(set(RULE_FILES) & set(NOT_RULES))
        self.assertEqual(found - set(RULE_FILES) - set(NOT_RULES), set())

    def test_rule_file_list_is_the_decided_one(self):
        self.assertEqual(
            RULE_FILES,
            ("config.toml", "config.py", "text.py", "title.py", "years.py", "location.py", "sponsorship.py",
             "eligibility.py", "facts.py", "decide.py"),
        )
        self.assertEqual(NOT_RULES, ("__init__.py", "version.py", "rebuild.py"))


if __name__ == "__main__":
    unittest.main()
