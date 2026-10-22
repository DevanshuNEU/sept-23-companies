"""The 30-minute tick: due boards in a fixed order, a budget, a lock, a ruleset check and a host breaker (#21, Task 12).

A fresh store, one `FakeClient` routed per board, and a fake clock. Nothing here touches the network or the live
store.
"""
import dataclasses
import json
import shutil
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from outreach.crawl import tick
from outreach.crawl.cadence import add_seconds
from outreach.crawl.config import load_crawl_config
from outreach.crawl.ingest import read_board
from outreach.crawl.polite import PoliteClient
from outreach.crawl.tick import (
    AlreadyRunning, TickReport, ensure_ruleset_current, read_company_boards, resolve_combiner, run_tick, tick_lock)
from outreach.net import Response
from outreach.rules import ruleset_version
from tests.fakes import FakeClient, status_response, text_response
from tests.storetest import fresh_store, seed_board, seed_company

FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
JOBS = (FIXTURES / "greenhouse_jobs.json").read_bytes()
T0 = "2026-10-22T08:00:00Z"
TODAY = "2026-10-22"  # T0 is 04:00 in New York
FLOOR = 21600


def gh(token: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


def gh_eu(token: str) -> str:
    return f"https://boards-api.eu.greenhouse.io/v1/boards/{token}/jobs?content=true"


def lever(token: str) -> str:
    return f"https://api.lever.co/v0/postings/{token}?mode=json"


def ok(etag='"A"'):
    return Response(200, "", JOBS, {"etag": etag})


def at(seconds: int) -> str:
    return add_seconds(T0, seconds)


class Clock:
    """A monotonic clock the test moves; `sleep` moves it too, so the polite spacing never really waits."""

    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class TickCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.cfg = load_crawl_config()
        self.clock = Clock()
        self.client = None

    def board(self, token, *, ats="greenhouse", company=None, **state):
        cid = company or seed_company(self.conn, f"Company {token}")
        bid = seed_board(self.conn, cid, ats, token)
        if state:
            sets = ", ".join(f"{k} = ?" for k in state)
            self.conn.execute(f"UPDATE board SET {sets} WHERE board_id = ?", (*state.values(), bid))
        return bid

    def read_before(self, bid, seconds_ago, *, result="ok", next_in=None, error_streak=0):
        """Give the board a past read `seconds_ago` with its next read `next_in` seconds from T0."""
        then = at(-seconds_ago)
        self.conn.execute(
            "UPDATE board SET last_read = ?, last_read_at = ?, streak_since = ?, error_streak = ?, next_read_at = ? "
            "WHERE board_id = ?",
            (result, then, then, error_streak, at(next_in) if next_in is not None else None, bid))

    def tick(self, routes, *, cfg=None, **kw) -> TickReport:
        self.client = FakeClient(routes)
        kw.setdefault("now_fn", lambda: T0)
        report = run_tick(self.conn, self.client, cfg=cfg or self.cfg, clock=self.clock, sleep=self.clock.sleep, **kw)
        self.assertEqual(self.client.unrouted, [])
        return report

    def row(self, bid):
        return self.conn.execute("SELECT * FROM board WHERE board_id = ?", (bid,)).fetchone()

    def reads(self, bid=None):
        sql = "SELECT * FROM board_read" + (" WHERE board_id = ?" if bid else "")
        return self.conn.execute(sql, (bid,) if bid else ()).fetchall()

    def jobs(self, job=None):
        sql = "SELECT * FROM job_run" + (" WHERE job = ?" if job else "") + " ORDER BY run_id"
        return self.conn.execute(sql, (job,) if job else ()).fetchall()

    def count(self, table):
        return self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def plan(self, company_id, rank):
        self.conn.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES (?, ?, 10) "
                          "ON CONFLICT DO NOTHING", (TODAY, T0))
        self.conn.execute(
            "INSERT INTO daily_list_entry(list_date, company_id, list_group, rank, state, proposed_at) "
            "VALUES (?, ?, 'level_role', ?, 'proposed', ?)", (TODAY, company_id, rank, T0))

    def company_of(self, bid):
        return self.row(bid)["company_id"]


