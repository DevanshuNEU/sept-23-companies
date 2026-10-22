"""Helpers every ATS adapter shares: the token check, one list request, the JSON check and the closing step (#21).

The rule they keep: a 200 is `ok` only when its body has the shape the ATS returns. A non-200, a redirect, a
transport failure, a page that is not JSON and (in each adapter) a wrong shape are all `error`, never `empty`.
"""
from __future__ import annotations

import datetime
import re
from collections.abc import Iterable, Mapping

from outreach.crawl.base import JobRef, ReadResult, Validators, ids_fingerprint, valid_token
from outreach.net import FetchError, HttpClient, Response

__all__ = ["safe_token", "read_error", "fetch_list", "json_body", "finish_list", "d10"]


def safe_token(ats: str, token: str) -> bool:
    """The one token grammar (`valid_token`): an adapter checks it before it puts a token in a URL."""
    return valid_token(ats, token)


def read_error(message: str, *, status: int | None = None) -> ReadResult:
    return ReadResult("error", (), Validators(), http_status=status, error=message)


def fetch_list(client: HttpClient, url: str, validators: Validators, *, headers: Mapping[str, str] | None = None,
               use_etag: bool = False, method: str = "GET", body: bytes | None = None) -> Response | ReadResult:
    """Send the list request. Returns the 200 `Response`, or the `ReadResult` that ends the read.

    With `use_etag` and a stored ETag the request carries If-None-Match, and a 304 is `unchanged`. A 304 to a request
    that carried no If-None-Match proves nothing, so it is an `error`. `HostTripped` is not caught: it says nothing
    about the board.
    """
    sent = dict(headers or {})
    conditional = bool(use_etag and validators.etag)
    if conditional:
        sent["If-None-Match"] = validators.etag
    try:
        resp = client.post(url, body or b"", sent) if method == "POST" else client.get(url, sent)
    except FetchError as e:
        return read_error(f"fetch failed: {e}")
    if resp.status == 304 and conditional:
        return ReadResult("unchanged", (), validators, http_status=304)
    if resp.status != 200:
        kind = "redirect" if 300 <= resp.status < 400 else "HTTP"
        return read_error(f"{kind} {resp.status} from {url}", status=resp.status)
    return resp


def json_body(resp: Response) -> object | ReadResult:
    """The parsed body, or an `error` read when it is not JSON (a challenge page, an HTML 200)."""
    try:
        return resp.json()
    except ValueError as e:
        return read_error(f"the 200 answer is not JSON ({e})", status=resp.status)


def finish_list(jobs: Iterable[JobRef], resp: Response, validators: Validators, *, use_etag: bool = False,
                fingerprint_ids: Iterable[str] | None = None, board_name: str = "") -> ReadResult:
    """Turn parsed jobs into the read's result: repeated ids dropped (first wins), then empty, unchanged or ok.

    An ETag adapter never reports `unchanged` here: its 200 already says the board changed. A fingerprint adapter
    reports `unchanged` when the ids are the ones it saw last time.
    """
    seen: set[str] = set()
    unique: list[JobRef] = []
    for job in jobs:
        if job.native_id not in seen:
            seen.add(job.native_id)
            unique.append(job)
    if not unique:
        return ReadResult("empty", (), Validators(), board_name=board_name)
    fingerprint = ids_fingerprint(list(fingerprint_ids) if fingerprint_ids is not None else seen)
    if not use_etag and fingerprint == validators.ids_fingerprint:
        return ReadResult("unchanged", (), validators, board_name=board_name)
    etag = resp.headers.get("etag", "") if use_etag else ""
    return ReadResult("ok", tuple(unique), Validators(etag=etag, ids_fingerprint=fingerprint), board_name=board_name)


_ISO_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def d10(value) -> str | None:
    """An ISO date prefix, or epoch seconds or milliseconds, as YYYY-MM-DD; None when it is neither."""
    if isinstance(value, bool) or not value:
        return None
    if isinstance(value, (int, float)):
        text = str(int(value)) if value == value and abs(value) != float("inf") else ""
    else:
        text = str(value)
    m = _ISO_DATE.match(text)
    if m:
        return m.group(1)
    if not text.isdigit():
        return None
    n = int(text)
    seconds = n / 1000 if n > 1e11 else n
    try:
        return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None
