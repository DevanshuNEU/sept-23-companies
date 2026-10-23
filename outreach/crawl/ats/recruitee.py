"""Recruitee: one list request that carries every offer with its text (#21, Task 24).

`{token}.recruitee.com/api/offers/` answers `{"offers": [...]}`. The body must be an object whose `offers` is a list:
`{"offers": null}`, a missing key or another shape is `error`, never `empty` (Review Focus 2; the ats-scrapers
references write `payload.get("offers") or []`, which would turn all of them into "no jobs").

The raw body changes on every request (counters, timestamps), so there is no ETag: the fingerprint is over the
sorted ids only. `body()` has nothing to fetch, because the offer's description and requirements are in the list.

The token becomes a host label, so it must be one: letters, digits and inner hyphens.
"""
from __future__ import annotations

import re

from outreach.crawl.ats._common import d10, fetch_list, finish_list, json_body, read_error, safe_token
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.text import html_to_text
from outreach.net import HttpClient

__all__ = ["RecruiteeAdapter"]

_LIST = "https://{token}.recruitee.com/api/offers/"
_OFFER = "https://{token}.recruitee.com/o/{slug}"
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")
_WEB_URL = re.compile(r"https?://[^\s]+")


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _native_id(raw) -> str:
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        return ""
    text = str(raw).strip()
    return text if _SAFE_ID.fullmatch(text) else ""


def _location(offer: dict) -> str:
    return _text(offer.get("location")) or ", ".join(
        p for p in (_text(offer.get("city")), _text(offer.get("country"))) if p)


def _country(value) -> str | None:
    code = _text(value).upper()
    return code if len(code) == 2 and code.isalpha() else None


def _body(offer: dict) -> str | None:
    parts = [html_to_text(v) for v in (offer.get("description"), offer.get("requirements")) if isinstance(v, str)]
    return "\n\n".join(p for p in parts if p) or None


def _parse(token: str, offer: object) -> JobRef | None:
    """One offer, or None when it lacks a usable id or a title."""
    if not isinstance(offer, dict):
        return None
    native_id, title = _native_id(offer.get("id")), _text(offer.get("title"))
    if not native_id or not title:
        return None
    url, slug = _text(offer.get("careers_url")), _text(offer.get("slug"))
    if not _WEB_URL.fullmatch(url):
        url = _OFFER.format(token=token, slug=slug) if _SAFE_ID.fullmatch(slug) else ""
    return JobRef(native_id=native_id, title=title, url=url, location=_location(offer),
                  country=_country(offer.get("country_code")), posted_on=d10(offer.get("created_at")),
                  body=_body(offer))


class RecruiteeAdapter:
    ats = "recruitee"

    def __init__(self, client: HttpClient):
        self.client = client

    def list(self, token: str, validators: Validators) -> ReadResult:
        if not (safe_token(self.ats, token) and _LABEL.fullmatch(token)):
            return read_error(f"{token!r} is not a valid recruitee token")
        resp = fetch_list(self.client, _LIST.format(token=token), validators, use_etag=False)
        if isinstance(resp, ReadResult):
            return resp
        data = json_body(resp)
        if isinstance(data, ReadResult):
            return data
        offers = data.get("offers") if isinstance(data, dict) else None
        if not isinstance(offers, list):
            return read_error("the 200 answer is not an object with an offers list", status=resp.status)
        jobs: list[JobRef] = []
        for item in offers:
            job = _parse(token, item)
            if job is None:
                return read_error("an offer has no usable id or no title", status=resp.status)
            jobs.append(job)
        return finish_list(jobs, resp, validators, use_etag=False)

    def body(self, token: str, job: JobRef) -> str | None:
        return None
