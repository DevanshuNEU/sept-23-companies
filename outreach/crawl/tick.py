"""The 30-minute tick: read the due boards within a time budget, then the watched pages (#21, Task 12).

One tick is one `read_boards` job run. It rebuilds first when the store's ruleset is not this code's, so a rebuild
and a crawl never overlap; `tick_lock` keeps two ticks from running at once. Boards come from `due_boards` in its
fixed order (today's plan, then never read, then the oldest due), and once `tick_budget_s` has passed no new board
is listed and no listed board still waiting for the writer is written, so a Mac that slept for days reads what
matters first and leaves the rest due. A board whose ATS's reader is
switched off in config.toml (`ats_off`, #46) is never due: nothing is sent, deferred or written for it, so its state,
postings and decisions stay as they are until the reader is on again. The report counts such boards in `switched_off`.

Every request goes through one `PoliteClient`. An outage is not a finding about any one board (Review Focus 3), so:

- a board whose request the breaker refuses is deferred (`next_read_at = now + defer_s`), with no `board_read` and no
  streak change;
- an `error` read is held back until the end of the tick. The tick also counts, per host bucket, error reads in a
  row that the polite breaker does not see as host failures (a challenge page or another wrong-shape 200, a blanket
  401 or 400; a 404 or 410 is about the board and is not counted), from boards whose last read was good only. When
  that count reaches `breaker_failures` the tick trips the bucket itself. At the end, a held error from a bucket that
  tripped either way becomes a deferral when that board is shielded: its last read was good (`ok`, `unchanged` or
  `empty`) and is younger than `outage_grace_s`. A board that read fine and now fails with its whole host is caught
  in the outage. A never-read or already-erroring board's error is written as the error read it is, so a handful of
  broken boards that sort together can never trip their bucket tick after tick, defer each other forever and starve
  the healthy boards behind them; they back off 6, 12, 24 hours, spread out, and reach their own items (or the
  host's one item, hostblock.py). The grace does the same for a host that blocks for good: after a day its boards'
  errors are written, so the host reaches Needs you instead of being deferred forever.
- a well-formed `empty` read from a shielded board that has open postings is held too. When `MASS_EMPTY_BOARDS` or
  more of them come from one bucket in one tick, that is an outage (an API answering an empty list for everyone), not
  that many boards emptied at once: they become deferrals, so nothing closes and no `board_empty` item is raised.
  Fewer are written as the empty reads they are, and the two-empties rule applies.

A board whose ingest raises (our bug, not the board's error) is recorded as `failed`, keeps its streaks and waits
the backoff its next error would get; the job then ends `partial`. `PRAGMA wal_checkpoint(PASSIVE)` runs after
every tick, whatever happened.
"""
from __future__ import annotations

import contextlib
import fcntl
import importlib
import json
import os
import sqlite3
import time
import urllib.parse
from collections.abc import Callable, Collection, Iterator, Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from outreach.crawl import fanout
from outreach.crawl.ats import adapter_for, reading_ats
from outreach.crawl.base import Adapter, JobRef, ReadResult, Validators
from outreach.crawl.boardstate import BoardRow
from outreach.crawl.cadence import add_seconds, due_boards, error_backoff_s, plan_company_ids, switched_off_boards
from outreach.crawl.config import CrawlConfig, load_crawl_config
from outreach.crawl.fanout import Listed
from outreach.crawl.ingest import full_read_due, read_board
from outreach.crawl.polite import HostTripped, PoliteClient, bucket_for
from outreach.crawl.probe import PROBE_LIMIT, ProbeReport, probe_slugs
from outreach.crawl.watch import WatchReport, watch_pages
from outreach.net import HttpClient, Response
from outreach.outcomes.windows import ny_day
from outreach.pipeline import alarms, alerts
from outreach.pipeline.config import load_pipeline_config
from outreach.pipeline.simplify_feed import note_streak, poll as poll_simplify_feed
from outreach.rules import RulesetNotCurrent, rebuild, ruleset_version
from outreach.store import append_event, atomic, describe_error, finish_job, start_job, utcnow
from outreach.sending.notify import Notifier, notify_once
from outreach.store.timefmt import ISO_FMT

__all__ = ["TickReport", "AlreadyRunning", "LOCK_NAME", "tick_lock", "ensure_ruleset_current", "resolve_combiner",
           "plan_date", "run_tick", "read_company_boards"]

