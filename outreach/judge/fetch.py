"""The judge's page reads: a company's homepage, its about page and, when the store has no body for it, its best
posting, each read politely and turned into plain text.

Politeness, in order, for every request (the redirect hops included, since redirects are followed here, one at a
time):
- only http and https, never LinkedIn (`is_linkedin_url`);
- the site's robots.txt, read once per site and run with the crawler's declared User-Agent (`urllib.robotparser`):
  a 404 or 410 means no rules (allowed), a 401 or 403 means nothing is allowed, and a robots.txt that cannot be read
  (another status, a timeout, a network error) means the page is not read;
- the crawler's polite client (`outreach.crawl.polite.PoliteClient`): its User-Agent, its per-host spacing, its
  breaker, and its switched-off hosts (`ats_off`, #46), which are never sent to;
- the judge's own client: its timeout and size cap (`[fetch] timeout_s`, `max_bytes`), and the size checked again here;
- only an HTML or plain text answer is read; a JavaScript shell or a challenge page (`crawl.watch.usable_text`) is
  not.
A page that cannot be read is `unavailable` with the reason in plain words, and never an error: the judge says so
and judges with what it has. The text given to the model is cut to `[fetch] page_max_chars`.
"""
from __future__ import annotations

import hashlib
import urllib.parse
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser

from outreach.crawl.config import CrawlConfig
from outreach.crawl.polite import HostTripped, PoliteClient
from outreach.crawl.text import html_to_text
from outreach.crawl.watch import usable_text
from outreach.judge.config import JudgeConfig
from outreach.net import FetchError, HttpClient, UrllibClient
from outreach.plan.people import is_linkedin_url
from outreach.rules.text import strip_invisible
from outreach.store import utcnow

__all__ = ["Fetched", "SiteReader", "build_reader", "unavailable"]

_REDIRECTS = (301, 302, 303, 307, 308)
_TEXT_TYPES = ("text/html", "text/plain", "application/xhtml+xml")
_ABOUT_PATHS = ("/about", "/about-us", "/company", "/our-story")


@dataclass(frozen=True)
class Fetched:
    page: dict  # kind, url, status (ok or unavailable), why, text, fetched_at, sha256
    markup: str = ""  # the HTML of a page that was read, for the about link; never stored


def unavailable(kind: str, url: str, why: str, now: str) -> Fetched:
    return Fetched({"kind": kind, "url": url, "status": "unavailable", "why": why, "text": "", "fetched_at": now,
                    "sha256": ""})


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


def _site(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


class SiteReader:
    """Reads pages for the judge. `client` is the polite client; robots.txt answers are kept for the reader's life
    (one run), so each site's robots.txt is read once a run."""

    def __init__(self, client: HttpClient, *, cfg: JudgeConfig, user_agent: str, max_redirects: int,
                 now: Callable[[], str] = utcnow):
        self.client, self.cfg, self.user_agent, self.max_redirects, self.now = (client, cfg, user_agent,
                                                                                max_redirects, now)
        self._robots: dict[str, urllib.robotparser.RobotFileParser | str] = {}

    def _rules(self, url: str) -> urllib.robotparser.RobotFileParser | str:
        """The site's parsed robots.txt, or the reason the site's pages are not read."""
        site = _site(url)
        if site not in self._robots:
            rules = urllib.robotparser.RobotFileParser()
            try:
                r = self.client.get(site + "/robots.txt", follow_redirects=True)
            except HostTripped as err:
                self._robots[site] = f"the host is not asked: {err}"
                return self._robots[site]
            except FetchError as err:
                self._robots[site] = f"robots.txt could not be read ({err})"
                return self._robots[site]
            if r.status == 200:
                rules.parse(r.body[:self.cfg.max_bytes].decode("utf-8", errors="replace").splitlines())
            elif r.status in (401, 403):
                rules.disallow_all = True
            elif 400 <= r.status < 500:
                rules.allow_all = True
            else:
                self._robots[site] = f"robots.txt could not be read (HTTP {r.status})"
                return self._robots[site]
            self._robots[site] = rules
        return self._robots[site]

    def _refusal(self, url: str) -> str | None:
        """Why `url` is not requested, or None when it may be."""
        try:
            parts = urllib.parse.urlsplit(url)
        except ValueError:
            return "not a web address"
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return "not a web address"
        if is_linkedin_url(url):
            return "LinkedIn is never read"
        rules = self._rules(url)
        if isinstance(rules, str):
            return rules
        if not rules.can_fetch(self.user_agent, url):
            return "robots.txt does not allow it"
        return None

    def read(self, url: str, kind: str) -> Fetched:
        """One page as plain text, or `unavailable` with the reason. Redirects are followed here, each hop asked of
        robots.txt first."""
        now = self.now()
        hops = 0
        while True:
            why = self._refusal(url)
            if why is not None:
                return unavailable(kind, url, why, now)
            try:
                r = self.client.get(url, follow_redirects=False)
            except HostTripped as err:
                return unavailable(kind, url, f"the host is not asked: {err}", now)
            except FetchError as err:
                return unavailable(kind, url, str(err), now)
            if r.status in _REDIRECTS and r.headers.get("location") and hops < self.max_redirects:
                hops += 1
                url = urllib.parse.urljoin(url, r.headers["location"])
                continue
            break
        if r.status != 200:
            return unavailable(kind, url, f"HTTP {r.status}", now)
        if len(r.body) > self.cfg.max_bytes:
            return unavailable(kind, url, f"too large: {len(r.body)} bytes over {self.cfg.max_bytes}", now)
        ctype = (r.headers.get("content-type") or "text/html").split(";")[0].strip().lower()
        if ctype not in _TEXT_TYPES:
            return unavailable(kind, url, f"not a web page ({ctype})", now)
        markup = r.text()
        if not usable_text(markup, self.cfg.min_text_chars):
            return unavailable(kind, url, "no readable text (a JavaScript page or a challenge page)", now)
        text = html_to_text(markup) if ctype != "text/plain" else strip_invisible(markup).strip()  # #112
        text = text[:self.cfg.page_max_chars]
        return Fetched({"kind": kind, "url": url, "status": "ok", "why": "", "text": text, "fetched_at": now,
                        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}, markup)

    @staticmethod
    def about_url(home: Fetched) -> str | None:
        """The first link on a homepage that was read to an about page on the same site, else None."""
        if home.page["status"] != "ok" or not home.markup:
            return None
        base = home.page["url"]
        links = _Links()
        links.feed(home.markup)
        site = _site(base)
        for href in links.hrefs:
            target = urllib.parse.urljoin(base, href.strip())
            parts = urllib.parse.urlsplit(target)
            if _site(target) == site and parts.path.rstrip("/").lower() in _ABOUT_PATHS:
                return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        return None


def build_reader(cfg: JudgeConfig, crawl: CrawlConfig) -> SiteReader:
    """The real reader: the judge's urllib client (declared profile, its timeout and size cap, no redirects of its
    own) inside the crawler's polite client. No test builds it."""
    inner = UrllibClient("declared", timeout_s=cfg.timeout_s, max_bytes=cfg.max_bytes, max_redirects=0)
    return SiteReader(PoliteClient(inner, crawl), cfg=cfg, user_agent=crawl.user_agent,
                      max_redirects=crawl.max_redirects)
