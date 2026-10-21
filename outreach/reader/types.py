"""The posting reader's contract: the fields a reading holds, what each may be, and the quote guard (#30).

A reading is a model's answer about one posting. A hide rests on it, so every known value carries a quote and the
quote must appear in the text the model was given (`quote_in_body`). A wrong hide is the worst failure; a quote
that does not check out means the field is refused, never trusted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = ["READING_SCHEMA_VERSION", "FIELDS", "ALLOWED", "INT_FIELDS", "TEXT_FIELDS", "check_value", "FieldReading", "PostingReading", "quote_in_body",
           "ReadRequest"]

READING_SCHEMA_VERSION = 1

FIELDS = ("engineering", "level", "years_low", "years_high", "sponsorship", "citizenship_or_clearance",
          "student_or_grad_window", "start_date", "location", "remote", "pay")

_YES_NO = ("yes", "no", "unknown")

# The closed lists. Every value here is a string a reading may hold for that field.
ALLOWED: dict[str, tuple[str, ...]] = {
    "engineering": _YES_NO,
    "level": ("intern", "new_grad", "early_career", "mid", "senior_plus", "unknown"),
    "sponsorship": ("wont_sponsor", "will_sponsor", "opt_only", "silent", "unknown"),
    "citizenship_or_clearance": _YES_NO,
    "student_or_grad_window": _YES_NO,
    "remote": _YES_NO,
}
# Not closed lists: a whole number 0 to YEARS_MAX or None, and free text or ''.
INT_FIELDS = ("years_low", "years_high")
TEXT_FIELDS = ("start_date", "location", "pay")

_YEARS_MAX = 30
# A quote may be '' only when the value says nothing was found.
_NO_QUOTE_VALUES = ("unknown", "silent", None, "")
_MIN_QUOTE_WORDS = 3
_WHITESPACE = re.compile(r"\s+")
_HAS_WORD_CHAR = re.compile(r"\w")
# Characters that join two letters into one word: hyphen, apostrophe, right quote, the Unicode hyphens U+2010 to
# U+2013 (hyphen, non-breaking hyphen, figure dash, en dash) and the soft hyphen U+00AD.
_JOINERS = r"-'\u2019\u2010\u2011\u2012\u2013\u00ad"
# A word edge: not next to a word character, and not next to one across a joining character.
_EDGE_BEFORE = r"(?<!\w)(?<!\w[" + _JOINERS + r"])"
_EDGE_AFTER = r"(?!\w)(?![" + _JOINERS + r"]\w)"


@dataclass(frozen=True)
class FieldReading:
    """One field: its value and the words from the posting that show it. `quote` is '' only when `value` is
    `unknown`, `silent`, `None` or `''`."""
    value: str | int | None
    quote: str


@dataclass(frozen=True)
class PostingReading:
    posting_id: int
    body_sha256: str
    reader_version: str
    fields: dict[str, FieldReading]

    @classmethod
    def from_json(cls, obj: Any) -> PostingReading:
        """Build a reading from a model's answer. Raises `ValueError` on anything off: a missing or extra key or
        field, a value outside `ALLOWED`, or a quote missing where the value needs one."""
        if not isinstance(obj, dict):
            raise ValueError("a reading must be an object")
        _exact_keys(obj, ("posting_id", "body_sha256", "reader_version", "fields"), "reading")
        posting_id = obj["posting_id"]
        if not _is_int(posting_id):
            raise ValueError("posting_id must be an integer")
        for key in ("body_sha256", "reader_version"):
            if not isinstance(obj[key], str) or not obj[key]:
                raise ValueError(f"{key} must be a non-empty string")
        raw_fields = obj["fields"]
        if not isinstance(raw_fields, dict):
            raise ValueError("fields must be an object")
        _exact_keys(raw_fields, FIELDS, "fields")
        fields: dict[str, FieldReading] = {}
        for name in FIELDS:
            raw = raw_fields[name]
            if not isinstance(raw, dict):
                raise ValueError(f"{name} must be an object with value and quote")
            _exact_keys(raw, ("value", "quote"), name)
            value, quote = raw["value"], raw["quote"]
            check_value(name, value)
            if not isinstance(quote, str):
                raise ValueError(f"{name}: quote must be a string")
            if value not in _NO_QUOTE_VALUES and not quote.strip():
                raise ValueError(f"{name}: a known value needs a quote")
            fields[name] = FieldReading(value, quote)
        return cls(posting_id, obj["body_sha256"], obj["reader_version"], fields)

    def to_json(self) -> dict:
        return {
            "posting_id": self.posting_id,
            "body_sha256": self.body_sha256,
            "reader_version": self.reader_version,
            "fields": {name: {"value": f.value, "quote": f.quote} for name, f in self.fields.items()},
        }


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _exact_keys(obj: dict, keys: tuple[str, ...], where: str) -> None:
    missing = [k for k in keys if k not in obj]
    extra = [k for k in obj if k not in keys]
    if missing:
        raise ValueError(f"{where}: missing {', '.join(missing)}")
    if extra:
        raise ValueError(f"{where}: unexpected {', '.join(map(str, extra))}")


def check_value(name: str, value: Any) -> None:
    """Raise `ValueError` unless `value` is allowed for field `name`. Covers all 11 fields; the one check
    `PostingReading.from_json` and the gold loader share."""
    if name in INT_FIELDS:
        if value is not None and (not _is_int(value) or not 0 <= value <= _YEARS_MAX):
            raise ValueError(f"{name}: must be a whole number from 0 to {_YEARS_MAX} or null")
    elif name in TEXT_FIELDS:
        if not isinstance(value, str):
            raise ValueError(f"{name}: must be text")
    elif name not in ALLOWED:
        raise ValueError(f"unknown field {name!r}")
    elif not isinstance(value, str) or value not in ALLOWED[name]:
        raise ValueError(f"{name}: {value!r} is not one of {', '.join(ALLOWED[name])}")


_check_value = check_value


def _collapse(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().lower()


def quote_in_body(quote: str, body: str) -> bool:
    """True when `quote` is a word-for-word piece of `body`, starting and ending on a word edge.

    This is a presence check and nothing more. It cannot see a negation outside the quote: "security clearance
    is required" is present in "No security clearance is required.". The rules' agreement (a hide needs the rules
    and the reading to say the same thing) is the real defense against a wrong hide; this only stops a model from
    quoting words the posting never had.

    Both sides get every run of whitespace turned into one space and are lower-cased; nothing else is folded, so
    punctuation, smart quotes and dashes must match. The quote needs at least 3 words (tokens with a letter or
    digit in them, so ". . ." is no words). It must not start or end inside a word: a hyphen, dash or apostrophe
    between letters joins them, so "us citizens" is not found in "non-us citizens" nor "able to" in "unable to".
    A paraphrase never passes. Anything that is not text never passes.
    """
    if not isinstance(quote, str) or not isinstance(body, str):
        return False
    q = _collapse(quote)
    if sum(1 for token in q.split(" ") if _HAS_WORD_CHAR.search(token)) < _MIN_QUOTE_WORDS:
        return False
    return re.search(_EDGE_BEFORE + re.escape(q) + _EDGE_AFTER, _collapse(body)) is not None


@dataclass(frozen=True)
class ReadRequest:
    """What the reader is given for one posting."""
    posting_id: int
    body_sha256: str
    reader_version: str
    title: str
    body: str
