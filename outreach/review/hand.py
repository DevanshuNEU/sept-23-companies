"""Read and write the values Devanshu types to settle an item: a size, an email domain, a sponsorship answer (#16).

Every reader refuses bad input with `ReviewError('bad_request', ...)` and a plain reason ending "Nothing was
saved.", before anything is written. A value he sets is a locked hand fact: `company_fact` picks it over any
automated row, and automation never overwrites it.
"""
from __future__ import annotations

import re
import sqlite3

from outreach.review import words
from outreach.review.errors import ReviewError
from outreach.review.play import ny_date
from outreach.rules.facts import EvidenceDraft
from outreach.sources.keys import classify_domain, registrable_domain
from outreach.store import append_event, is_shared_key, utcnow
from outreach.store.timefmt import iso_utc

__all__ = ["NOT_SAVED", "parse_hand_size", "normalize_domain", "normalize_sponsorship", "write_hand_fact"]

NOT_SAVED = "Nothing was saved."

_APPROX = re.compile(r"(?:about|around|roughly|approximately|approx\.?|~)\s*")
_PEOPLE = r"(?:\s*(?:people|employees|staff))?"
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://")
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+\Z")
_SPONSORSHIP = {"yes": "yes", "opt only": "opt_only", "opt": "opt_only", "unclear": "unclear"}


def _bad(text: str) -> ReviewError:
    return ReviewError("bad_request", f"{text} {NOT_SAVED}")


def parse_hand_size(text) -> EvidenceDraft:
    """A size draft from what he typed, when the WHOLE text is one size (the grammar of #11 Task 20's `hand_size`):
    "45" or "45 people" is `confirmed`; "about 45" (or around, roughly, approximately, approx, ~), "11 to 50",
    "11-50" and "50+" are `estimate`; a "guess" anywhere makes it `guess`. Commas are dropped ("1,000"). `source_kind`
    is `hand` and `quote` his text.

    Nothing is searched for inside a longer text: "450 people today" is refused, never read as an estimate of 450,
    because a locked estimate over 300 hides a company. Zero, an empty text, words and a range that runs backwards
    are refused too."""
    raw = text.strip() if isinstance(text, str) else ""
    t = raw.lower().replace(",", "")
    confidence = "confirmed"
    if re.search(r"\bguess\b", t):
        confidence, t = "guess", re.sub(r"\bguess\b", "", t).strip()
    approx = _APPROX.match(t)
    if approx and approx.end():
        t = t[approx.end():].strip()
        if confidence != "guess":
            confidence = "estimate"
    rough = "guess" if confidence == "guess" else "estimate"
    low = high = None
    value = None
    if m := re.fullmatch(r"(\d+)\s*(?:to|-)\s*(\d+)" + _PEOPLE, t):
        low, high = int(m[1]), int(m[2])
        value, confidence = f"{low}-{high}", rough
        if not 1 <= low <= high:
            value = None
    elif m := re.fullmatch(r"(\d+)\s*\+" + _PEOPLE, t):
        low, value, confidence = int(m[1]), f"{int(m[1])}+", rough
    elif m := re.fullmatch(r"(\d+)" + _PEOPLE, t):
        low = high = int(m[1])
        value = str(low)
    if value is None or low < 1:
        raise _bad("A size is a number like 45, or a range like 11 to 50.")
    return EvidenceDraft("size", value, confidence, "hand", quote=raw, num_low=low, num_high=high)


def normalize_domain(conn: sqlite3.Connection, text) -> str:
    """The bare domain from a domain, a URL or an address ("https://www.Acme.io/jobs", "ana@acme.io"): lower case,
    no leading `www.`, no trailing dot. Refused when it is not a dotted host; when `classify_domain` says it is not a
    company's own (personal mail, a payroll or employer-of-record host, a platform: code hosts, job boards, ATS
    hosts, hosting suffixes); or when it (or its registrable root) is a shared key. A platform host as a company's
    domain would become an identity key and pull other companies' candidates onto this one."""
    t = _SCHEME.sub("", text.strip().lower()) if isinstance(text, str) else ""
    t = t.rpartition("@")[2]
    t = re.split(r"[/?#:]", t, maxsplit=1)[0].strip().rstrip(".")
    if t.startswith("www.") and "." in t[4:]:
        t = t[4:]
    if not t:
        raise _bad("Type the company's email domain, such as acme.com.")
    if not _HOST.match(t):
        raise _bad(f"{t} is not a domain like acme.com.")
    kind = classify_domain(t)
    root = registrable_domain(t)
    if kind == "free":
        raise _bad(f"{t} is a personal mail provider, not the company's own domain.")
    if kind == "peo":
        raise _bad(f"{t} is a payroll or employer-of-record provider, not the company's own domain.")
    if kind == "platform":
        if root is not None and root != t:
            raise _bad(f"{t} is an address on {root}, a site many companies share. Give the company's own domain.")
        raise _bad(f"{t} is a platform, job board or hosting site, not the company's own domain.")
    if kind != "company":
        raise _bad(f"{t} is not a company's own domain.")
    if is_shared_key(conn, "domain", t) or (root is not None and is_shared_key(conn, "domain", root)):
        raise _bad(f"{t} is shared by several employers, so it cannot name one company.")
    return t


def normalize_sponsorship(text) -> str:
    """`yes`, `opt_only` or `unclear` from what he picked. No is a no-go, not a value: `not_allowed`."""
    t = " ".join(text.strip().lower().replace("_", " ").split()) if isinstance(text, str) else ""
    if t == "no":
        raise ReviewError("not_allowed", "Sponsorship No is a no-go. Use Mark no-go.")
    if t not in _SPONSORSHIP:
        raise _bad("Sponsorship is Yes, OPT only or Unclear.")
    return _SPONSORSHIP[t]


def write_hand_fact(conn: sqlite3.Connection, company_id: int, draft: EvidenceDraft, *, posting_id: int | None = None,
                    now: str | None = None) -> int:
    """Insert one locked hand `evidence` row (`source_ref` the draft's source, `observed_on` the New York date) and
    its `hand_set` event by `devanshu`. Returns the evidence id. Two calls with one value make two rows: evidence is
    append-only and the newest hand row wins."""
    if draft.source_kind != "hand":
        raise ValueError(f"a hand fact has source_kind 'hand', not {draft.source_kind!r}")
    source = (draft.source_ref or "").strip()
    if not source:
        raise _bad("Say where you saw it: a fix needs a source.")
    at = iso_utc(now) if now else utcnow()
    evidence_id = conn.execute(
        "INSERT INTO evidence(company_id, fact, value, num_low, num_high, confidence, locked, source_kind, source_ref, "
        "quote, posting_id, observed_on, recorded_at) VALUES (?, ?, ?, ?, ?, ?, 1, 'hand', ?, ?, ?, ?, ?)",
        (company_id, draft.fact, draft.value, draft.num_low, draft.num_high, draft.confidence, source, draft.quote,
         posting_id, ny_date(at), at)).lastrowid
    append_event(conn, entity="evidence", kind="hand_set", actor="devanshu", entity_id=evidence_id,
                 company_id=company_id, at=at, idempotency_key=f"hand-set:{evidence_id}",
                 detail={"fact": draft.fact, "value": draft.value, "source": source, "posting_id": posting_id,
                         "text": words.hand_set_text(draft.fact, draft.value, source)})
    return evidence_id
