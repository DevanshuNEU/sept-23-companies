"""reachable_person: someone at the company to write to, or an HN post that asks for email (#6)."""
from __future__ import annotations

from outreach.ranking.confidence import Assessment
from outreach.ranking.facts import RankFacts

_DEFAULT = {"person": "Reachable person on file", "hn_post": "An HN post asks for email"}


class ReachablePerson:
    name = "reachable_person"

    def __init__(self, params: dict):
        self.params = params

    def score(self, facts: RankFacts) -> Assessment:
        grade = facts.grade(self.name)
        via = facts.reach_via
        if via in _DEFAULT:
            return Assessment(float(self.params[via]), facts.reach_note.strip() or _DEFAULT[via], grade)
        return Assessment(0.0, "No reachable person yet", grade)
