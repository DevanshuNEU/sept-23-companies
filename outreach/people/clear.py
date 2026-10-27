"""Clear the LinkedIn To reach list for the new system (Part of #15).

Devanshu's ruling: the people on the LinkedIn page now go, because the new system finds new people. Only people nobody
ever contacted and he never touched go; everyone with history stays. `plan_clear` reads and decides, `write_backup`
copies the whole store, `apply_clear` deletes in one transaction. Nothing here drops or works around a trigger.

A LinkedIn person (`person.channel = 'linkedin'`) is kept when any of these holds:

- an `outreach_touch` names them (any channel, an undone one too): a touch is never deleted (a trigger), and it
  feeds dedup and the weekly count, so its person stays;
- a `linkedin_log` row (append-only), or an email `touch` thread;
- he added them (`added_by 'devanshu'`), saved or confirmed their note (`linkedin_draft.saved_by 'devanshu'` or a
  reason line past `proposed`), or changed them (a `person` event with actor `devanshu`);
- a `no_go` row names them (it stops anyone adding them again) or a `review_item` does (open or settled);
- a plan row dated today or later (New York) names them, or names anyone at their company; or a past plan row that
  names them was decided by him (`picked` or `removed`).

For a person who goes: their `linkedin_draft` (the prepare step's, saved by `claude`) and `person_source` rows are
deleted, a past `proposed` or `backlog` plan row that names them keeps the row and loses the name (`person_id` set
to NULL; the column allows it), and `person` goes last. Events stay: they are history. `--include-history` also
looks at people whose only history is imported touches, but a touch is never deleted and deleting its person would
orphan it, so it keeps every one of them: it removes no one more than the default.

`apply_clear` takes the plan the caller printed, decides again under `BEGIN IMMEDIATE`, and deletes only a person
whose record is the same in both (same revision, drafts, sources and plan rows, and still no reason to keep). So a
person added, touched or changed while the command ran stays. Each delete checks its row count; any failure rolls
the whole transaction back. One `people_cleared` event (actor `system`) records every id so the rows can be put back
from the backup.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from outreach.plan.dates import today_ny
from outreach.ranking.common import canonical_ids
from outreach.store import append_event, transaction

__all__ = ["SCOPES", "BackupFailed", "ClearChanged", "PlanRow", "Removal", "Kept", "ClearPlan", "Applied",
           "plan_clear", "write_backup", "backup_path", "apply_clear", "table_counts"]

TZ = "America/New_York"
SCOPES = ("never_contacted", "include_history")
DECIDED_BY_HIM = ("picked", "removed")

# What the run prints before and after: the tables it writes and the history it must leave alone.
COUNTED = (
    ("person", "SELECT count(*) FROM person"),
    ("LinkedIn people", "SELECT count(*) FROM person WHERE channel = 'linkedin'"),
    ("linkedin_draft", "SELECT count(*) FROM linkedin_draft"),
    ("person_source", "SELECT count(*) FROM person_source"),
    ("plan rows naming a person", "SELECT count(*) FROM daily_list_entry WHERE person_id IS NOT NULL"),
    ("daily_list_entry", "SELECT count(*) FROM daily_list_entry"),
    ("outreach_touch", "SELECT count(*) FROM outreach_touch"),
    ("linkedin_log", "SELECT count(*) FROM linkedin_log"),
    ("touch", "SELECT count(*) FROM touch"),
    ("no_go", "SELECT count(*) FROM no_go"),
    ("review_item", "SELECT count(*) FROM review_item"),
    ("event", "SELECT count(*) FROM event"),
)


class BackupFailed(RuntimeError):
    """The backup copy could not be written or did not check out; nothing may be deleted."""


class ClearChanged(RuntimeError):
    """A delete touched a different number of rows than the plan said; the transaction rolls back."""


@dataclass(frozen=True)
class PlanRow:
    list_date: str
    company_id: int
    revision: int


@dataclass(frozen=True)
class Removal:
    person_id: int
    name: str
    company_id: int
    company: str
    added_by: str
    revision: int
    updated_at: str
    drafts: tuple[tuple[int, int], ...]  # (draft_id, revision)
    sources: tuple[int, ...]
    plan_rows: tuple[PlanRow, ...]


@dataclass(frozen=True)
class Kept:
    person_id: int
    name: str
    company: str
    reasons: tuple[str, ...]
    history: bool  # has a touch, a log row or an email thread


@dataclass(frozen=True)
class ClearPlan:
    scope: str
    today: str
    remove: tuple[Removal, ...]
    kept: tuple[Kept, ...]


@dataclass(frozen=True)
class Applied:
    removed: tuple[Removal, ...]
    changed: tuple[Removal, ...]  # in the printed plan, but changed before the transaction: kept


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {name: conn.execute(sql).fetchone()[0] for name, sql in COUNTED}


def _count(conn: sqlite3.Connection, sql: str, *args) -> int:
    return conn.execute(sql, args).fetchone()[0]


def _reasons(conn: sqlite3.Connection, p: sqlite3.Row, today: str, scope: str, his_events: set[int],
             live_plan: dict[int, set[int]], canon: dict[int, int]) -> tuple[list[str], bool]:
    pid = p["person_id"]
    reasons: list[str] = []
    touches = conn.execute("SELECT channel, count(*) FROM outreach_touch WHERE person_id = ? GROUP BY channel "
                           "ORDER BY channel", (pid,)).fetchall()
    logs = _count(conn, "SELECT count(*) FROM linkedin_log WHERE person_id = ?", pid)
    threads = _count(conn, "SELECT count(*) FROM touch WHERE person_id = ?", pid)
    if touches:
        said = ", ".join(f"{channel} x{n}" for channel, n in touches)
        why = f"has outreach touches ({said}): a touch is never deleted and feeds dedup and the weekly count"
        if scope == "include_history":
            why += "; deleting this person would orphan it, so --include-history keeps them too"
        reasons.append(why)
    if logs:
        reasons.append(f"has {logs} LinkedIn log rows (append-only history)")
    if threads:
        reasons.append("has an email thread (a touch row)")
    if p["added_by"] == "devanshu":
        reasons.append("he added this person himself")
    if _count(conn, "SELECT count(*) FROM linkedin_draft WHERE person_id = ? "
                    "AND (saved_by = 'devanshu' OR reason_state <> 'proposed')", pid):
        reasons.append("has a note he saved or confirmed")
    if pid in his_events:
        reasons.append("he changed this person (an event by him)")
    if _count(conn, "SELECT count(*) FROM no_go WHERE person_id = ?", pid):
        reasons.append("is on the no-go list (that row stops anyone adding them again)")
    items = _count(conn, "SELECT count(*) FROM review_item WHERE person_id = ?", pid)
    if items:
        reasons.append(f"is named in {items} Needs-you items")
    for row in conn.execute("SELECT list_date, state FROM daily_list_entry WHERE person_id = ? ORDER BY list_date",
                            (pid,)):
        if row["list_date"] >= today:
            reasons.append(f"is on the plan for {row['list_date']}")
        elif row["state"] in DECIDED_BY_HIM:
            reasons.append(f"is on a plan row he decided ({row['state']}, {row['list_date']})")
    if live_plan.get(canon.get(p["company_id"], p["company_id"]), set()) - {pid}:
        reasons.append("their company has a person row on the plan for today or later")
    return reasons, bool(touches or logs or threads)


def plan_clear(conn: sqlite3.Connection, *, scope: str = "never_contacted", now: str) -> ClearPlan:
    """Who would go and who stays, and why. Reads only."""
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}, got {scope!r}")
    today = today_ny(now, TZ).isoformat()
    canon = canonical_ids(conn)
    his_events = {r[0] for r in conn.execute(
        "SELECT DISTINCT entity_id FROM event WHERE entity = 'person' AND actor = 'devanshu' "
        "AND entity_id IS NOT NULL")}
    live_plan: dict[int, set[int]] = {}
    for r in conn.execute("SELECT company_id, person_id FROM daily_list_entry WHERE list_date >= ? "
                          "AND person_id IS NOT NULL", (today,)):
        live_plan.setdefault(canon.get(r[0], r[0]), set()).add(r[1])
    remove: list[Removal] = []
    kept: list[Kept] = []
    people = conn.execute(
        "SELECT p.*, c.name AS company FROM person p JOIN company c ON c.company_id = p.company_id "
        "WHERE p.channel = 'linkedin' ORDER BY p.person_id").fetchall()
    for p in people:
        reasons, history = _reasons(conn, p, today, scope, his_events, live_plan, canon)
        if reasons:
            kept.append(Kept(p["person_id"], p["name"], p["company"], tuple(reasons), history))
            continue
        pid = p["person_id"]
        remove.append(Removal(
            person_id=pid, name=p["name"], company_id=p["company_id"], company=p["company"],
            added_by=p["added_by"], revision=p["revision"], updated_at=p["updated_at"],
            drafts=tuple((r[0], r[1]) for r in conn.execute(
                "SELECT draft_id, revision FROM linkedin_draft WHERE person_id = ? ORDER BY draft_id", (pid,))),
            sources=tuple(r[0] for r in conn.execute(
                "SELECT source_id FROM person_source WHERE person_id = ? ORDER BY source_id", (pid,))),
            plan_rows=tuple(PlanRow(r[0], r[1], r[2]) for r in conn.execute(
                "SELECT list_date, company_id, revision FROM daily_list_entry WHERE person_id = ? "
                "ORDER BY list_date, company_id", (pid,))),
        ))
    return ClearPlan(scope, today, tuple(remove), tuple(kept))


def backup_path(store_path: str, now: str) -> Path:
    stamp = now.replace("-", "").replace(":", "")
    return Path(store_path).parent / "backups" / f"clear-reach-{stamp}.db"


def write_backup(conn: sqlite3.Connection, store_path: str, now: str) -> Path:
    """A full copy of the store (SQLite's backup API) in `backups/` next to it, checked before it is trusted.
    Never overwrites a file. Raises `BackupFailed` on any problem."""
    target = backup_path(store_path, now)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise BackupFailed(f"{target} already exists; it is never overwritten")
        dest = sqlite3.connect(target)
        try:
            conn.backup(dest)
            check = dest.execute("PRAGMA quick_check").fetchone()[0]
            copied = dest.execute("SELECT count(*) FROM person").fetchone()[0]
        finally:
            dest.close()
    except BackupFailed:
        raise
    except (OSError, sqlite3.Error) as err:
        raise BackupFailed(f"could not write {target}: {err}") from err
    if check != "ok":
        raise BackupFailed(f"{target} failed its check: {check}")
    if copied != _count(conn, "SELECT count(*) FROM person"):
        raise BackupFailed(f"{target} does not hold the same people as the store")
    return target


def _changed(cur: sqlite3.Cursor, want: int, what: str) -> None:
    if cur.rowcount != want:
        raise ClearChanged(f"{what}: {cur.rowcount} rows changed, the plan said {want}")


def _detail(r: Removal) -> dict:
    return {"person_id": r.person_id, "name": r.name, "company_id": r.company_id, "company": r.company,
            "added_by": r.added_by, "draft_ids": [d for d, _ in r.drafts], "source_ids": list(r.sources),
            "plan_rows": [{"list_date": x.list_date, "company_id": x.company_id} for x in r.plan_rows]}


def apply_clear(conn: sqlite3.Connection, before: ClearPlan, *, backup: Path, now: str) -> Applied:
    """Delete the people of `before` that are still the same and still have no reason to stay, in one transaction."""
    if not before.remove:
        return Applied((), ())
    with transaction(conn):
        fresh = {r.person_id: r for r in plan_clear(conn, scope=before.scope, now=now).remove}
        go = tuple(r for r in before.remove if fresh.get(r.person_id) == r)
        changed = tuple(r for r in before.remove if fresh.get(r.person_id) != r)
        for r in go:
            for x in r.plan_rows:
                _changed(conn.execute(
                    "UPDATE daily_list_entry SET person_id = NULL, revision = revision + 1 "
                    "WHERE list_date = ? AND company_id = ? AND person_id = ? AND revision = ?",
                    (x.list_date, x.company_id, r.person_id, x.revision)), 1, f"plan row {x.list_date}")
            _changed(conn.execute(
                "DELETE FROM linkedin_draft WHERE person_id = ? AND saved_by = 'claude' AND reason_state = 'proposed'",
                (r.person_id,)), len(r.drafts), f"drafts of person {r.person_id}")
            _changed(conn.execute("DELETE FROM person_source WHERE person_id = ?", (r.person_id,)),
                     len(r.sources), f"sources of person {r.person_id}")
            _changed(conn.execute("DELETE FROM person WHERE person_id = ? AND revision = ? AND channel = 'linkedin'",
                                  (r.person_id, r.revision)), 1, f"person {r.person_id}")
        if go:
            append_event(conn, entity="person", kind="people_cleared", actor="system", at=now, detail={
                "scope": before.scope, "backup": str(backup), "people": [_detail(r) for r in go],
                "counts": {"people": len(go), "drafts": sum(len(r.drafts) for r in go),
                           "sources": sum(len(r.sources) for r in go),
                           "plan_rows_cleared": sum(len(r.plan_rows) for r in go)},
            })
    return Applied(go, changed)
