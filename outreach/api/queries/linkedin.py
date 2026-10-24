"""The LinkedIn list and the steps he logs on it. Nothing here reaches LinkedIn: it records what he did by hand.

A step is one `linkedin_log` row and, for the steps that make an outcome record, the `outreach_touch` row, written
together in the caller's transaction (`run_write`):

- `request_sent` makes a `linkedin_request` touch and `message_sent` a `linkedin_message` touch, both with a context
  of source `own` (a request logged from a plan row goes through Task 11 and has source `plan`);
- `accepted` sets `accepted_at` on the request touch and `replied` the reply on the message touch (the request touch
  when no message went out), through Task 12's `answer`, so a stop reply adds the person's `no_go` and stops their
  unsent email there too;
- a step he takes back is a `linkedin_log` `undo` row. A request or message touch is never deleted: it gets
  `undone_at` and counts nowhere. An accept or a reply is cleared through Task 12's `correct`.

Every step follows the one before: `accepted` needs a request, `message_sent` needs the accept, and `replied` needs
a message or the accept. Only the newest step can be taken back. A live request touch with no log row (the plan
logged it, or an import) counts as the request step, so that person is waiting and never stuck in to_reach.

A person on the no-go list, at a company on it (a no-go on a duplicate merged away counts for the survivor), or at a
removed company is never listed, and nobody at a closed company is offered as new. Only a no-go refuses a new
request or message: logging a request he already sent by hand to someone at a removed or closed company is allowed,
because it is history, not an offer.
"""
import sqlite3
from collections import defaultdict

from outreach.api.config import ApiConfig
from outreach.api.errors import ApiProblem
from outreach.api.idempotency import WriteCtx
from outreach.api.models.company import RoleToName
from outreach.api.models.linkedin import (
    HeldBy, LinkedInCounts, LinkedInGroup, LinkedInPerson, LinkedInView, LoggedStep, LogStepBody, UndoStepBody,
)
from outreach.api.models.outcomes import CorrectionBody, OutcomeBody
from outreach.api.queries import outcomes
from outreach.api.queries.company import (
    group_of, company_holds, merge_parents, person_ref, rank_postings, role_to_name, survivor_of,
)
from outreach.api.queries.outcomes import NOT_SAVED, by_ids, company_refs
from outreach.api.queries.people import day, live_steps
from outreach.drafts.followups import company_map, no_go_sets
from outreach.outcomes.windows import day_bounds_utc, week_bounds
from outreach.store import log_touch, undo_touch

TOUCH_OF_STEP = {"request_sent": "linkedin_request", "message_sent": "linkedin_message"}
STEP_WORDS = {"request_sent": "request sent", "accepted": "accepted", "message_sent": "message sent",
              "replied": "reply"}
OUTCOMES = (("accepted_at", "an accept"), ("replied_at", "a reply"), ("referred_at", "a referral"),
            ("interview_at", "an interview"), ("offer_at", "an offer"))


# ---------------------------------------------------------------------------------------------- reads

def _refuse(message: str) -> ApiProblem:
    return ApiProblem("not_allowed", f"{message} {NOT_SAVED}")


def _logs(conn: sqlite3.Connection, person_ids: set[int] | None = None) -> dict[int, list[sqlite3.Row]]:
    """Each LinkedIn person's `linkedin_log` rows, oldest first (every LinkedIn person when `person_ids` is None)."""
    sql = "SELECT l.* FROM linkedin_log l {where} ORDER BY l.at, l.entry_id"
    found: dict[int, list[sqlite3.Row]] = defaultdict(list)
    if person_ids is None:
        rows = conn.execute(sql.format(where="JOIN person p ON p.person_id = l.person_id WHERE p.channel = 'linkedin'"))
    else:
        rows = by_ids(conn, sql.format(where="WHERE l.person_id IN ({ids})"), person_ids)
        rows.sort(key=lambda r: (r["at"], r["entry_id"]))
    for r in rows:
        found[r["person_id"]].append(r)
    return found


