"""small: a company of 250 people or fewer has room for a new graduate to be noticed (#6).

A known size follows a curve (P5): the value of its band in `band_tops` and `band_values` (the smaller, the higher),
times `early_value` for an early or unknown stage and `other_value` for any other. Unknown size is exactly neutral, and a stage alone never makes a company small. The one exception is a weak flag
(many open roles, many software H-1B filings, a shared domain, a later stage with no size): it lowers an unknown size.
The bottom push for a large company belongs to `large_later_stage`, so this signal never goes below the weak flag.
`params` is the `[small]` section.
"""
from __future__ import annotations

from outreach.ranking.confidence import Assessment
from outreach.ranking.facts import RankFacts


class Small:
    name = "small"

    def __init__(self, params: dict):
        self.params = params

    def score(self, facts: RankFacts) -> Assessment:
        p = self.params
        grade = facts.grade(self.name)
        size = facts.size_high if facts.size_high is not None else facts.size_low
        if size is None:
            if facts.weak_flags:
                return Assessment(float(p["weak_flag_value"]),
                                  "Size unknown, signs it may not be small: " + "; ".join(facts.weak_flags), grade)
            return Assessment(0.0, "Size unknown", grade)
        if size <= p["max_headcount"]:
            early = facts.stage is None or facts.stage in p["early_stages"]
            stage = f", stage {facts.stage.replace('_', ' ')}" if facts.stage else ""
            band = next(v for top, v in zip(p["band_tops"], p["band_values"]) if size <= top)
            return Assessment(round(band * p["early_value" if early else "other_value"], 4),
                              f"Small company: about {size} people{stage}", grade)
        if size <= p["borderline_headcount"]:
            return Assessment(float(p["borderline_value"]), f"Borderline size: about {size} people", grade)
        return Assessment(0.0, f"Not small: about {size} people", grade)
