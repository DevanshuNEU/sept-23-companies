"""The Outbox writes: save, the one approve, unapprove, skip and the reply kind. Each runs in the caller's
`run_write` transaction, checks the revision the page loaded, and appends one event by Devanshu keyed
`ctx.event_key(0)`.

`approve_message` is the only code in the API that moves a message to `approved` (a test pins it). It decides on
the store as it is now: `run_checks` inside the write transaction, after the caller has looked the mail server up
outside it. Any block raises `Blocked` with the fresh checks; the route stores those checks in a second short
transaction and answers 422. Warns never block.

A said-no or asked-to-stop reply kind applies the outcome writes' own reply effects (`apply_reply_effects`: the
person's no-go on every channel and every unsent message stopped). An auto-reply puts the thread back as it was
before the reply (open, bounced or closed), takes the reply off its outcome row and clears the hold it made, so
neither the hold nor the reply stop counts it.
"""
import itertools
import json
import sqlite3

from outreach.api.errors import ApiProblem
from outreach.api.idempotency import WriteCtx
from outreach.api.queries.outcomes import apply_reply_effects
from outreach.api.revisions import bump
from outreach.drafts.checks import draft_input, email_domain, run_checks
from outreach.drafts.followups import canonical_company
from outreach.outcomes.correct import correct_outcome
from outreach.sending.followups import stop_planned
from outreach.store.writes import append_event

NOT_SAVED = "Nothing was saved."
CLAIMED = ("sending", "send_unknown", "sent")
ALREADY_OUT = "It is already being sent or was sent."
SKIPPABLE = ("drafted", "approved", "failed")


class Blocked(Exception):
    """Approve found a block. Raised inside the write so it rolls back; the route saves `checks` and answers 422."""

    def __init__(self, message_id: int, revision: int, checks: list[dict]):
        super().__init__("blocked")
        self.message_id, self.revision, self.checks = message_id, revision, checks

    @property
    def message(self) -> str:
        titles = "; ".join(c["title"] for c in self.checks if c["severity"] == "block")
        return f"This draft cannot be approved: {titles}. Fix it, then approve again. Nothing was approved."


def _message(conn: sqlite3.Connection, message_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT m.*, t.state AS touch_state, t.first_sent_at, p.person_id, p.company_id FROM message m "
        "JOIN touch t ON t.touch_id = m.touch_id JOIN person p ON p.person_id = t.person_id "
        "WHERE m.message_id = ?", (message_id,)).fetchone()
    if row is None:
        raise ApiProblem("not_found", f"There is no email {message_id}.")
    return row


def _fresh(row: sqlite3.Row, revision: int) -> None:
    if row["revision"] != revision:
        raise ApiProblem("conflict", f"The draft changed since you loaded it. {NOT_SAVED}")


def _event(ctx: WriteCtx, row: sqlite3.Row, kind: str, detail: dict) -> None:
    append_event(ctx.conn, entity="message", entity_id=row["message_id"],
                 company_id=canonical_company(ctx.conn, row["company_id"]), kind=kind, actor="devanshu", at=ctx.now,
                 detail={"seq": row["seq"], **detail}, idempotency_key=ctx.event_key(0))


def _checks(ctx: WriteCtx, message_id: int, banned: list) -> list[dict]:
    return run_checks(ctx.conn, draft_input(ctx.conn, message_id, now=ctx.now), banned=banned, now=ctx.now)


def mail_domain_of(conn: sqlite3.Connection, message_id: int) -> str | None:
    """The address domain approve looks up before its transaction (None when there is none to look up)."""
    row = conn.execute("SELECT p.email FROM message m JOIN touch t ON t.touch_id = m.touch_id "
                       "JOIN person p ON p.person_id = t.person_id WHERE m.message_id = ?", (message_id,)).fetchone()
    if row is None:
        raise ApiProblem("not_found", f"There is no email {message_id}.")
    return email_domain(row["email"])


