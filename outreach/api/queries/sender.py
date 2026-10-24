"""The sender's state for a status line: derived from the `sender` row, today's sends and the clock."""
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from outreach.api.models.health import SenderState, SenderSummary
from outreach.sending import calendar, ramp, signals
from outreach.sending.config import SendConfig, load_config
from outreach.store.timefmt import ISO_FMT


def sender_state(conn: sqlite3.Connection, now: datetime, cfg: SendConfig) -> SenderState:
    """What a person should be told. A pause or a sign-in problem wins (it needs an action), then a day off, then
    the hours. The window is read on the New York clock (the sender's own day)."""
    row = conn.execute("SELECT paused, pause_kind, auth_state FROM sender WHERE sender_id = 1").fetchone()
    if row is not None:
        if row["paused"] and row["pause_kind"] == "hard_block":
            return "paused_hard_block"
        if row["paused"]:
            return "paused_by_you"
        if row["auth_state"] in ("signed_out", "wrong_account"):
            return "signed_out"
    local = now.astimezone(ZoneInfo(cfg.day_tz))
    if not calendar.is_business_day(local.date(), cfg):
        return "weekend"
    if not cfg.window_start <= local.time() <= cfg.window_end:
        return "outside_window"
    return "running"


def sender_summary(conn: sqlite3.Connection, now: str) -> SenderSummary:
    """State, how many went out today and today's limit (0 on a day the sender does not run)."""
    cfg = load_config()
    moment = datetime.strptime(now, ISO_FMT).replace(tzinfo=timezone.utc)
    today = calendar.ny_date(moment, cfg)
    return SenderSummary(
        state=sender_state(conn, moment, cfg),
        sent_today=signals.sends_on(conn, today, cfg),
        limit_today=ramp.effective_limit(conn, today, cfg) if calendar.is_business_day(today, cfg) else 0,
    )
