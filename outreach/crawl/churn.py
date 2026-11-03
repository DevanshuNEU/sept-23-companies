"""Churn per board (#152, Task 3): how many postings each board closed, opened and listed again over a window.

Store only, nothing sent. The window is `[now - days, now]`. `closed` counts postings whose `closed_at` falls in it,
`new` those whose `first_seen_at` does, `open` the postings open now. `relisted` counts the new ones whose board has
another posting with the same title and location that closed in the 7 days before the new one was first seen: a
board that closes and re-opens the same job shows up here instead of looking like real movement. `us_engineering_open`
is the open postings whose current decision is the engineering lane and not outside the US.

One grouped scan over `posting` finds the boards that moved. The rest (the open counts, the relist check) runs only
for the boards kept by `limit`, each through the `posting_board_open` index, and the decision is joined by its key the
way `ingest._undecided` does, not through the `current_decision` view.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

__all__ = ["BoardChurn", "churn", "RELIST_DAYS"]

RELIST_DAYS = 7
_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class BoardChurn:
    board_id: int
    ats: str
    token: str
    open: int
    closed: int
    new: int
    relisted: int
    us_engineering_open: int


def _back(stamp: str, days: int) -> str:
    return (datetime.strptime(stamp, _FORMAT) - timedelta(days=days)).strftime(_FORMAT)


def churn(conn: sqlite3.Connection, now: str, days: int, limit: int) -> list[BoardChurn]:
    """The `limit` boards that closed the most postings in the last `days` days (ties: more new postings, then board
    id). Only boards with a posting closed or first seen in the window are listed."""
    start = _back(now, days)
    moved = conn.execute(
        "SELECT p.board_id, b.ats, b.token, "
        "COALESCE(SUM(p.closed_at >= :start AND p.closed_at <= :now), 0) AS closed, "
        "COALESCE(SUM(p.first_seen_at >= :start AND p.first_seen_at <= :now), 0) AS new "
        "FROM posting p JOIN board b ON b.board_id = p.board_id "
        "WHERE (p.closed_at >= :start AND p.closed_at <= :now) "
        "OR (p.first_seen_at >= :start AND p.first_seen_at <= :now) "
        "GROUP BY p.board_id", {"start": start, "now": now}).fetchall()
    moved.sort(key=lambda r: (-r["closed"], -r["new"], r["board_id"]))
    kept = moved[:limit]
    if not kept:
        return []
    ids = [r["board_id"] for r in kept]
    marks = ",".join("?" * len(ids))
    open_counts = {r["board_id"]: (r["open"], r["us_eng"]) for r in conn.execute(
        "SELECT p.board_id, COUNT(*) AS open, "
        "COALESCE(SUM(d.lane = 'engineering' AND d.us != 'no'), 0) AS us_eng "
        "FROM posting p LEFT JOIN decision d ON d.posting_id = p.posting_id "
        "AND d.ruleset_version = (SELECT ruleset_version FROM ruleset WHERE is_current = 1) "
        f"WHERE p.board_id IN ({marks}) AND p.closed_at IS NULL GROUP BY p.board_id", ids)}
    return [BoardChurn(
        board_id=r["board_id"], ats=r["ats"], token=r["token"], open=open_counts.get(r["board_id"], (0, 0))[0],
        closed=r["closed"], new=r["new"], relisted=_relisted(conn, r["board_id"], start, now),
        us_engineering_open=open_counts.get(r["board_id"], (0, 0))[1]) for r in kept]


def _relisted(conn: sqlite3.Connection, board_id: int, start: str, now: str) -> int:
    """New postings of the board that match one closed in the 7 days before their first sight."""
    new = conn.execute(
        "SELECT posting_id, title, location, first_seen_at FROM posting "
        "WHERE board_id = ? AND first_seen_at >= ? AND first_seen_at <= ?", (board_id, start, now)).fetchall()
    if not new:
        return 0
    closed: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for r in conn.execute("SELECT posting_id, title, location, closed_at FROM posting "
                          "WHERE board_id = ? AND closed_at >= ? AND closed_at <= ?",
                          (board_id, _back(start, RELIST_DAYS), now)):
        closed.setdefault((r["title"], r["location"]), []).append((r["posting_id"], r["closed_at"]))
    count = 0
    for r in new:
        earliest = _back(r["first_seen_at"], RELIST_DAYS)
        if any(pid != r["posting_id"] and earliest <= at <= r["first_seen_at"]
               for pid, at in closed.get((r["title"], r["location"]), ())):
            count += 1
    return count
