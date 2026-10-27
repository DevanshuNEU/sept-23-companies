"""Loads outreach/judge/config.toml into a validated JudgeConfig.

A missing section or key raises KeyError naming it, as the reader's config does, so a typo fails at load instead of
quietly turning a limit off. `enabled` must be a real true or false: only `true` turns the job on.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = ["CONFIG_PATH", "JudgeConfig", "load_judge_config"]

CONFIG_PATH = Path(__file__).with_name("config.toml")

_BOOL_KEYS = {"judge": ("enabled",)}
_STR_KEYS = {"judge": ("model",)}
_INT_KEYS = {
    "judge": ("top_n", "per_run", "cache_days", "schema_version"),
    "fetch": ("timeout_s", "max_bytes", "page_max_chars", "min_text_chars"),
    "run": ("call_timeout_s", "max_consecutive_failures"),
    "eval": ("yes_at",),
}
_LIST_KEYS = {"run": ("session_limit_patterns", "auth_patterns")}


@dataclass(frozen=True)
class JudgeConfig:
    enabled: bool
    model: str
    top_n: int
    per_run: int
    cache_days: int
    schema_version: int
    timeout_s: int
    max_bytes: int
    page_max_chars: int
    min_text_chars: int
    call_timeout_s: int
    max_consecutive_failures: int
    yes_at: int
    session_limit_patterns: tuple[str, ...]
    auth_patterns: tuple[str, ...]


def load_judge_config(path: Path = CONFIG_PATH) -> JudgeConfig:
    path = Path(path)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    values: dict[str, object] = {}
    for kinds in (_BOOL_KEYS, _STR_KEYS, _INT_KEYS, _LIST_KEYS):
        for section, keys in kinds.items():
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
    for keys in _INT_KEYS.values():
        for key in keys:
            value = values[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{key} must be a whole number of at least 1, got {value!r}")
    if values["yes_at"] > 100:
        raise ValueError(f"yes_at is a fit from 1 to 100, got {values['yes_at']!r}")
    if values["per_run"] > values["top_n"]:
        raise ValueError(f"per_run ({values['per_run']}) must not be above top_n ({values['top_n']})")
    for keys in _LIST_KEYS.values():
        for key in keys:
            value = values[key]
            if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
                raise ValueError(f"{key} must be a list of non-empty text")
            values[key] = tuple(value)
    return JudgeConfig(**values)
