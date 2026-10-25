"""Today: `TodayView`, `DailyEntry` and `StillEmailing` as in `web/src/api/types.ts`, and the write bodies.

This is the lean Today. #15's daily plan replaces its list and keeps these shapes.
"""
from typing import Literal

from pydantic import Field

from outreach.api.models.base import Wire, WireRequest
from outreach.api.models.company import CompanyRef, Fact, RoleToName, SponsorshipShown
from outreach.api.models.health import JobRunSummary

ListGroup = Literal["level_role", "intern_junior", "any_us_role", "reason_no_role"]
EntryState = Literal["proposed", "picked", "backlog", "removed"]
RemoveReason = Literal["not_a_fit", "already_in_touch", "bigger_than_it_looks", "other"]
NOTE_MAX = 2000


class DailyCompany(CompanyRef):
    domain: str | None
    hq: str


class OtherRole(Wire):
    title: str
    first_seen_at: str


class DailyEntry(Wire):
    company: DailyCompany
    list_group: ListGroup
    rank: int
    state: EntryState
    revision: int  # the company's revision: a write on this row checks it
    role: RoleToName | None
    size: Fact | None
    stage: Fact | None
    sponsorship: SponsorshipShown
    sponsorship_fact: Fact | None
    domain_fact: Fact | None
    mail_server_ok: bool | None
    why: str
    other_roles: list[OtherRole]
    people_count: int
    draft_count: int
    first_proposed_on: str


class NextStep(Wire):
    kind: Literal["review_drafts", "fix_review", "answer_reply", "nothing", "paused"]
    text: str


class StillEmailing(Wire):
    company: CompanyRef
    day: int
    days: int
    emailed: int
    budget: int
    next_step: NextStep
    state: Literal["emailing", "held", "paused", "waiting"]


class NotReady(Wire):
    reason: Literal["weekend", "not_built", "job_failed"]
    text: str


class TodayView(Wire):
    as_of: str
    date: str
    built_at: str | None
    capacity: int
    picked: int
    not_ready: NotReady | None
    proposed: list[DailyEntry]  # proposed and picked rows, best rank first
    backlog: list[DailyEntry]
    still_emailing: list[StillEmailing]
    list_job: JobRunSummary | None


class RemoveCompanyBody(WireRequest):
    reason: RemoveReason
    note: str = Field(default="", max_length=NOTE_MAX)
    revision: int = Field(ge=1)


class RestoreCompanyBody(WireRequest):
    reason: str = Field(max_length=500)
    revision: int = Field(ge=1)
