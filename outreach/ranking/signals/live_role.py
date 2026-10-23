"""live_role: is there an open role this person can apply to, and how fresh is it (#6)."""
from __future__ import annotations

from outreach.ranking.common import clean_text
from outreach.ranking.confidence import Assessment
from outreach.ranking.facts import RankFacts
from outreach.ranking.roles import newest_stale

_LANES = ("level_role", "intern_junior", "any_us_role")  # strongest first
_LABELS = {"new_grad": "new grad", "early_career": "early career", "swe1": "SWE I", "swe2": "SWE II"}
_TITLE_BASIS = "Title says"


def _label(lane: str, tier: str | None, basis: str) -> str:
    """What the role is, from where its level came: the title's tier only when the title set it ("Open SWE II
    role"), else what the posting said ("Open role at his level (asks 2 years)"); an open level names no tier."""
    if lane == "intern_junior":
        return "Open intern role"
    if lane == "level_role" and tier == "open":
        return "Open role, no level in the title"
    if lane == "level_role" and basis and not basis.startswith(_TITLE_BASIS):
        return f"Open role at his level ({basis[0].lower()}{basis[1:]})"
    return f"Open {_LABELS.get(tier, 'engineering')} role"


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
        label = _label(lane, facts.best_level_tier, clean_text(facts.best_level_basis))
        title = f": {clean_text(facts.best_title)}" if facts.best_title else ""
        return Assessment(round(min(1.0, self.params[lane] + bonus), 4),
                          f"{label}{title}{_posted(facts.best_role_age_days, facts.best_dated)}", grade)

    @staticmethod
    def _no_role(facts: RankFacts) -> str:
        if facts.best_posting_id is not None:
            # A live posting with no role lane: only an any_us_role posting, which a large company does not earn.
            return "Open role at a large company, not counted"
        if not facts.stale_postings:
            return "No open role yet"
        newest = newest_stale(facts.stale_postings)
        reason = clean_text(newest.stale_reason)
        if len(facts.stale_postings) == 1:
            return f"No live role: the only posting is stale ({reason})"
        return f"No live role: {len(facts.stale_postings)} postings are stale ({reason})"
