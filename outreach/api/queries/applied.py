"""The Applied list (#218): every posting he applied to, newest first.

A row is a live `application` touch (`undone_at IS NULL`) that names a posting: one row per posting, at its first
live touch. The role and link come from the posting (the application's saved url when the posting has none), the day
from the application row (else the touch's New York day), and the company is the survivor of any merge, read now.
"""
import sqlite3

from outreach.api.models.applied import AppliedRow, AppliedView
from outreach.api.queries.company import merge_parents, survivor_of
from outreach.outcomes.windows import ny_day

_ROWS = (
    "SELECT t.posting_id, min(t.sent_at) AS sent_at, min(t.touch_id) AS touch_id, p.company_id, p.title, p.url, "
    "p.closed_at, (SELECT a.applied_on FROM application a WHERE a.posting_id = t.posting_id "
    "ORDER BY a.application_id LIMIT 1) AS applied_on, "
    "(SELECT a.url FROM application a WHERE a.posting_id = t.posting_id ORDER BY a.application_id LIMIT 1) AS app_url "
    "FROM outreach_touch t JOIN posting p ON p.posting_id = t.posting_id "
    "WHERE t.channel = 'application' AND t.undone_at IS NULL GROUP BY t.posting_id")


def applied_count(conn: sqlite3.Connection) -> int:
    """How many postings he applied to: the rows of the Applied list."""
    return conn.execute("SELECT count(DISTINCT posting_id) FROM outreach_touch WHERE channel = 'application' "
                        "AND undone_at IS NULL AND posting_id IS NOT NULL").fetchone()[0]


def applied_view(conn: sqlite3.Connection, now: str) -> AppliedView:
    parents = merge_parents(conn)
    names = dict(conn.execute("SELECT company_id, name FROM company"))
    rows = []
    for r in conn.execute(_ROWS):
        company = survivor_of(parents, r["company_id"])
        rows.append((r["applied_on"] or ny_day(r["sent_at"]), r["sent_at"], r["touch_id"], AppliedRow(
            posting_id=r["posting_id"], company_id=company, company_name=names[company], title=r["title"],
            url=r["url"] or r["app_url"] or "", applied_on=r["applied_on"] or ny_day(r["sent_at"]),
            open=r["closed_at"] is None)))
    rows.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    return AppliedView(as_of=now, rows=[row for *_, row in rows])
