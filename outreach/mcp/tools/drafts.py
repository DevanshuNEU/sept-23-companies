"""The draft tools (#13): save_draft (the first email), save_followup (a planned follow-up) and save_linkedin_note.

Both run the #9 checks of `outreach.drafts.checks.run_checks` on every save and store them on the message, inside one
write transaction with the preconditions, so what is stored is what was checked. A content block (three links, a
placeholder, no mail server) still saves, drafted, with its checks, so Claude can fix it and save again. A
precondition refuses and stores nothing: no address, a LinkedIn contact, a no-go, a person who said no, a closed or
removed company, an email already sent, a message he saved or approved. Nothing here approves: the message is always
`drafted` and `saved_by` claude, and the guard refuses anything else on this connection.

The mail server answer comes from the `mx_check` cache only (this server has no network): fresh `ok` or `none`, else
`unknown`, which warns. The banned phrases list is read on every save; an unreadable list blocks (fail closed).

save_linkedin_note goes through #15's one writer of `linkedin_draft`, `outreach.plan.drafts.save_linkedin_draft`,
always as `saved_by='claude'`, so the reason line is stored `proposed` and only Devanshu confirms or writes it. That
writer refuses a note he confirmed, wrote or saved himself (`DraftLocked`, here `draft_locked`) and runs #15's
`check_note`; a block still saves. The note scopes of the banned list and me.md's open questions are read on every
save, and either one unreadable blocks the note. An identical second save writes nothing.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

from pydantic import Field

from outreach.drafts.checks import HOOK_DATE, URL, DraftInput, cached_mx, email_domain, load_banned, run_checks
from outreach.drafts.followups import company_map, due_followups, held_survivors, no_go_sets, resolve_company
from outreach.mcp.guard import Grant
from outreach.mcp.tools import PREPARED_BY, Ctx, Input, ToolError, ToolSpec
from outreach.mcp.tools.people import said_no_ids
from outreach.mcp.tools.reads import HOOK_CHARS, MAX_ID, PROJECT_CHARS, Family, company_row, why_not_email
from outreach.plan.config import default_plan_config
from outreach.plan.drafts import DraftLocked, LinkedinDraft, get_draft, save_linkedin_draft
from outreach.plan.notecheck import Fact, NoteCheck
from outreach.store import append_event, transaction

__all__ = ["TOOLS"]

DATE = r"^\d{4}-\d{2}-\d{2}$"
FOLLOWUP_WINDOW = 7  # days ahead, the most followups_due lists
HIS_WORDS = "He has edited this one. Leave it."
HIS_FIX = "Do not change this message. Tell Devanshu if you think it needs something."

REFUSALS = {
    "no_email": ("{name} has no email address.", "Add the address with add_people, or reach them on LinkedIn."),
    "linkedin_person": ("{name} is a LinkedIn contact, not an email one.", "Reach them on LinkedIn instead."),
    "person_no_go": ("{name} is on the no-go list.", "Nothing goes to them. Pick someone else at the company."),
    "said_no": ("{name} said no or asked to stop.", "Never contact this person again."),
    "company_no_go": ("{company} is on the no-go list.", "Nothing goes to anyone there."),
    "company_closed": ("{company} is closed.", "Devanshu reopens a company; tell him if that should change."),
    "company_removed": ("{company} was removed.", "Devanshu restores a company; tell him if that should change."),
    "already_emailed": ("The first email to {name} already went out.",
                        "Draft a follow-up when followups_due lists one."),
    "edited_by_devanshu": (HIS_WORDS, HIS_FIX),
    "approved_or_later": (HIS_WORDS, HIS_FIX),
    "skipped": ("Devanshu skipped the email to {name}. Leave it.", "Do not draft this person again unless he asks."),
}


def refuse(code: str, *, name: str = "", company: str = "") -> ToolError:
    message, fix = REFUSALS[code]
    return ToolError(code, message.format(name=name, company=company), fix)


def text(value: str, field: str) -> str:
    if not value.strip():
        raise ToolError("bad_input", f"{field} is empty.", f"Send the {field}. Nothing was written.")
    return value


def word_count(body: str) -> int:
    """Words as the length check counts them: links are not words."""
    return len(URL.sub(" ", body).split())


def brief(check: dict) -> dict:
    return {"code": check["code"], "title": check["title"], "detail": check["detail"], "fix": check["fix"]}


def result(message_id: int, revision: int, *, created: bool, unchanged: bool, body: str, found: list[dict]) -> dict:
    return {"message_id": message_id, "state": "drafted", "revision": revision, "created": created,
            "unchanged": unchanged, "word_count": word_count(body),
            "blocks": [brief(c) for c in found if c["severity"] == "block"],
            "warns": [brief(c) for c in found if c["severity"] == "warn"],
            "passes": sum(1 for c in found if c["severity"] == "pass")}


def banned_list() -> list:
    """The banned phrases, read before the write transaction opens; an unusable list is [] and run_checks blocks
    on it (fail closed)."""
    return load_banned()[0]


def checks_of(ctx: Ctx, person: sqlite3.Row, *, message_id: int | None, seq: int, subject: str, body: str,
              hook: str, banned: list) -> list[dict]:
    domain = email_domain(person["email"])
    d = DraftInput(message_id=message_id, seq=seq, subject=subject, body=body, hook=hook,
                   role_kind=person["role_kind"], person_name=person["name"], person_email=person["email"],
                   person_id=person["person_id"], company_id=person["company_id"],
                   mx=cached_mx(ctx.conn, domain, ctx.now) if domain else "none")
    return run_checks(ctx.conn, d, banned=banned, now=ctx.now)


def person_row(conn: sqlite3.Connection, person_id: int) -> sqlite3.Row:
    p = conn.execute("SELECT * FROM person WHERE person_id = ?", (person_id,)).fetchone()
    if p is None:
        raise ToolError("not_found", f"No person {person_id}.", "Use a person id from company, person or "
                                                                 "followups_due.")
    return p


def stale(revision: int) -> ToolError:
    return ToolError("stale_revision", f"The message changed since revision {revision}.",
                     "Read it again with the person tool, then save with its current revision.")


def redraft(ctx: Ctx, m: sqlite3.Row, sets: dict, *, company_id: int, kind: str, found: list[dict]) -> int:
    """Updates message `m` with `sets` plus the checks, saved_by claude and a revision bump, guarded on the revision
    read; appends the event. Returns the new revision."""
    columns = {**sets, "checks": json.dumps(found), "saved_by": "claude", "updated_at": ctx.now}
    cur = ctx.conn.execute(
        f"UPDATE message SET {', '.join(f'{c} = ?' for c in columns)}, revision = revision + 1 "
        f"WHERE message_id = ? AND revision = ?", (*columns.values(), m["message_id"], m["revision"]))
    if cur.rowcount != 1:
        raise stale(m["revision"])
    revision = m["revision"] + 1
    append_event(ctx.conn, entity="message", kind=kind, actor="claude", entity_id=m["message_id"],
                 company_id=company_id, at=ctx.now,
                 detail={"seq": m["seq"], "revision": revision,
                         "blocks": [c["code"] for c in found if c["severity"] == "block"]})
    return revision


# ---------------------------------------------------------------------------------------------- save_draft

class SaveDraftInput(Input):
    person_id: int = Field(ge=1, le=MAX_ID)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000, description="The whole email, links included.")
    hook: str = Field(min_length=1, max_length=HOOK_CHARS, description="The one fact about the person the email opens on.")
    hook_on: str | None = Field(None, max_length=10, pattern=DATE,
                                description="YYYY-MM-DD the hook happened; stored as 'hook (date)' so the checks can "
                                            "tell an old hook.")
    project: str = Field(min_length=1, max_length=PROJECT_CHARS, description="The project the second link points at.")
    revision: int | None = Field(None, ge=1, le=MAX_ID, description="The revision you read; a newer one refuses.")


def stored_hook(args: SaveDraftInput, today: date) -> str:
    hook = text(args.hook, "hook").strip()
    if args.hook_on is None:
        return hook
    try:
        on = date.fromisoformat(args.hook_on)
    except ValueError:
        raise ToolError("bad_input", f"hook_on {args.hook_on} is not a real date.",
                        "Send YYYY-MM-DD. Nothing was written.") from None
    if on > today:
        raise ToolError("bad_input", f"hook_on {args.hook_on} is after today ({today.isoformat()}).",
                        "Send the day the hook happened. Nothing was written.")
    if HOOK_DATE.search(hook):
        raise ToolError("bad_input", "The hook already ends in a date and hook_on gives another.",
                        "Send the hook without the date, with hook_on. Nothing was written.")
    return f"{hook} ({on.isoformat()})"


def save_draft(ctx: Ctx, args: SaveDraftInput) -> dict:
    conn = ctx.conn
    subject, body, project = text(args.subject, "subject"), text(args.body, "body"), text(args.project, "project")
    hook = stored_hook(args, ctx.today)
    banned = banned_list()
    with transaction(conn):
        p = person_row(conn, args.person_id)
        family = Family(conn)
        main = company_row(conn, family.survivor(p["company_id"]))
        touch = conn.execute("SELECT touch_id, state, reply_kind, first_sent_at FROM touch WHERE person_id = ?",
                             (p["person_id"],)).fetchone()
        first = conn.execute("SELECT * FROM message WHERE touch_id = ? AND seq = 0",
                             (touch["touch_id"],)).fetchone() if touch else None
        why = why_not_email(p, main, family, conn, touch, first)
        if why is not None:
            raise refuse(why, name=p["name"], company=main["name"])
        if first is not None and first["state"] != "drafted":
            raise ToolError("not_a_draft", f"The first email to {p['name']} is {first['state']}, not a draft.",
                            HIS_FIX)
        if first is not None and args.revision is not None and args.revision != first["revision"]:
            raise stale(args.revision)
        found = checks_of(ctx, p, message_id=first["message_id"] if first else None, seq=0, subject=subject,
                          body=body, hook=hook, banned=banned)
        if first is not None and (first["subject"], first["body"], first["hook"], first["project"]) == (
                subject, body, hook, project):
            return result(first["message_id"], first["revision"], created=False, unchanged=True, body=body,
                          found=found)
        if first is not None:
            revision = redraft(ctx, first, {"subject": subject, "body": body, "hook": hook, "project": project},
                               company_id=main["company_id"], kind="redrafted", found=found)
            return result(first["message_id"], revision, created=False, unchanged=False, body=body, found=found)
        touch_id = touch["touch_id"] if touch else conn.execute(
            "INSERT INTO touch(person_id, created_at, updated_at) VALUES (?, ?, ?)",
            (p["person_id"], ctx.now, ctx.now)).lastrowid
        message_id = conn.execute(
            "INSERT INTO message(touch_id, seq, state, subject, body, hook, project, checks, saved_by, created_at, "
            "updated_at) VALUES (?, 0, 'drafted', ?, ?, ?, ?, ?, 'claude', ?, ?)",
            (touch_id, subject, body, hook, project, json.dumps(found), ctx.now, ctx.now)).lastrowid
        append_event(conn, entity="message", kind="drafted", actor="claude", entity_id=message_id,
                     company_id=main["company_id"], at=ctx.now,
                     detail={"seq": 0, "revision": 1, "blocks": [c["code"] for c in found if c["severity"] == "block"]})
    return result(message_id, 1, created=True, unchanged=False, body=body, found=found)


# ---------------------------------------------------------------------------------------------- save_followup

class SaveFollowupInput(Input):
    person_id: int = Field(ge=1, le=MAX_ID)
    seq: int = Field(ge=1, le=3, description="Which follow-up (followups_due gives it).")
    body: str = Field(min_length=1, max_length=3000, description="The follow-up text; the subject is set for you.")
    revision: int | None = Field(None, ge=1, le=MAX_ID, description="The revision you read; a newer one refuses.")


def stopped(conn: sqlite3.Connection, p: sqlite3.Row, touch: sqlite3.Row) -> ToolError | None:
    """Why no follow-up may be drafted for this thread now (the conditions `due_followups` applies), or None."""
    if touch["state"] != "open":
        word = {"replied": "replied", "bounced": "bounced", "closed": "closed"}[touch["state"]]
        return ToolError(word, f"The thread with {p['name']} has {word}; no follow-up goes out.",
                         "Leave it. A reply is Devanshu's to answer.")
    companies = company_map(conn)
    survivor = resolve_company(companies, p["company_id"])[-1]
    no_go_people, no_go_companies = no_go_sets(conn, companies)
    if p["person_id"] in no_go_people:
        return refuse("person_no_go", name=p["name"])
    name = company_row(conn, survivor)["name"]
    if survivor in no_go_companies:
        return refuse("company_no_go", company=name)
    if survivor in held_survivors(companies):
        return ToolError("company_held", f"{name} is on hold: someone there replied.",
                         "Leave the follow-ups until Devanshu releases the hold.")
    status = companies[survivor]["status"]
    if status in ("closed", "removed"):
        return refuse(f"company_{status}", company=name)
    return None


def save_followup(ctx: Ctx, args: SaveFollowupInput) -> dict:
    conn = ctx.conn
    body = text(args.body, "body")
    banned = banned_list()
    with transaction(conn):
        p = person_row(conn, args.person_id)
        touch = conn.execute("SELECT touch_id, state, first_sent_at FROM touch WHERE person_id = ?",
                             (p["person_id"],)).fetchone()
        first = conn.execute("SELECT subject FROM message WHERE touch_id = ? AND seq = 0",
                             (touch["touch_id"],)).fetchone() if touch else None
        if touch is None or touch["first_sent_at"] is None or first is None:
            raise ToolError("first_email_not_sent", f"The first email to {p['name']} has not gone out yet.",
                            "A follow-up comes after the first email is sent; followups_due lists them.")
        m = conn.execute("SELECT * FROM message WHERE touch_id = ? AND seq = ?",
                         (touch["touch_id"], args.seq)).fetchone()
        if m is None:
            raise ToolError("no_such_followup", f"There is no follow-up {args.seq} planned for {p['name']}.",
                            "Draft only the follow-ups followups_due lists.")
        if m["state"] not in ("planned", "drafted"):
            raise ToolError("not_a_draft", f"Follow-up {args.seq} to {p['name']} is {m['state']}, not a draft.",
                            HIS_FIX)
        if m["saved_by"] == "devanshu":
            raise refuse("edited_by_devanshu")
        problem = stopped(conn, p, touch)
        if problem is not None:
            raise problem
        if m["state"] == "planned" and m["message_id"] not in due_followups(conn, ctx.today,
                                                                            days_ahead=FOLLOWUP_WINDOW):
            raise ToolError("not_due", f"Follow-up {args.seq} to {p['name']} is due {m['due_on']}, more than "
                                       f"{FOLLOWUP_WINDOW} days out.", "Draft it when followups_due lists it.")
        if args.revision is not None and args.revision != m["revision"]:
            raise stale(args.revision)
        subject = f"Re: {first['subject']}"
        found = checks_of(ctx, p, message_id=m["message_id"], seq=args.seq, subject=subject, body=body, hook="",
                          banned=banned)
        if m["state"] == "drafted" and (m["subject"], m["body"]) == (subject, body):
            return result(m["message_id"], m["revision"], created=False, unchanged=True, body=body, found=found)
        revision = redraft(ctx, m, {"state": "drafted", "subject": subject, "body": body},
                           company_id=Family(conn).survivor(p["company_id"]),
                           kind="drafted" if m["state"] == "planned" else "redrafted", found=found)
    return result(m["message_id"], revision, created=m["state"] == "planned", unchanged=False, body=body,
                  found=found)


# ---------------------------------------------------------------------------------------------- save_linkedin_note

NOTE_LOCKED = {
    "confirmed": "Devanshu confirmed the reason line of this note, so it is his now.",
    "written": "Devanshu wrote the reason line of this note himself, so it is his now.",
    "his_save": "Devanshu edited this note himself, so it is his now.",
}


class NoteFact(Input):
    text: str = Field(min_length=1, max_length=300, description="The fact, in the words the note uses.")
    label: str = Field("", max_length=120, description="A short name for the page, such as Blog.")
    url: str = Field("", max_length=300, description="The public https page it came from (not LinkedIn).")
    in_note: bool = Field(False, description="True when the note uses this fact.")


class SaveNoteInput(Input):
    person_id: int = Field(ge=1, le=MAX_ID)
    body: str = Field(min_length=1, max_length=2000, description="The whole connection note.")
    reason_line: str = Field(min_length=1, max_length=300,
                             description="One line, word for word in the note, saying why this person. A proposal: "
                                         "only Devanshu confirms it or writes his own.")
    facts: list[NoteFact] = Field(default_factory=list, max_length=8)
    posting_id: int | None = Field(None, ge=1, le=MAX_ID, description="A posting of this person's company it names.")
    revision: int | None = Field(None, ge=1, le=MAX_ID, description="The revision you read; a newer one refuses.")


def note_brief(check: NoteCheck) -> dict:
    """A check as the result shows it. #15's checks keep the what-to-do in `detail`; `fix` repeats it when the check
    has no fix of its own, so every block says how to clear it."""
    fix = check.fix if isinstance(check.fix, str) and check.fix else check.detail
    return {"code": check.code, "title": check.title, "detail": check.detail, "fix": fix}


def note_result(draft: LinkedinDraft, *, created: bool, unchanged: bool, max_chars: int) -> dict:
    return {"draft_id": draft.draft_id, "revision": draft.revision, "created": created, "unchanged": unchanged,
            "reason_state": draft.reason_state, "needs_his_line": True, "length": len(draft.body),
            "max_chars": max_chars,
            "blocks": [note_brief(c) for c in draft.checks if c.severity == "block"],
            "warns": [note_brief(c) for c in draft.checks if c.severity == "warn"]}


def note_refusal(conn: sqlite3.Connection, p: sqlite3.Row, main: sqlite3.Row, family: Family) -> ToolError | None:
    """Why no note may be drafted for this person, or None: the person or company is on the no-go list, the person
    said no, or the company is closed or removed."""
    no_go_people, no_go_companies = no_go_sets(conn, family.companies)
    if p["person_id"] in no_go_people:
        return refuse("person_no_go", name=p["name"])
    if p["person_id"] in said_no_ids(conn, [p["person_id"]]):
        return refuse("said_no", name=p["name"])
    if main["company_id"] in no_go_companies:
        return refuse("company_no_go", company=main["name"])
    if main["status"] in ("closed", "removed"):
        return refuse(f"company_{main['status']}", company=main["name"])
    return None


def locked(draft: LinkedinDraft | None, name: str) -> ToolError:
    why = ("confirmed" if draft is not None and draft.reason_state == "confirmed"
           else "written" if draft is not None and draft.reason_state == "written" else "his_save")
    return ToolError("draft_locked", f"{NOTE_LOCKED[why]} Claude never overwrites the note to {name}.",
                     "Leave this note. Tell Devanshu if you think it needs something.")


def save_linkedin_note(ctx: Ctx, args: SaveNoteInput) -> dict:
    conn = ctx.conn
    body, reason = text(args.body, "body"), text(args.reason_line, "reason_line")
    facts = [Fact(text=f.text, label=f.label, url=f.url, in_note=f.in_note) for f in args.facts]
    cfg = default_plan_config()
    max_chars = cfg.get("note", "max_chars")
    banned = load_banned(scopes=tuple(cfg.get("note", "banned_scopes")))[0]  # unusable: [], and every note blocks
    with transaction(conn):
        p = person_row(conn, args.person_id)
        family = Family(conn)
        main = company_row(conn, family.survivor(p["company_id"]))
        problem = note_refusal(conn, p, main, family)
        if problem is not None:
            raise problem
        if args.posting_id is not None:
            row = conn.execute("SELECT company_id FROM posting WHERE posting_id = ?", (args.posting_id,)).fetchone()
            if row is None or row["company_id"] not in family.members(main["company_id"]):
                raise ToolError("bad_input", f"posting_id {args.posting_id} is not a posting of {main['name']}.",
                                "Use a posting id from the company tool for this person's company, or leave it "
                                "out. Nothing was written.")
        stored = get_draft(conn, p["person_id"])
        if (stored is not None and stored.saved_by == "claude"  # the foundation CHECK: claude's line is proposed
                and (args.revision is None or args.revision == stored.revision)
                and (stored.body, stored.reason_line, list(stored.facts)) == (body, reason, facts)
                and args.posting_id in (None, stored.posting_id)):
            return note_result(stored, created=False, unchanged=True, max_chars=max_chars)
        try:
            saved = save_linkedin_draft(conn, person_id=p["person_id"], body=body, reason_line=reason, facts=facts,
                                        saved_by="claude", posting_id=args.posting_id, prepared_by_version=PREPARED_BY,
                                        revision=args.revision, cfg=cfg, banned=banned)
        except DraftLocked:
            raise locked(stored, p["name"]) from None
        except LookupError:
            raise ToolError("stale_revision", f"The note to {p['name']} is not at revision {args.revision}, or it "
                                              "changed while this was saved.",
                            "Read it again with the person tool, then save with its current revision.") from None
    return note_result(saved, created=stored is None, unchanged=False, max_chars=max_chars)


TOOLS = (
    ToolSpec("save_draft",
             "Save the first email to a person as a draft for Devanshu to review. It runs the #9 checks and returns "
             "blocks, warns and a count of passes; a block still saves, so fix it and save again. It never "
             "approves. A message he saved or approved is refused.", SaveDraftInput, save_draft,
             {"touch": Grant(insert=True),
              "message": Grant(insert=True, update=frozenset({"subject", "body", "hook", "project", "checks",
                                                              "saved_by", "updated_at", "revision"})),
              "event": Grant(insert=True)}),
    ToolSpec("save_followup",
             "Fill a planned follow-up that followups_due lists, as a draft. The subject is 'Re: ' plus the first "
             "email's subject; give only the body. Runs the same checks as save_draft. It never approves.",
             SaveFollowupInput, save_followup,
             {"message": Grant(update=frozenset({"state", "subject", "body", "checks", "saved_by", "updated_at",
                                                 "revision"})),
              "event": Grant(insert=True)}),
    ToolSpec("save_linkedin_note",
             "Save the LinkedIn connection note to a person as a draft, with its reason line and the facts it uses "
             "(each with its public page). The reason line is a proposal that only Devanshu can confirm or rewrite. "
             "It runs the note checks; a block still saves. A note he confirmed, wrote or edited is refused.",
             SaveNoteInput, save_linkedin_note,
             {"linkedin_draft": Grant(insert=True, update=frozenset({"posting_id", "body", "reason_line", "facts",
                                                                     "checks", "prepared_by_version", "updated_at",
                                                                     "revision"})),
              "event": Grant(insert=True)}),
)
