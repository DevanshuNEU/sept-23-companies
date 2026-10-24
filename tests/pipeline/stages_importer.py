"""The `import_history` stage (#17 owns it): his history goes in first, before discovery.

The stage reads the pipeline import fixtures under `tests/fixtures/pipeline/import/` (two made-up companies: Heron
Works, whose engineer replied and then interviewed, and Pebble Forge, emailed once from contacted.txt) through the
importer's own readers, builds the plan and applies it to the harness store. It uses three of the ten sources: the
flow test checks the writer on a store other stages share, while the count report's every-source check is the
importer's own tests' job. No board and no posting, so later stages' board counts are their own.

`register` leaves `harness.import_plan`, `harness.import_result` and `harness.import_now` for the invariant.
"""
from __future__ import annotations

from outreach.importer.manifest import Overrides
from outreach.importer.plan import build_plan
from outreach.importer.read_notes import read_devvault_notes, read_interview_prep
from outreach.importer.read_text import read_contacted_txt
from outreach.importer.write import apply_plan
from outreach.sending.config import load_config

__all__ = ["register", "NOW"]

NOW = "2026-10-20T12:00:00Z"


def register(harness) -> None:
    folder = harness.fixture("import")

    def import_history() -> None:
        records = [*read_contacted_txt(folder / "contacted.txt"),
                   *read_devvault_notes(folder / "companies"),
                   *read_interview_prep(folder / "interview-prep" / "companies")]
        plan = build_plan(records, Overrides({}, frozenset(), {}, frozenset(), frozenset(), {}), now=NOW,
                          send_cfg=load_config())
        result = apply_plan(harness.conn, plan, now=NOW)
        if result.status != "ok":
            raise RuntimeError(f"the import ended {result.status}: {result.error}")
        harness.import_plan, harness.import_result, harness.import_now = plan, result, NOW

    harness.stages.append(("import_history", import_history))
