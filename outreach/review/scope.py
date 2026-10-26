"""Which companies' items ask for his attention (#16, the company-first ruling in #9).

Needs you holds only items for companies he is working: in play (`play.in_play`), being emailed (`play.emailing`)
or held by a reply (`held_survivors`, the sender's one hold rule). That set is `attention`. The crawler's own data
gaps reach further, to the top of the ranked list (`crawler_scope`), so a careers pass over the whole store cannot
flood the list again. Nothing here hides a company or changes a decision: it only decides which gaps ask.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Collection, Mapping

from outreach.drafts.followups import company_map, company_reply_held, held_survivors
from outreach.ranking.common import canonical_ids
from outreach.review.config import ReviewConfig, load_review_config
from outreach.review.items import clear_item, unit
from outreach.review.play import emailing, in_play, ny_date
from outreach.store.timefmt import iso_utc

__all__ = ["ranked_ids", "held_companies", "attention", "crawler_scope", "clear_out_of_scope", "OUT_OF_SCOPE_NOTE"]

OUT_OF_SCOPE_NOTE = "Not in your plan. It comes back if the company does."


def ranked_ids(conn: sqlite3.Connection, top_n: int | None) -> list[int]:
    """The top `top_n` company ids of the ranked list (all of it for None), highest first; empty when the ranker is
    not there or has not run."""
    try:
        from outreach.ranking.scores import ranked_list
    except ImportError:
        return []
    return [row.company_id for row in ranked_list(conn, limit=top_n)]


def held_companies(conn: sqlite3.Connection) -> frozenset[int]:
    """The surviving companies held by a reply (`held_survivors`). In a merge cycle each company keeps its own."""
    companies = company_map(conn)
    try:
        return frozenset(held_survivors(companies))
    except RuntimeError:
        return frozenset(cid for cid, row in companies.items()
                         if row["held_by_touch"] is not None or company_reply_held(row))


def attention(conn: sqlite3.Connection, *, today: str, planned_days: int,
              canon: Mapping[int, int] | None = None) -> frozenset[int]:
    """The companies whose open items belong in Needs you: in play, being emailed, or held by a reply. `canon` as
    in `play.live_company_ids`."""
    canon = canonical_ids(conn) if canon is None else canon
    playing = in_play(conn, today=today, planned_days=planned_days, canon=canon)
    mailing = emailing(conn, today=today, planned_days=planned_days, canon=canon)
    return frozenset(playing) | mailing | held_companies(conn)


def crawler_scope(conn: sqlite3.Connection, *, today: str, cfg: ReviewConfig,
                  canon: Mapping[int, int] | None = None) -> tuple[frozenset[int], list[int]]:
    """What the crawler may raise a data gap for: (`attention`, the top `crawler_scope_top` of the ranked list in
    its order)."""
    return (attention(conn, today=today, planned_days=cfg.planned_days, canon=canon),
            ranked_ids(conn, cfg.crawler_scope_top))


def clear_out_of_scope(conn: sqlite3.Connection, *, now: str, cfg: ReviewConfig | None = None,
                       company_ids: Collection[int] | None = None) -> int | None:
    """Clear, at once, every open crawler `domain_missing` item whose company (its survivor) is outside
    `crawler_scope`, with the note `OUT_OF_SCOPE_NOTE`. The crawler raises it again (the cleared row reopens) when
    the company comes back into scope. Only the system's own open items: an item he settled is never touched, nor
    another producer's or another kind's.

    Returns how many it cleared, or None when the ranked list is empty: the top is then unknown, and "not in the
    top" read from missing input is not a reason to clear. `company_ids` limits it to those companies."""
    cfg = cfg or load_review_config()
    at = iso_utc(now)
    rows = conn.execute("SELECT review_id, company_id FROM review_item WHERE kind = 'domain_missing' "
                        "AND raised_by = 'crawler' AND state = 'open' ORDER BY review_id").fetchall()
    if company_ids is not None:
        wanted = set(company_ids)
        rows = [r for r in rows if r["company_id"] in wanted]
    if not rows:
        return 0
    canon = canonical_ids(conn)
    focus, top = crawler_scope(conn, today=ny_date(at), cfg=cfg, canon=canon)
    if not top:
        return None
    keep = focus | set(top)
    cleared = 0
    with unit(conn):
        for row in rows:
            if canon.get(row["company_id"], row["company_id"]) not in keep:
                cleared += clear_item(conn, row["review_id"], OUT_OF_SCOPE_NOTE, at)
    return cleared
