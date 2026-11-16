"""A scriptable, in-memory Mail.app: the real `MailAppClient` with its runner replaced.

`FakeMailApp` is a MailAppClient whose runner (`_mailapp`) answers each of the client's scripts the way that script
does in the real Mail.app: the same arguments in, the same records out (fields escaped as the scripts' `esc`
handler does, dates as local wall-clock text), the subject matched without regard to case as AppleScript's `is`
does. So every test and the simulator run the client's own code; only Mail.app is fake. Nothing here starts
osascript, opens a socket or a file. `FakeMailApp.sent` is the only count of emails that "went out".

Hooks, as FakeGmail's: `fail_next_send`, `slow_sent` (the email waits in Mail.app's Outbox), `deliver_reply`,
`deliver_auto_reply`, `deliver_ndr` (a Microsoft "Undeliverable" notice), `deliver_unrelated`, `add_to_sent`,
`add_to_mailbox`, `copy_to`, `fail_mailbox`, and `closed` (Mail.app not running: every script fails with `app_closed`).
`probe(method_name)` runs at the start of every client method; `calls` lists them and `scripts` the scripts run.
"""
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr

from outreach.sending.applescript import escape_field
from outreach.sending.config import AccountConfig
from outreach.sending.mail import GmailError, HistoryPage, MessageMeta, Outgoing, Profile, Sent
from outreach.sending.mailapp import (FIND_SCRIPT, HISTORY_SCRIPT, MESSAGE_SCRIPT, PROFILE_SCRIPT, REPLY_SCRIPT,
                                      SEND_SCRIPT, MailAppClient, local_text)

__all__ = ["FakeMailApp", "Stored", "MAILBOXES", "NDR_FROM"]

# The Exchange account's mailboxes as the spike listed them (2026-11-15).
MAILBOXES = ("Inbox", "Junk Email", "Deleted Items", "Outbox", "Drafts", "Sent Items", "Archive", "Job Search", "NEU",
             "Newsletters", "Outreach", "Notes", "Tasks", "Journal", "Conversation History")
NDR_FROM = "Microsoft Outlook <MicrosoftExchange329e71ec88ae4615bbc36ab6ce41109e@northeastern.edu>"
_FOREVER = datetime.max.replace(tzinfo=timezone.utc)
_NAMES = {PROFILE_SCRIPT: "profile", SEND_SCRIPT: "send", REPLY_SCRIPT: "reply", FIND_SCRIPT: "find",
          HISTORY_SCRIPT: "history", MESSAGE_SCRIPT: "message"}


@dataclass
class Stored:
    """One message in the fake Mail.app. `msgid` is bare, as Mail.app says it. A sent email with `leaves_outbox`
    later than now is still in Mail.app's Outbox, not in its mailbox."""
    box: str
    msgid: str
    sender: str
    to: str
    subject: str
    sent_at: datetime
    received_at: datetime
    headers: dict[str, str] = field(default_factory=dict)
    content: str = ""
    replied_to: str | None = None
    leaves_outbox: datetime | None = None

    @property
    def rfc_id(self) -> str:
        return f"<{self.msgid}>"

    def all_headers(self) -> str:
        return "\n".join(f"{name}: {value}" for name, value in self.headers.items())


@dataclass
class _Failure:
    error: BaseException
    accepted: bool
    in_sent: bool
    lands_after: timedelta | None


def _shipped_account() -> AccountConfig:
    from outreach.sending.config import load_config
    return next(a for a in load_config().accounts if a.kind == "mailapp")


def _delivery_block(content: str) -> str:
    """The script's block field: 1200 characters from `Final-Recipient:` (else `Reporting-MTA:`), or nothing."""
    for marker in ("Final-Recipient:", "Reporting-MTA:"):
        at = content.find(marker)
        if at >= 0:
            return content[at:at + 1200]
    return ""


def _record(*fields: str) -> str:
    return "\t".join(fields)


def _esc(value: str | None) -> str:
    return escape_field(value or "")


