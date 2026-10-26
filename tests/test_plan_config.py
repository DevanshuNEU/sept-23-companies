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
            "linkedin": {"plan_quota": 150, "hard_total": 200, "rotate_days": 14,
                         "rotate_top_n": 150, "max_people_per_company": 8},
            "queue": {"companies_per_day": 8, "requests_per_company": 4},
            "lanes": {"role": ["level_role", "intern_junior"], "reason": ["reason_no_role"],
                      "fill": ["any_us_role"], "role_share": 0.8, "reason_share": 0.2},
            "email": {"start_limit": 20, "rows_max": 15, "people_per_company": 2, "hn_post_days": 90},
            "plan": {"timezone": "America/New_York", "scan_limit": 400, "board_read_top_n": 30,
                     "roll_forward_days": 5, "propose_people": False},
            "people": {"founder_max_headcount": 25, "employer_fresh_days": 30,
                       "generic_mailboxes": ["info", "hello", "contact", "support", "sales", "press", "admin",
                                             "team", "careers", "jobs", "hr", "recruiting", "noreply", "no-reply"]},
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

    def test_route_and_bridge_keys_are_checked(self):
        c = load_plan_config()
        self.assertIs(c.get("route", "prefer_ic_in_role"), True)
        self.assertTrue(c.get("bridge", "contacted_txt").endswith("contacted.txt"))
        for old, new in (("prefer_ic_in_role = true", 'prefer_ic_in_role = "yes"'),
                         ("log_back_days = 3", "log_back_days = 4")):
            with self.subTest(new=new), self.assertRaises(ValueError):
                load_plan_config(_variant(old, new))

    def test_the_queue_is_the_one_source_for_the_day_and_the_wave(self):
        # #9 company-first ruling: about 8 companies a day, one wave of up to 4 people each. The old per-request
        # pace and the route's own wave size are gone, so no second number can disagree.
        c = load_plan_config()
        self.assertEqual((c.get("queue", "companies_per_day"), c.get("queue", "requests_per_company")), (8, 4))
        for section, key in (("linkedin", "per_day"), ("route", "wave_size")):
            with self.subTest(key=key), self.assertRaises(KeyError):
                c.get(section, key)

    def test_queue_keys_are_checked(self):
        for old, new, name in (("companies_per_day = 8", "companies_per_day = 0", "queue.companies_per_day"),
                               ("companies_per_day = 8", "companies_per_day = 31", "queue.companies_per_day"),
                               ("companies_per_day = 8", "companies_per_day = true", "queue.companies_per_day"),
                               ("requests_per_company = 4", "requests_per_company = 0", "queue.requests_per_company"),
                               ("requests_per_company = 4", "requests_per_company = 9", "queue.requests_per_company"),
                               ("requests_per_company = 4", "requests_per_company = 2.5",
                                "queue.requests_per_company"),
                               ("max_people_per_company = 8", "max_people_per_company = 3",
                                "linkedin.max_people_per_company")):
            with self.subTest(new=new), self.assertRaises(ValueError) as ctx:
                load_plan_config(_variant(old, new))
            self.assertIn(name, str(ctx.exception))
        for key in ("companies_per_day", "requests_per_company"):
            with self.subTest(key=key), self.assertRaises(KeyError) as ctx:
                load_plan_config(_variant(f"\n{key} = ", "\nunused = "))
            self.assertIn(f"queue.{key}", str(ctx.exception))
        self.assertEqual(load_plan_config(_variant("companies_per_day = 8", "companies_per_day = 30"))
                         .get("queue", "companies_per_day"), 30)

    def test_no_dashes_in_config(self):
        text = CONFIG_PATH.read_text(encoding="utf-8")
        self.assertFalse(any(ord(ch) in (0x2013, 0x2014) for ch in text))


if __name__ == "__main__":
    unittest.main()
