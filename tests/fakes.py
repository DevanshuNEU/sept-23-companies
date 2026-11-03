"""The one fake HTTP client every stream's tests use (#30 Task 2).

`FakeClient(routes)`: a route key is a URL (a GET) or a `("POST", url)` pair. The value is one of
  - a `Response`, or `bytes`, `str` or `Path` (a 200 body),
  - an `Exception` (raised; a `FetchError` stands for a transport failure),
  - a callable `(method, url, headers, body) -> Response`,
  - a list of the above, consumed in order (an exhausted list is an unrouted request).
An unrouted request raises `UnexpectedRequest` (an AssertionError), so a test can never pass by accident. But code
under test that catches `Exception` can swallow that error, so every unrouted URL is also recorded in
`client.unrouted`: a test should assert `client.unrouted == []`.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from outreach.net import FetchError, Response

__all__ = ["FakeClient", "UnexpectedRequest", "feed_off", "json_response", "text_response", "status_response",
           "with_greenhouse_details"]


class UnexpectedRequest(AssertionError):
    """The code under test asked for a URL the test did not route."""


def _lower(headers: Mapping[str, str] | None) -> dict[str, str]:
    return {k.lower(): v for k, v in (headers or {}).items()}


def feed_off():
    """A patch (call `.start()`, or use it as a context) that switches the tick's SimplifyJobs feed poll off, for tests
    that are about something else and route no feed URL (#153: the poll is test_pipeline_simplify_feed's business)."""
    from unittest import mock

    from outreach.pipeline.config import PipelineConfig, SimplifyConfig
    return mock.patch("outreach.crawl.tick.load_pipeline_config",
                      return_value=PipelineConfig(SimplifyConfig(url="https://example.invalid/off", categories=("x",),
                                                                 enabled=False)))


def json_response(obj: object, *, status: int = 200, etag: str | None = None) -> Response:
    headers = {"content-type": "application/json"}
    if etag is not None:
        headers["etag"] = etag
    return Response(status, "", json.dumps(obj).encode("utf-8"), headers)


def text_response(text: str, *, status: int = 200, headers: Mapping[str, str] | None = None) -> Response:
    return Response(status, "", text.encode("utf-8"), _lower(headers))


def status_response(code: int) -> Response:
    return Response(code, "", b"", {})


class FakeClient:
    def __init__(self, routes: Mapping[object, object]):
        self.routes = {k: (list(v) if isinstance(v, list) else v) for k, v in routes.items()}
        self.calls: list[tuple[str, str, dict, bytes | None]] = []
        self.requests: list[str] = []
        self.unrouted: list[str] = []
        self.max_bytes_seen: list[int | None] = []  # the per-request cap of each get (None: not given)

    def _answer(self, method: str, url: str, headers: Mapping[str, str] | None, body: bytes | None) -> Response:
        headers = dict(headers or {})
        self.calls.append((method, url, headers, body))
        self.requests.append(url)
        key = url if method == "GET" else (method, url)
        if key not in self.routes:
            self.unrouted.append(url)
            raise UnexpectedRequest(f"{method} {url} is not routed; routes: {sorted(map(str, self.routes))}")
        value = self.routes[key]
        if isinstance(value, list):
            if not value:
                self.unrouted.append(url)
                raise UnexpectedRequest(f"{method} {url}: the route's list is used up")
            value = value.pop(0)
        return self._resolve(value, method, url, headers, body)

    @staticmethod
    def _resolve(value, method, url, headers, body) -> Response:
        if isinstance(value, Response):
            return value
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, (bytes, bytearray)):
            return Response(200, url, bytes(value), {})
        if isinstance(value, str):
            return Response(200, url, value.encode("utf-8"), {})
        if isinstance(value, Path):
            return Response(200, url, value.read_bytes(), {})
        if callable(value):
            return value(method, url, headers, body)
        raise TypeError(f"unusable route value {type(value).__name__}")

    def get(self, url: str, headers: Mapping[str, str] | None = None, *, follow_redirects: bool = False,
            max_bytes: int | None = None) -> Response:
        """`max_bytes` is recorded in `max_bytes_seen` and enforced like the real client: a body over it is a
        `too_large` FetchError."""
        self.max_bytes_seen.append(max_bytes)
        r = self._answer("GET", url, headers, None)
        if max_bytes is not None and len(r.body) > max_bytes:
            raise FetchError("too_large", f"over {max_bytes} bytes")
        return r

    def post(self, url: str, body: bytes, headers: Mapping[str, str] | None = None) -> Response:
        return self._answer("POST", url, headers, body)

    def download(self, url: str, dest: Path, headers: Mapping[str, str] | None = None) -> int:
        """Like UrllibClient.download: a non-2xx answer is a FetchError and leaves no file."""
        r = self._answer("GET", url, headers, None)
        if not 200 <= r.status < 300:
            raise FetchError("network", f"{url}: HTTP {r.status}")
        dest = Path(dest)
        part = dest.with_name(dest.name + ".part")
        part.write_bytes(r.body)
        os.replace(part, dest)
        return len(r.body)


_GH_LIST = re.compile(r"https://boards-api(?:\.eu)?\.greenhouse\.io/v1/boards/[a-z0-9-]+/jobs")
_GH_DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "crawl" / "greenhouse_detail.json"


def with_greenhouse_details(routes: Mapping[object, object], details: Mapping[str, object] | None = None,
                            ids: Iterable[str] = ()) -> dict:
    """`routes` plus a detail answer (`<list url>/<id>`) for every job a Greenhouse list route carries and for each of
    `ids`, so a read of the board can fetch bodies (#152: the list carries none). `ids` covers a read whose list
    answer carries no jobs (a 304) but whose stored postings still lack a body. `details` maps a job id to the
    answer: a `Response`, or text, which becomes the posting's HTML content. A job it does not name answers like the
    fixture's job (4012345001) when it is that one, and 404 otherwise. A route already in `routes` is kept."""
    out = dict(routes)
    for url, value in routes.items():
        if not isinstance(url, str) or not _GH_LIST.fullmatch(url):
            continue
        found = {str(i) for i in ids}
        for item in value if isinstance(value, list) else [value]:
            raw = item.body if isinstance(item, Response) else item if isinstance(item, (bytes, bytearray)) else None
            try:
                jobs = json.loads(raw).get("jobs") if raw else None
            except (ValueError, AttributeError):
                continue
            found |= {str(j.get("id")) for j in jobs if isinstance(j, dict)} if isinstance(jobs, list) else set()
        for native_id in sorted(found):
            if f"{url}/{native_id}" in out:
                continue
            answer = (details or {}).get(native_id)
            if isinstance(answer, str):
                answer = json_response({"id": native_id, "content": answer})
            elif answer is None:
                answer = (Response(200, "", _GH_DETAIL_FIXTURE.read_bytes(), {})
                          if native_id == "4012345001" else status_response(404))
            out[f"{url}/{native_id}"] = answer
    return out
