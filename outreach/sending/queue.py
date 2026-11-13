"""Who may be sent now, why a message waits, and in what order (#9: spacing, per-company limits, recipients' hours).

Read only: nothing here writes, and nothing opens a transaction. `verdicts` gives every approved message one
reason in a fixed order, so the first reason that applies is the one Devanshu reads. A stop reason means the message
will never go out as it stands (the tick's sweep removes it); every other reason is a wait. Every read of a company
follows `merged_into` to its end (`canonical_company`), so holds, pauses, status and the per-company counts all use
the surviving company.

Accounts (multi-sender design, "Lanes" and "Window and pacing"): given the accounts in play, every message gets the
account `accounts.route` picks (`Verdict.account`), and the daily limit, spacing, pause and sign-in are that account's.
What protects the recipient stays global: company stops and holds, the company's size band (its first emails a day and
its gap count every account's sends), the window, weekdays, holidays, the recipient's time zone and `first_only`.
An account whose inbound mail was not fully read this tick (`unread`) holds its own messages and every message to a
company it has a touch at, as `inbound_unread`: a reply there may not be recorded yet. Every caller passes the
accounts in play (`accounts.sync` in the tick, `accounts.in_play` for a reader). What a caller knows per account and
the queue cannot read (this tick's sign-in, the marker proof) it applies after with `gate_accounts`.
"""
import dataclasses
import random
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from outreach.drafts.followups import (canonical_company, company_map, held_survivors, no_go_sets,
                                       replied_people, resolve_company)
from outreach.sending import calendar as cal
from outreach.sending import accounts as _accounts
from outreach.sending import human, pace, ramp, signals
from outreach.sending.accounts import AccountRow
from outreach.sending.config import SendConfig, load_config
from outreach.sending.pace import CompanyPace
from outreach.sending.ramp import DayState
from outreach.store.timefmt import ISO_FMT

__all__ = ["REASONS", "STOP_REASONS", "TIME_REASONS", "REASON_TEXT", "Queued", "Verdict", "AccountDay", "Route",
           "canonical_company", "load_queue", "window_close", "minutes_left", "minutes_left_for", "day_minutes_for",
           "window_minutes", "Prepared", "prepare",
           "decide_all", "verdicts", "first_only", "gate_accounts", "pick"]

STOP_REASONS = ("company_closed", "no_go", "reply_stop", "touch_done")
REASONS = ("ready", "sender_paused", "signed_out", "old_sender_running", "message_id_unproven", "not_business_day",
           "daily_limit", "spacing", "window", "bad_timezone", "followup_not_due", "earlier_followup", "company_paused",
           "company_held", "per_company_limit", "per_company_gap", "recruiter_waits", "no_address",
           "no_thread", "lane_waits", "inbound_unread") + STOP_REASONS
# The waits that end by themselves: their verdict carries the time they end.
TIME_REASONS = frozenset({"not_business_day", "daily_limit", "spacing", "window", "followup_not_due",
                          "per_company_limit", "per_company_gap"})

