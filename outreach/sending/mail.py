"""The mail client contract: the only way the sender reaches Gmail.

Two clients keep this contract: `outreach.sending.gmail.GmailClient` (the real one, live checkout only) and
`outreach.sending.fakegmail.FakeGmail` (every test and the simulator). The sender never calls either inside a
store transaction.

Errors: every client method may raise `GmailError`, whose `kind` says what went wrong. Its `detail` is at most
300 characters and never holds a header, a token or a secret; `GmailError` scrubs it on the way in, so a caller
that passes a raw body by mistake still cannot log a token.
"""
import re
from collections.abc import Mapping
from dataclasses import dataclass
from email.utils import getaddresses
from typing import Literal, Protocol, runtime_checkable

__all__ = [
    "Outgoing", "Sent", "Profile", "MessageMeta", "Added", "HistoryPage", "HistoryExpired", "GmailError", "KINDS",
    "classify_http", "RETRYABLE", "MailClient", "META_HEADERS", "check_outgoing", "normalise_message_id",
    "MESSAGE_ID", "CLOCK_SKEW_MS", "settle_minutes", "page_reaches", "miss_is_proof",
]

# The only header names a MessageMeta carries (lower case). The metadata scope cannot read bodies; these are
# what classify (Task 5) needs to tell a person from an out-of-office or a bounce.
META_HEADERS = (
    "auto-submitted", "x-autoreply", "x-autorespond", "precedence", "x-auto-response-suppress",
    "message-id", "in-reply-to", "references", "x-failed-recipients", "content-type",
)


@dataclass(frozen=True)
class Outgoing:
    """One plain-text email to one person. `message_id` is the Message-ID claimed in the store before the call."""
    to: str
    subject: str
    body: str
    from_name: str
    from_addr: str
    message_id: str
    thread_id: str | None
    in_reply_to: str | None
    references: tuple[str, ...]


@dataclass(frozen=True)
class Sent:
    gmail_message_id: str
    thread_id: str
    rfc_message_id: str


@dataclass(frozen=True)
class Profile:
    email: str
    history_id: str


@dataclass(frozen=True)
class MessageMeta:
    gmail_message_id: str
    thread_id: str
    from_addr: str
    to_addr: str
    subject: str
    internal_ms: int
    labels: tuple[str, ...]
    headers: Mapping[str, str]          # lower-case names, only META_HEADERS
    snippet: str


@dataclass(frozen=True)
class Added:
    """One message added to the mailbox. `history_id` is the id of the history record that added it (Gmail's
    `history[].id`), so a read that stops part way can move the cursor to the last record it finished; None when
    the client does not know it."""
    gmail_message_id: str
    thread_id: str
    labels: tuple[str, ...]
    history_id: str | None = None


@dataclass(frozen=True)
class HistoryPage:
    added: tuple[Added, ...]
    history_id: str


class HistoryExpired(Exception):
    """The history cursor is empty, too old or unknown: read the tracked threads once, then take a new cursor."""


KINDS = frozenset({"auth", "rate_limit", "daily_limit", "account_blocked", "bad_request", "server", "network",
                   "timeout", "message_id_rewritten"})

# A failure of these kinds says nothing about the message itself (it never went out because of the account, the
# token, Gmail or the network), so a send proven missing from Sent may be tried again. Not retryable: a bad request
# (a bad address or header would fail the same way again) and message_id_rewritten (the email went out; Gmail
# stored it under another Message-ID, so a Sent scan by the claimed id would never find it).
RETRYABLE = frozenset({"rate_limit", "daily_limit", "account_blocked", "auth", "server", "network", "timeout"})

DETAIL_LIMIT = 300

