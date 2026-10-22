"""The sending engine's numbers: `config.toml` loaded into a frozen `SendConfig`.

`load_config` refuses a config that breaks one of #9's limits and names the key that does.
"""
import tomllib
from dataclasses import dataclass, fields
from datetime import date, time
from pathlib import Path

HARD_CEILING = 100  # #9; also the send_day.send_limit CHECK
DEFAULT_PATH = Path(__file__).with_name("config.toml")
MAX_FOLLOWUPS = 3  # message.seq is 0 to 3


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
    hard_bounce_rate: float
    hard_bounce_last_n: int
    hard_bounce_min_sends: int
    stop_replies_hard: int
    stop_replies_days: int
    window_start: time
    window_end: time
    weekdays: tuple[int, ...]
    holidays: tuple[date, ...]
    per_company_daily: int
    per_company_gap_minutes: int
    gap_minutes: tuple[float, float]
    followup_business_days: tuple[int, ...]
    followup_lapse_business_days: int
    close_after_business_days: int
    reconcile_after_minutes: int
    max_send_attempts: int
    rethread_days: int
    inbound_meta_cap: int
    old_sender_label: str
    prune_noop_after_days: int


def _time(key: str, value) -> time:
    try:
        return time.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a time like \"08:30\", not {value!r}") from None


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
    if not cfg.weekdays or any(not 0 <= d <= 6 for d in cfg.weekdays):
        raise ValueError(f"weekdays must be a non-empty list of 0 (Monday) to 6 (Sunday), not {list(cfg.weekdays)}")


def load_config(path: Path = DEFAULT_PATH) -> SendConfig:
    """Read the TOML file at `path` into a SendConfig. Raises ValueError naming the key that is wrong, missing or unknown."""
    with open(path, "rb") as handle:
        raw = tomllib.load(handle)
    names = {f.name for f in fields(SendConfig)}
    for key in sorted(raw.keys() - names):
        raise ValueError(f"{key} is not a sending setting")
    for key in sorted(names - raw.keys()):
        raise ValueError(f"{key} is missing from the sending config")
    values = dict(raw)
    values["window_start"] = _time("window_start", raw["window_start"])
    values["window_end"] = _time("window_end", raw["window_end"])
    values["weekdays"] = _ints("weekdays", raw["weekdays"])
    values["followup_business_days"] = _ints("followup_business_days", raw["followup_business_days"])
    holidays = raw["holidays"]
    if not isinstance(holidays, list):
        raise ValueError(f"holidays must be a list of dates, not {holidays!r}")
    values["holidays"] = tuple(_date("holidays", h) for h in holidays)
    gap = raw["gap_minutes"]
    if not isinstance(gap, list) or len(gap) != 2 or not all(isinstance(g, (int, float)) for g in gap):
        raise ValueError(f"gap_minutes must be [low, high], not {gap!r}")
    values["gap_minutes"] = (gap[0], gap[1])
    cfg = SendConfig(**values)
    _check(cfg)
    return cfg
