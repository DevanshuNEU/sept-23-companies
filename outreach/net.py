"""The one HTTP layer: every stream's requests go through here (standard library only; #31 and #21 import it).

Transport failures raise FetchError. Any HTTP status, 404 and 503 included, comes back as a Response, because what a
status means is the caller's call. The default profile declares itself with USER_AGENT and sends nothing else that
identifies anyone: no email address, no contact URL, no name.
"""
from __future__ import annotations

import http.client
import http.cookiejar
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

__all__ = ["USER_AGENT", "Response", "FetchError", "HttpClient", "UrllibClient", "SpacedClient"]

USER_AGENT = "outreach-crawler/1.0 (personal job search)"

# The Chrome header set from recon/lca/dl.sh: the DOL site answers 403 to a bare client. No Accept-Encoding, because
# urllib does not decompress and the server would send a compressed body we could not read.
_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/129.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "sec-ch-ua": '"Chromium";v="129", "Not=A?Brand";v="8"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}
_DECLARED_HEADERS = {"User-Agent": USER_AGENT}
_PROFILES = {"declared": _DECLARED_HEADERS, "browser": _BROWSER_HEADERS}
_REDIRECTS = (301, 302, 303, 307, 308)
_CHUNK = 64 * 1024


@dataclass(frozen=True)
class Response:
    status: int
    url: str
    body: bytes
    headers: Mapping[str, str]  # keys lower case

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> object:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ValueError(f"not JSON: {e}") from e


class FetchError(Exception):
    """The request did not produce an HTTP response: a network failure, a timeout, or a body over the size cap."""

    def __init__(self, kind: Literal["network", "timeout", "too_large"], message: str = ""):
        super().__init__(f"{kind}: {message}" if message else kind)
        self.kind = kind


class HttpClient(Protocol):
    def get(self, url: str, headers: Mapping[str, str] | None = None, *, follow_redirects: bool = False) -> Response:
        ...

    def post(self, url: str, body: bytes, headers: Mapping[str, str] | None = None) -> Response:
        ...

    def download(self, url: str, dest: Path, headers: Mapping[str, str] | None = None) -> int:
        """Stream to `dest.part`, then rename. A failure leaves no `dest`. Returns the bytes written."""
        ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _is_timeout(e: BaseException) -> bool:
    return isinstance(e, (socket.timeout, TimeoutError)) or (
        isinstance(e, urllib.error.URLError) and isinstance(e.reason, (socket.timeout, TimeoutError)))


