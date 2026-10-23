"""Progress, findings and wins: the models named as #33's seam block in `web/src/api/types.ts`.

Every field the page shows is derived here (ruling 6): `enough`, `quiet`, the win line, the finding text and its
caution. A funnel row carries its denominator (`from`), never a rate: `base` and the stage counts are counts, and
the page decides whether a rate is honest to show.
"""
from typing import Literal

from pydantic import Field

from outreach.api.models.base import Wire, WireRequest
from outreach.api.models.company import CompanyRef, PersonRef
from outreach.api.models.outcomes import StoredReplyKind

FunnelKey = Literal["requests", "accepts", "replies", "referrals", "interviews"]
WinKind = Literal["accepted", "replied", "referred", "interview", "offer"]


class FunnelFrom(Wire):
    stage: FunnelKey
    count: int


class FunnelStage(Wire):
    stage: FunnelKey
    label: str
    count: int
    from_: FunnelFrom | None = Field(alias="from")  # what this stage is counted against; null for requests


class EffortLine(Wire):
    label: str
    count: int


class ProgressPeriod(Wire):
    label: str
    from_: str = Field(alias="from")  # first New York day, inclusive
    to: str                           # last New York day, inclusive
    stages: list[FunnelStage]
    effort: list[EffortLine]
    enough: bool
    quiet: bool


class BaseRates(Wire):
    since: str
    requests: int
    accepts: int
    replies: int
    median_days_to_reply: float | None
    enough: bool
    min_requests: int


class TodayProgress(Wire):
    planned: int
    done: int
    requests: int
    applications: int
    accepted: int
    replied: int


class ProgressView(Wire):
    as_of: str
    today: TodayProgress
    week: ProgressPeriod
    month: ProgressPeriod
    base: BaseRates


class FindingCount(Wire):
    label: str
    count: int  # replies in the group
    of: int     # touches in the group


class Finding(Wire):
    id: int
    text: str
    counts: list[FindingCount]
    caution: str | None
    changed: str | None


class FindingChange(Wire):
    at: str
    text: str


class FindingsView(Wire):
    as_of: str
    month: str                  # the first day of the month the report is for
    reported_on: str | None     # the New York date of the newest touch or outcome the findings read; null without findings
    findings: list[Finding]
    changes: list[FindingChange]


class Win(Wire):
    win_id: int
    kind: WinKind
    at: str
    touch_id: int
    person: PersonRef | None
    company: CompanyRef
    line: str
    sent_note: str | None
    role: str | None
    reply_kind: StoredReplyKind | None
    unseen: bool


class WinCounts(Wire):
    accepted: int
    replied: int
    referred: int
    interview: int
    offer: int


class WinsView(Wire):
    as_of: str
    wins: list[Win]
    unseen: int
    counts: WinCounts
    since: str


class WinsSeenBody(WireRequest):
    win_ids: list[int] = Field(max_length=500)


class WinsSeen(Wire):
    marked: int  # win ids newly marked; ids already seen do not count
