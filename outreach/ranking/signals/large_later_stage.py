"""large_later_stage: a big or late-stage company is ranked last and never hidden (#6).

Only a guess reaches here: a confirmed or estimated size over 300 is hidden by the hide policy before it is ranked.
Unknown size and unknown stage are neutral. `params` is the `[small]` section, which holds the stage lists and the
borderline headcount; this signal has no keys of its own.
"""
from __future__ import annotations

from outreach.ranking.confidence import Assessment
from outreach.ranking.facts import RankFacts, is_large


class LargeLaterStage:
    name = "large_later_stage"

    def __init__(self, params: dict):
        self.params = params

    def score(self, facts: RankFacts) -> Assessment:
        grade = facts.grade(self.name)
        if not is_large(facts.stage, facts.size_low, self.params):
            return Assessment(0.0, "No sign of a large later-stage company", grade)
        causes: list[str] = []
        if facts.stage in self.params["later_stages"]:
            causes.append(f"stage {facts.stage.replace('_', ' ')}")
        if facts.size_low is not None and facts.size_low > self.params["borderline_headcount"]:
            how = "a guess" if facts.size_confidence in (None, "guess") else facts.size_confidence
            causes.append(f"size about {facts.size_low}, {how}")
        return Assessment(-1.0, f"Large later-stage company: ranked last, never hidden ({'; '.join(causes)})", grade)
