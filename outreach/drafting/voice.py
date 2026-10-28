"""The voice pack: his DevVault voice files, read by code so the model never decides whether to read them (#135).

`OUTREACH_VOICE_DIR` overrides the folder (tests point it at `tests/fixtures/devvault`); the default is
`~/Desktop/DevVault`. A missing, unreadable or empty file raises `VoiceUnreadable`: nothing is drafted from a pack
that is not whole.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

__all__ = ["VOICE_FILES", "VoicePack", "VoiceUnreadable", "load_voice_pack"]

DEFAULT_ROOT = "~/Desktop/DevVault"
VOICE_FILES = ("skills/human-voice.md", "lens/voice.md", "lens/contexts/context_outreach.md")


class VoiceUnreadable(Exception):
    """A voice file could not be read, or is empty. `path` names it."""

    def __init__(self, path: Path | str, why: str = "cannot be read"):
        super().__init__(f"{path} {why}")
        self.path = str(path)


@dataclass(frozen=True)
class VoicePack:
    files: tuple[tuple[str, str], ...]  # (path relative to the DevVault folder, text), in VOICE_FILES order
    digest: str                         # sha256 hex over every path and text, so any edit changes it


def load_voice_pack(root: Path | str | None = None) -> VoicePack:
    base = Path(root or os.environ.get("OUTREACH_VOICE_DIR") or DEFAULT_ROOT).expanduser()
    files, sha = [], hashlib.sha256()
    for rel in VOICE_FILES:
        try:
            text = (base / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise VoiceUnreadable(base / rel) from None
        if not text.strip():
            raise VoiceUnreadable(base / rel, "is empty")
        files.append((rel, text))
        sha.update(rel.encode() + b"\0" + text.encode() + b"\0")
    return VoicePack(tuple(files), sha.hexdigest())
