"""Outcomes: the prompt, an answer, a correction, a touch's history and a touch he logs himself.

`PromptTouch` and `OutcomePromptView` are named as #33's seam block in `web/src/api/types.ts`; `person` is null for an
application (a touch with no person, #33 ruling 2).
"""
from typing import Literal

from pydantic import Field

from outreach.api.models.base import Wire, WireRequest
from outreach.api.models.company import CompanyEvent, CompanyRef, PersonRef

Channel = Literal["linkedin_request", "linkedin_message", "email", "application", "hn_reply", "other"]
OutcomeKind = Literal["accepted", "replied", "referred", "interview", "offer", "quiet"]
# What a reply is, as stored on a touch. An answer from the prompt never sends `unsorted` (it says what the reply was).
StoredReplyKind = Literal["unsorted", "interested", "not_now", "said_no", "asked_to_stop"]
AnswerReplyKind = Literal["interested", "not_now", "said_no", "asked_to_stop"]


class PromptTouch(Wire):
    touch_id: int
    person: PersonRef | None
    company: CompanyRef
    channel: Channel
    sent_at: str
    age_days: int  # New York days since it was sent
    context: str  # one plain line from the touch's context
    revision: int


class OutcomePromptView(Wire):
    as_of: str
    touches: list[PromptTouch]


class OutcomeBody(WireRequest):
    """An answer to "what came of this?". `quiet` is "Nothing yet": a snooze before day 21, then the quiet mark."""

    kind: OutcomeKind
    reply_kind: AnswerReplyKind | None = None  # needed with `replied`, refused with anything else
    revision: int


class CorrectionBody(WireRequest):
    """Set one outcome to `at` (any ISO-8601 time) or clear it (`at` null), from the touch's history."""

    at: str | None
    reply_kind: StoredReplyKind | None = None
    revision: int


class TouchRevision(Wire):
    revision: int


class TouchHistory(Wire):
    touch_id: int
    channel: Channel
    person: PersonRef | None
    company: CompanyRef
    sent_at: str
    accepted: str | None
    replied: str | None
    reply_kind: StoredReplyKind | None
    referred: str | None
    interview: str | None
    offer: str | None
    quiet: str | None
    events: list[CompanyEvent]  # newest first
    revision: int


class LogTouchBody(WireRequest):
    """A touch he made himself: a reply to an HN post, or any other touch. The other channels have their own writes."""

    channel: Channel
    company_id: int
    person_id: int | None = None
    posting_id: int | None = None
    idempotency_key: str = Field(min_length=1, max_length=200)  # the touch's own key: one touch per key, ever


class TouchLogged(Wire):
    touch_id: int
    revision: int
