"""Company facts as evidence drafts: size, stage and E-Verify, read from the old dashboard's data.js wording.

A draft is one row for the `evidence` table, checked against its CHECKs when it is made, so a bad value fails here
and not at insert time. Sizes come from `sizeWhy`, `head` and `yc.team`; stage from `funding`; E-Verify from the
`everify` object. `sizeClass` is never read for a size: "small" was the default when no visa filing was found
(audit section 1; discover_radar.py:144), so it says nothing about headcount. Visa filing counts ("lifetime visa
filings", "no filing record") never yield a size either; when a text has a "source estimate" part, that part is
the only one read. The old dashboard's hide verdicts that are not headcount come out as `OldSignal`s, the only
place `sizeClass` is read. Every pattern is a list of literals with bounded gaps, so reading stays linear.
"""
import datetime
import functools
import re
from dataclasses import dataclass

from .config import Config, default_config, phrases

# The evidence table's CHECK vocabularies (outreach/store/migrations/0001_initial.sql).
FACTS = ("size", "stage", "sponsorship", "domain", "hq", "everify")
CONFIDENCES = ("confirmed", "estimate", "guess")
SOURCE_KINDS = ("hand", "posting", "old_dashboard", "yc", "builtin", "linkedin", "website", "formd", "lca", "everify",
                "other")
STAGES = ("pre_seed", "seed", "series_a", "series_b", "series_c", "series_d", "series_e_plus", "late_private",
          "public", "yc", "early")
SPONSORSHIP = ("yes", "no", "opt_only", "unclear")
EVERIFY = ("enrolled", "not_enrolled")


def _is_date(value) -> bool:
    """Whether a value is a YYYY-MM-DD date, as SQLite's date() would write it back."""
    if not isinstance(value, str):
        return False
    try:
        return datetime.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


@dataclass(frozen=True)
class EvidenceDraft:
    """One evidence row before it has a company: the fields the rules decide. Raises ValueError on a bad value."""
    fact: str
    value: str
    confidence: str
    source_kind: str
    source_ref: str = ""
    quote: str = ""
    num_low: int | None = None
    num_high: int | None = None
    observed_on: str | None = None

    def __post_init__(self):
        problems = []
        if self.fact not in FACTS:
            problems.append(f"fact {self.fact!r}")
        if not isinstance(self.value, str) or not self.value:
            problems.append("an empty value")
        if self.confidence not in CONFIDENCES:
            problems.append(f"confidence {self.confidence!r}")
        if self.source_kind not in SOURCE_KINDS:
            problems.append(f"source_kind {self.source_kind!r}")
        if self.fact == "size":
            if self.num_low is None or self.num_low < 0:
                problems.append("a size without num_low >= 0")
            elif self.num_high is not None and self.num_high < self.num_low:
                problems.append("num_high below num_low")
        elif self.num_low is not None or self.num_high is not None:
            problems.append(f"numbers on a {self.fact} fact")
        if self.fact == "stage" and self.value not in STAGES:
            problems.append(f"stage {self.value!r}")
        if self.fact == "sponsorship" and self.value not in SPONSORSHIP:
            problems.append(f"sponsorship {self.value!r}")
        if self.fact == "everify" and (self.value not in EVERIFY or self.observed_on is None):
            problems.append("an everify fact needs enrolled or not_enrolled and observed_on")
        if self.observed_on is not None and not _is_date(self.observed_on):
            problems.append(f"observed_on {self.observed_on!r}")
        if problems:
            raise ValueError("bad evidence draft: " + "; ".join(problems))


@dataclass(frozen=True)
class OldSignal:
    """An old dashboard hide verdict that is not a headcount: over_500, big_by_filings, sec_filings,
    defence_contractor or agency. `quote` is the sizeWhy it came from."""
    kind: str
    quote: str


# Size wording: under N, over N, N-M or N to M, N+ and N, the first one in the text that counts (see _counts).
_DASH = "[\\-" + chr(0x2010) + chr(0x2011) + chr(0x2013) + "]"  # built from code points: no literal en dash here


def _num(name: str) -> str:
    """A number named `name`: 50, 1000 or 1,000, never part of a word, an amount, a decimal or a percent."""
    return r"(?<![\w$.,])(?P<" + name + r">\d{1,3}(?:,\d{3})+(?!\d)|\d{1,7})(?![\w%]|[.,]\d)"


