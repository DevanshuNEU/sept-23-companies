"""The Done view: `DoneView` and `DoneCompany` as in `web/src/api/types.ts`."""
from typing import Literal

from outreach.api.models.base import Wire
from outreach.api.models.company import CompanyRef


class DoneCompany(Wire):
    company: CompanyRef
    outcome: Literal["interview", "referral"]
    outcome_label: str
    closed_at: str
    how_started: str
    imported: bool
    role: str | None
    note: str


class WeekReplies(Wire):
    week_of: str  # the Monday, a New York date
    count: int


class ReplyRate(Wire):
    """Of the emails first sent this month, how many have a reply (a cohort, so the rate is never above 1)."""

    replies: int
    first_emails: int


class DoneMetrics(Wire):
    replies_this_month: int
    replies_per_week: list[WeekReplies]  # the last 8 New York weeks, oldest first, the current week last
    reply_rate: ReplyRate
    referrals: int  # companies closed with a referral
    interviews: int  # companies closed with an interview


class DoneView(Wire):
    as_of: str
    companies: list[DoneCompany]
    metrics: DoneMetrics
