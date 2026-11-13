"""The sender's status and the queue's schedule, for the API (#11) and the daily plan (#15) to read.

Read only: nothing here writes, opens a transaction or calls Gmail. The snapshot is what the Outbox's sending strip
shows; `queue_schedule` is the same `verdicts` the tick uses, over the same accounts (`accounts.in_play`), with the
same waits (his pause and the old sender for every account; each account's own pause, sign-in and marker proof for
its own messages) and the same one first email per account while its proof is open (`queue.gate_accounts`), so
"when it will go" on the Outbox is the sender's own answer and never a second copy of the rules. A message waiting
on the proof reads the line for where its account's first email stands (`reason_text`).

The snapshot lists every account (`AccountSnapshot`, Task A11): what it does today in a few plain words (sending with
its count, paused, signed out with its own sign-in command, reading replies only, not sending yet). Today's sent count
and limit are sums over the accounts that send; the ramp, the step down and the bounce rate are the lead account's
(the first that sends). His pause stays on `sender` row 1 and holds every account.

The store cannot say whether the old mailer's launchd job is loaded (only a `launchctl` call can), so a caller that
knows passes `old_sender_loaded=True`; the snapshot then shows `paused_by_you` with a reason, and every message
waits as `old_sender_running`, exactly as the tick treats it.
"""
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from outreach.sending import calendar as cal
from outreach.sending import accounts, projection, proof, queue, ramp, signals
from outreach.sending.config import AccountConfig, SendConfig
from outreach.sending.queue import REASON_TEXT, STOP_REASONS, Verdict
from outreach.store.timefmt import ISO_FMT

__all__ = ["SendingSnapshot", "AccountSnapshot", "ACCOUNT_STATES", "sending_snapshot", "queue_schedule",
           "queue_projection", "reason_text", "sender_gate", "lead", "OLD_SENDER_TEXT", "NOTHING_APPROVED"]

OLD_SENDER_TEXT = "The old mail sender is still running, so the new one waits until it is stopped."
NOTHING_APPROVED = "Nothing is approved and waiting to send."
ALL_STOPPED = "Every email waiting is stopped and will be removed."
_GMAIL_TEXT = {"gmail_limit": "Gmail said the account hit a sending limit",
               "gmail_policy": "Gmail said this account may not send"}


# What one account does today, as the strip names it.
ACCOUNT_STATES = ("sending", "paused_by_you", "paused_hard_block", "signed_out", "reading_only", "not_sending_yet")


@dataclass(frozen=True)
class AccountSnapshot:
    """One account on the strip. `text` is its line after the address ("4 of 15 today", "reading replies only");
    `limit` is today's effective limit (0 for an account that does not send, and on a day the sender does not run)."""
    address: str
    lane: str | None
    sends: bool
    state: str  # ACCOUNT_STATES
    text: str
    paused: bool
    pause_reason: str
    paused_at: str | None
    auth_state: str
    sent_today: int
    limit: int


@dataclass(frozen=True)
class SendingSnapshot:
    state: str  # the web SenderState values
    paused_reason: str
    paused_at: str | None
    gmail_message: str | None
    sent_today: int
    limit_today: int
    start_limit: int
    ramp_level: int
    ramp_levels: tuple[int, ...]
    next_limit_if_clean: int
    last_step_down: tuple[str, str] | None
    bounce_rate_3d: float
    gmail_errors_today: int
    stop_replies_today: int
    imported_unverified: int
    next_send: tuple[int, str, str] | None
    none_reason: str | None
    revision: int
    accounts: tuple[AccountSnapshot, ...] = ()


def _stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime(ISO_FMT)


_BAD_AUTH = ("signed_out", "wrong_account")


def _gate(conn: sqlite3.Connection, *, ignore_pause: bool = False) -> str | None:
    """The reason every account gives for sending nothing: his pause (sender row 1). Each account's own pause,
    sign-in, the old sender and its proof are its own messages' waits (`_account_gates`), in the tick's order.
    `ignore_pause` leaves his pause out, to ask what would block the sender once he resumes."""
    paused = conn.execute("SELECT paused FROM sender WHERE sender_id = 1").fetchone()["paused"]
    return "sender_paused" if paused and not ignore_pause else None


