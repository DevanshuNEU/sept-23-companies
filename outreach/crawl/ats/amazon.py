"""Amazon: the public search JSON of amazon.jobs (#237, 2026-11-12). Its robots.txt disallows only the `/internal` pages.

`/en/search.json?country[]=USA&category[]=<category>&result_limit=100&offset=N&sort=recent` answers
`{"error": null, "hits": n, "jobs": [...]}`, newest first. Each job carries its text (description, basic and preferred
qualifications), so no detail request is ever made. The token names the category: `amazon` (the board the import
left) reads `software-development`; any other token is a category slug as amazon.jobs spells it.

A list is read page by page until `hits` are seen, at most `MAX_PAGES` pages. As for Workday and Oracle (#232), a
broken row is skipped and a list a little short of `hits` (`short_allowed`) is a partial `ok`: what it listed is kept
and nothing is closed. The list carries no ETag, so `unchanged` is the id fingerprint. `posted_date` ("November 11,
2026") is the board's date.
"""
from __future__ import annotations

import re
from datetime import datetime

from outreach.crawl.ats._common import fetch_list, finish_list, json_body, read_error
from outreach.crawl.ats.workday import short_allowed
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.net import HttpClient, Response

__all__ = ["AmazonAdapter", "search_url", "posted_on"]

_SITE = "https://www.amazon.jobs"
_SEARCH = (_SITE + "/en/search.json?country%5B%5D=USA&category%5B%5D={category}&result_limit=" + "{limit}"
           "&offset={offset}&sort=recent")
PAGE = 100
MAX_PAGES = 60  # 6,000 postings: far above the 2,600 US software development jobs of 2026-11-12
_CATEGORY = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_DEFAULT_CATEGORY = {"amazon": "software-development"}
_PATH = re.compile(r"/[A-Za-z0-9/_.~%-]+")


def search_url(category: str, offset: int) -> str:
    return _SEARCH.format(category=category, limit=PAGE, offset=offset)


def posted_on(value) -> str | None:
    """"November 11, 2026" as 2026-11-11; anything else is None."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _category(token: str) -> str | None:
    category = _DEFAULT_CATEGORY.get(token, token)
    return category if isinstance(category, str) and _CATEGORY.fullmatch(category) else None


def _body(job: dict) -> str | None:
    parts = [_text(job.get("description"))]
    for heading, key in (("Basic qualifications", "basic_qualifications"),
                         ("Preferred qualifications", "preferred_qualifications")):
        if text := _text(job.get(key)):
            parts.append(f"{heading}:\n{text}")
    return "\n\n".join(p for p in parts if p) or None


def _parse(job: object) -> JobRef | None:
    """One posting, or None when it lacks an id, a title or a usable path."""
    if not isinstance(job, dict):
        return None
    native_id, title, path = _text(job.get("id_icims")), _text(job.get("title")), _text(job.get("job_path"))
    if not native_id.isdigit() or not title or not _PATH.fullmatch(path):
        return None
    return JobRef(native_id=native_id, title=title, url=_SITE + path,
                  location=_text(job.get("normalized_location")) or _text(job.get("location")),
                  country="US" if _text(job.get("country_code")) == "USA" else None,
                  posted_on=posted_on(job.get("posted_date")), body=_body(job))


class AmazonAdapter:
    ats = "amazon"

    def __init__(self, client: HttpClient):
        self.client = client

    def _page(self, category: str, offset: int) -> tuple[list, int, Response] | ReadResult:
        got = fetch_list(self.client, search_url(category, offset), Validators())
        if isinstance(got, ReadResult):
            return got
        data = json_body(got)
        if isinstance(data, ReadResult):
            return data
        if not isinstance(data, dict) or data.get("error") or not isinstance(data.get("jobs"), list):
            return read_error("the 200 answer is not an amazon.jobs search result", status=got.status)
        hits = data.get("hits")
        if isinstance(hits, bool) or not isinstance(hits, int) or hits < 0:
            return read_error("the 200 answer has no hits count", status=got.status)
        return data["jobs"], hits, got

    def list(self, token: str, validators: Validators) -> ReadResult:
        category = _category(token)
        if category is None:
            return read_error(f"{token!r} is not an amazon.jobs category")
        jobs: dict[str, JobRef] = {}
        broken = 0
        total = 0
        resp: Response | None = None
        for n in range(MAX_PAGES):
            got = self._page(category, n * PAGE)
            if isinstance(got, ReadResult):
                return got
            rows, hits, resp = got
            if n == 0:
                total = hits
            before = len(jobs)
            for item in rows:
                parsed = _parse(item)
                if parsed is None:
                    broken += 1
                    continue
                jobs.setdefault(parsed.native_id, parsed)
            if len(jobs) + broken >= total or not rows or len(jobs) == before:
                break
        if resp is None:
            return read_error("no page was read")
        if len(jobs) > total or total - len(jobs) > short_allowed(total):
            return read_error(f"{len(jobs)} postings listed but hits is {total}", status=resp.status)
        missing = total - len(jobs)
        note = (f"{len(jobs)} of {total} postings read ({broken} broken, {max(missing - broken, 0)} missing)"
                if missing or broken else "")
        return finish_list(jobs.values(), resp, validators, partial_note=note)

    def body(self, token: str, job: JobRef) -> str | None:
        return None  # the list carries every posting's text
