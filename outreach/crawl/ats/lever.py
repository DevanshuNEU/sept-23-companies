"""Lever: one list request carries every posting with its text (#21, Task 5).

`api.lever.co/v0/postings/{token}?mode=json` answers a top-level list of postings, sends an ETag and answers 304 to
If-None-Match. A board found at `jobs.eu.lever.co` is stored as a plain `lever` token (#31), and the US API host
answers 404 for it, so a 404 is tried once on `api.eu.lever.co` (ruling 2026-10-22). A 404 from both is `error`.
"""
from __future__ import annotations

from outreach.crawl.ats._common import d10, fetch_list, finish_list, json_body, read_error, safe_token
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.text import html_to_text
from outreach.net import HttpClient, Response

__all__ = ["LeverAdapter"]

_US = "https://api.lever.co/v0/postings/{token}?mode=json"
_EU = "https://api.eu.lever.co/v0/postings/{token}?mode=json"


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _plain_or_html(plain, html_value) -> str:
    """The plain-text field when it has text, else the HTML field turned into text."""
    return _text(plain) or (html_to_text(html_value) if isinstance(html_value, str) else "")


def _location(job: dict) -> str:
    cats = job.get("categories")
    return _text(cats.get("location")) if isinstance(cats, dict) else ""


def _body(job: dict) -> str | None:
    """Description, then each list as its heading and items, then the closing text, one part per line."""
    parts = [_plain_or_html(job.get("descriptionPlain"), job.get("description"))]
    lists = job.get("lists")
    for item in lists if isinstance(lists, list) else ():
        if isinstance(item, dict):
            parts.append(_text(item.get("text")))
            parts.append(_plain_or_html("", item.get("content")))
    parts.append(_plain_or_html(job.get("additionalPlain"), job.get("additional")))
    return "\n".join(p for p in parts if p) or None


def _parse(job: object) -> JobRef | None:
    """One posting, or None when it lacks an id or a title."""
    if not isinstance(job, dict):
        return None
    native_id, title = _text(job.get("id")), _text(job.get("text"))
    if not native_id or not title:
        return None
    return JobRef(native_id=native_id, title=title, url=_text(job.get("hostedUrl")), location=_location(job),
                  country=_text(job.get("country")) or None, posted_on=d10(job.get("createdAt")), body=_body(job))


class LeverAdapter:
    ats = "lever"

    def __init__(self, client: HttpClient):
        self.client = client

    def _fetch(self, token: str, validators: Validators) -> Response | ReadResult:
        got = fetch_list(self.client, _US.format(token=token), validators, use_etag=True)
        if isinstance(got, ReadResult) and got.http_status == 404:
            got = fetch_list(self.client, _EU.format(token=token), validators, use_etag=True)
            if isinstance(got, ReadResult) and got.http_status == 404:
                return read_error("HTTP 404 from both the US and the EU Lever hosts", status=404)
        return got

    def list(self, token: str, validators: Validators) -> ReadResult:
        if not safe_token(self.ats, token):
            return read_error(f"{token!r} is not a valid lever token")
        resp = self._fetch(token, validators)
        if isinstance(resp, ReadResult):
            return resp
        data = json_body(resp)
        if isinstance(data, ReadResult):
            return data
        if not isinstance(data, list):
            return read_error("the 200 answer is not a list of postings", status=resp.status)
        jobs: list[JobRef] = []
        for item in data:
            job = _parse(item)
            if job is None:
                return read_error("a posting has no id or no title", status=resp.status)
            jobs.append(job)
        return finish_list(jobs, resp, validators, use_etag=True)

    def body(self, token: str, job: JobRef) -> str | None:
        return None
