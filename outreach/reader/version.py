"""The reader version: what a stored reading was made by (#32).

A reading is only as good as the prompt, the model and the field list behind it, so the version hashes all three.
Change any one and the version changes: the queue then reads every posting again under the new version, in shadow,
until the eval gate lets it be promoted. `body_sha256` is not here; every reader module takes it from
`outreach.store.writes`.
"""
from __future__ import annotations

import hashlib
import re

from outreach.reader.config import ReaderConfig, load_reader_config
from outreach.reader.types import READING_SCHEMA_VERSION

__all__ = ["SIDE_PREFIX", "VERSION_PATTERN", "reader_version", "side_version", "promoted_version"]

# What a reader version looks like: `rv-` and 12 lower-case hex. Only such a version can be promoted.
VERSION_PATTERN = re.compile(r"rv-[0-9a-f]{12}\Z")

# What a side session's readings are filed under, before the files' version (see `side_version`).
SIDE_PREFIX = "side-"


def reader_version(cfg: ReaderConfig | None = None) -> str:
    """`rv-` plus 12 hex of sha256 over the prompt file bytes, the model name and the reading schema version."""
    cfg = cfg if cfg is not None else load_reader_config()
    digest = hashlib.sha256(
        cfg.prompt_path.read_bytes() + b"\0" + cfg.model.encode("utf-8") + b"\0"
        + str(READING_SCHEMA_VERSION).encode("ascii"))
    return "rv-" + digest.hexdigest()[:12]


def side_version(cfg: ReaderConfig | None = None) -> str:
    """The version a side session's readings (`next` and `submit` on the command line, #13's reader tools) are
    filed under: `side-` plus `reader_version`. A side session reads with the pinned prompt but not through the
    night job's `claude -p` call (its own model, context and tools), so its readings are kept apart: they never sit
    under the night's version, and `promote` takes only an `rv-` version, so they can never make a hide final."""
    return SIDE_PREFIX + reader_version(cfg)


def promoted_version(cfg: ReaderConfig | None = None) -> str:
    """The promoted version that may decide postings: `cfg.promoted_version` when it is a reader version (`rv-` and
    12 hex), else "" (none). A side version, or anything else set by hand, is treated as no promoted version, so it
    can never make a row final."""
    cfg = cfg if cfg is not None else load_reader_config()
    value = cfg.promoted_version
    return value if isinstance(value, str) and VERSION_PATTERN.match(value) else ""