def sender_gate(conn: sqlite3.Connection, cfg: SendConfig, old_sender_loaded: bool) -> str | None:
    """The reason the whole sender sends nothing now, in the tick's order, or None when some account may send: his
    pause; else, when every sending account is held, the first one's reason (its pause, its sign-in, the old sender,
    its marker proof not open). The old sender holds a store with no sending account too."""
    gate = _gate(conn)
    if gate is not None:
        return gate
    reasons = []
    for row in accounts.sending(accounts.in_play(conn, cfg)):
        if accounts.pause_of(conn, row.account_id) is not None:
            reasons.append("sender_paused")
        elif row.auth_state in _BAD_AUTH:
            reasons.append("signed_out")
        elif old_sender_loaded:
            reasons.append("old_sender_running")
        elif proof.phase(conn, row.account_id) not in proof.OPEN:
            reasons.append("message_id_unproven")
        else:
            return None
    return reasons[0] if reasons else ("old_sender_running" if old_sender_loaded else None)


def _account_gates(conn: sqlite3.Connection, rows: dict[str, accounts.AccountRow], old_sender_loaded: bool
                   ) -> tuple[dict[str | None, str | None], list[str]]:
    """Each account's own waits in the tick's order: its sign-in as its row says (`signed_out`; a fresh store's
    `unknown` is not a block, the first tick finds out), the old sender, then its marker proof: an account whose
    proof is not open waits as `message_id_unproven`, and one at its first send lets its first email go alone."""
    gates: dict[str | None, str | None] = {None: "old_sender_running" if old_sender_loaded else None}
    first: list[str] = []
    for address, row in rows.items():
        stage = proof.phase(conn, row.account_id)
        if row.auth_state in _BAD_AUTH:
            gates[address] = "signed_out"
        elif old_sender_loaded:
            gates[address] = "old_sender_running"
        elif stage not in proof.OPEN:
            gates[address] = "message_id_unproven"
        elif stage == proof.FIRST_SEND:
            first.append(address)
    return gates, first


def lead(rows: dict[str, accounts.AccountRow]) -> accounts.AccountRow:
    """The account a one-account reader shows: the first that sends, else the first."""
    return next(iter(accounts.sending(rows)), None) or next(iter(rows.values()))


def reason_text(reason: str, stage: str, text: str | None = None) -> str:
    """The sentence for a queue reason: the verdict's own `text` when it has one (`lane_waits` names the account it
    waits for), else `message_id_unproven` says where the first email stands (`stage`, a `proof` phase), else the
    reason's own line."""
    if text:
        return text
    if reason == "message_id_unproven":
        return proof.PHASE_TEXT.get(stage, REASON_TEXT[reason])
    return REASON_TEXT[reason]


def queue_schedule(conn: sqlite3.Connection, now: datetime, cfg: SendConfig, *,
                   old_sender_loaded: bool = False) -> dict[int, Verdict]:
    """The tick's verdict for every approved message, by message id, in queue order: the reason it waits (or
    `ready`) and, for a wait that ends by itself, when. While Gmail is unproven and no email is claimed yet, only
    the first email keeps its own verdict, as in the tick (the marker proof, `proof`)."""
    rows = accounts.in_play(conn, cfg)
    days = {row.cfg.address: ramp.today_state(conn, row, now, cfg) for row in accounts.sending(rows)}
    gate = _gate(conn)
    found = queue.verdicts(conn, now, cfg, rows, day=days, gate=gate)
    if gate is None:
        found = queue.gate_accounts(found, *_account_gates(conn, rows, old_sender_loaded))
    return {v.message_id: v for v in found}


