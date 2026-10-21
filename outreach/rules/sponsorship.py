"""Sponsorship stance and citizenship or clearance, read from a posting body.

Ported from refresh/finalcheck.py:61-68 and refresh/crawl_boards.py:127, with the reading the #4 plan sets. Only a
sentence that names a `topic` word (sponsor, visa, H-1B) is about sponsorship; "immigration" and "work
authorization" alone are not, because EEO lines say "without regard to ... immigration status". Benefit wording
("employer-sponsored health insurance") is blanked out first, and a clause with an equal opportunity marker says
nothing. Each topic sentence is cut into clauses at "but", "however", "and", "though", "while" and ", which", and a
negation is read before anything else, so a wrong "yes" from "will not sponsor ... OPT" cannot happen. A refusal counts only in a clause with a topic word, and
a clause is also cut at a comma or spaced dash before a new subject, at ", except" and around a parenthetical that has a
verb, so "Visa sponsorship is available, relocation is not provided" reads yes. A clause with a topic word and any
negator reads unclear, never yes, but it still counts as a yes against a refusal in another sentence, so "We sponsor
H-1B visas at no cost to you. We do not sponsor contractors." reads unclear, never no. A topic word
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
# and, only in the cases _clauses lists, " and ". (sentences() already ends a sentence at a
# semicolon, so "; however" only shows up as "however," at the start of the next one.) "though", "while", "which"
# and a clause-joining "and" start a new subject often enough ("We offer visa sponsorship and relocation is not
# provided") that a generic negation after them is about something else.
#
# Three more cuts, for sponsorship only (citizenship keeps the cuts above): ", except", which is where an exception
# starts ("unable to hire candidates who need sponsorship, except for OPT"); a comma or a spaced dash followed by a new
# subject ("Visa sponsorship is available, relocation is not provided"), cut only when the left side holds a verb and
# the right side is one to three plain words and a verb, like " and "; and a parenthesis (see _split_parens).
_DASH_CHARS = "\\-" + chr(0x2010) + chr(0x2011) + chr(0x2013) + chr(0x2014)
_CLAUSE_BREAK = re.compile(
    r",\s{0,3}but(?!\w)|\sbut\s|;\s{0,3}however(?!\w)|(?<!\w)however,|(?P<and>\sand\s)|\sthough\s|\swhile\s"
    r"|,\s{0,3}which(?!\w)|(?P<except>,?\s(?=except\s))|(?P<comma>,\s{1,3})|(?P<dash>\s[" + _DASH_CHARS + r"]{1,2}\s)",
    re.IGNORECASE,
)
_FINE_CUTS = ("except", "comma", "dash")
# A parenthetical that holds a verb of its own is read as a clause of its own: "(we do not provide relocation)".
_PARENS = re.compile(r"\([^()]{0,300}\)")
_CURLY = chr(0x2019)  # a curly apostrophe, read as a straight one so "don't" in the lists matches "don" + curly + "t"

# A `negation_near` phrase ("not available", "now or in the future") is a refusal only when a topic word in the
# same clause is at most this many characters from it, and a `field_no` answer (": No") only when a topic word ends
# at most this many characters before it. 30 holds "Visa sponsorship for this role is not available" (18) and
# "will not consider candidates who need visa" (21), and leaves out "visas for strong candidates who need them now
# or in the future" (37).
NEAR_CHARS = 30
# How far before a `negation_cued` phrase ("now or in the future") its cue word ("not", "without") may sit.
CUE_CHARS = 60
# Read past either end of a window, so a phrase that starts inside it is never cut by the search bounds. Longer
# than any citizenship_preference entry.
_SLACK = 20

# A clause's reading. NEUTRAL is a clause with nothing to say ("We are a growing team, but ...", "executive
# sponsors", an EEO line). LEANS_YES is a yes clause that holds a negator ("We sponsor H-1B visas at no cost"): the
# sentence reads unclear, and the body counts it as a yes against a refusal elsewhere.
NO, YES, UNCLEAR, NEUTRAL, LEANS_YES = "no", "yes", "unclear", "neutral", "leans_yes"


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
        # The right side of an " and ": up to three plain words that are neither verbs nor "and" or "or", then a verb
        # (which may end the sentence: "and relocation not provided."). One match per "and", possessive so it never
        # backtracks; the cheap "and"/"or" test first, so a run of "visa and visa and" fails fast.
        verb = self.clause_verbs.pattern
        self.right_side = re.compile(
            r"((?:(?!(?:and|or)\s)(?!" + verb + r")\w[\w'-]*+\s{1,3}+){0,3}+)" + verb, self.clause_verbs.flags
        )
        # ": No" as an ATS field value; the answer list goes through phrases() like every other list
        self.field_no = re.compile(r":\s{0,3}(?:" + phrases(s["field_no"]).pattern + ")", re.IGNORECASE)
        self.hedge = phrases(s["hedge"])
        self.negation_elided = phrases(s["negation_elided"])
        self.elided_complete = phrases(s["elided_complete"])
        self.continuation = phrases(s["continuation"])
        self.not_subject = phrases(s["not_subject"])
        self.negation_cued = phrases(s["negation_cued"])
        self.negation_cue = phrases(s["negation_cue"])
        # "not", "no", ...; and any contraction ending in n't ("isn't", "can't", "weren't"), apostrophes already straight
        self.negator = re.compile(phrases(s["negators"]).pattern + r"|(?<!\w)\w+n't(?!\w)", re.IGNORECASE)
        self.opt = phrases(s["opt"])
        self.opt_acronyms = phrases(s["opt_acronyms"], case=True)  # "OPT", never "opt in" or "opt out"
        self.opt_support = phrases(s["opt_support"])
        self.non_us = phrases(cfg.location["non_us_countries"])
        forms = s["citizenship_us_forms"]
        templates = [t.replace("{us}", form) for t in s["citizenship_templates"] for form in forms]
        self.citizenship = phrases(s["citizenship"] + templates)
        self.itar = phrases(s["itar"])
        self.itar_cue = phrases(s["itar_cue"])
        self.citizenship_negation = phrases(s["citizenship_negation"])
        self.citizenship_preference = phrases(s["citizenship_preference"])


@functools.lru_cache(maxsize=8)
def _patterns(cfg: Config) -> _Patterns:
    """One compiled set per Config (a Config hashes by identity): decide() over thousands of postings compiles once."""
    return _Patterns(cfg)


def _blank(m: re.Match) -> str:
    return " " * len(m.group(0))  # same length, so offsets still point into the body


def _blank_benefits(text: str, p: _Patterns) -> tuple[str, int]:
    """The text with every benefit phrase blanked, and how many were. One that overlaps a refusal stays: "We cannot be
    the sponsor of your visa" is a refusal, and "the sponsor of" in it is not a business sponsor."""
    benefits = list(p.benefit.finditer(text))
    if not benefits:
        return text, 0
    refusals = [m.span() for m in p.negation.finditer(text)]
    starts = [a for a, _ in refusals]
    pieces, last, blanked = [], 0, 0
    for m in benefits:
        i = bisect.bisect_right(starts, m.start()) - 1
        if (i >= 0 and refusals[i][1] > m.start()) or (i + 1 < len(starts) and starts[i + 1] < m.end()):
            continue
        pieces.append(text[last:m.start()])
        pieces.append(_blank(m))
        last = m.end()
        blanked += 1
    pieces.append(text[last:])
    return "".join(pieces), blanked