LOCK_NAME = "crawl.lock"
# Today's plan is Devanshu's day: the daily list is dated in New York, like the send days.
PLAN_TZ = ZoneInfo("America/New_York")
# An error read with one of these statuses is about the board (moved, closed), never a sign the host is down.
_BOARD_STATUSES = frozenset({404, 410})
# A board whose last read was one of these can be caught in an outage; any other board's error is its own.
_GOOD_LAST = frozenset({"ok", "unchanged", "empty"})
# Empty reads of boards with open postings, from one bucket in one tick, that make a mass-empty outage.
MASS_EMPTY_BOARDS = 5
# The keys of a `by_ats` row (`profile` reads them by name, #152).
_PROFILE_KEYS = ("boards", "list_requests", "list_ms", "body_requests", "body_ms", "ingest_ms", "new", "closed",
                 "reopened")
_MAX_ERROR_LINES = 20  # board failures named in job_run.error; the count is always complete
_RULESET_CHANGED = "the ruleset changed during the tick; the next tick rebuilds first"


@dataclass(frozen=True)
class TickReport:
    """What one tick did, and the `read_boards` job summary (`tripped` a list, `probe` and `watch` objects or null).
    `read` counts the boards whose read was recorded (ok, empty, error, unchanged); `deferred` the boards put off
    with no read recorded; `tripped` the host buckets the breaker stopped or that read a mass empty. The plan's
    `queued` is gone (ruling 2). `switched_off` counts the boards left alone because their ATS's reader is switched
    off in config.toml (#46): not read, not deferred, nothing about them written. `by_ats` says where the tick's time
    went (#152): per ATS, the boards listed, the list requests and milliseconds, the body requests and milliseconds,
    the milliseconds of everything else a read wrote, and the postings it found new, closed and reopened. A board's
    list is counted when it is made, so a read held for the end of the tick is counted once."""
    run_id: int | None
    rebuilt: bool
    read: int = 0
    ok: int = 0
    empty: int = 0
    error: int = 0
    unchanged: int = 0
    deferred: int = 0
    failed: int = 0
    new: int = 0
    closed: int = 0
    switched_off: int = 0
    seconds: float = 0.0
    tripped: tuple[str, ...] = ()
    watch: WatchReport | None = None  # the watched-page pass (its own `watch_pages` run); None when it did not run
    watch_error: str = ""
    probe: ProbeReport | None = None  # the daily slug-probe pass; None when it did not run (or failed: `probe_error`)
    probe_error: str = ""
    feed: dict[str, int] | None = None  # the SimplifyJobs feed poll (#153): changed (0 or 1), seen, new; None if not run
    feed_error: str = ""
    alarm_error: str = ""  # the pipeline alarm check or its notice failed (#153); the tick itself did not
    alerted: int = 0  # the postings the new-job notice announced this tick (#156)
    alert_error: str = ""  # the new-job alert failed (#156); the tick itself did not
    by_ats: dict[str, dict[str, int]] = field(default_factory=dict)
    check_runs: tuple[int, ...] = ()  # the Check now runs (#194) this tick made, each its own `read_boards` run
    check_error: str = ""  # a Check now run failed (#194); the tick itself did not


class AlreadyRunning(Exception):
    """Another tick holds the crawl lock."""


@contextlib.contextmanager
def tick_lock(store_path: str | os.PathLike) -> Iterator[Path]:
    """Hold `crawl.lock` beside the store file for the block (`flock`, exclusive, never waits). Raises
    `AlreadyRunning` when another process, or another open of the file in this one, holds it. The lock goes with the
    file descriptor, so a crashed tick never leaves it held."""
    path = Path(store_path).with_name(LOCK_NAME)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise AlreadyRunning(f"another crawl holds {path}") from e
        yield path
    finally:
        os.close(fd)


def resolve_combiner(conn: sqlite3.Connection):
    """The reader's combiner for every decision the crawler makes (ruling 10): `reader_combiner(conn)` from
    `outreach.reader.combine` through a lazy import, None while that module does not exist. An import error from
    inside the reader is a bug and propagates."""
    try:
        module = importlib.import_module("outreach.reader.combine")
    except ModuleNotFoundError as e:
        if e.name in ("outreach.reader", "outreach.reader.combine"):
            return None
        raise
    return module.reader_combiner(conn)


def ensure_ruleset_current(conn: sqlite3.Connection, combiner=None) -> bool:
    """Run `rebuild` when the store's current ruleset is not this code's; returns whether it did. The combiner goes
    to `rebuild` only when there is one, so the call works before and after #32 gives `rebuild` that argument."""
    row = conn.execute("SELECT ruleset_version FROM ruleset WHERE is_current = 1").fetchone()
    if row is not None and row[0] == ruleset_version():
        return False
    rebuild(conn, **({} if combiner is None else {"combiner": combiner}))
    return True


