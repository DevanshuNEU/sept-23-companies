import dataclasses
import re
import tempfile
import unittest
from datetime import date, time
from pathlib import Path

from tests import SHIPPED_SEND_CONFIG
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


def accounts_with(**changes) -> Path:
    """A copy of config.toml with a key replaced in every [[account]] block (single-line TOML values)."""
    text = DEFAULT_PATH.read_text(encoding="utf-8")
    for key, toml_value in changes.items():
        text, n = re.subn(rf"^{key} = .*$", f"{key} = {toml_value}", text, flags=re.M)
        assert n >= 1, key
    folder = Path(tempfile.mkdtemp(prefix="outreach-sending-config-"))
    path = folder / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def blocks_text(*blocks: str) -> Path:
    """config.toml with its [[account]] blocks replaced by the given block texts."""
    text = DEFAULT_PATH.read_text(encoding="utf-8")
    head = text[:text.index("# Sender accounts")]
    folder = Path(tempfile.mkdtemp(prefix="outreach-sending-config-"))
    path = folder / "config.toml"
    path.write_text(head + "\n".join(blocks), encoding="utf-8")
    return path


def block(address="a@example.com", kind="gmail", lane=None, sends=True, extra="", ceiling=40):
    lines = [f'[[account]]', f'address = "{address}"', f'kind = "{kind}"']
    if lane:
        lines.append(f'lane = "{lane}"')
    lines += [f'sends = {"true" if sends else "false"}', 'ramp_start = 15', 'ramp_step = 3', 'ramp_floor = 15',
              f'ramp_ceiling = {ceiling}']
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