def _split_parens(text: str, p: _Patterns) -> tuple[str, list[tuple[int, int]]]:
    """The text with each parenthetical that holds a verb blanked (same length, so offsets hold), and the span of
    what was inside each. "We sponsor H-1B visas (we do not provide relocation)." reads as two clauses; one with no
    verb ("(H-1B, TN)", "(OPT)") stays where it is, since it only glosses the words around it."""
    spans = []
    pieces, last = [], 0
    for m in _PARENS.finditer(text):
        if not p.clause_verbs.search(text, m.start() + 1, m.end() - 1):
            continue
        spans.append((m.start() + 1, m.end() - 1))
        pieces.append(text[last:m.start()])
        pieces.append(" " * len(m.group(0)))
        last = m.end()
    if not spans:
        return text, spans
    pieces.append(text[last:])
    return "".join(pieces), spans


def _right_side(sentence: str, at: int, p: _Patterns) -> str | None:
    """What follows an " and ": "clause" for one to three plain words and then a verb ("relocation is", "we
    sponsor"), "verb" for a verb first ("welcome OPT", "do not offer relocation"), None otherwise (punctuation
    ends a subject, so "and visa sponsorship." has no verb of its own)."""
    m = p.right_side.match(sentence, at)
    if m is None:
        return None
    return "clause" if m.group(1) else "verb"


