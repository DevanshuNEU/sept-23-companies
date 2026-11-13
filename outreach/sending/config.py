"""The sending engine's numbers: `config.toml` loaded into a frozen `SendConfig`.

`load_config` refuses a config that breaks one of #9's limits and names the key that does.
"""
import tomllib
from dataclasses import dataclass, fields
from datetime import date, time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from outreach.drafts.followups import MAX_FOLLOWUPS

HARD_CEILING = 200  # #9 (2026-11-12, #227); also the send_day.send_limit CHECK (migration 0012)
DEFAULT_PATH = Path(__file__).with_name("config.toml")


KINDS = ("gmail", "mailapp")
LANES = ("sponsor", "general")


@dataclass(frozen=True)
class AccountConfig:
    """One sender account: where it sends from, which lane it serves and its own daily ramp."""
    address: str
    kind: Literal["gmail", "mailapp"]
    lane: Literal["sponsor", "general"] | None
    sends: bool
    ramp_start: int
    ramp_step: int
    ramp_floor: int
    ramp_ceiling: int
    mailapp_account: str | None  # Mail.app account name, mailapp only
    token_dir: str | None  # gmail only; None means the default token folder (account 1)


@dataclass(frozen=True)
class SendConfig:
    accounts: tuple[AccountConfig, ...]
    from_name: str
    day_tz: str
    spread_jitter: tuple[float, float]
    spread_gap_minutes: tuple[int, int]
    clean_bounce_rate: float
    bounce_days: int
    stop_replies_hard: int
    stop_replies_days: int
    window_start: time
    window_end: time
    weekdays: tuple[int, ...]
    holidays: tuple[date, ...]
    per_company_daily: int
    per_company_gap_minutes: int
    break_chance: float
    break_minutes: tuple[float, float]
    pick_pool: int
    day_start_jitter_minutes: int
    per_company_gap_jitter_minutes: int
    followup_business_days: tuple[int, ...]
    followup_lapse_business_days: int
    close_after_business_days: int
    reconcile_after_minutes: int
    max_send_attempts: int
    rethread_days: int
    inbound_meta_cap: int
    old_sender_label: str
    prune_noop_after_days: int
    size_band_up_to: tuple[int, ...]
    size_band_daily: tuple[int, ...]
    size_band_gap_minutes: tuple[int, ...]
    size_band_gap_jitter_minutes: tuple[int, ...]
    size_guessed_big_band: int

    @property
    def from_address(self) -> str:
        """compat until Task A10: the first account block's address, whatever its `sends` says."""
        return self.accounts[0].address

    def account(self, address: str) -> AccountConfig:
        for acct in self.accounts:
            if acct.address == address:
                return acct
        raise KeyError(address)

    def lane_account(self, lane: str) -> AccountConfig | None:
        """The sending account for a lane, or None when no account in it sends."""
        for acct in self.accounts:
            if acct.lane == lane and acct.sends:
                return acct
        return None


# Fields that are not top-level keys in config.toml: `accounts` comes from [[account]] blocks (each carries its ramp).
DERIVED_FIELDS = ("accounts",)


# Whole numbers that count things, so zero or less makes no sense.
COUNT_KEYS = ("bounce_days", "stop_replies_hard",
              "stop_replies_days", "per_company_daily", "followup_lapse_business_days", "close_after_business_days",
              "reconcile_after_minutes", "max_send_attempts", "rethread_days", "inbound_meta_cap",
              "prune_noop_after_days", "pick_pool")
# Whole numbers that may be zero.
WHOLE_KEYS = ("per_company_gap_minutes", "day_start_jitter_minutes", "per_company_gap_jitter_minutes")
RATE_KEYS = ("clean_bounce_rate", "break_chance")
TEXT_KEYS = ("from_name", "day_tz", "old_sender_label")


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _types(raw: dict) -> None:
    """Refuse a value of the wrong type or outside its plain range, naming the key."""
    for key in COUNT_KEYS:
        if not _whole(raw[key]) or raw[key] < 1:
            raise ValueError(f"{key} must be a whole number of at least 1, not {raw[key]!r}")
    for key in WHOLE_KEYS:
        if not _whole(raw[key]) or raw[key] < 0:
            raise ValueError(f"{key} must be a whole number, 0 or more, not {raw[key]!r}")
    for key in RATE_KEYS:
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < 1:
            raise ValueError(f"{key} must be a number between 0 and 1 (not 0 or 1), not {value!r}")
    for key in TEXT_KEYS:
        if not isinstance(raw[key], str) or not raw[key].strip():
            raise ValueError(f"{key} must be non-empty text, not {raw[key]!r}")
    try:
        ZoneInfo(raw["day_tz"])
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValueError(f"day_tz must be a time zone this computer knows, not {raw['day_tz']!r}") from None


