import tempfile
import unittest
from pathlib import Path

from outreach.plan.config import CONFIG_PATH, default_plan_config, load_plan_config


def _variant(old: str, new: str) -> Path:
    text = CONFIG_PATH.read_text(encoding="utf-8")
    assert old in text, old
    path = Path(tempfile.mkdtemp(prefix="plan-config-test-")) / "config.toml"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return path


class PlanConfigTest(unittest.TestCase):
    def test_config_loads_every_number_named_here(self):
        c = load_plan_config()
        want = {
            "linkedin": {"plan_quota": 150, "hard_total": 200, "per_day": 7, "rotate_days": 14,
                         "rotate_top_n": 150, "max_people_per_company": 3},
            "lanes": {"role": ["level_role", "intern_junior"], "reason": ["reason_no_role"],
                      "fill": ["any_us_role"], "role_share": 0.8, "reason_share": 0.2},
            "email": {"start_limit": 20, "rows_max": 15, "people_per_company": 2, "hn_post_days": 90},
            "plan": {"timezone": "America/New_York", "scan_limit": 400, "board_read_top_n": 30,
                     "roll_forward_days": 5, "propose_people": False},
            "people": {"founder_max_headcount": 25, "employer_fresh_days": 30,
                       "generic_mailboxes": ["info", "hello", "contact", "support", "sales", "press", "admin",
                                             "team", "careers", "jobs", "hr", "recruiting", "noreply", "no-reply"],
                       "search_hint": "software engineer"},
            "prepare": {"enabled": False, "multiple": 2.0, "retry_days": 7, "max_candidates": 2, "model": "claude-sonnet-5-5",
                        "call_timeout_s": 600, "max_consecutive_failures": 2,
                        "session_limit_patterns": ["usage limit", "rate limit"],
                        "auth_patterns": ["not logged in", "please run /login"], "schema_version": 1},
            "note": {"max_chars": 300, "max_links": 2, "banned_scopes": ["both", "dm"],
                     "extra_banned": ["can you refer", "could you refer", "would you refer", "refer me",
                                      "ms from northeastern", "finishing my ms", "graduating from northeastern",
                                      "mutual connection"],
                     "banned_path": "~/Desktop/DevVault/job-search/outreach/banned_phrases.txt"},
        }
        for section, keys in want.items():
            for key, value in keys.items():
                self.assertEqual(c.get(section, key), value, f"{section}.{key}")
        self.assertIsInstance(c.raw, bytes)
        self.assertIs(default_plan_config(), default_plan_config())

    def test_missing_key_names_it(self):
        with self.assertRaises(KeyError) as ctx:
            default_plan_config().get("linkedin", "nope")
        self.assertIn("linkedin.nope", str(ctx.exception))
        with self.assertRaises(KeyError) as ctx:
            default_plan_config().get("nosuch", "x")
        self.assertIn("nosuch.x", str(ctx.exception))

    def test_missing_key_in_the_file_names_it(self):
        with self.assertRaises(KeyError) as ctx:
            load_plan_config(_variant("role_share = 0.8\n", ""))
        self.assertIn("lanes.role_share", str(ctx.exception))

    def test_shares_must_sum_to_one(self):
        with self.assertRaises(ValueError):
            load_plan_config(_variant("reason_share = 0.2", "reason_share = 0.3"))

    def test_scan_limit_must_cover_rotate_top_n(self):
        with self.assertRaises(ValueError):
            load_plan_config(_variant("scan_limit = 400", "scan_limit = 100"))

    def test_note_limit_range(self):
        for bad in (99, 301):
            with self.assertRaises(ValueError):
                load_plan_config(_variant("max_chars = 300", f"max_chars = {bad}"))
        for good in (100, 300):
            load_plan_config(_variant("max_chars = 300", f"max_chars = {good}"))

    def test_unknown_timezone_refused(self):
        with self.assertRaises(ValueError):
            load_plan_config(_variant('"America/New_York"', '"Mars/Olympus"'))

    def test_lane_in_two_groups_refused(self):
        with self.assertRaises(ValueError):
            load_plan_config(_variant('fill = ["any_us_role"]', 'fill = ["any_us_role", "reason_no_role"]'))

    def test_company_first_switches_ship_off(self):
        # #9 company-first ruling (2026-10-26): no night prepare, no person rows in the plan.
        c = load_plan_config()
        self.assertIs(c.get("prepare", "enabled"), False)
        self.assertIs(c.get("plan", "propose_people"), False)
        self.assertIs(c.get("prepare", "deny_rule_checked"), True)  # a separate fact, kept as it was

    def test_company_first_switches_must_be_booleans(self):
        for old, new, name in (("enabled = false", 'enabled = "false"', "prepare.enabled"),
                               ("enabled = false", "enabled = 0", "prepare.enabled"),
                               ("propose_people = false", "propose_people = 1", "plan.propose_people"),
                               ("deny_rule_checked = true", 'deny_rule_checked = "yes"', "prepare.deny_rule_checked")):
            with self.assertRaises(ValueError) as ctx:
                load_plan_config(_variant(old, new))
            self.assertIn(name, str(ctx.exception))
        self.assertIs(load_plan_config(_variant("enabled = false", "enabled = true")).get("prepare", "enabled"), True)

    def test_company_first_switches_missing_name_themselves(self):
        for old, name in (("enabled = false\n", "prepare.enabled"), ("propose_people = false\n", "plan.propose_people"),
                          ("deny_rule_checked = true\n", "prepare.deny_rule_checked")):
            with self.assertRaises(KeyError) as ctx:
                load_plan_config(_variant(old, ""))
            self.assertIn(name, str(ctx.exception))

    def test_no_dashes_in_config(self):
        text = CONFIG_PATH.read_text(encoding="utf-8")
        self.assertFalse(any(ord(ch) in (0x2013, 0x2014) for ch in text))


if __name__ == "__main__":
    unittest.main()
