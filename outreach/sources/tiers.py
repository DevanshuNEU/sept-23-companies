"""Work tiers (#31 Task 8, design 5.1): which companies get board finding first.

Tier 1 is worked first, tier 2 next, tier 3 (staffing-flagged) only when promoted. `work_tier` holds one row per
company; a company with no row (an HN post, a by-hand company, anything no tiered source saw) counts as tier 1.
`promote` is Devanshu's override and a later source run never clears it.
"""
from __future__ import annotations

import sqlite3

from outreach.store import append_event, atomic, utcnow

__all__ = ["set_work_tier", "effective_tier", "promote", "register_source"]


def set_work_tier(conn: sqlite3.Connection, company_id: int, tier: int, reason: str, source_version: str) -> bool:
    """Set the tier a source assigned. Writes only when the tier or the reason changed (a re-run changes nothing,
    not even `set_at`); returns whether it wrote. A write appends a `tier_changed` event (actor `source`) holding the
    old tier (None for a first one), the new tier, the reason and the source version. `promoted` is never touched.
    `sqlite3.IntegrityError` for a tier outside 1 to 3 or a blank reason. The row and its event are one unit: a
    transaction when none is open, else a savepoint in the caller's."""
    with atomic(conn, "work_tier_write"):
        old = conn.execute("SELECT tier FROM work_tier WHERE company_id = ?", (company_id,)).fetchone()
        cur = conn.execute(
            "INSERT INTO work_tier(company_id, tier, reason, source_version, set_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(company_id) DO UPDATE SET tier = excluded.tier, reason = excluded.reason, "
            "source_version = excluded.source_version, set_at = excluded.set_at "
            "WHERE work_tier.tier != excluded.tier OR work_tier.reason != excluded.reason",
            (company_id, tier, reason, source_version, utcnow()))
        if cur.rowcount == 0:
            return False
        append_event(conn, entity="company", kind="tier_changed", actor="source", entity_id=company_id,
                     company_id=company_id,
                     detail={"from": old["tier"] if old else None, "to": tier, "reason": reason,
                             "version": source_version})
        return True


def effective_tier(conn: sqlite3.Connection, company_id: int) -> int:
    """1 when promoted, the stored tier otherwise, 1 when there is no row."""
    row = conn.execute("SELECT tier, promoted FROM work_tier WHERE company_id = ?", (company_id,)).fetchone()
    if row is None or row["promoted"]:
        return 1
    return row["tier"]


def promote(conn: sqlite3.Connection, company_id: int, actor: str) -> None:
    """Work this company as tier 1 whatever a source says. Sets `promoted` and appends one event; promoting a
    company already promoted writes nothing. A company with no tier row gets one (tier 1, reason naming the actor),
    so a later source run that says tier 3 still cannot take the promotion away. Atomic like `set_work_tier`."""
    now = utcnow()
    with atomic(conn, "work_tier_write"):
        conn.execute(
            "INSERT INTO work_tier(company_id, tier, promoted, reason, source_version, set_at) "
            "VALUES (?, 1, 0, ?, '', ?) ON CONFLICT(company_id) DO NOTHING",
            (company_id, f"promoted by {actor}", now))
        cur = conn.execute("UPDATE work_tier SET promoted = 1 WHERE company_id = ? AND promoted = 0", (company_id,))
        if cur.rowcount:
            append_event(conn, entity="company", kind="promoted", actor=actor, entity_id=company_id,
                         company_id=company_id)


def register_source(conn: sqlite3.Connection, name: str, version: str, run_id: int) -> None:
    """Record the version of a source and the run that used it (`source` has one row per source)."""
    conn.execute(
        "INSERT INTO source(source_name, version, last_run_id) VALUES (?, ?, ?) "
        "ON CONFLICT(source_name) DO UPDATE SET version = excluded.version, last_run_id = excluded.last_run_id",
        (name, version, run_id))
