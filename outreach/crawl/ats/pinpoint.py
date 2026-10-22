"""Pinpoint: one list request that carries every posting with its text (#21, Task 22).

`{token}.pinpointhq.com/postings.json` answers `{"data": [...]}`, sends an ETag and answers 304 to If-None-Match. A
tenant with no careers site redirects: the request never follows redirects, so that is `error`. The body must be an
object whose `data` is a list (a missing key or `null` is `error`, never `empty`, Review Focus 2). The description is
HTML-escaped text, so it is unescaped and then turned into plain text; `body()` has nothing to fetch.

The token becomes a host label, so it must be one: letters, digits and inner hyphens.
"""
from __future__ import annotations

import html
import re
import urllib.parse

from outreach.crawl.ats._common import d10, fetch_list, finish_list, json_body, read_error, safe_token
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.text import html_to_text
from outreach.net import HttpClient

__all__ = ["PinpointAdapter"]

_LIST = "https://{token}.pinpointhq.com/postings.json"
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
_POSTING_URL = "https://{token}.pinpointhq.com/en/postings/{id}"
_WEB_URL = re.compile(r"https?://[^\s]+")


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _native_id(raw) -> str:
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        return ""
    return str(raw).strip()


def _location(value) -> str:
    """The location's `name`, else its city and province."""
    if not isinstance(value, dict):
        return ""
    return _text(value.get("name")) or ", ".join(p for p in (_text(value.get("city")), _text(value.get("province"))) if p)


def _body(description) -> str | None:
    if not isinstance(description, str):
        return None
    return html_to_text(html.unescape(description)) or None


def _parse(token: str, item: object) -> JobRef | None:
    """One posting, or None when it lacks an id or a title."""
    if not isinstance(item, dict):
        return None
    native_id, title = _native_id(item.get("id")), _text(item.get("title"))
    if not native_id or not title:
        return None
    url = _text(item.get("url"))
    if not _WEB_URL.fullmatch(url):
        url = _POSTING_URL.format(token=token, id=urllib.parse.quote(native_id, safe=""))
    return JobRef(native_id=native_id, title=title, url=url, location=_location(item.get("location")),
                  country=None, posted_on=d10(item.get("first_published_at")),
                  body=_body(item.get("description")))


class PinpointAdapter:
    ats = "pinpoint"

    def __init__(self, client: HttpClient):
        self.client = client

    def list(self, token: str, validators: Validators) -> ReadResult:
        if not (safe_token(self.ats, token) and _LABEL.fullmatch(token)):
            return read_error(f"{token!r} is not a valid pinpoint token")
        resp = fetch_list(self.client, _LIST.format(token=token), validators, use_etag=True)
        if isinstance(resp, ReadResult):
            return resp
        data = json_body(resp)
        if isinstance(data, ReadResult):
            return data
        postings = data.get("data") if isinstance(data, dict) else None
        if not isinstance(postings, list):
            return read_error("the 200 answer is not an object with a data list", status=resp.status)
        jobs: list[JobRef] = []
        for item in postings:
            job = _parse(token, item)
            if job is None:
                return read_error("a posting has no id or no title", status=resp.status)
            jobs.append(job)
        return finish_list(jobs, resp, validators, use_etag=True)

    def body(self, token: str, job: JobRef) -> str | None:
        return None
