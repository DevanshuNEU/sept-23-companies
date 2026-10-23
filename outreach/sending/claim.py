"""Claim, send and record: the only code that sends an email.

The order is what keeps an email from going out twice:
1. `claim` commits `approved -> sending` with the new Message-ID in `pending_rfc_id`, on a fullfsync connection,
   before Gmail is called. The `message_one_sending` index lets only one message be `sending` at a time.
2. `client.send` runs with no transaction open.
3. `record_sent` commits `sent` with Gmail's ids, or `record_unclear` commits `send_unknown` when the call failed.

Each crash window ends in a state that is settled by Message-ID, never by sending again:
- before the call: the row stays `sending`; the next tick turns it into `send_unknown` (`leftover_claims`), and only
  a complete Sent scan that misses its Message-ID makes it `failed`, then `approved` again.
- after Gmail accepted, before the record: the same path, and the Sent scan finds it, so it is recorded `sent`.
- during the record: the record is one transaction, so it either committed (`sent`) or left `sending`.
"""
import json
import math
import random
import sqlite3
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from outreach.drafts.followups import canonical_company, company_map, held_survivors, no_go_sets, resolve_company
from outreach.outcomes.context import build_context
from outreach.sending import calendar as cal
from outreach.sending.config import SendConfig
from outreach.sending.followups import plan_followups
from outreach.sending.gmail import new_message_id
from outreach.sending.mail import (MESSAGE_ID, RETRYABLE, GmailError, MailClient, Outgoing, Sent,
                                   normalise_message_id, scrub)
from outreach.sending.queue import Queued
from outreach.sending.signals import as_datetime
from outreach.store.atomic import atomic, describe_error
from outreach.store.db import transaction
from outreach.store.timefmt import ISO_FMT
from outreach.store.writes import append_event, log_touch

__all__ = ["AnotherSendInFlight", "claim", "outgoing_for", "email_context", "record_sent", "record_unclear",
           "send_one", "PLACEHOLDER_DOMAIN"]

# #17 writes Message-IDs on this domain for imported mail whose real id is unknown; they are never put in a header.
PLACEHOLDER_DOMAIN = "@outreach.invalid>"

_NEW_YORK = ZoneInfo("America/New_York")

REWRITTEN_REASON = ("Sending is paused because Gmail stored a sent email under another Message-ID, so a send in "
                    "doubt could not be found in Sent. The email was recorded as sent. Check Gmail before resuming.")


class AnotherSendInFlight(RuntimeError):
    """Another message is `sending`: only one send is in flight across the whole store."""


def _stamp(at: datetime | str) -> str:
    return as_datetime(at).strftime(ISO_FMT)


# -- the claim --------------------------------------------------------------------------------------------------

def _still_sendable(conn: sqlite3.Connection, message_id: int) -> bool:
    """Read inside the claim's transaction: False when a stop, hold or pause landed after the queue picked the
    message (a no-go on the person or the company family, a closed, removed, paused or held company, a touch that
    is no longer open, or a paused sender). The queue already checked these; this closes the gap between the pick
    and the claim, so a person who said stop a second ago is never sent to."""
    row = conn.execute("SELECT t.state AS touch_state, p.person_id, p.company_id FROM message m "
                       "JOIN touch t ON t.touch_id = m.touch_id JOIN person p ON p.person_id = t.person_id "
                       "WHERE m.message_id = ?", (message_id,)).fetchone()
    if row is None or row["touch_state"] != "open":
        return False
    if conn.execute("SELECT paused FROM sender WHERE sender_id = 1").fetchone()["paused"]:
        return False
    companies = company_map(conn)
    survivor = resolve_company(companies, row["company_id"])[-1]
    no_go_people, no_go_companies = no_go_sets(conn, companies)
    company = companies[survivor]
    return not (row["person_id"] in no_go_people or survivor in no_go_companies
                or company["status"] in ("closed", "removed") or company["paused_at"] is not None
                or survivor in held_survivors(companies))


def _attempts(conn: sqlite3.Connection, message_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM event WHERE entity = 'message' AND entity_id = ? AND kind = 'claimed'",
                        (message_id,)).fetchone()[0]


