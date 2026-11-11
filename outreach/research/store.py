"""The research store: checked company facts, written and read on the merge survivor (#205).

Standard library only. No wall clock: the caller passes `now`. The table refuses LinkedIn links and bad shapes, so a
bad fact raises sqlite3.IntegrityError and nothing from that call is kept.
"""
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from outreach.drafts.followups import canonical_company
from outreach.store.atomic import atomic
from outreach.store.timefmt import ISO_FMT

__all__ = ["FRESH_DAYS", "Fact", "store_company_facts", "fresh_company_facts"]

# A company fact is fresh this many days after code last checked it; the brief shows only fresh ones.
FRESH_DAYS = 14


@dataclass(frozen=True)
class Fact:
    fact: str
    quote: str
    url: str
    page_date: str | None = None


def store_company_facts(conn: sqlite3.Connection, company_id: int, facts: Sequence[Fact], *, found_by: str,
                        now: str) -> int:
    """Store checked facts on the company's merge survivor. Returns how many rows were written: a new fact, or a
    repeat (same url and quote) re-checked later, which gets the newer verified_at (and a page_date if it had none)
    and keeps its first found_by. A repeat that is not newer changes nothing. All or none: a refused fact undoes
    the whole call."""
    survivor = canonical_company(conn, company_id)
    inserted = 0
    with atomic(conn, "research_facts"):
        for f in facts:
            cur = conn.execute(
                "INSERT INTO research_fact(company_id, fact, quote, url, page_date, found_by, verified_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (company_id, url, quote) DO UPDATE SET "
                "verified_at = excluded.verified_at, page_date = coalesce(excluded.page_date, research_fact.page_date) "
                "WHERE excluded.verified_at > research_fact.verified_at",
                (survivor, f.fact, f.quote, f.url, f.page_date, found_by, now))
            inserted += cur.rowcount
    return inserted


def fresh_company_facts(conn: sqlite3.Connection, company_id: int, *, now: str) -> list[dict]:
    """The survivor's facts verified within the last `FRESH_DAYS` days, newest first."""
    survivor = canonical_company(conn, company_id)
    cutoff = (datetime.strptime(now, ISO_FMT) - timedelta(days=FRESH_DAYS)).strftime(ISO_FMT)
    rows = conn.execute(
        "SELECT fact, quote, url, page_date, found_by, verified_at FROM research_fact "
        "WHERE company_id = ? AND verified_at >= ? ORDER BY verified_at DESC, fact_id DESC", (survivor, cutoff))
    return [dict(r) for r in rows]
