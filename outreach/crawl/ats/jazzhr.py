"""JazzHR: the public jobs page, posting text from the JSON-LD block of the detail page (#21, Task 26).

`{token}.applytojob.com/apply/jobs` answers an HTML page with one table row per job: the link has class
`job_title_link` and an href ending `/apply/jobs/details/{id}`, the second cell is the location. A 200 is the listing
only when an element has a `resumator...` class or id (the name in a stylesheet link does not count). A Cloudflare 403, a challenge page and anything else is `error`
(Review Focus 2), and no User-Agent or header is added to get past one: a blocked board is a Needs-you item.

A page that is cut off (no closing `</html>`, or ends inside a row) is `error`. So is a page where the count of
`job_title_link` anchors differs from the rows read, or when any link to a detail page, whatever its class, is not a
job read. So a markup change cannot turn into "no jobs" or a short list.

The raw page changes on every request, so there is no ETag: the fingerprint is over the ids. `body()` reads the
`application/ld+json` block whose `@type` is `JobPosting`. Its URL is built from the token and the id, and a redirect
is followed only while it stays on `https://{token}.applytojob.com`.

The token becomes a host label, so it must be one: letters, digits and inner hyphens.
"""
from __future__ import annotations

import html
import json
import re
import urllib.parse
from html.parser import HTMLParser

from outreach.crawl.ats._common import fetch_list, finish_list, read_error, safe_token
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.text import html_to_text
from outreach.net import HttpClient, Response

__all__ = ["JazzHRAdapter"]

_LIST = "https://{token}.applytojob.com/apply/jobs"
_DETAIL = "https://{token}.applytojob.com/apply/jobs/details/{id}"
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")
_DETAIL_PATH = re.compile(r"/apply/jobs/details/([A-Za-z0-9_-]+)/?")
_MAX_HOPS = 3
_REDIRECTS = (301, 302, 303, 307, 308)  # a 304 is not a redirect


def _words(parts: list[str]) -> str:
    return " ".join("".join(parts).split())


def _valid(token: str) -> bool:
    return safe_token("jazzhr", token) and _LABEL.fullmatch(token) is not None


class _Rows(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[str, str, str]] = []  # (href, title, location)
        self.links = 0
        self.container = False  # an element whose class or id is the listing's `resumator...` name
        self.detail_ids: set[str] = set()  # the id of every <a> that points at a detail page, whatever its class
        self._row: dict | None = None

    def _finish_cell(self) -> None:
        row = self._row
        if row is not None and row["cell"] is not None:
            row["cells"].append(_words(row["cell"]))
            row["cell"] = None

    def _finish_row(self) -> None:
        row = self._row
        if row is None:
            return
        self._finish_cell()
        if row["linked"]:
            cells = row["cells"]
            self.rows.append((row["href"], _words(row["title"]), cells[1] if len(cells) > 1 else ""))
        self._row = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        names = (attrs.get("class") or "").lower().split() + [(attrs.get("id") or "").lower()]
        if any(name.startswith("resumator") for name in names):
            self.container = True
        if tag == "a":
            found = _DETAIL_PATH.fullmatch(_path(attrs.get("href")))
            if found:
                self.detail_ids.add(found.group(1))
        if tag == "tr":
            self._finish_row()
            self._row = {"cells": [], "cell": None, "linked": False, "in_link": False, "href": "", "title": []}
        elif tag in ("td", "th") and self._row is not None:
            self._finish_cell()
            self._row["cell"] = []
        elif tag == "a" and "job_title_link" in (attrs.get("class") or "").split():
            self.links += 1
            row = self._row
            if row is not None and not row["linked"]:
                row["linked"], row["in_link"], row["href"] = True, True, (attrs.get("href") or "").strip()

    def handle_data(self, data):
        row = self._row
        if row is None:
            return
        if row["in_link"]:
            row["title"].append(data)
        if row["cell"] is not None:
            row["cell"].append(data)

    def handle_endtag(self, tag):
        row = self._row
        if row is None:
            return
        if tag == "a":
            row["in_link"] = False
        elif tag in ("td", "th"):
            self._finish_cell()
        elif tag == "tr":
            self._finish_row()

    @property
    def cut_off(self) -> bool:
        return self._row is not None


