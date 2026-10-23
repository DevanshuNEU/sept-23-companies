"""Loads outreach/reader/config.toml into a validated ReaderConfig (#32).

A missing section or key raises KeyError naming it, as `outreach.rules.config` does, so a typo in the TOML fails at
load instead of quietly turning a limit off.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = ["CONFIG_PATH", "PROMPT_PATH", "ReaderConfig", "load_reader_config"]

CONFIG_PATH = Path(__file__).with_name("config.toml")
PROMPT_PATH = Path(__file__).with_name("prompts") / "read_posting.md"

_STR_KEYS = {"reader": ("model", "promoted_version")}
_INT_KEYS = {
    "reader": ("min_body_chars", "batch_size", "lease_minutes", "max_attempts"),
    "priority": ("top_companies",),
    "night": ("max_postings_per_night", "max_batches", "max_consecutive_failures", "call_timeout_s"),
}
_LIST_KEYS = {"night": ("session_limit_patterns", "auth_patterns")}


@dataclass(frozen=True)
class ReaderConfig:
    model: str
    promoted_version: str
    min_body_chars: int
    batch_size: int
    lease_minutes: int
    max_attempts: int
    top_companies: int
    max_postings_per_night: int
    max_batches: int
    max_consecutive_failures: int
    call_timeout_s: int
    session_limit_patterns: tuple[str, ...]
    auth_patterns: tuple[str, ...]
    prompt_path: Path = PROMPT_PATH


def load_reader_config(path: Path = CONFIG_PATH) -> ReaderConfig:
    path = Path(path)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    values: dict[str, object] = {}
    for kinds in (_STR_KEYS, _INT_KEYS, _LIST_KEYS):
        for section, keys in kinds.items():
            if section not in data:
                raise KeyError(f"[{section}] is missing from {path.name}")
            for key in keys:
                if key not in data[section]:
                    raise KeyError(f"[{section}] {key} is missing from {path.name}")
                values[key] = data[section][key]
    for keys in _STR_KEYS.values():
        for key in keys:
            if not isinstance(values[key], str) or (key == "model" and not values[key]):
                raise ValueError(f"{key} must be text{' and not empty' if key == 'model' else ''}")
    for keys in _INT_KEYS.values():
        for key in keys:
            value = values[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{key} must be a whole number of at least 1, got {value!r}")
    for keys in _LIST_KEYS.values():
        for key in keys:
            value = values[key]
            if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
                raise ValueError(f"{key} must be a list of non-empty text")
            values[key] = tuple(value)
    return ReaderConfig(**values)
