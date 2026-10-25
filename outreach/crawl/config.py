"""Loads outreach/crawl/config.toml into a validated CrawlConfig (#21)."""
from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = ["ATS_HOSTS", "CONFIG_PATH", "CrawlConfig", "load_crawl_config"]

CONFIG_PATH = Path(__file__).with_name("config.toml")

# Each ATS the crawler has a reader for, and the host suffix every request of that reader goes to (its host bucket).
# A test keeps the names equal to `outreach.crawl.ats.supported_ats()`; it lives here so loading the config imports no
# adapter.
ATS_HOSTS = {
    "greenhouse": "greenhouse.io", "lever": "lever.co", "ashby": "ashbyhq.com", "smartrecruiters": "smartrecruiters.com",
    "workday": "myworkdayjobs.com", "workable": "workable.com", "oracle_orc": "oraclecloud.com",
    "rippling": "rippling.com", "breezy": "breezy.hr", "pinpoint": "pinpointhq.com", "teamtailor": "teamtailor.com",
    "recruitee": "recruitee.com", "bamboohr": "bamboohr.com", "jazzhr": "applytojob.com",
}

_INT_KEYS = ("floor_s", "ceiling_s", "age_divisor", "defer_s", "max_bytes", "max_redirects", "breaker_failures",
             "batch_rows", "decide_batch", "detail_per_read", "boards_per_tick", "tick_budget_s", "top_companies",
             "watch_interval_s", "watch_per_tick", "watch_min_text_chars", "stale_after_months", "body_refetch_days",
             "outage_grace_s")
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
    body_refetch_days: int
    outage_grace_s: int
    ats_off: dict[str, str]  # ATS name -> why its reader is switched off (#46)

    def reads(self, ats: str) -> bool:
        """False for an ATS whose reader is switched off: its boards are never read, probed or slug-probed."""
        return ats not in self.ats_off

    def off_host(self, host: str) -> str | None:
        """The switched-off ATS whose host suffix `host` is (or is a subdomain of), else None."""
        host = (host or "").lower().rstrip(".")
        for ats in self.ats_off:
            suffix = ATS_HOSTS[ats]
            if host == suffix or host.endswith("." + suffix):
                return ats
        return None


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_positive(v: object) -> bool:
    return _is_number(v) and math.isfinite(v) and v > 0


def load_crawl_config(path: Path = CONFIG_PATH) -> CrawlConfig:
    path = Path(path)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    for key in (*_INT_KEYS, *_NUM_KEYS, "user_agent", "host_interval_s", "ats_off"):
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
    if not isinstance(hosts, dict) or not all(_is_positive(v) for v in hosts.values()):
        raise ValueError("host_interval_s must map host suffixes to finite seconds above 0")
    off = data["ats_off"]
    if not isinstance(off, dict):
        raise ValueError("ats_off must be a table of ATS names, each with the reason its reader is off")
    for ats, reason in off.items():
        if ats not in ATS_HOSTS:
            raise ValueError(f"ats_off names {ats}, which is not an ATS the crawler reads "
                             f"(it reads: {', '.join(sorted(ATS_HOSTS))})")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"ats_off.{ats} must give the reason its reader is off, as text")

    if "@" in data["user_agent"]:
        raise ValueError("user_agent must not carry an email address")
    for key in _INT_KEYS:
        if key != "max_redirects" and key != "batch_rows" and key != "stale_after_months" and data[key] <= 0:
            raise ValueError(f"{key} must be positive, got {data[key]}")
    if data["max_redirects"] < 0:
        raise ValueError(f"max_redirects must not be negative, got {data['max_redirects']}")
    if data["floor_s"] > data["ceiling_s"]:
        raise ValueError(f"floor_s ({data['floor_s']}) must be at most ceiling_s ({data['ceiling_s']})")
    if not 2000 <= data["batch_rows"] <= 5000:
        raise ValueError(f"batch_rows must be 2000 to 5000, got {data['batch_rows']}")
    if not 1 <= data["stale_after_months"] <= 60:
        raise ValueError(f"stale_after_months must be 1 to 60, got {data['stale_after_months']}")
    for key in _NUM_KEYS:
        if not _is_positive(data[key]):
            raise ValueError(f"{key} must be a finite number above 0, got {data[key]!r}")

    kwargs = {k: data[k] for k in (*_INT_KEYS, *_NUM_KEYS, "user_agent")}
    kwargs["host_interval_s"] = {k.lower(): float(v) for k, v in hosts.items()}
    kwargs["ats_off"] = {ats: reason.strip() for ats, reason in off.items()}
    return CrawlConfig(**kwargs)
