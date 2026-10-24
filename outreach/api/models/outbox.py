"""The Outbox and the sending strip: every model is named as `web/src/api/types.ts` names it.

`SendingStatus` is the sender's own snapshot on the wire, and `Schedule.reason` is one of the queue's own reasons
(`outreach.sending.queue.REASONS`), so the strip and each row say what the sender itself would do.
"""
from typing import Literal

from pydantic import Field

from outreach.api.models.base import Wire, WireRequest
from outreach.api.models.checks import Check, CheckSeverity
from outreach.api.models.company import CompanyRef, PersonRef, RoleToName, ThreadMessage, TouchState
from outreach.api.models.health import SenderState

OutboxGroup = Literal["replied", "review", "approved", "sending", "checking", "sent", "paused", "closed"]
MessageState = Literal["planned", "drafted", "approved", "sending", "send_unknown", "sent", "failed", "skipped"]
ReplyKind = Literal["unsorted", "interested", "not_now", "said_no", "asked_to_stop", "auto_reply"]
SavedBy = Literal["claude", "devanshu", "sender", "import"]
# outreach.sending.queue.REASONS, spelled out so the schema lists them (a test pins the two together).
ScheduleReason = Literal[
    "ready", "sender_paused", "signed_out", "old_sender_running", "message_id_unproven", "not_business_day",
    "daily_limit", "spacing", "window", "bad_timezone", "followup_not_due", "company_paused", "company_held",
    "per_company_limit", "per_company_gap", "recruiter_waits", "no_address", "no_thread", "company_closed", "no_go",
    "reply_stop", "touch_done",
]
Seq = Literal[0, 1, 2, 3]
FollowupSeq = Literal[1, 2, 3]


# ---------------------------------------------------------------------------------------------- sending

class SendingStepDown(Wire):
    at: str
    reason: str


class SendingSignals(Wire):
    bounce_rate_3d: float = Field(alias="bounceRate3d")
    gmail_errors_today: int
    stop_replies_today: int


class SendingWindow(Wire):
    start: str
    end: str


class NextSend(Wire):
    at: str
    tz: str
    touch_id: int
    person_name: str
    company_name: str


class SendingStatus(Wire):
    state: SenderState
    paused_reason: str
    paused_at: str | None
    gmail_message: str | None
    sent_today: int
    limit_today: int
    ramp_level: int
    ramp_levels: list[int]
    next_limit_if_clean: int
    last_step_down: SendingStepDown | None
    signals: SendingSignals
    window: SendingWindow
    next_send: NextSend | None
    none_reason: str | None
    revision: int


class PauseSendingBody(WireRequest):
    revision: int = Field(ge=1)


class ResumeSendingBody(WireRequest):
    revision: int = Field(ge=1)
    acknowledge_block: bool = False  # true only after he has read the hard block's reason


# ---------------------------------------------------------------------------------------------- the list

class Schedule(Wire):
    earliest_at: str | None
    tz: str
    reason: ScheduleReason
    text: str
    company_sent_today: int
    company_slots_left: int


class NextDue(Wire):
    seq: FollowupSeq
    on: str


class OutboxItem(Wire):
    touch_id: int
    message_id: int
    seq: Seq
    state: MessageState
    touch_state: TouchState
    group: OutboxGroup
    sort_key: str
    person: PersonRef
    company: CompanyRef
    subject: str
    preview: str
    summary: str
    worst: CheckSeverity | None
    block_count: int
    warn_count: int
    at: str
    schedule: Schedule | None
    next_due: NextDue | None
    revision: int


class OutboxCounts(Wire):
    replied: int = 0
    review: int = 0
    approved: int = 0
    sending: int = 0
    checking: int = 0
    sent: int = 0
    paused: int = 0
    closed: int = 0


class ClosedBreakdown(Wire):
    bounced: int = 0
    skipped: int = 0
    company_closed: int = 0


class OutboxView(Wire):
    as_of: str
    sending: SendingStatus
    items: list[OutboxItem]
    counts: OutboxCounts
    closed_breakdown: ClosedBreakdown


# ---------------------------------------------------------------------------------------------- one thread

class OutboxHook(Wire):
    text: str
    on: str | None


class OutboxFollowup(Wire):
    seq: FollowupSeq
    due_on: str
    sent_at: str | None
    state: MessageState


class OutboxReply(Wire):
    from_: str = Field(alias="from")
    at: str
    text: str
    kind: ReplyKind


class HeldMessage(Wire):
    touch_id: int
    person_name: str
    state: MessageState
    seq: Seq
    due_on: str | None


class OutboxDetail(Wire):
    as_of: str
    item: OutboxItem
    body: str
    hook: OutboxHook
    project: str
    role: RoleToName | None
    checks: list[Check]
    saved_by: SavedBy
    saved_at: str
    followups: list[OutboxFollowup]
    first_sent_at: str | None
    gmail_thread_url: str | None
    reply: OutboxReply | None
    touch_revision: int
    held: list[HeldMessage]
    thread: list[ThreadMessage]
    bounce_reason: str | None


class CompareSide(Wire):
    message_id: int
    person_name: str
    body: str


class CompareMatch(Wire):
    a_start: int
    a_end: int
    b_start: int
    b_end: int


class DraftCompare(Wire):
    a: CompareSide
    b: CompareSide
    similarity: float
    matches: list[CompareMatch]
