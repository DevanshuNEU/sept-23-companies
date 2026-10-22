import dataclasses
import re
import tempfile
import unittest
from datetime import date, time
from pathlib import Path

from outreach.sending.config import DEFAULT_PATH, HARD_CEILING, SendConfig, load_config


def config_with(**changes) -> Path:
    """A copy of config.toml with some single-line keys replaced by TOML text, in a scratch folder."""
    text = DEFAULT_PATH.read_text(encoding="utf-8")
    for key, toml_value in changes.items():
        text, n = re.subn(rf"^{key} = .*$", f"{key} = {toml_value}", text, flags=re.M)
        assert n == 1, key
    folder = Path(tempfile.mkdtemp(prefix="outreach-sending-config-"))
    path = folder / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


class SendConfigTest(unittest.TestCase):
    def refused(self, key, **changes):
        with self.assertRaises(ValueError) as cm:
            load_config(config_with(**changes))
        self.assertIn(key, str(cm.exception))

    def test_defaults_are_the_nine_numbers(self):
        cfg = load_config()
        self.assertEqual((cfg.ramp_start, cfg.ramp_step, cfg.ramp_ceiling, cfg.ramp_floor), (20, 10, 100, 20))
        self.assertEqual((cfg.clean_bounce_rate, cfg.bounce_days), (0.02, 3))
        self.assertEqual((cfg.window_start, cfg.window_end), (time(8, 30), time(16, 30)))
        self.assertEqual(cfg.weekdays, (0, 1, 2, 3, 4))
        self.assertEqual((cfg.per_company_daily, cfg.per_company_gap_minutes), (3, 60))
        self.assertEqual(cfg.followup_business_days, (7,))
        self.assertEqual(cfg.gap_minutes, (4, 9))
        self.assertEqual(cfg.holidays, ())
        self.assertEqual(cfg.day_tz, "America/New_York")
        self.assertEqual(HARD_CEILING, 100)

    def test_every_other_default_matches_the_plan(self):
        cfg = load_config()
        self.assertEqual(
            (cfg.hard_bounce_rate, cfg.hard_bounce_last_n, cfg.hard_bounce_min_sends,
             cfg.stop_replies_hard, cfg.stop_replies_days),
            (0.03, 100, 10, 2, 7))
        self.assertEqual(
            (cfg.followup_lapse_business_days, cfg.close_after_business_days, cfg.reconcile_after_minutes,
             cfg.max_send_attempts, cfg.rethread_days, cfg.inbound_meta_cap, cfg.prune_noop_after_days),
            (5, 7, 10, 2, 45, 200, 14))
        self.assertEqual(cfg.old_sender_label, "com.devanshu.outreach-mail-sender")

    def test_config_is_frozen_and_has_every_field(self):
        cfg = load_config()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            cfg.ramp_start = 50
        self.assertIsInstance(cfg, SendConfig)
        self.assertEqual(len(dataclasses.fields(SendConfig)), 30)

    def test_holidays_load_as_dates(self):
        cfg = load_config(config_with(holidays='["2026-11-26", "2026-12-25"]'))
        self.assertEqual(cfg.holidays, (date(2026, 11, 26), date(2026, 12, 25)))

    def test_ceiling_above_100_is_refused(self):
        self.refused("ramp_ceiling", ramp_ceiling="101")

    def test_ramp_start_and_floor_must_sit_inside_the_ramp(self):
        self.refused("ramp_start", ramp_start="10")
        self.refused("ramp_start", ramp_start="110", ramp_ceiling="100")
        self.refused("ramp_floor", ramp_floor="0")
        self.refused("ramp_floor", ramp_floor="110")
        self.assertEqual(load_config(config_with(ramp_floor="10")).ramp_floor, 10)

    def test_four_follow_ups_are_refused(self):
        self.refused("followup_business_days", followup_business_days="[3, 7, 15, 20]")

    def test_no_follow_ups_is_refused(self):
        self.refused("followup_business_days", followup_business_days="[]")

    def test_follow_up_days_must_increase(self):
        self.refused("followup_business_days", followup_business_days="[7, 7]")
        self.refused("followup_business_days", followup_business_days="[15, 7]")
        self.assertEqual(load_config(config_with(followup_business_days="[3, 7, 15]")).followup_business_days,
                         (3, 7, 15))

    def test_inverted_window_is_refused(self):
        self.refused("window_start", window_start='"17:00"')
        self.refused("window_start", window_end='"08:30"')

    def test_inverted_gap_is_refused(self):
        self.refused("gap_minutes", gap_minutes="[9, 4]")

    def test_weekday_outside_zero_to_six_is_refused(self):
        self.refused("weekdays", weekdays="[0, 1, 2, 3, 7]")
        self.refused("weekdays", weekdays="[-1, 0]")
        self.refused("weekdays", weekdays="[]")

    def test_from_address_is_the_one_sender(self):
        cfg = load_config()
        self.assertEqual(cfg.from_address, "devanshurajesh@gmail.com")
        self.assertEqual(cfg.from_name, "Devanshu Chicholikar")

    def test_unknown_or_missing_key_is_refused(self):
        folder = Path(tempfile.mkdtemp(prefix="outreach-sending-config-"))
        text = DEFAULT_PATH.read_text(encoding="utf-8")
        path = folder / "extra.toml"
        path.write_text(text + "\nbogus_key = 1\n", encoding="utf-8")
        with self.assertRaises(ValueError) as cm:
            load_config(path)
        self.assertIn("bogus_key", str(cm.exception))
        path = folder / "missing.toml"
        path.write_text(re.sub(r"^ramp_step = .*$", "", text, flags=re.M), encoding="utf-8")
        with self.assertRaises(ValueError) as cm:
            load_config(path)
        self.assertIn("ramp_step", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
