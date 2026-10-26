"""The plan's writes, each inside the caller's `run_write` transaction (#11 Task 11).

They record what Devanshu did by hand on a plan row and keep his notes. Nothing here sends, approves, reaches LinkedIn
or confirms a reason line on anyone's behalf: `confirm_note` runs only from his click on the confirm route.

- `log_request`: one `linkedin_log` row (`request_sent`) and one `linkedin_request` touch together (ruling 1), written by
  `outreach.outcomes.requests.record_request` as every request log is, with a context of source `plan` (#15's quota
  of 150 in any 7 days counts it). The touch's own key is the body's `idempotencyKey`, so a press after the first
  commit landed (a new request id) finds the touch and writes nothing.
  On a company row (no person: company first, #9) he types the name of the person he asked: `add_candidate` finds
  them at the company by name or adds them (`added_by 'devanshu'`, role kind `other`, no sources), then the same two
  rows are written. The entry keeps no person; its request step is done by #15's company rule.
- `log_people` (company first, #9 P2): the fallback for a company row when the terminal bridge is not there. He
  pastes `Name | Title | note` lines (up to 10); each is one person found or added at the company by the same
  `add_candidate` door (`added_by 'devanshu'`, the role kind read from the title by `outreach.plan.route.classify_role`)
  and one request through the company row's own path above, with his note on the log row. One `request_logged` event
  per new request says it came from the app and which note checks fired. His note already went out, so it is checked
  as his own (`check_note(author='user')`) and logged as sent; only a note over `note.max_chars` is refused, since
  LinkedIn could not have sent it (a paste mistake). A paused or no-go company or person refuses the whole paste.
- `save_search`: his LinkedIn people search for the row's company, kept as an `event` (`entity 'company'`, `kind
  'linkedin_search_saved'`, `detail {url}`, actor devanshu). Only a LinkedIn people search is kept, and the same
  address twice writes nothing. A `currentCompany=["<id>"]` filter is also stored as the company's LinkedIn id
  (`company_alias` source `linkedin`, key `linkedin:<id>`) unless another company already holds that id: identity
  never moves an alias, so that one is left as it is.
- `log_applied`: an `application` row and an `application` touch with no person (ruling 2), the same way.
- `skip_row`: the entry goes to `backlog` with `decided_at` (the next #15 build carries it) and a `row_skipped`
  event records it.
- `save_note` and `confirm_note`: #15's `save_linkedin_draft(saved_by='devanshu')` and `confirm_reason`, so the checks
  are #15's `check_note` and a later Claude prepare never overwrites his note.

Every write on a row moves the entry's revision on; every answer is the row as `GET /api/plan` shows it. Skip checks
the revision first (it changes the row's state); the two step logs do not (a log only adds a touch, its key stops a
double press, and two logs sent out of order when the tab closes must both land).
"""
import sqlite3
from collections.abc import Sequence
from datetime import date

from outreach.api.errors import ApiProblem
from outreach.api.idempotency import WriteCtx
from outreach.api.models.plan import LoggedPerson, LogPeopleResult, NoteDraft, PlanRow
from outreach.api.models.plan_writes import LogAppliedBody, LogPeopleBody, LogRequestBody
from outreach.api.queries import plan as queries
from outreach.api.queries.queue import LINKEDIN_ALIAS, SAVED_SEARCH, saved_searches
from outreach.api.queries.company import merge_parents, survivor_of
from outreach.api.queries.outcomes import NOT_SAVED
from outreach.api.revisions import bump
from outreach.drafts.followups import company_map, no_go_sets
from outreach.outcomes.requests import plan_context, record_request
from outreach.outcomes.windows import day_bounds_utc, ny_day
from outreach.plan.config import PlanConfig
from outreach.plan.drafts import confirm_reason, save_linkedin_draft
from outreach.plan.notecheck import check_note
from outreach.plan.people import add_candidate, linkedin_company_id, linkedin_search_url
from outreach.plan.route import classify_role
from outreach.ranking.common import canonical_ids
from outreach.store import add_alias, append_event, log_touch, name_key

NO_ROLE = "No role to apply for."
PEOPLE_MAX = 10
NAME_MAX = TITLE_MAX = 200
# The checks that say the checker could not run (a list it could not read): not a fault in his note.
CHECKER_CODES = ("banned_list_unreadable", "unconfirmed_list_unreadable")


