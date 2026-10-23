"""The quiet sweeper: a touch with no outcome `quiet_after_days` after it was sent is marked quiet (#33 ruling 4).

`sweep_quiet` is the rule on one connection. `sweep_pass` is one run against the store in the settings: a busy store
skips that run with a warning, and any other failure is logged, so the API never stops because of the sweeper.
`run_forever` is what the app starts from its lifespan when `settings.sweeper` is on: a pass at startup, then one
an hour, each in a worker thread so SQLite never blocks the event loop.
"""
import logging
import sqlite3

import anyio

from outreach.api.db import connect_for, is_busy
from outreach.api.queries.outcomes import open_touches
from outreach.api.settings import Settings
from outreach.outcomes.prompt import PromptConfig, should_mark_quiet
from outreach.store import atomic, record_outcome

log = logging.getLogger("outreach.api")

INTERVAL_SECONDS = 3600


def sweep_quiet(conn: sqlite3.Connection, now: str, cfg: PromptConfig) -> int:
    """Mark every touch past `cfg.quiet_after_days` with no outcome as quiet (actor `system`), in one write unit.
    A touch with any outcome, or undone, is never touched. Returns how many it marked."""
    marked = 0
    with atomic(conn, "sweep_quiet"):
        for row in open_touches(conn, now, cfg.quiet_after_days):
            if should_mark_quiet(row, now, cfg):
                record_outcome(conn, row["touch_id"], kind="quiet", at=now, revision=row["revision"], actor="system")
                marked += 1
    return marked


def sweep_pass(settings: Settings, now: str, cfg: PromptConfig) -> int | None:
    """One sweep on its own connection. Returns how many touches it marked, or None when the store was busy (the
    next pass tries again)."""
    try:
        conn = connect_for(settings)
        try:
            marked = sweep_quiet(conn, now, cfg)
        finally:
            conn.close()
    except sqlite3.OperationalError as err:
        if not is_busy(err):
            raise
        log.warning("The store was busy, so the quiet sweep skipped this pass.")
        return None
    if marked:
        log.info("Marked %d touches quiet.", marked)
    return marked


def _safe_pass(settings: Settings, now: str, cfg: PromptConfig) -> None:
    try:
        sweep_pass(settings, now, cfg)
    except Exception:  # the sweeper must never take the API down; the next pass tries again
        log.exception("The quiet sweep failed; it runs again in an hour.")


async def run_forever(settings: Settings, clock, cfg: PromptConfig, interval: float = INTERVAL_SECONDS) -> None:
    """A pass now, then one every `interval` seconds, until cancelled. A pass already running finishes first."""
    while True:
        await anyio.to_thread.run_sync(_safe_pass, settings, clock(), cfg)
        await anyio.sleep(interval)
