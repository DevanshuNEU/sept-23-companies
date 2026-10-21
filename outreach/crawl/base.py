"""The crawl contract: board adapters, what a read returns, the change validators and the token grammar (#30)."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol

__all__ = ["Validators", "JobRef", "ReadResult", "Adapter", "ids_fingerprint", "valid_token", "BOARD_ATS"]


@dataclass(frozen=True)
class Validators:
    """What a board told us last time, sent back to skip an unchanged read."""
    etag: str = ""
    ids_fingerprint: str = ""


@dataclass(frozen=True)
class JobRef:
    """One posting in a board's list response. `body` is set only when the list response carries it."""
    native_id: str
    title: str
    url: str
    location: str
    country: str | None
    posted_on: str | None
    body: str | None


_RESULTS = ("ok", "empty", "error", "unchanged")


@dataclass(frozen=True)
class ReadResult:
    """The outcome of listing one board.

    `unchanged` and `error` carry no jobs, `error` needs a message, `empty` has no jobs and `ok` has at least one.
    `board_name` is the org name the ATS reports for the board when the list response carries it, else ''.
    """
    result: Literal["ok", "empty", "error", "unchanged"]
    jobs: tuple[JobRef, ...]
    validators: Validators
    http_status: int | None = None
    error: str = ""
    board_name: str = ""

    def __post_init__(self):
        if self.result not in _RESULTS:
            raise ValueError(f"result {self.result!r} is not one of {_RESULTS}")
        if self.result in ("unchanged", "error", "empty") and self.jobs:
            raise ValueError(f"a {self.result} read carries no jobs")
        if self.result == "error" and not self.error:
            raise ValueError("an error read needs an error message")
        if self.result == "ok" and not self.jobs:
            raise ValueError("an ok read has at least one job")


class Adapter(Protocol):
    """One ATS. `list` reads a board; `body` fetches one posting's text when the list did not carry it."""
    ats: str

    def list(self, token: str, validators: Validators) -> ReadResult:
        ...

    def body(self, token: str, job: JobRef) -> str | None:
        ...


def ids_fingerprint(native_ids: Iterable[str]) -> str:
    """sha256 hex of the sorted ids joined by a newline: the same set of ids gives the same value in any order."""
    return hashlib.sha256("\n".join(sorted(native_ids)).encode("utf-8")).hexdigest()


_SLUG = re.compile(r"[a-z0-9][a-z0-9._-]*")
_WORKDAY = re.compile(r"[a-z0-9-]+\.wd[0-9]+/[A-Za-z0-9_-]+")
_ORACLE = re.compile(r"[a-z0-9.-]+\.oraclecloud\.com/[A-Za-z0-9_-]+")
_ICIMS = re.compile(r"[a-z0-9.-]+\.icims\.com")
_GRAMMAR = {"workday": _WORKDAY, "oracle_orc": _ORACLE, "icims": _ICIMS}
# The board.ats CHECK list in 0001_initial.sql; a test keeps the two in sync.
BOARD_ATS = frozenset({
    "ashby", "greenhouse", "lever", "workable", "smartrecruiters", "rippling", "gem", "recruitee", "bamboohr", "dover",
    "breezy", "workday", "eightfold", "oracle_orc", "amazon", "apple", "google", "pinpoint", "teamtailor", "jazzhr",
    "paycom", "icims", "personio", "join_com", "cornerstone", "successfactors"})


def valid_token(ats: str, token: str) -> bool:
    """The one board-token grammar every stream shares (plan, Shared formats).

    Workday `<tenant>.wd<N>/<site>`, Oracle `<host>/<siteNumber>` (site kept as the ATS spells it), iCIMS the lower-case
    board host, and every other ATS a lower-case slug with no `%`. An ATS name outside `board.ats` (`BOARD_ATS`) is
    never valid, so a misspelled name fails loudly instead of quietly getting the slug grammar.
    """
    if not isinstance(token, str) or ats not in BOARD_ATS:
        return False
    return _GRAMMAR.get(ats, _SLUG).fullmatch(token) is not None
