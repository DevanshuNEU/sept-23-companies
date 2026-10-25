"""everify: STEM OPT needs the employer enrolled in E-Verify (#6). Unknown is neutral."""
from __future__ import annotations

from outreach.ranking.confidence import Assessment
from outreach.ranking.facts import RankFacts


class EVerify:
    name = "everify"

    def __init__(self, params: dict):
        self.params = params

    def score(self, facts: RankFacts) -> Assessment:
        grade = facts.grade(self.name)
        if facts.everify == "enrolled":
            return Assessment(float(self.params["enrolled"]), "E-Verify enrolled (STEM OPT needs it by July 2027)", grade)
        if facts.everify == "not_enrolled":
            return Assessment(float(self.params["not_enrolled"]), "Not enrolled in E-Verify (STEM OPT needs it)", grade)
        return Assessment(0.0, "E-Verify status unknown", grade)
