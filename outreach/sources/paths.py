"""Where the sources keep their downloads (LCA files, the stapply dump, Form D indexes)."""
import os
from pathlib import Path


def sources_dir() -> Path:
    """`OUTREACH_SOURCES_DIR`, or `~/Library/Application Support/outreach/sources`. Not created here."""
    override = os.environ.get("OUTREACH_SOURCES_DIR")
    if override:
        return Path(override)
    return Path.home() / "Library" / "Application Support" / "outreach" / "sources"
