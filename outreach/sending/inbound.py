"""Reading replies, auto-replies and bounces, and what each one does to the store (#14 Task 10).

Two steps, so Gmail is never called inside a transaction:
1. `read_inbound` reads the store once (`snapshot`), then Gmail with no store access at all: new messages after
   the account's history cursor, or, with no usable cursor, every thread of its touches sent in the last 45 days
   (taking the profile's history id first, so nothing that arrives during the read is missed). An incomplete read
   keeps the old cursor.
2. `apply_inbound` classifies each message and writes it in one transaction.

A missed reply or bounce would send the next follow-up to someone who already answered, so: a read that fails is
an incomplete read (never "no replies"), a reply holds the whole surviving company and stops the replier's own
queued messages, and a bounce stops that touch's queued messages and puts its address on the dead-address list. A
Gmail limit notice is about his account, not the recipient: it pauses sending and never marks a touch bounced.

Per sender account (#9 multi-sender ruling, 2026-11-12): each account reads its own mailbox with its own history
cursor (`sender_account.history_id`). A Gmail thread id means something only in its own mailbox, so a message is
matched by thread only to that account's touches (a touch with no account is account 1's); a Message-ID is unique
across mailboxes, so the reply headers match any account's email. A reply on any account holds the company, which
stops queued email to it on every account. `inbound_seen` keys are the Gmail id for account 1 (as before accounts)
and `"{account_id}:{gmail id}"` for any other.
"""
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from outreach.drafts.followups import canonical_company
from outreach.sending import calendar as cal
from outreach.sending import accounts, signals
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

__all__ = ["Snapshot", "InboundRead", "tracked_threads", "tracked_subjects", "tracked_addresses",
           "tracked_message_ids", "match_by_reply_headers", "seen_key", "known_ids",
           "snapshot", "read_inbound", "read_mailbox", "apply_inbound", "observe_stop_tags", "verify_imported"]


# -- what the read needs from the store -------------------------------------------------------------------------

# A touch's account: NULL is account 1 (sent before touches carried one).
_ON_ACCOUNT = "COALESCE(t.account_id, 1) = ?"


def tracked_threads(conn: sqlite3.Connection, account_id: int) -> dict[str, int]:
    """Each Gmail thread id of the account's touches to its touch id. Thread ids are per mailbox: another account's
    thread of the same id is another conversation."""
    return {row[0]: row[1] for row in conn.execute(
        "SELECT t.gmail_thread_id, t.touch_id FROM touch t WHERE t.gmail_thread_id IS NOT NULL "
        f"AND t.gmail_thread_id <> '' AND {_ON_ACCOUNT}", (account_id,))}


def tracked_subjects(conn: sqlite3.Connection, account_id: int) -> dict[str, int]:
    """Each subject of a message the account sent to its touch id. A subject sent on two of its touches is left
    out: a bounce that echoes it could belong to either, and a wrong match would stop a live person's follow-up. A
    bounce comes back to the mailbox that sent, so another account's subjects are not looked at."""
    found: dict[str, int] = {}
    shared: set[str] = set()
    for subject, touch_id in conn.execute(
            "SELECT DISTINCT m.subject, m.touch_id FROM message m JOIN touch t ON t.touch_id = m.touch_id "
            f"WHERE m.state = 'sent' AND m.subject <> '' AND {_ON_ACCOUNT}", (account_id,)):
        if found.setdefault(subject, touch_id) != touch_id:
            shared.add(subject)
    return {s: t for s, t in found.items() if s not in shared}


def tracked_addresses(conn: sqlite3.Connection, account_id: int) -> dict[str, int]:
    """Each address the account emailed (lower case) to its touch id, for a bounce Gmail did not thread."""
    return {row[0].strip().lower(): row[1] for row in conn.execute(
        "SELECT p.email, t.touch_id FROM touch t JOIN person p ON p.person_id = t.person_id "
        f"WHERE t.first_sent_at IS NOT NULL AND p.email IS NOT NULL AND p.email <> '' AND {_ON_ACCOUNT}",
        (account_id,))}


