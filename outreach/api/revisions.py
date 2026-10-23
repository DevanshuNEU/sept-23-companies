"""Revision-checked updates: a write lands only if the row is still at the revision the page loaded.

`bump` builds its UPDATE from a fixed allow-list of tables and their key columns, and checks every column name
against the table itself, so no caller can turn it into SQL of their own.
"""
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass

from outreach.api.errors import ApiProblem
from outreach.store.timefmt import utcnow

_NAME = re.compile(r"[a-z_][a-z0-9_]*\Z")


@dataclass(frozen=True)
class Rule:
    key: str  # the column a page names the row by (`rowid` for daily_list_entry, as `plan_entry_id`)
    what: str  # how a message names the row: "The company changed since you loaded it."
    stamps_updated_at: bool


ALLOWED: dict[str, Rule] = {
    "company": Rule("company_id", "company", False),
    "person": Rule("person_id", "person", True),
    "touch": Rule("touch_id", "email thread", True),
    "message": Rule("message_id", "draft", True),
    "linkedin_draft": Rule("draft_id", "note", True),
    "outreach_touch": Rule("touch_id", "touch", False),
    "application": Rule("application_id", "application", False),
    "review_item": Rule("review_id", "review item", False),
    "daily_list_entry": Rule("rowid", "plan row", False),
    "sender": Rule("sender_id", "sender", True),
}
_OWNED = {"revision", "updated_at"}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def bump(
    conn: sqlite3.Connection,
    table: str,
    key_col: str,
    key: int,
    revision: int,
    sets: Mapping[str, object],
    *,
    at: str | None = None,
) -> int:
    """Set `sets` on one row and move its revision on by one, only if it is still at `revision`.

    Run it inside the write's transaction. Returns the new revision. Zero rows changed is `not_found` when the row
    is absent and `conflict` when it moved on. `at` stamps `updated_at` where the table has one (default now).
    A table, key or column outside the allow-list is a programming error (ValueError), never a client error.
    """
    rule = ALLOWED.get(table)
    if rule is None:
        raise ValueError(f"bump does not update {table!r}; allowed tables: {sorted(ALLOWED)}")
    if key_col != rule.key:
        raise ValueError(f"{table} rows are named by {rule.key}, not {key_col!r}")
    columns = _columns(conn, table)
    for name in sets:
        if not isinstance(name, str) or not _NAME.match(name) or name not in columns:
            raise ValueError(f"{table} has no column {name!r}")
        if name in _OWNED or name == rule.key:
            raise ValueError(f"bump sets {name!r} itself; it cannot be in sets")
    assignments = [f"{name} = ?" for name in sets] + ["revision = revision + 1"]
    params: list[object] = list(sets.values())
    if rule.stamps_updated_at:
        assignments.append("updated_at = ?")
        params.append(at or utcnow())
    cur = conn.execute(
        f"UPDATE {table} SET {', '.join(assignments)} WHERE {rule.key} = ? AND revision = ?",
        (*params, key, revision),
    )
    if cur.rowcount == 1:
        return revision + 1
    if conn.execute(f"SELECT 1 FROM {table} WHERE {rule.key} = ?", (key,)).fetchone() is None:
        raise ApiProblem("not_found", f"There is no {rule.what} {key}.")
    raise ApiProblem("conflict", f"The {rule.what} changed since you loaded it.")
