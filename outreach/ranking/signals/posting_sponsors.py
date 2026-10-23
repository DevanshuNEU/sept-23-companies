"""posting_sponsors: the best live posting says the company sponsors visas, or welcomes OPT (#6)."""
from __future__ import annotations

from outreach.ranking.confidence import Assessment
from outreach.ranking.facts import RankFacts

_REASONS = {"yes": "Posting says the company sponsors visas", "opt_only": "Posting welcomes OPT"}


class PostingSponsors:
    name = "posting_sponsors"

    def __init__(self, params: dict):
        self.params = params

    def score(self, facts: RankFacts) -> Assessment:
        stance = facts.best_sponsorship
        grade = facts.grade(self.name)
        if stance in _REASONS:
            return Assessment(float(self.params[stance]), _REASONS[stance], grade)
        if stance is None:
            return Assessment(0.0, "No live posting to read sponsorship from", grade)
        return Assessment(0.0, "Posting does not say it sponsors visas", grade)
