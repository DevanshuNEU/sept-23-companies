"""The read tools (#13, lean v1): ranked_companies, company, person, followups_due. None declares a grant, so the
guard refuses every write they could try. Every query is one per table (bulk, never one per row); no posting body
and no colleague's message body is ever selected.

The company views copy small pieces of the API's company page query (fact choice, the posting rule order)
because that module imports the API and the sender; the merged-company walk, holds, no-go sets and due follow-ups
come from `outreach.drafts.followups`, and the ranked list from `outreach.ranking.scores`, unchanged.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

from pydantic import Field

from outreach.drafts.followups import company_map, due_followups, held_survivors, no_go_sets, resolve_company
from outreach.mcp.tools import Ctx, Input, ToolError, ToolSpec
from outreach.ranking.scores import latest_version, ranked_list

__all__ = ["TOOLS"]

MAX_ID = 2 ** 62
PROFILE_CHARS = 3000
NOTES_CHARS = 2000
BODY_CHARS = 4000
QUOTE_CHARS = 500
TEXT_CHARS = 200   # a title, a name, a reason in a list
URL_CHARS = 300
LIST_PEOPLE = 3    # people per company in ranked_companies; `company` shows them all
COMPANY_PEOPLE = 40
OPEN_POSTINGS = 5
OPEN_REVIEWS = 10
SOURCES = 6
COLLEAGUES = 20
FOLLOWUPS = 50

TIER_RANK = {"new_grad": 0, "early_career": 1, "swe1": 2, "swe2": 3, "open": 4, "not_stated": 5, "senior": 6}
STATE_RANK = {"open": 0, "out_of_lane": 1, "hidden": 2}
FACT_ORDER = ("size", "stage", "sponsorship", "domain", "hq", "everify", "lca", "funding", "headcount", "cap_exempt",
              "staffing_risk")
SENT_STATES = ("sending", "send_unknown", "sent")
STOPPED_REPLIES = ("said_no", "asked_to_stop")


# ---------------------------------------------------------------------------------------------- small helpers

def clip(text: str | None, limit: int = TEXT_CHARS) -> str | None:
    return text if text is None or len(text) <= limit else text[:limit]


def capped(text: str, limit: int) -> dict:
    """Untrusted text (a profile, notes) with whether it was cut."""
    return {"text": text[:limit], "truncated": len(text) > limit, "untrusted": True}


def age_days(posted_on: str | None, first_seen_at: str, today: date) -> int:
    seen = date.fromisoformat(posted_on or first_seen_at[:10])
    return max(0, (today - seen).days)


def marks(ids) -> str:
    return ",".join("?" * len(ids))


class Family:
    """The merged-company walk over one read of `company`: survivors, and the ids that end at each."""

    def __init__(self, conn: sqlite3.Connection):
        self.companies = company_map(conn)
        self._survivor = {cid: resolve_company(self.companies, cid)[-1] for cid in self.companies}

    def survivor(self, company_id: int) -> int:
        return self._survivor[company_id]

    def members(self, survivor: int) -> list[int]:
        return sorted(cid for cid, s in self._survivor.items() if s == survivor)


def company_row(conn: sqlite3.Connection, company_id: int) -> sqlite3.Row:
    return conn.execute("SELECT company_id, name, status, close_reason, paused_at, held_by_touch FROM company "
                        "WHERE company_id = ?", (company_id,)).fetchone()


# ---------------------------------------------------------------------------------------------- postings

def posting_state(d: sqlite3.Row | None) -> str:
    """open, out_of_lane or hidden, by the rule order of the company page (a posting with no decision is open)."""
    if d is None:
        return "open"
    if d["lane"] != "engineering":
        return "out_of_lane"
    if d["us"] == "no" or d["sponsorship"] == "no" or d["citizenship"] or d["eligibility"] == "excluded":
        return "hidden"
    if not d["level_ok"] and (d["level_tier"] != "not_stated" or d["years_asked"] is not None):
        return "out_of_lane"
    return "open"


def open_postings(conn: sqlite3.Connection, ids: list[int], today: date) -> tuple[int, list[dict]]:
    """(every posting of the family, the ones not closed best first: open state, level fit, tier, start, newest)."""
    rows = conn.execute(f"SELECT posting_id, title, url, posted_on, first_seen_at, closed_at FROM posting "
                        f"WHERE company_id IN ({marks(ids)})", ids).fetchall()
    live = [r for r in rows if r["closed_at"] is None]
    decisions = {}
    if live:
        live_ids = [r["posting_id"] for r in live]
        decisions = {d["posting_id"]: d for d in conn.execute(
            f"SELECT posting_id, lane, level_tier, level_ok, years_asked, us, sponsorship, citizenship, eligibility, "
            f"start_2027, provisional FROM posting_decision WHERE posting_id IN ({marks(live_ids)})", live_ids)}
    out = []
    for r in sorted(live, key=lambda r: r["first_seen_at"], reverse=True):
        d = decisions.get(r["posting_id"])
        out.append({
            "posting_id": r["posting_id"], "title": clip(r["title"]), "url": clip(r["url"], URL_CHARS),
            "age_days": age_days(r["posted_on"], r["first_seen_at"], today), "state": posting_state(d),
            "level_tier": d["level_tier"] if d else "not_stated", "sponsorship": d["sponsorship"] if d else "silent",
            "provisional": bool(d["provisional"]) if d else True,
            "_key": (STATE_RANK[posting_state(d)], 0 if d is not None and d["level_ok"] else 1,
                     TIER_RANK[d["level_tier"] if d else "not_stated"], 0 if d is not None and d["start_2027"] else 1),
        })
    out.sort(key=lambda p: p.pop("_key"))  # stable: newest first within a key
    return len(rows), out


def role_to_name(postings: list[dict]) -> dict | None:
    best = next((p for p in postings if p["state"] == "open"), None)
    if best is None:
        return None
    return {k: best[k] for k in ("posting_id", "title", "url", "age_days")}


# ---------------------------------------------------------------------------------------------- people

def people_rows(conn: sqlite3.Connection, ids: list[int]) -> list[sqlite3.Row]:
    return conn.execute(f"SELECT person_id, company_id, name, title, role_kind, channel FROM person "
                        f"WHERE company_id IN ({marks(ids)}) ORDER BY person_id", ids).fetchall()


def email_status(conn: sqlite3.Connection, person_ids: list[int]) -> dict[int, str]:
    """person_id to a word: not_started, the touch's end state (replied, bounced, closed), or the state of the newest
    message that is not planned."""
    if not person_ids:
        return {}
    touches = {r["person_id"]: r for r in conn.execute(
        f"SELECT touch_id, person_id, state FROM touch WHERE person_id IN ({marks(person_ids)})", person_ids)}
    latest: dict[int, str] = {}
    touch_ids = [t["touch_id"] for t in touches.values()]
    if touch_ids:
        for m in conn.execute(f"SELECT touch_id, state FROM message WHERE touch_id IN ({marks(touch_ids)}) "
                              f"AND state <> 'planned' ORDER BY touch_id, seq", touch_ids):
            latest[m["touch_id"]] = m["state"]
    out = {}
    for pid in person_ids:
        t = touches.get(pid)
        out[pid] = ("not_started" if t is None else t["state"] if t["state"] != "open"
                    else latest.get(t["touch_id"], "not_started"))
    return out


def person_brief(p: sqlite3.Row) -> dict:
    return {"person_id": p["person_id"], "name": clip(p["name"]), "title": clip(p["title"]),
            "role_kind": p["role_kind"], "channel": p["channel"]}


# ---------------------------------------------------------------------------------------------- ranked_companies

class RankedInput(Input):
    limit: int = Field(20, ge=1, le=60, description="How many companies, best first (1 to 60).")


def _touched_survivors(conn: sqlite3.Connection, family: Family) -> set[int]:
    """Survivors with a live touch: any outreach touch not undone, or a first email that went out."""
    ids = {r[0] for r in conn.execute("SELECT DISTINCT company_id FROM outreach_touch WHERE undone_at IS NULL")}
    ids |= {r[0] for r in conn.execute("SELECT DISTINCT p.company_id FROM touch t JOIN person p "
                                       "ON p.person_id = t.person_id WHERE t.first_sent_at IS NOT NULL")}
    return {family.survivor(c) for c in ids}


def ranked_companies(ctx: Ctx, args: RankedInput) -> dict:
    conn = ctx.conn
    version = latest_version(conn)
    if version is None:
        return {"ranked": False, "message": "No ranking has run yet, so there is no list to show. The ranking runs "
                                            "in the live checkout's refresh (python -m outreach.ranking)."}
    family = Family(conn)
    held = held_survivors(family.companies)
    _no_go_people, no_go_companies = no_go_sets(conn, family.companies)
    touched = _touched_survivors(conn, family)
    left_out = {"touched": 0, "no_go": 0, "held": 0, "closed_or_removed": 0}
    eligible = []
    for s in ranked_list(conn, version, laned_only=True):
        row = family.companies.get(s.company_id)
        if row is None or row["status"] in ("closed", "removed"):  # a merged duplicate is removed
            left_out["closed_or_removed"] += 1
        elif family.survivor(s.company_id) in no_go_companies:
            left_out["no_go"] += 1
        elif s.company_id in held:
            left_out["held"] += 1
        elif s.company_id in touched:
            left_out["touched"] += 1
        else:
            eligible.append(s)
    shown = eligible[:args.limit]
    ids = [s.company_id for s in shown]
    names = {r["company_id"]: r["name"] for r in conn.execute(
        f"SELECT company_id, name FROM company WHERE company_id IN ({marks(ids)})", ids)} if ids else {}
    best_ids = [s.signals.get("best_posting_id") for s in shown if s.signals.get("best_posting_id")]
    postings = {r["posting_id"]: r for r in conn.execute(
        f"SELECT posting_id, title, url, posted_on, first_seen_at FROM posting WHERE closed_at IS NULL "
        f"AND posting_id IN ({marks(best_ids)})", best_ids)} if best_ids else {}
    people: dict[int, list] = {}
    if ids:
        members = {cid: family.members(cid) for cid in ids}
        all_ids = sorted({m for ms in members.values() for m in ms})
        by_company: dict[int, list] = {}
        for p in people_rows(conn, all_ids):
            by_company.setdefault(family.survivor(p["company_id"]), []).append(p)
        people = by_company
    companies = []
    for rank, s in enumerate(shown, 1):
        reason = s.signals.get("rank_reason") or {}
        post = postings.get(s.signals.get("best_posting_id"))
        known = people.get(s.company_id, [])
        companies.append({
            "rank": rank, "company_id": s.company_id, "name": clip(names[s.company_id]), "lanes": list(s.lanes),
            "reason": clip(reason.get("text"), 300), "confidence": reason.get("level"),
            "role": None if post is None else {
                "posting_id": post["posting_id"], "title": clip(post["title"]), "url": clip(post["url"], URL_CHARS),
                "age_days": age_days(post["posted_on"], post["first_seen_at"], ctx.today)},
            "people": [{"person_id": p["person_id"], "name": clip(p["name"]), "title": clip(p["title"])}
                       for p in known[:LIST_PEOPLE]],
            "people_total": len(known),
        })
    return {"ranked": True, "ranker_version": version, "scored_at": shown[0].scored_at if shown else None,
            "eligible": len(eligible), "shown": len(companies), "left_out": left_out, "companies": companies}


# ---------------------------------------------------------------------------------------------- company

class CompanyInput(Input):
    company_id: int = Field(ge=1, le=MAX_ID, description="A company id from ranked_companies or person.")


def fact_text(fact: str, value: str) -> str:
    """Filings and fundings are JSON in the store; shown as a person reads them."""
    try:
        if fact == "lca":
            d = json.loads(value)
            return f"{d['software']} software filing{'' if d['software'] == 1 else 's'} in FY{d['fy']}"
        if fact == "funding":
            d = json.loads(value)
            amount = d.get("amount_usd")
            return f"{d['kind']} filed {d['filed_on']}" + (f", ${amount:,}" if amount else "")
    except (ValueError, KeyError, TypeError):
        return clip(value)
    return clip(value)


def _neg(text: str) -> tuple:
    return tuple(-ord(c) for c in text)


def _fact_rank(row: sqlite3.Row) -> tuple:
    """The company_fact view's own order, to choose between the winners of merged companies."""
    confidence = {"confirmed": 0, "estimate": 1}.get(row["confidence"], 2)
    return (-row["locked"], _neg(row["recorded_at"]) if row["locked"] else (), confidence, _neg(row["recorded_at"]),
            -row["evidence_id"])


