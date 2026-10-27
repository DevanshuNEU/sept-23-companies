"""Browse companies (#120): `GET /api/plan/browse`, one page of every company the plan could pick.

`outreach.plan.browse.browse_page` decides which companies and in which order; this turns the page into the bench's
compact row (`bench.bench_rows`, so a browse row is a pick row) with the stored position, the reason it cannot be
swapped in, if any, and whether he worked it before. Reads only.
"""
import sqlite3
from datetime import date

from outreach.api.models.plan import BrowseCompany, BrowseView
from outreach.api.queries.bench import bench_rows
from outreach.outcomes.windows import ny_day
from outreach.plan.browse import BrowseFilters, blocked_text, browse_page
from outreach.plan.config import PlanConfig
from outreach.plan.fresh import as_utc
from outreach.plan.select import bench_pick_of

__all__ = ["browse_view"]


def browse_view(conn: sqlite3.Connection, now: str, cfg: PlanConfig, f: BrowseFilters, after: int,
                limit: int) -> BrowseView:
    day = ny_day(now)
    page = browse_page(conn, date.fromisoformat(day), as_utc(now), cfg, f, after, limit)
    rows = bench_rows(conn, [bench_pick_of(p, cfg) for p in page.prospects], page.stored, day, now, cfg)
    companies = [
        BrowseCompany(**r.model_dump() | {"position": p.position}, blocked=blocked_text(p, cfg),
                      worked=p.company_id in page.worked)
        for r, p in zip(rows, page.prospects)]
    return BrowseView(as_of=now, date=day, companies=companies, total=page.total, next_after=page.next_after)
