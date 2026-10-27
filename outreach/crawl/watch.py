"""Weekly reads of watched careers pages and the `careers_changed` item (issue #21, Task 14).

A watched page belongs to a company with no readable board. Each pass fetches the pages that are due, at most
`watch_per_tick`, one request per page. A page's fingerprint is a hash of its text with dates removed, so a new
copyright year or a "3 days ago" never counts as a change. A different fingerprint is fetched once more at once and
only a change both fetches agree on is stored and raised, so a page that rotates its content raises nothing.

An error never concludes anything: a non-200, a transport failure, a challenge page or a page with no text (a
JavaScript shell) leaves the fingerprint alone and only backs the page off. A page that has never read and comes back
with no text raises a `flagged` "careers page unreadable" item at once; every other failure, including a short page
on a page that read before, raises it on the third error in a row. The item is raised once per evidence key: a
dismissed item stays dismissed, and the next usable read of the page clears an open one (resolution `cleared`), which
a later run of failures reopens.

A page on the host of an ATS whose reader is switched off (`ats_off`, #46) is never fetched: the polite client refuses
it (`HostOff`, a `HostTripped`) and the page is deferred like any refused host, with no error and no item.

No transaction is open during a request: every page is fetched first and written afterwards in one unit.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass

from outreach.crawl.cadence import add_seconds
from outreach.crawl.config import CrawlConfig, load_crawl_config
from outreach.crawl.polite import HostTripped
from outreach.crawl.text import html_to_text
from outreach.net import FetchError, HttpClient
from outreach.store import atomic, describe_error, finish_job, start_job, utcnow

__all__ = ["WatchReport", "page_fingerprint", "usable_text", "watch_pages", "UNREADABLE_AFTER"]

# Errors in a row before a page that is not JavaScript-only is flagged as unreadable.
UNREADABLE_AFTER = 3

_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
          r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_DASH = "(?:-|to|" + chr(0x2013) + ")"
_COPYRIGHT = re.compile(r"(?:\(c\)|©|copyright)(?:\s*(?:\(c\)|©))?\s*\d{4}(?:\s*" + _DASH + r"\s*\d{4})?")
_DATES = re.compile("|".join((
    r"\b\d{4}-\d{2}-\d{2}(?:[t ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:z|[+-]\d{2}:?\d{2})?)?",  # ISO date and time
    r"\b" + _MONTH + r"\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b",                           # October 18, 2026
    r"\b\d{1,2}(?:st|nd|rd|th)?\s+" + _MONTH + r"\.?,?\s+\d{4}\b",                           # 18 October 2026
    r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",                                                           # 10/18/2026
    r"\b(?:\d+|an?)\s+(?:minute|hour|day|week|month)s?\s+ago\b",                              # 3 days ago
)))

_CHALLENGE = ("just a moment", "enable javascript", "access denied", "captcha", "checking your browser",
              "verify you are human", "pardon our interruption")
# A page with more text than this is a real page that happens to mention a captcha (a reCAPTCHA footer), not a
# challenge page: a challenge page is a few lines.
_CHALLENGE_MAX_CHARS = 2000

_JS_ONLY = "javascript"
_CHALLENGED = "challenge"


@dataclass(frozen=True)
class WatchReport:
    baseline: int = 0
    unchanged: int = 0
    changed: int = 0
    unstable: int = 0
    error: int = 0
    deferred: int = 0  # the host's breaker was open: not requested, no streak change


def page_fingerprint(markup: str) -> str:
    """sha256 of the page text, lower-cased, with dates and copyright years removed (standalone years are kept:
    "2027 new grad" is content)."""
    text = html_to_text(markup, visible_only=False).lower()
    text = _COPYRIGHT.sub(" ", text)
    text = _DATES.sub(" ", text)
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def _unusable(markup: str, min_chars: int) -> str | None:
    """None when the page has real text, else why not: a challenge page, or too little text (a JavaScript shell)."""
    text = html_to_text(markup, visible_only=False)
    if len(text) < _CHALLENGE_MAX_CHARS and any(m in text.lower() for m in _CHALLENGE):
        return _CHALLENGED
    if len(text) < min_chars:
        return _JS_ONLY
    return None


def usable_text(markup: str, min_chars: int) -> bool:
    """False for text shorter than `min_chars` or a challenge page ("just a moment", "enable javascript", "access
    denied", "captcha")."""
    return _unusable(markup, min_chars) is None


@dataclass(frozen=True)
class _Fetched:
    kind: str  # "ok", "error" or "javascript"
    markup: str = ""
    why: str = ""


def _fetch(client: HttpClient, url: str, cfg: CrawlConfig) -> _Fetched:
    """One request. HostTripped propagates (it says nothing about the page)."""
    try:
        r = client.get(url, follow_redirects=True)
    except FetchError as e:
        return _Fetched("error", why=str(e))
    if r.status != 200:
        return _Fetched("error", why=f"HTTP {r.status}")
    markup = r.text()
    problem = _unusable(markup, cfg.watch_min_text_chars)
    if problem == _JS_ONLY:
        return _Fetched("javascript", why="the page has no readable text (JavaScript only)")
    if problem == _CHALLENGED:
        return _Fetched("error", why="a challenge page")
    return _Fetched("ok", markup)


def _due(conn: sqlite3.Connection, now: str, n: int) -> list[sqlite3.Row]:
    """Never-fetched pages first, then the oldest due; not for a closed, removed or merged company."""
    return conn.execute(
        "SELECT w.company_id, w.url, w.fingerprint, w.error_streak FROM watched_page w "
        "JOIN company c ON c.company_id = w.company_id "
        "WHERE c.status NOT IN ('closed', 'removed') AND c.merged_into IS NULL "
        "AND (w.next_fetch_at IS NULL OR w.next_fetch_at <= ?) "
        "ORDER BY w.next_fetch_at IS NOT NULL, w.next_fetch_at, w.company_id LIMIT ?", (now, n)).fetchall()


def watch_pages(conn: sqlite3.Connection, client: HttpClient, *, cfg: CrawlConfig | None = None,
                now_fn: Callable[[], str] = utcnow, limit: int | None = None) -> WatchReport:
    """Read the due watched pages once (one `job_run` of job `watch_pages`) and write what each read says.

    At most `cfg.watch_per_tick` pages, fewer when `limit` is smaller. Starts with no transaction open.
    """
    if conn.in_transaction:
        raise ValueError("watch_pages sends requests, so it must start with no transaction open")
    cfg = cfg or load_crawl_config()
    n = cfg.watch_per_tick if limit is None else min(limit, cfg.watch_per_tick)
    counts = {k: 0 for k in asdict(WatchReport())}
    run_id = start_job(conn, "watch_pages")
    try:
        for row in _due(conn, now_fn(), n):
            counts[_watch_one(conn, client, cfg, now_fn, row)] += 1
    except BaseException as e:
        finish_job(conn, run_id, "failed", counts, describe_error(e))
        raise
    finish_job(conn, run_id, "ok", counts)
    return WatchReport(**counts)


def _watch_one(conn: sqlite3.Connection, client: HttpClient, cfg: CrawlConfig, now_fn: Callable[[], str],
               row: sqlite3.Row) -> str:
    """Fetch one page (no transaction open), then write the outcome in one unit. Returns the report field to count."""
    company_id, url, stored = row["company_id"], row["url"], row["fingerprint"]
    try:
        first = _fetch(client, url, cfg)
        fp = second_fp = None
        if first.kind == "ok":
            fp = page_fingerprint(first.markup)
            if stored and fp != stored:  # a different page: ask again at once, only a change both fetches see counts
                second = _fetch(client, url, cfg)
                if second.kind != "ok":
                    first = second
                else:
                    second_fp = page_fingerprint(second.markup)
    except HostTripped:
        _write(conn, company_id, next_fetch_at=add_seconds(now_fn(), cfg.defer_s))
        return "deferred"
    except Exception as e:  # one page must not end the pass: it counts as an error read and backs off
        first = _Fetched("error", why=describe_error(e))

    now = now_fn()
    ordinary = add_seconds(now, cfg.watch_interval_s)
    if first.kind != "ok":
        streak = row["error_streak"] + 1
        wait = min(cfg.watch_interval_s, 86400 * 2 ** min(streak - 1, 30))
        with atomic(conn, "watch_error"):
            _write(conn, company_id, last_fetch_at=now, next_fetch_at=add_seconds(now, wait), error_streak=streak)
            # A page that never read and has no text will not read next week either: flag it now. Otherwise wait.
            if streak >= UNREADABLE_AFTER or (first.kind == "javascript" and not stored):
                _flag_unreadable(conn, company_id, url, first.why, now)
        return "error"
    if not stored:
        outcome, columns = "baseline", {"fingerprint": fp}
    elif fp == stored:
        outcome, columns = "unchanged", {}
    elif second_fp != fp:
        outcome, columns = "unstable", {}
    else:
        outcome, columns = "changed", {"fingerprint": fp, "last_change_at": now}
    with atomic(conn, "watch_read"):
        _write(conn, company_id, last_fetch_at=now, next_fetch_at=ordinary, error_streak=0, **columns)
        _clear_unreadable(conn, company_id, url, now)
        if outcome == "changed":
            conn.execute(
                "INSERT INTO review_item(kind, company_id, reason, evidence_key, raised_by, created_at) "
                "VALUES ('careers_changed', ?, ?, ?, 'crawler', ?) ON CONFLICT DO NOTHING",
                (company_id, f"Careers page changed: {url}", fp[:16], now))
    return outcome


def _write(conn: sqlite3.Connection, company_id: int, **columns: object) -> None:
    names = ", ".join(f"{k} = ?" for k in columns)
    conn.execute(f"UPDATE watched_page SET {names} WHERE company_id = ?", (*columns.values(), company_id))


def _unreadable_key(company_id: int, url: str) -> str:
    return f"careers-unreadable:{company_id}:{hashlib.sha256(url.encode('utf-8')).hexdigest()[:8]}"


def _flag_unreadable(conn: sqlite3.Connection, company_id: int, url: str, why: str, now: str) -> None:
    """Ruling 7: one `flagged` item per evidence key, so a dismissed item stays dismissed. An item the crawler
    itself cleared (the page read again) reopens, because the evidence key is the same for the page's next failure."""
    key = _unreadable_key(company_id, url)
    reason = f"careers page unreadable: {url} ({why})"
    conn.execute(
        "INSERT INTO review_item(kind, company_id, reason, evidence_key, raised_by, created_at) "
        "VALUES ('flagged', ?, ?, ?, 'crawler', ?) ON CONFLICT DO NOTHING", (company_id, reason, key, now))
    conn.execute(
        "UPDATE review_item SET state = 'open', resolution = NULL, resolved_at = NULL, reason = ?, "
        "revision = revision + 1 WHERE kind = 'flagged' AND company_id = ? AND evidence_key = ? "
        "AND state = 'resolved' AND resolution = 'cleared'", (reason, company_id, key))


def _clear_unreadable(conn: sqlite3.Connection, company_id: int, url: str, now: str) -> None:
    """The page read: its open "unreadable" item no longer holds."""
    conn.execute(
        "UPDATE review_item SET state = 'resolved', resolution = 'cleared', resolved_at = ?, revision = revision + 1 "
        "WHERE kind = 'flagged' AND company_id = ? AND evidence_key = ? AND state = 'open'",
        (now, company_id, _unreadable_key(company_id, url)))
