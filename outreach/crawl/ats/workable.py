"""Workable: one widget request carries every posting with its text (#21, Task 19).

`apply.workable.com/api/v1/widget/accounts/{token}?details=true` answers `{"name", "jobs": [...]}` and sends no ETag,
so an unchanged board is told by the fingerprint of the shortcodes. The widget answers 200 for slugs that belong to
nobody, with an empty `jobs` list: that is `empty`, and the `board_empty` item is the right outcome. A 200 whose body
is not an object with a `jobs` list is `error`, never `empty` (Review Focus 2).

`body()` is the fallback for a posting whose list entry carries no description: the posting's `.md` page.
"""
from __future__ import annotations

import re

from outreach.crawl.ats._common import d10, fetch_list, finish_list, json_body, read_error, safe_token
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.text import html_to_text
from outreach.net import HttpClient

__all__ = ["WorkableAdapter"]

_LIST = "https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"
_MARKDOWN = "https://apply.workable.com/{token}/jobs/view/{shortcode}.md"
_PUBLIC = "https://apply.workable.com/{token}/j/{shortcode}/"
_SAFE_CODE = re.compile(r"[A-Za-z0-9_-]+")
_HTML_START = re.compile(r"\s*(<!doctype\s+html|<html)", re.IGNORECASE)


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _parse(token: str, job: object) -> JobRef | None:
    """One posting, or None when it lacks a shortcode or a title."""
    if not isinstance(job, dict):
        return None
    shortcode, title = _text(job.get("shortcode")), _text(job.get("title"))
    if not shortcode or not title:
        return None
    country = _text(job.get("country"))
    location = ", ".join(p for p in (_text(job.get("city")), _text(job.get("state")), country) if p)
    description = job.get("description")
    body = html_to_text(description) if isinstance(description, str) else ""
    return JobRef(
        native_id=shortcode, title=title,
        url=_text(job.get("url")) or _text(job.get("shortlink")) or _PUBLIC.format(token=token, shortcode=shortcode),
        location=location, country=country.upper() if len(country) == 2 and country.isalpha() else None,
        posted_on=d10(job.get("published_on")) or d10(job.get("created_at")), body=body or None)


class WorkableAdapter:
    ats = "workable"

    def __init__(self, client: HttpClient):
        self.client = client

    def list(self, token: str, validators: Validators) -> ReadResult:
        if not safe_token(self.ats, token):
            return read_error(f"{token!r} is not a valid workable token")
        resp = fetch_list(self.client, _LIST.format(token=token), validators)
        if isinstance(resp, ReadResult):
            return resp
        data = json_body(resp)
        if isinstance(data, ReadResult):
            return data
        raw = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            return read_error("the 200 answer has no jobs list", status=resp.status)
        jobs: list[JobRef] = []
        for item in raw:
            job = _parse(token, item)
            if job is None:
                return read_error("a posting has no shortcode or no title", status=resp.status)
            jobs.append(job)
        return finish_list(jobs, resp, validators, board_name=_text(data.get("name")))

    def body(self, token: str, job: JobRef) -> str | None:
        """The posting's markdown, or None when it is blank. A failed request, a redirect or an HTML page (a
        challenge) raises, so the caller counts a failed fetch instead of storing a page that is not the posting."""
        if not safe_token(self.ats, token) or not _SAFE_CODE.fullmatch(job.native_id):
            raise ValueError(f"cannot fetch {job.native_id!r} from {token!r}")
        resp = self.client.get(_MARKDOWN.format(token=token, shortcode=job.native_id))
        if resp.status != 200:
            raise ValueError(f"HTTP {resp.status} for posting {job.native_id}")
        text = resp.text()
        if _HTML_START.match(text):
            raise ValueError(f"posting {job.native_id} answered a web page, not markdown")
        return text.strip() or None
