"""The Mail.app client: the Northeastern account (an Exchange account in macOS Mail.app) as a sender (Phase B, #255).

`MailAppClient` keeps the sender's `MailClient` contract through AppleScript, with the substitutions in the spec's
"Mail.app transport" table (docs/superpowers/specs/2026-11-12-multi-sender-design.md):

- Ids. Mail.app gives a message's Message-ID without angle brackets; the client always says it with them (`<id>`),
  as Gmail's headers do. A message's id for the sender (`gmail_message_id`) is `mailapp:<id>`. A thread is named by
  the Message-ID of its first email: a message's thread is the first id in its References, else its In-Reply-To,
  else its own id. So a reply to Knock's first email, and a Microsoft bounce notice naming it, both land in the
  thread of that email (`touch.gmail_thread_id`).
- Send. A first email is `make new outgoing message`; a follow-up is `reply` to its first email in Sent Items (the
  spike: Mail.app sets In-Reply-To, and the body is only the new text). Then Sent Items is polled for it, all within
  60 seconds of the start; not found in time is a send in doubt (`GmailError("timeout")`), never a resend.
- A send in doubt. Mail.app cannot put the sender's `X-Outreach-Id` marker on an email, so it is found by the Sent
  Items rule instead: the one message in Sent Items to that address with that exact subject, sent at or after the
  claim (subjects are unique per person). A copy still in the Outbox makes the search incomplete; two matches raise
  `SentAmbiguous` (the send stays in doubt and he is told). A hit carries the marker it was searched for.
- New mail. `history` reads every mailbox of the account but Sent Items, Drafts and Outbox, for messages received
  after the cursor less 24 hours, and returns all `message` needs, so the page costs one script. The cursor is a
  time (UTC, ISO), not a Gmail history id. There are no threads to read (`thread` raises ThreadUnsupported): with no
  cursor the inbound read takes the last 45 days with `history`.

AppleScript text lives here as constants, each starting with `COMMON_HANDLERS` (the one escape handler every script
prints its fields through, and the check that Mail.app is running: a script never starts it). Every value reaches a
script as an argument (`on run argv`), never in its text, so no subject or address can change what a script does.
Every loop walks a list already read into a variable (`set found to (messages of mb whose ...)`, then `repeat with
h in found`): walking the whose expression itself re-runs the filter for each property read (the real Mail.app took
over 120 s for 17 messages that way, about 3 s read first). Dates come back as Mail.app's local wall-clock time
(`«class isot»`) and are read here in the Mac's own zone.

Live checkout only: the client on the real runner (`osascript_runner`) refuses to be built anywhere else. Tests use
`outreach.sending.fakemailapp.FakeMailApp`, this client with an in-memory Mail.app as its runner.
"""
import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email.parser import HeaderParser
from email.utils import parseaddr

import live_checkout
from outreach.sending.applescript import Runner, osascript_runner, parse_records
from outreach.sending.config import AccountConfig
from outreach.sending.mail import (CLOCK_SKEW_MS, META_HEADERS, Added, GmailError, HistoryExpired, HistoryPage,
                                   MailClient, MessageMeta, Outgoing, Profile, Sent, SentAmbiguous, ThreadUnsupported,
                                   check_outgoing, miss_is_proof, normalise_message_id, settle_minutes)
from outreach.store.timefmt import ISO_FMT

__all__ = ["MailAppClient", "COMMON_HANDLERS", "PROFILE_SCRIPT", "SEND_SCRIPT", "REPLY_SCRIPT", "FIND_SCRIPT",
           "HISTORY_SCRIPT", "MESSAGE_SCRIPT", "ID_PREFIX", "SENT_BOX", "DRAFTS_BOX", "OUTBOX_BOX", "JUNK_BOX",
           "SKIPPED_BOXES", "SEND_TIMEOUT", "READ_TIMEOUT", "POLL_SECONDS", "HISTORY_OVERLAP", "SNIPPET_LENGTH",
           "bracket", "bare", "local_text", "local_parts", "read_local"]

ID_PREFIX = "mailapp:"
# The Exchange account's own mailboxes (the spike, 2026-11-15).
SENT_BOX = "Sent Items"
DRAFTS_BOX = "Drafts"
OUTBOX_BOX = "Outbox"
JUNK_BOX = "Junk Email"
SKIPPED_BOXES = (SENT_BOX, DRAFTS_BOX, OUTBOX_BOX)  # never read for new mail

