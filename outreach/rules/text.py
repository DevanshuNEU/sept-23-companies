"""Sentence splitting and quoting, shared by every rule that reads a posting body.

`text.py` is part of the ruleset hash: where a sentence ends decides which words a rule sees together.
"""
import functools
import re

# Characters that render as nothing (#112). One inside a word splits it past every word list: "do n‍ot sponsor"
# read yes and "spon­sor" read unclear. Zero-width and word joiners, bidi controls, the soft hyphen, the BOM, the
# Unicode tag characters (an invisible copy of ASCII a model reads as text) and variation selectors are dropped; the
# line and paragraph separators become line breaks, so they still end a sentence. The lists come from
# tools/posting_guard.py in ai-job-search-dev/ai-job-search (MIT, Mads Lorentzen).
_INVISIBLE = {ord(c): None for c in "​‌‍⁠⁡⁢⁣⁤"
                                    "‎‏‪‫‬‭‮⁦⁧⁨⁩­﻿"}
_INVISIBLE.update(dict.fromkeys(range(0xE0000, 0xE0080)))
_INVISIBLE.update(dict.fromkeys(range(0xFE00, 0xFE10)))
_INVISIBLE.update(dict.fromkeys(range(0xE0100, 0xE01F0)))
_INVISIBLE.update({0x2028: "\n", 0x2029: "\n"})


def strip_invisible(text: str) -> str:
    """The text without characters that render as nothing; U+2028 and U+2029 become line breaks."""
    return text.translate(_INVISIBLE)


# A stop is a . ! or ? run (with any closing quote or bracket) that is followed by a space or the end, or a
# semicolon, a line break or a bullet character. Dots inside words and numbers ("3.5", "node.js", "$120.5k") are
# never followed by a space, so they are never stops. Possessive quantifiers and the lookbehind keep it linear.
_BULLETS = "•·▪◦●‣⁃"
_CLOSERS = "\"')\\]”’"
_STOP = re.compile(r"(?<![.!?])[.!?]++[" + _CLOSERS + r"]*+(?=\s|$)|[;\n\r" + _BULLETS + "]")
_WORD_BEFORE = re.compile(r"[A-Za-z][A-Za-z.]*$")
_LEADING_MARKER = re.compile(r"[-*" + _BULLETS + r"]+\s+")
_NEXT_IS_CAPITAL = re.compile(r"\s*[A-Z]")

# A dot after one of these never ends a sentence: initialisms, and titles that always precede a name.
_NEVER_ENDS = frozenset({
    "u.s.", "u.s.a.", "b.s.", "m.s.", "b.a.", "m.a.", "b.sc.", "m.sc.", "ph.d.", "i.e.", "e.g.", "a.m.", "p.m.",
    "vs.", "mr.", "mrs.", "ms.", "dr.", "st.", "sr.", "jr.",
})
# A dot after one of these ends a sentence only when a capital letter follows ("Acme Inc. is hiring" stays whole).
_ENDS_BEFORE_CAPITAL = frozenset({"etc.", "inc.", "ltd.", "llc.", "corp.", "co."})


def _ends_sentence(text: str, stop: re.Match) -> bool:
    found = stop.group(0)
    if found[0] != "." or found.rstrip(_CLOSERS) != ".":
        return True  # ! ? ; line breaks, bullets, "..." runs
    word = _WORD_BEFORE.search(text[max(0, stop.start() - 12):stop.start()])
    if word is None:
        return True
    abbreviation = word.group(0).lower() + "."
    if abbreviation in _NEVER_ENDS:
        return False
    if abbreviation in _ENDS_BEFORE_CAPITAL:
        return _NEXT_IS_CAPITAL.match(text, stop.end()) is not None or text[stop.end():].strip() == ""
    return True


def _trim(text: str, a: int, b: int) -> tuple[int, int]:
    """Shrink [a, b) past whitespace and a leading bullet or dash marker."""
    while a < b and text[a].isspace():
        a += 1
    marker = _LEADING_MARKER.match(text, a, b)
    if marker:
        a = marker.end()
    while a < b and text[a].isspace():
        a += 1
    while b > a and text[b - 1].isspace():
        b -= 1
    return a, b


@functools.lru_cache(maxsize=16)
def _spans(text: str) -> tuple[tuple[int, int], ...]:
    spans = []
    pos = 0

    def add(a: int, b: int) -> None:
        a, b = _trim(text, a, b)
        if a < b:
            spans.append((a, b))

    for stop in _STOP.finditer(text):
        if not _ends_sentence(text, stop):
            continue
        if stop.group(0)[0] in ";.!?":
            add(pos, stop.end())  # the stop stays with its sentence
        else:
            add(pos, stop.start())  # a line break or bullet is dropped
        pos = stop.end()
    add(pos, len(text))
    return tuple(spans)


def sentences(text: str) -> list[str]:
    """Split on . ! ? ; line breaks and bullets, keeping abbreviations (U.S., B.S., i.e., Inc.) and decimals whole."""
    return [text[a:b] for a, b in _spans(text)]


_ELLIPSIS = "..."
_WHITESPACE = re.compile(r"\s")


def quote_around(text: str, start: int, end: int, limit: int = 260) -> str:
    """The sentence holding the match text[start:end], cut to whole words if it is longer than `limit`.

    A match that crosses a sentence boundary takes every sentence it touches. A sentence longer than `limit`
    is cut to a window around the match, at word boundaries, with "..." where text was dropped.
    """
    spans = _spans(text)
    if not spans:
        return ""
    first = next((i for i, (_, b) in enumerate(spans) if b > start), len(spans) - 1)
    last = first
    while last + 1 < len(spans) and spans[last + 1][0] < end:
        last += 1
    a0, b0 = spans[first][0], spans[last][1]
    snippet = text[a0:b0]
    if len(snippet) <= limit:
        return snippet
    ms = min(max(start - a0, 0), len(snippet))
    me = min(max(end - a0, ms), len(snippet))
    budget = max(limit - 2 * len(_ELLIPSIS), 1)
    if me - ms >= budget:
        a, b = ms, ms + budget
    else:
        a = max(0, min(ms - (budget - (me - ms)) // 2, len(snippet) - budget))
        b = a + budget
    if a > 0 and not snippet[a - 1].isspace() and not snippet[a].isspace():  # do not start mid-word
        gap = _WHITESPACE.search(snippet, a)
        a = min(gap.end() if gap else len(snippet), ms)
    if b < len(snippet) and not snippet[b - 1].isspace() and not snippet[b].isspace():  # do not end mid-word
        gaps = [g.start() for g in _WHITESPACE.finditer(snippet, a, b)]
        b = max(gaps[-1] if gaps else b, me)
    out = (_ELLIPSIS if a > 0 else "") + snippet[a:b].strip() + (_ELLIPSIS if b < len(snippet) else "")
    return out[:limit]