_SECRET_PATTERNS = (
    (re.compile(r"(?i)\bbearer\s+[^\s\"',;]+"), "Bearer [redacted]"),
    (re.compile(r"(?i)[\"']?\b(access_token|refresh_token|id_token|client_secret|code_verifier|authorization)"
                r"[\"']?\s*[:=]\s*[\"']?[^\s\"',}]+"), r"\1: [redacted]"),
    (re.compile(r"ya29\.[\w.\-]+"), "[redacted]"),          # Google access tokens
    (re.compile(r"1//[\w.\-]+"), "[redacted]"),             # Google refresh tokens
)


def scrub(text: str, *secrets: str) -> str:
    """One line, at most 300 characters, with every known secret shape and each given secret removed."""
    out = str(text)
    for secret in secrets:
        if secret:
            out = out.replace(secret, "[redacted]")
    for pattern, repl in _SECRET_PATTERNS:
        out = pattern.sub(repl, out)
    out = " ".join(out.split())
    return out[:DETAIL_LIMIT]


class GmailError(Exception):
    """A Gmail call failed. `kind` is one of KINDS; `status` the HTTP status when there was one."""

    def __init__(self, kind: Literal["auth", "rate_limit", "daily_limit", "account_blocked", "bad_request",
                                     "server", "network", "timeout", "message_id_rewritten"],
                 status: int | None = None, detail: str = "", *, sent: "Sent | None" = None):
        if kind not in KINDS:
            raise ValueError(f"unknown Gmail error kind {kind!r}")
        if (kind == "message_id_rewritten") != (sent is not None):
            raise ValueError("message_id_rewritten, and only it, carries the Sent ids of the email that went out")
        self.kind = kind
        self.status = status
        self.detail = scrub(detail)
        self.sent = sent
        super().__init__(f"Gmail {kind}" + (f" {status}" if status is not None else "") +
                         (f": {self.detail}" if self.detail else ""))

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE


def classify_http(status: int, body: str) -> str:
    """The GmailError kind for an HTTP error status and its body.

    Gmail reports a sending limit as 403 (or 429) with a reason in the body, so 403 is read by its reason: a daily
    limit, a rate limit, a sign-in or scope problem, or else the account is not allowed to send. A status this
    table does not name (not 4xx) is `server`.
    """
    text = (body or "").lower()
    if status == 401:
        return "auth"
    if status == 403:
        if "dailylimitexceeded" in text or "limit for sending" in text:
            return "daily_limit"
        if "userratelimitexceeded" in text or "ratelimitexceeded" in text:
            return "rate_limit"
        if "insufficientpermissions" in text or "invalid_grant" in text:
            return "auth"
        return "account_blocked"
    if status == 429:
        return "rate_limit"
    if 400 <= status < 500:
        return "bad_request"
    return "server"


# One Message-ID: <dot-atom@dot-atom>, RFC 5322 dot-atom ASCII on both sides (make_msgid's shape). Anything else
# (non-ASCII, quotes, parentheses, control characters, empty or doubled dots) is refused before it is claimed.
_ATOM = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
MESSAGE_ID = re.compile(rf"<{_ATOM}(?:\.{_ATOM})*@{_ATOM}(?:\.{_ATOM})*>")


def normalise_message_id(value: str | None) -> str:
    """The form both clients compare Message-IDs in: header folding and surrounding whitespace removed. A
    Message-ID never holds whitespace, so removing all of it cannot join two different ids into one."""
    return "".join((value or "").split())


# Gmail's internalDate is Gmail's clock and the claim time is the Mac's. A Sent scan stops only at a message older
# than the claim by this much, so a Mac clock running fast cannot end a scan before the message it is looking for.
CLOCK_SKEW_MS = 10 * 60 * 1000


def settle_minutes(value: int | None = None) -> int:
    """How long after a failed send returned a Sent miss starts to count as proof (config reconcile_after_minutes)."""
    if value is not None:
        return value
    from outreach.sending.config import load_config
    return load_config().reconcile_after_minutes


