"""The careers page check (#31 Task 15, design 5.3 step 3): which boards a company's own careers page points to.

`CareersChecker.check(domain)` makes at most three requests: `https://<domain>/careers`; when that is a 404 or 410,
the homepage; and the first same-site link on the homepage that looks like a jobs page. Only a 404 or 410 is a
definite `none`. A fetch error, a 5xx, a 429, a 403 or any other status is `error`: nothing is concluded, so the
caller leaves no marker and asks again next run (error is not empty).

The checker follows redirects itself, one hop at a time (at most the crawl config's `max_redirects`), and every
request and hop goes through the crawler's host block (`CrawlConfig.off_host`, the `[ats_off]` list, #46): a hop to
a switched-off ATS's host is never sent and the check is an `error`, never a definite `none`.
"""
from __future__ import annotations

import dataclasses
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal

from outreach.crawl.config import CrawlConfig, load_crawl_config  # stdlib only: the one [ats_off] list (#46)
from outreach.net import FetchError, HttpClient, Response
from outreach.sources.ats_detect import detect_all, detect_unsupported
from outreach.sources.boards import BoardRef

__all__ = ["CareersResult", "CareersChecker"]

_LINK_WORDS = ("career", "jobs", "join")
_SKIPPED_SCHEMES = ("mailto:", "tel:", "javascript:", "#")
_REDIRECTS = (301, 302, 303, 307, 308)


class _Refused(Exception):
    """A request or redirect hop to a switched-off ATS's host: not sent."""


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
    def __init__(self, client: HttpClient, *, cfg: CrawlConfig | None = None):
        self.client = client
        self.cfg = cfg or load_crawl_config()

    def _allowed(self, url: str) -> None:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        ats = self.cfg.off_host(host)
        if ats is not None:
            raise _Refused(f"{host}: the {ats} reader is switched off ({self.cfg.ats_off[ats]}), not sending")

    def _get(self, url: str) -> Response:
        """GET `url`, following redirects here so each hop passes the host block first. The answer carries the URL
        it came from. A redirect left after `max_redirects` hops is returned as it is (an unexpected status)."""
        self._allowed(url)
        resp = self.client.get(url, follow_redirects=False)
        hops = 0
        while resp.status in _REDIRECTS and resp.headers.get("location") and hops < self.cfg.max_redirects:
            hops += 1
            url = urllib.parse.urljoin(resp.url or url, resp.headers["location"])
            self._allowed(url)
            resp = self.client.get(url, follow_redirects=False)
        if resp.url:
            self._allowed(resp.url)  # a client that followed a redirect on its own: its page is not read
        return resp if resp.url else dataclasses.replace(resp, url=url)

    def check(self, domain: str) -> CareersResult:
        domain = domain.strip().lower()
        careers_url = f"https://{domain}/careers"
        try:
            resp = self._get(careers_url)
            if resp.status == 200:
                return _read(resp, careers_url, domain)
            if not _is_no(resp.status):
                return _unexpected(resp, careers_url)
            home_url = f"https://{domain}/"
            home = self._get(home_url)
            if home.status != 200:
                return CareersResult("none") if _is_no(home.status) else _unexpected(home, home_url)
            link = _first_link(home, home_url, domain, skip={careers_url.rstrip("/")})
            if link is None:
                return CareersResult("none")
            resp = self._get(link)
            if resp.status == 200:
                return _read(resp, link, domain)
            return CareersResult("none") if _is_no(resp.status) else _unexpected(resp, link)
        except (FetchError, _Refused) as err:
            return CareersResult("error", error=str(err))


def _is_no(status: int) -> bool:
    """Only 404 and 410 say the page is not there. A 403, a 401 or a 400 may be a bot block: nothing concluded."""
    return status in (404, 410)


def _unexpected(resp: Response, url: str) -> CareersResult:
    return CareersResult("error", error=f"HTTP {resp.status} for {url}")


def _read(resp: Response, requested: str, domain: str) -> CareersResult:
    """Detect on the page. When a redirect left the company's site (a parent's page, an ATS host) the body is not the
    company's: only the final URL is read, so a redirect straight to a board still counts and nothing else does."""
    final = resp.url or requested
    on_site = _same_site((urllib.parse.urlsplit(final).hostname or "").lower(), domain)
    text = final + "\n" + resp.text() if on_site else final
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
