"""Loads outreach/crawl/config.toml into a validated CrawlConfig (#21)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = ["CONFIG_PATH", "CrawlConfig", "load_crawl_config"]

CONFIG_PATH = Path(__file__).with_name("config.toml")

_INT_KEYS = ("floor_s", "ceiling_s", "age_divisor", "defer_s", "max_bytes", "max_redirects", "breaker_failures",
             "batch_rows", "decide_batch", "detail_per_read", "boards_per_tick", "tick_budget_s", "top_companies",
             "watch_interval_s", "watch_per_tick", "watch_min_text_chars", "stale_after_months")
_NUM_KEYS = ("timeout_s", "default_host_interval_s")


@dataclass(frozen=True, eq=False)  # eq=False: host_interval_s is a dict, identity hashing is enough
class CrawlConfig:
    floor_s: int
    ceiling_s: int
    age_divisor: int
    defer_s: int
    user_agent: str
    timeout_s: float
    max_bytes: int
    max_redirects: int
    default_host_interval_s: float
    host_interval_s: dict[str, float]
    breaker_failures: int
    batch_rows: int
    decide_batch: int
    detail_per_read: int
    boards_per_tick: int
    tick_budget_s: int
    top_companies: int
    watch_interval_s: int
    watch_per_tick: int
    watch_min_text_chars: int
    stale_after_months: int


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def load_crawl_config(path: Path = CONFIG_PATH) -> CrawlConfig:
    path = Path(path)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    for key in (*_INT_KEYS, *_NUM_KEYS, "user_agent", "host_interval_s"):
        if key not in data:
            raise KeyError(f"{key} is missing from {path.name}")
    for key in _INT_KEYS:
        if not isinstance(data[key], int) or isinstance(data[key], bool):
            raise ValueError(f"{key} must be a whole number, got {data[key]!r}")
    for key in _NUM_KEYS:
        if not _is_number(data[key]):
            raise ValueError(f"{key} must be a number, got {data[key]!r}")
    if not isinstance(data["user_agent"], str) or not data["user_agent"].strip():
        raise ValueError("user_agent must be a non-empty string")
    hosts = data["host_interval_s"]
    if not isinstance(hosts, dict) or not all(_is_number(v) and v >= 0 for v in hosts.values()):
        raise ValueError("host_interval_s must map host suffixes to non-negative seconds")

    if "@" in data["user_agent"]:
        raise ValueError("user_agent must not carry an email address")
    if data["floor_s"] <= 0 or data["floor_s"] > data["ceiling_s"]:
        raise ValueError(f"floor_s ({data['floor_s']}) must be above 0 and at most ceiling_s ({data['ceiling_s']})")
    if data["age_divisor"] < 1:
        raise ValueError("age_divisor must be at least 1")
    if not 2000 <= data["batch_rows"] <= 5000:
        raise ValueError(f"batch_rows must be 2000 to 5000, got {data['batch_rows']}")
    if not 1 <= data["stale_after_months"] <= 60:
        raise ValueError(f"stale_after_months must be 1 to 60, got {data['stale_after_months']}")
    for key in ("defer_s", "max_bytes", "max_redirects", "breaker_failures", "decide_batch", "detail_per_read",
                "boards_per_tick", "tick_budget_s", "top_companies", "watch_interval_s", "watch_per_tick",
                "watch_min_text_chars"):
        if data[key] < 0 or (key in ("breaker_failures", "decide_batch", "max_bytes") and data[key] < 1):
            raise ValueError(f"{key} must be positive, got {data[key]}")
    if data["timeout_s"] <= 0 or data["default_host_interval_s"] < 0:
        raise ValueError("timeout_s must be above 0 and default_host_interval_s must not be negative")

    kwargs = {k: data[k] for k in (*_INT_KEYS, *_NUM_KEYS, "user_agent")}
    kwargs["host_interval_s"] = {k.lower(): float(v) for k, v in hosts.items()}
    return CrawlConfig(**kwargs)
