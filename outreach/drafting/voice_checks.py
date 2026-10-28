"""The voice checks: pure checks on a finished draft body, after the model and before it is stored (#135).

They read the rules of his DevVault voice doctrine that a program can see: eager phrases, house words, system words,
a reader's own profile recited back, the rhythm tells, an OPT line for anyone but a recruiter, and numbers that no
source backs. Every check is a warn or a pass, never a block: a voice problem is for him to see, not a reason to
refuse the save. Each returns one `Check` dict of the same shape as `outreach.drafts.checks`.
"""
from __future__ import annotations

import re
import statistics
from typing import Sequence

__all__ = ["voice_checks"]

URL = re.compile(r"https?://\S+|\b(?:[a-z0-9-]+\.)+(?:com|ai|io|dev|app|co|org|net)\b(?:/\S*)?", re.I)
EAGER = ("glad to", "happy to", "i'd love", "i would love", "looking forward", "excited", "thrilled",
         "confident i can", "passionate", "thank you for being open")
HOUSE = ("genuinely", "curious", "no agenda", "either way", "leverage")
SYSTEM = ("lane", "wave")  # his system's words; a reader never says them (req ID stays: recruiters get it by #9)
RECITAL = re.compile(r"\byour (?:resume|profile|linkedin|bio) (?:lists|says|shows|mentions)\b|"
                     r"\bi (?:saw|noticed|read) that you\b", re.I)
NOT_BUT = re.compile(r"\bnot (?:just |only )?[^.,]{1,50}, (?:it's|its|but)\b", re.I)
OPT = re.compile(r"F-1|\bOPT\b", re.I)  # sponsorship wording belongs to outreach/rules, so only the OPT line here
NUMBER = re.compile(r"\d[\d,.]*%?")
YEAR = re.compile(r"^(?:19|20)\d\d$")
SENTENCE_ENDS = re.compile(r"(?<=[.!?])\s+")
MIN_SENTENCES, EVEN_BAND = 4, 2


def _check(code: str, severity: str, title: str, detail: str) -> dict:
    return {"code": code, "severity": severity, "title": title, "detail": detail, "fix": None, "spans": []}


def _hits(text: str, words: Sequence[str]) -> list[str]:
    low = text.lower()
    return [w for w in words if re.search(rf"(?<![\w']){re.escape(w)}(?![\w])", low)]


def _words_check(code: str, title: str, text: str, words: Sequence[str], why: str) -> dict:
    found = _hits(text, words)
    if found:
        return _check(code, "warn", title, f"{why}: {', '.join(repr(w) for w in found)}.")
    return _check(code, "pass", title, "None found.")


def _sentences(text: str) -> list[str]:
    body = "\n".join(line for line in text.splitlines()
                     if line.strip() and not re.match(r"^\s*(hi|hello|hey|thanks|thank you)\b.{0,40}[,.]?\s*$|"
                                                      r"^\s*devanshu chicholikar\s*$", line, re.I))
    return [s.strip() for s in SENTENCE_ENDS.split(body.replace("\n", " ")) if s.strip()]


def _rhythm(text: str) -> dict:
    if NOT_BUT.search(text):
        return _check("voice_rhythm", "warn", "Model rhythm", "A \"not X, it's Y\" turn reads as generated.")
    counts = [len(s.split()) for s in _sentences(text)]
    if len(counts) >= MIN_SENTENCES:
        mean = statistics.fmean(counts)
        if all(abs(n - mean) <= EVEN_BAND for n in counts):
            return _check("voice_rhythm", "warn", "Model rhythm",
                          "Every sentence is about the same length. Mix a short one with a longer one.")
    return _check("voice_rhythm", "pass", "Rhythm varies", "No model rhythm found.")


def _plain(number: str) -> str:
    """A number without its thousands commas or its percent sign: '1,808' is '1808', '94%' is '94'."""
    return re.sub(r"(?<=\d),(?=\d{3}\b)", "", number).rstrip("%")


def _ungrounded(text: str, ground: str) -> dict:
    plain_ground, missing = _plain(ground), []
    for raw in NUMBER.findall(text):
        n = raw.rstrip(".,")
        if not n or YEAR.match(n) or n in missing:
            continue
        # A whole number only: '66' is not found inside '665' or '2012', and '94' is not found inside '94.5'.
        if not re.search(rf"(?<![\d.,]){re.escape(_plain(n))}(?!\d|\.\d)", plain_ground):
            missing.append(n)
    if missing:
        return _check("voice_ungrounded", "warn", "Number without a source",
                      "Not found in me.md or the person's text: " + ", ".join(missing) + ".")
    return _check("voice_ungrounded", "pass", "Numbers have a source", "Every number is in me.md or his paste.")


def voice_checks(body: str, role_kind: str, *, ground: str | None = None) -> list[dict]:
    """The voice checks of one body. `ground` is the text the numbers must come from (me.md, the person's profile and
    notes, the hook); None skips the number check, as the Outbox editor does."""
    text = URL.sub(" ", body)
    out = [
        _words_check("voice_eager", "Eager phrase", text, EAGER, "He is the one being sought, so none of these"),
        _words_check("voice_house_word", "House word", text, HOUSE, "These read as generated"),
        _words_check("voice_system_word", "System word", text, SYSTEM, "These are his system's words, not a reader's"),
    ]
    if RECITAL.search(text):
        out.append(_check("voice_recital", "warn", "Profile read back",
                          "Do not recite their profile or resume back. Name the thing and react to it."))
    else:
        out.append(_check("voice_recital", "pass", "No profile recital", "Nothing recites their profile back."))
    out.append(_rhythm(text))
    if role_kind != "recruiter" and OPT.search(text):
        out.append(_check("voice_opt_line", "warn", "Work-authorization line",
                          "The OPT line goes to recruiters only. Engineers, managers and founders do not get it."))
    else:
        out.append(_check("voice_opt_line", "pass", "Work-authorization line fits", "No stray OPT line."))
    if ground is not None:
        out.append(_ungrounded(text, ground))
    return out
