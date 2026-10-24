"""The facts the Needs-you view shows that a stored row cannot hold on its own (#16): a board item's reason from the
board as it is now, what changed when an item came back, and how many items he settled today."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from outreach.review import words
from outreach.review.items import get_item
from outreach.review.play import ny_date
from outreach.store.timefmt import ISO_FMT

__all__ = ["display_reason", "reopened_note", "settled_today"]

_NEW_YORK = ZoneInfo("America/New_York")
_BOARD_KINDS = ("board_unreadable", "board_empty")
_HIS = ("marked_ok", "marked_no_go", "field_edited")


def _row(conn: sqlite3.Connection, row: sqlite3.Row | int) -> sqlite3.Row:
    """The item row, given the row or its id."""
    return get_item(conn, row) if isinstance(row, int) else row


def display_reason(conn: sqlite3.Connection, row: sqlite3.Row | int) -> str:
    """The reason to show. A board item's is built from the board now (`words.board_reason`): #21 raises
    `board_empty` on the first empty read, so a stored sentence would still say "once" after the second empty read
    closed the postings. Closed postings are those closed `board_empty_twice` since the streak began. Every other
    kind, and a board never read, shows the stored reason."""
    row = _row(conn, row)
    if row["kind"] not in _BOARD_KINDS or row["board_id"] is None:
        return row["reason"]
    board = conn.execute("SELECT * FROM board WHERE board_id = ?", (row["board_id"],)).fetchone()
    if board is None or board["last_read"] is None:
        return row["reason"]
    since = board["streak_since"] or row["created_at"]
    last_ok = conn.execute("SELECT max(at) FROM board_read WHERE board_id = ? AND result IN ('ok', 'unchanged')",
                           (row["board_id"],)).fetchone()[0]
    open_postings = conn.execute("SELECT count(*) FROM posting WHERE board_id = ? AND closed_at IS NULL",
                                 (row["board_id"],)).fetchone()[0]
    closed = conn.execute("SELECT count(*) FROM posting WHERE board_id = ? AND close_reason = 'board_empty_twice' "
                          "AND closed_at >= ?", (row["board_id"], since)).fetchone()[0]
    return words.board_reason(row["kind"], ats=board["ats"], error_streak=board["error_streak"],
                              empty_streak=board["empty_streak"], last_error=board["last_error"], since=since,
                              last_ok_at=last_ok, open_postings=open_postings, closed_postings=closed)


def reopened_note(conn: sqlite3.Connection, row: sqlite3.Row | int) -> dict[str, str] | None:
    """What an item that came back should say, through `words.reopened`: the newest settled sibling (same kind,
    company, board and person, and the same posting except for trigger 3, which is one item per company) with
    another evidence key, raised no later than this one. None for a first item. Takes the row or its id."""
    row = _row(conn, row)
    sql = ("SELECT * FROM review_item WHERE kind = ? AND company_id = ? AND ifnull(board_id, 0) = ? "
           "AND ifnull(person_id, 0) = ? AND state = 'resolved' AND evidence_key <> ? AND review_id <> ? "
           "AND created_at <= ?")
    args: list = [row["kind"], row["company_id"], row["board_id"] or 0, row["person_id"] or 0, row["evidence_key"],
                  row["review_id"], row["created_at"]]
    if row["kind"] != "sponsorship_unclear":
        sql += " AND ifnull(posting_id, 0) = ?"
        args.append(row["posting_id"] or 0)
    previous = conn.execute(sql + " ORDER BY resolved_at DESC, review_id DESC LIMIT 1", args).fetchone()
    if previous is None:
        return None
    return words.reopened(row["kind"], dict(previous), row["evidence_key"])


def _day_bounds(day: str) -> tuple[str, str]:
    """[start, end) of a New York date, in canonical UTC."""
    start = datetime.combine(date.fromisoformat(day), time(), _NEW_YORK)
    end = datetime.combine(date.fromisoformat(day) + timedelta(days=1), time(), _NEW_YORK)
    return tuple(t.astimezone(timezone.utc).strftime(ISO_FMT) for t in (start, end))


def settled_today(conn: sqlite3.Connection, now: str) -> int:
    """How many items he settled (ok, no-go or a field he set) on today's New York date. Items that cleared by
    themselves do not count."""
    start, end = _day_bounds(ny_date(now))
    return conn.execute(
        "SELECT count(*) FROM review_item WHERE state = 'resolved' AND resolution IN (?, ?, ?) "
        "AND resolved_at >= ? AND resolved_at < ?", (*_HIS, start, end)).fetchone()[0]
