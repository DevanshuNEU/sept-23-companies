"""Eightfold: the public PCSX search JSON of an Eightfold careers site (#246, 2026-11-12). Every tenant's robots.txt
allows `/api/pcsx` (checked 2026-11-12 on Micron, Qualcomm, PayPal, Microsoft and Netflix).

`https://<host>/api/pcsx/search?domain=<domain>&query=&location=United%20States&start=N&sort_by=timestamp` answers
`{"status": 200, "data": {"count": n, "positions": [...]}}`, ten positions a page whatever is asked, newest first.
Each position has its `id`, `name`, `locations` (or `standardizedLocations`), `postedTs` (epoch seconds) and
`positionUrl`. The text comes from `/api/pcsx/position_details?position_id=<id>&domain=<domain>` (`data.jobDescription`).

The board token names the tenant: `TENANTS` gives the host and the domain the site answers for; a token not there
reads `<token>.eightfold.ai` for `<token>.com`. A list is read page by page until `count` are seen, at most
`MAX_PAGES` pages. As for Workday, Oracle and Amazon (#232), a broken row is skipped and a list a little short of
`count` (`short_allowed`) is a partial `ok`: what it listed is kept and nothing is closed. There is no ETag, so
`unchanged` is the id fingerprint. A site that answers 403 "Not authorized for PCSX" (the older v2 API, Netflix) or
429 (Microsoft, 2026-11-12) is an error and is never worked around.

A head read (#263) is page 1 alone (`start=0`): the search is newest first, so it is a partial `ok` that closes
nothing, or `unchanged` when the page lists nothing.
"""
from __future__ import annotations

import re
import urllib.parse

from outreach.crawl.ats._common import d10, fetch_list, finish_list, json_body, read_error
from outreach.crawl.ats.workday import short_allowed
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.text import html_to_text
from outreach.net import HttpClient, Response

__all__ = ["EightfoldAdapter", "TENANTS", "tenant", "search_url", "detail_url"]

# token -> (host, domain), as each careers site answers (2026-11-12).
TENANTS = {
    "micron": ("careers.micron.com", "micron.com"),
    "qualcomm": ("careers.qualcomm.com", "qualcomm.com"),
    "appliedmaterials": ("careers.appliedmaterials.com", "appliedmaterials.com"),
    "morganstanley": ("morganstanley.eightfold.ai", "morganstanley.com"),
    "paypal": ("paypal.eightfold.ai", "paypal.com"),
    "microsoft": ("apply.careers.microsoft.com", "microsoft.com"),
}
PAGE = 10  # the site's own page size; it ignores a larger `num`
MAX_PAGES = 300  # 3,000 postings: far above Applied Materials' 1,159 US jobs of 2026-11-12
_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_ID = re.compile(r"[0-9]{1,20}")


def tenant(token: str) -> tuple[str, str] | None:
    """(host, domain) for a token, None for one that cannot be a host label."""
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        return None
    return TENANTS.get(token, (f"{token}.eightfold.ai", f"{token}.com"))


def search_url(host: str, domain: str, start: int) -> str:
    query = urllib.parse.urlencode({"domain": domain, "query": "", "location": "United States", "start": start,
                                    "sort_by": "timestamp"}, quote_via=urllib.parse.quote)
    return f"https://{host}/api/pcsx/search?{query}"


def detail_url(host: str, domain: str, native_id: str) -> str:
    query = urllib.parse.urlencode({"position_id": native_id, "domain": domain, "hl": "en"})
    return f"https://{host}/api/pcsx/position_details?{query}"


def _text(value) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _native_id(value) -> str:
    if isinstance(value, bool):
        return ""
    text = str(value).strip() if isinstance(value, (int, str)) else ""
    return text if _ID.fullmatch(text) else ""


def _places(job: dict) -> str:
    for key in ("locations", "standardizedLocations"):
        value = job.get(key)
        if isinstance(value, list):
            places = [_text(v) for v in value if _text(v)]
            if places:
                return "; ".join(places)
    return _text(job.get("location"))


def _parse(host: str, job: object) -> JobRef | None:
    """One position, or None when it lacks an id or a title."""
    if not isinstance(job, dict):
        return None
    native_id, title = _native_id(job.get("id")), _text(job.get("name"))
    if not native_id or not title:
        return None
    return JobRef(native_id=native_id, title=title, url=f"https://{host}/careers/job/{native_id}",
                  location=_places(job), country=None, posted_on=d10(job.get("postedTs")), body=None)


