"""The one-click fixes for a review item: mark ok, set a field with a source, mark no-go, merge (#16).

Only his own no-go (or a size he types) can hide anything here. A fix never touches a `message` row: approve and
the sender already refuse a company or person on the no-go list. Every fix is one write unit, so a failure part way
leaves nothing behind, and the update matches the revision he loaded, so a second tab or a double click settles the
item once.

Rules ported from the API's first copy (`outreach/api/queries/needs_you.py`, #11 Task 20): a fix lands on the
company the item names, a merged-away one included (while merged every read follows it to the survivor, and an
unmerge keeps it where it was); a no-go on an item that names a person is a person no-go; a merge question has no
no-go and no edit.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
from dataclasses import dataclass
from typing import Literal

from outreach.review import words
from outreach.review.errors import ReviewError
from outreach.review.boardfix import edit_board
from outreach.review.hand import (NOT_SAVED, normalize_domain, normalize_sponsorship, parse_hand_size,
                                  write_hand_fact)
from outreach.review.items import clear_item, get_item, resolve_item, settle_siblings, unit
from outreach.review.kinds import KINDS, NO_GO_REASONS, spec_of
from outreach.review.merge import _merge, unmerge_company
from outreach.rules.facts import EvidenceDraft
from outreach.store import append_event, utcnow
from outreach.store.timefmt import iso_utc

__all__ = ["Fix", "FixResult", "apply_fix", "reopen_item", "can_undo"]

_FIX_KINDS = ("ok", "edit", "no_go", "merge")
_PERSON_KEY = "claude-no-go:"
_NOT_MERGE = tuple(k for k in KINDS if k != "merge_candidate")


@dataclass(frozen=True)
class Fix:
    kind: Literal["ok", "edit", "no_go", "merge"]
    field: str | None = None
    value: str | None = None
    source: str | None = None
    reason: str | None = None
    keep: Literal["a", "b"] | None = None


@dataclass(frozen=True)
class FixResult:
    review_id: int
    revision: int
    resolution: str
    note: str
    company_id: int
    settled: tuple[int, ...]


def _bad(text: str) -> ReviewError:
    return ReviewError("bad_request", f"{text} {NOT_SAVED}")


def _family(field: str) -> tuple[str, ...]:
    """The kinds an edit of `field` settles: every kind whose edit sets that field."""
    return tuple(k for k, spec in KINDS.items() if spec.edit_field == field)


def _refuse(kind: str, fix: Fix) -> str:
    """Why the kind does not take this fix, in plain words."""
    if fix.kind == "merge":
        return "Only a possible duplicate can be merged."
    if kind == "merge_candidate":
        if fix.kind == "no_go":
            return "A merge question has no no-go. Merge it or keep them separate."
        return "A merge question has nothing to edit. Merge it or keep them separate."
    return "This item has nothing to edit. Mark it ok or no-go."


def _source(fix: Fix) -> str:
    source = fix.source.strip() if isinstance(fix.source, str) else ""
    if not source:
        raise _bad("Say where you saw it: a fix needs a source.")
    return source


def _check(row: sqlite3.Row, fix: Fix, revision: int) -> None:
    """Refuse before any write: a settled item, a stale revision, a fix the kind does not take, a missing field."""
    if fix.kind not in _FIX_KINDS:
        raise _bad(f"A fix is ok, edit, no-go or merge, not {fix.kind!r}.")
    if row["state"] == "resolved":
        raise ReviewError("not_allowed", "Already resolved.")
    if row["revision"] != revision:
        raise ReviewError("conflict", f"This item changed since you loaded it. {NOT_SAVED}")
    spec = spec_of(row["kind"])
    if fix.kind not in spec.fixes:
        raise ReviewError("not_allowed", _refuse(row["kind"], fix))
    if fix.kind == "edit":
        if fix.field != spec.edit_field:
            raise _bad(f"This item is settled by setting the {spec.edit_field.replace('_', ' ')}.")
        if not isinstance(fix.value, str) or not fix.value.strip():
            raise _bad("An edit needs a value.")
        _source(fix)
    if fix.kind == "no_go":
        _source(fix)
        if fix.reason is not None and fix.reason not in NO_GO_REASONS:
            raise _bad("Pick one of the no-go reasons.")
        if fix.reason is None and _person_key_reason(row) is None:
            raise _bad("Pick one of the no-go reasons.")
    if fix.kind == "merge" and fix.keep not in ("a", "b"):
        raise _bad("Say which company to keep: a or b.")


def _person_key_reason(row: sqlite3.Row) -> str | None:
    """The reason in Claude's suggestion key `claude-no-go:<person id>:<reason>`, on an item that names a person."""
    key = row["evidence_key"]
    if row["person_id"] is None or not key.startswith(_PERSON_KEY):
        return None
    reason = key.rpartition(":")[2]
    return reason if reason in NO_GO_REASONS else None


