"""One write unit that works with or without the caller's transaction, and how a failure is written in a report."""
from __future__ import annotations

import contextlib
import re
import sqlite3

from outreach.store.db import transaction

__all__ = ["atomic", "describe_error"]

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def describe_error(err: BaseException) -> str:
    """`TypeError: text`: how a failure is written in a report, a `failed` list or `job_run.error`."""
    return f"{type(err).__name__}: {err}"


@contextlib.contextmanager
def atomic(conn: sqlite3.Connection, name: str):
    """A write unit: a transaction of its own when none is open, else a savepoint called `name` inside the caller's.

    A failure undoes only this unit and is re-raised. SQLite may have rolled the whole transaction back already (a
    full disk, an interrupt); then there is nothing left to undo and the original error still propagates.
    """
    if not _NAME.match(name):
        raise ValueError(f"savepoint name {name!r} is not a plain identifier")
    if not conn.in_transaction:
        with transaction(conn):
            yield
        return
    conn.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        if conn.in_transaction:
            conn.execute(f"ROLLBACK TO {name}")
            conn.execute(f"RELEASE {name}")
        raise
    conn.execute(f"RELEASE {name}")
