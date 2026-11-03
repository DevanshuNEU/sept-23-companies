"""The miss log: `POST /api/pipeline/misses` takes a pasted job link and answers with the first cause found for it."""
from typing import Literal

from pydantic import Field

from outreach.api.models.base import Wire, WireRequest

# outreach.pipeline.misses.CAUSES; tests/test_api_pipeline.py pins the two together.
MissCause = Literal["no_company", "no_board", "not_read_yet", "read_wrong_level", "read_not_tech", "hidden_visa",
                    "not_us", "wrong_date", "found", "unknown_site"]


class MissBody(WireRequest):
    url: str = Field(min_length=1, max_length=2000)
    note: str = Field("", max_length=500)


class MissResult(Wire):
    miss_id: int
    cause: MissCause
    cause_text: str


class GapCompany(Wire):
    company_name: str
    ats: str | None
    listings: int
    knock_knows_company: bool
    knock_has_board: bool


class ScorecardCard(Wire):
    """`outreach.pipeline.scorecard.Scorecard`, as stored in the day's event. Shares run from 0 to 1."""
    simplify_listings: int
    simplify_coverage: float | None
    level_recall: float | None
    lead_over_simplify: float | None
    detection_p95_hours: float | None
    date_truth: None
    small_company_coverage: None
    gap_top: list[GapCompany]


class ScorecardView(Wire):
    """The newest stored card and the New York day it was taken; both null before the first one."""
    day: str | None
    card: ScorecardCard | None


class PipelineLine(Wire):
    """The sidebar's scorecard line: `text` from the newest card, `alarms` the texts of the alarms that hold, `tone`
    `bad` when there is any alarm, else `idle`."""
    text: str
    alarms: list[str]
    tone: Literal["bad", "idle"]
