"""The heavy views' cache, single flight and queue (`outreach.api.views`).

A reused answer is the answer computed for its key; a commit on any other connection, a write through the API, a new
slice of the clock or a new day computes afresh; identical requests share one computation; at most `max_heavy` run
at once and a request that waits `queue_wait_seconds` answers 503 busy; health and pulse never wait behind them.
"""
import asyncio
import dataclasses
import threading
import time
import unittest
from unittest import mock

import httpx

from outreach.api import views
from outreach.api.clock import get_clock
from outreach.api.queries import shell as shell_queries
from tests import apiseed as s
from tests.apitest import BASE_URL, ORIGIN, ApiCase
from tests.plan_seed import seed_scored_company
from tests import test_api_shell

# Every route that goes through the cache, as the page asks for it.
CACHED = ("/api/shell", "/api/plan", "/api/plan/bench", "/api/needs-you", "/api/progress", "/api/wins", "/api/watch",
          "/api/outcomes/prompt", "/api/search/index", "/api/linkedin", "/api/outbox")


async def _plain(request, model, compute, extra=None):
    """`views.serve` without the cache: the route's model goes back to FastAPI, which renders it as it always did."""
    return compute(get_clock(request)(), extra() if extra else None)


class ViewCase(ApiCase):
    def cache(self) -> views.ViewCache:
        return self.app.state.view_cache

    def configure(self, **numbers):
        self.app.state.config = dataclasses.replace(self.app.state.config, **numbers)

    def body(self, path: str) -> bytes:
        res = self.get(path)
        self.assertEqual(res.status_code, 200, res.text)
        return res.content


class CacheTests(ViewCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.cache().close)

    def test_a_second_read_of_an_unchanged_store_is_the_stored_answer(self):
        first = self.body("/api/shell")
        second = self.body("/api/shell")
        self.assertEqual(first, second)
        self.assertEqual(self.cache().computed, 1)

    def test_a_different_query_string_is_a_different_answer(self):
        self.body("/api/shell")
        self.body("/api/shell?tab=2")
        self.assertEqual(self.cache().computed, 2)

    def test_a_commit_on_another_connection_computes_afresh(self):
        before = self.get("/api/shell").json()
        work = s.company(self.conn, "Work", "working")  # self.conn is the test's own connection: another writer
        s.send_first(self.conn, s.touch(self.conn, s.person(self.conn, work, "R1"), "replied", reply_kind="unsorted"),
                     "2026-11-05T15:00:00Z")
        after = self.get("/api/shell").json()
        self.assertEqual(before["unhandledReplies"], 0)
        self.assertEqual(after["unhandledReplies"], 1)
        self.assertEqual(self.cache().computed, 2)

    def test_a_write_through_the_api_computes_afresh(self):
        test_api_shell.Shell.seed(self)
        self.assertEqual(self.get("/api/shell").json()["winsUnseen"], 2)
        win = self.get("/api/wins").json()["wins"][0]["winId"]
        local = self.cache().version.local
        res = self.post("/api/wins/seen", {"winIds": [win]})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.cache().version.local, local + 1)  # bumped by run_write itself
        self.assertEqual(self.get("/api/shell").json()["winsUnseen"], 1)

    def test_the_local_counter_alone_computes_afresh(self):
        self.body("/api/wins")
        self.cache().version.bump()
        self.body("/api/wins")
        self.assertEqual(self.cache().computed, 2)

    def test_a_refused_write_still_bumps_the_counter(self):
        local = self.cache().version.local
        self.post("/api/wins/seen", {"winIds": "not a list"})  # refused before run_write: nothing to bump
        self.post("/api/watch/999/dismiss")  # run_write runs and refuses: not found
        self.assertEqual(self.cache().version.local, local + 1)

    def test_the_same_slice_of_the_clock_is_one_answer_and_the_next_slice_is_another(self):
        self.assertEqual(self.app.state.config.cache_seconds, 15)
        self.set_now("2026-11-10T15:14:00Z")  # the start of a 15 second slice
        first = self.get("/api/shell").json()
        self.set_now("2026-11-10T15:14:14Z")
        self.assertEqual(self.get("/api/shell").json(), first)  # still the first slice: the stored answer
        self.assertEqual(self.cache().computed, 1)
        self.set_now("2026-11-10T15:14:15Z")
        self.assertEqual(self.get("/api/shell").json()["asOf"], "2026-11-10T15:14:15Z")
        self.assertEqual(self.cache().computed, 2)

    def test_an_answer_older_than_cache_seconds_is_never_used(self):
        self.body("/api/wins")
        later = time.monotonic() + 16
        with mock.patch.object(views.time, "monotonic", return_value=later):
            self.body("/api/wins")
        self.assertEqual(self.cache().computed, 2)

    def test_the_old_mailer_check_is_part_of_the_key(self):
        self.body("/api/shell")
        self.old_sender = True
        self.body("/api/shell")
        self.assertEqual(self.cache().computed, 2)

    def test_a_changed_config_is_a_different_answer(self):
        self.body("/api/shell")
        self.configure(linkedin_per_day=3)
        self.body("/api/shell")
        self.assertEqual(self.cache().computed, 2)

    def test_an_unreadable_version_reuses_nothing(self):
        with mock.patch.object(views.StoreVersion, "read", return_value=None):
            self.assertEqual(self.body("/api/wins"), self.body("/api/wins"))
        self.assertEqual(self.cache().computed, 2)

    def test_a_failed_computation_is_not_stored(self):
        with mock.patch.object(shell_queries, "shell_summary", side_effect=RuntimeError("boom")):
            self.assertEqual(self.get("/api/shell").status_code, 500)
        self.body("/api/shell")
        self.assertEqual(self.cache().computed, 1)