def tracked_message_ids(conn: sqlite3.Connection) -> dict[str, int]:
    """Each Message-ID the sender (or the import) put on a sent email, to its touch id: every sent message's
    `rfc_message_id` and every touch's `first_rfc_id`. A reply Gmail filed outside the thread still names one."""
    found: dict[str, int] = {}
    for rfc, touch_id in conn.execute(
            "SELECT rfc_message_id, touch_id FROM message WHERE state = 'sent' AND rfc_message_id IS NOT NULL "
            "UNION ALL SELECT first_rfc_id, touch_id FROM touch WHERE first_rfc_id IS NOT NULL"):
        key = normalise_message_id(rfc)
        if key:
            found.setdefault(key, touch_id)
    return found


_REFERENCE = re.compile(r"<[^<>\s]+>")


def match_by_reply_headers(meta: MessageMeta, ids: Mapping[str, int]) -> int | None:
    """The touch a message answers, by its In-Reply-To, then its References (newest first), against our own
    Message-IDs; None when it names none of them."""
    for name in ("in-reply-to", "references"):
        refs = _REFERENCE.findall(meta.headers.get(name) or "")
        for ref in reversed(refs):
            touch_id = ids.get(normalise_message_id(ref))
            if touch_id is not None:
                return touch_id
    return None


def seen_key(account_id: int, gmail_message_id: str) -> str:
    """The `inbound_seen` key of a message in this account's mailbox: its Gmail id for account 1 (the keys stored
    before accounts), else `"{account_id}:{gmail id}"` (Gmail ids are per mailbox)."""
    return gmail_message_id if account_id == 1 else f"{account_id}:{gmail_message_id}"


def known_ids(conn: sqlite3.Connection, account_id: int) -> set[str]:
    """The Gmail ids of the account's mailbox already in the store: inbound messages read before (stored once
    whatever mailbox), its mail read and left alone (`inbound_seen`), and its own sent messages. None of them is
    read again."""
    if account_id == 1:
        seen = conn.execute("SELECT gmail_message_id FROM inbound_seen WHERE instr(gmail_message_id, ':') = 0")
    else:
        prefix = f"{account_id}:"
        seen = conn.execute("SELECT substr(gmail_message_id, ?) FROM inbound_seen WHERE substr(gmail_message_id, 1, ?) "
                            "= ?", (len(prefix) + 1, len(prefix), prefix))
    found = {row[0] for row in seen}
    found.update(row[0] for row in conn.execute("SELECT gmail_message_id FROM inbound_message"))
    found.update(row[0] for row in conn.execute(
        "SELECT m.gmail_message_id FROM message m JOIN touch t ON t.touch_id = m.touch_id "
        f"WHERE m.gmail_message_id IS NOT NULL AND m.gmail_message_id <> '' AND {_ON_ACCOUNT}", (account_id,)))
    return found


@dataclass(frozen=True)
class Snapshot:
    """What a read of one account's mailbox needs, read from the store before any Gmail call. `recent_threads` are
    the threads of its touches first sent within `rethread_days`, newest first: what a read with no usable cursor
    goes through."""
    account_id: int
    threads: Mapping[str, int]
    subjects: Mapping[str, int]
    known: frozenset[str]
    history_id: str
    recent_threads: tuple[str, ...]


def _cursor(conn: sqlite3.Connection, account_id: int) -> str:
    """The account's history cursor. Account 1 with none yet takes the one the single sender kept
    (`sender.history_id`), so the live cursor is not lost; `apply_inbound` copies it over once."""
    row = conn.execute("SELECT history_id FROM sender_account WHERE account_id = ?", (account_id,)).fetchone()
    cursor = row[0] if row is not None else ""
    if not cursor and account_id == 1:
        cursor = conn.execute("SELECT history_id FROM sender WHERE sender_id = 1").fetchone()[0]
    return cursor or ""


def snapshot(conn: sqlite3.Connection, account_id: int, *, now: datetime, cfg: SendConfig) -> Snapshot:
    """Read only."""
    since = (now.astimezone(timezone.utc) - timedelta(days=cfg.rethread_days)).strftime(ISO_FMT)
    recent = tuple(row[0] for row in conn.execute(
        "SELECT t.gmail_thread_id FROM touch t WHERE t.gmail_thread_id IS NOT NULL AND t.gmail_thread_id <> '' "
        f"AND t.first_sent_at >= ? AND {_ON_ACCOUNT} ORDER BY t.first_sent_at DESC, t.touch_id DESC",
        (since, account_id)))
    return Snapshot(account_id=account_id, threads=tracked_threads(conn, account_id),
                    subjects=tracked_subjects(conn, account_id), known=frozenset(known_ids(conn, account_id)),
                    history_id=_cursor(conn, account_id), recent_threads=recent)


