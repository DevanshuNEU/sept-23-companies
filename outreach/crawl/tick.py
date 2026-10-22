"""The 30-minute tick: read the due boards within a time budget, then the watched pages (#21, Task 12).

One tick is one `read_boards` job run. It rebuilds first when the store's ruleset is not this code's, so a rebuild
and a crawl never overlap; `tick_lock` keeps two ticks from running at once. Boards come from `due_boards` in its
fixed order (today's plan, then never read, then the oldest due), and no new board starts once `tick_budget_s` has
passed, so a Mac that slept for days reads what matters first and leaves the rest due.

Every request goes through one `PoliteClient`. An outage is not a finding about any one board (Review Focus 3), so:

- a board whose request the breaker refuses is deferred (`next_read_at = now + defer_s`), with no `board_read` and no
  streak change;
- an `error` read is held back until the end of the tick. The tick also counts, per host bucket, error reads in a
  row that the polite breaker does not see as host failures (a challenge page or another wrong-shape 200, a blanket
  401 or 400; a 404 or 410 is about the board and is not counted). When that count reaches `breaker_failures` the
  tick trips the bucket itself. At the end, every held error from a bucket that tripped either way becomes a
  deferral too; the rest are written as the error reads they are.

A board whose ingest raises (our bug, not the board's error) is recorded as `failed`, keeps its streaks and waits
the backoff its next error would get; the job then ends `partial`. `PRAGMA wal_checkpoint(PASSIVE)` runs after
every tick, whatever happened.
"""
from __future__ import annotations

import contextlib
import fcntl
import importlib
import os
import sqlite3
import time
import urllib.parse
from collections.abc import Callable, Collection, Iterator, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from outreach.crawl.ats import adapter_for, supported_ats
from outreach.crawl.base import Adapter, JobRef, ReadResult, Validators
from outreach.crawl.boardstate import BoardRow
from outreach.crawl.cadence import add_seconds, due_boards, error_backoff_s, plan_company_ids
from outreach.crawl.config import CrawlConfig, load_crawl_config
from outreach.crawl.ingest import list_board, read_board
from outreach.crawl.polite import HostTripped, PoliteClient, bucket_for
from outreach.crawl.watch import WatchReport, watch_pages
from outreach.net import HttpClient, Response
from outreach.rules import RulesetNotCurrent, rebuild, ruleset_version
from outreach.store import atomic, describe_error, finish_job, start_job, utcnow
from outreach.store.timefmt import ISO_FMT

__all__ = ["TickReport", "AlreadyRunning", "LOCK_NAME", "tick_lock", "ensure_ruleset_current", "resolve_combiner",
           "plan_date", "run_tick", "read_company_boards"]

LOCK_NAME = "crawl.lock"
# Today's plan is Devanshu's day: the daily list is dated in New York, like the send days.
PLAN_TZ = ZoneInfo("America/New_York")
# An error read with one of these statuses is about the board (moved, closed), never a sign the host is down.
_BOARD_STATUSES = frozenset({404, 410})
_MAX_ERROR_LINES = 20  # board failures named in job_run.error; the count is always complete


@dataclass(frozen=True)
class TickReport:
    """What one tick did; everything but the watch fields is also the `read_boards` job summary. `read` counts the
    boards whose read was recorded (ok, empty, error, unchanged); `deferred` the boards put off with no read
    recorded; `tripped` the host buckets the breaker stopped. The plan's `queued` is gone (ruling 2)."""
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
    seconds: float = 0.0
    tripped: tuple[str, ...] = ()
    watch: WatchReport | None = None  # the watched-page pass (its own `watch_pages` run); None when it did not run
    watch_error: str = ""


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
    its own) and remembers the bucket of the first request, which is the board's list request."""

    def __init__(self, polite: HttpClient, cfg: CrawlConfig, down: set[str]):
        self.polite, self.cfg, self.down = polite, cfg, down
        self.first_bucket: str | None = None

    def _check(self, url: str) -> None:
        bucket = _bucket_of(url, self.cfg)
        if self.first_bucket is None:
            self.first_bucket = bucket
        if bucket in self.down:
            raise HostTripped(bucket)

    def get(self, url: str, headers: Mapping[str, str] | None = None, *, follow_redirects: bool = False) -> Response:
        self._check(url)
        return self.polite.get(url, headers, follow_redirects=follow_redirects)

    def post(self, url: str, body: bytes, headers: Mapping[str, str] | None = None) -> Response:
        self._check(url)
        return self.polite.post(url, body, headers)

    def download(self, url: str, dest: Path, headers: Mapping[str, str] | None = None) -> int:
        self._check(url)
        return self.polite.download(url, dest, headers)


