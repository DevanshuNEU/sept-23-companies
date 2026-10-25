"""The `discover`, `resolve` and `find_boards` stages (#31 owns them): the sources stream on the pipeline fixtures.

`discover` reads the LCA extract (3 small software employers: one with its own FEIN, two sharing a FEIN under
different names and states), `resolve` runs the identity pass, and `find_boards` looks each company up in the stapply
fixture (one Greenhouse row, for the own-FEIN employer, whose board list is the fixture the crawler stage reads). No
careers checker is given, so nothing here makes a request.

The extract's file name has no fiscal year, and the LCA reader takes the year from the name, so `discover` reads a
copy made under a DOL-style name (`LCA_Disclosure_Data_FY2025_Q2.csv`; the extract's dates are Q2 of fiscal 2025).
"""
from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path

from outreach.sources.jobs import run_discover, run_find_boards, run_resolve
from outreach.sources.lca import LcaSource
from outreach.sources.stapply import StapplyDirectory

__all__ = ["register"]


def register(harness) -> None:
    conn = harness.conn
    extract = harness.fixture("lca_extract.csv")
    folder = Path(tempfile.mkdtemp(prefix="outreach-pipeline-lca-"))
    atexit.register(shutil.rmtree, folder, True)
    named = folder / "LCA_Disclosure_Data_FY2025_Q2.csv"
    shutil.copyfile(extract, named)

    def discover() -> None:
        run_discover(conn, [LcaSource([named])])

    def resolve() -> None:
        run_resolve(conn)

    def find_boards() -> None:
        run_find_boards(conn, directory=StapplyDirectory.load(harness.fixture("stapply")), careers=None)

    harness.stages += [("discover", discover), ("resolve", resolve), ("find_boards", find_boards)]