def steps_of(entries: list[sqlite3.Row], request_at: str | None) -> list:
    """The person's live steps. A live request touch with no live `request_sent` row counts as that step, first."""
    steps = live_steps(entries)
    if request_at is not None and not any(e["kind"] == "request_sent" for e in steps):
        steps = [{"kind": "request_sent", "at": request_at, "entry_id": None, "reply_kind": None}, *steps]
    return steps


def _request_times(conn: sqlite3.Connection, person_ids: set[int] | None = None) -> dict[int, str]:
    """When each person's live request touch was sent (the newest, when there are two)."""
    sql = ("SELECT person_id, sent_at FROM outreach_touch WHERE channel = 'linkedin_request' "
           "AND undone_at IS NULL AND person_id IS NOT NULL{where} ORDER BY sent_at, touch_id")
    if person_ids is None:
        rows = conn.execute(sql.format(where=""))
    else:
        rows = by_ids(conn, sql.format(where=" AND person_id IN ({ids})"), person_ids)
    return {r["person_id"]: r["sent_at"] for r in rows}  # the newest wins: the rows are oldest first per call


def _person(row: sqlite3.Row, entries: list[sqlite3.Row], request_at: str | None) -> LinkedInPerson:
    steps = steps_of(entries, request_at)
    replies = [e for e in steps if e["kind"] == "replied"]
    return LinkedInPerson(
        person=person_ref(row), reason=row["linkedin_reason"] or "no_email",
        steps=[LoggedStep(step=e["kind"], at=e["at"]) for e in steps],
        reply_kind=(replies[-1]["reply_kind"] or "unsorted") if replies else None,
        tab="replied" if replies else "waiting" if steps else "to_reach",
    )


def person_view(conn: sqlite3.Connection, person_id: int) -> LinkedInPerson | None:
    """One person as the list shows them (even one who is hidden from it), or None when there is no such person."""
    row = conn.execute("SELECT * FROM person WHERE person_id = ?", (person_id,)).fetchone()
    if row is None:
        return None
    return _person(row, _logs(conn, {person_id}).get(person_id, []), _request_times(conn, {person_id}).get(person_id))


def roles(conn: sqlite3.Connection, parents: dict[int, int], survivors: set[int]) -> dict[int, RoleToName]:
    """The role to name for each company: the open posting the company page names (Task 7's ranking)."""
    groups = {s: group_of(parents, s) for s in survivors}
    members = {m for ids in groups.values() for m in ids}
    if not members:
        return {}
    postings = by_ids(conn, "SELECT * FROM posting WHERE company_id IN ({ids})", members)
    decisions = {r["posting_id"]: r for r in by_ids(
        conn, "SELECT d.* FROM posting_decision d JOIN posting p ON p.posting_id = d.posting_id "
              "WHERE p.company_id IN ({ids})", members)}
    out = {}
    for survivor, ids in groups.items():
        rows = [p for p in postings if p["company_id"] in ids]
        named = role_to_name(rank_postings(rows, decisions, set(), conn)[1])
        if named is not None:
            out[survivor] = named
    return out


def requests_this_week(conn: sqlite3.Connection, now: str) -> int:
    """Requests sent in the New York week of `now`, not counting one he took back (the count progress shows)."""
    monday, sunday = week_bounds(now)
    return conn.execute(
        "SELECT count(*) FROM outreach_touch WHERE channel = 'linkedin_request' AND undone_at IS NULL "
        "AND sent_at >= ? AND sent_at < ?", (day_bounds_utc(monday)[0], day_bounds_utc(sunday)[1])).fetchone()[0]


