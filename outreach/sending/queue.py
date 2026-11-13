"""Who may be sent now, why a message waits, and in what order (#9: spacing, per-company limits, recipients' hours).

Read only: nothing here writes, and nothing opens a transaction. `verdicts` gives every approved message one
reason in a fixed order, so the first reason that applies is the one Devanshu reads. A stop reason means the message
will never go out as it stands (the tick's sweep removes it); every other reason is a wait. Every read of a company
follows `merged_into` to its end (`canonical_company`), so holds, pauses, status and the per-company counts all use
the surviving company.
"""
import random
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from outreach.drafts.followups import (canonical_company, company_map, held_survivors, no_go_sets,
                                       replied_people, resolve_company)
from outreach.sending import calendar as cal
from outreach.sending import accounts, human, pace, ramp, signals
from outreach.sending.config import SendConfig, load_config
from outreach.sending.pace import CompanyPace
from outreach.sending.ramp import DayState
from outreach.store.timefmt import ISO_FMT

__all__ = ["REASONS", "STOP_REASONS", "TIME_REASONS", "REASON_TEXT", "Queued", "Verdict", "canonical_company",
           "load_queue", "Prepared", "prepare", "decide_all", "verdicts", "first_only", "pick"]

STOP_REASONS = ("company_closed", "no_go", "reply_stop", "touch_done")
REASONS = ("ready", "sender_paused", "signed_out", "old_sender_running", "message_id_unproven", "not_business_day",
           "daily_limit", "spacing", "window", "bad_timezone", "followup_not_due", "earlier_followup", "company_paused",
           "company_held", "per_company_limit", "per_company_gap", "recruiter_waits", "no_address",
           "no_thread") + STOP_REASONS
# The waits that end by themselves: their verdict carries the time they end.
TIME_REASONS = frozenset({"not_business_day", "daily_limit", "spacing", "window", "followup_not_due",
                          "per_company_limit", "per_company_gap"})

REASON_TEXT = {
    "ready": "Ready to send.",
    "sender_paused": "Sending is paused.",
    "signed_out": "Gmail is signed out, so nothing can be sent.",
    "old_sender_running": "The old mail sender is still running, so the new one waits.",
    # While Gmail is not shown to keep the sender's marker (the code keeps its first name), the words depend on
    # where the first email stands (`proof.PHASE_TEXT`); this is the line for the first of those, when nothing is
    # claimed yet.
    "message_id_unproven": "The first email goes out alone, then Gmail is checked. The rest follow.",
    "not_business_day": "Not a sending day: emails go out on weekdays, New York time.",
    "daily_limit": "Today's send limit is used up. This goes out on the next sending day.",
    "spacing": "Waiting a few minutes, so sends are spaced like a person's.",
    "window": "Outside this person's sending hours in their own time zone.",
    "bad_timezone": "This person's time zone is not one the sender knows, so their sending hours are unknown. "
                    "Fix the time zone.",
    "followup_not_due": "This follow-up is not due yet.",
    "earlier_followup": "An earlier follow-up in this thread has not gone out yet. This one goes after it.",
    "company_paused": "This company is paused. Unpause it to send.",
    "company_held": "This company is held because someone there replied. Release the hold to send.",
    "per_company_limit": "The daily limit for this company is used up. This goes out on the next sending day.",
    "per_company_gap": "Another email went to this company a little while ago. Waiting out its gap (20 minutes to 2 "
                       "hours, by company size).",
    "recruiter_waits": "A recruiter waits while an email to someone else at this company is still queued.",
    "no_address": "No email address for this person.",
    "no_thread": "This follow-up has no Gmail thread to reply in, so it is not sent. Skip it, or send it as a new "
                 "first email.",
    "company_closed": "This company is closed or removed, so this email will not be sent.",
    "no_go": "This person or company is on the no-go list, so this email will not be sent.",
    "reply_stop": "This person replied, so this email will not be sent.",
    "touch_done": "This thread is finished (it bounced or was closed), so this email will not be sent.",
}


@dataclass(frozen=True)
class Queued:
    message_id: int
    touch_id: int
    seq: int
    revision: int
    person_id: int
    to: str
    tz: str
    role_kind: str
    company_id: int  # the surviving company
    approved_at: str
    due_on: date | None