SEND_TIMEOUT = 60       # seconds: a send, and the Sent Items poll after it, all within this
READ_TIMEOUT = 120      # seconds: any read
POLL_SECONDS = 2        # between Sent Items reads after a send
HISTORY_OVERLAP = timedelta(hours=24)  # controller ruling (B2 fix round 1); known ids dedup what was read
SNIPPET_LENGTH = 400    # of the body, whitespace collapsed (the message script returns its first 2000 characters)

# Shared by every script: the check that Mail.app is running (error -600, which the runner reports as
# `app_closed`; `tell application "Mail"` would otherwise start it), the field escape `parse_records` undoes
# (backslash first, then tab, then line breaks), a date as local wall-clock text, and record joining.
COMMON_HANDLERS = r'''
on requireMail()
	if application "Mail" is not running then error "Mail.app is not running" number -600
end requireMail

on swapText(t, a, b)
	set saved to AppleScript's text item delimiters
	set AppleScript's text item delimiters to a
	set parts to text items of t
	set AppleScript's text item delimiters to b
	set t to parts as text
	set AppleScript's text item delimiters to saved
	return t
end swapText

on esc(v)
	if v is missing value then return ""
	set t to v as text
	set t to my swapText(t, "\\", "\\\\")
	set t to my swapText(t, tab, "\\t")
	set t to my swapText(t, (character id 13) & (character id 10), "\\n")
	set t to my swapText(t, character id 13, "\\n")
	set t to my swapText(t, character id 10, "\\n")
	return t
end esc

on isoOf(d)
	if d is missing value then return ""
	return (d as «class isot» as string)
end isoOf

on joinWith(parts, sep)
	set saved to AppleScript's text item delimiters
	set AppleScript's text item delimiters to sep
	set t to parts as text
	set AppleScript's text item delimiters to saved
	return t
end joinWith

on fields(parts)
	return my joinWith(parts, tab)
end fields

on firstTo(h)
	tell application "Mail"
		try
			return address of first to recipient of h
		on error
			return ""
		end try
	end tell
end firstTo

on messageRecord(boxName, h)
	tell application "Mail"
		set c to ""
		try
			set c to (content of h) as text
		end try
		if (length of c) > 2000 then set c to text 1 thru 2000 of c
		return my fields({"msg", my esc(boxName), my esc(message id of h), my isoOf(date received of h), my isoOf(date sent of h), my esc(sender of h), my esc(my firstTo(h)), my esc(subject of h), my esc(all headers of h), my esc(c)})
	end tell
end messageRecord
'''

# argv: account name. Prints `missing` when Mail.app has no such account, else one `address <addr>` per address.
# The address list is read first: walking `email addresses of account ...` directly fails on the real Mail.app (-1700).
PROFILE_SCRIPT = COMMON_HANDLERS + r'''
on run argv
	my requireMail()
	set acctName to item 1 of argv
	set out to {}
	tell application "Mail"
		if not (exists account acctName) then return "missing"
		set addrs to (get email addresses of account acctName)
	end tell
	repeat with a in addrs
		set end of out to my fields({"address", my esc(a)})
	end repeat
	return my joinWith(out, linefeed)
end run
'''

# argv: subject, body, sender ("Name <address>"), to address. Prints `sent`.
SEND_SCRIPT = COMMON_HANDLERS + r'''
on run argv
	my requireMail()
	set {subj, bodyText, fromText, toAddr} to {item 1 of argv, item 2 of argv, item 3 of argv, item 4 of argv}
	tell application "Mail"
		set m to make new outgoing message with properties {subject:subj, content:bodyText, visible:false}
		set sender of m to fromText
		tell m to make new to recipient at end of to recipients with properties {address:toAddr}
		send m
	end tell
	return "sent"
end run
'''

# argv: account name, Sent Items name, the first email's Message-ID (no brackets), subject, body, sender, to
# address. Prints `noorig <count>` (nothing sent) unless exactly one Sent Items message has that id, else `sent`.
REPLY_SCRIPT = COMMON_HANDLERS + r'''
on run argv
	my requireMail()
	set {acctName, sentName, origId} to {item 1 of argv, item 2 of argv, item 3 of argv}
	set {subj, bodyText, fromText, toAddr} to {item 4 of argv, item 5 of argv, item 6 of argv, item 7 of argv}
	tell application "Mail"
		set hits to (messages of mailbox sentName of account acctName whose message id is origId)
		if (count of hits) is not 1 then return my fields({"noorig", (count of hits) as text})
		set r to reply (item 1 of hits) opening window no
		delete every to recipient of r
		tell r to make new to recipient at end of to recipients with properties {address:toAddr}
		set subject of r to subj
		set content of r to bodyText
		set sender of r to fromText
		send r
	end tell
	return "sent"
end run
'''

