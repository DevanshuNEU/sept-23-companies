"""SmartRecruiters: paged list requests, posting text from a detail request per posting (#21, Task 17).

`api.smartrecruiters.com/v1/companies/{token}/postings?limit=100&offset=N` answers
`{"offset", "limit", "totalFound", "content": [...]}`. The first page sends an ETag (its `totalFound` changes with the
count, so a posting added or removed flips it) and answers 304 to If-None-Match. Later pages carry no validator.

A list that may be incomplete is `error`, never `ok` (Review Focus 1): the distinct ids collected must equal the
first page's `totalFound`, and a later page that fails, comes back empty early or repeats ids ends the read as `error`.
The list carries no text, so `body()` asks for each posting's detail.
"""
from __future__ import annotations

import re

from outreach.crawl.ats._common import d10, fetch_list, finish_list, json_body, read_error, safe_token
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.text import html_to_text
from outreach.net import HttpClient, Response

__all__ = ["SmartRecruitersAdapter"]

_LIST = "https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100&offset={offset}"
_DETAIL = "https://api.smartrecruiters.com/v1/companies/{token}/postings/{id}"
_POSTING_URL = "https://jobs.smartrecruiters.com/{token}/{id}"
_SECTIONS = ("companyDescription", "jobDescription", "qualifications", "additionalInformation")
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _native_id(raw) -> str:
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        return ""
    return str(raw).strip()


def _parse(token: str, item: object) -> JobRef | None:
    """One posting, or None when it lacks an id or a name."""
    if not isinstance(item, dict):
        return None
    native_id, title = _native_id(item.get("id")), _text(item.get("name"))
    if not native_id or not title:
        return None
    loc = item.get("location")
    loc = loc if isinstance(loc, dict) else {}
    country = _text(loc.get("country")).upper()
    return JobRef(native_id=native_id, title=title, url=_POSTING_URL.format(token=token, id=native_id),
                  location=", ".join(p for p in (_text(loc.get("city")), _text(loc.get("region"))) if p),
                  country=country if len(country) == 2 and country.isalpha() else None,
                  posted_on=d10(item.get("releasedDate")), body=None)


def _page(resp: Response) -> tuple[list, int] | ReadResult:
    """The postings and `totalFound` of one 200 page, or the error read for a page of another shape."""
    data = json_body(resp)
    if isinstance(data, ReadResult):
        return data
    content = data.get("content") if isinstance(data, dict) else None
    total = data.get("totalFound") if isinstance(data, dict) else None
    if not isinstance(content, list) or isinstance(total, bool) or not isinstance(total, int) or total < 0:
        return read_error("the 200 answer has no content list and totalFound count", status=resp.status)
    return content, total


class SmartRecruitersAdapter:
    ats = "smartrecruiters"

    def __init__(self, client: HttpClient):
        self.client = client

    def list(self, token: str, validators: Validators) -> ReadResult:
        if not safe_token(self.ats, token):
            return read_error(f"{token!r} is not a valid smartrecruiters token")
        first = fetch_list(self.client, _LIST.format(token=token, offset=0), validators, use_etag=True)
        if isinstance(first, ReadResult):
            return first
        parsed = _page(first)
        if isinstance(parsed, ReadResult):
            return parsed
        content, total = parsed
        jobs: dict[str, JobRef] = {}
        name = ""
        offset = 0
        resp = first
        while True:
            for item in content:
                job = _parse(token, item)
                if job is None:
                    return read_error("a posting has no id or no name", status=resp.status)
                if not name and isinstance(item.get("company"), dict):
                    name = _text(item["company"].get("name"))
                if job.native_id in jobs:
                    return read_error(f"posting {job.native_id} is listed twice (the pages moved under the read)",
                                      status=resp.status)
                jobs[job.native_id] = job
            if len(jobs) >= total:
                break
            if not content:
                return read_error(f"the pages ended with {len(jobs)} postings but totalFound is {total}",
                                  status=resp.status)
            offset += len(content)
            # Later pages carry no validator: a 304 here is not a reading of the board.
            got = fetch_list(self.client, _LIST.format(token=token, offset=offset), Validators(), use_etag=False)
            if isinstance(got, ReadResult):
                return got
            parsed = _page(got)
            if isinstance(parsed, ReadResult):
                return parsed
            resp, (content, later_total) = got, parsed
            if later_total != total:
                return read_error(f"totalFound moved from {total} to {later_total} during the read",
                                  status=resp.status)
        if len(jobs) != total:
            return read_error(f"{len(jobs)} postings listed but totalFound is {total}", status=first.status)
        return finish_list(jobs.values(), first, validators, use_etag=True, board_name=name)

    def body(self, token: str, job: JobRef) -> str | None:
        """The posting's text, or None when it has none. A failed request or a wrong shape raises, so the caller
        counts a failed fetch instead of storing an empty body."""
        if not safe_token(self.ats, token) or not _SAFE_ID.fullmatch(job.native_id):
            raise ValueError(f"cannot fetch {job.native_id!r} from {token!r}")
        resp = self.client.get(_DETAIL.format(token=token, id=job.native_id))
        if resp.status != 200:
            raise ValueError(f"HTTP {resp.status} for posting {job.native_id}")
        data = resp.json()
        ad = data.get("jobAd") if isinstance(data, dict) else None
        sections = ad.get("sections") if isinstance(ad, dict) else None
        if not isinstance(sections, dict):
            raise ValueError(f"posting {job.native_id} has no jobAd sections")
        parts = []
        for key in _SECTIONS:
            section = sections.get(key)
            text = html_to_text(section["text"]) if isinstance(section, dict) and isinstance(
                section.get("text"), str) else ""
            if text:
                parts.append(text)
        return "\n".join(parts) or None
