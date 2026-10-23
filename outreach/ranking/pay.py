"""Pay text to an OFLC wage level (#6).

`parse_pay` reads one clear USD annual range and refuses everything else (unknown is neutral). `pay_level` places it
against the wage table for the posting's location, and only for a location that names the area's state or an
unambiguous metro. Nothing here invents a level: junk pay, another currency, a remote role or an unlisted city gives
None, and the signal stays neutral.
"""
from __future__ import annotations

import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = ["WAGES_PATH", "Area", "PayRange", "PayLevel", "load_wages", "parse_pay", "level_at", "matching_areas",
           "pay_level", "lca_pay_level"]

WAGES_PATH = Path(__file__).with_name("wages.toml")

_HOURS_PER_YEAR = 2080
_MIN_ANNUAL = 20_000
_MAX_ANNUAL = 1_000_000
_MAX_TEXT = 5_000  # a pay field is short; cap it so a hostile body cannot make the scan long


@dataclass(frozen=True)
class Area:
    name: str
    aliases: tuple[str, ...]  # city names: they count only with one of the area's states
    levels: tuple[int, int, int, int]
    states: tuple[str, ...] = ()  # two letter codes
    metro: tuple[str, ...] = ()  # names that are unambiguous without a state ("nyc", "bay area")


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
        states = tuple(str(x).upper() for x in row.get("states", ()))
        if not states or not all(x in _US_STATES for x in states):
            raise ValueError(f"area {name}: states must be US state codes, got {row.get('states')!r}")
        metro = tuple(str(x).lower() for x in row.get("metro", ()))
        areas.append(Area(name=name, aliases=aliases, levels=levels, states=states, metro=metro))
    return tuple(areas)


_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_CUR = r"(?:(?<![A-Za-z])(?:US)?\$|\bUSD\b)\s{0,2}"
_K = r"(?:[kK](?![A-Za-z]))?"
_SEP = r"\s{0,3}(?:[-\u2013\u2014]|to\b)\s{0,3}"
_MONEY = re.compile(
    rf"{_CUR}(?P<n1>{_NUM})(?P<k1>{_K})(?:{_SEP}(?P<c2>{_CUR})?(?P<n2>{_NUM})(?P<k2>{_K}))?")
# Anything that says the figure is not a plain US dollar annual salary, anywhere in the text.
_NOT_ANNUAL = re.compile(
    r"/\s{0,2}(?:hr|hour|h|mo|month|wk|week|day)\b"
    r"|\b(?:per|an?|each)\s+(?:hour|hr|month|week|day)\b"
    r"|\b(?:hourly|monthly|weekly|daily|biweekly|bi-weekly)\b", re.IGNORECASE)
_NOT_USD = re.compile(
    r"[£€¥₹]"
    r"|\b(?:CAD|AUD|NZD|EUR|GBP|CHF|INR|SGD|JPY|CNY|RMB|MXN|BRL|SEK|NOK|DKK|PLN|ILS|AED|HKD|ZAR)\b"
    r"|\b(?:euros?|pounds?|sterling|rupees?|yen|canadian|australian)\b", re.IGNORECASE)
_FOREIGN_DOLLAR = re.compile(r"([A-Za-z]{1,3})\$")  # C$, CA$, A$, AU$, NZ$, HK$; "US$" is fine
# Words that make a figure something other than base pay, checked in the range's own sentence.
_NOT_BASE = re.compile(
    r"\b(?:bonus(?:es)?|sign[- ]?on|signing|relocation|equity|stock|rsus?|stipend|tuition|raised|funding|commission"
    r"|up\s+to)\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?:[^\n.!?]|[.!?](?!\s|$))+")


def _value(number: str, k: str) -> float:
    return float(number.replace(",", "")) * (1000 if k else 1)


def _range_ends(m: re.Match) -> tuple[float, float] | None:
    """The two ends of a money match, scaled by K, or None when it is a single figure (or its second figure is a
    stray small number such as the 3 in "$120,000 to 3 openings")."""
    if m.group("n2") is None:
        return None
    k1, k2 = m.group("k1"), m.group("k2")
    if not m.group("c2") and not k2 and _value(m.group("n2"), "") < 1000:
        return None
    a, b = _value(m.group("n1"), k1), _value(m.group("n2"), k2)
    if k2 and not k1 and a < 1000:
        a *= 1000  # "$120-150K"
    elif k1 and not k2 and b < 1000:
        b *= 1000
    return a, b


def parse_pay(text: str) -> PayRange | None:
    """Annual dollars from exactly one clear USD annual range ("$A - $B", "$AK to $BK"), else None.

    Both ends must be present with 20,000 <= A <= B <= 1,000,000. A single number, "up to X", more than one range
    (pay zones), another currency, a per hour, month, week or day figure, a bonus, sign-on, relocation, equity,
    stock, stipend, tuition, raised or funding word in the range's sentence, or text over the 5,000 character cap
    (never truncated) gives None."""
    if not isinstance(text, str) or len(text) > _MAX_TEXT:
        return None
    if _NOT_USD.search(text) or _NOT_ANNUAL.search(text):
        return None
    if any(m.group(1).upper() != "US" for m in _FOREIGN_DOLLAR.finditer(text)):
        return None
    matches = list(_MONEY.finditer(text))
    ranges = [(m, ends) for m in matches if (ends := _range_ends(m)) is not None]
    if len(ranges) != 1:
        return None
    found, (a, b) = ranges[0]
    sentence = next(x for x in _SENTENCE.finditer(text) if x.start() <= found.start() < x.end())
    if _NOT_BASE.search(sentence.group()):
        return None
    if any(m is not found and sentence.start() <= m.start() < sentence.end() for m in matches):
        return None  # another figure in the same sentence: not clear which is the salary
    low, high = math.floor(a + 0.5), math.floor(b + 0.5)
    if _MIN_ANNUAL <= low <= high <= _MAX_ANNUAL:
        return PayRange(low, high)
    return None


