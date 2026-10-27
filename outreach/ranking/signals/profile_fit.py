"""profile_fit: a small lift toward companies whose own pages show they build what Devanshu builds (Part of #6 and #15).

One way: the value is how far the profile judge's fit is above `[profile_fit] floor` (50), as a share of the way to 100 (a
fit of 50 or less earns nothing, 75 earns half, 100 earns all), from 0 to 1, so the signal only adds points and a weak
fit never beats an unjudged company. It never
lowers a score, and `decide_hide` never sees it, so it cannot hide a company or write a no-go. A company with no fresh
judgment (none, or older than `[profile_fit] max_age_days`) gets 0 and the reason "Not judged yet". The reason is the
judge's own words: what the company builds and why it fits. Like the interview tilt it grades itself: medium when the
judge read the company's pages and quoted them, low when the evidence was thin, never high (a model read it). The
judgment is read once per run by `facts.load_facts` (`judged.load_judgments`); no model is called at rank time.
`params` is the `[profile_fit]` section.
"""
from __future__ import annotations

from outreach.ranking.confidence import Assessment, Confidence
from outreach.ranking.facts import RankFacts

NOT_JUDGED = "Not judged yet"


class ProfileFit:
    name = "profile_fit"

    def __init__(self, params: dict):
        self.params = params

    def score(self, facts: RankFacts) -> Assessment:
        j = facts.judgment
        if j is None:
            return Assessment(0.0, NOT_JUDGED, Confidence("low", "not judged yet"))
        reason = f"Judge, fit {j.fit}: {j.builds} {j.why}"
        if j.thin:
            confidence = Confidence("low", "the judge read little of the company's pages")
        else:
            confidence = Confidence("medium", f"the judge quoted the company's pages ({len(j.quotes)} quotes)")
        floor = self.params["floor"]
        return Assessment(max(0.0, (j.fit - floor) / (100 - floor)), reason, confidence)
