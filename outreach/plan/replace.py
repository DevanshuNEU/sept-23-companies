"""Replacing a day's untouched plan (#15): the safety checks and the delete. `build_plan(replace=True)` uses them.

A plan build writes `daily_list` (one row), `daily_list_entry` (its rows), two `event` rows and a `job_run`, and moves
older `proposed` rows to `backlog`; it writes no person, note, touch or log row (prepare and the API do). So a replace
deletes only the day's `daily_list_entry` rows and its `daily_list` row, and only when `blockers` finds nothing. Events
and job runs are append-only history and stay; the older plans' rows are not touched (the carry reads them again).

`blockers` lists, in plain words, everything that shows he acted on the list. Any of these refuses:

- a row that is not `proposed` (a skip, a carry, a pick or a remove), or whose revision moved on (any write by him on
  a row moves it: a logged step, a saved search);
- since the start of the day (New York): an outreach touch (undone ones too) at a listed company or for a person on
  a row, a LinkedIn log row for any person there, an application there, an email thread or message for any person
  there, a person he added there, or an event of his for a listed company or a row;
- a note on a row's person that he saved or confirmed (any day), or one at a listed company he saved or confirmed
  today;
- a plan for a later date (it may have carried rows from this one);
- a list the plan job did not build (the lean Today's: its rowids may key touches, and it is not the plan's).

A listed company counts with its whole merge family. Reads only.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from outreach.plan.dates import utc_window
from outreach.ranking.common import canonical_ids

__all__ = ["ReplaceRefused", "OldRow", "old_rows", "blockers", "delete_plan"]

_NAMES = 5


class ReplaceRefused(ValueError):
    """The day's plan was acted on, so it is not replaced. `reasons` says what was found."""

    def __init__(self, day: date, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__(f"today's plan ({day.isoformat()}) was not replaced, nothing was written: "
                         + "; ".join(self.reasons) + ".")


@dataclass(frozen=True)
class OldRow:
    rowid: int
    rank: int
    company_id: int
    person_id: int | None
    channel: str | None
    list_group: str
    state: str
    revision: int


def old_rows(conn: sqlite3.Connection, day: date) -> tuple[OldRow, ...]:
    return tuple(OldRow(r["rowid"], r["rank"], r["company_id"], r["person_id"], r["channel"], r["list_group"],
                        r["state"], r["revision"])
                 for r in conn.execute("SELECT rowid, * FROM daily_list_entry WHERE list_date = ? ORDER BY rank",
                                       (day.isoformat(),)))


def _ids(values) -> str:
    return json.dumps(sorted({int(v) for v in values if v is not None}))


_IN = "IN (SELECT value FROM json_each(?))"


def _named(conn: sqlite3.Connection, company_ids) -> str:
    ids = sorted(set(company_ids))
    names = [r[0] for r in conn.execute(f"SELECT name FROM company WHERE company_id {_IN} ORDER BY name",
                                        (_ids(ids),))]
    more = len(names) - _NAMES
    return ", ".join(names[:_NAMES]) + (f" and {more} more" if more > 0 else "")


def _from_plan_job(conn: sqlite3.Connection, day: date) -> bool:
    """The API's `from_plan_job` test (`outreach.api.queries.plan`), read here so the plan does not import the API: a
    row that names a channel, or an ok `plan` run that built the date."""
    if conn.execute("SELECT 1 FROM daily_list_entry WHERE list_date = ? AND channel IS NOT NULL LIMIT 1",
                    (day.isoformat(),)).fetchone() is not None:
        return True
    return conn.execute(
        "SELECT 1 FROM job_run WHERE job = 'plan' AND status = 'ok' AND json_valid(summary) "
        "AND json_extract(summary, '$.date') = ? AND ifnull(json_extract(summary, '$.status'), '') <> 'already_built' "
        "LIMIT 1", (day.isoformat(),)).fetchone() is not None


def blockers(conn: sqlite3.Connection, day: date, tz: str) -> list[str]:
    """Every reason not to replace `day`'s plan, in plain words. Empty: every row is untouched."""
    rows = old_rows(conn, day)
    since = utc_window(day, day, tz)[0]
    canon = canonical_ids(conn)
    listed = {canon.get(r.company_id, r.company_id) for r in rows}
    family = {c for c, s in canon.items() if s in listed} | listed | {r.company_id for r in rows}
    fam = _ids(family)
    row_people = _ids(r.person_id for r in rows)
    people = _ids([r[0] for r in conn.execute(f"SELECT person_id FROM person WHERE company_id {_IN}", (fam,))]
                  + json.loads(row_people))
    out: list[str] = []

    decided = [r.company_id for r in rows if r.state != "proposed"]
    if decided:
        out.append(f"{len(decided)} rows have a decision by you (a skip, carry, pick or remove): "
                   f"{_named(conn, decided)}")
    written = [r.company_id for r in rows if r.state == "proposed" and r.revision != 1]
    if written:
        out.append(f"{len(written)} rows were written to after the build (a logged step or a saved search): "
                   f"{_named(conn, written)}")

    def companies(sql: str, *args) -> list[int]:
        return [r[0] for r in conn.execute(sql, args)]

    touched = companies(f"SELECT company_id FROM outreach_touch WHERE (company_id {_IN} OR person_id {_IN}) "
                        "AND (created_at >= ? OR sent_at >= ?)", fam, people, since, since)
    if touched:
        out.append(f"a touch was logged today at {_named(conn, touched)}")
    logged = companies(f"SELECT p.company_id FROM linkedin_log l JOIN person p ON p.person_id = l.person_id "
                       f"WHERE l.person_id {_IN} AND (l.recorded_at >= ? OR l.at >= ?)", people, since, since)
    if logged:
        out.append(f"a LinkedIn log row was written today for someone at {_named(conn, logged)}")
    applied = companies(f"SELECT company_id FROM application WHERE company_id {_IN} "
                        "AND (applied_on >= ? OR outcome_at >= ?)", fam, day.isoformat(), since)
    if applied:
        out.append(f"an application was logged today at {_named(conn, applied)}")
    emailed = companies(
        f"SELECT p.company_id FROM touch t JOIN person p ON p.person_id = t.person_id WHERE t.person_id {_IN} "
        "AND (t.created_at >= ? OR t.updated_at >= ? OR EXISTS (SELECT 1 FROM message m WHERE m.touch_id = t.touch_id "
        "AND (m.created_at >= ? OR m.updated_at >= ?)))", people, since, since, since, since)
    if emailed:
        out.append(f"an email was written or changed today for someone at {_named(conn, emailed)}")
    noted = companies(
        f"SELECT p.company_id FROM linkedin_draft d JOIN person p ON p.person_id = d.person_id "
        f"WHERE (d.saved_by = 'devanshu' OR d.reason_state <> 'proposed') "
        f"AND (d.person_id {_IN} OR (d.person_id {_IN} AND (d.created_at >= ? OR d.updated_at >= ?)))",
        row_people, people, since, since)
    if noted:
        out.append(f"a note was saved or confirmed by you for someone at {_named(conn, noted)}")
    added = companies(f"SELECT company_id FROM person WHERE company_id {_IN} AND added_by = 'devanshu' "
                      "AND created_at >= ?", fam, since)
    if added:
        out.append(f"you added people today at {_named(conn, added)}")
    acted = companies(
        f"SELECT coalesce(company_id, 0) FROM event WHERE actor = 'devanshu' AND (at >= ? OR recorded_at >= ?) "
        f"AND (company_id {_IN} OR (entity = 'daily_list' AND entity_id {_IN}))",
        since, since, fam, _ids(r.rowid for r in rows))
    if acted:
        out.append(f"you did something today at {_named(conn, acted) or 'a row of the plan'}")
    if not _from_plan_job(conn, day):
        out.append("the list was not built by the plan job (the lean Today stored it), so it is not the plan's to "
                   "replace")
    later = conn.execute("SELECT min(list_date) FROM daily_list WHERE list_date > ?", (day.isoformat(),)).fetchone()[0]
    if later is not None:
        out.append(f"a plan for a later day ({later}) is already built and may have carried rows from this one")
    return out


def delete_plan(conn: sqlite3.Connection, day: date) -> int:
    """Delete the day's rows and its `daily_list` row. Run inside the caller's transaction, after `blockers`."""
    n = conn.execute("DELETE FROM daily_list_entry WHERE list_date = ?", (day.isoformat(),)).rowcount
    conn.execute("DELETE FROM daily_list WHERE list_date = ?", (day.isoformat(),))
    return n
