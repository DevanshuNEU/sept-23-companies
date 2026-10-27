"""The LinkedIn list and the answer to logging a step: `LinkedInView`, `LinkedInGroup` and `LinkedInPerson` as in
`web/src/api/types.ts`. The tab of each person is derived here, never by the page."""
from typing import Literal

from outreach.api.models.base import Wire, WireRequest
from outreach.api.models.company import CompanyRef, PersonRef, RoleToName
from outreach.api.models.outcomes import AnswerReplyKind, StoredReplyKind

LinkedInStep = Literal["request_sent", "accepted", "message_sent", "replied"]
LinkedInReason = Literal["past_budget", "no_email", "alum_first"]
LinkedInTab = Literal["to_reach", "waiting", "replied"]


class LoggedStep(Wire):
    step: LinkedInStep
    at: str


class LinkedInPerson(Wire):
    person: PersonRef
    reason: LinkedInReason
    steps: list[LoggedStep]  # oldest first; a step he took back is not here
    reply_kind: StoredReplyKind | None
    tab: LinkedInTab  # to_reach: no step yet; waiting: a request or message went out; replied: they answered


class HeldBy(Wire):
    touch_id: int
    person_name: str


class LinkedInGroup(Wire):
    company: CompanyRef
    role: RoleToName | None
    emailed: int  # people at the company whose first email went out
    on_linked_in: int
    held_by: HeldBy | None  # whose reply holds the company's queued email
    people: list[LinkedInPerson]


class LinkedInCounts(Wire):
    to_reach: int
    waiting: int
    replied: int
    all: int


class LinkedInActivity(Wire):
    """Counts only, no rate: people with a live LinkedIn request (messaged) and people with a live reply step
    (replied), over the last 7 New York days (today and the 6 before) and in all. A reply in the window may answer a
    request from before it, so the two are never divided."""

    messaged_week: int
    replied_week: int
    messaged_total: int
    replied_total: int


class LinkedInView(Wire):
    as_of: str
    groups: list[LinkedInGroup]  # everyone recorded; the page lists only `tab == 'replied'` unless he asks for all
    counts: LinkedInCounts
    activity: LinkedInActivity
    requests_this_week: int  # New York week, not counting a request he took back


class LogStepBody(WireRequest):
    """A step he took on LinkedIn by hand. `replyKind` goes with `replied` and nothing else; an out-of-office is
    not a reply, so `auto_reply` is not a kind here."""

    step: LinkedInStep
    reply_kind: AnswerReplyKind | None = None


class UndoStepBody(WireRequest):
    """Take back the newest step he logged (only the newest, so the steps left always make sense)."""

    step: LinkedInStep
