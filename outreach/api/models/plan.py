"""The people plan: `PlanView`, `PlanRow`, `PlanStep`, `NoteDraft` and `NoteFact` as #33's seam block names them.

Every derived field (row state, reason and its sources, the search link, tomorrow's line, the note limit) is the
API's; the page computes none of them.
"""
from typing import Literal

from outreach.api.models.base import Wire
from outreach.api.models.checks import Check
from outreach.api.models.company import CompanyRef, PersonRef, RoleToName
from outreach.api.models.health import JobRunSummary
from outreach.api.models.outbox import SavedBy
from outreach.api.models.today import DailyCompany, ListGroup, NotReady
from outreach.api.models.watch import SourceLink

StepKind = Literal["request", "apply"]
ReasonState = Literal["proposed", "confirmed", "written"]
RowState = Literal["todo", "done", "skipped"]
PlanChannel = Literal["linkedin_request", "email"]
StandingStatus = Literal["new", "in_flight", "asked_before", "replied"]


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
    needs_your_line: bool  # reason_state == 'proposed' and saved_by == 'claude': his own note needs no line
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


class QueueStanding(Wire):
    """Where a company stands (`outreach.plan.contact`): new, a wave in flight, asked before with no answer, or a
    reply that holds it. `text` is the line the row shows."""

    status: StandingStatus
    text: str  # "New", "4 asked 6 days ago, free again Nov 18", "2 asked before. No answer for 20 days"
    reason: str  # ContactState.reason
    asked: int  # people in the current wave
    room: int  # people he may still ask in this wave (queue.requests_per_company less asked)
    free_on: str | None  # the day the company is free again, while a wave is in flight


class CompanyQueue(Wire):
    """A company row's lines (company first, #9 2026-10-26). Every line is the API's; the page shows them as given."""

    size_line: str | None  # "40 to 60 people (estimate), seed (guess)"; None when neither is known
    role_line: str | None  # "Forward Deployed Engineer, posted 6 days ago"; None with no role
    hiring: int  # open engineering postings posted (else first seen) in the last 30 days
    hiring_line: str
    sponsorship_line: str  # "Sponsorship not stated, E-Verify enrolled"
    reasons: list[str]  # why it ranks: up to 3 of the stored why signals, else the row's reason
    standing: QueueStanding
    last_touch: str | None  # "Request to Priya Rao (Engineer) on Oct 20"
    email_hint: str | None  # set when the email rule (`pick_channel`) allows email to someone on record
    people_on_record: int
    people_line: str  # "2 people on record, none asked"
    people_search_url: str  # his saved search, else the LinkedIn id filter, else the name; never fetched
    search_saved: bool  # the link is the search he saved
    careers_url: str | None  # the posting, else the board's page, else the watched page, else https://<domain>
    terminal_line: str  # "Knock: Tessera Labs (company 1304), role: Forward Deployed Engineer"


class InFlightCompany(Wire):
    """A company with a wave out in the last `linkedin.rotate_days`, or a reply: the track half of the queue."""

    company: CompanyRef
    status: Literal["in_flight", "replied"]
    text: str
    reason: str
    free_on: str | None


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
    queue: CompanyQueue | None = None  # set only on a company row (no person, not an email row)


class LinkedinWeek(Wire):
    used: int  # plan-sourced requests in the last 7 days (New York days, today included)
    limit: int  # the plan quota, in any 7 days
    hard_used: int  # every request in the last 7 days, his own included
    hard_limit: int  # LinkedIn's limit, in any 7 days


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
    linkedin_week: LinkedinWeek
    tomorrow: Tomorrow
    week: PlanWeek
    in_flight: list[InFlightCompany] = []  # waves out in the last 14 days and replies, replies first
