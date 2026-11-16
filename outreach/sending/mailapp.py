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
  after the cursor less 24 hours, in two steps so a busy day never outgrows the 120 second read: it lists each
  message's mailbox, Message-ID and date (cheap), then fetches full records only for the ones the caller has not
  handled (`skip_ids`), at most 40 a call and none started after 80 seconds, oldest first. Neither step queries
  message by message: each mailbox's window is resolved once and walked. The page holds every message it names, so
  `message` costs no further script. One read covers at most 3 days (`HISTORY_SLICE`) from the cursor less 24
  hours. The cursor is a time (UTC, ISO), not a Gmail history id: when nothing was left out it moves to the slice's
  end, or to the read's start when the slice reached now. There are no threads to read (`thread` raises
  ThreadUnsupported): with no cursor the inbound read starts an hour before the account's earliest first send of the
  last 45 days, and catches up from there.

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
import contextlib
import re
import time
from collections.abc import Callable, Collection, Iterator
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
           "LIST_SCRIPT", "FETCH_SCRIPT", "MESSAGE_SCRIPT", "ID_PREFIX", "SENT_BOX", "DRAFTS_BOX", "OUTBOX_BOX",
           "JUNK_BOX", "SKIPPED_BOXES", "SEND_TIMEOUT", "READ_TIMEOUT", "POLL_SECONDS", "HISTORY_OVERLAP",
           "HISTORY_SLICE",
           "FETCH_LIMIT", "FETCH_BUDGET", "FETCH_ASK", "SNIPPET_LENGTH", "bracket", "bare", "local_text",
           "local_parts", "read_local"]

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
# One read lists at most this much (controller ruling, B4 fix round 2): a 45-day list outlasted the 120 s read on the
# real account, a 2-day one took about 20 s. A cursor further back catches up a slice a tick.
HISTORY_SLICE = timedelta(days=3)
# One fetch prints at most FETCH_LIMIT full records (about 0.9 s each on the real Mail.app) and starts none after
# FETCH_BUDGET seconds (controller ruling, B4 fix round 1): the real guard against the 120 s read timeout. The ids it
# is asked about are at most FETCH_ASK, oldest first; what is left waits for the next tick.
FETCH_LIMIT = 40
FETCH_BUDGET = 80
FETCH_ASK = 400
# Of the body, whitespace collapsed. All 2000 characters the message script returns: a Microsoft notice puts its status
# code and failed address a long way down, and Gmail's own snippet is a different path (`gmail.py`).
SNIPPET_LENGTH = 2000

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

on cutoffOf(y, mo, d, secs)
	set cutoff to current date
	set day of cutoff to 1
	set year of cutoff to y as integer
	set month of cutoff to mo as integer
	set day of cutoff to d as integer
	set time of cutoff to secs as integer
	return cutoff
end cutoffOf

on secondsSince(t)
	return (current date) - t
end secondsSince

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
	set c to ""
	tell application "Mail"
		try
			set c to (content of h) as text
		end try
	end tell
	-- A delivery notice keeps its machine-readable part (Final-Recipient, Status, Diagnostic-Code) after the quoted
	-- original, far past the first 2000 characters: send the 1200 characters from there as one more field.
	set blk to ""
	set pos to offset of "Final-Recipient:" in c
	if pos is 0 then set pos to offset of "Reporting-MTA:" in c
	if pos > 0 then
		set stopAt to pos + 1199
		if stopAt > (length of c) then set stopAt to length of c
		set blk to text pos thru stopAt of c
	end if
	if (length of c) > 2000 then set c to text 1 thru 2000 of c
	tell application "Mail"
		return my fields({"msg", my esc(boxName), my esc(message id of h), my isoOf(date received of h), my isoOf(date sent of h), my esc(sender of h), my esc(my firstTo(h)), my esc(subject of h), my esc(all headers of h), my esc(c), my esc(blk)})
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

# argv: subject, body, sender ("Name <address>"), to address. Prints `sent`, or `notsent <text>` when building the
# email failed before Mail.app's `send` (nothing went out). An error at or after `send` is not caught: the runner
# reports it, and the client takes it as a send in doubt.
SEND_SCRIPT = COMMON_HANDLERS + r'''
on run argv
	my requireMail()
	set {subj, bodyText, fromText, toAddr} to {item 1 of argv, item 2 of argv, item 3 of argv, item 4 of argv}
	tell application "Mail"
		try
			set m to make new outgoing message with properties {subject:subj, content:bodyText, visible:false}
			set sender of m to fromText
			tell m to make new to recipient at end of to recipients with properties {address:toAddr}
		on error errText number errNum
			return my fields({"notsent", my esc(errText & " (" & errNum & ")")})
		end try
		send m
	end tell
	return "sent"
end run
'''

