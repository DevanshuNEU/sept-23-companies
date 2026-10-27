"""Whether Gmail is shown to keep the Message-ID the sender sets, and the one first send that shows it.

A send in doubt is settled by finding its Message-ID in Sent. If Gmail stored sent mail under an id of its own, a
send that went out would look missing and could go out twice, so nothing goes out freely until that is proven.
Proof is one of two events: `import_verified` (an imported email found in Sent under its own id, `verify_imported`)
or `send_verified` (this sender's own first email, read back from Gmail by its Gmail id: `SENT` label and the
Message-ID it was claimed with, `verify_sent`).

With no proof yet, the sender may claim exactly one email, ever, and only while no message has a `claimed` event.
After that claim the phase follows that email:
- `in_doubt`: it is `sending` or `send_unknown`. Reconcile may find it in Sent by Message-ID; a miss proves nothing
  while Gmail is unproven, so nothing else is claimed and it is never retried.
- `checking`: it is `sent` and waits for `verify_sent` (the next tick). A Gmail error keeps it here.
- `stopped`: it is anything else (a Sent miss made it `failed`, or a retry put it back to `approved`): its outcome
  cannot be shown, so sending stays stopped until Gmail is proven another way or a person clears it.
- `mismatch`: `verify_sent` found it under another Message-ID, not in Sent, or gone. Event `send_id_mismatch`
  stops sending for good, even if a proof turns up later; only a person clearing it by hand starts it again.
`claim.claim` asks `may_claim` inside its own transaction, so no caller can claim a second email while unproven.
Read only, except `verify_sent`, which reads Gmail with no transaction open and then writes once.
"""
import sqlite3
from datetime import datetime, timezone

from outreach.drafts.followups import canonical_company
from outreach.sending.mail import GmailError, MailClient, normalise_message_id
from outreach.store.atomic import atomic
from outreach.store.timefmt import ISO_FMT
from outreach.store.writes import append_event

__all__ = ["PROOF_SQL", "MISMATCH_SQL", "PROVEN", "FIRST_SEND", "CHECKING", "IN_DOUBT", "STOPPED", "MISMATCH",
           "PHASES", "OPEN", "PHASE_TEXT", "phase", "may_claim", "verify_sent"]

PROOF_SQL = ("SELECT 1 FROM event WHERE entity = 'message' AND kind IN ('import_verified', 'send_verified') "
             "LIMIT 1")
MISMATCH_SQL = "SELECT 1 FROM event WHERE entity = 'message' AND kind = 'send_id_mismatch' LIMIT 1"

PROVEN, FIRST_SEND, CHECKING, IN_DOUBT, STOPPED, MISMATCH = (
    "proven", "first_send", "checking", "in_doubt", "stopped", "mismatch")
PHASES = (PROVEN, FIRST_SEND, CHECKING, IN_DOUBT, STOPPED, MISMATCH)
# The phases in which a claim may be made: every email when proven, the one first email otherwise.
OPEN = frozenset({PROVEN, FIRST_SEND})

# What an approved email waiting on the proof shows (the queue reason `message_id_unproven`), by phase.
PHASE_TEXT = {
    FIRST_SEND: "The first email goes out alone, then Gmail is checked. The rest follow.",
    CHECKING: "First email sent, checking it in Sent. The rest follow.",
    IN_DOUBT: "The first email's send is in doubt. It is looked for in Sent, and nothing else goes out until then.",
    STOPPED: "Sending is stopped: the first email could not be found in Sent, so it is not known whether Gmail "
             "keeps the Message-ID. Nothing more is sent until this is cleared by hand.",
    MISMATCH: "Sending is stopped because Gmail did not keep the Message-ID on the first email, so an email in "
              "doubt could not be found in Sent. Nothing more is sent until this is cleared by hand.",
}

_CLAIMED = ("SELECT DISTINCT m.message_id, m.state FROM message m JOIN event e ON e.entity = 'message' "
            "AND e.entity_id = m.message_id AND e.kind = 'claimed'")


def phase(conn: sqlite3.Connection) -> str:
    """Where the proof stands (see the module text). Read only."""
    if conn.execute(MISMATCH_SQL).fetchone() is not None:
        return MISMATCH
    if conn.execute(PROOF_SQL).fetchone() is not None:
        return PROVEN
    states = {row["state"] for row in conn.execute(_CLAIMED)}
    if not states:
        return FIRST_SEND
    if states & {"sending", "send_unknown"}:
        return IN_DOUBT
    if "sent" in states:
        return CHECKING
    return STOPPED


