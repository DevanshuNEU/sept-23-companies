"""The newest stored scorecard (#153): `GET /api/pipeline/scorecard`. A pure read of the day's event."""
import sqlite3

from outreach.api.models.pipeline import ScorecardCard, ScorecardView
from outreach.outcomes.windows import ny_day
from outreach.pipeline.alarms import newest_card


def scorecard_view(conn: sqlite3.Connection) -> ScorecardView:
    newest = newest_card(conn)
    if newest is None:
        return ScorecardView(day=None, card=None)
    at, card = newest
    return ScorecardView(day=ny_day(at), card=ScorecardCard.model_validate(card))