def _time(key: str, value) -> time:
    try:
        parsed = time.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a time like \"08:30\", not {value!r}") from None
    if parsed.tzinfo is not None:
        raise ValueError(f"{key} is a time on the recipient's clock and takes no zone, not {value!r}")
    return parsed


def _date(key: str, value) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must hold dates like \"2026-11-26\", not {value!r}") from None


def _ints(key: str, value) -> tuple[int, ...]:
    if not isinstance(value, list) or not all(isinstance(v, int) and not isinstance(v, bool) for v in value):
        raise ValueError(f"{key} must be a list of whole numbers, not {value!r}")
    return tuple(value)


ACCOUNT_REQUIRED = ("address", "kind", "sends", "ramp_start", "ramp_step", "ramp_floor", "ramp_ceiling")
ACCOUNT_OPTIONAL = ("lane", "mailapp_account", "token_dir")


def _account(index: int, raw: dict) -> AccountConfig:
    """One [[account]] block into an AccountConfig, naming the bad key and the block."""
    where = f"[[account]] #{index + 1}"
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be a table, not {raw!r}")
    for key in sorted(raw.keys() - set(ACCOUNT_REQUIRED) - set(ACCOUNT_OPTIONAL)):
        raise ValueError(f"{key} is not an account setting ({where})")
    for key in ACCOUNT_REQUIRED:
        if key not in raw:
            raise KeyError(f"{key} is missing from {where}")
    address = raw["address"]
    if not isinstance(address, str) or not address.strip():
        raise ValueError(f"address must be non-empty text, not {address!r} ({where})")
    where = f"account {address}"
    if raw["kind"] not in KINDS:
        raise ValueError(f"kind must be one of {list(KINDS)}, not {raw['kind']!r} ({where})")
    lane = raw.get("lane")
    if lane is not None and lane not in LANES:
        raise ValueError(f"lane must be one of {list(LANES)} or unset, not {lane!r} ({where})")
    if not isinstance(raw["sends"], bool):
        raise ValueError(f"sends must be true or false, not {raw['sends']!r} ({where})")
    for key in ("ramp_start", "ramp_step", "ramp_floor", "ramp_ceiling"):
        if not _whole(raw[key]) or raw[key] < 1:
            raise ValueError(f"{key} must be a whole number of at least 1, not {raw[key]!r} ({where})")
    for key in ("mailapp_account", "token_dir"):
        value = raw.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{key} must be non-empty text when set, not {value!r} ({where})")
    if raw["kind"] == "mailapp" and raw.get("mailapp_account") is None:
        raise ValueError(f"mailapp_account is needed when kind is \"mailapp\" ({where})")
    if raw["kind"] == "gmail" and raw.get("mailapp_account") is not None:
        raise ValueError(f"mailapp_account is for kind \"mailapp\" only ({where})")
    if raw["kind"] == "mailapp" and raw.get("token_dir") is not None:
        raise ValueError(f"token_dir is for kind \"gmail\" only ({where})")
    acct = AccountConfig(address=address, kind=raw["kind"], lane=lane, sends=raw["sends"],
                         ramp_start=raw["ramp_start"], ramp_step=raw["ramp_step"], ramp_floor=raw["ramp_floor"],
                         ramp_ceiling=raw["ramp_ceiling"], mailapp_account=raw.get("mailapp_account"),
                         token_dir=raw.get("token_dir"))
    if not 0 < acct.ramp_ceiling <= HARD_CEILING:
        raise ValueError(f"ramp_ceiling must be 1 to {HARD_CEILING} (#9's hard ceiling), not {acct.ramp_ceiling} "
                         f"({where})")
    if acct.ramp_floor > acct.ramp_ceiling:
        raise ValueError(f"ramp_floor must be 1 to ramp_ceiling ({acct.ramp_ceiling}), not {acct.ramp_floor} ({where})")
    if not acct.ramp_floor <= acct.ramp_start <= acct.ramp_ceiling:
        raise ValueError(f"ramp_start must be ramp_floor..ramp_ceiling ({acct.ramp_floor}..{acct.ramp_ceiling}), "
                         f"not {acct.ramp_start} ({where})")
    return acct


