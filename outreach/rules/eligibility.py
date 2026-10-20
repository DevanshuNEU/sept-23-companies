"""Graduation eligibility and the 2027 start flag, read from a posting's title and body.

Ported from refresh/eligibility.py:32-91 in the same order, with the 2027-start reading of refresh/finalcheck.py:48-56
and refresh/final_merge.py:17, 62. A dated graduation sentence decides first; then wording that lets graduates in;
then a current-student requirement; then a title naming a graduating cohort. Per the #9 ruling a cohort title names
graduates ("2027 University Graduate"), while "New Grad 2027" names a start date: that is a flag for the list, never
a reason to hide. The graduation month is [candidate].graduated. Dates are found once per sentence and every
pattern has bounded gaps, so reading stays linear in the body.
"""
import bisect
import re

from .config import Config, phrases
from .text import quote_around, sentences

DATE_BEFORE = 40  # a date counts when it sits this many characters before a graduation word
DATE_AFTER = 140  # or this many after it, so a year elsewhere in a run-on paragraph does not (eligibility.py:56-62)
START_YEAR = "2027"  # the year starts_2027 flags in a title

_DASHES = "\\-" + chr(0x2010) + chr(0x2011) + chr(0x2013)  # hyphen, Unicode hyphens, en dash (see config.py)
_MONTH = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|"
          r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|spring|summer|fall|autumn|winter")
# A year from 2023 to 2029, with an optional month or season before it ("May 2027", "Summer, 2027", "May/June 2027").
_DATE = re.compile(
    r"(?:(?<!\w)(?P<month>" + _MONTH + r")(?!\w)(?:\s{0,2}[/&" + _DASHES + r"]\s{0,2}[a-z]{1,9})?\s{0,3},?\s{0,3})?"
    r"(?<!\d)(?P<year>202[3-9])(?!\d)",
    re.IGNORECASE,
)
# What follows an upper_bound word for it to bound a date: "by Summer 2027", "no later than the end of 2027".
# The years are the old range (eligibility.py:76).
_BOUND_TAIL = r"\s{1,3}(?:the\s{1,3})?(?:end\s{1,3}of\s{1,3})?(?:[a-z]{1,9},?\s{1,3})?202[5-8](?!\d)"
_TITLE_YEAR = re.compile(r"(?<!\d)" + START_YEAR + r"(?!\d)")
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10,
           "nov": 11, "dec": 12}
# A season as a month, as the old system read it (eligibility.py:33-35): a spring graduation is May, summer is
# August, and fall or winter is December.
_SEASONS = {"spring": 5, "summer": 8, "fall": 12, "autumn": 12, "winter": 12}


class _Patterns:
    """The [eligibility] word lists, compiled once per call."""

    def __init__(self, cfg: Config):
        e = cfg.eligibility
        self.grad = phrases(e["grad_words"])
        self.current_student = phrases(e["current_student"])
        self.graduates_welcome = phrases(e["graduates_welcome"])
        self.soft = phrases(e["soft"])
        self.company_history = phrases(e["company_history"])
        self.upper_bound = re.compile(phrases(e["upper_bound"]).pattern + _BOUND_TAIL, re.IGNORECASE)
        self.cohort_title = phrases(e["cohort_title"])
        self.start_2027 = phrases(e["start_2027"])
        self.immediate_start = phrases(e["immediate_start"])


def _month_number(cfg: Config) -> int:
    """[candidate].graduated ("2026-05") as a month count, year * 12 + month."""
    year, month = cfg.candidate["graduated"].split("-")
    return int(year) * 12 + int(month)


def _month_of(name: str | None) -> int | None:
    if not name:
        return None
    name = name.lower()
    return _SEASONS.get(name) or _MONTHS[name[:3]]


def _dates_near_grad_words(sentence: str, grads: list[re.Match]) -> list[tuple[int, int | None]]:
    """(year, month or None) of each date within DATE_BEFORE or DATE_AFTER characters of a graduation word."""
    dates = list(_DATE.finditer(sentence))
    if not dates:
        return []
    starts = [d.start() for d in dates]
    near: set[int] = set()
    for g in grads:
        lo, hi = g.start() - DATE_BEFORE, g.end() + DATE_AFTER
        i = bisect.bisect_left(starts, lo)
        while i < len(dates) and starts[i] < hi:
            if dates[i].end() <= hi:
                near.add(i)
            i += 1
    return [(int(dates[i].group("year")), _month_of(dates[i].group("month"))) for i in sorted(near)]


def _window_holds(sentence: str, dates: list[tuple[int, int | None]], graduated: int, p: _Patterns) -> bool:
    """True when the graduation month falls in the window the dates draw (a year alone spans January to December)."""
    lo = min(year * 12 + (month or 1) for year, month in dates)
    hi = max(year * 12 + (month or 12) for year, month in dates)
    if p.upper_bound.search(sentence):
        lo = 0  # "by Summer 2027" bounds the window above only: anyone who already graduated qualifies
    return lo <= graduated <= hi


def _quote(body: str, m: re.Match, offset: int = 0) -> str:
    start, end = offset + m.start(), offset + m.end()
    return quote_around(body, start, end) or body[start:end]


def eligibility(title: str, body: str, cfg: Config) -> tuple[str, str | None, str]:
    """(ok | excluded, grad_window | current_student | cohort_title | None, quote).

    The quote is the deciding sentence, or the title for a cohort title, and "" when nothing decided.
    """
    p = _Patterns(cfg)
    body = body or ""
    graduated = _month_number(cfg)
    # 1) a graduation sentence with dates decides
    pos = 0
    for sentence in sentences(body):
        offset = body.find(sentence, pos)
        pos = offset + len(sentence)
        grads = list(p.grad.finditer(sentence))
        if not grads or p.company_history.search(sentence):
            continue  # no graduation word, or the company's own history ("our founders graduated from YC")
        dates = _dates_near_grad_words(sentence, grads)
        if not dates:
            continue
        quote = _quote(body, grads[0], offset)
        if p.graduates_welcome.search(sentence) or p.soft.search(sentence):
            return "ok", None, quote
        if _window_holds(sentence, dates, graduated, p):
            return "ok", None, quote
        return "excluded", "grad_window", quote
    # 2) wording that lets graduates in
    if m := p.graduates_welcome.search(body):
        return "ok", None, _quote(body, m)
    # 3) a current-student requirement
    if m := p.current_student.search(body):
        return "excluded", "current_student", _quote(body, m)
    # 4) a title that names a graduating cohort
    if p.cohort_title.search(title or ""):
        return "excluded", "cohort_title", title
    return "ok", None, ""


def starts_2027(title: str, body: str, cfg: Config) -> bool:
    """True for start wording ("2027 start", "starts in July 2027") or a 2027 title that does not name graduates.

    An immediate-start phrase anywhere cancels it. Graduation wording ("class of 2027") is not a start date.
    """
    p = _Patterns(cfg)
    title, body = title or "", body or ""
    if p.immediate_start.search(title) or p.immediate_start.search(body):
        return False
    if p.start_2027.search(title) or p.start_2027.search(body):
        return True
    return bool(_TITLE_YEAR.search(title)) and not p.cohort_title.search(title)
