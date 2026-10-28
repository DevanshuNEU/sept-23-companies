"""The brief: what the drafter is given, and the id that proves it was given (#135).

The evidence is me.md above its "Needs Devanshu" section, so an open question is never handed over as a fact. The
brief id is a short hash of the voice pack, the evidence and the person. It is stateless: `save_draft` recomputes it
from the files as they are now, so a voice file or me.md edited after the brief makes the id stale.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path

from outreach.drafting.voice import VoicePack, VoiceUnreadable

__all__ = ["me_path", "read_evidence", "brief_id", "check_brief"]

_REPO_MAIL = Path(__file__).resolve().parents[2] / "mail"
OPEN_QUESTIONS = "## Needs Devanshu"


def me_path() -> Path:
    """me.md in `OUTREACH_MAIL_HOME`, else the repo's mail folder (the same rule the note checks use)."""
    return Path(os.environ.get("OUTREACH_MAIL_HOME") or _REPO_MAIL) / "me.md"


def read_evidence(path: Path | str | None = None) -> str:
    """me.md from the top up to, not including, the Needs Devanshu section. Raises `VoiceUnreadable` when the file
    cannot be read or holds nothing above that section."""
    p = Path(path) if path is not None else me_path()
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise VoiceUnreadable(p) from None
    lines = []
    for line in text.splitlines():
        if line.startswith(OPEN_QUESTIONS):
            break
        lines.append(line)
    evidence = "\n".join(lines).strip()
    if not evidence:
        raise VoiceUnreadable(p, "has no evidence above its open questions")
    return evidence


def brief_id(pack: VoicePack, evidence: str, person_id: int) -> str:
    sha = hashlib.sha256()
    for part in (pack.digest, evidence, str(person_id)):
        sha.update(part.encode() + b"\0")
    return sha.hexdigest()[:16]


def check_brief(brief: str, pack: VoicePack, evidence: str, person_id: int) -> bool:
    return hmac.compare_digest(brief.encode(), brief_id(pack, evidence, person_id).encode())