REASON_TEXT = {
    "ready": "Ready to send.",
    "sender_paused": "Sending is paused.",
    # The verdict's own `text` names the account when the message has one (`signed_out_text`).
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
    # The verdict's own `text` says which lane or account it waits for.
    "lane_waits": "No sending account can take this email yet, so it waits.",
    # The verdict's own `text` names the account whose replies could not be read.
    "inbound_unread": "Waits: replies could not be read yet.",
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
    account: str | None = None  # the address of the account that sends it; None when none can (`lane_waits`)
    # its own sentence, when `REASON_TEXT` is not enough (`lane_waits`, `inbound_unread`, `signed_out`)
    text: str | None = None


@dataclass(frozen=True)
class AccountDay:
    """One account's side of the queue, read once by `prepare`: today's limit, what it has used of it, when it may
    send next, and whether its own row is paused (a hard block) or signed out."""
    address: str
    account_id: int
    limit: int
    sent_today: int
    next_send: datetime | None
    paused: bool = False
    signed_out: bool = False

    @property
    def block(self) -> str | None:
        """The account's own reason to send nothing: its pause first, then its sign-in."""
        return "sender_paused" if self.paused else "signed_out" if self.signed_out else None


@dataclass(frozen=True)
class Route:
    """The account a message goes from (its address), or None with the sentence that says what it waits for."""
    account: str | None
    text: str | None = None


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


# One send to a company: when, whether it was a first email, and the account it went from.
Send = tuple[datetime, bool, int]


def _company_sends(conn: sqlite3.Connection, companies: dict[int, sqlite3.Row],
                   since: str) -> dict[int, list[Send]]:
    """When each surviving company was sent to from `since` on, each send with whether it was a first email (the
    per-company limit counts only those; the gap counts every send) and its account (a touch with none is account
    1's). A claimed message that may have gone out (`sending`, `send_unknown`) counts at its claim time: better one
    wait too many than one send too many."""
    sends: dict[int, list[Send]] = {}
    for row in conn.execute(
            "SELECT p.company_id, m.seq, COALESCE(t.account_id, 1) AS account_id, "
            "CASE m.state WHEN 'sent' THEN m.sent_at ELSE m.claimed_at END AS at "
            "FROM message m JOIN touch t ON t.touch_id = m.touch_id JOIN person p ON p.person_id = t.person_id "
            "WHERE m.state IN ('sent', 'sending', 'send_unknown') AND at >= ?", (since,)):
        survivor = resolve_company(companies, row["company_id"])[-1]
        sends.setdefault(survivor, []).append((_utc(row["at"]), row["seq"] == 0, row["account_id"]))
    return sends


def window_close(now: datetime, zone: ZoneInfo | None, cfg: SendConfig) -> datetime:
    """The moment the recipient's window closes on the local date it is at `now` (`window_end` there; New York when
    the zone is unknown). Readiness checks the recipient's own date, so this does too: a Tokyo recipient at 19:00
    Monday in New York is on Tuesday and closes Tuesday 15:00 Tokyo time."""
    zone = zone or ZoneInfo(cfg.day_tz)
    return _local(now.astimezone(zone).date(), cfg.window_end, zone)


# The waits that still end inside today's window: the message goes today if its wait ends before the window closes.
_STILL_TODAY = frozenset({"spacing", "per_company_gap", "window"})


def _same(address: str | None, account: str) -> bool:
    return address is not None and address.lower() == account.lower()


def _windows_today(verdicts: Iterable[Verdict], queued: Iterable[Queued], now: datetime, cfg: SendConfig,
                   account: str | None) -> list[tuple[datetime, datetime]]:
    """The window (open, close) of each recipient that can still be sent today, on the recipient's own local date
    (see `minutes_left_for`); New York's alone when there is none."""
    by_id = {q.message_id: q for q in queued}
    out = []
    for v in verdicts:
        q = by_id.get(v.message_id)
        if q is None or (v.reason != "ready" and v.reason not in _STILL_TODAY):
            continue
        if account is not None and not _same(v.account, account):
            continue
        zone = cal.recipient_zone(q.tz)
        if zone is None:
            continue
        close = window_close(now, zone, cfg)
        if v.reason != "ready" and (v.earliest_at is None or v.earliest_at > close):
            continue
        out.append((_local(now.astimezone(zone).date(), cfg.window_start, zone), close))
    if not out:
        zone = ZoneInfo(cfg.day_tz)
        out.append((_local(now.astimezone(zone).date(), cfg.window_start, zone), window_close(now, None, cfg)))
    return out


def minutes_left_for(verdicts: Iterable[Verdict], queued: Iterable[Queued], now: datetime, cfg: SendConfig,
                     account: str | None = None) -> float:
    """Minutes from `now` to the latest window close among the recipients that can still be sent today: a message
    that is `ready`, or that waits only on time (`spacing`, `per_company_gap`, `window` not yet open) and whose wait
    ends before its window closes. A follow-up due later, a paused or held company, a company at its daily limit
    and a closed window do not count. With `account` (an address), only that account's messages count. With none,
    the close in New York. Never below 0."""
    now = now.astimezone(timezone.utc)
    close = max(c for _, c in _windows_today(verdicts, queued, now, cfg, account))
    return max((close - now).total_seconds() / 60, 0.0)


def day_minutes_for(verdicts: Iterable[Verdict], queued: Iterable[Queued], now: datetime, cfg: SendConfig,
                    account: str | None = None) -> float:
    """The account's full sending window today, in minutes, for the floor of `human.spread_gap`: from the earliest
    window open (`window_start`, local) to the latest close among the same recipients `minutes_left_for` counts, in
    absolute time. New York only is 08:00 to 15:00, 420; Los Angeles only is 11:00 to 18:00 New York, 420; the two
    mixed is 08:00 to 18:00 New York, 600. Never below 0."""
    now = now.astimezone(timezone.utc)
    windows = _windows_today(verdicts, queued, now, cfg, account)
    opens, close = min(o for o, _ in windows), max(c for _, c in windows)
    return max((close - opens).total_seconds() / 60, 0.0)


def minutes_left(conn: sqlite3.Connection, now: datetime, cfg: SendConfig, account: str,
                 accounts: Mapping[str, AccountRow]) -> float:
    """How long the account's day still has, for `human.spread_gap`: `minutes_left_for` over the account's own
    messages in the approved queue as the sender sees it at `now`. `account` is its address; `accounts` are the
    accounts in play, as for `verdicts`."""
    return window_minutes(conn, now, cfg, account, accounts)[0]


def window_minutes(conn: sqlite3.Connection, now: datetime, cfg: SendConfig, account: str,
                   accounts: Mapping[str, AccountRow]) -> tuple[float, float]:
    """`minutes_left` and `day_minutes_for` from one read of the queue: how long the account's day still has, and
    how long its whole window is today."""
    found = verdicts(conn, now, cfg, accounts, day=None, gate=None)
    items = load_queue(conn, cfg)
    return (minutes_left_for(found, items, now, cfg, account), day_minutes_for(found, items, now, cfg, account))


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
    sends: dict[int, list[Send]]
    # The first sending account's day (else the first account's), for the callers that read one account (tests).
    sent_today: int
    limit: int
    next_send: datetime | None
    paces: dict[int, CompanyPace] = field(default_factory=dict)  # each queued company's size band (#229)
    days: dict[str, AccountDay] = field(default_factory=dict)  # every account in play, by address
    routes: dict[int, Route] = field(default_factory=dict)  # message id to the account it goes from
    senders: tuple[str, ...] = ()  # the accounts a projection places mail for, in config order
    paused: bool = False  # his pause (sender row 1): it stops every account
    # The accounts whose inbound mail was not fully read (id to address), and each surviving company with a touch
    # on one of them (to that account's address).
    unread: dict[int, str] = field(default_factory=dict)
    unread_companies: dict[int, str] = field(default_factory=dict)


_BAD_AUTH = ("signed_out", "wrong_account", "app_closed")


def _account_day(conn: sqlite3.Connection, account: AccountRow, today: date, cfg: SendConfig,
                 day: DayState | None, *, own_block: bool) -> AccountDay:
    """The account's day. `own_block`: read its pause and sign-in from its row (else the caller's gate says them)."""
    limit = ramp.effective_limit(conn, account, today, cfg)
    if day is not None and day.day == today:
        limit = min(limit, day.effective_limit)  # the tick's snapshot can only be older, so it can only be higher
    # The ramp counts only what is sent; the limit also counts the account's claimed messages that may have gone
    # out. An email that bounced does not use a slot (#224). A touch with no account is account 1's.
    sent_today = signals.counted_sends_on(conn, account.account_id, today, cfg) + conn.execute(
        "SELECT COUNT(*) FROM message m JOIN touch t ON t.touch_id = m.touch_id "
        "WHERE m.state IN ('sending', 'send_unknown') AND COALESCE(t.account_id, 1) = ? AND m.claimed_at >= ? "
        "AND m.claimed_at < ?", (account.account_id, *cal.ny_day_bounds(today, cfg))).fetchone()[0]
    return AccountDay(address=account.cfg.address, account_id=account.account_id, limit=limit,
                      sent_today=sent_today, next_send=_utc(account.next_send_at) if account.next_send_at else None,
                      paused=own_block and account.paused,
                      signed_out=own_block and account.auth_state in _BAD_AUTH)


def _route(conn: sqlite3.Connection, cfg: SendConfig, q: Queued, routed: tuple[AccountRow | None, str]) -> Route:
    return route_line(conn, cfg, q.touch_id, q.company_id, routed)


def route_line(conn: sqlite3.Connection, cfg: SendConfig, touch_id: int, company_id: int,
               routed: tuple[AccountRow | None, str]) -> Route:
    """`accounts.route`'s answer for one touch (`routed`; `company_id` is its surviving company), with the sentence
    for an email no sending account can take: it names the account it waits for. Read only. The queue and the
    Outbox's "from" line share it."""
    account, why = routed
    if account is not None and not account.cfg.sends:  # a thread on an account that rests
        return Route(None, f"Waits: its thread is on {account.cfg.address}, which only reads now.")
    if why == "thread_missing":
        row = conn.execute("SELECT a.address, COALESCE(t.account_id, 1) AS account_id FROM touch t LEFT JOIN "
                           "sender_account a ON a.account_id = COALESCE(t.account_id, 1) WHERE t.touch_id = ?",
                           (touch_id,)).fetchone()
        address = row["address"] or f"account {row['account_id']}"
        return Route(None, f"Waits: its thread is on {address}, which is not set up to send now.")
    if account is None:
        lane = _accounts.lane_of(conn, company_id)
        named = next((a for a in cfg.accounts if a.lane == lane), None)
        if named is None:
            return Route(None, "Waits: no account is set up to send to this company yet.")
        if not named.sends:
            return Route(None, f"Waits for {named.address}, which does not send yet.")
        return Route(None, f"Waits for {named.address}, which is not set up to send yet.")
    return Route(account.cfg.address)


def _unread(conn: sqlite3.Connection, companies: dict[int, sqlite3.Row],
            account_ids: frozenset[int]) -> tuple[dict[int, str], dict[int, str]]:
    """The unread accounts by id (to their address), and each surviving company with a touch on one of them. A
    touch is on an account when it names it, or, with none, when it was sent (a thread or a first send): then it is
    account 1's, as everywhere else. A touch not sent yet is on no account."""
    if not account_ids:
        return {}, {}
    marks = ",".join("?" * len(account_ids))
    ids = sorted(account_ids)
    names = {row[0]: row[1] for row in conn.execute(
        f"SELECT account_id, address FROM sender_account WHERE account_id IN ({marks})", ids)}
    unread = {a: names.get(a, f"account {a}") for a in ids}
    held: dict[int, str] = {}
    on = ("COALESCE(t.account_id, CASE WHEN t.first_sent_at IS NOT NULL OR ifnull(t.gmail_thread_id, '') <> '' "
          "THEN 1 END)")
    for row in conn.execute(
            f"SELECT p.company_id, {on} AS on_account FROM touch t JOIN person p ON p.person_id = t.person_id "
            f"WHERE {on} IN ({marks}) ORDER BY on_account, t.touch_id", ids):
        held.setdefault(resolve_company(companies, row["company_id"])[-1], unread[row["on_account"]])
    return unread, held


DaySnapshots = DayState | Mapping[str, DayState | None] | None


def _snapshot(day: DaySnapshots, address: str, first: str | None) -> DayState | None:
    """The tick's snapshot of one account's day: by address, or one bare DayState for the first account in play."""
    if day is None or isinstance(day, DayState):
        return day if address == first else None
    return day.get(address)


def prepare(conn: sqlite3.Connection, now: datetime, cfg: SendConfig,
            accounts: Mapping[str, AccountRow], *, day: DaySnapshots,
            unread: frozenset[int] = frozenset()) -> Prepared:
    """The queue and the facts around it, read once. `accounts`, `day` and `unread` as for `verdicts`."""
    rows, companies = _rows(conn)
    now = now.astimezone(timezone.utc)
    today = cal.ny_date(now, cfg)
    day_start, _ = cal.ny_day_bounds(today, cfg)
    gap = human.max_company_gap(cfg)  # how far back a company's last send can still hold the next one
    queued = _sorted([_queued(row, companies) for row in rows], cfg)
    paused = bool(conn.execute("SELECT paused FROM sender WHERE sender_id = 1").fetchone()["paused"])
    first = next(iter(accounts), None)
    days = {}
    for row in accounts.values():
        row = _accounts.by_id(conn, cfg, row.account_id) or row  # its pause, sign-in and spacing as stored now
        days[row.cfg.address] = _account_day(conn, row, today, cfg, _snapshot(day, row.cfg.address, first),
                                             own_block=True)
    routed = _accounts.routes(conn, cfg, [q.touch_id for q in queued], dict(accounts))
    routes = {q.message_id: _route(conn, cfg, q, routed[q.touch_id]) for q in queued}
    senders = tuple(row.cfg.address for row in accounts.values() if row.cfg.sends)
    lead = senders[0] if senders else first
    own = days.get(lead) if lead is not None else None
    own = own or AccountDay(address="", account_id=0, limit=0, sent_today=0, next_send=None)
    unread_accounts, unread_companies = _unread(conn, companies, frozenset(unread))
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
        sent_today=own.sent_today, limit=own.limit, next_send=own.next_send,
        days=days, routes=routes, senders=senders, paused=paused, unread=unread_accounts,
        unread_companies=unread_companies)


