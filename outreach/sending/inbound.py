"""Reading replies, auto-replies and bounces, and what each one does to the store (#14 Task 10).

Two steps, so Gmail is never called inside a transaction:
1. `read_inbound` reads Gmail with no store access at all: new messages after the history cursor, or, with no
   usable cursor, every thread of a touch sent in the last 45 days (taking the profile's history id first, so
   nothing that arrives during the read is missed). An incomplete read keeps the old cursor.
2. `apply_inbound` classifies each message and writes it in one transaction.

A missed reply or bounce would send the next follow-up to someone who already answered, so: a read that fails is
an incomplete read (never "no replies"), a reply holds the whole surviving company and stops the replier's own
queued messages, and a bounce stops that touch's queued messages. A Gmail limit notice is about his account, not
the recipient: it pauses sending and never marks a touch bounced.
"""
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from outreach.drafts.followups import canonical_company
from outreach.sending import calendar as cal
from outreach.sending import signals
from outreach.sending.claim import PLACEHOLDER_DOMAIN
from outreach.sending.classify import Inbound, classify, match_unthreaded
from outreach.sending.config import SendConfig
from outreach.sending.followups import stop_touch_messages
from outreach.sending.mail import GmailError, HistoryExpired, MailClient, MessageMeta, normalise_message_id
from outreach.sending.notify import Notifier
from outreach.sending.ramp import note_signal
from outreach.store.atomic import atomic
from outreach.store.timefmt import ISO_FMT
from outreach.store.writes import append_event, record_outcome

__all__ = ["Snapshot", "InboundRead", "tracked_threads", "tracked_subjects", "tracked_addresses", "known_ids",
           "snapshot", "read_inbound", "apply_inbound", "observe_stop_tags", "verify_imported"]


# -- what the read needs from the store -------------------------------------------------------------------------

def tracked_threads(conn: sqlite3.Connection) -> dict[str, int]:
    """Every touch's Gmail thread id to its touch id."""
    return {row[0]: row[1] for row in conn.execute(
        "SELECT gmail_thread_id, touch_id FROM touch WHERE gmail_thread_id IS NOT NULL AND gmail_thread_id <> ''")}


def tracked_subjects(conn: sqlite3.Connection) -> dict[str, int]:
    """Each subject of a sent message to its touch id. A subject sent on two touches is left out: a bounce that
    echoes it could belong to either, and a wrong match would stop a live person's follow-up."""
    found: dict[str, int] = {}
    shared: set[str] = set()
    for subject, touch_id in conn.execute(
            "SELECT DISTINCT subject, touch_id FROM message WHERE state = 'sent' AND subject <> ''"):
        if found.setdefault(subject, touch_id) != touch_id:
            shared.add(subject)
    return {s: t for s, t in found.items() if s not in shared}


def tracked_addresses(conn: sqlite3.Connection) -> dict[str, int]:
    """Each emailed person's address (lower case) to their touch id, for a bounce Gmail did not thread."""
    return {row[0].strip().lower(): row[1] for row in conn.execute(
        "SELECT p.email, t.touch_id FROM touch t JOIN person p ON p.person_id = t.person_id "
        "WHERE t.first_sent_at IS NOT NULL AND p.email IS NOT NULL AND p.email <> ''")}


def known_ids(conn: sqlite3.Connection) -> set[str]:
    """The Gmail ids already in the store: inbound messages read before and the sender's own sent messages."""
    return {row[0] for row in conn.execute(
        "SELECT gmail_message_id FROM inbound_message UNION "
        "SELECT gmail_message_id FROM message WHERE gmail_message_id IS NOT NULL AND gmail_message_id <> ''")}


@dataclass(frozen=True)
class Snapshot:
    """What `read_inbound` needs, read from the store before any Gmail call. `recent_threads` are the threads of
    touches first sent within `rethread_days`, newest first: what a read with no usable cursor goes through."""
    threads: Mapping[str, int]
    subjects: Mapping[str, int]
    known: frozenset[str]
    history_id: str
    recent_threads: tuple[str, ...]


def snapshot(conn: sqlite3.Connection, *, now: datetime, cfg: SendConfig) -> Snapshot:
    """Read only."""
    since = (now.astimezone(timezone.utc) - timedelta(days=cfg.rethread_days)).strftime(ISO_FMT)
    recent = tuple(row[0] for row in conn.execute(
        "SELECT gmail_thread_id FROM touch WHERE gmail_thread_id IS NOT NULL AND gmail_thread_id <> '' "
        "AND first_sent_at >= ? ORDER BY first_sent_at DESC, touch_id DESC", (since,)))
    cursor = conn.execute("SELECT history_id FROM sender WHERE sender_id = 1").fetchone()[0]
    return Snapshot(threads=tracked_threads(conn), subjects=tracked_subjects(conn),
                    known=frozenset(known_ids(conn)), history_id=cursor or "", recent_threads=recent)