class SelectionTests(TickCase):
    def test_tick_reads_only_due_boards(self):
        due = self.board("due")
        later = self.board("later")
        self.read_before(later, 3600, next_in=3600)
        past = self.board("past")
        self.read_before(past, 30000, next_in=-60)
        report = self.tick({gh("due"): ok(), gh("past"): ok()})
        self.assertEqual(sorted(self.client.requests), sorted([gh("due"), gh("past")]))
        self.assertEqual((report.read, report.ok), (2, 2))
        self.assertEqual(self.row(later)["last_read_at"], at(-3600))  # untouched
        self.assertEqual(self.row(due)["last_read"], "ok")

    def test_a_board_with_no_adapter_is_not_touched(self):
        bid = self.board("acme-icims.icims.com", ats="icims")
        report = self.tick({})
        self.assertEqual(self.client.requests, [])
        self.assertEqual(report.read, 0)
        row = self.row(bid)
        self.assertIsNone(row["last_read"])
        self.assertIsNone(row["next_read_at"])

    def test_plan_company_boards_are_read_first(self):
        a = self.board("alpha")
        self.read_before(a, 30000, next_in=-600)
        b = self.board("beta")  # never read
        c = self.board("gamma")
        self.read_before(c, 30000, next_in=86400)  # not due, but its company is on today's plan
        self.plan(self.company_of(c), 1)
        self.tick({gh("alpha"): ok(), gh("beta"): ok(), gh("gamma"): ok()})
        self.assertEqual(self.client.requests, [gh("gamma"), gh("beta"), gh("alpha")])
        self.assertEqual(self.row(b)["last_read"], "ok")

    def test_after_a_long_gap_the_order_is_plan_then_never_read_then_oldest(self):
        # Review Focus 5: a Mac asleep for days. Every board is overdue; the order is fixed.
        old = self.board("old")
        self.read_before(old, 5 * 86400, next_in=-4 * 86400)
        older = self.board("older")
        self.read_before(older, 6 * 86400, next_in=-5 * 86400)
        fresh = self.board("fresh")  # never read
        planned = self.board("planned")
        self.read_before(planned, 2 * 86400, next_in=-86400)
        self.plan(self.company_of(planned), 1)
        routes = {gh(t): ok() for t in ("old", "older", "fresh", "planned")}
        self.tick(routes)
        self.assertEqual(self.client.requests, [gh("planned"), gh("fresh"), gh("older"), gh("old")])
        self.assertEqual(self.row(fresh)["last_read"], "ok")

    def test_budget_stops_the_tick_and_leaves_the_rest_due(self):
        # Review Focus 5: each request takes 1000 s on the fake clock; the budget is 1500 s.
        tokens = ["b1", "b2", "b3", "b4", "b5"]
        ids = [self.board(t) for t in tokens]

        def slow(method, url, headers, body):
            self.clock.t += 1000
            return ok()

        report = self.tick({gh(t): slow for t in tokens}, cfg=dataclasses.replace(self.cfg, tick_budget_s=1500))
        self.assertEqual(self.client.requests, [gh("b1"), gh("b2")])
        self.assertEqual(report.read, 2)
        for bid in ids[2:]:
            row = self.row(bid)
            self.assertIsNone(row["last_read"])
            self.assertIsNone(row["next_read_at"])  # still due
        self.assertGreaterEqual(report.seconds, 2000)

    def test_limit_caps_the_boards_started(self):
        for t in ("b1", "b2", "b3"):
            self.board(t)
        report = self.tick({gh("b1"): ok(), gh("b2"): ok()}, limit=2)
        self.assertEqual(report.read, 2)


