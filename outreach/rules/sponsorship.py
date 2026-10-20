"""Sponsorship stance and citizenship or clearance, read from a posting body.

Ported from refresh/finalcheck.py:61-68 and refresh/crawl_boards.py:127, with the reading the #4 plan sets. Only a
sentence that names a `topic` word (sponsor, visa, H-1B) is about sponsorship; "immigration" and "work
authorization" alone are not, because EEO lines say "without regard to ... immigration status". Benefit wording
("employer-sponsored health insurance") is blanked out first, and a clause with an equal opportunity marker says
nothing. Each topic sentence is cut into clauses at "but", "however", "and", "though", "while" and ", which", and a
negation is read before anything else, so a wrong "yes" from "will not sponsor ... OPT" cannot happen. A topic word
reads yes only next to a sponsorship context word (visa, H-1B, work authorization), so "executive sponsors" and
"Visa and Mastercard" say nothing. Every pattern is a list of literals with bounded gaps, and every window is
bounded, so reading stays linear in the body.
"""
import bisect
import functools
import re

from .config import Config, phrases
from .text import quote_around, sentences

# Where a sentence is cut into clauses: ", but", " but ", "; however", "however,", " though ", " while ", ", which"
# and, between two whole clauses only, " and " (see _and_joins_clauses). (sentences() already ends a sentence at a
# semicolon, so "; however" only shows up as "however," at the start of the next one.) "though", "while", "which"
# and a clause-joining "and" start a new subject often enough ("We offer visa sponsorship and relocation is not
# provided") that a generic negation after them is about something else.
_CLAUSE_BREAK = re.compile(
    r",\s{0,3}but(?!\w)|\sbut\s|;\s{0,3}however(?!\w)|(?<!\w)however,|(?P<and>\sand\s)|\sthough\s|\swhile\s"
    r"|,\s{0,3}which(?!\w)",
    re.IGNORECASE,
)
# The right side of an " and " is read this far, enough for a subject of three words and its verb.
_AND_LOOKAHEAD = 80
_PLAIN_WORD = re.compile(r"\w[\w'-]*")  # a word with no punctuation after it, so a subject never runs past a comma
_CURLY = chr(0x2019)  # a curly apostrophe, read as a straight one so "don't" in the lists matches "don" + curly + "t"

# A `negation_near` phrase ("not available", "now or in the future") is a refusal only when a topic word in the
# same clause is at most this many characters from it, and a `field_no` answer (": No") only when a topic word ends
# at most this many characters before it. 30 holds "Visa sponsorship for this role is not available" (18) and
# "will not consider candidates who need visa" (21), and leaves out "visas for strong candidates who need them now
# or in the future" (37).
NEAR_CHARS = 30
# Read past either end of a window, so a phrase that starts inside it is never cut by the search bounds. Longer
# than any citizenship_preference entry.
_SLACK = 20

# A clause's reading. NEUTRAL is a clause with nothing to say ("We are a growing team, but ...", "executive
# sponsors", an EEO line).
NO, YES, UNCLEAR, NEUTRAL = "no", "yes", "unclear", "neutral"


class _Patterns:
    """The [sponsorship] word lists and the non-US countries, compiled once per Config (see _patterns)."""

    def __init__(self, cfg: Config):
        s = cfg.sponsorship
        self.topic = phrases(s["topic"])
        self.context = phrases(s["context"])
        self.self_context = phrases(s["self_context"])
        self.benefit = phrases(s["benefit"])
        self.non_discrimination = phrases(s["non_discrimination"])
        self.negation = phrases(s["negation"])
        self.negation_near = phrases(s["negation_near"])
        self.clause_verbs = phrases(s["clause_verbs"])
        # ": No" as an ATS field value; the answer list goes through phrases() like every other list
        self.field_no = re.compile(r":\s{0,3}(?:" + phrases(s["field_no"]).pattern + ")", re.IGNORECASE)
        self.hedge = phrases(s["hedge"])
        self.opt = phrases(s["opt"])
        self.opt_acronyms = phrases(s["opt_acronyms"], case=True)  # "OPT", never "opt in" or "opt out"
        self.opt_support = phrases(s["opt_support"])
        self.non_us = phrases(cfg.location["non_us_countries"])
        self.citizenship = phrases(s["citizenship"])
        self.citizenship_negation = phrases(s["citizenship_negation"])
        self.citizenship_preference = phrases(s["citizenship_preference"])


