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
import sqlite3
from dataclasses import dataclass
from typing import Literal

from outreach.review import words
from outreach.review.errors import ReviewError
from outreach.review.hand import (NOT_SAVED, normalize_domain, normalize_sponsorship, parse_hand_size,
                                  write_hand_fact)
from outreach.review.items import get_item, resolve_item, settle_siblings, unit
from outreach.review.kinds import KINDS, NO_GO_REASONS, spec_of
from outreach.rules.facts import EvidenceDraft
from outreach.store import append_event, utcnow
from outreach.store.timefmt import iso_utc

__all__ = ["Fix", "FixResult", "apply_fix"]

_FIX_KINDS = ("ok", "edit", "no_go", "merge")
_PERSON_KEY = "claude-no-go:"


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
        raise ReviewError("not_allowed", "Replacing a job board from here is not available yet.")
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
        no_go_id = conn.execute(
            f"INSERT INTO no_go({column}, reason, quote, note, source, source_ref, created_at) "
            "VALUES (?, ?, ?, 'From Needs you.', 'devanshu', ?, ?) ON CONFLICT DO NOTHING",
            (target, reason, quote, source, at)).lastrowid
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
            raise ReviewError("not_allowed", "Merging from here is not available yet.")
        detail.update(resolution=resolution, note=note, settled=list(settled))
        append_event(conn, entity="review_item", kind="resolved", actor=actor, entity_id=review_id,
                     company_id=row["company_id"], at=at, detail=detail,
                     idempotency_key=f"review-resolved:{review_id}:{new_revision}")
    return FixResult(review_id, new_revision, resolution, note, row["company_id"], tuple(settled))