def plan_date(now: str) -> str:
    """The daily-list date `now` (canonical UTC) falls on in New York."""
    return datetime.strptime(now, ISO_FMT).replace(tzinfo=timezone.utc).astimezone(PLAN_TZ).date().isoformat()


def _bucket_of(url: str, cfg: CrawlConfig) -> str:
    return bucket_for(urllib.parse.urlsplit(url).hostname or "", cfg)


class _TickClient:
    """The PoliteClient as the tick hands it out: it refuses a bucket the tick has tripped (as the breaker refuses
    its own) and remembers the bucket of the first request, which is the board's list request.
    `requests` counts the requests it let through to the polite client (#152)."""

    def __init__(self, polite: HttpClient, cfg: CrawlConfig, down: set[str]):
        self.polite, self.cfg, self.down = polite, cfg, down
        self.first_bucket: str | None = None
        self.requests = 0

    def _check(self, url: str) -> None:
        bucket = _bucket_of(url, self.cfg)
        if self.first_bucket is None:
            self.first_bucket = bucket
        if bucket in self.down:
            raise HostTripped(bucket)

    def get(self, url: str, headers: Mapping[str, str] | None = None, *, follow_redirects: bool = False,
            max_bytes: int | None = None) -> Response:
        self._check(url)
        self.requests += 1
        extra = {} if max_bytes is None else {"max_bytes": max_bytes}
        return self.polite.get(url, headers, follow_redirects=follow_redirects, **extra)

    def post(self, url: str, body: bytes, headers: Mapping[str, str] | None = None) -> Response:
        self._check(url)
        self.requests += 1
        return self.polite.post(url, body, headers)

    def download(self, url: str, dest: Path, headers: Mapping[str, str] | None = None) -> int:
        self._check(url)
        self.requests += 1
        return self.polite.download(url, dest, headers)


class _Listed:
    """An adapter whose list answer the tick already has: `read_board` writes it without a second list request, and
    body requests go to the real adapter. It is made once the list request is done, so `body_requests` is what the
    view has sent since (#152): a body an adapter builds without asking the board counts for nothing."""

    def __init__(self, adapter: Adapter, result: ReadResult, view: _TickClient):
        self.ats = adapter.ats
        self._adapter, self._result, self._view, self._listed = adapter, result, view, view.requests

    def list(self, token: str, validators: Validators) -> ReadResult:
        return self._result

    def body(self, token: str, job: JobRef) -> str | None:
        return self._adapter.body(token, job)

    @property
    def body_requests(self) -> int:
        return self._view.requests - self._listed


@dataclass(frozen=True)
class _Held:
    board: BoardRow
    adapter: Adapter
    result: ReadResult
    now: str
    bucket: str | None
    view: _TickClient