def decide_all(p: Prepared, now: datetime, cfg: SendConfig, *, gate: str | None, pending: list[Queued],
               sends: dict[int, list[Send]],
               sent_by_day: Mapping[str, Mapping[date, int]] | None = None,
               next_send: Mapping[str, datetime | None] | None = None) -> list[Verdict]:
    """One verdict per message of `pending` (in its order) as of `now`, with `sends` (every account's, by company),
    each account's sent count (`sent_by_day`: address to New York date to count) and each account's next free moment
    (`next_send`, by address) as given; each defaults to what `p` read. `verdicts` passes what the store says; a
    projection passes what it has placed so far. Each account's limit is `p.days`'."""
    now = now.astimezone(timezone.utc)
    today = cal.ny_date(now, cfg)
    if sent_by_day is None:
        sent_by_day = {a: {today: d.sent_today} for a, d in p.days.items()}
    if next_send is None:
        next_send = {a: d.next_send for a, d in p.days.items()}
    # A message that blocks a recruiter is one that could go out: not a stop, with an address, a thread and an
    # account, due.
    live_others = {q.company_id for q in pending if q.role_kind != "recruiter" and q.message_id not in p.stops
                   and q.message_id not in p.no_thread and q.message_id not in p.behind
                   and p.routes[q.message_id].account is not None
                   and q.to and not (q.due_on is not None
                                     and now < _due_start(q.due_on, cal.recipient_zone(q.tz), cfg))}
    day_begins = _utc(cal.ny_day_bounds(today, cfg)[0])
    tomorrow = _local(cal.add_business_days(today, 1, cfg), cfg.window_start, ZoneInfo(cfg.day_tz))
    opens_at = _local(today, cfg.window_start, ZoneInfo(cfg.day_tz))
    own: dict[str, tuple[bool, datetime | None, datetime | None, str | None]] = {}
    for address, d in p.days.items():
        used = sent_by_day.get(address, {}).get(today, 0)
        # The account's first send of the day waits a different few minutes each day after the window opens, seeded
        # by the account (`human.day_start_offset`).
        opens = opens_at + human.day_start_offset(today, cfg, d.account_id) if used == 0 else None
        own[address] = (used >= d.limit, opens, next_send.get(address), d.block)
    out = []
    for q in pending:
        route = p.routes[q.message_id]
        if route.account is None:
            day_full, day_opens, free_at, block, account_id = False, None, None, None, None
        else:
            day_full, day_opens, free_at, block = own[route.account]
            account_id = p.days[route.account].account_id
        if p.paused:
            block = "sender_paused"  # his pause stops every account
        # The account's own mailbox, else one that emailed this company, was not fully read.
        unread = p.unread.get(account_id) if account_id is not None else None
        unread = unread or p.unread_companies.get(q.company_id)
        reason, at = _decide(
            q, p.stops, gate, p.companies[q.company_id], q.company_id in p.held, now, cfg, today, day_full,
            sends.get(q.company_id, []), day_begins, day_opens, tomorrow, free_at, q.company_id in live_others,
            no_thread=q.message_id in p.no_thread, behind=q.message_id in p.behind,
            not_before=p.not_before.get(q.message_id),
            company_pace=p.paces.get(q.company_id) or pace.default_pace(cfg), block=block,
            no_account=route.account is None, account_id=account_id, unread=unread is not None)
        text = route.text if reason == "lane_waits" else None
        if reason == "inbound_unread":
            text = f"Waits: replies on {unread} could not be read yet."
        if reason == "signed_out" and route.account is not None:
            text = signed_out_text(route.account)
        out.append(Verdict(q.message_id, reason, at, account=route.account, text=text))
    return out


