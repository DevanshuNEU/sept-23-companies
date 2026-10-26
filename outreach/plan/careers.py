"""Where Devanshu opens a company's careers page from Today's queue (company first, #9 2026-10-26).

`careers_url(ats, token)` is a board's public page for the four boards whose page has one plain shape (Ashby,
Greenhouse, Lever, Workable). `pick_careers_url` is the order the queue row uses: the best posting's own link, then
a board's public page, then the watched careers page, then `https://<domain>`. A value that is not a plain link is
skipped, never shown. Pure: no store, no clock, no network; the app never fetches these pages.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit

__all__ = ["BOARD_PAGES", "careers_url", "pick_careers_url"]

BOARD_PAGES = {
    "ashby": "https://jobs.ashbyhq.com/{}",
    "greenhouse": "https://job-boards.greenhouse.io/{}",
    "lever": "https://jobs.lever.co/{}",
    "workable": "https://apply.workable.com/{}/",
}
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_DOMAIN = re.compile(r"(?=.{1,253}\Z)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")


def careers_url(ats: str | None, token: str | None) -> str | None:
    """The board's public page, or None for another board or a token that is not one plain path segment."""
    page = BOARD_PAGES.get((ats or "").lower())
    if page is None or not isinstance(token, str) or not _TOKEN.fullmatch(token):
        return None
    return page.format(token)


def _web(url: str | None) -> str | None:
    """A plain http(s) link with a host, else None."""
    if not isinstance(url, str) or not url.strip() or re.search(r"\s", url.strip()):
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    return url.strip() if parts.scheme in ("http", "https") and parts.hostname else None


def pick_careers_url(*, posting_url: str | None, boards: Iterable[tuple[str, str]], watched_url: str | None,
                     domain: str | None) -> str | None:
    """The first of: the posting's link, a board's public page (in the order given), the watched careers page,
    `https://<domain>`. None when there is none of them."""
    found = _web(posting_url)
    if found:
        return found
    for ats, token in boards:
        page = careers_url(ats, token)
        if page:
            return page
    found = _web(watched_url)
    if found:
        return found
    host = (domain or "").strip().lower().rstrip(".")
    return f"https://{host}" if _DOMAIN.fullmatch(host) else None