class _Listed:
    """An adapter whose list answer the tick already has: `read_board` writes it without a second list request, and
    body requests go to the real adapter."""

    def __init__(self, adapter: Adapter, result: ReadResult):
        self.ats = adapter.ats
        self._adapter, self._result = adapter, result

    def list(self, token: str, validators: Validators) -> ReadResult:
        return self._result

    def body(self, token: str, job: JobRef) -> str | None:
        return self._adapter.body(token, job)


@dataclass(frozen=True)
class _Held:
    board: BoardRow
    adapter: Adapter
    result: ReadResult
    now: str
    bucket: str | None


class _Pass:
    """One run over a list of boards, inside one `read_boards` job run."""

    def __init__(self, conn, polite: PoliteClient, cfg: CrawlConfig, rules_cfg, combiner, now_fn, run_id: int):
        self.conn, self.polite, self.cfg = conn, polite, cfg
        self.rules_cfg, self.combiner, self.now_fn, self.run_id = rules_cfg, combiner, now_fn, run_id
        self.counts = {k: 0 for k in ("read", "ok", "empty", "error", "unchanged", "deferred", "failed", "new",
                                      "closed")}
        self.down: set[str] = set()  # buckets the tick will not send to again
        self.seen: set[str] = set()
        self.error_run: dict[str, int] = {}  # error reads in a row per bucket that the breaker does not count
        self.held: list[_Held] = []
        self.errors: list[str] = []
        self.failed_ids: list[int] = []
        self.stopped = False

    def client(self) -> _TickClient:
        return _TickClient(self.polite, self.cfg, self.down)

    def read(self, board: BoardRow) -> None:
        now = self.now_fn()
        view = self.client()
        adapter = adapter_for(board.ats, view)
        if adapter is None:  # due_boards selects supported ATSs only
            return
        try:
            result = list_board(adapter, board)
        except HostTripped as e:
            self._trip(e.bucket)
            self._push(board, add_seconds(now, self.cfg.defer_s))
            self.counts["deferred"] += 1
            return
        bucket = view.first_bucket
        if bucket is not None:
            self.seen.add(bucket)
        if result.result == "error":
            self.held.append(_Held(board, adapter, result, now, bucket))
            if bucket is not None and result.http_status not in _BOARD_STATUSES:
                self.error_run[bucket] = self.error_run.get(bucket, 0) + 1
                if self.error_run[bucket] >= self.cfg.breaker_failures:
                    self._trip(bucket)
            return
        if bucket is not None:
            self.error_run[bucket] = 0
        self._ingest(board, _Listed(adapter, result), now)

    def finish(self) -> None:
        """The held error reads: a deferral when their bucket tripped, else the error read they are."""
        self.down |= {b for b in self.seen if self.polite.tripped(b)}
        for h in self.held:
            if h.bucket is not None and h.bucket in self.down:
                self._push(h.board, add_seconds(h.now, self.cfg.defer_s))
                self.counts["deferred"] += 1
            else:
                self._ingest(h.board, _Listed(h.adapter, h.result), h.now)
        self.held.clear()

    def _trip(self, bucket: str) -> None:
        self.down.add(bucket)
        self.seen.add(bucket)

    def _ingest(self, board: BoardRow, adapter: Adapter, now: str) -> None:
        try:
            out = read_board(self.conn, board, adapter, run_id=self.run_id, now=now, cfg=self.cfg,
                             rules_cfg=self.rules_cfg, combiner=self.combiner)
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
            self.errors.append("the ruleset changed during the tick; the next tick rebuilds first")
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
                 rebuilt: bool) -> tuple[TickReport, _Pass]:
    """One `read_boards` job run over `select(now)`, starting no board once the budget is spent."""
    run_id = start_job(conn, "read_boards", ruleset_version())
    run = _Pass(conn, polite, cfg, rules_cfg, combiner, now_fn, run_id)
    try:
        for board in select(now_fn()):
            if run.stopped or clock() - started >= cfg.tick_budget_s:
                break
            run.read(board)
        run.finish()
    except BaseException as e:
        try:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            finish_job(conn, run_id, "failed", run.counts, describe_error(e))
        except Exception as finish_err:  # keep the original error as the one that propagates
            e.add_note(f"could not record the failed run: {describe_error(finish_err)}")
        raise
    report = TickReport(run_id=run_id, rebuilt=rebuilt, **run.counts, seconds=round(clock() - started, 3),
                        tripped=tuple(sorted(run.down)))
    summary = {k: v for k, v in asdict(report).items() if k not in ("watch", "watch_error")}
    if run.failed_ids:
        summary["failed_boards"] = run.failed_ids
    status = "partial" if run.failed_ids or run.stopped else "ok"
    finish_job(conn, run_id, status, summary, "; ".join(run.errors))
    return report, run


