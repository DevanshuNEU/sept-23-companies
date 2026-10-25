import re
import tempfile
import unittest
from pathlib import Path

from outreach.review import KINDS, ReviewError
from outreach.review.config import ReviewConfig, load_review_config
from outreach.review.kinds import (BOARD_DOUBTFUL_PREFIX, CAREERS_UNREADABLE_PREFIX, CONFLICT_PREFIX, NO_GO_REASONS,
                                   flag_family, spec_of, trigger_of)
from tests.storetest import fresh_store

SCHEMA_RAISERS = {"crawler", "rules", "daily_list", "sender", "claude", "import", "source", "reader", "ranker"}


def _check_list(sql: str, column: str) -> set[str]:
    """The quoted values of `column TEXT ... CHECK (column IN (...))` in a CREATE TABLE statement."""
    match = re.search(rf"\b{column}\s+TEXT[^,]*?CHECK\s*\(\s*{column}\s+IN\s*\(([^)]*)\)", sql, re.S)
    assert match, f"no CHECK on {column}"
    return set(re.findall(r"'([^']+)'", match.group(1)))


class KindsTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.sql = self.conn.execute("SELECT sql FROM sqlite_master WHERE name = 'review_item'").fetchone()[0]

    def test_every_kind_in_the_schema_has_a_spec(self):
        self.assertEqual(_check_list(self.sql, "kind"), set(KINDS))
        for kind, spec in KINDS.items():
            self.assertEqual(spec.kind, kind)
            self.assertIs(spec_of(kind), spec)
        with self.assertRaisesRegex(KeyError, "nonesuch"):
            spec_of("nonesuch")

    def test_every_raised_by_a_spec_names_is_in_the_schema(self):
        self.assertEqual(_check_list(self.sql, "raised_by"), SCHEMA_RAISERS)
        for spec in KINDS.values():
            self.assertTrue(spec.raised_by, spec.kind)
            self.assertLessEqual(set(spec.raised_by), SCHEMA_RAISERS, spec.kind)
        self.assertEqual(set(KINDS["flagged"].raised_by), {"ranker", "crawler", "claude", "import"})

    def test_triggers_one_to_five_are_the_kinds_the_issue_names(self):
        self.assertEqual({s.trigger: s.kind for s in KINDS.values() if s.trigger},
                         {1: "board_unreadable", 2: "board_empty", 3: "sponsorship_unclear", 4: "size_unknown",
                          5: "domain_missing"})

    def test_flagged_follows_its_reason(self):
        self.assertEqual(trigger_of("flagged", "Postings disagree on sponsorship: posting 4 refuses"), 3)
        self.assertEqual(trigger_of("flagged", "Careers page unreadable: https://x.example/jobs"), 1)
        self.assertIsNone(trigger_of("flagged", "board match doubtful: names differ"))
        self.assertIsNone(trigger_of("flagged", "check this one"))
        self.assertIsNone(trigger_of("flagged"))
        self.assertEqual(trigger_of("board_empty", "anything"), 2)
        self.assertIsNone(trigger_of("merge_candidate"))

    def test_flag_family_is_a_case_insensitive_prefix(self):
        self.assertEqual(flag_family(BOARD_DOUBTFUL_PREFIX.upper() + ": x"), "board_identity")
        self.assertEqual(flag_family(CONFLICT_PREFIX.lower()), "sponsorship_conflict")
        self.assertEqual(flag_family(CAREERS_UNREADABLE_PREFIX.title()), "careers_unreadable")
        self.assertEqual(flag_family("the board match doubtful"), "other")

    def test_size_variants_share_trigger_4s_fix(self):
        for kind in ("size_guess", "size_check"):
            self.assertEqual(KINDS[kind].edit_field, "size")
            self.assertIsNone(KINDS[kind].trigger)
            self.assertEqual(KINDS[kind].family, "size")
        self.assertEqual({k for k, s in KINDS.items() if s.holds_email},
                         {"domain_missing", "size_unknown", "size_guess", "size_check"})

    def test_merge_has_no_no_go_and_reader_and_flags_have_no_edit_field(self):
        self.assertNotIn("no_go", KINDS["merge_candidate"].fixes)
        self.assertIn("merge", KINDS["merge_candidate"].fixes)
        for kind in ("reader_disagrees", "flagged"):
            self.assertIsNone(KINDS[kind].edit_field)
            self.assertNotIn("edit", KINDS[kind].fixes)
        for spec in KINDS.values():
            self.assertLessEqual(set(spec.fixes), {"ok", "edit", "no_go", "merge"})
            self.assertEqual("edit" in spec.fixes, spec.edit_field is not None, spec.kind)
        self.assertEqual(KINDS["board_unreadable"].edit_field, "board_url")
        self.assertEqual(KINDS["sponsorship_unclear"].edit_field, "sponsorship")
        self.assertEqual(KINDS["domain_missing"].edit_field, "domain")

    def test_no_go_reasons_equal_the_schema_check(self):
        sql = self.conn.execute("SELECT sql FROM sqlite_master WHERE name = 'no_go'").fetchone()[0]
        self.assertEqual(set(NO_GO_REASONS), _check_list(sql, "reason"))
        self.assertEqual(len(NO_GO_REASONS), len(set(NO_GO_REASONS)))

    def test_review_error_carries_its_code(self):
        err = ReviewError("conflict", "This item changed since you loaded it.")
        self.assertEqual((err.code, err.message, str(err)), ("conflict", "This item changed since you loaded it.",
                                                            "This item changed since you loaded it."))


class ConfigTest(unittest.TestCase):
    def _load(self, text: str):
        folder = tempfile.TemporaryDirectory(prefix="outreach-review-config-")
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "config.toml"
        path.write_text(text, encoding="utf-8")
        return load_review_config(path)

    def test_config_loads_and_refuses_an_unknown_key_or_zero(self):
        self.assertEqual(load_review_config(), ReviewConfig(sponsorship_top_n=150, planned_days=4, stale_days=14))
        good = "[sweep]\nsponsorship_top_n = 10\nplanned_days = 2\nstale_days = 3\n"
        self.assertEqual(self._load(good), ReviewConfig(10, 2, 3))
        with self.assertRaisesRegex(ValueError, "sweep_top"):
            self._load(good + "sweep_top = 1\n")
        with self.assertRaisesRegex(ValueError, "planned_days"):
            self._load(good.replace("planned_days = 2", "planned_days = 0"))
        with self.assertRaisesRegex(ValueError, "stale_days"):
            self._load(good.replace("stale_days = 3", "stale_days = -1"))
        with self.assertRaisesRegex(ValueError, "stale_days"):
            self._load("[sweep]\nsponsorship_top_n = 10\nplanned_days = 2\n")
        with self.assertRaisesRegex(ValueError, "sponsorship_top_n"):
            self._load(good.replace("sponsorship_top_n = 10", "sponsorship_top_n = 1.5"))


if __name__ == "__main__":
    unittest.main()
