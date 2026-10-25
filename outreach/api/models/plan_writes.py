"""The bodies of the plan's writes: log a request, log applied, skip a row, save a note, confirm its reason line."""
from pydantic import Field

from outreach.api.models.base import WireRequest
from outreach.api.routers import MAX_ID

TOUCH_KEY = r"^[A-Za-z0-9:._-]{1,200}$"  # no `#`: an undone touch keeps `<key>#undone-<id>`, never a body key
NOTE_TEXT_MAX = 3000  # far over any note limit: an over-long note is saved with its block check, not refused
REASON_MAX = 500


class LogRequestBody(WireRequest):
    """He sent the LinkedIn request by hand. `idempotencyKey` is the touch's own key (`plan:<entryId>:request`): one
    touch per key, whatever the request id."""

    person_id: int = Field(ge=1, le=MAX_ID)
    idempotency_key: str = Field(pattern=TOUCH_KEY)
    revision: int = Field(ge=1)


class LogAppliedBody(WireRequest):
    """He applied to the row's role. `idempotencyKey` is `plan:<entryId>:apply`."""

    posting_id: int = Field(ge=1, le=MAX_ID)
    idempotency_key: str = Field(pattern=TOUCH_KEY)
    revision: int = Field(ge=1)


class SkipRowBody(WireRequest):
    """Carry the row to tomorrow."""

    revision: int = Field(ge=1)


class SaveNoteBody(WireRequest):
    text: str = Field(max_length=NOTE_TEXT_MAX)
    reason_line: str = Field(max_length=REASON_MAX)
    revision: int = Field(ge=1)


class ConfirmReasonBody(WireRequest):
    revision: int = Field(ge=1)
