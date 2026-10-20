"""Years of experience a posting asks for, and entry-level wording in its body.

Ported from refresh/finalcheck.py:41-44, 107-108 and refresh/crawl_boards.py:29-31, with the fixes in the #4 plan:
the largest requirement wins (the old min() read "5+ years ... 1+ years of Go" as 1), and preferences, years that
stand in for a degree, the company's age and the company's own years are not requirements.
"""
import bisect
import re

from .config import Config, phrases
from .text import quote_around, sentences

MAX_YEARS = 30  # a larger number is not a requirement anyone means
EXPERIENCE_WINDOW = 70  # an experience word must start within this many characters after "years", as in finalcheck
COMPANY_AGE_WORDS = 3  # a company-age word this many words either side of the number rules it out
CLAUSE_CHARS = 120  # a degree alternative must follow the number within its clause and this many characters
ENTRY_CHARS = 6000  # entry wording counts only near the top of the body

_DASHES = "\\-" + chr(0x2010) + chr(0x2011) + chr(0x2013)  # hyphen, Unicode hyphens, en dash (see config.py)
# A number of years: "3 years", "3+ yrs", "2 or more years", and a range ("1-3", "1 to 3", "3 or 4", "between 2
# and 5") that counts its low end. The number may not continue a word, a number or a decimal ("2.5", "100", "v2").
# Every quantifier is bounded, so a search costs a fixed amount per position and stays linear on any body.
_YEARS = re.compile(
    r"(?<![\w.,])(?P<n>\d{1,2})(?:\s{0,2}\+)?"
    r"(?:\s{0,2}(?P<joint>[" + _DASHES + r"]|to|or|and)\s{0,2}(?P<hi>\d{1,2})(?:\s{0,2}\+)?)?"
    r"(?:\s{1,2}(?:or\s{1,2}more|plus))?"
    r"\s{0,3}(?:years?|yrs?)(?!\w)",
    re.IGNORECASE,
)
_BETWEEN_BEFORE = re.compile(r"(?<!\w)between\s{1,3}\Z", re.IGNORECASE)  # "and" joins a range only after this
_OR_BEFORE = re.compile(r"(?<!\w)or\s{1,3}\Z", re.IGNORECASE)  # "degree or 4 years"
_DEGREE_CLAUSE_END = re.compile(r"[,;:]")
# Where a clause ends for preferences and voice (rulings R10, R11): a comma, a bracket, " while " or " but ".
_CLAUSE_BREAK = re.compile(r"[,()\[\]]|\s(?:while|but)\s", re.IGNORECASE)
_WORD = re.compile(r"\w")
_LOOK = 80  # characters read either side of a number when counting words around it


class _Patterns:
    """The [years] word lists, compiled once per call so a body with many numbers does not rebuild them."""

    def __init__(self, cfg: Config):
        y = cfg.years
        self.experience = phrases(y["experience_words"])
        self.preferred = phrases(y["preferred"])
        self.degree_alternative = phrases(y["degree_alternative"])
        self.company_age = phrases(y["company_age"])
        self.company_voice = phrases(y["company_voice"])
        self.candidate_voice = phrases(y["candidate_voice"])


def _starts(pattern: re.Pattern, sentence: str) -> list[int]:
    return [m.start() for m in pattern.finditer(sentence)]


def _any_in(starts: list[int], lo: int, hi: int) -> bool:
    """True when some position in the sorted list `starts` is in [lo, hi)."""
    i = bisect.bisect_left(starts, lo)
    return i < len(starts) and starts[i] < hi


class _Sentence:
    """One sentence and where each word list and clause break sits in it, found once so each number is a lookup."""

    def __init__(self, sentence: str, p: _Patterns):
        self.text = sentence
        self.p = p
        self.experience = _starts(p.experience, sentence)
        self.preferred = _starts(p.preferred, sentence)
        self.company_voice = _starts(p.company_voice, sentence)
        self.candidate_voice = _starts(p.candidate_voice, sentence)
        breaks = list(_CLAUSE_BREAK.finditer(sentence))
        self.break_starts = [b.start() for b in breaks]
        self.break_ends = [b.end() for b in breaks]
        # "Ideally, ..." or "Nice to have: ...": a preference that opens the sentence covers all of it.
        self.opens_with_preference = bool(self.preferred) and _WORD.search(sentence, 0, self.preferred[0]) is None

    def clause(self, start: int, end: int) -> tuple[int, int]:
        """The clause holding text[start:end]: from the break before it to the break after it."""
        i = bisect.bisect_right(self.break_ends, start)
        a = self.break_ends[i - 1] if i else 0
        j = bisect.bisect_left(self.break_starts, end)
        b = self.break_starts[j] if j < len(self.break_starts) else len(self.text)
        return a, b

    def counts(self, start: int, end: int) -> bool:
        """True when the number at text[start:end] (its first digit through "years") is a requirement."""
        s, p = self.text, self.p
        i = bisect.bisect_left(self.experience, end)
        if i == len(self.experience) or self.experience[i] - end > EXPERIENCE_WINDOW:
            return False
        a, b = self.clause(start, end)
        if self.opens_with_preference or _any_in(self.preferred, a, b):
            return False
        if self.company_voice and self.company_voice[0] < start and not _any_in(self.candidate_voice, a, b):
            return False
        if _OR_BEFORE.search(s[max(0, start - 8):start]):
            return False
        degree_end = min(len(s), end + CLAUSE_CHARS)  # bounded, so a huge sentence with no comma stays linear
        found = _DEGREE_CLAUSE_END.search(s, end, degree_end)
        if p.degree_alternative.search(s, end, found.start() if found else degree_end):
            return False
        before = s[max(0, start - _LOOK):start].split()[-COMPANY_AGE_WORDS:]
        after = s[end:end + _LOOK].split()[:COMPANY_AGE_WORDS]
        return p.company_age.search(" ".join(before + ["|"] + after)) is None


def _number(sentence: str, m: re.Match) -> tuple[int, int]:
    """The number a match asks for and where it starts: a range's low end, except "3 and 5" without "between"."""
    if m.group("joint") and m.group("joint").lower() == "and":
        if not _BETWEEN_BEFORE.search(sentence[max(0, m.start() - 12):m.start()]):
            return int(m.group("hi")), m.start("hi")
    return int(m.group("n")), m.start()


def years_asked(body: str, cfg: Config) -> tuple[int | None, str]:
    """The largest number of years the body requires and the sentence that says it, or (None, "")."""
    p = _Patterns(cfg)
    best: tuple[int, int, int] | None = None  # (years, start, end) in body
    pos = 0
    for sentence in sentences(body):
        offset = body.find(sentence, pos)
        pos = offset + len(sentence)
        if not _YEARS.search(sentence):
            continue
        read = _Sentence(sentence, p)
        for m in _YEARS.finditer(sentence):
            n, start = _number(sentence, m)
            if n > MAX_YEARS or not read.counts(start, m.end()):
                continue
            if best is None or n > best[0]:
                best = (n, offset + start, offset + m.end())
    if best is None:
        return None, ""
    n, start, end = best
    return n, quote_around(body, start, end) or body[start:end]


def entry_wording(body: str, cfg: Config) -> str:
    """The sentence with entry-level wording (new grad, early career, "0-2 years") in the top of the body, or ""."""
    m = phrases(cfg.years["body_entry"]).search(body[:ENTRY_CHARS])
    if m is None:
        return ""
    return quote_around(body, m.start(), m.end()) or m.group(0)