class _Pass:
    """One run over a list of boards, inside one `read_boards` job run."""

    def __init__(self, conn, polite: PoliteClient, cfg: CrawlConfig, rules_cfg, combiner, now_fn, run_id: int,
                 clock: Callable[[], float] = time.monotonic):
        self.conn, self.polite, self.cfg = conn, polite, cfg
        self.rules_cfg, self.combiner, self.now_fn, self.run_id = rules_cfg, combiner, now_fn, run_id
        self.clock = clock
        self.by_ats: dict[str, dict[str, int]] = {}
        self.counts = {k: 0 for k in ("read", "ok", "empty", "error", "unchanged", "deferred", "failed", "new",
                                      "closed")}
        self.down: set[str] = set()  # buckets the tick will not send to again
        self.seen: set[str] = set()
        self.error_run: dict[str, int] = {}  # error reads in a row per bucket that the breaker does not count
        self.held: list[_Held] = []
        self.held_empty: list[_Held] = []
        self.empties: dict[str, int] = {}  # held empty reads per bucket
        self.errors: list[str] = []
        self.failed_ids: list[int] = []
        self.stopped = False

    def client(self) -> _TickClient:
        return _TickClient(self.polite, self.cfg, self.down)

    def start(self, board: BoardRow) -> tuple[Adapter | None, _TickClient]:
        """The board's adapter (None when no adapter reads its ATS) and the view its requests go through. Writes
        nothing: the fan-out calls it for every board before the first list is taken."""
        view = self.client()
        return adapter_for(board.ats, view), view

    def take(self, listed: Listed) -> None:
        """Write one listed board, on the thread that owns the store. The time is taken here, when the board is
        written, not when it was started.

        The tick trips a bucket here, as it takes the error that reaches `breaker_failures`, but worker threads may
        already have listed more of that bucket's boards by then. Their answers arrive late, and each is deferred,
        as a serial tick that never sent them would: no read, no streak change. Those extra requests are bounded by
        the bucket's boards already in flight or queued when the trip is taken."""
        if listed.adapter is None:  # due_boards selects the ATSs the crawler reads only
            return
        board, adapter, view, result = listed.board, listed.adapter, listed.view, listed.result
        now = self.now_fn()
        if listed.tripped is not None:
            self._trip(listed.tripped)
            self._push(board, add_seconds(now, self.cfg.defer_s))
            self.counts["deferred"] += 1
            return
        self._profile(board.ats, boards=1, list_requests=view.requests, list_ms=listed.list_ms)
        bucket = view.first_bucket
        if bucket is not None and bucket in self.down:  # listed before the writer took the trip: a late answer
            self._push(board, add_seconds(now, self.cfg.defer_s))
            self.counts["deferred"] += 1
            return
        if bucket is not None:
            self.seen.add(bucket)
        if result.result == "error":
            self.held.append(_Held(board, adapter, result, now, bucket, view))
            if bucket is not None and board.last_read in _GOOD_LAST and result.http_status not in _BOARD_STATUSES:
                self.error_run[bucket] = self.error_run.get(bucket, 0) + 1
                if self.error_run[bucket] >= self.cfg.breaker_failures:
                    self._trip(bucket)
            return
        if bucket is not None:
            self.error_run[bucket] = 0
        if result.result == "empty" and bucket is not None and self._shielded(board, now) and self._has_open(board):
            self.held_empty.append(_Held(board, adapter, result, now, bucket, view))
            self.empties[bucket] = self.empties.get(bucket, 0) + 1
            return
        self._ingest(board, _Listed(adapter, result, view), now, bucket)

    def _ms(self, since: float) -> int:
        return round((self.clock() - since) * 1000)

    def _profile(self, ats: str, **add: int) -> None:
        """Add to the ATS's row of `by_ats` (#152). Every key starts at 0 so the row always has the same shape."""
        row = self.by_ats.setdefault(ats, dict.fromkeys(_PROFILE_KEYS, 0))
        for key, n in add.items():
            row[key] += n

    def finish(self) -> None:
        """The held reads. An error: a deferral when its bucket tripped and the board is shielded, else the error read
        it is. An empty: a deferral when its bucket gave `MASS_EMPTY_BOARDS` or more, else the empty read it is."""
        self.down |= {b for b in self.seen if self.polite.tripped(b)}
        for h in self.held:
            if h.bucket is not None and h.bucket in self.down and self._shielded(h.board, h.now):
                self._defer(h)
            else:
                self._ingest(h.board, _Listed(h.adapter, h.result, h.view), h.now, h.bucket)
        for h in self.held_empty:
            if self.empties[h.bucket] >= MASS_EMPTY_BOARDS:
                self._trip(h.bucket)
                self._defer(h)
            else:
                self._ingest(h.board, _Listed(h.adapter, h.result, h.view), h.now, h.bucket)
        self.held.clear()
        self.held_empty.clear()

    def _defer(self, h: _Held) -> None:
        self._push(h.board, add_seconds(h.now, self.cfg.defer_s))
        self.counts["deferred"] += 1

    def _shielded(self, board: BoardRow, now: str) -> bool:
        """An outage may hold this board's read back: its last read was good and is younger than the grace."""
        return (board.last_read in _GOOD_LAST and board.last_read_at is not None
                and board.last_read_at > add_seconds(now, -self.cfg.outage_grace_s))

    def _has_open(self, board: BoardRow) -> bool:
        return self.conn.execute("SELECT 1 FROM posting WHERE board_id = ? AND closed_at IS NULL LIMIT 1",
                                 (board.board_id,)).fetchone() is not None

    def _trip(self, bucket: str) -> None:
        self.down.add(bucket)
        self.seen.add(bucket)

    def _ingest(self, board: BoardRow, adapter: _Listed, now: str, bucket: str | None) -> None:
        started = self.clock()
        try:
            out = read_board(self.conn, board, adapter, run_id=self.run_id, now=now, cfg=self.cfg,
                             rules_cfg=self.rules_cfg, combiner=self.combiner, bucket=bucket, clock=self.clock)
        except HostTripped as e:  # read_board ends its body fetches itself; kept so a trip can never fail a board
            self._rollback()
            self._trip(e.bucket)
            self._push(board, add_seconds(now, self.cfg.defer_s))
            self.counts["deferred"] += 1
            return
        except RulesetNotCurrent:
            # Someone changed the ruleset mid-tick: start no more boards; the next tick rebuilds first.
            self._rollback()
            self._push(board, add_seconds(now, self.cfg.defer_s))
            self.counts["deferred"] += 1
            self.stopped = True
            self.errors.append(_RULESET_CHANGED)
            return
        except Exception as e:
            # Our bug, not the board's error: no streak change, and the wait its next error would get.
            self._rollback()
            self._push(board, add_seconds(now, error_backoff_s(board.error_streak + 1, self.cfg)))
            self.counts["failed"] += 1
            self.failed_ids.append(board.board_id)
            if len(self.failed_ids) <= _MAX_ERROR_LINES:
                self.errors.append(f"board {board.board_id} ({board.ats} {board.token}): {describe_error(e)}")
            return
        self.counts["read"] += 1
        self.counts[out.result] += 1
        self.counts["new"] += out.new
        self.counts["closed"] += out.closed
        self._profile(board.ats, body_requests=adapter.body_requests, body_ms=out.body_ms,
                      ingest_ms=max(0, self._ms(started) - out.body_ms), new=out.new, closed=out.closed,
                      reopened=out.reopened)
        if out.tripped:
            self._trip(out.tripped)

    def _rollback(self) -> None:
        if self.conn.in_transaction:
            self.conn.execute("ROLLBACK")

    def _push(self, board: BoardRow, next_read_at: str) -> None:
        with atomic(self.conn, "tick_push"):
            self.conn.execute("UPDATE board SET next_read_at = ? WHERE board_id = ?", (next_read_at, board.board_id))


