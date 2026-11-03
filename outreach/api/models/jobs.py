"""The job list (#156): `JobsView` for `GET /api/jobs` and the answers of Pass and Go ahead.

A row is the facts he checks, never a score and never a reason for its place: the list sorts by date.
"""
from typing import Literal

from outreach.api.models.base import Wire, WireRequest

JobView = Literal["your_level", "higher"]
JobWindow = Literal["7", "30", "60", "90", "any"]
GoAheadState = Literal["added", "requested"]
JobSize = Literal["any", "small", "big", "unknown"]


class JobRowWire(Wire):
    posting_id: int
    title: str
    url: str
    location: str
    level_tag: str  # "new grad", "early career", "SDE 1", "SDE 2" or "level not stated"
    date: str  # YYYY-MM-DD
    date_kind: Literal["posted", "seen"]
    age_days: int
    company_id: int
    company_name: str
    size: int | None
    big: bool
    says_no_sponsorship: bool
    everify: bool
    yc: bool
    contacted_on: str | None  # YYYY-MM-DD, the newest day he reached a person there


class JobCounts(Wire):
    your_level: int
    higher: int


class JobsView(Wire):
    as_of: str
    rows: list[JobRowWire]
    total: int
    next_after: str | None
    counts: JobCounts


class JobWriteBody(WireRequest):
    """The body of Pass and Go ahead: nothing. The page's `Idempotency-Key` header is the write's key."""


class PassJobResult(Wire):
    posting_id: int
    company_id: int
    passed: bool  # always true: the posting is off the list, its company is not touched


class GoAheadResult(Wire):
    posting_id: int
    company_id: int
    state: GoAheadState  # added: on today's list now; requested: the 08:00 plan puts it first
    rank: int | None  # the pick's rank on today's list, None until the plan is built
    entry_id: int | None  # the plan row's id (`GET /api/plan`), None until the plan is built
    text: str
