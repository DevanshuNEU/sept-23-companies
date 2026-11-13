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

The row's `new` is what the job list would mark New: postings this check inserted that the list shows
(`joblist.LISTED_SQL`), not a first read of their board (`seen_gap_s` set) and not a repost. The job run's own `new`
still counts every row it inserted.

A host that tripped stays paused for `defer_s`: a `check_ats` ATS the last check listed in `paused_hosts` is not read
again until `defer_s` after the check that tripped it (`paused_at`, kept as it was by the checks that skip it), and it
is listed in `paused_hosts` again meanwhile, so the line still says it is paused.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import time
from collections.abc import Callable, Mapping

from outreach.crawl.ats import reading_ats
from outreach.crawl.base import PLAIN_ICIMS_SQL
from outreach.crawl.boardstate import BoardRow
from outreach.crawl.cadence import add_seconds, due_boards, plan_company_ids
from outreach.crawl.config import ATS_HOSTS, CrawlConfig
from outreach.crawl.polite import PoliteClient
from outreach.crawl.tick import _Pass, _read_boards, plan_date, resolve_combiner
from outreach.pipeline import check_state
from outreach.pipeline.joblist import LISTED_SQL, SOFTWARE_SQL, listed_params
from outreach.store import atomic, describe_error

__all__ = ["select_check", "held_ats", "run_pending_check", "write_progress", "PROGRESS_EVERY_S"]

PROGRESS_EVERY_S = 1.0  # the progress row is written at most this often while a check runs
_NO_LIMIT = 2 ** 62  # due_boards wants a limit; a check reads every due board its budget allows


def held_ats(last: Mapping | None, now: str, cfg: CrawlConfig) -> dict[str, str]:
    """The `check_ats` ATSs a check leaves alone, each with the time it was paused: those the last check (`last`, the
    progress row) listed in `paused_hosts`, while `defer_s` has not passed since they were paused (`paused_at`, or the
    check's `finished_at` for a row without it). A check that has not ended holds nothing."""
    if not last or not isinstance(last.get("finished_at"), str):
        return {}
    since = last.get("paused_at") if isinstance(last.get("paused_at"), dict) else {}
    out: dict[str, str] = {}
    for ats in last.get("paused_hosts") or []:
        at = since.get(ats, last["finished_at"])
        if ats not in cfg.check_ats or not isinstance(at, str):
            continue
        try:
            if add_seconds(at, cfg.defer_s) > now:
                out[ats] = at
        except ValueError:  # not a canonical time: hold nothing for it
            continue
    return out


def select_check(conn: sqlite3.Connection, now: str, cfg: CrawlConfig, *,
                 held: Mapping[str, str] | None = None) -> list[BoardRow]:
    """The boards a check reads, in order. First every active board of a live company on a `check_ats` ATS whose
    reader is on and that is not `held` (default: `held_ats` of the stored progress row), except a board read in the
    last `check_skip_recent_s` seconds and an erroring board still inside its backoff (its last read an error and its
    `next_read_at` not yet come): never-read boards first, then the oldest `last_read_at`, then board id. Then the
    boards `due_boards` returns for the other ATSs the crawler reads (never a held one)."""
    held = held_ats(check_state.progress(conn), now, cfg) if held is None else held
    check_ats = sorted(a for a in set(cfg.check_ats) & reading_ats(cfg) if a not in held)
    columns = ", ".join(f"b.{f.name}" for f in dataclasses.fields(BoardRow))
    rows = conn.execute(
        f"SELECT {columns} FROM board b JOIN company c ON c.company_id = b.company_id "
        "WHERE b.active = 1 AND c.status NOT IN ('closed', 'removed') AND c.merged_into IS NULL "
        f"AND b.ats IN (SELECT value FROM json_each(:ats)) AND NOT {PLAIN_ICIMS_SQL} "
        "AND (b.last_read_at IS NULL OR b.last_read_at <= :recent) "
        "AND NOT (b.last_read IS 'error' AND b.next_read_at IS NOT NULL AND b.next_read_at > :now) "
        "ORDER BY (b.last_read_at IS NULL) DESC, b.last_read_at, b.board_id",
        {"ats": json.dumps(check_ats), "now": now, "recent": add_seconds(now, -cfg.check_skip_recent_s)}).fetchall()
    others = reading_ats(cfg) - set(cfg.check_ats)
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


_NEW_SQL = ("SELECT count(*) FROM posting p JOIN decision d ON d.posting_id = p.posting_id "
            "AND d.ruleset_version = (SELECT ruleset_version FROM ruleset WHERE is_current = 1) "
            f"WHERE p.posting_id > :floor AND p.seen_gap_s IS NOT NULL AND p.repost_of IS NULL AND {LISTED_SQL} "
            f"AND {SOFTWARE_SQL}")


