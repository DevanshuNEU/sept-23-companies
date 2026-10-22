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
    """Every company_id to the end of its `merged_into` chain. A cycle (A into B into A) raises ValueError."""
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
            if cur in seen:  # a store error, as in outreach.identity.live_company: never pick a survivor silently
                raise ValueError(f"merged_into loops back to company {cur} from {start}")
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
    doubtful; a board found by name or slug before its first good read (so #21 has not checked it); and an
    inactive board (the crawler no longer reads it)."""
    marks = ", ".join("?" for _ in UNREAD_DOUBTFUL_FOUND_BY)
    # "Before its first good read" restates outreach/crawl/ingest.py _before_first_good_read: never read, or only
    # errors so far (last read an error, no empty run under it, no kept good board_read row and no read_state event
    # to a good state). An error carries no name and no jobs, so #21's check waits for a good read, and so do we.
    rows = conn.execute(
        "SELECT board_id FROM review_item WHERE kind = 'flagged' AND raised_by = 'crawler' AND state = 'open' "
        "AND board_id IS NOT NULL AND reason LIKE ? "
        "UNION SELECT board_id FROM board WHERE active = 0 "
        f"UNION SELECT b.board_id FROM board b WHERE b.found_by IN ({marks}) AND (b.last_read IS NULL OR "
        "(b.last_read = 'error' AND b.empty_streak = 0 "
        "AND NOT EXISTS (SELECT 1 FROM board_read r WHERE r.board_id = b.board_id "
        "AND r.result IN ('ok', 'empty', 'unchanged')) "
        "AND NOT EXISTS (SELECT 1 FROM event e WHERE e.entity = 'board' AND e.entity_id = b.board_id "
        "AND e.kind = 'read_state' AND json_extract(e.detail, '$.to') IN ('ok', 'empty', 'unchanged'))))",
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