def _polite(client: HttpClient, cfg: CrawlConfig, clock, sleep) -> PoliteClient:
    return client if isinstance(client, PoliteClient) else PoliteClient(client, cfg, clock=clock, sleep=sleep)


def _checkpoint(conn: sqlite3.Connection) -> None:
    with contextlib.suppress(sqlite3.Error):  # housekeeping: a failed checkpoint must not hide what happened
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")


def _read_boards(conn: sqlite3.Connection, polite: PoliteClient, select: Callable[[str], list[BoardRow]], *,
                 cfg: CrawlConfig, rules_cfg, combiner, now_fn, clock, started: float,
                 rebuilt: bool, switched_off: int = 0,
                 workers: Mapping[str, int] | None = None, budget_s: int | None = None,
                 summary_extra: Mapping[str, object] | None = None,
                 on_take: Callable[[_Pass, int, int], None] | None = None) -> tuple[TickReport, _Pass]:
    """One `read_boards` job run over `select(now)`. Once the budget is spent (or the pass stopped) no board is
    listed, and under `workers` no listed board still waiting for the writer is written: listing on threads is fast
    and writing (with its body fetches) is not, so the budget bounds both. A board listed but not written keeps its
    `next_read_at` and is read again next tick. Serially each board is written as soon as it is listed, as before.
    `switched_off` is the count of boards `select` leaves out because their reader is off; it goes into the report
    as it is.

    The budget is `budget_s` seconds from `started`, `cfg.tick_budget_s` when None (a Check now passes its own,
    #194). `summary_extra` is added to the job summary (a Check now's `mode` and `requested_at`). `on_take(run,
    total, taken)` is called on this thread once the boards are selected (`taken` 0) and after each board is taken,
    so a caller can report progress; it may write the store.

    The lists go through `fanout.list_boards`: inline in board order when `workers` is None, else on worker threads
    (#158). Every write happens here, on the thread that owns `conn`, as each list comes back; the body fetches stay
    inside `read_board`. A ruleset change a worker meets is handled as one an ingest meets: no new board starts, the
    run ends `partial`, and the boards not written keep their `next_read_at`."""
    budget = cfg.tick_budget_s if budget_s is None else budget_s
    extra = dict(summary_extra or {})
    run_id = start_job(conn, "read_boards", ruleset_version())
    run = _Pass(conn, polite, cfg, rules_cfg, combiner, now_fn, run_id, clock)
    try:
        now = now_fn()

        def should_stop() -> bool:  # asked on worker threads: never touches the store
            return run.stopped or clock() - started >= budget

        boards = select(now)
        taken = 0
        if on_take is not None:
            on_take(run, len(boards), taken)
        listed = fanout.list_boards(boards, run.start, lambda b: full_read_due(conn, b, now), workers=workers,
                                    should_stop=should_stop, clock=clock)
        with contextlib.closing(listed):  # a break or a raise stops the workers now, not when the frame is collected
            try:
                for item in listed:
                    # Serially a board is written as soon as it is listed. Fanned out, lists run ahead of the
                    # writer, so a listed board may wait here, and the budget stops its write too.
                    if workers is not None and should_stop():
                        break
                    run.take(item)
                    taken += 1
                    if on_take is not None:
                        on_take(run, len(boards), taken)
            except RulesetNotCurrent:  # a worker met it; an ingest that met it first has said so already
                if not run.stopped:
                    run.errors.append(_RULESET_CHANGED)
                run.stopped = True
        run.finish()
    except BaseException as e:
        try:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            finish_job(conn, run_id, "failed", {**run.counts, **extra}, describe_error(e))
        except Exception as finish_err:  # keep the original error as the one that propagates
            e.add_note(f"could not record the failed run: {describe_error(finish_err)}")
        raise
    report = TickReport(run_id=run_id, rebuilt=rebuilt, **run.counts, switched_off=switched_off,
                        seconds=round(clock() - started, 3), tripped=tuple(sorted(run.down)), by_ats=run.by_ats)
    summary = {k: v for k, v in asdict(report).items()
               if k not in ("watch", "watch_error", "probe", "probe_error", "feed", "feed_error", "alarm_error", "alerted",
                            "alert_error", "check_runs", "check_error")}
    summary.update(extra)
    if run.failed_ids:
        summary["failed_boards"] = run.failed_ids
    status = "partial" if run.failed_ids or run.stopped else "ok"
    finish_job(conn, run_id, status, summary, "; ".join(run.errors))
    return report, run


