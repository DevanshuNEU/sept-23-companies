"""iCIMS Jibe: the JSON list of a Jibe careers site on the company's own host (#220, 2026-11-13).

Many iCIMS customers run their careers site on Jibe, iCIMS's site builder, on their own domain (careers.jhuapl.edu,
careers.garmin.com) or on `<tenant>.jibeapply.com`; their job links read `https://<host>/jobs/<id>?icims=1`. The board
token is that host. A token on `*.icims.com` is a plain iCIMS site, which has no reader yet: it is an error read and
nothing is sent.

robots.txt on careers.jhuapl.edu (checked 2026-11-13) allows every path with `crawl-delay: 5`. Jibe hosts share no
suffix for a host bucket, so the requests are sent inside `polite.sent_by("icims")` and the polite client spaces each
host by `[ats_interval_s] icims` (5 seconds) on its own key.

`GET /api/jobs?page=N&sortBy=posted_date&descending=true&internal=false` (N from 1) answers
`{"jobs": [{"data": {...}}], "totalCount": n, ...}`, 10 a page, newest first. Each `data` has `slug` and `req_id` (the
job number), `title`, `description`, `responsibilities` and `qualifications` (HTML; on careers.jhuapl.edu the
description already holds the other two), `city`, `state`, `country_code`, `full_location`, `multipleLocations`,
`posted_date` ("2026-11-13T17:40:00+0000"), `create_date` and `update_date`. The page a person opens is
`/jobs/<slug>?icims=1&lang=en-us`, the form the SimplifyJobs feed links, so `outreach.pipeline.urls.board_key` reads it.

A head read is page 1: a partial `ok`, or `unchanged` when it lists nothing. A full read pages until `totalCount` jobs
are seen, at most `MAX_PAGES` pages. As for Apple and Eightfold (#268), a broken row is skipped, a list a little short
of the total (`short_allowed`) is a partial `ok` that closes nothing, a later page that fails ends the read as a
partial `ok` of the pages read (no further page, no retry), and a first page that fails is an error. There is no ETag,
so `unchanged` is the id fingerprint.
"""
from __future__ import annotations

import datetime
import re

from outreach.crawl.ats._common import d10, fetch_list, finish_list, json_body, read_error, safe_token, utc_time
from outreach.crawl.ats.workday import short_allowed
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.polite import sent_by
from outreach.crawl.text import html_to_text
from outreach.net import HttpClient, Response

__all__ = ["IcimsAdapter", "MAX_PAGES", "list_url", "PLAIN_ICIMS"]

_LIST = "https://{host}/api/jobs?page={page}&sortBy=posted_date&descending=true&internal=false"
PAGE = 10  # the site's own page size
MAX_PAGES = 300  # 3,000 jobs: careers.jhuapl.edu listed 539 on 2026-11-13
PLAIN_ICIMS = "a plain iCIMS site, not a Jibe site, has no reader yet"
_ID = re.compile(r"[A-Za-z0-9_-]+")
_LANG = re.compile(r"[a-z]{2}(?:-[a-z]{2})?")
_COUNTRY = re.compile(r"[A-Z]{2}")
_REPOST = datetime.timedelta(days=1)


def list_url(host: str, page: int) -> str:
    return _LIST.format(host=host, page=page)


def _text(value) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _html(value) -> str:
    return html_to_text(value).strip() if isinstance(value, str) else ""


def _body(data: dict) -> str | None:
    """The description, then the responsibilities and qualifications each only when the description (and what came
    before) does not already hold their text."""
    parts: list[str] = []
    for key in ("description", "responsibilities", "qualifications"):
        text = _html(data.get(key))
        if text and " ".join(text.split()) not in " ".join(" ".join(parts).split()):
            parts.append(text)
    return "\n\n".join(parts) or None


def _posted_at(data: dict) -> str | None:
    """`posted_date` as the posting time, only while the job is no older on the site than that. A repost moves
    `posted_date` and leaves `create_date` (careers.jhuapl.edu 59297: created 2026-07-01, posted 2026-11-13), so a job
    created more than a day before its posted_date gets no posting time: its list time falls back to Knock's first
    sighting (#193). `posted_on` keeps the board's date either way; icims is not in `DATE_TRUSTED`, since that date
    moves on a repost as Eightfold's does (#246)."""
    posted, created = utc_time(data.get("posted_date")), utc_time(data.get("create_date"))
    if posted is None or created is None:
        return None
    gap = datetime.datetime.fromisoformat(posted) - datetime.datetime.fromisoformat(created)
    return posted if gap <= _REPOST else None


