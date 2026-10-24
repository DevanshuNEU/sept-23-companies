"""Loads outreach/review/config.toml: the three numbers the Needs you sweep uses."""
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("config.toml")

_KEYS = ("sponsorship_top_n", "planned_days", "stale_days")


@dataclass(frozen=True)
class ReviewConfig:
    sponsorship_top_n: int
    planned_days: int
    stale_days: int


def load_review_config(path: Path | str | None = None) -> ReviewConfig:
    """Read the config. An unknown key, a missing key or a value under 1 is a ValueError naming it."""
    where = Path(path) if path is not None else CONFIG_PATH
    data = tomllib.loads(where.read_text(encoding="utf-8"))
    unknown_sections = sorted(set(data) - {"sweep"})
    if unknown_sections:
        raise ValueError(f"unknown section [{unknown_sections[0]}] in {where.name}")
    sweep = data.get("sweep")
    if not isinstance(sweep, dict):
        raise ValueError(f"[sweep] is missing from {where.name}")
    for key in sweep:
        if key not in _KEYS:
            raise ValueError(f"unknown key {key!r} in [sweep] of {where.name}")
    values = {}
    for key in _KEYS:
        if key not in sweep:
            raise ValueError(f"[sweep] {key} is missing from {where.name}")
        value = sweep[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"[sweep] {key} must be a whole number of 1 or more, not {value!r}")
        values[key] = value
    return ReviewConfig(**values)
