"""The company writes and a person's role kind, each inside `run_write`'s one transaction.

A company named by a merged-away duplicate is written on its survivor (the page shows the survivor and its
revision), and every read of the merged family follows `merged_into` to its end. Each write appends one event by
Devanshu keyed `ctx.event_key(0)`; a second row it writes (a no-go, a lifted no-go) takes `event_key(1)`.

Pause sets `paused_at`; the sender's queue then gives every approved email there `company_paused`, so nothing
goes out. Close stops every planned follow-up through the sender's own `stop_planned` and, for no sponsorship,
adds the company no-go (source `devanshu`). Reopen takes back only the no-go its own close wrote: any other active
no-go on the family refuses it.
"""
import json
import sqlite3
from collections.abc import Sequence

from outreach.api.errors import ApiProblem
from outreach.api.idempotency import WriteCtx
from outreach.api.models.company import PersonRef
from outreach.api.models.company_writes import CompanyResumed, CompanyWritten
from outreach.api.queries.company import CLOSE_WORDS, person_ref
from outreach.api.revisions import bump
from outreach.drafts.checks import draft_input, run_checks
from outreach.drafts.followups import canonical_company, company_map, no_go_sets, resolve_company
from outreach.sending.followups import stop_planned
from outreach.store.writes import append_event

NOT_SAVED = "Nothing was saved."
WAITING = ("drafted", "approved")  # what a pause holds back and a resume lets go
RECHECKED = ("drafted", "approved", "failed")  # a person's messages that can still go out, rechecked on a role change
CLOSE_STOP_REASON = "company_closed"  # the queue's stop reason for a closed company, on each stopped follow-up


def _survivor(conn: sqlite3.Connection, company_id: int) -> sqlite3.Row:
    if conn.execute("SELECT 1 FROM company WHERE company_id = ?", (company_id,)).fetchone() is None:
        raise ApiProblem("not_found", f"There is no company {company_id}.")
    survivor = canonical_company(conn, company_id)
    return conn.execute("SELECT * FROM company WHERE company_id = ?", (survivor,)).fetchone()


def _fresh(row: sqlite3.Row, revision: int) -> None:
    if row["revision"] != revision:
        raise ApiProblem("conflict", f"The company changed since you loaded it. {NOT_SAVED}")


def _family(conn: sqlite3.Connection, survivor: int) -> list[int]:
    companies = company_map(conn)
    return sorted(cid for cid in companies if resolve_company(companies, cid)[-1] == survivor)


def _in(ids: Sequence) -> str:
    return ",".join("?" * len(ids))


def _messages(conn: sqlite3.Connection, family: list[int], states: Sequence[str]) -> list[sqlite3.Row]:
    """The family's email messages in `states`, oldest first."""
    return conn.execute(
        f"SELECT m.message_id, m.state, t.state AS touch_state FROM message m "
        f"JOIN touch t ON t.touch_id = m.touch_id JOIN person p ON p.person_id = t.person_id "
        f"WHERE p.company_id IN ({_in(family)}) AND m.state IN ({_in(states)}) ORDER BY m.message_id",
        (*family, *states)).fetchall()


def _event(ctx: WriteCtx, company_id: int, kind: str, detail: dict, n: int = 0) -> None:
    append_event(ctx.conn, entity="company", entity_id=company_id, company_id=company_id, kind=kind,
                 actor="devanshu", at=ctx.now, detail=detail, idempotency_key=ctx.event_key(n))


def pause(ctx: WriteCtx, company_id: int, revision: int) -> CompanyWritten:
    row = _survivor(ctx.conn, company_id)
    _fresh(row, revision)
    if row["status"] != "working":
        raise ApiProblem("not_allowed", f"Only a company you are emailing can be paused, and {row['name']} is "
                                        f"{row['status']}. {NOT_SAVED}")
    if row["paused_at"] is not None:
        raise ApiProblem("not_allowed", f"{row['name']} is already paused. {NOT_SAVED}")
    new = bump(ctx.conn, "company", "company_id", row["company_id"], revision, {"paused_at": ctx.now}, at=ctx.now)
    _event(ctx, row["company_id"], "paused", {"text": "Paused by you"})
    return CompanyWritten(revision=new)


def resume(ctx: WriteCtx, company_id: int, revision: int) -> CompanyResumed:
    """Clears his pause. A hold by a reply is not a pause: releasing it is the Outbox's release-hold (#11 Task 19)."""
    row = _survivor(ctx.conn, company_id)
    _fresh(row, revision)
    if row["paused_at"] is None:
        raise ApiProblem("not_allowed", f"{row['name']} is not paused. {NOT_SAVED}")
    released = sum(1 for m in _messages(ctx.conn, _family(ctx.conn, row["company_id"]), WAITING)
                   if m["touch_state"] == "open")
    new = bump(ctx.conn, "company", "company_id", row["company_id"], revision,
               {"paused_at": None, "pause_note": ""}, at=ctx.now)
    _event(ctx, row["company_id"], "resumed", {"text": "Resumed by you", "released": released})
    return CompanyResumed(released=released, revision=new)


