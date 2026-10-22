"""Posting bodies: the stored hash, the one-statement save, and the detail-request backfill (#21, Task 9).

A body is plain text (the adapter strips HTML before it returns one). The hash is `outreach.store.body_sha256`, the
one every stream shares, so a body that only changes its whitespace is not a new body. `fetch_missing_bodies` runs
with no transaction open; the caller stores what it returns, so a request never sits inside a write.
"""
from __future__ import annotations

import sqlite3

from outreach.crawl.base import Adapter, JobRef
from outreach.crawl.boardstate import BoardRow
from outreach.crawl.polite import HostTripped
from outreach.crawl.postings import native_id_of
from outreach.store import body_sha256, utcnow

__all__ = ["stored_sha", "save_body", "missing_body_ids", "fetch_missing_bodies", "BODY_LANES"]

# Lanes whose postings get a body: the rules and the reader read a body for these only.
BODY_LANES = ("engineering", "intern")


def stored_sha(conn: sqlite3.Connection, posting_id: int) -> str | None:
    """The stored `body_sha256`; the hash of the stored text when it is '' (an imported body, filled lazily); None when
    the posting has no body. Reads only."""
    row = conn.execute("SELECT body, body_sha256 FROM posting_body WHERE posting_id = ?", (posting_id,)).fetchone()
    if row is None:
        return None
    return row[1] or body_sha256(row[0])


def save_body(conn: sqlite3.Connection, posting_id: int, text: str) -> str:
    """Store the body and its hash in one upsert statement. Returns the hash."""
    sha = body_sha256(text)
    conn.execute(
        "INSERT INTO posting_body(posting_id, body, fetched_at, body_sha256) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(posting_id) DO UPDATE SET body = excluded.body, fetched_at = excluded.fetched_at, "
        "body_sha256 = excluded.body_sha256", (posting_id, text, utcnow(), sha))
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


def fetch_missing_bodies(conn: sqlite3.Connection, adapter: Adapter, board: BoardRow, *, limit: int) -> dict[int, str]:
    """Ask the adapter for the body of up to `limit` postings from `missing_body_ids`. Returns posting_id to text for
    the non-empty answers; nothing is stored here.

    Opens no transaction. A failure for one posting leaves it missing, so the next read asks again. A `HostTripped`
    is not a failure of the posting: it propagates, and the rest of the bucket's requests are not sent.
    """
    wanted = missing_body_ids(conn, board, limit)
    found: dict[int, str] = {}
    for posting_id, native_id in wanted:
        row = conn.execute("SELECT title, url, location, country, posted_on FROM posting WHERE posting_id = ?",
                           (posting_id,)).fetchone()
        job = JobRef(native_id, row[0], row[1], row[2], row[3], row[4], None)
        try:
            text = adapter.body(board.token, job)
        except HostTripped:
            raise
        except Exception:
            continue
        if text and text.strip():
            found[posting_id] = text
    return found
