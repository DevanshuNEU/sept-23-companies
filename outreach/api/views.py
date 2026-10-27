"""The heavy read views: one computation per answer, a few at a time, and a reused answer until the store changes.

A heavy view (shell, plan, bench, needs you, progress, wins, watch, the outcome prompt, the search index, LinkedIn,
the Outbox list) reads a large part of the store, and a browser with several tabs asks for each of them at once when
it reconnects. So such a route hands its computation to `serve`, which:

1. keys the answer by the path and query string, the store's version, the API's config, the route's own extra input
   (whether the old mailer is loaded, for the views that show it), the New York day and the slice of the clock
   `[api] cache_seconds` long that `now` falls in. The store's version is `PRAGMA data_version` on a read-only
   connection of its own, which moves whenever ANY other connection commits (this API's writes, the sender, the
   crawler, the jobs, the MCP server, the judge), plus a counter `run_write` bumps after every write through the API;
2. answers a key it already holds (at most `cache_seconds` old) with the stored bytes;
3. otherwise computes it once: identical requests that arrive while it runs wait for that one computation;
4. lets at most `[api] max_heavy` computations run at the same time. A request that cannot get its answer within
   `[api] queue_wait_seconds` answers 503 `busy`; it never hangs. The computation it was waiting for still finishes
   and fills the cache for the retry.

The stored bytes are what FastAPI itself would send for the route's `response_model` (validated, then `dump_json` by
alias), so a reused answer is byte for byte the answer computed for that key. When the store's version cannot be
read, nothing is reused: every request computes (still a few at a time).

`/api/health` and `/api/pulse` never come here, so they answer at once while heavy views wait.
"""
import asyncio
import sqlite3
import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable, Hashable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anyio import to_thread
from fastapi import Request, Response
from pydantic import TypeAdapter

from outreach.api.clock import get_clock
from outreach.api.errors import ApiProblem
from outreach.outcomes.windows import ny_day
from outreach.store.db import guard
from outreach.store.timefmt import ISO_FMT

BUSY_TEXT = "Knock is busy right now. Nothing was saved. Try again in a moment."
BUSY_HEADERS = {"Retry-After": "2"}
MAX_ENTRIES = 64  # a dozen views, each held for its newest key; older keys are never asked for again


def busy() -> ApiProblem:
    return ApiProblem("busy", BUSY_TEXT, headers=dict(BUSY_HEADERS))


class StoreVersion:
    """A cheap number that changes whenever the store does.

    `PRAGMA data_version` on one read-only connection that never writes: it changes when any other connection, in
    this process or another, commits. `local` is bumped by this API's own writes too, so a write through the API is
    seen even before SQLite's number moves. `read` returns None when the store cannot be asked (nothing is reused)."""

    def __init__(self, db_path: str):
        self._path = db_path
        self._open_conns: list[sqlite3.Connection] = []  # the one open connection, if any
        self._lock = threading.Lock()
        self.local = 0
        weakref.finalize(self, _close_all, self._open_conns)  # an app dropped without its shutdown closes it too

    def _conn(self) -> sqlite3.Connection:
        if not self._open_conns:
            guard(self._path)
            conn = sqlite3.connect(Path(self._path).as_uri() + "?mode=ro", uri=True, isolation_level=None,
                                   check_same_thread=False)
            self._open_conns.append(conn)
            conn.execute("PRAGMA busy_timeout = 1000")
        return self._open_conns[0]

    def read(self) -> tuple[int, int] | None:
        with self._lock:
            try:
                return self._conn().execute("PRAGMA data_version").fetchone()[0], self.local
            except sqlite3.Error:
                _close_all(self._open_conns)
                return None

    def bump(self) -> None:
        with self._lock:
            self.local += 1

    def close(self) -> None:
        with self._lock:
            _close_all(self._open_conns)


def _close_all(conns: list[sqlite3.Connection]) -> None:
    while conns:
        conns.pop().close()


def clock_slice(now: str, seconds: int) -> int:
    """Which `seconds`-long slice of the clock `now` falls in."""
    moment = datetime.strptime(now, ISO_FMT).replace(tzinfo=timezone.utc)
    return int(moment.timestamp()) // seconds


_ADAPTERS: dict[Any, TypeAdapter] = {}


def render(model: Any, value: Any) -> bytes:
    """The JSON FastAPI sends for `value` under `response_model=model`: validated, then dumped by alias."""
    adapter = _ADAPTERS.get(model)
    if adapter is None:
        adapter = _ADAPTERS[model] = TypeAdapter(model)
    return adapter.dump_json(adapter.validate_python(value, from_attributes=True), by_alias=True)


