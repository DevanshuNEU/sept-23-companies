"""`Check`, as `web/src/api/types.ts` names it. The error envelope carries a list of them."""
from typing import Annotated, Literal

from pydantic import Field

from outreach.api.models.base import Wire

CheckSeverity = Literal["block", "warn", "pass"]


class CheckFixCompare(Wire):
    kind: Literal["compare"]
    other_message_id: int


class CheckFixReviewItem(Wire):
    kind: Literal["review_item"]
    review_id: int


class CheckFixNoGo(Wire):
    kind: Literal["no_go"]
    no_go_id: int


class CheckFixRoleKind(Wire):
    kind: Literal["role_kind"]


CheckFix = Annotated[
    CheckFixCompare | CheckFixReviewItem | CheckFixNoGo | CheckFixRoleKind,
    Field(discriminator="kind"),
]


class CheckSpan(Wire):
    field: Literal["subject", "body"]
    start: int
    end: int


class Check(Wire):
    code: str
    severity: CheckSeverity
    title: str
    detail: str
    fix: CheckFix | None
    spans: list[CheckSpan]
