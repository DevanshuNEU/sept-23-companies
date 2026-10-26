"""Bands and the insights config (#81, Task 2): one source of size, stage, level and board words for the dataset,
the findings and the tilt. Unknown is None and never a band."""
import tempfile
import unittest
from pathlib import Path

from outreach.insights import bands as B
from outreach.insights.bands import (FEATURE_NAMES, Features, board_words, default_bands, features_of, hq_state,
                                     level_band, load_bands, size_band, stage_band)
from outreach.insights.config import default_insights_config, load_insights_config

PACKAGE = Path(B.__file__).resolve().parent
DASHES = chr(0x2013) + chr(0x2014)


def _write(text: str) -> Path:
    folder = Path(tempfile.mkdtemp(prefix="insights-bands-test-"))
    path = folder / "file.toml"
    path.write_text(text, encoding="utf-8")
    return path


class BandsTest(unittest.TestCase):
    def setUp(self):
        self.b = default_bands()

    def test_size_bands_at_the_edges(self):
        self.assertEqual("1 to 50", size_band(None, 50, self.b))
        self.assertEqual("51 to 300", size_band(51, None, self.b))
        self.assertEqual("51 to 300", size_band(200, 300, self.b))
        self.assertEqual("301 to 1,000", size_band(250, 400, self.b))  # the high end wins
        self.assertEqual("over 1,000", size_band(5000, None, self.b))
        self.assertEqual("1 to 50", size_band(1, None, self.b))
        self.assertEqual("301 to 1,000", size_band(1000, None, self.b))

    def test_stage_bands(self):
        self.assertEqual("seed or earlier", stage_band("yc", self.b))
        self.assertEqual("seed or earlier", stage_band("pre_seed", self.b))
        self.assertEqual("Series A or B", stage_band("series_b", self.b))
        self.assertEqual("later private", stage_band("series_d", self.b))
        self.assertEqual("later private", stage_band("late_private", self.b))
        self.assertEqual("public", stage_band("public", self.b))

    def test_level_bands(self):
        self.assertEqual("entry level", level_band("new_grad", intern=False, b=self.b))
        self.assertEqual("entry level", level_band("swe1", intern=False, b=self.b))
        self.assertEqual("SWE II", level_band("swe2", intern=False, b=self.b))
        self.assertEqual("level not stated", level_band("not_stated", intern=False, b=self.b))
        self.assertEqual("level not stated", level_band("open", intern=False, b=self.b))
        self.assertEqual("senior", level_band("senior", intern=False, b=self.b))
        for tier in ("new_grad", "swe2", "senior", "not_stated"):
            self.assertEqual("intern", level_band(tier, intern=True, b=self.b))

    def test_unknown_values_have_no_band(self):
        self.assertIsNone(size_band(None, None, self.b))
        self.assertIsNone(stage_band(None, self.b))
        self.assertIsNone(stage_band("bogus", self.b))
        self.assertIsNone(level_band(None, intern=False, b=self.b))
        self.assertIsNone(level_band("bogus", intern=False, b=self.b))
        self.assertIsNone(hq_state("Remote"))
        self.assertIsNone(hq_state(None))
        self.assertIsNone(hq_state(""))
        empty = features_of(size_low=None, size_high=None, stage=None, level_tier=None, intern=False,
                            board_system=None, bands=self.b)
        self.assertEqual(Features(), empty)
        self.assertEqual({name: None for name in FEATURE_NAMES}, empty.as_dict())

    def test_hq_state_reads_the_last_two_letters(self):
        self.assertEqual("CA", hq_state("San Francisco, CA"))
        self.assertEqual("NY", hq_state("Brooklyn, New York, NY "))
        self.assertIsNone(hq_state("Austin, Texas"))
        self.assertIsNone(hq_state("London, UK"))  # two capitals, but not a US state

    def test_board_words(self):
        self.assertEqual("Ashby", board_words("ashby"))
        self.assertEqual("SmartRecruiters", board_words("smartrecruiters"))
        self.assertEqual("BambooHR", board_words("bamboohr"))
        self.assertEqual("Oracle", board_words("oracle_orc"))
        self.assertEqual("JazzHR", board_words("jazzhr"))
        self.assertEqual("iCIMS", board_words("icims"))
        self.assertEqual("JOIN", board_words("join_com"))
        self.assertEqual("SuccessFactors", board_words("successfactors"))
        self.assertEqual("Greenhouse", board_words("greenhouse"))
        self.assertEqual("Lever", board_words("lever"))

    def test_features_of_names_the_four_traits(self):
        f = features_of(size_low=80, size_high=None, stage="series_a", level_tier="swe1", intern=False,
                        board_system="ashby", bands=self.b)
        self.assertEqual(("size_band", "stage_band", "level_band", "board_system"), FEATURE_NAMES)
        self.assertEqual({"size_band": "51 to 300", "stage_band": "Series A or B", "level_band": "entry level",
                          "board_system": "ashby"}, f.as_dict())

    def test_a_stage_in_two_bands_is_refused(self):
        text = (PACKAGE / "bands.toml").read_text(encoding="utf-8").replace(
            'series_a_b = ["series_a", "series_b"]', 'series_a_b = ["series_a", "series_b", "seed"]')
        self.assertIn('"seed"]', text)
        with self.assertRaisesRegex(ValueError, "seed"):
            load_bands(_write(text))

    def test_tops_must_increase(self):
        text = (PACKAGE / "bands.toml").read_text(encoding="utf-8").replace(
            "size_tops = [50, 300, 1000]", "size_tops = [50, 50, 1000]")
        with self.assertRaisesRegex(ValueError, "size_tops"):
            load_bands(_write(text))

    def test_a_missing_key_is_named(self):
        text = (PACKAGE / "bands.toml").read_text(encoding="utf-8").replace("entry_tiers", "entry_tierz")
        with self.assertRaisesRegex(KeyError, "entry_tiers"):
            load_bands(_write(text))

    def test_the_shipped_file_loads_with_its_bytes(self):
        b = load_bands()
        self.assertEqual((50, 300, 1000), b.size_tops)
        self.assertEqual(frozenset({"new_grad", "early_career", "swe1"}), b.entry_tiers)
        self.assertEqual((PACKAGE / "bands.toml").read_bytes(), b.raw)


class InsightsConfigTest(unittest.TestCase):
    def test_shipped_values(self):
        cfg = default_insights_config()
        self.assertEqual(20, cfg.group_min)
        self.assertEqual(3, cfg.share_min_count)
        self.assertEqual(300, cfg.lookalike_pool)
        self.assertEqual(15, cfg.lookalike_count)
        self.assertEqual(Path("~/Desktop/outreach-scratch/insights").expanduser(), cfg.export_dir)

    def test_group_min_matches_the_findings_minimum(self):
        from outreach.api.config import load_config
        self.assertEqual(load_config().finding_group_min, default_insights_config().group_min)

    def test_a_number_under_one_is_refused(self):
        text = (PACKAGE / "config.toml").read_text(encoding="utf-8").replace("share_min_count = 3",
                                                                            "share_min_count = 0")
        with self.assertRaisesRegex(ValueError, "share_min_count"):
            load_insights_config(_write(text))


class CopyTest(unittest.TestCase):
    def test_no_dashes_in_band_and_config_files(self):
        for name in ("bands.toml", "bands.py", "config.toml", "config.py", "__init__.py"):
            text = (PACKAGE / name).read_text(encoding="utf-8")
            self.assertFalse(any(c in text for c in DASHES), name)


if __name__ == "__main__":
    unittest.main()
