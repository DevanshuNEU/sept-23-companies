"""A scriptable, in-memory Gmail and a clock that only moves when a test moves it.

Every sending test and the simulator use these; nothing here opens a socket or a file. `FakeGmail.sent` is the
only count of emails that "went out". Ids are deterministic (`m1`, `t1`, ...) so tests can name them.

Gmail behaviour copied on purpose:
- a follow-up joins the thread only when its threadId is known and its subject matches (after `Re:`), as the
  Gmail API's threading rule says; an unknown threadId is a 404;
- `history` returns messages added after the cursor (exclusive) and the mailbox's newest history id;
- Gmail's own bounce (DSN) shape: `Mail Delivery Subsystem <mailer-daemon@googlemail.com>`,
  `Delivery Status Notification (Failure)` or `(Delay)`, `X-Failed-Recipients` on failures only.
"""
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from email.utils import formataddr, parseaddr
from typing import Literal

from outreach.sending.mail import (META_HEADERS, Added, GmailError, HistoryExpired, HistoryPage, MailClient,
                                   MessageMeta, Outgoing, Profile, Sent, check_outgoing, miss_is_proof,
                                   normalise_message_id, page_reaches, settle_minutes)

__all__ = ["FakeClock", "FakeGmail", "DSN_FROM"]

DSN_FROM = "Mail Delivery Subsystem <mailer-daemon@googlemail.com>"
FAIL_SUBJECT = "Delivery Status Notification (Failure)"
DELAY_SUBJECT = "Delivery Status Notification (Delay)"
DSN_TYPE = "multipart/report; report-type=delivery-status"
_SNIPPETS = {
    "hard": ("Address not found Your message wasn't delivered to {to} because the address couldn't be found, or "
             "is unable to receive mail. The response from the remote server was: 550 5.1.1 The email account "
             "that you tried to reach does not exist."),
    "policy": ("Message blocked Your message to {to} has been blocked. The response from the remote server was: "
               "550 5.7.1 Message rejected as unsolicited mail by the recipient's policy."),
    "delay": ("Message not delivered yet There was a temporary problem delivering your message to {to}. Gmail "
              "will retry for 47 more hours. You'll be notified if the delivery fails permanently."),
    "limit": ("You have reached a limit for sending mail. Your message was not sent. Learn more about sending "
              "limits."),
}


class FakeClock:
    """`clock()` is now (aware UTC); it moves only through advance and set."""

    def __init__(self, start: datetime):
        self._now = self._aware(start)

    @staticmethod
    def _aware(at: datetime) -> datetime:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("FakeClock needs an aware datetime")
        return at

    def __call__(self) -> datetime:
        return self._now

    def advance(self, **timedelta_kwargs) -> None:
        self._now = self._now + timedelta(**timedelta_kwargs)

    def set(self, at: datetime) -> None:
        self._now = self._aware(at)


def _subject_key(subject: str) -> str:
    s = " ".join((subject or "").split()).casefold()
    while True:
        for prefix in ("re:", "fwd:", "fw:"):
            if s.startswith(prefix):
                s = s[len(prefix):].lstrip()
                break
        else:
            return s