@functools.lru_cache(maxsize=8)
def _patterns(cfg: Config) -> _Patterns:
    """One compiled set per Config (a Config hashes by identity): decide() over thousands of postings compiles once."""
    return _Patterns(cfg)


def _blank(m: re.Match) -> str:
    return " " * len(m.group(0))  # same length, so offsets still point into the body


def _starts_clause(sentence: str, at: int, p: _Patterns) -> bool:
    """Whether the text at `at` is one to three plain words and then a verb: "relocation is", "we sponsor"."""
    for i, token in enumerate(sentence[at:at + _AND_LOOKAHEAD].split(None, 4)[:4]):
        if p.clause_verbs.fullmatch(token):
            return 1 <= i <= 3  # a verb first ("and will not transfer") shares the left side's subject
        if not _PLAIN_WORD.fullmatch(token):
            return False  # punctuation ends the subject: "and visa sponsorship." has no verb of its own
    return False


def _clauses(sentence: str, p: _Patterns) -> list[tuple[int, int]]:
    """[start, end) of each clause of a sentence. " and " cuts only between two whole clauses, so a refusal on one
    side ("Visa sponsorship and relocation are not available") still covers the other."""
    spans, pos = [], 0
    left_verb, scanned = False, 0  # whether [pos, scanned) holds a verb; scanned only grows, so this stays linear
    for cut in _CLAUSE_BREAK.finditer(sentence):
        if cut.lastgroup == "and":
            if not _starts_clause(sentence, cut.end(), p):
                continue
            if not left_verb:
                left_verb = p.clause_verbs.search(sentence, max(scanned, pos), cut.start()) is not None
                scanned = cut.start()  # a space, so endpos never cuts a word in two
            if not left_verb:
                continue
        spans.append((pos, cut.start()))
        pos = scanned = cut.end()
        left_verb = False
    spans.append((pos, len(sentence)))
    return spans


def _opt_word(clause: str, p: _Patterns) -> re.Match | None:
    """The first OPT word: a spelled-out form in any case, or an acronym in capitals."""
    found = [m for m in (p.opt.search(clause), p.opt_acronyms.search(clause)) if m]
    return min(found, key=lambda m: m.start()) if found else None


def _supports_opt(clause: str, p: _Patterns) -> re.Match | None:
    """The OPT word of a clause that names OPT, STEM OPT or CPT with a support word and no negation."""
    opt = _opt_word(clause, p)
    if opt and p.opt_support.search(clause) and not p.negation.search(clause):
        return opt
    return None


def _near_topic(ends: list[int], starts: list[int], start: int, end: int) -> bool:
    """Whether a topic word (given by its sorted starts and ends) is within NEAR_CHARS of [start, end)."""
    i = bisect.bisect_left(ends, start - NEAR_CHARS)  # the first topic word that ends late enough
    return i < len(starts) and starts[i] <= end + NEAR_CHARS


def _after_topic(ends: list[int], start: int) -> bool:
    """Whether a topic word ends at most NEAR_CHARS before `start`."""
    i = bisect.bisect_right(ends, start) - 1
    return i >= 0 and start - ends[i] <= NEAR_CHARS


def _has_context(clause: str, topics: list[tuple[int, int]], p: _Patterns) -> bool:
    """Whether the clause names a self_context word ("Sponsorship is available"), or a context word apart from a
    topic word ("Visa" alone is its own topic word, not its own context)."""
    if p.self_context.search(clause):
        return True
    for c in p.context.finditer(clause):
        if len(topics) > 1 or c.end() <= topics[0][0] or c.start() >= topics[0][1]:
            return True  # with two topic words, any context word is apart from one of them; so this loop ends fast
    return False


def _read_clause(clause: str, p: _Patterns) -> tuple[str, re.Match | None]:
    """no, unclear, yes or neutral, and the match that decided it. A refusal first, then the equal opportunity
    marker ("We do not sponsor visas, regardless of seniority" is a refusal), then hedges and countries."""
    if m := p.negation.search(clause):
        return NO, m
    topics = [t.span() for t in p.topic.finditer(clause)]
    if topics:
        starts, ends = [a for a, _ in topics], [b for _, b in topics]
        for m in p.negation_near.finditer(clause):
            if _near_topic(ends, starts, m.start(), m.end()):
                return NO, m
        for m in p.field_no.finditer(clause) if ":" in clause else ():
            if _after_topic(ends, m.start()):
                return NO, m  # "Sponsorship Available: No"
    if p.non_discrimination.search(clause):
        return NEUTRAL, None  # "We do not discriminate based on immigration or visa status."
    if m := p.hedge.search(clause):
        return UNCLEAR, m
    if m := p.non_us.search(clause):
        return UNCLEAR, m
    if topics and _has_context(clause, topics, p):
        return YES, p.topic.search(clause)
    return NEUTRAL, None  # no topic word, or one with no sponsorship context ("executive sponsors")


