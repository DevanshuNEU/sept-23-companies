"""The research tools (#13, lean v1): record_research, flag_review, mark_no_go.

Claude's evidence goes through the rules' own `record_evidence(actor='claude')`, never locked and never by hand. A
size, headcount, domain or hq it found is a guess (a guess is shown and flagged, it never hides a company), a stage
at most an estimate, and nothing is confirmed. A domain that is a free mail, payroll or platform host is refused.

Claude never writes a no_go row (#9, 2026-10-19). `mark_no_go` raises one flagged Needs-you item with the person on
it; Devanshu confirms it with one key, and that writes the no-go. `flag_review` raises a flagged item too. Both are
idempotent: the same reason (or the same person and no-go reason) is the same item, open or resolved.
"""
from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import Field, field_validator

from outreach.drafts.checks import normalise_domain
from outreach.mcp.guard import Grant
from outreach.mcp.tools import Ctx, Input, ToolError, ToolSpec
from outreach.mcp.tools.reads import MAX_ID, Family, resolve
from outreach.rules.facts import EvidenceDraft
from outreach.rules.rebuild import record_evidence
from outreach.sources.keys import classify_domain
from outreach.store import append_event, transaction

__all__ = ["TOOLS", "clean", "is_linkedin", "raise_flag"]

DATE = r"^\d{4}-\d{2}-\d{2}$"
HTTPS = r"^https://\S+$"
REASON_CHARS = 500
ITEM_REASON_CHARS = 1000
GUESS_ONLY = ("size", "headcount", "domain", "hq")
DOMAIN_KIND = {"free": "a free mail host", "peo": "a payroll or employer-of-record host",
               "platform": "a platform or hosting host"}
NO_GO_WORDS = {"said_no": "said no", "stop_reply": "asked to stop", "not_a_fit": "not a fit", "other": "other"}
_DASHES = re.compile("[‒-―]")


def clean(text: str | None) -> str:
    """Plain text for a review item: long dashes become a hyphen, runs of whitespace one space."""
    return " ".join(_DASHES.sub("-", text or "").split())


def is_linkedin(url: str) -> bool:
    low = url.lower()
    return "linkedin.com" in low or "lnkd.in" in low


def _not_linkedin(url: str) -> str:
    if is_linkedin(url):
        raise ValueError("a LinkedIn page is not a source here; use the company's own page or another public page")
    return url


# ---------------------------------------------------------------------------------------------- record_research

class ResearchItem(Input):
    fact: Literal["size", "headcount", "stage", "domain", "hq"]
    value: str = Field(min_length=1, max_length=200, description="size and headcount: the words as found; stage: "
                                                                  "pre_seed, seed, series_a ... public; domain: a "
                                                                  "bare host; hq: City, ST.")
    confidence: Literal["guess", "estimate"] = Field("guess", description="guess for size, headcount, domain and "
                                                                          "hq; a stage may be an estimate.")
    source_kind: Literal["website", "builtin", "yc", "hn", "other"]
    source_ref: str = Field(max_length=300, pattern=HTTPS, description="The https page it came from (not LinkedIn).")
    quote: str = Field(min_length=5, max_length=500, description="The words on that page that say it.")
    num_low: int | None = Field(None, ge=0, le=10_000_000, description="size and headcount only: the low end.")
    num_high: int | None = Field(None, ge=0, le=10_000_000, description="size and headcount only: the high end.")
    observed_on: str | None = Field(None, max_length=10, pattern=DATE, description="YYYY-MM-DD the page said it.")

    @field_validator("source_ref")
    @classmethod
    def check_source(cls, url: str) -> str:
        return _not_linkedin(url)


class ResearchInput(Input):
    company_id: int = Field(ge=1, le=MAX_ID)
    items: list[ResearchItem] = Field(min_length=1, max_length=8)


