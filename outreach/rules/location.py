"""Whether a posting's location is in the US: yes, no or unknown.

Ported from refresh/finalcheck.py:69-82 and refresh/crawl_boards.py:34-49, with the order the #4 plan sets: a
structured country first, then US state names (set aside so "New Mexico" never reads as Mexico), then a bounded,
word-matched non-US list, then US marks, state codes and cities, and last the city names shared by the US and
other countries. Every pattern is a list of literals with bounded gaps, so a search is linear in the location.
"""
import re

from .config import Config, phrases

# Places in one location string: "SF | London", "Austin; Remote", "Toronto or Austin". Only a lower-case " or "
# separates, because "OR" is Oregon ("Portland, OR | Seattle").
_SPLIT = re.compile(r"[|;]|\sor\s")


class _Patterns:
    """The [location] word lists, compiled once per call so a location with many parts does not rebuild them."""

    def __init__(self, cfg: Config):
        loc = cfg.location
        self.state_names = phrases(loc["us_state_names"])
        self.non_us = phrases(loc["non_us_countries"] + loc["non_us_cities"])
        self.us_marks = phrases(loc["us_marks"])
        # An upper-case state code after a comma ("Lima, OH") or at the end ("Austin TX"), never a word like "in".
        code = phrases(loc["us_state_codes"], case=True).pattern
        self.state_code = re.compile(r",\s{0,3}" + code + "|" + code + r"\W*\Z")
        self.us_cities = phrases(loc["us_cities"])
        self.shared = phrases(loc["shared_city_names"])


def _read_part(part: str, p: _Patterns) -> str:
    found_state = p.state_names.search(part) is not None
    rest = p.state_names.sub(" ", part)  # a state name is set aside before any country is read
    if p.non_us.search(rest):
        return "no"
    if found_state or p.us_marks.search(rest) or p.state_code.search(rest) or p.us_cities.search(rest):
        return "yes"
    if p.shared.search(rest):
        return "no"
    return "unknown"


def us_status(location: str, country: str | None, cfg: Config) -> str:
    """yes, no or unknown. A structured country wins; otherwise any US part of the location makes it yes."""
    if country is not None and country.strip():
        return "yes" if phrases(cfg.location["us_marks"]).fullmatch(country.strip()) else "no"
    p = _Patterns(cfg)
    parts = {_read_part(part, p) for part in _SPLIT.split(location or "")}
    if "yes" in parts:
        return "yes"
    if "no" in parts:
        return "no"
    return "unknown"
