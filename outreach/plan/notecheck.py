"""The one LinkedIn note checker (#15, Task 7). The API and the MCP server import `check_note`; neither keeps a copy.

A note is plain text under `note.max_chars`. It is ready only with no block check and a reason line that Devanshu
has confirmed or written. The text rules (banned phrases, dashes, placeholders, link shorteners, unconfirmed me.md
facts) are the email draft rules of `outreach.drafts.checks`, read through its helpers; this module adds the note's
own: length, facts that need a safe link, and the reason line. Pure: no store, no clock, no network.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from outreach.drafts.checks import DASHES, PLACEHOLDER, SHORTENERS, find_links, link_host
from outreach.plan.config import PlanConfig

__all__ = ["NoteCheck", "Fact", "check_note", "is_ready", "load_unconfirmed", "READY_STATES", "BLOCK_CODES",
           "WARN_CODES"]

READY_STATES = ("confirmed", "written")
BLOCK_CODES = ("note_empty", "note_too_long", "banned_list_unreadable", "banned_phrase", "dash", "placeholder",
               "unconfirmed_list_unreadable", "unconfirmed_fact", "link_shortener", "fact_no_link", "reason_missing",
               "reason_not_in_note")
WARN_CODES = ("too_many_links", "fact_not_in_note", "number_not_linked", "reason_unconfirmed")
_ORDER = {code: i for i, code in enumerate(BLOCK_CODES + WARN_CODES)}
_DIGITS = re.compile(r"\d+")


@dataclass(frozen=True)
class NoteCheck:
    code: str
    severity: str  # 'block' or 'warn'
    title: str
    detail: str
    fix: dict | None = None
    spans: tuple[tuple[int, int], ...] = ()

    def to_json(self) -> dict:
        """The `Check` shape of the API: spans are in the note body."""
        return {"code": self.code, "severity": self.severity, "title": self.title, "detail": self.detail,
                "fix": self.fix, "spans": [{"field": "body", "start": a, "end": b} for a, b in self.spans]}

    @classmethod
    def from_json(cls, data: dict) -> "NoteCheck":
        return cls(code=data["code"], severity=data["severity"], title=data["title"], detail=data["detail"],
                   fix=data.get("fix"), spans=tuple((s["start"], s["end"]) for s in data.get("spans", ())))


@dataclass(frozen=True)
class Fact:
    text: str
    label: str
    url: str
    in_note: bool


def load_unconfirmed(me_path: Path | str) -> list[str] | None:
    """The values quoted in backticks on the ASK items of me.md (an item may wrap onto indented lines): things the
    note must not claim. None when the file cannot be read (a caller must not treat that as an empty list)."""
    try:
        text = Path(me_path).expanduser().read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    out, in_ask = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("- "):
            in_ask = line.lstrip().startswith("- ASK:")
        elif not line.startswith((" ", "\t")) or not line.strip():
            in_ask = False
        if in_ask:
            out += re.findall(r"`([^`]+)`", line)
    return out


def _safe_link(url: str) -> bool:
    """A fact's link is safe when it is a plain https page: no credentials, no whitespace, not LinkedIn, no
    shortener."""
    if not isinstance(url, str) or not url or url != url.strip() or re.search(r"\s", url):
        return False
    low = url.lower()
    if "linkedin.com" in low or "lnkd.in" in low:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() != "https" or not host or parts.username or parts.password:
        return False
    host = host[4:] if host.startswith("www.") else host
    return host not in SHORTENERS


def _check(code: str, severity: str, title: str, detail: str, spans: Sequence[tuple[int, int]] = ()) -> NoteCheck:
    return NoteCheck(code, severity, title, detail, None, tuple(spans))


def _spans(rx: re.Pattern, text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in rx.finditer(text) if m.end() > m.start()]


def check_note(body: str, reason_line: str, reason_state: str, facts: Sequence[Fact], *, cfg: PlanConfig,
               banned: Sequence, unconfirmed: Sequence[str] | None) -> list[NoteCheck]:
    """Every problem with one LinkedIn note, blocks and warns, in a fixed order.

    `banned` is what `outreach.drafts.checks.load_banned` returned for the note scopes; an empty list means the file
    could not be used, and the note does not pass on that (`banned_list_unreadable`). `unconfirmed` is
    `load_unconfirmed`'s answer; None blocks the same way. The banned list is extended by `note.extra_banned`.
    """
    out: list[NoteCheck] = []
    body = body or ""
    reason = (reason_line or "").strip()
    max_chars = cfg.get("note", "max_chars")
    max_links = cfg.get("note", "max_links")

    if not body.strip():
        out.append(_check("note_empty", "block", "The note is empty", "Write the note before it can go out."))
    if len(body) > max_chars:
        out.append(_check("note_too_long", "block", "Over the length limit",
                          f"{len(body)} characters; the limit is {max_chars}. Cut {len(body) - max_chars}.",
                          [(max_chars, len(body))]))

    if not banned:
        out.append(_check("banned_list_unreadable", "block", "Banned phrases list missing",
                          "The banned phrases list could not be read, so this note was not checked for banned "
                          "phrases. Fix the list, then check again."))
    rules = [(src, rx) for src, rx in banned]
    rules += [(p, re.compile(p, re.I)) for p in cfg.get("note", "extra_banned")]
    for src, rx in rules:
        spans = _spans(rx, body)
        if spans:
            out.append(_check("banned_phrase", "block", "Banned phrase",
                              f"On the banned list: {src!r}. Say it plainly.", spans))

    spans = _spans(DASHES, body)
    if spans:
        out.append(_check("dash", "block", "Em or en dash",
                          "Use a comma, a colon, a full stop or a plain hyphen instead.", spans))
    spans = _spans(PLACEHOLDER, body)
    if spans:
        out.append(_check("placeholder", "block", "Placeholder text left in",
                          "TODO, ASK: or {{ }} is still in the note. Replace it with the real words.", spans))

    if unconfirmed is None:
        out.append(_check("unconfirmed_list_unreadable", "block", "Facts list missing",
                          "me.md could not be read, so this note was not checked for facts Devanshu has not "
                          "confirmed. Fix the file, then check again."))
    else:
        used, spans = [], []
        for value in unconfirmed:
            hits = [(m.start(), m.end()) for m in re.finditer(r"(?<!\w)" + re.escape(value) + r"(?!\w)", body, re.I)]
            if hits:
                used.append(value)
                spans += hits
        if used:
            out.append(_check("unconfirmed_fact", "block", "Uses a fact that is not confirmed",
                              "me.md marks these as open questions: " + ", ".join(repr(v) for v in used) +
                              ". Leave them out.", sorted(spans)))

    links = find_links(body)
    short = [(s, e) for s, e, u in links if link_host(u) in SHORTENERS]
    if short:
        out.append(_check("link_shortener", "block", "Link shortener", "Use the full link, not a shortener.", short))
    if len(links) > max_links:
        out.append(_check("too_many_links", "warn", f"More than {max_links} links",
                          f"{len(links)} links in a short note.", [(s, e) for s, e, _ in links]))

    facts = tuple(facts)
    vouching = []
    for fact in facts:
        if _safe_link(fact.url):
            vouching.append(fact)
        else:
            out.append(_check("fact_no_link", "block", "A fact has no safe link",
                              f"{fact.text!r} needs a public https link that is not LinkedIn and not a shortener. "
                              "Add the page it came from or leave the fact out."))
    low = body.lower()
    for fact in facts:
        if fact.in_note and (not fact.text.strip() or fact.text.lower() not in low):
            out.append(_check("fact_not_in_note", "warn", "A fact is not in the note",
                              f"{fact.text!r} is marked as used in the note but its words are not there."))

    masked = body
    for _, _, url in links:  # a number inside a link belongs to the link
        masked = masked.replace(url, " " * len(url))
    for fact in vouching:
        if fact.text.strip():
            masked = re.sub(re.escape(fact.text), lambda m: " " * len(m.group()), masked, flags=re.I)
    spans = [(m.start(), m.end()) for m in _DIGITS.finditer(masked)]
    if spans:
        out.append(_check("number_not_linked", "warn", "A number with no link behind it",
                          "A figure in the note has no linked fact. Add the source or take the number out.", spans))

    if not reason:
        out.append(_check("reason_missing", "block", "No reason line", "Say in one line why this person."))
    elif reason not in body:
        out.append(_check("reason_not_in_note", "block", "The reason line is not in the note",
                          "The reason line must appear in the note text word for word."))
    if reason_state not in READY_STATES:
        out.append(_check("reason_unconfirmed", "warn", "The reason line is not yet yours",
                          "Claude proposed this line. Confirm it or write your own before the note goes out."))
    return sorted(out, key=lambda c: _ORDER[c.code])


def is_ready(checks: Sequence[NoteCheck], reason_state: str) -> bool:
    """No block check, and a reason line that Devanshu confirmed or wrote."""
    return reason_state in READY_STATES and not any(c.severity == "block" for c in checks)
