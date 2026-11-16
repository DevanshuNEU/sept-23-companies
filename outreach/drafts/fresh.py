"""Which open posting a company's role is named from (#290): a fresh one before a stale one.

On 2026-11-16 Knock named Ataraxis AI's 136-day-old research role over the Software Engineer job posted that day,
because the level order ("open" before "not stated") came before the date. A posting is fresh when its real date
(the board's posted date, else when Knock first saw it) is FRESH_DAYS old or newer and it is not a senior role.
The MCP tools and the company page and Outbox rank with this one rule, so they name the same posting.
"""
from __future__ import annotations

from datetime import date

FRESH_DAYS = 30


def real_date(posted_on: str | None, first_seen_at: str) -> date:
    """The posting's real date: the board's posted date, else the day Knock first saw it."""
    return date.fromisoformat(posted_on or first_seen_at[:10])


def stale_rank(posted_on: str | None, first_seen_at: str, level_tier: str, today: date) -> int:
    """0 for a fresh posting at a level he could take, else 1: the first key after a posting's state."""
    fresh = (today - real_date(posted_on, first_seen_at)).days <= FRESH_DAYS
    return 0 if fresh and level_tier != "senior" else 1
