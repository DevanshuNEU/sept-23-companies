"""A board's stored read state, and the one write that records a read: streaks, validators, board_read, review items.

`write_board_state` is the last write of a read (Task 11 phase 8), so the stored validators never run ahead of the
postings they describe: a crash before it leaves the old validators, and the next read lists the board again.
"""
from __future__ import annotations

import dataclasses
import sqlite3
from dataclasses import dataclass

from outreach.crawl.base import ReadResult
from outreach.crawl.hostblock import clear_host_block, host_blocked
from outreach.crawl.streaks import EMPTIES_TO_CLOSE, Streaks, needs_review, next_streaks
from outreach.store import append_event, atomic

__all__ = ["BoardRow", "load_board", "write_board_state", "board_empty_reason", "BOARD_READS_KEPT"]

BOARD_READS_KEPT = 10

@dataclass(frozen=True)
class BoardRow:
    board_id: int
    company_id: int
    ats: str
    token: str
    last_read: str | None
    empty_streak: int
    error_streak: int
    streak_since: str | None
    etag: str
    ids_fingerprint: str
    change_interval_s: int | None
    next_read_at: str | None
    last_read_at: str | None
    created_at: str
    # Beyond the plan's interface: how the board was found (the first-read check of ruling 8 needs it; no default,
    # so a BoardRow built by hand without it fails loudly) and the stored error text.
    found_by: str
    last_error: str = ""


_COLUMNS = tuple(f.name for f in dataclasses.fields(BoardRow))
# ok and unchanged are one state for the read_state event: both say the board was read and is fine.
_STATE = {"ok": "ok", "unchanged": "ok", "empty": "empty", "error": "error", None: None}


def load_board(conn: sqlite3.Connection, board_id: int) -> BoardRow:
    """The board's row. Raises LookupError when there is none."""
    row = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM board WHERE board_id = ?", (board_id,)).fetchone()
    if row is None:
        raise LookupError(f"no board {board_id}")
    return BoardRow(*row)


def write_board_state(conn: sqlite3.Connection, board: BoardRow, result: ReadResult, *, run_id: int, now: str,
                      open_postings: int, next_read_at: str, interval_s: int | None,
                      bucket: str | None = None) -> Streaks:
    """Record one read of `board` as one unit (its own BEGIN IMMEDIATE transaction, or a savepoint in the caller's).

    Validators: `ok` stores the result's; `unchanged` and `error` keep the stored ones (an error result carries
    empty validators, which must never overwrite good ones); `empty` clears both, so a board that comes back after
    emptying can never be read as `unchanged` and its postings get the chance to reopen (Review Focus 4).
    Review items (raised by the crawler, keyed on `streak_since` so a resolved item stays resolved while the same
    streak goes on): `board_unreadable` from the third error in a row, `board_empty` on the second empty read of an
    empty run, the one that closes the postings (#9: zero jobs only after 2 runs in a row; an error inside the run
    neither ends it nor raises the item again). The `board_empty` key is the run's first empty read, so an item an
    older first-read rule raised is the same item, and its reason is rewritten to say the postings closed. An `ok`
    or `unchanged` read clears
    both when open; an `empty` read clears only `board_unreadable`. The `read_state` event is appended when the state
    flips, with `ok` and `unchanged` as one state. Returns the new streaks.

    With the read's host `bucket` (the tick passes it), the host ruling applies (hostblock.py): a `board_unreadable`
    that is one of many on a host refusing the crawler becomes the host's one item, and a good read clears that item.
    """
    streaks = next_streaks(board.last_read, board.empty_streak, board.error_streak, board.streak_since,
                           result.result, now)
    if result.result == "ok":
        etag, fingerprint = result.validators.etag, result.validators.ids_fingerprint
    elif result.result == "empty":
        etag, fingerprint = "", ""
    else:  # unchanged, error
        etag, fingerprint = board.etag, board.ids_fingerprint
    error = result.error if result.result == "error" else ""
    postings = open_postings if result.result == "unchanged" else len(result.jobs)
    with atomic(conn, "board_state"):
        conn.execute(
            "UPDATE board SET last_read = ?, last_read_at = ?, empty_streak = ?, error_streak = ?, streak_since = ?, "
            "last_error = ?, etag = ?, ids_fingerprint = ?, next_read_at = ?, "
            "change_interval_s = coalesce(?, change_interval_s) WHERE board_id = ?",
            (streaks.last_read, now, streaks.empty_streak, streaks.error_streak, streaks.streak_since, error, etag,
             fingerprint, next_read_at, interval_s, board.board_id))
        conn.execute(
            "INSERT INTO board_read(board_id, run_id, at, result, http_status, error, postings) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(board_id, run_id) DO NOTHING",
            (board.board_id, run_id, now, result.result, result.http_status, error, postings))
        conn.execute(
            "DELETE FROM board_read WHERE board_id = ? AND run_id NOT IN ("
            "SELECT run_id FROM board_read WHERE board_id = ? ORDER BY at DESC, run_id DESC LIMIT ?)",
            (board.board_id, board.board_id, BOARD_READS_KEPT))
        if bucket and result.result != "error":
            clear_host_block(conn, bucket, now)
        if result.result in ("ok", "unchanged"):
            _clear(conn, board.board_id, ("board_unreadable", "board_empty"), now)
        elif result.result == "empty":
            _clear(conn, board.board_id, ("board_unreadable",), now)
            if streaks.empty_streak == EMPTIES_TO_CLOSE:  # #9: zero jobs only after 2 runs in a row
                closed = board_empty_reason(board, streaks.empty_streak, now)
                _raise(conn, board, "board_empty", _empty_run_start(conn, board.board_id, streaks.streak_since),
                       now, closed)
                # An item the first empty read raised before the two-read rule must say the postings closed.
                conn.execute(
                    "UPDATE review_item SET reason = ?, revision = revision + 1 "
                    "WHERE board_id = ? AND kind = 'board_empty' AND state = 'open' AND reason <> ?",
                    (closed, board.board_id, closed))
        if needs_review(streaks.error_streak) and not (bucket and host_blocked(conn, board.ats, bucket, now)):
            _raise(conn, board, "board_unreadable", streaks.streak_since, now,
                   f"The {board.ats} board {board.token} could not be read {streaks.error_streak} times in a row "
                   f"since {streaks.streak_since[:10]}. Its postings stay open. Last error: {error}")
        if _STATE[board.last_read] != _STATE[streaks.last_read]:
            append_event(conn, entity="board", kind="read_state", actor="crawler", entity_id=board.board_id,
                         company_id=board.company_id, at=now,
                         detail={"from": board.last_read, "to": streaks.last_read, "run_id": run_id,
                                 "empty_streak": streaks.empty_streak, "error_streak": streaks.error_streak},
                         idempotency_key=f"board-read-state:{board.board_id}:{run_id}")
    return streaks