def verdicts(conn: sqlite3.Connection, now: datetime, cfg: SendConfig,
             accounts: Mapping[str, AccountRow], *, day: DaySnapshots,
             gate: str | None, unread: frozenset[int] = frozenset()) -> list[Verdict]:
    """One verdict per approved message, in queue order, each with the account that sends it.
    `accounts` are the accounts in play (`accounts.sync`, or `accounts.in_play` for a reader), by address: each
    message goes from the one `accounts.route` picks, and its daily limit, spacing, pause and sign-in (as its row
    says) are that account's (a message none can take waits as `lane_waits`). `day` is the tick's snapshot of each
    account's day, by address (a bare DayState is the first account's), or None.
    `gate` (`sender_paused`, `signed_out`, `old_sender_running`, `message_id_unproven`) is a reason the whole tick
    knows; it applies to every message that is not a stop.
    `unread` are the ids of the accounts whose inbound mail this tick did not fully read (or failed to apply): a
    message from one of them, or to a company with a touch on one of them (merged companies count), waits as
    `inbound_unread`, with a sentence naming the account; every other message goes on.
    Order of reasons: stops; the gate; the account's own block (his pause, the account's pause, its sign-in);
    inbound unread; company paused; company held (a hold on any company merged into the survivor counts); no
    address; a follow-up with no Gmail thread; no account can take it; follow-up not due; an earlier follow-up in
    the thread not gone out; bad time zone; not a business day; the account's daily limit; per-company limit;
    per-company gap; recipient's window; recruiter waits; the account's spacing."""
    p = prepare(conn, now, cfg, accounts, day=day, unread=unread)
    if not p.queue:
        return []
    return decide_all(p, now, cfg, gate=gate, pending=p.queue, sends=p.sends)


