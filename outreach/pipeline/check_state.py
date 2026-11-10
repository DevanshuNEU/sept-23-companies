"""Check now (#194, design 4.3): what a check is doing, read from the store.

The API records a request as an `event` (entity `source`, kind `check_now_requested`, actor `devanshu`). The crawl
job, which runs the check, keeps its progress in one `pipeline_meta` row (`PROGRESS_KEY`) as JSON
`{run_id, checked, total, new, started_at, finished_at, paused_hosts}`: written when the check starts, updated as it
goes, given a `finished_at` when it ends. Reads only. Times are canonical UTC text, so they compare as text.

A run that never wrote its end (the crawler died) is stale once its `started_at` is older than the crawl config's
`check_budget_s` plus `STALE_GRACE_S`: it reads as idle, so a new click can start a check.
"""
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from outreach.crawl.config import load_crawl_config
from outreach.store.timefmt import ISO_FMT, utcnow

PROGRESS_KEY = "check_progress"
REQUEST_KIND = "check_now_requested"
STALE_GRACE_S = 300  # a run with no end is stale once it is older than `check_budget_s` plus this


@dataclass(frozen=True)
class CheckStatus:
    state: str  # idle, queued, running or done
    checked: int = 0
    total: int = 0
    new: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    paused_hosts: list[str] = field(default_factory=list)


def progress(conn: sqlite3.Connection) -> dict | None:
    """The crawler's progress row, or None when no check ever started (or the row is not readable JSON)."""
    row = conn.execute("SELECT value FROM pipeline_meta WHERE key = ?", (PROGRESS_KEY,)).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row[0])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _stale(last: dict, now: str, budget_s: int) -> bool:
    """A run with no end whose start is more than `budget_s + STALE_GRACE_S` seconds before `now`."""
    started = last.get("started_at")
    if last.get("finished_at") is not None or not isinstance(started, str):
        return False
    try:
        begun = datetime.strptime(started, ISO_FMT).replace(tzinfo=timezone.utc)
        at = datetime.strptime(now, ISO_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return at - begun > timedelta(seconds=budget_s + STALE_GRACE_S)


def pending_request(conn: sqlite3.Connection) -> str | None:
    """The newest request time that is newer than the last check's end (or, for a run with no end, its start), else
    None: a run has taken the requests made before it began."""
    last = progress(conn)
    finished = (last.get("finished_at") or last.get("started_at")) if last else None
    row = conn.execute("SELECT max(at) FROM event WHERE entity = 'source' AND kind = ? AND (? IS NULL OR at > ?)",
                       (REQUEST_KIND, finished, finished)).fetchone()
    return row[0]


def status(conn: sqlite3.Connection, now: str | None = None, budget_s: int | None = None) -> CheckStatus:
    """Running while the progress row has no `finished_at`, unless it is stale (then it counts for nothing, so a new
    click can start a check); else queued when a request waits; else done when a check finished; else idle. `now`
    defaults to the clock and `budget_s` to the crawl config's `check_budget_s`."""
    last = progress(conn)
    if last is not None and last.get("finished_at") is None:
        budget = load_crawl_config().check_budget_s if budget_s is None else budget_s
        if _stale(last, now or utcnow(), budget):
            return CheckStatus(state="queued" if pending_request(conn) is not None else "idle")
        return CheckStatus(state="running", **_numbers(last))
    if pending_request(conn) is not None:
        return CheckStatus(state="queued")  # the numbers of an older check are not this check's
    return CheckStatus(state="done", **_numbers(last)) if last is not None else CheckStatus(state="idle")


def _numbers(last: dict) -> dict:
    return dict(checked=int(last.get("checked") or 0), total=int(last.get("total") or 0),
                new=int(last.get("new") or 0), started_at=last.get("started_at"),
                finished_at=last.get("finished_at"), paused_hosts=[str(h) for h in last.get("paused_hosts") or []])
