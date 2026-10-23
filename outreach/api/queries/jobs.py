"""Job runs: the last run of each job, the read queue, boards and watched pages, and when a job should run next.

Pure reads: every function takes a connection (inside the caller's snapshot) and `now` as canonical UTC text. Nothing
here reads the clock.
"""
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from outreach.api.models.health import (
    HealthBoards,
    HealthStore,
    HealthWatchedPages,
    JobCount,
    JobRunSummary,
    ReadQueueStatus,
)
from outreach.store.timefmt import ISO_FMT

ET = ZoneInfo("America/New_York")
HOUR = 3600
DAILY_GRACE_S = 2 * HOUR
JOB_DETAIL_LIMIT = 20


@dataclass(frozen=True)
class Every:
    """A job that runs on an interval, in seconds."""

    seconds: int


@dataclass(frozen=True)
class At:
    """A job that runs at one New York wall-clock time, every day or on weekdays only (#15's launchd templates)."""

    hour: int
    minute: int
    weekdays_only: bool = False


# The cadences. A job name in the `job_run.job` CHECK is either here or in UNSCHEDULED (a test fails until a new
# name is placed in one of them).
SCHEDULED: dict[str, Every | At] = {
    "sender": Every(600),
    "read_boards": Every(1800),
    "watch_pages": Every(1800),
    "read_postings": At(2, 30),
    "plan": At(8, 0, weekdays_only=True),
    "prepare": At(5, 30, weekdays_only=True),
}
# Run by hand or inside another job (`rank` runs inside `plan`): no expected time, never late.
UNSCHEDULED = frozenset({
    "install", "rebuild", "crawl", "daily_list", "import", "discover", "resolve", "find_boards", "rank",
})


def _parse(text: str) -> datetime:
    return datetime.strptime(text, ISO_FMT).replace(tzinfo=timezone.utc)


