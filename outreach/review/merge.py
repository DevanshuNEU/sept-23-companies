"""Merge two companies from a merge question, and take a merge back (#16; this plan owns both).

A merge writes no `no_go` row and moves no other row: people, touches, messages, holds and no-gos of the company
that goes away count for the survivor because every reader follows `merged_into`, and an unmerge gives each company
back exactly its own. Identity matches names through `company_name_key` and follows `merged_into`, so a merge needs
no name-key move.

Rules ported from the API's first copy (`outreach/api/queries/needs_you.py`, #11 Task 20): a merge must not release
mail the loser stops (the paused-loser ruling), a merge into or of a merged company is a conflict, a merge that
would loop is a conflict, and an unmerge restores the removed reason too.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from outreach.identity import follow_merged
from outreach.review import words
from outreach.review.errors import ReviewError
from outreach.review.hand import NOT_SAVED
from outreach.review.items import clear_item, get_item, unit
from outreach.store import add_alias, append_event, utcnow
from outreach.store.timefmt import iso_utc

__all__ = ["MergeSide", "MergeCompare", "merge_compare", "merge_companies", "unmerge_company"]

MERGED_ELSEWHERE = ("One of these was already merged into another company. Keep them separate here, then merge from "
                    "that company's page.")
_LOST_STOP = {"paused": "This one is paused. Keep the paused company, or unpause it first.",
              "closed": "This one is closed. Keep the closed company, or reopen it first.",
              "removed": "This one is removed. Keep the removed company, or restore it first."}
_STATUSES = ("candidate", "backlog", "working", "closed", "removed")


@dataclass(frozen=True)
class MergeSide:
    company_id: int
    name: str
    domain: str | None
    size: str | None
    stage: str | None
    aliases: tuple[str, ...]
    boards: tuple[str, ...]
    open_postings: int
    people: int
    touches: int
    status: str


@dataclass(frozen=True)
class MergeCompare:
    a: MergeSide
    b: MergeSide
    why: str
    suggested_keep: Literal["a", "b"]
    warning: str | None = None  # said on the confirm: a tie company collects name-only mentions of either


def _int(text) -> int | None:
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _company(conn: sqlite3.Connection, company_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM company WHERE company_id = ?", (company_id,)).fetchone()


def _side(conn: sqlite3.Connection, company_id: int) -> MergeSide:
    c = _company(conn, company_id)
    if c is None:
        raise ReviewError("not_found", f"There is no company {company_id}.")
    facts = {r["fact"]: r["value"] for r in conn.execute(
        "SELECT fact, value FROM company_fact WHERE company_id = ? AND fact IN ('domain', 'size', 'stage')",
        (company_id,))}
    one = lambda sql: conn.execute(sql, (company_id,)).fetchone()[0]  # noqa: E731
    return MergeSide(
        company_id=company_id, name=c["name"], domain=facts.get("domain"), size=facts.get("size"),
        stage=facts.get("stage"),
        aliases=tuple(r[0] for r in conn.execute(
            "SELECT alias FROM company_alias WHERE company_id = ? ORDER BY alias", (company_id,))),
        boards=tuple(f"{r[0]}:{r[1]}" for r in conn.execute(
            "SELECT ats, token FROM board WHERE company_id = ? ORDER BY board_id", (company_id,))),
        open_postings=one("SELECT count(*) FROM posting WHERE company_id = ? AND closed_at IS NULL"),
        people=one("SELECT count(*) FROM person WHERE company_id = ?"),
        touches=one("SELECT count(*) FROM touch t JOIN person p ON p.person_id = t.person_id WHERE p.company_id = ?"),
        status=c["status"])


def _is_tie(conn: sqlite3.Connection, company_id: int) -> bool:
    """A tie company (#31 identity rule 4): its `created` event says `"tie": true`."""
    return conn.execute(
        "SELECT 1 FROM event WHERE company_id = ? AND entity = 'company' AND kind = 'created' "
        "AND json_extract(detail, '$.tie') = 1 LIMIT 1", (company_id,)).fetchone() is not None


def _pair(row: sqlite3.Row) -> tuple[int, int]:
    other = _int(row["evidence_key"])
    if other is None:
        raise ReviewError("not_allowed", "This question does not name a second company.")
    return row["company_id"], other


def merge_compare(conn: sqlite3.Connection, review_id: int) -> MergeCompare:
    """Both companies of a merge question side by side. `a` is the item's company, `b` the one its key names; the
    suggestion keeps the side with more touches, then more people, then the lower id."""
    row = get_item(conn, review_id)
    if row["kind"] != "merge_candidate":
        raise ReviewError("not_allowed", "Only a possible duplicate has two sides to compare.")
    a_id, b_id = _pair(row)
    a, b = _side(conn, a_id), _side(conn, b_id)
    rank = lambda side: (side.touches, side.people, -side.company_id)  # noqa: E731
    keep = "a" if rank(a) >= rank(b) else "b"
    warning = words.TIE_WARNING if _is_tie(conn, a_id) or _is_tie(conn, b_id) else None
    return MergeCompare(a, b, row["reason"], keep, warning)


def _stops_mail(company: sqlite3.Row) -> bool:
    return company["paused_at"] is not None or company["status"] in ("closed", "removed")


def _refuse_lost_stop(keep: sqlite3.Row, lose: sqlite3.Row) -> None:
    """A merge must not release mail the loser stops. The sender reads only the survivor's pause and status, and a
    merge clears the loser's pause (the company CHECKs force it), so a paused loser needs a keeper that stops mail
    too; a closed or removed loser needs a closed or removed keeper. Keeping the stopped company is always fine."""
    if lose["status"] in ("closed", "removed") and keep["status"] not in ("closed", "removed"):
        raise ReviewError("not_allowed", _LOST_STOP[lose["status"]])
    if lose["paused_at"] is not None and not _stops_mail(keep):
        raise ReviewError("not_allowed", _LOST_STOP["paused"])


def _settle_pairs(conn: sqlite3.Connection, lose_id: int, loser_name: str, at: str) -> list[int]:
    """Clear the open merge questions the merge answered: those naming the loser (or a company merged into it)
    whose two sides now resolve to one company, or whose pair another open question already asks."""
    gone = {r[0] for r in conn.execute(
        "WITH RECURSIVE gone(id) AS (SELECT ? UNION SELECT c.company_id FROM company c JOIN gone g "
        "ON c.merged_into = g.id) SELECT id FROM gone", (lose_id,))}
    rows = conn.execute("SELECT review_id, company_id, evidence_key FROM review_item WHERE state = 'open' "
                        "AND kind = 'merge_candidate' ORDER BY review_id").fetchall()
    pairs: dict[int, frozenset[int]] = {}
    for r in rows:
        other = _int(r["evidence_key"])
        if other is None:
            continue
        try:
            pairs[r["review_id"]] = frozenset((follow_merged(conn, r["company_id"]), follow_merged(conn, other)))
        except (LookupError, ValueError):
            continue
    asked = Counter(pairs.values())
    note = words.same_company_note(loser_name)
    cleared = []
    for r in rows:
        rid = r["review_id"]
        if rid not in pairs or (r["company_id"] not in gone and _int(r["evidence_key"]) not in gone):
            continue
        pair = pairs[rid]
        if (len(pair) == 1 or asked[pair] > 1) and clear_item(conn, rid, note, at):
            asked[pair] -= 1
            cleared.append(rid)
    return cleared


def _merge(conn: sqlite3.Connection, *, keep_id: int, lose_id: int, review_id: int | None, at: str,
           actor: str) -> list[int]:
    """The merge itself. Returns the merge questions it settled."""
    keep, lose = _company(conn, keep_id), _company(conn, lose_id)
    if keep is None or lose is None:
        raise ReviewError("not_found", f"There is no company {keep_id if keep is None else lose_id}.")
    if keep_id == lose_id:
        raise ReviewError("not_allowed", "They are already one company.")
    if keep["merged_into"] is not None or lose["merged_into"] is not None:
        try:
            one = follow_merged(conn, keep_id) == follow_merged(conn, lose_id)
        except ValueError:
            one = False
        raise ReviewError("conflict", "These are already one company." if one else MERGED_ELSEWHERE)
    _refuse_lost_stop(keep, lose)
    conn.execute(
        "UPDATE company SET merged_into = ?, status = 'removed', remove_reason = 'merged', close_reason = NULL, "
        "paused_at = NULL, pause_note = '', status_at = ?, revision = revision + 1 WHERE company_id = ?",
        (keep_id, at, lose_id))
    try:
        follow_merged(conn, lose_id)  # identity's own walk: a loop is corruption, never a merge
    except ValueError:
        raise ReviewError("conflict", f"That merge would make a loop. {NOT_SAVED}") from None
    try:
        alias_added = add_alias(conn, keep_id, lose["name"], "merge")
    except ValueError:
        # The key is taken: by the loser itself (it resolves to the keeper through merged_into) or by a third
        # company (identity raises its own question for that). No alias row moves either way.
        alias_added = False
    revision = lose["revision"] + 1
    append_event(conn, entity="company", kind="merged", actor=actor, entity_id=lose_id, company_id=lose_id, at=at,
                 idempotency_key=f"company-merged:{lose_id}:{revision}", detail={
                     "previous_status": lose["status"], "previous_close_reason": lose["close_reason"],
                     "previous_remove_reason": lose["remove_reason"], "previous_paused_at": lose["paused_at"],
                     "previous_pause_note": lose["pause_note"], "kept_id": keep_id, "review_id": review_id,
                     "alias_added": alias_added,
                     "text": words.clean(f"Merged into {keep['name']} (#{keep_id}) by you.")})
    return _settle_pairs(conn, lose_id, lose["name"], at)


def merge_companies(conn: sqlite3.Connection, *, keep_id: int, lose_id: int, review_id: int | None = None,
                    now: str | None = None, actor: str = "devanshu") -> None:
    """Merge `lose_id` into `keep_id`: the loser gets `merged_into`, status `removed` and remove reason `merged`
    (its close reason and pause are cleared, as the company CHECKs need, and kept in the `merged` event for an
    unmerge); the loser's name becomes an alias of the keeper unless its key is taken; merge questions the merge
    answered are settled. `conflict` when either company is already merged or the merge would loop; `not_allowed`
    when it would release mail the loser stops, or for one company twice; `not_found` for no such company."""
    at = iso_utc(now) if now else utcnow()
    with unit(conn):
        _merge(conn, keep_id=keep_id, lose_id=lose_id, review_id=review_id, at=at, actor=actor)


def merged_event(conn: sqlite3.Connection, company_id: int) -> dict:
    """The detail of the company's latest `merged` event; empty when there is none."""
    found = conn.execute("SELECT detail FROM event WHERE entity = 'company' AND entity_id = ? AND kind = 'merged' "
                         "ORDER BY event_id DESC LIMIT 1", (company_id,)).fetchone()
    return json.loads(found[0]) if found else {}


def unmerge_company(conn: sqlite3.Connection, company_id: int, *, revision: int, now: str | None = None,
                    actor: str = "devanshu") -> int:
    """Take a merged company back out: `merged_into` and the `merged` remove reason cleared, its status (with the
    close reason, removed reason and pause it had) restored from the latest `merged` event, an `unmerged` event.
    The alias the merge added stays with the survivor and nothing else moves back. Works when the keeper has since
    been merged elsewhere. Returns the new revision."""
    at = iso_utc(now) if now else utcnow()
    with unit(conn):
        row = _company(conn, company_id)
        if row is None:
            raise ReviewError("not_found", f"There is no company {company_id}.")
        if row["merged_into"] is None:
            raise ReviewError("not_allowed", "Not merged.")
        if row["revision"] != revision:
            raise ReviewError("conflict", f"The company changed since you loaded it. {NOT_SAVED}")
        was = merged_event(conn, company_id)
        status = was.get("previous_status")
        if status not in _STATUSES or (status == "removed" and not was.get("previous_remove_reason")):
            status = "candidate"  # merged by some other writer, with no record of what it was
        conn.execute(
            "UPDATE company SET merged_into = NULL, status = ?, remove_reason = ?, close_reason = ?, paused_at = ?, "
            "pause_note = ?, status_at = ?, revision = revision + 1 WHERE company_id = ? AND revision = ?",
            (status, was.get("previous_remove_reason") if status == "removed" else None,
             (was.get("previous_close_reason") or "other") if status == "closed" else None,
             was.get("previous_paused_at") if status == "working" else None,
             was.get("previous_pause_note") or "", at, company_id, revision))
        new = revision + 1
        append_event(conn, entity="company", kind="unmerged", actor=actor, entity_id=company_id,
                     company_id=company_id, at=at, idempotency_key=f"company-unmerged:{company_id}:{new}",
                     detail={"kept_id": row["merged_into"], "restored_status": status, "text": "Unmerged by you."})
    return new
