"""The `rules` stage (#30 owns it): re-decide every open posting, then keep what a verified reading decided.

A rebuild alone resets every `provisional` flag to 1. When the reader exists (#32, `outreach.reader.combine`) the
stage passes its combiner into the rebuild and then runs `apply_all`, so a decision a verified reading made is still
final afterwards. Until the reader exists the stage is a plain `rebuild(conn)`.
"""
from __future__ import annotations

import importlib

from outreach.rules.rebuild import rebuild

__all__ = ["register"]

_COMBINE = "outreach.reader.combine"


def _reader_combine():
    """The reader's combine module, or None while it does not exist. A module that exists but fails to import
    raises: a broken reader must not look like a missing one."""
    try:
        return importlib.import_module(_COMBINE)
    except ModuleNotFoundError as err:
        if err.name not in (_COMBINE, "outreach.reader"):
            raise
        return None


def register(harness) -> None:
    conn = harness.conn

    def rules() -> None:
        combine = _reader_combine()
        if combine is None:
            rebuild(conn)
            return
        rebuild(conn, combiner=combine.reader_combiner(conn))
        combine.apply_all(conn)

    harness.stages.append(("rules", rules))