def _decide(q: Queued, stops: dict, gate: str | None, company: sqlite3.Row, held: bool, now: datetime, cfg: SendConfig,
            today: date, day_full: bool, sends: list[Send], day_begins: datetime,
            day_opens: datetime | None, tomorrow: datetime, next_send: datetime | None, others_queued: bool, *,
            no_thread: bool = False, behind: bool = False,
            not_before: date | None = None, company_pace: CompanyPace | None = None, block: str | None = None,
            no_account: bool = False, account_id: int | None = None,
            unread: bool = False) -> tuple[str, datetime | None]:
    company_pace = company_pace or pace.default_pace(cfg)
    if q.message_id in stops:
        return stops[q.message_id], None
    if gate is not None:
        return gate, None
    if block is not None:
        return block, None
    if unread:
        return "inbound_unread", None
    if company["paused_at"] is not None:
        return "company_paused", None
    if held:
        return "company_held", None
    if not q.to:
        return "no_address", None
    if no_thread:
        return "no_thread", None
    if no_account:
        return "lane_waits", None
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
    # limit and the gap are the company's size band (#229), counted over every account's sends. A company never gets
    # first emails from two accounts on one New York day (multi-sender review focus 3): once one account sent (or
    # claimed) a first email there today, a first email from another account waits for the next sending day.
    if q.seq == 0:
        firsts = [aid for at, first, aid in sends if first and at >= day_begins]
        if len(firsts) >= company_pace.daily or (account_id is not None and any(a != account_id for a in firsts)):
            return "per_company_limit", tomorrow
    last = max((at for at, _, _ in sends), default=None)
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


