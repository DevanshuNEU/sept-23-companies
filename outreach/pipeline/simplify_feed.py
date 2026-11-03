"""The SimplifyJobs new grad feed, polled once per crawl tick to measure Knock against it (#153).

Knock keeps each listing's link, company, title, posted hint and first sight in `feed_listing`, and nothing else. A
listing is never shown as a job and never gives a posting a date: `posted_hint` is only for the scorecard to read.

The poll is one conditional GET (`If-None-Match` when an ETag is stored) through the client the tick hands in, so it
inherits the polite spacing and the breaker. A 304 writes nothing. A changed file is written in one unit: every kept
listing is upserted (`first_seen_at` only on insert, `last_seen_at` and `active = 1` every time) and every stored
listing the file no longer keeps goes inactive. The rows in the file when the poll first ran carry `backfill = 1`:
they have no honest first-seen time.

Kept: `active` and `is_visible`, a configured category, and a US location (`is_us_location`). The request has its
own response cap, `MAX_BYTES`, because the file grows past the crawl client's.
"""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from outreach.net import HttpClient
from outreach.pipeline.config import PipelineConfig, load_pipeline_config
from outreach.pipeline.urls import board_key
from outreach.store import atomic
from outreach.store.timefmt import ISO_FMT

__all__ = ["ETAG_KEY", "FAIL_STREAK_KEY", "FeedError", "FeedPoll", "MAX_BYTES", "SOURCE", "is_us_location", "note_streak", "poll"]

SOURCE = "simplify"
ETAG_KEY = "simplify_etag"
FAIL_STREAK_KEY = "simplify_fail_streak"
BATCH = 500
# The feed's own response cap (#153): the file is 13 MB and growing, the crawl client's cap is 20 MB.
MAX_BYTES = 60_000_000

_STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH "
           "OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
_STATE_NAMES = (
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware", "Florida",
    "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland",
    "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma",
    "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont",
    "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming", "District of Columbia")
# Places that make a US-looking string not US: "Tel Aviv, IL" ends in Illinois's code, "Remote in Mexico" is not "New
# Mexico". A small explicit list, as ruled; the Israeli cities stand in for the country because "IL" is the one code a
# feed uses for them without naming Israel.
_NON_US = ("Israel", "Tel Aviv", "Tel-Aviv", "Jerusalem", "Haifa", "Canada", "India", "UK", "United Kingdom", "Germany",
           "France", "Mexico", "Brazil", "Ireland", "Netherlands", "Singapore", "Japan", "China", "Australia", "Poland",
           "Spain")
# Capitals only for the short forms: "us", "sf" and "la" are words or noise in any other case.
_US_INITIALS = re.compile(r"\b(?:US|USA)\b")
_US_NAME = re.compile(r"\bUnited States\b", re.IGNORECASE)
_STATE_END = re.compile(r",\s*(?:%s)$" % "|".join(_STATES))
_STATE_NAME = re.compile(r"\b(?:%s)\b" % "|".join(_STATE_NAMES), re.IGNORECASE)
# "UK" is matched in capitals only, the rest in any case.
_NON_US_PLACE = re.compile(r"\b(?:UK|(?i:%s))\b" % "|".join(re.escape(p) for p in _NON_US if p != "UK"))
_HUB = re.compile(r"\b(?:San Francisco|Bay Area|Los Angeles|Seattle|Boston|Austin|Chicago|Denver|Washington,? D\.?C\.?"
                  r"|New York)\b", re.IGNORECASE)
_HUB_INITIALS = re.compile(r"\b(?:SF|NYC|LA)\b")

_UPSERT = (
    "INSERT INTO feed_listing (source, listing_id, url, ats, token, native_id, company_name, title, posted_hint, "
    "first_seen_at, last_seen_at, active, backfill) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?) "
    "ON CONFLICT (source, listing_id) DO UPDATE SET url = excluded.url, ats = excluded.ats, token = excluded.token, "
    "native_id = excluded.native_id, company_name = excluded.company_name, title = excluded.title, "
    "posted_hint = excluded.posted_hint, last_seen_at = excluded.last_seen_at, active = 1")


class FeedError(ValueError):
    """The feed answered, but not with a listing file."""


@dataclass(frozen=True)
class FeedPoll:
    """`changed` is False on a 304. `seen` counts the kept listings in the file, `new` those not stored before and
    `closed` the stored active listings the file no longer keeps."""
    changed: bool
    seen: int
    new: int
    closed: int