# -- reading Gmail ----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class InboundRead:
    """The messages read and the cursor to move to. `new_history_id` is None unless the read was complete."""
    metas: list[MessageMeta]
    new_history_id: str | None
    complete: bool


def _gone(err: GmailError) -> bool:
    """A 404 for one message or thread: he deleted it. Skipping it is complete; any other error is not."""
    return err.status == 404


def read_inbound(client: MailClient, snap: Snapshot, *, cfg: SendConfig, now: datetime) -> InboundRead:
    """Read new mail from Gmail. No store access: run it with no transaction open.

    With a cursor: `history`, then `message` for each new id, the tracked threads' first and then INBOX mail in
    other threads (only to catch a bounce Gmail did not thread), at most `inbound_meta_cap` in all. With no cursor,
    or one Gmail calls expired: `profile` first (its history id becomes the cursor), then `thread` for each recent
    tracked thread. A failed call, or the cap, ends the read incomplete: what was read is kept and applied, and the
    cursor stays where it was so the next tick reads the rest."""
    known = set(snap.known)
    metas: list[MessageMeta] = []
    if snap.history_id:
        try:
            page = client.history(snap.history_id)
        except HistoryExpired:
            page = None
        except GmailError:
            return InboundRead([], None, False)
        if page is not None:
            tracked, others = [], []
            for added in page.added:
                if added.gmail_message_id in known:
                    continue
                known.add(added.gmail_message_id)
                if added.thread_id in snap.threads:
                    tracked.append(added.gmail_message_id)
                elif "INBOX" in added.labels:
                    others.append(added.gmail_message_id)
            wanted = tracked + others
            complete = len(wanted) <= cfg.inbound_meta_cap
            for gmail_id in wanted[:cfg.inbound_meta_cap]:
                try:
                    metas.append(client.message(gmail_id))
                except GmailError as err:
                    if _gone(err):
                        continue
                    complete = False
                    break
            return InboundRead(metas, page.history_id if complete else None, complete)
    try:
        cursor = client.profile().history_id
    except GmailError:
        return InboundRead([], None, False)
    complete = True
    for thread_id in snap.recent_threads:
        try:
            thread = client.thread(thread_id)
        except GmailError as err:
            if _gone(err):
                continue
            complete = False
            break
        for meta in thread:
            if meta.gmail_message_id not in known:
                known.add(meta.gmail_message_id)
                metas.append(meta)
    return InboundRead(metas, (cursor or None) if complete else None, complete)


# -- applying it ------------------------------------------------------------------------------------------------

def _stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime(ISO_FMT)


def _received(meta: MessageMeta) -> str:
    return datetime.fromtimestamp(meta.internal_ms / 1000, tz=timezone.utc).strftime(ISO_FMT)


def _insert_inbound(conn: sqlite3.Connection, touch_id: int, meta: MessageMeta, found: Inbound, received: str,
                    stamp: str) -> bool:
    """One `inbound_message` row; False when this Gmail message was stored before (then nothing else happens)."""
    return conn.execute(
        "INSERT INTO inbound_message(touch_id, gmail_message_id, from_addr, subject, body_text, kind, received_at, "
        "recorded_at, dsn_class) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(gmail_message_id) DO NOTHING",
        (touch_id, meta.gmail_message_id, meta.from_addr or "", meta.subject or "", meta.snippet or "", found.kind,
         received, stamp, found.dsn_class)).rowcount == 1


def _company_of(conn: sqlite3.Connection, touch_id: int) -> int:
    row = conn.execute("SELECT p.company_id FROM touch t JOIN person p ON p.person_id = t.person_id "
                       "WHERE t.touch_id = ?", (touch_id,)).fetchone()
    return canonical_company(conn, row[0])


def _family_holds(conn: sqlite3.Connection, survivor: int) -> bool:
    """Whether any company that resolves to `survivor` already has a hold by an email touch."""
    rows = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT company_id, merged_into, held_by_touch FROM company")}
    for cid, (_, held_by) in rows.items():
        if held_by is None:
            continue
        current, seen = cid, {cid}
        while rows[current][0] is not None and rows[current][0] not in seen:
            current = rows[current][0]
            seen.add(current)
        if current == survivor:
            return True
    return False


