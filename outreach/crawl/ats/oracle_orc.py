"""Oracle Recruiting Cloud: paged list requests, posting text from a detail request per posting (#21, Task 20).

The token is `host/siteNumber` (`acme.fa.us2.oraclecloud.com/CX_1001`); the site number keeps the case Oracle gives it.
`.../recruitingCEJobRequisitions?...finder=findReqs;siteNumber=S,limit=200,offset=N` answers
`{"items": [{"TotalJobsCount": n, "requisitionList": [...]}]}`. Oracle ignores a top-level `limit` and `offset`, so both
live inside the finder.

A list that may be incomplete is `error`, never `ok` (Review Focus 1): the distinct ids collected must equal
`TotalJobsCount` (not a sum of the pages' sizes), and every loop is bounded: a huge total is refused, the pages are
capped, and a page that brings no new id ends the read. The list carries no ETag, so `unchanged` is the id fingerprint.
"""
from __future__ import annotations

import re

from outreach.crawl.ats._common import d10, fetch_list, finish_list, json_body, read_error, safe_token
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.text import html_to_text
from outreach.net import HttpClient, Response

__all__ = ["OracleAdapter"]

_LIST = ("https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
         "&expand=requisitionList&finder=findReqs;siteNumber={site},limit=200,offset={offset}")
_DETAIL = ("https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
           "&finder=ById;Id=\"{id}\",siteNumber={site}")
_JOB_URL = "https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{id}"
_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+oraclecloud\.com")
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")
_FIELDS = ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")
_MAX_TOTAL = 10_000  # a board listing more than this is refused, not read in part
_MAX_PAGES = 100


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _native_id(raw) -> str:
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        return ""
    text = str(raw).strip()
    return text if _SAFE_ID.fullmatch(text) else ""


def _split(token: str) -> tuple[str, str] | None:
    """`(host, site)` for a token that passes the grammar and names an oraclecloud.com host, else None."""
    if not safe_token("oracle_orc", token):
        return None
    host, _, site = token.partition("/")
    return (host, site) if _HOST.fullmatch(host) and _SAFE_ID.fullmatch(site) else None


def _parse(host: str, site: str, item: object) -> JobRef | None:
    """One requisition, or None when it lacks a usable id or a title."""
    if not isinstance(item, dict):
        return None
    native_id, title = _native_id(item.get("Id")), _text(item.get("Title"))
    if not native_id or not title:
        return None
    country = _text(item.get("PrimaryLocationCountry")).upper()
    return JobRef(native_id=native_id, title=title, url=_JOB_URL.format(host=host, site=site, id=native_id),
                  location=_text(item.get("PrimaryLocation")),
                  country=country if len(country) == 2 and country.isalpha() else None,
                  posted_on=d10(item.get("PostedDate")), body=None)


def _page(resp: Response) -> tuple[list, int] | ReadResult:
    """The requisitions and `TotalJobsCount` of one 200 page, or the error read for a page of another shape."""
    data = json_body(resp)
    if isinstance(data, ReadResult):
        return data
    items = data.get("items") if isinstance(data, dict) else None
    first = items[0] if isinstance(items, list) and items else None
    listed = first.get("requisitionList") if isinstance(first, dict) else None
    total = first.get("TotalJobsCount") if isinstance(first, dict) else None
    if not isinstance(listed, list) or isinstance(total, bool) or not isinstance(total, int) or total < 0:
        return read_error("the 200 answer has no requisitionList and TotalJobsCount", status=resp.status)
    return listed, total


class OracleAdapter:
    ats = "oracle_orc"

    def __init__(self, client: HttpClient):
        self.client = client

    def list(self, token: str, validators: Validators) -> ReadResult:
        parts = _split(token)
        if parts is None:
            return read_error(f"{token!r} is not a valid oracle_orc token (host.oraclecloud.com/siteNumber)")
        host, site = parts
        jobs: dict[str, JobRef] = {}
        total = -1
        rows_read = 0
        resp: Response | None = None
        for _ in range(_MAX_PAGES):
            # Later pages carry no validator: a 304 here is not a reading of the board, and none is ever sent.
            got = fetch_list(self.client, _LIST.format(host=host, site=site, offset=rows_read), Validators())
            if isinstance(got, ReadResult):
                return got
            parsed = _page(got)
            if isinstance(parsed, ReadResult):
                return parsed
            resp, (listed, page_total) = got, parsed
            if total >= 0 and page_total != total:
                return read_error(f"TotalJobsCount moved from {total} to {page_total} during the read",
                                  status=resp.status)
            total = page_total
            if total > _MAX_TOTAL:
                return read_error(f"TotalJobsCount {total} is over the {_MAX_TOTAL} limit", status=resp.status)
            before = len(jobs)
            for item in listed:
                job = _parse(host, site, item)
                if job is None:
                    return read_error("a requisition has no usable Id or no Title", status=resp.status)
                jobs.setdefault(job.native_id, job)
            rows_read += len(listed)
            if len(jobs) >= total:
                break
            if len(jobs) == before:
                return read_error(f"a page brought no new posting: {len(jobs)} listed but TotalJobsCount is {total}",
                                  status=resp.status)
        if resp is None or len(jobs) != total:
            return read_error(f"{len(jobs)} distinct postings listed but TotalJobsCount is {total}",
                              status=resp.status if resp else None)
        return finish_list(jobs.values(), resp, validators)

    def body(self, token: str, job: JobRef) -> str | None:
        """The posting's text, or None when it has none. A failed request or a wrong shape raises, so the caller
        counts a failed fetch instead of storing an empty body."""
        parts = _split(token)
        if parts is None or not _SAFE_ID.fullmatch(job.native_id):
            raise ValueError(f"cannot fetch {job.native_id!r} from {token!r}")
        host, site = parts
        resp = self.client.get(_DETAIL.format(host=host, site=site, id=job.native_id))
        if resp.status != 200:
            raise ValueError(f"HTTP {resp.status} for requisition {job.native_id}")
        data = resp.json()
        items = data.get("items") if isinstance(data, dict) else None
        item = items[0] if isinstance(items, list) and items else None
        if not isinstance(item, dict) or not any(key in item for key in _FIELDS):
            raise ValueError(f"requisition {job.native_id} has no external text fields")
        if "Id" in item and _native_id(item["Id"]) != job.native_id:
            raise ValueError(f"the detail answer is for requisition {item['Id']!r}, not {job.native_id}")
        texts = (html_to_text(item[key]) if isinstance(item.get(key), str) else "" for key in _FIELDS)
        return "\n".join(t for t in texts if t) or None