def _bad(text: str) -> ApiProblem:
    return ApiProblem("bad_request", f"{text} {NOT_SAVED}")


def _refuse(text: str) -> ApiProblem:
    return ApiProblem("not_allowed", f"{text} {NOT_SAVED}")


def _changed() -> ApiProblem:
    return ApiProblem("conflict", f"The plan row changed since you loaded it. {NOT_SAVED}")


def _entry(conn: sqlite3.Connection, entry_id: int) -> sqlite3.Row:
    row = conn.execute(f"{queries.ENTRY_SQL} WHERE rowid = ?", (entry_id,)).fetchone()
    if row is None:
        raise ApiProblem("not_found", f"There is no plan row {entry_id}.")
    if row["state"] == "removed":
        raise _refuse("That company was taken off the plan.")
    return row


def _answer(conn: sqlite3.Connection, entry_id: int, cfg: PlanConfig) -> PlanRow:
    row = queries.build_row(conn, entry_id, cfg)
    if row is None:  # unreachable: the entry exists and is not removed
        raise ApiProblem("not_found", f"There is no plan row {entry_id}.")
    return row


def _fresh(entry: sqlite3.Row, revision: int) -> None:
    if entry["revision"] != revision:
        raise _changed()


def _bump(ctx: WriteCtx, entry: sqlite3.Row, revision: int, sets: dict | None = None) -> None:
    bump(ctx.conn, "daily_list_entry", "rowid", queries.plan_entry_id(entry), revision, sets or {}, at=ctx.now)


def _bump_logged(ctx: WriteCtx, entry: sqlite3.Row) -> None:
    """A step log moves the revision on from wherever it stands, inside this write's transaction (no check): the
    stored revision, so several logs in one write (a pasted list of people) each move it on by one."""
    current = ctx.conn.execute("SELECT revision FROM daily_list_entry WHERE rowid = ?",
                               (queries.plan_entry_id(entry),)).fetchone()[0]
    _bump(ctx, entry, current)


