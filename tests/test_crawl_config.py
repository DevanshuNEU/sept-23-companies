"""Crawler config (issue #21, Task 1)."""
import re
import tempfile
import unittest
from pathlib import Path

from outreach.crawl.config import CONFIG_PATH, CrawlConfig, load_crawl_config
from outreach.net import USER_AGENT


def _load_with(**changes):
    """Load the shipped config with some top-level `key = value` lines replaced (values are TOML text)."""
    text = CONFIG_PATH.read_text(encoding="utf-8")
    for key, value in changes.items():
        text, n = re.subn(rf"(?m)^{key}\s*=.*$", f"{key} = {value}", text)
        assert n == 1, key
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.toml"
        p.write_text(text, encoding="utf-8")
        return load_crawl_config(p)


class CrawlConfigTests(unittest.TestCase):
    def test_shipped_config_has_the_design_values(self):
        cfg = load_crawl_config()
        self.assertIsInstance(cfg, CrawlConfig)
        self.assertEqual((cfg.floor_s, cfg.ceiling_s, cfg.age_divisor), (21600, 604800, 8))
        self.assertEqual((cfg.defer_s, cfg.timeout_s, cfg.max_bytes, cfg.max_redirects),
                         (1800, 30, 20_000_000, 3))
        self.assertEqual((cfg.default_host_interval_s, cfg.breaker_failures), (1.0, 5))
        self.assertEqual((cfg.batch_rows, cfg.decide_batch, cfg.detail_per_read, cfg.boards_per_tick),
                         (2000, 100, 150, 500))
        self.assertEqual((cfg.tick_budget_s, cfg.top_companies), (1500, 300))
        self.assertEqual((cfg.watch_interval_s, cfg.watch_per_tick, cfg.watch_min_text_chars), (604800, 40, 200))
        self.assertEqual(cfg.host_interval_s["greenhouse.io"], 0.25)
        self.assertEqual(cfg.host_interval_s["workable.com"], 2.0)
        self.assertEqual(len(cfg.host_interval_s), 14)

    def test_floor_above_ceiling_is_refused(self):
        with self.assertRaisesRegex(ValueError, "floor_s"):
            _load_with(floor_s=700000)

    def test_batch_rows_outside_2000_to_5000_is_refused(self):
        for n in (1999, 5001):
            with self.assertRaisesRegex(ValueError, "batch_rows must be 2000 to 5000"):
                _load_with(batch_rows=n)
        self.assertEqual(_load_with(batch_rows=5000).batch_rows, 5000)

    def test_user_agent_with_an_email_is_refused(self):
        with self.assertRaisesRegex(ValueError, "user_agent must not carry an email"):
            _load_with(user_agent='"outreach-crawler/1.0 (me@example.com)"')

    def test_missing_key_is_named(self):
        text = CONFIG_PATH.read_text(encoding="utf-8")
        broken = re.sub(r"(?m)^breaker_failures\s*=.*\n", "", text)
        self.assertNotEqual(broken, text)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text(broken, encoding="utf-8")
            with self.assertRaises(KeyError) as ctx:
                load_crawl_config(p)
        self.assertIn("breaker_failures", str(ctx.exception))

    def test_shipped_user_agent_equals_the_shared_one(self):
        self.assertEqual(load_crawl_config().user_agent, USER_AGENT)

    def test_shipped_stale_after_months_is_twelve(self):
        self.assertEqual(load_crawl_config().stale_after_months, 12)

    def test_shipped_body_refetch_days_is_thirty(self):
        self.assertEqual(load_crawl_config().body_refetch_days, 30)

    def test_stale_after_months_outside_1_to_60_is_refused(self):
        for n in (0, 61):
            with self.assertRaisesRegex(ValueError, "stale_after_months must be 1 to 60"):
                _load_with(stale_after_months=n)
        self.assertEqual(_load_with(stale_after_months=60).stale_after_months, 60)
        self.assertEqual(_load_with(stale_after_months=1).stale_after_months, 1)

    def test_counts_that_must_be_positive_refuse_zero(self):
        for key in ("defer_s", "max_bytes", "breaker_failures", "decide_batch", "detail_per_read", "boards_per_tick",
                    "tick_budget_s", "top_companies", "watch_interval_s", "watch_per_tick", "watch_min_text_chars",
                    "age_divisor", "floor_s", "body_refetch_days"):
            for bad in (0, -1):
                with self.assertRaisesRegex(ValueError, f"{key} must be positive"):
                    _load_with(**{key: bad})

    def test_zero_redirects_is_allowed_but_negative_is_not(self):
        self.assertEqual(_load_with(max_redirects=0).max_redirects, 0)
        with self.assertRaisesRegex(ValueError, "max_redirects"):
            _load_with(max_redirects=-1)

    def test_float_values_must_be_finite_and_positive(self):
        for key in ("timeout_s", "default_host_interval_s"):
            for bad in ("nan", "inf", "-inf", "0", "0.0", "-1.5"):
                with self.assertRaisesRegex(ValueError, f"{key} must be a finite number above 0"):
                    _load_with(**{key: bad})

    def test_a_host_interval_must_be_finite_and_positive(self):
        text = CONFIG_PATH.read_text(encoding="utf-8")
        for bad in ("nan", "inf", "0", "-1"):
            broken = text.replace('"greenhouse.io" = 0.25', f'"greenhouse.io" = {bad}')
            self.assertNotEqual(broken, text)
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "config.toml"
                p.write_text(broken, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "host_interval_s"):
                    load_crawl_config(p)


if __name__ == "__main__":
    unittest.main()
