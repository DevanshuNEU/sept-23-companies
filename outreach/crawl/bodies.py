"""Posting bodies: the stored hash, the replace that keeps a decision honest, and the detail-request fetches (#21).

A body is plain text (the adapter strips HTML before it returns one). The hash is `outreach.store.body_sha256`, the
one every stream shares, so a body that only changes its whitespace is not a new body. The fetches run with no
transaction open and hand each body to the caller as it arrives, so a request never sits inside a write and a tripped
host cannot throw away the bodies already fetched.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Collection
from dataclasses import dataclass

from outreach.crawl.base import Adapter, JobRef
from outreach.crawl.boardstate import BoardRow
from outreach.crawl.decisions import reset_provisional
from outreach.crawl.polite import HostTripped
from outreach.crawl.postings import native_id_of
from outreach.store import atomic, body_sha256, utcnow

__all__ = ["stored_sha", "save_body", "missing_body_ids", "refetch_body_ids", "fetch_bodies", "fetch_missing_bodies",
           "BodyFetch", "BODY_LANES"]

# Lanes whose postings get a body: the rules and the reader read a body for these only.
BODY_LANES = ("engineering", "intern")


@dataclass(frozen=True)
class BodyFetch:
    """What a fetch pass got: posting_id to text for the non-empty answers, and how many requests failed (an adapter
    exception; an empty answer is not a failure)."""
    bodies: dict[int, str]
    failed: int


def stored_sha(conn: sqlite3.Connection, posting_id: int) -> str | None:
    """The stored `body_sha256`; the hash of the stored text when it is '' (an imported body, filled lazily); None when
    the posting has no body. Reads only."""
    row = conn.execute("SELECT body, body_sha256 FROM posting_body WHERE posting_id = ?", (posting_id,)).fetchone()
    if row is None:
        return None
    return row[1] or body_sha256(row[0])


def _upsert_body(conn: sqlite3.Connection, posting_id: int, text: str, sha: str) -> None:
    conn.execute(
        "INSERT INTO posting_body(posting_id, body, fetched_at, body_sha256) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(posting_id) DO UPDATE SET body = excluded.body, fetched_at = excluded.fetched_at, "
        "body_sha256 = excluded.body_sha256", (posting_id, text, utcnow(), sha))


def save_body(conn: sqlite3.Connection, posting_id: int, text: str) -> str:
    """The one way to store a body. Returns the hash.

    In one unit it compares the stored hash with the new one and, when they differ (or there was no body), first makes
    the posting's current-ruleset decision provisional with no reader version, then upserts the body. A crash can
    therefore never leave a new body under an old final decision: the unit either keeps the old body with its old
    decision, or has both new. A body that only changes whitespace keeps its decision.
    """
    sha = body_sha256(text)
    with atomic(conn, "save_body"):
        if stored_sha(conn, posting_id) != sha:
            reset_provisional(conn, [posting_id])
        _upsert_body(conn, posting_id, text, sha)
    return sha


def missing_body_ids(conn: sqlite3.Connection, board: BoardRow, limit: int) -> list[tuple[int, str]]:
    """`(posting_id, native_id)` of the board's open postings that have no body and whose current decision is in
    `BODY_LANES`, newest first (posted date, then first seen; an undated posting sorts after the dated ones)."""
    marks = ", ".join("?" * len(BODY_LANES))
    rows = conn.execute(
        "SELECT p.posting_id, p.source_key FROM posting p "
        "JOIN current_decision d ON d.posting_id = p.posting_id "
        "LEFT JOIN posting_body b ON b.posting_id = p.posting_id "
        f"WHERE p.board_id = ? AND p.closed_at IS NULL AND b.posting_id IS NULL AND d.lane IN ({marks}) "
        "ORDER BY p.posted_on DESC, p.first_seen_at DESC, p.posting_id DESC LIMIT ?",
        (board.board_id, *BODY_LANES, limit)).fetchall()
    return [(r[0], native_id_of(r[1], board.ats, board.token)) for r in rows]


def refetch_body_ids(conn: sqlite3.Connection, board: BoardRow, changed_posting_ids: Collection[int], now: str,
                     max_age_days: int, limit: int) -> list[tuple[int, str]]:
    """`(posting_id, native_id)` of bodies to fetch again (ruling 6): open postings of the board that already have a
    body and are either in `changed_posting_ids` (the list showed a changed title or date) with a decision in
    `BODY_LANES`, or lane 1 (engineering, `level_ok`) with a body fetched `max_age_days` ago or longer (an imported
    body has no fetch time and counts as old). Changed ones first, then the oldest; at most `limit`. A posting with no
    body at all belongs to `missing_body_ids`."""
    if limit < 1:
        return []
    marks = ", ".join("?" * len(BODY_LANES))
    rows = conn.execute(
        "SELECT p.posting_id, p.source_key FROM posting p "
        "JOIN current_decision d ON d.posting_id = p.posting_id "
        "JOIN posting_body b ON b.posting_id = p.posting_id "
        "WHERE p.board_id = ? AND p.closed_at IS NULL AND ("
        f"(p.posting_id IN (SELECT value FROM json_each(?)) AND d.lane IN ({marks})) "
        "OR (d.lane = 'engineering' AND d.level_ok = 1 AND "
        "(b.fetched_at IS NULL OR b.fetched_at <= strftime('%Y-%m-%dT%H:%M:%SZ', ?, ?)))) "
        "ORDER BY (p.posting_id IN (SELECT value FROM json_each(?))) DESC, b.fetched_at, p.posting_id LIMIT ?",
        (board.board_id, json.dumps(sorted(set(changed_posting_ids))), *BODY_LANES, now, f"-{max_age_days} days",
         json.dumps(sorted(set(changed_posting_ids))), limit)).fetchall()
    return [(r[0], native_id_of(r[1], board.ats, board.token)) for r in rows]


def fetch_bodies(conn: sqlite3.Connection, adapter: Adapter, board: BoardRow, wanted: list[tuple[int, str]], *,
                 on_body: Callable[[int, str], None] | None = None) -> BodyFetch:
    """Ask the adapter for the body of each `(posting_id, native_id)` in `wanted`.

    Opens no transaction. Each non-empty answer goes to `on_body(posting_id, text)` as soon as it arrives, between
    requests, so the caller stores as it goes (`save_body`). A failure for one posting leaves it as it was and is
    counted in the result. A `HostTripped` is not a failure of the posting: it propagates, and what `on_body` already
    stored stays stored.
    """
    found: dict[int, str] = {}
    failed = 0
    for posting_id, native_id in wanted:
        row = conn.execute("SELECT title, url, location, country, posted_on FROM posting WHERE posting_id = ?",
                           (posting_id,)).fetchone()
        job = JobRef(native_id, row[0], row[1], row[2], row[3], row[4], None)
        try:
            text = adapter.body(board.token, job)
        except HostTripped:
            raise
        except Exception:
            failed += 1
            continue
        if text and text.strip():
            found[posting_id] = text
            if on_body is not None:
                on_body(posting_id, text)
    return BodyFetch(found, failed)


def fetch_missing_bodies(conn: sqlite3.Connection, adapter: Adapter, board: BoardRow, *, limit: int,
                         on_body: Callable[[int, str], None] | None = None) -> BodyFetch:
    """`fetch_bodies` for up to `limit` postings from `missing_body_ids`."""
    return fetch_bodies(conn, adapter, board, missing_body_ids(conn, board, limit), on_body=on_body)
