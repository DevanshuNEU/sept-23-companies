"""Years of experience a posting asks for, and entry-level wording in its body.

Ported from refresh/finalcheck.py:41-44, 107-108 and refresh/crawl_boards.py:29-31, with the fixes in the #4 plan:
the largest requirement wins (the old min() read "5+ years ... 1+ years of Go" as 1), and preferences, years that
stand in for a degree, the company's age and the company's own years are not requirements.
"""
import bisect
import re
from typing import NamedTuple

from .config import Config, phrases
from .text import quote_around, sentences

MAX_YEARS = 30  # a larger number is not a requirement anyone means
EXPERIENCE_WINDOW = 70  # an experience word must start within this many characters after "years", as in finalcheck
COMPANY_AGE_WORDS = 3  # a company-age word this many words either side of the number rules it out
CLAUSE_CHARS = 120  # a degree alternative must follow the number within its clause and this many characters
ENTRY_CHARS = 6000  # entry wording counts only near the top of the body
TIER_WORDS = 3  # a master's word must end at most this many words before its number ("MS and 0+ years")
_TIER_CHARS = 40  # and this many characters, so a far word is rejected without splitting a long stretch
_TIE_CHARS = 200  # what follows "years" is read this far when asking whether a technology owns the figure

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
# A degree tier: "BS and 2 years, or MS and 0 years", "or 1 year with an advanced degree". The master's word comes
# at most TIER_WORDS words before the number, or the number is followed by "with a master's degree".
_WITH_DEGREE = re.compile(
    r"\s{0,3}(?:of\s{1,3}experience\s{1,3})?(?:with|if\s{1,3}you\s{1,3}(?:have|hold)|holding)\s{1,3}(?:an?\s{1,3}|the\s{1,3})?",
    re.IGNORECASE)
# "BS or MS in CS and 5+ years": a master's word that follows a bachelor's word and "or" is an alternative degree
_ALTERNATIVE_JOIN = re.compile(r"\W{0,4}(?:degrees?\s{1,3})?(?:or|and/or|and|/)?\s{0,3}\Z", re.IGNORECASE)
_ALT_LOOK = 30
# A figure owned by one technology: the years, then "[of] [words] experience", then "with/in/using ..." and a tail.
_AFTER_YEARS = re.compile(r"\s{0,3}(?:of\s{1,3})?(?P<pre>(?:[\w+#.\-/]{1,20}\s{1,3}){0,4}?)experience(?!\w)(?P<post>.*)",
                          re.IGNORECASE | re.DOTALL)
_EXPERIENCE_IN = re.compile(r"\s{1,3}(?:with|in|using|of|on|working\s{1,3}with)\s{1,3}", re.IGNORECASE)
_TIE_END = re.compile(r"[,;()\[\]\n]")  # the tail of "experience with ..." ends at the first of these
_TECH_JOIN = r"(?:\s{0,3}(?:and/or|and|or|&|/|plus)?\s{0,3})"
_WORD = re.compile(r"\w")
_LOOK = 80  # characters read either side of a number when counting words around it
VOICE_WORDS = 3  # a company_voice phrase must end at most this many words before the number
_VOICE_CHARS = 60  # and this many characters, so a far phrase is rejected without splitting a long stretch
_CURLY = chr(0x2019)  # a curly apostrophe, read as a straight one in voice phrases


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
        # master's words match as written, abbreviations ("MS", "M.S.") only in capitals so "10 ms" is not one
        self.master = re.compile("(?:(?i:" + phrases(y["master_words"]).pattern + ")|"
                                 + phrases(y["master_abbreviations"], case=True).pattern + ")")
        self.bachelor = phrases(y["bachelor_words"])
        self.upper_bound = re.compile("(?:" + phrases(y["upper_bound"]).pattern + r")\s{0,3}\Z", re.IGNORECASE)
        tech = "(?:" + phrases(y["technologies"]).pattern + ")"
        self.tech_only = re.compile(r"\s*" + tech + "(?:" + _TECH_JOIN + tech + r")*[\s.!?]*\Z", re.IGNORECASE)
        self.tech_before = re.compile(tech + r"\s{0,2}\Z", re.IGNORECASE)


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
        straight = sentence.replace(_CURLY, "'")  # same length, so positions carry over
        company = list(p.company_voice.finditer(straight))
        self.company_starts = [m.start() for m in company]
        self.company_ends = [m.end() for m in company]
        self.candidate_voice = _starts(p.candidate_voice, straight)
        self.straight = straight
        masters = list(p.master.finditer(straight))
        self.master_starts = [m.start() for m in masters]
        self.master_ends = [m.end() for m in masters]
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

    def company_owns(self, start: int, clause_start: int) -> bool:
        """True when a company_voice phrase in the number's clause ends at most VOICE_WORDS words before it."""
        i = bisect.bisect_right(self.company_ends, start) - 1  # the last phrase ending at or before the number
        if i < 0 or self.company_starts[i] < clause_start or start - self.company_ends[i] > _VOICE_CHARS:
            return False
        return len(self.text[self.company_ends[i]:start].split()) <= VOICE_WORDS

    def ms_tier(self, start: int, end: int) -> bool:
        """True when the number at text[start:end] is the figure for a master's degree ("or MS and 0+ years")."""
        if not self.master_starts or not self.experience:
            return False  # a tier figure needs an experience word somewhere in the sentence
        s = self.straight
        a, b = self.clause(start, end)
        i = bisect.bisect_right(self.master_ends, start) - 1  # the last master's word ending at or before the number
        if i >= 0 and self.master_starts[i] >= a and start - self.master_ends[i] <= _TIER_CHARS \
                and len(s[self.master_ends[i]:start].split()) <= TIER_WORDS and not self._alternative_degree(i):
            return True
        j = _WITH_DEGREE.match(s, end)
        if j is None:
            return False
        k = bisect.bisect_left(self.master_starts, j.end())
        return k < len(self.master_starts) and self.master_starts[k] == j.end() and self.master_starts[k] < b

    def _alternative_degree(self, i: int) -> bool:
        """True when master's word i follows a bachelor's word and "or" ("BS or MS"), so it is no tier of its own."""
        s, at = self.straight, self.master_starts[i]
        lo = max(0, at - _ALT_LOOK)
        last = None
        for m in self.p.bachelor.finditer(s, lo, at):
            last = m
        return last is not None and _ALTERNATIVE_JOIN.match(s, last.end(), at) is not None

    def tied_to_technology(self, end: int) -> bool:
        """True when the years are tied to named technologies: "3+ years of experience with Python", "Go experience".

        Read after "years": optional words, "experience", and then either the words before "experience" are only
        technologies ("3 years of Python experience") or "with/in/using/of/on" opens a tail up to the clause end
        (a comma, bracket or semicolon) that is only technologies and "and", "or", "/" ("with Python or Go"). A
        tail that says more ("in Python or Go building backend systems") leaves the figure standing.
        """
        m = _AFTER_YEARS.match(self.text[end:end + _TIE_CHARS])
        if m is None:
            return False
        pre = m.group("pre").strip()
        if pre and self.p.tech_only.match(pre) is not None:
            return True
        post = m.group("post")
        w = _EXPERIENCE_IN.match(post)
        if w is None:
            return False
        stop = _TIE_END.search(post, w.end())
        return self.p.tech_only.match(post[w.end():stop.start() if stop else len(post)]) is not None

    def counts(self, start: int, end: int, tier: bool = False) -> bool:
        """True when the number at text[start:end] (its first digit through "years") is a requirement.

        `tier` is a master's figure: it needs no experience word after it (the tier's own sentence has one) and
        may follow "or".
        """
        s, p = self.text, self.p
        if not tier:
            i = bisect.bisect_left(self.experience, end)
            if i == len(self.experience) or self.experience[i] - end > EXPERIENCE_WINDOW:
                return False
        if self.tied_to_technology(end):
            return False
        a, b = self.clause(start, end)
        if self.opens_with_preference or _any_in(self.preferred, a, b):
            return False
        if self.company_owns(start, a) and not _any_in(self.candidate_voice, a, b):
            return False
        if not tier and _OR_BEFORE.search(s[max(0, start - 8):start]):
            return False
        degree_end = min(len(s), end + CLAUSE_CHARS)  # bounded, so a huge sentence with no comma stays linear
        found = _DEGREE_CLAUSE_END.search(s, end, degree_end)
        if p.degree_alternative.search(s, end, found.start() if found else degree_end):
            return False
        before = s[max(0, start - _LOOK):start].split()[-COMPANY_AGE_WORDS:]
        after = s[end:end + _LOOK].split()[:COMPANY_AGE_WORDS]
        return p.company_age.search(" ".join(before + ["|"] + after)) is None