class EightfoldAdapter:
    ats = "eightfold"

    def __init__(self, client: HttpClient):
        self.client = client

    def _page(self, host: str, domain: str, start: int) -> tuple[list, int, Response] | ReadResult:
        got = fetch_list(self.client, search_url(host, domain, start), Validators(), headers={"Accept": "application/json"})
        if isinstance(got, ReadResult):
            return got
        data = json_body(got)
        if isinstance(data, ReadResult):
            return data
        body = data.get("data") if isinstance(data, dict) else None
        if not isinstance(body, dict) or not isinstance(body.get("positions"), list):
            return read_error("the 200 answer is not an Eightfold PCSX search result", status=got.status)
        count = body.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return read_error("the 200 answer has no position count", status=got.status)
        return body["positions"], count, got

    def list(self, token: str, validators: Validators) -> ReadResult:
        site = tenant(token)
        if site is None:
            return read_error(f"{token!r} is not an Eightfold tenant token")
        host, domain = site
        jobs: dict[str, JobRef] = {}
        broken = 0
        total = 0
        resp: Response | None = None
        for n in range(MAX_PAGES):
            got = self._page(host, domain, n * PAGE)
            if isinstance(got, ReadResult):
                return got
            rows, count, resp = got
            if n == 0:
                total = count
            before = len(jobs)
            for item in rows:
                parsed = _parse(host, item)
                if parsed is None:
                    broken += 1
                    continue
                jobs.setdefault(parsed.native_id, parsed)
            if len(jobs) + broken >= total or not rows or len(jobs) == before:
                break
        if resp is None:
            return read_error("no page was read")
        if len(jobs) > total or total - len(jobs) > short_allowed(total):
            return read_error(f"{len(jobs)} positions listed but count is {total}", status=resp.status)
        missing = total - len(jobs)
        note = (f"{len(jobs)} of {total} positions read ({broken} broken, {max(missing - broken, 0)} missing)"
                if missing or broken else "")
        return finish_list(jobs.values(), resp, validators, partial_note=note)

    def head(self, token: str) -> ReadResult:
        """A head read (#251 part 2, #263): page 1 of the search, which is already newest first. A partial `ok` that
        closes nothing, or `unchanged` when it lists nothing, so it never counts as an empty read."""
        site = tenant(token)
        if site is None:
            return read_error(f"{token!r} is not an Eightfold tenant token")
        host, domain = site
        got = self._page(host, domain, 0)
        if isinstance(got, ReadResult):
            return got
        rows, count, resp = got
        if not rows:
            return ReadResult("unchanged", (), Validators(), http_status=resp.status, head=True)
        jobs: dict[str, JobRef] = {}
        for item in rows:
            if (parsed := _parse(host, item)) is not None:
                jobs.setdefault(parsed.native_id, parsed)
        if not jobs:
            return read_error(f"none of the {len(rows)} positions on page 1 could be read", status=resp.status)
        return ReadResult("ok", tuple(jobs.values()), Validators(), http_status=resp.status, partial=True,
                          partial_note=f"head read: the newest {len(jobs)} of {count} positions", head=True)

    def body(self, token: str, job: JobRef) -> str | None:
        """The description's text, None when it is empty. A failed request, another position's answer or one with
        no `jobDescription` raises, so no empty body is stored by mistake."""
        site = tenant(token)
        if site is None or not _ID.fullmatch(job.native_id or ""):
            raise ValueError(f"cannot fetch position {job.native_id!r} from {token!r}")
        resp = self.client.get(detail_url(*site, job.native_id), {"Accept": "application/json"})
        if resp.status != 200:
            raise ValueError(f"HTTP {resp.status} for position {job.native_id}")
        data = resp.json()
        body = data.get("data") if isinstance(data, dict) else None
        if not isinstance(body, dict) or _native_id(body.get("id")) != job.native_id:
            raise ValueError(f"the detail answer is not for position {job.native_id}")
        description = body.get("jobDescription")
        if not isinstance(description, str):
            raise ValueError(f"position {job.native_id} has no jobDescription")
        return html_to_text(description) or None
