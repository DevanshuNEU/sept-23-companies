"""Ashby: one list request carries every public posting with its text (#21, Task 6).

`api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true` answers `{"apiVersion": ..., "jobs": [...]}`,
sends an ETag and answers 304 to If-None-Match. A job with `isListed` false is not public, so it is skipped. Ashby
has one API host and no EU rule.
"""
from __future__ import annotations

from outreach.crawl.ats._common import d10, fetch_list, finish_list, json_body, read_error, safe_token
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.text import html_to_text
from outreach.net import HttpClient

__all__ = ["AshbyAdapter"]

_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _country(job: dict) -> str | None:
    address = job.get("address")
    postal = address.get("postalAddress") if isinstance(address, dict) else None
    return (_text(postal.get("addressCountry")) or None) if isinstance(postal, dict) else None


def _body(job: dict) -> str | None:
    html_value = job.get("descriptionHtml")
    return _text(job.get("descriptionPlain")) or (html_to_text(html_value) if isinstance(html_value, str) else "") \
        or None


def _parse(job: dict) -> JobRef | None:
    """One public posting, or None when it lacks an id or a title."""
    raw_id = job.get("id")
    native_id, title = (raw_id.strip() if isinstance(raw_id, str) else ""), _text(job.get("title"))
    if not native_id or not title:
        return None
    return JobRef(native_id=native_id, title=title, url=_text(job.get("jobUrl")), location=_text(job.get("location")),
                  country=_country(job), posted_on=d10(job.get("publishedAt") or job.get("updatedAt")),
                  body=_body(job))


class AshbyAdapter:
    ats = "ashby"

    def __init__(self, client: HttpClient):
        self.client = client

    def list(self, token: str, validators: Validators) -> ReadResult:
        if not safe_token(self.ats, token):
            return read_error(f"{token!r} is not a valid ashby token")
        resp = fetch_list(self.client, _URL.format(token=token), validators, use_etag=True)
        if isinstance(resp, ReadResult):
            return resp
        data = json_body(resp)
        if isinstance(data, ReadResult):
            return data
        raw = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
            return read_error("the 200 answer has no list of jobs", status=resp.status)
        jobs: list[JobRef] = []
        for item in raw:
            if item.get("isListed") is False:
                continue
            job = _parse(item)
            if job is None:
                return read_error("a posting has no id or no title", status=resp.status)
            jobs.append(job)
        return finish_list(jobs, resp, validators, use_etag=True)

    def body(self, token: str, job: JobRef) -> str | None:
        return None