def _probe_step(conn: sqlite3.Connection, run: _Pass, cfg: CrawlConfig, now_fn, clock, started: float
                ) -> tuple[ProbeReport | None, str]:
    """The daily slug probe (Task 26a): once per New York day, after the boards, through the tick's client and tripped
    buckets, for up to `PROBE_LIMIT` companies with no board. Skipped once the budget is spent. The day is recorded
    (a `slug_probe_run` event keyed by the date) only when the pass completed, so a failure is tried again on the next
    tick; whatever it raises is returned as text and never touches the board reads."""
    if run.stopped or clock() - started >= cfg.tick_budget_s:
        return None, ""
    now = now_fn()
    key = f"slug-probe-run:{plan_date(now)}"
    if conn.execute("SELECT 1 FROM event WHERE idempotency_key = ?", (key,)).fetchone():
        return None, ""
    try:
        ids = [r[0] for r in conn.execute("SELECT company_id FROM company")]
        report = probe_slugs(conn, ids, run.client(), limit=PROBE_LIMIT, cfg=cfg, now=now)
        append_event(conn, entity="source", kind="slug_probe_run", actor="crawler", detail=asdict(report), at=now,
                     idempotency_key=key)
    except Exception as e:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        return None, describe_error(e)
    return report, ""


def _feed_step(conn: sqlite3.Connection, run: _Pass, cfg: CrawlConfig, now_fn, clock, started: float
               ) -> tuple[dict[str, int] | None, str]:
    """The SimplifyJobs feed poll (#153): once per tick, after the boards, through the tick's client. Whatever it
    raises is returned as text and never touches the board reads; `simplify_fail_streak` counts the errors in a row.
    Like the probe, it is skipped once the pass stopped or the budget is spent. A switched-off feed
    (`[simplify] enabled = false`) sends nothing and changes nothing."""
    if run.stopped or clock() - started >= cfg.tick_budget_s:
        return None, ""
    try:
        cfg = load_pipeline_config()
        if not cfg.simplify.enabled:
            return None, ""
        result = poll_simplify_feed(conn, run.client(), now_fn(), cfg=cfg)
    except Exception as e:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        with contextlib.suppress(sqlite3.Error):  # the streak is a count for an alarm: it must not fail the tick
            note_streak(conn, ok=False)
        return None, describe_error(e)
    with contextlib.suppress(sqlite3.Error):
        note_streak(conn, ok=True, now=now_fn())
    return {"changed": int(result.changed), "seen": result.seen, "new": result.new}, ""