def _apply_reply(conn: sqlite3.Connection, touch_id: int, meta: MessageMeta, received: str, stamp: str) -> None:
    touch = conn.execute("SELECT state, reply_kind FROM touch WHERE touch_id = ?", (touch_id,)).fetchone()
    survivor = _company_of(conn, touch_id)
    if touch["state"] in ("open", "closed", "bounced"):
        kind = touch["reply_kind"] if touch["reply_kind"] not in (None, "auto_reply") else "unsorted"
        conn.execute("UPDATE touch SET state = 'replied', replied_at = coalesce(replied_at, ?), reply_kind = ?, "
                     "updated_at = ?, revision = revision + 1 WHERE touch_id = ?", (received, kind, stamp, touch_id))
        append_event(conn, entity="touch", entity_id=touch_id, company_id=survivor, kind="replied", actor="sender",
                     at=received, idempotency_key=f"replied:{touch_id}:{meta.gmail_message_id}",
                     detail={"gmail_message_id": meta.gmail_message_id, "was": touch["state"]})
    outcome = conn.execute("SELECT touch_id, revision, sent_at, replied_at, undone_at FROM outreach_touch "
                           "WHERE email_touch_id = ?", (touch_id,)).fetchone()
    if outcome is not None and outcome["replied_at"] is None and outcome["undone_at"] is None:
        # Gmail's clock and the Mac's differ: a reply is never recorded as before the email it answers.
        record_outcome(conn, outcome["touch_id"], kind="replied", at=max(received, outcome["sent_at"]),
                       revision=outcome["revision"], actor="sender")
    if not _family_holds(conn, survivor):
        conn.execute("UPDATE company SET held_by_touch = ?, held_at = ?, revision = revision + 1 "
                     "WHERE company_id = ?", (touch_id, stamp, survivor))
        append_event(conn, entity="company", entity_id=survivor, company_id=survivor, kind="held", actor="sender",
                     at=stamp, idempotency_key=f"held:{survivor}:{touch_id}:{meta.gmail_message_id}",
                     detail={"touch_id": touch_id, "gmail_message_id": meta.gmail_message_id})
    stop_touch_messages(conn, touch_id, reason="replied", now=stamp)


def _apply_auto_reply(conn: sqlite3.Connection, touch_id: int, meta: MessageMeta, received: str, stamp: str) -> None:
    if conn.execute("UPDATE touch SET auto_reply_at = ?, updated_at = ?, revision = revision + 1 "
                    "WHERE touch_id = ? AND auto_reply_at IS NULL", (received, stamp, touch_id)).rowcount:
        append_event(conn, entity="touch", entity_id=touch_id, company_id=_company_of(conn, touch_id),
                     kind="auto_reply", actor="sender", at=received,
                     idempotency_key=f"auto_reply:{touch_id}:{meta.gmail_message_id}",
                     detail={"gmail_message_id": meta.gmail_message_id})


def _apply_bounce(conn: sqlite3.Connection, touch_id: int, meta: MessageMeta, found: Inbound, received: str,
                  stamp: str, *, now: datetime, cfg: SendConfig, notify: Notifier) -> None:
    detail = {"touch_id": touch_id, "gmail_message_id": meta.gmail_message_id, "dsn_class": found.dsn_class}
    if conn.execute("UPDATE touch SET state = 'bounced', bounced_at = ?, updated_at = ?, revision = revision + 1 "
                    "WHERE touch_id = ? AND state = 'open'", (received, stamp, touch_id)).rowcount:
        append_event(conn, entity="touch", entity_id=touch_id, company_id=_company_of(conn, touch_id), kind="bounced",
                     actor="sender", at=received, idempotency_key=f"bounced:{touch_id}:{meta.gmail_message_id}",
                     detail=detail)
    stop_touch_messages(conn, touch_id, reason="bounced", now=stamp)
    signal = {"at": received, "cfg": cfg, "notify": notify, "now": now}
    if found.dsn_class == "policy":
        note_signal(conn, "policy_rejection", detail=detail, key=f"policy_rejection:{meta.gmail_message_id}",
                    **signal)
    day = cal.ny_date(signals.as_datetime(received), cfg)
    rate = signals.bounce_rate_3d(conn, day, cfg)
    if signals.bounce_rate_warns(rate, cfg):
        note_signal(conn, "bounce_warning", detail={**detail, "rate": rate}, key=f"bounce_warning:{day.isoformat()}",
                    **signal)
    if signals.hard_bounce_tripped(conn, cfg):
        hard_rate, count = signals.hard_bounce_rate(conn, cfg)
        # Keyed by how many touches have bounced, so resuming after this pause never pauses again on the same ones.
        bounced = conn.execute("SELECT COUNT(*) FROM touch WHERE bounced_at IS NOT NULL").fetchone()[0]
        note_signal(conn, "hard_bounce_rate", detail={**detail, "rate": hard_rate, "first_emails": count},
                    key=f"hard_bounce_rate:{bounced}", **signal)