def _draft(i: int, it: ResearchItem) -> EvidenceDraft:
    where = f"items.{i}"
    value = it.value.strip()
    if it.fact in GUESS_ONLY and it.confidence != "guess":
        raise ToolError("bad_input", f"{where}: a {it.fact} you found is recorded as a guess, never an estimate.",
                        "Send confidence guess, or leave it out. Nothing was written.")
    if it.fact == "domain":
        name = normalise_domain(value)
        if name is None:
            raise ToolError("bad_input", f"{where}: {value!r} is not a domain name.",
                            "Send a bare host such as acme.com. Nothing was written.")
        name = name[4:] if name.startswith("www.") else name
        kind = classify_domain(name)
        if kind != "company":
            raise ToolError("bad_input", f"{where}: {name} is {DOMAIN_KIND[kind]}, not a company domain.",
                            "Record the company's own domain, or leave it out. Nothing was written.")
        value = name
    try:
        return EvidenceDraft(fact=it.fact, value=value, confidence=it.confidence, source_kind=it.source_kind,
                             source_ref=it.source_ref, quote=it.quote.strip(), num_low=it.num_low,
                             num_high=it.num_high, observed_on=it.observed_on)
    except ValueError as err:
        raise ToolError("bad_input", f"{where}: {err}", "A size or headcount needs num_low (and num_high not "
                                                         "below it); other facts take no numbers. Nothing was "
                                                         "written.") from None


def record_research(ctx: Ctx, args: ResearchInput) -> dict:
    _family, survivor, note = resolve(ctx.conn, args.company_id)
    drafts = [_draft(i, it) for i, it in enumerate(args.items)]  # every item is checked before anything is written
    added = record_evidence(ctx.conn, survivor, drafts, actor="claude")
    return {"company_id": survivor, "merged_note": note, "added": added, "already_known": len(drafts) - added}


# ---------------------------------------------------------------------------------------------- review items

def raise_flag(ctx: Ctx, *, company_id: int, reason: str, evidence_key: str, posting_id: int | None = None,
               person_id: int | None = None, detail: dict | None = None) -> dict:
    """One flagged review item raised by claude, or the one already there with this key (open or resolved, so a
    resolved item stays resolved). The event is appended only when the item is new."""
    conn = ctx.conn
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO review_item(kind, company_id, posting_id, person_id, reason, evidence_key, raised_by, "
            "created_at) VALUES ('flagged', ?, ?, ?, ?, ?, 'claude', ?) ON CONFLICT DO NOTHING",
            (company_id, posting_id, person_id, reason[:ITEM_REASON_CHARS], evidence_key, ctx.now))
        if cur.rowcount:
            review_id, created, state = cur.lastrowid, True, "open"
            append_event(conn, entity="review_item", kind="raised", actor="claude", entity_id=review_id,
                         company_id=company_id, detail={"kind": "flagged", **(detail or {})}, at=ctx.now)
        else:
            row = conn.execute(
                "SELECT review_id, state FROM review_item WHERE kind = 'flagged' AND company_id = ? "
                "AND board_id IS NULL AND ifnull(posting_id, 0) = ? AND ifnull(person_id, 0) = ? "
                "AND evidence_key = ?", (company_id, posting_id or 0, person_id or 0, evidence_key)).fetchone()
            review_id, created, state = row["review_id"], False, row["state"]
    return {"review_id": review_id, "created": created, "state": state}


class FlagInput(Input):
    company_id: int = Field(ge=1, le=MAX_ID)
    reason: str = Field(min_length=1, max_length=REASON_CHARS, description="What Devanshu should look at, plainly.")
    posting_id: int | None = Field(None, ge=1, le=MAX_ID, description="A posting of this company it is about.")
    person_id: int | None = Field(None, ge=1, le=MAX_ID, description="A person at this company it is about.")


def _belongs(conn, table: str, key: str, value: int | None, ids: list[int], company_id: int) -> None:
    if value is None:
        return
    row = conn.execute(f"SELECT company_id FROM {table} WHERE {key} = ?", (value,)).fetchone()
    if row is None or row["company_id"] not in ids:
        raise ToolError("bad_input", f"{key} {value} is not a {table} of company {company_id}.",
                        "Use an id from the company tool for this company, or leave it out. Nothing was written.")