# argv: account name, Sent Items name, the first email's Message-ID (no brackets), subject, body, sender, to
# address. Prints `noorig <count>` (nothing sent) unless exactly one Sent Items message has that id, `notsent <text>`
# when building the reply failed before Mail.app's `send` (nothing went out), else `sent`.
REPLY_SCRIPT = COMMON_HANDLERS + r'''
on run argv
	my requireMail()
	set {acctName, sentName, origId} to {item 1 of argv, item 2 of argv, item 3 of argv}
	set {subj, bodyText, fromText, toAddr} to {item 4 of argv, item 5 of argv, item 6 of argv, item 7 of argv}
	tell application "Mail"
		try
			set hits to (messages of mailbox sentName of account acctName whose message id is origId)
			if (count of hits) is not 1 then return my fields({"noorig", (count of hits) as text})
			set r to reply (item 1 of hits) opening window no
			delete every to recipient of r
			tell r to make new to recipient at end of to recipients with properties {address:toAddr}
			set subject of r to subj
			set content of r to bodyText
			set sender of r to fromText
		on error errText number errNum
			return my fields({"notsent", my esc(errText & " (" & errNum & ")")})
		end try
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

# argv: account name, the slice's start and end, each as the local year, month, day and seconds into that day, then
# the mailbox names to skip. The first step of a read: one `id <position> <mailbox> <Message-ID> <date received>`
# record per message received after the start and at or before the end. Each mailbox's window is resolved once (`whose`, about 1 s) and walked item by item: a
# bulk property read of a resolved list fails on the real Mail.app, and a `whose message id is` query per message
# costs about 2 s each. The position is the mailbox's place in `every mailbox of account` (two mailboxes may share a
# name). A mailbox that cannot be read prints `error <position> <mailbox> <text>` and the client fails the whole read.
LIST_SCRIPT = COMMON_HANDLERS + r'''
on run argv
	my requireMail()
	set acctName to item 1 of argv
	set lo to my cutoffOf(item 2 of argv, item 3 of argv, item 4 of argv, item 5 of argv)
	set hi to my cutoffOf(item 6 of argv, item 7 of argv, item 8 of argv, item 9 of argv)
	set skipNames to items 10 thru -1 of argv
	set out to {}
	tell application "Mail"
		set boxes to (get every mailbox of account acctName)
		repeat with pos from 1 to (count of boxes)
			set mb to item pos of boxes
			set boxName to name of mb
			if skipNames does not contain boxName then
				try
					set found to (messages of mb whose date received > lo and date received <= hi)
					repeat with h in found
						set end of out to my fields({"id", pos as text, my esc(boxName), my esc(message id of h), my isoOf(date received of h)})
					end repeat
				on error errText number errNum
					set end of out to my fields({"error", pos as text, my esc(boxName), my esc(errText & " (" & errNum & ")")})
				end try
			end if
		end repeat
	end tell
	return my joinWith(out, linefeed)
end run
'''

# argv: account name, the slice as the list script's (start and end), the time budget in seconds, the most records to
# print, then for each mailbox that holds wanted ids: its position, how many ids, and the ids (no
# brackets). The second step: each such mailbox's window is resolved once and walked item by item, and each message
# whose Message-ID is wanted is printed as one `msg` record (`messageRecord`). It starts no new record once the
# budget is spent or the most records are printed, and then prints a last `more` record: the ids not printed were
# left out, not gone. A mailbox that cannot be read prints `error <position> <mailbox> <text>`.
FETCH_SCRIPT = COMMON_HANDLERS + r'''
on run argv
	my requireMail()
	set started to current date
	set acctName to item 1 of argv
	set lo to my cutoffOf(item 2 of argv, item 3 of argv, item 4 of argv, item 5 of argv)
	set hi to my cutoffOf(item 6 of argv, item 7 of argv, item 8 of argv, item 9 of argv)
	set budget to (item 10 of argv) as integer
	set most to (item 11 of argv) as integer
	set k to 12
	set printed to 0
	set stopped to false
	set out to {}
	tell application "Mail"
		set boxes to (get every mailbox of account acctName)
		repeat while k <= (count of argv)
			set pos to (item k of argv) as integer
			set n to (item (k + 1) of argv) as integer
			set wanted to items (k + 2) thru (k + 1 + n) of argv
			set k to k + 2 + n
			set mb to item pos of boxes
			set boxName to name of mb
			try
				set found to (messages of mb whose date received > lo and date received <= hi)
				repeat with h in found
					if printed >= most or (my secondsSince(started)) > budget then
						set stopped to true
						exit repeat
					end if
					if wanted contains (message id of h) then
						set end of out to my messageRecord(boxName, h)
						set printed to printed + 1
					end if
				end repeat
			on error errText number errNum
				set end of out to my fields({"error", pos as text, my esc(boxName), my esc(errText & " (" & errNum & ")")})
			end try
			if stopped then exit repeat
		end repeat
	end tell
	if stopped then set end of out to "more"
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
    """`at` in the Mac's zone as the list script's arguments: year, month, day and seconds into the day."""
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