class JobRunTests(TickCase):
    def test_tick_records_a_read_boards_job_run_with_counts(self):
        self.board("good")
        self.board("gone")
        report = self.tick({gh("good"): ok(), gh("gone"): [status_response(404)], gh_eu("gone"): status_response(404)})
        job, = self.jobs("read_boards")
        self.assertEqual(job["status"], "ok")
        self.assertEqual(job["ruleset_version"], ruleset_version())
        self.assertEqual(report.run_id, job["run_id"])
        summary = json.loads(job["summary"])
        self.assertEqual((summary["read"], summary["ok"], summary["error"], summary["new"]), (2, 1, 1, 3))
        self.assertEqual((report.read, report.ok, report.error, report.new, report.deferred, report.failed),
                         (2, 1, 1, 3, 0, 0))
        self.assertNotIn("queued", summary)  # the crawler writes no queue row (ruling 2)
        self.assertFalse(report.rebuilt)
        self.assertEqual({r["run_id"] for r in self.reads()}, {job["run_id"]})

    def test_wal_checkpoint_runs_even_after_an_exception(self):
        self.board("acme")
        seen = []
        self.conn.set_trace_callback(seen.append)
        self.addCleanup(self.conn.set_trace_callback, None)
        with mock.patch.object(tick, "due_boards", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.tick({})
        self.assertIn("PRAGMA wal_checkpoint(PASSIVE)", seen)
        job, = self.jobs("read_boards")
        self.assertEqual(job["status"], "failed")
        self.assertIn("boom", job["error"])

    def test_wal_checkpoint_runs_after_a_good_tick(self):
        self.board("acme")
        seen = []
        self.conn.set_trace_callback(seen.append)
        self.addCleanup(self.conn.set_trace_callback, None)
        self.tick({gh("acme"): ok()})
        self.assertEqual(seen[-1], "PRAGMA wal_checkpoint(PASSIVE)")

    def test_no_connection_transaction_is_open_during_a_request(self):
        for t in ("a1", "a2"):
            self.board(t)

        def check(method, url, headers, body):
            assert not self.conn.in_transaction, f"a transaction is open during {url}"
            return ok()

        report = self.tick({gh("a1"): check, gh("a2"): check})
        self.assertEqual(report.ok, 2)

    def test_one_polite_client_serves_the_tick(self):
        built = []

        class Spy(PoliteClient):
            def __init__(self, *a, **kw):
                built.append(1)
                super().__init__(*a, **kw)

        for t in ("a1", "a2"):
            self.board(t)
        with mock.patch.object(tick, "PoliteClient", Spy):
            self.tick({gh("a1"): ok(), gh("a2"): ok()})
            self.assertEqual(built, [1])
            # A client that already is one is used as is.
            polite = Spy(FakeClient({gh("a3"): ok()}), self.cfg, clock=self.clock, sleep=self.clock.sleep)
            self.board("a3")
            run_tick(self.conn, polite, cfg=self.cfg, now_fn=lambda: at(60), clock=self.clock)
        self.assertEqual(built, [1, 1])
        self.assertEqual(polite.inner.requests, [gh("a3")])


class RulesetTests(TickCase):
    def make_other_ruleset_current(self):
        self.conn.execute("INSERT INTO ruleset(ruleset_version, config_toml, created_at) VALUES "
                          "('r-000000000000', '', ?)", (T0,))
        self.conn.execute("UPDATE ruleset SET is_current = 0 WHERE is_current = 1")
        self.conn.execute("UPDATE ruleset SET is_current = 1 WHERE ruleset_version = 'r-000000000000'")

    def current(self):
        return self.conn.execute("SELECT ruleset_version FROM ruleset WHERE is_current = 1").fetchone()[0]

    def test_ruleset_change_rebuilds_before_crawling(self):
        self.board("acme")
        self.make_other_ruleset_current()
        jobs_before = len(self.jobs())
        report = self.tick({gh("acme"): ok()})
        self.assertTrue(report.rebuilt)
        self.assertEqual(self.current(), ruleset_version())
        self.assertEqual([j["job"] for j in self.jobs()[jobs_before:]], ["rebuild", "read_boards", "watch_pages"])
        self.assertEqual(report.ok, 1)
        self.assertEqual(self.count("current_decision"), 3)  # decided under the code's ruleset

    def test_a_current_ruleset_runs_no_rebuild(self):
        before = len(self.jobs("rebuild"))
        self.assertFalse(ensure_ruleset_current(self.conn))
        self.assertEqual(len(self.jobs("rebuild")), before)

    def test_ruleset_change_rebuild_uses_the_combiner(self):
        # Ruling 10: the rebuild at the start of a tick and every read get the reader's combiner. rules.rebuild and
        # decide_and_store take no combiner yet (#32 adds it), so the spies record it and call the real ones without.
        sentinel = object()
        combine = types.ModuleType("outreach.reader.combine")
        combine.reader_combiner = lambda conn: sentinel
        got = {"rebuild": [], "read": []}
        real_rebuild = tick.rebuild

        def rebuild_spy(conn, **kw):
            got["rebuild"].append(kw.pop("combiner", None))
            return real_rebuild(conn, **kw)

        def read_spy(conn, board, adapter, **kw):
            got["read"].append(kw.get("combiner"))
            return read_board(conn, board, adapter, **{**kw, "combiner": None})

        self.board("acme")
        self.make_other_ruleset_current()
        with mock.patch.dict(sys.modules, {"outreach.reader.combine": combine}), \
                mock.patch.object(tick, "rebuild", rebuild_spy), mock.patch.object(tick, "read_board", read_spy):
            self.assertIs(resolve_combiner(self.conn), sentinel)
            report = self.tick({gh("acme"): ok()})
        self.assertTrue(report.rebuilt)
        self.assertEqual(got, {"rebuild": [sentinel], "read": [sentinel]})

    def test_a_tick_with_no_reader_package_still_runs(self):
        self.board("acme")
        self.make_other_ruleset_current()
        with mock.patch.dict(sys.modules, {"outreach.reader": None, "outreach.reader.combine": None}):
            self.assertIsNone(resolve_combiner(self.conn))
            report = self.tick({gh("acme"): ok()})
        self.assertTrue(report.rebuilt)
        self.assertEqual(report.ok, 1)

    def test_no_combiner_module_yet_means_none(self):
        with mock.patch.dict(sys.modules, {"outreach.reader.combine": None}):
            self.assertIsNone(resolve_combiner(self.conn))

    def test_a_broken_reader_module_is_not_hidden(self):
        # Only a missing reader means "no combiner"; an import error inside it is a bug and propagates.
        with mock.patch("importlib.import_module", side_effect=ModuleNotFoundError("no yaml", name="yaml")):
            with self.assertRaises(ModuleNotFoundError):
                resolve_combiner(self.conn)

    def test_a_ruleset_change_during_the_tick_stops_it(self):
        for t in ("a1", "a2", "a3"):
            self.board(t)
        calls = []

        def read_spy(conn, board, adapter, **kw):
            calls.append(board.token)
            if board.token == "a2":
                from outreach.rules import RulesetNotCurrent
                raise RulesetNotCurrent("changed")
            return read_board(conn, board, adapter, **kw)

        with mock.patch.object(tick, "read_board", read_spy):
            report = self.tick({gh("a1"): ok(), gh("a2"): ok()})
        self.assertEqual(calls, ["a1", "a2"])
        self.assertEqual((report.ok, report.deferred), (1, 1))
        job, = self.jobs("read_boards")
        self.assertEqual(job["status"], "partial")
        a2 = self.conn.execute("SELECT * FROM board WHERE token = 'a2'").fetchone()
        self.assertEqual((a2["last_read"], a2["error_streak"], a2["next_read_at"]),
                         (None, 0, at(self.cfg.defer_s)))
        a3 = self.conn.execute("SELECT * FROM board WHERE token = 'a3'").fetchone()
        self.assertIsNone(a3["next_read_at"])  # never started, still due


class BreakerTests(TickCase):
    """Review Focus 3: one ATS outage must not raise dozens of Needs-you items."""

    def outage(self, answer, n=6, failures=3):
        ids = [self.board(f"g{i}") for i in range(n)]
        cfg = dataclasses.replace(self.cfg, breaker_failures=failures)
        report = self.tick({gh(f"g{i}"): answer for i in range(n)}, cfg=cfg)
        return ids, report

    def assert_deferred(self, bid):
        row = self.row(bid)
        self.assertEqual((row["last_read"], row["error_streak"], row["next_read_at"]),
                         (None, 0, at(self.cfg.defer_s)))
        self.assertEqual(self.reads(bid), [])

    def test_host_outage_defers_the_rest_without_streaks(self):
        ids, report = self.outage(status_response(503))
        self.assertEqual(len(self.client.requests), 3)  # the breaker opened after three: nothing more was sent
        # The three error reads came from the bucket the breaker tripped, so they too are deferrals: an outage is
        # not a finding about any one board.
        for bid in ids:
            self.assert_deferred(bid)
        self.assertEqual((report.error, report.deferred, report.read), (0, 6, 0))
        self.assertEqual(self.count("review_item"), 0)
        self.assertEqual(report.tripped, ("greenhouse.io",))
        job, = self.jobs("read_boards")
        self.assertEqual(json.loads(job["summary"])["tripped"], ["greenhouse.io"])

    def test_an_outage_over_many_ticks_raises_no_unreadable_item(self):
        ids = [self.board(f"g{i}") for i in range(6)]
        cfg = dataclasses.replace(self.cfg, breaker_failures=3)
        for n in range(5):
            self.client = FakeClient({gh(f"g{i}"): status_response(503) for i in range(6)})
            run_tick(self.conn, self.client, cfg=cfg, now_fn=lambda n=n: at(n * self.cfg.defer_s), clock=self.clock,
                     sleep=self.clock.sleep)
        self.assertEqual(self.count("review_item"), 0)
        self.assertEqual({self.row(b)["error_streak"] for b in ids}, {0})

    def test_wrong_shape_200s_trip_the_bucket_too(self):
        # A challenge page on every board is a 200 the polite breaker counts as healthy; the tick counts it.
        ids, report = self.outage(text_response("<html>Just a moment...</html>"))
        self.assertEqual(len(self.client.requests), 3)
        for bid in ids:
            self.assert_deferred(bid)
        self.assertEqual((report.error, report.deferred), (0, 6))
        self.assertEqual(self.count("review_item"), 0)

    def test_a_blanket_4xx_trips_the_bucket(self):
        ids, report = self.outage(status_response(401))
        self.assertEqual(len(self.client.requests), 3)
        self.assertEqual(report.deferred, 6)

    def test_dead_boards_answering_404_are_errors_not_an_outage(self):
        # A 404 is about the board (it moved or closed), so six of them in a row are six error reads.
        ids = [self.board(f"g{i}") for i in range(6)]
        cfg = dataclasses.replace(self.cfg, breaker_failures=3)
        routes = {}
        for i in range(6):
            routes[gh(f"g{i}")] = status_response(404)
            routes[gh_eu(f"g{i}")] = status_response(404)
        report = self.tick(routes, cfg=cfg)
        self.assertEqual((report.error, report.deferred), (6, 0))
        for bid in ids:
            row = self.row(bid)
            self.assertEqual((row["last_read"], row["error_streak"]), ("error", 1))
            self.assertEqual(row["next_read_at"], at(FLOOR))
            self.assertEqual(len(self.reads(bid)), 1)

    def test_errors_below_the_breaker_are_written_as_error_reads(self):
        a, b, c = (self.board(t) for t in ("a", "b", "c"))
        cfg = dataclasses.replace(self.cfg, breaker_failures=3)
        report = self.tick({gh("a"): status_response(503), gh("b"): ok(), gh("c"): status_response(503)}, cfg=cfg)
        self.assertEqual((report.ok, report.error, report.deferred), (1, 2, 0))
        for bid in (a, c):
            row = self.row(bid)
            self.assertEqual((row["last_read"], row["error_streak"]), ("error", 1))
            read, = self.reads(bid)
            self.assertEqual((read["result"], read["http_status"]), ("error", 503))

    def test_a_good_read_between_wrong_shapes_resets_the_count(self):
        # Two challenge pages, a good board, two more: never three in a row, so these are four boards' own errors.
        for t in ("a", "b", "c", "d", "e"):
            self.board(t)
        page = text_response("<html>Just a moment...</html>")
        routes = {gh("a"): page, gh("b"): page, gh("c"): ok(), gh("d"): page, gh("e"): page}
        report = self.tick(routes, cfg=dataclasses.replace(self.cfg, breaker_failures=3))
        self.assertEqual((report.ok, report.error, report.deferred, report.tripped), (1, 4, 0, ()))

    def test_an_outage_in_one_bucket_leaves_another_alone(self):
        g = [self.board(f"g{i}") for i in range(4)]
        lv = self.board("lv", ats="lever")
        cfg = dataclasses.replace(self.cfg, breaker_failures=3)
        routes = {gh(f"g{i}"): status_response(503) for i in range(4)}
        routes[lever("lv")] = Response(200, "", b"[]", {})
        report = self.tick(routes, cfg=cfg)
        self.assertEqual(self.row(lv)["last_read"], "empty")
        self.assertEqual((report.empty, report.deferred), (1, 4))
        for bid in g:
            self.assert_deferred(bid)

    def test_a_trip_during_body_fetches_defers_the_rest_of_the_bucket(self):
        ids = [self.board(f"g{i}") for i in range(3)]
        real = tick.read_board

        def tripping(conn, board, adapter, **kw):
            out = real(conn, board, adapter, **kw)
            return dataclasses.replace(out, tripped="greenhouse.io")

        with mock.patch.object(tick, "read_board", tripping):
            report = self.tick({gh("g0"): ok()})
        self.assertEqual(self.client.requests, [gh("g0")])
        self.assertEqual(self.row(ids[0])["last_read"], "ok")  # the read itself counts
        for bid in ids[1:]:
            self.assert_deferred(bid)
        self.assertEqual((report.ok, report.deferred), (1, 2))


class FailedBoardTests(TickCase):
    def test_a_board_that_crashes_ingest_is_failed_not_errored(self):
        a, b, c = (self.board(t) for t in ("a", "b", "c"))
        real = tick.read_board

        def crash(conn, board, adapter, **kw):
            if board.token == "b":
                raise sqlite3.IntegrityError("CHECK constraint failed: posting")
            return real(conn, board, adapter, **kw)

        with mock.patch.object(tick, "read_board", crash):
            report = self.tick({gh(t): ok() for t in ("a", "b", "c")})
        self.assertEqual((report.ok, report.failed, report.error), (2, 1, 0))
        job, = self.jobs("read_boards")
        self.assertEqual(job["status"], "partial")
        self.assertIn("IntegrityError", job["error"])
        summary = json.loads(job["summary"])
        self.assertEqual(summary["failed"], 1)
        row = self.row(b)
        self.assertEqual((row["last_read"], row["error_streak"], row["empty_streak"]), (None, 0, 0))
        self.assertEqual(row["next_read_at"], at(FLOOR))
        self.assertEqual(self.reads(b), [])
        for bid in (a, c):
            self.assertEqual(self.row(bid)["last_read"], "ok")

    def test_a_failed_board_keeps_its_streaks_and_waits_its_error_backoff(self):
        bid = self.board("b")
        self.read_before(bid, 30000, result="error", next_in=-60, error_streak=2)
        with mock.patch.object(tick, "read_board", side_effect=ValueError("bug")):
            report = self.tick({gh("b"): ok()})
        self.assertEqual(report.failed, 1)
        row = self.row(bid)
        self.assertEqual((row["last_read"], row["error_streak"]), ("error", 2))
        self.assertEqual(row["next_read_at"], at(86400))  # the third error's backoff


CAREERS = (FIXTURES / "careers_a.html").read_text(encoding="utf-8")
PAGE = "https://acme.example/careers"


class WatchPassTests(TickCase):
    """Task 14's watched-page pass runs after the boards, through the same PoliteClient, as its own job run."""

    def watch(self, company):
        self.conn.execute("INSERT INTO watched_page(company_id, url) VALUES (?, ?)", (company, PAGE))

    def test_the_watched_page_pass_runs_after_the_boards(self):
        self.board("acme")
        self.watch(seed_company(self.conn, "Paged"))
        report = self.tick({gh("acme"): ok(), PAGE: text_response(CAREERS)})
        self.assertEqual(self.client.requests, [gh("acme"), PAGE])
        self.assertEqual((report.ok, report.watch.baseline), (1, 1))
        self.assertEqual([j["job"] for j in self.jobs()][-2:], ["read_boards", "watch_pages"])
        self.assertEqual(self.jobs("watch_pages")[0]["status"], "ok")

    def test_the_pass_shares_the_tick_breaker(self):
        # A page on a host the boards tripped is deferred, not fetched.
        for i in range(3):
            self.board(f"g{i}")
        company = seed_company(self.conn, "Paged")
        self.conn.execute("INSERT INTO watched_page(company_id, url) VALUES (?, ?)",
                          (company, "https://boards.greenhouse.io/paged"))
        cfg = dataclasses.replace(self.cfg, breaker_failures=3)
        report = self.tick({gh(f"g{i}"): status_response(503) for i in range(3)}, cfg=cfg)
        self.assertEqual(report.watch.deferred, 1)
        self.assertEqual(len(self.client.requests), 3)

    def test_a_watch_failure_is_reported_and_the_board_reads_stay(self):
        self.board("acme")
        with mock.patch.object(tick, "watch_pages", side_effect=RuntimeError("watch bug")):
            report = self.tick({gh("acme"): ok()})
        self.assertEqual(report.ok, 1)
        self.assertIsNone(report.watch)
        self.assertIn("watch bug", report.watch_error)
        self.assertEqual(self.jobs("read_boards")[0]["status"], "ok")

    def test_read_company_boards_runs_no_watch_pass(self):
        company = seed_company(self.conn, "Acme")
        self.board("acme", company=company)
        self.watch(company)
        self.client = FakeClient({gh("acme"): ok()})
        report = read_company_boards(self.conn, self.client, [company], cfg=self.cfg, now_fn=lambda: T0)
        self.assertEqual(self.client.requests, [gh("acme")])
        self.assertIsNone(report.watch)


class LockTests(unittest.TestCase):
    def test_second_tick_while_locked_does_nothing(self):
        folder = tempfile.mkdtemp(prefix="outreach-tick-lock-")
        self.addCleanup(shutil.rmtree, folder, True)
        store = Path(folder) / "outreach.db"
        with tick_lock(store):
            self.assertTrue((Path(folder) / "crawl.lock").exists())
            with self.assertRaises(AlreadyRunning):
                with tick_lock(store):
                    self.fail("a second tick ran while the first held the lock")
        with tick_lock(store):  # released when the first one ended
            pass


class CompanyBoardsTests(TickCase):
    def test_read_company_boards_forces_the_floor_not_the_backoff(self):
        company = seed_company(self.conn, "Acme")
        quiet = self.board("quiet", company=company)
        self.read_before(quiet, FLOOR + 60, next_in=3 * 86400)  # not due, but past the floor: read
        recent = self.board("recent", company=company)
        self.read_before(recent, 3600, next_in=86400)  # inside the floor: not read
        failing = self.board("failing", company=company)
        self.read_before(failing, FLOOR + 60, result="error", next_in=3600, error_streak=1)  # keeps its backoff
        other = self.board("other")  # another company, due: not read
        self.client = FakeClient({gh("quiet"): ok()})
        report = read_company_boards(self.conn, self.client, [company], cfg=self.cfg, now_fn=lambda: T0)
        self.assertEqual(self.client.requests, [gh("quiet")])
        self.assertEqual(self.client.unrouted, [])
        self.assertEqual(report.ok, 1)
        job, = self.jobs("read_boards")
        self.assertEqual(job["run_id"], report.run_id)
        self.assertEqual(self.row(quiet)["last_read_at"], T0)
        self.assertEqual(self.row(recent)["last_read_at"], at(-3600))
        self.assertEqual(self.row(failing)["last_read"], "error")
        self.assertIsNone(self.row(other)["last_read"])

    def test_no_company_ids_reads_nothing(self):
        self.board("acme")
        self.client = FakeClient({})
        report = read_company_boards(self.conn, self.client, [], cfg=self.cfg, now_fn=lambda: T0)
        self.assertEqual((report.read, self.client.requests), (0, []))


if __name__ == "__main__":
    unittest.main()