def _location(data: dict) -> str:
    if place := _text(data.get("full_location")):
        return place
    return ", ".join(p for p in (_text(data.get("city")), _text(data.get("state"))) if p)


def _parse(host: str, item: object) -> JobRef | None:
    """One job, or None when it lacks an id or a title."""
    data = item.get("data") if isinstance(item, dict) else None
    if not isinstance(data, dict):
        return None
    native_id = next((v for v in (data.get("slug"), data.get("req_id")) if isinstance(v, str) and _ID.fullmatch(v)), "")
    title = _text(data.get("title"))
    if not native_id or not title:
        return None
    lang = data.get("language") if isinstance(data.get("language"), str) and _LANG.fullmatch(data["language"]) \
        else "en-us"
    code = data.get("country_code")
    country = code if isinstance(code, str) and _COUNTRY.fullmatch(code) and data.get("multipleLocations") is not True \
        else None
    return JobRef(native_id=native_id, title=title, url=f"https://{host}/jobs/{native_id}?icims=1&lang={lang}",
                  location=_location(data), country=country, posted_on=d10(data.get("posted_date")),
                  body=_body(data), posted_at=_posted_at(data))


def _bad_token(token: str) -> ReadResult | None:
    if not safe_token("icims", token):
        return read_error(f"{token!r} is not a Jibe careers host")
    if token.endswith(".icims.com"):
        return read_error(f"{token}: {PLAIN_ICIMS}")
    return None


class IcimsAdapter:
    ats = "icims"

    def __init__(self, client: HttpClient):
        self.client = client

    def _page(self, host: str, n: int) -> tuple[list, int, Response] | ReadResult:
        with sent_by(self.ats):
            got = fetch_list(self.client, list_url(host, n), Validators())
        if isinstance(got, ReadResult):
            return got
        data = json_body(got)
        if isinstance(data, ReadResult):
            return data
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            return read_error(f"page {n}: the 200 answer is not a Jibe job list", status=got.status)
        total = data.get("totalCount")
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            return read_error(f"page {n}: the 200 answer has no totalCount", status=got.status)
        return data["jobs"], total, got

    def list(self, token: str, validators: Validators) -> ReadResult:
        if bad := _bad_token(token):
            return bad
        jobs: dict[str, JobRef] = {}
        stopped = ""
        broken = 0
        total = 0
        resp: Response | None = None
        for n in range(1, MAX_PAGES + 1):
            got = self._page(token, n)
            if isinstance(got, ReadResult):
                if n == 1 or not jobs:
                    return got
                # A later page failed (a timeout, a 429): keep what was read as a partial ok that closes nothing, and
                # send no further page (#268).
                stopped = f"stopped at page {n}: {got.error}"
                break
            rows, count, resp = got
            if n == 1:
                total = count
            before = len(jobs)
            for item in rows:
                parsed = _parse(token, item)
                if parsed is None:
                    broken += 1
                    continue
                jobs.setdefault(parsed.native_id, parsed)
            if len(jobs) + broken >= total or not rows or len(jobs) == before:
                break
        if resp is None:
            return read_error("no page was read")
        if stopped:
            return finish_list(jobs.values(), resp, validators,
                               partial_note=f"{len(jobs)} of {total} jobs read, {stopped}")
        if len(jobs) > total or total - len(jobs) > short_allowed(total):
            return read_error(f"{len(jobs)} jobs listed but totalCount is {total}", status=resp.status)
        missing = total - len(jobs)
        note = (f"{len(jobs)} of {total} jobs read ({broken} broken, {max(missing - broken, 0)} missing)"
                if missing or broken else "")
        return finish_list(jobs.values(), resp, validators, partial_note=note)

    def head(self, token: str) -> ReadResult:
        """A head read (#251 part 2): page 1, newest first. A partial `ok` that closes nothing, or `unchanged` when
        it lists nothing, so it never counts as an empty read."""
        if bad := _bad_token(token):
            return bad
        got = self._page(token, 1)
        if isinstance(got, ReadResult):
            return got
        rows, total, resp = got
        if not rows:
            return ReadResult("unchanged", (), Validators(), http_status=resp.status, head=True)
        jobs: dict[str, JobRef] = {}
        for item in rows:
            if (parsed := _parse(token, item)) is not None:
                jobs.setdefault(parsed.native_id, parsed)
        if not jobs:
            return read_error(f"none of the {len(rows)} jobs on page 1 could be read", status=resp.status)
        return ReadResult("ok", tuple(jobs.values()), Validators(), http_status=resp.status, partial=True,
                          partial_note=f"head read: the newest {len(jobs)} of {total} jobs", head=True)

    def body(self, token: str, job: JobRef) -> str | None:
        return None  # the list carries every job's text
