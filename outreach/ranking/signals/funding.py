"""funding: money raised lately means the company is hiring (#6). Fades to zero at the end of the window."""
from __future__ import annotations

from outreach.ranking.confidence import Assessment
from outreach.ranking.facts import RankFacts


class Funding:
    name = "funding"

    def __init__(self, params: dict, window_months: int):
        """`params` is the empty `[funding]` section; the window is `[ranker] funding_window_months`."""
        self.params = params
        self.window = window_months

    def score(self, facts: RankFacts) -> Assessment:
        grade = facts.grade(self.name)
        months = facts.funded_months_ago
        if months is None:
            return Assessment(0.0, "No funding on record", grade)
        noun = "month" if months == 1 else "months"
        return Assessment(round(max(0.0, 1 - months / self.window), 4), f"Raised money {months} {noun} ago", grade)
