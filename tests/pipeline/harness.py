"""The pipeline harness: one scratch store, the fixtures, and the ordered stages every stream registers (#30 Task 6).

Registration convention (stated once; every stream follows it). Each stream owns one module
`tests/pipeline/stages_<stream>.py` with a module-level `register(harness: PipelineHarness) -> None` that appends its
stage or stages to `harness.stages`, each as `(name, callable)`. A stage's callable takes no arguments: `register`
closes over the harness it was given. The streams and their stage names:

  stages_sources.py  (#31)  discover, resolve, find_boards
  stages_crawl.py    (#21)  read_boards
  stages_reader.py   (#32)  read_postings
  stages_rules.py    (#30)  rules
  stages_ranking.py  (#6)   rank
  stages_plan.py     (#15)  plan

`STREAMS` lists the modules in stage order and `PipelineHarness()` imports each one that exists and calls its
`register`, so a stream's PR adds its one file and edits nothing else in this folder except `test_flow.py` (its
invariant). An import of a missing stream module is skipped; a stream module that exists but fails to import is an
error. A stream may only append stage names from `STAGE_NAMES`, in order after the stages already registered.
"""
from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path

from tests.storetest import fresh_store

__all__ = ["STAGE_NAMES", "STREAMS", "PipelineHarness"]

STAGE_NAMES = ("discover", "resolve", "find_boards", "read_boards", "read_postings", "rules", "rank", "plan")
STREAMS = ("sources", "crawl", "reader", "rules", "ranking", "plan")

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "pipeline"


class PipelineHarness:
    """`.conn` a scratch store, `.fixture(name)` a path under `tests/fixtures/pipeline/`, `.stages` the ordered
    `(name, callable)` list. `register_streams=False` gives an empty harness for tests of the harness itself."""

    def __init__(self, *, register_streams: bool = True):
        self.conn = fresh_store()
        self.stages: list[tuple[str, Callable[[], object]]] = []
        if register_streams:
            try:
                for stream in STREAMS:
                    self._register(stream)
            except BaseException:
                self.close()
                raise

    def close(self) -> None:
        self.conn.close()

    def fixture(self, name: str) -> Path:
        path = FIXTURES / name
        if not path.exists():
            raise FileNotFoundError(f"no pipeline fixture {name!r} in {FIXTURES}")
        return path

    def run(self, through: str | None = None) -> None:
        """Run every registered stage in order, stopping after the one named `through` (all of them by default).
        Raises ValueError when `through` is not a registered stage."""
        names = [name for name, _ in self.stages]
        if through is not None and through not in names:
            raise ValueError(f"stage {through!r} is not registered; registered: {names}")
        for name, stage in self.stages:
            stage()
            if name == through:
                break

    def _register(self, stream: str) -> None:
        module_name = f"tests.pipeline.stages_{stream}"
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as err:
            if err.name != module_name:
                raise
            return
        before = len(self.stages)
        module.register(self)
        last = max((STAGE_NAMES.index(n) for n, _ in self.stages[:before]), default=-1)
        for name, _ in self.stages[before:]:
            if name not in STAGE_NAMES:
                raise ValueError(f"{module_name} registered {name!r}, which is not a pipeline stage: {STAGE_NAMES}")
            if STAGE_NAMES.index(name) <= last:
                raise ValueError(f"{module_name} registered {name!r} out of order: stages run as {STAGE_NAMES}")
            last = STAGE_NAMES.index(name)