def approve_message(ctx: WriteCtx, message_id: int, revision: int, banned: list) -> int:
    """Approves a clean draft and returns its touch id. Only a `drafted` message at its loaded revision; any block
    raises `Blocked` (nothing written here)."""
    row = _message(ctx.conn, message_id)
    _fresh(row, revision)
    if row["state"] != "drafted":
        raise ApiProblem("not_allowed", "Only a draft can be approved.")
    found = _checks(ctx, message_id, banned)
    if any(c["severity"] == "block" for c in found):
        raise Blocked(message_id, revision, found)
    cur = ctx.conn.execute(
        "UPDATE message SET state = 'approved', approved_at = ?, checks = ?, updated_at = ?, revision = revision + 1 "
        "WHERE message_id = ? AND revision = ? AND state = 'drafted'",
        (ctx.now, json.dumps(found), ctx.now, message_id, revision))
    if cur.rowcount != 1:  # cannot happen inside BEGIN IMMEDIATE after the reads above; never approve on a doubt
        raise ApiProblem("conflict", f"The draft changed since you loaded it. {NOT_SAVED}")
    _event(ctx, row, "approved", {"revision": revision + 1,
                                  "warns": [c["code"] for c in found if c["severity"] == "warn"]})
    return row["touch_id"]


def save_blocked_checks(conn: sqlite3.Connection, blocked: Blocked) -> None:
    """The second short transaction of a refused approve: the fresh checks on the message, its revision unchanged,
    and only while it is still the draft approve read."""
    conn.execute("UPDATE message SET checks = ? WHERE message_id = ? AND revision = ? AND state = 'drafted'",
                 (json.dumps(blocked.checks), blocked.message_id, blocked.revision))


def save_message(ctx: WriteCtx, message_id: int, subject: str, body: str, revision: int, banned: list) -> int:
    """His edit of a draft, with fresh checks stored. Only a draft: an approved email is unapproved first, so no edit
    goes out unchecked. The mail server is read from the cache (approve looks it up)."""
    row = _message(ctx.conn, message_id)
    _fresh(row, revision)
    if row["state"] != "drafted":
        hint = " Unapprove it first." if row["state"] == "approved" else ""
        raise ApiProblem("not_allowed", f"Only a draft can be edited.{hint} {NOT_SAVED}")
    bump(ctx.conn, "message", "message_id", message_id, revision,
         {"subject": subject, "body": body, "saved_by": "devanshu"}, at=ctx.now)
    found = _checks(ctx, message_id, banned)
    ctx.conn.execute("UPDATE message SET checks = ? WHERE message_id = ?", (json.dumps(found), message_id))
    _event(ctx, row, "saved", {"revision": revision + 1,
                               "blocks": [c["code"] for c in found if c["severity"] == "block"]})
    return row["touch_id"]


def unapprove_message(ctx: WriteCtx, message_id: int, revision: int) -> int:
    row = _message(ctx.conn, message_id)
    _fresh(row, revision)
    if row["state"] in CLAIMED:
        raise ApiProblem("not_allowed", ALREADY_OUT)
    if row["state"] != "approved":
        raise ApiProblem("not_allowed", f"Only an approved email can be unapproved. {NOT_SAVED}")
    bump(ctx.conn, "message", "message_id", message_id, revision, {"state": "drafted", "approved_at": None},
         at=ctx.now)
    _event(ctx, row, "unapproved", {"revision": revision + 1})
    return row["touch_id"]


def skip_message(ctx: WriteCtx, message_id: int, revision: int) -> int:
    """Skips one email. A planned follow-up has no text, so it goes through the sender's own `stop_planned`. A first
    email skipped before anything went out closes its thread (`skipped`)."""
    row = _message(ctx.conn, message_id)
    _fresh(row, revision)
    if row["state"] in CLAIMED:
        raise ApiProblem("not_allowed", ALREADY_OUT)
    if row["state"] == "planned":
        stop_planned(ctx.conn, message_id, reason="skipped", now=ctx.now, actor="devanshu")
        return row["touch_id"]
    if row["state"] not in SKIPPABLE:
        raise ApiProblem("not_allowed", f"It is already skipped. {NOT_SAVED}")
    bump(ctx.conn, "message", "message_id", message_id, revision, {"state": "skipped"}, at=ctx.now)
    _event(ctx, row, "skipped", {"revision": revision + 1})
    if row["seq"] == 0 and row["touch_state"] == "open" and row["first_sent_at"] is None:
        ctx.conn.execute("UPDATE touch SET state = 'closed', close_reason = 'skipped', closed_at = ?, updated_at = ?, "
                         "revision = revision + 1 WHERE touch_id = ? AND state = 'open'",
                         (ctx.now, ctx.now, row["touch_id"]))
        append_event(ctx.conn, entity="touch", entity_id=row["touch_id"],
                     company_id=canonical_company(ctx.conn, row["company_id"]), kind="closed", actor="devanshu",
                     at=ctx.now, detail={"reason": "skipped"}, idempotency_key=ctx.event_key(1))
    return row["touch_id"]


