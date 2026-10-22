"""staffing_risk: a staffing firm cannot offer STEM OPT, because the work is a third-party placement (#6).

A tier 3 employer that is not promoted counts as high risk even with no evidence row. Promoted, a high risk is
softened. This is a bottom signal: its weight is large enough to sort a hit company below every other.
"""
from __future__ import annotations

from outreach.ranking.confidence import Assessment
from outreach.ranking.facts import RankFacts

_WHY = "STEM OPT cannot be a third-party placement"


class StaffingRisk:
    name = "staffing_risk"

    def __init__(self, params: dict):
        self.params = params

    def score(self, facts: RankFacts) -> Assessment:
        grade = facts.grade(self.name)
        risk, promoted = facts.staffing_risk, facts.work_promoted
        if (facts.work_tier == 3 or risk == "high") and not promoted:
            return Assessment(float(self.params["high"]), f"Staffing-firm risk high: {_WHY}", grade)
        if risk == "high":
            return Assessment(float(self.params["promoted_high"]), f"Staffing-firm risk high (promoted): {_WHY}", grade)
        if risk == "medium":
            return Assessment(float(self.params["medium"]), f"Staffing-firm risk medium: {_WHY}", grade)
        return Assessment(0.0, "No staffing-firm risk found", grade)