# -- reading Gmail ----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class InboundRead:
    """The messages read and the cursor to move to: the page's for a complete read, the last finished history
    record's for a partial one, or None (stay put). `account_id` is the account whose mailbox was read (None only
    for a read a test builds by hand)."""
    metas: list[MessageMeta]
    new_history_id: str | None
    complete: bool
    account_id: int | None = None


def _gone(err: GmailError) -> bool:
    """A 404 for one message or thread: he deleted it. Skipping it is complete; any other error is not."""
    return err.status == 404


def _read_through(added: tuple, unread: set[str]) -> str | None:
    """The id of the last history record before the first one that holds an unread message: every message up to
    it was read (or did not need reading). None when that is no record, or a record id is unknown."""
    first = next(i for i, a in enumerate(added) if a.gmail_message_id in unread)
    stop = added[first].history_id
    before = [a.history_id for a in added[:first] if a.history_id != stop]
    if stop is None or not before or any(h is None for h in before):
        return None
    return before[-1]


def read_inbound(conn: sqlite3.Connection, client: MailClient, account: accounts.AccountRow, *, cfg: SendConfig,
                 now: datetime) -> InboundRead:
    """Read the account's new mail with its own client: its `snapshot` from the store first (read only), then
    Gmail with no store access (`read_mailbox`). Run it with no transaction open."""
    if conn.in_transaction:
        raise RuntimeError("read_inbound calls Gmail, so it runs with no transaction open")
    return read_mailbox(client, snapshot(conn, account.account_id, now=now, cfg=cfg), cfg=cfg, now=now)


def read_mailbox(client: MailClient, snap: Snapshot, *, cfg: SendConfig, now: datetime) -> InboundRead:
    """Read new mail from Gmail. No store access: run it with no transaction open.

    With a cursor: `history`, then `message` for each new id, the tracked threads' first and then INBOX mail in
    other threads (only to catch a bounce Gmail did not thread), at most `inbound_meta_cap` in all. With no cursor,
    or one Gmail calls expired: `profile` first (its history id becomes the cursor), then `thread` for each recent
    tracked thread. A failed call, or the cap, ends the read incomplete: what was read is kept and applied, and the
    cursor moves only to the last history record whose messages were all read (it stays put when that is none, or
    when the client gives no record ids), so the next tick reads the rest and every tick makes progress."""
    known = set(snap.known)
    metas: list[MessageMeta] = []
    if snap.history_id:
        try:
            page = client.history(snap.history_id)
        except HistoryExpired:
            page = None
        except GmailError:
            return InboundRead([], None, False, snap.account_id)
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
            done: set[str] = set()
            for gmail_id in wanted[:cfg.inbound_meta_cap]:
                try:
                    metas.append(client.message(gmail_id))
                except GmailError as err:
                    if not _gone(err):
                        break
                done.add(gmail_id)
            if len(done) == len(wanted):
                return InboundRead(metas, page.history_id, True, snap.account_id)
            return InboundRead(metas, _read_through(page.added, set(wanted) - done), False, snap.account_id)
    try:
        cursor = client.profile().history_id
    except GmailError:
        return InboundRead([], None, False, snap.account_id)
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
    return InboundRead(metas, (cursor or None) if complete else None, complete, snap.account_id)


# -- applying it ------------------------------------------------------------------------------------------------

def _stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime(ISO_FMT)


def _received(meta: MessageMeta) -> str:
    return datetime.fromtimestamp(meta.internal_ms / 1000, tz=timezone.utc).strftime(ISO_FMT)


# How long a message read and left alone is remembered: well past Gmail's history cursor (about a week).
SEEN_DAYS = 30


def _seen(conn: sqlite3.Connection, account_id: int, meta: MessageMeta, why: str, stamp: str) -> None:
    """Remember a message of this account's mailbox read and left alone, so no later tick reads it again."""
    conn.execute("INSERT INTO inbound_seen(gmail_message_id, thread_id, why, seen_at) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(gmail_message_id) DO NOTHING",
                 (seen_key(account_id, meta.gmail_message_id), meta.thread_id or "", why, stamp))


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


