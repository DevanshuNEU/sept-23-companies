"""Replace a job board from Needs you: the board he pastes becomes the company's, the board the item is about stops
being read (#16).

Ported from the API's first copy (`_replace_board` in `outreach/api/queries/needs_you.py`, #11 Task 20). Every
refusal comes before any write. The writes run in one unit, so a company is never left with no board and no postings
by a half write.
"""
from __future__ import annotations

import sqlite3

from outreach.identity import follow_merged
from outreach.review import words
from outreach.review.errors import ReviewError
from outreach.review.hand import NOT_SAVED
from outreach.review.items import unit
from outreach.sources.ats_detect import detect_ats, detect_unsupported
from outreach.sources.boards import add_board
from outreach.store import append_event
from outreach.store.timefmt import iso_utc

__all__ = ["edit_board"]

_EXAMPLE = "Paste the board's own address, for example https://jobs.ashbyhq.com/company."


def _bad(text: str) -> ReviewError:
    return ReviewError("bad_request", f"{text} {NOT_SAVED}")


def _owner(conn: sqlite3.Connection, ats: str, token: str) -> int | None:
    """The company that has this board: its row, else the `ats` alias that claims it."""
    row = conn.execute("SELECT company_id FROM board WHERE ats = ? AND token = ?", (ats, token)).fetchone()
    row = row or conn.execute("SELECT company_id FROM company_alias WHERE alias_key = lower(?)",
                              (f"{ats}:{token}",)).fetchone()
    return row[0] if row else None


def edit_board(conn: sqlite3.Connection, item_row: sqlite3.Row, url: str, source: str, *, now: str,
               actor: str = "devanshu", detail: dict | None = None) -> str:
    """Give the item's company the board at `url`, retire the board the item is about, and return `<ats>:<token>`.

    The new board is `hand` found, active and due now. The old board gets `active = 0`, its open postings close with
    `close_reason 'hand'`, and a `board` / `replaced` event records both. `detail`, when given, receives the counts
    the caller records. The caller settles the item.

    `bad_request` when the address is not a board we read (an unsupported host is named) or is the board the item
    is about; `conflict` when the board belongs to another company, or the item names another company's board. No
    `merge_candidate` is raised for it, and nothing is written.
    """
    at = iso_utc(now)
    survivor = follow_merged(conn, item_row["company_id"])
    with unit(conn):
        found = detect_ats(url)
        if not found:
            unsupported = detect_unsupported(url)
            if unsupported:
                raise _bad(f"{unsupported[0]} is a job site we cannot read yet. {_EXAMPLE}")
            raise _bad(f"That address is not a job board we can read. {_EXAMPLE}")
        ref = found[0]
        old = None
        if item_row["board_id"] is not None:
            old = conn.execute("SELECT * FROM board WHERE board_id = ?", (item_row["board_id"],)).fetchone()
        if old is not None and follow_merged(conn, old["company_id"]) != survivor:
            raise ReviewError("conflict", f"The board this item names belongs to another company. {NOT_SAVED}")
        if old is not None and (old["ats"], old["token"]) == (ref.ats, ref.token):
            raise _bad("That is the board we already read. If the board is right, mark ok.")
        # Before add_board, which raises a merge question of its own when the board is another company's.
        owner = _owner(conn, ref.ats, ref.token)
        if owner is not None and follow_merged(conn, owner) != survivor:
            name = conn.execute("SELECT name FROM company WHERE company_id = ?", (owner,)).fetchone()
            raise ReviewError("conflict", f"That board belongs to {name[0] if name else 'another company'} "
                                          f"(#{owner}). {NOT_SAVED}")
        try:
            added = add_board(conn, item_row["company_id"], ref, via="hand", matched_on="needs_you", found_by="hand",
                              actor=actor, now=at)
        except ValueError as err:
            raise _bad(f"That board cannot be added: {err}.") from None
        if added == "conflict":  # another writer claimed it since the check: retire nothing
            raise ReviewError("conflict", f"That board belongs to another company. {NOT_SAVED}")
        conn.execute("UPDATE board SET active = 1, next_read_at = NULL WHERE ats = ? AND token = ?",
                     (ref.ats, ref.token))
        new = f"{ref.ats}:{ref.token}"
        closed = 0
        if old is not None:
            conn.execute("UPDATE board SET active = 0 WHERE board_id = ?", (old["board_id"],))
            closed = conn.execute("UPDATE posting SET closed_at = ?, close_reason = 'hand' WHERE board_id = ? "
                                  "AND closed_at IS NULL", (at, old["board_id"])).rowcount
            append_event(conn, entity="board", kind="replaced", actor=actor, entity_id=old["board_id"],
                         company_id=survivor, at=at,
                         detail={"old": f"{old['ats']}:{old['token']}", "new": new, "closed_postings": closed,
                                 "source": source, "text": words.board_replaced_text(new, source)},
                         idempotency_key=f"board-replaced:{item_row['review_id']}:{item_row['revision']}")
        if detail is not None:
            detail["closed_postings"] = closed
            detail["old_board_id"] = old["board_id"] if old is not None else None
    return new
