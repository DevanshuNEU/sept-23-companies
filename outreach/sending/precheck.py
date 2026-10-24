"""The pre-claim check: the approve checks run again on the store as it is now, right before every claim (#14).

The sender calls `outreach.drafts.checks.run_checks`, the same function the API runs when Devanshu approves, so
there is one copy of the rules. Between his click and the send a colleague's draft can be saved, a no-go added or
a mail server lookup cached as missing; any `block` found then moves the message back to `drafted` for his review,
with the fresh checks stored (so the reason shows) and a `send_blocked` event. It is never sent as it stands.

Warnings never stop a send. The mail server comes from the `mx_check` cache only (the sender makes no DNS call):
a missing or stale row is `unknown`, which only warns.

A banned phrases list that cannot be read blocks every claim (fail closed), but that is not the draft's fault, so
the message stays `approved`, untouched, and goes out once the list is fixed.
"""
import json
import re
import sqlite3
from collections.abc import Sequence

from outreach.drafts.checks import draft_input, run_checks
from outreach.sending.queue import Queued
from outreach.store.atomic import atomic
from outreach.store.timefmt import iso_utc
from outreach.store.writes import append_event

__all__ = ["LIST_MISSING", "precheck"]

# The one block that is about the sender's own inputs, not the draft.
LIST_MISSING = "banned_list_missing"


def precheck(conn: sqlite3.Connection, q: Queued, *, now: str,
             banned: Sequence[tuple[str, re.Pattern]]) -> list[dict] | None:
    """Run the approve checks on `q` as stored now. Returns the blocks found, or None when it may be claimed.

    One transaction (a savepoint inside the caller's), so the checks and the move read the same data. On a block
    the message moves `approved -> drafted` with `checks` set to the fresh list, `updated_at` `now`, revision on by
    one, and event `send_blocked` naming the codes. When the only block is `banned_list_missing` (the list that
    `load_banned` returned is empty) nothing is written and the blocks are returned: no claim, approval kept. A
    message that is no longer `approved` returns None and is left to the claim, which then changes nothing."""
    stamp = iso_utc(now)
    with atomic(conn, "precheck"):
        row = conn.execute("SELECT state FROM message WHERE message_id = ?", (q.message_id,)).fetchone()
        if row is None or row["state"] != "approved":
            return None
        found = run_checks(conn, draft_input(conn, q.message_id, now=stamp), banned=banned, now=stamp)
        blocks = [c for c in found if c["severity"] == "block"]
        if not blocks:
            return None
        if all(b["code"] == LIST_MISSING for b in blocks):
            return blocks
        cur = conn.execute(
            "UPDATE message SET state = 'drafted', checks = ?, updated_at = ?, revision = revision + 1 "
            "WHERE message_id = ? AND state = 'approved' RETURNING revision", (json.dumps(found), stamp, q.message_id))
        revision = cur.fetchone()[0]
        append_event(conn, entity="message", entity_id=q.message_id, company_id=q.company_id, kind="send_blocked",
                     actor="sender", at=stamp, idempotency_key=f"send_blocked:{q.message_id}:{revision}",
                     detail={"codes": [b["code"] for b in blocks], "seq": q.seq, "revision": revision})
    return blocks
