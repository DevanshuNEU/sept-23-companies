"""Loads outreach/plan/config.toml into a PlanConfig (#15).

A missing section or key raises KeyError naming `section.key`, so a typo in the TOML fails loudly instead of quietly
turning a rule off.
"""
from __future__ import annotations

import functools
import tomllib
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = ["CONFIG_PATH", "SECTIONS", "PlanConfig", "load_plan_config", "default_plan_config"]

CONFIG_PATH = Path(__file__).with_name("config.toml")
SECTIONS = ("linkedin", "lanes", "email", "plan", "people", "prepare", "note", "route", "bridge")


@dataclass(frozen=True, eq=False)  # eq=False: the dict fields are not hashable, identity hashing is enough
class PlanConfig:
    linkedin: dict
    lanes: dict
    email: dict
    plan: dict
    people: dict
    prepare: dict
    note: dict
    route: dict
    bridge: dict
    raw: bytes  # the file bytes

    def get(self, section: str, key: str):
        """The value of `[section] key`. KeyError names `section.key`."""
        try:
            table = getattr(self, section) if section in SECTIONS else None
        except AttributeError:  # pragma: no cover
            table = None
        if table is None or key not in table:
            raise KeyError(f"{section}.{key}")
        return table[key]


def _check(data: dict) -> None:
    lanes = data["lanes"]
    if abs(lanes["role_share"] + lanes["reason_share"] - 1.0) > 1e-9:
        raise ValueError(f"lanes.role_share + lanes.reason_share must be 1.0, got "
                         f"{lanes['role_share']} + {lanes['reason_share']}")
    if data["plan"]["scan_limit"] < data["linkedin"]["rotate_top_n"]:
        raise ValueError(f"plan.scan_limit ({data['plan']['scan_limit']}) must cover linkedin.rotate_top_n "
                         f"({data['linkedin']['rotate_top_n']})")
    chars = data["note"]["max_chars"]
    if isinstance(chars, bool) or not isinstance(chars, int) or not 100 <= chars <= 300:
        raise ValueError(f"note.max_chars must be a whole number from 100 to 300, got {chars!r}")
    for section, key in _SWITCHES:  # a switch that is not a real boolean ("false" is true in Python) fails loudly
        if not isinstance(data[section][key], bool):
            raise ValueError(f"{section}.{key} must be true or false, got {data[section][key]!r}")
    wave = data["route"]["wave_size"]
    if isinstance(wave, bool) or not isinstance(wave, int) or not 1 <= wave <= 8:
        raise ValueError(f"route.wave_size must be a whole number from 1 to 8, got {wave!r}")
    if not isinstance(data["route"]["prefer_ic_in_role"], bool):
        raise ValueError("route.prefer_ic_in_role must be true or false")
    back = data["bridge"]["log_back_days"]
    if isinstance(back, bool) or not isinstance(back, int) or not 0 <= back <= 3:
        raise ValueError(f"bridge.log_back_days must be a whole number from 0 to 3, got {back!r}")
    tz = data["plan"]["timezone"]
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
        raise ValueError(f"plan.timezone {tz!r} is not a known time zone") from None
    seen: dict[str, str] = {}
    for group in ("role", "reason", "fill"):
        for lane in lanes[group]:
            if lane in seen:
                raise ValueError(f"lane {lane!r} is in both lanes.{seen[lane]} and lanes.{group}")
            seen[lane] = group


def load_plan_config(path: Path = CONFIG_PATH) -> PlanConfig:
    path = Path(path)
    raw = path.read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))
    for section in SECTIONS:
        if section not in data:
            raise KeyError(f"{section} (section missing from {path.name})")
    _check_keys(data, path)
    _check(data)
    return PlanConfig(**{s: data[s] for s in SECTIONS}, raw=raw)


# The keys the checks above read directly; every other key is read through `get`, which names a missing one.
_CHECKED = {"lanes": ("role", "reason", "fill", "role_share", "reason_share"),
            "plan": ("scan_limit", "timezone", "propose_people"), "linkedin": ("rotate_top_n",), "note": ("max_chars",),
            "prepare": ("enabled", "deny_rule_checked"),
            "route": ("wave_size", "prefer_ic_in_role"), "bridge": ("log_back_days",)}
# The on and off switches, each checked to be a boolean. `prepare.enabled` and `plan.propose_people` are the
# company-first switches (#9, 2026-10-26); `prepare.deny_rule_checked` records the one-time LinkedIn deny check.
_SWITCHES = (("prepare", "enabled"), ("prepare", "deny_rule_checked"), ("plan", "propose_people"))


def _check_keys(data: dict, path: Path) -> None:
    for section, keys in _CHECKED.items():
        for key in keys:
            if key not in data[section]:
                raise KeyError(f"{section}.{key} (missing from {path.name})")


@functools.lru_cache(maxsize=1)
def default_plan_config() -> PlanConfig:
    return load_plan_config()
