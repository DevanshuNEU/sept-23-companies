"""Greenhouse: one list request carries every posting, a detail request carries one posting's text (#21, #152).

`boards-api.greenhouse.io/v1/boards/{token}/jobs` answers `{"jobs": [...], "meta": {...}}`, sends an ETag and answers
304 to If-None-Match. It is read without `?content=true`: that flag adds every job's full HTML to the list, which is
most of the bytes of a big board and mostly postings nobody reads. A body comes from `.../jobs/{id}` instead, and
ingest asks for it only for the postings it wants (#152). A board found at `boards.eu.greenhouse.io` is stored as a
plain `greenhouse` token (#31), and the US host answers 404 for it, so a 404 is tried once on the EU API host. The
plan names no EU host: `boards-api.eu.greenhouse.io` is the API host that mirrors the US one. The adapter is made for
one board read, so it remembers which host answered the list and asks that host for the details.
"""
from __future__ import annotations

import html
import re

from outreach.crawl.ats._common import d10, fetch_list, finish_list, json_body, read_error, safe_token
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.text import html_to_text
from outreach.net import FetchError, HttpClient, Response

__all__ = ["GreenhouseAdapter"]

_US = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
_EU = "https://boards-api.eu.greenhouse.io/v1/boards/{token}/jobs"
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _location(job: dict) -> str:
    loc = job.get("location")
    if isinstance(loc, dict):
        return _text(loc.get("name"))
    return _text(loc)


def _native_id(job: dict) -> str:
    raw = job.get("id")
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        return ""
    return str(raw).strip()


def _parse(job: object) -> JobRef | None:
    """One posting, or None when it lacks an id or a title."""
    if not isinstance(job, dict):
        return None
    native_id, title = _native_id(job), _text(job.get("title"))
    if not native_id or not title:
        return None
    return JobRef(native_id=native_id, title=title, url=_text(job.get("absolute_url")), location=_location(job),
                  country=None, posted_on=d10(job.get("first_published")) or d10(job.get("updated_at")),
                  body=None)


class GreenhouseAdapter:
    ats = "greenhouse"

    def __init__(self, client: HttpClient):
        self.client = client
        self._listed_on: str | None = None  # the host template that answered this board's list, once it has

    def _fetch(self, token: str, validators: Validators) -> Response | ReadResult:
        got = fetch_list(self.client, _US.format(token=token), validators, use_etag=True)
        if not (isinstance(got, ReadResult) and got.http_status == 404):
            self._listed_on = _US
        else:
            got = fetch_list(self.client, _EU.format(token=token), validators, use_etag=True)
            if isinstance(got, ReadResult) and got.http_status == 404:
                return read_error("HTTP 404 from both the US and the EU Greenhouse hosts", status=404)
            self._listed_on = _EU
        return got

    def list(self, token: str, validators: Validators) -> ReadResult:
        if not safe_token(self.ats, token):
            return read_error(f"{token!r} is not a valid greenhouse token")
        resp = self._fetch(token, validators)
        if isinstance(resp, ReadResult):
            return resp
        data = json_body(resp)
        if isinstance(data, ReadResult):
            return data
        raw = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            return read_error("the 200 answer has no jobs list", status=resp.status)
        meta = data.get("meta")
        total = meta.get("total") if isinstance(meta, dict) else None
        if isinstance(total, int) and not isinstance(total, bool) and total != len(raw):
            return read_error("list length does not match meta.total", status=resp.status)
        jobs: list[JobRef] = []
        for item in raw:
            job = _parse(item)
            if job is None:
                return read_error("a posting has no id or no title", status=resp.status)
            jobs.append(job)
        name = next((n for n in (_text(j.get("company_name")) for j in raw) if n), "")
        return finish_list(jobs, resp, validators, use_etag=True, board_name=name)

    def _detail(self, url: str) -> Response | None:
        try:
            return self.client.get(url)
        except FetchError:
            return None

    def body(self, token: str, job: JobRef) -> str | None:
        """The posting's text from its detail answer, or None when there is none: a bad token or id, a 404, any other
        failure, a page that is not JSON or a blank `content`. One posting's failure never raises; only `HostTripped`
        does, because it says nothing about the posting."""
        if not safe_token(self.ats, token) or not _SAFE_ID.fullmatch(job.native_id):
            return None
        hosts = [self._listed_on] if self._listed_on else [_US, _EU]
        resp = None
        for host in hosts:
            resp = self._detail(f"{host.format(token=token)}/{job.native_id}")
            if resp is None or resp.status != 404:
                break
        if resp is None or resp.status != 200:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        content = data.get("content") if isinstance(data, dict) else None
        if not isinstance(content, str):
            return None
        return html_to_text(html.unescape(content)) or None
