"""Years of experience a posting asks for, and entry-level wording in its body.

Ported from refresh/finalcheck.py:41-44, 107-108 and refresh/crawl_boards.py:29-31, with the fixes in the #4 plan:
the largest requirement wins (the old min() read "5+ years ... 1+ years of Go" as 1), and preferences, years that
stand in for a degree, and the company's age are not requirements.
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
# A number of years: "3 years", "3+ yrs", "2 or more years", and a range ("1-3", "1 to 3", "3 or 4") that counts
# its low end. The number may not continue a word, a number or a decimal ("2.5", "100", "v2"). Every quantifier
# is bounded, so a search costs a fixed amount per position and stays linear on any body.
_YEARS = re.compile(
    r"(?<![\w.,])(?P<n>\d{1,2})(?:\s{0,2}\+)?"
    r"(?:\s{0,2}(?:[" + _DASHES + r"]|to|or)\s{0,2}\d{1,2}(?:\s{0,2}\+)?)?"
    r"(?:\s{1,2}(?:or\s{1,2}more|plus))?"
    r"\s{0,3}(?:years?|yrs?)(?!\w)",
    re.IGNORECASE,
)
_OR_BEFORE = re.compile(r"(?<!\w)or\s{1,3}\Z", re.IGNORECASE)  # "degree or 4 years"
_CLAUSE_END = re.compile(r"[,;:]")
_LOOK = 80  # characters read either side of a number when counting words around it


class _Patterns:
    """The [years] word lists, compiled once per call so a body with many numbers does not rebuild them."""

    def __init__(self, cfg: Config):
        y = cfg.years
        self.experience = phrases(y["experience_words"])
        self.preferred = phrases(y["preferred"])
        self.degree_alternative = phrases(y["degree_alternative"])
        self.company_age = phrases(y["company_age"])


def _counts(sentence: str, m: re.Match, experience_starts: list[int], p: _Patterns) -> bool:
    """True when the number m found in `sentence` is a requirement, per the rules in the module docstring.

    `experience_starts` is where every experience word in the sentence starts, found once per sentence so a
    sentence with many numbers is still read once.
    """
    start, end = m.start(), m.end()
    i = bisect.bisect_left(experience_starts, end)
    if i == len(experience_starts) or experience_starts[i] - end > EXPERIENCE_WINDOW:
        return False
    if _OR_BEFORE.search(sentence[max(0, start - 8):start]):
        return False
    clause_end = min(len(sentence), end + CLAUSE_CHARS)  # bounded, so a huge sentence with no comma stays linear
    clause = _CLAUSE_END.search(sentence, end, clause_end)
    if p.degree_alternative.search(sentence, end, clause.start() if clause else clause_end):
        return False
    before = sentence[max(0, start - _LOOK):start].split()[-COMPANY_AGE_WORDS:]
    after = sentence[end:end + _LOOK].split()[:COMPANY_AGE_WORDS]
    return p.company_age.search(" ".join(before + ["|"] + after)) is None


def years_asked(body: str, cfg: Config) -> tuple[int | None, str]:
    """The largest number of years the body requires and the sentence that says it, or (None, "")."""
    p = _Patterns(cfg)
    best: tuple[int, int, int] | None = None  # (years, start, end) in body
    pos = 0
    for sentence in sentences(body):
        offset = body.find(sentence, pos)
        pos = offset + len(sentence)
        if not _YEARS.search(sentence) or p.preferred.search(sentence):
            continue
        experience_starts = [e.start() for e in p.experience.finditer(sentence)]
        for m in _YEARS.finditer(sentence):
            n = int(m.group("n"))
            if n > MAX_YEARS or not _counts(sentence, m, experience_starts, p):
                continue
            if best is None or n > best[0]:
                best = (n, offset + m.start(), offset + m.end())
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