def claim(conn: sqlite3.Connection, q: Queued, *, rfc_id: str, now: str) -> bool:
    """Commit `approved -> sending` with `rfc_id` as `pending_rfc_id`, in one transaction, and append `claimed`.
    False when nothing changed: the message moved on or was edited since `q` was read (its revision), or a stop,
    hold or pause landed since the pick. Raises AnotherSendInFlight when another message is `sending`, and
    ValueError when `rfc_id` is not one Message-ID."""
    if not MESSAGE_ID.fullmatch(rfc_id or ""):
        raise ValueError("a claim needs exactly one Message-ID")
    stamp = _stamp(now)
    try:
        with transaction(conn):
            if not _still_sendable(conn, q.message_id):
                return False
            cur = conn.execute(
                "UPDATE message SET state = 'sending', pending_rfc_id = ?, claimed_at = ?, updated_at = ?, "
                "revision = revision + 1 WHERE message_id = ? AND state = 'approved' AND revision = ?",
                (rfc_id, stamp, stamp, q.message_id, q.revision))
            if cur.rowcount == 0:
                return False
            append_event(conn, entity="message", entity_id=q.message_id, company_id=q.company_id, kind="claimed",
                         actor="sender", at=stamp,
                         detail={"pending_rfc_id": rfc_id, "attempt": _attempts(conn, q.message_id) + 1,
                                 "seq": q.seq},
                         idempotency_key=f"claimed:{rfc_id}")
    except sqlite3.IntegrityError as err:
        if conn.execute("SELECT 1 FROM message WHERE state = 'sending' AND message_id <> ?",
                        (q.message_id,)).fetchone():
            raise AnotherSendInFlight("another message is being sent; only one send is in flight") from err
        raise
    return True


# -- the email --------------------------------------------------------------------------------------------------

def _header_id(value: str | None) -> str | None:
    """A stored Message-ID fit for In-Reply-To and References, or None: one valid Message-ID that is not one of
    #17's placeholders."""
    text = normalise_message_id(value)
    if not MESSAGE_ID.fullmatch(text) or text.lower().endswith(PLACEHOLDER_DOMAIN):
        return None
    return text


def outgoing_for(conn: sqlite3.Connection, q: Queued, cfg: SendConfig, rfc_id: str) -> Outgoing:
    """The email for `q` as stored. A follow-up (seq 1 or more) goes in the touch's Gmail thread, In-Reply-To the
    newest sent message of that touch and References every one of them, oldest first. A placeholder or malformed
    Message-ID is left out of both; with none left, the follow-up still goes in the thread by `thread_id`."""
    row = conn.execute(
        "SELECT m.subject, m.body, m.seq, m.touch_id, p.email, t.gmail_thread_id FROM message m "
        "JOIN touch t ON t.touch_id = m.touch_id JOIN person p ON p.person_id = t.person_id WHERE m.message_id = ?",
        (q.message_id,)).fetchone()
    if row is None:
        raise LookupError(f"no message {q.message_id}")
    thread_id, in_reply_to, references = None, None, ()
    if row["seq"] >= 1:
        thread_id = row["gmail_thread_id"]
        ids = [_header_id(r["rfc_message_id"]) for r in conn.execute(
            "SELECT rfc_message_id FROM message WHERE touch_id = ? AND state = 'sent' AND message_id <> ? "
            "ORDER BY sent_at, seq", (row["touch_id"], q.message_id))]
        kept = tuple(i for i in ids if i is not None)
        if kept:
            in_reply_to, references = kept[-1], kept
    return Outgoing(to=row["email"] or "", subject=row["subject"], body=row["body"], from_name=cfg.from_name,
                    from_addr=cfg.from_address, message_id=rfc_id, thread_id=thread_id, in_reply_to=in_reply_to,
                    references=references)


# -- the outcome row's context ----------------------------------------------------------------------------------

