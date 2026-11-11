"""The Applied view (#218): `AppliedView` and `AppliedRow` as in `web/src/api/types.ts`."""
from outreach.api.models.base import Wire


class AppliedRow(Wire):
    posting_id: int
    company_id: int  # the survivor of any merge
    company_name: str
    title: str
    url: str  # the posting's link, else the one saved with the application; may be empty
    applied_on: str  # YYYY-MM-DD in New York
    open: bool  # the posting is still on its board


class AppliedView(Wire):
    as_of: str
    rows: list[AppliedRow]