def _preferred(text: str, a: int, b: int, m: re.Match, p: _Patterns) -> bool:
    """Whether a citizenship_preference sits within NEAR_CHARS of the requirement m, in clause [a, b), with no comma
    between ("Security clearance is a plus." yes; "Clearance required, TS/SCI preferred." no, for the first one)."""
    lo, hi = max(a, m.start() - NEAR_CHARS - _SLACK), min(b, m.end() + NEAR_CHARS + _SLACK)
    for w in p.citizenship_preference.finditer(text, lo, hi):
        if w.end() <= m.start():
            gap = text[w.end():m.start()]
        elif w.start() >= m.end():
            gap = text[m.end():w.start()]
        else:
            gap = ""  # the two overlap
        if len(gap) <= NEAR_CHARS and "," not in gap:
            return True
    return False


class _Reading:
    """What one sentence says: a stance (or None) and where in the body the deciding words are."""

    def __init__(self, stance: str | None, start: int, end: int, opt: bool):
        self.stance = stance  # no, yes, unclear, opt_only, or None for a sentence that only supports OPT
        self.start, self.end = start, end
        self.opt = opt  # the sentence has a clause that supports OPT


def _read_sentence(sentence: str, offset: int, p: _Patterns) -> _Reading | None:
    """A topic sentence's stance from its clauses; for a sentence without a topic word, OPT support or nothing."""
    text = sentence.replace(_CURLY, "'") if _CURLY in sentence else sentence  # same length: offsets still hold
    topic = p.topic.search(text) is not None
    if topic:
        text, blanked = p.benefit.subn(_blank, text)  # "employer-sponsored health insurance" is not about visas
        topic = not blanked or p.topic.search(text) is not None
    names_opt = _opt_word(text, p) is not None  # read per clause only when the sentence names OPT at all
    if not topic:
        if names_opt:
            for a, b in _clauses(text, p):  # "We support OPT and CPT." counts only next to a "no" elsewhere
                if m := _supports_opt(text[a:b], p):
                    return _Reading(None, offset + a + m.start(), offset + a + m.end(), True)
        return None
    found: dict[str, tuple[int, int]] = {}  # reading -> the first match that gave it, as body offsets
    opt = False
    for a, b in _clauses(text, p):
        clause = text[a:b]
        if names_opt and (m := _supports_opt(clause, p)):
            opt = True
            found.setdefault("opt", (offset + a + m.start(), offset + a + m.end()))
        reading, m = _read_clause(clause, p)
        if reading != NEUTRAL:
            found.setdefault(reading, (offset + a + m.start(), offset + a + m.end()))
    if not found:
        return None  # every clause is neutral: "executive sponsors", "Visa and Mastercard", an EEO line
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
    p = _patterns(cfg)
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
    clearance required." and an EEO line naming citizenship never count. A preference ("a plus", "preferred")
    cancels only the requirement next to it (see _preferred).
    """
    p = _patterns(cfg)
    pos = 0
    for sentence in sentences(body or ""):
        offset = body.find(sentence, pos)
        pos = offset + len(sentence)
        text = sentence.replace(_CURLY, "'") if _CURLY in sentence else sentence
        clauses: list[tuple[int, int]] = []
        starts: list[int] = []
        negated: dict[int, bool] = {}  # clause index -> has a citizenship_negation, read once per clause
        for m in p.citizenship.finditer(text):
            if not clauses:
                clauses = _clauses(text, p)
                starts = [a for a, _ in clauses]
            i = bisect.bisect_right(starts, m.start()) - 1
            if i not in negated:
                a, b = clauses[i]
                negated[i] = p.citizenship_negation.search(text[a:b]) is not None
            if not negated[i] and not _preferred(text, *clauses[i], m, p):
                start, end = offset + m.start(), offset + m.end()
                return True, quote_around(body, start, end) or body[start:end]
    return False, ""