def page_reaches(dates: list[int | None], not_before_ms: int) -> bool:
    """True when the last dated message on a Sent page (newest first) is older than the claim, less the skew margin.
    One stray old message earlier on the page does not end the scan; a page with no dates proves nothing."""
    dated = [d for d in dates if d is not None]
    return bool(dated) and dated[-1] < not_before_ms - CLOCK_SKEW_MS


def miss_is_proof(complete: bool, now_ms: int, send_returned_ms: int, settle: int) -> bool:
    """A miss proves the email did not go out only after a complete scan begun `settle` minutes after the send
    returned: Gmail can take a while to show an accepted message in Sent."""
    return complete and now_ms >= send_returned_ms + settle * 60_000


def check_outgoing(m: Outgoing) -> None:
    """Refuse anything but one plain email to one person with a claimed Message-ID. Raises ValueError.

    Both clients run this before anything leaves, so the fake refuses exactly what the real client refuses.
    """
    fields = {"to": m.to, "subject": m.subject, "from_name": m.from_name, "from_addr": m.from_addr,
              "message_id": m.message_id, "thread_id": m.thread_id or "", "in_reply_to": m.in_reply_to or "",
              "references": " ".join(m.references)}
    for name, value in fields.items():
        if "\r" in value or "\n" in value:
            raise ValueError(f"{name} holds a line break")
    to = (m.to or "").strip()
    if not to or "," in to or ";" in to:
        raise ValueError("an email goes to exactly one address")
    parsed = [addr for _, addr in getaddresses([to]) if addr]
    if len(parsed) != 1 or "@" not in parsed[0]:
        raise ValueError("an email goes to exactly one address")
    if "@" not in (m.from_addr or ""):
        raise ValueError("from_addr is not an address")
    if not MESSAGE_ID.fullmatch(m.message_id or ""):
        raise ValueError("message_id is empty or not one Message-ID")
    if m.in_reply_to is not None and not MESSAGE_ID.fullmatch(m.in_reply_to):
        raise ValueError("in_reply_to is not one Message-ID")
    for ref in m.references:
        if not MESSAGE_ID.fullmatch(ref or ""):
            raise ValueError("references holds something that is not one Message-ID")
    if not (m.subject or "").strip() or not (m.body or "").strip():
        raise ValueError("subject and body must not be empty")


@runtime_checkable
class MailClient(Protocol):
    """What the sender needs from Gmail. Any method may raise GmailError."""

    def profile(self) -> Profile:
        """The signed-in address and the mailbox's current history id. Proves the token works."""
        ...

    def send(self, m: Outgoing) -> Sent:
        """Send one email. An exception means the outcome is unclear: settle it with find_sent. The one exception
        that is not unclear is GmailError kind message_id_rewritten: the email went out, and `err.sent` holds the
        ids Gmail stored it under (record it as sent, never retry it)."""
        ...

    def history(self, start_history_id: str) -> HistoryPage:
        """Messages added after `start_history_id`. Raises HistoryExpired for an empty, old or unknown cursor."""
        ...

    def message(self, gmail_message_id: str) -> MessageMeta:
        ...

    def thread(self, thread_id: str) -> list[MessageMeta]:
        """Every message in the thread, oldest first."""
        ...

    def find_sent(self, rfc_message_id: str, not_before_ms: int, *, send_returned_ms: int) -> tuple[Sent | None, bool]:
        """Look in Sent for the message with exactly this Message-ID (never by To or Subject).

        `not_before_ms` is the claim time; `send_returned_ms` is when the failed send call returned. After a crash
        with no record of that, pass the time the stale `sending` row was first found (recorded when it moves to
        `send_unknown`), never the claim time: the Mac can sleep between the claim and the send. Both are the
        Mac's clock. A hit is (Sent, True) at any time. A miss
        is (None, True), proof the email did not go out, only when the scan reached a page whose last dated message
        is older than the claim less CLOCK_SKEW_MS, or the end of Sent, and it began at least `settle_minutes`
        after the send returned. Any other miss is (None, False).
        """
        ...