_MSG_FIELDS = 11  # a `msg` record: kind, mailbox, id, received, sent, sender, to, subject, all headers, body, block


def _meta(rec: list[str], fallback_id: str = "") -> MessageMeta:
    """A MessageMeta from one `msg` record (`messageRecord`): the META_HEADERS of its `all headers`, the first 2000
    characters of the body with whitespace collapsed, then the delivery block (up to 1200 characters from
    `Final-Recipient:` or `Reporting-MTA:`, collapsed too), as the snippet, and `SENT`, `SPAM` or `INBOX` by its mailbox."""
    _, box, msgid, received, sent, sender, to, subject, header_text, content, block = rec
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
                       snippet=" ".join((" ".join(content.split())[:SNIPPET_LENGTH], " ".join(block.split()))).strip())


def _fail_on_error(records: list[list[str]]) -> None:
    """A read whose script could not read a mailbox (`error <position> <mailbox> <text>`) fails whole (`server`),
    naming it: never a silent skip."""
    failed = [rec for rec in records if rec[0] == "error"]
    if failed:
        raise GmailError("server", None, f"Mail.app could not read the mailbox {failed[0][2]}: {failed[0][3]}")


def _shape(records: list[list[str]], kinds: dict[str, int]) -> list[list[str]]:
    """Each record checked to start with a known kind and to have that kind's field count."""
    for rec in records:
        if not rec or kinds.get(rec[0]) != len(rec):
            raise GmailError("server", None, "Mail.app gave an answer the sender cannot read")
    return records


# The send script's failures that come before Mail.app could take the email: Mail.app not running (`requireMail`, the
# script's first line) and macOS refusing the Apple events (the first `tell`). Everything else from the send script
# on may have sent it.
_NOT_STARTED = frozenset({"app_closed", "not_allowed"})


