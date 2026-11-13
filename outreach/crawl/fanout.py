"""List many boards at once on worker threads, one queue per ATS (#158).

Only the list request runs on a worker. `start` and `full` run on the calling thread before a board is queued, and
every store write stays with the caller, who reads each `Listed` as it is yielded. The politeness (spacing per host,
the breaker) lives in the shared `PoliteClient`, so more threads never send faster than the config allows.

A worker that meets something unexpected (anything `list_board` does not turn into an error read, such as a
`RulesetNotCurrent`) hands it to the calling thread, which raises it from the generator after telling the other
workers to start no new board and joining them. Boards in flight at that moment are lost, not written, so they are
read again next tick.
"""
from __future__ import annotations

import queue
import threading
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from outreach.crawl.base import Adapter, ReadResult
from outreach.crawl.boardstate import BoardRow
from outreach.crawl.ingest import list_board
from outreach.crawl.polite import HostTripped

__all__ = ["Listed", "list_boards"]


@dataclass(frozen=True)
class Listed:
    board: BoardRow
    adapter: Adapter | None  # None when no adapter reads this ATS
    view: object | None  # the per-board client the caller made (its first_bucket and requests)
    result: ReadResult | None  # None when the list request was refused
    tripped: str | None  # the bucket of a HostTripped, else None
    list_ms: int


@dataclass(frozen=True)
class _Work:
    board: BoardRow
    adapter: Adapter
    view: object
    full: bool
    head: bool = False


@dataclass(frozen=True)
class _Failed:
    error: BaseException


_END = object()  # on a work queue: this worker has no more boards
_DONE = object()  # on the result queue: one worker has finished


def _list_one(work: _Work, clock: Callable[[], float]) -> Listed:
    began = clock()
    try:
        result = list_board(work.adapter, work.board, full=work.full, head=work.head)
    except HostTripped as e:
        return Listed(work.board, work.adapter, work.view, None, e.bucket, round((clock() - began) * 1000))
    return Listed(work.board, work.adapter, work.view, result, None, round((clock() - began) * 1000))


def list_boards(boards: Sequence[BoardRow], start: Callable[[BoardRow], tuple[Adapter | None, object]],
                full: Callable[[BoardRow], bool], *, workers: Mapping[str, int] | None,
                head: Callable[[BoardRow], bool] = lambda board: False,
                should_stop: Callable[[], bool], clock: Callable[[], float]) -> Iterator[Listed]:
    """Yield one `Listed` per board listed. `workers=None` lists inline, in board order, on the calling thread.
    Otherwise each ATS gets a queue (in the order its first board appears) and `workers.get(ats, workers["default"])`
    threads, and results come as they finish. `should_stop` is asked before each board starts; a board never started
    is never yielded."""
    if workers is None:
        for board in boards:
            if should_stop():
                return
            adapter, view = start(board)
            if adapter is None:
                yield Listed(board, None, view, None, None, 0)
                continue
            yield _list_one(_Work(board, adapter, view, f := full(board), not f and head(board)), clock)
        return
    yield from _fan_out(boards, start, full, workers, should_stop, clock, head)


def _fan_out(boards: Sequence[BoardRow], start, full, workers: Mapping[str, int], should_stop,
             clock, head=lambda board: False) -> Iterator[Listed]:
    results: queue.Queue = queue.Queue()
    abort = threading.Event()
    queues: dict[str, queue.Queue] = {}
    counts: dict[str, int] = {}
    threads: list[threading.Thread] = []
    wanted = Counter(b.ats for b in boards)

    def work(q: queue.Queue) -> None:
        try:
            while True:
                item = q.get()
                if item is _END:
                    return
                if abort.is_set() or should_stop():
                    continue  # started nothing; keep draining to the end marker
                results.put(_list_one(item, clock))
        except BaseException as e:  # nothing unexpected may vanish in a worker
            abort.set()
            results.put(_Failed(e))
        finally:
            results.put(_DONE)

    def stop_workers() -> None:
        abort.set()
        for ats, q in queues.items():
            for _ in range(counts[ats]):
                q.put(_END)
        queues.clear()

    try:
        for board in boards:
            if abort.is_set() or should_stop():
                break
            adapter, view = start(board)
            if adapter is None:
                results.put(Listed(board, None, view, None, None, 0))
                continue
            item = _Work(board, adapter, view, f := full(board), not f and head(board))
            q = queues.get(board.ats)
            if q is None:
                q = queues[board.ats] = queue.Queue()
                counts[board.ats] = min(wanted[board.ats], workers.get(board.ats, workers["default"]))
                for n in range(counts[board.ats]):
                    t = threading.Thread(target=work, args=(q,), name=f"list-{board.ats}-{n}", daemon=True)
                    threads.append(t)
                    t.start()
            q.put(item)
        # Every board is queued: tell the workers to finish once the queues run dry (abort stays unset).
        for ats, q in queues.items():
            for _ in range(counts[ats]):
                q.put(_END)
        queues.clear()
        alive = len(threads)
        while alive or not results.empty():  # no thread at all when no board has an adapter
            item = results.get()
            if item is _DONE:
                alive -= 1
            elif isinstance(item, _Failed):
                raise item.error
            else:
                yield item
    finally:
        stop_workers()
        for t in threads:
            t.join()
