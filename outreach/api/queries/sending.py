"""The sending strip and the global pause: the sender's own snapshot on the wire, and his pause and resume.

`SendingStatus` is `outreach.sending.status.sending_snapshot` reshaped, never a second reading of the sender's
rules; health's one-line sender summary reads the same snapshot. The one thing the store cannot say is whether the
old mailer's launchd job is still loaded, so the app passes that in (`old_sender_check`).

A pause is his (`pause_kind 'by_you'`). A plain resume clears only his pause: a hard block (a Gmail limit, a hard
bounce rate, a rewritten Message-ID) stays until a resume says `acknowledgeBlock`, and the refusal shows its reason.
A pause never overwrites a hard block, so a plain resume can never clear one.
"""
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone

from outreach.api.errors import ApiProblem
from outreach.api.idempotency import WriteCtx
from outreach.api.models.health import SenderSummary
from outreach.api.models.outbox import NextSend, SendingSignals, SendingStatus, SendingStepDown, SendingWindow
from outreach.api.revisions import bump
from outreach.drafts.followups import canonical_company
from outreach.sending import calendar as cal
from outreach.sending import signals
from outreach.sending.config import SendConfig, load_config
from outreach.sending.status import SendingSnapshot, sending_snapshot
from outreach.sending.status import _gate as tick_gate  # the one gate the queue and the strip share
from outreach.store.timefmt import ISO_FMT
from outreach.store.writes import append_event

log = logging.getLogger("outreach.api")

NOT_SAVED = "Nothing was saved."
PAUSED_TEXT = "Paused by you"
STEP_DOWN_KINDS = signals.WARNING_KINDS + signals.HARD_KINDS


def moment(now: str) -> datetime:
    return datetime.strptime(now, ISO_FMT).replace(tzinfo=timezone.utc)


def old_sender_check() -> Callable[[], bool]:
    """Whether the old mailer's launchd job is loaded, asked the way the sender's own `status` command asks
    (`launchctl print`). When it cannot be asked, the answer is yes: the tick takes it as loaded too, so the strip
    never says running while the sender waits."""
    def loaded() -> bool:
        from outreach.sending.__main__ import launchctl_loaded  # stdlib only; loaded on first use, not at import
        try:
            return bool(launchctl_loaded(load_config().old_sender_label)())
        except Exception as err:  # noqa: BLE001 - any failure to ask is "loaded", the sender's safe answer
            log.warning("Could not tell whether the old mail sender is loaded (%s); showing it as loaded.",
                        type(err).__name__)
            return True
    return loaded


def _step_down(conn: sqlite3.Connection, snap: SendingSnapshot, cfg: SendConfig) -> SendingStepDown | None:
    """The snapshot names the day and the reason; the time is that day's newest warning or block signal (else the
    day's start in New York), so the page never reads a bare date as midnight UTC."""
    if snap.last_step_down is None:
        return None
    day, reason = snap.last_step_down
    row = conn.execute(
        f"SELECT max(at) FROM event WHERE entity = 'sender' AND kind IN ({','.join('?' * len(STEP_DOWN_KINDS))}) "
        "AND json_extract(detail, '$.day') = ?", (*STEP_DOWN_KINDS, day)).fetchone()
    at = row[0] or cal.ny_day_bounds(date.fromisoformat(day), cfg)[0]
    return SendingStepDown(at=at, reason=reason)


def _next_send(conn: sqlite3.Connection, snap: SendingSnapshot) -> NextSend | None:
    if snap.next_send is None:
        return None
    message_id, at, tz = snap.next_send
    row = conn.execute("SELECT m.touch_id, p.name, p.company_id FROM message m JOIN touch t ON t.touch_id = m.touch_id "
                       "JOIN person p ON p.person_id = t.person_id WHERE m.message_id = ?", (message_id,)).fetchone()
    company = conn.execute("SELECT name FROM company WHERE company_id = ?",
                           (canonical_company(conn, row["company_id"]),)).fetchone()
    return NextSend(at=at, tz=tz, touch_id=row["touch_id"], person_name=row["name"], company_name=company["name"])


def sending_status(conn: sqlite3.Connection, now: str, cfg: SendConfig, *, old_sender_loaded: bool) -> SendingStatus:
    """The strip, from the sender's snapshot at `now`. Read only."""
    snap = sending_snapshot(conn, moment(now), cfg, old_sender_loaded=old_sender_loaded)
    return SendingStatus(
        state=snap.state, paused_reason=snap.paused_reason, paused_at=snap.paused_at, gmail_message=snap.gmail_message,
        sent_today=snap.sent_today, limit_today=snap.limit_today, ramp_level=snap.ramp_level,
        ramp_levels=list(snap.ramp_levels), next_limit_if_clean=snap.next_limit_if_clean,
        last_step_down=_step_down(conn, snap, cfg),
        signals=SendingSignals(bounce_rate_3d=snap.bounce_rate_3d, gmail_errors_today=snap.gmail_errors_today,
                               stop_replies_today=snap.stop_replies_today),
        window=SendingWindow(start=cfg.window_start.strftime("%H:%M"), end=cfg.window_end.strftime("%H:%M")),
        next_send=_next_send(conn, snap), none_reason=snap.none_reason, revision=snap.revision)


