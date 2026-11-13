"""Whether Gmail is shown to keep the sender's marker, and the one first marked send that shows it.

A send in doubt (its HTTP answer lost after Gmail took it) is settled by finding it in Sent. Gmail's API replaces
the Message-ID the sender sets with one of its own (the first live send showed it), so the sender puts its own
header, `X-Outreach-Id`, on every email: the token it claimed before the call (`pending_rfc_id`, recorded as
`marker` on the `claimed` event). Sent is searched by that marker. If Gmail dropped it, a send that went out would
look missing and could go out twice, so nothing goes out freely until Gmail is shown to keep it.

The proof is event `marker_verified` and nothing else: an email this sender claimed with a marker, read back from
Gmail by its Gmail id, with the `SENT` label and an `X-Outreach-Id` equal (normalised) to the marker it was claimed
with (`verify_marker`). The older events say nothing about the marker and never count: `import_verified` and
`send_verified` (a Message-ID found where it was set), `send_id_mismatch` (a Message-ID Gmail replaced, as it always
does), and a `sent` or `reconciled_sent` event. A claim made before the marker existed (its `claimed` event has no
`marker`) is not a marked claim: it neither proves nor blocks anything here.

With no proof, the sender may claim exactly one marked email, ever, and only while no message has a marked claim.
After that claim the phase follows that email:
- `in_doubt`: it is `sending` or `send_unknown`. Reconcile may find it in Sent by its marker; a miss proves nothing
  while the marker is unproven, so nothing else is claimed and it is never retried.
- `checking`: it is `sent` and waits for `verify_marker` (the next tick). A Gmail error keeps it here.
- `stopped`: it is anything else (a Sent miss made it `failed`, or a retry put it back to `approved`): its outcome
  cannot be shown, so sending stays stopped until a person clears it.
- `missing`: a marked email read back from Gmail came without its marker, under another one, not in Sent, or gone.
  Event `marker_missing` stops sending for good, even after a proof; only a person clearing it by hand starts it
  again.
Every marked email sent is read back once (`verify_marker`), the first one for the proof and every later one to
keep checking: one that comes back without its marker stops sending the same way. The read back also records the
Message-ID Gmail stored, when the send could not read it, so follow-ups and replies match on Gmail's own id.
`claim.claim` asks `may_claim` inside its own transaction, so no caller can claim a second email while unproven.
Read only, except `verify_marker`, which reads Gmail with no transaction open and then writes once.

All of it is per sender account (#9 multi-sender ruling, 2026-11-12). An event or a claim belongs to the account of
its message's touch (`touch.account_id`; none is account 1, the one account before accounts existed, and so is an
event that names no message). So account 1's old `marker_verified` still proves account 1, a new account sends its
own first email alone and is proven by its own read back, and `marker_missing` stops only the account it was found
on. `verify_marker` reads back only its own account's mail, with that account's client: another account's email is
not in this mailbox, and reading it there would look like Gmail lost it.
"""
import sqlite3
from datetime import datetime, timezone

from outreach.drafts.followups import canonical_company
from outreach.sending.mail import MESSAGE_ID, GmailError, MailClient, normalise_message_id
from outreach.store.atomic import atomic
from outreach.store.timefmt import ISO_FMT
from outreach.store.writes import append_event

__all__ = ["PROOF_SQL", "MISSING_SQL", "PROVEN", "FIRST_SEND", "CHECKING", "IN_DOUBT", "STOPPED", "MISSING",
           "PHASES", "OPEN", "PHASE_TEXT", "phase", "may_claim", "verify_marker"]

# The account an event on a message belongs to: its touch's, where none (a touch sent before accounts existed, or an
# event that names no message) is account 1.
_EVENT_ACCOUNT = ("COALESCE((SELECT t.account_id FROM message m JOIN touch t ON t.touch_id = m.touch_id "
                  "WHERE m.message_id = e.entity_id), 1)")
# Each takes one parameter, the account id.
PROOF_SQL = (f"SELECT 1 FROM event e WHERE e.entity = 'message' AND e.kind = 'marker_verified' "
             f"AND {_EVENT_ACCOUNT} = ? LIMIT 1")
