"""Title lane and title level: what kind of role a posting title names, and what level it states.

Ported from refresh/finalcheck.py:25-44, 91-105, refresh/crawl_boards.py:26-28, 59 and refresh/final_merge.py:28-37,
with the fixes in the #4 plan: "member of technical staff" is rewritten to MTS before any senior check, "staff" is
always senior, and a level numeral counts only right after a level noun.
"""
import re
from dataclasses import dataclass

from .config import Config, phrases

_MTS = re.compile(r"(?<!\w)member\s+of\s+(?:technical|product|engineering)\s+staff(?!\w)", re.IGNORECASE)

# A level numeral, longest first so "I/II" wins over "I" and "III" over "II". It must end there: not "10", not
# "2.0", and a slash may follow only before another numeral ("II/III", not "I/O").
_NUMERAL = r"(?:I\s*/\s*II|1\s*/\s*2|III|IV|II|I|V|[1-5])(?!\w|\.\d|/(?![IV\d]))"
_DASHES = "\\-" + chr(0x2010) + chr(0x2011) + chr(0x2013)  # hyphen, Unicode hyphens, en dash (see config.py)
_SEP = "[\\s,(" + _DASHES + "]{0,3}"
# "Software Engineer Front-End 2": one role qualifier from config (title.level_qualifiers) may sit between the noun
# and the numeral. Any other word in between means the numeral is not a level ("Web 3", "Python 3", "Layer 2").
_QUAL_BEFORE = "[\\s,(" + _DASHES + "]{1,3}"
_QUAL_AFTER = "[\\s,()" + _DASHES + "]{1,3}"
_LEVEL_WORD = re.compile(r"(?<!\w)level[\s" + _DASHES + r"]{0,3}(II|I|2|1)(?!\w|\.\d)", re.IGNORECASE)

_SWE1 = {"i", "1", "i/ii", "1/2"}
_SWE2 = {"ii", "2"}
_SENIOR_NUMERALS = {"iii", "iv", "v", "3", "4", "5"}


@dataclass(frozen=True)
class TitleResult:
    lane: str  # general_application, intern, not_engineering, off_lane or engineering
    tier: str | None  # senior, new_grad, early_career, swe1, swe2, open, or None when the title states no level
    basis: str  # one short sentence for the UI


def _level_pattern(nouns: list[str], qualifiers: list[str]) -> re.Pattern:
    # The nouns are single words. A numeral may sit right on the noun ("SDE2"), so the noun ends at anything but a
    # letter instead of at a word boundary.
    noun = "(?<!\\w)(?P<noun>" + "|".join(re.escape(n) for n in sorted(nouns, key=len, reverse=True)) + ")(?![^\\W\\d])"
    qualifier = _QUAL_BEFORE + "(?:" + phrases(qualifiers).pattern + ")" + _QUAL_AFTER
    return re.compile(
        noun + "(?:" + _SEP + "(?P<num>" + _NUMERAL + ")" + "|" + qualifier + "(?P<qualnum>" + _NUMERAL + "))",
        re.IGNORECASE,
    )


def _levels(title: str, cfg: Config) -> list[tuple[str, str]]:
    """Every (as written, normalised numeral) a level noun or the word "level" gives the title."""
    found = [(m.group(0), m.group(1).lower()) for m in _LEVEL_WORD.finditer(title)]  # first, so the basis says "Level 1"
    for m in _level_pattern(cfg.title["level_nouns"], cfg.title["level_qualifiers"]).finditer(title):
        num = m.group("num") or m.group("qualnum")
        found.append((f"{m.group('noun')} {num}", re.sub(r"\s+", "", num).lower()))
    return found


def _lane(title: str, cfg: Config) -> tuple[str, str]:
    t = cfg.title
    if m := phrases(t["general_application"]).search(title):
        return "general_application", f"Title says {m.group(0)}, a general application"
    if m := phrases(t["intern"]).search(title):
        return "intern", f"Title says {m.group(0)}, an internship"
    if not phrases(t["engineering"]).search(title):
        return "not_engineering", "Title names no engineering role"
    if m := phrases(t["off_lane"]).search(title):
        return "off_lane", f"Title says {m.group(0)}, outside his lane"
    return "engineering", ""


def _tier(title: str, cfg: Config) -> tuple[str | None, str]:
    t = cfg.title
    tt = _MTS.sub("MTS", title)  # before any senior check, so "Staff" in the phrase never counts
    if m := phrases(t["manager"]).search(tt):
        return "senior", f"Title says {m.group(0)}, a manager role"
    if (m := phrases(t["senior"]).search(tt)) and not phrases(t["strong_entry"]).search(tt):
        return "senior", f"Title says {m.group(0)}, above entry level"
    levels = _levels(tt, cfg)
    for written, num in levels:
        if num in _SENIOR_NUMERALS:  # no entry wording overrides a level numeral
            return "senior", f"Title says {written}, above entry level"
    if phrases(t["new_grad"]).search(tt):
        return "new_grad", "Title says new grad"
    if phrases(t["early_career"]).search(tt):
        return "early_career", "Title says early career"
    for tier, nums in (("swe1", _SWE1), ("swe2", _SWE2)):
        for written, num in levels:
            if num in nums:
                return tier, f"Title says {written}"
    if m := phrases(t["open_level"]).search(tt):
        return "open", f"{m.group(0)} role, no level in the title"
    return None, "Title states no level"


def classify_title(title: str, cfg: Config) -> TitleResult:
    title = title or ""
    lane, basis = _lane(title, cfg)
    if lane != "engineering":
        return TitleResult(lane=lane, tier=None, basis=basis)
    tier, basis = _tier(title, cfg)
    return TitleResult(lane=lane, tier=tier, basis=basis)
