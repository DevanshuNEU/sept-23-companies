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
from outreach.ranking.version import (INSIGHTS_DIR, INSIGHTS_FILES, NOT_INSIGHTS, NOT_RANKING, RANKING_DIR,
                                      RANKING_FILES, ranker_version)

BOTTOM = ("staffing_risk", "large_later_stage")
TILT = "interview_like"


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


def _temp_insights_dir(tc: unittest.TestCase) -> Path:
    """A copy of every hashed insights file, plus the not-hashed ones as stubs."""
    tmp = Path(tempfile.mkdtemp())
    tc.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    for name in INSIGHTS_FILES:
        (tmp / name).write_bytes((INSIGHTS_DIR / name).read_bytes())
    for name in NOT_INSIGHTS:
        (tmp / name).write_text("# not hashed\n", encoding="utf-8")
    return tmp


def _set(text: str, section: str, key: str, value: str) -> str:
    """The config text with `key` in `[section]` set to the TOML text `value`."""
    head = text.index(f"\n[{section}]\n") + 1
    nxt = text.find("\n[", head + 1)
    end = len(text) if nxt == -1 else nxt + 1
    block, n = re.subn(rf"(?m)^{key}\s*=.*$", f"{key} = {value}", text[head:end])
    assert n == 1, (section, key)
    return text[:head] + block + text[end:]


def _load_edited(tc: unittest.TestCase, *edits: tuple[str, str, str]):
    text = CONFIG_PATH.read_text(encoding="utf-8")
    for section, key, value in edits:
        text = _set(text, section, key, value)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.toml"
        p.write_text(text, encoding="utf-8")
        return load_ranker_config(p)


