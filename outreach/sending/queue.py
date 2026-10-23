"""Who may be sent now, why a message waits, and in what order (#9: spacing, per-company limits, recipients' hours).

Read only: nothing here writes, and nothing opens a transaction. `verdicts` gives every approved message one
reason in a fixed order, so the first reason that applies is the one Devanshu reads. A stop reason means the message
will never go out as it stands (the tick's sweep removes it); every other reason is a wait. Every read of a company
follows `merged_into` to its end (`canonical_company`), so holds, pauses, status and the per-company counts all use
the surviving company.
"""
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from outreach.drafts.followups import (canonical_company, company_map, held_survivors, no_go_sets,
                                       resolve_company)
from outreach.sending import calendar as cal
from outreach.sending import ramp, signals
from outreach.sending.config import SendConfig, load_config
from outreach.sending.ramp import DayState
from outreach.store.timefmt import ISO_FMT

__all__ = ["REASONS", "STOP_REASONS", "TIME_REASONS", "REASON_TEXT", "Queued", "Verdict", "canonical_company",
           "load_queue", "verdicts", "pick"]

STOP_REASONS = ("company_closed", "no_go", "reply_stop", "touch_done")
REASONS = ("ready", "sender_paused", "signed_out", "old_sender_running", "not_business_day", "daily_limit",
           "spacing", "window", "bad_timezone", "followup_not_due", "company_paused", "company_held",
           "per_company_limit", "per_company_gap", "recruiter_waits", "no_address", "no_thread") + STOP_REASONS
# The waits that end by themselves: their verdict carries the time they end.
TIME_REASONS = frozenset({"not_business_day", "daily_limit", "spacing", "window", "followup_not_due",
                          "per_company_limit", "per_company_gap"})