def _no_go_the_bounced(conn: sqlite3.Connection, touch_id: int, meta: MessageMeta, stamp: str) -> None:
    """A bounce puts the person on the no-go list for good (#224): the address does not take mail, so it is never
    emailed or drafted again. The address stays on the person row, so the same address pasted again merges into
    this person and meets the no-go instead of becoming a new one. Someone who already has a no-go row, lifted or
    not, gets none: a lifted row is his choice, and automation never re-adds it."""
    touch = conn.execute("SELECT t.person_id, p.company_id, p.email FROM touch t JOIN person p USING (person_id) "
                         "WHERE t.touch_id = ?", (touch_id,)).fetchone()
    if touch is None or conn.execute("SELECT 1 FROM no_go WHERE person_id = ?", (touch["person_id"],)).fetchone():
        return
    note = f"Email bounced: {touch['email'] or 'no address on record'}."
    no_go_id = conn.execute(
        "INSERT INTO no_go(person_id, reason, source, source_ref, note, created_at) "
        "VALUES (?, 'other', 'rules', ?, ?, ?)",
        (touch["person_id"], meta.gmail_message_id, note, stamp)).lastrowid
    append_event(conn, entity="no_go", entity_id=no_go_id, company_id=_company_of(conn, touch_id), kind="added",
                 actor="sender", at=stamp, idempotency_key=f"no_go_bounced:{touch['person_id']}",
                 detail={"reason": "other", "source": "rules", "personId": touch["person_id"], "touchId": touch_id,
                         "text": f"No-go: {note.lower().rstrip('.')}."})


def _signal_account(conn: sqlite3.Connection, cfg: SendConfig, touch_id: int | None) -> accounts.AccountRow:
    """The account a signal belongs to: the touch's own account (a touch with none is account 1's), else account 1
    (the first account block)."""
    if touch_id is not None:
        row = conn.execute("SELECT account_id FROM touch WHERE touch_id = ?", (touch_id,)).fetchone()
        found = accounts.by_id(conn, cfg, row["account_id"] if row is not None else None)
        if found is not None:
            return found
    return accounts.primary(conn, cfg)


def _list_the_bounced(conn: sqlite3.Connection, touch_id: int, meta: MessageMeta, received: str) -> None:
    """The touch's address goes on the dead-address list (`bounced_address`), for every account: it is never
    claimed, approved or added again. An address already listed keeps its first row."""
    row = conn.execute("SELECT p.email FROM touch t JOIN person p USING (person_id) WHERE t.touch_id = ?",
                       (touch_id,)).fetchone()
    address = (row["email"] or "").strip() if row is not None else ""
    if address:
        conn.execute("INSERT OR IGNORE INTO bounced_address(address, first_seen_at, source, source_ref) "
                     "VALUES (?, ?, 'bounce', ?)", (address, received, meta.gmail_message_id))


def _apply_bounce(conn: sqlite3.Connection, touch_id: int, meta: MessageMeta, found: Inbound, received: str,
                  stamp: str, *, now: datetime, cfg: SendConfig, notify: Notifier) -> None:
    detail = {"touch_id": touch_id, "gmail_message_id": meta.gmail_message_id, "dsn_class": found.dsn_class}
    if conn.execute("UPDATE touch SET state = 'bounced', bounced_at = ?, updated_at = ?, revision = revision + 1 "
                    "WHERE touch_id = ? AND state = 'open'", (received, stamp, touch_id)).rowcount:
        append_event(conn, entity="touch", entity_id=touch_id, company_id=_company_of(conn, touch_id), kind="bounced",
                     actor="sender", at=received, idempotency_key=f"bounced:{touch_id}:{meta.gmail_message_id}",
                     detail=detail)
    stop_touch_messages(conn, touch_id, reason="bounced", now=stamp)
    _list_the_bounced(conn, touch_id, meta, received)
    _no_go_the_bounced(conn, touch_id, meta, stamp)
    account = _signal_account(conn, cfg, touch_id)
    signal = {"at": received, "cfg": cfg, "notify": notify, "now": now, "account": account}
    if found.dsn_class == "policy":
        note_signal(conn, "policy_rejection", detail=detail, key=f"policy_rejection:{meta.gmail_message_id}",
                    **signal)
    day = cal.ny_date(signals.as_datetime(received), cfg)
    rate = signals.bounce_rate_3d(conn, account.account_id, day, cfg)
    if signals.bounce_rate_warns(rate, cfg):
        note_signal(conn, "bounce_warning", detail={**detail, "rate": rate},
                    key=signals.day_key("bounce_warning", account.account_id, day), **signal)
    bounced, sent = signals.bounce_rate_last_100(conn, account.account_id)
    if signals.bounce_rate_hard(bounced, sent):
        note_signal(conn, "bounce_rate_hard", detail={**detail, "bounced": bounced, "sent": sent},
                    key=signals.day_key("bounce_rate_hard", account.account_id, day), **signal)


