"""Shared fixtures for tests that need a migrated store.

`fresh_store()` is the only way a test gets a store. Seed helpers add the
smallest valid rows so each test states only what it is about.
"""
import atexit
import hashlib
import itertools
import shutil
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from outreach.install import install
from outreach.store import connect
from outreach.store.timefmt import utcnow

_counter = itertools.count(1)


def fresh_store() -> sqlite3.Connection:
    """A migrated store in a new temp directory.

    Built by `install` and opened by `connect`, so tests run on the real connection
    settings: autocommit, `sqlite3.Row` rows, foreign keys on. The folder is removed
    when the test process exits.
    """
    folder = tempfile.mkdtemp(prefix="outreach-store-test-")
    atexit.register(shutil.rmtree, folder, True)
    path = str(Path(folder) / "outreach.db")
    install(path)
    return connect(path)


# The 14 ATSs the crawler plan builds an adapter for (#21). The other board.ats values (gem, icims, cornerstone and
# so on) have none, so #31 gives a company that only has those a watched careers page.
PLANNED_ADAPTERS = frozenset({
    "greenhouse", "lever", "ashby", "smartrecruiters", "workday", "workable", "oracle_orc", "rippling", "breezy",
    "pinpoint", "teamtailor", "recruitee", "bamboohr", "jazzhr"})


def planned_adapters(case: unittest.TestCase) -> None:
    """For the test's length, `outreach.crawl.ats.supported_ats()` names the 14 `PLANNED_ADAPTERS`.

    The crawler is built one adapter at a time, so the real set grows with every task and a #31 test that depended on
    it would change with each. Pinning the plan's end state keeps #31's last find_boards pass (a company whose boards
    all lack an adapter gets a watched page, which reads its careers page) the same in every task. The test must
    route the careers pages of the companies that pass reaches. A test of the pass that needs another set patches
    `sys.modules` itself, inside this pin.
    """
    fake = types.SimpleNamespace(supported_ats=lambda: PLANNED_ADAPTERS)
    patch = mock.patch.dict(sys.modules, {"outreach.crawl.ats": fake})
    patch.start()
    case.addCleanup(patch.stop)


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
        "INSERT INTO posting(company_id, source_key, title, location, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (company_id, f"url:seed-{next(_counter)}", title, location, now, now),
    )
    if body:
        conn.execute("INSERT INTO posting_body(posting_id, body) VALUES (?, ?)", (cur.lastrowid, body))
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


MARKED_TABLES = ("company", "company_alias", "company_name_key", "company_source", "evidence", "board",
                 "review_item", "event", "watched_page", "work_tier", "company_post", "shared_key")


def _rows_hash(conn: sqlite3.Connection, table: str, order: str) -> int:
    """A stable 60-bit hash of every row in order, for a table with no rowid to mark."""
    text = repr([tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")])
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:15], 16)


def table_marks(conn: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    """(row count, max rowid) for each table a source run can write; for `company_name_key` (WITHOUT ROWID) the second
    number is a hash of its ordered rows, so a delete plus an insert, or a changed `strong`, shows. Idempotency tests
    compare two snapshots: equal means nothing was added or deleted (an update in place, such as
    `company_source.last_seen_at`, keeps both)."""
    marks = {}
    for t in MARKED_TABLES:
        if t == "company_name_key":
            marks[t] = (conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0],
                        _rows_hash(conn, t, "name_key, company_id"))
        else:
            marks[t] = tuple(conn.execute(f"SELECT COUNT(*), coalesce(MAX(rowid), 0) FROM {t}").fetchone())
    return marks