REASON_TEXT = {
    "ready": "Ready to send.",
    "sender_paused": "Sending is paused.",
    "signed_out": "Gmail is signed out, so nothing can be sent.",
    "old_sender_running": "The old mail sender is still running, so the new one waits.",
    "not_business_day": "Not a sending day: emails go out on weekdays, New York time.",
    "daily_limit": "Today's send limit is used up. This goes out on the next sending day.",
    "spacing": "Waiting a few minutes after the last send.",
    "window": "Outside this person's sending hours in their own time zone.",
    "bad_timezone": "This person's time zone is not one the sender knows, so their sending hours are unknown. "
                    "Fix the time zone.",
    "followup_not_due": "This follow-up is not due yet.",
    "company_paused": "This company is paused. Unpause it to send.",
    "company_held": "This company is held because someone there replied. Release the hold to send.",
    "per_company_limit": "The daily limit for this company is used up. This goes out on the next sending day.",
    "per_company_gap": "Another email went to this company less than an hour ago. Waiting out the gap.",
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


def _stop_reasons(rows: list[sqlite3.Row], companies: dict[int, sqlite3.Row], conn: sqlite3.Connection) -> dict:
    """Message id to its stop reason, for the messages that have one."""
    no_go_people, no_go_companies = no_go_sets(conn, companies)
    stops = {}
    for row in rows:
        chain = resolve_company(companies, row["person_company"])
        if companies[chain[-1]]["status"] in ("closed", "removed"):
            stops[row["message_id"]] = "company_closed"
        elif row["person_id"] in no_go_people or chain[-1] in no_go_companies:
            stops[row["message_id"]] = "no_go"
        elif row["touch_state"] == "replied":
            stops[row["message_id"]] = "reply_stop"
        elif row["touch_state"] in ("bounced", "closed"):
            stops[row["message_id"]] = "touch_done"
    return stops


def _company_sends(conn: sqlite3.Connection, companies: dict[int, sqlite3.Row],
                   since: str) -> dict[int, list[datetime]]:
    """When each surviving company was last sent to, from `since` on. A claimed message that may have gone out
    (`sending`, `send_unknown`) counts at its claim time: better one wait too many than one send too many."""
    sends: dict[int, list[datetime]] = {}
    for row in conn.execute(
            "SELECT p.company_id, CASE m.state WHEN 'sent' THEN m.sent_at ELSE m.claimed_at END AS at "
            "FROM message m JOIN touch t ON t.touch_id = m.touch_id JOIN person p ON p.person_id = t.person_id "
            "WHERE m.state IN ('sent', 'sending', 'send_unknown') AND at >= ?", (since,)):
        survivor = resolve_company(companies, row["company_id"])[-1]
        sends.setdefault(survivor, []).append(_utc(row["at"]))
    return sends


def verdicts(conn: sqlite3.Connection, now: datetime, cfg: SendConfig, *, day: DayState | None,
             gate: str | None) -> list[Verdict]:
    """One verdict per approved message, in queue order. `gate` (`sender_paused`, `signed_out`,
    `old_sender_running`) is a reason the whole tick knows; it applies to every message that is not a stop.
    Order of reasons: stops; the gate; company paused; company held (a hold on any company merged into the
    survivor counts); no address; a follow-up with no Gmail thread; follow-up not due; bad time zone; not a business day; daily limit; per-company
    limit; per-company gap; recipient's window; recruiter waits; spacing."""
    rows, companies = _rows(conn)
    items = {row["message_id"]: _queued(row, companies) for row in rows}
    queue = _sorted(list(items.values()), cfg)
    if not queue:
        return []
    stops = _stop_reasons(rows, companies, conn)
    # A follow-up goes only in its touch's Gmail thread; without one it would start a new thread, so it waits.
    no_thread = {row["message_id"] for row in rows if row["seq"] >= 1 and not row["gmail_thread_id"]}
    now = now.astimezone(timezone.utc)
    # A message that blocks a recruiter is one that could go out: not a stop, with an address and a thread, due.
    live_others = {q.company_id for q in queue if q.role_kind != "recruiter" and q.message_id not in stops
                   and q.message_id not in no_thread
                   and q.to and not (q.due_on is not None
                                     and now < _due_start(q.due_on, cal.recipient_zone(q.tz), cfg))}
    held = held_survivors(companies)
    ny_zone = ZoneInfo(cfg.day_tz)
    today = cal.ny_date(now, cfg)
    day_start, _ = cal.ny_day_bounds(today, cfg)
    gap = timedelta(minutes=cfg.per_company_gap_minutes)
    sends = _company_sends(conn, companies, min(day_start, (now - gap).strftime(ISO_FMT)))
    day_begins = _utc(day_start)
    limit = ramp.effective_limit(conn, today, cfg)
    if day is not None and day.day == today:
        limit = min(limit, day.effective_limit)  # the tick's snapshot can only be older, so it can only be higher
    # The ramp counts only what is sent; the limit also counts a claimed message that may have gone out.
    sent_today = signals.sends_on(conn, today, cfg) + conn.execute(
        "SELECT COUNT(*) FROM message WHERE state IN ('sending', 'send_unknown') AND claimed_at >= ? "
        "AND claimed_at < ?", cal.ny_day_bounds(today, cfg)).fetchone()[0]
    next_send = conn.execute("SELECT next_send_at FROM sender WHERE sender_id = 1").fetchone()["next_send_at"]
    next_send = _utc(next_send) if next_send else None
    tomorrow = _local(cal.add_business_days(today, 1, cfg), cfg.window_start, ny_zone)

    out = []
    for q in queue:
        reason, earliest = _decide(q, stops, gate, companies[q.company_id], q.company_id in held, now, cfg, today,
                                   sent_today >= limit, sends.get(q.company_id, []), day_begins, gap, tomorrow,
                                   next_send, q.company_id in live_others, no_thread=q.message_id in no_thread)
        out.append(Verdict(q.message_id, reason, earliest))
    return out


def _decide(q: Queued, stops: dict, gate: str | None, company: sqlite3.Row, held: bool, now: datetime, cfg: SendConfig,
            today: date, day_full: bool, sends: list[datetime], day_begins: datetime, gap: timedelta,
            tomorrow: datetime, next_send: datetime | None, others_queued: bool, *,
            no_thread: bool = False) -> tuple[str, datetime | None]:
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
    if q.due_on is not None:
        due = _due_start(q.due_on, zone, cfg)
        if now < due:
            return "followup_not_due", cal.next_window_start(due, zone or ZoneInfo(cfg.day_tz), cfg)
    if zone is None:
        return "bad_timezone", None
    if not cal.is_business_day(today, cfg):
        return "not_business_day", tomorrow
    if day_full:
        return "daily_limit", tomorrow
    if sum(1 for at in sends if at >= day_begins) >= cfg.per_company_daily:
        return "per_company_limit", tomorrow
    if sends and now < max(sends) + gap:
        return "per_company_gap", max(sends) + gap
    if not cal.in_window(now, zone, cfg):
        return "window", cal.next_window_start(now, zone, cfg)
    if q.role_kind == "recruiter" and others_queued:
        return "recruiter_waits", None
    if next_send is not None and now < next_send:
        return "spacing", next_send
    return "ready", None


def pick(verdicts: list[Verdict], queue: list[Queued]) -> Queued | None:
    """The first message in queue order whose verdict is `ready`, or None."""
    ready = {v.message_id for v in verdicts if v.reason == "ready"}
    return next((q for q in queue if q.message_id in ready), None)