class SameAnswerTests(ViewCase):
    """Every cached route answers exactly what FastAPI renders from the route's model without the cache."""

    def setUp(self):
        super().setUp()
        self.addCleanup(self.cache().close)
        test_api_shell.Shell.seed(self)
        for n, name in enumerate(("Tessera Labs", "Orbit Works", "Kite Data")):
            seed_scored_company(self.conn, name, 60 - n, ["level_role"])

    def test_cached_and_uncached_bodies_are_byte_identical(self):
        for path in CACHED:
            with self.subTest(path=path):
                with mock.patch.object(views, "serve", _plain):
                    plain = self.get(path)
                self.assertEqual(plain.status_code, 200, plain.text)
                computed = self.body(path)
                stored = self.body(path)
                self.assertEqual(computed, plain.content)
                self.assertEqual(stored, plain.content)
                self.assertEqual(self.get(path).headers["content-type"], plain.headers["content-type"])
        self.assertEqual(self.cache().computed, len(CACHED))


class Slow:
    """A stand-in for a query that takes `seconds`, counting its calls and how many ran at the same time."""

    def __init__(self, real, seconds: float):
        self.real, self.seconds = real, seconds
        self.calls = self.running = self.most = 0
        self.lock = threading.Lock()

    def __call__(self, *args, **kwargs):
        with self.lock:
            self.calls += 1
            self.running += 1
            self.most = max(self.most, self.running)
        try:
            time.sleep(self.seconds)
            return self.real(*args, **kwargs)
        finally:
            with self.lock:
                self.running -= 1