def queue_projection(conn: sqlite3.Connection, now: datetime, cfg: SendConfig, *,
                     old_sender_loaded: bool = False) -> tuple[dict[int, datetime], bool]:
    """When each approved message is expected to send (`projection.project`), and whether the sender is paused.
    A pause is not a reason to show nothing: the projection runs as if he resumed now. A sign-in problem, the old
    sender and an unproven Gmail marker (the first email goes alone) are: the queue's own sentence says what is wrong, so nothing is projected."""
    rows = accounts.in_play(conn, cfg)
    senders = accounts.sending(rows)
    paused = bool(senders) and all(accounts.pause_of(conn, row.account_id) is not None for row in senders)
    if old_sender_loaded:
        return {}, paused
    # Only the accounts that send freely are projected: a sign-in problem or an unproven marker (the first email
    # goes alone) is the queue's own sentence for that account's mail, so its messages are not projected.
    open_rows = {address: row for address, row in rows.items()
                 if row.auth_state not in _BAD_AUTH and proof.phase(conn, row.account_id) == proof.PROVEN}
    days = {row.cfg.address: ramp.today_state(conn, row, now, cfg) for row in accounts.sending(open_rows)}
    prepared = queue.prepare(conn, now, cfg, open_rows, day=days)
    return projection.project(prepared, now, cfg), paused


_NO_TOKEN = "no Gmail token"  # how the tick's auth_error starts for an account that has never signed in


def _sign_in_line(row: accounts.AccountRow, auth_error: str, cfg: SendConfig) -> str:
    """The line for an account whose sign-in fails, with its own sign-in command (`gmail.sign_in_command`)."""
    from outreach.sending.gmail import sign_in_command  # standard library only
    command = sign_in_command(cfg, row.cfg.address)
    if row.auth_state == "wrong_account":
        what = "signed in as another account"
    elif (auth_error or "").startswith(_NO_TOKEN):
        what = "not signed in yet"
    else:
        what = "signed out"
    if not row.cfg.sends:
        what += ", so its replies are not read"
    return f"{what}. Run: {command}"


def _account_snapshot(conn: sqlite3.Connection, row: accounts.AccountRow, now: datetime, cfg: SendConfig
                      ) -> AccountSnapshot:
    """One account's line, in the tick's order for a sending account: a pause (its own hard block first, then his
    button on sender row 1), then its sign-in, then its count. An account that does not send reads replies (Gmail)
    or waits (Mail.app until it sends); a resting account whose sign-in fails says its replies are not read."""
    today = cal.ny_date(now, cfg)
    pause = accounts.pause_of(conn, row.account_id)
    own = conn.execute("SELECT auth_state, auth_error FROM sender_account WHERE account_id = ?",
                       (row.account_id,)).fetchone()
    auth_error = own["auth_error"] if own is not None else ""
    business = cal.is_business_day(today, cfg)
    sent = signals.counted_sends_on(conn, row.account_id, today, cfg)
    limit = ramp.effective_limit(conn, row, today, cfg) if row.cfg.sends and business else 0
    count = f"{sent} of {limit} today" if business else "no sending today"
    signed_out = row.auth_state in _BAD_AUTH
    if not row.cfg.sends:
        if row.cfg.kind != "gmail":
            state, text = "not_sending_yet", "waits, not sending yet"
        elif signed_out:
            state, text = "signed_out", _sign_in_line(row, auth_error, cfg)
        else:
            state, text = "reading_only", "reading replies only"
    elif pause is not None and pause.kind == "hard_block":
        state, text = "paused_hard_block", f"paused: hard block, {count}"
    elif pause is not None:
        state, text = "paused_by_you", f"paused by you, {count}"
    elif signed_out:
        state, text = "signed_out", _sign_in_line(row, auth_error, cfg)
    else:
        state, text = "sending", count
    return AccountSnapshot(
        address=row.cfg.address, lane=row.cfg.lane, sends=row.cfg.sends, state=state, text=text,
        paused=pause is not None, pause_reason=pause.reason if pause is not None else "",
        paused_at=pause.at if pause is not None else None, auth_state=row.auth_state, sent_today=sent, limit=limit)


