"""The people plan (#15) as the page reads it: `GET /api/plan`.

#15's plan job writes the day's `daily_list` and its `daily_list_entry` rows (a person, a channel and a `why` JSON per
row); its prepare step writes `linkedin_draft` notes. This module reads them and derives everything the page shows:

- a row's steps: `request` (a live `linkedin_request` touch for the row's person sent on or after the list date),
  then `apply` when the row has a role (an open posting, or one he already applied to: a live `application` touch for
  that posting, at any time). An email row has no steps: it is done once its first email left `drafted` (approved or
  later), and is reviewed in the Outbox. A find-someone row (no person) has a request step nobody can log yet;
- the state: `done` when every step has a time (an email row: approved or later), else `skipped` when the entry's
  own state is `backlog` (a skip sets it), else `todo`. A `removed` entry ("not a fit") is not on the plan;
- the per-row text, parsed by `parse_plan_why` with exactly #15's `why` keys (its plan's Hand-off). A key that is
  missing or of the wrong type is ignored: the reason falls back to the lane's sentence, the search link is built
  from the company name, the sources are empty;
- `notReady` (weekend, not built, the plan job failed today), the LinkedIn month from #15's `linkedin_capacity`,
  tomorrow's total from #15's `plan_capacity` and its carried rows from #15's `load_carried`, and the week's counts
  with the progress page's own funnel.

`PlanRow.entryId` is the entry's `rowid` (its key is `(list_date, company_id)`); `plan_entry_id` is the only place
that knows. Reads only: nothing here writes, builds a plan or calls a model.
"""
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import urlsplit

from outreach.api.models.checks import Check
from outreach.api.models.plan import (
    FindSomeone, LinkedinMonth, NoteDraft, NoteFact, PlanCompany, PlanPerson, PlanRow, PlanStep, PlanView, PlanWeek,
    Tomorrow, TomorrowRow,
)
from outreach.api.models.today import NotReady
from outreach.api.models.watch import SourceLink
from outreach.api.queries import jobs
from outreach.api.queries.company import company_holds, merge_parents, person_ref
from outreach.api.queries.outcomes import by_ids, people_by_ids
from outreach.api.queries.progress import live_touches
from outreach.api.queries.today import LANE_WORDS, build_entries
from outreach.outcomes import report
from outreach.outcomes.windows import day_bounds_utc, next_weekday, ny_day, week_bounds
from outreach.plan.capacity import linkedin_capacity, plan_capacity
from outreach.plan.config import PlanConfig
from outreach.plan.outbound import load_outbound
from outreach.plan.people import people_search_url
from outreach.plan.prospects import HANDOFF, load_carried
from outreach.ranking.common import canonical_ids

EMAIL_DONE = ("approved", "sending", "send_unknown", "sent")  # the first email left drafted
SEARCH_PREFIX = "https://www.linkedin.com/search/results/people/"
SEARCH_HINT = "engineer"
WEEKEND_TEXT = "No plan on weekends. The next one is below."
NOT_BUILT_TEXT = "Today's plan is not built yet. It builds at 8:00 on weekdays."
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

ENTRY_SQL = "SELECT rowid, * FROM daily_list_entry"


def plan_entry_id(row: sqlite3.Row) -> int:
    """`PlanRow.entryId`: the entry's rowid. Change this one function if the table gains its own id."""
    return row["rowid"]


# ---------------------------------------------------------------------------------------------- the why

@dataclass(frozen=True)
class PlanWhy:
    """The parts of `daily_list_entry.why` the page needs, each None or empty when the stored value is missing or
    not of the stored type."""

    reason: str | None = None
    reason_sources: list[SourceLink] = field(default_factory=list)
    role_url: str | None = None
    role_title: str | None = None
    found_on: SourceLink | None = None
    find_someone: FindSomeone | None = None
    carried_from: str | None = None


