"""Sameness across his first emails (#203): what he has used lately, and what a draft repeats.

The tell people actually catch is the same lines across many emails, not any one email (lab, 2026-11-09). This module
reads his first emails as one corpus and answers the questions the checks and the brief ask. It only reads; it makes
no model calls.

A sentence counts when it has at least `MIN_WORDS` words, after the greeting line and above the sign-off and the
signature. Text is compared lowercased, with punctuation dropped and spaces collapsed.
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter
from dataclasses import dataclass

from outreach.drafting.signature import signature_lines

__all__ = ["FIRST_EMAIL_STATES", "Corpus", "core", "load_corpus", "sentences", "first_sentence", "reused",
           "opening_count", "overused", "recent_openings", "recent_subjects", "evidence_used"]

# A first email that is a real draft or a real email (the checks' OPEN_STATES): planned rows are empty, skipped ones
# never go out.
FIRST_EMAIL_STATES = ("drafted", "approved", "sending", "send_unknown", "sent", "failed")
MIN_WORDS = 6
RUN = 6  # the length of a run of words the brief reports as overused
GREETING = re.compile(r"^\s*(hi|hello|hey|dear)\b[^\n]{0,40},\s*$", re.I)
URL = re.compile(r"https?://\S+?(?=[.,;:!?)\]]*(?:\s|$))", re.I)  # a full stop after a link still ends the sentence
ENDS = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True)
class Corpus:
    bodies: tuple[str, ...]  # newest first
    subjects: tuple[str, ...]


def core(body: str) -> str:
    """The body without its greeting line, and without the signature and the sign-off word line before it."""
    text = body.replace("’", "'").rstrip()
    want = signature_lines()
    lines = [(m.start(), m.group().strip()) for m in re.finditer(r"[^\n]+", text) if m.group().strip()]
    if len(lines) >= len(want) and [t for _, t in lines[-len(want):]] == want:
        text = text[:lines[-len(want)][0]].rstrip()
        head, _, last = text.rpartition("\n")
        if head and len(last.split()) <= 3 and last.strip().endswith(","):
            text = head.rstrip()
    first, _, rest = text.partition("\n")
    if GREETING.match(first):
        text = rest
    return text.strip()


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s']", " ", text.lower()).replace(" '", " ").replace("' ", " ").split()).strip("'")


def _all_sentences(body: str) -> list[str]:
    parts = (_norm(p) for p in ENDS.split(URL.sub(" ", core(body))))
    return [p for p in parts if p]


def sentences(body: str) -> list[str]:
    return [s for s in _all_sentences(body) if len(s.split()) >= MIN_WORDS]


def first_sentence(body: str) -> str:
    found = _all_sentences(body)
    return found[0] if found else ""


def load_corpus(conn: sqlite3.Connection, *, exclude_message_id: int | None) -> Corpus:
    rows = conn.execute(
        f"SELECT body, subject FROM message WHERE seq = 0 AND state IN ({','.join('?' * len(FIRST_EMAIL_STATES))}) "
        "AND message_id IS NOT ? ORDER BY created_at DESC, message_id DESC",
        (*FIRST_EMAIL_STATES, exclude_message_id)).fetchall()
    return Corpus(bodies=tuple(r[0] for r in rows), subjects=tuple(r[1] for r in rows))


def reused(body: str, corpus: Corpus, min_count: int = 3) -> list[tuple[str, int]]:
    """Each sentence of `body` found in at least `min_count` other emails of the corpus, with its count, most first."""
    mine = list(dict.fromkeys(sentences(body)))
    if not mine:
        return []
    counts = Counter()
    for other in corpus.bodies:
        theirs = set(sentences(other))
        counts.update(s for s in mine if s in theirs)
    return sorted(((s, n) for s, n in counts.items() if n >= min_count), key=lambda x: (-x[1], mine.index(x[0])))


def _opening(body: str, words: int) -> str:
    return " ".join(first_sentence(body).split()[:words])


def opening_count(body: str, corpus: Corpus, last: int = 100, words: int = 4) -> int:
    """How many of the newest `last` emails open with the same first `words` words as `body`."""
    key = _opening(body, words)
    if not key:
        return 0
    return sum(1 for other in corpus.bodies[:last] if _opening(other, words) == key)


def overused(corpus: Corpus, min_count: int = 10, limit: int = 30) -> list[dict]:
    """Sentences and runs of `RUN` words found in at least `min_count` emails, most first."""
    counts = Counter()
    for body in corpus.bodies:
        found = set(sentences(body))
        for s in _all_sentences(body):
            w = s.split()
            found.update(" ".join(w[i:i + RUN]) for i in range(len(w) - RUN + 1))
        counts.update(found)
    hits = [(t, n) for t, n in counts.items() if n >= min_count]
    hits.sort(key=lambda x: (-x[1], x[0]))
    return [{"text": t, "count": n} for t, n in hits[:limit]]


def recent_openings(corpus: Corpus, n: int = 30) -> list[str]:
    return [s for s in (first_sentence(b) for b in corpus.bodies[:n]) if s]


def recent_subjects(corpus: Corpus, n: int = 30) -> list[str]:
    return list(corpus.subjects[:n])


def evidence_used(conn: sqlite3.Connection, *, since: str) -> dict[str, int]:
    """How many first emails created at or after `since` leaned on each project (the `project` field)."""
    rows = conn.execute(
        f"SELECT project, COUNT(*) FROM message WHERE seq = 0 AND state IN ({','.join('?' * len(FIRST_EMAIL_STATES))}) "
        "AND project <> '' AND created_at >= ? GROUP BY project ORDER BY COUNT(*) DESC, project",
        (*FIRST_EMAIL_STATES, since)).fetchall()
    return {r[0]: r[1] for r in rows}
