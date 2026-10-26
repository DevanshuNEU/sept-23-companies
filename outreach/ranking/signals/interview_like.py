"""interview_like: a small lift toward companies like the ones that gave Devanshu interviews (#81).

One way: the value is 0 to 1, so the signal only adds points. It never lowers a score, and `decide_hide` never sees
it, so it cannot hide a company or write a no-go. The value comes from `outreach.insights.similar`: the nearest
interview company on four traits, never the company itself, 0 under the minimum. This is the one signal that does not
copy `facts.grade(name)`: its confidence belongs to the interview profile (never high), not to the company's facts.
`params` is the `[interview_like]` section.
"""
from __future__ import annotations

from outreach.insights.similar import InterviewProfile, reason_text, similarity
from outreach.ranking.confidence import Assessment
from outreach.ranking.facts import RankFacts


class InterviewLike:
    name = "interview_like"

    def __init__(self, params: dict, profile: InterviewProfile):
        self.params = params
        self.profile = profile

    def score(self, facts: RankFacts) -> Assessment:
        p = self.params
        m = similarity(facts.company_id, facts.features, self.profile, min_matches=p["min_matches"],
                       min_companies=p["min_interview_companies"])
        return Assessment(m.value, reason_text(m, self.profile, min_companies=p["min_interview_companies"]),
                          self.profile.confidence)
