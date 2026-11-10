"""The polite wrapper: per-host spacing and a circuit breaker around any HttpClient (#21).

It adds nothing else: no retries, no parsing, no judgment of a status beyond "did this host push back". The
declared User-Agent comes from the crawl config. A host that keeps failing trips its bucket and the crawler defers
that bucket's boards with no streak change (a tripped bucket is an outage, not a finding about any one board). A host
of an ATS whose reader is switched off (`ats_off`, #46) is never sent to: `HostOff`, a `HostTripped`.
"""
from __future__ import annotations

import re
import threading
import time
import urllib.parse
from collections.abc import Mapping
from pathlib import Path

from outreach.crawl.config import CrawlConfig
from outreach.net import FetchError, HttpClient, Response

__all__ = ["HostTripped", "HostOff", "bucket_for", "spacing_key", "PoliteClient"]


class HostTripped(Exception):
    """A bucket's breaker is open, so the request was not sent. Not a FetchError: an adapter that turns fetch
    failures into an `error` read must not swallow this, because it says nothing about the board."""

    def __init__(self, bucket: str):
        super().__init__(f"{bucket}: too many failures, not sending")
        self.bucket = bucket


class HostOff(HostTripped):
    """The host belongs to an ATS whose reader is switched off in config.toml (#46), so the request was not sent. A
    HostTripped, so every caller treats it as an outage (a deferral, no streak change, no item), never as a finding
    about a board or a page."""

    def __init__(self, bucket: str, host: str, ats: str, reason: str):
        Exception.__init__(self, f"{host}: the {ats} reader is switched off ({reason}), not sending")
        self.bucket, self.host, self.ats, self.reason = bucket, host, ats, reason


def bucket_for(host: str, cfg: CrawlConfig) -> str:
    """The longest configured suffix that matches `host` (the suffix itself or a subdomain of it), else the host."""
    host = host.lower().rstrip(".")
    best = ""
    for suffix in cfg.host_interval_s:
        if (host == suffix or host.endswith("." + suffix)) and len(suffix) > len(best):
            best = suffix
    return best or host


def spacing_key(host: str, cfg: CrawlConfig) -> str:
    """What a request waits behind: the host itself when its bucket is in `split_hosts` (each tenant of an ATS is its
    own server), else the bucket. The breaker always counts by bucket."""
    bucket = bucket_for(host, cfg)
    return host.lower().rstrip(".") if bucket in cfg.split_hosts else bucket


_REDIRECTS = (301, 302, 303, 307, 308)
_HTTP_STATUS = re.compile(r"\bHTTP (\d{3})\b")