# Numbers that are exact in binary, so a boundary test can sit exactly on the limit: the positive weights add to 126
# (120 plus the interview tilt's 6) and the worst non-bottom negatives are 12 * 0.5 + 10 * 0.25 + 12 * 0.25 = 11.5, a
# limit of 137.5.
EXACT = (("everify", "not_enrolled", "-0.5"), ("pay_level", "level_1", "-0.25"), ("small", "weak_flag_value", "-0.25"))
LIMIT = 137.5


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
        missing = ranker_version(ranking, crawl)  # does not raise
        (ranking / "hide.py").write_bytes(b"")
        self.assertEqual(ranker_version(ranking, crawl), missing, "a missing file must hash as an empty one")
        (ranking / "hide.py").write_bytes(b"x")
        self.assertNotEqual(ranker_version(ranking, crawl), missing)

    def test_version_moves_with_the_job_because_compute_rank_lives_there(self):
        self.assertIn("job.py", RANKING_FILES)
        self.assertNotIn("job.py", NOT_RANKING)
        ranking, crawl = _temp_ranking_dir(self)
        base = ranker_version(ranking, crawl)
        (ranking / "job.py").write_text("# compute_rank changed\n", encoding="utf-8")
        self.assertNotEqual(ranker_version(ranking, crawl), base)

    def test_version_moves_with_the_rules_version(self):
        ranking, crawl = _temp_ranking_dir(self)
        with mock.patch.object(ranking_version, "ruleset_version", return_value="r-aaaaaaaaaaaa"):
            first = ranker_version(ranking, crawl)
            self.assertEqual(ranker_version(ranking, crawl), first)
        with mock.patch.object(ranking_version, "ruleset_version", return_value="r-bbbbbbbbbbbb"):
            self.assertNotEqual(ranker_version(ranking, crawl), first)

    def test_the_shipped_version_reads_the_real_rules_version(self):
        from outreach.rules.version import ruleset_version
        self.assertIs(ranking_version.ruleset_version, ruleset_version)

    def test_version_ignores_diff_and_bookkeeping_files(self):
        ranking, crawl = _temp_ranking_dir(self)
        base = ranker_version(ranking, crawl)
        for name in ("diff.py", "scores.py", "review.py", "version.py", "__init__.py", "__main__.py"):
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
        """Pins the format: sha256 over files, stale.py bytes, `stale_after_months=<n>`, `ruleset=<version>`."""
        import hashlib
        ranking, crawl = _temp_ranking_dir(self)
        digest = hashlib.sha256()
        rels = sorted([*RANKING_FILES, *(f"signals/{p.name}" for p in (ranking / "signals").glob("*.py"))])
        for rel in rels:
            digest.update(rel.encode() + b"\0" + (ranking / rel).read_bytes() + b"\0")
        for name in sorted(INSIGHTS_FILES):
            digest.update(f"insights/{name}".encode() + b"\0" + (INSIGHTS_DIR / name).read_bytes() + b"\0")
        digest.update((crawl / "stale.py").read_bytes())
        digest.update(f"stale_after_months={load_crawl_config().stale_after_months}".encode())
        digest.update(f"ruleset={ranking_version.ruleset_version()}".encode())
        self.assertEqual(ranker_version(ranking, crawl), "rk-" + digest.hexdigest()[:12])

    def test_every_top_level_file_is_ranking_or_not(self):
        listed = set(RANKING_FILES) | set(NOT_RANKING)
        self.assertFalse(set(RANKING_FILES) & set(NOT_RANKING), "a file cannot be in both groups")
        on_disk = {p.name for p in RANKING_DIR.iterdir() if p.is_file() and p.suffix in (".py", ".toml")}
        self.assertEqual(sorted(on_disk - listed), [], "a new ranking file must choose: RANKING_FILES or NOT_RANKING")

    def test_version_moves_with_the_insights_band_files(self):
        ranking, crawl = _temp_ranking_dir(self)
        insights = _temp_insights_dir(self)
        base = ranker_version(ranking, crawl, insights)
        self.assertEqual(ranker_version(ranking, crawl, insights), base)
        self.assertEqual(("bands.toml", "bands.py", "dataset.py", "similar.py"), INSIGHTS_FILES)
        for name in INSIGHTS_FILES:
            with self.subTest(file=name):
                path = insights / name
                original = path.read_bytes()
                _flip(path)
                self.assertNotEqual(ranker_version(ranking, crawl, insights), base)
                path.write_bytes(original)
                self.assertEqual(ranker_version(ranking, crawl, insights), base)
        for name in ("export.py", "findings.py", "config.toml"):
            with self.subTest(file=name):
                (insights / name).write_text("# changed\n", encoding="utf-8")
                self.assertEqual(ranker_version(ranking, crawl, insights), base)

    def test_every_insights_file_is_hashed_or_not(self):
        self.assertFalse(set(INSIGHTS_FILES) & set(NOT_INSIGHTS), "a file cannot be in both groups")
        on_disk = {p.name for p in INSIGHTS_DIR.iterdir() if p.is_file() and p.suffix in (".py", ".toml")}
        listed = set(INSIGHTS_FILES) | set(NOT_INSIGHTS)
        self.assertEqual(sorted(on_disk - listed), [], "a new insights file must choose: INSIGHTS_FILES or NOT_INSIGHTS")
        self.assertTrue(set(INSIGHTS_FILES) <= on_disk)

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
        self.assertEqual(positive, 126.0, "120 plus the interview tilt's 6: the tilt counts toward the bottom push")
        for name in BOTTOM:
            self.assertGreater(w[name], positive, f"{name} must outweigh every positive weight added together")

    def test_the_guard_rejects_a_config_where_a_bottom_weight_is_too_small(self):
        with self.assertRaises(ValueError) as cm:
            _load_edited(self, ("weights", "large_later_stage", "100.0"))
        self.assertIn("large_later_stage", str(cm.exception))

    def test_the_shipped_bottom_weights_clear_the_limit_with_the_worst_negatives(self):
        cfg = default_ranker_config()
        w = cfg.weights
        positive = sum(v for k, v in w.items() if k not in BOTTOM)
        worst = (0.5 * w["everify"] + 0.3 * w["pay_level"] + 0.3 * w["small"])
        self.assertGreater(abs(cfg.section("staffing_risk")["high"]) * w["staffing_risk"], positive + worst)
        self.assertGreater(w["large_later_stage"], positive + worst)

    def test_bottom_weight_at_the_limit_is_refused_and_one_over_is_accepted(self):
        for name in BOTTOM:
            with self.subTest(weight=name, at="limit"):
                with self.assertRaises(ValueError) as cm:
                    _load_edited(self, *EXACT, ("weights", name, str(LIMIT)))
                self.assertIn(name, str(cm.exception))
            with self.subTest(weight=name, at="below"):
                with self.assertRaises(ValueError):
                    _load_edited(self, *EXACT, ("weights", name, str(LIMIT - 1)))
            with self.subTest(weight=name, at="one over"):
                cfg = _load_edited(self, *EXACT, ("weights", name, str(LIMIT + 1)))
                self.assertEqual(cfg.weights[name], LIMIT + 1)

    def test_the_limit_includes_the_worst_non_bottom_negatives(self):
        # Without the negatives the limit would be 126, so 130 passes; with them (137.5) it must not.
        edits = (("weights", "staffing_risk", "130.0"), ("weights", "large_later_stage", "300.0"))
        with self.assertRaises(ValueError):
            _load_edited(self, *EXACT, *edits)
        zero = (("everify", "not_enrolled", "0.0"), ("pay_level", "level_1", "0.0"), ("small", "weak_flag_value", "0.0"))
        _load_edited(self, *zero, *edits)  # no negatives: 130 > 126 is fine
        # Each negative moves the limit on its own.
        for section, key, weight, value in (("everify", "not_enrolled", "everify", "-1.0"),
                                            ("pay_level", "level_1", "pay_level", "-1.0"),
                                            ("small", "weak_flag_value", "small", "-1.0")):
            with self.subTest(negative=key):
                with self.assertRaises(ValueError):
                    _load_edited(self, *zero, (section, key, value), *edits)

    def test_a_staffing_risk_high_above_minus_one_is_refused(self):
        for value in ("-0.99", "-0.5", "0.0", "1.0"):
            with self.subTest(high=value):
                with self.assertRaises(ValueError) as cm:
                    _load_edited(self, ("staffing_risk", "high", value))
                self.assertIn("high", str(cm.exception))
        self.assertEqual(_load_edited(self, ("staffing_risk", "high", "-1.0")).section("staffing_risk")["high"], -1.0)
        with self.assertRaises(ValueError):
            _load_edited(self, ("staffing_risk", "high", "-1.5"))

    def test_the_staffing_risk_weight_is_scaled_by_its_high_value(self):
        # A weight of 200 with high = -1.0 is 200 points; the guard reads weight * |high|, so a smaller |high| would
        # be caught by the high check first. Pin the arithmetic through the shipped pair.
        cfg = default_ranker_config()
        self.assertEqual(abs(cfg.section("staffing_risk")["high"]) * cfg.weights["staffing_risk"], 200.0)

    def test_a_non_number_negative_value_is_refused(self):
        with self.assertRaises(ValueError):
            _load_edited(self, ("everify", "not_enrolled", '"lots"'))

    def test_confidence_factors_must_be_in_range_and_ordered(self):
        for edits in ((("confidence", "factor_high", "1.5"),), (("confidence", "factor_low", "0.0"),),
                      (("confidence", "factor_low", "-0.1"),),
                      (("confidence", "factor_medium", "0.4"),),  # below low
                      (("confidence", "factor_high", "0.8"),),  # below medium
                      (("confidence", "factor_high", '"1.0"'),)):
            with self.subTest(edits=edits):
                with self.assertRaises(ValueError):
                    _load_edited(self, *edits)
        _load_edited(self, ("confidence", "factor_low", "0.85"))  # equal to medium is allowed

    def test_the_hide_size_limit_and_confidences_are_checked(self):
        for edits in ((("hide", "size_over", "299"),), (("hide", "size_over", "true"),),
                      (("hide", "size_over", '"300"'),), (("hide", "size_over", "300.5"),),
                      (("hide", "size_confidences", '["confirmed", "guess"]'),),
                      (("hide", "size_confidences", '"confirmed"'),),
                      (("hide", "size_confidences", '["Confirmed"]'),)):
            with self.subTest(edits=edits):
                with self.assertRaises(ValueError):
                    _load_edited(self, *edits)
        self.assertEqual(_load_edited(self, ("hide", "size_over", "500")).hide["size_over"], 500)
        self.assertEqual(_load_edited(self, ("hide", "size_confidences", '["confirmed"]')).hide["size_confidences"],
                         ["confirmed"])
        _load_edited(self, ("hide", "size_confidences", "[]"))  # never hide by size: allowed

    def test_size_check_top_n_must_be_a_whole_number_of_at_least_one(self):
        for value in ("0", "-5", "2.5", "true", '"100"'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    _load_edited(self, ("ranker", "size_check_top_n", value))
        self.assertEqual(_load_edited(self, ("ranker", "size_check_top_n", "1")).ranker["size_check_top_n"], 1)

    def test_the_funding_window_and_the_lca_junior_cap_must_be_above_zero(self):
        for section, key in (("ranker", "funding_window_months"), ("lca_junior", "cap")):
            for value in ("0", "-3", "2.5", "true", '"18"'):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError) as cm:
                        _load_edited(self, (section, key, value))
                    self.assertIn(key, str(cm.exception))
        cfg = _load_edited(self, ("ranker", "funding_window_months", "1"), ("lca_junior", "cap", "1"))
        self.assertEqual((cfg.ranker["funding_window_months"], cfg.section("lca_junior")["cap"]), (1, 1))

    def test_confidence_shares_and_counts_are_checked(self):
        for key, value in (("rank_high_share", "0.0"), ("rank_high_share", "1.0"), ("rank_low_share", "0.0"),
                           ("rank_low_share", "1.5"), ("reason_count", "0"), ("reason_count", "2.5"),
                           ("evidence_old_days", "0"), ("lca_pay_medium_filings", "0")):
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    _load_edited(self, ("confidence", key, value))

    def test_interview_like_weight_above_the_cap_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            _load_edited(self, ("weights", TILT, "12.5"))
        self.assertIn("12.5", str(cm.exception))
        self.assertIn("12", str(cm.exception).replace("12.5", ""))
        self.assertEqual(_load_edited(self, ("weights", TILT, "12.0")).weights[TILT], 12.0)  # exactly the cap

    def test_the_tilt_cap_follows_its_share(self):
        cfg = _load_edited(self, ("interview_like", "max_weight_share", "0.2"), ("weights", TILT, "20.0"))
        self.assertEqual(20.0, cfg.weights[TILT])
        for value in ("0.0", "-0.1", "0.3", '"0.1"', "true"):
            with self.subTest(max_weight_share=value):
                with self.assertRaises(ValueError) as cm:
                    _load_edited(self, ("interview_like", "max_weight_share", value))
                self.assertIn("max_weight_share", str(cm.exception))

    def test_the_tilt_counts_must_be_whole_numbers_of_at_least_one(self):
        for key in ("min_interview_companies", "min_matches", "confident_at"):
            for value in ("0", "-1", "2.5", "true", '"3"'):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError) as cm:
                        _load_edited(self, ("interview_like", key, value))
                    self.assertIn(key, str(cm.exception))

    def test_zero_is_allowed_only_for_interview_like(self):
        self.assertEqual(0.0, _load_edited(self, ("weights", TILT, "0.0")).weights[TILT])
        with self.assertRaises(ValueError):
            _load_edited(self, ("weights", TILT, "-1.0"))
        for name in ("everify", "funding", "live_role"):
            with self.subTest(weight=name):
                with self.assertRaises(ValueError) as cm:
                    _load_edited(self, ("weights", name, "0.0"))
                self.assertIn(name, str(cm.exception))

    def test_the_shipped_tilt_is_small(self):
        cfg = default_ranker_config()
        w = cfg.weights
        others = sum(v for k, v in w.items() if k not in BOTTOM and k != TILT)
        self.assertEqual(120.0, others)
        self.assertLessEqual(w[TILT], 0.1 * others)
        self.assertEqual(0.1, cfg.section(TILT)["max_weight_share"])
        self.assertEqual((3, 2, 20), tuple(cfg.section(TILT)[k] for k in
                                          ("min_interview_companies", "min_matches", "confident_at")))

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
