"""The source contract: what a company source yields and the Protocol every source follows (#30)."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

from outreach.rules.rebuild import EvidenceDraft

__all__ = ["Candidate", "EvidenceDraft", "Source", "KEY_NAMES"]

# `ats` is "<ats>:<token>"; the rest are the identifiers a source may know for a company.
KEY_NAMES = frozenset({"fein", "dba", "legal_name", "ats", "linkedin", "formd_cik"})


@dataclass(frozen=True)
class Candidate:
    """One company a source found, before it has a store row.

    `keys` holds only identifiers named in KEY_NAMES. Raises ValueError on an unknown key, an empty name, or a domain
    with an at sign, upper case or no dot (a domain is a bare lower-case host such as `acme.com`, or None).
    """
    name: str
    domain: str | None
    keys: dict[str, str]
    evidence: tuple[EvidenceDraft, ...] = field(default=())
    source_ref: str = ""

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("candidate name is empty")
        unknown = sorted(set(self.keys) - KEY_NAMES)
        if unknown:
            raise ValueError(f"unknown candidate key(s) {unknown}; allowed: {sorted(KEY_NAMES)}")
        d = self.domain
        if d is not None and ("@" in d or d != d.lower() or "." not in d):
            raise ValueError(f"bad candidate domain {d!r}: a lower-case host with a dot and no at sign")


class Source(Protocol):
    """A company source. `version` changes whenever its output could change, so a rerun can tell what set a row."""
    name: str
    version: str

    def discover(self, since: str | None) -> Iterator[Candidate]:
        ...