@dataclass(frozen=True)
class Verdict:
    message_id: int
    reason: str
    earliest_at: datetime | None


# The states of a message that has not gone out and may still: an earlier follow-up in one of them holds a later one.
_UNSENT = ("planned", "drafted", "approved", "sending", "send_unknown", "failed")

_APPROVED = (
    "SELECT m.message_id, m.touch_id, m.seq, m.revision, m.approved_at, m.due_on, t.person_id, t.state AS touch_state, "
    "t.gmail_thread_id, p.email, p.tz, p.role_kind, p.company_id AS person_company FROM message m "
    "JOIN touch t ON t.touch_id = m.touch_id JOIN person p ON p.person_id = t.person_id WHERE m.state = 'approved'")


def _utc(text: str) -> datetime:
    return signals.as_datetime(text)


def _local(d: date, t: time, zone: ZoneInfo) -> datetime:
    return datetime.combine(d, t, tzinfo=zone).astimezone(timezone.utc)


def _due_start(due_on: date, zone: ZoneInfo | None, cfg: SendConfig) -> datetime:
    """`due_on` at the window start in the recipient's time (New York when the zone is unknown)."""
    return _local(due_on, cfg.window_start, zone or ZoneInfo(cfg.day_tz))


def _rows(conn: sqlite3.Connection) -> tuple[list[sqlite3.Row], dict[int, sqlite3.Row]]:
    return conn.execute(_APPROVED).fetchall(), company_map(conn)


def _queued(row: sqlite3.Row, companies: dict[int, sqlite3.Row]) -> Queued:
    return Queued(message_id=row["message_id"], touch_id=row["touch_id"], seq=row["seq"], revision=row["revision"],
                  person_id=row["person_id"], to=row["email"] or "", tz=row["tz"], role_kind=row["role_kind"],
                  company_id=resolve_company(companies, row["person_company"])[-1],
                  approved_at=row["approved_at"],
                  due_on=date.fromisoformat(row["due_on"]) if row["due_on"] else None)


def _sorted(items: list[Queued], cfg: SendConfig) -> list[Queued]:
    def key(q: Queued):
        ready = _utc(q.approved_at)
        if q.due_on is not None:
            ready = max(ready, _due_start(q.due_on, cal.recipient_zone(q.tz), cfg))
        return ready, q.message_id
    return sorted(items, key=key)


def load_queue(conn: sqlite3.Connection, cfg: SendConfig | None = None) -> list[Queued]:
    """The `approved` messages, first approved first. A follow-up counts from the later of its approval and the
    start of its due day in the recipient's time, so a follow-up approved early does not jump the line."""
    cfg = cfg or load_config()
    rows, companies = _rows(conn)
    return _sorted([_queued(row, companies) for row in rows], cfg)


def _gap(seq: int, cfg: SendConfig) -> int:
    """Business days between follow-up `seq - 1` and follow-up `seq` in the schedule (seq 2 and up)."""
    days = cfg.followup_business_days
    if len(days) < 2:
        return days[0]
    i = min(seq, len(days)) - 1
    return days[i] - days[i - 1]


def _not_before(conn: sqlite3.Connection, cfg: SendConfig) -> dict[int, date]:
    """For each approved follow-up from seq 2, the first day it may go: its schedule gap after the New York date of
    the latest earlier message sent in its thread."""
    out = {}
    for row in conn.execute(
            "SELECT m.message_id, m.seq, (SELECT max(e.sent_at) FROM message e WHERE e.touch_id = m.touch_id "
            "AND e.seq BETWEEN 1 AND m.seq - 1 AND e.state = 'sent') AS prev FROM message m "
            "WHERE m.state = 'approved' AND m.seq >= 2"):
        if row["prev"]:
            out[row["message_id"]] = cal.add_business_days(cal.ny_date(_utc(row["prev"]), cfg), _gap(row["seq"], cfg),
                                                            cfg)
    return out


