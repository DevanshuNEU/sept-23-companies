"""Size, stage, level and board bands (#81): `bands.toml` read into `Bands`, and the pure functions that put a
company's facts in a band.

Unknown is `None` and never a band, so two unknowns never match. This module imports nothing from
`outreach.ranking` or `outreach.api`: the ranking tilt imports it, not the other way round.
"""
from __future__ import annotations

import functools
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from outreach.sources.keys import US_STATES

__all__ = ["BANDS_PATH", "STAGE_BANDS", "Bands", "load_bands", "default_bands", "size_band", "stage_band",
           "level_band", "hq_state", "board_words", "FEATURE_NAMES", "Features", "features_of"]

BANDS_PATH = Path(__file__).with_name("bands.toml")

# The `[stages]` keys of bands.toml and the words each band shows.
STAGE_BANDS = {"seed_or_earlier": "seed or earlier", "series_a_b": "Series A or B",
               "later_private": "later private", "public": "public"}
INTERN = "intern"
ENTRY = "entry level"
_LEVEL_WORDS = {"swe2": "SWE II", "open": "level not stated", "not_stated": "level not stated", "senior": "senior"}
_BOARD_WORDS = {"ashby": "Ashby", "smartrecruiters": "SmartRecruiters", "bamboohr": "BambooHR",
                "oracle_orc": "Oracle", "jazzhr": "JazzHR", "icims": "iCIMS", "join_com": "JOIN",
                "successfactors": "SuccessFactors"}
_STATE = re.compile(r"^([A-Z]{2})$")


@dataclass(frozen=True)
class Bands:
    size_tops: tuple[int, ...]
    stages: dict[str, str]  # stage code to band words
    entry_tiers: frozenset[str]
    raw: bytes  # the file bytes


def _need(data: dict, key: str, where: str = "bands.toml"):
    if key not in data:
        raise KeyError(f"{where} has no {key}")
    return data[key]


def load_bands(path: Path = BANDS_PATH) -> Bands:
    """Reads the bands file. `KeyError` names a missing key; `ValueError` for size tops that do not strictly
    increase or a stage code in two bands."""
    raw = Path(path).read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))
    tops = _need(data, "size_tops")
    if (not isinstance(tops, list) or not tops
            or any(isinstance(t, bool) or not isinstance(t, int) or t < 1 for t in tops)
            or any(b <= a for a, b in zip(tops, tops[1:]))):
        raise ValueError(f"size_tops must be whole numbers of 1 or more that strictly increase, got {tops!r}")
    tiers = _need(data, "entry_tiers")
    if not isinstance(tiers, list) or not all(isinstance(t, str) and t for t in tiers):
        raise ValueError(f"entry_tiers must be a list of level tiers, got {tiers!r}")
    table = _need(data, "stages")
    stages: dict[str, str] = {}
    for key, words in STAGE_BANDS.items():
        codes = _need(table, key, "bands.toml [stages]")
        if not isinstance(codes, list) or not all(isinstance(c, str) and c for c in codes):
            raise ValueError(f"[stages] {key} must be a list of stage codes, got {codes!r}")
        for code in codes:
            if code in stages:
                raise ValueError(f"stage {code} is in two bands: {stages[code]} and {words}")
            stages[code] = words
    return Bands(size_tops=tuple(tops), stages=stages, entry_tiers=frozenset(tiers), raw=raw)


@functools.cache
def default_bands() -> Bands:
    return load_bands()


def size_band(low: int | None, high: int | None, b: Bands) -> str | None:
    """The band of `high`, else `low` (the `small` signal's rule); None when both are unknown."""
    n = high if high is not None else low
    if n is None:
        return None
    floor = 1
    for top in b.size_tops:
        if n <= top:
            return f"{floor:,} to {top:,}"
        floor = top + 1
    return f"over {b.size_tops[-1]:,}"


def stage_band(stage: str | None, b: Bands) -> str | None:
    return b.stages.get(stage) if stage else None


def level_band(tier: str | None, *, intern: bool, b: Bands) -> str | None:
    """`intern` for a posting in the intern lane, whatever its tier; else the tier's band; None when unknown."""
    if intern:
        return INTERN
    if tier is None:
        return None
    if tier in b.entry_tiers:
        return ENTRY
    return _LEVEL_WORDS.get(tier)


def hq_state(hq: str | None) -> str | None:
    """The two capital letters after the last comma ("San Francisco, CA" is CA), when they name a US state or
    territory; else None ("Remote", "Austin, Texas", "London, UK")."""
    if not hq or "," not in hq:
        return None
    m = _STATE.match(hq.rpartition(",")[2].strip())
    return m.group(1) if m and m.group(1) in US_STATES else None


def board_words(ats: str) -> str:
    """The board system as people write it: `ashby` is Ashby, `icims` is iCIMS; others in title case."""
    return _BOARD_WORDS.get(ats) or ats.replace("_", " ").title()


FEATURE_NAMES = ("size_band", "stage_band", "level_band", "board_system")


@dataclass(frozen=True)
class Features:
    """The four traits the tilt compares. `board_system` is the ats code (`ashby`); words come from `board_words`."""
    size_band: str | None = None
    stage_band: str | None = None
    level_band: str | None = None
    board_system: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {name: getattr(self, name) for name in FEATURE_NAMES}


def features_of(*, size_low: int | None, size_high: int | None, stage: str | None, level_tier: str | None,
                intern: bool, board_system: str | None, bands: Bands) -> Features:
    return Features(size_band=size_band(size_low, size_high, bands), stage_band=stage_band(stage, bands),
                    level_band=level_band(level_tier, intern=intern, b=bands), board_system=board_system or None)
