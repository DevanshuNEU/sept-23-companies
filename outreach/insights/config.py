"""The Insights screen's numbers: `config.toml` loaded into a frozen `InsightsConfig` (#81)."""
from __future__ import annotations

import functools
import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = ["CONFIG_PATH", "InsightsConfig", "load_insights_config", "default_insights_config"]

CONFIG_PATH = Path(__file__).with_name("config.toml")
_NUMBERS = ("group_min", "share_min_count", "lookalike_pool", "lookalike_count")


@dataclass(frozen=True)
class InsightsConfig:
    group_min: int
    share_min_count: int
    lookalike_pool: int
    lookalike_count: int
    export_dir: Path  # `~` expanded


def load_insights_config(path: Path = CONFIG_PATH) -> InsightsConfig:
    """`KeyError` names a missing key; `ValueError` for a number that is not a whole number of 1 or more."""
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    for key in (*_NUMBERS, "export_dir"):
        if key not in data:
            raise KeyError(f"insights config.toml has no {key}")
    for key in _NUMBERS:
        value = data[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} must be a whole number of 1 or more, got {value!r}")
    if not isinstance(data["export_dir"], str) or not data["export_dir"].strip():
        raise ValueError(f"export_dir must be a folder path, got {data['export_dir']!r}")
    return InsightsConfig(**{k: data[k] for k in _NUMBERS}, export_dir=Path(data["export_dir"]).expanduser())


@functools.cache
def default_insights_config() -> InsightsConfig:
    return load_insights_config()
