"""Ranker config, version hash and the weights guard (issue #6, Task 1)."""
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from outreach.crawl.config import load_crawl_config
from outreach.crawl import stale as stale_module
from outreach.ranking import version as ranking_version
from outreach.ranking.config import CONFIG_PATH, RankerConfig, default_ranker_config, load_ranker_config
from outreach.ranking.version import NOT_RANKING, RANKING_DIR, RANKING_FILES, ranker_version

BOTTOM = ("staffing_risk", "large_later_stage")


def _temp_ranking_dir(tc: unittest.TestCase) -> tuple[Path, Path]:
    """A copy of the ranking folder and of the crawler's stale.py, with every hashed file present (a file that does
    not exist yet is written as a stub, so the tests keep working while the folder fills up)."""
    tmp = Path(tempfile.mkdtemp())
    tc.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    ranking, crawl = tmp / "ranking", tmp / "crawl"
    (ranking / "signals").mkdir(parents=True)
    crawl.mkdir()
    for name in RANKING_FILES:
        src = RANKING_DIR / name
        (ranking / name).write_bytes(src.read_bytes() if src.is_file() else b"# stub\n")
    for src in (RANKING_DIR / "signals").glob("*.py") if (RANKING_DIR / "signals").is_dir() else ():
        shutil.copy(src, ranking / "signals" / src.name)
    if not list((ranking / "signals").glob("*.py")):
        (ranking / "signals" / "stub.py").write_text("# stub\n", encoding="utf-8")
    for name in NOT_RANKING:
        (ranking / name).write_text("# not hashed\n", encoding="utf-8")
    shutil.copy(Path(stale_module.__file__), crawl / "stale.py")
    return ranking, crawl


def _flip(path: Path) -> None:
    path.write_bytes(path.read_bytes() + b"\n#x")


class VersionTests(unittest.TestCase):
    def test_version_shape(self):
        self.assertRegex(ranker_version(), r"^rk-[0-9a-f]{12}$")

    def test_version_moves_with_config_wages_and_a_signal_file(self):
        ranking, crawl = _temp_ranking_dir(self)
        base = ranker_version(ranking, crawl)
        self.assertEqual(ranker_version(ranking, crawl), base)
        targets = ["config.toml", "wages.toml", *(f"signals/{p.name}" for p in (ranking / "signals").glob("*.py"))]
        self.assertTrue(any(t.startswith("signals/") for t in targets))
        for rel in targets:
            with self.subTest(file=rel):
                path = ranking / rel
                original = path.read_bytes()
                _flip(path)
                self.assertNotEqual(ranker_version(ranking, crawl), base)
                path.write_bytes(original)
                self.assertEqual(ranker_version(ranking, crawl), base)

    def test_version_moves_with_every_hashed_code_file(self):
        ranking, crawl = _temp_ranking_dir(self)
        base = ranker_version(ranking, crawl)
        for name in RANKING_FILES:
            with self.subTest(file=name):
                path = ranking / name
                original = path.read_bytes()
                _flip(path)
                self.assertNotEqual(ranker_version(ranking, crawl), base)
                path.write_bytes(original)

    def test_a_new_signal_file_moves_it_and_a_missing_file_hashes_as_empty(self):
        ranking, crawl = _temp_ranking_dir(self)
        base = ranker_version(ranking, crawl)
        (ranking / "signals" / "brand_new.py").write_text("x = 1\n", encoding="utf-8")
        self.assertNotEqual(ranker_version(ranking, crawl), base)
        (ranking / "signals" / "brand_new.py").unlink()
        (ranking / "hide.py").unlink()
        ranker_version(ranking, crawl)  # does not raise
        (ranking / "hide.py").write_bytes(b"")
        self.assertEqual(ranker_version(ranking, crawl), ranker_version(ranking, crawl))

    def test_version_ignores_job_and_diff_files(self):
        ranking, crawl = _temp_ranking_dir(self)
        base = ranker_version(ranking, crawl)
        for name in ("job.py", "diff.py", "scores.py", "review.py", "version.py", "__init__.py", "__main__.py"):
            with self.subTest(file=name):
                (ranking / name).write_text("# changed\n", encoding="utf-8")
                self.assertEqual(ranker_version(ranking, crawl), base)

    def test_version_moves_with_the_stale_module_and_the_stale_months_value(self):
        ranking, crawl = _temp_ranking_dir(self)
        base = ranker_version(ranking, crawl)
        _flip(crawl / "stale.py")
        self.assertNotEqual(ranker_version(ranking, crawl), base)
        ranking, crawl = _temp_ranking_dir(self)
        months = load_crawl_config().stale_after_months
        other = months + 6
        with mock.patch.object(ranking_version, "load_crawl_config",
                               return_value=mock.Mock(stale_after_months=other)):
            self.assertNotEqual(ranker_version(ranking, crawl), base)
        with mock.patch.object(ranking_version, "load_crawl_config",
                               return_value=mock.Mock(stale_after_months=months)):
            self.assertEqual(ranker_version(ranking, crawl), base)

    def test_version_hashes_the_stale_text_not_a_bare_number(self):
        """Pins the format: sha256 over files, then stale.py bytes, then `stale_after_months=<n>`."""
        import hashlib
        ranking, crawl = _temp_ranking_dir(self)
        digest = hashlib.sha256()
        rels = sorted([*RANKING_FILES, *(f"signals/{p.name}" for p in (ranking / "signals").glob("*.py"))])
        for rel in rels:
            digest.update(rel.encode() + b"\0" + (ranking / rel).read_bytes() + b"\0")
        digest.update((crawl / "stale.py").read_bytes())
        digest.update(f"stale_after_months={load_crawl_config().stale_after_months}".encode())
        self.assertEqual(ranker_version(ranking, crawl), "rk-" + digest.hexdigest()[:12])

    def test_every_top_level_file_is_ranking_or_not(self):
        listed = set(RANKING_FILES) | set(NOT_RANKING)
        self.assertFalse(set(RANKING_FILES) & set(NOT_RANKING), "a file cannot be in both groups")
        on_disk = {p.name for p in RANKING_DIR.iterdir() if p.is_file() and p.suffix in (".py", ".toml")}
        self.assertEqual(sorted(on_disk - listed), [], "a new ranking file must choose: RANKING_FILES or NOT_RANKING")

    def test_confidence_and_stale_are_in_the_hashed_set(self):
        self.assertIn("confidence.py", RANKING_FILES)
        self.assertIn("wages.toml", RANKING_FILES)
        self.assertIn("config.toml", RANKING_FILES)


