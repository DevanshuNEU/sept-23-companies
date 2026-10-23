"""The company page: one query per table for the company and every company merged into it, joined in Python.

A merged company answers as its survivor, and the survivor's page carries the history of every company merged into
it (events, evidence, postings, people), so nothing is lost by a merge.
"""
import json
import sqlite3
from datetime import date, datetime, timezone

from outreach.api.models.company import (
    CompanyBoard, CompanyClosed, CompanyEvent, CompanyHeader, CompanyPerson, CompanyTouch, CompanyView, Fact,
    PersonRef, PostingFlag, PostingRow, RoleToName, ThreadMessage,
)
from outreach.api.queries import people as people_queries
from outreach.api.queries.people import scope
from outreach.drafts.followups import company_map, held_survivors, resolve_company
from outreach.sending import calendar
from outreach.sending.config import load_config
from outreach.store.timefmt import ISO_FMT
from outreach.store.writes import _NAME_ALIAS_SOURCES, name_key

EVENT_LIMIT = 500  # the newest events; a board read every few days adds up
READS_SHOWN = 10
DAYS_OF_EMAIL = 3
NAME_ALIAS_SOURCES = _NAME_ALIAS_SOURCES  # the store's own list of name-like alias sources
IMPORT_SOURCE_KEY = "importSource"  # the key of an import event's detail that names the old place it came from
EMAIL_HOLDING_KINDS = ("domain_missing", "size_unknown", "size_guess", "size_check")
IMPORT_SOURCES = ("data_js", "local_storage", "contacts_json", "radar_db", "devboard", "contacted_txt", "devvault_notes")
TIER_RANK = {"new_grad": 0, "early_career": 1, "swe1": 2, "swe2": 3, "open": 4, "not_stated": 5, "senior": 6}
STATE_RANK = {"open": 0, "out_of_lane": 1, "hidden": 2, "closed": 3}
FACT_ORDER = ("size", "stage", "sponsorship", "domain", "hq", "everify", "lca", "funding", "headcount", "cap_exempt",
              "staffing_risk")
CLOSE_WORDS = {"referral": "referral", "interview": "interview", "no_sponsorship": "no sponsorship",
               "no_referrals": "no referrals", "already_hired": "already hired", "not_now": "not now", "other": "other"}
POSTING_CLOSE_WORDS = {
    "gone_from_board": "Closed on the board", "board_empty_twice": "The board went empty twice",
    "page_closed": "The page closed", "hand": "Closed by you", "imported_closed": "Closed in the old system",
}
ATS_LABELS = {
    "ashby": "Ashby", "greenhouse": "Greenhouse", "lever": "Lever", "workable": "Workable",
    "smartrecruiters": "SmartRecruiters", "rippling": "Rippling", "gem": "Gem", "recruitee": "Recruitee",
    "bamboohr": "BambooHR", "dover": "Dover", "breezy": "Breezy", "workday": "Workday", "teamtailor": "Teamtailor",
    "pinpoint": "Pinpoint", "jazzhr": "JazzHR", "join_com": "JOIN", "personio": "Personio", "icims": "iCIMS",
}
ATS_URLS = {
    "ashby": "https://jobs.ashbyhq.com/{t}", "greenhouse": "https://boards.greenhouse.io/{t}",
    "lever": "https://jobs.lever.co/{t}", "workable": "https://apply.workable.com/{t}",
    "smartrecruiters": "https://jobs.smartrecruiters.com/{t}", "rippling": "https://ats.rippling.com/{t}/jobs",
    "gem": "https://jobs.gem.com/{t}", "recruitee": "https://{t}.recruitee.com", "breezy": "https://{t}.breezy.hr",
    "bamboohr": "https://{t}.bamboohr.com/careers", "teamtailor": "https://{t}.teamtailor.com",
    "pinpoint": "https://{t}.pinpointhq.com", "jazzhr": "https://{t}.applytojob.com",
    "join_com": "https://join.com/companies/{t}", "personio": "https://{t}.jobs.personio.de",
    "dover": "https://app.dover.com/apply/{t}",
}
HIDDEN_RULES = {
    "us": "Not a US role", "sponsorship": "Says it will not sponsor", "citizenship": "Needs US citizenship or a clearance",
    "grad_window": "Open only to a graduation window we are outside", "current_student": "Only for current students",
    "cohort_title": "A cohort or program role",
}
LANE_RULES = {
    "off_lane": "Outside the engineering lane", "not_engineering": "Not an engineering role", "intern": "Internship",
    "general_application": "A general application, not a role",
}
RULES_QUOTE = {"sponsorship": "sponsorship_quote", "citizenship_or_clearance": "citizenship_quote",
               "student_or_grad_window": "eligibility_quote"}
