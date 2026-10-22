"""The one query of due follow-ups, and the merged-company walk it needs.

Standard library only and nothing from `outreach.sending`: the sender (#14) and the web server (#13) both import
it, so there is one copy of "which follow-ups are due".
"""
import sqlite3
from datetime import date, timedelta

__all__ = ["canonical_company", "company_map", "resolve_company", "held_survivors", "due_followups"]


def canonical_company(conn: sqlite3.Connection, company_id: int) -> int:
    """The surviving company: `company_id` followed through `merged_into` to its end. A cycle raises RuntimeError."""
    seen = {company_id}
    current = company_id
    while True:
        row = conn.execute("SELECT merged_into FROM company WHERE company_id = ?", (current,)).fetchone()
        if row is None or row["merged_into"] is None:
            return current
        current = row["merged_into"]
        if current in seen:
            raise RuntimeError(f"company {company_id} is in a merge cycle at {current}")
        seen.add(current)


def company_map(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    """Every company row by id, for resolving many companies with one query."""
    return {row["company_id"]: row for row in conn.execute(
        "SELECT company_id, status, paused_at, held_by_touch, merged_into FROM company")}


def resolve_company(companies: dict[int, sqlite3.Row], company_id: int) -> list[int]:
    """The ids from `company_id` to its survivor, in order (the survivor is last). A cycle raises RuntimeError."""
    chain = [company_id]
    current = companies[company_id]["merged_into"]
    while current is not None:
        if current in chain:
            raise RuntimeError(f"company {company_id} is in a merge cycle at {current}")
        chain.append(current)
        current = companies[current]["merged_into"]
    return chain


def held_survivors(companies: dict[int, sqlite3.Row]) -> set[int]:
    """The surviving companies that are held: a survivor is held when any company that resolves to it has
    `held_by_touch`, so a hold set on a duplicate before it was merged away still holds the survivor."""
    return {resolve_company(companies, cid)[-1] for cid, row in companies.items() if row["held_by_touch"] is not None}


def due_followups(conn: sqlite3.Connection, today: date, *, days_ahead: int = 0) -> list[int]:
    """The `message_id`s of `planned` rows due on or before `today + days_ahead`, oldest `due_on` first.

    Only rows on a touch that is still `open`, whose first email was sent and which has its thread count, and only when the surviving
    company (through `merged_into`) is not held, `closed` or `removed`. A paused company still lists its due
    follow-ups: pausing is Devanshu's, and the sender holds the send, not the draft. Read only.
    """
    horizon = (today + timedelta(days=days_ahead)).isoformat()
    rows = conn.execute(
        "SELECT m.message_id, p.company_id FROM message m "
        "JOIN touch t ON t.touch_id = m.touch_id JOIN person p ON p.person_id = t.person_id "
        "WHERE m.state = 'planned' AND m.due_on <= ? AND t.state = 'open' AND t.first_sent_at IS NOT NULL AND t.gmail_thread_id IS NOT NULL "
        "ORDER BY m.due_on, m.message_id", (horizon,)).fetchall()
    if not rows:
        return []
    companies = company_map(conn)
    held = held_survivors(companies)
    due = []
    for row in rows:
        survivor_id = resolve_company(companies, row["company_id"])[-1]
        if survivor_id not in held and companies[survivor_id]["status"] not in ("closed", "removed"):
            due.append(row["message_id"])
    return due