def _fmt(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(ISO_FMT)


def expected_next_at(job: str, last_started_at: str | None, now: str) -> str | None:
    """When `job` should next start, from when it last started.

    An interval job counts from its last start (None when it never ran). A daily or weekday job takes the first slot
    after its last start (after `now` when it never ran), at the same New York wall-clock time on each date, so the
    time holds across a daylight saving change. Unscheduled jobs have none.
    """
    cadence = SCHEDULED.get(job)
    if cadence is None:
        return None
    if isinstance(cadence, Every):
        if last_started_at is None:
            return None
        return _fmt(_parse(last_started_at) + timedelta(seconds=cadence.seconds))
    after = _parse(last_started_at or now)
    day = after.astimezone(ET).date()
    for _ in range(9):  # a weekday job skips at most a weekend
        slot = datetime.combine(day, time(cadence.hour, cadence.minute), tzinfo=ET)
        if slot.astimezone(timezone.utc) > after and not (cadence.weekdays_only and day.weekday() >= 5):
            return _fmt(slot)
        day += timedelta(days=1)
    raise AssertionError("no slot within nine days")  # unreachable


def grace_seconds(job: str) -> int:
    cadence = SCHEDULED.get(job)
    if isinstance(cadence, Every):
        return max(2 * cadence.seconds, HOUR)
    return DAILY_GRACE_S


def is_late(job: str, expected: str | None, now: str) -> bool:
    """True when `now` is past `expected` by more than the job's grace (twice its interval, at least an hour; two
    hours for a daily job). Unscheduled jobs are never late."""
    if expected is None or job not in SCHEDULED:
        return False
    return _parse(now) > _parse(expected) + timedelta(seconds=grace_seconds(job))


def humanize(key: str) -> str:
    """`boards_read` becomes `Boards read`."""
    words = key.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def counts_of(summary: str) -> list[JobCount]:
    """`job_run.summary` keys with whole-number values, in stored order. Anything else is dropped."""
    try:
        data = json.loads(summary)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    return [
        JobCount(label=humanize(key), value=value)
        for key, value in data.items()
        if isinstance(value, int) and not isinstance(value, bool)
    ]


def run_summary(row: sqlite3.Row, now: str) -> JobRunSummary:
    expected = expected_next_at(row["job"], row["started_at"], now)
    return JobRunSummary(
        job=row["job"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        status=row["status"],
        error=row["error"],
        counts=counts_of(row["summary"]),
        expected_next_at=expected,
        late=is_late(row["job"], expected, now),
    )


def last_runs(conn: sqlite3.Connection, now: str) -> list[JobRunSummary]:
    """The newest run of each job that has run, newest first."""
    rows = conn.execute(
        "SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY job ORDER BY started_at DESC, run_id DESC) AS n"
        " FROM job_run) WHERE n = 1 ORDER BY started_at DESC, run_id DESC"
    ).fetchall()
    return [run_summary(row, now) for row in rows]


def runs_of(conn: sqlite3.Connection, job: str, now: str, limit: int = JOB_DETAIL_LIMIT) -> list[JobRunSummary]:
    """The last `limit` runs of one job, newest first. Only the newest carries `expectedNextAt` and `late`."""
    rows = conn.execute(
        "SELECT * FROM job_run WHERE job = ? ORDER BY started_at DESC, run_id DESC LIMIT ?", (job, limit)
    ).fetchall()
    summaries = [run_summary(row, now) for row in rows]
    for older in summaries[1:]:
        older.expected_next_at, older.late = None, False
    return summaries


def read_queue_status(conn: sqlite3.Connection, now: str) -> ReadQueueStatus:
    """Rows by state. A `leased` row whose lease ran out counts as `leaseExpired`, not `leased`, so the two never
    overlap. `lastNight` is the newest `read_postings` run."""
    row = conn.execute(
        "SELECT"
        " COALESCE(SUM(state = 'queued'), 0) AS queued,"
        " COALESCE(SUM(state = 'leased' AND lease_until >= ?1), 0) AS leased,"
        " COALESCE(SUM(state = 'leased' AND lease_until < ?1), 0) AS lease_expired,"
        " COALESCE(SUM(state = 'done'), 0) AS done,"
        " COALESCE(SUM(state = 'refused'), 0) AS refused,"
        " MIN(CASE WHEN state = 'queued' THEN enqueued_at END) AS oldest"
        " FROM read_queue",
        (now,),
    ).fetchone()
    age = None
    if row["oldest"] is not None:
        age = round(max((_parse(now) - _parse(row["oldest"])).total_seconds(), 0) / HOUR, 1)
    night = conn.execute(
        "SELECT * FROM job_run WHERE job = 'read_postings' ORDER BY started_at DESC, run_id DESC LIMIT 1"
    ).fetchone()
    return ReadQueueStatus(
        queued=row["queued"], leased=row["leased"], lease_expired=row["lease_expired"], done=row["done"],
        refused=row["refused"], oldest_queued_age_hours=age,
        last_night=run_summary(night, now) if night is not None else None,
    )


def store_info(conn: sqlite3.Connection) -> HealthStore:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    ruleset = conn.execute("SELECT ruleset_version FROM ruleset WHERE is_current = 1").fetchone()
    return HealthStore(version=version, ruleset_version=ruleset[0] if ruleset else None)


def board_counts(conn: sqlite3.Connection, now: str) -> HealthBoards:
    """Active boards, those due now (never scheduled, or `next_read_at` reached) and those whose last read failed."""
    row = conn.execute(
        "SELECT COUNT(*) AS active,"
        " COALESCE(SUM(next_read_at IS NULL OR next_read_at <= ?), 0) AS due_now,"
        " COALESCE(SUM(last_read = 'error'), 0) AS erroring"
        " FROM board WHERE active = 1",
        (now,),
    ).fetchone()
    return HealthBoards(active=row["active"], due_now=row["due_now"], erroring=row["erroring"])


def watched_page_counts(conn: sqlite3.Connection, now: str) -> HealthWatchedPages:
    since = _fmt(_parse(now) - timedelta(days=7))
    row = conn.execute(
        "SELECT COUNT(*) AS total, COALESCE(SUM(last_change_at >= ?), 0) AS changed FROM watched_page", (since,)
    ).fetchone()
    return HealthWatchedPages(total=row["total"], changed_last_7_days=row["changed"])