def _stop_reasons(rows: list[sqlite3.Row], companies: dict[int, sqlite3.Row], conn: sqlite3.Connection) -> dict:
    """Message id to its stop reason, for the messages that have one."""
    no_go_people, no_go_companies = no_go_sets(conn, companies)
    replied = replied_people(conn)
    stops = {}
    for row in rows:
        chain = resolve_company(companies, row["person_company"])
        if companies[chain[-1]]["status"] in ("closed", "removed"):
            stops[row["message_id"]] = "company_closed"
        elif row["person_id"] in no_go_people or chain[-1] in no_go_companies:
            stops[row["message_id"]] = "no_go"
        elif row["touch_state"] == "replied" or row["person_id"] in replied:  # a reply on any channel
            stops[row["message_id"]] = "reply_stop"
        elif row["touch_state"] in ("bounced", "closed"):
            stops[row["message_id"]] = "touch_done"
    return stops


def _company_sends(conn: sqlite3.Connection, companies: dict[int, sqlite3.Row],
                   since: str) -> dict[int, list[tuple[datetime, bool]]]:
    """When each surviving company was sent to from `since` on, each send with whether it was a first email (the
    per-company limit counts only those; the gap counts every send). A claimed message that may have gone out
    (`sending`, `send_unknown`) counts at its claim time: better one wait too many than one send too many."""
    sends: dict[int, list[tuple[datetime, bool]]] = {}
    for row in conn.execute(
            "SELECT p.company_id, m.seq, CASE m.state WHEN 'sent' THEN m.sent_at ELSE m.claimed_at END AS at "
            "FROM message m JOIN touch t ON t.touch_id = m.touch_id JOIN person p ON p.person_id = t.person_id "
            "WHERE m.state IN ('sent', 'sending', 'send_unknown') AND at >= ?", (since,)):
        survivor = resolve_company(companies, row["company_id"])[-1]
        sends.setdefault(survivor, []).append((_utc(row["at"]), row["seq"] == 0))
    return sends


@dataclass(frozen=True)
class Prepared:
    """What one pass over the approved queue reads from the store, so a projection can decide the same queue at many
    moments without reading it again. Nothing here depends on the moment decided, except `sends`, which holds every
    company send since the start of today or an hour before `now`, whichever is earlier."""
    queue: list[Queued]
    stops: dict[int, str]
    no_thread: frozenset[int]
    behind: frozenset[int]
    not_before: dict[int, date]
    companies: dict[int, sqlite3.Row]
    held: frozenset[int]
    sends: dict[int, list[tuple[datetime, bool]]]
    sent_today: int
    limit: int
    next_send: datetime | None
    paces: dict[int, CompanyPace] = field(default_factory=dict)  # each queued company's size band (#229)


def prepare(conn: sqlite3.Connection, now: datetime, cfg: SendConfig, *, day: DayState | None) -> Prepared:
    """The queue and the facts around it, read once. `day` is the tick's snapshot of today (see `verdicts`)."""
    rows, companies = _rows(conn)
    now = now.astimezone(timezone.utc)
    today = cal.ny_date(now, cfg)
    day_start, _ = cal.ny_day_bounds(today, cfg)
    gap = human.max_company_gap(cfg)  # how far back a company's last send can still hold the next one
    account = accounts.primary(conn, cfg)  # one account until the per-account loop lands
    limit = ramp.effective_limit(conn, account, today, cfg)
    if day is not None and day.day == today:
        limit = min(limit, day.effective_limit)  # the tick's snapshot can only be older, so it can only be higher
    # The ramp counts only what is sent; the limit also counts a claimed message that may have gone out. An email
    # that bounced does not use a slot (#224).
    sent_today = signals.counted_sends_on(conn, account.account_id, today, cfg) + conn.execute(
        "SELECT COUNT(*) FROM message WHERE state IN ('sending', 'send_unknown') AND claimed_at >= ? "
        "AND claimed_at < ?", cal.ny_day_bounds(today, cfg)).fetchone()[0]
    next_send = conn.execute("SELECT next_send_at FROM sender WHERE sender_id = 1").fetchone()["next_send_at"]
    queued = _sorted([_queued(row, companies) for row in rows], cfg)
    return Prepared(
        queue=queued, stops=_stop_reasons(rows, companies, conn),
        paces=pace.company_paces(conn, {q.company_id for q in queued}, cfg),
        # A follow-up goes only in its touch's Gmail thread; without one it would start a new thread, so it waits.
        no_thread=frozenset(row["message_id"] for row in rows if row["seq"] >= 1 and not row["gmail_thread_id"]),
        # Follow-ups in one thread go out in order (#141): one waits while an earlier one has not gone out, and then
        # keeps the schedule's gap after it (an earlier one sent late does not pull the next one right behind it).
        behind=frozenset(row[0] for row in conn.execute(
            "SELECT m.message_id FROM message m WHERE m.state = 'approved' AND m.seq >= 2 AND EXISTS (SELECT 1 FROM "
            "message e WHERE e.touch_id = m.touch_id AND e.seq BETWEEN 1 AND m.seq - 1 "
            f"AND e.state IN ({','.join('?' * len(_UNSENT))}))", _UNSENT)),
        not_before=_not_before(conn, cfg), companies=companies, held=frozenset(held_survivors(companies)),
        sends=_company_sends(conn, companies, min(day_start, (now - gap).strftime(ISO_FMT))),
        sent_today=sent_today, limit=limit, next_send=_utc(next_send) if next_send else None)


