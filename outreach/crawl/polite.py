"""The polite wrapper: per-host spacing and a circuit breaker around any HttpClient (#21).

It adds nothing else: no retries, no parsing, no judgment of a status beyond "did this host push back". The
declared User-Agent comes from the crawl config. A host that keeps failing trips its bucket and the crawler defers
that bucket's boards with no streak change (a tripped bucket is an outage, not a finding about any one board).
"""
from __future__ import annotations

import time
import urllib.parse
from collections.abc import Mapping
from pathlib import Path

from outreach.crawl.config import CrawlConfig
from outreach.net import FetchError, HttpClient, Response

__all__ = ["HostTripped", "bucket_for", "PoliteClient"]


class HostTripped(Exception):
    """A bucket's breaker is open, so the request was not sent. Not a FetchError: an adapter that turns fetch
    failures into an `error` read must not swallow this, because it says nothing about the board."""

    def __init__(self, bucket: str):
        super().__init__(f"{bucket}: too many failures, not sending")
        self.bucket = bucket


def bucket_for(host: str, cfg: CrawlConfig) -> str:
    """The longest configured suffix that matches `host` (the suffix itself or a subdomain of it), else the host."""
    host = host.lower().rstrip(".")
    best = ""
    for suffix in cfg.host_interval_s:
        if (host == suffix or host.endswith("." + suffix)) and len(suffix) > len(best):
            best = suffix
    return best or host


def _bucket_of_url(url: str, cfg: CrawlConfig) -> str:
    return bucket_for(urllib.parse.urlsplit(url).hostname or "", cfg)


class PoliteClient:
    def __init__(self, inner: HttpClient, cfg: CrawlConfig, *, clock=time.monotonic, sleep=time.sleep):
        self.inner = inner
        self.cfg = cfg
        self._clock = clock
        self._sleep = sleep
        self._next_start: dict[str, float] = {}
        self._failures: dict[str, int] = {}

    def tripped(self, bucket: str) -> bool:
        return self._failures.get(bucket, 0) >= self.cfg.breaker_failures

    def _headers(self, headers: Mapping[str, str] | None) -> dict[str, str]:
        merged = {k: v for k, v in (headers or {}).items() if k.lower() != "user-agent"}
        merged["User-Agent"] = self.cfg.user_agent
        return merged

    def _begin(self, url: str) -> str:
        """Refuse a tripped bucket, then wait out the bucket's spacing. Returns the bucket."""
        bucket = _bucket_of_url(url, self.cfg)
        if self.tripped(bucket):
            raise HostTripped(bucket)
        interval = self.cfg.host_interval_s.get(bucket, self.cfg.default_host_interval_s)
        now = self._clock()
        due = self._next_start.get(bucket, now)
        wait = max(0.0, due - now)
        if wait:
            self._sleep(wait)
        self._next_start[bucket] = max(now, due) + interval
        return bucket

    def _failed(self, bucket: str, *, at_once: bool = False) -> None:
        self._failures[bucket] = self.cfg.breaker_failures if at_once else self._failures.get(bucket, 0) + 1

    def _judge(self, bucket: str, status: int) -> None:
        if status == 429:
            self._failed(bucket, at_once=True)
        elif status == 403 or status >= 500:
            self._failed(bucket)
        elif status < 400 or status in (404, 410):
            self._failures[bucket] = 0
        # any other 4xx says nothing about the host: the count stays where it is

    def get(self, url: str, headers: Mapping[str, str] | None = None, *, follow_redirects: bool = False) -> Response:
        bucket = self._begin(url)
        try:
            r = self.inner.get(url, self._headers(headers), follow_redirects=follow_redirects)
        except FetchError:
            self._failed(bucket)
            raise
        self._judge(bucket, r.status)
        return r

    def post(self, url: str, body: bytes, headers: Mapping[str, str] | None = None) -> Response:
        bucket = self._begin(url)
        try:
            r = self.inner.post(url, body, self._headers(headers))
        except FetchError:
            self._failed(bucket)
            raise
        self._judge(bucket, r.status)
        return r

    def download(self, url: str, dest: Path, headers: Mapping[str, str] | None = None) -> int:
        bucket = self._begin(url)
        try:
            n = self.inner.download(url, dest, self._headers(headers))
        except FetchError:
            self._failed(bucket)
            raise
        self._failures[bucket] = 0
        return n
