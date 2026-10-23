"""The health view: what ran, what is queued, and whether the sender is going."""
from typing import Literal

from outreach.api.models.base import Wire

# Every name `job_run.job` allows (a test pins this list to the CHECK).
JobKind = Literal[
    "install", "rebuild", "crawl", "daily_list", "sender", "import", "discover", "resolve", "find_boards",
    "read_boards", "watch_pages", "read_postings", "rank", "plan", "prepare",
]
JobStatus = Literal["running", "ok", "partial", "failed"]
SenderState = Literal["running", "paused_by_you", "paused_hard_block", "outside_window", "weekend", "signed_out"]


class JobCount(Wire):
    label: str
    value: int


class JobRunSummary(Wire):
    job: JobKind
    started_at: str
    finished_at: str | None
    status: JobStatus
    error: str
    counts: list[JobCount]
    expected_next_at: str | None
    late: bool


class ReadQueueStatus(Wire):
    queued: int
    leased: int
    lease_expired: int
    done: int
    refused: int
    oldest_queued_age_hours: float | None
    last_night: JobRunSummary | None


class SenderSummary(Wire):
    state: SenderState
    sent_today: int
    limit_today: int


class HealthStore(Wire):
    version: int
    ruleset_version: str | None


class HealthBoards(Wire):
    active: int
    due_now: int
    erroring: int


class HealthWatchedPages(Wire):
    total: int
    changed_last_7_days: int


class HealthView(Wire):
    as_of: str
    store: HealthStore
    jobs: list[JobRunSummary]
    read_queue: ReadQueueStatus
    boards: HealthBoards
    watched_pages: HealthWatchedPages
    sender: SenderSummary
