"""Open the store, and refuse to open the live one from anywhere but the live checkout.

`connect` never creates a store and never migrates one: `outreach.install` is the only
code that does either. The guard compares paths by what they point at, not by how they
are spelled, and runs before this module touches the filesystem, so a refused call
creates nothing.
"""
import os
import sqlite3
import unicodedata
from contextlib import contextmanager
from pathlib import Path

import live_checkout
from outreach.store.migrate import latest_version
from outreach.store.timefmt import iso_date, iso_utc, utcnow

__all__ = [
    "DEFAULT_PATH", "StoreError", "connect", "db_path", "guard", "is_live_path",
    "transaction", "utcnow", "iso_utc", "iso_date",
]

DEFAULT_PATH = os.path.expanduser("~/Library/Application Support/outreach/outreach.db")

INSTALL_COMMAND = "uv run python -m outreach.install"


class StoreError(RuntimeError):
    """The store is missing, older or newer than this code."""


def normalise(path: str) -> str:
    """Expand `~`, make absolute against the working folder, and resolve links."""
    return os.path.realpath(os.path.abspath(os.path.expanduser(str(path))))


def db_path() -> str:
    """`OUTREACH_DB` if set, else the default location, normalised."""
    return normalise(os.environ.get("OUTREACH_DB") or DEFAULT_PATH)


def _fold(path: str) -> str:
    # APFS ignores letter case and Unicode normalisation form, so two spellings that differ
    # only that way are one file.
    return unicodedata.normalize("NFD", path).casefold()


def is_live_path(path: str) -> bool:
    """True when `path` is the default store file under any spelling."""
    candidate, live = normalise(path), normalise(DEFAULT_PATH)
    if _fold(candidate) == _fold(live):
        return True
    return os.path.exists(candidate) and os.path.exists(live) and os.path.samefile(candidate, live)


def guard(path: str) -> None:
    """Exit unless this is the live checkout, when `path` is the live store."""
    if is_live_path(path):
        live_checkout.require_live("opening the live store")


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open an installed store, read-write. Raises StoreError if there is none or the version is wrong."""
    target = normalise(path) if path is not None else db_path()
    guard(target)
    try:
        conn = sqlite3.connect(Path(target).as_uri() + "?mode=rw", uri=True, isolation_level=None)
    except sqlite3.OperationalError as err:
        if not os.path.exists(target):
            raise StoreError(f"no store at {target}; run: {INSTALL_COMMAND}") from err
        raise
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # FULL, not the WAL default of NORMAL: NORMAL can lose the last commit on power loss,
        # and a lost send claim could mean a second send.
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA journal_size_limit = 67108864")
        have = conn.execute("PRAGMA user_version").fetchone()[0]
        want = latest_version()
        if have < want:
            raise StoreError(f"store at {target} is at version {have}, code needs {want}; run: {INSTALL_COMMAND}")
        if have > want:
            raise StoreError(
                f"store at {target} is at version {have}, newer than the {want} this code knows; "
                f"this code is older than the store"
            )
    except BaseException:
        conn.close()
        raise
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """One write transaction: BEGIN IMMEDIATE, COMMIT, or ROLLBACK on any exception."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
