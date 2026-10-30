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
            (cfg.stop_replies_hard, cfg.stop_replies_days),
            (2, 7))
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
        self.assertEqual(len(dataclasses.fields(SendConfig)), 27)

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

    def test_counts_must_be_at_least_one(self):
        for key in ("bounce_days", "per_company_daily", "ramp_step",
                    "stop_replies_hard", "stop_replies_days", "followup_lapse_business_days",
                    "close_after_business_days", "max_send_attempts", "rethread_days", "inbound_meta_cap",
                    "prune_noop_after_days", "reconcile_after_minutes"):
            with self.subTest(key=key):
                self.refused(key, **{key: "0"})
                self.refused(key, **{key: "-1"})

    def test_rates_sit_strictly_between_0_and_1(self):
        for key in ("clean_bounce_rate",):
            for bad in ("0", "0.0", "1", "1.5", "-0.1"):
                with self.subTest(key=key, bad=bad):
                    self.refused(key, **{key: bad})
        self.assertEqual(load_config(config_with(clean_bounce_rate="0.5")).clean_bounce_rate, 0.5)

    def test_values_of_the_wrong_type_name_their_key(self):
        self.refused("bounce_days", bounce_days='"three"')
        self.refused("bounce_days", bounce_days="3.5")
        self.refused("bounce_days", bounce_days="true")
        self.refused("clean_bounce_rate", clean_bounce_rate='"low"')
        self.refused("clean_bounce_rate", clean_bounce_rate="false")
        self.refused("from_address", from_address="5")
        self.refused("from_address", from_address='""')
        self.refused("day_tz", day_tz="5")
        self.refused("old_sender_label", old_sender_label="[]")

    def test_a_time_zone_that_does_not_load_is_refused(self):
        self.refused("day_tz", day_tz='"Mars/Olympus"')

    def test_a_window_time_with_a_zone_is_refused(self):
        self.refused("window_start", window_start='"08:30+01:00"')
        self.refused("window_end", window_end='"16:30Z"')

    def test_a_refusal_says_what_is_wrong(self):
        with self.assertRaises(ValueError) as cm:
            load_config(config_with(bounce_days="0"))
        self.assertIn("at least 1", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            load_config(config_with(clean_bounce_rate="1.5"))
        self.assertIn("between 0 and 1", str(cm.exception))

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