def best_facts(conn: sqlite3.Connection, ids: list[int]) -> dict[str, sqlite3.Row]:
    best: dict[str, sqlite3.Row] = {}
    for row in conn.execute(f"SELECT evidence_id, fact, value, confidence, locked, source_kind, recorded_at "
                            f"FROM company_fact WHERE company_id IN ({marks(ids)})", ids):
        if row["fact"] not in best or _fact_rank(row) < _fact_rank(best[row["fact"]]):
            best[row["fact"]] = row
    return best


def resolve(conn: sqlite3.Connection, company_id: int) -> tuple[Family, int, str | None]:
    family = Family(conn)
    if company_id not in family.companies:
        raise ToolError("not_found", f"No company {company_id}.", "Use a company id from ranked_companies or person.")
    survivor = family.survivor(company_id)
    note = None if survivor == company_id else (f"Company {company_id} was merged into {survivor}; "
                                                f"this is {survivor}, with the history of both.")
    return family, survivor, note


def company(ctx: Ctx, args: CompanyInput) -> dict:
    conn = ctx.conn
    family, survivor, note = resolve(conn, args.company_id)
    ids = family.members(survivor)
    main = company_row(conn, survivor)
    best = best_facts(conn, ids)
    total, live = open_postings(conn, ids, ctx.today)
    people = people_rows(conn, ids)
    status = email_status(conn, [p["person_id"] for p in people])
    no_go = conn.execute(f"SELECT reason, source, created_at FROM no_go WHERE lifted_at IS NULL "
                         f"AND company_id IN ({marks(ids)}) ORDER BY created_at DESC LIMIT 1", ids).fetchone()
    reviews = conn.execute(f"SELECT review_id, kind, reason FROM review_item WHERE state = 'open' "
                           f"AND company_id IN ({marks(ids)}) ORDER BY created_at, review_id", ids).fetchall()
    version = latest_version(conn)
    score = conn.execute("SELECT signals, lanes FROM company_score WHERE company_id = ? AND ranker_version = ?",
                         (survivor, version)).fetchone() if version else None
    rank = None
    if score is not None:
        signals = json.loads(score["signals"])
        reason = signals.get("rank_reason") or {}
        hide = signals.get("hide")
        rank = {"reason": clip(reason.get("line"), 400), "confidence": reason.get("level"),
                "lanes": json.loads(score["lanes"]), "hidden": clip(hide.get("reason"), 400) if hide else None}
    return {
        "company": {"id": survivor, "name": main["name"], "status": main["status"],
                    "close_reason": main["close_reason"], "paused": main["paused_at"] is not None,
                    "held": survivor in held_survivors(family.companies)},
        "merged_note": note,
        "domain": best["domain"]["value"] if "domain" in best else None,
        "facts": [{"fact": k, "value": fact_text(k, best[k]["value"]), "confidence": best[k]["confidence"],
                   "source_kind": best[k]["source_kind"], "locked": bool(best[k]["locked"])}
                  for k in FACT_ORDER if k in best],
        "rank": rank,
        "postings": {"total": total, "open_total": len(live), "open": live[:OPEN_POSTINGS]},
        "people": [{**person_brief(p), "status": "linkedin" if p["channel"] == "linkedin" else status[p["person_id"]]}
                   for p in people[:COMPANY_PEOPLE]],
        "people_total": len(people),
        "no_go": None if no_go is None else {"reason": no_go["reason"], "source": no_go["source"],
                                             "since": no_go["created_at"]},
        "open_review": [{"review_id": r["review_id"], "kind": r["kind"], "reason": clip(r["reason"], 400)}
                        for r in reviews[:OPEN_REVIEWS]],
    }


