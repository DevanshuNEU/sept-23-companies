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
_POLICY_WITH_WORDS = re.compile(_CODE.format(r"5\.7\.1"))
_POLICY_ALWAYS = re.compile(_CODE.format(r"5\.7\.511"))
# A 4.x.x code is temporary ("try again later", 4.7.28 and 4.7.5xx included): the server keeps trying, so it is a
# delay notice, never a bounce, unless a permanent 5.x.x code is in the same notice.
_TEMPORARY = re.compile(_CODE.format(r"4\.\d{1,3}\.\d{1,3}"))
_PERMANENT = re.compile(_CODE.format(r"5\.\d{1,3}\.\d{1,3}"))
_HARD_CODE = re.compile(_CODE.format(r"5\.1\.1|5\.1\.10|5\.4\.1"))
_HARD_WORDS = re.compile(r"address not found|does not exist", re.I)
# The provider refused the ACCOUNT, not the recipient: Microsoft's 5.7.708 (traffic from this IP), 5.7.705 (tenant
# over its limit) and 5.1.8 (bad outbound sender), or the words "restricted from sending". The address is fine.
_BLOCK = re.compile(_CODE.format(r"5\.7\.708|5\.7\.705|5\.1\.8") + r"|restricted from sending", re.I)
# A Microsoft Exchange system sender, which is not a postmaster or daemon address.
_EXCHANGE_FROM = re.compile(r"MicrosoftExchange[0-9a-f]+@", re.I)
_REPORT = re.compile(r"multipart/report\b[^;]*;[^\n]*report-type\s*=\s*\"?delivery-status", re.I)
# Where a notice names the address that failed, most reliable first. The text is whitespace-collapsed.
_ADDR = r"([^\s<>()\"',;]+@[^\s<>()\"',;]+)"
_FAILED_IN_TEXT = tuple(re.compile(p, re.I) for p in (
    r"Final-Recipient:\s*rfc822;\s*" + _ADDR + r"(?=[\s.,;]|$)",
    r"Your message to\s+" + _ADDR + r"\s+couldn't be delivered",
    _ADDR + r"\s+Remote Server returned",
    r"<" + _ADDR + r">\.\.\.\s*User unknown",
    r"had permanent fatal errors\W*<?" + _ADDR + r">?(?=[\s>.,;]|$)",
))
_STATUS_LINE = re.compile(r"Status:\s*([245])\.\d{1,3}\.\d{1,3}(?!\d)", re.I)


@dataclass(frozen=True)
class Inbound:
    kind: Literal["ours", "human", "auto_reply", "bounce", "delay", "other"]
    dsn_class: str | None
    failed_recipient: str | None


def failed_recipients(meta: MessageMeta) -> list[str]:
    """Every address in `X-Failed-Recipients`, lower case, in order; with no such header, the one address the
    notice's text names (`text_recipient`)."""
    value = meta.headers.get("x-failed-recipients") or ""
    found = [addr.strip().lower() for _, addr in getaddresses([value]) if addr.strip()]
    if found:
        return found
    named = text_recipient(meta.snippet or "")
    return [named] if named else []


def text_recipient(text: str) -> str | None:
    """The failed address a Microsoft or Proofpoint notice names in its text: a `Final-Recipient:` line, "Your
    message to X couldn't be delivered", "X Remote Server returned", "<X>... User unknown", then the address
    after "had permanent fatal errors". Lower case; None when none of those is there."""
    for pattern in _FAILED_IN_TEXT:
        hit = pattern.search(text)
        if hit:
            return hit.group(1).strip(".,;").lower()
    return None


def dsn_class(meta: MessageMeta) -> str:
    """How a bounce reads, from its snippet: `limit`, `provider_block`, `policy`, `hard` or `unknown` (counted as a
    bounce)."""
    text = meta.snippet or ""
    if _LIMIT.search(text):
        return "limit"
    if _BLOCK.search(text):
        return "provider_block"
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


def _is_notice(meta: MessageMeta) -> bool:
    """Reads as a delivery notice: a bounce subject, or a multipart/report delivery-status body."""
    return bool(BOUNCE_SUBJ.search(meta.subject or "")
                or _REPORT.search(meta.headers.get("content-type") or ""))


def classify(meta: MessageMeta, account: str) -> Inbound:
    """`ours` (from `account`, any display name or case, or a dotted or `+tag` form of a Gmail address), then a
    bounce or delay notice (a daemon or postmaster sender, or a bounce subject with a machine-made header), then
    an automatic message, else `human`. Whether a human message matters is decided by the thread it is in, not here."""
    sender = parseaddr(meta.from_addr or "")[1].strip()
    if sender and _gmail_key(sender) == _gmail_key(account):
        return Inbound("ours", None, None)
    subject = meta.subject or ""
    sender_text = meta.from_addr or ""
    if BOUNCE.search(sender_text) or (_is_notice(meta) and (_machine_made(meta) or _EXCHANGE_FROM.search(sender_text))):
        failed = failed_recipients(meta)
        klass = dsn_class(meta)
        text = meta.snippet or ""
        # A `Status:` line decides when the notice has one; else any 4.x.x code with no 5.x.x beside it.
        status = _STATUS_LINE.search(text)
        temporary = klass not in ("limit", "provider_block") and (
            status.group(1) == "4" if status else bool(_TEMPORARY.search(text) and not _PERMANENT.search(text)))
        if (DELAY_SUBJ.search(subject) and klass == "unknown") or temporary:
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
