"""The Outbox writes' request bodies. Every write answers with the thread's `OutboxDetail`."""
from typing import Literal

from pydantic import Field

from outreach.api.models.base import WireRequest

SUBJECT_MAX = 200
BODY_MAX = 5000
SetReplyKind = Literal["interested", "not_now", "said_no", "asked_to_stop", "auto_reply"]


class MessageRevisionBody(WireRequest):
    revision: int = Field(ge=1)


class SaveMessageBody(WireRequest):
    subject: str = Field(min_length=1, max_length=SUBJECT_MAX)
    body: str = Field(min_length=1, max_length=BODY_MAX)
    revision: int = Field(ge=1)


class ReplyKindBody(WireRequest):
    kind: SetReplyKind
    revision: int = Field(ge=1)