# ---------------------------------------------------------------------------------------------- person

class PersonInput(Input):
    person_id: int = Field(ge=1, le=MAX_ID, description="A person id from ranked_companies, company or followups_due.")


def why_not_email(person, survivor_row, family: Family, conn, touch, first) -> str | None:
    """The first reason this person cannot get a first email drafted by Claude, in the plan's order; None if they
    can. `first` is their seq 0 message, if any."""
    no_go_people, no_go_companies = no_go_sets(conn, family.companies)
    if person["email"] is None:
        return "no_email"
    if person["channel"] == "linkedin":
        return "linkedin_person"
    if person["person_id"] in no_go_people:
        return "person_no_go"
    if touch is not None and touch["reply_kind"] in STOPPED_REPLIES:
        return "said_no"
    if survivor_row["company_id"] in no_go_companies:
        return "company_no_go"
    if survivor_row["status"] == "closed":
        return "company_closed"
    if survivor_row["status"] == "removed":
        return "company_removed"
    if (touch is not None and touch["first_sent_at"] is not None) or (first is not None
                                                                      and first["state"] in SENT_STATES):
        return "already_emailed"
    if first is not None and first["saved_by"] == "devanshu":
        return "edited_by_devanshu"
    if first is not None and first["state"] == "approved":
        return "approved_or_later"
    if first is not None and first["state"] == "skipped":
        return "skipped"
    return None


