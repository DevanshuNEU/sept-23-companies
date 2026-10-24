"""Seeders for the review package's tests: a valid review item with the rows its kind needs."""
import itertools
import sqlite3

from outreach.review.items import raise_item
from outreach.review.kinds import KINDS
from outreach.store.timefmt import utcnow
from tests.storetest import seed_board, seed_company, seed_posting

_keys = itertools.count(1)


def seed_person(conn: sqlite3.Connection, company_id: int, name: str = "Pat") -> int:
    now = utcnow()
    return conn.execute(
        "INSERT INTO person(company_id, name, role_kind, email, channel, added_by, created_at, updated_at) "
        "VALUES (?, ?, 'engineer', ?, 'email', 'devanshu', ?, ?)",
        (company_id, name, f"{name.lower()}@example.com", now, now)).lastrowid


def seed_item(conn: sqlite3.Connection, kind: str = "size_unknown", company_id: int | None = None, **kw) -> int:
    """A valid open item raised through `raise_item`. A kind that needs a board or a posting gets one; every call
    that does not pass `evidence_key` gets a new one, so two calls make two items."""
    if company_id is None:
        company_id = seed_company(conn)
    if kind in ("board_unreadable", "board_empty") and "board_id" not in kw:
        kw["board_id"] = seed_board(conn, company_id, token=f"seed-{next(_keys)}")
    if kind in ("sponsorship_unclear", "reader_disagrees") and "posting_id" not in kw:
        kw["posting_id"] = seed_posting(conn, company_id, "Software Engineer")
    kw.setdefault("reason", f"A seeded {kind} item.")
    kw.setdefault("evidence_key", f"seed-{next(_keys)}")
    kw.setdefault("raised_by", KINDS[kind].raised_by[0])
    return raise_item(conn, kind=kind, company_id=company_id, **kw).review_id
