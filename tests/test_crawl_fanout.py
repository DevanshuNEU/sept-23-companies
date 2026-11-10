"""Listing boards on worker threads (#158, Task 1.3)."""
import threading
import time
import unittest
from collections import Counter

from outreach.crawl.base import ReadResult, Validators
from outreach.crawl.boardstate import BoardRow
from outreach.crawl.fanout import Listed, list_boards
from outreach.crawl.polite import HostTripped
from outreach.rules import RulesetNotCurrent

WORKERS = {"default": 2, "greenhouse": 6, "ashby": 4, "lever": 1, "workable": 1}
EMPTY = ReadResult("empty", (), Validators())


def board(i: int, ats: str = "greenhouse") -> BoardRow:
    return BoardRow(board_id=i, company_id=i, ats=ats, token=f"t{i}", last_read=None, empty_streak=0, error_streak=0,
                    streak_since=None, etag="", ids_fingerprint="", change_interval_s=None, next_read_at=None,
                    last_read_at=None, created_at="2026-11-01T00:00:00Z", found_by="test")


class FakeAdapter:
    """Counts the calls in flight per ATS and lets a test hook each list call."""

    def __init__(self, ats, stats, hook=None):
        self.ats, self.stats, self.hook = ats, stats, hook

    def list(self, token, validators):
        self.stats.enter(self.ats)
        try:
            if self.hook is not None:
                return self.hook(self.ats, token)
            time.sleep(0.002)
            return EMPTY
        finally:
            self.stats.leave(self.ats)

    def body(self, token, job):
        return None


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.now, self.peak = Counter(), Counter()
        self.threads: set[int] = set()

    def enter(self, ats):
        with self.lock:
            self.now[ats] += 1
            self.peak[ats] = max(self.peak[ats], self.now[ats])
            self.threads.add(threading.get_ident())

    def leave(self, ats):
        with self.lock:
            self.now[ats] -= 1


def run(boards, *, workers, hook=None, full=lambda b: False, should_stop=lambda: False, start=None, stats=None):
    stats = stats or Stats()
    start = start or (lambda b: (FakeAdapter(b.ats, stats, hook), object()))
    return list(list_boards(boards, start, full, workers=workers, should_stop=should_stop, clock=time.monotonic)), stats