def _score_parts(score: sqlite3.Row | None) -> tuple[str | None, str | None, dict[str, float]]:
    """The newest score's first lane (None when it has none), its ranker version and its signal values."""
    if score is None:
        return None, None, {}
    try:
        lanes = json.loads(score["lanes"])
        raw = json.loads(score["signals"])
    except ValueError:
        return None, score["ranker_version"], {}
    lane = lanes[0] if isinstance(lanes, list) and lanes and isinstance(lanes[0], str) and lanes[0] else None
    values: dict[str, float] = {}
    entries = raw.get("values") if isinstance(raw, dict) else None
    for name, entry in (entries.items() if isinstance(entries, dict) else ()):
        value = entry.get("value") if isinstance(entry, dict) else None
        if (isinstance(name, str) and name and isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value)):
            values[name] = float(value)
    return lane, score["ranker_version"], values


def _ny_day(text: str) -> date:
    """The New York date of a canonical UTC time (the outcome report counts role age in New York days)."""
    return as_datetime(text).astimezone(_NEW_YORK).date()


def email_context(conn: sqlite3.Connection, person_id: int, company_id: int, *, at: str) -> dict:
    """The context of the outreach touch an email logs (#30's seven keys, through #11's `build_context`).

    `source` is `plan` when the person is on a daily list row with channel `email` (the newest such row gives the
    lane and the posting), else `own`. The lane is that row's group, else the company's newest score's first lane,
    else `own` (as the API's own touches; `build_context` refuses an empty lane). The role's age is in New York days
    from the row's posting's first sighting to `at`. `company_id` is the surviving company."""
    entry = conn.execute(
        "SELECT e.list_group, e.posting_id, po.first_seen_at FROM daily_list_entry e "
        "LEFT JOIN posting po ON po.posting_id = e.posting_id WHERE e.person_id = ? AND e.channel = 'email' "
        "ORDER BY e.list_date DESC LIMIT 1", (person_id,)).fetchone()
    score = conn.execute("SELECT ranker_version, signals, lanes FROM company_score WHERE company_id = ? "
                         "ORDER BY scored_at DESC, ranker_version DESC LIMIT 1", (company_id,)).fetchone()
    score_lane, version, values = _score_parts(score)
    role_kind = conn.execute("SELECT role_kind FROM person WHERE person_id = ?", (person_id,)).fetchone()[0]
    age = None
    if entry is not None and entry["first_seen_at"] is not None:
        age = max((_ny_day(at) - _ny_day(entry["first_seen_at"])).days, 0)
    return build_context(
        source="plan" if entry is not None else "own",
        lane=entry["list_group"] if entry is not None else (score_lane or "own"),
        role_kind=role_kind, role_age_days=age, ranker_version=version, signals=values,
        role_named=entry is not None and entry["posting_id"] is not None)


# -- recording --------------------------------------------------------------------------------------------------

def _push_gap(conn: sqlite3.Connection, at: str, cfg: SendConfig, rng: random.Random) -> None:
    """`sender.next_send_at` becomes `at` plus a random gap, unless it is already later: a gap never shrinks."""
    candidate = (as_datetime(at) + timedelta(minutes=rng.uniform(*cfg.gap_minutes))).strftime(ISO_FMT)
    current = conn.execute("SELECT next_send_at FROM sender WHERE sender_id = 1").fetchone()[0]
    if current is None or candidate > current:
        conn.execute("UPDATE sender SET next_send_at = ? WHERE sender_id = 1", (candidate,))


