"""The approve checks (#9): the safety blocks and the style warns of one email draft, read on current data.

Standard library only: the API (approve), the sender (before every claim) and the MCP server (on every save) all call
`run_checks`, so there is one copy of the rules. The link, banned-phrase, dash and similarity rules are a copy of
`mail/check_draft.py` (not imported: `mail/` retires at the switch).

The checks only read. They never approve, send or change a message; the caller stores what they return.

Blocks are #9's safety list: no mail server, more than 2 links, a link shortener, near-identical to a colleague's
draft, a no-go on the person or the company, a person who said no or asked to stop, a company closed for no
sponsorship. Two more come from his 2026-11-09 rulings: a markdown link (email is plain text, so the brackets would
arrive as typed) and any visa, OPT or sponsorship mention, for every reader. An address with no usable domain counts
as no mail server. Placeholder text (TODO, ASK:, {{ }}) blocks,
as it does in `mail/check_draft.py`. Two more block because a send could not be checked or could not thread: a
banned phrases list that cannot be read (fail closed), and a follow-up subject that is not `Re: ` plus the first
email's subject (#14). There is no attachment check because a message has no attachment column (a test pins that).
"""
import difflib
import os
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from outreach.drafting.signature import signature_lines
from outreach.drafting.voice_checks import voice_checks
from outreach.drafts.followups import company_map, no_go_sets, resolve_company

__all__ = ["DEFAULT_BANNED_PHRASES", "MX_TTL", "DraftInput", "load_banned", "normalise_domain", "email_domain",
           "cached_mx", "draft_input", "run_checks", "BLOCK_CODES", "WARN_CODES", "find_links", "link_host"]

DEFAULT_BANNED_PHRASES = "~/Desktop/DevVault/job-search/outreach/banned_phrases.txt"
MX_TTL = timedelta(hours=24)
NEW_YORK = ZoneInfo("America/New_York")

PORTFOLIO = "devanshuchicholikar.com"
DEFAULT_WORDS = (0, 120)          # #9, 2026-11-09: at most 120 words, anything under that is fine, every reader
FOLLOWUP_MAX_WORDS = 80
MAX_LINKS = 2
SIMILAR = 0.8
STALE_HOOK_DAYS = 30
WEAK_SUBJECT = ["quick question", "following up", "excited", "opportunity", "hoping to connect", "touching base",
                "checking in", "application for", "referral request"]
SUBJECT_MAX_CHARS = 40  # about what Gmail shows of a subject on a phone
# Lines about the email itself, the reader's inbox or their time: every lab judge read them as performed (2026-11-09).
NARRATED = ("i'll be brief", "i'll keep this short", "i'll keep it short", "short one", "quick note",
            "a lot of these", "many of these", "your inbox", "thank you for opening", "thanks for opening",
            "you don't owe me", "i hope it's okay", "sorry to bother", "i know you're busy", "out of the blue",
            "this cold email")
SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "buff.ly", "is.gd", "rebrand.ly", "cutt.ly",
              "lnkd.in", "shorturl.at", "rb.gy", "tiny.cc", "s.id", "t.ly"}
DASHES = re.compile("[\u2012-\u2015]")
# Placeholder text a draft must not keep: the regex of mail/check_draft.py (copied, not imported).
PLACEHOLDER = re.compile(r"\bTODO\b|\bASK:|\{\{|\}\}")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
URL = re.compile(r"https?://[^\s<>()\"']+", re.I)
# A bare domain becomes a link in Gmail too, so it counts.
BARE = re.compile(r"(?<![\w@./-])(?:[a-z0-9-]+\.)+(?:com|ai|io|dev|app|co|org|net|ly|gl|in|at|cc|id|me|sh|so|xyz|us)"
                  r"\b(?:/[^\s<>()\"']*)?", re.I)
RE_PREFIX = re.compile(r"re\s*:", re.I)
MARKDOWN_LINK = re.compile(r"\[[^\]\n]+\]\(\s*https?://[^)\s]+\s*\)", re.I)
# No visa, OPT or sponsorship in any email, to anyone (#9, 2026-11-09). A block also pulls an approved email back, so
# ordinary words pass: OPT only in upper case and not "OPT-in", "visa" only in lower case (Visa the company passes),
# F-1 not as a score, sponsorship but not "sponsored by", immigration only as a status.
VISA = re.compile(r"\bF-1\b(?!\s+score)|\bOPT\b(?!-in)|\bvisas?\b|(?i:\bsponsorship\b|\bsponsor (?:me|my|him)\b|"
                  r"\bH-?1B\b|work authori[sz]ation|authori[sz]ed to work|\bgreen card\b|\bimmigration status\b)")