class _JsonLd(HTMLParser):
    """The text of every `<script type="application/ld+json">` block."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._open: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "script" and (dict(attrs).get("type") or "").strip().lower() == "application/ld+json":
            self._open = []

    def handle_data(self, data):
        if self._open is not None:
            self._open.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._open is not None:
            self.blocks.append("".join(self._open))
            self._open = None


def _job_posting(node) -> dict | None:
    if isinstance(node, list):
        for item in node:
            found = _job_posting(item)
            if found is not None:
                return found
    elif isinstance(node, dict):
        kind = node.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            return node
        return _job_posting(node.get("@graph"))
    return None


def _path(href) -> str:
    try:
        return urllib.parse.urlsplit((href or "").strip()).path
    except ValueError:
        return ""


def _on_tenant_host(token: str, url: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(url)
        return (parts.scheme == "https" and parts.hostname == f"{token}.applytojob.com" and parts.username is None
                and parts.password is None and parts.port is None)
    except ValueError:
        return False


class JazzHRAdapter:
    ats = "jazzhr"

    def __init__(self, client: HttpClient):
        self.client = client

    def list(self, token: str, validators: Validators) -> ReadResult:
        if not _valid(token):
            return read_error(f"{token!r} is not a valid jazzhr token")
        resp = fetch_list(self.client, _LIST.format(token=token), validators, use_etag=False)
        if isinstance(resp, ReadResult):
            return resp
        page = resp.text()
        lower = page.lower()
        parser = _Rows()
        parser.feed(page)
        parser.close()
        if not parser.container:
            return read_error("the 200 answer is not the JazzHR job listing (no resumator element)", status=resp.status)
        if parser.cut_off or "</html" not in lower:
            return read_error("the job listing is cut off", status=resp.status)
        jobs: list[JobRef] = []
        for href, title, location in parser.rows:
            found = _DETAIL_PATH.fullmatch(_path(href))
            if not found or not title:
                return read_error("a job row has no usable id or no title", status=resp.status)
            native_id = found.group(1)
            jobs.append(JobRef(native_id=native_id, title=title, url=_DETAIL.format(token=token, id=native_id),
                               location=location, country=None, posted_on=None, body=None))
        if parser.links != len(parser.rows) or parser.detail_ids != {job.native_id for job in jobs}:
            return read_error("a job link is not a table row read, so the list may be incomplete", status=resp.status)
        return finish_list(jobs, resp, validators, use_etag=False)

    def _get(self, token: str, url: str) -> Response:
        """GET `url`, following a redirect only while it stays on the tenant's own host."""
        for _hop in range(_MAX_HOPS + 1):
            resp = self.client.get(url)
            if resp.status not in _REDIRECTS:
                return resp
            target = urllib.parse.urljoin(url, resp.headers.get("location", ""))
            if not _on_tenant_host(token, target):
                raise ValueError(f"{url} redirects to {target!r}, which is not {token}.applytojob.com")
            url = target
        raise ValueError(f"too many redirects from {url}")

    def body(self, token: str, job: JobRef) -> str | None:
        """The JobPosting description's text, None when it is empty. A failed request, a redirect off the tenant's
        host, or a page with no JobPosting block and description raises, so no empty body is stored by mistake."""
        if not _valid(token) or not _SAFE_ID.fullmatch(job.native_id):
            raise ValueError(f"cannot fetch position {job.native_id!r} from {token!r}")
        resp = self._get(token, _DETAIL.format(token=token, id=job.native_id))
        if resp.status != 200:
            raise ValueError(f"HTTP {resp.status} for position {job.native_id}")
        parser = _JsonLd()
        parser.feed(resp.text())
        parser.close()
        for block in parser.blocks:
            try:
                posting = _job_posting(json.loads(block))
            except ValueError:
                continue
            if posting is not None:
                description = posting.get("description")
                if not isinstance(description, str):
                    raise ValueError(f"position {job.native_id} has no description in its JobPosting block")
                return html_to_text(html.unescape(description)) or None
        raise ValueError(f"position {job.native_id} has no JobPosting block")
