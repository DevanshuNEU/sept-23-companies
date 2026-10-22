"""BambooHR: the careers embed widget, posting text from the detail JSON (#21, Task 25).

`{token}.bamboohr.com/jobs/embed2.php` answers an HTML widget: `BambooHR-ATS-Jobs-Item` entries with the position id
in `bhrPositionID_{id}`, the title in the link and the place in `BambooHR-ATS-Location`. (The `/careers/list` JSON
that the research table names was retired in 2024; `probe` confirms what a tenant answers before this is trusted.)

A 200 is the widget only when an element carries a `BambooHR-ATS` class. A login page, a challenge page or an error
page does not, so it is `error`; the widget with no jobs is `empty` (Review Focus 2). A widget cut off in the middle
of an item, or before its root element closes, is `error`: a short list read as `ok` would close real postings.

There is no ETag and the raw page changes, so the fingerprint is over the ids. `body()` asks the detail endpoint for
JSON, builds its URL from the token and the id (never from a URL the list returned) and follows no redirect.

The token becomes a host label, so it must be one: letters, digits and inner hyphens.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

from outreach.crawl.ats._common import fetch_list, finish_list, read_error, safe_token
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.text import html_to_text
from outreach.net import HttpClient

__all__ = ["BambooHRAdapter"]

_LIST = "https://{token}.bamboohr.com/jobs/embed2.php"
_POSTING = "https://{token}.bamboohr.com/careers/{id}"
_DETAIL = "https://{token}.bamboohr.com/careers/{id}/detail"
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")
_POSITION = re.compile(r"bhrPositionID_([A-Za-z0-9_-]+)")
_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"})


def _words(parts: list[str]) -> str:
    return " ".join("".join(parts).split())


class _Widget(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.marker = False
        self.rows: list[tuple[str, str, str]] = []  # (id or "", title, location)
        self._root: tuple[str, int] | None = None
        self._root_closed = False
        self._item: list | None = None  # [tag, depth, id, title parts, title done, in link, location parts or None]
        self._loc: tuple[str, int] | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").lower().split()
        if any(c.startswith("bamboohr-ats") for c in classes):
            self.marker = True
        if tag in _VOID:
            return
        if self._root is None and not self._root_closed and "bamboohr-ats-board" in classes:
            self._root = (tag, 1)
        elif self._root is not None and tag == self._root[0]:
            self._root = (tag, self._root[1] + 1)
        item = self._item
        if item is None:
            if "bamboohr-ats-jobs-item" in classes:
                found = _POSITION.fullmatch(attrs.get("id") or "")
                self._item = [tag, 1, found.group(1) if found else "", [], False, False, None]
            return
        if tag == item[0]:
            item[1] += 1
        if tag == "a" and not item[4] and not item[5]:
            item[5] = True
        if "bamboohr-ats-location" in classes and self._loc is None:
            self._loc = (tag, 1)
            item[6] = []
        elif self._loc is not None and tag == self._loc[0]:
            self._loc = (tag, self._loc[1] + 1)

    def handle_data(self, data):
        item = self._item
        if item is None:
            return
        if item[5]:
            item[3].append(data)
        if self._loc is not None:
            item[6].append(data)

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        item = self._item
        if item is not None:
            if tag == "a" and item[5]:
                item[5], item[4] = False, True
            if self._loc is not None and tag == self._loc[0]:
                depth = self._loc[1] - 1
                self._loc = (tag, depth) if depth else None
            if tag == item[0]:
                item[1] -= 1
                if item[1] == 0:
                    self.rows.append((item[2], _words(item[3]), _words(item[6] or [])))
                    self._item = None
                    self._loc = None
        if self._root is not None and tag == self._root[0]:
            depth = self._root[1] - 1
            self._root = (tag, depth) if depth else None
            self._root_closed = self._root is None

    @property
    def cut_off(self) -> bool:
        return self._item is not None or self._root is not None


def _parse(token: str, rows: list[tuple[str, str, str]]) -> list[JobRef] | None:
    jobs = []
    for native_id, title, location in rows:
        if not native_id or not title:
            return None
        jobs.append(JobRef(native_id=native_id, title=title, url=_POSTING.format(token=token, id=native_id),
                           location=location, country=None, posted_on=None, body=None))
    return jobs


def _valid(token: str) -> bool:
    return safe_token("bamboohr", token) and _LABEL.fullmatch(token) is not None


class BambooHRAdapter:
    ats = "bamboohr"

    def __init__(self, client: HttpClient):
        self.client = client

    def list(self, token: str, validators: Validators) -> ReadResult:
        if not _valid(token):
            return read_error(f"{token!r} is not a valid bamboohr token")
        resp = fetch_list(self.client, _LIST.format(token=token), validators, use_etag=False)
        if isinstance(resp, ReadResult):
            return resp
        parser = _Widget()
        parser.feed(resp.text())
        parser.close()
        if not parser.marker:
            return read_error("the 200 answer is not the BambooHR careers widget", status=resp.status)
        if parser.cut_off:
            return read_error("the careers widget is cut off", status=resp.status)
        jobs = _parse(token, parser.rows)
        if jobs is None:
            return read_error("a job has no usable id or no title", status=resp.status)
        return finish_list(jobs, resp, validators, use_etag=False)

    def body(self, token: str, job: JobRef) -> str | None:
        """The description's text from the detail JSON, None when it is empty. A failed request, a redirect, an HTML
        page or JSON without `result.jobOpening.description` raises, so no empty body is stored by mistake."""
        if not _valid(token) or not _SAFE_ID.fullmatch(job.native_id):
            raise ValueError(f"cannot fetch position {job.native_id!r} from {token!r}")
        resp = self.client.get(_DETAIL.format(token=token, id=job.native_id), {"Accept": "application/json"})
        if resp.status != 200:
            raise ValueError(f"HTTP {resp.status} for position {job.native_id}")
        data = resp.json()
        result = data.get("result") if isinstance(data, dict) else None
        opening = result.get("jobOpening") if isinstance(result, dict) else None
        description = opening.get("description") if isinstance(opening, dict) else None
        if not isinstance(description, str):
            raise ValueError(f"position {job.native_id} has no description in the detail answer")
        return html_to_text(description) or None
