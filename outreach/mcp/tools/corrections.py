"""Person corrections (#181): update_person, mark_email_bounced and move_person.

Claude can fix a person after the fact. Each change applies at once and is one unit: the person row, one `event`
(`entity=person`, actor claude, the old and new value of every field that changed) and one flagged review item for
Devanshu, who clears it with ok. Nothing here writes a touch or a message. A cleared address is stopped by the
sender's own `no_address` hold, so a drafted or approved email to it never goes out.

A person on the email channel must have an address, so clearing it moves them to the LinkedIn channel (`no_email`),
and giving an address to a LinkedIn person makes them an email contact, as `add_people` already does. A replacement
address is refused after a sent first email that did not bounce: the follow-ups ride that Gmail thread and a new
address may be a different human. Removing a person is not here (#183).

A cleared address goes on the dead-address list (`bounced_address`, source `marked`, with the person's id), which the
sender refuses for good; no correction may put an address from that list back on anyone (#9 multi-sender ruling,
2026-11-12).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Literal

from pydantic import Field

from outreach.drafts.followups import no_go_sets
from outreach.mcp.guard import MOVE_ONLY_COLUMNS, Grant
from outreach.mcp.tools import Ctx, Input, ToolError, ToolSpec
from outreach.mcp.tools.people import refuse_bounced
from outreach.mcp.tools.reads import MAX_ID, SENT_STATES, Family, company_row, resolve
from outreach.mcp.tools.research import clean, raise_flag_in
from outreach.sources.keys import EMAIL, linkedin_key
from outreach.store import append_event, transaction

__all__ = ["TOOLS", "PERSON_EDIT_COLUMNS", "already_sent", "record_edit"]

PERSON_EDIT_COLUMNS = frozenset({"name", "title", "role_kind", "email", "linkedin_url", "profile_text", "notes",
                                 "channel", "linkedin_reason", "updated_at", "revision"})
SHOWN_CHARS = 60  # how much of one value a review reason quotes
REASON_CHARS = 300


def _person(conn: sqlite3.Connection, person_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM person WHERE person_id = ?", (person_id,)).fetchone()
    if row is None:
        raise ToolError("not_found", f"No person {person_id}.", "Use a person id from company or person.")
    return row


def already_sent(conn: sqlite3.Connection, person_id: int, *, thread_only: bool = False) -> bool:
    """Whether a first email went to this person on a thread that has not bounced: a sent touch, an import touch
    (contacted before the system) or a first message in a sent state. A bounced thread is over. `thread_only`
    leaves the import touch out: it has no Gmail thread, so no follow-up can ride it."""
    touch = conn.execute("SELECT touch_id, state, origin, first_sent_at FROM touch WHERE person_id = ?",
                         (person_id,)).fetchone()
    if touch is None or touch["state"] == "bounced":
        return False
    if touch["first_sent_at"] is not None or (touch["origin"] == "import" and not thread_only):
        return True
    marks = ",".join("?" * len(SENT_STATES))
    return conn.execute(f"SELECT 1 FROM message WHERE touch_id = ? AND seq = 0 AND state IN ({marks})",
                        (touch["touch_id"], *SENT_STATES)).fetchone() is not None


def _shown(value) -> str:
    if value is None or value == "":
        return "(none)"
    text = clean(str(value))
    return text if len(text) <= SHOWN_CHARS else text[:SHOWN_CHARS - 3] + "..."


def record_edit(ctx: Ctx, person: sqlite3.Row, changes: dict[str, tuple], *, kind: str, summary: str,
                company_id: int | None = None, extra: dict | None = None) -> dict:
    """The event and the review item for one edit, inside the caller's write transaction. `changes` maps each changed
    field to (old, new); `summary` is the review reason. The item's key hashes the person, their revision and every
    old and new value, so a change made again after a later edit is a new notice, not a resolved one. `company_id`
    is the company the person is at after the edit (the item and the event go there)."""
    conn = ctx.conn
    survivor = Family(conn).survivor(company_id if company_id is not None else person["company_id"])
    detail = {field: {"old": old, "new": new} for field, (old, new) in changes.items()}
    detail.update(extra or {})
    append_event(conn, entity="person", kind=kind, actor="claude", entity_id=person["person_id"],
                 company_id=survivor, at=ctx.now, detail=detail)
    digest = hashlib.sha256(json.dumps(
        [person["person_id"], person["revision"], sorted((f, o, n) for f, (o, n) in changes.items())],
        default=str).encode("utf-8")).hexdigest()[:16]
    raised = raise_flag_in(ctx, company_id=survivor, reason=summary, person_id=person["person_id"],
                           evidence_key=f"claude-edit:{digest}", detail={"edit": kind})
    return {"review_id": raised["review_id"], "review_created": raised["created"]}


def _email_changes(conn: sqlite3.Connection, person: sqlite3.Row, new_email: str | None) -> dict[str, tuple]:
    """The email edit and the channel it implies, as field changes. Refuses a taken address."""
    old_email = person["email"]
    if new_email == old_email:
        return {}
    if new_email is not None:
        refuse_bounced(conn, new_email, person["name"])
        other = conn.execute("SELECT person_id, name FROM person WHERE email = ? AND person_id <> ?",
                             (new_email, person["person_id"])).fetchone()
        if other is not None:
            raise ToolError("email_in_use", f"{new_email} is already {other['name']}'s address (person "
                                            f"{other['person_id']}).",
                            f"Check you have the right person. If {other['name']} is the same person, use "
                            f"person {other['person_id']}. Nothing was written.")
        # Clearing first does not get round this: with no address on record only a real thread still refuses.
        if already_sent(conn, person["person_id"], thread_only=old_email is None):
            raise ToolError("email_sent_to", f"A first email already went to {old_email} and it did not bounce.",
                            "Follow-ups ride that thread, and the new address may be someone else. Clear the old "
                            "address with mark_email_bounced if it is dead, or ask Devanshu. Nothing was written.")
    changes: dict[str, tuple] = {"email": (old_email, new_email)}
    if new_email is None and person["channel"] == "email":
        changes["channel"] = ("email", "linkedin")
        changes["linkedin_reason"] = (person["linkedin_reason"], "no_email")
    elif new_email is not None and person["channel"] == "linkedin" and (
            old_email is None or person["linkedin_reason"] == "no_email"):
        # Only an address that fills a gap makes them an email contact. A LinkedIn person who already had an address
        # (alum first, past budget) stays where their reason put them when the address is corrected.
        changes["channel"] = ("linkedin", "email")
        changes["linkedin_reason"] = (person["linkedin_reason"], None)
    return changes


def _channel_after(person: sqlite3.Row, changes: dict[str, tuple]) -> str:
    return changes["channel"][1] if "channel" in changes else person["channel"]


def _write(conn: sqlite3.Connection, person_id: int, changes: dict[str, tuple], now: str) -> None:
    sets = [f"{field} = ?" for field in changes]
    try:
        conn.execute(f"UPDATE person SET {', '.join(sets)}, updated_at = ?, revision = revision + 1 "
                     f"WHERE person_id = ?", (*(new for _old, new in changes.values()), now, person_id))
    except sqlite3.IntegrityError as err:
        if "linkedin_url" in str(err):
            raise ToolError("linkedin_in_use", "That LinkedIn profile is already on another person.",
                            "Use that person instead. Nothing was written.") from None
        if "email" in str(err):
            raise ToolError("email_in_use", "That address is already on another person.",
                            "Use that person instead. Nothing was written.") from None
        raise


# ---------------------------------------------------------------------------------------------- update_person

class UpdatePersonInput(Input):
    person_id: int = Field(ge=1, le=MAX_ID)
    name: str | None = Field(None, max_length=200)
    title: str | None = Field(None, max_length=200)
    role_kind: Literal["engineer", "manager", "founder", "recruiter", "alum", "other"] | None = None
    email: str | None = Field(None, max_length=254, description="A new address, or null to clear it.")
    linkedin_url: str | None = Field(None, max_length=300, description="linkedin.com/in/..., or null to clear it.")
    profile_text: str | None = Field(None, max_length=8000)
    notes: str | None = Field(None, max_length=2000)


def _wanted(args: UpdatePersonInput) -> dict:
    """The fields sent, cleaned. Only email and linkedin_url may be sent as null (clear)."""
    sent = args.model_fields_set - {"person_id"}
    out: dict = {}
    for field in sorted(sent):
        value = getattr(args, field)
        if value is None and field not in ("email", "linkedin_url"):
            raise ToolError("bad_input", f"{field} cannot be null.",
                            "Send a value, or leave the field out. For text, send an empty string to empty it. "
                            "Nothing was written.")
        if field == "name":
            value = " ".join(value.split())
            if not value:
                raise ToolError("bad_input", "name is empty.", "Send the person's name. Nothing was written.")
        elif field in ("title", "profile_text", "notes"):
            value = value.strip()
        elif field == "email" and value is not None:
            value = value.strip().lower() or None
            if value is not None and not EMAIL.fullmatch(value):
                raise ToolError("bad_input", f"{value!r} is not an email address.",
                                "Send name@domain, or null to clear it. Nothing was written.")
        elif field == "linkedin_url" and value is not None:
            key = linkedin_key(value) if value.strip() else None
            if key is None and value.strip():
                raise ToolError("bad_input", "linkedin_url is not a LinkedIn profile link.",
                                "Send https://www.linkedin.com/in/<their name>, or null to clear it. Nothing was "
                                "written.")
            value = key
        out[field] = value
    return out


def update_person(ctx: Ctx, args: UpdatePersonInput) -> dict:
    conn = ctx.conn
    person = _person(conn, args.person_id)
    wanted = _wanted(args)
    changes: dict[str, tuple] = {}
    for field, value in wanted.items():
        if field == "email":
            changes.update(_email_changes(conn, person, value))
        elif person[field] != value:
            changes[field] = (person[field], value)
    if not changes:
        return {"person_id": person["person_id"], "changed": [], "review_id": None, "channel": person["channel"],
                "message": "Nothing to change."}
    said = "; ".join(f"{field} {_shown(old)} -> {_shown(new)}" for field, (old, new) in changes.items()
                     if field not in ("channel", "linkedin_reason"))
    with transaction(conn):
        _write(conn, person["person_id"], changes, ctx.now)
        noted = record_edit(ctx, person, changes, kind="edited",
                            summary=clean(f"Claude changed {person['name']}: {said}"))
    return {"person_id": person["person_id"], "changed": sorted(changes), "review_id": noted["review_id"],
            "channel": _channel_after(person, changes),
            "message": "Done. Devanshu sees it in Needs you; the old values are in the event."}


# ---------------------------------------------------------------------------------------------- mark_email_bounced

class BouncedInput(Input):
    person_id: int = Field(ge=1, le=MAX_ID)
    reason: str = Field(min_length=3, max_length=REASON_CHARS,
                        description="Why you know it is dead: what Gmail or the sender said, or what he saw.")


def mark_email_bounced(ctx: Ctx, args: BouncedInput) -> dict:
    conn = ctx.conn
    person = _person(conn, args.person_id)
    reason = clean(args.reason)
    if len(reason) < 3:
        raise ToolError("bad_input", "reason is empty.", "Say why the address is dead. Nothing was written.")
    old = person["email"]
    if old is None:
        raise ToolError("no_email", f"{person['name']} has no address on record.",
                        "Nothing to clear. Nothing was written.")
    changes = _email_changes(conn, person, None)
    with transaction(conn):
        _write(conn, person["person_id"], changes, ctx.now)
        if not conn.execute("SELECT 1 FROM bounced_address WHERE address = ?", (old.strip(),)).fetchone():
            conn.execute("INSERT INTO bounced_address(address, first_seen_at, source, source_ref) "
                         "VALUES (?, ?, 'marked', ?)", (old.strip(), ctx.now, str(person["person_id"])))
        noted = record_edit(ctx, person, changes, kind="email_bounced", extra={"reason": reason},
                            summary=clean(f"Claude cleared {person['name']}'s email {old} as bounced: {reason}"))
    return {"person_id": person["person_id"], "cleared_email": old, "channel": _channel_after(person, changes),
            "review_id": noted["review_id"],
            "message": "Cleared. The sender holds any email still queued for them. Devanshu sees it in Needs you."}


# ---------------------------------------------------------------------------------------------- move_person

class MoveInput(Input):
    person_id: int = Field(ge=1, le=MAX_ID)
    company_id: int = Field(ge=1, le=MAX_ID, description="The company they work at now.")


def move_person(ctx: Ctx, args: MoveInput) -> dict:
    conn = ctx.conn
    person = _person(conn, args.person_id)
    family, target, merged_note = resolve(conn, args.company_id)
    current = family.survivor(person["company_id"])
    if target == current:
        raise ToolError("same_company", f"{person['name']} is already at {company_row(conn, current)['name']}.",
                        "Nothing to move. Nothing was written.")
    people, blocked = no_go_sets(conn, family.companies)
    if person["person_id"] in people:
        raise ToolError("person_no_go", f"{person['name']} is on the no-go list.",
                        "Devanshu lifts a no-go in the app. Nothing was written.")
    to_row, from_row = company_row(conn, target), company_row(conn, current)
    if to_row["status"] == "removed":
        raise ToolError("company_removed", f"{to_row['name']} was removed.",
                        "Use the company they work at now, or ask Devanshu. Nothing was written.")
    warnings = []
    if to_row["status"] == "closed":
        warnings.append(f"{to_row['name']} is closed.")
    if target in blocked:
        warnings.append(f"{to_row['name']} is on the no-go list.")
    changes = {"company_id": (person["company_id"], target)}
    with transaction(conn):
        _write(conn, person["person_id"], changes, ctx.now)
        noted = record_edit(ctx, person, changes, kind="moved", company_id=target,
                            extra={"from": from_row["name"], "to": to_row["name"]},
                            summary=clean(f"Claude moved {person['name']} from {from_row['name']} to "
                                          f"{to_row['name']}"))
    return {"person_id": person["person_id"], "from_company": {"id": current, "name": from_row["name"]},
            "to_company": {"id": target, "name": to_row["name"]}, "warnings": warnings, "merged_note": merged_note,
            "review_id": noted["review_id"],
            "message": "Moved. Earlier sends stay under the company they went to. Devanshu sees it in Needs you."}


TOOLS = (
    ToolSpec("update_person",
             "Correct a person after the fact: name, title, role_kind, linkedin_url, email (null clears it), "
             "profile_text or notes. Only the fields you send change. It applies at once, logs the old value and "
             "raises a Needs-you note. Clearing an address makes them a LinkedIn contact; a replacement address "
             "is refused after a sent first email that did not bounce.",
             UpdatePersonInput, update_person,
             {"person": Grant(update=PERSON_EDIT_COLUMNS), "review_item": Grant(insert=True),
              "event": Grant(insert=True)}),
    ToolSpec("mark_email_bounced",
             "An address bounced or is dead: clear it from the person and record why. They become a LinkedIn "
             "contact, and the address is never used again. Writes no message and no touch: the sender already "
             "holds any email to a person with no address. Applies at once, logs the old address and raises a "
             "Needs-you note.",
             BouncedInput, mark_email_bounced,
             {"person": Grant(update=frozenset({"email", "channel", "linkedin_reason", "updated_at", "revision"})),
              "review_item": Grant(insert=True), "event": Grant(insert=True),
              "bounced_address": Grant(insert=True)}),
    ToolSpec("move_person",
             "A person works at a different company now: move them there. Applies at once, logs both companies "
             "and raises a Needs-you note. Earlier sends and events stay under the company they happened at. "
             "Refused for someone on the no-go list or a removed company; a closed or no-go company is a warning.",
             MoveInput, move_person,
             {"person": Grant(update=MOVE_ONLY_COLUMNS), "review_item": Grant(insert=True),
              "event": Grant(insert=True)}),
)
