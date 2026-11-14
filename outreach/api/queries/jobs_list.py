"""The job list (#156): `GET /api/jobs`. `outreach.pipeline.joblist.query` decides which postings and in which order;
this turns the page into the wire model. Reads only."""
import sqlite3

from outreach.api.models.jobs import JobCounts, JobRowWire, JobsView
from outreach.api.queries.h1b import h1b_wire
from outreach.pipeline.joblist import JobFilters, query

__all__ = ["jobs_view"]


def jobs_view(conn: sqlite3.Connection, now: str, f: JobFilters, after: str | None, limit: int) -> JobsView:
    """One page. A malformed cursor is a ValueError (the router makes it a 400)."""
    page = query(conn, now, f, after, limit)
    rows = [JobRowWire(**vars(r), h1b=h1b_wire(r.company_name)) for r in page.rows]
    return JobsView(as_of=now, rows=rows, total=page.total, next_after=page.next_after,
                    counts=JobCounts(**page.counts))
