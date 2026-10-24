"""Raise, resolve, clear and settle review items (#16).

`raise_item` is the one helper every plan uses to raise an item. The unique index `review_item_once` makes the same
evidence a no-op, so an item he settled stays settled until its evidence key changes. Every write here runs in a
`unit`, so it works on its own and inside a caller's transaction.
"""
from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from outreach.review.errors import ReviewError
from outreach.store import append_event, atomic, utcnow
from outreach.store.timefmt import iso_utc

__all__ = ["unit", "Raised", "raise_item", "get_item", "resolve_item", "clear_item", "settle_siblings",
           "decided_before"]

_DASHES = (chr(0x2013), chr(0x2014))
# What he can settle an item as. `cleared` is the system's: only `clear_item` writes it.
_DECISIONS = ("marked_ok", "marked_no_go", "field_edited")


@contextlib.contextmanager
def unit(conn: sqlite3.Connection):
    """A write unit: BEGIN IMMEDIATE when the connection is idle, a savepoint inside a caller's transaction.

    A failure undoes only this unit and is re-raised.
    """
    with atomic(conn, "review_unit"):
        yield


@dataclass(frozen=True)
class Raised:
    review_id: int
    created: bool  # a new row was inserted
    reopened: bool  # a cleared row was put back open
    state: str  # the row's state after the call


