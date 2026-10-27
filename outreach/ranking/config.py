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

__all__ = ["CONFIG_PATH", "REQUIRED", "BOTTOM_WEIGHTS", "ZERO_WEIGHT_OK", "TILT_WEIGHT", "RankerConfig",
           "load_ranker_config", "default_ranker_config"]

CONFIG_PATH = Path(__file__).with_name("config.toml")

# Every section and key a loaded config must have. Sections with no keys of their own are listed with ().
REQUIRED: dict[str, tuple[str, ...]] = {
    "ranker": ("keep_versions", "size_check_top_n", "many_postings", "lca_software_many", "funding_window_months",
               "lca_recent_years", "hn_post_days", "diff_min_rank_move", "diff_top_n", "lca_wage_min_filings"),
    "weights": ("live_role", "posting_sponsors", "everify", "cap_exempt", "pay_level", "lca_junior", "small",
                "reachable_person", "funding", "staffing_risk", "large_later_stage", "interview_like"),
    "hide": ("size_over", "size_confidences"),
    "live_role": ("intern_junior", "level_role", "any_us_role", "fresh_days", "fresh_bonus",
                  "tier_values", "early_career_max_age_days"),
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
    "interview_like": ("min_interview_companies", "min_matches", "confident_at", "max_weight_share"),
    "confidence": ("factor_high", "factor_medium", "factor_low", "evidence_old_days", "lca_pay_medium_filings",
                   "rank_high_share", "rank_low_share", "reason_count"),
}

# The two weights that push a company to the bottom (see _check_bottom_weights).
BOTTOM_WEIGHTS = ("staffing_risk", "large_later_stage")
# Weights that may be exactly 0.0 (switched off). Every other weight must be above 0.
ZERO_WEIGHT_OK = ("interview_like",)
# The one-way interview tilt (#81), capped by _check_tilt.
TILT_WEIGHT = "interview_like"
_TILT_MAX_SHARE = 0.25
# The level tiers [live_role.tier_values] must give, each a value from 0 to 1, and no other.
LIVE_ROLE_TIERS = ("new_grad", "early_career", "swe1", "swe2", "open")
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


def _check_tilt(data: dict) -> None:
    """The interview tilt stays small: its weight is at most `max_weight_share` of the top score, the sum of every
    other weight except the bottom push, and the share itself is above 0 and at most 0.25. Its counts are whole
    numbers of at least 1."""
    section = data["interview_like"]
    for key in ("min_interview_companies", "min_matches", "confident_at"):
        _whole_at_least_one(section[key], f"[interview_like] {key}")
    share = section["max_weight_share"]
    if not _is_number(share) or not math.isfinite(share) or not 0 < share <= _TILT_MAX_SHARE:
        raise ValueError(f"[interview_like] max_weight_share must be above 0 and at most {_TILT_MAX_SHARE}, "
                         f"got {share!r}")
    w = data["weights"]
    top = sum(v for name, v in w.items() if name not in BOTTOM_WEIGHTS and name != TILT_WEIGHT)
    cap = share * top
    if w[TILT_WEIGHT] > cap:
        raise ValueError(f"[weights] {TILT_WEIGHT} is {w[TILT_WEIGHT]:g}, above its cap of {cap:g} "
                         f"({share:g} of the top score {top:g}), so the tilt would no longer be small")


def _check_tier_values(section: dict) -> None:
    """[live_role.tier_values] is a table with a value from 0 to 1 for every level tier, and no unknown tier (a typo
    would otherwise fall back to level_role without a word)."""
    tiers = section["tier_values"]
    if not isinstance(tiers, dict):
        raise ValueError(f"[live_role] tier_values must be a table of level tiers, got {tiers!r}")
    for tier in LIVE_ROLE_TIERS:
        if tier not in tiers:
            raise KeyError(f"[live_role.tier_values] {tier} is missing")
    for tier, value in tiers.items():
        if tier not in LIVE_ROLE_TIERS:
            raise ValueError(f"[live_role.tier_values] {tier} is not a level tier (one of {LIVE_ROLE_TIERS})")
        if not 0 <= _number(value, f"[live_role.tier_values] {tier}") <= 1:
            raise ValueError(f"[live_role.tier_values] {tier} must be from 0 to 1, got {value!r}")


def _check_early_age(section: dict) -> None:
    """[live_role] early_career_max_age_days: whole days from 1 to 365."""
    days = section["early_career_max_age_days"]
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 365:
        raise ValueError(f"[live_role] early_career_max_age_days must be a whole number from 1 to 365, got {days!r}")


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


def _whole_at_least_one(value: object, where: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{where} must be a whole number of at least 1, got {value!r}")


def _check_ranker(data: dict) -> None:
    """Counts the ranker divides by or compares against: 0 would make lca_junior divide by zero and the funding
    window never open."""
    for key in ("size_check_top_n", "funding_window_months"):
        _whole_at_least_one(data["ranker"][key], f"[ranker] {key}")
    _whole_at_least_one(data["lca_junior"]["cap"], "[lca_junior] cap")


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
        if not _is_number(w) or not math.isfinite(w) or w < 0 or (w == 0 and name not in ZERO_WEIGHT_OK):
            raise ValueError(f"[weights] {name} must be a finite number above 0, got {w!r}")
    _check_bottom_weights(data)
    _check_tilt(data)
    _check_confidence(data["confidence"])
    _check_tier_values(data["live_role"])
    _check_early_age(data["live_role"])
    _check_hide(data["hide"])
    _check_ranker(data)
    others = {k: v for k, v in data.items() if k not in ("ranker", "weights", "hide")}
    return RankerConfig(ranker=data["ranker"], weights=weights, hide=data["hide"], sections=others, raw=raw)


@functools.lru_cache(maxsize=1)
def default_ranker_config() -> RankerConfig:
    """The shipped config, loaded once."""
    return load_ranker_config()
