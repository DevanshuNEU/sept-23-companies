"""Loads outreach/review/config.toml: the numbers the Needs you sweep and the crawler's scope use."""
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("config.toml")

_SECTIONS = {
    "sweep": ("sponsorship_top_n", "planned_days", "stale_days"),
    "needs_you": ("crawler_scope_top", "crawler_cap_per_run"),
}


@dataclass(frozen=True)
class ReviewConfig:
    sponsorship_top_n: int
    planned_days: int
    stale_days: int
    crawler_scope_top: int = 150
    crawler_cap_per_run: int = 10


def load_review_config(path: Path | str | None = None) -> ReviewConfig:
    """Read the config. An unknown section or key, a missing one or a value under 1 is a ValueError naming it."""
    where = Path(path) if path is not None else CONFIG_PATH
    data = tomllib.loads(where.read_text(encoding="utf-8"))
    unknown_sections = sorted(set(data) - set(_SECTIONS))
    if unknown_sections:
        raise ValueError(f"unknown section [{unknown_sections[0]}] in {where.name}")
    values = {}
    for name, keys in _SECTIONS.items():
        section = data.get(name)
        if not isinstance(section, dict):
            raise ValueError(f"[{name}] is missing from {where.name}")
        for key in section:
            if key not in keys:
                raise ValueError(f"unknown key {key!r} in [{name}] of {where.name}")
        for key in keys:
            if key not in section:
                raise ValueError(f"[{name}] {key} is missing from {where.name}")
            value = section[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"[{name}] {key} must be a whole number of 1 or more, not {value!r}")
            values[key] = value
    return ReviewConfig(**values)