def raise_item(conn: sqlite3.Connection, *, kind: str, company_id: int, reason: str, evidence_key: str,
               raised_by: str, actor: str = "system", reopen: bool = True, board_id: int | None = None,
               posting_id: int | None = None, person_id: int | None = None, now: str | None = None) -> Raised:
    """Raise one review item, or find the one already there for this evidence.

    The item is identified by the columns of `review_item_once`: kind, company, board, posting, person and
    `evidence_key`. A new row also appends a `raised` event by `actor`. A row already there is returned as it is,
    open or settled, with one exception: a row the system cleared (`cleared`) comes back open with the new reason
    when `reopen` is true. A row he settled never does.

    With `reopen=False` the only writes are the insert and its event, so it runs under the MCP write guard.

    Refuses a blank reason or one holding an en or em dash (`ValueError`; clean outside text with `words.clean`
    first). A CHECK the store enforces (an unknown kind or `raised_by`, a board item with no board, a posting item
    with no posting) raises `sqlite3.IntegrityError`; it is never swallowed.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("a review item needs a reason")
    if any(d in reason for d in _DASHES):
        raise ValueError("a review reason holds an en or em dash; clean it with words.clean first")
    created_at = iso_utc(now) if now else utcnow()
    with unit(conn):
        cur = conn.execute(
            "INSERT INTO review_item(kind, company_id, board_id, posting_id, person_id, reason, evidence_key, "
            "raised_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (kind, company_id, board_id, posting_id, person_id, reason, evidence_key, raised_by, created_at))
        if cur.rowcount:
            review_id = cur.lastrowid
            append_event(conn, entity="review_item", kind="raised", actor=actor, entity_id=review_id,
                         company_id=company_id, detail={"kind": kind, "raised_by": raised_by}, at=created_at,
                         idempotency_key=f"review-raised:{review_id}")
            return Raised(review_id, True, False, "open")
        row = conn.execute(
            "SELECT review_id, state, resolution FROM review_item WHERE kind = ? AND company_id = ? "
            "AND ifnull(board_id, 0) = ? AND ifnull(posting_id, 0) = ? AND ifnull(person_id, 0) = ? "
            "AND evidence_key = ?", (kind, company_id, board_id or 0, posting_id or 0, person_id or 0,
                                     evidence_key)).fetchone()
        if row is None:  # a conflict on the index means the row exists; reaching here is a bug, not a quiet no-op
            raise sqlite3.IntegrityError("review item conflicted on its index but cannot be found")
        review_id = row["review_id"]
        if reopen and row["state"] == "resolved" and row["resolution"] == "cleared":
            conn.execute(
                "UPDATE review_item SET state = 'open', resolution = NULL, resolved_at = NULL, "
                "resolution_note = '', reason = ?, revision = revision + 1 WHERE review_id = ?", (reason, review_id))
            return Raised(review_id, False, True, "open")
        return Raised(review_id, False, False, row["state"])


def get_item(conn: sqlite3.Connection, review_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM review_item WHERE review_id = ?", (review_id,)).fetchone()
    if row is None:
        raise ReviewError("not_found", "That review item does not exist.")
    return row


def resolve_item(conn: sqlite3.Connection, review_id: int, *, resolution: str, note: str, revision: int,
                 now: str | None = None) -> int:
    """Settle an open item as `marked_ok`, `marked_no_go` or `field_edited`. Returns the new revision.

    The update matches the revision he loaded, so a second tab or a double click settles it once.
    """
    if resolution not in _DECISIONS:
        raise ReviewError("bad_request", f"An item is settled as one of {', '.join(_DECISIONS)}, not {resolution!r}.")
    at = iso_utc(now) if now else utcnow()
    with unit(conn):
        cur = conn.execute(
            "UPDATE review_item SET state = 'resolved', resolution = ?, resolution_note = ?, resolved_at = ?, "
            "revision = revision + 1 WHERE review_id = ? AND revision = ? AND state = 'open'",
            (resolution, note, at, review_id, revision))
        if cur.rowcount:
            return revision + 1
        row = get_item(conn, review_id)
    if row["state"] == "resolved":
        raise ReviewError("not_allowed", "This item is already settled.")
    raise ReviewError("conflict", "This item changed since you loaded it.")


def clear_item(conn: sqlite3.Connection, review_id: int, note: str, now: str | None = None) -> bool:
    """The condition went away by itself: open becomes resolved `cleared`. False when the item was not open."""
    at = iso_utc(now) if now else utcnow()
    with unit(conn):
        cur = conn.execute(
            "UPDATE review_item SET state = 'resolved', resolution = 'cleared', resolution_note = ?, "
            "resolved_at = ?, revision = revision + 1 WHERE review_id = ? AND state = 'open'",
            (note, at, review_id))
        if not cur.rowcount:
            return False
        row = get_item(conn, review_id)
        append_event(conn, entity="review_item", kind="cleared", actor="system", entity_id=review_id,
                     company_id=row["company_id"], detail={"kind": row["kind"], "note": note}, at=at,
                     idempotency_key=f"review-cleared:{review_id}:{row['revision']}")
        return True


def settle_siblings(conn: sqlite3.Connection, company_id: int, *, kinds: Iterable[str] | None = None,
                    except_id: int, note: str, now: str | None = None) -> tuple[int, ...]:
    """Clear every other open item of the company (all kinds, or only `kinds`). Returns the ids it cleared."""
    wanted = None if kinds is None else tuple(kinds)
    if wanted is not None and not wanted:
        return ()
    sql = "SELECT review_id FROM review_item WHERE company_id = ? AND state = 'open' AND review_id <> ?"
    args: list = [company_id, except_id]
    if wanted is not None:
        sql += f" AND kind IN ({', '.join('?' * len(wanted))})"
        args.extend(wanted)
    with unit(conn):
        ids = [r["review_id"] for r in conn.execute(sql + " ORDER BY review_id", args)]
        return tuple(i for i in ids if clear_item(conn, i, note, now))


def decided_before(conn: sqlite3.Connection, *, kind: str, company_id: int, evidence_key: str) -> sqlite3.Row | None:
    """An item of this kind, company and key that he settled (ok, no-go or a field edit), on any posting, board or
    person. An item the system cleared is not a decision."""
    return conn.execute(
        "SELECT * FROM review_item WHERE kind = ? AND company_id = ? AND evidence_key = ? AND state = 'resolved' "
        "AND resolution IN ('marked_ok', 'marked_no_go', 'field_edited') "
        "ORDER BY resolved_at DESC, review_id DESC LIMIT 1", (kind, company_id, evidence_key)).fetchone()
