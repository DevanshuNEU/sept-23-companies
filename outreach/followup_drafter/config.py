"""Loads outreach/followup_drafter/config.toml into a validated DrafterConfig.

A missing section or key raises KeyError naming it, so a typo fails at load instead of quietly turning a limit off.
`enabled` must be a real true or false.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = ["CONFIG_PATH", "DrafterConfig", "load_drafter_config"]

CONFIG_PATH = Path(__file__).with_name("config.toml")

_KEYS = {
    "drafter": ("enabled", "model", "max_per_run"),
    "run": ("call_timeout_s", "max_consecutive_failures", "session_limit_patterns", "auth_patterns"),
}


@dataclass(frozen=True)
class DrafterConfig:
    enabled: bool
    model: str
    max_per_run: int
    call_timeout_s: int
    max_consecutive_failures: int
    session_limit_patterns: tuple[str, ...]
    auth_patterns: tuple[str, ...]


def load_drafter_config(path: Path = CONFIG_PATH) -> DrafterConfig:
    path = Path(path)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    values: dict[str, object] = {}
    for section, keys in _KEYS.items():
        if section not in data:
            raise KeyError(f"[{section}] is missing from {path.name}")
        for key in keys:
            if key not in data[section]:
                raise KeyError(f"[{section}] {key} is missing from {path.name}")
            values[key] = data[section][key]
    if not isinstance(values["enabled"], bool):
        raise ValueError(f"enabled must be true or false, got {values['enabled']!r}")
    if not isinstance(values["model"], str) or not values["model"].strip():
        raise ValueError("model must be text and not empty")
    for key in ("max_per_run", "call_timeout_s", "max_consecutive_failures"):
        value = values[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} must be a whole number of at least 1, got {value!r}")
    for key in ("session_limit_patterns", "auth_patterns"):
        value = values[key]
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            raise ValueError(f"{key} must be a list of non-empty text")
        values[key] = tuple(value)
    return DrafterConfig(**values)