def _alarm_step(conn: sqlite3.Connection, cfg: CrawlConfig, now_fn, notifier: Notifier) -> str:
    """The pipeline alarms (#153), after the feed step: at most one Mac notice a New York day, naming the first alarm
    and how many more there are. A failure here is not the tick's: it is returned as text (`alarm_error`)."""
    try:
        now = now_fn()
        found = alarms.check(conn, now, cfg=cfg)
        if not found:
            return ""
        text = found[0].text + (f" and {len(found) - 1} more" if len(found) > 1 else "")
        with atomic(conn, "pipeline_alarm"):
            notify_once(conn, notifier, "pipeline_alarm", date.fromisoformat(ny_day(now)), "Knock pipeline", text,
                        at=now)
    except Exception as e:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        return describe_error(e)
    return ""


def _alert_step(conn: sqlite3.Connection, now_fn, notifier: Notifier) -> tuple[int, str]:
    """The new-job alert (#156), after the alarm step: one grouped Mac notice for the postings at his level that came in
    today (`alerts.run`). A failure here is not the tick's: the count is 0 and the error is returned as text."""
    try:
        return alerts.run(conn, notifier, now_fn()), ""
    except Exception as e:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        return 0, describe_error(e)


def _add_passes(conn: sqlite3.Connection, report: TickReport) -> None:
    """Put the probe, watch, feed, alarm and alert passes into the tick's `read_boards` summary, so the health view reads one row:
    each pass's counts (null when it did not run) and its error text, and the Check now runs the tick made."""
    passes = {"probe": asdict(report.probe) if report.probe is not None else None, "probe_error": report.probe_error,
              "watch": asdict(report.watch) if report.watch is not None else None, "watch_error": report.watch_error,
              "feed": report.feed, "feed_error": report.feed_error, "alarm_error": report.alarm_error,
              "alerted": report.alerted, "alert_error": report.alert_error, "check_runs": list(report.check_runs),
              "check_error": report.check_error}
    with atomic(conn, "tick_summary"):
        row = conn.execute("SELECT summary FROM job_run WHERE run_id = ?", (report.run_id,)).fetchone()
        summary = {**json.loads(row[0]), **passes}
        conn.execute("UPDATE job_run SET summary = ? WHERE run_id = ?", (json.dumps(summary, sort_keys=True),
                                                                          report.run_id))


def _no_notice(title: str, text: str) -> None:
    """The default notifier: says nothing. The crawl command passes `mac_notifier`; a caller that passes none gets no
    notice, so a test or a script can never pop one by accident."""


def run_tick(conn: sqlite3.Connection, client: HttpClient, *, cfg: CrawlConfig | None = None, rules_cfg=None,
             combiner=None, now_fn: Callable[[], str] = utcnow, clock: Callable[[], float] = time.monotonic,
             sleep: Callable[[float], None] = time.sleep, limit: int | None = None,
             notifier: Notifier = _no_notice, workers: Mapping[str, int] | None = None) -> TickReport:
    """One tick: the ruleset check, a Check now when one waits (#194), the due boards (at most `limit or
    cfg.boards_per_tick`, within the budget), then the daily slug probe (Task 26a, `probe`), then the watched-page pass
    (Task 14), then the SimplifyJobs feed poll (#153, `feed`), then another Check now when a request came in meanwhile,
    all through the same client and breaker; the watch pass is its own `watch_pages` run, and each check its own
    `read_boards` run (`check_all.run_pending_check`, listed in `check_runs`). A failed probe, watch, feed or check
    pass is reported in `probe_error`, `watch_error`, `feed_error` or `check_error` and leaves the board reads as they
    are. The first check's time is not counted in the tick's budget, so a check never eats the tick's own pass. The
    pipeline alarms are checked next and may send one Mac notice a day through `notifier`; the new-job alert (#156) comes last and
    sends at most one grouped notice every 15 minutes through the same `notifier`, so it covers what a check found.
    The caller holds `tick_lock`.

    `combiner=None` means `resolve_combiner(conn)`. `clock` and `sleep` drive the budget and the polite spacing.
    `workers` (the `[workers]` table, #158) lists the boards on that many threads per ATS; None lists them one by one
    on this thread. Only the board lists fan out: the probe, watch and feed passes run here as before.
    """
    cfg = cfg or load_crawl_config()
    started = clock()
    try:
        if conn.in_transaction:
            raise ValueError("a tick sends requests, so it must start with no transaction open")
        combiner = combiner if combiner is not None else resolve_combiner(conn)
        rebuilt = ensure_ruleset_current(conn, combiner)
        polite = _polite(client, cfg, clock, sleep)

        def select(now: str) -> list[BoardRow]:
            return due_boards(conn, now, supported=reading_ats(cfg), plan_ids=plan_company_ids(conn, plan_date(now)),
                              cfg=cfg, limit=limit or cfg.boards_per_tick)

        from outreach.crawl.check_all import run_pending_check  # check_all builds on this module
        off = len(switched_off_boards(conn, cfg.ats_off))
        check_runs: list[int] = []
        check_errors: list[str] = []

        def check() -> None:
            run_id, error = run_pending_check(conn, polite, cfg=cfg, rules_cfg=rules_cfg, combiner=combiner,
                                              now_fn=now_fn, clock=clock, switched_off=off, workers=workers)
            if run_id is not None:
                check_runs.append(run_id)
            if error:
                check_errors.append(error)

        before = clock()
        check()
        started += clock() - before  # the check's time is not the tick's: its own pass keeps its whole budget
        report, run = _read_boards(conn, polite, select, cfg=cfg, rules_cfg=rules_cfg, combiner=combiner,
                                   now_fn=now_fn, clock=clock, started=started, rebuilt=rebuilt, switched_off=off,
                                   workers=workers)
        probe, probe_error = _probe_step(conn, run, cfg, now_fn, clock, started)
        report = replace(report, probe=probe, probe_error=probe_error)
        try:
            report = replace(report, watch=watch_pages(conn, run.client(), cfg=cfg, now_fn=now_fn))
        except Exception as e:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            report = replace(report, watch_error=describe_error(e))
        feed, feed_error = _feed_step(conn, run, cfg, now_fn, clock, started)
        report = replace(report, feed=feed, feed_error=feed_error)
        check()  # a request that came in while the tick ran
        report = replace(report, check_runs=tuple(check_runs), check_error="; ".join(check_errors))
        report = replace(report, alarm_error=_alarm_step(conn, cfg, now_fn, notifier))
        alerted, alert_error = _alert_step(conn, now_fn, notifier)
        report = replace(report, alerted=alerted, alert_error=alert_error)
        _add_passes(conn, report)
        return report
    finally:
        _checkpoint(conn)