_SIZE = re.compile(
    r"(?:(?<!\w)(?:under|less than|fewer than)\s{1,3}|<\s{0,2})" + _num("under")
    + r"|(?:(?<!\w)(?:well over|over|more than)\s{1,3}|>\s{0,2})" + _num("over")
    + r"|" + _num("low") + r"\s{0,3}(?:" + _DASH + r"|(?<!\w)to(?!\w))\s{0,3}~?\s{0,2}" + _num("high")
    + r"|" + _num("plus") + r"\s{0,2}\+"
    + r"|" + _num("one"),
    re.IGNORECASE,
)
_PARENS = re.compile(r"\([^()]{0,200}\)")  # "(Built In)", "(guess)", "(6-month-old startup)"
_YEAR = re.compile(r"(?:19|20)\d\d")  # "founded in 2015", "Est. 2015": a year is no headcount on its own
_CUE_CHARS = 40  # how far back a size cue is looked for; the cue must end right where the number starts


class _Patterns:
    """The [facts] word lists, compiled once per Config (see _patterns)."""

    def __init__(self, cfg: Config):
        f = cfg.facts
        self.guess = phrases(f["size_guess_words"])
        self.not_size = phrases(f["not_size"])
        self.source_estimate = phrases(f["source_estimate"])
        self.stage_guess = phrases(f["stage_guess"])
        # a cue that ends the text before a number ("about", "~"), and a noun that starts the text after one
        self.cue_before = re.compile("(?:" + phrases(f["size_cues"]).pattern + r")$", re.IGNORECASE)
        self.noun_after = re.compile(r"\s{0,3}(?:" + phrases(f["headcount_nouns"]).pattern + ")", re.IGNORECASE)
        for table, allowed in ((f["stage"], STAGES), (f["size_source"], SOURCE_KINDS)):
            if unknown := sorted(set(table) - set(allowed)):
                raise ValueError(f"[facts] names values the evidence table does not allow: {unknown}")
        self.stage = [(value, phrases(words)) for value, words in f["stage"].items()]  # config order decides
        self.size_source = [(kind, phrases(words)) for kind, words in f["size_source"].items()]


@functools.lru_cache(maxsize=8)
def _patterns(cfg: Config) -> _Patterns:
    return _Patterns(cfg)


def _p(cfg: Config | None) -> _Patterns:
    return _patterns(cfg if cfg is not None else default_config())


def _int(digits: str) -> int:
    return int(digits.replace(",", ""))


def _size_numbers(m: re.Match) -> tuple[str, int, int | None] | None:
    """(value, num_low, num_high) for one size match, or None when the numbers make no headcount."""
    if m["under"]:
        high = _int(m["under"]) - 1
        if high < 1:
            return None
        return ("1", 1, 1) if high == 1 else (f"1-{high}", 1, high)
    if m["over"]:
        n = _int(m["over"]) + 1
        return f"{n}+", n, None
    if m["low"]:
        low, high = _int(m["low"]), _int(m["high"])
        if high < 1 or high < low:
            return None
        return (str(low), low, low) if low == high else (f"{low}-{high}", low, high)
    if m["plus"]:
        n = _int(m["plus"])
        return (f"{n}+", n, None) if n >= 1 else None
    n = _int(m["one"])
    return (str(n), n, n) if n >= 1 else None


def _counts(m: re.Match, bare: str, first_after_marker: bool, p: _Patterns) -> bool:
    """Whether a size match is a headcount. Under, over and ranges always are. A bare N or N+ is one only right
    after a size cue ("about 50", "~1,000+"), right before a headcount noun ("40 engineers"), or as the first thing
    after "source estimate" ("source estimate 7"); a year ("founded in 2015") only before a headcount noun."""
    digits = m["plus"] or m["one"]
    if digits is None:
        return True
    if p.noun_after.match(bare, m.end()):
        return True
    if _YEAR.fullmatch(digits):
        return False
    if first_after_marker and not bare[:m.start()].strip():
        return True
    return p.cue_before.search(bare[max(0, m.start() - _CUE_CHARS):m.start()].rstrip()) is not None