def signed_out_text(address: str) -> str:
    """A `signed_out` verdict's own sentence: it names the account, since another may be signed in and sending."""
    return f"Waits: {address} is not signed in."


def first_only(verdicts: list[Verdict], account: str | None = None) -> list[Verdict]:
    """The verdicts while one first email may go before Gmail is proven to keep the sender's marker (`proof`): the
    first `ready` message in queue order (else the time wait that ends first) keeps its verdict, and every other
    message that is `ready` or waits on time waits as `message_id_unproven` instead. Stops and the waits that need an
    action from him (a paused or held company, no address, no thread, a bad time zone, a recruiter waiting) keep
    theirs. With `account` (an address) only that account's messages are looked at; the proof is per account."""
    def mine(v: Verdict) -> bool:
        return account is None or _same(v.account, account)

    movable = [v for v in verdicts if mine(v) and (v.reason == "ready" or v.reason in TIME_REASONS)]
    first = next((v for v in movable if v.reason == "ready"), None)
    if first is None:
        timed = [(v.earliest_at, i) for i, v in enumerate(movable) if v.earliest_at is not None]
        first = movable[min(timed)[1]] if timed else None
    return [v if v is first or not mine(v) or not (v.reason == "ready" or v.reason in TIME_REASONS)
            else dataclasses.replace(v, reason="message_id_unproven", earliest_at=None, text=None) for v in verdicts]


