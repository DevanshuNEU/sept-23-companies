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

# The two weights that push a company to the bottom (see _check_bottom_weights).
BOTTOM_WEIGHTS = ("staffing_risk", "large_later_stage")
# The negative values of the other signals: (section, key, the weight it is multiplied by).
_OTHER_NEGATIVES = (("everify", "not_enrolled", "everify"), ("pay_level", "level_1", "pay_level"),
                    ("small", "weak_flag_value", "small"))


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


def _number(value: object, where: str) -> float:
    if not _is_number(value) or not math.isfinite(value):
        raise ValueError(f"{where} must be a finite number, got {value!r}")
    return float(value)


def _check_bottom_weights(data: dict) -> None:
    """A company a bottom signal hits must sort below every company it does not hit, so the bottom signal's points
    (weight times the size of its value) must exceed the most any other company can score: every other weight added
    together, plus the worst of the other negative signals (E-Verify not enrolled, level I pay, a weak size flag)."""
    w = data["weights"]
    positive = sum(v for name, v in w.items() if name not in BOTTOM_WEIGHTS)
    worst = sum(max(0.0, -_number(data[section][key], f"[{section}] {key}")) * w[weight]
                for section, key, weight in _OTHER_NEGATIVES)
    high = _number(data["staffing_risk"]["high"], "[staffing_risk] high")
    if high != -1.0:
        raise ValueError(f"[staffing_risk] high must be -1.0 (the full bottom push), got {high}")
    points = {"staffing_risk": w["staffing_risk"] * abs(high), "large_later_stage": w["large_later_stage"] * 1.0}
    limit = positive + worst
    for name in BOTTOM_WEIGHTS:
        if points[name] <= limit:
            raise ValueError(f"[weights] {name} gives {points[name]} points at most, which must exceed {limit} (every "
                             f"other weight plus the worst other negative), or a hit company no longer sorts last")


def _check_confidence(c: dict) -> None:
    factors = [_number(c[f"factor_{level}"], f"[confidence] factor_{level}") for level in ("high", "medium", "low")]
    if not all(0 < f <= 1 for f in factors):
        raise ValueError(f"[confidence] factors must be above 0 and at most 1, got {factors}")
    if not factors[0] >= factors[1] >= factors[2]:
        raise ValueError(f"[confidence] factor_high >= factor_medium >= factor_low is required, got {factors}")
    for key in ("rank_high_share", "rank_low_share"):
        if not 0 < _number(c[key], f"[confidence] {key}") < 1:
            raise ValueError(f"[confidence] {key} must be between 0 and 1, got {c[key]!r}")
    for key in ("reason_count", "evidence_old_days", "lca_pay_medium_filings"):
        if not isinstance(c[key], int) or isinstance(c[key], bool) or c[key] < 1:
            raise ValueError(f"[confidence] {key} must be a whole number of at least 1, got {c[key]!r}")


_HIDE_CONFIDENCES = ("confirmed", "estimate")


def _check_hide(h: dict) -> None:
    """The size hide may never hide more than #9 allows: over 300 people, on confirmed or estimated evidence."""
    over = h["size_over"]
    if not isinstance(over, int) or isinstance(over, bool) or over < 300:
        raise ValueError(f"[hide] size_over must be a whole number of at least 300, got {over!r}")
    conf = h["size_confidences"]
    if not isinstance(conf, list) or not all(isinstance(c, str) and c in _HIDE_CONFIDENCES for c in conf):
        raise ValueError(f"[hide] size_confidences must be a list of {_HIDE_CONFIDENCES}, got {conf!r}")


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
    _check_bottom_weights(data)
    _check_confidence(data["confidence"])
    _check_hide(data["hide"])
    others = {k: v for k, v in data.items() if k not in ("ranker", "weights", "hide")}
    return RankerConfig(ranker=data["ranker"], weights=weights, hide=data["hide"], sections=others, raw=raw)


@functools.lru_cache(maxsize=1)
def default_ranker_config() -> RankerConfig:
    """The shipped config, loaded once."""
    return load_ranker_config()
