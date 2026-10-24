"""The `needs_you` stage (#16 owns it): the last stage, which sweeps what every stage before it left.

It runs the real `sweep_needs_you` on the harness store with the wall clock, as launchd does every 30 minutes. It
seeds nothing: the sweep reads the plan, the rules' decisions and the queued email the earlier stages made. Its
invariant is `needs_you_items_obey_the_contract_and_a_second_sweep_changes_nothing` in `test_flow.py`.
"""
from __future__ import annotations

from outreach.review.triggers import sweep_needs_you

__all__ = ["register"]


def register(harness) -> None:
    harness.stages.append(("needs_you", lambda: sweep_needs_you(harness.conn)))
