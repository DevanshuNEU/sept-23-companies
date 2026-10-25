"""Needs you: `NeedsYouView`, `ResolvedReview` and the `ReviewFix` bodies as in `web/src/api/types.ts`, plus the
answers to a resolve, a reopen and an unmerge. `ReviewItem` itself lives with the company page, which lists the same
items."""
from typing import Annotated, Literal

from pydantic import Field

from outreach.api.models.base import Wire, WireRequest
from outreach.api.models.company import ReviewItem, ReviewKind
from outreach.api.models.health import JobRunSummary

NoGoReason = Literal[
    "no_sponsorship", "citizenship", "over_300", "defence_contractor", "agency", "said_no", "stop_reply", "not_a_fit",
    "other",
]
EditField = Literal["domain", "size", "sponsorship", "board_url"]


class ResolvedReview(Wire):
    id: int
    kind: ReviewKind
    company_name: str
    resolution: Literal["marked_ok", "marked_no_go", "field_edited"]
    note: str
    at: str
    locked: bool  # an edit wrote a locked value (a merge can be unmerged, so it is not locked)
    revision: int  # the item's revision, which a reopen sends back
    can_undo: bool  # `outreach.review.fixes.can_undo`: a reopen would take this settling back


class NeedsYouView(Wire):
    as_of: str
    open: list[ReviewItem]  # in-play companies first, then the rest ("Later"); oldest first within each
    resolved_this_week: list[ResolvedReview]  # his own resolutions this New York week, newest first
    boards_job: JobRunSummary | None  # the last read_boards run, else the last crawl
    settled_today: int  # items he settled (ok, no-go or a field he set) on today's New York date


class ReviewFixOk(WireRequest):
    kind: Literal["ok"]


class ReviewFixEdit(WireRequest):
    kind: Literal["edit"]
    field: EditField
    value: str
    source: str


class ReviewFixNoGo(WireRequest):
    kind: Literal["no_go"]
    reason: NoGoReason
    source: str


class ReviewFixMerge(WireRequest):
    kind: Literal["merge"]
    keep: Literal["a", "b"]  # a: the item's company; b: the company its evidence_key names


ReviewFix = Annotated[ReviewFixOk | ReviewFixEdit | ReviewFixNoGo | ReviewFixMerge, Field(discriminator="kind")]


class ResolveReviewBody(WireRequest):
    fix: ReviewFix
    revision: int


class ReviewResolved(Wire):
    id: int
    revision: int


class ReopenReviewBody(WireRequest):
    revision: int  # the settled item's revision, as `ResolvedReview.revision` gave it


class Reopened(Wire):
    revision: int  # the item's new revision; it is open again


class UnmergeBody(WireRequest):
    revision: int


class Unmerged(Wire):
    revision: int  # the company's new revision
