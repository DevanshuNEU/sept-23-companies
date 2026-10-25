"""Whether a posting's location is in the US: yes, no or unknown.

Ported from refresh/finalcheck.py:69-82 and refresh/crawl_boards.py:34-49, with the order the #4 plan sets: a
structured US country first, then the location text (a US place there beats a structured non-US country, ruling
2026-10-21), then that non-US country. In the text: US state names (set aside so "New Mexico" never reads as
Mexico), then US marks (ruling R12: "US" beats a country next to it), then a bounded, word-matched non-US list, then
state codes and cities, and last the city names shared by the US and other countries. Every pattern is a list of
literals with bounded gaps, so a search is linear in the location.
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
        # A US mark names the country outright (ruling R12). The bare two-letter "us" counts only in upper case, so
        # "Remote for us" is not a mark; the longer marks ("usa", "u.s.", "united states") match in any case.
        bare = [m for m in loc["us_marks"] if m.lower() == "us"]
        self.us_bare = phrases([m.upper() for m in bare], case=True)
        self.us_marks = phrases([m for m in loc["us_marks"] if m.lower() != "us"])
        self.country_marks = phrases(loc["us_marks"])  # a whole structured country may be the bare "us" in any case
        # An upper-case state code after a comma ("Lima, OH") or at the end ("Austin TX"), never a word like "in".
        code = phrases(loc["us_state_codes"], case=True).pattern
        self.state_code = re.compile(r",\s{0,3}" + code + "|" + code + r"\W*\Z")
        self.us_cities = phrases(loc["us_cities"])
        self.shared = phrases(loc["shared_city_names"])
        self.countries = phrases(loc["non_us_countries"])
        self.fillers = phrases(loc["title_fillers"])


def _read_part(part: str, p: _Patterns) -> str:
    found_state = p.state_names.search(part) is not None
    rest = p.state_names.sub(" ", part)  # a state name is set aside before any country is read
    if p.us_marks.search(rest) or p.us_bare.search(rest):
        return "yes"  # "Remote - US & Canada": naming the US beats a non-US country in the same part
    if p.non_us.search(rest):
        return "no"  # a state code, state name or US city does not beat one ("Toronto, CA", "Tbilisi, Georgia")
    if found_state or p.state_code.search(rest) or p.us_cities.search(rest):
        return "yes"
    if p.shared.search(rest):
        return "no"
    return "unknown"


def _read_country(country: str, p: _Patterns) -> str | None:
    """yes for a country that names the US ("US-Remote", "United States (Remote)", "Puerto Rico"), no for a known
    non-US country, None for anything else ("North America", "Georgia", a country the lists do not know)."""
    if p.country_marks.fullmatch(country) or p.us_marks.search(country) or p.us_bare.search(country):
        return "yes"
    if p.non_us.search(country):
        return "no"
    return None


def _title_names_non_us_country(title: str, p: _Patterns) -> bool:
    """True when the title names a non-US country and no US place. A country counts only as a whole segment of the
    title ("(Venezuela)", "- Remote Canada"), so a name that merely contains one ("Jordan Lake", "India Hicks") is no
    country. A US mark, state, state code or US city in any segment ("(USA or Canada)", "Georgia Tech", "Peru, IN")
    means the title names a US place, and the title is never refused."""
    segments = [seg for chunk in re.split(r"[()\[\],:;|/\u2013\u2014]|\s-\s", title) for seg in _SPLIT.split(chunk)]
    found = False
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        if _read_part(seg, p) == "yes":
            return False
        bare = p.fillers.sub(" ", p.state_names.sub(" ", seg)).strip(" \t.-")
        if bare and p.countries.fullmatch(bare):
            found = True
    return found


def us_status(location: str, country: str | None, cfg: Config, title: str | None = None) -> str:
    """yes, no or unknown. A structured country that names the US wins. A US place in the location text beats a
    known non-US country (Devanshu's ruling, 2026-10-21: a multi-location posting carries one country, often the
    primary office, so "Remote - US or Canada" with country Canada reads yes); with no US place in the text a known
    non-US country reads no. Any other country is no evidence, so the location text decides (a country the lists do
    not know is never a "no" on its own). When all of those say nothing, a title that names a non-US country and no US
    place reads no ("Software Engineer II (Venezuela)"); a title never overrides what the location or country said."""
    p = _Patterns(cfg)
    read = None
    if country is not None and country.strip():
        read = _read_country(country.strip(), p)
        if read == "yes":
            return read
    parts = {_read_part(part, p) for part in _SPLIT.split(location or "")}
    if "yes" in parts:
        return "yes"
    if read == "no" or "no" in parts:
        return "no"
    if title and _title_names_non_us_country(title, p):
        return "no"  # the location and country said nothing, and the title names a non-US country (2026-10-23)
    return "unknown"
