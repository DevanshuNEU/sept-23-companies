"""add_people (#13, lean v1): people Devanshu gives Claude, stored added_by claude (set here, never an input).

The whole call is checked first: any schema problem, a bad address, a LinkedIn link that is not a profile, or a
source that breaks the person_source rules rejects every person and writes nothing. Then, in one transaction:

- A merged company id adds to the company it was merged into. A removed company, or one on an active no-go (any
  company of its merged family), refuses the call.
- Someone already known by email, else by LinkedIn profile (compared in one normal form), else, with neither given,
  by name at the company, is not added again: their id comes back with created false and flag `duplicate`. At the
  same company an empty profile or empty notes are filled (never over text already there) and new sources added,
  except on a name-only match (flag `name_only_match`), which writes nothing: a name alone may be someone else.
  Known at another company: returned with flag `other_company` and left as they are.
- A known person on an active no-go, or who said no or asked to stop (on an email thread or a logged outreach
  touch), is that person's refusal (`person_no_go`, `said_no`), with a plain reason; the others in the call still go
  through. The same-name lookup across the company family runs even when an address or profile is given, so a new
  address never brings such a person back as someone fresh and draftable; a namesake who may be emailed does not
  stop the add, which is flagged `same_name_as:<id>`. An address and a profile of two different people is
  `two_people`.

The address, profile and name keys come from `outreach.sources.keys`, which the history import (#17) matches people
with too, so a person the import brought in is the person this tool finds.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Literal

from pydantic import Field, field_validator

from outreach.drafts.followups import no_go_sets
from outreach.mcp.guard import Grant
from outreach.mcp.tools import Ctx, Input, ToolError, ToolSpec
from outreach.mcp.tools.reads import MAX_ID, Family, company_row, marks, resolve
from outreach.mcp.tools.research import DATE, HTTPS, is_linkedin
from outreach.sources.keys import EMAIL, linkedin_key
from outreach.sources.keys import person_name_key as name_key
from outreach.store import append_event, transaction

__all__ = ["TOOLS", "linkedin_key", "said_no_ids"]

class SourceIn(Input):
    kind: Literal["profile", "team", "context", "email", "post"]
    url: str = Field(max_length=300, pattern=HTTPS, description="A public https page, never LinkedIn.")
    label: str = Field("", max_length=200)
    quote: str = Field("", max_length=500, description="The words on the page about them.")
    email_basis: Literal["published", "verified", "pattern"] | None = Field(
        None, description="Only for kind email: how the page gives the address.")
    observed_on: str | None = Field(None, max_length=10, pattern=DATE)

    @field_validator("url")
    @classmethod
    def check_url(cls, url: str) -> str:
        if is_linkedin(url):
            raise ValueError("LinkedIn is never a source; give the LinkedIn profile as linkedin_url")
        return url

    @field_validator("observed_on")
    @classmethod
    def check_day(cls, day: str | None) -> str | None:
        if day is not None:
            date.fromisoformat(day)  # a ValueError is bad_input
        return day


class PersonIn(Input):
    name: str = Field(min_length=1, max_length=200)
    title: str = Field("", max_length=200)
    role_kind: Literal["engineer", "manager", "founder", "recruiter", "alum", "other"]
    email: str | None = Field(None, max_length=254)
    linkedin_url: str | None = Field(None, max_length=300, description="Their LinkedIn profile, linkedin.com/in/...")
    profile_text: str | None = Field(None, max_length=8000, description="Profile text he pasted (stored as data).")
    notes: str | None = Field(None, max_length=2000)
    sources: list[SourceIn] = Field(default_factory=list, max_length=6)


class AddPeopleInput(Input):
    company_id: int = Field(ge=1, le=MAX_ID)
    people: list[PersonIn] = Field(min_length=1, max_length=10)


def _clean(i: int, p: PersonIn) -> dict:
    """The person as stored, or bad_input naming `people.<i>`."""
    def bad(what: str, fix: str) -> ToolError:
        return ToolError("bad_input", f"people.{i}: {what}", f"{fix} Nothing was written.")

    name = " ".join(p.name.split())
    if not name:
        raise bad("name is empty.", "Send the person's name.")
    email = p.email.strip().lower() if p.email and p.email.strip() else None
    if email is not None and not EMAIL.fullmatch(email):
        raise bad(f"{email!r} is not an email address.", "Send the address as name@domain, or leave it out.")
    linkedin = None
    if p.linkedin_url and p.linkedin_url.strip():
        linkedin = linkedin_key(p.linkedin_url)
        if linkedin is None:
            raise bad("linkedin_url is not a LinkedIn profile link.",
                      "Send https://www.linkedin.com/in/<their name>, or leave it out.")
    for j, s in enumerate(p.sources):
        if (s.kind == "email") != (s.email_basis is not None):
            raise bad(f"sources.{j}: an email source needs email_basis, and only an email source has one.",
                      "Give email_basis with kind email, or use another kind.")
    return {"name": name, "title": p.title.strip(), "role_kind": p.role_kind, "email": email, "linkedin": linkedin,
            "profile_text": (p.profile_text or "").strip(), "notes": (p.notes or "").strip(), "sources": p.sources}


def _namesakes(conn: sqlite3.Connection, want: dict, family_ids: list[int]) -> list[sqlite3.Row]:
    """Everyone with this name at the company family, whatever address or profile was given."""
    return [r for r in conn.execute(f"SELECT * FROM person WHERE company_id IN ({marks(family_ids)}) "
                                    f"ORDER BY person_id", family_ids)
            if name_key(r["name"]) == name_key(want["name"])]


def said_no_ids(conn: sqlite3.Connection, person_ids: list[int]) -> set[int]:
    """The people whose reply said no or asked to stop, on an email thread or a logged outreach touch."""
    if not person_ids:
        return set()
    stop = ("said_no", "asked_to_stop")
    return {r[0] for r in conn.execute(
        f"SELECT person_id FROM touch WHERE person_id IN ({marks(person_ids)}) AND reply_kind IN (?, ?) "
        f"UNION SELECT person_id FROM outreach_touch WHERE person_id IN ({marks(person_ids)}) "
        f"AND undone_at IS NULL AND reply_kind IN (?, ?)", (*person_ids, *stop, *person_ids, *stop))}


def _stop_reason(conn: sqlite3.Connection, people: list[sqlite3.Row], no_go_people: set[int]) -> dict | None:
    """The refusal for the first of `people` who must not be emailed (a person no-go, or said no), else None."""
    said_no = said_no_ids(conn, [p["person_id"] for p in people])
    for p in people:
        if p["person_id"] in no_go_people:
            return {"person_id": p["person_id"], "created": False, "refused": "person_no_go", "flags": [],
                    "message": f"{p['name']} is on the no-go list, so nothing was added or changed for them."}
        if p["person_id"] in said_no:
            return {"person_id": p["person_id"], "created": False, "refused": "said_no", "flags": [],
                    "message": f"{p['name']} said no or asked to stop, so nothing was added or changed for them."}
    return None


def _match(conn: sqlite3.Connection, want: dict) -> tuple[sqlite3.Row | None, str | None]:
    """(the person known by this address or profile, a refusal message when the address and the profile are two
    different people)."""
    by_email = conn.execute("SELECT * FROM person WHERE email = ?", (want["email"],)).fetchone() \
        if want["email"] else None
    by_page = None
    if want["linkedin"]:
        by_page = next((r for r in conn.execute("SELECT * FROM person WHERE linkedin_url IS NOT NULL "
                                                "ORDER BY person_id")
                        if linkedin_key(r["linkedin_url"]) == want["linkedin"]), None)
    if by_email is not None and by_page is not None and by_email["person_id"] != by_page["person_id"]:
        return None, (f"The address is {by_email['name']}'s (person {by_email['person_id']}) and the LinkedIn page "
                      f"is {by_page['name']}'s (person {by_page['person_id']}).")
    return by_email or by_page, None


def _sources(ctx: Ctx, person_id: int, sources) -> int:
    added = 0
    for s in sources:
        cur = ctx.conn.execute(
            "INSERT INTO person_source(person_id, kind, url, label, quote, email_basis, observed_on, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (person_id, s.kind, s.url, s.label.strip(), s.quote.strip(), s.email_basis, s.observed_on, ctx.now))
        added += cur.rowcount
    return added


def _known(ctx: Ctx, known: sqlite3.Row, want: dict, *, family: Family, survivor: int, no_go_people: set[int]
           ) -> dict:
    conn = ctx.conn
    stop = _stop_reason(conn, [known], no_go_people)
    if stop is not None:
        return stop
    if family.survivor(known["company_id"]) != survivor:
        theirs = company_row(conn, family.survivor(known["company_id"]))
        return {"person_id": known["person_id"], "created": False, "refused": None,
                "flags": ["duplicate", "other_company"],
                "message": f"Already known at {theirs['name']} (company {theirs['company_id']}); left as they are."}
    flags = ["duplicate"]
    filled = {c: want[c] for c in ("profile_text", "notes") if want[c] and not known[c]}
    if filled:
        conn.execute(f"UPDATE person SET {', '.join(f'{c} = ?' for c in filled)}, updated_at = ?, "
                     f"revision = revision + 1 WHERE person_id = ?", (*filled.values(), ctx.now, known["person_id"]))
        flags += [f"filled_{c}" for c in filled]
    added = _sources(ctx, known["person_id"], want["sources"])
    if added:
        flags.append("added_sources")
    if filled or added:
        append_event(conn, entity="person", kind="updated", actor="claude", entity_id=known["person_id"],
                     company_id=survivor, at=ctx.now, detail={"filled": sorted(filled), "sources_added": added})
    return {"person_id": known["person_id"], "created": False, "refused": None, "flags": flags,
            "message": "Already known; not added again."}


def _new(ctx: Ctx, want: dict, survivor: int, flags: list[str]) -> dict:
    channel = "email" if want["email"] else "linkedin"
    person_id = ctx.conn.execute(
        "INSERT INTO person(company_id, name, title, role_kind, email, linkedin_url, profile_text, notes, channel, "
        "linkedin_reason, added_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'claude', ?, ?)",
        (survivor, want["name"], want["title"], want["role_kind"], want["email"], want["linkedin"],
         want["profile_text"], want["notes"], channel, None if channel == "email" else "no_email", ctx.now,
         ctx.now)).lastrowid
    added = _sources(ctx, person_id, want["sources"])
    append_event(ctx.conn, entity="person", kind="added", actor="claude", entity_id=person_id, company_id=survivor,
                 at=ctx.now, detail={"channel": channel, "role_kind": want["role_kind"], "sources": added})
    return {"person_id": person_id, "created": True, "refused": None, "flags": flags,
            "message": "Someone with this name is already at the company; check it is not the same person."
            if flags else None}


def add_people(ctx: Ctx, args: AddPeopleInput) -> dict:
    conn = ctx.conn
    wanted = [_clean(i, p) for i, p in enumerate(args.people)]  # the whole call is checked before any write
    with transaction(conn):
        family, survivor, note = resolve(conn, args.company_id)
        main = company_row(conn, survivor)
        no_go_people, no_go_companies = no_go_sets(conn, family.companies)
        if main["status"] == "removed":
            raise ToolError("company_removed", f"{main['name']} was removed, so no one is added there.",
                            "Tell Devanshu if it should come back.")
        if survivor in no_go_companies:
            raise ToolError("company_no_go", f"{main['name']} is on the no-go list, so no one is added there.",
                            "Nothing goes to anyone at a no-go company. Tell Devanshu if that should change.")
        ids = family.members(survivor)
        results = []
        for i, want in enumerate(wanted):
            known, conflict = _match(conn, want)
            namesakes = _namesakes(conn, want, ids)
            stopped = _stop_reason(conn, namesakes, no_go_people)
            if conflict is not None:
                out = {"person_id": None, "created": False, "refused": "two_people", "flags": [],
                       "message": conflict + " Nothing was added for this person."}
            elif known is not None:
                out = _known(ctx, known, want, family=family, survivor=survivor, no_go_people=no_go_people)
            elif stopped is not None:  # a new address or profile never brings back someone who must not be emailed
                out = stopped
            elif namesakes and not want["email"] and not want["linkedin"]:
                out = {"person_id": namesakes[0]["person_id"], "created": False, "refused": None,
                       "flags": ["duplicate", "name_only_match"],
                       "message": "Matched by name only, so nothing was filled in or added for them. Give their "
                                  "email or LinkedIn URL to add details."}
            else:
                out = _new(ctx, want, survivor, [f"same_name_as:{p['person_id']}" for p in namesakes])
            results.append({"index": i, "name": want["name"], **out})
    return {"company": {"id": survivor, "name": main["name"], "status": main["status"]}, "merged_note": note,
            "results": results}


TOOLS = (
    ToolSpec("add_people",
             "Add people Devanshu gave you at one company (1 to 10): name, role kind, and an email, a LinkedIn "
             "profile, profile text, notes and public source pages when he gave them. Someone already known is "
             "not added again (their id comes back, created false). A no-go company or person is refused.",
             AddPeopleInput, add_people,
             {"person": Grant(insert=True, update=frozenset({"profile_text", "notes", "updated_at", "revision"})),
              "person_source": Grant(insert=True), "event": Grant(insert=True)}),
)
