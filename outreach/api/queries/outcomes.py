"""Outcomes: the prompt, answers, corrections, a touch's history and a touch he logs himself.

The rules are Task 9's (`outreach.outcomes`): `due_for_prompt` and `should_mark_quiet` decide when to ask and when a
touch is quiet, `correct_outcome` changes or clears one outcome, `build_context` makes a touch's context. The store's
`record_outcome` and `log_touch` write the touch. This module reads rows, refuses in plain words and puts the writes
of one answer into the caller's transaction (`run_write`): the outcome, the `linkedin_log` row of a LinkedIn touch,
and for a reply that says no or asks to stop, on any channel, the person's `no_go` row and the stop of every unsent
message on their email touch.
"""
import itertools
import json
import math
import sqlite3
from collections.abc import Iterator, Mapping
from datetime import date, datetime, timedelta, timezone

from outreach.api.errors import ApiProblem
from outreach.api.idempotency import WriteCtx
from outreach.api.models.company import CompanyRef
from outreach.api.models.outcomes import (
    CorrectionBody, LogTouchBody, OutcomeBody, OutcomePromptView, PromptTouch, TouchHistory, TouchLogged,
)
from outreach.api.queries.company import Holds, company_holds, merge_parents, person_ref, survivor_of, to_event
from outreach.outcomes.context import build_context
from outreach.outcomes.correct import OUTCOME_COLUMNS, correct_outcome
from outreach.outcomes.prompt import PromptConfig, due_for_prompt, should_mark_quiet
from outreach.outcomes.windows import ny_day
from outreach.sending.followups import stop_planned
from outreach.store import append_event, iso_utc, log_touch, record_outcome
from outreach.store.timefmt import ISO_FMT

NOT_SAVED = "Nothing was saved."
LINKEDIN = ("linkedin_request", "linkedin_message")
OWN_CHANNELS = ("hn_reply", "other")
LOG_KINDS = {"accepted": "accepted", "replied": "replied"}  # the outcomes a linkedin_log row records
STOP_REASONS = {"said_no": "said_no", "asked_to_stop": "stop_reply"}  # reply kind to the person's no_go reason
SNOOZE = "prompt_snoozed"
CHUNK = 500

CHANNEL_WORDS = {
    "linkedin_request": "LinkedIn request", "linkedin_message": "LinkedIn message", "email": "Email",
    "application": "Application", "hn_reply": "HN reply", "other": "Touch",
}
LANE_WORDS = {
    "level_role": "for a role at your level", "intern_junior": "for an intern or junior role",
    "reason_no_role": "with a reason to reach out and no open role", "any_us_role": "for a US role",
    "own": "on your own pick",
}
OUTCOME_WORDS = {
    "accepted": "an accept", "replied": "a reply", "referred": "a referral", "interview": "an interview",
    "offer": "an offer", "quiet": "a quiet mark",
}

# Touches with no outcome yet, oldest first: the ones the prompt may ask about and the sweeper may mark quiet.
OPEN_TOUCHES_SQL = """
SELECT * FROM outreach_touch
WHERE undone_at IS NULL AND accepted_at IS NULL AND replied_at IS NULL AND referred_at IS NULL
  AND interview_at IS NULL AND offer_at IS NULL AND quiet_at IS NULL AND sent_at <= ?
ORDER BY sent_at, touch_id
"""


# ---------------------------------------------------------------------------------------------- shared reads

def _shift(now: str, days: int) -> str:
    moment = datetime.strptime(now, ISO_FMT).replace(tzinfo=timezone.utc) - timedelta(days=days)
    return moment.strftime(ISO_FMT)


def ny_days_between(earlier: str, later: str) -> int:
    return (date.fromisoformat(ny_day(later)) - date.fromisoformat(ny_day(earlier))).days


def open_touches(conn: sqlite3.Connection, now: str, at_least_days: int) -> list[sqlite3.Row]:
    """Open touches sent at least about `at_least_days` before `now` (a day of slack: the rules count New York days,
    so the caller still applies them)."""
    return conn.execute(OPEN_TOUCHES_SQL, (_shift(now, max(at_least_days - 1, 0)),)).fetchall()