def may_claim(conn: sqlite3.Connection) -> bool:
    """True when a new claim is allowed: Gmail is proven to keep the Message-ID, or no email was ever claimed."""
    return phase(conn) in OPEN


def _stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime(ISO_FMT)


def _company_of(conn: sqlite3.Connection, touch_id: int) -> int:
    row = conn.execute("SELECT p.company_id FROM touch t JOIN person p ON p.person_id = t.person_id "
                       "WHERE t.touch_id = ?", (touch_id,)).fetchone()
    return canonical_company(conn, row[0])


_TO_VERIFY = (
    "SELECT m.message_id, m.touch_id, m.gmail_message_id, "
    "(SELECT json_extract(e.detail, '$.claimed_rfc_id') FROM event e WHERE e.entity = 'message' "
    " AND e.entity_id = m.message_id AND e.kind IN ('sent', 'reconciled_sent') ORDER BY e.event_id DESC LIMIT 1) "
    "AS claimed_rfc_id FROM message m WHERE m.state = 'sent' "
    "AND EXISTS (SELECT 1 FROM event e WHERE e.entity = 'message' AND e.entity_id = m.message_id "
    "AND e.kind = 'claimed') "
    "AND NOT EXISTS (SELECT 1 FROM event e WHERE e.entity = 'message' AND e.entity_id = m.message_id "
    "AND e.kind IN ('send_verified', 'send_id_mismatch')) ORDER BY m.message_id LIMIT ?")


def verify_sent(conn: sqlite3.Connection, client: MailClient, *, now: datetime, limit: int = 5) -> dict[str, int]:
    """While nothing proves Gmail keeps the Message-ID (and no mismatch was found), read each email this sender
    recorded `sent` back from Gmail by its Gmail id, with no transaction open, then write once. The `SENT` label and
    a Message-ID header equal (normalised) to the one it was claimed with (the `claimed_rfc_id` of its `sent` or
    `reconciled_sent` event) append `send_verified`: the proof. Anything else Gmail answers (another or no
    Message-ID, no `SENT` label, a 404) appends `send_id_mismatch`, which stops sending for good. Any other Gmail
    error writes nothing and ends the round; the next tick tries again. Returns counts."""
    if conn.in_transaction:
        raise RuntimeError("verify_sent calls Gmail, so it runs with no transaction open")
    counts = {"verified": 0, "mismatch": 0, "errors": 0}
    if phase(conn) != CHECKING:
        return counts
    results = []
    for row in conn.execute(_TO_VERIFY, (limit,)).fetchall():
        claimed = normalise_message_id(row["claimed_rfc_id"])
        detail = {"gmail_message_id": row["gmail_message_id"], "claimed_rfc_id": row["claimed_rfc_id"]}
        if not claimed or not row["gmail_message_id"]:
            results.append((row, "send_id_mismatch", {**detail, "reason": "no_ids"}))
            continue
        try:
            meta = client.message(row["gmail_message_id"])
        except GmailError as err:
            if err.status != 404:
                counts["errors"] += 1
                break
            results.append((row, "send_id_mismatch", {**detail, "reason": "not_found"}))
            continue
        stored = normalise_message_id(meta.headers.get("message-id"))
        found = {**detail, "found_rfc_id": stored, "thread_id": meta.thread_id}
        if "SENT" in meta.labels and stored == claimed:
            results.append((row, "send_verified", found))
        else:
            reason = "not_in_sent" if "SENT" not in meta.labels else "other_message_id"
            results.append((row, "send_id_mismatch", {**found, "reason": reason}))
    stamp = _stamp(now)
    with atomic(conn, "verify_sent"):
        for row, kind, detail in results:
            append_event(conn, entity="message", entity_id=row["message_id"],
                         company_id=_company_of(conn, row["touch_id"]), kind=kind, actor="sender", at=stamp,
                         idempotency_key=f"{kind}:{row['message_id']}", detail=detail)
            counts["verified" if kind == "send_verified" else "mismatch"] += 1
    return counts