@dataclass(frozen=True)
class SenderRead:
    """One read of the sender for a page that needs both its line and its day's room: the snapshot (None when the
    sending config cannot be loaded, which means nothing is being sent) and the tick's own gate, the reason the
    whole tick sends nothing (`sender_paused`, `signed_out`, `old_sender_running`, `message_id_unproven`)."""

    snapshot: SendingSnapshot | None
    gate: str | None


def read_sender(conn: sqlite3.Connection, now: str, *, old_sender_loaded: bool) -> SenderRead:
    try:
        cfg = load_config()
    except (ValueError, OSError) as err:
        log.warning("The sending config could not be loaded: %s", err)
        return SenderRead(None, None)
    return SenderRead(sending_snapshot(conn, moment(now), cfg, old_sender_loaded=old_sender_loaded),
                      tick_gate(conn, old_sender_loaded))  # the tick's own order, never a copy of it


def summary_of(read: SenderRead) -> SenderSummary:
    """Health's sender line: the snapshot's state, today's sends and today's limit. No snapshot (a sending config the
    sender cannot load) reads as stopped (`paused_hard_block`, 0 of 0) instead of failing the whole view."""
    if read.snapshot is None:
        return SenderSummary(state="paused_hard_block", sent_today=0, limit_today=0)
    return SenderSummary(state=read.snapshot.state, sent_today=read.snapshot.sent_today,
                         limit_today=read.snapshot.limit_today)


def sender_summary(conn: sqlite3.Connection, now: str, *, old_sender_loaded: bool) -> SenderSummary:
    return summary_of(read_sender(conn, now, old_sender_loaded=old_sender_loaded))


# ---------------------------------------------------------------------------------------------- writes

def _sender(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute("SELECT * FROM sender WHERE sender_id = 1").fetchone()


def _event(ctx: WriteCtx, kind: str, detail: dict) -> None:
    append_event(ctx.conn, entity="sender", entity_id=1, kind=kind, actor="devanshu", at=ctx.now, detail=detail,
                 idempotency_key=ctx.event_key(0))


def pause_sending(ctx: WriteCtx, revision: int, cfg: SendConfig, *, old_sender_loaded: bool) -> SendingStatus:
    """His pause. Already paused (by him or by a hard block) writes nothing and answers with the status: the
    sender is already stopped, and a hard block must never turn into a pause a plain resume could clear."""
    if not _sender(ctx.conn)["paused"]:
        bump(ctx.conn, "sender", "sender_id", 1, revision,
             {"paused": 1, "pause_kind": "by_you", "paused_reason": PAUSED_TEXT, "paused_at": ctx.now}, at=ctx.now)
        _event(ctx, "paused", {"text": "Sending paused by you"})
    return sending_status(ctx.conn, ctx.now, cfg, old_sender_loaded=old_sender_loaded)


def resume_sending(ctx: WriteCtx, revision: int, acknowledge_block: bool, cfg: SendConfig, *,
                   old_sender_loaded: bool) -> SendingStatus:
    """Clears his pause. A hard block is cleared only with `acknowledge_block`; without it the refusal carries the
    block's reason. Not paused writes nothing. The revision is checked first in every case."""
    row = _sender(ctx.conn)
    if row["revision"] != revision:
        raise ApiProblem("conflict", f"Sending changed since you loaded it. {NOT_SAVED}")
    if row["paused"]:
        hard = row["pause_kind"] == "hard_block"
        if hard and not acknowledge_block:
            reason = row["paused_reason"] or "The sender stopped sending to protect the account."
            raise ApiProblem("not_allowed", f"Sending stopped on a hard block. {reason} Confirm that you read this "
                                            f"to resume. {NOT_SAVED}")
        bump(ctx.conn, "sender", "sender_id", 1, revision,
             {"paused": 0, "pause_kind": None, "paused_reason": "", "paused_at": None}, at=ctx.now)
        _event(ctx, "resumed", {"text": "Sending resumed by you", "cleared": row["pause_kind"],
                                "reason": row["paused_reason"], "acknowledged": bool(acknowledge_block and hard)})
    return sending_status(ctx.conn, ctx.now, cfg, old_sender_loaded=old_sender_loaded)