def by_ids(conn: sqlite3.Connection, sql: str, ids: set[int]) -> list[sqlite3.Row]:
    """`sql` has one `{ids}` placeholder; the ids go in chunks so a long list never passes SQLite's limit."""
    listed, rows = sorted(ids), []
    for start in range(0, len(listed), CHUNK):
        part = listed[start:start + CHUNK]
        rows.extend(conn.execute(sql.format(ids=",".join("?" * len(part))), part))
    return rows


def company_refs(conn: sqlite3.Connection, ids: set[int], *, holds: Holds | None = None
                 ) -> dict[int, CompanyRef]:
    """Each company's ref. Its hold is read across its merged family (`company_holds`, read here when `holds` is
    None), so a hold left on a duplicate that was merged away shows on the survivor, and a reply on any channel
    shows as a hold."""
    if holds is None:
        holds = company_holds(conn)
    return {r["company_id"]: CompanyRef(id=r["company_id"], name=r["name"], status=r["status"],
                                        paused_at=r["paused_at"], **holds.fields(r["company_id"]))
            for r in by_ids(conn, "SELECT company_id, name, status, paused_at, held_by_touch FROM company "
                                   "WHERE company_id IN ({ids})", ids)}


def people_by_ids(conn: sqlite3.Connection, ids: set[int]) -> dict[int, sqlite3.Row]:
    return {r["person_id"]: r for r in by_ids(conn, "SELECT * FROM person WHERE person_id IN ({ids})", ids)}


def snoozes_of(conn: sqlite3.Connection, touch_ids: set[int]) -> dict[int, list[str]]:
    """Each touch's "Nothing yet" answers, oldest first."""
    found: dict[int, list[str]] = {}
    for r in by_ids(conn, "SELECT entity_id, at FROM event WHERE entity = 'outreach_touch' AND kind = '"
                           + SNOOZE + "' AND entity_id IN ({ids}) ORDER BY at, event_id", touch_ids):
        found.setdefault(r["entity_id"], []).append(r["at"])
    return found


def context_line(channel: str, raw: str) -> str:
    """One plain line from a touch's context: what it was, where it came from, its lane, whether a role was named."""
    try:
        ctx = json.loads(raw)
    except (TypeError, ValueError):
        ctx = {}
    if not isinstance(ctx, dict):
        ctx = {}
    origin = "from the plan" if ctx.get("source") == "plan" else "you logged yourself"
    lane = ctx.get("lane")
    parts = [f"{CHANNEL_WORDS.get(channel, 'Touch')} {origin}"]
    if isinstance(lane, str) and lane:
        parts.append(LANE_WORDS.get(lane) or f"lane {lane.replace('_', ' ')}")
    if ctx.get("roleNamed") is True:
        parts.append("naming the role")
    return ", ".join(parts) + "."


# ---------------------------------------------------------------------------------------------- the prompt

def prompt_view(conn: sqlite3.Connection, now: str, cfg: PromptConfig) -> OutcomePromptView:
    """Every touch to ask "what came of this?" about now, oldest first."""
    rows = open_touches(conn, now, cfg.prompt_after_days)
    snoozes = snoozes_of(conn, {r["touch_id"] for r in rows})
    due = [r for r in rows if due_for_prompt(r, snoozes.get(r["touch_id"], []), now, cfg)]
    if not due:
        return OutcomePromptView(as_of=now, touches=[])
    parents = merge_parents(conn)
    companies = company_refs(conn, {survivor_of(parents, r["company_id"]) for r in due})
    people = people_by_ids(conn, {r["person_id"] for r in due if r["person_id"] is not None})
    touches = []
    for r in due:
        company = companies.get(survivor_of(parents, r["company_id"]))
        if company is None:
            continue
        person = people.get(r["person_id"]) if r["person_id"] is not None else None
        touches.append(PromptTouch(
            touch_id=r["touch_id"], person=person_ref(person) if person is not None else None, company=company,
            channel=r["channel"], sent_at=r["sent_at"], age_days=ny_days_between(r["sent_at"], now),
            context=context_line(r["channel"], r["context"]), revision=r["revision"],
        ))
    return OutcomePromptView(as_of=now, touches=touches)


