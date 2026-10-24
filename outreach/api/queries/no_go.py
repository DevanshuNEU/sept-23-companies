"""The No-go list, and taking an entry off it.

Active `no_go` rows for companies and people, plus the companies he removed (a company merged away is not
"removed by you"). A company row is shown as its survivor, because a no-go on a merged duplicate stops the whole
family (the sender's `no_go_sets`). The ranker's own hides come last, from its stored scores, each with the reason
it stored: so every hide shows why, and a provisional one reads "hidden, pending reader".

A lift sets `lifted_at` and `lift_reason`. The row stays, so automation sees the choice and never adds it back.
"""
import sqlite3
from collections import Counter

from outreach.api.errors import ApiProblem
from outreach.api.idempotency import WriteCtx
from outreach.api.models.base import Empty
from outreach.api.models.no_go import (
    HiddenCompany, NoGoRow, NoGoTargetCompany, NoGoTargetPerson, NoGoView, ReasonCount, RemovedCompany, RemovedRow,
)
from outreach.api.queries.company import IMPORT_SOURCES, company_holds, merge_parents, person_ref, survivor_of
from outreach.api.queries.outcomes import by_ids, company_refs, people_by_ids
from outreach.drafts.followups import canonical_company
from outreach.ranking.scores import hidden_list
from outreach.store.writes import append_event

NOT_SAVED = "Nothing was saved."


def source_url(source_ref: str) -> str | None:
    ref = source_ref.strip()
    return ref if ref.lower().startswith(("http://", "https://")) else None


def import_source(source_ref: str) -> str | None:
    """`import:<source>:<key>` names the old place the row came from; anything else names none."""
    parts = source_ref.split(":", 2)
    return parts[1] if len(parts) >= 2 and parts[0] == "import" and parts[1] in IMPORT_SOURCES else None


def no_go_count(conn: sqlite3.Connection) -> int:
    """The rows the view lists: active no-go rows (company and person) and the companies he removed."""
    active = conn.execute("SELECT count(*) FROM no_go WHERE lifted_at IS NULL").fetchone()[0]
    removed = conn.execute("SELECT count(*) FROM company WHERE status = 'removed' AND merged_into IS NULL").fetchone()[0]
    return active + removed


def no_go_view(conn: sqlite3.Connection, now: str) -> NoGoView:
    parents = merge_parents(conn)
    rows = conn.execute("SELECT * FROM no_go WHERE lifted_at IS NULL ORDER BY created_at DESC, no_go_id DESC").fetchall()
    removed = conn.execute(
        "SELECT company_id, remove_reason, status_note, status_at, revision FROM company "
        "WHERE status = 'removed' AND merged_into IS NULL ORDER BY status_at DESC, company_id DESC").fetchall()
    hidden = hidden_list(conn)
    if not (rows or removed or hidden):
        return NoGoView(as_of=now, companies=[], people=[], counts_by_reason=[], hidden_companies=[])
    people = people_by_ids(conn, {r["person_id"] for r in rows if r["person_id"] is not None})
    holds = company_holds(conn)
    wanted = {survivor_of(parents, r["company_id"]) for r in rows if r["company_id"] is not None}
    wanted |= {survivor_of(parents, p["company_id"]) for p in people.values()}
    wanted |= {r["company_id"] for r in removed} | {survivor_of(parents, h.company_id) for h in hidden}
    refs = company_refs(conn, wanted, holds=holds)

    def row_of(r: sqlite3.Row) -> NoGoRow:
        if r["company_id"] is not None:
            target = NoGoTargetCompany(kind="company", company=refs[survivor_of(parents, r["company_id"])])
        else:
            person = people[r["person_id"]]
            target = NoGoTargetPerson(kind="person", person=person_ref(person),
                                      company_name=refs[survivor_of(parents, person["company_id"])].name)
        return NoGoRow(kind="no_go", no_go_id=r["no_go_id"], target=target, reason=r["reason"], quote=r["quote"],
                       note=r["note"], source=r["source"], source_ref=r["source_ref"],
                       source_url=source_url(r["source_ref"]), import_source=import_source(r["source_ref"]),
                       added_at=r["created_at"])

    company_rows = [row_of(r) for r in rows if r["company_id"] is not None]
    person_rows = [row_of(r) for r in rows if r["company_id"] is None]
    removed_rows = [RemovedRow(
        kind="removed", company=RemovedCompany(**refs[r["company_id"]].model_dump(), revision=r["revision"]),
        remove_reason=r["remove_reason"], note=r["status_note"], removed_at=r["status_at"]) for r in removed]
    counts = Counter(r.reason for r in company_rows)
    if removed_rows:
        counts["removed"] = len(removed_rows)
    return NoGoView(
        as_of=now, companies=[*company_rows, *removed_rows], people=person_rows,
        counts_by_reason=[ReasonCount(reason=k, count=n) for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))],
        hidden_companies=[HiddenCompany(company=refs[survivor_of(parents, h.company_id)], kind=h.signals["hide"]["kind"],
                                        reason=h.signals["hide"]["reason"], provisional=bool(h.signals["hide"]["provisional"]))
                          for h in hidden])


def lift(ctx: WriteCtx, no_go_id: int, reason: str) -> Empty:
    """Take one entry off the list. The row is kept; a second lift is refused."""
    conn = ctx.conn
    why = reason.strip()
    row = conn.execute("SELECT * FROM no_go WHERE no_go_id = ?", (no_go_id,)).fetchone()
    if row is None:
        raise ApiProblem("not_found", f"There is no no-go entry {no_go_id}.")
    if not why:
        raise ApiProblem("bad_request", f"Say why you are taking it off the list. {NOT_SAVED}")
    if row["lifted_at"] is not None:
        raise ApiProblem("not_allowed", f"That entry was already taken off the list. {NOT_SAVED}")
    conn.execute("UPDATE no_go SET lifted_at = ?, lift_reason = ? WHERE no_go_id = ?", (ctx.now, why, no_go_id))
    if row["company_id"] is not None:
        company_id = canonical_company(conn, row["company_id"])
    else:
        company_id = canonical_company(conn, conn.execute("SELECT company_id FROM person WHERE person_id = ?",
                                                          (row["person_id"],)).fetchone()[0])
    append_event(conn, entity="no_go", entity_id=no_go_id, company_id=company_id, kind="lifted", actor="devanshu",
                 at=ctx.now, idempotency_key=ctx.event_key(0),
                 detail={"reason": why, "text": f"Taken off the no-go list: {why}"})
    return Empty()