def _limit_notice(conn: sqlite3.Connection, touch_id: int | None, meta: MessageMeta, received: str, *,
                  account: accounts.AccountRow, now: datetime, cfg: SendConfig, notify: Notifier) -> None:
    """Gmail refused to send for the limit of the account whose mailbox holds the notice: a hard block on that
    account, whatever thread the notice is in."""
    note_signal(conn, "gmail_limit", at=received, key=f"gmail_limit:dsn:{meta.gmail_message_id}", cfg=cfg,
                notify=notify, now=now, account=account,
                detail={"touch_id": touch_id, "gmail_message_id": meta.gmail_message_id, "source": "dsn"})


def apply_inbound(conn: sqlite3.Connection, read: InboundRead, account: accounts.AccountRow, *, now: datetime,
                  cfg: SendConfig, notify: Notifier) -> dict[str, int]:
    """Classify and apply what `read_inbound` read from `account`'s mailbox, in one transaction (a savepoint inside
    the caller's). A read of another account's mailbox is refused (ValueError) before anything is written.

    His own messages, delay notices and mail outside the tracked threads write nothing, except a reply whose
    In-Reply-To or References names one of our Message-IDs (Gmail split the thread) and a bounce matched by those
    headers, its failed recipient or a sent subject: those count as if they were in the thread. Everything else
    writes one `inbound_message` row, once per Gmail message, and then:
    - a person's reply: the touch `replied` (from `open`, `closed` or `bounced`) with `replied_at` and kind
      `unsorted` when unset; its outreach touch's first `replied` outcome (actor `sender`, at its current revision);
      the surviving company held when no company in its family is held yet; the touch's unsent messages stopped
      (reason `replied`). A second reply finds all of that done and writes only its row.
    - an automatic reply: `touch.auto_reply_at` when unset. It holds nothing.
    - a bounce: an `open` touch becomes `bounced`; its unsent messages are stopped (reason `bounced`); its address
      goes on `bounced_address` (source `bounce`, the bounce's Gmail id) and the person on the no-go list (#224);
      then the policy, 3-day bounce rate and hard bounce rate signals. A Gmail limit notice instead raises
      `gmail_limit` and leaves the touch alone, even when it matches no touch.
    Matching by thread, failed recipient and sent subject uses only the account's own touches; the reply headers
    match any account's email. A message that writes nothing is remembered in `inbound_seen` under the account's
    key (forgotten after `SEEN_DAYS`), so it is never read again. The account's cursor moves to
    `read.new_history_id` (a partial read's is the last record it finished). Returns counts."""
    if read.account_id is not None and read.account_id != account.account_id:
        raise ValueError(f"a read of account {read.account_id}'s mailbox cannot be applied as account "
                         f"{account.account_id}'s")
    account_id = account.account_id
    stamp = _stamp(now)
    counts = {"replies": 0, "auto_replies": 0, "bounces": 0, "limit_notices": 0, "ignored": 0}
    with atomic(conn, "apply_inbound"):
        if account_id == 1:
            # Once: the cursor the single sender kept becomes account 1's own, even when this read moves nothing.
            conn.execute("UPDATE sender_account SET history_id = (SELECT history_id FROM sender WHERE sender_id = 1) "
                         "WHERE account_id = 1 AND history_id = '' "
                         "AND (SELECT history_id FROM sender WHERE sender_id = 1) <> ''")
        threads = tracked_threads(conn, account_id)
        unthreaded: tuple[dict, dict] | None = None
        our_ids: dict[str, int] | None = None
        for meta in read.metas:
            found = classify(meta, account.cfg.address)
            touch_id = threads.get(meta.thread_id)
            if touch_id is None and found.kind in ("human", "auto_reply", "bounce"):
                # Gmail split the thread (a new subject, or a client that dropped it): the reply headers still
                # name our email.
                if our_ids is None:
                    our_ids = tracked_message_ids(conn)
                touch_id = match_by_reply_headers(meta, our_ids)
            if touch_id is None and found.kind == "bounce":
                if unthreaded is None:
                    unthreaded = (tracked_addresses(conn, account_id), tracked_subjects(conn, account_id))
                touch_id = match_unthreaded(meta, *unthreaded)
            if found.kind == "bounce" and found.dsn_class == "limit":
                if touch_id is not None:
                    _insert_inbound(conn, touch_id, meta, found, _received(meta), stamp)
                else:
                    _seen(conn, account_id, meta, "untracked", stamp)
                _limit_notice(conn, touch_id, meta, _received(meta), account=account, now=now, cfg=cfg,
                              notify=notify)
                counts["limit_notices"] += 1
                continue
            if touch_id is None or found.kind not in ("human", "auto_reply", "bounce"):
                _seen(conn, account_id, meta, found.kind if found.kind in ("ours", "delay") else "untracked", stamp)
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
        if read.new_history_id:  # a complete read's cursor, or the last record a partial read finished
            conn.execute("UPDATE sender_account SET history_id = ? WHERE account_id = ?",
                         (read.new_history_id, account_id))
        conn.execute("DELETE FROM inbound_seen WHERE seen_at < ?",
                     (_stamp(now - timedelta(days=SEEN_DAYS)),))
    return counts