# ---------------------------------------------------------------------------------------------- refusals

def store_refusal(err: sqlite3.IntegrityError) -> ApiProblem:
    """A constraint the store enforced, as a plain `bad_request` (never a 500, never the SQL text)."""
    text = str(err)
    if "FOREIGN KEY" in text:
        message = "That names a company, person or posting the store does not have."
    elif "UNIQUE" in text:
        message = "That is already recorded."
    else:
        message = ("The store refused that change: an outcome cannot be before the touch was sent, a touch that "
                   "replied cannot be quiet, and an undone touch takes no outcome.")
    return ApiProblem("bad_request", f"{message} {NOT_SAVED}")


def plain_refusal(err: ValueError) -> ApiProblem:
    text = str(err).strip().rstrip(".")
    return ApiProblem("bad_request", f"{text[:1].upper()}{text[1:]}. {NOT_SAVED}")


def _missing(touch_id: int) -> ApiProblem:
    return ApiProblem("not_found", f"There is no touch {touch_id}.")


def touch_changed() -> ApiProblem:
    return ApiProblem("conflict", f"The touch changed since you loaded it. {NOT_SAVED}")


def _touch(conn: sqlite3.Connection, touch_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM outreach_touch WHERE touch_id = ?", (touch_id,)).fetchone()
    if row is None:
        raise _missing(touch_id)
    if row["undone_at"] is not None:
        raise ApiProblem("not_allowed", f"This touch was undone, so it takes no outcome. {NOT_SAVED}")
    return row


def _has_outcome(row: Mapping) -> bool:
    return any(row[c] is not None for c in OUTCOME_COLUMNS.values())


# ---------------------------------------------------------------------------------------------- reply effects

def add_person_no_go(conn: sqlite3.Connection, touch: Mapping, reply_kind: str, *, now: str,
                     keys: Iterator[str]) -> int | None:
    """The person's `no_go` row for a reply that says no or asks to stop (source `reply`). A person who already has
    an active no-go keeps it (no second row, no event). Returns the new row's id or None."""
    reason = STOP_REASONS[reply_kind]
    note = "Said no in a reply." if reply_kind == "said_no" else "Asked to stop in a reply."
    cur = conn.execute(
        "INSERT INTO no_go(person_id, reason, source, note, created_at) VALUES (?, ?, 'reply', ?, ?) "
        "ON CONFLICT(person_id) WHERE person_id IS NOT NULL AND lifted_at IS NULL DO NOTHING",
        (touch["person_id"], reason, note, now))
    if not cur.rowcount:
        return None
    no_go_id = cur.lastrowid
    append_event(conn, entity="no_go", entity_id=no_go_id, company_id=touch["company_id"], kind="added",
                 actor="devanshu", at=now, idempotency_key=next(keys),
                 detail={"reason": reason, "source": "reply", "personId": touch["person_id"],
                         "touchId": touch["touch_id"], "text": f"No-go: {note.lower().rstrip('.')}."})
    return no_go_id


STOPPED = "skipped_by_sender"  # the sending plan's event kind for a stopped message (#14, stop_planned)


def stop_unsent_messages(conn: sqlite3.Connection, person_id: int, reply_kind: str, *, now: str,
                         keys: Iterator[str]) -> int:
    """Stop every unsent message on the person's email touch, as the sending plan's stops do (#14 `stop_planned`
    and `sweep_stopped`). Returns how many it stopped.

    A `planned` row holds no text and the `message` CHECK refuses an empty subject or body in every state but
    `planned`, so it is deleted by the sender's own `stop_planned` (actor `devanshu`), with a `touch` event keyed
    `followup-stopped:<touch_id>:<seq>:<due_on>`. A
    `drafted`, `approved` or `failed` row has text and moves to `skipped` (revision on by one), with a `message`
    event. Both events are `skipped_by_sender` with detail `{seq, due_on, reason}`. A `sending` or `send_unknown`
    row may already be out: the sender settles it.
    """
    rows = conn.execute(
        "SELECT m.message_id, m.touch_id, m.seq, m.due_on, m.state, p.company_id FROM message m "
        "JOIN touch t ON t.touch_id = m.touch_id JOIN person p ON p.person_id = t.person_id "
        "WHERE t.person_id = ? AND m.state IN ('planned', 'drafted', 'approved', 'failed') ORDER BY m.seq",
        (person_id,)).fetchall()
    stopped = 0
    for r in rows:
        detail = {"seq": r["seq"], "due_on": r["due_on"], "reason": reply_kind}
        if r["state"] == "planned":
            # The sender's one stop for a planned row: deleted, with the touch event its own stops write.
            stop_planned(conn, r["message_id"], reason=reply_kind, now=now, actor="devanshu")
        else:
            if not conn.execute(
                    "UPDATE message SET state = 'skipped', updated_at = ?, revision = revision + 1 "
                    "WHERE message_id = ? AND state = ?", (now, r["message_id"], r["state"])).rowcount:
                continue
            append_event(conn, entity="message", entity_id=r["message_id"], company_id=r["company_id"],
                         kind=STOPPED, actor="devanshu", at=now, detail=detail, idempotency_key=next(keys))
        stopped += 1
    return stopped


def apply_reply_effects(conn: sqlite3.Connection, touch: Mapping, reply_kind: str | None, *, now: str,
                        keys: Iterator[str]) -> None:
    """What a reply that says no or asks to stop does beyond the outcome, in the caller's transaction, on any
    channel: the person's no-go (a person who said stop is never contacted again) and the stop of every unsent
    message on their email touch. Any other reply kind does nothing here. (Task 16's LinkedIn log calls this too.)"""
    if reply_kind not in STOP_REASONS or touch["person_id"] is None:
        return
    add_person_no_go(conn, touch, reply_kind, now=now, keys=keys)
    stop_unsent_messages(conn, touch["person_id"], reply_kind, now=now, keys=keys)


def _linkedin_log(conn: sqlite3.Connection, touch: Mapping, kind: str, reply_kind: str | None, now: str) -> None:
    conn.execute(
        "INSERT INTO linkedin_log(person_id, kind, reply_kind, at, recorded_at) VALUES (?, ?, ?, ?, ?)",
        (touch["person_id"], LOG_KINDS[kind], reply_kind if kind == "replied" else None, now, now))


# ---------------------------------------------------------------------------------------------- answers

def answer(ctx: WriteCtx, touch_id: int, body: OutcomeBody, cfg: PromptConfig) -> int:
    """Record an answer from the prompt inside the write's transaction. Returns the touch's revision after it.
    Only the outcome write maps a store constraint to `bad_request`; any other IntegrityError is a bug and a 500."""
    conn, keys = ctx.conn, (ctx.event_key(n) for n in itertools.count())
    if body.kind == "replied" and body.reply_kind is None:
        raise ApiProblem("bad_request", f"Say what kind of reply it was. {NOT_SAVED}")
    if body.kind != "replied" and body.reply_kind is not None:
        raise ApiProblem("bad_request", f"A reply kind goes only with a reply. {NOT_SAVED}")
    row = _touch(conn, touch_id)
    column = OUTCOME_COLUMNS[body.kind]
    if body.kind == "quiet":
        if _has_outcome(row):
            raise ApiProblem("not_allowed", f"This touch already has an outcome, so there is nothing to wait for. "
                                            f"{NOT_SAVED}")
    elif row[column] is not None:
        raise ApiProblem("not_allowed", f"Already recorded: this touch has {OUTCOME_WORDS[body.kind]} on "
                                        f"{ny_day(row[column])}. To change it, correct it from the touch's history.")
    if row["revision"] != body.revision:
        raise touch_changed()
    if body.kind == "quiet" and not should_mark_quiet(row, ctx.now, cfg):
        # Once per touch per New York day: two tabs answering the same day cannot use up both snoozes.
        append_event(conn, entity="outreach_touch", entity_id=touch_id, company_id=row["company_id"], kind=SNOOZE,
                     actor="devanshu", at=ctx.now, idempotency_key=f"{SNOOZE}:{touch_id}:{ny_day(ctx.now)}",
                     detail={"text": "Nothing yet."})
        return row["revision"]
    try:
        revision = record_outcome(conn, touch_id, kind=body.kind, at=ctx.now, reply_kind=body.reply_kind,
                                  revision=body.revision)
    except LookupError:
        raise touch_changed() from None
    except sqlite3.IntegrityError as err:
        raise store_refusal(err) from err
    except ValueError as err:
        raise plain_refusal(err) from None
    if row["channel"] in LINKEDIN and body.kind in LOG_KINDS:
        _linkedin_log(conn, row, body.kind, body.reply_kind, ctx.now)
    if body.kind == "replied":
        apply_reply_effects(conn, row, body.reply_kind, now=ctx.now, keys=keys)
    return revision


# ---------------------------------------------------------------------------------------------- corrections

def correct(ctx: WriteCtx, touch_id: int, kind: str, body: CorrectionBody) -> int:
    """Set or clear one outcome from the touch's history (`correct_outcome`, stamped with the write's `now`)."""
    conn = ctx.conn
    _touch(conn, touch_id)
    at = body.at
    if at is not None:
        try:
            if iso_utc(at) is None:
                raise ValueError
        except ValueError:
            raise ApiProblem("bad_request", "The date must be an ISO time such as 2026-11-10T15:14:00Z, or null "
                                            f"to clear it. {NOT_SAVED}") from None
    try:
        revision = correct_outcome(conn, touch_id, kind, at=at, reply_kind=body.reply_kind,
                                   revision=body.revision, now=ctx.now)
    except LookupError:
        raise touch_changed() from None
    except sqlite3.IntegrityError as err:
        raise store_refusal(err) from err
    except ValueError as err:
        raise plain_refusal(err) from None
    if kind == "replied":  # a reply corrected to said_no or asked_to_stop stops them as an answer would
        row = conn.execute("SELECT * FROM outreach_touch WHERE touch_id = ?", (touch_id,)).fetchone()
        if row["replied_at"] is not None:
            apply_reply_effects(conn, row, row["reply_kind"], now=ctx.now,
                                keys=(ctx.event_key(n) for n in itertools.count()))
    return revision


# ---------------------------------------------------------------------------------------------- history

def touch_history(conn: sqlite3.Connection, touch_id: int) -> TouchHistory | None:
    """One touch, its outcome dates and its events newest first; None when there is no such touch."""
    row = conn.execute("SELECT * FROM outreach_touch WHERE touch_id = ?", (touch_id,)).fetchone()
    if row is None:
        return None
    survivor = survivor_of(merge_parents(conn), row["company_id"])
    company = company_refs(conn, {survivor})[survivor]
    person = people_by_ids(conn, {row["person_id"]}).get(row["person_id"]) if row["person_id"] is not None else None
    events = conn.execute(
        "SELECT * FROM event WHERE entity = 'outreach_touch' AND entity_id = ? ORDER BY at DESC, event_id DESC",
        (touch_id,)).fetchall()
    return TouchHistory(
        touch_id=touch_id, channel=row["channel"], person=person_ref(person) if person is not None else None,
        company=company, sent_at=row["sent_at"], accepted=row["accepted_at"], replied=row["replied_at"],
        reply_kind=row["reply_kind"], referred=row["referred_at"], interview=row["interview_at"],
        offer=row["offer_at"], quiet=row["quiet_at"], events=[to_event(e) for e in events], revision=row["revision"],
    )


# ---------------------------------------------------------------------------------------------- his own touches

def _newest_score(conn: sqlite3.Connection, company_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT ranker_version, signals, lanes FROM company_score WHERE company_id = ? "
        "ORDER BY scored_at DESC, ranker_version DESC LIMIT 1", (company_id,)).fetchone()


def _score_parts(score: sqlite3.Row | None) -> tuple[str, str | None, dict[str, float]]:
    """The lane (the newest score's first lane, else `own`), the ranker version and the signal values."""
    if score is None:
        return "own", None, {}
    try:
        lanes = json.loads(score["lanes"])
        signals = json.loads(score["signals"])
    except ValueError:
        return "own", score["ranker_version"], {}
    lane = lanes[0] if isinstance(lanes, list) and lanes and isinstance(lanes[0], str) and lanes[0] else "own"
    values: dict[str, float] = {}
    raw = signals.get("values") if isinstance(signals, dict) else None
    for name, entry in (raw.items() if isinstance(raw, dict) else ()):
        value = entry.get("value") if isinstance(entry, dict) else None
        if isinstance(name, str) and name and isinstance(value, (int, float)) and not isinstance(value, bool) \
                and math.isfinite(value):
            values[name] = float(value)
    return lane, score["ranker_version"], values


def own_context(conn: sqlite3.Connection, company_id: int, role_kind: str | None, role_first_seen_at: str | None,
                now: str) -> dict:
    """The context of a touch he logged himself (`source 'own'`): the lane, ranker version and signals come from the
    company's newest score (lane `own` when it has none), and a role was named when `role_first_seen_at` is given."""
    lane, version, signals = _score_parts(_newest_score(conn, company_id))
    return build_context(
        source="own", lane=lane, role_kind=role_kind,
        role_age_days=max(ny_days_between(role_first_seen_at, now), 0) if role_first_seen_at is not None else None,
        ranker_version=version, signals=signals, role_named=role_first_seen_at is not None,
    )


def log_own_touch(ctx: WriteCtx, body: LogTouchBody) -> TouchLogged:
    """Log an HN reply or another touch he made himself, once per `idempotencyKey`, with `source 'own'`. Every input
    is checked first, so a store constraint error here is a bug (a logged 500), never the client's mistake."""
    conn = ctx.conn
    if body.channel not in OWN_CHANNELS:
        raise ApiProblem("bad_request", "Log a LinkedIn step from the LinkedIn list, an application from the plan; "
                                        f"emails are logged by the sender. Here: an HN reply or another touch. {NOT_SAVED}")
    if conn.execute("SELECT 1 FROM company WHERE company_id = ?", (body.company_id,)).fetchone() is None:
        raise ApiProblem("not_found", f"There is no company {body.company_id}.")
    parents = merge_parents(conn)
    survivor = survivor_of(parents, body.company_id)
    if body.person_id is None:
        raise ApiProblem("bad_request", f"Name the person you reached. {NOT_SAVED}")
    person = conn.execute("SELECT * FROM person WHERE person_id = ?", (body.person_id,)).fetchone()
    if person is None:
        raise ApiProblem("not_found", f"There is no person {body.person_id}.")
    if survivor_of(parents, person["company_id"]) != survivor:
        raise ApiProblem("bad_request", f"{person['name']} is not at that company. {NOT_SAVED}")
    posting = None
    if body.posting_id is not None:
        posting = conn.execute("SELECT company_id, first_seen_at FROM posting WHERE posting_id = ?",
                               (body.posting_id,)).fetchone()
        if posting is None:
            raise ApiProblem("not_found", f"There is no posting {body.posting_id}.")
        if survivor_of(parents, posting["company_id"]) != survivor:
            raise ApiProblem("bad_request", f"That posting is at another company. {NOT_SAVED}")
    context = own_context(conn, survivor, person["role_kind"], posting["first_seen_at"] if posting else None, ctx.now)
    touch_id = log_touch(conn, person_id=body.person_id, company_id=survivor, channel=body.channel,
                         sent_at=ctx.now, context=context, posting_id=body.posting_id,
                         idempotency_key=body.idempotency_key)
    revision = conn.execute("SELECT revision FROM outreach_touch WHERE touch_id = ?", (touch_id,)).fetchone()[0]
    return TouchLogged(touch_id=touch_id, revision=revision)