def _state(conn: sqlite3.Connection, lines: tuple[AccountSnapshot, ...], now: datetime, cfg: SendConfig,
           old_sender_loaded: bool) -> tuple[str, str, str | None]:
    """The sender's state, its reason line and when it paused. A hard block on sender row 1 (an older store) wins,
    then every sending account held by its own hard block (or, all held, the first one blocked), then his pause, then
    every sending account signed out, then the old sender, then a day off, then the hours (read on the New York
    clock). One account held while another sends is the held account's own line, not the sender's state."""
    row = conn.execute("SELECT paused, pause_kind, paused_reason, paused_at FROM sender WHERE sender_id = 1").fetchone()
    senders = [a for a in lines if a.sends]
    held = [a for a in senders if a.state in ("paused_hard_block", "signed_out")]
    blocked = next((a for a in held if a.state == "paused_hard_block"), None)
    if row["paused"] and row["pause_kind"] == "hard_block":
        return "paused_hard_block", row["paused_reason"], row["paused_at"]
    if senders and len(held) == len(senders) and blocked is not None:
        return "paused_hard_block", blocked.pause_reason, blocked.paused_at
    if row["paused"]:
        return "paused_by_you", row["paused_reason"], row["paused_at"]
    if senders and len(held) == len(senders):
        first = held[0]
        error = conn.execute("SELECT auth_error FROM sender_account WHERE address = ?", (first.address,)).fetchone()
        detail = error["auth_error"] if error is not None and first.auth_state == "wrong_account" else ""
        detail = f" {detail[:1].upper()}{detail[1:]}." if detail else ""
        return "signed_out", f"{first.address}: {first.text}.{detail}", None
    if old_sender_loaded:
        return "paused_by_you", OLD_SENDER_TEXT, None
    local = now.astimezone(ZoneInfo(cfg.day_tz))
    if not cal.is_business_day(local.date(), cfg):
        return "weekend", "", None
    if not cfg.window_start <= local.time() <= cfg.window_end:
        return "outside_window", "", None
    return "running", "", None


def _levels(acct: AccountConfig) -> tuple[int, ...]:
    return tuple(range(acct.ramp_floor, acct.ramp_ceiling + 1, acct.ramp_step))


def _gmail_message(conn: sqlite3.Connection, account_id: int, today: str) -> tuple[int, str | None]:
    """How many Gmail limit or policy signals belong to today, and a plain line for the newest."""
    rows = conn.execute(
        "SELECT kind, json_extract(detail, '$.error_kind') AS error_kind, json_extract(detail, '$.status') AS status "
        "FROM event WHERE entity = 'sender' AND entity_id = ? AND kind IN ('gmail_limit', 'gmail_policy') "
        "AND json_extract(detail, '$.day') = ? ORDER BY event_id DESC", (account_id, today)).fetchall()
    if not rows:
        return 0, None
    newest = rows[0]
    text = _GMAIL_TEXT[newest["kind"]]
    extra = ", ".join(part for part in (newest["error_kind"], f"HTTP {newest['status']}" if newest["status"] else "")
                      if part)
    return len(rows), f"{text} ({extra})" if extra else text


def _last_step_down(conn: sqlite3.Connection, account_id: int) -> tuple[str, str] | None:
    row = conn.execute("SELECT day, outcome, outcome_note FROM send_day WHERE account_id = ? "
                       "AND outcome IN ('warning', 'hard_block') ORDER BY day DESC LIMIT 1", (account_id,)).fetchone()
    if row is None:
        return None
    if row["outcome_note"]:
        return row["day"], row["outcome_note"]
    kinds = signals.WARNING_KINDS + signals.HARD_KINDS
    event = conn.execute(
        f"SELECT kind FROM event WHERE entity = 'sender' AND entity_id = ? AND kind IN ({','.join('?' * len(kinds))}) "
        "AND json_extract(detail, '$.day') = ? ORDER BY event_id DESC LIMIT 1",
        (account_id, *kinds, row["day"])).fetchone()
    return row["day"], event["kind"] if event is not None else row["outcome"]


def _stop_replies(conn: sqlite3.Connection, today, cfg: SendConfig) -> int:
    """Email touches tagged `asked_to_stop` whose reply is dated today (New York), from the touch or from its
    `outreach_touch` (an undone one does not count); a touch tagged in both counts once."""
    start, end = cal.ny_day_bounds(today, cfg)
    return conn.execute(
        "SELECT COUNT(*) FROM ("
        " SELECT touch_id FROM touch WHERE reply_kind = 'asked_to_stop' AND replied_at >= ? AND replied_at < ?"
        " UNION"
        " SELECT email_touch_id FROM outreach_touch WHERE email_touch_id IS NOT NULL"
        "  AND reply_kind = 'asked_to_stop' AND undone_at IS NULL AND replied_at >= ? AND replied_at < ?)",
        (start, end, start, end)).fetchone()[0]


