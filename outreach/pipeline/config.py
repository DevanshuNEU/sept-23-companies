"""Loads outreach/pipeline/config.toml into a validated PipelineConfig (#153).

A missing section or key raises KeyError naming it, an unknown one raises ValueError, as the other packages' loaders
do, so a typo in the TOML fails at load instead of quietly turning a setting off.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

__all__ = ["CONFIG_PATH", "PipelineConfig", "SimplifyConfig", "load_pipeline_config"]

CONFIG_PATH = Path(__file__).with_name("config.toml")

_SIMPLIFY_KEYS = ("url", "categories", "enabled")


@dataclass(frozen=True)
class SimplifyConfig:
    url: str
    categories: tuple[str, ...]
    enabled: bool


@dataclass(frozen=True)
class StaffingConfig:
    """Known staffing firms (#257): names, name words, and the client-posting share that marks one."""
    known: tuple[str, ...] = ()
    name_words: tuple[str, ...] = ()
    client_min: int = 3
    client_share: float = 0.5


_STAFFING_KEYS = ("known", "name_words", "client_min", "client_share")


@dataclass(frozen=True)
class PipelineConfig:
    simplify: SimplifyConfig
    staffing: StaffingConfig = StaffingConfig()


def _staffing(table, name: str) -> StaffingConfig:
    if not isinstance(table, dict):
        raise ValueError("staffing must be a table")
    unknown = sorted(set(table) - set(_STAFFING_KEYS))
    if unknown:
        raise ValueError(f"[staffing] has unknown key(s): {', '.join(unknown)}")
    for key in _STAFFING_KEYS:
        if key not in table:
            raise KeyError(f"[staffing] {key} is missing from {name}")
    for key in ("known", "name_words"):
        if not isinstance(table[key], list) or not all(isinstance(w, str) and w.strip() for w in table[key]):
            raise ValueError(f"{key} must be a list of non-empty text")
    lo, share = table["client_min"], table["client_share"]
    if not isinstance(lo, int) or isinstance(lo, bool) or lo < 1:
        raise ValueError(f"client_min must be a whole number of at least 1, got {lo!r}")
    if not isinstance(share, (int, float)) or isinstance(share, bool) or not 0 < share <= 1:
        raise ValueError(f"client_share must be above 0 and at most 1, got {share!r}")
    return StaffingConfig(known=tuple(w.strip() for w in table["known"]),
                          name_words=tuple(w.strip() for w in table["name_words"]),
                          client_min=lo, client_share=float(share))


def load_pipeline_config(path: Path = CONFIG_PATH) -> PipelineConfig:
    path = Path(path)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    unknown = sorted(set(data) - {"simplify", "staffing"})
    if unknown:
        raise ValueError(f"{path.name} has unknown section(s): {', '.join(unknown)}")
    table = data.get("simplify")
    if table is None:
        raise KeyError(f"[simplify] is missing from {path.name}")
    if not isinstance(table, dict):
        raise ValueError("simplify must be a table")
    unknown = sorted(set(table) - set(_SIMPLIFY_KEYS))
    if unknown:
        raise ValueError(f"[simplify] has unknown key(s): {', '.join(unknown)}")
    for key in _SIMPLIFY_KEYS:
        if key not in table:
            raise KeyError(f"[simplify] {key} is missing from {path.name}")
    url, categories, enabled = table["url"], table["categories"], table["enabled"]
    if not isinstance(url, str) or urlsplit(url).scheme != "https" or not urlsplit(url).hostname:
        raise ValueError(f"url must be an https link, got {url!r}")
    if (not isinstance(categories, list) or not categories
            or not all(isinstance(c, str) and c.strip() for c in categories)):
        raise ValueError("categories must be a non-empty list of non-empty text")
    if not isinstance(enabled, bool):
        raise ValueError(f"enabled must be true or false, got {enabled!r}")
    if "staffing" not in data:
        raise KeyError(f"[staffing] is missing from {path.name}")
    return PipelineConfig(SimplifyConfig(url=url, categories=tuple(c.strip() for c in categories), enabled=enabled),
                          _staffing(data["staffing"], path.name))
