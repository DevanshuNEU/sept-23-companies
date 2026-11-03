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
