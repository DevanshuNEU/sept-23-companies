"""The sending engine's numbers: `config.toml` loaded into a frozen `SendConfig`.

`load_config` refuses a config that breaks one of #9's limits and names the key that does.
"""
import tomllib
from dataclasses import dataclass, fields
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from outreach.drafts.followups import MAX_FOLLOWUPS

HARD_CEILING = 200  # #9 (2026-11-12, #227); also the send_day.send_limit CHECK (migration 0012)
DEFAULT_PATH = Path(__file__).with_name("config.toml")


@dataclass(frozen=True)
class SendConfig:
    from_address: str
    from_name: str
    day_tz: str
    ramp_start: int
    ramp_step: int
    ramp_ceiling: int
    ramp_floor: int
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
    gap_minutes: tuple[float, float]
    gap_median_minutes: float
    gap_spread: float
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


# Whole numbers that count things, so zero or less makes no sense.
COUNT_KEYS = ("ramp_step", "bounce_days", "stop_replies_hard",
              "stop_replies_days", "per_company_daily", "followup_lapse_business_days", "close_after_business_days",
              "reconcile_after_minutes", "max_send_attempts", "rethread_days", "inbound_meta_cap",
              "prune_noop_after_days", "pick_pool")
# Whole numbers that may be zero.
WHOLE_KEYS = ("ramp_start", "ramp_ceiling", "ramp_floor", "per_company_gap_minutes", "day_start_jitter_minutes",
              "per_company_gap_jitter_minutes")
RATE_KEYS = ("clean_bounce_rate", "break_chance")
TEXT_KEYS = ("from_address", "from_name", "day_tz", "old_sender_label")


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


def _check(cfg: SendConfig) -> None:
    if not 0 < cfg.ramp_ceiling <= HARD_CEILING:
        raise ValueError(f"ramp_ceiling must be 1 to {HARD_CEILING} (#9's hard ceiling), not {cfg.ramp_ceiling}")
    if cfg.ramp_floor < 1 or cfg.ramp_floor > cfg.ramp_ceiling:
        raise ValueError(f"ramp_floor must be 1 to ramp_ceiling ({cfg.ramp_ceiling}), not {cfg.ramp_floor}")
    if not cfg.ramp_floor <= cfg.ramp_start <= cfg.ramp_ceiling:
        raise ValueError(f"ramp_start must be ramp_floor..ramp_ceiling ({cfg.ramp_floor}..{cfg.ramp_ceiling}), "
                         f"not {cfg.ramp_start}")
    days = cfg.followup_business_days
    if not days:
        raise ValueError("followup_business_days must name at least one day")
    if len(days) > MAX_FOLLOWUPS:
        raise ValueError(f"followup_business_days has {len(days)} entries, at most {MAX_FOLLOWUPS} (message.seq)")
    if days[0] < 1 or any(b <= a for a, b in zip(days, days[1:])):
        raise ValueError(f"followup_business_days must be positive and increasing, not {list(days)}")
    if cfg.window_start >= cfg.window_end:
        raise ValueError(f"window_start ({cfg.window_start}) must be before window_end ({cfg.window_end})")
    low, high = cfg.gap_minutes
    if low < 0 or low > high:
        raise ValueError(f"gap_minutes must be [low, high] with 0 <= low <= high, not {list(cfg.gap_minutes)}")
    if cfg.gap_median_minutes <= 0 or cfg.gap_spread < 0:
        raise ValueError("gap_median_minutes must be above 0 and gap_spread 0 or more, not "
                         f"{cfg.gap_median_minutes} and {cfg.gap_spread}")
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
    names = {f.name for f in fields(SendConfig)}
    for key in sorted(raw.keys() - names):
        raise ValueError(f"{key} is not a sending setting")
    for key in sorted(names - raw.keys()):
        raise ValueError(f"{key} is missing from the sending config")
    _types(raw)
    values = dict(raw)
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
    gap = raw["gap_minutes"]
    if not isinstance(gap, list) or len(gap) != 2 or not all(isinstance(g, (int, float)) for g in gap):
        raise ValueError(f"gap_minutes must be [low, high], not {gap!r}")
    values["gap_minutes"] = (gap[0], gap[1])
    for key in ("gap_median_minutes", "gap_spread"):
        if isinstance(raw[key], bool) or not isinstance(raw[key], (int, float)):
            raise ValueError(f"{key} must be a number, not {raw[key]!r}")
    breaks = raw["break_minutes"]
    if not isinstance(breaks, list) or len(breaks) != 2 or not all(isinstance(b, (int, float)) for b in breaks):
        raise ValueError(f"break_minutes must be [low, high], not {breaks!r}")
    values["break_minutes"] = (breaks[0], breaks[1])
    cfg = SendConfig(**values)
    _check(cfg)
    return cfg
