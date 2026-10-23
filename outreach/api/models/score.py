"""The score breakdown of one company: what the ranker stored, shaped for the page. Nothing here is recomputed."""
from typing import Literal

from outreach.api.models.base import Wire
from outreach.api.models.watch import SourceLink

ListGroup = Literal["level_role", "intern_junior", "any_us_role"]
ConfidenceLevel = Literal["high", "medium", "low"]


class ScoreSignal(Wire):
    name: str
    label: str
    value: float
    weight: float
    points: float
    share: float  # points / sum of abs(points), signed; 0 when every signal is neutral
    neutral: bool
    reason: str
    confidence: ConfidenceLevel
    basis: str
    source: SourceLink | None


class ScoreLane(Wire):
    lane: ListGroup
    strongest: bool


class ScoreConfidence(Wire):
    level: ConfidenceLevel
    basis: str


class PreviousScore(Wire):
    ranker_version: str
    score: float


class ScoreView(Wire):
    as_of: str
    company_id: int
    ranker_version: str
    score: float
    rank: int | None  # None for a hidden company: it keeps its score and has no place in the list
    of_companies: int
    lanes: list[ScoreLane]
    signals: list[ScoreSignal]
    rank_reason: str
    confidence: ScoreConfidence
    scored_at: str
    previous: PreviousScore | None
