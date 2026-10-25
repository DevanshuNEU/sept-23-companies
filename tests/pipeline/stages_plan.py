"""The `plan` stage (#15 owns it): Today's plan on top of the ranked list the earlier stages made.

The earlier stages leave a ranked list but no people and no outreach, so the stage first seeds a situation relative to
that list, once (`tests.plan_seed.seed_plan_situation`: a team engineer with a draft at rank 1, a request 5 days old
at rank 2, a request 20 days old and a second candidate at rank 3, an accepted request at rank 4, a reply at rank 5, a
founder with a published address at rank 6, nobody at rank 7, then recruiters and nobody), then runs the real
`build_plan` for `TODAY` with `read_boards=False`: no crawler and no ranker, so no network and no model.

`register` leaves the stage on the harness as `harness.plan` (a `PlanStage`): `.situation` is what was seeded and
`.result` the last `PlanResult`. The registered callable seeds if it has not yet, then builds, so calling it again is
a re-run of the build for the same date, which changes nothing.
"""
from __future__ import annotations

from outreach.plan.build import PlanResult, build_plan
from outreach.ranking.scores import ranked_list
from tests.plan_seed import TODAY, PlanSituation, seed_plan_situation

__all__ = ["register", "PlanStage", "TODAY"]


class PlanStage:
    def __init__(self, harness):
        self.harness = harness
        self.conn = harness.conn
        self.situation: PlanSituation | None = None
        self.result: PlanResult | None = None

    def seed(self) -> PlanSituation:
        if self.situation is None:
            self.situation = seed_plan_situation(self.conn, ranked_list(self.conn, laned_only=True))
        return self.situation

    def run(self) -> PlanResult:
        self.seed()
        self.result = build_plan(self.conn, today=TODAY, read_boards=False)
        return self.result


def register(harness) -> None:
    stage = PlanStage(harness)
    harness.plan = stage
    harness.stages.append(("plan", stage.run))