def _clauses(sentence: str, p: _Patterns, *, any_right: bool = False) -> list[tuple[int, int]]:
    """[start, end) of each clause of a sentence.

    " and " cuts only when the left side holds a verb and the right side is a whole clause ("and relocation is not
    provided"), or starts with a verb and either names OPT ("and welcome OPT candidates") or holds its own refusal
    ("and do not offer relocation"). Otherwise a refusal on one side ("Visa sponsorship and relocation are not
    available", "We are unable to sponsor and transfer H-1B visas") still covers the other. `any_right` (citizenship)
    cuts whenever the left side holds a verb and the right side has one too, whatever comes first ("clearance
    required and travel not required", "a U.S. citizen and not require relocation"); a bare noun on either side
    ("Security clearance and travel are not required") is still one clause.
    """
    cuts = list(_CLAUSE_BREAK.finditer(sentence))
    spans, pos = [], 0
    left_verb, scanned = False, 0  # whether [pos, scanned) holds a verb; scanned only grows, so this stays linear
    # Where OPT words and refusals start, each found in one pass the first time a verb-first "and" needs them.
    opt_starts: list[int] | None = None
    neg_starts: list[int] = []
    for k, cut in enumerate(cuts):
        group = cut.lastgroup
        if group in _FINE_CUTS and any_right:
            continue  # citizenship reads "Security clearance (not required)" and "required, TS/SCI preferred" whole
        if group in ("and", "comma", "dash"):
            right = _right_side(sentence, cut.end(), p)
            if right is None:
                continue
            if group != "and" and (right != "clause" or p.not_subject.match(sentence, cut.end())):
                continue  # a comma or dash cuts only before a new subject, never before ", not available"
            if not left_verb:
                left_verb = p.clause_verbs.search(sentence, max(scanned, pos), cut.start()) is not None
                scanned = cut.start()  # a space, so endpos never cuts a word in two
            if not left_verb:
                continue
            if group == "and" and right == "verb" and not any_right:
                if opt_starts is None:
                    opt_starts = sorted(m.start() for pat in (p.opt, p.opt_acronyms) for m in pat.finditer(sentence))
                    neg_starts = [m.start() for m in p.negation.finditer(sentence)]
                if not opt_starts and not neg_starts:
                    continue  # nothing in the sentence could make a verb-first "and" a cut
                right_end = cuts[k + 1].start() if k + 1 < len(cuts) else len(sentence)  # never inside a word
                i = bisect.bisect_left(opt_starts, pos)
                names_opt = i < len(opt_starts) and opt_starts[i] < right_end  # on either side
                j = bisect.bisect_left(neg_starts, cut.end())
                own_refusal = j < len(neg_starts) and neg_starts[j] < right_end  # on the right side
                if not names_opt and not own_refusal:
                    continue  # "and transfer H-1B visas", "and will not transfer": one subject, one refusal
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


def _negator(clause: str, p: _Patterns) -> re.Match | None:
    """The first negator of the clause. A "no" or "none" right after a colon answers another field ("Relocation: No,
    H-1B sponsorship: Yes") and is not one."""
    for m in p.negator.finditer(clause):
        if m.group(0).lower() in ("no", "none") and clause[max(0, m.start() - 4):m.start()].rstrip().endswith(":"):
            continue
        return m
    return None


def _read_clause(clause: str, p: _Patterns) -> tuple[str, re.Match | None]:
    """no, unclear, yes or neutral, and the match that decided it. A refusal first, then the equal opportunity
    marker ("We do not sponsor visas, regardless of seniority" is a refusal), then hedges and countries. A refusal
    counts only in a clause with a topic word: "we do not provide relocation" is about relocation."""
    topics = [t.span() for t in p.topic.finditer(clause)]
    if topics and (m := p.negation.search(clause)):
        return NO, m
    if m := p.negation_elided.search(clause):
        if topics:
            return NO, m
        # "we are unable at this time" refuses the sentence's topic; "we are unable to provide relocation" only
        # tells us something is unclear.
        tail = clause[m.end():].lstrip()
        if re.match(r"to\s", tail, re.IGNORECASE) and not p.elided_complete.match(tail):
            return UNCLEAR, m
        return NO, m
    if topics:
        starts, ends = [a for a, _ in topics], [b for _, b in topics]
        for m in p.negation_near.finditer(clause):
            if _near_topic(ends, starts, m.start(), m.end()):
                return NO, m
        for m in p.negation_cued.finditer(clause):
            if _near_topic(ends, starts, m.start(), m.end()) and p.negation_cue.search(
                    clause, max(0, m.start() - CUE_CHARS), m.start()):
                return NO, m  # "must not require sponsorship now or in the future"; not "available now or in the future"
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
        # "yes" needs a clause with no negator at all: a refusal the lists do not know ("Sponsorship is not part of
        # this role", "visa sponsorship for candidates with no gaps") reads unclear, never as the best answer. It
        # still leans yes, so a refusal in another sentence cannot turn the body into no.
        if m := _negator(clause, p):
            return LEANS_YES, m
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

    def __init__(self, stance: str | None, start: int, end: int, opt: bool, leans_yes: bool = False):
        self.stance = stance  # no, yes, unclear, opt_only, or None for a sentence that only supports OPT
        self.start, self.end = start, end
        self.opt = opt  # the sentence has a clause that supports OPT
        self.leans_yes = leans_yes  # an unclear sentence with a yes clause that holds a negator