SPONSORSHIP_SHOWN = {"yes": "says_yes", "no": "says_no", "opt_only": "opt_only", "unclear": "unclear"}


# ---------------------------------------------------------------------------------------------- merged companies

def merge_parents(conn: sqlite3.Connection) -> dict[int, int]:
    """company_id to the company it was merged into, for every merged company (a partial index covers it)."""
    return {r[0]: r[1] for r in conn.execute("SELECT company_id, merged_into FROM company WHERE merged_into IS NOT NULL")}


def survivor_of(parents: dict[int, int], company_id: int) -> int:
    """Follow `merged_into` to its end (a loop cannot happen, but one would stop at the id it came back to)."""
    seen = {company_id}
    while company_id in parents and parents[company_id] not in seen:
        company_id = parents[company_id]
        seen.add(company_id)
    return company_id


def holding_touches(companies: dict[int, sqlite3.Row]) -> dict[int, int]:
    """survivor id to the touch that holds it, read across the merged family as the sender reads it: the survivors
    are `held_survivors` of `company_map`, so a hold left on a duplicate that was merged away holds the survivor. The
    survivor's own hold wins, else the lowest company id's. In a merge cycle (no survivor) each company keeps its
    own."""
    try:
        held = held_survivors(companies)
    except RuntimeError:
        return {cid: r["held_by_touch"] for cid, r in companies.items() if r["held_by_touch"] is not None}
    found: dict[int, int] = {}
    for cid in sorted(companies, key=lambda c: (companies[c]["merged_into"] is not None, c)):
        touch = companies[cid]["held_by_touch"]
        if touch is not None:
            found.setdefault(resolve_company(companies, cid)[-1], touch)
    return {survivor: found[survivor] for survivor in held}


def group_of(parents: dict[int, int], survivor: int) -> list[int]:
    """The survivor and every company that ends at it."""
    return [survivor] + sorted(c for c in parents if survivor_of(parents, c) == survivor)


def status_line(status: str, close_reason: str | None, paused_at: str | None, held: int | None) -> str:
    """One plain line for a company in the search list."""
    if status == "closed":
        word = CLOSE_WORDS.get(close_reason or "", "closed")
        return f"Done: {word}" if close_reason in ("interview", "referral") else f"Closed: {word}"
    if status == "removed":
        return "Removed by you"
    if status == "working":
        return "Paused by you" if paused_at else "Held: reply" if held is not None else "Emailing"
    return "Backlog" if status == "backlog" else "Candidate"


# ---------------------------------------------------------------------------------------------- facts

def fact_text(fact: str, value: str) -> str:
    """The stored value as a person reads it: filings and fundings are JSON in the store."""
    if fact == "lca":
        d = json.loads(value)
        n = d["software"]
        return f"{n} software filing{'' if n == 1 else 's'} in FY{d['fy']}"
    if fact == "funding":
        d = json.loads(value)
        amount = d.get("amount_usd")
        return f"{d['kind']} filed {d['filed_on']}" + (f", ${amount:,}" if amount else "")
    return value


def to_fact(row: sqlite3.Row) -> Fact:
    return Fact(
        id=row["evidence_id"], fact=row["fact"], value=fact_text(row["fact"], row["value"]), low=row["num_low"],
        high=row["num_high"], confidence=row["confidence"], locked=bool(row["locked"]), source_kind=row["source_kind"],
        source_ref=row["source_ref"], quote=row["quote"], observed_on=row["observed_on"],
    )


def fact_rank(row: sqlite3.Row) -> tuple:
    """The `company_fact` view's own order, for choosing between the winners of two merged companies."""
    confidence = {"confirmed": 0, "estimate": 1}.get(row["confidence"], 2)
    return (-row["locked"], _neg(row["recorded_at"]) if row["locked"] else "", confidence, _neg(row["recorded_at"]), -row["evidence_id"])


def _neg(text: str) -> tuple:
    return tuple(-ord(c) for c in text)


def best_facts(rows: list[sqlite3.Row]) -> dict[str, sqlite3.Row]:
    best: dict[str, sqlite3.Row] = {}
    for row in rows:
        if row["fact"] not in best or fact_rank(row) < fact_rank(best[row["fact"]]):
            best[row["fact"]] = row
    return best


# ---------------------------------------------------------------------------------------------- postings