def _next_send(schedule: dict[int, Verdict], queued: dict[int, queue.Queued], now: datetime,
               stages: dict[str | None, str]):
    """The message that goes first and when (`ready` is now), else why none does."""
    best = None
    for index, verdict in enumerate(schedule.values()):
        when = now if verdict.reason == "ready" else verdict.earliest_at
        if when is not None and (best is None or (when, index) < best[0]):
            best = ((when, index), verdict.message_id)
    if best is not None:
        when, message_id = best[0][0], best[1]
        return (message_id, _stamp(when), queued[message_id].tz), None
    if not schedule:
        return None, NOTHING_APPROVED
    waiting = next((v for v in schedule.values() if v.reason not in STOP_REASONS), None)
    if waiting is None:
        return None, ALL_STOPPED
    return None, reason_text(waiting.reason, stages.get(waiting.account, proof.PROVEN), waiting.text)


def sending_snapshot(conn: sqlite3.Connection, now: datetime, cfg: SendConfig, *,
                     old_sender_loaded: bool = False) -> SendingSnapshot:
    """Everything the sending strip shows, read from the store: one line per account, today's sent count and limit
    summed over the accounts that send (each at its effective limit: its day's row clamped to its ramp, else what
    `limit_for` will give it; 0 on a day the sender does not run), and the lead account's ramp and signals."""
    sender = conn.execute("SELECT * FROM sender WHERE sender_id = 1").fetchone()
    today = cal.ny_date(now, cfg)
    rows = accounts.in_play(conn, cfg)
    lines = tuple(_account_snapshot(conn, row, now, cfg) for row in rows.values())
    senders = [a for a in lines if a.sends]
    state, reason, paused_at = _state(conn, lines, now, cfg, old_sender_loaded)
    account = lead(rows)
    acct = account.cfg
    row = conn.execute("SELECT send_limit FROM send_day WHERE account_id = ? AND day = ?",
                       (account.account_id, today.isoformat())).fetchone()
    start = row["send_limit"] if row is not None else ramp.limit_for(conn, account, today, cfg)
    levels = _levels(acct)
    level = max((i for i, value in enumerate(levels, start=1) if value <= start), default=1)
    errors, gmail_message = 0, None
    blocked = next((a for a in senders if a.state == "paused_hard_block"), None)
    for sending in accounts.sending(rows) or [account]:
        n, text = _gmail_message(conn, sending.account_id, today.isoformat())
        errors += n
        if text is not None and (gmail_message is None or (blocked is not None
                                                            and sending.cfg.address == blocked.address)):
            gmail_message = text  # the blocked account's own message wins
    schedule = queue_schedule(conn, now, cfg, old_sender_loaded=old_sender_loaded)
    queued = {q.message_id: q for q in queue.load_queue(conn, cfg)}
    stages = {address: proof.phase(conn, r.account_id) for address, r in rows.items()}
    next_send, none_reason = _next_send(schedule, queued, now, stages)
    return SendingSnapshot(
        state=state, paused_reason=reason, paused_at=paused_at, gmail_message=gmail_message,
        sent_today=sum(a.sent_today for a in senders), limit_today=sum(a.limit for a in senders),
        start_limit=start, ramp_level=level, ramp_levels=levels,
        next_limit_if_clean=min(start + acct.ramp_step, acct.ramp_ceiling),
        last_step_down=_last_step_down(conn, account.account_id),
        bounce_rate_3d=signals.bounce_rate_3d(conn, account.account_id, today, cfg), gmail_errors_today=errors,
        stop_replies_today=_stop_replies(conn, today, cfg),
        imported_unverified=conn.execute(
            "SELECT COUNT(*) FROM event WHERE entity = 'message' AND kind = 'import_not_in_sent'").fetchone()[0],
        next_send=next_send, none_reason=none_reason, revision=sender["revision"], accounts=lines)
