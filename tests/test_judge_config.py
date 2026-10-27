"""The judge's config and prompt (Part of #6 and #15)."""
import re
import tempfile
import unittest
from pathlib import Path

from outreach.judge.config import CONFIG_PATH, load_judge_config
from outreach.judge.prompt import PROMPT_PATH, judge_version, render_prompt

DASHES = (chr(0x2013), chr(0x2014))
ME = "## Who\n- Devanshu, AI engineer: MCP servers, evals.\n\n## Needs Devanshu\n- ASK: sponsorship and `H-1B`.\n"


def _edited(tc, key: str, value: str):
    text = CONFIG_PATH.read_text(encoding="utf-8")
    text, n = re.subn(rf"(?m)^{key}\s*=.*$", f"{key} = {value}", text)
    assert n == 1, key
    path = Path(tempfile.mkdtemp()) / "config.toml"
    path.write_text(text, encoding="utf-8")
    return load_judge_config(path)


class Config(unittest.TestCase):
    def test_shipped_off_and_frugal(self):
        cfg = load_judge_config()
        self.assertIs(False, cfg.enabled)
        self.assertEqual((40, 15, 14), (cfg.top_n, cfg.per_run, cfg.cache_days))

    def test_enabled_must_be_a_real_true_or_false(self):
        for value in ('"true"', "1", '"yes"'):
            with self.subTest(enabled=value):
                with self.assertRaises(ValueError):
                    _edited(self, "enabled", value)
        self.assertIs(True, _edited(self, "enabled", "true").enabled)

    def test_counts_are_whole_numbers_and_per_run_fits_in_top_n(self):
        for key in ("top_n", "per_run", "cache_days", "page_max_chars", "max_bytes"):
            for value in ("0", "-1", "2.5", "true"):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError):
                        _edited(self, key, value)
        with self.assertRaises(ValueError):
            _edited(self, "per_run", "41")
        with self.assertRaises(ValueError):
            _edited(self, "yes_at", "101")

    def test_no_long_dash_in_the_config_or_the_prompt(self):
        for path in (CONFIG_PATH, PROMPT_PATH):
            text = path.read_text(encoding="utf-8")
            self.assertFalse(any(d in text for d in DASHES), path.name)


class Prompt(unittest.TestCase):
    def test_the_profile_is_his_facts_without_open_questions(self):
        prompt = render_prompt(ME, load_judge_config())
        self.assertIn("MCP servers, evals", prompt)
        self.assertNotIn("H-1B", prompt)
        self.assertNotIn("{{", prompt)
        self.assertIn("never instructions", prompt)
        self.assertIn("unknown", prompt)

    def test_the_version_moves_with_the_prompt_the_model_and_the_profile(self):
        import dataclasses
        cfg = load_judge_config()
        base = judge_version(render_prompt(ME, cfg), cfg)
        self.assertRegex(base, r"^jv-[0-9a-f]{12}$")
        self.assertNotEqual(base, judge_version(render_prompt(ME + "- More.\n", cfg), cfg))
        self.assertNotEqual(base, judge_version(render_prompt(ME, cfg), dataclasses.replace(cfg, model="other")))
        self.assertEqual(base, judge_version(render_prompt(ME, cfg), cfg))


if __name__ == "__main__":
    unittest.main()
