"""live_role: is there an open role this person can apply to, and how fresh is it (#6)."""
from __future__ import annotations

from outreach.ranking.common import clean_text
from outreach.ranking.confidence import Assessment
from outreach.ranking.facts import RankFacts

_LANES = ("level_role", "intern_junior", "any_us_role")  # strongest first
_LABELS = {"new_grad": "new grad", "early_career": "early career", "swe1": "SWE I", "swe2": "SWE II",
           "open": "open level"}


def _posted(days: int | None, dated: bool) -> str:
    if days is None:
        return ""
    when = "today" if days == 0 else "1 day ago" if days == 1 else f"{days} days ago"
    return f", {'posted' if dated else 'first seen'} {when}"


class LiveRole:
    name = "live_role"

    def __init__(self, params: dict):
        self.params = params

    def score(self, facts: RankFacts) -> Assessment:
        grade = facts.grade(self.name)
        lane = next((lane for lane in _LANES if lane in facts.lanes), None)
        if lane is None:
            return Assessment(0.0, self._no_role(facts), grade)
        bonus = 0.0
        if facts.best_role_age_days is not None and facts.best_dated:  # an age from first seen earns no bonus
            for limit, extra in zip(self.params["fresh_days"], self.params["fresh_bonus"]):
                if facts.best_role_age_days <= limit:
                    bonus = extra
                    break
        label = "intern" if lane == "intern_junior" else _LABELS.get(facts.best_level_tier, "engineering")
        title = f": {clean_text(facts.best_title)}" if facts.best_title else ""
        return Assessment(round(min(1.0, self.params[lane] + bonus), 4),
                          f"Open {label} role{title}{_posted(facts.best_role_age_days, facts.best_dated)}", grade)

    @staticmethod
    def _no_role(facts: RankFacts) -> str:
        if not facts.stale_postings:
            return "No open role yet"
        newest = min(facts.stale_postings, key=lambda b: (b.age_days is None, b.age_days or 0, -b.posting_id))
        reason = clean_text(newest.stale_reason)
        if len(facts.stale_postings) == 1:
            return f"No live role: the only posting is stale ({reason})"
        return f"No live role: {len(facts.stale_postings)} postings are stale ({reason})"