def _host(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def _status_of(e: FetchError) -> int | None:
    """The HTTP status a failed download reports in its message ("<url>: HTTP 404"), else None."""
    m = _HTTP_STATUS.search(str(e))
    return int(m.group(1)) if m else None


class PoliteClient:
    def __init__(self, inner: HttpClient, cfg: CrawlConfig, *, clock=time.monotonic, sleep=time.sleep):
        self.inner = inner
        self.cfg = cfg
        self._clock = clock
        self._sleep = sleep
        # One lock guards the two dicts below and nothing else: never a sleep, never a request. Several worker
        # threads share one client (#158).
        self._lock = threading.Lock()
        self._next_start: dict[str, float] = {}
        self._failures: dict[str, int] = {}

    def tripped(self, bucket: str) -> bool:
        with self._lock:
            return self._failures.get(bucket, 0) >= self.cfg.breaker_failures

    def _headers(self, headers: Mapping[str, str] | None) -> dict[str, str]:
        merged = {k: v for k, v in (headers or {}).items() if k.lower() != "user-agent"}
        merged["User-Agent"] = self.cfg.user_agent
        return merged

    def _begin(self, url: str) -> str:
        """Refuse a switched-off ATS's host and a tripped bucket, wait out the bucket's spacing, then refuse a bucket
        that tripped meanwhile. Returns the bucket. Every request and every redirect hop starts here, so nothing reaches a switched-off host."""
        host = _host(url)
        bucket = bucket_for(host, self.cfg)
        ats = self.cfg.off_host(host)
        if ats is not None:
            raise HostOff(bucket, host, ats, self.cfg.ats_off[ats])
        key = spacing_key(host, self.cfg)
        interval = self.cfg.host_interval_s.get(bucket, self.cfg.default_host_interval_s)
        with self._lock:
            if self._failures.get(bucket, 0) >= self.cfg.breaker_failures:
                raise HostTripped(bucket)
            # Reserve this request's start slot under the lock, then wait for it outside the lock.
            now = self._clock()
            due = self._next_start.get(key, now)
            self._next_start[key] = max(now, due) + interval
        wait = max(0.0, due - now)
        if wait:
            self._sleep(wait)
            # Another worker's 429 may have opened the breaker while this one slept. The switched-off check needs
            # no repeat: the config never changes.
            if self.tripped(bucket):
                raise HostTripped(bucket)
        return bucket

    def _failed(self, bucket: str, *, at_once: bool = False) -> None:
        with self._lock:
            self._failures[bucket] = self.cfg.breaker_failures if at_once else self._failures.get(bucket, 0) + 1

    def _reset(self, bucket: str) -> None:
        with self._lock:
            self._failures[bucket] = 0

    def _judge(self, bucket: str, status: int) -> None:
        if status == 429:
            self._failed(bucket, at_once=True)
        elif status == 403 or status >= 500:
            self._failed(bucket)
        elif status < 400 or status in (404, 410):
            self._reset(bucket)
        # any other 4xx says nothing about the host: the count stays where it is

    def _failed_fetch(self, bucket: str, e: FetchError) -> None:
        """Count a FetchError. A too_large body is the page's size, not the host's health. A status carried in the
        message (a failed download) counts as the same status on get."""
        if e.kind == "too_large":
            return
        status = _status_of(e)
        if status is not None:
            self._judge(bucket, status)
        else:
            self._failed(bucket)

    def _get_once(self, url: str, headers: Mapping[str, str] | None, max_bytes: int | None = None) -> Response:
        bucket = self._begin(url)
        extra = {} if max_bytes is None else {"max_bytes": max_bytes}
        try:
            r = self.inner.get(url, self._headers(headers), **extra)
        except FetchError as e:
            self._failed_fetch(bucket, e)
            raise
        self._judge(bucket, r.status)
        return r

    def get(self, url: str, headers: Mapping[str, str] | None = None, *, follow_redirects: bool = False,
            max_bytes: int | None = None) -> Response:
        """Redirects are followed here, never by the inner client, so each hop is spaced and counted in its own
        host's bucket. A hop to another host does not carry the caller's headers (the User-Agent is added again).
        `max_bytes` caps each response of this call only (#153); None keeps the inner client's own cap."""
        r = self._get_once(url, headers, max_bytes)
        hops = 0
        while follow_redirects and r.status in _REDIRECTS and r.headers.get("location") and hops < self.cfg.max_redirects:
            hops += 1
            target = urllib.parse.urljoin(url, r.headers["location"])
            if _host(target) != _host(url):
                headers = {}
            url = target
            r = self._get_once(url, headers, max_bytes)
        return r

    def post(self, url: str, body: bytes, headers: Mapping[str, str] | None = None) -> Response:
        bucket = self._begin(url)
        try:
            r = self.inner.post(url, body, self._headers(headers))
        except FetchError as e:
            self._failed_fetch(bucket, e)
            raise
        self._judge(bucket, r.status)
        return r

    def download(self, url: str, dest: Path, headers: Mapping[str, str] | None = None) -> int:
        bucket = self._begin(url)
        try:
            n = self.inner.download(url, dest, self._headers(headers))
        except FetchError as e:
            self._failed_fetch(bucket, e)
            raise
        self._reset(bucket)
        return n
