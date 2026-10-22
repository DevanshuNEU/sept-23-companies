"""Loaders and helpers every ranking module shares (#6): merged companies, untrusted boards, ages and clean text.

Each loader is one query over the whole store, never a query per company.
"""
from __future__ import annotations

import sqlite3
from datetime import date

__all__ = ["BOARD_DOUBTFUL_PREFIX", "UNREAD_DOUBTFUL_FOUND_BY", "canonical_ids", "doubtful_board_ids", "age_days",
           "clean_text"]

# #21 raises an open `flagged` item with this reason prefix on a board it thinks is another company's.
BOARD_DOUBTFUL_PREFIX = "board match doubtful"
# Boards found by name or by guessing a slug: #21 checks the match on the first read, so until then they are doubtful
# (ruling 7; the ledger adds slug_probe, which #21 checks the same way).
UNREAD_DOUBTFUL_FOUND_BY = ("stapply_name", "slug_probe")
_DASHES = {0x2013: "-", 0x2014: "-"}


def canonical_ids(conn: sqlite3.Connection) -> dict[int, int]:
    """Every company_id to the end of its `merged_into` chain. A cycle (A into B into A) never loops: every member
    of the cycle maps to its smallest id, so the members still agree."""
    nxt = {cid: into for cid, into in conn.execute("SELECT company_id, merged_into FROM company")}
    out: dict[int, int] = {}
    for start in nxt:
        if start in out:
            continue
        path, seen = [], set()
        cur = start
        while True:
            if cur in out:
                end = out[cur]
                break
            if cur in seen:  # a cycle: stop at the first repeat
                end = min(path[path.index(cur):])
                break
            seen.add(cur)
            path.append(cur)
            into = nxt.get(cur)
            if into is None:
                end = cur
                break
            cur = into
        for cid in path:
            out[cid] = end
    return out


def doubtful_board_ids(conn: sqlite3.Connection) -> frozenset[int]:
    """Boards whose postings count for nothing: an open crawler `flagged` item that says the board match is
    doubtful, or a board found by name or slug that has not had its first read (so #21 has not checked it)."""
    marks = ", ".join("?" for _ in UNREAD_DOUBTFUL_FOUND_BY)
    rows = conn.execute(
        "SELECT board_id FROM review_item WHERE kind = 'flagged' AND raised_by = 'crawler' AND state = 'open' "
        "AND board_id IS NOT NULL AND reason LIKE ? "
        f"UNION SELECT board_id FROM board WHERE found_by IN ({marks}) AND last_read IS NULL",
        (BOARD_DOUBTFUL_PREFIX + "%", *UNREAD_DOUBTFUL_FOUND_BY)).fetchall()
    return frozenset(r[0] for r in rows)


def age_days(on: str | None, today: date) -> int | None:
    """Whole days from an ISO date or timestamp to `today`, never below 0; None for None or a string that is not
    a date."""
    if not on or not isinstance(on, str):
        return None
    try:
        day = date.fromisoformat(on[:10])
    except ValueError:
        return None
    return max((today - day).days, 0)


def clean_text(text: str) -> str:
    """The text with en and em dashes turned into a plain hyphen: a posting title may hold one, a reason may not."""
    return text.translate(_DASHES)
