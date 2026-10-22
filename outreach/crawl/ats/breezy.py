"""Breezy: one list request, posting text from the position page (#21, Task 22).

`{token}.breezy.hr/json` answers a top-level list, sends an ETag and answers 304 to If-None-Match. A tenant with no
careers site answers a 302 to the marketing page: the list request never follows redirects, so that is `error`, not an
empty board (Review Focus 2). The list carries no text, so `body()` reads the position page (redirects followed) and
takes the element with class `description`; a page without one raises instead of storing nothing.

The token becomes a host label, so it must be one: letters, digits and inner hyphens. A posting's own URL is used only
when it is an https address on the tenant's host; otherwise it is `https://{token}.breezy.hr/p/{id}`, so `body()` can
only ever ask the tenant's own host.
"""
from __future__ import annotations

import re
import urllib.parse
from html.parser import HTMLParser

from outreach.crawl.ats._common import d10, fetch_list, finish_list, json_body, read_error, safe_token
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.text import html_to_text
from outreach.net import HttpClient

__all__ = ["BreezyAdapter"]

_LIST = "https://{token}.breezy.hr/json"
_POSITION = "https://{token}.breezy.hr/p/{id}"
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")
_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"})


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _native_id(raw) -> str:
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        return ""
    text = str(raw).strip()
    return text if _SAFE_ID.fullmatch(text) else ""


def _valid(token: str) -> bool:
    return safe_token("breezy", token) and _LABEL.fullmatch(token) is not None


def _on_tenant_host(token: str, url: str) -> bool:
    """True for an https URL whose host is exactly `{token}.breezy.hr`, with no user, password or port."""
    try:
        parts = urllib.parse.urlsplit(url)
        return (parts.scheme == "https" and parts.hostname == f"{token}.breezy.hr" and parts.username is None
                and parts.password is None and parts.port is None)
    except ValueError:
        return False


def _location(value) -> str:
    """The structured location's `name`, else its city and state name."""
    if not isinstance(value, dict):
        return ""
    name = _text(value.get("name"))
    if name:
        return name
    state = value.get("state")
    return ", ".join(p for p in (_text(value.get("city")), _text(state.get("name")) if isinstance(state, dict) else "")
                     if p)


def _country(value) -> str | None:
    country = value.get("country") if isinstance(value, dict) else None
    code = _text(country.get("id")).upper() if isinstance(country, dict) else ""
    return code if len(code) == 2 and code.isalpha() else None


def _parse(token: str, item: object) -> JobRef | None:
    """One posting, or None when it lacks a usable id or a name."""
    if not isinstance(item, dict):
        return None
    native_id, title = _native_id(item.get("id")), _text(item.get("name"))
    if not native_id or not title:
        return None
    url = _text(item.get("url"))
    if not _on_tenant_host(token, url):
        url = _POSITION.format(token=token, id=native_id)
    loc = item.get("location")
    return JobRef(native_id=native_id, title=title, url=url, location=_location(loc), country=_country(loc),
                  posted_on=d10(item.get("published_date")), body=None)


class _Block(HTMLParser):
    """Finds the first element whose class list has `description` and remembers where its inner HTML starts and ends."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self._line_starts = [0] + [m.end() for m in re.finditer("\n", source)]
        self._tag: str | None = None
        self._depth = 0
        self.begin: int | None = None
        self.end: int | None = None

    def _here(self) -> int:
        line, col = self.getpos()
        return self._line_starts[line - 1] + col

    def handle_starttag(self, tag, attrs):
        if self.end is not None or tag in _VOID:
            return
        if self._tag is None:
            classes = (dict(attrs).get("class") or "").lower().split()
            if "description" in classes:
                self._tag, self._depth = tag, 1
                self.begin = self._here() + len(self.get_starttag_text())
        elif tag == self._tag:
            self._depth += 1

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if self._tag is not None and self.end is None and tag == self._tag:
            self._depth -= 1
            if self._depth == 0:
                self.end = self._here()


def _description(page: str) -> str | None:
    """The plain text of the description block, '' when it is empty, None when the page has no such block."""
    parser = _Block(page)
    parser.feed(page)
    parser.close()
    if parser.begin is None:
        return None
    return html_to_text(page[parser.begin:parser.end if parser.end is not None else len(page)])


class BreezyAdapter:
    ats = "breezy"

    def __init__(self, client: HttpClient):
        self.client = client

    def list(self, token: str, validators: Validators) -> ReadResult:
        if not _valid(token):
            return read_error(f"{token!r} is not a valid breezy token")
        resp = fetch_list(self.client, _LIST.format(token=token), validators, use_etag=True)
        if isinstance(resp, ReadResult):
            return resp
        data = json_body(resp)
        if isinstance(data, ReadResult):
            return data
        if not isinstance(data, list):
            return read_error("the 200 answer is not a list of positions", status=resp.status)
        jobs: list[JobRef] = []
        for item in data:
            job = _parse(token, item)
            if job is None:
                return read_error("a position has no usable id or no name", status=resp.status)
            jobs.append(job)
        return finish_list(jobs, resp, validators, use_etag=True)

    def body(self, token: str, job: JobRef) -> str | None:
        """The description block's text, or None when the block is empty. A failed request or a page with no
        description block raises, so the caller counts a failed fetch instead of storing an empty body."""
        if not _valid(token) or not _on_tenant_host(token, job.url):
            raise ValueError(f"cannot fetch {job.url!r} from {token!r}")
        resp = self.client.get(job.url, follow_redirects=True)
        if resp.status != 200:
            raise ValueError(f"HTTP {resp.status} for position {job.native_id}")
        text = _description(resp.text())
        if text is None:
            raise ValueError(f"position {job.native_id} has no description block")
        return text or None