@contextlib.contextmanager
def _in_doubt(*, before: Collection[str] = frozenset()) -> Iterator[None]:
    """Every failure inside is raised as a GmailError with `maybe_sent` (the email may have gone out), but a
    GmailError whose kind is in `before` (it stopped the script before Mail.app's `send`), which is raised as it is."""
    try:
        yield
    except GmailError as err:
        if err.maybe_sent or err.kind in before:
            raise
        raise GmailError(err.kind, err.status, err.detail, maybe_sent=True) from err
    except Exception as err:
        raise GmailError("server", None, f"the send failed after Mail.app was asked to send: {type(err).__name__}",
                         maybe_sent=True) from err


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
        """Send through Mail.app, then find it in Sent Items within 60 seconds of the start. A follow-up whose first
        email is not in Sent Items is refused (`bad_request`), and so is one that fails before Mail.app's `send`
        (`notsent`: `server`), each with nothing sent; Mail.app not running is `app_closed`. Any other failure, from
        the send script on (it timed out, failed at `send`, or printed `sent` and the poll did not see it), may have
        sent the email: it is raised with `maybe_sent`, a send in doubt settled by `find_sent` and never retried."""
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
            script, args = REPLY_SCRIPT, [self.mailapp_account, SENT_BOX, bare(first), m.subject, m.body, sender, to]
        else:
            script, args = SEND_SCRIPT, [m.subject, m.body, sender, to]
        with _in_doubt(before=_NOT_STARTED):
            answer = _shape(self._records(script, args, SEND_TIMEOUT), {"sent": 1, "noorig": 2, "notsent": 2})
        if answer and answer[0][0] == "noorig":
            raise GmailError("bad_request", None, "the first email is not in Sent Items once, so the follow-up "
                             "cannot reply to it; nothing was sent")
        if answer and answer[0][0] == "notsent":
            text = answer[0][1]
            kind = "not_allowed" if "(-1743)" in text else "app_closed" if "(-600)" in text else "server"
            raise GmailError(kind, None, f"Mail.app could not build the email, so nothing was sent: {text}")
        if [rec[0] for rec in answer] != ["sent"]:
            raise GmailError("server", None, "Mail.app gave an answer the sender cannot read after the send",
                             maybe_sent=True)
        since_ms = int(start.timestamp() * 1000) - CLOCK_SKEW_MS
        with _in_doubt():  # Mail.app took it: whatever fails now, the email may have gone out
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

    def history(self, start_history_id: str, *, skip_ids: Collection[str] = frozenset()) -> HistoryPage:
        """Messages received after the cursor time less 24 hours (`HISTORY_OVERLAP`), in every mailbox but Sent Items,
        Drafts and Outbox, up to 3 days later at most (`HISTORY_SLICE`, never past now), in two steps: the slice's
        Message-IDs (`LIST_SCRIPT`), then the full records of the ones
        not in `skip_ids` (bracketed or bare Message-IDs the caller already handled), oldest first, each once
        (`FETCH_SCRIPT`: at most `FETCH_LIMIT` records, none started after `FETCH_BUDGET` seconds). An id the fetch
        stopped before is left out; one it did not find though it read that mailbox through is gone (deleted or moved
        since the list), so it never holds the read. With none left out the new cursor is the time the read began
        when the slice reached now (`more` False), else the slice's end (`more` True: the read is behind and the next
        call reads on from there); with some left out it is the cursor given (`more` True) and the next call fetches
        the rest. A cursor that is
        not a time (empty, or a Gmail history id) is HistoryExpired. A mailbox Mail.app could not read fails the whole
        read (`server`). The page holds every message it names (`held`), so `message` serves them with no script."""
        now = self._clock().astimezone(timezone.utc).replace(microsecond=0)
        read_at = now.strftime(ISO_FMT)
        start = _cursor_time(start_history_id) - HISTORY_OVERLAP
        end = min(now, start + HISTORY_SLICE)
        window = [*local_parts(start), *local_parts(end)]
        listed = _shape(self._records(LIST_SCRIPT, [self.mailapp_account, *window, *SKIPPED_BOXES], READ_TIMEOUT),
                        {"id": 5, "error": 4})
        _fail_on_error(listed)
        skip = {bracket(i) for i in skip_ids} - {""}
        wanted: dict[str, str] = {}  # Message-ID to the position of the mailbox it is fetched from, oldest first
        for _, pos, box, msgid, _ in sorted(listed, key=lambda rec: (read_local(rec[4]) is None,
                                                                     read_local(rec[4]) or 0)):
            own = bracket(msgid)
            if box not in SKIPPED_BOXES and own and own not in skip and own not in wanted:
                wanted[own] = pos
        asked = list(wanted.items())[:FETCH_ASK]
        fetched: dict[str, MessageMeta] = {}
        stopped = False
        if asked:
            by_box: dict[str, list[str]] = {}
            for own, pos in asked:
                by_box.setdefault(pos, []).append(bare(own))
            args = [self.mailapp_account, *window, str(FETCH_BUDGET), str(FETCH_LIMIT)]
            for pos, ids in by_box.items():
                args += [pos, str(len(ids)), *ids]
            records = _shape(self._records(FETCH_SCRIPT, args, READ_TIMEOUT),
                             {"msg": _MSG_FIELDS, "error": 4, "more": 1})
            _fail_on_error(records)
            stopped = any(rec[0] == "more" for rec in records)
            for rec in records:
                if rec[0] == "msg":
                    meta = _meta(rec)
                    fetched.setdefault(meta.gmail_message_id, meta)
        page = {_gmail_id(own): fetched[_gmail_id(own)] for own, _ in asked if _gmail_id(own) in fetched}
        self._page = page  # oldest first; an id not printed is gone, or left out when the fetch stopped early
        left_out = (stopped and len(page) < len(asked)) or len(wanted) > len(asked)
        if left_out:
            cursor = start_history_id  # the next read lists this slice again and fetches the rest
        elif end < now:
            cursor = end.strftime(ISO_FMT)  # this slice is read; the next one starts from its end
        else:
            cursor = read_at
        return HistoryPage(added=tuple(Added(gmail_message_id=m.gmail_message_id, thread_id=m.thread_id,
                                             labels=m.labels) for m in page.values()),
                           history_id=cursor, held=True, more=left_out or end < now)

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
