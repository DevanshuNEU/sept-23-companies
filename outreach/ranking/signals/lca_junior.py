"""lca_junior: the employer files junior H-1B petitions, so it has hired people at this level before (#6)."""
from __future__ import annotations

from outreach.ranking.confidence import Assessment
from outreach.ranking.facts import RankFacts


class LcaJunior:
    name = "lca_junior"

    def __init__(self, params: dict):
        self.params = params

    def score(self, facts: RankFacts) -> Assessment:
        grade = facts.grade(self.name)
        n, m, cap = facts.lca_jr_recent, facts.lca_level1_recent, self.params["cap"]
        if n <= 0:
            return Assessment(0.0, "No junior H-1B filings in the last two fiscal years", grade)
        noun = "filing" if n == 1 else "filings"
        return Assessment(round(min(n, cap) / cap, 4),
                          f"{n} junior H-1B {noun} in the last two fiscal years ({m} at level I)", grade)
