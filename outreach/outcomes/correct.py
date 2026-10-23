"""Correcting an outcome from a touch's history: set or clear one outcome date, with a revision check.

`record_outcome` (the foundation) records an outcome once; this is the way to change or take one back. The schema
CHECKs stay true: clearing `replied_at` clears `reply_kind`, setting `replied_at` clears `quiet_at`, no date is
before the touch was sent, and an undone touch has no outcome. The UPDATE and the event are two statements: wrap
the call in `db.transaction` beside other writes.
"""
import sqlite3

from outreach.outcomes.wins import REPLY_KINDS
from outreach.store import append_event, iso_utc

OUTCOME_COLUMNS = {
    "accepted": "accepted_at", "replied": "replied_at", "referred": "referred_at",
    "interview": "interview_at", "offer": "offer_at", "quiet": "quiet_at",
}


def correct_outcome(
    conn: sqlite3.Connection,
    touch_id: int,
    kind: str,
    *,
    at: str | None,
    reply_kind: str | None,
    revision: int,
    actor: str = "devanshu",
) -> int:
    """Set outcome `kind` to `at` (any ISO-8601 time, stored as UTC) or clear it (`at=None`). Returns the new
    revision and appends a `corrected` event whose detail holds the old and new dates.

    `reply_kind` goes only with `replied`: given, it replaces the stored one; left out, a reply that already has
    a kind keeps it and a new reply is `unsorted`. Clearing `replied` also clears the reply kind.

    `LookupError` when the touch is missing or `revision` is stale (nothing is written; the API maps it to
    `conflict`). `ValueError`, in plain words, when `kind` is unknown, the touch is undone, `at` is before the
    touch was sent, `reply_kind` is not a reply kind or does not go with `kind`, or a touch that replied is made
    quiet.
    """
    column = OUTCOME_COLUMNS.get(kind)
    if column is None:
        raise ValueError(f"unknown outcome {kind!r}")
    if reply_kind is not None and kind != "replied":
        raise ValueError(f"a reply kind goes with a reply, not with {kind}")
    if reply_kind is not None and reply_kind not in REPLY_KINDS:
        raise ValueError(f"{reply_kind!r} is not a reply kind")
    if reply_kind is not None and at is None:
        raise ValueError("a reply kind needs a reply date; clear the reply to clear its kind")
    row = conn.execute("SELECT * FROM outreach_touch WHERE touch_id = ?", (touch_id,)).fetchone()
    if row is None:
        raise LookupError(f"no touch {touch_id}")
    if row["revision"] != revision:
        raise LookupError(f"touch {touch_id} is at revision {row['revision']}, not {revision}")
    if row["undone_at"] is not None:
        raise ValueError(f"touch {touch_id} was undone, so it has no outcomes to correct")
    when = iso_utc(at) if at is not None else None
    if when is not None and when < row["sent_at"]:
        raise ValueError(f"{kind} at {when} is before the touch was sent at {row['sent_at']}")
    if kind == "quiet" and when is not None and row["replied_at"] is not None:
        raise ValueError(f"touch {touch_id} has a reply, so it cannot be quiet")

    old_reply_kind = row["reply_kind"]
    sets, params = [f"{column} = ?"], [when]
    new_reply_kind = old_reply_kind
    if kind == "replied":
        new_reply_kind = None if when is None else (reply_kind or old_reply_kind or "unsorted")
        sets.append("reply_kind = ?")
        params.append(new_reply_kind)
        if when is not None:
            sets.append("quiet_at = NULL")
    cur = conn.execute(
        f"UPDATE outreach_touch SET {', '.join(sets)}, revision = revision + 1 WHERE touch_id = ? AND revision = ?",
        (*params, touch_id, revision),
    )
    if cur.rowcount == 0:
        raise LookupError(f"touch {touch_id} changed since revision {revision}")
    detail = {"outcome": kind, "old": row[column], "new": when}
    if kind == "replied":
        detail.update({"oldReplyKind": old_reply_kind, "newReplyKind": new_reply_kind})
    append_event(conn, entity="outreach_touch", kind="corrected", actor=actor, entity_id=touch_id,
                 company_id=row["company_id"], detail=detail)
    return revision + 1