def board_empty_reason(board: BoardRow, empty_streak: int, now: str) -> str:
    """What a `board_empty` item says, by how many empty reads in a row the board has had."""
    if empty_streak >= 2:
        return (f"The {board.ats} board {board.token} listed no jobs on {empty_streak} reads in a row "
                f"(the last on {now[:10]}), so its postings closed.")
    return (f"The {board.ats} board {board.token} listed no jobs on {now[:10]}. Its postings stay open for now; "
            "they close if the next read is empty too.")


def _empty_run_start(conn: sqlite3.Connection, board_id: int, fallback: str) -> str:
    """When the board's current empty run began: its first `empty` read since the last `ok` or `unchanged` one, from
    the reads kept. That is the `streak_since` the first empty read set, which an error inside the run moves on.
    `fallback` when the kept reads no longer hold it."""
    row = conn.execute(
        "SELECT min(at) FROM board_read WHERE board_id = ? AND result = 'empty' AND at > (SELECT coalesce(max(at), '') "
        "FROM board_read WHERE board_id = ? AND result IN ('ok', 'unchanged'))", (board_id, board_id)).fetchone()
    return row[0] or fallback


def _clear(conn: sqlite3.Connection, board_id: int, kinds: tuple[str, ...], now: str) -> None:
    marks = ", ".join("?" * len(kinds))
    conn.execute(
        "UPDATE review_item SET state = 'resolved', resolution = 'cleared', resolved_at = ?, revision = revision + 1 "
        f"WHERE board_id = ? AND state = 'open' AND kind IN ({marks})", (now, board_id, *kinds))


def _raise(conn: sqlite3.Connection, board: BoardRow, kind: str, evidence_key: str, now: str, reason: str) -> None:
    """One item per evidence key: the unique index makes a repeat a no-op, so a resolved item stays resolved."""
    conn.execute(
        "INSERT INTO review_item(kind, company_id, board_id, reason, evidence_key, raised_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'crawler', ?) ON CONFLICT DO NOTHING",
        (kind, board.company_id, board.board_id, reason, evidence_key, now))