def state_before_reply(conn: sqlite3.Connection, touch: sqlite3.Row) -> str:
    """The state a thread had before the reply an auto-reply turned out to be: the sender's own `was` on its newest
    `replied` event, else bounced when it bounced, closed when it was closed, else open. A bounced or closed thread
    stays finished, so the queue still stops it (`touch_done`)."""
    row = conn.execute("SELECT json_extract(detail, '$.was') FROM event WHERE entity = 'touch' AND entity_id = ? "
                       "AND kind = 'replied' ORDER BY event_id DESC LIMIT 1", (touch["touch_id"],)).fetchone()
    was = row[0] if row is not None else None
    if was == "bounced" and touch["bounced_at"] is not None:
        return "bounced"
    if was == "closed" and touch["closed_at"] is not None and touch["close_reason"] is not None:
        return "closed"
    if was == "open":
        return "open"
    if touch["bounced_at"] is not None:
        return "bounced"
    if touch["closed_at"] is not None and touch["close_reason"] is not None:
        return "closed"
    return "open"


def set_reply_kind(ctx: WriteCtx, touch_id: int, kind: str, revision: int) -> int:
    conn = ctx.conn
    touch = conn.execute("SELECT t.*, p.company_id FROM touch t JOIN person p ON p.person_id = t.person_id "
                         "WHERE t.touch_id = ?", (touch_id,)).fetchone()
    if touch is None:
        raise ApiProblem("not_found", f"There is no email thread {touch_id}.")
    if touch["replied_at"] is None:
        raise ApiProblem("not_allowed", f"This email has no reply to sort. {NOT_SAVED}")
    if touch["revision"] != revision:
        raise ApiProblem("conflict", f"The reply changed since you loaded it. {NOT_SAVED}")
    survivor = canonical_company(conn, touch["company_id"])
    outcome = conn.execute("SELECT * FROM outreach_touch WHERE email_touch_id = ? AND undone_at IS NULL",
                           (touch_id,)).fetchone()
    if kind == "auto_reply":
        bump(conn, "touch", "touch_id", touch_id, revision,
             {"state": state_before_reply(conn, touch), "reply_kind": "auto_reply"}, at=ctx.now)
        if outcome is not None and outcome["replied_at"] is not None:  # an out-of-office is not an outcome
            correct_outcome(conn, outcome["touch_id"], "replied", at=None, reply_kind=None,
                            revision=outcome["revision"], now=ctx.now)
        conn.execute("UPDATE company SET held_by_touch = NULL, held_at = NULL, revision = revision + 1 "
                     "WHERE held_by_touch = ?", (touch_id,))
    else:
        bump(conn, "touch", "touch_id", touch_id, revision, {"state": "replied", "reply_kind": kind}, at=ctx.now)
        if outcome is not None:
            at = outcome["replied_at"] or max(touch["replied_at"], outcome["sent_at"])
            correct_outcome(conn, outcome["touch_id"], "replied", at=at, reply_kind=kind,
                            revision=outcome["revision"], now=ctx.now)
        keys = (ctx.event_key(n) for n in itertools.count(1))
        apply_reply_effects(conn, {"person_id": touch["person_id"], "company_id": survivor, "touch_id": touch_id},
                            kind, now=ctx.now, keys=keys)
    append_event(conn, entity="touch", entity_id=touch_id, company_id=survivor, kind="reply_kind_set",
                 actor="devanshu", at=ctx.now, idempotency_key=ctx.event_key(0),
                 detail={"from": touch["reply_kind"], "to": kind,
                         "text": f"Reply marked {kind.replace('_', ' ')}"})
    return touch_id
