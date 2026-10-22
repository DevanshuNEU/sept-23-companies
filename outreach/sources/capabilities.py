"""What a source can offer the identity step, as small protocols (#31 Task 4)."""
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class SharedKey:
    """A key seen on more than one employer. It is recorded, never used to merge."""
    kind: Literal["fein", "domain"]
    value: str
    reason: str


class SharedKeySource(Protocol):
    def shared_keys(self) -> list[SharedKey]: ...


class TieredSource(Protocol):
    def work_tier(self, source_ref: str) -> tuple[int, str] | None: ...


@dataclass(frozen=True)
class PostRecord:
    """One post a source kept for a candidate (an HN "who is hiring" comment): `source_ref` is the candidate's."""
    source_ref: str
    url: str
    posted_at: str | None
    text: str
    asks_email: bool


class PostSource(Protocol):
    def posts(self) -> list[PostRecord]: ...