def _winning_fact(conn: sqlite3.Connection, company_id: int, fact: str) -> dict | None:
    row = conn.execute("SELECT * FROM company_fact WHERE company_id = ? AND fact = ?", (company_id, fact)).fetchone()
    return dict(row) if row is not None else None


def _edit(conn: sqlite3.Connection, row: sqlite3.Row, fix: Fix, revision: int, at: str, detail: dict) -> tuple:
    """Parse, resolve, write the locked hand fact, settle the family. Returns (revision, note, settled)."""
    field, source = fix.field, _source(fix)
    if field == "board_url":
        return _edit_board(conn, row, fix, revision, at, detail)
    if field == "size":
        draft = parse_hand_size(fix.value)
    elif field == "domain":
        value = normalize_domain(conn, fix.value)
        draft = EvidenceDraft("domain", value, "confirmed", "hand", quote=fix.value.strip())
    else:
        value = normalize_sponsorship(fix.value)
        draft = EvidenceDraft("sponsorship", value, "confirmed", "hand", quote=fix.value.strip())
    draft = dataclasses.replace(draft, source_ref=source)
    company_id = row["company_id"]
    detail.update(field=field, value=draft.value, source=source,
                  previous_fact=_winning_fact(conn, company_id, field))
    note = words.fix_note(field, draft.value, source)
    new_revision = resolve_item(conn, row["review_id"], resolution="field_edited", note=note, revision=revision,
                                now=at)
    posting_id = row["posting_id"] if field == "sponsorship" else None
    detail["evidence_id"] = write_hand_fact(conn, company_id, draft, posting_id=posting_id, now=at)
    settled = settle_siblings(conn, company_id, kinds=_family(field), except_id=row["review_id"],
                              note=words.settled_by_field(field), now=at)
    return new_revision, note, settled


def _edit_board(conn: sqlite3.Connection, row: sqlite3.Row, fix: Fix, revision: int, at: str, detail: dict) -> tuple:
    """Replace the board the item is about (`boardfix.edit_board`), resolve, and settle the other board items about
    that same board. A board item about another board of the company stays open: that board is not fixed.
    Returns (revision, note, settled)."""
    source = _source(fix)
    new = edit_board(conn, row, fix.value.strip(), source, now=at, actor="devanshu", detail=detail)
    detail.update(field="board_url", value=new, source=source)
    note = words.fix_note("board_url", new, source)
    new_revision = resolve_item(conn, row["review_id"], resolution="field_edited", note=note, revision=revision,
                                now=at)
    siblings = [r[0] for r in conn.execute(
        "SELECT review_id FROM review_item WHERE state = 'open' AND board_id = ? AND review_id <> ? "
        "ORDER BY review_id", (row["board_id"], row["review_id"]))]
    settled = tuple(i for i in siblings if clear_item(conn, i, words.settled_by_field("job board"), at))
    return new_revision, note, settled


def _sponsorship_quote(conn: sqlite3.Connection, posting_id: int | None) -> str:
    if posting_id is None:
        return ""
    found = conn.execute("SELECT sponsorship_quote FROM posting_decision WHERE posting_id = ?",
                         (posting_id,)).fetchone()
    return (found[0] or "") if found is not None else ""