def hidden_rule(d: sqlite3.Row) -> tuple[str, str | None]:
    """(state, the rule that put it there) for a posting that has a decision and is not closed."""
    if d["lane"] != "engineering":
        return "out_of_lane", LANE_RULES.get(d["lane"], "Outside the engineering lane")
    if d["us"] == "no":
        return "hidden", HIDDEN_RULES["us"]
    if d["sponsorship"] == "no":
        return "hidden", HIDDEN_RULES["sponsorship"]
    if d["citizenship"]:
        return "hidden", HIDDEN_RULES["citizenship"]
    if d["eligibility"] == "excluded":
        return "hidden", HIDDEN_RULES.get(d["eligibility_reason"], "Not open to you")
    if not d["level_ok"] and (d["level_tier"] != "not_stated" or d["years_asked"] is not None):
        return "out_of_lane", d["level_basis"]
    return "open", None


def load_explain():
    """The reader's `explain`, or None while the reader package (#32) is not on this branch. Only the import is
    guarded: a failing `explain` call is an error, never "no flags"."""
    try:
        from outreach.reader.combine import explain
    except ImportError:
        return None
    return explain


def flags_from_verdicts(verdicts, decision: sqlite3.Row | None) -> list[PostingFlag]:
    """The fields where reader and rules disagree (or the reader could not say) and either side would hide."""
    flags = []
    for v in verdicts:
        if v.state not in ("disagree", "missing") or not (v.would_hide_rules or v.would_hide_reader):
            continue
        column = RULES_QUOTE.get(v.field)
        flags.append(PostingFlag(
            field=v.field, reader_value=_text(v.reader_value), rules_value=_text(v.rules_value),
            reader_quote=v.reader_quote or "", rules_quote=(decision[column] if decision is not None and column else "") or "",
        ))
    return flags


def _text(value) -> str | None:
    if value is None:
        return None
    return ("yes" if value else "no") if isinstance(value, bool) else str(value)


# ---------------------------------------------------------------------------------------------- the view

def company_view(conn: sqlite3.Connection, company_id: int, now: str) -> CompanyView | None:
    """The page of `company_id` (answered as its survivor), or None when there is no such company."""
    from outreach.api.queries import needs_you  # it builds the review items, and imports this module

    if conn.execute("SELECT 1 FROM company WHERE company_id = ?", (company_id,)).fetchone() is None:
        return None
    parents = merge_parents(conn)
    survivor = survivor_of(parents, company_id)
    ids = group_of(parents, survivor)
    where, params = scope(ids, "company_id")

    companies = {r["company_id"]: r for r in conn.execute(f"SELECT * FROM company{where}", params)}
    main = companies[survivor]
    evidence_rows = conn.execute(f"SELECT * FROM evidence{where} ORDER BY recorded_at DESC, evidence_id DESC", params).fetchall()
    fact_rows = conn.execute(f"SELECT * FROM company_fact{where}", params).fetchall()
    best = best_facts(fact_rows)
    alias_rows = conn.execute(f"SELECT * FROM company_alias{where}", params).fetchall()
    boards = conn.execute(f"SELECT * FROM board{where}", params).fetchall()
    posting_rows = conn.execute(f"SELECT * FROM posting{where}", params).fetchall()
    where_p, params_p = scope(ids, "p.company_id")
    decisions = {
        r["posting_id"]: r for r in conn.execute(
            f"SELECT d.* FROM posting_decision d JOIN posting p ON p.posting_id = d.posting_id{where_p}", params_p)
    }
    read_ids = {
        r[0] for r in conn.execute(
            f"SELECT DISTINCT r.posting_id FROM posting_reading r JOIN posting p ON p.posting_id = r.posting_id{where_p}",
            params_p)
    }
    data = people_queries.load(conn, ids, bodies=True)
    event_rows = conn.execute(
        f"SELECT * FROM event{where} ORDER BY at DESC, event_id DESC LIMIT {EVENT_LIMIT}", params).fetchall()
    review_rows = conn.execute(
        f"SELECT * FROM review_item{where} AND state = 'open' ORDER BY created_at, review_id", params).fetchall()
    picked_dates = [r[0] for r in conn.execute(
        f"SELECT DISTINCT list_date FROM daily_list_entry{where} AND state = 'picked' ORDER BY list_date DESC", params)]
    ruleset = conn.execute("SELECT ruleset_version FROM ruleset WHERE is_current = 1").fetchone()

    domain = best["domain"]["value"] if "domain" in best else None
    mx = conn.execute("SELECT status FROM mx_check WHERE domain = ?", (domain,)).fetchone() if domain else None

    postings, role = rank_postings(posting_rows, decisions, read_ids, conn)
    cfg = load_config()
    today = calendar.ny_date(datetime.strptime(now, ISO_FMT).replace(tzinfo=timezone.utc), cfg)
    picked_on = picked_dates[0] if picked_dates else None

    # A person on a merged duplicate is judged against the survivor's pause and the family's hold.
    held_by = holding_touches(company_map(conn)).get(survivor)
    person_lines = people_queries.lines(data, {c: companies[survivor_of(parents, c)] for c in companies},
                                        {survivor: held_by} if held_by is not None else {})
    people = [
        CompanyPerson(
            person=person_ref(p), channel=p["channel"], status_line=person_lines[p["person_id"]].status_line,
            tone=person_lines[p["person_id"]].tone, last_touch_at=person_lines[p["person_id"]].last_touch_at,
        )
        for p in data.persons
    ]
    by_person = {p["person_id"]: p for p in data.persons}
    touches = [
        CompanyTouch(
            touch_id=t["touch_id"], person=person_ref(by_person[t["person_id"]]), state=t["state"],
            summary=people_queries.touch_summary(t, data.messages_by_touch.get(t["touch_id"], [])),
            messages=[ThreadMessage(**m) for m in people_queries.thread(data, t)],
        )
        for t in sorted(data.touch_by_person.values(), key=lambda t: t["touch_id"])
    ]

    return CompanyView(
        as_of=now,
        company=CompanyHeader(
            id=survivor, name=main["name"], status=main["status"], paused_at=main["paused_at"],
            held_by_touch_id=held_by, domain=domain, aliases=aliases_for(companies, survivor, alias_rows),
            hq=best["hq"]["value"] if "hq" in best else "", picked_on=picked_on,
            day=_day(main, picked_on, today, cfg),
            closed=CompanyClosed(reason=main["close_reason"], note=main["status_note"], at=main["status_at"])
            if main["status"] == "closed" else None,
            revision=main["revision"],
        ),
        summary=_summary(main["status"], bool(main["paused_at"]), held_by is not None, role, data.persons),
        facts=[to_fact(best[k]) for k in FACT_ORDER if k in best],
        sponsorship_shown=_sponsorship_shown(best, role, [p for p in postings if p['state'] != 'closed'], evidence_rows),
        mail_server_ok=None if mx is None else mx["status"] == "ok",
        board=_board(boards),
        role=role_to_name(role),
        postings=[p["model"] for p in postings],
        people=people,
        touches=touches,
        evidence=[to_fact(r) for r in evidence_rows],
        events=[_event(r) for r in event_rows],
        review_items=needs_you.review_items(conn, review_rows, parents, now),
        ruleset_version=ruleset["ruleset_version"] if ruleset else "",
    )


