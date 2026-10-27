"""live_role: is there an open role this person can apply to, and how fresh is it (#6)."""
from __future__ import annotations

from outreach.ranking.common import clean_text
from outreach.ranking.confidence import Assessment
from outreach.ranking.facts import RankFacts
from outreach.ranking.roles import newest_stale, rank_tier

_LANES = ("level_role", "intern_junior", "any_us_role")  # strongest first
_LABELS = {"new_grad": "new grad", "early_career": "early career", "swe1": "SWE I", "swe2": "SWE II"}
_TITLE_BASIS = "Title says"
_AFTER = ", ranked after new grad and SWE I"


def _label(lane: str, tier: str | None, basis: str, years: int | None) -> str:
    """What the role is, named by the tier ranking uses (roles.rank_tier): "Open new grad role", "Open SWE I role".
    A level the posting set, not the title, adds the basis ("Open early career role (asks 2 years)"); an open level
    names no tier. A SWE II or open level role says it ranks after the early career tiers (#6, level priority)."""
    if lane == "intern_junior":
        return "Open intern role"
    if lane != "level_role":
        return f"Open {_LABELS.get(tier, 'engineering')} role"
    ranked = rank_tier(tier, basis, years)
    after = _AFTER if ranked in ("swe2", "open") else ""
    if ranked == "open":
        return f"Open role, no level in the title{after}"
    name = _LABELS.get(ranked, "engineering")
    if basis and not basis.startswith(_TITLE_BASIS):
        return f"Open {name} role ({basis[0].lower()}{basis[1:]}){after}"
    return f"Open {name} role{after}"


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
        basis = clean_text(facts.best_level_basis)
        base = self.params[lane]
        if lane == "level_role":  # by the best posting's tier; a tier with no value keeps level_role
            tier = rank_tier(facts.best_level_tier, basis, facts.best_years_asked)
            base = self.params["tier_values"].get(tier, base)
        label = _label(lane, facts.best_level_tier, basis, facts.best_years_asked)
        title = f": {clean_text(facts.best_title)}" if facts.best_title else ""
        return Assessment(round(min(1.0, base + bonus), 4),
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