def is_us_location(location: str) -> bool:
    """True for a location in the US. In order:
    1. it names the US, USA or United States (capitals for the first two): yes, whatever else it names;
    2. it names a place in `_NON_US` ("Toronto, ON, Canada", "Tel Aviv, IL", "Remote in Mexico"): no;
    3. it is exactly "Remote", ends in ", XX" for a US state code or DC, names a state in full, or names one of the
       hubs (San Francisco, SF, Bay Area, New York, NYC, Los Angeles, LA, Seattle, Boston, Austin, Chicago, Denver,
       Washington DC): yes.
    "New Mexico" is not read as Mexico."""
    text = location.strip()
    if _US_INITIALS.search(text) or _US_NAME.search(text):
        return True
    if _NON_US_PLACE.search(text.replace("New Mexico", "")):
        return False
    return bool(text.lower() == "remote" or _STATE_END.search(text) or _STATE_NAME.search(text)
                or _HUB.search(text) or _HUB_INITIALS.search(text))


def _kept(row: object, categories: frozenset[str]) -> bool:
    if not isinstance(row, Mapping):
        return False
    if row.get("active") is not True or row.get("is_visible") is not True or row.get("category") not in categories:
        return False
    locations = row.get("locations")
    return isinstance(locations, list) and any(isinstance(loc, str) and is_us_location(loc) for loc in locations)


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _posted_hint(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc).strftime(ISO_FMT)
    except (OverflowError, OSError, ValueError):
        return None


def _values(row: Mapping, now: str, backfill: int) -> tuple | None:
    """The upsert parameters for one kept listing, None when it lacks an id, link, company or title."""
    listing_id, url = _text(row.get("id")), _text(row.get("url"))
    company, title = _text(row.get("company_name")), _text(row.get("title"))
    if not (listing_id and url and company and title):
        return None
    key = board_key(url)
    ats, token, native_id = (key.ats, key.token, key.native_id) if key else (None, None, None)
    return (SOURCE, listing_id, url, ats, token, native_id, company, title, _posted_hint(row.get("date_posted")),
            now, now, backfill)


def _chunks(items: Sequence, size: int = BATCH) -> Iterator[Sequence]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM pipeline_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def poll(conn: sqlite3.Connection, client: HttpClient, now: str, *, cfg: PipelineConfig | None = None) -> FeedPoll:
    """One conditional GET of the feed, then the write described in the module docstring. An error status, a body that
    is not a JSON list, or a failed request raises and writes nothing; the tick turns that into `feed_error`."""
    simplify = (cfg or load_pipeline_config()).simplify
    etag = _meta(conn, ETAG_KEY)
    response = client.get(simplify.url, {"If-None-Match": etag} if etag else None, max_bytes=MAX_BYTES)
    if response.status == 304:
        return FeedPoll(False, 0, 0, 0)
    if response.status != 200:
        raise FeedError(f"the SimplifyJobs feed answered HTTP {response.status}")
    listings = response.json()
    if not isinstance(listings, list) or not listings:  # an empty list is a broken answer: it would close every row
        raise FeedError("the SimplifyJobs feed is not a list of listings")

    categories = frozenset(simplify.categories)
    first_poll = conn.execute("SELECT 1 FROM feed_listing WHERE source = ? LIMIT 1", (SOURCE,)).fetchone() is None
    kept: dict[str, tuple] = {}
    for row in listings:
        if _kept(row, categories):
            values = _values(row, now, 1 if first_poll else 0)
            if values is not None:
                kept[values[1]] = values
    new_etag = response.headers.get("etag")

    with atomic(conn, "simplify_feed"):
        stored = {r[0]: r[1] for r in conn.execute("SELECT listing_id, active FROM feed_listing WHERE source = ?",
                                                   (SOURCE,))}
        for batch in _chunks(list(kept.values())):
            conn.executemany(_UPSERT, batch)
        gone = [i for i, active in stored.items() if active and i not in kept]
        for batch in _chunks(gone):
            conn.executemany("UPDATE feed_listing SET active = 0 WHERE source = ? AND listing_id = ?",
                             [(SOURCE, i) for i in batch])
        if new_etag:
            conn.execute("INSERT INTO pipeline_meta (key, value) VALUES (?, ?) "
                         "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (ETAG_KEY, new_etag))
        else:  # a stale tag would ask for a 304 against a file the server no longer vouches for
            conn.execute("DELETE FROM pipeline_meta WHERE key = ?", (ETAG_KEY,))
    return FeedPoll(True, len(kept), sum(1 for i in kept if i not in stored), len(gone))


def note_streak(conn: sqlite3.Connection, *, ok: bool) -> None:
    """The count of polls in a row that failed, in `pipeline_meta`: 0 after a good poll (a 304 is good), one more
    after an error. The tick writes it; the scorecard's alarm reads it."""
    with atomic(conn, "simplify_streak"):
        if ok:
            conn.execute("INSERT INTO pipeline_meta (key, value) VALUES (?, '0') "
                         "ON CONFLICT (key) DO UPDATE SET value = '0'", (FAIL_STREAK_KEY,))
        else:
            conn.execute("INSERT INTO pipeline_meta (key, value) VALUES (?, '1') "
                         "ON CONFLICT (key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)",
                         (FAIL_STREAK_KEY,))
