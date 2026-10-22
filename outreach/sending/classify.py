"""What an inbound message is: his own, a person, an automatic reply, a bounce or a delay notice.

Pure functions over a `MessageMeta`. The rules are ported from the old mailer (`mail/sender.py` `BOUNCE`,
`BOUNCE_SUBJ`, `AUTO_SUBJ`, `is_auto`), with two differences: a delay notice is its own kind (the old subject
rule called "Delivery Status Notification (Delay)" a bounce), and his own messages are recognised first.
"""
import re
from collections.abc import Mapping
from dataclasses import dataclass
from email.utils import getaddresses, parseaddr
from typing import Literal

from outreach.sending.mail import MessageMeta

__all__ = ["Inbound", "classify", "dsn_class", "match_unthreaded", "failed_recipients"]

BOUNCE = re.compile(r"mailer-daemon|postmaster|mail delivery (sub)?system", re.I)
# Anchored at the start: "Re: Delivery failure question" from a person is not a bounce notice.
BOUNCE_SUBJ = re.compile(r"^\s*(delivery status notification|undeliverable|undelivered|returned mail|delivery failure)",
                         re.I)
AUTO_SUBJ = re.compile(r"^\s*(automatic reply|auto(matic)?[- ]?reply|autoreply|out of (the )?office|ooo\b|"
                       r"away from (the )?office|abwesenheit|absence|on leave|on vacation)", re.I)
# The subject forms of a "still trying" notice (Gmail, Exim, Postfix). A bounce can echo a subject with "delayed"
# in it, so the word alone proves nothing.
DELAY_SUBJ = re.compile(r"^\s*(delivery status notification \(delay\)|warning: .*delayed|delayed mail)", re.I)
MIN_SUBJECT = 20
AUTO_PRECEDENCE = ("auto_reply", "bulk", "junk", "list")

# An SMTP enhanced status code stands alone: not inside a longer number, not followed by more digits.
_CODE = r"(?<![\d.])(?:{})(?!\d)"
_LIMIT = re.compile(r"reached a limit for sending", re.I)
_POLICY_WORDS = re.compile(r"unsolicited|spam|policy", re.I)
_POLICY_WITH_WORDS = re.compile(_CODE.format(r"5\.7\.1|4\.7\.28"))
_POLICY_ALWAYS = re.compile(_CODE.format(r"5\.7\.511|4\.7\.5\d\d"))
_HARD_CODE = re.compile(_CODE.format(r"5\.1\.1|5\.1\.10|5\.4\.1"))
_HARD_WORDS = re.compile(r"address not found|does not exist", re.I)


@dataclass(frozen=True)
class Inbound:
    kind: Literal["ours", "human", "auto_reply", "bounce", "delay", "other"]
    dsn_class: str | None
    failed_recipient: str | None


def failed_recipients(meta: MessageMeta) -> list[str]:
    """Every address in `X-Failed-Recipients`, lower case, in order."""
    value = meta.headers.get("x-failed-recipients") or ""
    return [addr.strip().lower() for _, addr in getaddresses([value]) if addr.strip()]


def dsn_class(meta: MessageMeta) -> str:
    """How a bounce reads, from its snippet: `limit`, `policy`, `hard` or `unknown` (counted as a bounce)."""
    text = meta.snippet or ""
    if _LIMIT.search(text):
        return "limit"
    if _POLICY_ALWAYS.search(text) or (_POLICY_WITH_WORDS.search(text) and _POLICY_WORDS.search(text)):
        return "policy"
    if _HARD_CODE.search(text) or _HARD_WORDS.search(text):
        return "hard"
    return "unknown"


def _is_auto(meta: MessageMeta) -> bool:
    h = meta.headers
    if h.get("auto-submitted", "no").strip().lower() not in ("no", ""):
        return True
    if "x-autoreply" in h or "x-autorespond" in h or "x-auto-response-suppress" in h:
        return True
    if h.get("precedence", "").strip().lower() in AUTO_PRECEDENCE:
        return True
    return bool(AUTO_SUBJ.search(meta.subject or ""))


def _gmail_key(address: str) -> str:
    """The mailbox an address reaches: Gmail ignores dots and a `+tag` in the local part and treats googlemail.com
    as gmail.com. Any other address is compared as written (lower case)."""
    local, _, domain = address.strip().lower().partition("@")
    if domain in ("gmail.com", "googlemail.com"):
        return local.split("+", 1)[0].replace(".", "") + "@gmail.com"
    return address.strip().lower()


def _machine_made(meta: MessageMeta) -> bool:
    """A header only a mail system adds to a delivery notice: an Auto-Submitted other than `no`, or
    X-Failed-Recipients."""
    h = meta.headers
    return h.get("auto-submitted", "no").strip().lower() not in ("no", "") or bool(h.get("x-failed-recipients"))


def classify(meta: MessageMeta, account: str) -> Inbound:
    """`ours` (from `account`, any display name or case, or a dotted or `+tag` form of a Gmail address), then a
    bounce or delay notice (a daemon or postmaster sender, or a bounce subject with a machine-made header), then
    an automatic message, else `human`. Whether a human message matters is decided by the thread it is in, not here."""
    sender = parseaddr(meta.from_addr or "")[1].strip()
    if sender and _gmail_key(sender) == _gmail_key(account):
        return Inbound("ours", None, None)
    subject = meta.subject or ""
    if BOUNCE.search(meta.from_addr or "") or (BOUNCE_SUBJ.search(subject) and _machine_made(meta)):
        failed = failed_recipients(meta)
        klass = dsn_class(meta)
        if DELAY_SUBJ.search(subject) and klass == "unknown":
            return Inbound("delay", None, failed[0] if failed else None)
        return Inbound("bounce", klass, failed[0] if failed else None)
    if _is_auto(meta):
        return Inbound("auto_reply", None, None)
    return Inbound("human", None, None)


def match_unthreaded(meta: MessageMeta, tracked: Mapping[str, int], subjects: Mapping[str, int]) -> int | None:
    """The touch an unthreaded DSN belongs to. `tracked` maps a person's lower-case address to a touch id and
    `subjects` maps a sent subject to a touch id. A failed recipient equal to a tracked address wins; else a sent
    subject that appears exactly (same case and spacing) in the DSN subject, the longest when several do. A sent
    subject shorter than 20 characters matches only as the whole DSN subject: "Hi" is in too many subjects."""
    for address in failed_recipients(meta):
        if address in tracked:
            return tracked[address]
    subject = meta.subject or ""
    best = None
    for sent_subject, touch_id in subjects.items():
        whole = sent_subject == subject
        if sent_subject and (whole or (len(sent_subject) >= MIN_SUBJECT and sent_subject in subject)) \
                and (best is None or len(sent_subject) > len(best[0])):
            best = (sent_subject, touch_id)
    return best[1] if best else None