# argv: account name, Sent Items name, the account's Outbox name, subject. One record per message with that
# subject (Mail.app's match; the client checks it exactly): `sent <id> <date sent> <to> <subject> <all headers>`
# for Sent Items, `outbox <to> <subject>` for Mail.app's Outbox and the account's own.
FIND_SCRIPT = COMMON_HANDLERS + r'''
on run argv
	my requireMail()
	set {acctName, sentName, outName, subj} to {item 1 of argv, item 2 of argv, item 3 of argv, item 4 of argv}
	set out to {}
	tell application "Mail"
		set found to (messages of mailbox sentName of account acctName whose subject is subj)
		repeat with h in found
			set end of out to my fields({"sent", my esc(message id of h), my isoOf(date sent of h), my esc(my firstTo(h)), my esc(subject of h), my esc(all headers of h)})
		end repeat
		set queued to (messages of outbox whose subject is subj)
		repeat with h in queued
			set end of out to my fields({"outbox", my esc(my firstTo(h)), my esc(subject of h)})
		end repeat
		try
			set waiting to (messages of mailbox outName of account acctName whose subject is subj)
		on error
			set waiting to {}
		end try
		repeat with h in waiting
			set end of out to my fields({"outbox", my esc(my firstTo(h)), my esc(subject of h)})
		end repeat
	end tell
	return my joinWith(out, linefeed)
end run
'''

# argv: account name, the local year, month, day and seconds into that day to read from, then the mailbox names to
# skip. One `msg` record (as `messageRecord`) per message received after that time, everything `message()` needs, so
# reading the page's messages costs no further script. A mailbox that cannot be read prints `error <mailbox> <text>`
# and the client fails the whole read (never a silent skip).
HISTORY_SCRIPT = COMMON_HANDLERS + r'''
on run argv
	my requireMail()
	set acctName to item 1 of argv
	set cutoff to current date
	set day of cutoff to 1
	set year of cutoff to (item 2 of argv) as integer
	set month of cutoff to (item 3 of argv) as integer
	set day of cutoff to (item 4 of argv) as integer
	set time of cutoff to (item 5 of argv) as integer
	set skipNames to items 6 thru -1 of argv
	set out to {}
	tell application "Mail"
		set boxes to (get every mailbox of account acctName)
		repeat with mb in boxes
			set boxName to name of mb
			if skipNames does not contain boxName then
				try
					set found to (messages of mb whose date received > cutoff)
					repeat with h in found
						set end of out to my messageRecord(boxName, h)
					end repeat
				on error errText number errNum
					set end of out to my fields({"error", my esc(boxName), my esc(errText & " (" & errNum & ")")})
				end try
			end if
		end repeat
	end tell
	return my joinWith(out, linefeed)
end run
'''

# argv: account name, Message-ID (no brackets). The first message of the account with that id as one `msg` record
# (`messageRecord`), or nothing. Slow on the real Mail.app (about 30 s: it walks every mailbox), so `message()` uses
# it only for an id the last history page did not hold.
MESSAGE_SCRIPT = COMMON_HANDLERS + r'''
on run argv
	my requireMail()
	set {acctName, wanted} to {item 1 of argv, item 2 of argv}
	tell application "Mail"
		set boxes to (get every mailbox of account acctName)
		repeat with mb in boxes
			set hits to (messages of mb whose message id is wanted)
			if (count of hits) > 0 then return my messageRecord(name of mb, item 1 of hits)
		end repeat
	end tell
	return ""
end run
'''

_ID = re.compile(r"<[^<>\s]+>")


def bare(message_id: str | None) -> str:
    """A Message-ID as Mail.app says it: no whitespace, no angle brackets."""
    value = normalise_message_id(message_id)
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1]
    return value


def bracket(message_id: str | None) -> str:
    """A Message-ID as the sender says it, `<id>`, or '' for none."""
    value = bare(message_id)
    return f"<{value}>" if value else ""


def local_text(at: datetime) -> str:
    """`at` as Mail.app's `«class isot»` text: the Mac's local wall-clock time, to the second, with no zone."""
    return at.astimezone().strftime("%Y-%m-%dT%H:%M:%S")


