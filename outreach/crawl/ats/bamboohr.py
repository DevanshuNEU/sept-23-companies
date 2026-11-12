"""BambooHR: the public careers JSON (#21, Task 25; list rebuilt for #157 on 2026-11-12).

`{token}.bamboohr.com/careers/list` answers `{"meta": {"totalCount": n}, "result": [...]}`: each job its `id`,
`jobOpeningName`, `location` (`city`, `state`), `atsLocation` (`city`, `state`, `country`) and `isRemote`. The old
`/jobs/embed2.php` widget now answers JSON too, with no count, so the list is read here. Its robots.txt allows
everything (checked 2026-11-12).

A 200 that is not this JSON (a login page, a challenge page, an error) is `error`; `totalCount` 0 with no rows is
`empty`. Every row must be a job read with an id and a title, and the jobs read must be exactly `totalCount`, or the
read is `error`: a short list read as `ok` would close real postings. An unknown tenant answers a redirect, which is
never followed.

There is no ETag, so the fingerprint is over the ids. `body()` asks `/careers/{id}/detail` for JSON, builds its URL
from the token and the id (never from a URL the list returned) and follows no redirect; the answer must be the
opening's own (its share link ends in `/careers/{id}`).

The token becomes a host label, so it must be one: letters, digits and inner hyphens.
"""
from __future__ import annotations

import re

from outreach.crawl.ats._common import fetch_list, finish_list, json_body, read_error, safe_token
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.text import html_to_text
from outreach.net import HttpClient

__all__ = ["BambooHRAdapter"]

_LIST = "https://{token}.bamboohr.com/careers/list"
_POSTING = "https://{token}.bamboohr.com/careers/{id}"
_DETAIL = "https://{token}.bamboohr.com/careers/{id}/detail"
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")
_ACCEPT = {"Accept": "application/json"}


def _text(value) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _native_id(value) -> str:
    if isinstance(value, bool):
        return ""
    text = str(value) if isinstance(value, int) else _text(value)
    return text if _SAFE_ID.fullmatch(text) else ""


def _place(row: dict) -> tuple[str, str | None]:
    """(location, country): the row's city and state, else the ATS location's; "Remote" when it says so."""
    loc = row.get("location") if isinstance(row.get("location"), dict) else {}
    ats = row.get("atsLocation") if isinstance(row.get("atsLocation"), dict) else {}
    parts = [_text(loc.get("city")), _text(loc.get("state"))]
    if not any(parts):
        parts = [_text(ats.get("city")), _text(ats.get("state")) or _text(ats.get("province"))]
    place = ", ".join(p for p in parts if p)
    if row.get("isRemote") is True:
        place = f"Remote, {place}" if place else "Remote"
    return place, _text(ats.get("country")) or None


def _parse(token: str, rows: list) -> list[JobRef] | None:
    jobs = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        native_id, title = _native_id(row.get("id")), _text(row.get("jobOpeningName"))
        if not native_id or not title:
            return None
        location, country = _place(row)
        jobs.append(JobRef(native_id=native_id, title=title, url=_POSTING.format(token=token, id=native_id),
                           location=location, country=country, posted_on=None, body=None))
    return jobs


def _valid(token: str) -> bool:
    return safe_token("bamboohr", token) and _LABEL.fullmatch(token) is not None


def _is_opening(opening: dict, native_id: str) -> bool:
    """The detail is this opening's: its `id` when it carries one, else its share link's last path part."""
    if "id" in opening:
        return _native_id(opening.get("id")) == native_id
    link = opening.get("jobOpeningShareUrl")
    return isinstance(link, str) and link.rstrip("/").rsplit("/careers/", 1)[-1] == native_id


class BambooHRAdapter:
    ats = "bamboohr"

    def __init__(self, client: HttpClient):
        self.client = client

    def list(self, token: str, validators: Validators) -> ReadResult:
        if not _valid(token):
            return read_error(f"{token!r} is not a valid bamboohr token")
        resp = fetch_list(self.client, _LIST.format(token=token), validators, headers=_ACCEPT, use_etag=False)
        if isinstance(resp, ReadResult):
            return resp
        data = json_body(resp)
        if isinstance(data, ReadResult):
            return data
        meta = data.get("meta") if isinstance(data, dict) else None
        total = meta.get("totalCount") if isinstance(meta, dict) else None
        rows = data.get("result") if isinstance(data, dict) else None
        if isinstance(total, bool) or not isinstance(total, int) or total < 0 or not isinstance(rows, list):
            return read_error("the 200 answer is not the BambooHR careers list", status=resp.status)
        jobs = _parse(token, rows)
        if jobs is None:
            return read_error("a job has no usable id or no title", status=resp.status)
        if len({j.native_id for j in jobs}) != total:
            return read_error(f"{len(jobs)} jobs listed but totalCount is {total}, so the list may be incomplete",
                              status=resp.status)
        return finish_list(jobs, resp, validators, use_etag=False)

    def body(self, token: str, job: JobRef) -> str | None:
        """The description's text from the detail JSON, None when it is empty. A failed request, a redirect, an HTML
        page or JSON without `result.jobOpening.description` raises, so no empty body is stored by mistake."""
        if not _valid(token) or not _SAFE_ID.fullmatch(job.native_id):
            raise ValueError(f"cannot fetch position {job.native_id!r} from {token!r}")
        resp = self.client.get(_DETAIL.format(token=token, id=job.native_id), _ACCEPT)
        if resp.status != 200:
            raise ValueError(f"HTTP {resp.status} for position {job.native_id}")
        data = resp.json()
        result = data.get("result") if isinstance(data, dict) else None
        opening = result.get("jobOpening") if isinstance(result, dict) else None
        description = opening.get("description") if isinstance(opening, dict) else None
        if not isinstance(opening, dict) or not _is_opening(opening, job.native_id):
            raise ValueError(f"the detail answer is not for position {job.native_id}")
        if not isinstance(description, str):
            raise ValueError(f"position {job.native_id} has no description in the detail answer")
        return html_to_text(description) or None
