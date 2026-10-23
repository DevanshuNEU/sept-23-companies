"""The `rank` stage (#6 owns it): score every listed company in the store, after the crawl and rules stages.

The earlier stages leave only a few companies (the sources fixture and the one crawled board), so this stage first
seeds the ranking fixture (`tests/fixtures/ranking/`, employers A to U with their boards, postings and evidence),
once: the seeder runs the real `rebuild`, so every decision comes from the real rules. Then it runs `run_rank` with
the fixture day as `today` (2026-10-21) and the shipped config, the real `load_stale` and the shipped 12 months.

`register` leaves the stage on the harness as `harness.ranking` (a `RankStage`): `.ids` is the fixture companies
(key to company id, filled when the stage first runs) and `.report` the last `RankRun`. The registered callable seeds
if the fixture is absent and then ranks, so calling it again is a re-run of the ranker over the same store.
"""
from __future__ import annotations

from datetime import date

from outreach.ranking.job import RankRun, run_rank
from tests.ranking_seed import EMPLOYERS, POSTINGS, fixture_employers, fixture_ids, seed_ranking_fixture

__all__ = ["register", "RankStage", "TODAY"]

TODAY = date(2026, 10, 21)


class RankStage:
    def __init__(self, harness):
        self.harness = harness
        self.conn = harness.conn
        self.ids: dict[str, int] = {}
        self.report: RankRun | None = None

    def seed(self) -> dict[str, int]:
        """The fixture companies, seeded when they are absent. Some but not all present is an error."""
        found = fixture_ids(self.conn)
        if not found:
            found = seed_ranking_fixture(self.conn, EMPLOYERS, POSTINGS)
        elif len(found) != len(fixture_employers()):
            raise AssertionError(f"the ranking fixture is half in the store: {sorted(found)}")
        self.ids = found
        return found

    def run(self) -> RankRun:
        self.seed()
        self.report = run_rank(self.conn, today=TODAY)
        return self.report


def register(harness) -> None:
    stage = RankStage(harness)
    harness.ranking = stage
    harness.stages.append(("rank", stage.run))
