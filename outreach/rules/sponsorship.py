"""Sponsorship stance and citizenship or clearance, read from a posting body.

Ported from refresh/finalcheck.py:61-68 and refresh/crawl_boards.py:127, with the reading the #4 plan sets. Only a
sentence that names a `topic` word (sponsor, visa, H-1B) is about sponsorship; "immigration" and "work
authorization" alone are not, because EEO lines say "without regard to ... immigration status". Each such sentence
is cut into clauses at "but" and "however", and a negation is read before anything else, so a wrong "yes" from
"will not sponsor ... OPT" cannot happen. Every pattern is a list of literals with bounded gaps, so reading stays
linear in the body.
"""
import bisect
import re

from .config import Config, phrases
from .text import quote_around, sentences

# Where a sentence is cut into clauses: ", but", " but ", "; however" and "however,". (sentences() already ends a
# sentence at a semicolon, so "; however" only shows up as "however," at the start of the next one.)
_CLAUSE_BREAK = re.compile(r",\s{0,3}but(?!\w)|\sbut\s|;\s{0,3}however(?!\w)|(?<!\w)however,", re.IGNORECASE)
_CURLY = chr(0x2019)  # a curly apostrophe, read as a straight one so "don't" in the lists matches "don" + curly + "t"

# A clause's reading. NEUTRAL is a clause with no topic word and nothing to say ("We are a growing team, but ...").
NO, YES, UNCLEAR, NEUTRAL = "no", "yes", "unclear", "neutral"


class _Patterns:
    """The [sponsorship] word lists and the non-US countries, compiled once per call."""

    def __init__(self, cfg: Config):
        s = cfg.sponsorship
        self.topic = phrases(s["topic"])
        self.negation = phrases(s["negation"])
        self.hedge = phrases(s["hedge"])
        self.opt = phrases(s["opt"])
        self.opt_support = phrases(s["opt_support"])
        self.non_us = phrases(cfg.location["non_us_countries"])
        self.citizenship = phrases(s["citizenship"])
        self.citizenship_negation = phrases(s["citizenship_negation"])


def _clauses(sentence: str) -> list[tuple[int, int]]:
    """[start, end) of each clause of a sentence."""
    spans, pos = [], 0
    for cut in _CLAUSE_BREAK.finditer(sentence):
        spans.append((pos, cut.start()))
        pos = cut.end()
    spans.append((pos, len(sentence)))
    return spans


def _supports_opt(clause: str, p: _Patterns) -> re.Match | None:
    """The OPT word of a clause that names OPT, STEM OPT or CPT with a support word and no negation."""
    opt = p.opt.search(clause)
    if opt and p.opt_support.search(clause) and not p.negation.search(clause):
        return opt
    return None


def _read_clause(clause: str, p: _Patterns) -> tuple[str, re.Match | None]:
    """no, unclear, yes or neutral, and the match that decided it. Negation first, then hedges and countries."""
    if m := p.negation.search(clause):
        return NO, m
    if m := p.hedge.search(clause):
        return UNCLEAR, m
    if m := p.non_us.search(clause):
        return UNCLEAR, m
    if m := p.topic.search(clause):
        return YES, m
    return NEUTRAL, None


class _Reading:
    """What one sentence says: a stance (or None) and where in the body the deciding words are."""

    def __init__(self, stance: str | None, start: int, end: int, opt: bool):
        self.stance = stance  # no, yes, unclear, opt_only, or None for a sentence that only supports OPT
        self.start, self.end = start, end
        self.opt = opt  # the sentence has a clause that supports OPT


