"""The read streak rule: when a board's postings close and when Devanshu is told a board is unreadable.

Ported from refresh/board_status.py (update, action, needs_review). A read is `ok`, `empty`, `error` or `unchanged`.
An error never closes a posting. An empty board closes its postings only on the second empty read in a row; an error
in between leaves the empty count alone. Three errors in a row need review. `unchanged` (a 304, or the same ids)
counts as `ok`.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Streaks", "next_streaks", "closes_postings", "needs_review", "READS"]

READS = ("ok", "empty", "error", "unchanged")
EMPTIES_TO_CLOSE = 2
ERRORS_TO_REVIEW = 3


@dataclass(frozen=True)
class Streaks:
    last_read: str
    empty_streak: int
    error_streak: int
    streak_since: str  # when the current run of `last_read` began


def next_streaks(prev_last: str | None, prev_empty: int, prev_error: int, prev_since: str | None, read: str,
                 now: str) -> Streaks:
    """The board's streaks after one more read of kind `read` at `now`."""
    if read in ("ok", "unchanged"):
        empty = error = 0
    elif read == "empty":
        empty, error = prev_empty + 1, 0
    elif read == "error":
        empty, error = prev_empty, prev_error + 1
    else:
        raise ValueError(f"unknown read: {read!r}")
    since = prev_since if read == prev_last and prev_since else now
    return Streaks(last_read=read, empty_streak=empty, error_streak=error, streak_since=since)


def closes_postings(read: str, empty_streak: int) -> bool:
    """True only for the second (or later) empty read in a row: then every open posting closes, `board_empty_twice`."""
    return read == "empty" and empty_streak >= EMPTIES_TO_CLOSE


def needs_review(error_streak: int) -> bool:
    """Three errors in a row: raise `board_unreadable`. Never a close."""
    return error_streak >= ERRORS_TO_REVIEW
