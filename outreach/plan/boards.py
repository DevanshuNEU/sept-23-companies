"""The crawler and ranker hooks of the plan job (#15, Task 10).

The plan never waits on either: a hook that is absent (the module does not import) is `None`, and a hook that fails is
recorded by the job and skipped. The job receives the hooks as arguments, so a test passes fakes and nothing here runs
a crawl by itself.
"""
from __future__ import annotations

import importlib
import sqlite3
from collections.abc import Callable, Sequence

__all__ = ["BoardsReadError", "BoardsReader", "Ranker", "crawler_boards_reader", "rank_runner"]

BoardsReader = Callable[[Sequence[int]], None]
Ranker = Callable[[sqlite3.Connection], object]


class BoardsReadError(RuntimeError):
    """The crawler's `read` command did not finish cleanly."""


def crawler_boards_reader() -> BoardsReader | None:
    """A reader that runs the crawler's `read --company <ids>` command, or None when the crawler's command line is not
    there. The command refuses outside the live checkout (`SystemExit`) and exits non-zero when a board was refused or
    failed; both become `BoardsReadError`."""
    try:
        main = getattr(importlib.import_module("outreach.crawl.__main__"), "main", None)
    except ImportError:
        return None
    if not callable(main):
        return None

    def read(company_ids: Sequence[int]) -> None:
        argv = ["read", "--company", *(str(c) for c in company_ids)]
        try:
            code = main(argv)
        except SystemExit as stop:
            raise BoardsReadError(f"the crawler refused to run: {stop.code}") from None
        if code:
            raise BoardsReadError(f"the crawler's read command exited with {code}")

    return read


def rank_runner() -> Ranker | None:
    """`outreach.ranking.job.run_rank` when the ranker imports, else None."""
    try:
        run_rank = getattr(importlib.import_module("outreach.ranking.job"), "run_rank", None)
    except ImportError:
        return None
    return run_rank if callable(run_rank) else None
