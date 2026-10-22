"""Teamtailor: an RSS feed read in pages, with the posting text in the feed (#21, Task 23).

`{token}.teamtailor.com/jobs.rss?per_page=N&offset=M` answers an RSS feed whose items carry the link, the posted date,
the description and the locations. The feed is paged by asking for `PAGE_SIZE` items at a time. Whether the API really
honors `offset` is not something to assume (Review Focus 1): a page that repeats an id already seen means it did not,
and the read is `error`, because stopping there would be a guess about a list that may be longer. A page cap bounds
the loop in the same way.

Only the first page is asked with If-None-Match, and its ETag is kept only when that page was the whole feed: an ETag
of page one says nothing about page two. A longer feed, or one that sends no ETag, is compared by its ids instead.

The token becomes a host label, so it must be one: letters, digits and inner hyphens. Only `https://{token}.teamtailor.com`
is ever asked, and a redirect is never followed.
"""
from __future__ import annotations

import email.utils
import html
import re
import xml.etree.ElementTree as ET

from outreach.crawl.ats._common import d10, fetch_list, finish_list, read_error, safe_token
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.text import html_to_text
from outreach.net import HttpClient

__all__ = ["TeamtailorAdapter"]

PAGE_SIZE = 100
MAX_PAGES = 50  # 5,000 postings: a feed longer than this is `error`, not a quiet cut
_URL = "https://{token}.teamtailor.com/jobs.rss?per_page={size}&offset={offset}"
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
_JOB_NUMBER = re.compile(r"/jobs/(\d+)")
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")
_WEB_URL = re.compile(r"https?://[^\s]+")
_DOCTYPE = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child_text(node: ET.Element, name: str) -> str:
    for child in node:
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _native_id(link: str, guid: str) -> str:
    for source in (link, guid):
        found = _JOB_NUMBER.search(source)
        if found:
            return found.group(1)
    return guid if _SAFE_ID.fullmatch(guid) else ""


def _location(item: ET.Element) -> str:
    places: list[str] = []
    for node in item:
        if _local(node.tag) != "locations":
            continue
        for loc in node:
            name = _child_text(loc, "name") or ", ".join(
                p for p in (_child_text(loc, "city"), _child_text(loc, "country")) if p)
            if name and name not in places:
                places.append(name)
    return "; ".join(places)


def _posted(value: str) -> str | None:
    try:
        return d10(email.utils.parsedate_to_datetime(value).date().isoformat())
    except (TypeError, ValueError, IndexError):
        return None


def _parse(item: ET.Element) -> JobRef | None:
    """One posting, or None when it lacks a usable id or a title."""
    link, guid = _child_text(item, "link"), _child_text(item, "guid")
    native_id, title = _native_id(link, guid), _child_text(item, "title")
    if not native_id or not title:
        return None
    return JobRef(native_id=native_id, title=title, url=link if _WEB_URL.fullmatch(link) else "",
                  location=_location(item), country=None, posted_on=_posted(_child_text(item, "pubDate")),
                  body=html_to_text(html.unescape(_child_text(item, "description"))) or None)


def _items(body: bytes) -> list[ET.Element] | str:
    """The feed's items, or the reason the body is not an RSS feed."""
    if _DOCTYPE.search(body):
        return "the 200 answer declares a DOCTYPE or an entity"
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        return f"the 200 answer is not XML ({e})"
    if _local(root.tag) != "rss":
        return f"the 200 answer is not an RSS feed (root is {_local(root.tag)!r})"
    return [node for channel in root if _local(channel.tag) == "channel" for node in channel
            if _local(node.tag) == "item"]


class TeamtailorAdapter:
    ats = "teamtailor"

    def __init__(self, client: HttpClient):
        self.client = client

    def list(self, token: str, validators: Validators) -> ReadResult:
        if not (safe_token(self.ats, token) and _LABEL.fullmatch(token)):
            return read_error(f"{token!r} is not a valid teamtailor token")
        size = PAGE_SIZE
        jobs: list[JobRef] = []
        seen: set[str] = set()
        first = None
        pages = 0
        while True:
            if pages >= MAX_PAGES:
                return read_error(f"the feed is still full after {MAX_PAGES} pages, so the list may be incomplete")
            link = _URL.format(token=token, size=size, offset=pages * size)
            resp = fetch_list(self.client, link, validators, use_etag=pages == 0)
            if isinstance(resp, ReadResult):
                return resp
            items = _items(resp.body)
            if isinstance(items, str):
                return read_error(items, status=resp.status)
            page = []
            for node in items:
                job = _parse(node)
                if job is None:
                    return read_error("an item has no usable id or no title", status=resp.status)
                page.append(job)
            ids = [job.native_id for job in page]
            if pages and seen.intersection(ids):
                return read_error("pagination not honored: a later page repeats ids already read", status=resp.status)
            seen.update(ids)
            jobs.extend(page)
            first = first or resp
            pages += 1
            if len(items) < size:
                break
        # The first page's ETag covers the whole feed only when that page was all of it.
        use_etag = pages == 1 and bool(first.headers.get("etag"))
        return finish_list(jobs, first, validators, use_etag=use_etag)

    def body(self, token: str, job: JobRef) -> str | None:
        return None
