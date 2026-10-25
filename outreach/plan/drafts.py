"""LinkedIn note drafts (#15, Task 7): save, confirm, read. The one writer of `linkedin_draft`.

The rule this module enforces: only Devanshu moves a reason line past `proposed`. A save by `claude` always stores
`proposed` and can never overwrite a confirmed or written line, or any draft he saved himself (`DraftLocked`).
Every save recomputes the checks with `outreach.plan.notecheck.check_note`; a note that fails a block check is
stored with it and is never ready. Writes: `linkedin_draft` and `event` only.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from outreach.drafts.checks import load_banned
from outreach.plan.config import PlanConfig, default_plan_config
from outreach.plan.notecheck import READY_STATES, Fact, NoteCheck, check_note, load_unconfirmed
from outreach.store.atomic import atomic
from outreach.store.timefmt import utcnow
from outreach.store.writes import append_event

__all__ = ["DraftLocked", "LinkedinDraft", "save_linkedin_draft", "confirm_reason", "get_draft", "get_draft_by_id"]

_REPO_MAIL = Path(__file__).resolve().parents[2] / "mail"


class DraftLocked(RuntimeError):
    """Claude tried to overwrite a confirmed or written reason line, or a draft Devanshu saved."""


@dataclass(frozen=True)
class LinkedinDraft:
    draft_id: int
    person_id: int
    posting_id: int | None
    body: str
    reason_line: str
    reason_state: str
    facts: tuple[Fact, ...]
    checks: tuple[NoteCheck, ...]
    saved_by: str
    prepared_by_version: str
    created_at: str
    updated_at: str
    revision: int


def _draft(row: sqlite3.Row) -> LinkedinDraft:
    return LinkedinDraft(
        draft_id=row["draft_id"], person_id=row["person_id"], posting_id=row["posting_id"], body=row["body"],
        reason_line=row["reason_line"], reason_state=row["reason_state"],
        facts=tuple(Fact(f["text"], f["label"], f["url"], bool(f["in_note"])) for f in json.loads(row["facts"])),
        checks=tuple(NoteCheck.from_json(c) for c in json.loads(row["checks"])), saved_by=row["saved_by"],
        prepared_by_version=row["prepared_by_version"], created_at=row["created_at"], updated_at=row["updated_at"],
        revision=row["revision"])


def get_draft(conn: sqlite3.Connection, person_id: int) -> LinkedinDraft | None:
    row = conn.execute("SELECT * FROM linkedin_draft WHERE person_id = ?", (person_id,)).fetchone()
    return _draft(row) if row else None


def get_draft_by_id(conn: sqlite3.Connection, draft_id: int) -> LinkedinDraft | None:
    row = conn.execute("SELECT * FROM linkedin_draft WHERE draft_id = ?", (draft_id,)).fetchone()
    return _draft(row) if row else None


def _fact(value) -> Fact:
    if isinstance(value, Fact):
        return value
    return Fact(text=str(value["text"]), label=str(value.get("label", "")), url=str(value.get("url", "")),
                in_note=bool(value.get("in_note", False)))


def _facts_json(facts: Sequence[Fact]) -> str:
    return json.dumps([{"text": f.text, "label": f.label, "url": f.url, "in_note": f.in_note} for f in facts])


def _banned(cfg: PlanConfig, banned: Sequence | None) -> Sequence:
    if banned is not None:
        return banned
    # An unusable list comes back empty, and `check_note` blocks on an empty list.
    return load_banned(cfg.get("note", "banned_path"), scopes=tuple(cfg.get("note", "banned_scopes")))[0]


def _unconfirmed(unconfirmed: Sequence[str] | None) -> Sequence[str] | None:
    if unconfirmed is not None:
        return unconfirmed
    home = Path(os.environ.get("OUTREACH_MAIL_HOME") or _REPO_MAIL)
    return load_unconfirmed(home / "me.md")  # None when unreadable: the note is blocked, not passed


def save_linkedin_draft(conn: sqlite3.Connection, *, person_id: int, body: str, reason_line: str,
                        facts: Sequence[Fact | dict], saved_by: Literal["claude", "devanshu"],
                        posting_id: int | None = None, prepared_by_version: str = "", revision: int | None = None,
                        cfg: PlanConfig | None = None, banned: Sequence | None = None,
                        unconfirmed: Sequence[str] | None = None) -> LinkedinDraft:
    """Insert or update the person's one draft and recompute its checks.

    `saved_by 'claude'` stores `reason_state 'proposed'` and raises `DraftLocked` when the stored state is
    `confirmed` or `written`, or when the stored `saved_by` is `'devanshu'` (his body-only edit leaves the state
    `proposed`, and prepare must still never overwrite it). `saved_by 'devanshu'` stores `written` when `reason_line`
    differs from the stored line (a new draft stores `''` as the stored line), else keeps the stored state. A
    `revision` that is not the stored one raises `LookupError`. `ValueError` for a blank body or an unknown
    `saved_by`; `LookupError` for an unknown person. `banned` defaults to the configured list and an unreadable one
    blocks the note; `unconfirmed` defaults to me.md and an unreadable file blocks it too.
    """
    if saved_by not in ("claude", "devanshu"):
        raise ValueError(f"saved_by must be 'claude' or 'devanshu', got {saved_by!r}")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("a note needs a body")
    cfg = cfg or default_plan_config()
    facts = tuple(_fact(f) for f in facts)
    reason_line = reason_line or ""
    banned_list = _banned(cfg, banned)
    unconfirmed_list = _unconfirmed(unconfirmed)
    with atomic(conn, "save_linkedin_draft"):
        person = conn.execute("SELECT company_id FROM person WHERE person_id = ?", (person_id,)).fetchone()
        if person is None:
            raise LookupError(f"person {person_id} does not exist")
        stored = conn.execute("SELECT * FROM linkedin_draft WHERE person_id = ?", (person_id,)).fetchone()
        if stored is not None and saved_by == "claude" and (
                stored["reason_state"] in READY_STATES or stored["saved_by"] == "devanshu"):
            raise DraftLocked(f"draft {stored['draft_id']} is his: a confirmed or written line, or his own save")
        if revision is not None and (stored is None or stored["revision"] != revision):
            raise LookupError(f"draft revision {revision} is not current")
        if saved_by == "claude":
            state = "proposed"
        else:
            state = stored["reason_state"] if stored is not None else "proposed"
            if reason_line != (stored["reason_line"] if stored is not None else ""):
                state = "written"
        checks = check_note(body, reason_line, state, facts, cfg=cfg, banned=banned_list, unconfirmed=unconfirmed_list)
        checks_json = json.dumps([c.to_json() for c in checks])
        now = utcnow()
        if stored is None:
            # Another writer may have saved this person's first draft since the read above: the unique person
            # key turns that into a stale write, not a raw IntegrityError.
            cur = conn.execute(
                "INSERT INTO linkedin_draft(person_id, posting_id, body, reason_line, reason_state, facts, checks, "
                "saved_by, prepared_by_version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(person_id) DO NOTHING",
                (person_id, posting_id, body, reason_line, state, _facts_json(facts), checks_json, saved_by,
                 prepared_by_version, now, now))
            if cur.rowcount == 0:
                raise LookupError(f"person {person_id} got a draft from another writer first")
            draft_id = cur.lastrowid
        else:
            draft_id = stored["draft_id"]
            # The revision read above is in the WHERE, so a writer that got in between makes this change nothing.
            cur = conn.execute(
                "UPDATE linkedin_draft SET posting_id = ?, body = ?, reason_line = ?, reason_state = ?, facts = ?, "
                "checks = ?, saved_by = ?, prepared_by_version = ?, updated_at = ?, revision = revision + 1 "
                "WHERE draft_id = ? AND revision = ?",
                (posting_id if posting_id is not None else stored["posting_id"], body, reason_line, state,
                 _facts_json(facts), checks_json, saved_by, prepared_by_version or stored["prepared_by_version"],
                 now, draft_id, stored["revision"]))
            if cur.rowcount == 0:
                raise LookupError(f"draft {draft_id} was changed by another writer")
        saved = get_draft_by_id(conn, draft_id)
        append_event(conn, entity="linkedin", kind="draft_saved", actor=saved_by, entity_id=draft_id,
                     company_id=person["company_id"],
                     detail={"personId": person_id, "reasonState": state, "revision": saved.revision,
                             "blocks": sum(c.severity == "block" for c in checks)})
    return saved


def confirm_reason(conn: sqlite3.Connection, draft_id: int, revision: int, *, cfg: PlanConfig | None = None,
                   banned: Sequence | None = None, unconfirmed: Sequence[str] | None = None) -> LinkedinDraft:
    """Devanshu's one key: the reason line becomes `confirmed`, `saved_by 'devanshu'`, and the checks are redone.

    `LookupError` for an unknown draft or a stale `revision`; `ValueError` for an empty reason line. Confirming does
    not make a blocked note ready (`is_ready` still needs no block check)."""
    cfg = cfg or default_plan_config()
    banned_list = _banned(cfg, banned)
    unconfirmed_list = _unconfirmed(unconfirmed)
    with atomic(conn, "confirm_reason"):
        stored = conn.execute("SELECT * FROM linkedin_draft WHERE draft_id = ?", (draft_id,)).fetchone()
        if stored is None:
            raise LookupError(f"draft {draft_id} does not exist")
        if stored["revision"] != revision:
            raise LookupError(f"draft revision {revision} is not current")
        if not stored["reason_line"].strip():
            raise ValueError("there is no reason line to confirm")
        current = _draft(stored)
        checks = check_note(current.body, current.reason_line, "confirmed", current.facts, cfg=cfg,
                            banned=banned_list, unconfirmed=unconfirmed_list)
        cur = conn.execute(
            "UPDATE linkedin_draft SET reason_state = 'confirmed', saved_by = 'devanshu', checks = ?, "
            "updated_at = ?, revision = revision + 1 WHERE draft_id = ? AND revision = ?",
            (json.dumps([c.to_json() for c in checks]), utcnow(), draft_id, revision))
        if cur.rowcount == 0:
            raise LookupError(f"draft {draft_id} was changed by another writer")
        person = conn.execute("SELECT company_id FROM person WHERE person_id = ?", (stored["person_id"],)).fetchone()
        saved = get_draft_by_id(conn, draft_id)
        append_event(conn, entity="linkedin", kind="draft_confirmed", actor="devanshu", entity_id=draft_id,
                     company_id=person["company_id"],
                     detail={"personId": stored["person_id"], "revision": saved.revision})
    return saved
