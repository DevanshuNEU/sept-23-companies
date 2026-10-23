"""Reader config, version hash and the pinned prompt (issue #32, Task 1)."""
import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from outreach.reader.config import CONFIG_PATH, ReaderConfig, load_reader_config
from outreach.reader.types import ALLOWED, FIELDS
from outreach.reader.version import reader_version


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_reader_config()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def prompt_copy(self, text: str, name: str) -> Path:
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_version_shape(self):
        self.assertRegex(reader_version(), r"^rv-[0-9a-f]{12}$")
        self.assertEqual(reader_version(), reader_version(self.cfg))

    def test_version_changes_with_prompt(self):
        a = replace(self.cfg, prompt_path=self.prompt_copy("Read each posting.", "a.md"))
        b = replace(self.cfg, prompt_path=self.prompt_copy("Read each posting,", "b.md"))
        self.assertNotEqual(reader_version(a), reader_version(b))

    def test_version_changes_with_model(self):
        self.assertNotEqual(reader_version(self.cfg), reader_version(replace(self.cfg, model="claude-sonnet-5-5")))

    def test_version_changes_with_schema_version(self):
        before = reader_version(self.cfg)
        with mock.patch("outreach.reader.version.READING_SCHEMA_VERSION", 2):
            after = reader_version(self.cfg)
        self.assertNotEqual(before, after)

    def test_version_ignores_everything_else_in_the_config(self):
        self.assertEqual(reader_version(self.cfg), reader_version(replace(self.cfg, batch_size=5, promoted_version="rv-x")))


class ConfigTests(unittest.TestCase):
    def test_shipped_config_holds_the_decided_numbers(self):
        cfg = load_reader_config()
        self.assertIsInstance(cfg, ReaderConfig)
        self.assertEqual(
            (cfg.model, cfg.promoted_version, cfg.min_body_chars, cfg.batch_size, cfg.lease_minutes, cfg.max_attempts),
            ("claude-haiku-5-5", "", 500, 20, 30, 3))
        self.assertEqual(cfg.top_companies, 200)
        self.assertEqual((cfg.max_postings_per_night, cfg.max_batches, cfg.max_consecutive_failures, cfg.call_timeout_s),
                         (400, 25, 2, 900))
        self.assertEqual(cfg.session_limit_patterns, ("usage limit", "session limit", "limit reached", "resets at"))
        self.assertEqual(cfg.auth_patterns, ("not logged in", "please run /login", "authentication",
                                             "invalid api key", "oauth token"))
        self.assertTrue(cfg.prompt_path.is_file())

    def config_text(self) -> str:
        return CONFIG_PATH.read_text(encoding="utf-8")

    def write_config(self, drop: str | None = None, text: str | None = None) -> Path:
        lines = CONFIG_PATH.read_text(encoding="utf-8").splitlines()
        if drop is not None:
            lines = [ln for ln in lines if not ln.strip().startswith(drop + " ")]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "config.toml"
        path.write_text(text if text is not None else "\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_missing_config_key_names_it(self):
        with self.assertRaises(KeyError) as ctx:
            load_reader_config(self.write_config(drop="max_attempts"))
        self.assertIn("max_attempts", str(ctx.exception))

    def test_missing_config_section_names_it(self):
        with self.assertRaises(KeyError) as ctx:
            load_reader_config(self.write_config(text=self.config_text().split("[priority]")[0]))
        self.assertIn("priority", str(ctx.exception))

    def test_a_number_that_is_not_a_number_is_refused(self):
        text = CONFIG_PATH.read_text(encoding="utf-8").replace("batch_size = 20", 'batch_size = "20"')
        with self.assertRaises(ValueError) as ctx:
            load_reader_config(self.write_config(text=text))
        self.assertIn("batch_size", str(ctx.exception))


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.text = load_reader_config().prompt_path.read_text(encoding="utf-8")

    def test_prompt_names_every_field_and_allowed_value(self):
        for name in FIELDS:
            self.assertIn(name, self.text, f"the prompt never names the field {name}")
        for name, values in ALLOWED.items():
            for value in values:
                self.assertIn(value, self.text, f"the prompt never allows {value} for {name}")

    def test_prompt_has_no_dashes(self):
        self.assertNotIn(chr(0x2014), self.text)
        self.assertNotIn(chr(0x2013), self.text)

    def test_prompt_states_the_output_shape_and_the_quote_rule(self):
        for needle in ('"readings"', '"posting_id"', '"body_sha256"', '"fields"', '"value"', '"quote"',
                       "300 characters", "exactly"):
            self.assertIn(needle, self.text)

    def test_prompt_has_nothing_volatile(self):
        self.assertIsNone(re.search(r"\b20\d\d\b", self.text), "a year in the prompt changes the version every year")


if __name__ == "__main__":
    unittest.main()
