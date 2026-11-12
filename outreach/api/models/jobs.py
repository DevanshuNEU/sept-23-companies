"""The job list (#156): `JobsView` for `GET /api/jobs` and the answers of Pass, Done and its undo, and Check now.

A row is the facts he checks, never a score and never a reason for its place: the list sorts by when each job was
posted (`list_at`).
"""
from typing import Literal

from outreach.api.models.base import Wire, WireRequest

JobView = Literal["your_level", "higher", "other"]  # other: every listed role that is not software (#250)
JobWindow = Literal["1h", "6h", "24h", "7", "30", "60", "90", "any"]
CheckNowState = Literal["started", "running", "not_live"]
CheckState = Literal["idle", "queued", "running", "done"]
JobSize = Literal["any", "small", "big", "unknown"]


class JobRowWire(Wire):
    posting_id: int
    title: str
    url: str
    location: str
    level_tag: str  # "new grad", "early career", "SDE 1", "SDE 2" or "level not stated"
    list_at: str | None  # the time the list sorts by (UTC); null: Date unknown, listed after every dated row
    posted_at: str | None  # the board's own posting time, when it gives one Knock trusts
    first_seen_at: str  # when Knock first saw it
    seen_gap_s: int | None  # seconds since the board's last good read when Knock first saw it
    first_read: bool  # found on the board's first read (no seen gap)
    reposted_from: str | None  # a repost: when Knock first saw the posting it repeats
    company_id: int
    company_name: str
    size: int | None
    big: bool
    says_no_sponsorship: bool
    everify: bool
    yc: bool
    contacted_on: str | None  # YYYY-MM-DD, the newest day he reached it: a person contacted, or his own Done mark


class JobCounts(Wire):
    your_level: int
    higher: int
    other: int


class JobsView(Wire):
    as_of: str
    rows: list[JobRowWire]
    total: int
    next_after: str | None
    counts: JobCounts


class JobWriteBody(WireRequest):
    """The body of Pass, Applied, Done, Done's undo and Check now: nothing. The page's `Idempotency-Key` header is the
    write's key."""


class PassJobResult(Wire):
    posting_id: int
    company_id: int
    passed: bool  # always true: the posting is off the list, its company is not touched


class DoneResult(Wire):
    posting_id: int
    company_id: int  # the company marked: the survivor of any merge
    reached_on: str  # YYYY-MM-DD in New York, the day of the standing mark (the first one when it was already marked)
    text: str


class AppliedResult(Wire):
    posting_id: int
    company_id: int  # the survivor of any merge
    applied_on: str  # YYYY-MM-DD in New York: today, or the day of the standing application
    text: str


class ClearDoneResult(Wire):
    company_id: int  # the survivor of any merge
    cleared: bool  # false: there was no mark to take back, nothing written


class CheckNowResult(Wire):
    requested_at: str  # when the request was recorded; the one already waiting or running when state is running
    state: CheckNowState  # started: recorded and the crawl job asked to run; running: a check is already queued or
    # running, nothing recorded; not_live: recorded, but this is not the live checkout, so nothing was started


class CheckStatus(Wire):
    state: CheckState
    checked: int
    total: int
    new: int
    started_at: str | None
    finished_at: str | None
    paused_hosts: list[str]  # hosts the check stopped reading because they said to slow down