def read_company_boards(conn: sqlite3.Connection, client: HttpClient, company_ids: Collection[int], *,
                        cfg: CrawlConfig | None = None, rules_cfg=None, combiner=None,
                        now_fn: Callable[[], str] = utcnow, clock: Callable[[], float] = time.monotonic,
                        sleep: Callable[[float], None] = time.sleep,
                        workers: Mapping[str, int] | None = None) -> TickReport:
    """Read these companies' boards now, for a plan about to be built (#15, #13): `due_boards` with `force`, which
    skips `next_read_at` but keeps the floor and an erroring board's backoff. Its own `read_boards` run, the same
    breaker rules as a tick, and no watched-page pass. It takes the crawl lock itself (raising `AlreadyRunning` while
    a tick holds it), so it never reads a board a tick is reading. `workers` as in `run_tick`."""
    with _store_lock(conn):
        return _read_company_boards(conn, client, company_ids, cfg=cfg, rules_cfg=rules_cfg, combiner=combiner,
                                    now_fn=now_fn, clock=clock, sleep=sleep, workers=workers)


@contextlib.contextmanager
def _store_lock(conn: sqlite3.Connection) -> Iterator[None]:
    """`tick_lock` for the store file `conn` has open (an in-memory store has no file and needs no lock)."""
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    if not path:
        yield
        return
    with tick_lock(path):
        yield


def _read_company_boards(conn, client, company_ids, *, cfg, rules_cfg, combiner, now_fn, clock, sleep,
                         workers) -> TickReport:
    cfg = cfg or load_crawl_config()
    started = clock()
    ids = sorted(set(company_ids))
    try:
        if conn.in_transaction:
            raise ValueError("reading boards sends requests, so it must start with no transaction open")
        combiner = combiner if combiner is not None else resolve_combiner(conn)
        rebuilt = ensure_ruleset_current(conn, combiner)
        polite = _polite(client, cfg, clock, sleep)

        def select(now: str) -> list[BoardRow]:
            return due_boards(conn, now, supported=reading_ats(cfg), plan_ids=plan_company_ids(conn, plan_date(now)),
                              cfg=cfg, limit=cfg.boards_per_tick, only_company_ids=ids, force=True)

        off = len(switched_off_boards(conn, cfg.ats_off, only_company_ids=ids))
        report, _ = _read_boards(conn, polite, select, cfg=cfg, rules_cfg=rules_cfg, combiner=combiner,
                                 now_fn=now_fn, clock=clock, started=started, rebuilt=rebuilt, switched_off=off,
                                 workers=workers)
        return report
    finally:
        _checkpoint(conn)
