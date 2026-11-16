"""A company's size as the sender's pacing reads it (#229), shared with the MCP's pick_people (#290). Reads only; the
MCP may import it because it touches nothing that sends."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from outreach.pipeline.joblist import BIG_BY_POSTINGS, ENTERPRISE_ATS
from outreach.ranking.common import canonical_ids

__all__ = ["company_sizes"]


def company_sizes(conn: sqlite3.Connection, company_ids: Iterable[int]) -> dict[int, tuple[int | None, bool]]:
    """Each surviving company's (size, guessed big): its size fact (the high figure, else the low one; the survivor's own first, else the largest of its
    duplicates'), else None, and whether the job list would
    guess it big. Three reads over the companies asked for and their merged duplicates. The sender's pacing and the
    MCP's pick_people (#290) both read size here."""
    wanted = set(company_ids)
    if not wanted:
        return {}
    canon = canonical_ids(conn)
    family: dict[int, int] = {cid: cid for cid in wanted}  # every company id read, to the survivor it counts for
    for src, dst in canon.items():
        if dst in wanted:
            family[src] = dst
    ids = sorted(family)
    marks = ",".join("?" * len(ids))
    own: dict[int, int] = {}
    others: dict[int, int] = {}
    for cid, size in conn.execute(
            f"SELECT company_id, COALESCE(num_high, num_low) FROM company_fact WHERE fact = 'size' "
            f"AND company_id IN ({marks}) AND COALESCE(num_high, num_low) IS NOT NULL", ids):
        survivor = family[cid]
        if cid == survivor:
            own[survivor] = size
        else:
            others[survivor] = max(others.get(survivor, 0), size)
    enterprise = {family[cid] for (cid,) in conn.execute(
        f"SELECT DISTINCT company_id FROM board WHERE active = 1 AND company_id IN ({marks}) "
        f"AND ats IN ({','.join('?' * len(ENTERPRISE_ATS))})", (*ids, *ENTERPRISE_ATS))}
    us_open: dict[int, int] = {}
    for cid, n in conn.execute(
            f"SELECT p.company_id, COUNT(*) FROM posting p JOIN decision d ON d.posting_id = p.posting_id "
            f"AND d.ruleset_version = (SELECT ruleset_version FROM ruleset WHERE is_current = 1) "
            f"WHERE p.company_id IN ({marks}) AND p.closed_at IS NULL AND d.us = 'yes' GROUP BY p.company_id", ids):
        us_open[family[cid]] = us_open.get(family[cid], 0) + n
    return {cid: (own.get(cid, others.get(cid)), cid in enterprise or us_open.get(cid, 0) > BIG_BY_POSTINGS)
            for cid in wanted}