def person(ctx: Ctx, args: PersonInput) -> dict:
    conn = ctx.conn
    p = conn.execute("SELECT * FROM person WHERE person_id = ?", (args.person_id,)).fetchone()
    if p is None:
        raise ToolError("not_found", f"No person {args.person_id}.", "Use a person id from company or followups_due.")
    family = Family(conn)
    survivor = family.survivor(p["company_id"])
    ids = family.members(survivor)
    main = company_row(conn, survivor)
    sources = conn.execute("SELECT kind, url, label, quote, email_basis FROM person_source WHERE person_id = ? "
                           "ORDER BY source_id LIMIT ?", (p["person_id"], SOURCES)).fetchall()
    touch = conn.execute("SELECT touch_id, state, reply_kind, first_sent_at FROM touch WHERE person_id = ?",
                         (p["person_id"],)).fetchone()
    messages = conn.execute(
        "SELECT message_id, seq, state, subject, body, hook, project, saved_by, revision, due_on, sent_at "
        "FROM message WHERE touch_id = ? ORDER BY seq", (touch["touch_id"],)).fetchall() if touch else []
    first = next((m for m in messages if m["seq"] == 0), None)
    note = conn.execute("SELECT draft_id, body, reason_line, reason_state, saved_by, revision FROM linkedin_draft "
                        "WHERE person_id = ?", (p["person_id"],)).fetchone()
    colleagues = conn.execute(
        f"SELECT c.person_id, c.name, c.title, m.hook, m.subject FROM person c "
        f"LEFT JOIN touch t ON t.person_id = c.person_id LEFT JOIN message m ON m.touch_id = t.touch_id AND m.seq = 0 "
        f"WHERE c.company_id IN ({marks(ids)}) AND c.person_id <> ? ORDER BY c.person_id LIMIT ?",
        [*ids, p["person_id"], COLLEAGUES]).fetchall()
    _total, live = open_postings(conn, ids, ctx.today)
    why = why_not_email(p, main, family, conn, touch, first)
    return {
        "person": {"id": p["person_id"], "name": p["name"], "title": p["title"], "role_kind": p["role_kind"],
                   "channel": p["channel"], "email": p["email"], "linkedin_url": p["linkedin_url"],
                   "added_by": p["added_by"]},
        "company": {"id": survivor, "name": main["name"], "status": main["status"]},
        "profile": capped(p["profile_text"], PROFILE_CHARS),
        "notes": capped(p["notes"], NOTES_CHARS),
        "sources": [{"kind": s["kind"], "url": clip(s["url"], URL_CHARS), "label": clip(s["label"]),
                     "quote": clip(s["quote"], QUOTE_CHARS), "email_basis": s["email_basis"], "untrusted": True}
                    for s in sources],
        "role_to_name": role_to_name(live),
        "email": None if touch is None else {
            "touch_state": touch["state"], "reply_kind": touch["reply_kind"], "first_sent_at": touch["first_sent_at"],
            "messages": [{"message_id": m["message_id"], "seq": m["seq"], "state": m["state"],
                          "subject": m["subject"], "body": clip(m["body"], BODY_CHARS), "hook": m["hook"],
                          "project": m["project"], "saved_by": m["saved_by"], "revision": m["revision"],
                          "due_on": m["due_on"], "sent_at": m["sent_at"]} for m in messages]},
        "note_draft": None if note is None else {
            "draft_id": note["draft_id"], "body": clip(note["body"], BODY_CHARS), "reason_line": note["reason_line"],
            "reason_state": note["reason_state"], "saved_by": note["saved_by"], "revision": note["revision"]},
        "colleagues": [{"person_id": c["person_id"], "name": clip(c["name"]), "title": clip(c["title"]),
                        "hook": c["hook"], "subject": clip(c["subject"])} for c in colleagues],
        "can_draft_email": why is None,
        "why_not": why,
    }


