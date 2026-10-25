"""Key normalizers for identity (#31 Task 4): FEIN, registrable domain, what kind of domain it is, and the keys a
person is matched by (address, LinkedIn profile, name), shared by MCP add_people (#13) and the history import (#17)."""
import re
from collections.abc import Sequence
from typing import Literal

# The 50 states, DC and the territories. "Berlin, DE" cannot be told from Delaware, so DE stays.
US_STATES = frozenset("""
AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI
SC SD TN TX UT VT VA WA WV WI WY DC PR GU VI AS MP
""".split())

DomainKind = Literal["company", "free", "peo", "platform"]


def normalize_fein(raw: str | None) -> str | None:
    """Digits only. None unless exactly 9 digits and not all zeros ('46-3517853' gives '463517853')."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) != 9 or digits == "0" * 9:
        return None
    return digits


_HOST = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu"}


def registrable_domain(host_or_email: str | None) -> str | None:
    """Lower case, no leading `www.`, nothing before an `@`. Keeps the last two labels, or three when the
    second-to-last is co, com, org, net, ac, gov or edu and the last is two letters (`eng.nimbus.com` gives
    `nimbus.com`, `acme.co.uk` stays whole). None when there is no usable dotted host."""
    host = (host_or_email or "").strip().lower().rpartition("@")[2].strip().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if not _HOST.match(host):
        return None
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


FREE_MAIL = frozenset("""
gmail.com googlemail.com outlook.com hotmail.com live.com msn.com yahoo.com ymail.com rocketmail.com icloud.com
me.com mac.com proton.me protonmail.com pm.me aol.com gmx.com gmx.net gmx.de mail.com fastmail.com hey.com
comcast.net verizon.net att.net sbcglobal.net bellsouth.net cox.net charter.net earthlink.net qq.com 163.com
126.com naver.com rediffmail.com yandex.com mail.ru web.de tutanota.com
""".split())
_FREE_FIRST_LABEL = {"yahoo", "hotmail", "outlook"}  # yahoo.co.in, hotmail.co.uk, outlook.in and the rest

PEO = frozenset("""
trinet.com deel.com justworks.com insperity.com velocityglobal.com adp.com gusto.com rippling.com oysterhr.com
remote.com papayaglobal.com multiplier.com
""".split())

PLATFORM = frozenset("""
github.com github.io gitlab.com linkedin.com ycombinator.com notion.site notion.so medium.com substack.com x.com
twitter.com facebook.com instagram.com youtube.com google.com calendly.com linktr.ee bit.ly wixsite.com
wordpress.com squarespace.com
lever.co greenhouse.io ashbyhq.com workable.com bamboohr.com smartrecruiters.com jobvite.com icims.com
myworkdayjobs.com workday.com recruitee.com breezy.hr teamtailor.com personio.de personio.com wellfound.com
angel.co indeed.com glassdoor.com welcometothejungle.com
vercel.app netlify.app herokuapp.com pages.dev web.app firebaseapp.com appspot.com webflow.io myshopify.com
framer.website carrd.co onrender.com fly.dev replit.app azurewebsites.net cloudfront.net amazonaws.com blogspot.com
gitbook.io
t.co goo.gl lnkd.in forms.gle ow.ly tinyurl.com buff.ly typeform.com tally.so airtable.com jotform.com
techcrunch.com nytimes.com bloomberg.com wsj.com forbes.com reuters.com theverge.com wired.com venturebeat.com
wikipedia.org crunchbase.com reddit.com youtu.be producthunt.com arxiv.org
workatastartup.com gem.com pinpointhq.com applytojob.com jazzhr.com paylocity.com ultipro.com ukg.net ukg.com
paycomonline.net oraclecloud.com
""".split())


def classify_domain(domain: str) -> DomainKind:
    """`free` (public mailbox providers), `peo` (payroll and employer-of-record hosts), `platform` (code hosts,
    social sites, job boards, ATS hosts and hosting suffixes such as `vercel.app`), else `company`. A host such as
    `mail.gmail.com` is reduced first."""
    d = registrable_domain(domain) or (domain or "").strip().lower()
    if d in FREE_MAIL or ("." in d and d.partition(".")[0] in _FREE_FIRST_LABEL):
        return "free"
    if d in PEO:
        return "peo"
    if d in PLATFORM:
        return "platform"
    return "company"


_SHARED_COUNT = re.compile(r"(\d+) employers")


def shared_reason(n: int, names: Sequence[str]) -> str:
    """The `shared_key.reason` text: how many employer groups use the key, and up to 5 of their distinct legal
    names, sorted. LCA writes it (`n` counts groups, large filers included); `shared_count` reads it back."""
    return f"{n} employers: {', '.join(sorted(set(names))[:5])}"


def shared_count(reason: str) -> int | None:
    """The employer count at the start of a `shared_reason` text; None when it is not there or is below 2 (a
    shared key always has at least two employers, so anything less is unreadable, not a count)."""
    m = _SHARED_COUNT.match(reason or "")
    n = int(m.group(1)) if m else 0
    return n if n >= 2 else None


# ---- people (#13 add_people and #17 import match people with these, so both see one person the same way) --------

EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s.]+")
PROFILE = re.compile(r"https://(?:[a-z]{2,3}\.)?linkedin\.com/in/([^/?#\s]+)/?(?:[?#]\S*)?", re.I)


def email_key(text: str | None) -> str | None:
    """An address as the store keeps it (trimmed, lower case), or None when it is not an address."""
    email = (text or "").strip().lower()
    return email if EMAIL.fullmatch(email) else None


def linkedin_key(url: str | None) -> str | None:
    """One form of a LinkedIn profile link (`https://www.linkedin.com/in/<slug>`, slug lower case), or None when
    it is not a profile link."""
    m = PROFILE.fullmatch((url or "").strip())
    return f"https://www.linkedin.com/in/{m.group(1).lower()}" if m else None


def person_name_key(name: str) -> str:
    """The comparison key for a person's name: lower case, runs of spaces made one. Letters outside ASCII are kept,
    so two names in another script never share a key."""
    return " ".join(name.lower().split())