class SendConfigTest(unittest.TestCase):
    def refused(self, key, **changes):
        with self.assertRaises(ValueError) as cm:
            load_config(config_with(**changes))
        self.assertIn(key, str(cm.exception))

    def test_defaults_are_the_nine_numbers(self):
        cfg = load_config(SHIPPED_SEND_CONFIG)
        self.assertEqual((cfg.clean_bounce_rate, cfg.bounce_days), (0.02, 3))
        self.assertEqual((cfg.window_start, cfg.window_end), (time(8, 0), time(15, 0)))
        self.assertEqual(cfg.weekdays, (0, 1, 2, 3, 4))
        self.assertEqual((cfg.per_company_daily, cfg.per_company_gap_minutes), (2, 60))
        self.assertEqual(cfg.followup_business_days, (7,))
        self.assertEqual((cfg.break_chance, cfg.break_minutes), (0.04, (10, 25)))
        self.assertEqual((cfg.pick_pool, cfg.day_start_jitter_minutes, cfg.per_company_gap_jitter_minutes),
                         (5, 25, 60))
        self.assertEqual(cfg.day_tz, "America/New_York")
        self.assertEqual(HARD_CEILING, 200)

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
            cfg.from_name = "x"
        self.assertIsInstance(cfg, SendConfig)
        self.assertEqual(len(dataclasses.fields(SendConfig)), 34)

    def test_holidays_load_as_dates(self):
        cfg = load_config(config_with(holidays='["2026-11-26", "2026-12-25"]'))
        self.assertEqual(cfg.holidays, (date(2026, 11, 26), date(2026, 12, 25)))

    def test_ceiling_above_200_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            load_config(accounts_with(ramp_ceiling="201"))
        self.assertIn("ramp_ceiling", str(cm.exception))
        cfg = load_config(accounts_with(ramp_ceiling="200", ramp_start="200", ramp_floor="200"))
        self.assertEqual(cfg.account("devanshuchi9@gmail.com").ramp_ceiling, 200)

    def test_ramp_start_and_floor_must_sit_inside_the_ramp(self):
        def acct_refused(key, **changes):
            with self.assertRaises(ValueError) as cm:
                load_config(accounts_with(**changes))
            self.assertIn(key, str(cm.exception))
        acct_refused("ramp_start", ramp_start="10")
        acct_refused("ramp_start", ramp_start="110", ramp_ceiling="100", ramp_floor="100")
        acct_refused("ramp_floor", ramp_floor="0")
        acct_refused("ramp_floor", ramp_floor="210")
        self.assertEqual(load_config(accounts_with(ramp_floor="10")).account("devanshuchi9@gmail.com").ramp_floor, 10)

    def test_five_follow_ups_are_refused(self):
        self.refused("followup_business_days", followup_business_days="[3, 7, 14, 21, 28]")

    def test_no_follow_ups_is_refused(self):
        self.refused("followup_business_days", followup_business_days="[]")

    def test_follow_up_days_must_increase(self):
        self.refused("followup_business_days", followup_business_days="[7, 7]")
        self.refused("followup_business_days", followup_business_days="[15, 7]")
        self.assertEqual(load_config(config_with(followup_business_days="[3, 7, 15]")).followup_business_days,
                         (3, 7, 15))

    def test_inverted_window_is_refused(self):
        self.refused("window_start", window_start='"17:00"')
        self.refused("window_start", window_end='"07:30"')

    def test_inverted_gap_is_refused(self):
        self.refused("break_minutes", break_minutes="[30, 10]")
        self.refused("break_chance", break_chance="1.5")
        self.refused("pick_pool", pick_pool="0")

    def test_weekday_outside_zero_to_six_is_refused(self):
        self.refused("weekdays", weekdays="[0, 1, 2, 3, 7]")
        self.refused("weekdays", weekdays="[-1, 0]")
        self.refused("weekdays", weekdays="[]")

    def test_counts_must_be_at_least_one(self):
        for key in ("bounce_days", "per_company_daily",
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
        with self.assertRaises(ValueError) as cm:
            load_config(accounts_with(address="5"))
        self.assertIn("address", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            load_config(accounts_with(address='""'))
        self.assertIn("address", str(cm.exception))
        self.refused("day_tz", day_tz="5")
        self.refused("old_sender_label", old_sender_label="[]")

    def test_a_time_zone_that_does_not_load_is_refused(self):
        self.refused("day_tz", day_tz='"Mars/Olympus"')

    def test_a_window_time_with_a_zone_is_refused(self):
        self.refused("window_start", window_start='"08:00+01:00"')
        self.refused("window_end", window_end='"15:00Z"')

    def test_a_refusal_says_what_is_wrong(self):
        with self.assertRaises(ValueError) as cm:
            load_config(config_with(bounce_days="0"))
        self.assertIn("at least 1", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            load_config(config_with(clean_bounce_rate="1.5"))
        self.assertIn("between 0 and 1", str(cm.exception))

    def test_there_is_no_single_from_address(self):
        # Every email goes from its own account (Task A10): the first block is account 1, and it rests.
        cfg = load_config()
        self.assertFalse(hasattr(cfg, "from_address"))
        self.assertEqual(cfg.accounts[0].address, "devanshurajesh@gmail.com")
        self.assertFalse(cfg.accounts[0].sends)
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
        path.write_text(re.sub(r"^bounce_days = .*$", "", text, flags=re.M), encoding="utf-8")
        with self.assertRaises(ValueError) as cm:
            load_config(path)
        self.assertIn("bounce_days", str(cm.exception))
        path = folder / "noramp.toml"
        path.write_text(re.sub(r"^ramp_step = .*$", "", text, count=1, flags=re.M), encoding="utf-8")
        with self.assertRaises(KeyError) as cm:
            load_config(path)
        self.assertIn("ramp_step", str(cm.exception))

    def test_three_accounts_parsed(self):
        cfg = load_config(SHIPPED_SEND_CONFIG)
        self.assertEqual([a.address for a in cfg.accounts],
                         ["devanshurajesh@gmail.com", "devanshuchi9@gmail.com", "chicholikar.d@northeastern.edu"])
        one, two, three = cfg.accounts
        self.assertEqual((one.kind, one.lane, one.sends), ("gmail", None, False))
        self.assertEqual((two.kind, two.lane, two.sends), ("gmail", "general", True))
        self.assertEqual((two.ramp_start, two.ramp_step, two.ramp_floor, two.ramp_ceiling), (15, 3, 15, 40))
        self.assertEqual((three.kind, three.lane, three.sends, three.mailapp_account),
                         ("mailapp", "sponsor", False, "Exchange"))
        self.assertEqual((three.ramp_start, three.ramp_step, three.ramp_floor, three.ramp_ceiling), (15, 3, 15, 60))
        self.assertIsNone(one.mailapp_account)
        self.assertEqual(cfg.from_name, "Devanshu Chicholikar")
        self.assertEqual((cfg.spread_jitter, cfg.spread_gap_minutes), ((0.6, 1.4), (2, 45)))
        self.assertIs(cfg.account("devanshuchi9@gmail.com"), two)
        with self.assertRaises(KeyError):
            cfg.account("nobody@example.com")

    def test_two_sending_accounts_in_one_lane_refused(self):
        path = blocks_text(block("a@example.com", lane="general"), block("b@example.com", lane="general"))
        with self.assertRaises(ValueError) as cm:
            load_config(path)
        self.assertIn("general", str(cm.exception))
        load_config(blocks_text(block("a@example.com", lane="general"),
                                block("b@example.com", lane="general", sends=False)))

    def test_mailapp_needs_mailapp_account(self):
        with self.assertRaises(ValueError) as cm:
            load_config(blocks_text(block("a@example.com", kind="mailapp", lane="sponsor")))
        self.assertIn("mailapp_account", str(cm.exception))
        cfg = load_config(blocks_text(block("a@example.com", kind="mailapp", lane="sponsor",
                                            extra='mailapp_account = "Exchange"')))
        self.assertEqual(cfg.accounts[0].mailapp_account, "Exchange")

    def test_bad_account_values_name_their_key(self):
        for bad, key in ((block(kind="smtp"), "kind"), (block(lane="other"), "lane"),
                         (block(extra='bogus = 1'), "bogus")):
            with self.subTest(key=key):
                with self.assertRaises(ValueError) as cm:
                    load_config(blocks_text(bad))
                self.assertIn(key, str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            load_config(blocks_text(block("a@example.com"), block("a@example.com", sends=False)))
        self.assertIn("a@example.com", str(cm.exception))
        with self.assertRaises(ValueError):
            load_config(blocks_text())

    def test_lane_account_skips_non_sending(self):
        cfg = load_config(SHIPPED_SEND_CONFIG)
        self.assertEqual(cfg.lane_account("general").address, "devanshuchi9@gmail.com")
        self.assertIsNone(cfg.lane_account("sponsor"))  # the Northeastern block does not send until Phase B

    def test_window_and_holidays(self):
        cfg = load_config(SHIPPED_SEND_CONFIG)
        self.assertEqual((cfg.window_start, cfg.window_end), (time(8, 0), time(15, 0)))
        self.assertEqual(cfg.holidays, tuple(date.fromisoformat(d) for d in (
            "2026-11-26", "2026-11-27", "2026-12-24", "2026-12-25", "2026-12-31", "2027-01-01")))

    def test_spread_keys_are_checked(self):
        self.refused("spread_jitter", spread_jitter="[1.4, 0.6]")
        self.refused("spread_jitter", spread_jitter="[0, 1.4]")
        self.refused("spread_gap_minutes", spread_gap_minutes="[45, 2]")
        self.refused("spread_gap_minutes", spread_gap_minutes="[0, 45]")
        self.refused("spread_gap_minutes", spread_gap_minutes="[2]")


if __name__ == "__main__":
    unittest.main()