MISSING_SQL = (f"SELECT 1 FROM event e WHERE e.entity = 'message' AND e.kind = 'marker_missing' "
               f"AND {_EVENT_ACCOUNT} = ? LIMIT 1")

PROVEN, FIRST_SEND, CHECKING, IN_DOUBT, STOPPED, MISSING = (
    "proven", "first_send", "checking", "in_doubt", "stopped", "missing")
PHASES = (PROVEN, FIRST_SEND, CHECKING, IN_DOUBT, STOPPED, MISSING)
# The phases in which a claim may be made: every email when proven, the one first email otherwise.
OPEN = frozenset({PROVEN, FIRST_SEND})

# What an approved email waiting on the proof shows (the queue reason `message_id_unproven`), by phase.
PHASE_TEXT = {
    FIRST_SEND: "The first email goes out alone, then Gmail is checked. The rest follow.",
    CHECKING: "First email sent, checking it in Sent. The rest follow.",
    IN_DOUBT: "The first email's send is in doubt. It is looked for in Sent, and nothing else goes out until then.",
    STOPPED: "Sending is stopped: the first email could not be found in Sent, so it is not known whether Gmail "
             "keeps the sender's marker. Nothing more is sent until this is cleared by hand.",
    MISSING: "Sending is stopped because Gmail did not keep the sender's marker on a sent email, so an email in "
             "doubt could not be found in Sent. Nothing more is sent until this is cleared by hand.",
}

# One account's messages with a marked claim (a `claimed` event that names its marker), and their state.
_MARKED = ("SELECT DISTINCT m.message_id, m.state FROM message m JOIN touch t ON t.touch_id = m.touch_id "
           "JOIN event e ON e.entity = 'message' AND e.entity_id = m.message_id AND e.kind = 'claimed' "
           "AND json_extract(e.detail, '$.marker') IS NOT NULL WHERE COALESCE(t.account_id, 1) = ?")


def phase(conn: sqlite3.Connection, account_id: int) -> str:
    """Where the proof stands for this account (see the module text). Read only."""
    if conn.execute(MISSING_SQL, (account_id,)).fetchone() is not None:
        return MISSING
    if conn.execute(PROOF_SQL, (account_id,)).fetchone() is not None:
        return PROVEN
    states = {row["state"] for row in conn.execute(_MARKED, (account_id,))}
    if not states:
        return FIRST_SEND
    if states & {"sending", "send_unknown"}:
        return IN_DOUBT
    if "sent" in states:
        return CHECKING
    return STOPPED


def may_claim(conn: sqlite3.Connection, account_id: int) -> bool:
    """True when a new claim from this account is allowed: Gmail is proven to keep its marker, or no marked email was
    ever claimed from it."""
    return phase(conn, account_id) in OPEN


def _stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime(ISO_FMT)


def _company_of(conn: sqlite3.Connection, touch_id: int) -> int:
    row = conn.execute("SELECT p.company_id FROM touch t JOIN person p ON p.person_id = t.person_id "
                       "WHERE t.touch_id = ?", (touch_id,)).fetchone()
    return canonical_company(conn, row[0])


# One account's sent messages with a marked claim that were not read back yet, with the marker of the (last) marked
# claim of each.
_TO_VERIFY = (
    "SELECT m.message_id, m.touch_id, m.seq, m.gmail_message_id, m.rfc_message_id, "
    "(SELECT json_extract(e.detail, '$.marker') FROM event e WHERE e.entity = 'message' "
    " AND e.entity_id = m.message_id AND e.kind = 'claimed' AND json_extract(e.detail, '$.marker') IS NOT NULL "
    " ORDER BY e.event_id DESC LIMIT 1) AS marker FROM message m JOIN touch t ON t.touch_id = m.touch_id "
    "WHERE m.state = 'sent' AND COALESCE(t.account_id, 1) = ? "
    "AND EXISTS (SELECT 1 FROM event e WHERE e.entity = 'message' AND e.entity_id = m.message_id "
    "AND e.kind = 'claimed' AND json_extract(e.detail, '$.marker') IS NOT NULL) "
    "AND NOT EXISTS (SELECT 1 FROM event e WHERE e.entity = 'message' AND e.entity_id = m.message_id "
    "AND e.kind IN ('marker_verified', 'marker_missing')) ORDER BY m.message_id LIMIT ?")