def local_parts(at: datetime) -> list[str]:
    """`at` in the Mac's zone as the history script's arguments: year, month, day and seconds into the day."""
    local = at.astimezone()
    return [str(local.year), str(local.month), str(local.day),
            str(local.hour * 3600 + local.minute * 60 + local.second)]


def read_local(text: str) -> int | None:
    """Milliseconds since the epoch for a date the scripts print (local time, no zone), or None for none."""
    if not text:
        return None
    try:
        at = datetime.fromisoformat(text)
    except ValueError:
        return None
    return int(at.astimezone(timezone.utc).timestamp() * 1000)  # a naive time is read in the Mac's zone


def _cursor_time(cursor: str) -> datetime:
    """The time a Mail.app cursor names (UTC ISO text); HistoryExpired for anything else."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T[\d:.]+(Z|[+-]\d{2}:?\d{2})?", cursor or ""):
        raise HistoryExpired("the Mail.app cursor is not a time")
    try:
        at = datetime.fromisoformat(cursor)
    except ValueError:
        raise HistoryExpired("the Mail.app cursor is not a time") from None
    return at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)


def _gmail_id(message_id: str) -> str:
    return ID_PREFIX + bracket(message_id)


def _parse_headers(text: str) -> dict[str, str]:
    """`all headers` as a dict of lower-case names to unfolded values, the first of each name."""
    out: dict[str, str] = {}
    for name, value in HeaderParser().parsestr(text or "", headersonly=True).items():
        key = name.lower()
        if key not in out:
            out[key] = " ".join(str(value).split())
    return out


def _root(headers: dict[str, str], own: str) -> str:
    """The thread a message belongs to: the first Message-ID in its References, else in its In-Reply-To, else its
    own (a first email starts its thread)."""
    for name in ("references", "in-reply-to"):
        ids = _ID.findall(headers.get(name, ""))
        if ids:
            return bracket(ids[0])
    return bracket(own)


def _same_address(a: str, b: str) -> bool:
    return parseaddr(a)[1].strip().lower() == parseaddr(b)[1].strip().lower() != ""


def _label(box: str) -> str:
    return "SENT" if box == SENT_BOX else "SPAM" if box == JUNK_BOX else "INBOX"


_MSG_FIELDS = 10  # a `msg` record: kind, mailbox, id, received, sent, sender, to, subject, all headers, body


def _meta(rec: list[str], fallback_id: str = "") -> MessageMeta:
    """A MessageMeta from one `msg` record (`messageRecord`): the META_HEADERS of its `all headers`, the first 400
    characters of the body with whitespace collapsed as the snippet, and `SENT`, `SPAM` or `INBOX` by its mailbox."""
    _, box, msgid, received, sent, sender, to, subject, header_text, content = rec
    headers = _parse_headers(header_text)
    own = bracket(msgid) or bracket(fallback_id)
    label = _label(box)
    dates = (sent, received) if label == "SENT" else (received, sent)
    when = next((at for at in map(read_local, dates) if at is not None), None)
    if when is None:
        raise GmailError("server", None, "Mail.app gave a message without a usable date")
    return MessageMeta(gmail_message_id=_gmail_id(own), thread_id=_root(headers, own), from_addr=sender,
                       to_addr=to, subject=subject, internal_ms=when, labels=(label,),
                       headers={k: headers[k] for k in META_HEADERS if k in headers},
                       snippet=" ".join(content.split())[:SNIPPET_LENGTH])


def _shape(records: list[list[str]], kinds: dict[str, int]) -> list[list[str]]:
    """Each record checked to start with a known kind and to have that kind's field count."""
    for rec in records:
        if not rec or kinds.get(rec[0]) != len(rec):
            raise GmailError("server", None, "Mail.app gave an answer the sender cannot read")
    return records