def close(ctx: WriteCtx, company_id: int, reason: str, note: str, revision: int) -> CompanyWritten:
    conn = ctx.conn
    row = _survivor(conn, company_id)
    _fresh(row, revision)
    if row["status"] in ("closed", "removed"):
        raise ApiProblem("not_allowed", f"{row['name']} is already {row['status']}. {NOT_SAVED}")
    survivor = row["company_id"]
    # A closed company cannot stay paused (the store's CHECK), and closing stops its mail anyway.
    new = bump(conn, "company", "company_id", survivor, revision,
               {"status": "closed", "close_reason": reason, "status_note": note, "status_at": ctx.now,
                "paused_at": None, "pause_note": ""}, at=ctx.now)
    no_go_id = None
    if reason == "no_sponsorship":
        cur = conn.execute(
            "INSERT INTO no_go(company_id, reason, note, source, source_ref, created_at) "
            "VALUES (?, 'no_sponsorship', ?, 'devanshu', 'Closed by you', ?) "
            "ON CONFLICT(company_id) WHERE company_id IS NOT NULL AND lifted_at IS NULL DO NOTHING",
            (survivor, note, ctx.now))
        if cur.rowcount:  # a company already on the list keeps its row
            no_go_id = cur.lastrowid
            append_event(conn, entity="no_go", entity_id=no_go_id, company_id=survivor, kind="added",
                         actor="devanshu", at=ctx.now, idempotency_key=ctx.event_key(1),
                         detail={"reason": "no_sponsorship", "source": "devanshu", "companyId": survivor,
                                 "text": "No-go: no sponsorship."})
    _event(ctx, survivor, "closed", {
        "text": f"Closed: {CLOSE_WORDS.get(reason, reason)}", "reason": reason, "note": note,
        "previous_status": row["status"], "no_go_id": no_go_id})
    for m in _messages(conn, _family(conn, survivor), ("planned",)):
        stop_planned(conn, m["message_id"], reason=CLOSE_STOP_REASON, now=ctx.now, actor="devanshu")
    return CompanyWritten(revision=new)


def _reopen_status(conn: sqlite3.Connection, family: list[int], previous: str | None) -> str:
    """Back to `working` when it was being emailed before the close, else `backlog`. Without a close event (an
    imported close) it is `working` when it was ever picked or has an email thread."""
    if previous == "working":
        return "working"
    if previous in ("backlog", "candidate"):
        return "backlog"
    picked = conn.execute(f"SELECT 1 FROM daily_list_entry WHERE company_id IN ({_in(family)}) AND state = 'picked' "
                          "LIMIT 1", family).fetchone()
    touched = conn.execute(f"SELECT 1 FROM touch t JOIN person p ON p.person_id = t.person_id "
                           f"WHERE p.company_id IN ({_in(family)}) LIMIT 1", family).fetchone()
    return "working" if picked or touched else "backlog"


def reopen(ctx: WriteCtx, company_id: int, revision: int) -> CompanyWritten:
    conn = ctx.conn
    row = _survivor(conn, company_id)
    _fresh(row, revision)
    if row["status"] != "closed":
        raise ApiProblem("not_allowed", f"{row['name']} is not closed. {NOT_SAVED}")
    survivor = row["company_id"]
    closed = conn.execute("SELECT detail FROM event WHERE entity = 'company' AND entity_id = ? AND kind = 'closed' "
                          "ORDER BY event_id DESC LIMIT 1", (survivor,)).fetchone()
    detail = json.loads(closed["detail"]) if closed is not None else {}
    own = detail.get("no_go_id")
    if isinstance(own, int) and conn.execute(
            "UPDATE no_go SET lifted_at = ?, lift_reason = 'Reopened by you' WHERE no_go_id = ? "
            "AND lifted_at IS NULL", (ctx.now, own)).rowcount:
        append_event(conn, entity="no_go", entity_id=own, company_id=survivor, kind="lifted", actor="devanshu",
                     at=ctx.now, idempotency_key=ctx.event_key(1),
                     detail={"reason": "Reopened by you", "text": "Taken off the no-go list: reopened by you."})
    if survivor in no_go_sets(conn, company_map(conn))[1]:
        raise ApiProblem("not_allowed", f"{row['name']} is on the no-go list. Lift the no-go entry first, then "
                                        f"reopen it. {NOT_SAVED}")
    status = _reopen_status(conn, _family(conn, survivor), detail.get("previous_status"))
    new = bump(conn, "company", "company_id", survivor, revision,
               {"status": status, "close_reason": None, "status_note": "", "status_at": ctx.now}, at=ctx.now)
    _event(ctx, survivor, "reopened", {"text": "Reopened by you", "status": status})
    return CompanyWritten(revision=new)


def set_role_kind(ctx: WriteCtx, person_id: int, kind: str, revision: int, banned: list) -> PersonRef:
    """Sets the person's role kind and stores fresh checks on each of their messages that can still go out (the
    recruiter warning follows the kind). The checks read the stored mail-server answer only: no lookup here."""
    conn = ctx.conn
    row = conn.execute("SELECT * FROM person WHERE person_id = ?", (person_id,)).fetchone()
    if row is None:
        raise ApiProblem("not_found", f"There is no person {person_id}.")
    bump(conn, "person", "person_id", person_id, revision, {"role_kind": kind}, at=ctx.now)
    company_id = canonical_company(conn, row["company_id"])
    append_event(conn, entity="person", entity_id=person_id, company_id=company_id, kind="role_kind_changed",
                 actor="devanshu", at=ctx.now, idempotency_key=ctx.event_key(0),
                 detail={"from": row["role_kind"], "to": kind,
                         "text": f"{row['name']} is now marked {kind.replace('_', ' ')}"})
    for m in conn.execute(
            f"SELECT m.message_id FROM message m JOIN touch t ON t.touch_id = m.touch_id WHERE t.person_id = ? "
            f"AND m.state IN ({_in(RECHECKED)}) ORDER BY m.message_id", (person_id, *RECHECKED)).fetchall():
        found = run_checks(conn, draft_input(conn, m["message_id"], now=ctx.now), banned=banned, now=ctx.now)
        conn.execute("UPDATE message SET checks = ? WHERE message_id = ?", (json.dumps(found), m["message_id"]))
    return person_ref(conn.execute("SELECT * FROM person WHERE person_id = ?", (person_id,)).fetchone())
