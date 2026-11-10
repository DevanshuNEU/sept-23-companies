"""His signature (#191): the lines every email and follow-up ends with, after an optional sign-off word.

It holds his phone number, so it lives in a local file outside this public repo: `OUTREACH_SIGNATURE`, else
`~/Library/Application Support/outreach/signature.txt`. A missing, unreadable or empty file gives his name and the
portfolio, so a draft is never left without one.
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = ["FALLBACK", "signature_path", "read_signature", "signature_lines"]

FALLBACK = "Devanshu Chicholikar\nhttps://www.devanshuchicholikar.com"
DEFAULT_PATH = Path.home() / "Library" / "Application Support" / "outreach" / "signature.txt"


def signature_path() -> Path:
    return Path(os.environ.get("OUTREACH_SIGNATURE") or DEFAULT_PATH)


def read_signature(path: Path | str | None = None) -> str:
    """The signature's non-empty lines, each stripped, joined by newlines; FALLBACK when there are none."""
    p = Path(path) if path is not None else signature_path()
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return FALLBACK
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines) if lines else FALLBACK


def signature_lines() -> list[str]:
    return read_signature().split("\n")