def _no_go(conn: sqlite3.Connection, row: sqlite3.Row, fix: Fix, revision: int, at: str, detail: dict) -> tuple:
    """Add the no-go row (unless an active one is there), resolve, and on a company no-go settle its other items.
    An item that names a person (Claude's suggestion, #13) is a person no-go and settles nothing else."""
    source = _source(fix)
    person_id = row["person_id"]
    reason = fix.reason or _person_key_reason(row)
    if person_id is not None:
        column, target, quote = "person_id", person_id, words.clean(row["reason"])
    else:
        column, target = "company_id", row["company_id"]
        quote = _sponsorship_quote(conn, row["posting_id"]) if reason == "no_sponsorship" else ""
    found = conn.execute(f"SELECT no_go_id FROM no_go WHERE {column} = ? AND lifted_at IS NULL",
                         (target,)).fetchone()
    created = found is None
    if created:
        cur = conn.execute(
            f"INSERT INTO no_go({column}, reason, quote, note, source, source_ref, created_at) "
            "VALUES (?, ?, ?, 'From Needs you.', 'devanshu', ?, ?) ON CONFLICT DO NOTHING",
            (target, reason, quote, source, at))
        if cur.rowcount != 1:  # never record another row's id as the one this fix added
            raise sqlite3.IntegrityError("the no-go row was not written")
        no_go_id = cur.lastrowid
    else:
        no_go_id = found[0]
    if not created:
        note = words.already_no_go_note()
    elif person_id is not None:
        name = conn.execute("SELECT name FROM person WHERE person_id = ?", (person_id,)).fetchone()
        note = words.person_no_go_note(name[0] if name else "this person", reason)
    else:
        note = words.no_go_note(reason)
    detail.update(source=source, reason=reason, no_go_id=no_go_id, no_go_created=created,
                  **({"person_id": person_id} if person_id is not None else {}))
    new_revision = resolve_item(conn, row["review_id"], resolution="marked_no_go", note=note, revision=revision,
                                now=at)
    settled: tuple[int, ...] = ()
    if person_id is None:
        settled = settle_siblings(conn, row["company_id"], except_id=row["review_id"], note=words.NO_GO_SETTLED,
                                  now=at)
    return new_revision, note, settled


def _merge_fix(conn: sqlite3.Connection, row: sqlite3.Row, fix: Fix, revision: int, at: str, actor: str,
               detail: dict) -> tuple:
    """Resolve the question, merge the side he did not keep into the one he kept, then settle the loser's other
    open items. Its other merge questions are answered by the merge (`follow_merge_questions`): one that still asks
    a real pair moves to the keeper, and the raised ids go in the event so an undo takes them back. Returns
    (revision, note, settled)."""
    other = _int(row["evidence_key"])
    if other is None:
        raise ReviewError("not_allowed", "This question does not name a second company.")
    keep_id, lose_id = (row["company_id"], other) if fix.keep == "a" else (other, row["company_id"])
    names = dict(conn.execute("SELECT company_id, name FROM company WHERE company_id IN (?, ?)", (keep_id, lose_id)))
    note = words.merge_note(names.get(lose_id, f"#{lose_id}"), names.get(keep_id, f"#{keep_id}"))
    # Resolved first, so the merge does not count this question among the ones it answered.
    new_revision = resolve_item(conn, row["review_id"], resolution="field_edited", note=note, revision=revision,
                                now=at)
    pairs, raised = _merge(conn, keep_id=keep_id, lose_id=lose_id, review_id=row["review_id"], at=at, actor=actor)
    rest = settle_siblings(conn, lose_id, kinds=_NOT_MERGE, except_id=row["review_id"],
                           note=words.merged_away_note(names.get(keep_id, f"#{keep_id}")), now=at)
    detail.update(kept_id=keep_id, merged_id=lose_id, raised=raised)
    return new_revision, note, tuple(pairs) + rest


def _int(text) -> int | None:
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def apply_fix(conn: sqlite3.Connection, review_id: int, fix: Fix, *, revision: int, now: str | None = None,
              actor: str = "devanshu") -> FixResult:
    """Settle one open item with one fix, in one write unit, and append its `resolved` event.

    `not_found` for no such item; `not_allowed` when it is already settled or the kind does not take the fix;
    `conflict` when the revision moved on; `bad_request` for a missing source, a missing or bad value, the wrong
    field for the kind, or an unknown reason. Each is raised before anything is written.
    """
    at = iso_utc(now) if now else utcnow()
    with unit(conn):
        row = get_item(conn, review_id)
        _check(row, fix, revision)
        detail: dict = {"fix": fix.kind, "field": None, "value": None, "source": None, "reason": None,
                        "evidence_id": None, "previous_fact": None, "no_go_id": None, "no_go_created": False}
        if fix.kind == "ok":
            note = words.keep_separate_note() if row["kind"] == "merge_candidate" else words.ok_note()
            resolution = "marked_ok"
            new_revision = resolve_item(conn, review_id, resolution=resolution, note=note, revision=revision, now=at)
            settled: tuple[int, ...] = ()
        elif fix.kind == "edit":
            resolution = "field_edited"
            new_revision, note, settled = _edit(conn, row, fix, revision, at, detail)
        elif fix.kind == "no_go":
            resolution = "marked_no_go"
            new_revision, note, settled = _no_go(conn, row, fix, revision, at, detail)
        else:
            resolution = "field_edited"
            new_revision, note, settled = _merge_fix(conn, row, fix, revision, at, actor, detail)
        detail.update(resolution=resolution, note=note, settled=list(settled))
        append_event(conn, entity="review_item", kind="resolved", actor=actor, entity_id=review_id,
                     company_id=row["company_id"], at=at, detail=detail,
                     idempotency_key=f"review-resolved:{review_id}:{new_revision}")
    return FixResult(review_id, new_revision, resolution, note, row["company_id"], tuple(settled))


