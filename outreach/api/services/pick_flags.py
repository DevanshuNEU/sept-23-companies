"""What a Not now flag does (2026-10-28, from Cadence's Belo Horizonte posting and UHIN's Integration Engineer).

A pick can be wrong in a way that says nothing about the company, so the effect lands on the posting or the board:

- `wrong_location`: the pick's posting is not in the US. It is closed by hand (the crawler never reopens a hand close).
- `wrong_company`: the pick's board belongs to another company with a like name. It is detached (`detach_board`).
- `role`: the position does not fit his skills. Nothing changes here; the posting is remembered in the event
  (`outreach.plan.fresh.load_passed_postings`) and is never an opening again.
- `other` with a note, and the two above: one `flagged` review item says what he flagged, in his words, so it is in
  front of him in Needs you.

A pick with no opening (a company row with no role) has no posting or board: the flag is recorded on the company.
Everything here runs inside the caller's `run_write` transaction. The company is never touched.
"""
import sqlite3

from outreach.api.models.plan import QueueOpening
from outreach.review.boardfix import detach_board

__all__ = ["apply_flag"]

_WORDS = {"wrong_location": "is not in the US", "wrong_company": "is on another company's board",
          "other": "was not right for you", "role": "does not fit your skills"}


def _raise_item(conn: sqlite3.Connection, *, reason: str, note: str | None, company_id: int, posting_id: int | None,
                board_id: int | None, title: str | None, location: str, now: str) -> None:
    name = conn.execute("SELECT name FROM company WHERE company_id = ?", (company_id,)).fetchone()["name"]
    what = f"\"{title}\"" + (f" ({location})" if location else "") if title else f"a pick at {name}"
    text = f"You flagged {what}: it {_WORDS[reason]}." + (f" You wrote: {note}" if note else "")
    conn.execute(
        "INSERT INTO review_item(kind, company_id, board_id, posting_id, reason, evidence_key, raised_by, created_at) "
        "VALUES ('flagged', ?, ?, ?, ?, ?, 'daily_list', ?) ON CONFLICT DO NOTHING",
        (company_id, board_id, posting_id, text, f"pick-flag:{reason}:{posting_id or company_id}", now))


def apply_flag(conn: sqlite3.Connection, *, reason: str | None, note: str | None, company_id: int,
               opening: QueueOpening | None, now: str) -> dict:
    """Do what the reason says and return what the `pick_passed` event adds to its detail: the note, and the opening's
    `posting_id` and `title` (null with no opening), whatever the reason."""
    posting_id = opening.posting_id if opening is not None else None
    out: dict = {"note": note, "posting_id": posting_id, "title": opening.title if opening is not None else None}
    if reason not in ("wrong_location", "wrong_company") and not (reason == "other" and note):
        return out
    location, board_id = "", None
    if posting_id is not None:
        post = conn.execute("SELECT location, board_id FROM posting WHERE posting_id = ?", (posting_id,)).fetchone()
        location, board_id = (post["location"], post["board_id"]) if post is not None else ("", None)
    if reason == "wrong_location" and posting_id is not None:
        out["closed"] = conn.execute("UPDATE posting SET closed_at = ?, close_reason = 'hand' "
                                     "WHERE posting_id = ? AND closed_at IS NULL", (now, posting_id)).rowcount
    if reason == "wrong_company" and board_id is not None:
        out["board_id"] = board_id
        out["closed"] = detach_board(conn, board_id, now=now)
    _raise_item(conn, reason=reason, note=note, company_id=company_id, posting_id=posting_id,
                board_id=board_id if reason == "wrong_company" else None, title=out["title"], location=location,
                now=now)
    return out
