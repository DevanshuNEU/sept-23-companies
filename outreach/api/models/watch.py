"""The watch list: `WatchItem` and `WatchView` as in `web/src/api/types.ts`, plus the answer to a dismiss."""
from typing import Literal

from outreach.api.models.base import Wire
from outreach.api.models.company import CompanyRef

WatchKind = Literal["careers_changed", "new_reason_no_role", "merge_candidate"]


class SourceLink(Wire):
    label: str
    url: str


class WatchItem(Wire):
    id: int  # ref_id * 4 + the kind's index (careers_changed 0, new_reason_no_role 1, merge_candidate 2)
    kind: WatchKind
    company: CompanyRef
    text: str
    at: str
    link: SourceLink | None
    review_id: int | None


class WatchView(Wire):
    as_of: str
    items: list[WatchItem]


class WatchDismissed(Wire):
    """What a dismiss answers with: the id it was sent. (A write answers with JSON; an empty answer reads as broken.)"""

    id: int
