"""Rippling: one list request, posting text from a detail request per posting (#21, Task 21).

`api.rippling.com/platform/api/ats/v1/board/{token}/jobs` answers a top-level list of postings, or an object whose
`items` or `jobs` is that list. It answers `no-store`, so there is no ETag: `unchanged` is the id fingerprint. Anything
else, a missing key and `null` included, is `error`, never `empty` (Review Focus 2). The list has no text and no dates;
`body()` asks for the detail, which carries `description.role` and `description.company` as HTML (the role first).
"""
from __future__ import annotations

import re

from outreach.crawl.ats._common import fetch_list, finish_list, json_body, read_error, safe_token
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.text import html_to_text
from outreach.net import HttpClient

__all__ = ["RipplingAdapter"]

_LIST = "https://api.rippling.com/platform/api/ats/v1/board/{token}/jobs"
_DETAIL = "https://api.rippling.com/platform/api/ats/v1/board/{token}/jobs/{id}"
_POSTING_URL = "https://ats.rippling.com/{token}/jobs/{id}"
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")
_WEB_URL = re.compile(r"https?://[^\s]+")


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _native_id(item: dict) -> str:
    """The posting's `uuid` (else `id`) as text, or '' when it is missing or not a safe path segment."""
    raw = item.get("uuid")
    if raw is None or raw == "":
        raw = item.get("id")
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        return ""
    text = str(raw).strip()
    return text if _SAFE_ID.fullmatch(text) else ""


def _location(value) -> str:
    if isinstance(value, dict):
        return _text(value.get("label")) or _text(value.get("displayName"))
    return _text(value)


def _number(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _more_than_listed(data: dict, listed: int) -> str:
    """Why an object-form answer looks like a page of a larger list, or '' when it does not.

    A total above what was parsed, more than one page, `hasMore` true or a non-empty `next` all mean postings are
    missing from this read, and a read that lacks postings must be `error`, never `ok`.
    """
    for key in ("totalItems", "total", "count"):
        total = _number(data.get(key))
        if total is not None and total > listed:
            return f"{key} is {total}, {listed} listed"
    pages = _number(data.get("totalPages"))
    if pages is not None and pages > 1:
        return f"totalPages is {pages}"
    if data.get("hasMore") is True:
        return "hasMore is true"
    if data.get("next"):
        return "next is set"
    return ""


def _parse(token: str, item: object) -> JobRef | None:
    """One posting, or None when it lacks a usable id or a name."""
    if not isinstance(item, dict):
        return None
    native_id, title = _native_id(item), _text(item.get("name")) or _text(item.get("title"))
    if not native_id or not title:
        return None
    url = _text(item.get("url")) or _text(item.get("hostedUrl"))
    if not _WEB_URL.fullmatch(url):
        url = _POSTING_URL.format(token=token, id=native_id)
    return JobRef(native_id=native_id, title=title, url=url, location=_location(item.get("workLocation")),
                  country=None, posted_on=None, body=None)


class RipplingAdapter:
    ats = "rippling"

    def __init__(self, client: HttpClient):
        self.client = client

    def list(self, token: str, validators: Validators) -> ReadResult:
        if not safe_token(self.ats, token):
            return read_error(f"{token!r} is not a valid rippling token")
        resp = fetch_list(self.client, _LIST.format(token=token), validators)
        if isinstance(resp, ReadResult):
            return resp
        data = json_body(resp)
        if isinstance(data, ReadResult):
            return data
        items = data if isinstance(data, list) else None
        paged = None
        if isinstance(data, dict):
            items = next((data[key] for key in ("items", "jobs") if isinstance(data.get(key), list)), None)
            paged = data
        if items is None:
            return read_error("the 200 answer is not a list of postings or an object with an items or jobs list",
                              status=resp.status)
        jobs: list[JobRef] = []
        for item in items:
            job = _parse(token, item)
            if job is None:
                return read_error("a posting has no usable id or no name", status=resp.status)
            jobs.append(job)
        more = _more_than_listed(paged, len({j.native_id for j in jobs})) if paged is not None else ""
        if more:
            return read_error(f"the list may be one page of several ({more})", status=resp.status)
        return finish_list(jobs, resp, validators)

    def body(self, token: str, job: JobRef) -> str | None:
        """The posting's text, or None when it has none. A failed request or a wrong shape raises, so the caller
        counts a failed fetch instead of storing an empty body."""
        if not safe_token(self.ats, token) or not _SAFE_ID.fullmatch(job.native_id):
            raise ValueError(f"cannot fetch {job.native_id!r} from {token!r}")
        resp = self.client.get(_DETAIL.format(token=token, id=job.native_id))
        if resp.status != 200:
            raise ValueError(f"HTTP {resp.status} for posting {job.native_id}")
        data = resp.json()
        desc = data.get("description") if isinstance(data, dict) else None
        if not isinstance(desc, dict) or not any(isinstance(desc.get(k), str) for k in ("role", "company")):
            raise ValueError(f"posting {job.native_id} has no description.company or description.role")
        texts = (html_to_text(desc[k]) if isinstance(desc.get(k), str) else "" for k in ("role", "company"))
        return "\n".join(t for t in texts if t) or None