def _read_sentence(sentence: str, offset: int, p: _Patterns) -> _Reading | None:
    """A topic sentence's stance from its clauses; for a sentence without a topic word, OPT support or nothing."""
    text = sentence.replace(_CURLY, "'")  # same length, so offsets still point into the body
    if not p.topic.search(text):
        for a, b in _clauses(text):  # "We support OPT and CPT." counts only next to a "no" elsewhere
            if m := _supports_opt(text[a:b], p):
                return _Reading(None, offset + a + m.start(), offset + a + m.end(), True)
        return None
    found: dict[str, tuple[int, int]] = {}  # reading -> the first match that gave it, as body offsets
    opt = False
    for a, b in _clauses(text):
        clause = text[a:b]
        if m := _supports_opt(clause, p):
            opt = True
            found.setdefault("opt", (offset + a + m.start(), offset + a + m.end()))
        reading, m = _read_clause(clause, p)
        if reading != NEUTRAL:
            found.setdefault(reading, (offset + a + m.start(), offset + a + m.end()))
    if not found:
        return None  # cannot happen while no topic word contains a clause break; kept so a list edit cannot crash
    if NO in found and opt:
        stance = "opt_only"  # "We support OPT, but we are unable to sponsor H-1B visas."
    elif UNCLEAR in found or (NO in found and YES in found):
        stance = UNCLEAR  # a hedge or a country qualifies the sentence; or it says yes and no at once
    elif NO in found:
        stance = NO
    else:
        stance = YES  # a topic word sits in some clause, and that clause read yes
    start, end = found.get(stance, found.get(NO, found.get(YES, found.get("opt"))))
    return _Reading(stance, start, end, opt)


def _quote(body: str, reading: _Reading) -> str:
    return quote_around(body, reading.start, reading.end) or body[reading.start:reading.end]


def sponsorship_stance(body: str, cfg: Config) -> tuple[str, str]:
    """(yes | no | opt_only | unclear | silent, quote). The quote is the deciding sentence, and "" for silent."""
    p = _Patterns(cfg)
    readings: list[_Reading] = []
    pos = 0
    for sentence in sentences(body or ""):
        offset = body.find(sentence, pos)
        pos = offset + len(sentence)
        if reading := _read_sentence(sentence, offset, p):
            readings.append(reading)
    by_stance: dict[str, _Reading] = {}
    for reading in readings:
        if reading.stance is not None:
            by_stance.setdefault(reading.stance, reading)
    if "opt_only" in by_stance:
        return "opt_only", _quote(body, by_stance["opt_only"])
    opt = next((r for r in readings if r.opt), None)
    if NO in by_stance and YES not in by_stance:
        if opt is not None:  # "We support OPT and CPT. We are unable to sponsor H-1B visas."
            first, second = sorted((opt, by_stance[NO]), key=lambda r: r.start)
            return "opt_only", _quote(body, first) + " " + _quote(body, second)
        return NO, _quote(body, by_stance[NO])
    if YES in by_stance and NO not in by_stance:
        return YES, _quote(body, by_stance[YES])
    if by_stance:  # yes and no in different sentences, or only unclear ones
        first = next(r for r in readings if r.stance in (YES, NO, UNCLEAR))
        return UNCLEAR, _quote(body, first)
    return "silent", ""


def citizenship_required(body: str, cfg: Config) -> tuple[bool, str]:
    """(True, quote) for the first clause that requires citizenship or a clearance; (False, "") otherwise.

    A clause that also says "not required", "no clearance" or "regardless of" is not a requirement, so "No security
    clearance required." and an EEO line naming citizenship never count.
    """
    p = _Patterns(cfg)
    pos = 0
    for sentence in sentences(body or ""):
        offset = body.find(sentence, pos)
        pos = offset + len(sentence)
        text = sentence.replace(_CURLY, "'")
        clauses: list[tuple[int, int]] = []
        starts: list[int] = []
        negated: dict[int, bool] = {}  # clause index -> has a citizenship_negation, read once per clause
        for m in p.citizenship.finditer(text):
            if not clauses:
                clauses = _clauses(text)
                starts = [a for a, _ in clauses]
            i = bisect.bisect_right(starts, m.start()) - 1
            if i not in negated:
                a, b = clauses[i]
                negated[i] = p.citizenship_negation.search(text[a:b]) is not None
            if not negated[i]:
                start, end = offset + m.start(), offset + m.end()
                return True, quote_around(body, start, end) or body[start:end]
    return False, ""