def record_sent(conn: sqlite3.Connection, message_id: int, sent: Sent, *, at: str, cfg: SendConfig,
                rng: random.Random, reconciled: bool = False, now: str | None = None) -> None:
    """In one transaction (a savepoint inside the caller's): `sending` or `send_unknown` becomes `sent` with Gmail's
    ids, the claim cleared. A first email also fills the touch's first send, Message-ID and thread, logs its one
    outreach touch (`email:<touch_id>`, actor `sender`; none when one is already logged for this email touch) and
    plans its follow-ups while the touch is open. Event `sent`, or `reconciled_sent` with `reconciled_at` (`now`,
    default `at`). `at` is when it went out (a reconcile passes the claim time). `next_send_at` moves to `at` plus
    a gap, never earlier than it was. LookupError when the message is not `sending` or `send_unknown`."""
    stamp = _stamp(at)
    recorded = _stamp(now) if now else stamp
    with atomic(conn, "record_sent"):
        row = conn.execute(
            "SELECT m.state, m.seq, m.touch_id, m.pending_rfc_id, t.person_id, t.state AS touch_state, "
            "p.company_id FROM message m JOIN touch t ON t.touch_id = m.touch_id "
            "JOIN person p ON p.person_id = t.person_id WHERE m.message_id = ?", (message_id,)).fetchone()
        if row is None or row["state"] not in ("sending", "send_unknown"):
            raise LookupError(f"message {message_id} is not sending or send_unknown, so it cannot be recorded sent")
        company_id = canonical_company(conn, row["company_id"])
        conn.execute(
            "UPDATE message SET state = 'sent', rfc_message_id = ?, gmail_message_id = ?, sent_at = ?, "
            "pending_rfc_id = NULL, claimed_at = NULL, reconciled_at = ?, updated_at = ?, revision = revision + 1 "
            "WHERE message_id = ? AND state IN ('sending', 'send_unknown')",
            (sent.rfc_message_id, sent.gmail_message_id, stamp, recorded if reconciled else None, recorded,
             message_id))
        touch_id = row["touch_id"]
        if row["seq"] == 0:
            conn.execute("UPDATE touch SET first_sent_at = ?, first_rfc_id = ?, gmail_thread_id = ?, updated_at = ?, "
                         "revision = revision + 1 WHERE touch_id = ? AND first_sent_at IS NULL",
                         (stamp, sent.rfc_message_id, sent.thread_id, recorded, touch_id))
            logged = conn.execute("SELECT 1 FROM outreach_touch WHERE email_touch_id = ?", (touch_id,)).fetchone()
            if logged is None:
                log_touch(conn, person_id=row["person_id"], company_id=company_id, channel="email", sent_at=stamp,
                          context=email_context(conn, row["person_id"], company_id, at=stamp),
                          email_touch_id=touch_id, idempotency_key=f"email:{touch_id}", actor="sender")
            if row["touch_state"] == "open":
                plan_followups(conn, touch_id, cal.ny_date(as_datetime(stamp), cfg), cfg, now=recorded)
        kind = "reconciled_sent" if reconciled else "sent"
        append_event(conn, entity="message", entity_id=message_id, company_id=company_id, kind=kind, actor="sender",
                     at=stamp, idempotency_key=f"{kind}:{message_id}",
                     detail={"rfc_message_id": sent.rfc_message_id, "claimed_rfc_id": row["pending_rfc_id"],
                             "gmail_message_id": sent.gmail_message_id, "thread_id": sent.thread_id,
                             "seq": row["seq"]})
        _push_gap(conn, stamp, cfg, rng)


def _kind_of(err: BaseException) -> str:
    """The GmailError kind for an exception a client raised that is not a GmailError."""
    if isinstance(err, TimeoutError):
        return "timeout"
    if isinstance(err, OSError):
        return "network"
    return "server"


def record_unclear(conn: sqlite3.Connection, message_id: int, error: Exception, *, at: str, cfg: SendConfig,
                   rng: random.Random) -> None:
    """In one transaction: `sending -> send_unknown` with the error (one line, at most 300 characters, secrets
    removed) and event `send_unknown` with its kind and whether a proven miss may be retried. `at` is when the
    failed call returned: a Sent miss starts to count as proof only some minutes after it. The next send waits
    out a gap from `at` too, since the email may have gone out. LookupError when the message is not `sending`."""
    stamp = _stamp(at)
    kind = error.kind if isinstance(error, GmailError) else _kind_of(error)
    text = scrub(str(error) if isinstance(error, GmailError) else describe_error(error))
    with atomic(conn, "record_unclear"):
        row = conn.execute("SELECT state, pending_rfc_id, touch_id FROM message WHERE message_id = ?",
                           (message_id,)).fetchone()
        if row is None or row["state"] != "sending":
            raise LookupError(f"message {message_id} is not sending")
        conn.execute("UPDATE message SET state = 'send_unknown', error = ?, updated_at = ?, revision = revision + 1 "
                     "WHERE message_id = ? AND state = 'sending'", (text, stamp, message_id))
        append_event(conn, entity="message", entity_id=message_id, company_id=_company_of(conn, row["touch_id"]),
                     kind="send_unknown", actor="sender", at=stamp,
                     idempotency_key=f"send_unknown:{row['pending_rfc_id']}",
                     detail={"pending_rfc_id": row["pending_rfc_id"], "error": text, "error_kind": kind,
                             "retryable": kind in RETRYABLE})
        _push_gap(conn, stamp, cfg, rng)