class MailAppClient(MailClient):
    """One Mail.app account as a sender (`account.kind == "mailapp"`). `runner` runs a script with its arguments and
    a timeout and returns its output (tests pass a fake); `clock` is aware now; `sleep` waits between Sent Items reads
    after a send. Every method may raise GmailError: `app_closed` when Mail.app is not running, `timeout` when a
    script outlasts its time."""

    def __init__(self, account: AccountConfig, runner: Runner = osascript_runner,
                 clock: Callable[[], datetime] | None = None, *, sleep: Callable[[float], None] = time.sleep,
                 reconcile_after_minutes: int | None = None):
        if account.kind != "mailapp" or not account.mailapp_account:
            raise ValueError(f"{account.address} is not a Mail.app account")
        if runner is osascript_runner:
            live_checkout.require_live("the Mail.app client")
        self.address = account.address
        self.mailapp_account = account.mailapp_account
        self._run = runner
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep
        self.settle_minutes = settle_minutes(reconcile_after_minutes)
        self._page: dict[str, MessageMeta] = {}  # the last history page's messages, so `message` needs no script

    def _records(self, script: str, args: list[str], timeout: float) -> list[list[str]]:
        return parse_records(self._run(script, [str(a) for a in args], timeout))

    def _now_text(self) -> str:
        return self._clock().astimezone(timezone.utc).strftime(ISO_FMT)

    # -- the contract -------------------------------------------------------------------------------------------

    def profile(self) -> Profile:
        """The configured address when the Mail.app account holds it, else the account's first address (the tick
        then reports the wrong account). No such account is `auth`. The history id is now: a new cursor."""
        records = _shape(self._records(PROFILE_SCRIPT, [self.mailapp_account], READ_TIMEOUT),
                         {"missing": 1, "address": 2})
        if any(rec[0] == "missing" for rec in records):
            raise GmailError("auth", None, f"Mail.app has no account named {self.mailapp_account}")
        addresses = [rec[1].strip() for rec in records]
        email = self.address if any(a.lower() == self.address.lower() for a in addresses) else \
            (addresses[0] if addresses else "")
        return Profile(email=email, history_id=self._now_text())

    def send(self, m: Outgoing) -> Sent:
        """Send through Mail.app, then find it in Sent Items within 60 seconds of the start. Any failure after the
        script started is a send in doubt for `find_sent`; a follow-up whose first email is not in Sent Items is
        refused (`bad_request`) with nothing sent."""
        try:
            check_outgoing(m)
        except ValueError as err:
            raise GmailError("bad_request", None, f"refused before sending: {err}") from None
        if m.from_addr.strip().lower() != self.address.lower():
            raise GmailError("bad_request", None, f"refused before sending: {m.from_addr} is not {self.address}")
        to = parseaddr(m.to)[1]
        sender = f"{m.from_name} <{self.address}>"
        start = self._clock()
        deadline = start + timedelta(seconds=SEND_TIMEOUT)
        first = m.in_reply_to or m.thread_id  # a follow-up replies to the email it follows, in Sent Items
        if first:
            answer = self._records(REPLY_SCRIPT, [self.mailapp_account, SENT_BOX, bare(first), m.subject, m.body,
                                                  sender, to], SEND_TIMEOUT)
            if answer and answer[0][0] == "noorig":
                raise GmailError("bad_request", None, "the first email is not in Sent Items once, so the follow-up "
                                 "cannot reply to it; nothing was sent")
        else:
            self._records(SEND_SCRIPT, [m.subject, m.body, sender, to], SEND_TIMEOUT)
        since_ms = int(start.timestamp() * 1000) - CLOCK_SKEW_MS
        for _ in range(SEND_TIMEOUT // POLL_SECONDS + 2):  # bounded by tries too, whatever the clock does
            remaining = (deadline - self._clock()).total_seconds()
            if remaining < 1:
                break
            hits, _waiting = self._sent_items(to, m.subject, since_ms, remaining)
            if len(hits) > 1:
                raise GmailError("server", None, "more than one email in Sent Items has this address and subject")
            if len(hits) == 1:
                msgid, thread = hits[0]
                return Sent(gmail_message_id=_gmail_id(msgid), thread_id=m.thread_id or thread,
                            rfc_message_id=msgid, marker=m.marker)
            self._sleep(min(POLL_SECONDS, remaining))
        raise GmailError("timeout", None, f"the email was not in Sent Items {SEND_TIMEOUT} seconds after the send")

    def _sent_items(self, to: str, subject: str, since_ms: int,
                    timeout: float) -> tuple[list[tuple[str, str]], bool]:
        """The Sent Items messages to `to` with exactly `subject` sent at or after `since_ms`, as (Message-ID,
        thread), and whether the search is unsure: a copy still in an Outbox, or a match with no date or no id."""
        records = _shape(self._records(FIND_SCRIPT, [self.mailapp_account, SENT_BOX, OUTBOX_BOX, subject], timeout),
                         {"sent": 6, "outbox": 3})
        hits, unsure = [], False
        for rec in records:
            if rec[0] == "outbox":
                if rec[2] == subject and _same_address(rec[1], to):
                    unsure = True
                continue
            _, msgid, when, addr, subj, headers = rec
            if subj != subject or not _same_address(addr, to):
                continue
            sent_ms = read_local(when)
            if sent_ms is None or not bare(msgid):
                unsure = True
            elif sent_ms >= since_ms:
                hits.append((bracket(msgid), _root(_parse_headers(headers), msgid)))
        return hits, unsure

    def find_sent(self, marker: str, not_before_ms: int, *, send_returned_ms: int, to: str = "",
                  subject: str = "") -> tuple[Sent | None, bool]:
        """The Sent Items rule (module docstring): exactly one message to `to` with exactly `subject` sent at or after
        `not_before_ms` is a hit, carrying `marker`. Two raise SentAmbiguous. A copy in an Outbox, or a match with no
        date, is (None, False), and so is an empty `to` or `subject` (the address was cleared: nothing to match).
        A miss is proof only as MailClient.find_sent says (after `settle_minutes`)."""
        if not (to or "").strip() or not (subject or "").strip():
            return None, False  # nothing to match on (the address was cleared): in doubt, never proof, never resent
        started_ms = int(self._clock().timestamp() * 1000)
        hits, unsure = self._sent_items(parseaddr(to)[1], subject, not_before_ms, READ_TIMEOUT)
        if len(hits) > 1:
            raise SentAmbiguous(f"{len(hits)} emails in Sent Items match; none is taken as this send")
        if hits:
            msgid, thread = hits[0]
            return Sent(gmail_message_id=_gmail_id(msgid), thread_id=thread, rfc_message_id=msgid,
                        marker=normalise_message_id(marker)), True
        if unsure:
            return None, False
        return None, miss_is_proof(True, started_ms, send_returned_ms, self.settle_minutes)

    def history(self, start_history_id: str) -> HistoryPage:
        """Messages received after the cursor time less 24 hours (`HISTORY_OVERLAP`; the inbound read's known ids
        drop what was already read), in every mailbox but Sent Items, Drafts and Outbox, oldest first, each once. The
        new cursor is the time the read began. A cursor that is not a time (empty, or a Gmail history id) is
        HistoryExpired. A mailbox Mail.app could not read fails the whole read (`server`). The page's messages are
        kept, so `message` serves them without another script."""
        since = _cursor_time(start_history_id)
        read_at = self._now_text()
        records = _shape(self._records(HISTORY_SCRIPT, [self.mailapp_account, *local_parts(since - HISTORY_OVERLAP),
                                                        *SKIPPED_BOXES], READ_TIMEOUT),
                         {"msg": _MSG_FIELDS, "error": 3})
        failed = [rec for rec in records if rec[0] == "error"]
        if failed:
            raise GmailError("server", None, f"Mail.app could not read the mailbox {failed[0][1]}: {failed[0][2]}")
        rows = sorted(records, key=lambda rec: (read_local(rec[3]) is None, read_local(rec[3]) or 0))
        added: list[Added] = []
        page: dict[str, MessageMeta] = {}
        for rec in rows:
            if rec[1] in SKIPPED_BOXES or not bare(rec[2]) or _gmail_id(rec[2]) in page:
                continue
            meta = _meta(rec)
            page[meta.gmail_message_id] = meta
            added.append(Added(gmail_message_id=meta.gmail_message_id, thread_id=meta.thread_id, labels=meta.labels))
        self._page = page
        return HistoryPage(added=tuple(added), history_id=read_at)

    def message(self, gmail_message_id: str) -> MessageMeta:
        """One message by its `mailapp:<id>` (`_meta`). One the last history page held is served from it; any
        other is looked up in every mailbox (slow). Not in Mail.app is a 404, as Gmail's for a deleted message."""
        if not gmail_message_id.startswith(ID_PREFIX) or not bare(gmail_message_id[len(ID_PREFIX):]):
            raise GmailError("bad_request", 404, "not a Mail.app message id")
        wanted = bare(gmail_message_id[len(ID_PREFIX):])
        cached = self._page.get(_gmail_id(wanted))
        if cached is not None:
            return cached
        records = _shape(self._records(MESSAGE_SCRIPT, [self.mailapp_account, wanted], READ_TIMEOUT),
                         {"msg": _MSG_FIELDS})
        if not records:
            raise GmailError("bad_request", 404, "the message is not in Mail.app")
        return _meta(records[0], wanted)

    def thread(self, thread_id: str) -> list[MessageMeta]:
        raise ThreadUnsupported("Mail.app has no threads to read; read its mailboxes with history instead")