def linkedin_view(conn: sqlite3.Connection, now: str) -> LinkedInView:
    """Every LinkedIn person worth showing, by company, with the tab each is on."""
    rows = conn.execute("SELECT * FROM person WHERE channel = 'linkedin' ORDER BY person_id").fetchall()
    if not rows:
        return LinkedInView(as_of=now, groups=[], counts=LinkedInCounts(to_reach=0, waiting=0, replied=0, all=0),
                            requests_this_week=requests_this_week(conn, now))
    parents = merge_parents(conn)
    companies = company_map(conn)
    no_go_people, no_go_companies = no_go_sets(conn, companies)
    holds = company_holds(conn, companies)  # a hold left on a merged-away duplicate holds the survivor
    refs = company_refs(conn, {survivor_of(parents, r["company_id"]) for r in rows}, holds=holds)
    logs, requested = _logs(conn), _request_times(conn)
    listed: dict[int, list[LinkedInPerson]] = defaultdict(list)
    for row in rows:
        survivor = survivor_of(parents, row["company_id"])
        company = refs.get(survivor)
        if company is None or company.status == "removed" or row["person_id"] in no_go_people \
                or survivor in no_go_companies:
            continue
        person = _person(row, logs.get(row["person_id"], []), requested.get(row["person_id"]))
        if person.tab == "to_reach" and company.status == "closed":
            continue  # nobody new goes to a company he closed
        listed[survivor].append(person)
    named = roles(conn, parents, set(listed))
    emailed = _emailed(conn, parents)
    holders = {r["touch_id"]: r["name"] for r in by_ids(
        conn, "SELECT t.touch_id, p.name FROM touch t JOIN person p ON p.person_id = t.person_id "
              "WHERE t.touch_id IN ({ids})",
        {refs[s].held_by_touch_id for s in listed if refs[s].held_by_touch_id is not None})}
    groups = [
        LinkedInGroup(
            company=refs[s], role=named.get(s), emailed=emailed.get(s, 0), on_linked_in=len(people),
            held_by=HeldBy(touch_id=refs[s].held_by_touch_id, person_name=holders[refs[s].held_by_touch_id])
            if refs[s].held_by_touch_id in holders else None,
            people=people,
        )
        for s, people in sorted(listed.items(), key=lambda kv: (refs[kv[0]].name.lower(), kv[0]))
    ]
    everyone = [p for g in groups for p in g.people]
    tab = [p.tab for p in everyone]
    return LinkedInView(
        as_of=now, groups=groups, requests_this_week=requests_this_week(conn, now),
        counts=LinkedInCounts(to_reach=tab.count("to_reach"), waiting=tab.count("waiting"),
                              replied=tab.count("replied"), all=len(everyone)),
    )


def to_reach_count(conn: sqlite3.Connection) -> int:
    """How many people the list shows on the `to_reach` tab: `linkedin_view`'s own filters, counted with no groups,
    roles or company refs built, and without loading every company (the shell polls this): only the companies
    these people belong to are read."""
    rows = conn.execute("SELECT * FROM person WHERE channel = 'linkedin' ORDER BY person_id").fetchall()
    if not rows:
        return 0
    parents = merge_parents(conn)
    no_go_people, no_go_companies = set(), set()
    for r in conn.execute("SELECT person_id, company_id FROM no_go WHERE lifted_at IS NULL"):
        if r["person_id"] is not None:
            no_go_people.add(r["person_id"])
        else:
            no_go_companies.add(survivor_of(parents, r["company_id"]))
    survivors = {r["person_id"]: survivor_of(parents, r["company_id"]) for r in rows}
    status = {r["company_id"]: r["status"] for r in by_ids(
        conn, "SELECT company_id, status FROM company WHERE company_id IN ({ids})", set(survivors.values()))}
    logs, requested = _logs(conn), _request_times(conn)
    count = 0
    for row in rows:
        survivor = survivors[row["person_id"]]
        if status.get(survivor) in (None, "removed", "closed") or row["person_id"] in no_go_people \
                or survivor in no_go_companies:
            continue  # a closed company's people are listed only once they have a step, never as to reach
        if _person(row, logs.get(row["person_id"], []), requested.get(row["person_id"])).tab == "to_reach":
            count += 1
    return count