def _limit_notice(conn: sqlite3.Connection, touch_id: int | None, meta: MessageMeta, received: str, *,
                  now: datetime, cfg: SendConfig, notify: Notifier) -> None:
    """Gmail refused to send for his account's limit: a hard block, whatever thread the notice is in."""
    note_signal(conn, "gmail_limit", at=received, key=f"gmail_limit:dsn:{meta.gmail_message_id}", cfg=cfg,
                notify=notify, now=now,
                detail={"touch_id": touch_id, "gmail_message_id": meta.gmail_message_id, "source": "dsn"})


def apply_inbound(conn: sqlite3.Connection, read: InboundRead, *, account: str, now: datetime, cfg: SendConfig,
                  notify: Notifier) -> dict[str, int]:
    """Classify and apply what `read_inbound` read, in one transaction (a savepoint inside the caller's).

    His own messages, delay notices and mail outside the tracked threads (but for a bounce matched by its failed
    recipient or a sent subject) write nothing. Everything else writes one `inbound_message` row, once per Gmail
    message, and then:
    - a person's reply: the touch `replied` (from `open`, `closed` or `bounced`) with `replied_at` and kind
      `unsorted` when unset; its outreach touch's first `replied` outcome (actor `sender`, at its current revision);
      the surviving company held when no company in its family is held yet; the touch's unsent messages stopped
      (reason `replied`). A second reply finds all of that done and writes only its row.
    - an automatic reply: `touch.auto_reply_at` when unset. It holds nothing.
    - a bounce: an `open` touch becomes `bounced`; its unsent messages are stopped (reason `bounced`); then the
      policy, 3-day bounce rate and hard bounce rate signals. A Gmail limit notice instead raises `gmail_limit` and
      leaves the touch alone, even when it matches no touch.
    The cursor moves to `read.new_history_id` only when the read was complete. Returns counts."""
    stamp = _stamp(now)
    counts = {"replies": 0, "auto_replies": 0, "bounces": 0, "limit_notices": 0, "ignored": 0}
    with atomic(conn, "apply_inbound"):
        threads = tracked_threads(conn)
        unthreaded: tuple[dict, dict] | None = None
        for meta in read.metas:
            found = classify(meta, account)
            touch_id = threads.get(meta.thread_id)
            if touch_id is None and found.kind == "bounce":
                if unthreaded is None:
                    unthreaded = (tracked_addresses(conn), tracked_subjects(conn))
                touch_id = match_unthreaded(meta, *unthreaded)
            if found.kind == "bounce" and found.dsn_class == "limit":
                if touch_id is not None:
                    _insert_inbound(conn, touch_id, meta, found, _received(meta), stamp)
                _limit_notice(conn, touch_id, meta, _received(meta), now=now, cfg=cfg, notify=notify)
                counts["limit_notices"] += 1
                continue
            if touch_id is None or found.kind not in ("human", "auto_reply", "bounce"):
                counts["ignored"] += 1
                continue
            received = _received(meta)
            if not _insert_inbound(conn, touch_id, meta, found, received, stamp):
                continue
            if found.kind == "human":
                _apply_reply(conn, touch_id, meta, received, stamp)
                counts["replies"] += 1
            elif found.kind == "auto_reply":
                _apply_auto_reply(conn, touch_id, meta, received, stamp)
                counts["auto_replies"] += 1
            else:
                _apply_bounce(conn, touch_id, meta, found, received, stamp, now=now, cfg=cfg, notify=notify)
                counts["bounces"] += 1
        if read.complete and read.new_history_id:
            conn.execute("UPDATE sender SET history_id = ? WHERE sender_id = 1", (read.new_history_id,))
    return counts


# -- stop tags --------------------------------------------------------------------------------------------------

