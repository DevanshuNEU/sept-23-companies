"""Workday: paged POST lists, posting text from a detail request per posting (#21, Task 18).

A board token is `tenant.wdN/site`, the site as Workday spells it. `POST https://{tenant}.{wdN}.myworkdayjobs.com/
wday/cxs/{tenant}/{site}/jobs` takes `{"limit", "offset", "searchText", "appliedFacets"}` and answers
`{"total", "jobPostings": [...], "facets": [...]}`. Workday refuses a limit above 20, and it reports `total` on the
first page only (later pages say 0), so the count comes from page 1.

A list that may be incomplete is `error`, never `ok` (Review Focus 1). Workday lists at most 2,000 rows: past that,
offsets wrap to page 1. A list that reports 2,000 or more is split by the `jobFamilyGroup` facet, each family read
to its end and the postings joined by `externalPath`. The read is `error` ("list capped") when a family itself
reports 2,000 or more, when the first page has no family facet, when the families together cover fewer postings than
the total (a posting with no family would be missing, or two families overlap), or when any page fails, comes back
empty early or repeats a posting. Workday sends no ETag, so an unchanged board is told by the fingerprint of the
`externalPath` ids.
"""
from __future__ import annotations

import datetime
import json
import re

from outreach.crawl.ats._common import fetch_list, finish_list, json_body, read_error, safe_token
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.text import html_to_text
from outreach.net import HttpClient, Response

__all__ = ["WorkdayAdapter", "posted_on_from_relative"]

PAGE_SIZE = 20
LIST_CAP = 2000
_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
_LIST = "https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
_DETAIL = "https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{path}"
_POSTING = "https://{tenant}.{wd}.myworkdayjobs.com/{site}{path}"
_PATH = re.compile(r"(?:/[A-Za-z0-9_.%~(),+-]+)+")
_RELATIVE = re.compile(r"posted\s+(?:(today)|(yesterday)|(\d{1,2})\s+days?\s+ago)", re.IGNORECASE)
_OLD = 30  # "Posted 30+ Days Ago" is the oldest bucket Workday names: no date at or past it


def posted_on_from_relative(text: str, today: datetime.date) -> str | None:
    """A Workday `postedOn` phrase as YYYY-MM-DD. "Posted 30+ Days Ago" and anything unrecognised is None."""
    if not isinstance(text, str):
        return None
    m = _RELATIVE.fullmatch(text.strip())
    if not m:
        return None
    days = 0 if m.group(1) else 1 if m.group(2) else int(m.group(3))
    if days >= _OLD:
        return None
    return (today - datetime.timedelta(days=days)).isoformat()


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _valid_path(path: str) -> bool:
    """An `externalPath` the detail request can use: `/` segments of URL-safe characters, no `.` or `..`."""
    return bool(_PATH.fullmatch(path)) and not any(seg in (".", "..") for seg in path.split("/"))


class _Listing:
    """One fully paged list: its `total`, its postings by `externalPath` and the raw first page (for its facets).

    `capped` means the first page reported the cap or more: nothing was paged and `rows` is empty."""

    def __init__(self, total: int, rows: dict[str, dict], first: dict, resp: Response, capped: bool = False):
        self.total, self.rows, self.first, self.resp, self.capped = total, rows, first, resp, capped


def _family_ids(data: dict) -> list[str]:
    """The `jobFamilyGroup` facet's value ids, searched through nested facets. Empty when there is none."""
    found: list[str] = []

    def walk(node) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if node.get("facetParameter") == "jobFamilyGroup" and isinstance(node.get("values"), list):
                for value in node["values"]:
                    if isinstance(value, dict) and isinstance(value.get("id"), str) and value["id"].strip():
                        found.append(value["id"].strip())
            else:
                walk(node.get("values"))

    walk(data.get("facets"))
    return list(dict.fromkeys(found))


