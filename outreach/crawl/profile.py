"""Where the tick time goes (#152, Task 2): the stored `by_ats` summaries of the newest `read_boards` runs, summed.

Store only, nothing sent. An ATS's seconds are its list calls, its bodies (fetch and store) and its ingest, which the
tick keeps apart: `ingest_ms` already leaves `body_ms` out, so the three add up without counting a body twice.
`share` is the ATS's seconds over the summed `seconds` of the used runs (each summary's `seconds` is the whole tick).
A run whose summary has no `by_ats` (from before #152) is skipped and does not count toward `runs`.

When the tick lists on worker threads (#158), an ATS's list time is thread time: each worker's list calls, spacing
waits included, added up across the workers, not the time that passed. So the ATS rows can add up to more than the
runs' `seconds`, which is always the time that passed, and `share` can then go past 100%.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from outreach.crawl.tick import _PROFILE_KEYS

__all__ = ["AtsProfile", "Profile", "profile"]

_SUMMED = _PROFILE_KEYS


@dataclass(frozen=True)
class AtsProfile:
    ats: str
    boards: int
    seconds: float
    seconds_per_board: float
    share: float
    list_requests: int
    body_requests: int
    new: int
    closed: int
    reopened: int


@dataclass(frozen=True)
class Profile:
    runs: int
    seconds: float
    rows: list[AtsProfile] = field(default_factory=list)


def _summary(text: str) -> dict:
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def profile(conn: sqlite3.Connection, runs: int) -> Profile:
    """Sum `by_ats` over the newest `runs` finished `read_boards` runs that have one; rows sorted by seconds, longest
    first (ties by name)."""
    totals: dict[str, dict[str, int]] = {}
    used = 0
    seconds = 0.0
    cursor = conn.execute("SELECT summary FROM job_run WHERE job = 'read_boards' AND status != 'running' "
                          "AND finished_at IS NOT NULL ORDER BY run_id DESC")
    for (text,) in cursor:
        if used >= runs:
            break
        summary = _summary(text)
        by_ats = summary.get("by_ats")
        if not isinstance(by_ats, dict) or not by_ats:
            continue
        used += 1
        seconds += float(summary.get("seconds") or 0)
        for ats, row in by_ats.items():
            into = totals.setdefault(ats, dict.fromkeys(_SUMMED, 0))
            for key in _SUMMED:
                into[key] += int(row.get(key) or 0)
    rows = []
    for ats, t in totals.items():
        own = (t["list_ms"] + t["ingest_ms"] + t["body_ms"]) / 1000
        rows.append(AtsProfile(
            ats=ats, boards=t["boards"], seconds=round(own, 3),
            seconds_per_board=round(own / t["boards"], 3) if t["boards"] else 0.0,
            share=own / seconds if seconds else 0.0, list_requests=t["list_requests"],
            body_requests=t["body_requests"], new=t["new"], closed=t["closed"], reopened=t["reopened"]))
    rows.sort(key=lambda r: (-r.seconds, r.ats))
    return Profile(runs=used, seconds=round(seconds, 3), rows=rows)
