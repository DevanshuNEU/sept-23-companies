"""The host ruling: a host that refuses the crawler for days raises one Needs-you item, not one per board (#21).

The breaker covers an outage inside one tick. A host that keeps refusing while only a few of its boards are due per
tick never trips it, so each board would reach three errors on its own and raise its own `board_unreadable`. Instead,
when a board reaches that point and `HOST_BOARDS` or more boards of the same bucket are in error streaks that all
began after the bucket's last good read, one `flagged` item is raised (or kept) for the bucket, on the board whose
streak is oldest, and no per-board `board_unreadable` item is raised for that bucket while it is open. The bucket's
next good read (`ok`, `unchanged` or `empty`) clears it. With fewer boards, each gets its own item as before.

A board's bucket is not stored. For every adapter here one ATS talks to one bucket (`bucket_for` of its API hosts,
EU hosts included), so the boards of a bucket are the boards of the reading board's ATS. The evidence key carries
the date the oldest streak began, so a later outage of the same host raises a new item while a resolved one stays
resolved.
"""
from __future__ import annotations

import sqlite3

__all__ = ["HOST_BOARDS", "host_key_prefix", "host_blocked", "clear_host_block"]

HOST_BOARDS = 3
_GOOD = "('ok', 'empty', 'unchanged')"


def host_key_prefix(bucket: str) -> str:
    return f"host-blocked:{bucket}:"


def _open_item(conn: sqlite3.Connection, bucket: str) -> bool:
    prefix = host_key_prefix(bucket)
    return conn.execute(
        "SELECT 1 FROM review_item WHERE kind = 'flagged' AND state = 'open' AND raised_by = 'crawler' "
        "AND substr(evidence_key, 1, ?) = ? LIMIT 1", (len(prefix), prefix)).fetchone() is not None


def _last_good(conn: sqlite3.Connection, ats: str) -> str | None:
    """When a board of `ats` last read well: its kept `board_read` rows, or a board whose last read was good."""
    return conn.execute(
        "SELECT max(at) FROM ("
        f"SELECT max(r.at) AS at FROM board_read r JOIN board b ON b.board_id = r.board_id "
        f"WHERE b.ats = :ats AND r.result IN {_GOOD} "
        f"UNION ALL SELECT max(last_read_at) FROM board WHERE ats = :ats AND last_read IN {_GOOD})",
        {"ats": ats}).fetchone()[0]


def host_blocked(conn: sqlite3.Connection, ats: str, bucket: str, now: str) -> bool:
    """Called, inside the board-state unit, when a board of `ats` in `bucket` would raise `board_unreadable`. True when
    the bucket's host item is open or is raised now: then the board raises no item of its own."""
    if _open_item(conn, bucket):
        return True
    last_good = _last_good(conn, ats)
    failing = conn.execute(
        "SELECT board_id, company_id, streak_since FROM board "
        "WHERE ats = :ats AND active = 1 AND last_read = 'error' AND (:good IS NULL OR streak_since > :good) "
        "ORDER BY streak_since, board_id", {"ats": ats, "good": last_good}).fetchall()
    if len(failing) < HOST_BOARDS:
        return False
    board_id, company_id, since = failing[0]
    conn.execute(
        "INSERT INTO review_item(kind, company_id, board_id, reason, evidence_key, raised_by, created_at) "
        "VALUES ('flagged', ?, ?, ?, ?, 'crawler', ?) ON CONFLICT DO NOTHING",
        (company_id, board_id,
         f"{bucket} refused the crawler for {len(failing)} boards since {since[:10]}. Their postings stay open; "
         "the boards are read again on their backoff, and the first good read clears this.",
         host_key_prefix(bucket) + since, now))
    return True


def clear_host_block(conn: sqlite3.Connection, bucket: str, now: str) -> int:
    """A good read in `bucket`: resolve its open host item as cleared. Returns how many were resolved."""
    prefix = host_key_prefix(bucket)
    return conn.execute(
        "UPDATE review_item SET state = 'resolved', resolution = 'cleared', resolved_at = ?, revision = revision + 1 "
        "WHERE kind = 'flagged' AND state = 'open' AND raised_by = 'crawler' AND substr(evidence_key, 1, ?) = ?",
        (now, len(prefix), prefix)).rowcount