class ConcurrencyTests(ViewCase):
    """Many requests on one event loop, as a browser with several tabs sends them."""

    def setUp(self):
        super().setUp()
        self.addCleanup(self.cache().close)

    def run_together(self, paths: list[str]) -> list[tuple[httpx.Response, float]]:
        async def one(client, path):
            start = time.perf_counter()
            res = await client.get(path)
            return res, time.perf_counter() - start

        async def main():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url=BASE_URL, headers={"Origin": ORIGIN}) as c:
                got = await asyncio.gather(*(one(c, p) for p in paths))
            # A computation nobody waits for any more still finishes (the server's loop runs on; this one would end).
            await asyncio.gather(*(t for t in asyncio.all_tasks() if t is not asyncio.current_task()),
                                 return_exceptions=True)
            return got

        return asyncio.run(main())

    def slow_shell(self, seconds: float) -> Slow:
        slow = Slow(shell_queries.shell_summary, seconds)
        patcher = mock.patch.object(shell_queries, "shell_summary", slow)
        patcher.start()
        self.addCleanup(patcher.stop)
        return slow

    def test_identical_requests_share_one_computation(self):
        slow = self.slow_shell(0.3)
        got = self.run_together(["/api/shell"] * 12)
        self.assertEqual({res.status_code for res, _ in got}, {200})
        self.assertEqual(len({res.content for res, _ in got}), 1)
        self.assertEqual(slow.calls, 1)
        self.assertEqual(self.cache().computed, 1)

    def test_at_most_max_heavy_computations_run_at_once(self):
        self.configure(max_heavy=2)
        slow = self.slow_shell(0.2)
        got = self.run_together([f"/api/shell?n={n}" for n in range(6)])  # six different keys
        self.assertEqual({res.status_code for res, _ in got}, {200})
        self.assertEqual(slow.calls, 6)
        self.assertEqual(slow.most, 2)

    def test_a_request_that_waits_too_long_is_busy_never_a_hang(self):
        self.configure(max_heavy=1, queue_wait_seconds=1)
        self.slow_shell(0.6)  # the first is done at 0.6 s; the second starts then and is not done by 1 s
        got = self.run_together(["/api/shell?n=1", "/api/shell?n=2"])
        statuses = sorted(res.status_code for res, _ in got)
        self.assertEqual(statuses, [200, 503])
        busy, waited = next((res, t) for res, t in got if res.status_code == 503)
        self.assertEqual(busy.json()["code"], "busy")
        self.assertEqual(busy.json()["message"], views.BUSY_TEXT)
        self.assertEqual(busy.headers["retry-after"], "2")
        self.assertLess(waited, 1.4)

    def test_a_waiter_on_a_slow_shared_computation_is_busy_at_its_own_deadline(self):
        self.configure(queue_wait_seconds=1)
        self.slow_shell(1.5)
        got = self.run_together(["/api/shell"] * 3)
        self.assertEqual({res.status_code for res, _ in got}, {503})
        self.assertLess(max(t for _, t in got), 1.4)
        self.assertEqual(self.get("/api/shell").status_code, 200)  # the computation finished and filled the cache
        self.assertEqual(self.cache().computed, 1)

    def test_health_and_pulse_answer_at_once_while_heavy_views_wait(self):
        self.configure(max_heavy=1)
        self.slow_shell(0.6)
        got = self.run_together([f"/api/shell?n={n}" for n in range(4)] + ["/api/health", "/api/pulse"])
        heavy, (health, health_t), (pulse, pulse_t) = got[:4], got[4], got[5]
        self.assertEqual(health.status_code, 200, health.text)
        self.assertEqual(pulse.status_code, 200, pulse.text)
        self.assertGreater(max(t for _, t in heavy), 2.0)  # four in a row, one at a time
        self.assertLess(health_t, 0.5)
        self.assertLess(pulse_t, 0.5)


class RememberForTests(unittest.TestCase):
    def test_the_check_is_asked_once_per_period(self):
        calls, now = [], [100.0]

        def check():
            calls.append(1)
            return len(calls) % 2 == 1

        remembered = views.remember_for(check, 15, clock=lambda: now[0])
        self.assertTrue(remembered())
        now[0] = 114.9
        self.assertTrue(remembered())
        self.assertEqual(len(calls), 1)
        now[0] = 115.0
        self.assertFalse(remembered())
        self.assertEqual(len(calls), 2)


class SliceTests(unittest.TestCase):
    def test_clock_slice(self):
        self.assertEqual(views.clock_slice("2026-11-10T15:14:00Z", 15), views.clock_slice("2026-11-10T15:14:14Z", 15))
        self.assertEqual(views.clock_slice("2026-11-10T15:14:14Z", 15) + 1,
                         views.clock_slice("2026-11-10T15:14:15Z", 15))


if __name__ == "__main__":
    unittest.main()