def level_at(annual: int, area: Area) -> int:
    """The highest level N with `annual >= levelN`, and 1 below level I."""
    level = 1
    for n, floor in enumerate(area.levels, start=1):
        if annual >= floor:
            level = n
    return level


_US_STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado",
    "CT": "connecticut", "DE": "delaware", "DC": "district of columbia", "FL": "florida", "GA": "georgia",
    "HI": "hawaii", "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky",
    "LA": "louisiana", "ME": "maine", "MD": "maryland", "MA": "massachusetts", "MI": "michigan", "MN": "minnesota",
    "MS": "mississippi", "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada", "NH": "new hampshire",
    "NJ": "new jersey", "NM": "new mexico", "NY": "new york", "NC": "north carolina", "ND": "north dakota",
    "OH": "ohio", "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania", "RI": "rhode island",
    "SC": "south carolina", "SD": "south dakota", "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont",
    "VA": "virginia", "WA": "washington", "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming",
}
_STATE_CODE = re.compile(r"(?<![A-Za-z])(" + "|".join(_US_STATES) + r")(?![A-Za-z])")  # upper case only
_STATE_NAME = re.compile(
    r"(?<![a-z])(" + "|".join(sorted(map(re.escape, _US_STATES.values()), key=lambda n: (-len(n), n)))
    + r")(?![a-z])", re.IGNORECASE)
_NAME_TO_CODE = {name: code for code, name in _US_STATES.items()}
_NON_US = re.compile(
    r"(?<![a-z])(?:uk|u\.k\.|united kingdom|england|scotland|wales|northern ireland|ireland|canada|ontario|quebec"
    r"|british columbia|alberta|india|germany|france|spain|italy|netherlands|poland|portugal|sweden|switzerland"
    r"|israel|australia|new zealand|singapore|japan|china|brazil|mexico|argentina|colombia|costa rica|philippines"
    r"|united arab emirates|uae|south africa|nigeria|kenya|egypt)(?![a-z])", re.IGNORECASE)
_REMOTE = re.compile(r"(?<![a-z])(?:remote|anywhere)(?![a-z])", re.IGNORECASE)
_WORKSITE_SPLIT = re.compile(r"[;|/\n\r]+|\s+(?:or|and)\s+|\s&\s", re.IGNORECASE)


def _find_name(low: str, names: tuple[str, ...]) -> re.Match | None:
    """The first of `names` (longest first) found in `low` as a whole word."""
    for name in sorted(names, key=lambda n: (-len(n), n)):
        m = re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", low)
        if m:
            return m
    return None


def _site_matches(site: str, area: Area) -> bool:
    """One worksite (one place, such as "Boston, MA"). An area's city counts only with one of the area's states; a
    metro name counts alone. Another state or a non-US country makes the worksite unlisted for this area."""
    low = site.lower()
    metro = _find_name(low, area.metro)
    city = metro or _find_name(low, area.aliases)
    if city is None:
        return False
    rest = site[:city.start()] + " " + site[city.end():]  # the same offsets: `low` has the same length as `site`
    states = {m.group(1) for m in _STATE_CODE.finditer(rest)}
    states |= {_NAME_TO_CODE[m.group(1).lower()] for m in _STATE_NAME.finditer(rest)}
    if _NON_US.search(_STATE_NAME.sub(" ", rest)):
        return False
    if states - set(area.states):
        return False
    return metro is not None or bool(states)


def _matches(location: str, area: Area) -> bool:
    return any(_site_matches(site, area) for site in _WORKSITE_SPLIT.split(location))


def matching_areas(location: str, wages: tuple[Area, ...]) -> tuple[Area, ...]:
    """The listed areas a place names: a city with one of the area's states, or a metro name. Empty for a remote
    role, a bare state, an unlisted city or anything that is not text."""
    if not isinstance(location, str) or _REMOTE.search(location):
        return ()
    return tuple(a for a in wages if _matches(location, a))


def _dollars(n: int) -> str:
    return f"${n:,}"


def pay_level(pay_text: str, location: str, wages: tuple[Area, ...], basis: str = "midpoint") -> PayLevel | None:
    """The wage level of posted pay in the posting's location. With several matching areas (multiple worksites) the
    level at the minimum and at the midpoint is each the lowest across them. None when the pay does not parse or no
    area alias matches the location as a whole word."""
    if basis not in ("midpoint", "minimum"):
        raise ValueError(f"basis must be 'midpoint' or 'minimum', got {basis!r}")
    pay = parse_pay(pay_text)
    if pay is None:
        return None
    matched = matching_areas(location, wages)
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