HOOK_DATE = re.compile(r"\((\d{4}-\d{2}-\d{2})\)$")
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN = re.compile(rf"(?:{_LABEL}\.)+{_LABEL}")

# Messages that are a real draft or a real email: planned rows are empty, skipped ones will never go out.
OPEN_STATES = ("drafted", "approved", "sending", "send_unknown", "sent", "failed")
STOP_REPLIES = ("said_no", "asked_to_stop")

BLOCK_CODES = ("no_mail_server", "banned_list_missing", "placeholder", "markdown_link", "visa_line", "too_many_links",
               "link_shortener", "followup_subject", "near_identical", "no_go_person", "said_no", "closed_no_sponsorship")
WARN_CODES = ("banned_phrase", "dash", "length", "stale_hook", "subject_wording", "subject_length",
              "subject_reused", "link_count", "signature", "narrated_email", "recruiter", "mail_server_unknown", "voice_eager", "voice_house_word", "voice_system_word",
              "voice_recital", "voice_rhythm", "voice_ungrounded")
_ORDER = {code: i for i, code in enumerate(BLOCK_CODES + WARN_CODES)}
_RANK = {"block": 0, "warn": 1, "pass": 2}

Check = dict  # the `Check` of web/src/api/types.ts: code, severity, title, detail, fix, spans (camelCase fix keys)


@dataclass(frozen=True)
class DraftInput:
    message_id: int | None  # None for a draft not stored yet
    seq: int
    subject: str
    body: str
    hook: str
    role_kind: str
    person_name: str
    person_email: str | None
    person_id: int
    company_id: int
    mx: Literal["ok", "none", "unknown"]


# Inputs

def load_banned(path: Path | None = None, scopes: Sequence[str] = ("both", "email")
                ) -> tuple[list[tuple[str, re.Pattern]], bool]:
    """The patterns of a DevVault banned_phrases.txt (SCOPE<TAB>EXTENDED-REGEX) in the given `scopes`, by default
    `both` and `email` (a LinkedIn note reads `both` and `dm`), and whether the list could be used. `path` None
    reads OUTREACH_BANNED_PHRASES, else the DevVault default.

    Fails closed: a missing or unreadable file, a pattern that does not compile, or no pattern at all in the
    scopes gives `([], False)`, and `run_checks` blocks on an empty list. Never half a list."""
    if path is None:
        path = Path(os.environ.get("OUTREACH_BANNED_PHRASES") or DEFAULT_BANNED_PHRASES)
    path = Path(path).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return [], False
    out = []
    for line in text.splitlines():
        scope, _, pattern = line.partition("\t")
        if scope.strip() in scopes and pattern.strip():
            try:
                out.append((pattern, re.compile(pattern.replace("[[:space:]]", r"\s"), re.I)))
            except re.error:
                return [], False
    return (out, True) if out else ([], False)


def normalise_domain(domain: str | None) -> str | None:
    """The lower-case ASCII form of a domain name, or None when it is not one (nothing is looked up for it)."""
    if not isinstance(domain, str):
        return None
    name = domain.strip().lower().rstrip(".")
    try:
        name = name.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if len(name) > 253 or not _DOMAIN.fullmatch(name):
        return None
    return name


