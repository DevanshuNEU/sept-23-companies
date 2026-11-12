"""The software role rule (#250, #9 job board ruling 2026-11-12): which titles reach the Jobs list."""
import tempfile
import unittest
from pathlib import Path

from outreach.rules.config import CONFIG_PATH, default_config, load_config
from outreach.rules.role import ROLES, role_of

LABELED = Path(__file__).resolve().parent / "fixtures" / "role_titles.tsv"


def _labeled() -> list[tuple[str, str]]:
    rows = []
    for line in LABELED.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        title, label = line.split("\t")
        rows.append((title, label))
    return rows


class LabeledTitleTests(unittest.TestCase):
    def test_the_set_is_big_enough_and_well_formed(self):
        rows = _labeled()
        self.assertGreaterEqual(len(rows), 400)
        self.assertEqual({label for _, label in rows}, set(ROLES))
        self.assertEqual(len({t for t, _ in rows}), len(rows), "a title is labeled twice")

    def test_labeled_titles(self):
        cfg = default_config()
        misses = [f"{title!r}: labeled {label}, got {role_of(title, cfg)}"
                  for title, label in _labeled() if role_of(title, cfg) != label]
        self.assertEqual(misses, [], "\n" + "\n".join(misses))

    def test_the_screenshot_titles(self):
        # The Jobs list of 2026-11-12 that started #250: only the Forward Deployed role is his.
        cfg = default_config()
        self.assertEqual(role_of("Junior Forward Deployed Engineer", cfg), "software")
        for title in ("Professional Services Engineer", "COBOL Developer (All Levels & Internal Paths Available)",
                      "Junior Design Engineer", "Associate Database Engineer", "Data Scientist I", "Design Engineer"):
            with self.subTest(title=title):
                self.assertEqual(role_of(title, cfg), "other")

    def test_empty_title_is_other(self):
        self.assertEqual(role_of("", default_config()), "other")


class RoleSectionTests(unittest.TestCase):
    def test_role_section_is_required(self):
        broken = CONFIG_PATH.read_text(encoding="utf-8").replace("[role]", "[role_renamed]")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text(broken, encoding="utf-8")
            with self.assertRaisesRegex(KeyError, r"\[role\]"):
                load_config(p)


if __name__ == "__main__":
    unittest.main()
