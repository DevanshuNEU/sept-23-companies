"""The time a posting row sorts by in the job list, and how a repost is found (#193, design 4.4).

A row's list time is the board's own posting time, never later than Knock's first sighting. A board that gives only a
date (and whose date is honest) gives that date at 00:00 New York. A board that gives no time it can be trusted on
gives Knock's first sighting, but only when the board had been read before; on a board's first read such a row has no
list time and sits in the "Date unknown" group.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Collection, Sequence
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from outreach.crawl.cadence import add_seconds
from outreach.ranking.common import canonical_ids
from outreach.store.timefmt import ISO_FMT

__all__ = ["REPOST_DAYS", "GAP_TAG_S", "DATE_TRUSTED", "list_at", "repost_key", "company_family", "find_repost"]

REPOST_DAYS = 120  # a posting with the title of one closed this recently is a repost (the window #155 planned)
GAP_TAG_S = 7200  # the "found after a gap" tag shows when the board had not been read for longer than this
# The ATSs whose date-only `posted_on` is honest. Greenhouse and Ashby give `posted_at`; Lever's date comes from
# `createdAt`, which runs days to a year early, so it is not trusted. Rippling, BambooHR and JazzHR list no date
# today; they are here in case they start to.
DATE_TRUSTED = frozenset({"workable", "workday", "oracle_orc", "breezy", "teamtailor", "recruitee", "rippling",
                          "bamboohr", "jazzhr", "smartrecruiters", "pinpoint", "amazon"})

_NY = ZoneInfo("America/New_York")
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")


def list_at(ats: str, first_seen_at: str, posted_at: str | None, posted_on: str | None,
            seen_gap_s: int | None) -> str | None:
    """The row's list time (canonical UTC), or None when it is not known.

    `posted_at` when set; else `posted_on` at 00:00 New York for an ATS in `DATE_TRUSTED`; either one capped at
    `first_seen_at`. Else `first_seen_at` when the board had a good read before (`seen_gap_s` is not None), else None.
    A `posted_on` too extreme to turn into a time (an OverflowError) counts as no date.
    """
    if posted_at:
        return min(posted_at, first_seen_at)
    if posted_on and ats in DATE_TRUSTED:
        midnight = datetime.combine(date.fromisoformat(posted_on), time(0, 0), tzinfo=_NY)
        try:
            return min(midnight.astimezone(timezone.utc).strftime(ISO_FMT), first_seen_at)
        except OverflowError:
            pass
    if seen_gap_s is not None:
        return first_seen_at
    return None


def repost_key(title: str) -> str:
    """The title as a repost compares it: lower case, every run of other characters one space, trimmed."""
    return _NOT_ALNUM.sub(" ", title.lower()).strip()


def company_family(conn: sqlite3.Connection, company_id: int) -> list[int]:
    """The company and every company merged with it (the same end of the `merged_into` chain), sorted. It reads the
    whole company table, so a caller that looks up many reposts at one company computes it once."""
    canon = canonical_ids(conn)
    end = canon.get(company_id, company_id)
    return sorted({company_id, *(cid for cid, target in canon.items() if target == end)})


def find_repost(conn: sqlite3.Connection, company_id: int, title: str, now: str, *,
                family: Sequence[int] | None = None, closing: Collection[int] = ()) -> int | None:
    """The newest posting at the company (or a company merged with it) with the same `repost_key` that closed within
    `REPOST_DAYS` of `now`, or None. An open posting with the same title is not a repost of anything, except one in
    `closing`: the postings the same read is about to close, so a job closed and reposted in one read still counts.
    `family` is `company_family(conn, company_id)` when the caller already has it."""
    family = company_family(conn, company_id) if family is None else family
    key = repost_key(title)
    rows = conn.execute(
        "SELECT posting_id, title FROM posting WHERE company_id IN (SELECT value FROM json_each(?)) "
        "AND ((closed_at IS NOT NULL AND closed_at >= ?) OR posting_id IN (SELECT value FROM json_each(?))) "
        "ORDER BY first_seen_at DESC, posting_id DESC",
        (json.dumps(list(family)), add_seconds(now, -REPOST_DAYS * 86400), json.dumps(sorted(closing))))
    for posting_id, other in rows:
        if repost_key(other) == key:
            return posting_id
    return None