class UrllibClient:
    """The real client. `declared` sends USER_AGENT only; `browser` sends Chrome's header set and keeps cookies.

    A caller's explicit header, a User-Agent included, replaces the profile's value of the same name (case blind).
    """

    def __init__(self, profile: Literal["declared", "browser"] = "declared", timeout_s: float = 30.0,
                 max_bytes: int = 20_000_000, max_redirects: int = 3):
        if profile not in _PROFILES:
            raise ValueError(f"profile {profile!r} is not one of {sorted(_PROFILES)}")
        self.profile = profile
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        handlers: list = [_NoRedirect()]
        self._jar: http.cookiejar.CookieJar | None = None
        if profile == "browser":
            self._jar = http.cookiejar.CookieJar()
            handlers.append(urllib.request.HTTPCookieProcessor(self._jar))
        self._opener = urllib.request.build_opener(*handlers)
        self._opener.addheaders = []  # no urllib default User-Agent: the profile decides

    def _request(self, url: str, headers: Mapping[str, str] | None) -> urllib.request.Request:
        merged = {k.lower(): (k, v) for k, v in _PROFILES[self.profile].items()}
        for k, v in (headers or {}).items():
            merged[k.lower()] = (k, v)
        if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
            raise FetchError("network", f"{url!r} is not an http or https URL")
        try:
            return urllib.request.Request(url, headers=dict(merged.values()))
        except ValueError as e:
            raise FetchError("network", f"{url!r}: {e}") from e

    def _open(self, req: urllib.request.Request):
        """Send one request without following redirects; return (status, url, headers, stream)."""
        try:
            try:
                resp = self._opener.open(req, timeout=self.timeout_s)
            except urllib.error.HTTPError as e:
                resp = e  # an HTTP status is an answer, not a failure
            return resp
        except FetchError:
            raise
        except (OSError, http.client.HTTPException, ValueError) as e:
            raise FetchError("timeout" if _is_timeout(e) else "network", f"{req.full_url}: {e}") from e

    def _read(self, resp) -> bytes:
        declared = resp.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            raise FetchError("too_large", f"{declared} bytes over {self.max_bytes}")
        try:
            data = resp.read(self.max_bytes + 1)
        except (OSError, http.client.HTTPException) as e:
            raise FetchError("timeout" if _is_timeout(e) else "network", str(e)) from e
        if len(data) > self.max_bytes:
            raise FetchError("too_large", f"over {self.max_bytes} bytes")
        return data

    @staticmethod
    def _headers(resp) -> dict[str, str]:
        return {k.lower(): v for k, v in resp.headers.items()}

    def _send(self, req: urllib.request.Request, follow: bool, headers: Mapping[str, str] | None = None):
        """Open, following up to max_redirects GET redirects when asked. Returns the open response."""
        hops = 0
        while True:
            resp = self._open(req)
            status = resp.status if hasattr(resp, "status") else resp.getcode()
            location = resp.headers.get("Location")
            if not (follow and status in _REDIRECTS and location and hops < self.max_redirects):
                return resp
            resp.close()
            hops += 1
            req = self._request(urllib.parse.urljoin(req.full_url, location), headers)

    def _do(self, req, follow: bool, headers=None) -> Response:
        resp = self._send(req, follow, headers)
        try:
            status = resp.status if hasattr(resp, "status") else resp.getcode()
            return Response(status, resp.geturl() if hasattr(resp, "geturl") else req.full_url, self._read(resp),
                            self._headers(resp))
        finally:
            resp.close()

    def get(self, url: str, headers: Mapping[str, str] | None = None, *, follow_redirects: bool = False) -> Response:
        return self._do(self._request(url, headers), follow_redirects, headers)

    def post(self, url: str, body: bytes, headers: Mapping[str, str] | None = None) -> Response:
        req = self._request(url, headers)
        req.data = body
        req.method = "POST"
        return self._do(req, False)

    def download(self, url: str, dest: Path, headers: Mapping[str, str] | None = None) -> int:
        dest = Path(dest)
        part = dest.with_name(dest.name + ".part")
        resp = self._send(self._request(url, headers), True, headers)
        try:
            status = resp.status if hasattr(resp, "status") else resp.getcode()
            if not 200 <= status < 300:
                raise FetchError("network", f"{url}: HTTP {status}")
            declared = resp.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > self.max_bytes:
                raise FetchError("too_large", f"{declared} bytes over {self.max_bytes}")
            total = 0
            try:
                with open(part, "wb") as f:
                    while True:
                        try:
                            chunk = resp.read(_CHUNK)
                        except (OSError, http.client.HTTPException) as e:
                            raise FetchError("timeout" if _is_timeout(e) else "network", str(e)) from e
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > self.max_bytes:
                            raise FetchError("too_large", f"over {self.max_bytes} bytes")
                        f.write(chunk)
                os.replace(part, dest)
            except BaseException:
                part.unlink(missing_ok=True)
                raise
            return total
        finally:
            resp.close()


# data.sec.gov and www.sec.gov get a short interval: 0.15 s keeps SEC under 10 requests a second.
_DEFAULT_PER_HOST = {"data.sec.gov": 0.15, "www.sec.gov": 0.15}


class SpacedClient:
    """Waits so two requests to one host start at least the interval apart.

    `per_host` maps a host to its own interval; when given it replaces the SEC defaults. Hosts it does not name use
    `min_interval_s`.
    """

    def __init__(self, inner: HttpClient, *, min_interval_s: float = 1.5, per_host: dict[str, float] | None = None,
                 clock=time.monotonic, sleep=time.sleep):
        self.inner = inner
        self.min_interval_s = min_interval_s
        self.per_host = dict(_DEFAULT_PER_HOST if per_host is None else per_host)
        self._clock = clock
        self._sleep = sleep
        self._next_start: dict[str, float] = {}

    def _wait(self, url: str) -> None:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        interval = self.per_host.get(host, self.min_interval_s)
        now = self._clock()
        due = self._next_start.get(host, now)
        wait = max(0.0, due - now)
        if wait:
            self._sleep(wait)
        self._next_start[host] = max(now, due) + interval

    def get(self, url: str, headers: Mapping[str, str] | None = None, *, follow_redirects: bool = False) -> Response:
        self._wait(url)
        return self.inner.get(url, headers, follow_redirects=follow_redirects)

    def post(self, url: str, body: bytes, headers: Mapping[str, str] | None = None) -> Response:
        self._wait(url)
        return self.inner.post(url, body, headers)

    def download(self, url: str, dest: Path, headers: Mapping[str, str] | None = None) -> int:
        self._wait(url)
        return self.inner.download(url, dest, headers)