def observe_stop_tags(conn: sqlite3.Connection, *, now: datetime, cfg: SendConfig, notify: Notifier) -> int:
    """A warning (`stop_reply`, keyed by touch) for each email touch tagged `asked_to_stop` on the touch or on its
    outreach touch (not an undone one) that has none yet; then, when this call found a new one, a hard block
    (`stop_replies_hard`) once `stop_replies_hard` tags fall within `stop_replies_days`. Runs in the caller's
    transaction (a savepoint). Returns how many tags were new."""
    stamp = _stamp(now)
    new, last = 0, None
    with atomic(conn, "observe_stop_tags"):
        for (touch_id,) in conn.execute(
                "SELECT touch_id FROM touch WHERE reply_kind = 'asked_to_stop' UNION "
                "SELECT email_touch_id FROM outreach_touch WHERE email_touch_id IS NOT NULL "
                "AND reply_kind = 'asked_to_stop' AND undone_at IS NULL ORDER BY 1").fetchall():
            key = f"stop_reply:{touch_id}"
            if conn.execute("SELECT 1 FROM event WHERE idempotency_key = ?", (key,)).fetchone():
                continue
            note_signal(conn, "stop_reply", at=stamp, detail={"touch_id": touch_id}, key=key, cfg=cfg, notify=notify,
                        now=now)
            new, last = new + 1, touch_id
        if new:
            tags = signals.stop_tags_in(conn, now, cfg.stop_replies_days)
            if tags >= cfg.stop_replies_hard:
                note_signal(conn, "stop_replies_hard", at=stamp, detail={"tags": tags, "touch_id": last},
                            key=f"stop_replies_hard:{last}", cfg=cfg, notify=notify, now=now)
    return new


# -- imported history -------------------------------------------------------------------------------------------

_VERIFY = ("import_verified", "import_not_in_sent")


def verify_imported(conn: sqlite3.Connection, client: MailClient, *, now: datetime, limit: int = 20) -> dict[str, int]:
    """Check up to `limit` imported sent messages against Gmail by their Gmail id (#17), Gmail reads first and then
    one transaction. A message in Sent with the same Message-ID gets event `import_verified`; a 404, a message not
    in Sent or another Message-ID gets `import_not_in_sent`. A message is never changed. A placeholder Message-ID
    (`@outreach.invalid`) is never looked up. Any other Gmail error stops the round and is tried again next tick.
    This is also the check that Gmail keeps the Message-ID a sender sets. Returns counts."""
    if conn.in_transaction:
        raise RuntimeError("verify_imported calls Gmail, so it runs with no transaction open")
    rows = conn.execute(
        "SELECT m.message_id, m.gmail_message_id, m.rfc_message_id, m.touch_id FROM message m "
        "JOIN touch t ON t.touch_id = m.touch_id "
        "WHERE t.origin = 'import' AND m.state = 'sent' AND ifnull(m.gmail_message_id, '') <> '' "
        "AND lower(m.rfc_message_id) NOT LIKE ? AND NOT EXISTS (SELECT 1 FROM event e WHERE e.entity = 'message' "
        "AND e.entity_id = m.message_id AND e.kind IN (?, ?)) ORDER BY m.message_id LIMIT ?",
        (f"%{PLACEHOLDER_DOMAIN}", *_VERIFY, limit)).fetchall()
    counts = {"verified": 0, "not_in_sent": 0, "errors": 0}
    results = []
    for row in rows:
        detail = {"gmail_message_id": row["gmail_message_id"], "rfc_message_id": row["rfc_message_id"]}
        try:
            meta = client.message(row["gmail_message_id"])
        except GmailError as err:
            if not _gone(err):
                counts["errors"] += 1
                break
            results.append((row, "import_not_in_sent", {**detail, "reason": "not_found"}))
            continue
        stored = normalise_message_id(meta.headers.get("message-id"))
        if "SENT" in meta.labels and stored == normalise_message_id(row["rfc_message_id"]):
            results.append((row, "import_verified", detail))
        else:
            reason = "not_in_sent" if "SENT" not in meta.labels else "other_message_id"
            results.append((row, "import_not_in_sent", {**detail, "reason": reason, "found_rfc_id": stored}))
    stamp = _stamp(now)
    with atomic(conn, "verify_imported"):
        for row, kind, detail in results:
            append_event(conn, entity="message", entity_id=row["message_id"],
                         company_id=_company_of(conn, row["touch_id"]), kind=kind, actor="sender", at=stamp,
                         idempotency_key=f"{kind}:{row['message_id']}", detail=detail)
            counts["verified" if kind == "import_verified" else "not_in_sent"] += 1
    return counts
