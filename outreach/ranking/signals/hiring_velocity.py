"""hiring_velocity: a company that posted engineering roles lately is hiring now, so a note lands (P5).

The count is `RankFacts.eng_posted_30d`, loaded in bulk by `facts.py`: open, visible engineering postings with a
posted date in the window, plus undated ones first seen in the window on a board first read before it. The value is
the one of the largest `min_roles` count reached. One way: 0 to 1, so it only adds points, and `decide_hide` never
sees it. A large or later-stage company (`facts.is_large`), or one with over `many_postings` open roles, earns 0: many
roles there say nothing about a small team hiring. `params` is the `[hiring_velocity]` section, `small` the `[small]`
section (its stage lists and borderline headcount).
"""
from __future__ import annotations

from outreach.ranking.confidence import Assessment
from outreach.ranking.facts import RankFacts, is_large


class HiringVelocity:
    name = "hiring_velocity"

    def __init__(self, params: dict, small: dict, many_postings: int):
        self.params = params
        self.small = small
        self.many = many_postings

    def score(self, facts: RankFacts) -> Assessment:
        grade = facts.grade(self.name)
        if is_large(facts.stage, facts.size_low, self.small):
            return Assessment(0.0, "Hiring pace not counted: a large or later-stage company", grade)
        if facts.open_postings > self.many:
            return Assessment(0.0, f"Hiring pace not counted: {facts.open_postings} open roles", grade)
        n, days = facts.eng_posted_30d, self.params["window_days"]
        if n <= 0:
            return Assessment(0.0, f"No engineering role posted in the last {days} days", grade)
        value = 0.0
        for least, v in zip(self.params["min_roles"], self.params["values"]):
            if n >= least:
                value = float(v)
        noun = "role" if n == 1 else "roles"
        return Assessment(value, f"{n} engineering {noun} posted in the last {days} days", grade)
