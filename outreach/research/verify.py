"""The page check: a fact is kept only when code fetched its https page and found its quote there (#205).

Standard library only. The client is injected, so tests never reach the network.
"""
import html as html_lib
import ipaddress
import re
import socket
from collections.abc import Sequence
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from outreach.net import FetchError, HttpClient
from outreach.research.store import Fact

__all__ = ["page_text", "quote_on_page", "refused_url", "check_facts"]

MAX_BYTES = 3_000_000
MAX_HOPS = 3
MAX_URL = 500
MAX_QUERY = 200
MAX_FACT = 300
MAX_QUOTE = 500
MIN_QUOTE_WORDS = 4
MIN_QUOTE_CHARS = 20
_REDIRECTS = (301, 302, 303, 307, 308)
_CONTACT_LABELS = {"rocketreach", "zoominfo", "lusha", "signalhire", "contactout"}
_SKIP = {"script", "style", "noscript", "template", "head"}
_BREAKS = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}
_TRANSLATE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', " ": " "})


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag in _BREAKS:
            self.parts.append(" ")

    def handle_startendtag(self, tag, attrs):
        if tag in _BREAKS:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in _BREAKS:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s.translate(_TRANSLATE)).strip()


def page_text(html: str) -> str:
    """Visible text: scripts, styles and tags removed, entities unescaped, spaces collapsed."""
    p = _Text()
    p.feed(html)
    p.close()
    return _squash(html_lib.unescape("".join(p.parts)))


def quote_on_page(text: str, quote: str) -> bool:
    q = _squash(quote).lower()
    return bool(q) and q in _squash(text).lower()


def _private(host: str) -> bool:
    host = host.removesuffix(".")
    try:
        ipaddress.ip_address(host)  # any IP literal, v4 or v6 (urlsplit strips the brackets)
        return True
    except ValueError:
        pass
    try:
        socket.inet_aton(host)  # decimal, short and hex IPv4 forms the resolver also accepts
        return True
    except (OSError, ValueError):
        pass
    return host == "localhost" or host.endswith((".localhost", ".local", ".internal"))


def refused_url(url: str) -> str | None:
    """The reason a URL may not be used, or None."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removesuffix(".")
    if "linkedin.com" in url.lower() or "lnkd.in" in url.lower():  # the table refuses these anywhere in a link
        return "LinkedIn pages are not used"
    if len(url.strip()) > MAX_URL or len(parts.query) > MAX_QUERY:
        return "link too long to check"
    labels = host.split(".")
    if host in ("linkedin.com", "lnkd.in") or host.endswith((".linkedin.com", ".lnkd.in")):
        return "LinkedIn pages are not used"
    if _CONTACT_LABELS & set(labels) or host == "apollo.io" or host.endswith(".apollo.io"):
        return "contact-data sites are not used"
    if parts.scheme != "https" or not host:
        return "only https pages"
    if _private(host):
        return "private or local addresses are not used"
    return None


def _charset(content_type: str) -> str:
    m = re.search(r"charset\s*=\s*\"?([\w.-]+)", content_type, re.I)
    return m.group(1) if m else "utf-8"


def _load(client: HttpClient, url: str) -> tuple[str | None, str | None]:
    """(page text, None) or (None, reason)."""
    current = url
    for _hop in range(MAX_HOPS + 1):
        try:
            r = client.get(current, follow_redirects=False, max_bytes=MAX_BYTES)
        except FetchError as e:
            return None, f"page could not be fetched: {e.kind}"
        if r.status not in _REDIRECTS:
            break
        target = r.headers.get("location")
        if not target:
            return None, f"page answered {r.status}"
        current = urljoin(current, target.strip())
        reason = refused_url(current)
        if reason:
            return None, reason
    else:
        return None, "page redirected too many times"
    if r.status != 200:
        return None, f"page answered {r.status}"
    landed = refused_url(r.url) if r.url else None
    if landed:
        return None, landed
    ctype = (r.headers.get("content-type") or "").lower()
    if not (ctype.startswith("text/html") or ctype.startswith("text/plain")):
        return None, "page is not a text page"
    try:
        body = r.body.decode(_charset(ctype), errors="replace")
    except LookupError:
        body = r.body.decode("utf-8", errors="replace")
    return (page_text(body) if ctype.startswith("text/html") else _squash(body)), None


def _shape_problem(f: Fact) -> str | None:
    """The reason the table (or a useless quote) would refuse this fact, or None."""
    if not f.fact.strip():
        return "fact is blank"
    if len(f.fact) > MAX_FACT:
        return f"fact is too long ({MAX_FACT} characters at most)"
    if len(f.quote) > MAX_QUOTE:
        return f"quote is too long ({MAX_QUOTE} characters at most)"
    q = _squash(f.quote)
    if len(q.split()) < MIN_QUOTE_WORDS or len(q) < MIN_QUOTE_CHARS:
        return "quote too short to check"
    return None


def check_facts(client: HttpClient, facts: Sequence[Fact]) -> tuple[list[Fact], list[dict]]:
    """Kept facts, and {url, quote, reason} for each rejected one. One fetch per URL."""
    kept: list[Fact] = []
    rejected: list[dict] = []
    pages: dict[str, tuple[str | None, str | None]] = {}
    for f in facts:
        reason = refused_url(f.url) or _shape_problem(f)
        if reason is None:
            if f.url not in pages:
                pages[f.url] = _load(client, f.url)
            text, reason = pages[f.url]
            if text is not None and not quote_on_page(text, f.quote):
                reason = "quote not found on the page"
        if reason is None:
            kept.append(f)
        else:
            rejected.append({"url": f.url, "quote": f.quote, "reason": reason})
    return kept, rejected
