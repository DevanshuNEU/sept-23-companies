"""The one fake HTTP client every stream's tests use (#30 Task 2).

`FakeClient(routes)`: a route key is a URL (a GET) or a `("POST", url)` pair. The value is one of
  - a `Response`, or `bytes`, `str` or `Path` (a 200 body),
  - an `Exception` (raised; a `FetchError` stands for a transport failure),
  - a callable `(method, url, headers, body) -> Response`,
  - a list of the above, consumed in order (an exhausted list is an unrouted request).
An unrouted request raises `UnexpectedRequest` (an AssertionError), so a test can never pass by accident.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from outreach.net import FetchError, Response

__all__ = ["FakeClient", "UnexpectedRequest", "json_response", "text_response", "status_response"]


class UnexpectedRequest(AssertionError):
    """The code under test asked for a URL the test did not route."""


def _lower(headers: Mapping[str, str] | None) -> dict[str, str]:
    return {k.lower(): v for k, v in (headers or {}).items()}


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

    def _answer(self, method: str, url: str, headers: Mapping[str, str] | None, body: bytes | None) -> Response:
        headers = dict(headers or {})
        self.calls.append((method, url, headers, body))
        self.requests.append(url)
        key = url if method == "GET" else (method, url)
        if key not in self.routes:
            raise UnexpectedRequest(f"{method} {url} is not routed; routes: {sorted(map(str, self.routes))}")
        value = self.routes[key]
        if isinstance(value, list):
            if not value:
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

    def get(self, url: str, headers: Mapping[str, str] | None = None, *, follow_redirects: bool = False) -> Response:
        return self._answer("GET", url, headers, None)

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
