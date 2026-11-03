"""The job list (#156): `JobsView` for `GET /api/jobs`.

A row is the facts he checks, never a score and never a reason for its place: the list sorts by date.
"""
from typing import Literal

from outreach.api.models.base import Wire

JobView = Literal["your_level", "higher"]
JobWindow = Literal["7", "30", "60", "90", "any"]
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
