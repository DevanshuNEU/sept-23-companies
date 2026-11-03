"""How a link is matched to a stored posting, shared by the scorecard and the miss log (#153).

The crawler's `source_key`s are not all lower case (Workday paths and Workable shortcodes keep their case), and
`lower(source_key)` cannot use the unique index. So a link offers candidate keys: as built from the link, lower-cased
(`urls.posting_key`) and, for Workday, built with the stored board's token (a board keeps its site segment's case, a
link may not). A returned key is matched to its link by lower case. A bare `gh_jid` link has no board token to build a
key from: it matches the Greenhouse key ending in `:<native_id>`, found in a range scan of the `greenhouse:` keys
(index only; `LIKE` alone would not use the index).
"""
from __future__ import annotations

import sqlite3

from outreach.crawl.postings import source_key

__all__ = ["GREENHOUSE_KEYS_SQL", "workday_tokens", "key_candidates"]

GREENHOUSE_KEYS_SQL = ("SELECT posting_id, source_key FROM posting "
                       "WHERE source_key >= 'greenhouse:' AND source_key < 'greenhouse;'")


def workday_tokens(conn: sqlite3.Connection) -> dict[str, str]:
    """Stored Workday board tokens by lower-cased token. A board keeps its site segment's case (`acme.wd5/External`)
    and a link may spell it another way."""
    return {token.lower(): token for (token,) in conn.execute("SELECT token FROM board WHERE ats = 'workday'")}


def key_candidates(ats: str | None, token: str | None, native_id: str | None, workday: dict[str, str]
                   ) -> tuple[str, ...]:
    """The `source_key` a link names, in the link's case and lower-cased, and for Workday also with the stored
    board's token; () when the link names no job. The path's case is the link's own: a link whose path case differs
    from the stored one is not found."""
    if not ats or not token or not native_id:
        return ()
    exact = source_key(ats, token, native_id)
    found = [exact, exact.lower()]
    stored = workday.get(token.lower()) if ats == "workday" else None
    if stored:
        found.append(source_key("workday", stored, native_id))
    return tuple(dict.fromkeys(found))