# ---------------------------------------------------------------------------------------------- followups_due

class FollowupsInput(Input):
    days_ahead: int = Field(0, ge=0, le=7, description="Also list follow-ups due this many days ahead (0 to 7).")


def followups_due(ctx: Ctx, args: FollowupsInput) -> dict:
    conn = ctx.conn
    ids = due_followups(conn, ctx.today, days_ahead=args.days_ahead)
    shown = ids[:FOLLOWUPS]
    rows = {r["message_id"]: r for r in conn.execute(
        f"SELECT m.message_id, m.seq, m.due_on, m.touch_id, p.person_id, p.name, p.email, p.company_id "
        f"FROM message m JOIN touch t ON t.touch_id = m.touch_id JOIN person p ON p.person_id = t.person_id "
        f"WHERE m.message_id IN ({marks(shown)})", shown)} if shown else {}
    touch_ids = sorted({r["touch_id"] for r in rows.values()})
    firsts = {r["touch_id"]: r for r in conn.execute(
        f"SELECT touch_id, subject, sent_at FROM message WHERE seq = 0 AND touch_id IN ({marks(touch_ids)})",
        touch_ids)} if touch_ids else {}
    family = Family(conn) if rows else None
    survivors = sorted({family.survivor(r["company_id"]) for r in rows.values()}) if rows else []
    names = {r["company_id"]: r["name"] for r in conn.execute(
        f"SELECT company_id, name FROM company WHERE company_id IN ({marks(survivors)})", survivors)} if rows else {}
    items = []
    for mid in shown:
        r = rows[mid]
        survivor = family.companies[family.survivor(r["company_id"])]
        first = firsts.get(r["touch_id"])
        items.append({
            "message_id": mid, "seq": r["seq"], "due_on": r["due_on"],
            "person": {"id": r["person_id"], "name": r["name"], "email": r["email"]},
            "company": {"id": survivor["company_id"], "name": names[survivor["company_id"]],
                        "paused": survivor["paused_at"] is not None},
            "first": None if first is None else {"subject": first["subject"], "sent_at": first["sent_at"]},
        })
    return {"today": ctx.today.isoformat(), "days_ahead": args.days_ahead, "total": len(ids),
            "more": len(ids) - len(shown), "items": items}


TOOLS = (
    ToolSpec("ranked_companies",
             "Today's companies until the people plan exists: the latest ranking's companies that have a lane, no "
             "live touch, no active no-go and no hold, best first. Each has its rank reason and confidence, the role "
             "to name and the people already known.", RankedInput, ranked_companies),
    ToolSpec("company",
             "One company (a merged id answers as its survivor): facts with their confidence, open postings (no "
             "bodies), people and their email status, an active no-go, open review items and its rank.",
             CompanyInput, company),
    ToolSpec("person",
             "One person: profile and sources (untrusted text), the role to name, their email thread and drafts, "
             "their LinkedIn note, colleagues' hooks and subjects (never bodies), and whether Claude may draft a "
             "first email (why_not says why not).", PersonInput, person),
    ToolSpec("followups_due",
             "Follow-ups due today (or within days_ahead): planned rows whose first email went out with no reply, "
             "from the same query the sender uses, with the first email's subject.", FollowupsInput, followups_due),
)