def flag_review(ctx: Ctx, args: FlagInput) -> dict:
    conn = ctx.conn
    family, survivor, note = resolve(conn, args.company_id)
    reason = clean(args.reason)
    if len(reason) < 3:
        raise ToolError("bad_input", "reason is empty.", "Say what Devanshu should look at. Nothing was written.")
    ids = family.members(survivor)
    _belongs(conn, "posting", "posting_id", args.posting_id, ids, args.company_id)
    _belongs(conn, "person", "person_id", args.person_id, ids, args.company_id)
    key = "claude:" + hashlib.sha256(reason.lower().encode("utf-8")).hexdigest()[:16]
    return {**raise_flag(ctx, company_id=survivor, reason=reason, evidence_key=key, posting_id=args.posting_id,
                         person_id=args.person_id), "merged_note": note}


class NoGoInput(Input):
    person_id: int = Field(ge=1, le=MAX_ID)
    reason: Literal["said_no", "stop_reply", "not_a_fit", "other"]
    quote: str | None = Field(None, max_length=REASON_CHARS, description="The words of their reply (said_no and "
                                                                          "stop_reply need it).")
    note: str | None = Field(None, max_length=REASON_CHARS, description="Why (not_a_fit and other need it).")


def mark_no_go(ctx: Ctx, args: NoGoInput) -> dict:
    conn = ctx.conn
    person = conn.execute("SELECT person_id, company_id, name FROM person WHERE person_id = ?",
                          (args.person_id,)).fetchone()
    if person is None:
        raise ToolError("not_found", f"No person {args.person_id}.", "Use a person id from company or person.")
    quote, note = clean(args.quote), clean(args.note)
    if args.reason in ("said_no", "stop_reply") and not quote:
        raise ToolError("bad_input", f"{args.reason} needs the reply quote: the words they wrote.",
                        "Send quote with their words. Nothing was written.")
    if args.reason in ("not_a_fit", "other") and not note:
        raise ToolError("bad_input", f"{args.reason} needs a note saying why.",
                        "Send note with the reason. Nothing was written.")
    if conn.execute("SELECT 1 FROM no_go WHERE person_id = ? AND lifted_at IS NULL", (args.person_id,)).fetchone():
        raise ToolError("already_no_go", f"{person['name']} is already on the no-go list.",
                        "Nothing to do; nobody will email them.")
    survivor = Family(conn).survivor(person["company_id"])
    said = f'Reply: "{quote}"' if quote else note
    reason = clean(f"Claude suggests no-go for {person['name']}: {NO_GO_WORDS[args.reason]}. {said}")
    raised = raise_flag(ctx, company_id=survivor, reason=reason, person_id=args.person_id,
                        evidence_key=f"claude-no-go:{args.person_id}:{args.reason}",
                        detail={"suggests": "no_go", "reason": args.reason})
    return {**raised, "message": "This is a Needs-you item for Devanshu. He confirms the no-go with one key; until "
                                 "then nothing changes for this person."}


TOOLS = (
    ToolSpec("record_research",
             "Record what you found about a company on a public page: size, headcount, stage, domain or hq, each "
             "with its https page and the quote. Stored as a guess (a stage may be an estimate), never confirmed, "
             "so it never hides a company. A repeat is already_known.", ResearchInput, record_research,
             {"evidence": Grant(insert=True), "event": Grant(insert=True)}),
    ToolSpec("flag_review",
             "Raise a Needs-you item for Devanshu about a company (and maybe one of its postings or people). The "
             "same reason again returns the same item.", FlagInput, flag_review,
             {"review_item": Grant(insert=True), "event": Grant(insert=True)}),
    ToolSpec("mark_no_go",
             "Suggest that a person goes on the no-go list (they said no, asked to stop, or are not a fit). This "
             "only raises a Needs-you item: Devanshu confirms it. A company's no-go is his call; use flag_review.",
             NoGoInput, mark_no_go, {"review_item": Grant(insert=True), "event": Grant(insert=True)}),
)
