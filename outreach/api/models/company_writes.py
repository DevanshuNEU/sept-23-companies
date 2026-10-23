"""The company writes (pause, resume, close, reopen) and a person's role kind: request bodies and answers."""
from pydantic import Field

from outreach.api.models.base import Wire, WireRequest
from outreach.api.models.company import CloseReason, RoleKind

NOTE_MAX = 2000


class CompanyRevisionBody(WireRequest):
    revision: int = Field(ge=1)


class CloseCompanyBody(WireRequest):
    reason: CloseReason
    note: str = Field(default="", max_length=NOTE_MAX)
    revision: int = Field(ge=1)


class RoleKindBody(WireRequest):
    kind: RoleKind
    revision: int = Field(ge=1)


class CompanyWritten(Wire):
    """The company's new revision, after a pause, close or reopen."""

    revision: int


class CompanyResumed(Wire):
    """A resume: how many drafted or approved emails were waiting on the pause, and the new revision."""

    released: int
    revision: int