def decide_all(p: Prepared, now: datetime, cfg: SendConfig, *, gate: str | None, pending: list[Queued],
               sends: dict[int, list[tuple[datetime, bool]]], sent_by_day: dict[date, int],
               next_send: datetime | None, limit: int) -> list[Verdict]:
    """One verdict per message of `pending` (in its order) as of `now`, with `sends`, the day's sent count
    (`sent_by_day`, by New York date) and the sender's next free moment as given. `verdicts` passes what the store
    says; a projection passes what it has placed so far."""
    now = now.astimezone(timezone.utc)
    # A message that blocks a recruiter is one that could go out: not a stop, with an address and a thread, due.
    live_others = {q.company_id for q in pending if q.role_kind != "recruiter" and q.message_id not in p.stops
                   and q.message_id not in p.no_thread and q.message_id not in p.behind
                   and q.to and not (q.due_on is not None
                                     and now < _due_start(q.due_on, cal.recipient_zone(q.tz), cfg))}
    today = cal.ny_date(now, cfg)
    day_begins = _utc(cal.ny_day_bounds(today, cfg)[0])
    tomorrow = _local(cal.add_business_days(today, 1, cfg), cfg.window_start, ZoneInfo(cfg.day_tz))
    day_full = sent_by_day.get(today, 0) >= limit
    # The day's first send waits a different few minutes each day after the window opens (`human.day_start_offset`).
    day_opens = (_local(today, cfg.window_start, ZoneInfo(cfg.day_tz)) + human.day_start_offset(today, cfg)
                 if sent_by_day.get(today, 0) == 0 else None)
    return [Verdict(q.message_id, *_decide(
        q, p.stops, gate, p.companies[q.company_id], q.company_id in p.held, now, cfg, today, day_full,
        sends.get(q.company_id, []), day_begins, day_opens, tomorrow, next_send, q.company_id in live_others,
        no_thread=q.message_id in p.no_thread, behind=q.message_id in p.behind,
        not_before=p.not_before.get(q.message_id),
        company_pace=p.paces.get(q.company_id) or pace.default_pace(cfg))) for q in pending]


def verdicts(conn: sqlite3.Connection, now: datetime, cfg: SendConfig, *, day: DayState | None,
             gate: str | None) -> list[Verdict]:
    """One verdict per approved message, in queue order. `gate` (`sender_paused`, `signed_out`,
    `old_sender_running`, `message_id_unproven`) is a reason the whole tick knows; it applies to every message that
    is not a stop.
    Order of reasons: stops; the gate; company paused; company held (a hold on any company merged into the
    survivor counts); no address; a follow-up with no Gmail thread; follow-up not due; an earlier follow-up in the
    thread not gone out; bad time zone; not a business day; daily limit; per-company limit; per-company gap;
    recipient's window; recruiter waits; spacing."""
    p = prepare(conn, now, cfg, day=day)
    if not p.queue:
        return []
    return decide_all(p, now, cfg, gate=gate, pending=p.queue, sends=p.sends,
                      sent_by_day={cal.ny_date(now.astimezone(timezone.utc), cfg): p.sent_today},
                      next_send=p.next_send, limit=p.limit)


