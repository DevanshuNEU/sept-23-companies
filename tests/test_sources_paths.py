"""The sources cache folder (#31 Task 2)."""
import os
import unittest
from pathlib import Path
from unittest import mock

from outreach.sources.paths import sources_dir


class SourcesDirTest(unittest.TestCase):
    def test_sources_dir_uses_the_env_override(self):
        with mock.patch.dict(os.environ, {"OUTREACH_SOURCES_DIR": "/scratch/somewhere"}):
            self.assertEqual(sources_dir(), Path("/scratch/somewhere"))

    def test_sources_dir_default_is_under_application_support(self):
        env = {k: v for k, v in os.environ.items() if k != "OUTREACH_SOURCES_DIR"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(sources_dir(), Path.home() / "Library" / "Application Support" / "outreach" / "sources")

    def test_tests_init_points_sources_dir_at_scratch(self):
        live = Path.home() / "Library" / "Application Support" / "outreach"
        folder = Path(os.environ["OUTREACH_SOURCES_DIR"])
        self.assertTrue(folder.is_absolute())
        self.assertNotIn(live, (folder, *folder.parents))
        self.assertEqual(sources_dir(), folder)


if __name__ == "__main__":
    unittest.main()
