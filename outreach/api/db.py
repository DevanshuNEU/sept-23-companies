"""Store access for one request: its own connection, opened and closed inside the endpoint body.

Never a FastAPI dependency: a sync dependency and its endpoint can run on different threads, and a `sqlite3`
connection is bound to the thread that opened it. No connection outlives its request, so no transaction does either.
"""
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import Request

from outreach.api.errors import ApiProblem
from outreach.api.settings import Settings
from outreach.store import connect

BUSY_MESSAGE = "The store is busy right now. Nothing was saved. Try again in a moment."
RETRY_AFTER = {"Retry-After": "1"}


def connect_for(settings: Settings) -> sqlite3.Connection:
    """A new connection to the store in `settings`, waiting at most the configured busy timeout for a lock."""
    conn = connect(settings.db_path)
    conn.execute(f"PRAGMA busy_timeout = {int(settings.busy_timeout_ms)}")
    return conn


def open_conn(request: Request) -> sqlite3.Connection:
    """A new connection for this request."""
    return connect_for(request.app.state.settings)


def is_busy(err: BaseException) -> bool:
    """True when SQLite gave up waiting for a lock (the sender or a job holds the store)."""
    if not isinstance(err, sqlite3.OperationalError):
        return False
    code = getattr(err, "sqlite_errorcode", None)
    if code is not None:
        return code & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    return "locked" in str(err) or "busy" in str(err)


def busy_problem() -> ApiProblem:
    return ApiProblem("busy", BUSY_MESSAGE, headers=dict(RETRY_AFTER))


@contextmanager
def read_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """A read: one connection in one deferred transaction, so every query sees the same snapshot.

    Rolled back and closed on exit, whatever happened. A locked store is the `busy` envelope, never a 500.
    """
    conn = None
    try:
        conn = open_conn(request)
        conn.execute("BEGIN")
        yield conn
    except sqlite3.OperationalError as err:
        if is_busy(err):
            raise busy_problem() from err
        raise
    finally:
        if conn is not None:
            try:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
            finally:
                conn.close()