class FakeGmail(MailClient):
    """In-memory Gmail. `probe(method_name)` runs at the start of every client method, before it does anything."""

    def __init__(self, *, clock: Callable[[], datetime], account: str = "devanshurajesh@gmail.com",
                 probe: Callable[[str], None] | None = None, reconcile_after_minutes: int | None = None):
        self.clock = clock
        self.account = account
        self.probe = probe
        self.settle_minutes = settle_minutes(reconcile_after_minutes)
        self.sent: list[tuple[Outgoing, Sent, datetime]] = []
        self.sent_box: list[MessageMeta] = []        # what find_sent scans, oldest first
        self.calls: list[str] = []
        self.find_sent_page_cap: int | None = 10     # pages find_sent may read, as the real client; None unlimited
        self.find_sent_page_size = 50
        self.rewrite_message_ids = False             # Gmail stores sends under its own Message-ID
        self._lands_at: dict[str, datetime] = {}     # a Sent entry find_sent cannot see before this time
        self._messages: dict[str, MessageMeta] = {}
        self._threads: dict[str, list[str]] = {}
        self._history: list[tuple[int, str]] = []    # (history id, gmail message id)
        self._history_id = 1000
        self._next_message = 0
        self._next_thread = 0
        self._next_rfc = 0
        self._send_failures: list[tuple[BaseException, bool, bool, timedelta | None]] = []
        self._profile_error: BaseException | None = None
        self._expire_next_history = False

    # -- scripting (not client calls: no probe, not in calls) ---------------------------------------------------

    def fail_next_send(self, error: BaseException, *, accepted: bool = False, in_sent: bool = True,
                       lands_after: timedelta | None = None) -> None:
        """The next send raises `error`. With `accepted=True` the email goes out first, as a timeout after Gmail
        took it, and lands in Sent (`lands_after` later, or never with `in_sent=False`). Calls queue up."""
        self._send_failures.append((error, accepted, in_sent, lands_after))

    def fail_profile(self, error: BaseException | None) -> None:
        """Every profile call raises `error` until fail_profile(None): a dead token stays dead."""
        self._profile_error = error

    def expire_history(self) -> None:
        """The next history call raises HistoryExpired, as Gmail's 404 for a cursor that is too old."""
        self._expire_next_history = True

    def change_stored(self, gmail_message_id: str, *, message_id: str | None = None,
                      labels: tuple[str, ...] | None = None) -> None:
        """Gmail now shows a stored message with another Message-ID header or labels: a rewrite the send itself did
        not report (the real client's read back can fail and still report the send), or a message moved out of
        Sent. Sent scans and `message` see the change."""
        old = self._messages[gmail_message_id]
        headers = dict(old.headers)
        if message_id is not None:
            headers["message-id"] = message_id
        new = replace(old, headers=headers, labels=old.labels if labels is None else labels)
        self._messages[gmail_message_id] = new
        self.sent_box = [new if m.gmail_message_id == gmail_message_id else m for m in self.sent_box]

    def add_to_sent(self, to: str, subject: str, *, message_id: str, at: datetime | None = None,
                    thread_id: str | None = None) -> MessageMeta:
        """Put a message in Sent that this fake did not send (his own mail, or a decoy). Not counted in `sent`."""
        meta = self._store(thread_id or self._new_thread(), formataddr(("Devanshu", self.account)), to, subject,
                           ("SENT",), {"message-id": message_id, "content-type": 'text/plain; charset="utf-8"'},
                           "", at)
        self.sent_box.append(meta)
        return meta

    def deliver_reply(self, to: Sent, from_addr: str, *, subject: str | None = None,
                      headers: Mapping[str, str] | None = None, snippet: str = "",
                      new_thread: bool = False) -> MessageMeta:
        """A reply to `to`. `new_thread=True` is a reply Gmail filed in a thread of its own (a changed subject, or
        a client that drops the thread): its In-Reply-To and References still name our email."""
        original = self._messages[to.gmail_message_id]
        return self._inbound(self._new_thread() if new_thread else to.thread_id, from_addr,
                             subject or "Re: " + original.subject, self._reply_headers(to, from_addr, headers), snippet)

    def deliver_auto_reply(self, to: Sent, from_addr: str, *, subject: str = "Automatic reply",
                           new_thread: bool = False) -> MessageMeta:
        return self._inbound(self._new_thread() if new_thread else to.thread_id, from_addr, subject,
                             self._reply_headers(to, from_addr, {"auto-submitted": "auto-replied"}), "")

    def deliver_bounce(self, to: Sent, *, dsn: Literal["hard", "policy", "delay", "limit"] = "hard",
                       threaded: bool = True) -> MessageMeta:
        recipient = parseaddr(self._messages[to.gmail_message_id].to_addr)[1]
        headers = {"message-id": self._new_rfc("googlemail.com"), "content-type": DSN_TYPE}
        if dsn != "delay":
            headers["x-failed-recipients"] = recipient
        if threaded:
            headers["in-reply-to"] = to.rfc_message_id
            headers["references"] = to.rfc_message_id
        thread_id = to.thread_id if threaded else self._new_thread()
        return self._inbound(thread_id, DSN_FROM, DELAY_SUBJECT if dsn == "delay" else FAIL_SUBJECT, headers,
                             _SNIPPETS[dsn].format(to=recipient), checked=True)

    def deliver_unrelated(self, from_addr: str, subject: str) -> MessageMeta:
        domain = parseaddr(from_addr)[1].rpartition("@")[2] or "example.com"
        return self._inbound(self._new_thread(), from_addr, subject, {"message-id": self._new_rfc(domain)}, "",
                             checked=True)

    # -- the client ---------------------------------------------------------------------------------------------

    def _enter(self, name: str) -> None:
        self.calls.append(name)
        if self.probe is not None:
            self.probe(name)

    def profile(self) -> Profile:
        self._enter("profile")
        if self._profile_error is not None:
            raise self._profile_error
        return Profile(email=self.account, history_id=str(self._history_id))

    def send(self, m: Outgoing) -> Sent:
        self._enter("send")
        try:
            check_outgoing(m)
        except ValueError as err:
            raise GmailError("bad_request", None, f"refused before sending: {err}") from None
        if m.thread_id is not None and m.thread_id not in self._threads:
            raise GmailError("bad_request", 404, "Requested entity was not found.")
        failure = self._send_failures.pop(0) if self._send_failures else None
        if failure is not None and not failure[1]:
            raise failure[0]
        thread_id = m.thread_id
        if thread_id is not None:
            first = self._messages[self._threads[thread_id][0]]
            if _subject_key(first.subject) != _subject_key(m.subject):
                thread_id = None                       # Gmail threads only when the subjects match
        stored_id = m.message_id
        if self.rewrite_message_ids:
            self._next_rfc += 1
            stored_id = f"<rewritten{self._next_rfc}@mail.gmail.com>"
        headers = {"message-id": stored_id, "content-type": 'text/plain; charset="utf-8"'}
        if m.in_reply_to:
            headers["in-reply-to"] = m.in_reply_to
        refs = m.references or ((m.in_reply_to,) if m.in_reply_to else ())
        if refs:
            headers["references"] = " ".join(refs)
        meta = self._store(thread_id or self._new_thread(), formataddr((m.from_name, m.from_addr)), m.to,
                           m.subject, ("SENT",), headers, " ".join(m.body.split())[:100], None)
        sent = Sent(gmail_message_id=meta.gmail_message_id, thread_id=meta.thread_id, rfc_message_id=stored_id)
        self.sent.append((m, sent, self.clock()))
        if failure is None or failure[2]:
            self.sent_box.append(meta)
            if failure is not None and failure[3] is not None:
                self._lands_at[meta.gmail_message_id] = self.clock() + failure[3]
        if failure is not None:
            raise failure[0]
        if stored_id != m.message_id:
            raise GmailError("message_id_rewritten", None, "Gmail stored the email under another Message-ID",
                             sent=sent)
        return sent

    def history(self, start_history_id: str) -> HistoryPage:
        self._enter("history")
        if self._expire_next_history:
            self._expire_next_history = False
            raise HistoryExpired("history cursor expired")
        if not start_history_id:
            raise HistoryExpired("no history cursor")
        if not start_history_id.isdigit():
            raise GmailError("bad_request", 400, "Invalid startHistoryId")
        start = int(start_history_id)
        added = tuple(Added(gmail_message_id=mid, thread_id=self._messages[mid].thread_id,
                            labels=self._messages[mid].labels, history_id=str(hid))
                      for hid, mid in self._history if hid > start)
        return HistoryPage(added=added, history_id=str(self._history_id))

    def message(self, gmail_message_id: str) -> MessageMeta:
        self._enter("message")
        try:
            return self._messages[gmail_message_id]
        except KeyError:
            raise GmailError("bad_request", 404, "Requested entity was not found.") from None

    def thread(self, thread_id: str) -> list[MessageMeta]:
        self._enter("thread")
        if thread_id not in self._threads:
            raise GmailError("bad_request", 404, "Requested entity was not found.")
        return [self._messages[mid] for mid in self._threads[thread_id]]

    def find_sent(self, rfc_message_id: str, not_before_ms: int, *, send_returned_ms: int) -> tuple[Sent | None, bool]:
        """Newest first, a page at a time, by the same rules as the real client (see MailClient.find_sent)."""
        self._enter("find_sent")
        now = self.clock()
        visible = [(i, meta) for i, meta in enumerate(self.sent_box)
                   if self._lands_at.get(meta.gmail_message_id, now) <= now]
        newest_first = sorted(visible, key=lambda p: (p[1].internal_ms, p[0]), reverse=True)
        box = [meta for _, meta in newest_first]
        wanted = normalise_message_id(rfc_message_id)
        size, pages, complete = self.find_sent_page_size, 0, True
        for start in range(0, len(box), size):
            if self.find_sent_page_cap is not None and pages >= self.find_sent_page_cap:
                complete = False
                break
            pages += 1
            page = box[start:start + size]
            for meta in page:
                if normalise_message_id(meta.headers.get("message-id")) == wanted:
                    return Sent(meta.gmail_message_id, meta.thread_id, rfc_message_id), True
            if page_reaches([meta.internal_ms for meta in page], not_before_ms):
                break
        now_ms = int(now.timestamp() * 1000)
        return None, miss_is_proof(complete, now_ms, send_returned_ms, self.settle_minutes)

    # -- internals ----------------------------------------------------------------------------------------------

    def _new_thread(self) -> str:
        self._next_thread += 1
        return f"t{self._next_thread}"

    def _new_rfc(self, domain: str) -> str:
        self._next_rfc += 1
        return f"<in{self._next_rfc}@{domain}>"

    def _reply_headers(self, to: Sent, from_addr: str, extra: Mapping[str, str] | None) -> dict[str, str]:
        domain = parseaddr(from_addr)[1].rpartition("@")[2] or "example.com"
        headers = {"message-id": self._new_rfc(domain), "in-reply-to": to.rfc_message_id,
                   "references": to.rfc_message_id}
        headers.update({k.lower(): v for k, v in (extra or {}).items()})
        return headers

    def _inbound(self, thread_id: str, from_addr: str, subject: str, headers: Mapping[str, str], snippet: str,
                 *, checked: bool = False) -> MessageMeta:
        if not checked:
            unknown = set(headers) - set(META_HEADERS)
            if unknown:
                raise ValueError(f"FakeGmail carries only the metadata headers, not {sorted(unknown)}")
        return self._store(thread_id, from_addr, self.account, subject, ("INBOX", "UNREAD"), headers, snippet,
                           None)

    def _store(self, thread_id: str, from_addr: str, to_addr: str, subject: str, labels: tuple[str, ...],
               headers: Mapping[str, str], snippet: str, at: datetime | None) -> MessageMeta:
        self._next_message += 1
        when = at or self.clock()
        meta = MessageMeta(gmail_message_id=f"m{self._next_message}", thread_id=thread_id, from_addr=from_addr,
                           to_addr=to_addr, subject=subject, internal_ms=int(when.timestamp() * 1000),
                           labels=labels, headers=dict(headers), snippet=snippet)
        self._messages[meta.gmail_message_id] = meta
        self._threads.setdefault(thread_id, []).append(meta.gmail_message_id)
        self._history_id += 1
        self._history.append((self._history_id, meta.gmail_message_id))
        return meta
