"""The plan's writes, each inside the caller's `run_write` transaction (#11 Task 11).

They record what Devanshu did by hand on a plan row and keep his notes. Nothing here sends, approves, reaches LinkedIn
or confirms a reason line on anyone's behalf: `confirm_note` runs only from his click on the confirm route.

- `log_request`: one `linkedin_log` row (`request_sent`) and one `linkedin_request` touch together (ruling 1), with a
  context of source `plan` (#15's quota of 150 in any 7 days counts it). The touch's own key is the body's
  `idempotencyKey`, so a press after the first commit landed (a new request id) finds the touch and writes nothing.
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
from outreach.api.models.plan import NoteDraft, PlanRow
from outreach.api.models.plan_writes import LogAppliedBody, LogRequestBody
from outreach.api.queries import plan as queries
from outreach.api.queries.company import merge_parents, survivor_of
from outreach.api.queries.outcomes import NOT_SAVED, newest_score_parts
from outreach.api.revisions import bump
from outreach.drafts.followups import company_map, no_go_sets
from outreach.outcomes.context import build_context
from outreach.outcomes.windows import day_bounds_utc, ny_day
from outreach.plan.config import PlanConfig
from outreach.plan.drafts import confirm_reason, save_linkedin_draft
from outreach.store import append_event, log_touch

NO_ROLE = "No role to apply for."


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
    """A step log moves the revision on from wherever it stands, inside this write's transaction (no check)."""
    _bump(ctx, entry, entry["revision"])


def _keyed(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM outreach_touch WHERE idempotency_key = ?", (key,)).fetchone()


def _role_age(posting: sqlite3.Row | None, now: str) -> int | None:
    """Days since the role was posted (else first seen), New York dates: as #15's `why.role.age_days`."""
    if posting is None:
        return None
    since = date.fromisoformat(posting["posted_on"]) if posting["posted_on"] else \
        date.fromisoformat(ny_day(posting["first_seen_at"]))
    return max(0, (date.fromisoformat(ny_day(now)) - since).days)


def _context(conn: sqlite3.Connection, entry: sqlite3.Row, survivor: int, role_kind: str | None,
             posting: sqlite3.Row | None, now: str) -> dict:
    """`build_context(source='plan')`: the lane is the row's, the role kind the person's (None for an application),
    the role age the posting's, the ranker version and signal values the company's newest score at write time."""
    _, version, signals = newest_score_parts(conn, survivor)
    return build_context(source="plan", lane=entry["list_group"], role_kind=role_kind,
                         role_age_days=_role_age(posting, now), ranker_version=version, signals=signals,
                         role_named=posting is not None)


def _posting(conn: sqlite3.Connection, posting_id: int | None) -> sqlite3.Row | None:
    if posting_id is None:
        return None
    return conn.execute("SELECT * FROM posting WHERE posting_id = ?", (posting_id,)).fetchone()


def _release_key(ctx: WriteCtx, touch: sqlite3.Row) -> None:
    """An undone touch gives its key back: it keeps a derived key (`<key>#undone-<touch_id>`), so a new touch can take
    the row's key. Body keys cannot hold `#` (`LogRequestBody`), so the derived key is never anyone else's."""
    moved = f"{touch['idempotency_key']}#undone-{touch['touch_id']}"
    ctx.conn.execute("UPDATE outreach_touch SET idempotency_key = ? WHERE touch_id = ?", (moved, touch["touch_id"]))
    append_event(ctx.conn, entity="outreach_touch", entity_id=touch["touch_id"], company_id=touch["company_id"],
                 kind="key_released", actor="devanshu", at=ctx.now,
                 detail={"from": touch["idempotency_key"], "to": moved}, idempotency_key=ctx.event_key(1))


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
    posting = _posting(conn, entry["posting_id"])
    if posting is not None and posting["closed_at"] is not None:
        posting = None  # the row shows no role once its posting closed, so the touch names none either
    context = _context(conn, entry, survivor, person["role_kind"], posting, ctx.now)
    if found is not None:  # taken back on the LinkedIn page: the old touch stays, undone, under a derived key
        _release_key(ctx, found)
    # The log row first, the touch second, in one transaction: if either fails, neither stays.
    conn.execute("INSERT INTO linkedin_log(person_id, kind, at, recorded_at) VALUES (?, 'request_sent', ?, ?)",
                 (person["person_id"], ctx.now, ctx.now))
    log_touch(conn, person_id=person["person_id"], company_id=survivor, channel="linkedin_request", sent_at=ctx.now,
              context=context, posting_id=posting["posting_id"] if posting is not None else None,
              idempotency_key=body.idempotency_key)
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
    becomes `written`, the checks are redone, and Claude's prepare can never overwrite it after this."""
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