class WorkdayAdapter:
    ats = "workday"

    def __init__(self, client: HttpClient, today: datetime.date | None = None):
        self.client = client
        self._today = today

    def _post(self, url: str, offset: int, facets: dict) -> Response | ReadResult:
        payload = {"limit": PAGE_SIZE, "offset": offset, "searchText": "", "appliedFacets": facets}
        return fetch_list(self.client, url, Validators(), headers=_HEADERS, method="POST",
                          body=json.dumps(payload).encode("utf-8"))

    @staticmethod
    def _rows(resp: Response, first: bool) -> tuple[list, int | None, dict] | ReadResult:
        data = json_body(resp)
        if isinstance(data, ReadResult):
            return data
        rows = data.get("jobPostings") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return read_error("the 200 answer has no jobPostings list", status=resp.status)
        total = data.get("total")
        if first and (isinstance(total, bool) or not isinstance(total, int) or total < 0):
            return read_error("the 200 answer has no total count", status=resp.status)
        return rows, total if first else None, data

    def _listing(self, url: str, facets: dict) -> _Listing | ReadResult:
        got = self._post(url, 0, facets)
        if isinstance(got, ReadResult):
            return got
        parsed = self._rows(got, True)
        if isinstance(parsed, ReadResult):
            return parsed
        page, total, first = parsed
        if total >= LIST_CAP:
            return _Listing(total, {}, first, got, capped=True)
        if total == 0 and page:
            return read_error("the answer says total 0 but lists postings", status=got.status)
        rows: dict[str, dict] = {}
        resp, offset = got, 0
        while True:
            for item in page:
                path = _text(item.get("externalPath")) if isinstance(item, dict) else ""
                if not _valid_path(path) or not _text(item.get("title")):
                    return read_error("a posting has no usable externalPath or no title", status=resp.status)
                if path in rows:
                    return read_error(f"posting {path} is listed twice (the pages wrapped or moved)",
                                      status=resp.status)
                rows[path] = item
            if len(rows) >= total:
                break
            if not page:
                return read_error(f"the pages ended with {len(rows)} postings but total is {total}",
                                  status=resp.status)
            offset += len(page)
            if offset >= LIST_CAP:
                return read_error("list capped: the pages passed offset 2000 before the total was reached",
                                  status=resp.status)
            got = self._post(url, offset, facets)
            if isinstance(got, ReadResult):
                return got
            parsed = self._rows(got, False)
            if isinstance(parsed, ReadResult):
                return parsed
            page, _, _ = parsed
            resp = got
        if len(rows) != total:
            return read_error(f"{len(rows)} postings listed but total is {total}", status=resp.status)
        return _Listing(total, rows, first, got)

    def list(self, token: str, validators: Validators) -> ReadResult:
        if not safe_token(self.ats, token):
            return read_error(f"{token!r} is not a valid workday token")
        url, tenant, wd, site = self._parts(token)
        first = self._listing(url, {})
        if isinstance(first, ReadResult):
            return first
        rows, resp = first.rows, first.resp
        if first.capped:
            ids = _family_ids(first.first)
            if not ids:
                return read_error(f"list capped: {first.total} postings and no jobFamilyGroup facet to split by",
                                  status=resp.status)
            rows, covered = {}, 0
            for family in ids:
                part = self._listing(url, {"jobFamilyGroup": [family]})
                if isinstance(part, ReadResult):
                    return part
                if part.capped:
                    return read_error(f"list capped: family {family} alone reports {part.total} postings",
                                      status=resp.status)
                covered += part.total
                rows.update(part.rows)
            # The families' own totals can add up while they overlap, so what counts is the union.
            if len(rows) < first.total:
                return read_error(f"list capped: the families cover {len(rows)} distinct postings "
                                  f"({covered} counted) of {first.total}", status=resp.status)
        today = self._today or datetime.datetime.now(datetime.timezone.utc).date()
        jobs = [JobRef(native_id=path, title=_text(item.get("title")),
                       url=_POSTING.format(tenant=tenant, wd=wd, site=site, path=path),
                       location=_text(item.get("locationsText")), country=None,
                       posted_on=posted_on_from_relative(item.get("postedOn"), today), body=None)
                for path, item in rows.items()]
        return finish_list(jobs, resp, validators, use_etag=False)

    @staticmethod
    def _parts(token: str) -> tuple[str, str, str, str]:
        host_part, site = token.split("/")
        tenant, wd = host_part.split(".")
        return _LIST.format(tenant=tenant, wd=wd, site=site), tenant, wd, site

    def body(self, token: str, job: JobRef) -> str | None:
        """`Start date: <date>` (when Workday gives one) then the description, or None when there is no description.
        A failed request or a wrong shape raises, so the caller counts a failed fetch."""
        path = job.native_id
        if not safe_token(self.ats, token) or not _valid_path(path):
            raise ValueError(f"cannot fetch {path!r} from {token!r}")
        _, tenant, wd, site = self._parts(token)
        resp = self.client.get(_DETAIL.format(tenant=tenant, wd=wd, site=site, path=path),
                               {"Accept": "application/json"})
        if resp.status != 200:
            raise ValueError(f"HTTP {resp.status} for posting {path}")
        data = resp.json()
        info = data.get("jobPostingInfo") if isinstance(data, dict) else None
        if not isinstance(info, dict):
            raise ValueError(f"posting {path} has no jobPostingInfo")
        desc = info.get("jobDescription")
        text = html_to_text(desc) if isinstance(desc, str) else ""
        if not text:
            return None
        start = _text(info.get("startDate"))
        places = [_text(info.get("location"))]
        extra = info.get("additionalLocations")
        places += [_text(p) for p in extra] if isinstance(extra, list) else []
        places = list(dict.fromkeys(p for p in places if p))
        head = ([f"Start date: {start}"] if start else []) + ([f"Locations: {'; '.join(places)}"] if places else [])
        return "\n".join(head + [text])