def _company_of(conn: sqlite3.Connection, touch_id: int) -> int:
    row = conn.execute("SELECT p.company_id FROM touch t JOIN person p ON p.person_id = t.person_id "
                       "WHERE t.touch_id = ?", (touch_id,)).fetchone()
    return canonical_company(conn, row["company_id"])


def _pause_rewritten(conn: sqlite3.Connection, message_id: int, sent: Sent, claimed: str, *, at: str) -> None:
    """Gmail kept the email under another Message-ID: every later unclear send would look missing from Sent and
    could be sent twice, so sending pauses (`hard_block`, over his own pause too) until he looks."""
    sender = conn.execute("SELECT paused, pause_kind FROM sender WHERE sender_id = 1").fetchone()
    if not (sender["paused"] and sender["pause_kind"] == "hard_block"):
        conn.execute("UPDATE sender SET paused = 1, pause_kind = 'hard_block', paused_reason = ?, paused_at = ?, "
                     "updated_at = ?, revision = revision + 1 WHERE sender_id = 1", (REWRITTEN_REASON, at, at))
    append_event(conn, entity="sender", entity_id=1, kind="hard_block", actor="sender", at=at,
                 idempotency_key=f"hard_block:message_id_rewritten:{message_id}",
                 detail={"kind": "message_id_rewritten", "message_id": message_id, "claimed_rfc_id": claimed,
                         "stored_rfc_id": sent.rfc_message_id, "reason": REWRITTEN_REASON})


def send_one(conn: sqlite3.Connection, client: MailClient, q: Queued, cfg: SendConfig, *, now: str,
             clock: Callable[[], datetime], rng: random.Random, account: str) -> Literal["sent", "lost_race"]:
    """Claim `q` with a new Message-ID at `now`, send it with no transaction open, then record it.

    Returns `sent`, or `lost_race` when the claim changed nothing (nothing is sent then). When the call fails the
    outcome is unclear: the message is recorded `send_unknown` and a GmailError is raised so the tick can apply its
    kind (a raw timeout or network error becomes one). GmailError `message_id_rewritten` is the one failure that is
    not unclear: the email went out, so it is recorded `sent` under the id Gmail kept, sending pauses, and the error
    is raised for the tick to report. `clock` gives the moment the call returned (sent_at, and the start of the
    wait before a Sent miss counts), which can be long after `now` when the Mac slept mid-call. A failure while
    recording after Gmail accepted is not caught: the row stays `sending` and the next tick settles it by Sent."""
    rfc = new_message_id(account)
    if not claim(conn, q, rfc_id=rfc, now=now):
        return "lost_race"
    try:
        outgoing = outgoing_for(conn, q, cfg, rfc)
    except Exception as err:
        failure = GmailError("bad_request", None, f"the email could not be built: {describe_error(err)}")
        record_unclear(conn, q.message_id, failure, at=_stamp(clock()), cfg=cfg, rng=rng)
        raise failure from err
    try:
        sent = client.send(outgoing)
    except GmailError as err:
        returned = _stamp(clock())
        if err.kind == "message_id_rewritten":
            with transaction(conn):
                record_sent(conn, q.message_id, err.sent, at=returned, cfg=cfg, rng=rng)
                _pause_rewritten(conn, q.message_id, err.sent, rfc, at=returned)
            raise
        record_unclear(conn, q.message_id, err, at=returned, cfg=cfg, rng=rng)
        raise
    except Exception as err:
        failure = GmailError(_kind_of(err), None, describe_error(err))
        record_unclear(conn, q.message_id, failure, at=_stamp(clock()), cfg=cfg, rng=rng)
        raise failure from err
    record_sent(conn, q.message_id, sent, at=_stamp(clock()), cfg=cfg, rng=rng)
    return "sent"