def _number(sentence: str, m: re.Match, p: _Patterns) -> tuple[int, int]:
    """The number a match asks for and where it starts: a range's low end; "up to 5" is 0.

    "3 and 5" without "between" is a range too, except where the first number is a version ("Python 3 and 5+
    years"): then the second is the figure.
    """
    n, at = int(m.group("n")), m.start()
    if m.group("joint") and m.group("joint").lower() == "and":
        before = sentence[max(0, m.start() - 12):m.start()]
        if not _BETWEEN_BEFORE.search(before) and p.tech_before.search(sentence[max(0, m.start() - 25):m.start()]):
            return int(m.group("hi")), m.start("hi")
    if p.upper_bound.search(sentence[max(0, m.start() - 20):m.start()]):
        return 0, at  # "up to 5 years" asks for nothing
    return n, at


class YearsAsked(NamedTuple):
    """What a body asks: `general` for everyone, `ms` for a master's degree ("or MS and 0+ years"), and the
    sentence of the general figure. Either figure is None when the body does not say it."""
    general: int | None
    ms: int | None
    quote: str


def read_years(body: str, cfg: Config) -> YearsAsked:
    """The largest general figure, the largest master's figure and the sentence that says the general one."""
    p = _Patterns(cfg)
    best: tuple[int, int, int] | None = None  # (years, start, end) in body
    best_ms: int | None = None
    pos = 0
    for sentence in sentences(body):
        offset = body.find(sentence, pos)
        pos = offset + len(sentence)
        if not _YEARS.search(sentence):
            continue
        read = _Sentence(sentence, p)
        for m in _YEARS.finditer(sentence):
            n, start = _number(sentence, m, p)
            if n > MAX_YEARS:
                continue
            if read.ms_tier(start, m.end()):
                if read.counts(start, m.end(), tier=True) and (best_ms is None or n > best_ms):
                    best_ms = n
            elif read.counts(start, m.end()) and (best is None or n > best[0]):
                best = (n, offset + start, offset + m.end())
    if best is None:
        return YearsAsked(None, best_ms, "")
    n, start, end = best
    return YearsAsked(n, best_ms, quote_around(body, start, end) or body[start:end])


def years_asked(body: str, cfg: Config) -> tuple[int | None, str]:
    """The largest number of years the body requires and the sentence that says it, or (None, "")."""
    r = read_years(body, cfg)
    return r.general, r.quote


def entry_wording(body: str, cfg: Config) -> str:
    """The sentence with entry-level wording (new grad, early career, "0-2 years") in the top of the body, or ""."""
    m = phrases(cfg.years["body_entry"]).search(body[:ENTRY_CHARS])
    if m is None:
        return ""
    return quote_around(body, m.start(), m.end()) or m.group(0)