def _decide(q: Queued, stops: dict, gate: str | None, company: sqlite3.Row, held: bool, now: datetime, cfg: SendConfig,
            today: date, day_full: bool, sends: list[tuple[datetime, bool]], day_begins: datetime,
            day_opens: datetime | None, tomorrow: datetime, next_send: datetime | None, others_queued: bool, *,
            no_thread: bool = False, behind: bool = False,
            not_before: date | None = None, company_pace: CompanyPace | None = None) -> tuple[str, datetime | None]:
    company_pace = company_pace or pace.default_pace(cfg)
    if q.message_id in stops:
        return stops[q.message_id], None
    if gate is not None:
        return gate, None
    if company["paused_at"] is not None:
        return "company_paused", None
    if held:
        return "company_held", None
    if not q.to:
        return "no_address", None
    if no_thread:
        return "no_thread", None
    zone = cal.recipient_zone(q.tz)
    due_on = max((d for d in (q.due_on, not_before) if d is not None), default=None)
    if due_on is not None:
        due = _due_start(due_on, zone, cfg)
        if now < due:
            return "followup_not_due", cal.next_window_start(due, zone or ZoneInfo(cfg.day_tz), cfg)
    if behind:
        return "earlier_followup", None
    if zone is None:
        return "bad_timezone", None
    if not cal.is_business_day(today, cfg):
        return "not_business_day", tomorrow
    if day_full:
        return "daily_limit", tomorrow
    # #9, 2026-10-31: the per-company limit is first emails only; a follow-up neither counts nor waits for it. The
    # limit and the gap are the company's size band (#229).
    if q.seq == 0 and sum(1 for at, first in sends if first and at >= day_begins) >= company_pace.daily:
        return "per_company_limit", tomorrow
    last = max((at for at, _ in sends), default=None)
    if last is not None and now < last + human.company_gap(q.company_id, last, cfg, company_pace):
        return "per_company_gap", last + human.company_gap(q.company_id, last, cfg, company_pace)
    if not cal.in_window(now, zone, cfg):
        return "window", cal.next_window_start(now, zone, cfg)
    if q.role_kind == "recruiter" and others_queued:
        return "recruiter_waits", None
    if next_send is not None and now < next_send:
        return "spacing", next_send
    if day_opens is not None and now < day_opens:
        return "spacing", day_opens
    return "ready", None


def first_only(verdicts: list[Verdict]) -> list[Verdict]:
    """The verdicts while one first email may go before Gmail is proven to keep the sender's marker (`proof`): the
    first `ready` message in queue order (else the time wait that ends first) keeps its verdict, and every other
    message that is `ready` or waits on time waits as `message_id_unproven` instead. Stops and the waits that need an
    action from him (a paused or held company, no address, no thread, a bad time zone, a recruiter waiting) keep
    theirs."""
    movable = [v for v in verdicts if v.reason == "ready" or v.reason in TIME_REASONS]
    first = next((v for v in movable if v.reason == "ready"), None)
    if first is None:
        timed = [(v.earliest_at, i) for i, v in enumerate(movable) if v.earliest_at is not None]
        first = movable[min(timed)[1]] if timed else None
    return [v if v is first or not (v.reason == "ready" or v.reason in TIME_REASONS)
            else Verdict(v.message_id, "message_id_unproven", None) for v in verdicts]


def pick(verdicts: list[Verdict], queue: list[Queued], rng: random.Random | None = None,
         pool: int = 1) -> Queued | None:
    """A message whose verdict is `ready`, or None. With no `rng` it is the first in queue order; with one it is any
    of the first `pool` ready messages, so the order is not strictly first come, first served (`human.choose`)."""
    ready = {v.message_id for v in verdicts if v.reason == "ready"}
    candidates = [q for q in queue if q.message_id in ready]
    return human.choose(candidates, rng, pool) if rng is not None else (candidates[0] if candidates else None)
