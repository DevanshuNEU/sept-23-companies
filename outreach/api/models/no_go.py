"""The No-go view: `NoGoView`, `NoGoRow`, `RemovedRow` as in `web/src/api/types.ts`, the ranking's hides, and a lift."""
from typing import Annotated, Literal

from pydantic import Field

from outreach.api.models.base import Wire, WireRequest
from outreach.api.models.company import CompanyRef, ImportSource, PersonRef
from outreach.api.models.needs_you import NoGoReason

NoGoSource = Literal["devanshu", "rules", "reply", "import", "claude"]
HideKind = Literal["no_go", "over_size", "no_sponsorship"]  # outreach.ranking.hide.HIDE_KINDS


class NoGoTargetCompany(Wire):
    kind: Literal["company"]
    company: CompanyRef


class NoGoTargetPerson(Wire):
    kind: Literal["person"]
    person: PersonRef
    company_name: str


NoGoTarget = Annotated[NoGoTargetCompany | NoGoTargetPerson, Field(discriminator="kind")]


class NoGoRow(Wire):
    kind: Literal["no_go"]
    no_go_id: int
    target: NoGoTarget
    reason: NoGoReason
    quote: str
    note: str
    source: NoGoSource
    source_ref: str
    source_url: str | None  # the source_ref when it is a web address
    import_source: ImportSource | None  # from `import:<source>:<key>`
    added_at: str


class RemovedCompany(CompanyRef):
    revision: int


class RemovedRow(Wire):
    kind: Literal["removed"]
    company: RemovedCompany
    remove_reason: str
    note: str
    removed_at: str


class HiddenCompany(Wire):
    """A company the ranker hides, with the reason it stored. `provisional` is "hidden, pending reader"."""

    company: CompanyRef
    kind: HideKind
    reason: str
    provisional: bool


class ReasonCount(Wire):
    reason: NoGoReason | Literal["removed"]
    count: int


class NoGoView(Wire):
    as_of: str
    companies: list[Annotated[NoGoRow | RemovedRow, Field(discriminator="kind")]]
    people: list[NoGoRow]
    counts_by_reason: list[ReasonCount]
    hidden_companies: list[HiddenCompany]


class LiftNoGoBody(WireRequest):
    reason: str = Field(max_length=500)
