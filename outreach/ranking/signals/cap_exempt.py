"""cap_exempt: a cap-exempt employer needs no H-1B lottery (#6). `no` and unknown are neutral."""
from __future__ import annotations

from outreach.ranking.confidence import Assessment
from outreach.ranking.facts import RankFacts


class CapExempt:
    name = "cap_exempt"

    def __init__(self, params: dict):
        self.params = params

    def score(self, facts: RankFacts) -> Assessment:
        grade = facts.grade(self.name)
        if facts.cap_exempt in ("confirmed", "likely"):
            return Assessment(float(self.params[facts.cap_exempt]), "Cap-exempt employer: no lottery", grade)
        if facts.cap_exempt == "no":
            return Assessment(0.0, "Not a cap-exempt employer", grade)
        return Assessment(0.0, "Cap-exempt status unknown", grade)