def gate_accounts(verdicts: list[Verdict], gates: Mapping[str | None, str | None],
                  first_send: Iterable[str] = ()) -> list[Verdict]:
    """The waits a caller knows per account and the queue cannot read from the store: this tick's sign-in, the old
    sender, the marker proof. `gates` maps an address (any case; None for the messages no account can take) to the
    reason its messages wait for; it goes in front of every reason but a stop and a pause (`sender_paused`), as the
    one gate did when there was one account. Then each account in `first_send` (its proof's first send: nothing
    claimed yet) lets its first email go alone (`first_only` on that account's messages)."""
    folded = {(k.lower() if k is not None else None): g for k, g in gates.items()}
    out = []
    for v in verdicts:
        gate = folded.get(v.account.lower() if v.account is not None else None)
        if gate is not None and v.reason not in STOP_REASONS and v.reason != "sender_paused":
            text = signed_out_text(v.account) if gate == "signed_out" and v.account is not None else None
            v = dataclasses.replace(v, reason=gate, earliest_at=None, text=text)
        out.append(v)
    for address in first_send:
        out = first_only(out, address)
    return out


def pick(verdicts: list[Verdict], queue: list[Queued], rng: random.Random | None = None,
         pool: int = 1, *, now: datetime | None = None, cfg: SendConfig | None = None,
         account: str) -> Queued | None:
    """A ready message of `account` (an address), or None. With no `rng` it is the first in queue order; with one it
    is any of the first `pool` ready messages, so the order is not strictly first come, first served
    (`human.choose`). Only that account's ready messages are candidates: an account sends only its own mail."""
    ready = {v.message_id for v in verdicts if v.reason == "ready" and _same(v.account, account)}
    candidates = [q for q in queue if q.message_id in ready]
    if now is not None and cfg is not None:
        # Only the ready messages whose window closes first (absolute time, today's date) are candidates, in queue order:
        # a New York recipient goes before a Los Angeles one, so nobody is stranded when their window closes.
        def close(q: Queued) -> datetime:
            return window_close(now, cal.recipient_zone(q.tz), cfg)
        if candidates:
            earliest = min(close(q) for q in candidates)
            candidates = [q for q in candidates if close(q) == earliest]
    return human.choose(candidates, rng, pool) if rng is not None else (candidates[0] if candidates else None)
