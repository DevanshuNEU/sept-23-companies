"""The careers page check (#31 Task 15, design 5.3 step 3): which boards a company's own careers page points to.

`CareersChecker.check(domain)` makes at most three requests: `https://<domain>/careers`; when that is a 4xx other than
429, the homepage; and the first same-site link on the homepage that looks like a jobs page. Only a definite answer
is `none`. A fetch error, a 5xx, a 429 or any status it does not know is `error`: nothing is concluded, so the caller
leaves no marker and asks again next run (error is not empty).
"""
from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal

from outreach.net import FetchError, HttpClient, Response
from outreach.sources.ats_detect import detect_all, detect_unsupported
from outreach.sources.boards import BoardRef

__all__ = ["CareersResult", "CareersChecker"]

_LINK_WORDS = ("career", "jobs", "join")
_SKIPPED_SCHEMES = ("mailto:", "tel:", "javascript:", "#")


@dataclass(frozen=True)
class CareersResult:
    """`boards`: the page names these supported boards (all of them, in page order; the caller decides what one or
    several mean). `page`: a careers page with no board on it. `none`: a definite no page. `error`: unknown.
    `page_url` is the page read (after redirects) for `boards` and `page`; `unsupported` names job platforms the page
    links to that have no board reader."""
    kind: Literal["boards", "page", "none", "error"]
    boards: tuple[BoardRef, ...] = ()
    page_url: str | None = None
    unsupported: tuple[str, ...] = ()
    error: str = ""


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href and href.strip():
                self.hrefs.append(href.strip())


def _same_site(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


class CareersChecker:
    def __init__(self, client: HttpClient):
        self.client = client

    def check(self, domain: str) -> CareersResult:
        domain = domain.strip().lower()
        careers_url = f"https://{domain}/careers"
        try:
            resp = self.client.get(careers_url, follow_redirects=True)
            if resp.status == 200:
                return _read(resp, careers_url)
            if not _is_no(resp.status):
                return _unexpected(resp, careers_url)
            home_url = f"https://{domain}/"
            home = self.client.get(home_url, follow_redirects=True)
            if home.status != 200:
                return CareersResult("none") if _is_no(home.status) else _unexpected(home, home_url)
            link = _first_link(home, home_url, domain, skip={careers_url.rstrip("/")})
            if link is None:
                return CareersResult("none")
            resp = self.client.get(link, follow_redirects=True)
            if resp.status == 200:
                return _read(resp, link)
            return CareersResult("none") if _is_no(resp.status) else _unexpected(resp, link)
        except FetchError as err:
            return CareersResult("error", error=str(err))


def _is_no(status: int) -> bool:
    """A 4xx other than 429 is the site answering that the page is not there (or not for us)."""
    return 400 <= status < 500 and status != 429


def _unexpected(resp: Response, url: str) -> CareersResult:
    return CareersResult("error", error=f"HTTP {resp.status} for {url}")


def _read(resp: Response, requested: str) -> CareersResult:
    final = resp.url or requested
    text = final + "\n" + resp.text()  # a redirect straight to a board shows in the final URL
    boards, unsupported = detect_all(text), tuple(detect_unsupported(text))
    return CareersResult("boards" if boards else "page", tuple(boards), final, unsupported)


def _first_link(home: Response, home_url: str, domain: str, *, skip: set[str]) -> str | None:
    parser = _Links()
    parser.feed(home.text())
    base = home.url or home_url
    for href in parser.hrefs:
        lowered = href.lower()
        if lowered.startswith(_SKIPPED_SCHEMES) or not any(word in lowered for word in _LINK_WORDS):
            continue
        parts = urllib.parse.urlsplit(urllib.parse.urljoin(base, href))
        if parts.scheme not in ("http", "https") or not _same_site((parts.hostname or "").lower(), domain):
            continue
        url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))
        if url.rstrip("/") in skip:
            continue
        return url
    return None
