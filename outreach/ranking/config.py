"""Loads outreach/ranking/config.toml into a RankerConfig (#6).

`config.py` is part of the ranker version (see version.py). A missing section or key raises KeyError naming it, so a
typo in the TOML fails loudly instead of quietly turning a signal off.
"""
from __future__ import annotations

import functools
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = ["CONFIG_PATH", "REQUIRED", "BOTTOM_WEIGHTS", "RankerConfig", "load_ranker_config", "default_ranker_config"]

CONFIG_PATH = Path(__file__).with_name("config.toml")

# Every section and key a loaded config must have. Sections with no keys of their own are listed with ().
REQUIRED: dict[str, tuple[str, ...]] = {
    "ranker": ("keep_versions", "size_check_top_n", "many_postings", "lca_software_many", "funding_window_months",
               "lca_recent_years", "hn_post_days", "diff_min_rank_move", "diff_top_n", "lca_wage_min_filings"),
    "weights": ("live_role", "posting_sponsors", "everify", "cap_exempt", "pay_level", "lca_junior", "small",
                "reachable_person", "funding", "staffing_risk", "large_later_stage"),
    "hide": ("size_over", "size_confidences"),
    "live_role": ("intern_junior", "level_role", "any_us_role", "fresh_days", "fresh_bonus"),
    "posting_sponsors": ("yes", "opt_only"),
    "everify": ("enrolled", "not_enrolled"),
    "cap_exempt": ("confirmed", "likely"),
    "pay_level": ("basis", "level_1", "level_2", "level_3", "level_4"),
    "lca_junior": ("cap",),
    "funding": (),
    "small": ("max_headcount", "borderline_headcount", "early_stages", "later_stages", "early_value", "other_value",
              "borderline_value", "weak_flag_value"),
    "large_later_stage": (),
    "reachable_person": ("role_kinds", "person", "hn_post"),
    "staffing_risk": ("high", "medium", "promoted_high"),
    "confidence": ("factor_high", "factor_medium", "factor_low", "evidence_old_days", "lca_pay_medium_filings",
                   "rank_high_share", "rank_low_share", "reason_count"),
}

# The two weights that push a company to the bottom. Each must exceed every other weight added together.
BOTTOM_WEIGHTS = ("staffing_risk", "large_later_stage")


@dataclass(frozen=True, eq=False)  # eq=False: the dict fields are not hashable, identity hashing is enough
class RankerConfig:
    ranker: dict
    weights: dict
    hide: dict
    sections: dict[str, dict]  # every other table, keyed by signal name
    raw: bytes  # the file bytes

    def section(self, name: str) -> dict:
        if name not in self.sections:
            raise KeyError(f"[{name}] is not a section of the ranker config")
        return self.sections[name]


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def load_ranker_config(path: Path = CONFIG_PATH) -> RankerConfig:
    path = Path(path)
    raw = path.read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))
    for section, keys in REQUIRED.items():
        if section not in data:
            raise KeyError(f"[{section}] is missing from {path.name}")
        for key in keys:
            if key not in data[section]:
                raise KeyError(f"[{section}] {key} is missing from {path.name}")
    weights = data["weights"]
    for name, w in weights.items():
        if not _is_number(w) or not math.isfinite(w) or w <= 0:
            raise ValueError(f"[weights] {name} must be a finite number above 0, got {w!r}")
    positive = sum(w for name, w in weights.items() if name not in BOTTOM_WEIGHTS)
    for name in BOTTOM_WEIGHTS:
        if weights[name] <= positive:
            raise ValueError(f"[weights] {name} ({weights[name]}) must exceed every other weight added together "
                             f"({positive}), or a hit company no longer sorts last")
    others = {k: v for k, v in data.items() if k not in ("ranker", "weights", "hide")}
    return RankerConfig(ranker=data["ranker"], weights=weights, hide=data["hide"], sections=others, raw=raw)


@functools.lru_cache(maxsize=1)
def default_ranker_config() -> RankerConfig:
    """The shipped config, loaded once."""
    return load_ranker_config()