class ListBoardsTests(unittest.TestCase):
    def test_inline_mode_keeps_board_order(self):
        boards = [board(i, ("lever", "greenhouse", "ashby")[i % 3]) for i in range(9)]
        listed, stats = run(boards, workers=None)
        self.assertEqual([x.board.board_id for x in listed], list(range(9)))
        self.assertEqual(stats.threads, {threading.get_ident()})
        self.assertTrue(all(x.result is EMPTY and x.tripped is None for x in listed))

    def test_every_board_yielded_once(self):
        ats = ("greenhouse", "ashby", "lever", "workable")
        boards = [board(i, ats[i % 4]) for i in range(40)]
        listed, stats = run(boards, workers=WORKERS)
        self.assertEqual(sorted(x.board.board_id for x in listed), list(range(40)))
        self.assertNotIn(threading.get_ident(), stats.threads)
        self.assertTrue(all(isinstance(x, Listed) and x.result is EMPTY and x.list_ms >= 0 for x in listed))

    def test_the_view_the_caller_made_comes_back_with_its_board(self):
        views = {}

        def start(b):
            views[b.board_id] = object()
            return FakeAdapter(b.ats, Stats()), views[b.board_id]

        listed, _ = run([board(i) for i in range(5)], workers=WORKERS, start=start)
        self.assertTrue(all(x.view is views[x.board.board_id] and x.adapter.ats == "greenhouse" for x in listed))

    def test_workers_per_ats_bounded(self):
        arrived, gate = [0], threading.Lock()
        barrier = threading.Barrier(6, timeout=10)

        def hook(ats, token):
            if ats == "greenhouse":
                with gate:
                    arrived[0] += 1
                    first_six = arrived[0] <= 6
                if first_six:
                    barrier.wait()  # six boards in flight at once, or this raises BrokenBarrierError
            else:
                time.sleep(0.005)
            return EMPTY

        boards = [board(i, "greenhouse") for i in range(12)] + [board(100 + i, "lever") for i in range(6)]
        listed, stats = run(boards, workers=WORKERS, hook=hook)
        self.assertEqual(len(listed), 18)
        self.assertEqual(stats.peak["greenhouse"], 6)
        self.assertEqual(stats.peak["lever"], 1)

    def test_an_ats_without_its_own_count_gets_the_default(self):
        listed, stats = run([board(i, "breezy") for i in range(10)], workers=WORKERS,
                            hook=lambda ats, token: (time.sleep(0.005), EMPTY)[1])
        self.assertEqual(len(listed), 10)
        self.assertLessEqual(stats.peak["breezy"], 2)

    def test_should_stop_starts_no_new_board(self):
        stop, calls = threading.Event(), []

        def hook(ats, token):
            calls.append(token)
            if len(calls) == 3:
                stop.set()
            return EMPTY

        boards = [board(i, "lever") for i in range(10)]
        listed, _ = run(boards, workers={"default": 1}, hook=hook, should_stop=stop.is_set)
        self.assertEqual([x.board.board_id for x in listed], [0, 1, 2])
        self.assertEqual(len(calls), 3)

    def test_should_stop_inline_too(self):
        stop, calls = threading.Event(), []

        def hook(ats, token):
            calls.append(token)
            if len(calls) == 2:
                stop.set()
            return EMPTY

        listed, _ = run([board(i) for i in range(6)], workers=None, hook=hook, should_stop=stop.is_set)
        self.assertEqual([x.board.board_id for x in listed], [0, 1])

    def test_host_tripped_is_yielded_not_raised(self):
        def hook(ats, token):
            raise HostTripped("lever.co")

        for workers in (None, WORKERS):
            with self.subTest(workers=workers):
                listed, _ = run([board(i, "lever") for i in range(3)], workers=workers, hook=hook)
                self.assertEqual(len(listed), 3)
                for x in listed:
                    self.assertEqual(x.tripped, "lever.co")
                    self.assertIsNone(x.result)
                    self.assertIsNotNone(x.adapter)

    def test_start_and_full_called_on_calling_thread(self):
        main = threading.get_ident()
        seen = {"start": set(), "full": set()}
        stats = Stats()

        def start(b):
            seen["start"].add(threading.get_ident())
            return FakeAdapter(b.ats, stats), object()

        def full(b):
            seen["full"].add(threading.get_ident())
            return False

        listed, _ = run([board(i, ("greenhouse", "ashby")[i % 2]) for i in range(8)], workers=WORKERS, start=start,
                        full=full, stats=stats)
        self.assertEqual(len(listed), 8)
        self.assertEqual(seen, {"start": {main}, "full": {main}})
        self.assertNotIn(main, stats.threads)

    def test_the_full_flag_reaches_the_adapter_as_empty_validators(self):
        validators = {}

        class Adapter(FakeAdapter):
            def list(self, token, v):
                validators[token] = v
                return EMPTY

        boards = [board(1), board(2)]
        boards = [BoardRow(**{**b.__dict__, "etag": "abc"}) for b in boards]
        list(list_boards(boards, lambda b: (Adapter("greenhouse", Stats()), object()), lambda b: b.board_id == 1,
                         workers=WORKERS, should_stop=lambda: False, clock=time.monotonic))
        self.assertEqual(validators["t1"], Validators())
        self.assertEqual(validators["t2"].etag, "abc")

    def test_a_board_with_no_adapter_is_yielded_without_a_result(self):
        stats = Stats()

        def start(b):
            return (None, object()) if b.ats == "icims" else (FakeAdapter(b.ats, stats), object())

        boards = [board(1, "icims"), board(2, "greenhouse")]
        for workers in (None, WORKERS):
            with self.subTest(workers=workers):
                listed, _ = run(boards, workers=workers, start=start, stats=stats)
                by_id = {x.board.board_id: x for x in listed}
                self.assertEqual(set(by_id), {1, 2})
                self.assertIsNone(by_id[1].adapter)
                self.assertIsNone(by_id[1].result)
                self.assertIsNone(by_id[1].tripped)
                self.assertIs(by_id[2].result, EMPTY)

    def test_an_unexpected_failure_reaches_the_calling_thread_and_stops_the_workers(self):
        before = threading.active_count()

        def hook(ats, token):
            if token == "t3":
                raise RulesetNotCurrent("changed under us")
            time.sleep(0.002)
            return EMPTY

        boards = [board(i, "greenhouse") for i in range(30)]
        stats = Stats()
        gen = list_boards(boards, lambda b: (FakeAdapter(b.ats, stats, hook), object()), lambda b: False,
                          workers=WORKERS, should_stop=lambda: False, clock=time.monotonic)
        with self.assertRaises(RulesetNotCurrent):
            list(gen)
        self.assertEqual(threading.active_count(), before, "no worker is left running")
        self.assertLess(sum(stats.peak.values()), 30)

    def test_an_unexpected_failure_in_start_surfaces_and_leaves_no_thread(self):
        before = threading.active_count()

        def start(b):
            if b.board_id == 5:
                raise RuntimeError("store read failed")
            return FakeAdapter(b.ats, Stats()), object()

        with self.assertRaises(RuntimeError):
            list(list_boards([board(i) for i in range(10)], start, lambda b: False, workers=WORKERS,
                             should_stop=lambda: False, clock=time.monotonic))
        self.assertEqual(threading.active_count(), before)

    def test_closing_the_generator_early_joins_the_workers(self):
        before = threading.active_count()
        gen = list_boards([board(i) for i in range(30)], lambda b: (FakeAdapter(b.ats, Stats()), object()),
                          lambda b: False, workers=WORKERS, should_stop=lambda: False, clock=time.monotonic)
        next(gen)
        gen.close()
        self.assertEqual(threading.active_count(), before)

    def test_no_boards_yields_nothing(self):
        for workers in (None, WORKERS):
            listed, _ = run([], workers=workers)
            self.assertEqual(listed, [])


if __name__ == "__main__":
    unittest.main()