def run_tick(conn: sqlite3.Connection, client: HttpClient, *, cfg: CrawlConfig | None = None, rules_cfg=None,
             combiner=None, now_fn: Callable[[], str] = utcnow, clock: Callable[[], float] = time.monotonic,
             sleep: Callable[[float], None] = time.sleep, limit: int | None = None) -> TickReport:
    """One tick: the ruleset check, the due boards (at most `limit or cfg.boards_per_tick`, within the budget), then
    the watched-page pass (Task 14) through the same client and breaker, as its own `watch_pages` run. A failed watch
    pass is reported in `watch_error` and leaves the board reads as they are. The caller holds `tick_lock`.

    `combiner=None` means `resolve_combiner(conn)`. `clock` and `sleep` drive the budget and the polite spacing.
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
            return due_boards(conn, now, supported=supported_ats(), plan_ids=plan_company_ids(conn, plan_date(now)),
                              cfg=cfg, limit=limit or cfg.boards_per_tick)

        report, run = _read_boards(conn, polite, select, cfg=cfg, rules_cfg=rules_cfg, combiner=combiner,
                                   now_fn=now_fn, clock=clock, started=started, rebuilt=rebuilt)
        try:
            watched = watch_pages(conn, run.client(), cfg=cfg, now_fn=now_fn)
        except Exception as e:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            return replace(report, watch_error=describe_error(e))
        return replace(report, watch=watched)
    finally:
        _checkpoint(conn)


def read_company_boards(conn: sqlite3.Connection, client: HttpClient, company_ids: Collection[int], *,
                        cfg: CrawlConfig | None = None, rules_cfg=None, combiner=None,
                        now_fn: Callable[[], str] = utcnow, clock: Callable[[], float] = time.monotonic,
                        sleep: Callable[[float], None] = time.sleep) -> TickReport:
    """Read these companies' boards now, for a plan about to be built (#15, #13): `due_boards` with `force`, which
    skips `next_read_at` but keeps the floor and an erroring board's backoff. Its own `read_boards` run, the same
    breaker rules as a tick, and no watched-page pass."""
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
            return due_boards(conn, now, supported=supported_ats(), plan_ids=plan_company_ids(conn, plan_date(now)),
                              cfg=cfg, limit=cfg.boards_per_tick, only_company_ids=ids, force=True)

        report, _ = _read_boards(conn, polite, select, cfg=cfg, rules_cfg=rules_cfg, combiner=combiner,
                                 now_fn=now_fn, clock=clock, started=started, rebuilt=rebuilt)
        return report
    finally:
        _checkpoint(conn)
