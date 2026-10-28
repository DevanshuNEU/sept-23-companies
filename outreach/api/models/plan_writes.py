"""The bodies of the plan's writes: log a request, log applied, log the people he asked at a company, save his
people search, skip a row, save a note, confirm its reason line."""
from typing import Literal

from pydantic import Field, field_validator

from outreach.api.models.base import WireRequest
from outreach.api.routers import MAX_ID

TOUCH_KEY = r"^[A-Za-z0-9:._-]{1,200}$"  # no `#`: an undone touch keeps `<key>#undone-<id>`, never a body key
NOTE_TEXT_MAX = 3000  # far over any note limit: an over-long note is saved with its block check, not refused
REASON_MAX = 500
PASS_NOTE_MAX = 280  # the words he types under Other or Role not for me


PERSON_NAME_MAX = 200


class LogRequestBody(WireRequest):
    """He sent the LinkedIn request by hand. `idempotencyKey` is the touch's own key (`plan:<entryId>:request`): one
    touch per key, whatever the request id. `revision` is optional and never checked: a log only adds a touch, the key
    stops a double press, and two logs sent out of order (the tab closed) must both land. The entry's revision still
    moves on.

    A row with a person sends its `personId`. A company row (nobody on it: company first, #9) sends `personName`
    instead, the name of the person he asked; the person is added at the company (by him) or found there by name."""

    person_id: int | None = Field(default=None, ge=1, le=MAX_ID)
    person_name: str | None = Field(default=None, max_length=PERSON_NAME_MAX)
    idempotency_key: str = Field(pattern=TOUCH_KEY)
    revision: int | None = Field(default=None, ge=1)


PEOPLE_TEXT_MAX = 8000  # 10 lines of name, title and a 300 character note, with room to spare
PEOPLE_KEY = r"^[A-Za-z0-9:._-]{1,190}$"  # TOUCH_KEY with room for the line number (`<key>:<n>`)
SEARCH_URL_MAX = 2100  # over linkedin_search_url's 2000, so a long paste is refused in words, not by size


class LogPeopleBody(WireRequest):
    """The people he asked at a company row, pasted as `Name | Title | note` lines (up to 10; the terminal's paste
    block `Company | Name | Title | note` is read too). The API reads the lines; the page sends them as typed.
    `idempotencyKey` is the paste's own key (`plan:<entryId>:people:<n>`): line n's touch key is `<key>:<n>`, so the
    same paste sent twice writes once."""

    lines: str = Field(max_length=PEOPLE_TEXT_MAX)
    idempotency_key: str = Field(pattern=PEOPLE_KEY)
    revision: int | None = Field(default=None, ge=1)


class SaveSearchBody(WireRequest):
    """His LinkedIn people search for the row's company, the address as he copied it from his browser."""

    url: str = Field(max_length=SEARCH_URL_MAX)


class LogAppliedBody(WireRequest):
    """He applied to the row's role. `idempotencyKey` is `plan:<entryId>:apply`. `revision` as `LogRequestBody`."""

    posting_id: int = Field(ge=1, le=MAX_ID)
    idempotency_key: str = Field(pattern=TOUCH_KEY)
    revision: int | None = Field(default=None, ge=1)


class SkipRowBody(WireRequest):
    """Carry the row to tomorrow. It changes the row's state, so a stale `revision` conflicts."""

    revision: int = Field(ge=1)


class SaveNoteBody(WireRequest):
    text: str = Field(max_length=NOTE_TEXT_MAX)
    reason_line: str = Field(max_length=REASON_MAX)
    revision: int = Field(ge=1)


class ConfirmReasonBody(WireRequest):
    revision: int = Field(ge=1)


class PassPickBody(WireRequest):
    """Not now on a pick. `reason` is one of the chips, or none. `note` is what he typed (under Other, Role not for me,
    or any chip), up to 280 characters, trimmed; Wrong company and Wrong location act on the posting or board. `idempotencyKey` is the pass's own key
    (`plan:<entryId>:pass`): the same key again answers with the first answer and writes nothing. It changes the
    row's state, so a stale `revision` conflicts."""

    reason: Literal["visa", "too_big", "too_early", "role", "product", "know_it", "not_a_fit", "other",
                    "wrong_company", "wrong_location"] | None = None
    note: str | None = Field(default=None, max_length=PASS_NOTE_MAX)  # his own words; blank is none
    # The posting the pick showed him when he flagged it: the lead opening can change before the pass commits.
    posting_id: int | None = Field(default=None, ge=1, le=MAX_ID)
    idempotency_key: str = Field(pattern=TOUCH_KEY)
    revision: int = Field(ge=1)

    @field_validator("note")
    @classmethod
    def _trim(cls, v: str | None) -> str | None:
        return (v or "").strip() or None


class AddFromBenchBody(WireRequest):
    """Add a bench company to today's list. `idempotencyKey` is `bench:<companyId>:add`: once it landed, the same
    key answers with the row and writes nothing."""

    idempotency_key: str = Field(pattern=TOUCH_KEY)


class SwapPickBody(WireRequest):
    """Browse companies (#120): put `companyId` in the slot of this pick. `idempotencyKey` is
    `pick-swap:<entryId>:<companyId>`: the same key again answers with the first answer and writes nothing. It
    changes the pick's row, so a stale `revision` conflicts."""

    company_id: int = Field(ge=1, le=MAX_ID)
    idempotency_key: str = Field(pattern=TOUCH_KEY)
    revision: int = Field(ge=1)
