"""Decisions for the crawler: `outreach.rules` in batches, and the reset a changed body needs (#21, Task 9).

The crawler never classifies a title or reads a body for meaning; it calls `decide_and_store` and nothing else.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from outreach.rules import Decision, decide_and_store
from outreach.store import atomic

__all__ = ["decide_postings", "reset_provisional"]

_RESET_CHUNK = 500


def decide_postings(conn: sqlite3.Connection, posting_ids: Sequence[int], *, batch: int, rules_cfg=None,
                    combiner=None) -> dict[int, Decision]:
    """Decide each posting and upsert its row under the store's current ruleset, `batch` postings per transaction
    (decisions are CPU-bound, so a batch holds the write lock for about half a second at 100).

    `combiner` is the reader's (ruling 10); it is handed to `decide_and_store` only when there is one, so the call
    matches `decide_and_store` before and after #32 adds that argument. `RulesetNotCurrent` propagates; the batch it
    interrupts is rolled back and earlier batches stay.
    """
    if batch < 1:
        raise ValueError("batch must be at least 1")
    extra = {} if combiner is None else {"combiner": combiner}
    decided: dict[int, Decision] = {}
    for start in range(0, len(posting_ids), batch):
        with atomic(conn, "decide_batch"):
            for posting_id in posting_ids[start:start + batch]:
                decided[posting_id] = decide_and_store(conn, posting_id, cfg=rules_cfg, **extra)
    return decided


def reset_provisional(conn: sqlite3.Connection, posting_ids: Sequence[int]) -> int:
    """Set `provisional = 1` and `reader_version = NULL` on the current-ruleset rows of these postings: a changed body
    invalidates an earlier reader agreement. Returns how many rows changed (a row already provisional is left alone)."""
    changed = 0
    ids = list(dict.fromkeys(posting_ids))
    for start in range(0, len(ids), _RESET_CHUNK):
        chunk = ids[start:start + _RESET_CHUNK]
        with atomic(conn, "reset_provisional"):
            changed += conn.execute(
                "UPDATE decision SET provisional = 1, reader_version = NULL "
                f"WHERE posting_id IN ({', '.join('?' * len(chunk))}) "
                "AND ruleset_version = (SELECT ruleset_version FROM ruleset WHERE is_current = 1) "
                "AND (provisional <> 1 OR reader_version IS NOT NULL)", chunk).rowcount
    return changed
