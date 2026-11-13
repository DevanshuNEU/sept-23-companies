"""Apple: the search pages of jobs.apple.com (#263, 2026-11-13). jobs.apple.com has no robots.txt (the path
redirects to a not-found page, checked 2026-11-13), so no page is refused.

`https://jobs.apple.com/en-us/search?location=united-states-USA&sort=newest&page=N` (N from 1) is server-rendered: its
`window.__staticRouterHydrationData = JSON.parse("...")` call holds `loaderData.search.searchResults` (20 a page,
newest first) and `loaderData.search.totalRecords` (4,551 US jobs on 2026-11-13). The argument is a JS string literal
with the page's UTF-8 text raw inside it, so it is read as a JSON string and then as JSON. Each result has its `id`
(`200693202-3956`: the position and its location, or `PIPE-114438158` for a pipeline role), `postingTitle`,
`jobSummary` (plain text, the body: no detail request is made), `locations`, `postingDate` ("Nov 13, 2026"),
`postDateInGMT`, `transformedPostingTitle` (the link's slug) and `team.teamCode`. The link a person opens is
`/en-us/details/<id>/<slug>?team=<code>`, with a pipeline role under its `positionId`, as the page links them.

The board token names the search: `us` is the only one (`PRESETS`). A head read is page 1: a partial `ok`, or
`unchanged` when it lists nothing. A full read pages until `totalRecords` are seen, at most `MAX_PAGES` pages. As for
Workday, Oracle and Amazon (#232), a broken row is skipped and a list a little short of the total (`short_allowed`)
is a partial `ok`: what it listed is kept and nothing is closed. A page that fails ends the read as an error. There is
no ETag, so `unchanged` is the id fingerprint.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from outreach.crawl.ats._common import fetch_list, finish_list, read_error, utc_time
from outreach.crawl.ats.workday import short_allowed
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.net import HttpClient, Response

__all__ = ["AppleAdapter", "PRESETS", "MAX_PAGES", "search_url", "parse_page"]

_SITE = "https://jobs.apple.com"
PRESETS = {"us": "united-states-USA"}  # token -> the search's location
PAGE = 20  # the site's own page size
MAX_PAGES = 300  # 6,000 jobs: well above the 4,551 US jobs of 2026-11-13
_HYDRATION = re.compile(r'window\.__staticRouterHydrationData\s*=\s*JSON\.parse\("((?:[^"\\]|\\.)*)"\)', re.S)
_ID = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_TEAM = re.compile(r"[A-Z0-9]+")
_US = "iso-country-USA"


def search_url(token: str, page: int) -> str:
    return f"{_SITE}/en-us/search?location={PRESETS[token]}&sort=newest&page={page}"


def parse_page(text: str) -> tuple[list, int] | str:
    """(results, totalRecords) from the hydration call, or what is wrong with the page."""
    found = _HYDRATION.search(text)
    if found is None:
        return "the page has no __staticRouterHydrationData"
    try:
        data = json.loads(json.loads('"' + found.group(1) + '"'))
    except ValueError as e:
        return f"the hydration data is not JSON ({e})"
    loader = data.get("loaderData") if isinstance(data, dict) else None
    search = loader.get("search") if isinstance(loader, dict) else None
    if not isinstance(search, dict) or not isinstance(search.get("searchResults"), list):
        return "the hydration data has no search results"
    total = search.get("totalRecords")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        return "the hydration data has no totalRecords"
    return search["searchResults"], total


def _text(value) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _posted_on(value) -> str | None:
    """"Nov 13, 2026" as 2026-11-13; anything else is None."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), "%b %d, %Y").date().isoformat()
    except ValueError:
        return None