def _read_sentence(sentence: str, offset: int, p: _Patterns) -> _Reading | None:
    """A topic sentence's stance from its clauses; for a sentence without a topic word, OPT support or nothing."""
    text = sentence.replace(_CURLY, "'") if _CURLY in sentence else sentence  # same length: offsets still hold
    topic = p.topic.search(text) is not None
    if topic:
        text, blanked = _blank_benefits(text, p)  # "employer-sponsored health insurance" is not about visas
        topic = not blanked or p.topic.search(text) is not None
    names_opt = _opt_word(text, p) is not None  # read per clause only when the sentence names OPT at all
    if not topic:
        if names_opt:
            for a, b in _clauses(text, p):  # "We support OPT and CPT." counts only next to a "no" elsewhere
                if m := _supports_opt(text[a:b], p):
                    return _Reading(None, offset + a + m.start(), offset + a + m.end(), True)
        return None
    found: dict[str, tuple[int, int]] = {}  # reading -> the first match that gave it, as body offsets
    opt = leans_yes = False
    outer, parens = _split_parens(text, p)
    spans = [(a, b, outer) for a, b in _clauses(outer, p)] + [(a, b, text) for a, b in parens]
    for a, b, source in spans:
        clause = source[a:b]
        if names_opt and source is outer and (m := _supports_opt(clause, p)):
            opt = True
            found.setdefault("opt", (offset + a + m.start(), offset + a + m.end()))
        reading, m = _read_clause(clause, p)
        if reading == NEUTRAL and a > 0 and NO in found and not p.topic.search(clause):
            # "not provided for interns, but is for full-time hires": a clause with no subject of its own, after a
            # refusal, says the refusal is not the whole story
            lead = len(clause) - len(clause.lstrip())
            if m := p.continuation.match(clause, lead):
                reading = UNCLEAR
        if reading == YES and source is not outer:
            reading = NEUTRAL  # a parenthetical only qualifies: "(i.e. H1B, F-1 OPT, TN)" is not a yes
        if reading == LEANS_YES:
            leans_yes = leans_yes or source is outer  # a parenthetical only qualifies, so it never leans the body
            reading = UNCLEAR
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
    return _Reading(stance, start, end, opt, leans_yes and stance == UNCLEAR)


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
    leans_yes = any(r.leans_yes for r in readings)  # "We sponsor H-1B visas at no cost to you." counts as a yes here
    if NO in by_stance and YES not in by_stance and not leans_yes:
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
        cued: dict[int, bool] = {}  # clause index -> has an itar_cue, read once per clause
        hits = [(m, False) for m in p.citizenship.finditer(text)] + [(m, True) for m in p.itar.finditer(text)]
        if hits and len(hits) > 1:
            hits.sort(key=lambda hit: hit[0].start())
        for m, bare_itar in hits:
            if not clauses:
                clauses = _clauses(text, p, any_right=True)
                starts = [a for a, _ in clauses]
            i = bisect.bisect_right(starts, m.start()) - 1
            if bare_itar:
                if i not in cued:
                    a, b = clauses[i]
                    cued[i] = p.itar_cue.search(text, a, b) is not None
                if not cued[i]:
                    continue  # "ITAR compliant", "no ITAR restrictions": no cue, no requirement
            if i not in negated:
                a, b = clauses[i]
                negated[i] = p.citizenship_negation.search(text[a:b]) is not None
            if not negated[i] and not _preferred(text, *clauses[i], m, p):
                start, end = offset + m.start(), offset + m.end()
                return True, quote_around(body, start, end) or body[start:end]
    return False, ""