def person_ref(p: sqlite3.Row) -> PersonRef:
    return PersonRef(id=p["person_id"], name=p["name"], title=p["title"], role_kind=p["role_kind"], email=p["email"],
                     linkedin_url=p["linkedin_url"], revision=p["revision"])


def aliases_for(companies: dict, survivor: int, alias_rows) -> list[str]:
    own = name_key(companies[survivor]["name"])
    found: dict[str, str] = {}
    names = [r["alias"] for r in alias_rows if r["source"] in NAME_ALIAS_SOURCES]
    names += [c["name"] for cid, c in companies.items() if cid != survivor]  # a merged company lends its name
    for text in names:
        key = name_key(text)
        if key and key != own and key not in found:
            found[key] = text
    return sorted(found.values(), key=str.lower)


def _day(main: sqlite3.Row, picked_on: str | None, today: date, cfg) -> int | None:
    if main["status"] != "working" or picked_on is None:
        return None
    gone = calendar.business_days_between(date.fromisoformat(picked_on), today, cfg)
    return max(1, min(DAYS_OF_EMAIL, 1 + gone))


def _summary(status: str, paused: bool, held: bool, role, persons) -> str:
    phrase = "Paused" if paused else "Held for a reply" if held and status == "working" else {
        "candidate": "A candidate", "backlog": "In the backlog", "working": "Being emailed", "closed": "Closed",
        "removed": "Removed",
    }[status]
    emailed = sum(1 for p in persons if p["channel"] == "email")
    reached = len(persons) - emailed
    people = " and ".join(
        [f"{emailed} by email"] * bool(emailed) + [f"{reached} on LinkedIn"] * bool(reached)) or "no people yet"
    naming = f"naming {role['row']['title']}" if role else "no open role"
    return f"{phrase}, {naming}; {people}."


