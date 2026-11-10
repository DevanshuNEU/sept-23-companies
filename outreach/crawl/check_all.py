"""Check now (#194, design 4.3): the crawl job reads every Greenhouse and Ashby board when Devanshu asks.

The API records a `check_now_requested` event and starts the crawl job; `run_tick` calls `run_pending_check` before
its own pass and once more after it, so a request made while a tick runs is served when that tick ends. A check is one
`read_boards` job run over `select_check`, through the tick's `_read_boards` and `_Pass`, so it keeps every outage
rule, the breaker and the polite spacing; only the selection and the budget differ. Its budget is `check_budget_s`
from its own start; `tick_budget_s` and `boards_per_tick` do not apply. Its job summary carries `"mode": "check_all"`
and `"requested_at"`.

The check claims the newest waiting request by event id (`request_event_id` in the progress row,
`check_state.PROGRESS_KEY`), and with it every older one. Progress is written on this thread at most once a second,
and once more with `finished_at` in a `finally`, so a check that fails still ends and never reads as running.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import time
from collections.abc import Callable, Mapping

from outreach.crawl.ats import reading_ats
from outreach.crawl.boardstate import BoardRow
from outreach.crawl.cadence import add_seconds, due_boards, plan_company_ids
from outreach.crawl.config import ATS_HOSTS, CrawlConfig
from outreach.crawl.polite import PoliteClient
from outreach.crawl.tick import _Pass, _read_boards, plan_date
from outreach.pipeline import check_state
from outreach.store import atomic, describe_error

__all__ = ["select_check", "run_pending_check", "write_progress", "PROGRESS_EVERY_S"]

PROGRESS_EVERY_S = 1.0  # the progress row is written at most this often while a check runs
_NO_LIMIT = 2 ** 62  # due_boards wants a limit; a check reads every due board its budget allows


def select_check(conn: sqlite3.Connection, now: str, cfg: CrawlConfig) -> list[BoardRow]:
    """The boards a check reads, in order. First every active board of a live company on a `check_ats` ATS whose
    reader is on, except a board read in the last `check_skip_recent_s` seconds and an erroring board still inside its
    backoff (its last read an error and its `next_read_at` not yet come): never-read boards first, then the oldest
    `last_read_at`, then board id. Then the boards `due_boards` returns for the other ATSs the crawler reads."""
    check_ats = sorted(a for a in set(cfg.check_ats) & reading_ats(cfg))
    columns = ", ".join(f"b.{f.name}" for f in dataclasses.fields(BoardRow))
    rows = conn.execute(
        f"SELECT {columns} FROM board b JOIN company c ON c.company_id = b.company_id "
        "WHERE b.active = 1 AND c.status NOT IN ('closed', 'removed') AND c.merged_into IS NULL "
        "AND b.ats IN (SELECT value FROM json_each(:ats)) "
        "AND (b.last_read_at IS NULL OR b.last_read_at <= :recent) "
        "AND NOT (b.last_read IS 'error' AND b.next_read_at IS NOT NULL AND b.next_read_at > :now) "
        "ORDER BY (b.last_read_at IS NULL) DESC, b.last_read_at, b.board_id",
        {"ats": json.dumps(check_ats), "now": now, "recent": add_seconds(now, -cfg.check_skip_recent_s)}).fetchall()
    others = reading_ats(cfg) - set(check_ats)
    due = due_boards(conn, now, supported=others, plan_ids=plan_company_ids(conn, plan_date(now)), cfg=cfg,
                     limit=_NO_LIMIT) if others else []
    return [BoardRow(*r) for r in rows] + due


def write_progress(conn: sqlite3.Connection, value: Mapping[str, object]) -> None:
    """Store the progress row (`check_state.PROGRESS_KEY`) as JSON, in its own short transaction."""
    with atomic(conn, "check_progress"):
        conn.execute("INSERT INTO pipeline_meta (key, value) VALUES (?, ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                     (check_state.PROGRESS_KEY, json.dumps(dict(value), sort_keys=True)))


def _ats_name(bucket: str) -> str:
    """The ATS a host bucket belongs to (`greenhouse.io` is `greenhouse`); the bucket itself when none matches."""
    for ats, suffix in ATS_HOSTS.items():
        if bucket == suffix or bucket.endswith("." + suffix):
            return ats
    return bucket


class _Progress:
    """The progress row of one check, written on the thread that owns the store."""

    def __init__(self, conn: sqlite3.Connection, request: check_state.Request, started_at: str,
                 clock: Callable[[], float]):
        self.conn, self.clock = conn, clock
        self.run: _Pass | None = None
        self.last_write: float | None = None
        self.value: dict[str, object] = {"run_id": None, "checked": 0, "total": 0, "new": 0,
                                         "started_at": started_at, "finished_at": None, "paused_hosts": [],
                                         "request_event_id": request.event_id}

    def seen(self, run: _Pass, total: int, taken: int) -> None:
        """`_read_boards`' `on_take`: write when the first call comes (the run and its total are known) and then at
        most every `PROGRESS_EVERY_S` seconds."""
        self.run = run
        self.value.update(run_id=run.run_id, total=total, checked=taken)
        now = self.clock()
        if self.last_write is None or now - self.last_write >= PROGRESS_EVERY_S:
            self.last_write = now
            self._write()

    def finish(self, finished_at: str) -> None:
        self.value["finished_at"] = finished_at
        self._write()

    def _write(self) -> None:
        if self.run is not None:
            run = self.run
            # The polite breaker's own trips reach `run.down` only when the pass finishes; ask it now too.
            down = run.down | {b for b in run.seen if run.polite.tripped(b)}
            self.value.update(new=run.counts["new"], paused_hosts=sorted({_ats_name(b) for b in down}))
        write_progress(self.conn, self.value)


def run_pending_check(conn: sqlite3.Connection, polite: PoliteClient, *, cfg: CrawlConfig, rules_cfg, combiner,
                      now_fn: Callable[[], str], clock: Callable[[], float] = time.monotonic, switched_off: int = 0,
                      workers: Mapping[str, int] | None = None) -> tuple[int | None, str]:
    """Run a check when a request waits (`check_state.pending_request`). Returns the check's run id (None when no
    request waits, or the check failed before its boards were selected) and the error text when it failed. A failure is the
    check's, not the tick's: the progress row still gets its `finished_at` and the request stays claimed."""
    request = check_state.pending_request(conn)
    if request is None:
        return None, ""
    progress = _Progress(conn, request, now_fn(), clock)
    try:
        try:
            report, _ = _read_boards(
                conn, polite, lambda now: select_check(conn, now, cfg), cfg=cfg, rules_cfg=rules_cfg,
                combiner=combiner, now_fn=now_fn, clock=clock, started=clock(), rebuilt=False,
                switched_off=switched_off, workers=workers, budget_s=cfg.check_budget_s,
                summary_extra={"mode": "check_all", "requested_at": request.at}, on_take=progress.seen)
        finally:
            progress.finish(now_fn())
    except Exception as e:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        return progress.value["run_id"], describe_error(e)
    return report.run_id, ""