def email_domain(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return normalise_domain(email.rsplit("@", 1)[1])


def _ts(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def cached_mx(conn: sqlite3.Connection, domain: str | None, now: str) -> Literal["ok", "none", "unknown"]:
    """The stored mail server answer for `domain` when it is less than 24 hours old, else `unknown`. Read only."""
    name = normalise_domain(domain)
    if name is None:
        return "unknown"
    row = conn.execute("SELECT status, checked_at FROM mx_check WHERE domain = ?", (name,)).fetchone()
    if row is not None and _ts(row["checked_at"]) > _ts(now) - MX_TTL:
        return row["status"]
    return "unknown"


def draft_input(conn: sqlite3.Connection, message_id: int, *, now: str) -> DraftInput:
    """The `DraftInput` of a stored message, its mail server read from the cache (`unknown` when missing or stale,
    which only warns; `none` when the address has no usable domain). The API resolves the domain first
    (`outreach.api.mx.ensure`); the sender reads only the cache. LookupError for an unknown message."""
    row = conn.execute(
        "SELECT m.message_id, m.seq, m.subject, m.body, m.hook, p.role_kind, p.name, p.email, p.person_id, "
        "p.company_id FROM message m JOIN touch t ON t.touch_id = m.touch_id "
        "JOIN person p ON p.person_id = t.person_id WHERE m.message_id = ?", (message_id,)).fetchone()
    if row is None:
        raise LookupError(f"no message {message_id}")
    return DraftInput(
        message_id=row["message_id"], seq=row["seq"], subject=row["subject"], body=row["body"], hook=row["hook"],
        role_kind=row["role_kind"], person_name=row["name"], person_email=row["email"], person_id=row["person_id"],
        company_id=row["company_id"],
        mx=cached_mx(conn, domain, now) if (domain := email_domain(row["email"])) else "none")


# Text rules (a copy of mail/check_draft.py)

def _trim(u: str) -> str:
    return u.rstrip(".,;:!?")


def _links(text: str) -> list[tuple[int, int, str]]:
    """Every link as (start, end, text) in reading order: full URLs, then bare domains Gmail would link. Email
    addresses are not links. Positions are in `text` (masks keep the length)."""
    masked = EMAIL.sub(lambda m: " " * len(m.group()), text)
    found = []
    for m in URL.finditer(masked):
        u = _trim(m.group())
        found.append((m.start(), m.start() + len(u), u))
    rest = URL.sub(lambda m: " " * len(m.group()), masked)
    for m in BARE.finditer(rest):
        u = _trim(m.group())
        found.append((m.start(), m.start() + len(u), u))
    return sorted(found)


def _host(u: str) -> str:
    h = re.sub(r"^https?://", "", u, flags=re.I).split("/")[0].lower()
    return h[4:] if h.startswith("www.") else h


def _is_portfolio(u: str) -> bool:
    return _host(u) == PORTFOLIO and re.sub(r"^(?:https?://)?[^/]+", "", u, flags=re.I).strip("/") == ""


# The note checker (outreach.plan.notecheck) reads links the same way an email draft does.
find_links = _links
link_host = _host


def _word_count(body: str) -> int:
    return len(URL.sub(" ", body).split())


def _span(field: str, start: int, end: int) -> dict:
    return {"field": field, "start": start, "end": end}


def _check(code: str, severity: str, title: str, detail: str, fix: dict | None = None,
           spans: Sequence[dict] = ()) -> Check:
    return {"code": code, "severity": severity, "title": title, "detail": detail, "fix": fix, "spans": list(spans)}


def _mail_server(d: DraftInput) -> list[Check]:
    if email_domain(d.person_email) is None:  # nothing to look up, so nothing can take the mail
        return [_check("no_mail_server", "block", "No usable email address",
                       f"{d.person_email or 'This person has no address'}: there is no valid domain to send to. "
                       "Fix the address or skip this person.")]
    if d.mx == "none":
        return [_check("no_mail_server", "block", "No mail server",
                       f"{d.person_email or 'This address'} has no mail server, so this email would bounce. "
                       "Find another address or skip this person.")]
    if d.mx == "unknown":
        return [_check("mail_server_unknown", "warn", "Mail server not confirmed",
                       "The mail server lookup did not answer, so nobody knows yet whether this address takes mail. "
                       "You can still approve it.")]
    return [_check("no_mail_server", "pass", "Mail server found", "The address's domain takes mail.")]


def _banned(d: DraftInput, banned: Sequence[tuple[str, re.Pattern]]) -> list[Check]:
    out, spans, names = [], [], []
    if not banned:
        out.append(_check("banned_list_missing", "block", "Banned phrases list missing",
                          "The banned phrases list could not be read, so this draft was not checked for banned "
                          "phrases. Fix the list (OUTREACH_BANNED_PHRASES, or banned_phrases.txt in DevVault), "
                          "then check again."))
    rules = list(banned)
    for field, text in (("subject", d.subject), ("body", d.body)):
        for src, rx in rules:
            for m in rx.finditer(text):
                if m.end() > m.start():
                    spans.append(_span(field, m.start(), m.end()))
                    if src not in names:
                        names.append(src)
    if names:
        out.append(_check("banned_phrase", "warn", "Banned phrase",
                          "On the banned list: " + ", ".join(repr(n) for n in names) + ". Say it plainly.",
                          spans=sorted(spans, key=lambda s: (s["field"] != "subject", s["start"]))))
    elif banned:
        out.append(_check("banned_phrase", "pass", "No banned phrases", "Nothing from the banned list."))
    return out


def _dash(d: DraftInput) -> Check:
    spans = [_span(f, m.start(), m.end()) for f, text in (("subject", d.subject), ("body", d.body))
             for m in DASHES.finditer(text)]
    if spans:
        return _check("dash", "warn", "Em or en dash", "Use a comma, a colon, a full stop or a plain hyphen instead.",
                      spans=spans)
    return _check("dash", "pass", "No em or en dashes", "No long dashes.")


def _placeholder(d: DraftInput) -> Check:
    spans = [_span(f, m.start(), m.end()) for f, text in (("subject", d.subject), ("body", d.body))
             for m in PLACEHOLDER.finditer(text)]
    if spans:
        return _check("placeholder", "block", "Placeholder text left in",
                      "TODO, ASK: or {{ }} is still in the draft. Replace it with the real words.", spans=spans)
    return _check("placeholder", "pass", "No placeholder text", "No TODO, ASK: or {{ }} left in.")


def _markdown_link(d: DraftInput) -> Check:
    spans = [_span(f, m.start(), m.end()) for f, text in (("subject", d.subject), ("body", d.body))
             for m in MARKDOWN_LINK.finditer(text)]
    if spans:
        return _check("markdown_link", "block", "Markdown link",
                      "The email is plain text, so the brackets arrive as typed. Write the full link on its own line.",
                      spans=spans)
    return _check("markdown_link", "pass", "No markdown links", "Every link is a plain link.")


def _visa_line(d: DraftInput) -> Check:
    spans = [_span(f, m.start(), m.end()) for f, text in (("subject", d.subject), ("body", d.body))
             for m in VISA.finditer(text)]
    if spans:
        return _check("visa_line", "block", "Visa mention",
                      "No visa, OPT or sponsorship in any email (#9, 2026-11-09). Take this out; it comes up only if "
                      "they ask.", spans=spans)
    return _check("visa_line", "pass", "No visa mention", "Nothing about visa, OPT or sponsorship.")


def _link_rules(d: DraftInput) -> list[Check]:
    found = _links(d.body)
    spans = [_span("body", s, e) for s, e, _ in found]
    out = []
    if len(found) > MAX_LINKS:
        out.append(_check("too_many_links", "block", "More than 2 links",
                          f"{len(found)} links. At most 2: the portfolio, then one project.", spans=spans))
    else:
        out.append(_check("too_many_links", "pass", "At most 2 links", f"{len(found)} links."))
    short = [_span("body", s, e) for s, e, u in found if _host(u) in SHORTENERS]
    if short:
        out.append(_check("link_shortener", "block", "Link shortener", "Use the full link, not a shortener.",
                          spans=short))
    else:
        out.append(_check("link_shortener", "pass", "No link shorteners", "Every link is a full link."))
    if d.seq == 0:
        start = _signature_start(d.body)
        signed = start is not None and found and _is_portfolio(found[-1][2]) and found[-1][0] >= start
        if len(found) == MAX_LINKS and signed:
            out.append(_check("link_count", "pass", "One link, then the signed portfolio",
                              "2 links: one in the body, the portfolio in the signature."))
        elif len(found) != MAX_LINKS:
            out.append(_check("link_count", "warn", "Not 2 links",
                              f"{len(found)} links. A first email has 2: one link in the body (the project or the "
                              "writing it leans on), and the portfolio in the signature.", spans=spans))
        else:
            out.append(_check("link_count", "warn", "Portfolio belongs in the signature",
                              f"End with the signature; it carries https://www.{PORTFOLIO}. Keep one link in the "
                              "body.", spans=spans))
    return out


def _signature_start(body: str) -> int | None:
    """Where the signature begins in `body` when the body ends with it (each line compared stripped, blank lines
    skipped), else None."""
    want = signature_lines()
    lines = [(m.start(), m.group().strip()) for m in re.finditer(r"[^\n]+", body) if m.group().strip()]
    if len(lines) < len(want) or [text for _, text in lines[-len(want):]] != want:
        return None
    return lines[-len(want)][0]


def _signature(d: DraftInput) -> Check:
    if _signature_start(d.body) is not None:
        return _check("signature", "pass", "Signature is there", "The email ends with his signature.")
    return _check("signature", "warn", "Signature missing",
                  "End with an optional sign-off word, then his signature exactly: "
                  + " / ".join(signature_lines()) + ".")


def _length(d: DraftInput) -> Check:
    n = _word_count(d.body)
    if d.seq >= 1:
        lo, hi, aim = 0, FOLLOWUP_MAX_WORDS, f"a follow-up is at most {FOLLOWUP_MAX_WORDS}"
    else:
        lo, hi = DEFAULT_WORDS
        aim = f"a first email is at most {hi}"
    if lo <= n <= hi:
        return _check("length", "pass", "Length is right", f"{n} words; {aim}.")
    return _check("length", "warn", "Too long" if n > hi else "Too short", f"{n} words; {aim}.")


def _stale_hook(d: DraftInput, now: str) -> Check | None:
    """Only a hook that ends in (YYYY-MM-DD) is checked: no date means no check."""
    m = HOOK_DATE.search(d.hook.rstrip())
    if m is None:
        return None
    try:
        when = date.fromisoformat(m.group(1))
    except ValueError:
        return _check("stale_hook", "warn", "Hook date not readable",
                      f"{m.group(1)} is not a real date, so the hook's age is unknown.")
    age = (_ts(now).astimezone(NEW_YORK).date() - when).days
    if age > STALE_HOOK_DAYS:
        return _check("stale_hook", "warn", "Hook is old",
                      f"The hook is from {when.isoformat()}, {age} days ago. Use something from the last "
                      f"{STALE_HOOK_DAYS} days.")
    return _check("stale_hook", "pass", "Hook is recent", f"The hook is from {when.isoformat()}.")


def _subject_wording(d: DraftInput) -> Check:
    problems = [f"{w!r} reads like a mass send" for w in WEAK_SUBJECT if w in d.subject.lower()]
    if d.seq == 0 and RE_PREFIX.match(d.subject.strip()):
        problems.append("'Re:' on a first email")
    if problems:
        return _check("subject_wording", "warn", "Subject wording", "Subject: " + "; ".join(problems) + ".")
    return _check("subject_wording", "pass", "Subject reads well", "No weak subject wording.")


def _subject_length(d: DraftInput) -> Check | None:
    if d.seq >= 1:
        return None  # a follow-up's subject is "Re: " plus the first one, set by code
    n = len(d.subject.strip())
    if n > SUBJECT_MAX_CHARS:
        return _check("subject_length", "warn", "Subject too long",
                      f"{n} characters. A phone shows about {SUBJECT_MAX_CHARS}; keep it to that so it reads whole.")
    return _check("subject_length", "pass", "Subject fits a phone", f"{n} characters.")


def _narrated(d: DraftInput) -> Check | None:
    if d.seq >= 1:
        return None  # a follow-up may say it is following up, or that they may be busy (#9, 2026-11-09)
    spans = [_span("body", m.start(), m.end()) for phrase in NARRATED
             for m in re.finditer(rf"(?<![\w']){re.escape(phrase)}(?![\w])", d.body.replace("\u2019", "'"), re.I)]
    if spans:
        return _check("narrated_email", "warn", "A line about the email itself",
                      "This line talks about the email, their inbox or their time. Cut it; the specifics do the work.",
                      spans=sorted(spans, key=lambda s: s["start"]))
    return _check("narrated_email", "pass", "No lines about the email", "Nothing talks about the email itself.")


def _recruiter(d: DraftInput) -> Check:
    if d.role_kind != "recruiter":
        return _check("recruiter", "pass", "Not a recruiter", f"Role kind is {d.role_kind}.")
    return _check("recruiter", "warn", "Recruiter",
                  "This person is a recruiter. Email a recruiter only when no engineer, manager or founder is "
                  "found, and name the exact role up front. If the role kind is wrong, change it.",
                  fix={"kind": "role_kind"})


# Rules on current data

def _family(companies: dict, survivor: int) -> list[int]:
    """The survivor and every company merged into it, at any depth (a visited set, so a cycle cannot loop)."""
    children: dict[int, list[int]] = {}
    for cid, row in companies.items():
        if row["merged_into"] is not None:
            children.setdefault(row["merged_into"], []).append(cid)
    family, todo = [], [survivor]
    while todo:
        cid = todo.pop()
        if cid in family:
            continue
        family.append(cid)
        todo.extend(children.get(cid, ()))
    return sorted(family)


def _in(ids: Sequence[int]) -> str:
    return ",".join("?" for _ in ids)


def _no_go(conn, d: DraftInput, companies: dict, survivor: int, family: list[int]) -> Check:
    people, survivors = no_go_sets(conn, companies)
    if d.person_id not in people and survivor not in survivors:
        return _check("no_go_person", "pass", "Not on the no-go list", "No active no-go on the person or company.")
    row = conn.execute(
        f"SELECT no_go_id, person_id, reason FROM no_go WHERE lifted_at IS NULL "
        f"AND (person_id = ? OR company_id IN ({_in(family)})) ORDER BY person_id IS NULL, no_go_id LIMIT 1",
        (d.person_id, *family)).fetchone()
    who = f"{d.person_name} is" if row is None or row["person_id"] is not None else "This company is"
    reason = "" if row is None else f" (reason: {row['reason'].replace('_', ' ')})"
    return _check("no_go_person", "block", "On the no-go list", f"{who} on the no-go list{reason}. Nothing goes out.",
                  fix=None if row is None else {"kind": "no_go", "noGoId": row["no_go_id"]})


def _said_no(conn, d: DraftInput) -> Check:
    row = conn.execute(
        f"SELECT reply_kind FROM touch WHERE person_id = ? AND reply_kind IN ({_in(STOP_REPLIES)}) "
        f"UNION ALL SELECT reply_kind FROM outreach_touch WHERE person_id = ? AND undone_at IS NULL "
        f"AND reply_kind IN ({_in(STOP_REPLIES)}) LIMIT 1",
        (d.person_id, *STOP_REPLIES, d.person_id, *STOP_REPLIES)).fetchone()
    if row is None:
        return _check("said_no", "pass", "Has not said no", "No reply from this person said no or asked to stop.")
    what = "said no" if row["reply_kind"] == "said_no" else "asked to stop"
    return _check("said_no", "block", "Said no", f"{d.person_name} {what}. Never contact this person again.")


def _closed(conn, chain: list[int]) -> Check:
    row = conn.execute(
        f"SELECT name FROM company WHERE company_id IN ({_in(chain)}) AND status = 'closed' "
        f"AND close_reason = 'no_sponsorship' LIMIT 1", chain).fetchone()
    if row is None:
        return _check("closed_no_sponsorship", "pass", "Company not closed for sponsorship",
                      "The company is not closed as no sponsorship.")
    return _check("closed_no_sponsorship", "block", "Closed: no sponsorship",
                  f"{row['name']} is closed because it does not sponsor. Reopen it first if that changed.")


def _core(body: str) -> str:
    """The body without its greeting line, and without the signature and the sign-off word line before it."""
    text = body.rstrip()
    start = _signature_start(text)
    if start is not None:
        text = text[:start].rstrip()
        last = text.rsplit("\n", 1)
        if len(last) == 2 and len(last[1].split()) <= 3 and last[1].strip().endswith(","):
            text = last[0].rstrip()
    lines = text.split("\n", 1)
    if len(lines) == 2 and re.match(r"^\s*(hi|hello|hey|dear)\b[^\n]{0,40},\s*$", lines[0], re.I):
        text = lines[1]
    return text.strip()


def _near_identical(conn, d: DraftInput, family: list[int]) -> Check:
    rows = conn.execute(
        f"SELECT m.message_id, m.body, p.name FROM message m JOIN touch t ON t.touch_id = m.touch_id "
        f"JOIN person p ON p.person_id = t.person_id WHERE p.company_id IN ({_in(family)}) AND p.person_id <> ? "
        f"AND m.state IN ({_in(OPEN_STATES)}) AND m.message_id IS NOT ? ORDER BY m.message_id",
        (*family, d.person_id, *OPEN_STATES, d.message_id)).fetchall()
    best, best_ratio = None, 0.0
    # autojunk off: on a body over 200 characters it junks common letters, so the score drops and depends on order.
    # The greeting, the sign-off word and the signature are the same in every email, so they are not compared.
    matcher = difflib.SequenceMatcher(None, "", _core(d.body).lower(), autojunk=False)
    for row in rows:
        matcher.set_seq1(_core(row["body"]).lower())
        if matcher.real_quick_ratio() < SIMILAR or matcher.quick_ratio() < SIMILAR:
            continue  # both are upper bounds of ratio(), so skipping here never misses a match
        ratio = matcher.ratio()
        if ratio >= SIMILAR and ratio > best_ratio:
            best, best_ratio = row, ratio
    if best is None:
        return _check("near_identical", "pass", "Not a copy of a colleague's draft",
                      "No draft to a colleague at this company is close to this one.")
    return _check("near_identical", "block", "Near-identical to a colleague's draft",
                  f"This reads {round(best_ratio * 100)}% like the email to {best['name']} at the same company. "
                  "Rewrite one of them.", fix={"kind": "compare", "otherMessageId": best["message_id"]})


def _subject_reused(conn, d: DraftInput) -> Check:
    row = conn.execute(
        "SELECT p.name FROM message m JOIN touch t ON t.touch_id = m.touch_id JOIN person p ON p.person_id = "
        "t.person_id WHERE t.person_id <> ? AND m.state NOT IN ('planned', 'skipped') "
        "AND lower(trim(m.subject)) = lower(trim(?)) LIMIT 1", (d.person_id, d.subject)).fetchone()
    if row is None:
        return _check("subject_reused", "pass", "Subject is unique", "No one else got this subject.")
    return _check("subject_reused", "warn", "Subject used before",
                  f"The email to {row['name']} has this subject. Each person gets their own.")


def _followup_subject(conn, d: DraftInput) -> Check | None:
    """A follow-up's subject is exactly `Re: ` plus the first email's, so it threads for the recipient (#14)."""
    if d.seq < 1:
        return None
    row = conn.execute("SELECT m.subject FROM message m JOIN touch t ON t.touch_id = m.touch_id "
                       "WHERE t.person_id = ? AND m.seq = 0", (d.person_id,)).fetchone()
    if row is None:
        return _check("followup_subject", "block", "No first email on record",
                      "A follow-up needs the first email's subject to thread, and there is no first email.")
    want = f"Re: {row['subject']}"
    if d.subject == want:
        return _check("followup_subject", "pass", "Follow-up threads", "The subject matches the first email.")
    return _check("followup_subject", "block", "Follow-up subject does not thread",
                  f"A follow-up's subject must be exactly {want!r}, so it lands in the same thread.")


def run_checks(conn: sqlite3.Connection, d: DraftInput, *, banned: Sequence[tuple[str, re.Pattern]],
               now: str, ground: str | None = None) -> list[Check]:
    """Every check of one draft on the store as it is now: blocks, then warns, then the passes, each group in a
    fixed code order. `banned` is what `load_banned` returned; an empty list blocks (`banned_list_missing`).
    `ground` is the text a number in the body must come from (the voice checks, #135); None skips that one check.
    Read only: it writes nothing and leaves the caller's transaction as it found it."""
    companies = company_map(conn)
    if d.company_id not in companies:
        raise LookupError(f"no company {d.company_id}")
    chain = resolve_company(companies, d.company_id)
    family = _family(companies, chain[-1])
    if ground is not None:
        ground = ground + "\n" + "\n".join(signature_lines())  # his phone is in the signature, not in me.md
    found: list[Check | None] = [
        *_mail_server(d),
        *_banned(d, banned),
        _dash(d),
        _placeholder(d),
        _markdown_link(d),
        _visa_line(d),
        *_link_rules(d),
        _signature(d),
        _narrated(d),
        _length(d),
        _stale_hook(d, now),
        _subject_wording(d),
        _subject_length(d),
        _recruiter(d),
        *voice_checks(d.body, d.role_kind, ground=ground),
        _followup_subject(conn, d),
        _near_identical(conn, d, family),
        _subject_reused(conn, d),
        _no_go(conn, d, companies, chain[-1], family),
        _said_no(conn, d),
        _closed(conn, chain),
    ]
    return sorted((c for c in found if c is not None), key=lambda c: (_RANK[c["severity"]], _ORDER[c["code"]]))