def _sponsorship_shown(best: dict, role, live_postings, evidence_rows) -> str:
    fact = best.get("sponsorship")
    if fact is not None and fact["locked"] and fact["value"] in SPONSORSHIP_SHOWN and fact["value"] != "unclear":
        return SPONSORSHIP_SHOWN[fact["value"]]
    # A yes, opt_only or unclear counts only from an open posting that is not hidden (the role to name). A no counts
    # from any posting that is not closed: it is the evidence for the hide, and honest to show.
    if role is not None and role["sponsorship"] in SPONSORSHIP_SHOWN:
        return SPONSORSHIP_SHOWN[role["sponsorship"]]
    if any(p["sponsorship"] == "no" for p in live_postings):
        return "says_no"
    if any(json.loads(r["value"]).get("software", 0) > 0 for r in evidence_rows if r["fact"] == "lca"):
        return "has_sponsored"
    return "not_stated"


def _board(boards) -> CompanyBoard | None:
    if not boards:
        return None
    b = sorted(boards, key=lambda b: (-b["active"], _neg(b["last_read_at"] or ""), b["board_id"]))[0]
    return CompanyBoard(
        ats=ATS_LABELS.get(b["ats"], b["ats"].replace("_", " ").title()), token=b["token"],
        url=ATS_URLS.get(b["ats"], "").format(t=b["token"]), last_read="ok" if b["last_read"] == "unchanged" else b["last_read"],
        last_read_at=b["last_read_at"], error_streak=b["error_streak"], empty_streak=b["empty_streak"],
    )


def rank_postings(posting_rows, decisions: dict, read_ids: set, conn) -> tuple[list[dict], dict | None]:
    """Every posting as a row with its state, and the open one to name (best level first)."""
    explain = load_explain() if read_ids else None
    out = []
    for p in posting_rows:
        d = decisions.get(p["posting_id"])
        if p["closed_at"]:
            state, rule = "closed", None
        elif d is None:
            state, rule = "open", None
        else:
            state, rule = hidden_rule(d)
        flags = flags_from_verdicts(explain(conn, p["posting_id"]), d) if explain and p["posting_id"] in read_ids else []
        tier = d["level_tier"] if d else "not_stated"
        out.append({
            "row": p, "decision": d, "state": state, "tier": tier, "years": d["years_asked"] if d else None,
            "start": bool(d["start_2027"]) if d else False, "sponsorship": d["sponsorship"] if d else "silent",
            "model": PostingRow(
                id=p["posting_id"], title=p["title"], location=p["location"], url=p["url"],
                first_seen_at=p["first_seen_at"], last_seen_at=p["last_seen_at"], closed_at=p["closed_at"],
                close_reason=POSTING_CLOSE_WORDS.get(p["close_reason"]) if p["close_reason"] else None,
                level_tier=tier, sponsorship=d["sponsorship"] if d else "silent", state=state, hidden_rule=rule,
                flags=flags,
            ),
        })
    out.sort(key=lambda x: x["row"]["first_seen_at"], reverse=True)  # newest first, then a stable sort by state
    candidates = sorted(
        (x for x in out if x["state"] == "open"),
        key=lambda x: (0 if x["decision"] is not None and x["decision"]["level_ok"] else 1, TIER_RANK[x["tier"]],
                       0 if x["start"] else 1),
    )
    role = candidates[0] if candidates else None
    out.sort(key=lambda x: (-1 if x is role else STATE_RANK[x["state"]]))
    return out, role


def role_to_name(role: dict | None) -> RoleToName | None:
    """The role `rank_postings` chose to name, as the wire shape (None when there is no open role)."""
    if role is None:
        return None
    row = role["row"]
    return RoleToName(
        posting_id=row["posting_id"], title=row["title"], url=row["url"], location=row["location"],
        first_seen_at=row["first_seen_at"], level_tier=role["tier"], years_asked=role["years"], start2027=role["start"],
    )


def _event(r: sqlite3.Row) -> CompanyEvent:
    detail = json.loads(r["detail"])
    text = detail.get("text") if isinstance(detail.get("text"), str) and detail["text"] else r["kind"].replace("_", " ").capitalize()
    source = None
    if r["actor"] == "import":
        source = detail.get(IMPORT_SOURCE_KEY) if detail.get(IMPORT_SOURCE_KEY) in IMPORT_SOURCES else None
    return CompanyEvent(id=r["event_id"], at=r["at"], kind=r["kind"], text=text, actor=r["actor"], import_source=source)


def read_detail(read: sqlite3.Row, ats: str) -> str:
    """One read of a board, as the Needs you strip words it."""
    if read["result"] == "error":
        return read["error"] or (f"HTTP {read['http_status']}" if read["http_status"] else "The read failed")
    if read["result"] == "empty":
        return f"{ats} board returned no jobs"
    n = read["postings"]
    return f"{ats} board read, {n} posting{'' if n == 1 else 's'}"
