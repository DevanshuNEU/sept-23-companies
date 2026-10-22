"""Pay text to an OFLC wage level (#6).

`parse_pay` reads a posted salary or hourly range into annual dollars and refuses anything it cannot read with
certainty. `pay_level` places it against the wage table for the posting's location. Nothing here invents a level:
junk pay, another currency, a remote role or an unlisted city gives None, and the signal stays neutral.
"""
from __future__ import annotations

import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = ["WAGES_PATH", "Area", "PayRange", "PayLevel", "load_wages", "parse_pay", "level_at", "pay_level",
           "lca_pay_level"]

WAGES_PATH = Path(__file__).with_name("wages.toml")

_HOURS_PER_YEAR = 2080
_MIN_ANNUAL = 20_000
_MAX_ANNUAL = 1_000_000
_MAX_TEXT = 5_000  # a pay field is short; cap it so a hostile body cannot make the scan long


@dataclass(frozen=True)
class Area:
    name: str
    aliases: tuple[str, ...]
    levels: tuple[int, int, int, int]


@dataclass(frozen=True)
class PayRange:
    low: int
    high: int


@dataclass(frozen=True)
class PayLevel:
    level: int
    at_min: int
    at_mid: int
    areas: tuple[str, ...]
    note: str


def load_wages(path: Path = WAGES_PATH) -> tuple[Area, ...]:
    path = Path(path)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if "area" not in data:
        raise KeyError(f"[[area]] is missing from {path.name}")
    areas = []
    for row in data["area"]:
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"an area in {path.name} has no name")
        for key in ("aliases", "level1", "level2", "level3", "level4"):
            if key not in row:
                raise KeyError(f"[[area]] {name}: {key} is missing from {path.name}")
        levels = tuple(row[f"level{n}"] for n in (1, 2, 3, 4))
        if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in levels):
            raise ValueError(f"area {name}: levels must be whole dollars above 0, got {levels}")
        if not all(a < b for a, b in zip(levels, levels[1:])):
            raise ValueError(f"area {name}: levels must be strictly increasing, got {levels}")
        aliases = tuple(str(a).lower() for a in row["aliases"])
        if not aliases:
            raise ValueError(f"area {name} has no aliases")
        areas.append(Area(name=name, aliases=aliases, levels=levels))
    return tuple(areas)


_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_CUR = r"(?:(?<![A-Za-z])\$|(?<=US)\$|\bUSD\b)\s{0,2}"
_K = r"(?:[kK](?![A-Za-z]))?"
_SEP = r"\s{0,3}(?:[-\u2013\u2014]|to\b)\s{0,3}"
_MONEY = re.compile(
    rf"{_CUR}(?P<n1>{_NUM})(?P<k1>{_K})(?:{_SEP}(?P<c2>{_CUR})?(?P<n2>{_NUM})(?P<k2>{_K}))?")
_HOURLY_AFTER = re.compile(r"\s{0,2}(?:/\s{0,2}(?:hr|hour|h)\b|per\s+hour\b|an\s+hour\b|hourly\b)", re.IGNORECASE)
_HOURLY_BEFORE = re.compile(r"hourly\W{0,3}$", re.IGNORECASE)


def _value(number: str, k: str) -> float:
    return float(number.replace(",", "")) * (1000 if k else 1)


def parse_pay(text: str) -> PayRange | None:
    """Annual dollars from posted pay, or None. Dollars only (a `$` or `USD`); an hourly marker multiplies by 2080;
    `K` multiplies by 1000; one number gives low == high; a reversed range is swapped. A value under 20,000 or over
    1,000,000 a year, a bare number, another currency or no number gives None."""
    if not isinstance(text, str):
        return None
    text = text[:_MAX_TEXT]
    for m in _MONEY.finditer(text):
        k1, k2 = m.group("k1"), m.group("k2")
        first_end = m.end("k1") if k1 else m.end("n1")
        hourly = bool(_HOURLY_BEFORE.search(text[max(0, m.start() - 12):m.start()]))
        second = m.group("n2")
        if second is not None:
            hourly = hourly or bool(_HOURLY_AFTER.match(text, m.end()))
            bare_small = not m.group("c2") and not k2 and _value(second, "") < 1000
            if bare_small and not hourly:
                second = None  # "$120,000 to 3 openings": the 3 is not a pay figure
        else:
            hourly = hourly or bool(_HOURLY_AFTER.match(text, first_end))
        a = _value(m.group("n1"), k1)
        if second is None:
            b = a
        else:
            b = _value(second, k2)
            if k2 and not k1 and a < 1000:
                a *= 1000  # "$120-150K"
            elif k1 and not k2 and b < 1000:
                b *= 1000
        if hourly:
            a, b = a * _HOURS_PER_YEAR, b * _HOURS_PER_YEAR
        if not (math.isfinite(a) and math.isfinite(b)):
            continue
        low, high = sorted((round(a), round(b)))
        if _MIN_ANNUAL <= low and high <= _MAX_ANNUAL:
            return PayRange(low, high)
    return None


def level_at(annual: int, area: Area) -> int:
    """The highest level N with `annual >= levelN`, and 1 below level I."""
    level = 1
    for n, floor in enumerate(area.levels, start=1):
        if annual >= floor:
            level = n
    return level


def _matches(location: str, area: Area) -> bool:
    loc = location.lower()
    return any(re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", loc) for alias in area.aliases)


def _dollars(n: int) -> str:
    return f"${n:,}"


def pay_level(pay_text: str, location: str, wages: tuple[Area, ...], basis: str = "midpoint") -> PayLevel | None:
    """The wage level of posted pay in the posting's location. With several matching areas (multiple worksites) the
    level at the minimum and at the midpoint is each the lowest across them. None when the pay does not parse or no
    area alias matches the location as a whole word."""
    if basis not in ("midpoint", "minimum"):
        raise ValueError(f"basis must be 'midpoint' or 'minimum', got {basis!r}")
    pay = parse_pay(pay_text)
    if pay is None or not isinstance(location, str):
        return None
    matched = [a for a in wages if _matches(location, a)]
    if not matched:
        return None
    mid = (pay.low + pay.high) // 2
    at_min = min(level_at(pay.low, a) for a in matched)
    at_mid = min(level_at(mid, a) for a in matched)
    posted = _dollars(pay.low) if pay.low == pay.high else f"{_dollars(pay.low)} to {_dollars(pay.high)}"
    names = tuple(a.name for a in matched)
    where = " and ".join(names)
    note = f"posted {posted} in {where}: level {at_min} at the minimum, level {at_mid} at the midpoint"
    return PayLevel(level=at_mid if basis == "midpoint" else at_min, at_min=at_min, at_mid=at_mid, areas=names,
                    note=note)


def lca_pay_level(jr: int, level1: int, *, min_filings: int) -> tuple[int, str] | None:
    """The employer's pay level from its junior LCA filings: level 1 when at least half were level I, else level 2.
    None under `min_filings` junior filings."""
    if jr <= 0 or jr < min_filings:
        return None
    level = 1 if level1 / jr >= 0.5 else 2
    return level, f"LCA history: {level1} of {jr} junior filings at level I"