def _url(value) -> str | None:
    """A plain http(s) link with a host, else None (the page opens it in a new tab)."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    return value.strip() if parts.scheme in ("http", "https") and parts.hostname else None


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _link(value) -> SourceLink | None:
    if not isinstance(value, dict):
        return None
    label, url = _text(value.get("label")), _url(value.get("url"))
    return SourceLink(label=label, url=url) if label and url else None


def _iso_date(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value).isoformat() if len(value) == 10 else None
    except ValueError:
        return None


def parse_plan_why(text: str | None) -> PlanWhy:
    """Read exactly #15's `why` keys (`reason`, `reason_sources`, `role.url`, `role.title`, `person.found_on`,
    `find_someone.search_url` and `.handoff`, `carried_from`). Never raises: bad JSON or a wrong type is a missing
    value."""
    try:
        data = json.loads(text or "{}")
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        return PlanWhy()
    sources = data.get("reason_sources")
    role = data.get("role") if isinstance(data.get("role"), dict) else {}
    person = data.get("person") if isinstance(data.get("person"), dict) else {}
    find = data.get("find_someone") if isinstance(data.get("find_someone"), dict) else {}
    search = _url(find.get("search_url"))
    handoff = _text(find.get("handoff"))
    return PlanWhy(
        reason=_text(data.get("reason")),
        reason_sources=[link for link in (_link(x) for x in sources) if link] if isinstance(sources, list) else [],
        role_url=_url(role.get("url")),
        role_title=_text(role.get("title")),
        found_on=_link(person.get("found_on")),
        find_someone=FindSomeone(search_url=search, handoff=handoff)
        if search and search.startswith(SEARCH_PREFIX) and handoff else None,
        carried_from=_iso_date(data.get("carried_from")),
    )


# ---------------------------------------------------------------------------------------------- notes

def note_draft(row: sqlite3.Row, cfg: PlanConfig) -> NoteDraft:
    """A `linkedin_draft` row as the page shows it. The checks are the stored ones (#15's `check_note`)."""
    try:
        facts = json.loads(row["facts"])
    except ValueError:
        facts = []
    shown = []
    for f in facts if isinstance(facts, list) else []:
        if not isinstance(f, dict) or not isinstance(f.get("text"), str):
            continue
        shown.append(NoteFact(text=f["text"], source=SourceLink(label=str(f.get("label") or ""),
                                                                url=str(f.get("url") or "")),
                              in_note=bool(f.get("in_note"))))
    try:
        checks = [Check.model_validate(c) for c in json.loads(row["checks"] or "[]")]
    except ValueError:
        checks = []
    return NoteDraft(
        note_id=row["draft_id"], text=row["body"], chars=len(row["body"]), max_chars=cfg.get("note", "max_chars"),
        reason_line=row["reason_line"], reason_state=row["reason_state"],
        needs_your_line=row["reason_state"] == "proposed", facts=shown, checks=checks, saved_by=row["saved_by"],
        saved_at=row["updated_at"], revision=row["revision"])


def note_by_id(conn: sqlite3.Connection, note_id: int, cfg: PlanConfig) -> NoteDraft | None:
    row = conn.execute("SELECT * FROM linkedin_draft WHERE draft_id = ?", (note_id,)).fetchone()
    return note_draft(row, cfg) if row is not None else None


# ---------------------------------------------------------------------------------------------- rows

def entries_of(conn: sqlite3.Connection, list_date: str) -> list[sqlite3.Row]:
    """The plan's rows of one date, best rank first. A removed row ("not a fit") is not on the plan."""
    return conn.execute(f"{ENTRY_SQL} WHERE list_date = ? AND state <> 'removed' ORDER BY rank",
                        (list_date,)).fetchall()


def _first(rows, key: str) -> dict:
    """The oldest row for each value of `key` (the rows come oldest first)."""
    found: dict = {}
    for r in rows:
        found.setdefault(r[key], r)
    return found


def build_rows(conn: sqlite3.Connection, entries: list[sqlite3.Row], cfg: PlanConfig) -> list[PlanRow]:
    """A `PlanRow` for each entry, in the entries' order, positions 1 up. One query per table, however many rows."""
    if not entries:
        return []
    parents = merge_parents(conn)
    daily = build_entries(conn, entries, parents, company_holds(conn))  # company, its facts and the open role
    person_ids = {e["person_id"] for e in entries if e["person_id"] is not None}
    people = people_by_ids(conn, person_ids)
    drafts = {r["person_id"]: r for r in by_ids(conn, "SELECT * FROM linkedin_draft WHERE person_id IN ({ids})",
                                                 person_ids)}
    requests: dict[int, list[sqlite3.Row]] = {}
    for t in by_ids(conn, "SELECT person_id, touch_id, sent_at FROM outreach_touch WHERE channel = 'linkedin_request' "
                          "AND undone_at IS NULL AND person_id IN ({ids}) ORDER BY sent_at, touch_id", person_ids):
        requests.setdefault(t["person_id"], []).append(t)
    posting_ids = {e["posting_id"] for e in entries if e["posting_id"] is not None}
    applied = _first(by_ids(conn, "SELECT posting_id, touch_id, sent_at FROM outreach_touch WHERE channel = "
                                  "'application' AND undone_at IS NULL AND posting_id IN ({ids}) "
                                  "ORDER BY sent_at, touch_id", posting_ids), "posting_id")
    email_people = {e["person_id"] for e in entries if e["channel"] == "email" and e["person_id"] is not None}
    threads: dict[int, sqlite3.Row] = {}
    for t in by_ids(conn, "SELECT touch_id, person_id FROM touch WHERE person_id IN ({ids}) ORDER BY touch_id",
                    email_people):
        threads[t["person_id"]] = t  # the newest thread is the Outbox item
    states = ",".join(f"'{s}'" for s in EMAIL_DONE)
    gone = {r["touch_id"] for r in by_ids(
        conn, f"SELECT DISTINCT touch_id FROM message WHERE seq = 0 AND state IN ({states}) "
              "AND touch_id IN ({ids})", {t["touch_id"] for t in threads.values()})}
    rows = []
    for position, (e, d) in enumerate(zip(entries, daily), 1):
        parsed = parse_plan_why(e["why"])
        person = people.get(e["person_id"]) if e["person_id"] is not None else None
        channel = "email" if e["channel"] == "email" else "linkedin_request"
        role = d.role
        email_touch = None
        steps: list[PlanStep] = []
        if channel == "email":
            thread = threads.get(e["person_id"])
            email_touch = thread["touch_id"] if thread is not None else None
            done = email_touch in gone
        else:
            start = day_bounds_utc(e["list_date"])[0]
            sent = next((t for t in requests.get(e["person_id"], []) if t["sent_at"] >= start), None) \
                if person is not None else None
            steps.append(PlanStep(kind="request", touch_id=sent["touch_id"] if sent else None,
                                  at=sent["sent_at"] if sent else None))
            app = applied.get(e["posting_id"]) if e["posting_id"] is not None else None
            if role is not None or app is not None:
                steps.append(PlanStep(kind="apply", touch_id=app["touch_id"] if app else None,
                                      at=app["sent_at"] if app else None))
            done = all(st.at is not None for st in steps)
        state = "done" if done else "skipped" if e["state"] == "backlog" else "todo"
        draft = drafts.get(e["person_id"]) if person is not None and channel == "linkedin_request" else None
        find = None
        if person is None:
            find = parsed.find_someone or FindSomeone(
                search_url=people_search_url(d.company.name, SEARCH_HINT), handoff=HANDOFF)
        rows.append(PlanRow(
            entry_id=plan_entry_id(e), position=position, channel=channel, lane=e["list_group"],
            company=PlanCompany(**d.company.model_dump(), revision=d.revision),
            person=PlanPerson(person=person_ref(person), found_on=parsed.found_on) if person is not None else None,
            role=role, apply_url=(parsed.role_url or role.url or None) if role is not None else None,
            reason=parsed.reason or f"Ranked for {LANE_WORDS[e['list_group']]}.",
            reason_sources=parsed.reason_sources, note=note_draft(draft, cfg) if draft is not None else None,
            find_someone=find, email_touch_id=email_touch, steps=steps, state=state,
            carried_from=parsed.carried_from, revision=e["revision"]))
    return rows


def build_row(conn: sqlite3.Connection, entry_id: int, cfg: PlanConfig) -> PlanRow | None:
    """One row exactly as `GET /api/plan` shows it (its position among the rows of its date), or None when the entry
    is gone or removed. The writes answer with it."""
    row = conn.execute(f"{ENTRY_SQL} WHERE rowid = ?", (entry_id,)).fetchone()
    if row is None:
        return None
    return next((r for r in build_rows(conn, entries_of(conn, row["list_date"]), cfg) if r.entry_id == entry_id),
                None)


# ---------------------------------------------------------------------------------------------- the view

def _not_ready(day: str, last: sqlite3.Row | None) -> NotReady:
    if date.fromisoformat(day).weekday() >= 5:
        return NotReady(reason="weekend", text=WEEKEND_TEXT)
    if last is not None and last["status"] == "failed" and ny_day(last["started_at"]) == day:
        error = (last["error"] or "no error text").strip()
        return NotReady(reason="job_failed", text=f"Today's plan did not build. The plan job said: {error}")
    return NotReady(reason="not_built", text=NOT_BUILT_TEXT)


def _tomorrow(conn: sqlite3.Connection, day: str, canon: dict[int, int], cfg: PlanConfig) -> Tomorrow:
    nxt = next_weekday(day)
    when = date.fromisoformat(nxt)
    total = plan_capacity(conn, canon, when, cfg)
    carried = load_carried(conn, when, cfg, load_outbound(conn, canon, tz=cfg.get("plan", "timezone")))
    preview = _preview(conn, carried, canon)
    label = "Tomorrow" if when == date.fromisoformat(day) + timedelta(days=1) else WEEKDAYS[when.weekday()]
    text = f"{label}: {total} {'person' if total == 1 else 'people'}"
    text += f", {len(preview)} carried over." if preview else "."
    return Tomorrow(total=total, text=text, preview=preview)


def _preview(conn: sqlite3.Connection, carried: dict, canon: dict[int, int]) -> list[TomorrowRow]:
    """The carried rows, newest plan first, then by rank, with the company, person and role names they show."""
    if not carried:
        return []
    dates = sorted({c.carried_from.isoformat() for c in carried.values()})
    entries = {}
    for e in conn.execute(f"{ENTRY_SQL} WHERE list_date IN ({','.join('?' * len(dates))})", dates):
        entries[(e["list_date"], canon.get(e["company_id"], e["company_id"]))] = e
    names = {r["company_id"]: r["name"] for r in by_ids(conn, "SELECT company_id, name FROM company WHERE company_id "
                                                              "IN ({ids})", set(carried))}
    people = people_by_ids(conn, {c.person_id for c in carried.values() if c.person_id is not None})
    found = []
    for company, c in carried.items():
        e = entries.get((c.carried_from.isoformat(), company))
        rank = e["rank"] if e is not None else 10**6
        title = parse_plan_why(e["why"]).role_title if e is not None else None
        if title is None and e is not None and e["posting_id"] is not None:
            got = conn.execute("SELECT title FROM posting WHERE posting_id = ?", (e["posting_id"],)).fetchone()
            title = got["title"] if got else None
        person = people.get(c.person_id) if c.person_id is not None else None
        found.append(((-c.carried_from.toordinal(), rank, company), TomorrowRow(
            company_name=names.get(company, "Unknown company"), person_name=person["name"] if person else None,
            role_title=title)))
    return [row for _, row in sorted(found, key=lambda x: x[0])]


def _week(conn: sqlite3.Connection, now: str) -> PlanWeek:
    counts = {c.stage: c.count for c in report.funnel(live_touches(conn), week_bounds(now))}
    return PlanWeek(requests=counts["requests"], accepts=counts["accepts"], replies=counts["replies"])


def plan_view(conn: sqlite3.Connection, now: str, cfg: PlanConfig) -> PlanView:
    day = ny_day(now)
    canon = canonical_ids(conn)
    built = conn.execute("SELECT * FROM daily_list WHERE list_date = ?", (day,)).fetchone()
    last = conn.execute("SELECT * FROM job_run WHERE job = 'plan' ORDER BY started_at DESC, run_id DESC LIMIT 1"
                        ).fetchone()
    rows = build_rows(conn, entries_of(conn, day), cfg) if built is not None else []
    month = linkedin_capacity(conn, canon, date.fromisoformat(day), cfg)
    return PlanView(
        as_of=now, date=day, built_at=built["built_at"] if built is not None else None,
        list_job=jobs.run_summary(last, now) if last is not None else None,
        not_ready=None if built is not None else _not_ready(day, last),
        rows=rows, done=sum(1 for r in rows if r.state == "done"), total=len(rows),
        linkedin_month=LinkedinMonth(used=month.used, limit=month.budget, hard_used=month.hard_used,
                                     hard_limit=month.hard_limit),
        tomorrow=_tomorrow(conn, day, canon, cfg), week=_week(conn, now))