def _stored_id(conn: sqlite3.Connection, row: sqlite3.Row, stored: str, stamp: str) -> None:
    """Record the Message-ID Gmail stored for a sent message when it differs from the one recorded (the send could
    not read it back): on the message and, for a first email, on its touch, so a follow-up's In-Reply-To and a reply
    filed outside the thread match on Gmail's own id. Skipped for an id that is not one Message-ID or that another
    message already holds."""
    if not MESSAGE_ID.fullmatch(stored) or stored == normalise_message_id(row["rfc_message_id"]):
        return
    if conn.execute("SELECT 1 FROM message WHERE rfc_message_id = ? AND message_id <> ?",
                    (stored, row["message_id"])).fetchone():
        return
    conn.execute("UPDATE message SET rfc_message_id = ?, updated_at = ?, revision = revision + 1 "
                 "WHERE message_id = ? AND state = 'sent'", (stored, stamp, row["message_id"]))
    if row["seq"] == 0:
        conn.execute("UPDATE touch SET first_rfc_id = ?, updated_at = ?, revision = revision + 1 "
                     "WHERE touch_id = ? AND first_rfc_id IS ?",
                     (stored, stamp, row["touch_id"], row["rfc_message_id"]))


def verify_marker(conn: sqlite3.Connection, client: MailClient, account_id: int, *, now: datetime,
                  limit: int = 5) -> dict[str, int]:
    """Read each marked email this account recorded `sent` back from Gmail by its Gmail id, once, with no transaction
    open, then write once. `client` is that account's own: only its mail is read. While unproven that is the one first
    email (phase `checking`); once proven, every later one. The `SENT` label and an `X-Outreach-Id` equal
    (normalised) to the marker it was claimed with append `marker_verified` (the proof, when it is the first).
    Anything else Gmail answers (no marker, another one, no `SENT` label, a 404) appends `marker_missing`, which stops
    this account for good. Either way the Message-ID Gmail stored is recorded when it differs from the one the send
    recorded. Any other Gmail error writes nothing and ends the round; the next tick tries again. Nothing is read in
    any other phase. Returns counts."""
    if conn.in_transaction:
        raise RuntimeError("verify_marker calls Gmail, so it runs with no transaction open")
    counts = {"verified": 0, "missing": 0, "errors": 0}
    if phase(conn, account_id) not in (CHECKING, PROVEN):
        return counts
    results = []
    for row in conn.execute(_TO_VERIFY, (account_id, limit)).fetchall():
        claimed = normalise_message_id(row["marker"])
        detail = {"gmail_message_id": row["gmail_message_id"], "marker": row["marker"]}
        if not claimed or not row["gmail_message_id"]:
            results.append((row, "marker_missing", {**detail, "reason": "no_ids"}, ""))
            continue
        try:
            meta = client.message(row["gmail_message_id"])
        except GmailError as err:
            if err.status != 404:
                counts["errors"] += 1
                break
            results.append((row, "marker_missing", {**detail, "reason": "not_found"}, ""))
            continue
        found = normalise_message_id(meta.headers.get("x-outreach-id"))
        stored = normalise_message_id(meta.headers.get("message-id"))
        seen = {**detail, "found_marker": found, "stored_rfc_id": stored, "thread_id": meta.thread_id}
        if "SENT" in meta.labels and found == claimed:
            results.append((row, "marker_verified", seen, stored))
        else:
            reason = "not_in_sent" if "SENT" not in meta.labels else ("no_marker" if not found else "other_marker")
            results.append((row, "marker_missing", {**seen, "reason": reason}, stored))
    stamp = _stamp(now)
    with atomic(conn, "verify_marker"):
        for row, kind, detail, stored in results:
            if stored:
                _stored_id(conn, row, stored, stamp)
            append_event(conn, entity="message", entity_id=row["message_id"],
                         company_id=_company_of(conn, row["touch_id"]), kind=kind, actor="sender", at=stamp,
                         idempotency_key=f"{kind}:{row['message_id']}", detail=detail)
            counts["verified" if kind == "marker_verified" else "missing"] += 1
    return counts