# -- stop tags --------------------------------------------------------------------------------------------------

def observe_stop_tags(conn: sqlite3.Connection, *, now: datetime, cfg: SendConfig, notify: Notifier) -> int:
    """A warning (`stop_reply`, keyed by touch) for each email touch tagged `asked_to_stop` on the touch or on its
    outreach touch (not an undone one) that has none yet; then, for each account that got a new one, a hard block
    (`stop_replies_hard`) on that account once `stop_replies_hard` of its own tags fall within `stop_replies_days`:
    stop replies to one account never pause another. Runs in the caller's transaction (a savepoint). Returns how many
    tags were new."""
    stamp = _stamp(now)
    new = 0
    last: dict[int, tuple[int, accounts.AccountRow]] = {}  # account id to its newest new tag and the account
    with atomic(conn, "observe_stop_tags"):
        for (touch_id,) in conn.execute(
                "SELECT touch_id FROM touch WHERE reply_kind = 'asked_to_stop' UNION "
                "SELECT email_touch_id FROM outreach_touch WHERE email_touch_id IS NOT NULL "
                "AND reply_kind = 'asked_to_stop' AND undone_at IS NULL ORDER BY 1").fetchall():
            key = f"stop_reply:{touch_id}"
            if conn.execute("SELECT 1 FROM event WHERE idempotency_key = ?", (key,)).fetchone():
                continue
            account = _signal_account(conn, cfg, touch_id)
            note_signal(conn, "stop_reply", at=stamp, detail={"touch_id": touch_id}, key=key, cfg=cfg, notify=notify,
                        now=now, account=account)
            new += 1
            last[account.account_id] = (touch_id, account)
        for account_id, (touch_id, account) in sorted(last.items()):
            tags = signals.stop_tags_in(conn, now, cfg.stop_replies_days, account_id)
            if tags >= cfg.stop_replies_hard:
                note_signal(conn, "stop_replies_hard", at=stamp, detail={"tags": tags, "touch_id": touch_id},
                            key=f"stop_replies_hard:{touch_id}", cfg=cfg, notify=notify, now=now, account=account)
    return new


# -- imported history -------------------------------------------------------------------------------------------

_VERIFY = ("import_verified", "import_not_in_sent")


def verify_imported(conn: sqlite3.Connection, client: MailClient, *, now: datetime, limit: int = 20) -> dict[str, int]:
    """Check up to `limit` imported sent messages against Gmail by their Gmail id (#17), Gmail reads first and then
    one transaction. A message in Sent with the same Message-ID gets event `import_verified`; a 404, a message not
    in Sent or another Message-ID gets `import_not_in_sent`. A message is never changed. A placeholder Message-ID
    (`@outreach.invalid`) is never looked up. Any other Gmail error stops the round and is tried again next tick.
    It checks imported history only: it proves nothing about the sender's marker (`proof`). Returns counts."""
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