def parse_size(text: str, source_kind: str, cfg: Config | None = None) -> EvidenceDraft | None:
    """A size draft from one sizeWhy or head string, or None when it gives no headcount.

    Reads "about N", "around N to M", "N-M employees", "~N", "~N-M", "under N", "est. <N", "well over N" and "N+".
    Visa filing wording never gives a size; when a "source estimate" part is present only it is read, and a source
    in parentheses there ("(Built In)") is the draft's source_kind. Words in parentheses are never read as a size,
    and a bare number needs a cue or a headcount noun (see _counts), so "500 companies use us" is no size.
    The confidence is guess when the read part says guess, uncertain or est., else estimate.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    p = _p(cfg)
    marker = p.source_estimate.search(text)
    read = text[marker.end():] if marker else text
    if p.not_size.search(read):
        return None
    asides = [m.group(0) for m in _PARENS.finditer(read)]
    bare = _PARENS.sub(lambda m: " " * len(m.group(0)), read)
    m = next((m for m in _SIZE.finditer(bare) if _counts(m, bare, marker is not None, p)), None)
    numbers = _size_numbers(m) if m else None
    if numbers is None:
        return None
    value, low, high = numbers
    if marker:
        source_kind = next((kind for kind, words in p.size_source if any(words.search(a) for a in asides)),
                           source_kind)
    confidence = "guess" if p.guess.search(read) else "estimate"
    return EvidenceDraft("size", value, confidence, source_kind, quote=text.strip(), num_low=low, num_high=high)


def parse_stage(text: str, source_kind: str, cfg: Config | None = None) -> EvidenceDraft | None:
    """A stage draft from one funding string, or None ("not researched", "Unverified", "Private, profitable").

    The first [facts.stage] list (in config order) with a phrase in the text gives the value; wording that names
    two stages ([facts] stage_guess, "Series A/B era") makes it a guess.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    p = _p(cfg)
    value = next((stage for stage, words in p.stage if words.search(text)), None)
    if value is None:
        return None
    confidence = "guess" if p.stage_guess.search(text) else "estimate"
    return EvidenceDraft("stage", value, confidence, source_kind, quote=text.strip())


def everify_status(record: dict) -> EvidenceDraft | None:
    """An E-Verify draft from an old dashboard `everify` object, or None without a status or a `checked` date.

    The old pipeline matched employers by name (refresh/apply_everify.py), so the confidence is estimate.
    """
    if not isinstance(record, dict):
        return None
    value = {"enrolled": "enrolled", "none": "not_enrolled"}.get(record.get("status"))
    checked = record.get("checked")
    if value is None or not _is_date(checked):
        return None
    entity, enrolled = record.get("entity"), record.get("enrolled")
    parts = [entity] if isinstance(entity, str) and entity else []
    if isinstance(enrolled, str) and enrolled:
        parts.append(f"enrolled {enrolled}")
    searched = record.get("searched")
    if not parts and isinstance(searched, list) and searched:
        parts.append("searched: " + ", ".join(str(s) for s in searched))
    return EvidenceDraft("everify", value, "estimate", "everify", quote="; ".join(parts), observed_on=checked)


def _yc_team(company: dict) -> EvidenceDraft | None:
    yc = company.get("yc")
    team = yc.get("team") if isinstance(yc, dict) else None
    if not isinstance(team, int) or isinstance(team, bool) or team < 1:
        return None
    return EvidenceDraft("size", str(team), "estimate", "yc", quote=f"YC team size {team}", num_low=team,
                         num_high=team)


def from_old_dashboard(company: dict, cfg: Config | None = None) -> list[EvidenceDraft]:
    """One data.js company's size, stage and E-Verify drafts, from sizeWhy, head, yc.team, funding and everify.

    `sizeClass` is never read here.
    """
    drafts = [
        parse_size(company.get("sizeWhy"), "old_dashboard", cfg),
        parse_size(company.get("head"), "old_dashboard", cfg),
        _yc_team(company),
        parse_stage(company.get("funding"), "old_dashboard", cfg),
        everify_status(company.get("everify")),
    ]
    return [d for d in drafts if d is not None]


# The old dashboard's sizeWhy hide verdicts that are not headcount, and the sizeClass values that are not sizes.
_OVER_500 = phrases(["well over 500 people"])
_BIG_BY_FILINGS = re.compile(r"lifetime visa filings;\s{0,3}every known under" + _DASH + r"500 company", re.IGNORECASE)
_SEC_FILINGS = phrases(["decades of sec filings"])
_CLASS_SIGNALS = {"cleared": "defence_contractor", "agency": "agency"}


def old_dashboard_signals(company: dict) -> list[OldSignal]:
    """The old dashboard's hide verdicts that are not a headcount, for #17 to record. The only reader of sizeClass."""
    why = company.get("sizeWhy")
    why = why if isinstance(why, str) else ""
    if kind := _CLASS_SIGNALS.get(company.get("sizeClass")):
        return [OldSignal(kind, why)]
    for kind, pattern in (("big_by_filings", _BIG_BY_FILINGS), ("sec_filings", _SEC_FILINGS),
                          ("over_500", _OVER_500)):
        if pattern.search(why):
            return [OldSignal(kind, why)]
    return []
