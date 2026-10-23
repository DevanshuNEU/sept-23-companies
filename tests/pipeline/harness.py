"""The pipeline harness: one scratch store, the fixtures, and the ordered stages every stream registers (#30 Task 6).

Registration convention (stated once; every stream follows it). A stream adds exactly one file,
`tests/pipeline/stages_<stream>.py`, with a module-level `register(harness: PipelineHarness) -> None` that appends its
stage or stages to `harness.stages`, each as `(name, callable)`, and one `@invariant("<stage>")` function in
`tests/pipeline/test_flow.py`. It edits nothing else in `tests/pipeline/`: `STREAM_STAGES` below already names every
stream, so the harness finds the module by its name. A stage's callable takes no arguments: `register` closes over the
harness it was given. A stage runs on the fixtures in `tests/fixtures/pipeline/` and its invariant counts only the rows
its own stage made or its own fixture companies, never a global total.

`STREAM_STAGES` maps each stream to the stage names it may register, in stage order:

  importer (#17)  import_history   (first: history before discovery)
  sources  (#31)  discover, resolve, find_boards
  crawl    (#21)  read_boards
  reader   (#32)  read_postings
  rules    (#30)  rules
  ranking  (#6)   rank
  plan     (#15)  plan
  mcp      (#13)  draft            (Claude drafts after the plan)
  sending  (#14)  send             (only what was approved)
  review   (#16)  needs_you        (last: sweeps what every stage left)

`PipelineHarness()` imports each `tests.pipeline.stages_<stream>` that exists, in that order, and calls its `register`.
An import of a missing stream module is skipped; a stream module that exists but fails to import is an error. A stream
that registers a name it does not own, or one out of pipeline order, is an error. A flow test runs every registered
stage up to and including its own, in order, on a fresh harness.
"""
from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path

from tests.storetest import fresh_store

__all__ = ["STAGE_NAMES", "STREAM_STAGES", "PipelineHarness"]

STREAM_STAGES = {
    "importer": ("import_history",),                   # #17, first: history before discovery
    "sources": ("discover", "resolve", "find_boards"),  # #31
    "crawl": ("read_boards",),                         # #21
    "reader": ("read_postings",),                      # #32
    "rules": ("rules",),                               # #30
    "ranking": ("rank",),                              # #6
    "plan": ("plan",),                                 # #15
    "mcp": ("draft",),                                 # #13: Claude drafts after the plan
    "sending": ("send",),                              # #14: only what was approved
    "review": ("needs_you",),                          # #16: last, sweeps what every stage left
}
STAGE_NAMES = tuple(name for names in STREAM_STAGES.values() for name in names)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "pipeline"


class PipelineHarness:
    """`.conn` a scratch store, `.fixture(name)` a path under `tests/fixtures/pipeline/`, `.stages` the ordered
    `(name, callable)` list. `register_streams=False` gives an empty harness for tests of the harness itself."""

    def __init__(self, *, register_streams: bool = True):
        self.conn = fresh_store()
        self.stages: list[tuple[str, Callable[[], object]]] = []
        if register_streams:
            try:
                for stream in STREAM_STAGES:
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
            if name not in STREAM_STAGES[stream]:
                raise ValueError(f"{module_name} registered {name!r}; the {stream} stream may register only "
                                 f"{STREAM_STAGES[stream]}")
            if STAGE_NAMES.index(name) <= last:
                raise ValueError(f"{module_name} registered {name!r} out of order: stages run as {STAGE_NAMES}")
            last = STAGE_NAMES.index(name)