def remember_for(check: Callable[[], bool], seconds: int, clock: Callable[[], float] = time.monotonic
                 ) -> Callable[[], bool]:
    """`check`, asked at most once every `seconds` (the old mailer's `launchctl` question: a process per request
    otherwise)."""
    state: dict[str, Any] = {}
    lock = threading.Lock()

    def remembered() -> bool:
        with lock:
            at = state.get("at")
            if at is not None and clock() - at < seconds:
                return state["value"]
        value = check()
        with lock:
            state["at"], state["value"] = clock(), value
        return value

    return remembered


class ViewCache:
    """The answers, the computations in flight and the limit on them. One per app; the event loop owns the dicts."""

    def __init__(self, db_path: str):
        self.version = StoreVersion(db_path)
        self._entries: OrderedDict[Hashable, tuple[float, bytes]] = OrderedDict()
        self._flights: dict[Hashable, asyncio.Task] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._limit: asyncio.Semaphore | None = None
        self.computed = 0  # computations that finished (tests count these)

    def _bind(self, max_heavy: int) -> asyncio.Semaphore:
        """The limit and the flights belong to the running loop (a test client may start a new loop per call)."""
        loop = asyncio.get_running_loop()
        if self._loop is not loop or self._limit is None:
            self._loop, self._limit, self._flights = loop, asyncio.Semaphore(max_heavy), {}
        return self._limit

    def _get(self, key: Hashable, max_age: int) -> bytes | None:
        found = self._entries.get(key)
        if found is None:
            return None
        if time.monotonic() - found[0] > max_age:
            del self._entries[key]
            return None
        return found[1]

    def _put(self, key: Hashable, body: bytes, max_age: int) -> None:
        now = time.monotonic()
        self._entries[key] = (now, body)
        self._entries.move_to_end(key)
        while self._entries:
            oldest_key, (at, _) = next(iter(self._entries.items()))
            if len(self._entries) <= MAX_ENTRIES and now - at <= max_age:
                break
            del self._entries[oldest_key]

    async def _compute(self, key: Hashable | None, limit: asyncio.Semaphore, wait: float, max_age: int,
                       run: Callable[[], bytes]) -> bytes:
        try:
            await asyncio.wait_for(limit.acquire(), timeout=wait)
        except TimeoutError:
            raise busy() from None
        try:
            body = await to_thread.run_sync(run)
        finally:
            limit.release()
        self.computed += 1
        if key is not None:
            self._put(key, body, max_age)
        return body

    async def serve(self, request: Request, model: Any, compute: Callable[[str, Any], Any],
                    extra: Callable[[], Hashable] | None = None) -> Response:
        """Answer with `compute(now, extra())` rendered as `model`, reusing or sharing it as the module says."""
        cfg = request.app.state.config
        limit = self._bind(cfg.max_heavy)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + cfg.queue_wait_seconds
        now = get_clock(request)()
        version, inputs = await to_thread.run_sync(lambda: (self.version.read(), extra() if extra else None))
        key = None if version is None else (
            request.url.path, request.url.query, version, cfg, inputs, ny_day(now),
            clock_slice(now, cfg.cache_seconds))
        if key is not None:
            body = self._get(key, cfg.cache_seconds)
            if body is not None:
                return Response(content=body, media_type="application/json")
        task = self._flights.get(key) if key is not None else None
        if task is None:
            task = loop.create_task(self._compute(key, limit, cfg.queue_wait_seconds, cfg.cache_seconds,
                                                  lambda: render(model, compute(now, inputs))))
            task.add_done_callback(_retrieved)
            if key is not None:
                self._flights[key] = task
                task.add_done_callback(lambda done: self._forget(key, done))
        try:
            body = await asyncio.wait_for(asyncio.shield(task), timeout=max(deadline - loop.time(), 0))
        except TimeoutError:
            raise busy() from None
        return Response(content=body, media_type="application/json")

    def _forget(self, key: Hashable, task: asyncio.Task) -> None:
        if self._flights.get(key) is task:
            del self._flights[key]

    def clear(self) -> None:
        self._entries.clear()

    def close(self) -> None:
        self.version.close()


def _retrieved(task: asyncio.Task) -> None:
    """Mark a failed computation's error as seen: every waiter may have given up already, and that is fine."""
    if not task.cancelled():
        task.exception()


async def serve(request: Request, model: Any, compute: Callable[[str, Any], Any],
                extra: Callable[[], Hashable] | None = None) -> Response:
    """A heavy view's answer: `compute(now, extra())` (run in a worker thread) as `model`, through the app's cache.
    `extra` is any input besides the store, the clock and the config; it runs in the worker thread too."""
    return await request.app.state.view_cache.serve(request, model, compute, extra)
