"""Shared fixtures for tests that need a migrated store.

`fresh_store()` is the only way a test gets a store. Seed helpers add the
smallest valid rows so each test states only what it is about.
"""
import atexit
import itertools
import shutil
import sqlite3
import tempfile
from pathlib import Path

from outreach.store.migrate import migrate
from outreach.store.timefmt import utcnow

_counter = itertools.count(1)


def fresh_store() -> sqlite3.Connection:
    """A migrated store in a new temp directory.

    Autocommit (`isolation_level=None`), `sqlite3.Row` rows and foreign keys on.
    The folder is removed when the test process exits.
    """
    folder = tempfile.mkdtemp(prefix="outreach-store-test-")
    atexit.register(shutil.rmtree, folder, True)
    conn = sqlite3.connect(Path(folder) / "outreach.db", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def seed_company(conn: sqlite3.Connection, name: str = "Acme") -> int:
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO company(name, status_at, created_at) VALUES (?, ?, ?)",
        (name, now, now),
    )
    return cur.lastrowid


def seed_posting(
    conn: sqlite3.Connection,
    company_id: int,
    title: str,
    body: str = "",
    location: str = "",
) -> int:
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO posting(company_id, source_key, title, location, body, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (company_id, f"url:seed-{next(_counter)}", title, location, body, now, now),
    )
    return cur.lastrowid


def seed_touch(conn: sqlite3.Connection, company_id: int, email: str) -> int:
    """A person at the company with this address, and the touch for them. Returns the touch id."""
    now = utcnow()
    person_id = conn.execute(
        "INSERT INTO person(company_id, name, role_kind, email, channel, added_by, created_at, updated_at) "
        "VALUES (?, ?, 'engineer', ?, 'email', 'devanshu', ?, ?)",
        (company_id, email.split("@")[0], email, now, now),
    ).lastrowid
    cur = conn.execute(
        "INSERT INTO touch(person_id, created_at, updated_at) VALUES (?, ?, ?)",
        (person_id, now, now),
    )
    return cur.lastrowid
