"""pay_level: the wage level of posted pay, else of the employer's junior H-1B history (#6). Unknown is neutral."""
from __future__ import annotations

from outreach.ranking.confidence import Assessment
from outreach.ranking.facts import RankFacts


class PayLevel:
    name = "pay_level"

    def __init__(self, params: dict):
        self.params = params

    def score(self, facts: RankFacts) -> Assessment:
        grade = facts.grade(self.name)
        level = facts.pay_level
        if level not in (1, 2, 3, 4):
            return Assessment(0.0, "Pay level unknown", grade)
        note = f": {facts.pay_note}" if facts.pay_note else ""
        return Assessment(float(self.params[f"level_{level}"]), f"Wage level {level}{note}", grade)