# ---------------------------------------------------------------------------------------------- undo

LOCKED = "A value you set is locked and cannot be taken back here."
_LIFT_REASON = "Undone from Needs you"


def _resolve_detail(conn: sqlite3.Connection, row: sqlite3.Row) -> dict | None:
    """The detail of the `resolved` event that wrote the item's current resolution, or None (an item settled before
    this package wrote that event)."""
    found = conn.execute("SELECT at, detail FROM event WHERE entity = 'review_item' AND kind = 'resolved' "
                         "AND entity_id = ? ORDER BY event_id DESC LIMIT 1", (row["review_id"],)).fetchone()
    if found is None or found["at"] != row["resolved_at"]:
        return None
    detail = json.loads(found["detail"])
    return detail if detail.get("resolution") == row["resolution"] else None


def _made_here(conn: sqlite3.Connection, no_go_id: int) -> bool:
    """Whether a fix here wrote this no-go row. A row he added on the no-go list, or an import, is never lifted
    by an undo here."""
    return conn.execute(
        "SELECT 1 FROM event WHERE entity = 'review_item' AND kind = 'resolved' "
        "AND json_extract(detail, '$.no_go_id') = ? AND json_extract(detail, '$.no_go_created') = 1 LIMIT 1",
        (no_go_id,)).fetchone() is not None


def _relied_on(conn: sqlite3.Connection, no_go: sqlite3.Row, except_id: int) -> bool:
    """Whether another item he still has settled as a no-go stands on this row: its latest `resolved` event names
    the row, or the API's `review_resolved` event names the same person or company."""
    for (text,) in conn.execute(
            "SELECT (SELECT e.detail FROM event e WHERE e.entity = 'review_item' AND e.entity_id = r.review_id "
            "AND e.kind IN ('resolved', 'review_resolved') ORDER BY e.event_id DESC LIMIT 1) FROM review_item r "
            "WHERE r.state = 'resolved' AND r.resolution = 'marked_no_go' AND r.review_id <> ?", (except_id,)):
        detail = json.loads(text) if text else {}
        if detail.get("no_go_id") == no_go["no_go_id"]:
            return True
        if no_go["person_id"] is not None and detail.get("person_id") == no_go["person_id"]:
            return True
        if no_go["company_id"] is not None and detail.get("company_id") == no_go["company_id"]:
            return True
    return False