class FakeMailApp(MailAppClient):
    """In-memory Mail.app behind the real client. `account` defaults to the shipped Mail.app account. `id_prefix`
    starts every Message-ID it makes, so two fakes in one test never make the same one."""

    def __init__(self, *, clock: Callable[[], datetime], account: AccountConfig | None = None,
                 probe: Callable[[str], None] | None = None, reconcile_after_minutes: int | None = None,
                 id_prefix: str = ""):
        account = account or _shipped_account()
        super().__init__(account, self._mailapp, clock, sleep=self._wait,
                         reconcile_after_minutes=reconcile_after_minutes)
        self.clock = clock
        self.probe = probe
        self.id_prefix = id_prefix
        self.closed = False                                  # Mail.app is not running
        self.accounts_in_app = {account.mailapp_account}     # the Mail.app account names that exist
        self.addresses = [account.address]                   # the account's email addresses
        self.store: list[Stored] = []
        self.sent: list[tuple[Outgoing | None, str, datetime]] = []  # (email, Message-ID, when) for each that went
        self.calls: list[str] = []
        self.scripts: list[str] = []
        self._failures: list[_Failure] = []
        self._slow: list[timedelta | None] = []
        self._failing_boxes: set[str] = set()
        self._outgoing: Outgoing | None = None
        self._next = 0

    # -- scripting (not client calls: no probe, not in calls) ---------------------------------------------------

    def fail_next_send(self, error: BaseException, *, accepted: bool = False, in_sent: bool = True,
                       lands_after: timedelta | None = None) -> None:
        """The next send script raises `error`. With `accepted=True` the email goes out first (a timeout after Mail.app
        took it) and reaches Sent Items (`lands_after` later, waiting in the Outbox until then, or never with
        `in_sent=False`). Calls queue up."""
        self._failures.append(_Failure(error, accepted, in_sent, lands_after))

    def fail_mailbox(self, mailbox: str) -> None:
        """Mail.app cannot read `mailbox` (as an Exchange Tasks or Notes folder may refuse `messages of`): the history
        script prints an error record for it, every time from now on."""
        self._failing_boxes.add(mailbox)

    def slow_sent(self, after: timedelta | None) -> None:
        """The next email sent waits in Mail.app's Outbox for `after` before it reaches Sent Items (None: it never
        leaves). Longer than the client's 60 second poll makes its send a send in doubt."""
        self._slow.append(after)

    def deliver_reply(self, to: Sent, from_addr: str, *, subject: str | None = None, content: str = "",
                      headers: Mapping[str, str] | None = None, mailbox: str = "Inbox",
                      at: datetime | None = None) -> Stored:
        original = self._by_id(to.rfc_message_id)
        extra = {"In-Reply-To": original.rfc_id, "References": self._references(original)}
        extra.update(headers or {})
        return self._inbound(mailbox, from_addr, subject or "Re: " + original.subject, extra, content, at)

    def deliver_auto_reply(self, to: Sent, from_addr: str, *, subject: str = "Automatic reply",
                           mailbox: str = "Inbox") -> Stored:
        return self.deliver_reply(to, from_addr, subject=subject, headers={"Auto-Submitted": "auto-replied"},
                                  mailbox=mailbox)

    def deliver_ndr(self, to: Sent, *, status: str = "5.1.1", mailbox: str = "Inbox",
                    at: datetime | None = None) -> Stored:
        """A Microsoft "Undeliverable" notice for `to`: In-Reply-To names it, the address is in the body."""
        original = self._by_id(to.rfc_message_id)
        recipient = parseaddr(original.to)[1]
        domain = recipient.rpartition("@")[2]
        content = (f"Your message to {recipient} couldn't be delivered.\n\n{domain} couldn't confirm that your "
                   f"message was delivered.\n\nDiagnostic information for administrators:\n\nGenerating server: "
                   f"PH0PR22MB3333.namprd22.prod.outlook.com\n\n{recipient}\nRemote server returned '550 {status} "
                   f"Recipient address rejected: User unknown'")
        headers = {"Content-Type": 'multipart/report; report-type=delivery-status;\n\tboundary="b1"',
                   "In-Reply-To": original.rfc_id}
        return self._inbound(mailbox, NDR_FROM, "Undeliverable: " + original.subject, headers, content, at,
                             to_addr=self.address)

    def deliver_unrelated(self, from_addr: str, subject: str, *, mailbox: str = "Inbox",
                          at: datetime | None = None) -> Stored:
        return self._inbound(mailbox, from_addr, subject, {}, "", at)

    def add_to_mailbox(self, mailbox: str, from_addr: str, to: str, subject: str, *, at: datetime | None = None,
                       headers: Mapping[str, str] | None = None, content: str = "") -> Stored:
        return self._inbound(mailbox, from_addr, subject, dict(headers or {}), content, at, to_addr=to)

    def add_to_sent(self, to: str, subject: str, *, at: datetime | None = None) -> Stored:
        """A message in Sent Items this fake did not send (his own mail, or a decoy). Not counted in `sent`."""
        return self._inbound("Sent Items", f"Devanshu Rajesh Chicholikar <{self.address}>", subject, {}, "", at,
                             to_addr=to)

    def copy_to(self, message: Stored, mailbox: str) -> Stored:
        """The same message (same Message-ID) in another mailbox too, as a rule that copies it would leave it."""
        copy = Stored(**{**message.__dict__, "box": mailbox, "headers": dict(message.headers)})
        self.store.append(copy)
        return copy

    def sent_items(self) -> list[Stored]:
        """Every email in Sent Items or on its way there, oldest first."""
        return [s for s in self.store if s.box == "Sent Items"]

    # -- the client, each call recorded and probed --------------------------------------------------------------

    def _enter(self, name: str) -> None:
        self.calls.append(name)
        if self.probe is not None:
            self.probe(name)

    def profile(self) -> Profile:
        self._enter("profile")
        return super().profile()

    def send(self, m: Outgoing) -> Sent:
        self._enter("send")
        self._outgoing = m
        try:
            return super().send(m)
        finally:
            self._outgoing = None

    def find_sent(self, marker: str, not_before_ms: int, *, send_returned_ms: int, to: str = "",
                  subject: str = "") -> tuple[Sent | None, bool]:
        self._enter("find_sent")
        return super().find_sent(marker, not_before_ms, send_returned_ms=send_returned_ms, to=to, subject=subject)

    def history(self, start_history_id: str) -> HistoryPage:
        self._enter("history")
        return super().history(start_history_id)

    def message(self, gmail_message_id: str) -> MessageMeta:
        self._enter("message")
        return super().message(gmail_message_id)

    def thread(self, thread_id: str) -> list[MessageMeta]:
        self._enter("thread")
        return super().thread(thread_id)

    # -- the fake Mail.app: one answer per script ---------------------------------------------------------------

    def _wait(self, seconds: float) -> None:
        advance = getattr(self.clock, "advance", None)
        if advance is not None:
            advance(seconds=seconds)

    def _mailapp(self, script: str, args: Sequence[str], timeout: float) -> str:
        if self.closed:
            raise GmailError("app_closed", None, "execution error: Mail.app is not running (-600)")
        name = _NAMES.get(script)
        if name is None:
            raise AssertionError("FakeMailApp was given a script that is not one of the client's")
        self.scripts.append(name)
        args = list(args)
        if name not in ("send", "profile") and args[0] not in self.accounts_in_app:
            raise GmailError("server", None, f"execution error: Can't get account \"{args[0]}\". (-1728)")
        lines = getattr(self, f"_{name}_script")(args)
        return "\n".join(lines) + "\n"

    def _profile_script(self, args: list[str]) -> list[str]:
        if args[0] not in self.accounts_in_app:
            return ["missing"]
        return [_record("address", _esc(a)) for a in self.addresses]

    def _send_script(self, args: list[str]) -> list[str]:
        subject, body, sender, to = args
        self._go_out(subject, body, sender, to, None)
        return ["sent"]

    def _reply_script(self, args: list[str]) -> list[str]:
        _account, sent_box, original_id, subject, body, sender, to = args
        hits = [s for s in self._visible(sent_box) if s.msgid == original_id]
        if len(hits) != 1:
            return [_record("noorig", str(len(hits)))]
        self._go_out(subject, body, sender, to, hits[0])
        return ["sent"]

    def _find_script(self, args: list[str]) -> list[str]:
        _account, sent_box, outbox, subject = args
        same = subject.casefold()
        out = [_record("sent", _esc(s.msgid), local_text(s.sent_at), _esc(parseaddr(s.to)[1]), _esc(s.subject),
                       _esc(s.all_headers()))
               for s in self._visible(sent_box) if s.subject.casefold() == same]
        out += [_record("outbox", _esc(parseaddr(s.to)[1]), _esc(s.subject))
                for s in self.store if self._in_outbox(s) and s.subject.casefold() == same]
        out += [_record("outbox", _esc(parseaddr(s.to)[1]), _esc(s.subject))
                for s in self._visible(outbox) if s.subject.casefold() == same]
        return out

    def _history_script(self, args: list[str]) -> list[str]:
        year, month, day, seconds = (int(a) for a in args[1:5])
        skip = set(args[5:])
        cutoff = (datetime(year, month, day) + timedelta(seconds=seconds)).astimezone()  # the Mac's local time
        out = []
        for box in MAILBOXES:
            if box in skip:
                continue
            if box in self._failing_boxes:
                out.append(_record("error", _esc(box), _esc("Mail got an error: Can't get every message. (-1728)")))
                continue
            out += [self._message_record(box, s) for s in self._visible(box)
                    if s.received_at.replace(microsecond=0) > cutoff]
        return out

    def _message_script(self, args: list[str]) -> list[str]:
        wanted = args[1]
        for box in MAILBOXES:
            for s in self._visible(box):
                if s.msgid == wanted:
                    return [self._message_record(box, s)]
        return []

    @staticmethod
    def _message_record(box: str, s: Stored) -> str:
        """The scripts' `messageRecord`."""
        return _record("msg", _esc(box), _esc(s.msgid), local_text(s.received_at), local_text(s.sent_at),
                       _esc(s.sender), _esc(parseaddr(s.to)[1]), _esc(s.subject), _esc(s.all_headers()),
                       _esc(s.content[:2000]), _esc(_delivery_block(s.content)))

    # -- internals ----------------------------------------------------------------------------------------------

    def _in_outbox(self, s: Stored) -> bool:
        return s.leaves_outbox is not None and self.clock() < s.leaves_outbox

    def _visible(self, box: str) -> list[Stored]:
        return [s for s in self.store if s.box == box and not self._in_outbox(s)]

    def _by_id(self, rfc_id: str) -> Stored:
        wanted = rfc_id.strip().strip("<>")
        return next(s for s in self.store if s.msgid == wanted)

    @staticmethod
    def _references(original: Stored) -> str:
        return " ".join(x for x in (original.headers.get("References", ""), original.rfc_id) if x)

    def _new_id(self, domain: str) -> str:
        self._next += 1
        return f"{self.id_prefix}m{self._next}@{domain}"

    def _go_out(self, subject: str, body: str, sender: str, to: str, original: Stored | None) -> None:
        failure = self._failures.pop(0) if self._failures else None
        if failure is not None and not failure.accepted:
            raise failure.error
        slow = self._slow.pop(0) if self._slow else timedelta(0)
        now = self.clock()
        msgid = self._new_id("northeastern.edu")
        headers = {"Message-ID": f"<{msgid}>", "From": sender, "To": to, "Subject": subject,
                   "Content-Type": 'multipart/alternative; boundary="_000_"'}
        if original is not None:
            headers["In-Reply-To"] = original.rfc_id
            headers["References"] = self._references(original)
        leaves = None
        if failure is not None and failure.lands_after is not None:
            leaves = now + failure.lands_after
        elif slow is None:
            leaves = _FOREVER
        elif slow:
            leaves = now + slow
        self.sent.append((self._outgoing, f"<{msgid}>", now))
        if failure is None or failure.in_sent:
            self.store.append(Stored(box="Sent Items", msgid=msgid, sender=sender, to=to, subject=subject,
                                     sent_at=now, received_at=now, headers=headers, content=body,
                                     replied_to=original.msgid if original is not None else None,
                                     leaves_outbox=leaves))
        if failure is not None:
            raise failure.error

    def _inbound(self, box: str, from_addr: str, subject: str, headers: Mapping[str, str], content: str,
                 at: datetime | None, *, to_addr: str | None = None) -> Stored:
        when = at or self.clock()
        domain = parseaddr(from_addr)[1].rpartition("@")[2] or "example.com"
        msgid = self._new_id(domain)
        stored = Stored(box=box, msgid=msgid, sender=from_addr, to=to_addr or self.address, subject=subject,
                        sent_at=when, received_at=when,
                        headers={"Received": "from mx.example (10.0.0.1) by outlook.office365.com",
                                 "Message-ID": f"<{msgid}>", "From": from_addr, "To": to_addr or self.address,
                                 "Subject": subject, "X-MS-Exchange-Organization-SCL": "1", **headers},
                        content=content)
        self.store.append(stored)
        return stored