def _listed_new(conn: sqlite3.Connection, floor: int, listed: Mapping[str, str]) -> int:
    """Postings above id `floor` that the job list would mark New: listed (`LISTED_SQL` with `listed`, from
    `listed_params`), not a first read and not a repost. One query over the rows above `floor`, by primary key."""
    return conn.execute(_NEW_SQL, {"floor": floor, **listed}).fetchone()[0]


class _Progress:
    """The progress row of one check, written on the thread that owns the store. `floor` is the highest posting id
    when the check began: only the crawler writes postings, and it holds the crawl lock, so every posting above it is
    this check's. `held` are the paused ATSs it leaves alone (`held_ats`)."""

    def __init__(self, conn: sqlite3.Connection, request: check_state.Request, started_at: str,
                 clock: Callable[[], float], held: Mapping[str, str]):
        self.conn, self.clock, self.held = conn, clock, dict(held)
        self.run: _Pass | None = None
        self.last_write: float | None = None
        self.floor = conn.execute("SELECT coalesce(max(posting_id), 0) FROM posting").fetchone()[0]
        self.listed: dict[str, str] | None = None  # read at the first write: no-go and passed lists move slowly
        self.tripped: set[str] = set()
        self.value: dict[str, object] = {"run_id": None, "checked": 0, "total": 0, "new": 0,
                                         "started_at": started_at, "finished_at": None,
                                         "paused_hosts": sorted(self.held), "request_event_id": request.event_id}

    def seen(self, run: _Pass, total: int, taken: int) -> None:
        """`_read_boards`' `on_take`: write when the first call comes (the run and its total are known) and then at
        most every `PROGRESS_EVERY_S` seconds. These writes are advisory: one that fails with a store error (the
        store busy, say) is counted in `progress_write_errors` and the check reads on; the next write or the final
        one catches up."""
        self.run = run
        self.value.update(run_id=run.run_id, total=total, checked=taken)
        now = self.clock()
        if self.last_write is None or now - self.last_write >= PROGRESS_EVERY_S:
            self.last_write = now
            try:
                self._write()
            except sqlite3.Error:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
                self.value["progress_write_errors"] = int(self.value.get("progress_write_errors") or 0) + 1

    def finish(self, finished_at: str) -> None:
        """The final write, with `finished_at` and `paused_at` (a held ATS keeps the time it was first paused).
        Strict: a failure here is the check's error."""
        self.value["finished_at"] = finished_at
        self._sync()
        self.value["paused_at"] = {**{ats: finished_at for ats in self.tripped}, **self.held}
        write_progress(self.conn, self.value)

    def _sync(self) -> None:
        if self.run is not None:
            run = self.run
            # The polite breaker's own trips reach `run.down` only when the pass finishes; ask it now too.
            down = run.down | {b for b in run.seen if run.polite.tripped(b)}
            self.tripped = {_ats_name(b) for b in down}
        if self.listed is None:
            self.listed = listed_params(self.conn)
        self.value.update(new=_listed_new(self.conn, self.floor, self.listed),
                          paused_hosts=sorted(self.tripped | set(self.held)))

    def _write(self) -> None:
        self._sync()
        write_progress(self.conn, self.value)


def run_pending_check(conn: sqlite3.Connection, polite: PoliteClient, *, cfg: CrawlConfig, rules_cfg=None,
                      combiner=None, now_fn: Callable[[], str], clock: Callable[[], float] = time.monotonic,
                      switched_off: int = 0, workers: Mapping[str, int] | None = None) -> tuple[int | None, str]:
    """Run a check when a request waits (`check_state.pending_request`). Returns the check's run id (None when no
    request waits, or the check failed before its boards were selected) and the error text when it failed. A failure
    is the check's, not the tick's: the progress row still gets its `finished_at` and the request stays claimed.
    `combiner=None` means `resolve_combiner(conn)`, asked only when a request waits."""
    request = check_state.pending_request(conn)
    if request is None:
        return None, ""
    combiner = combiner if combiner is not None else resolve_combiner(conn)
    started_at = now_fn()
    held = held_ats(check_state.progress(conn), started_at, cfg)
    progress = _Progress(conn, request, started_at, clock, held)
    try:
        try:
            report, _ = _read_boards(
                conn, polite, lambda now: select_check(conn, now, cfg, held=held), cfg=cfg, rules_cfg=rules_cfg,
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
