"""The people plan: `PlanView`, `PlanRow`, `PlanStep`, `NoteDraft` and `NoteFact` as #33's seam block names them.

Every derived field (row state, reason and its sources, the search link, tomorrow's line, the note limit) is the
API's; the page computes none of them.
"""
from typing import Literal

from outreach.api.models.base import Wire
from outreach.api.models.checks import Check
from outreach.api.models.company import PersonRef, RoleToName
from outreach.api.models.health import JobRunSummary
from outreach.api.models.outbox import SavedBy
from outreach.api.models.today import DailyCompany, ListGroup, NotReady
from outreach.api.models.watch import SourceLink

StepKind = Literal["request", "apply"]
ReasonState = Literal["proposed", "confirmed", "written"]
RowState = Literal["todo", "done", "skipped"]
PlanChannel = Literal["linkedin_request", "email"]


class NoteFact(Wire):
    text: str
    source: SourceLink
    in_note: bool


class NoteDraft(Wire):
    note_id: int  # linkedin_draft.draft_id
    text: str
    chars: int  # len(text)
    max_chars: int  # #15's note.max_chars
    reason_line: str
    reason_state: ReasonState
    needs_your_line: bool  # reason_state == 'proposed'
    facts: list[NoteFact]
    checks: list[Check]  # the stored ones (#15's check_note)
    saved_by: SavedBy
    saved_at: str
    revision: int


class PlanStep(Wire):
    kind: StepKind
    touch_id: int | None
    at: str | None  # None: not done yet


class PlanCompany(DailyCompany):
    revision: int  # the company's revision (a "not a fit" removal checks it)


class PlanPerson(Wire):
    person: PersonRef
    found_on: SourceLink | None


class FindSomeone(Wire):
    search_url: str
    handoff: str


class PlanRow(Wire):
    entry_id: int  # the rowid of daily_list_entry (`plan_entry_id`)
    position: int
    channel: PlanChannel
    lane: ListGroup
    company: PlanCompany
    person: PlanPerson | None  # None: find someone
    role: RoleToName | None
    apply_url: str | None
    reason: str
    reason_sources: list[SourceLink]
    note: NoteDraft | None
    find_someone: FindSomeone | None  # set only when person is None
    email_touch_id: int | None  # an email row: the Outbox item
    steps: list[PlanStep]  # request, then apply when the row has a role; none on an email row
    state: RowState
    carried_from: str | None
    revision: int  # the entry's own revision: every write on the row checks it


class LinkedinMonth(Wire):
    used: int  # plan-sourced requests this month
    limit: int  # the plan quota
    hard_used: int  # every request this month
    hard_limit: int


class TomorrowRow(Wire):
    company_name: str
    person_name: str | None
    role_title: str | None
    channel: PlanChannel | None = None  # the channel the plan would use; None when not known
    find_someone: bool = False  # no person yet: the row asks him to find someone at the company


class Tomorrow(Wire):
    total: int  # room on the next plan, not a count of the draft
    text: str
    preview: list[TomorrowRow]  # a dry run of the next plan, in its order; the page says "and N more"


class PlanWeek(Wire):
    requests: int
    accepts: int
    replies: int


class PlanView(Wire):
    as_of: str
    date: str
    built_at: str | None
    list_job: JobRunSummary | None
    not_ready: NotReady | None
    rows: list[PlanRow]
    done: int
    total: int
    linkedin_month: LinkedinMonth
    tomorrow: Tomorrow
    week: PlanWeek