def _keyed(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM outreach_touch WHERE idempotency_key = ?", (key,)).fetchone()


def _context(conn: sqlite3.Connection, entry: sqlite3.Row, survivor: int, role_kind: str | None,
             posting: sqlite3.Row | None, now: str) -> dict:
    """`plan_context`: the lane is the row's, the role kind the person's (None for an application), the role age
    the posting's, the ranker version and signal values the company's newest score at write time."""
    return plan_context(conn, lane=entry["list_group"], survivor=survivor, role_kind=role_kind, posting=posting,
                        now=now)


def _posting(conn: sqlite3.Connection, posting_id: int | None) -> sqlite3.Row | None:
    if posting_id is None:
        return None
    return conn.execute("SELECT * FROM posting WHERE posting_id = ?", (posting_id,)).fetchone()


def _release_key(ctx: WriteCtx, touch: sqlite3.Row, n: int = 1) -> None:
    """An undone touch gives its key back: it keeps a derived key (`<key>#undone-<touch_id>`), so a new touch can take
    the row's key. Body keys cannot hold `#` (`LogRequestBody`), so the derived key is never anyone else's."""
    moved = f"{touch['idempotency_key']}#undone-{touch['touch_id']}"
    ctx.conn.execute("UPDATE outreach_touch SET idempotency_key = ? WHERE touch_id = ?", (moved, touch["touch_id"]))
    append_event(ctx.conn, entity="outreach_touch", entity_id=touch["touch_id"], company_id=touch["company_id"],
                 kind="key_released", actor="devanshu", at=ctx.now,
                 detail={"from": touch["idempotency_key"], "to": moved}, idempotency_key=ctx.event_key(n))


def _open_to_him(conn: sqlite3.Connection, entry: sqlite3.Row, person: sqlite3.Row | None) -> int:
    """The row's company (its survivor, through merges), after refusing a paused company and an active no-go on the
    company or the person (a no-go on a merged-away duplicate counts for the survivor). Returns the survivor."""
    survivor = survivor_of(merge_parents(conn), entry["company_id"])
    company = conn.execute("SELECT name, paused_at FROM company WHERE company_id = ?", (survivor,)).fetchone()
    if company["paused_at"] is not None:
        raise _refuse(f"{company['name']} is paused. Resume it on its page first.")
    no_go_people, no_go_companies = no_go_sets(conn, company_map(conn))
    if survivor in no_go_companies:
        raise _refuse(f"{company['name']} is on the no-go list, so nothing is logged for it. Lift it first.")
    if person is not None and person["person_id"] in no_go_people:
        raise _refuse(f"{person['name']} is on the no-go list, so nothing goes to them. Lift it first.")
    return survivor


def log_request(ctx: WriteCtx, entry_id: int, body: LogRequestBody, cfg: PlanConfig) -> PlanRow:
    conn = ctx.conn
    entry = _entry(conn, entry_id)
    if entry["person_id"] is None and entry["channel"] != "email":
        return _log_company_request(ctx, entry_id, entry, body, cfg)
    if body.person_name is not None:
        raise _bad("This row already names its person.")
    found = _keyed(conn, body.idempotency_key)
    if found is not None and (found["channel"] != "linkedin_request" or found["person_id"] is None
                              or found["person_id"] != entry["person_id"] or body.person_id != entry["person_id"]):
        raise _bad("That key belongs to another touch, not this row's request.")
    if found is not None and found["undone_at"] is None:  # already logged under this key: nothing written
        return _answer(conn, entry_id, cfg)
    if entry["channel"] == "email":
        raise _refuse("This row is an email. Review it in the Outbox.")
    if entry["person_id"] is None:
        raise _refuse("Nobody is on this row yet. Find someone first.")
    if body.person_id != entry["person_id"]:
        raise _bad("That person is not on this row.")
    person = conn.execute("SELECT * FROM person WHERE person_id = ?", (entry["person_id"],)).fetchone()
    survivor = _open_to_him(conn, entry, person)
    logged = conn.execute(
        "SELECT sent_at FROM outreach_touch WHERE person_id = ? AND channel = 'linkedin_request' "
        "AND undone_at IS NULL AND sent_at >= ? ORDER BY sent_at LIMIT 1",
        (person["person_id"], day_bounds_utc(entry["list_date"])[0])).fetchone()
    if logged is not None:
        raise _refuse(f"Already logged: request sent to {person['name']} on {ny_day(logged['sent_at'])}.")
    _write_request(ctx, entry, survivor, person, body.idempotency_key, found)
    return _answer(conn, entry_id, cfg)


def _write_request(ctx: WriteCtx, entry: sqlite3.Row, survivor: int, person: sqlite3.Row, key: str,
                   found: sqlite3.Row | None, *, note: str = "", release: int = 1) -> tuple[int, bool]:
    conn = ctx.conn
    posting = _posting(conn, entry["posting_id"])
    if posting is not None and posting["closed_at"] is not None:
        posting = None  # the row shows no role once its posting closed, so the touch names none either
    context = _context(conn, entry, survivor, person["role_kind"], posting, ctx.now)
    if found is not None:  # taken back on the LinkedIn page: the old touch stays, undone, under a derived key
        _release_key(ctx, found, release)
    # The log row first, the touch second, in one transaction: if either fails, neither stays.
    written = record_request(conn, person_id=person["person_id"], company_id=survivor, sent_at=ctx.now,
                             recorded_at=ctx.now, context=context,
                             posting_id=posting["posting_id"] if posting is not None else None,
                             idempotency_key=key, note=note)
    _bump_logged(ctx, entry)
    return written


def _found_at_company(ctx: WriteCtx, entry: sqlite3.Row, survivor: int, name: str, title: str, role_kind: str,
                      cfg: PlanConfig) -> tuple[sqlite3.Row, bool]:
    """The person of that name at the company, found or added by him (`add_candidate`), after refusing a no-go on
    them. `(person, added)`."""
    conn = ctx.conn
    added = add_candidate(conn, company_id=survivor, name=name, title=title, role_kind=role_kind, sources=(),
                          added_by="devanshu", today=date.fromisoformat(ny_day(ctx.now)), cfg=cfg)
    if added.refused is not None:
        raise _refuse(f"{name} could not be added at this company ({added.refused}).")
    person = conn.execute("SELECT * FROM person WHERE person_id = ?", (added.person_id,)).fetchone()
    _open_to_him(conn, entry, person)  # a no-go on the person found by that name
    return person, added.created


def _not_asked_yet(conn: sqlite3.Connection, entry: sqlite3.Row, person: sqlite3.Row) -> None:
    logged = conn.execute(
        "SELECT sent_at FROM outreach_touch WHERE person_id = ? AND channel = 'linkedin_request' "
        "AND undone_at IS NULL AND sent_at >= ? ORDER BY sent_at LIMIT 1",
        (person["person_id"], day_bounds_utc(entry["list_date"])[0])).fetchone()
    if logged is not None:
        raise _refuse(f"Already logged: request sent to {person['name']} on {ny_day(logged['sent_at'])}.")


def _log_company_request(ctx: WriteCtx, entry_id: int, entry: sqlite3.Row, body: LogRequestBody,
                         cfg: PlanConfig) -> PlanRow:
    """A company row: the request he sent to the person he names. One request per row key: a second press finds the
    touch and writes nothing."""
    conn = ctx.conn
    survivor = survivor_of(merge_parents(conn), entry["company_id"])
    found = _keyed(conn, body.idempotency_key)
    if found is not None and (found["channel"] != "linkedin_request" or found["person_id"] is None
                              or found["company_id"] != survivor or body.person_id is not None):
        raise _bad("That key belongs to another touch, not this row's request.")
    if found is not None and found["undone_at"] is None:  # already logged under this key: nothing written
        return _answer(conn, entry_id, cfg)
    name = " ".join((body.person_name or "").split())
    if body.person_id is not None or not name:
        raise _refuse("Nobody is on this row. Type the name of the person you asked, then log it.")
    survivor = _open_to_him(conn, entry, None)  # a paused or no-go company first, before anyone is added
    person, _ = _found_at_company(ctx, entry, survivor, name, "", "other", cfg)
    _not_asked_yet(conn, entry, person)
    _write_request(ctx, entry, survivor, person, body.idempotency_key, found)
    return _answer(conn, entry_id, cfg)


# ---------------------------------------------------------------------------------------------- company first (P2)

def parse_people(text: str, company_name: str) -> list[tuple[str, str, str]]:
    """`(name, title, note)` for each non-blank line of `Name | Title | note`. A line of the terminal's paste block,
    `Company | Name | Title | note`, drops its company first. The note keeps his text as he sent it (a `|` inside
    it too); only its ends are trimmed. `ApiProblem` (bad_request) names the line that cannot be read."""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if not lines:
        raise _bad("Paste one line per person: Name | Title | note.")
    if len(lines) > PEOPLE_MAX:
        raise _bad(f"Paste at most {PEOPLE_MAX} people at a time; this has {len(lines)}.")
    company = name_key(company_name)
    out, seen = [], set()
    for n, line in enumerate(lines, 1):
        head, _, rest = line.partition("|")
        if line.count("|") >= 3 and company and name_key(head) == company:
            line = rest
        parts = line.split("|", 2)
        name = " ".join(parts[0].split())
        title = " ".join(parts[1].split()) if len(parts) > 1 else ""
        note = parts[2].strip() if len(parts) > 2 else ""
        if not name:
            raise _bad(f"Line {n} has no name. Start it with the person's name.")
        if len(name) > NAME_MAX or len(title) > TITLE_MAX:
            raise _bad(f"Line {n}: a name or title is over {NAME_MAX} characters.")
        if name_key(name) in seen:
            raise _bad(f"{name} is in the paste twice.")
        seen.add(name_key(name))
        out.append((name, title, note))
    return out


def _note_warnings(note: str, cfg: PlanConfig, banned: Sequence, unconfirmed) -> tuple[list[str], list[str]]:
    """His note's checks as his own text: `(warnings, codes)`. Over the length limit raises: it cannot have gone."""
    if not note:
        return [], []
    checks = [c for c in check_note(note, "", "written", [], cfg=cfg, banned=banned, unconfirmed=unconfirmed,
                                    author="user") if c.code not in CHECKER_CODES]
    if any(c.code == "note_too_long" for c in checks):
        raise _bad(f"A note is {len(note)} characters; LinkedIn takes {cfg.get('note', 'max_chars')}. Paste the note "
                   "exactly as you sent it.")
    return [f"{c.title}: {c.detail}" for c in checks], sorted({c.code for c in checks})


def log_people(ctx: WriteCtx, entry_id: int, body: LogPeopleBody, cfg: PlanConfig, banned: Sequence,
               unconfirmed) -> LogPeopleResult:
    conn = ctx.conn
    entry = _entry(conn, entry_id)
    if entry["channel"] == "email":
        raise _refuse("This row is an email. Review it in the Outbox.")
    if entry["person_id"] is not None:
        raise _bad("This row already names its person.")
    survivor = _open_to_him(conn, entry, None)  # a paused or no-go company refuses before anyone is added
    company = conn.execute("SELECT name FROM company WHERE company_id = ?", (survivor,)).fetchone()["name"]
    lines = parse_people(body.lines, company)
    checked = [_note_warnings(note, cfg, banned, unconfirmed) for _, _, note in lines]
    logged = []
    for n, ((name, title, note), (warnings, codes)) in enumerate(zip(lines, checked), 1):
        key = f"{body.idempotency_key}:{n}"
        found = _keyed(conn, key)
        if found is not None and (found["channel"] != "linkedin_request" or found["company_id"] != survivor):
            raise _bad("That key belongs to another touch, not this row's people.")
        person, added = _found_at_company(ctx, entry, survivor, name, title, classify_role(title, cfg), cfg)
        if found is not None and found["undone_at"] is None:  # this paste was logged before: nothing written
            logged.append(_logged(person, False, note, warnings))
            continue
        _not_asked_yet(conn, entry, person)
        touch_id, created = _write_request(ctx, entry, survivor, person, key, found, note=note, release=n + 1)
        if created:
            append_event(conn, entity="outreach_touch", entity_id=touch_id, company_id=survivor,
                         kind="request_logged", actor="devanshu", at=ctx.now, idempotency_key=f"{key}:logged",
                         detail={"via": "app", "personId": person["person_id"], "noteChars": len(note),
                                 "source": "plan", "checks": codes})
        logged.append(_logged(person, added, note, warnings))
    return LogPeopleResult(row=_answer(conn, entry_id, cfg), people=logged)


def _logged(person: sqlite3.Row, added: bool, note: str, warnings: list[str]) -> LoggedPerson:
    return LoggedPerson(person_id=person["person_id"], name=person["name"], title=person["title"],
                        role_kind=person["role_kind"], added=added, note_chars=len(note), warnings=warnings)


def save_search(ctx: WriteCtx, entry_id: int, url: str, cfg: PlanConfig) -> PlanRow:
    conn = ctx.conn
    entry = _entry(conn, entry_id)
    survivor = survivor_of(merge_parents(conn), entry["company_id"])
    clean = linkedin_search_url(url)
    if clean is None:
        raise _bad("That is not a LinkedIn people search. Copy the address of the search page; it starts with "
                   "https://www.linkedin.com/search/results/people/.")
    if saved_searches(conn, canonical_ids(conn), {survivor}).get(survivor) == clean:
        return _answer(conn, entry_id, cfg)  # the same search again: nothing written
    append_event(conn, entity="company", entity_id=survivor, company_id=survivor, kind=SAVED_SEARCH,
                 actor="devanshu", at=ctx.now, detail={"url": clean}, idempotency_key=ctx.event_key(0))
    company_id = linkedin_company_id(clean)
    if company_id is not None:
        try:
            add_alias(conn, survivor, f"{LINKEDIN_ALIAS}{company_id}", "linkedin")
        except ValueError:
            pass  # another company holds this LinkedIn id: identity never moves an alias
    _bump_logged(ctx, entry)
    return _answer(conn, entry_id, cfg)


def log_applied(ctx: WriteCtx, entry_id: int, body: LogAppliedBody, cfg: PlanConfig) -> PlanRow:
    conn = ctx.conn
    entry = _entry(conn, entry_id)
    found = _keyed(conn, body.idempotency_key)
    if found is not None and (found["channel"] != "application" or found["posting_id"] is None
                              or found["posting_id"] != entry["posting_id"] or body.posting_id != entry["posting_id"]):
        raise _bad("That key belongs to another touch, not this row's application.")
    if found is not None and found["undone_at"] is None:
        return _answer(conn, entry_id, cfg)
    if entry["channel"] == "email":
        raise _refuse("This row is an email. Review it in the Outbox.")
    posting = _posting(conn, entry["posting_id"])
    if posting is None or posting["closed_at"] is not None:
        raise ApiProblem("not_allowed", NO_ROLE)
    if body.posting_id != posting["posting_id"]:
        raise _bad("That role is not this row's.")
    person = conn.execute("SELECT * FROM person WHERE person_id = ?", (entry["person_id"],)).fetchone() \
        if entry["person_id"] is not None else None
    survivor = _open_to_him(conn, entry, person)
    logged = conn.execute("SELECT sent_at FROM outreach_touch WHERE channel = 'application' AND posting_id = ? "
                          "AND undone_at IS NULL ORDER BY sent_at LIMIT 1", (posting["posting_id"],)).fetchone()
    if logged is not None:
        raise _refuse(f"Already logged: applied on {ny_day(logged['sent_at'])}.")
    context = _context(conn, entry, survivor, None, posting, ctx.now)
    if found is not None:
        _release_key(ctx, found)
    # One application row per posting: a touch taken back and logged again keeps the row it already has.
    if conn.execute("SELECT 1 FROM application WHERE posting_id = ? LIMIT 1", (posting["posting_id"],)).fetchone() is None:
        conn.execute("INSERT INTO application(company_id, posting_id, applied_on, url) VALUES (?, ?, ?, ?)",
                     (survivor, posting["posting_id"], ny_day(ctx.now), posting["url"]))
    log_touch(conn, person_id=None, company_id=survivor, channel="application", sent_at=ctx.now, context=context,
              posting_id=posting["posting_id"], idempotency_key=body.idempotency_key)
    _bump_logged(ctx, entry)
    return _answer(conn, entry_id, cfg)


def skip_row(ctx: WriteCtx, entry_id: int, revision: int, cfg: PlanConfig) -> PlanRow:
    conn = ctx.conn
    entry = _entry(conn, entry_id)
    if _answer(conn, entry_id, cfg).state == "done":
        raise _refuse("This row is done, so it stays on today's plan.")
    _fresh(entry, revision)
    if entry["state"] == "backlog":
        raise _refuse("This row is already carried to tomorrow.")
    _bump(ctx, entry, revision, {"state": "backlog", "decided_at": ctx.now})
    append_event(conn, entity="daily_list", entity_id=entry_id, company_id=entry["company_id"], kind="row_skipped",
                 actor="devanshu", at=ctx.now,
                 detail={"list_date": entry["list_date"], "company_id": entry["company_id"]},
                 idempotency_key=ctx.event_key(0))
    return _answer(conn, entry_id, cfg)


def _note_row(conn: sqlite3.Connection, note_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM linkedin_draft WHERE draft_id = ?", (note_id,)).fetchone()
    if row is None:
        raise ApiProblem("not_found", f"There is no note {note_id}.")
    return row


def _note_changed() -> ApiProblem:
    return ApiProblem("conflict", f"The note changed since you loaded it. {NOT_SAVED}")


def save_note(ctx: WriteCtx, note_id: int, text: str, reason_line: str, revision: int, cfg: PlanConfig,
              banned: Sequence) -> NoteDraft:
    """His edit, through #15's `save_linkedin_draft(saved_by='devanshu')` with the stored facts: a changed reason line
    becomes `written`, the checks are redone as his text (no reason line or fact rule from Claude's draft, and the
    facts' `in_note` read from his text), and Claude's prepare can never overwrite it after this."""
    conn = ctx.conn
    stored = _note_row(conn, note_id)
    if not text.strip():
        raise _bad("A note needs some text.")
    try:
        save_linkedin_draft(conn, person_id=stored["person_id"], body=text, reason_line=reason_line,
                            facts=queries.stored_facts(stored), saved_by="devanshu", revision=revision, cfg=cfg,
                            banned=banned)
    except LookupError:
        raise _note_changed() from None
    return queries.note_by_id(conn, note_id, cfg)


def confirm_note(ctx: WriteCtx, note_id: int, revision: int, cfg: PlanConfig, banned: Sequence) -> NoteDraft:
    """His key on the reason line: #15's `confirm_reason` (`confirmed`, `saved_by 'devanshu'`)."""
    conn = ctx.conn
    _note_row(conn, note_id)
    try:
        confirm_reason(conn, note_id, revision, cfg=cfg, banned=banned)
    except LookupError:
        raise _note_changed() from None
    except ValueError:
        raise _bad("There is no reason line to confirm. Write one first.") from None
    return queries.note_by_id(conn, note_id, cfg)