def _emailed(conn: sqlite3.Connection, parents: dict[int, int]) -> dict[int, int]:
    """Per company (its survivor): people whose first email went out."""
    counts: dict[int, int] = defaultdict(int)
    for r in conn.execute("SELECT p.company_id, count(*) AS n FROM touch t JOIN person p ON p.person_id = t.person_id "
                          "WHERE t.first_sent_at IS NOT NULL GROUP BY p.company_id"):
        counts[survivor_of(parents, r["company_id"])] += r["n"]
    return counts


# ---------------------------------------------------------------------------------------------- writes

def _row(conn: sqlite3.Connection, person_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM person WHERE person_id = ?", (person_id,)).fetchone()
    if row is None:
        raise ApiProblem("not_found", f"There is no person {person_id}.")
    return row


def _live_touch(conn: sqlite3.Connection, person_id: int, channel: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM outreach_touch WHERE person_id = ? AND channel = ? AND undone_at IS NULL "
        "ORDER BY touch_id DESC LIMIT 1", (person_id, channel)).fetchone()


def _append(ctx: WriteCtx, person_id: int, kind: str, *, reply_kind: str | None = None, undoes: int | None = None) -> None:
    ctx.conn.execute(
        "INSERT INTO linkedin_log(person_id, kind, reply_kind, undoes, at, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
        (person_id, kind, reply_kind, undoes, ctx.now, ctx.now))


def _needs(step: str, have: set[str]) -> str | None:
    """What he must log first, in plain words, or None when `step` may go next."""
    if "request_sent" not in have and step != "request_sent":
        return "Log the request first."
    if step == "message_sent" and "accepted" not in have:
        return "Log that they accepted first."
    if step == "replied" and not have & {"message_sent", "accepted"}:
        return "Log that they accepted, or your message, first."
    return None


def log_step(ctx: WriteCtx, person_id: int, body: LogStepBody, cfg: ApiConfig) -> LinkedInPerson:
    """Log one step he took by hand, in the write's transaction. Returns the person as the list shows them."""
    conn = ctx.conn
    row = _row(conn, person_id)
    step = body.step
    if (step == "replied") != (body.reply_kind is not None):
        raise ApiProblem("bad_request", "A reply needs its kind (interested, not now, said no or asked to stop), and "
                                        f"no other step takes one. {NOT_SAVED}")
    request = _live_touch(conn, person_id, "linkedin_request")
    steps = steps_of(_logs(conn, {person_id}).get(person_id, []), request["sent_at"] if request else None)
    have = {e["kind"]: e for e in steps}
    if step in have:
        raise _refuse(f"Already logged: {STEP_WORDS[step]} for {row['name']} on {day(have[step]['at'])}.")
    if (missing := _needs(step, set(have))) is not None:
        raise _refuse(missing)
    parents = merge_parents(conn)
    survivor = survivor_of(parents, row["company_id"])
    if step in TOUCH_OF_STEP:
        return _log_outbound(ctx, row, step, parents, survivor)
    if request is None:
        raise _refuse(f"There is no request touch for {row['name']} to record that on: their request was logged "
                      "without one.")
    target = request
    if step == "replied":
        target = _live_touch(conn, person_id, "linkedin_message") or request
    outcomes.answer(ctx, target["touch_id"],
                    OutcomeBody(kind=step, reply_kind=body.reply_kind,
                                revision=target["revision"]), cfg)
    return person_view(conn, person_id)


def _log_outbound(ctx: WriteCtx, row: sqlite3.Row, step: str, parents: dict[int, int], survivor: int) -> LinkedInPerson:
    """A request or a message: the log row and its own touch, with a context of source `own`."""
    conn = ctx.conn
    no_go_people, no_go_companies = no_go_sets(conn, company_map(conn))
    if row["person_id"] in no_go_people or survivor in no_go_companies:
        raise _refuse(f"{row['name']} is on the no-go list, so nothing goes to them. Lift it first.")
    role = roles(conn, parents, {survivor}).get(survivor)  # the role the list shows beside them
    context = outcomes.own_context(conn, survivor, row["role_kind"], role.first_seen_at if role else None, ctx.now)
    log_touch(conn, person_id=row["person_id"], company_id=survivor, channel=TOUCH_OF_STEP[step], sent_at=ctx.now,
              context=context, posting_id=role.posting_id if role else None,
              idempotency_key=f"linkedin:{ctx.request_id}")
    _append(ctx, row["person_id"], step)
    return person_view(conn, row["person_id"])


def _clear(ctx: WriteCtx, touch: sqlite3.Row, kind: str) -> None:
    """Take back one outcome of a touch (a correction to no date)."""
    fresh = ctx.conn.execute("SELECT revision FROM outreach_touch WHERE touch_id = ?", (touch["touch_id"],)).fetchone()
    outcomes.correct(ctx, touch["touch_id"], kind, CorrectionBody(at=None, revision=fresh["revision"]))


def _undo_touch(ctx: WriteCtx, touch: sqlite3.Row) -> None:
    """Undo a request or message touch: kept, never deleted, counted nowhere. A quiet mark the sweeper made goes
    first; any real outcome means the touch happened, so it stays."""
    conn = ctx.conn
    for column, words in OUTCOMES:
        if touch[column] is not None:
            raise _refuse(f"This touch already has {words}, so it cannot be taken back. Correct it from the touch's "
                          "history first.")
    if touch["quiet_at"] is not None:
        _clear(ctx, touch, "quiet")
    revision = conn.execute("SELECT revision FROM outreach_touch WHERE touch_id = ?", (touch["touch_id"],)).fetchone()[0]
    try:
        undo_touch(conn, touch["touch_id"], at=ctx.now, revision=revision)
    except LookupError:
        raise outcomes.touch_changed() from None
    except ValueError as err:
        raise outcomes.plain_refusal(err) from None


def undo_step(ctx: WriteCtx, person_id: int, body: UndoStepBody) -> LinkedInPerson:
    """Take back the newest step: an `undo` row in the log and, for the touch, the matching change above. A stop
    reply that was already acted on keeps its no-go (the safe side; lift it from the No-go page if it was a slip)."""
    conn = ctx.conn
    row = _row(conn, person_id)
    request = _live_touch(conn, person_id, "linkedin_request")
    steps = steps_of(_logs(conn, {person_id}).get(person_id, []), request["sent_at"] if request else None)
    newest = steps[-1] if steps else None
    if newest is None or newest["kind"] != body.step:
        if not any(e["kind"] == body.step for e in steps):
            raise _refuse(f"{STEP_WORDS[body.step].capitalize()} is not logged for {row['name']}, so there is "
                          "nothing to take back.")
        raise _refuse(f"Only the newest step can be taken back, and that is {STEP_WORDS[newest['kind']]} for "
                      f"{row['name']}.")
    message = _live_touch(conn, person_id, "linkedin_message")
    if body.step == "request_sent" and request is not None:
        # The request is the root of the thread: while a message of theirs has an outcome, the request stays.
        for column, words in OUTCOMES:
            if message is not None and message[column] is not None:
                raise _refuse(f"Their message already has {words}, so the request cannot be taken back. Correct "
                              "that from the touch's history first.")
        _undo_touch(ctx, request)
    elif body.step == "message_sent" and message is not None:
        _undo_touch(ctx, message)
    elif body.step in ("accepted", "replied"):
        column = "accepted_at" if body.step == "accepted" else "replied_at"
        holder = next((t for t in (message, request) if t is not None and t[column] is not None), None)
        if holder is not None:
            _clear(ctx, holder, body.step)
    if newest["entry_id"] is not None:  # a request that is only a touch has no log row to point an undo at
        _append(ctx, person_id, "undo", undoes=newest["entry_id"])
    return person_view(conn, person_id)
