"""Google: the server-rendered results page of Google Careers (#263, 2026-11-13).

`https://www.google.com/about/careers/applications/jobs/results?location=United%20States&target_level=EARLY&sort_by=date
&q=software%20engineer` carries its jobs in the page itself, in the `AF_initDataCallback({key: 'ds:1', ..., data:<JSON>,
sideChannel: {}})` block: `data[0]` is the list of the 20 newest jobs and `data[2]` the total the search counts. Each
job is a list: [0] id, [1] title, [3] [null, responsibilities HTML], [4] [null, qualifications HTML, minimum then
preferred], [9] locations, each `[display, [address], city, zip, state, country]`, [10] [null, description HTML],
[12] [created epoch seconds, nanos], [13] [updated epoch seconds, nanos], [15] [null, notes HTML] (where a job says
it gives no visa sponsorship) and [19] [null, the minimum qualifications again]. So no detail request is ever made.

Google's robots.txt (User-agent *, checked 2026-11-13) allows that path and disallows `results?page=` and
`results?*&page=`: the reader never sends a `page` parameter, so it sees page 1 only. Every read is therefore a
partial `ok` that closes nothing (a posting past page 1 is not gone, just not seen), and a page with no jobs is
`unchanged`, never `empty`. `list` and `head` make the same single request; `head` also says it is a head read.

The board token names the search: `early-software` is the only one (`PRESETS`). A job's link is the one the page
gives it (`jobs/results/<id>-<slug>`), else `jobs/results/<id>`.
"""
from __future__ import annotations

import datetime
import json
import re

from outreach.crawl.ats._common import fetch_list, read_error
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.text import html_to_text
from outreach.net import HttpClient, Response
from outreach.store.timefmt import ISO_FMT

__all__ = ["GoogleAdapter", "PRESETS", "results_url", "parse_page"]

_SITE = "https://www.google.com/about/careers/applications/"
# token -> the results query. robots.txt disallows any `page` parameter, so none of these may carry one.
PRESETS = {
    "early-software": "location=United%20States&target_level=EARLY&sort_by=date&q=software%20engineer",
}
_BLOCK = re.compile(r"AF_initDataCallback\(\{\s*key:\s*'ds:1'\s*,[^{}]*?\bdata:")
_LINK = re.compile(r'href="jobs/results/([0-9]{1,30})-([a-z0-9]+(?:-[a-z0-9]+)*)[?"]')
_ID = re.compile(r"[0-9]{1,30}")


def results_url(token: str) -> str | None:
    query = PRESETS.get(token) if isinstance(token, str) else None
    return f"{_SITE}jobs/results?{query}" if query else None


def parse_page(text: str) -> tuple[list, int] | str:
    """(rows, total) from the ds:1 block, or what is wrong with the page. No jobs is ([], total)."""
    found = _BLOCK.search(text)
    if found is None:
        return "the page has no ds:1 data block"
    try:
        data, _ = json.JSONDecoder().raw_decode(text, found.end())
    except ValueError as e:
        return f"the ds:1 data block is not JSON ({e})"
    if not isinstance(data, list) or not data:
        return "the ds:1 data block is not a list"
    rows = data[0] if data[0] is not None else []
    if not isinstance(rows, list):
        return "the ds:1 data block has no list of jobs"
    total = data[2] if len(data) > 2 else None
    if total is None and not rows:
        total = 0
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        return "the ds:1 data block has no total"
    return rows, total


def _text(value) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _html(row: list, i: int) -> str:
    """The text of a `[null, html]` field, '' when it is missing."""
    cell = row[i] if len(row) > i else None
    html = cell[1] if isinstance(cell, list) and len(cell) > 1 else None
    return html_to_text(html) if isinstance(html, str) and html.strip() else ""


def _created(row: list) -> str | None:
    cell = row[12] if len(row) > 12 else None
    seconds = cell[0] if isinstance(cell, list) and cell else None
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
        return None
    try:
        return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).strftime(ISO_FMT)
    except (OverflowError, OSError, ValueError):
        return None


def _parse(row: object, links: dict[str, str]) -> JobRef | None:
    """One job, or None when it lacks an id or a title."""
    if not isinstance(row, list) or len(row) < 2:
        return None
    native_id = row[0] if isinstance(row[0], str) and _ID.fullmatch(row[0]) else ""
    title = _text(row[1])
    if not native_id or not title:
        return None
    places = row[9] if len(row) > 9 and isinstance(row[9], list) else []
    places = [p for p in places if isinstance(p, list) and p]
    shown = [_text(p[0]) for p in places if _text(p[0])]
    countries = [p[5] if len(p) > 5 else None for p in places]
    body = "\n\n".join(part for part in (_html(row, 10), _html(row, 3) and f"Responsibilities:\n{_html(row, 3)}",
                                         _html(row, 4), _html(row, 15)) if part)
    created = _created(row)
    return JobRef(native_id=native_id, title=title, url=f"{_SITE}jobs/results/{links.get(native_id, native_id)}",
                  location="; ".join(shown), country="US" if countries and all(c == "US" for c in countries) else None,
                  posted_on=created[:10] if created else None, body=body or None, posted_at=created)


class GoogleAdapter:
    ats = "google"

    def __init__(self, client: HttpClient):
        self.client = client

    def _read(self, token: str, head: bool) -> ReadResult:
        url = results_url(token)
        if url is None:
            return read_error(f"{token!r} is not a Google Careers search (the reader knows: {', '.join(PRESETS)})")
        got = fetch_list(self.client, url, Validators())
        if isinstance(got, ReadResult):
            return got
        return self._result(got, head)

    @staticmethod
    def _result(resp: Response, head: bool) -> ReadResult:
        text = resp.text()
        parsed = parse_page(text)
        if isinstance(parsed, str):
            return read_error(parsed, status=resp.status)
        rows, total = parsed
        if not rows:
            return ReadResult("unchanged", (), Validators(), http_status=resp.status, head=head)
        links = {m.group(1): f"{m.group(1)}-{m.group(2)}" for m in _LINK.finditer(text)}
        jobs: dict[str, JobRef] = {}
        broken = 0
        for row in rows:
            job = _parse(row, links)
            if job is None:
                broken += 1
                continue
            jobs.setdefault(job.native_id, job)
        if not jobs:
            return read_error(f"none of the {len(rows)} jobs on the page could be read", status=resp.status)
        note = f"first page only: the newest {len(jobs)} of {total} postings"
        if broken:
            note += f" ({broken} broken)"
        return ReadResult("ok", tuple(jobs.values()), Validators(), http_status=resp.status, partial=True,
                          partial_note=note, head=head)

    def list(self, token: str, validators: Validators) -> ReadResult:
        """Page 1, a partial `ok` (or `unchanged` when it lists nothing): robots.txt allows no other page."""
        return self._read(token, head=False)

    def head(self, token: str) -> ReadResult:
        """The same single request as `list`, marked as a head read (#251 part 2)."""
        return self._read(token, head=True)

    def body(self, token: str, job: JobRef) -> str | None:
        return None  # the page carries every job's text