def _undo_plan(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """What taking this settling back would do, or the plain reason it cannot be taken back (`not_allowed`)."""
    if row["state"] != "resolved":
        raise ReviewError("not_allowed", "This item is open. There is nothing to take back.")
    resolution = row["resolution"]
    if resolution == "cleared":
        raise ReviewError("not_allowed", "This item cleared by itself. There is nothing to take back.")
    detail = _resolve_detail(conn, row) or {}
    plan = {"previous_resolution": resolution, "settled": list(detail.get("settled") or []), "lift": None,
            "unmerge": None, "raised": list(detail.get("raised") or [])}
    if resolution == "marked_no_go":
        if not detail:
            raise ReviewError("not_allowed", "This no-go was set before it could be taken back here. "
                                             "Lift it from the no-go list.")
        found = conn.execute("SELECT * FROM no_go WHERE no_go_id = ?", (detail.get("no_go_id"),)).fetchone()
        if found is not None and found["lifted_at"] is not None:
            if detail.get("no_go_created"):
                raise ReviewError("not_allowed", "That no-go was lifted since. There is nothing to take back.")
        elif found is not None and _made_here(conn, found["no_go_id"]) and not _relied_on(conn, found,
                                                                                         row["review_id"]):
            plan["lift"] = found["no_go_id"]
    elif resolution == "field_edited":
        if row["kind"] != "merge_candidate":
            raise ReviewError("not_allowed", LOCKED)
        found = conn.execute(
            "SELECT event_id, entity_id, detail FROM event WHERE entity = 'company' AND kind = 'merged' "
            "AND json_extract(detail, '$.review_id') = ? ORDER BY event_id DESC LIMIT 1",
            (row["review_id"],)).fetchone()
        if found is None:
            raise ReviewError("not_allowed", "There is no merge to take back.")
        lose_id, kept_id = found["entity_id"], json.loads(found["detail"]).get("kept_id")
        merged_into = conn.execute("SELECT merged_into FROM company WHERE company_id = ?", (lose_id,)).fetchone()
        if merged_into is not None and merged_into[0] is not None:
            # Only the company's latest merge can be taken back: `unmerge_company` restores what the latest
            # `merged` event recorded, so undoing an older one would undo a merge another question owns.
            latest = conn.execute("SELECT max(event_id) FROM event WHERE entity = 'company' AND kind = 'merged' "
                                  "AND entity_id = ?", (lose_id,)).fetchone()[0]
            if merged_into[0] != kept_id or latest != found["event_id"]:
                raise ReviewError("not_allowed", "That company was merged again since. Undo the later merge first, "
                                                 "or unmerge it from its page.")
            plan["unmerge"] = lose_id
        # Unmerged from the company page since: only the question goes back open.
    return plan


def can_undo(conn: sqlite3.Connection, review_id: int) -> bool:
    """Whether `reopen_item` would take this item's settling back. Writes nothing."""
    try:
        _undo_plan(conn, get_item(conn, review_id))
    except ReviewError:
        return False
    return True


def reopen_item(conn: sqlite3.Connection, review_id: int, *, revision: int, now: str | None = None,
                actor: str = "devanshu") -> FixResult:
    """Take back an ok, a no-go or a merge after the Undo window: the item goes back open (resolution, time and
    note cleared, revision up). A no-go lifts the row a fix here added (`Undone from Needs you`), unless another
    item he still has settled as a no-go stands on it, and leaves a row that was already there; a merge is
    unmerged and the merge questions it moved to the keeper are cleared. The items the settling cleared come back open too, when nothing has moved
    them since. Appends a `reopened` event.

    `not_allowed` for an edit (locked), a cleared item, an open item and a no-go lifted since; `conflict` on a
    stale revision.
    """
    at = iso_utc(now) if now else utcnow()
    with unit(conn):
        row = get_item(conn, review_id)
        # The revision first: a second tab that loaded the settled item learns it changed, not that it is open.
        if row["revision"] != revision:
            raise ReviewError("conflict", f"This item changed since you loaded it. {NOT_SAVED}")
        plan = _undo_plan(conn, row)
        if plan["lift"] is not None:
            conn.execute("UPDATE no_go SET lifted_at = ?, lift_reason = ? WHERE no_go_id = ? AND lifted_at IS NULL",
                         (at, _LIFT_REASON, plan["lift"]))
        if plan["unmerge"] is not None:
            company_revision = conn.execute("SELECT revision FROM company WHERE company_id = ?",
                                            (plan["unmerge"],)).fetchone()[0]
            unmerge_company(conn, plan["unmerge"], revision=company_revision, now=at, actor=actor)
        taken_back = [rid for rid in plan["raised"] if clear_item(conn, rid, words.MERGE_TAKEN_BACK, at)]
        reopen = ("UPDATE review_item SET state = 'open', resolution = NULL, resolved_at = NULL, resolution_note = '', "
                  "revision = revision + 1 WHERE review_id = ? AND state = 'resolved'")
        conn.execute(reopen + " AND revision = ?", (review_id, revision))
        restored = [rid for rid in plan["settled"] if conn.execute(
            reopen + " AND resolution = 'cleared' AND resolved_at = ?", (rid, row["resolved_at"])).rowcount]
        new_revision = revision + 1
        append_event(conn, entity="review_item", kind="reopened", actor=actor, entity_id=review_id,
                     company_id=row["company_id"], at=at, idempotency_key=f"review-reopened:{review_id}:{new_revision}",
                     detail={"text": words.UNDONE_TEXT,
                             "previous_resolution": plan["previous_resolution"], "lifted_no_go_id": plan["lift"],
                             "unmerged_id": plan["unmerge"], "reopened": restored,
                             "cleared": taken_back})
    return FixResult(review_id, new_revision, "open", "", row["company_id"], ())