class ConfigTests(unittest.TestCase):
    def test_loads_the_shipped_values(self):
        cfg = default_ranker_config()
        self.assertIsInstance(cfg, RankerConfig)
        self.assertIs(default_ranker_config(), cfg)
        self.assertEqual(cfg.ranker["keep_versions"], 3)
        self.assertEqual(cfg.ranker["size_check_top_n"], 100)
        self.assertEqual(cfg.weights["live_role"], 40.0)
        self.assertEqual(cfg.hide["size_over"], 300)
        self.assertEqual(cfg.hide["size_confidences"], ["confirmed", "estimate"])
        self.assertEqual(cfg.section("live_role")["fresh_days"], [14, 30, 60])
        self.assertEqual(cfg.section("pay_level")["level_4"], 1.0)
        self.assertEqual(cfg.section("funding"), {})
        self.assertEqual(cfg.section("large_later_stage"), {})
        self.assertEqual(cfg.section("small")["later_stages"][-1], "public")
        self.assertEqual(cfg.raw, CONFIG_PATH.read_bytes())
        self.assertNotIn("ranker", cfg.sections)
        self.assertNotIn("weights", cfg.sections)
        self.assertNotIn("hide", cfg.sections)

    def test_section_names_a_missing_section(self):
        with self.assertRaises(KeyError) as cm:
            default_ranker_config().section("nope")
        self.assertIn("nope", str(cm.exception))

    def test_confidence_factors_are_ordered_and_high_is_one(self):
        c = default_ranker_config().section("confidence")
        self.assertEqual(c["factor_high"], 1.0)
        self.assertTrue(c["factor_high"] >= c["factor_medium"] >= c["factor_low"] > 0)
        self.assertTrue(0 < c["rank_high_share"] < 1)
        self.assertTrue(0 < c["rank_low_share"] < 1)
        self.assertGreaterEqual(c["reason_count"], 1)

    def test_missing_key_names_it(self):
        text = CONFIG_PATH.read_text(encoding="utf-8")
        text, n = re.subn(r"(?m)^small\s*=.*\n", "", text, count=1)
        self.assertEqual(n, 1)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text(text, encoding="utf-8")
            with self.assertRaises(KeyError) as cm:
                load_ranker_config(p)
        self.assertIn("small", str(cm.exception))

    def test_missing_section_names_it(self):
        text = CONFIG_PATH.read_text(encoding="utf-8")
        text = text.replace("[staffing_risk]", "[staffing_riskx]")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text(text, encoding="utf-8")
            with self.assertRaises(KeyError) as cm:
                load_ranker_config(p)
        self.assertIn("staffing_risk", str(cm.exception))

    def test_every_weight_is_a_float(self):
        for name, w in default_ranker_config().weights.items():
            with self.subTest(weight=name):
                self.assertIsInstance(w, float)
                self.assertGreater(w, 0)

    def test_a_non_number_weight_is_refused(self):
        text = CONFIG_PATH.read_text(encoding="utf-8")
        text, n = re.subn(r"(?m)^small\s*=.*$", 'small = "lots"', text, count=1)
        self.assertEqual(n, 1)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                load_ranker_config(p)

    def test_bottom_weights_outweigh_every_positive_weight(self):
        w = default_ranker_config().weights
        positive = sum(v for k, v in w.items() if k not in BOTTOM)
        self.assertEqual(positive, 120.0)
        for name in BOTTOM:
            self.assertGreater(w[name], positive, f"{name} must outweigh every positive weight added together")

    def test_the_guard_rejects_a_config_where_a_bottom_weight_is_too_small(self):
        text = CONFIG_PATH.read_text(encoding="utf-8")
        text, n = re.subn(r"(?m)^large_later_stage\s*=\s*200\.0", "large_later_stage = 100.0", text, count=1)
        self.assertEqual(n, 1)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError) as cm:
                load_ranker_config(p)
        self.assertIn("large_later_stage", str(cm.exception))

    def test_no_dashes_in_config_or_wages(self):
        for name in ("config.toml", "wages.toml"):
            path = RANKING_DIR / name
            if not path.is_file():
                continue  # wages.toml lands with Task 2
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(chr(0x2013), text, name)
            self.assertNotIn(chr(0x2014), text, name)


if __name__ == "__main__":
    unittest.main()