def _url(job: dict, native_id: str) -> str:
    path = native_id
    if native_id.startswith("PIPE-") and _ID.fullmatch(str(job.get("positionId") or "")):
        path = str(job["positionId"])
    url = f"{_SITE}/en-us/details/{path}"
    slug = job.get("transformedPostingTitle")
    if isinstance(slug, str) and _SLUG.fullmatch(slug):
        url += f"/{slug}"
    team = job.get("team")
    code = team.get("teamCode") if isinstance(team, dict) else None
    if isinstance(code, str) and _TEAM.fullmatch(code):
        url += f"?team={code}"
    return url


def _parse(job: object) -> JobRef | None:
    """One result, or None when it lacks an id or a title."""
    if not isinstance(job, dict):
        return None
    native_id = job.get("id") if isinstance(job.get("id"), str) and _ID.fullmatch(job["id"]) else ""
    title = _text(job.get("postingTitle"))
    if not native_id or not title:
        return None
    places = [p for p in job.get("locations") or [] if isinstance(p, dict)] if isinstance(job.get("locations"),
                                                                                         list) else []
    shown = [_text(p.get("name")) for p in places if _text(p.get("name"))]
    summary = job.get("jobSummary")
    body = summary.strip() if isinstance(summary, str) else ""
    posted_at = utc_time(job.get("postDateInGMT"))
    return JobRef(native_id=native_id, title=title, url=_url(job, native_id), location="; ".join(shown),
                  country="US" if places and all(p.get("countryID") == _US for p in places) else None,
                  posted_on=_posted_on(job.get("postingDate")) or (posted_at[:10] if posted_at else None),
                  body=body or None, posted_at=posted_at)


class AppleAdapter:
    ats = "apple"

    def __init__(self, client: HttpClient):
        self.client = client

    def _page(self, token: str, n: int) -> tuple[list, int, Response] | ReadResult:
        got = fetch_list(self.client, search_url(token, n), Validators())
        if isinstance(got, ReadResult):
            return got
        parsed = parse_page(got.text())
        if isinstance(parsed, str):
            return read_error(f"page {n}: {parsed}", status=got.status)
        return parsed[0], parsed[1], got

    @staticmethod
    def _bad_token(token: str) -> ReadResult | None:
        if isinstance(token, str) and token in PRESETS:
            return None
        return read_error(f"{token!r} is not a jobs.apple.com search (the reader knows: {', '.join(PRESETS)})")

    def list(self, token: str, validators: Validators) -> ReadResult:
        if bad := self._bad_token(token):
            return bad
        jobs: dict[str, JobRef] = {}
        broken = 0
        total = 0
        resp: Response | None = None
        for n in range(1, MAX_PAGES + 1):
            got = self._page(token, n)
            if isinstance(got, ReadResult):
                return got
            rows, count, resp = got
            if n == 1:
                total = count
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
            return read_error(f"{len(jobs)} jobs listed but totalRecords is {total}", status=resp.status)
        missing = total - len(jobs)
        note = (f"{len(jobs)} of {total} jobs read ({broken} broken, {max(missing - broken, 0)} missing)"
                if missing or broken else "")
        return finish_list(jobs.values(), resp, validators, partial_note=note)

    def head(self, token: str) -> ReadResult:
        """A head read (#251 part 2): page 1, newest first. A partial `ok` that closes nothing, or `unchanged` when
        it lists nothing, so it never counts as an empty read."""
        if bad := self._bad_token(token):
            return bad
        got = self._page(token, 1)
        if isinstance(got, ReadResult):
            return got
        rows, total, resp = got
        if not rows:
            return ReadResult("unchanged", (), Validators(), http_status=resp.status, head=True)
        jobs: dict[str, JobRef] = {}
        for item in rows:
            if (parsed := _parse(item)) is not None:
                jobs.setdefault(parsed.native_id, parsed)
        if not jobs:
            return read_error(f"none of the {len(rows)} jobs on page 1 could be read", status=resp.status)
        return ReadResult("ok", tuple(jobs.values()), Validators(), http_status=resp.status, partial=True,
                          partial_note=f"head read: the newest {len(jobs)} of {total} jobs", head=True)

    def body(self, token: str, job: JobRef) -> str | None:
        return None  # the search page carries every job's summary
