"""The reader version: what a stored reading was made by (#32).

A reading is only as good as the prompt, the model and the field list behind it, so the version hashes all three.
Change any one and the version changes: the queue then reads every posting again under the new version, in shadow,
until the eval gate lets it be promoted. `body_sha256` is not here; every reader module takes it from
`outreach.store.writes`.
"""
from __future__ import annotations

import hashlib

from outreach.reader.config import ReaderConfig, load_reader_config
from outreach.reader.types import READING_SCHEMA_VERSION

__all__ = ["reader_version"]


def reader_version(cfg: ReaderConfig | None = None) -> str:
    """`rv-` plus 12 hex of sha256 over the prompt file bytes, the model name and the reading schema version."""
    cfg = cfg if cfg is not None else load_reader_config()
    digest = hashlib.sha256(
        cfg.prompt_path.read_bytes() + b"\0" + cfg.model.encode("utf-8") + b"\0"
        + str(READING_SCHEMA_VERSION).encode("ascii"))
    return "rv-" + digest.hexdigest()[:12]
