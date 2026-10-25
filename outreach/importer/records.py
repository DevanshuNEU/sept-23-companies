"""One record read from one source, the error a source can raise, and the read-only opens.

A reader yields `SourceRecord`s. The record's JSON and hash are what `import_record` stores, so the same record
always hashes the same, whatever order its keys were read in.
"""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

SOURCES = (
    "data_js", "local_storage", "contacts_json", "send_log", "radar_db", "devboard", "contacted_txt",
    "devvault_notes", "interview_prep", "curated_touches",
)


class SourceError(Exception):
    """A required source is missing or unreadable. The message names the source and the path."""


@dataclass(frozen=True)
class SourceRecord:
    source: str
    source_key: str
    raw: object

    def __post_init__(self):
        if self.source not in SOURCES:
            raise ValueError(f"unknown import source {self.source!r}")
        if not self.source_key:
            raise ValueError(f"{self.source}: a record needs a non-empty key")

    def raw_json(self) -> str:
        return json.dumps(self.raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    def raw_sha256(self) -> str:
        return hashlib.sha256(self.raw_json().encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_digest(path: Path) -> str:
    """sha256 of a file, or of every file under a folder in sorted relative-path order.

    A folder's digest covers each file's relative path and its content, so a rename changes it.
    """
    path = Path(path)
    if path.is_dir():
        digest = hashlib.sha256()
        for file in sorted(p for p in path.rglob("*") if p.is_file()):
            digest.update(file.relative_to(path).as_posix().encode("utf-8") + b"\0")
            digest.update(_file_sha256(file).encode("ascii") + b"\n")
        return digest.hexdigest()
    if not path.is_file():
        raise SourceError(f"no file at {path}")
    try:
        return _file_sha256(path)
    except OSError as e:
        raise SourceError(f"cannot read {path}: {e}") from e


def open_radar(path: Path) -> sqlite3.Connection:
    """Job Radar's database, read only: `mode=ro` plus `query_only`, so nothing can be written, not even a journal."""
    path = Path(path)
    if not path.is_file():
        raise SourceError(f"radar_db: no file at {path}")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON")
        return conn
    except sqlite3.Error as e:
        raise SourceError(f"radar_db: cannot open {path}: {e}") from e