def _accounts(raw) -> tuple[AccountConfig, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("account must hold at least one [[account]] block")
    accounts = tuple(_account(i, block) for i, block in enumerate(raw))
    seen = set()
    for acct in accounts:
        if acct.address in seen:
            raise ValueError(f"address {acct.address} is on two [[account]] blocks")
        seen.add(acct.address)
    for lane in LANES:
        sending = [a.address for a in accounts if a.lane == lane and a.sends]
        if len(sending) > 1:
            raise ValueError(f"lane {lane} has {len(sending)} sending accounts ({', '.join(sending)}), at most 1")
    return accounts


def _pair(key: str, value, kind, low_min) -> tuple:
    if (not isinstance(value, list) or len(value) != 2
            or not all(isinstance(v, kind) and not isinstance(v, bool) for v in value)):
        raise ValueError(f"{key} must be [low, high], not {value!r}")
    low, high = value
    if low <= low_min or low > high:
        raise ValueError(f"{key} must be [low, high] with {low_min} < low <= high, not {value!r}")
    return (low, high)


def _check(cfg: SendConfig) -> None:
    days = cfg.followup_business_days
    if not days:
        raise ValueError("followup_business_days must name at least one day")
    if len(days) > MAX_FOLLOWUPS:
        raise ValueError(f"followup_business_days has {len(days)} entries, at most {MAX_FOLLOWUPS} (message.seq)")
    if days[0] < 1 or any(b <= a for a, b in zip(days, days[1:])):
        raise ValueError(f"followup_business_days must be positive and increasing, not {list(days)}")
    if cfg.window_start >= cfg.window_end:
        raise ValueError(f"window_start ({cfg.window_start}) must be before window_end ({cfg.window_end})")
    break_low, break_high = cfg.break_minutes
    if break_low < 0 or break_low > break_high:
        raise ValueError(f"break_minutes must be [low, high] with 0 <= low <= high, not {list(cfg.break_minutes)}")
    _check_bands(cfg)
    if not cfg.weekdays or any(not 0 <= d <= 6 for d in cfg.weekdays):
        raise ValueError(f"weekdays must be a non-empty list of 0 (Monday) to 6 (Sunday), not {list(cfg.weekdays)}")


BAND_KEYS = ("size_band_up_to", "size_band_daily", "size_band_gap_minutes", "size_band_gap_jitter_minutes")


def _check_bands(cfg: SendConfig) -> None:
    """The size bands line up (#229): increasing bounds of at least 1, one more band than bounds in each other
    column, a daily number of at least 1, gaps of 0 or more, and a guessed-big band that exists."""
    bounds = cfg.size_band_up_to
    if any(b < 1 for b in bounds) or any(b <= a for a, b in zip(bounds, bounds[1:])):
        raise ValueError(f"size_band_up_to must be increasing sizes of at least 1, not {list(bounds)}")
    bands = len(bounds) + 1
    for key in BAND_KEYS[1:]:
        if len(getattr(cfg, key)) != bands:
            raise ValueError(f"{key} must have {bands} entries (one more than size_band_up_to), "
                             f"not {list(getattr(cfg, key))}")
    if any(n < 1 for n in cfg.size_band_daily):
        raise ValueError(f"size_band_daily must be at least 1 in every band, not {list(cfg.size_band_daily)}")
    for key in ("size_band_gap_minutes", "size_band_gap_jitter_minutes"):
        if any(n < 0 for n in getattr(cfg, key)):
            raise ValueError(f"{key} must be 0 or more in every band, not {list(getattr(cfg, key))}")
    if not _whole(cfg.size_guessed_big_band) or not 1 <= cfg.size_guessed_big_band <= bands:
        raise ValueError(f"size_guessed_big_band must be a band from 1 to {bands}, not {cfg.size_guessed_big_band!r}")


def load_config(path: Path = DEFAULT_PATH) -> SendConfig:
    """Read the TOML file at `path` into a SendConfig. Raises ValueError naming the key that is wrong, missing or
    unknown."""
    with open(path, "rb") as handle:
        raw = tomllib.load(handle)
    names = {f.name for f in fields(SendConfig)} - set(DERIVED_FIELDS) | {"account"}
    for key in sorted(raw.keys() - names):
        raise ValueError(f"{key} is not a sending setting")
    for key in sorted(names - raw.keys()):
        raise ValueError(f"{key} is missing from the sending config")
    _types(raw)
    values = dict(raw)
    del values["account"]
    values["accounts"] = _accounts(raw["account"])
    values["spread_jitter"] = _pair("spread_jitter", raw["spread_jitter"], (int, float), 0)
    values["spread_gap_minutes"] = _pair("spread_gap_minutes", raw["spread_gap_minutes"], int, 0)
    values["window_start"] = _time("window_start", raw["window_start"])
    values["window_end"] = _time("window_end", raw["window_end"])
    values["weekdays"] = _ints("weekdays", raw["weekdays"])
    values["followup_business_days"] = _ints("followup_business_days", raw["followup_business_days"])
    for key in BAND_KEYS:
        values[key] = _ints(key, raw[key])
    holidays = raw["holidays"]
    if not isinstance(holidays, list):
        raise ValueError(f"holidays must be a list of dates, not {holidays!r}")
    values["holidays"] = tuple(_date("holidays", h) for h in holidays)
    breaks = raw["break_minutes"]
    if not isinstance(breaks, list) or len(breaks) != 2 or not all(isinstance(b, (int, float)) for b in breaks):
        raise ValueError(f"break_minutes must be [low, high], not {breaks!r}")
    values["break_minutes"] = (breaks[0], breaks[1])
    cfg = SendConfig(**values)
    _check(cfg)
    return cfg
