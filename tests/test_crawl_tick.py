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
from outreach.crawl.base import JobRef, ReadResult, Validators, ids_fingerprint
from outreach.crawl.cadence import add_seconds
from outreach.crawl.config import load_crawl_config
from outreach.crawl.ingest import read_board
from outreach.crawl.polite import PoliteClient
from outreach.crawl.probe import ProbeReport
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
    stub_probe = True  # the daily slug probe is its own tests' business (ProbeStepTests); the rest never see it

    def setUp(self):
        if self.stub_probe:
            patch = mock.patch.object(tick, "probe_slugs", return_value=ProbeReport(0, 0, 0, 0, 0, 0))
            patch.start()
            self.addCleanup(patch.stop)
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

    def test_no_transaction_is_open_during_a_detail_request(self):
        # A detail-request ATS: the list carries no body and `body` asks for each one, inside read_board.
        conn = self.conn

        def check(method, url, headers, body):
            assert not conn.in_transaction, f"a transaction is open during {url}"
            return Response(200, url, b"Build robots. We sponsor visas.", {})

        class Detail:
            ats = "greenhouse"

            def __init__(self, client):
                self.client = client

            def list(self, token, validators):
                self.client.get(f"https://detail.example/{token}/list")
                jobs = tuple(JobRef(f"{token}-{n}", "Software Engineer", f"https://detail.example/{token}/{n}",
                                    "Boston, MA", "US", "2026-10-01", None) for n in range(3))
                return ReadResult("ok", jobs, Validators("", ids_fingerprint(j.native_id for j in jobs)))

            def body(self, token, job):
                return self.client.get(job.url).text()

        self.board("acme")
        routes = {"https://detail.example/acme/list": check,
                  **{f"https://detail.example/acme/{n}": check for n in range(3)}}
        with mock.patch.object(tick, "adapter_for", lambda ats, client: Detail(client)):
            report = self.tick(routes)
        self.assertEqual(report.ok, 1)
        self.assertEqual(len(self.client.requests), 4)
        self.assertEqual(self.count("posting_body"), 3)

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

    def good_boards(self, n, prefix="g"):
        """`n` Greenhouse boards whose last read, an hour ago, was ok, all due now."""
        ids = [self.board(f"{prefix}{i}") for i in range(n)]
        for bid in ids:
            self.read_before(bid, 3600, next_in=-60)
        return ids

    def outage(self, answer, n=6, failures=3):
        ids = self.good_boards(n)
        cfg = dataclasses.replace(self.cfg, breaker_failures=failures)
        report = self.tick({gh(f"g{i}"): answer for i in range(n)}, cfg=cfg)
        return ids, report

    def assert_deferred(self, bid, last_read="ok"):
        row = self.row(bid)
        self.assertEqual((row["last_read"], row["error_streak"], row["next_read_at"]),
                         (last_read, 0, at(self.cfg.defer_s)))
        self.assertEqual(self.reads(bid), [])

    def test_host_outage_defers_the_rest_without_streaks(self):
        ids, report = self.outage(status_response(503))
        self.assertEqual(len(self.client.requests), 3)  # the breaker opened after three: nothing more was sent
        # The three error reads came from the bucket the breaker tripped and from boards that read fine last time,
        # so they too are deferrals: an outage is not a finding about any one board.
        for bid in ids:
            self.assert_deferred(bid)
        self.assertEqual((report.error, report.deferred, report.read), (0, 6, 0))
        self.assertEqual(self.count("review_item"), 0)
        self.assertEqual(report.tripped, ("greenhouse.io",))
        job, = self.jobs("read_boards")
        self.assertEqual(json.loads(job["summary"])["tripped"], ["greenhouse.io"])

    def test_an_outage_over_many_ticks_raises_no_unreadable_item(self):
        ids = self.good_boards(6)
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
            self.read_before(self.board(t), 86400, next_in=-60)
        page = text_response("<html>Just a moment...</html>")
        routes = {gh("a"): page, gh("b"): page, gh("c"): ok(), gh("d"): page, gh("e"): page}
        report = self.tick(routes, cfg=dataclasses.replace(self.cfg, breaker_failures=3))
        self.assertEqual((report.ok, report.error, report.deferred, report.tripped), (1, 4, 0, ()))

    def test_an_outage_in_one_bucket_leaves_another_alone(self):
        g = self.good_boards(4)
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
            self.assert_deferred(bid, last_read=None)
        self.assertEqual((report.ok, report.deferred), (1, 2))


    def ticks(self, routes, n, *, cfg=None, start=0, limit=None):
        """`n` ticks 30 minutes apart from T0 + `start` ticks, each with its own client on `routes`."""
        for k in range(start, start + n):
            self.client = FakeClient(routes)
            run_tick(self.conn, self.client, cfg=cfg or self.cfg, now_fn=lambda k=k: at(k * 1800), clock=self.clock,
                     sleep=self.clock.sleep, limit=limit)
            self.assertEqual(self.client.unrouted, [])

    def test_bad_boards_never_starve_the_healthy_ones(self):
        # C1: five never-read boards that answer 403 trip the breaker; the healthy boards behind them are deferred
        # once and read on the next tick, and the bad boards are written as the errors they are.
        bad = [self.board(f"bad{i}") for i in range(5)]
        healthy = [self.board(f"ok{i}") for i in range(3)]
        routes = {**{gh(f"bad{i}"): status_response(403) for i in range(5)},
                  **{gh(f"ok{i}"): ok() for i in range(3)}}
        self.ticks(routes, 2)
        for bid in healthy:
            self.assertEqual(self.row(bid)["last_read"], "ok")
        for bid in bad:
            self.assertGreaterEqual(self.row(bid)["error_streak"], 1)

    def test_erroring_boards_with_wrong_shapes_never_trip_the_bucket(self):
        # C1: boards already in an error streak do not count toward the tick's breaker, so five of them sorted
        # together cannot defer each other, or the healthy boards behind them, tick after tick.
        bad = [self.board(f"bad{i}") for i in range(5)]
        for bid in bad:
            self.read_before(bid, 86400, result="error", next_in=-86400, error_streak=1)
        healthy = self.good_boards(3, prefix="ok")
        page = text_response("<html>Just a moment...</html>")
        routes = {**{gh(f"bad{i}"): page for i in range(5)}, **{gh(f"ok{i}"): ok() for i in range(3)}}
        report = self.tick(routes)
        self.assertEqual((report.tripped, report.deferred, report.error, report.ok), ((), 0, 5, 3))
        self.ticks(routes, 2, start=1)
        for bid in healthy:
            self.assertEqual(self.row(bid)["last_read"], "ok")
        for bid in bad:
            self.assertEqual(self.row(bid)["error_streak"], 2)

    def test_a_deferred_never_read_board_keeps_its_never_read_priority(self):
        cfg = dataclasses.replace(self.cfg, breaker_failures=1)
        old = self.board("old")  # made first, so a tie on board id would put it ahead
        self.read_before(old, 5 * 86400, next_in=-4 * 86400)
        fresh = self.board("fresh")
        down = self.board("down")
        self.read_before(down, 3600, next_in=-60)
        self.plan(self.company_of(down), 1)
        # Tick 1: the plan board answers 503 and opens the breaker; the other two are deferred to the same time.
        self.tick({gh("down"): status_response(503)}, cfg=cfg)
        self.assertEqual(self.row(fresh)["next_read_at"], self.row(old)["next_read_at"])
        self.assertIsNone(self.row(fresh)["last_read"])
        # Tick 2: the never-read board still goes before the oldest due one.
        self.client = FakeClient({gh(t): ok() for t in ("down", "fresh", "old")})
        run_tick(self.conn, self.client, cfg=self.cfg, now_fn=lambda: at(1800), clock=self.clock)
        self.assertEqual(self.client.requests, [gh("down"), gh("fresh"), gh("old")])


    def test_a_host_that_blocks_for_good_ends_with_one_host_item(self):
        # The outage grace: a board is shielded only while its last good read is under a day old. After that its
        # errors are written, back off and spread out, and three or more streaks raise the host's one item.
        ids = self.good_boards(6)
        self.ticks({gh(f"g{i}"): status_response(503) for i in range(6)}, 4 * 48)
        self.assertGreater(self.count("board_read"), 0)
        self.assertTrue(all(self.row(b)["error_streak"] >= 1 for b in ids))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM review_item WHERE kind = 'board_unreadable'")
                         .fetchone()[0], 0)
        item, = self.conn.execute("SELECT * FROM review_item WHERE kind = 'flagged'").fetchall()
        self.assertEqual(item["state"], "open")
        self.assertTrue(item["evidence_key"].startswith("host-blocked:greenhouse.io:"), item["evidence_key"])

    def test_a_board_last_read_over_the_grace_ago_is_not_shielded(self):
        stale = self.board("stale")
        self.read_before(stale, self.cfg.outage_grace_s, next_in=-60)
        fresh = self.good_boards(2)
        report = self.tick({gh(t): status_response(503) for t in ("stale", "g0", "g1")},
                           cfg=dataclasses.replace(self.cfg, breaker_failures=3))
        self.assertEqual((report.error, report.deferred), (1, 2))
        self.assertEqual((self.row(stale)["last_read"], self.row(stale)["error_streak"]), ("error", 1))
        for bid in fresh:
            self.assert_deferred(bid)


class MassEmptyTests(TickCase):
    """Ruled: five or more boards of one bucket with open postings reading a well-formed empty in one tick is an
    outage (a broken API answering `{"jobs": []}`), not five boards emptied at once."""
    EMPTY = (FIXTURES / "greenhouse_empty.json").read_bytes()

    def open_boards(self, n):
        ids = [self.board(f"e{i}") for i in range(n)]
        self.tick({gh(f"e{i}"): ok() for i in range(n)})
        self.assertEqual(self.open_postings(), 3 * n)
        return ids

    def open_postings(self):
        return self.conn.execute("SELECT count(*) FROM posting WHERE closed_at IS NULL").fetchone()[0]

    def empty_tick(self, tokens, k):
        self.client = FakeClient({gh(t): Response(200, "", self.EMPTY, {}) for t in tokens})
        report = run_tick(self.conn, self.client, cfg=self.cfg, now_fn=lambda: at(k * FLOOR), clock=self.clock,
                          sleep=self.clock.sleep)
        self.assertEqual(self.client.unrouted, [])
        return report

    def test_six_boards_reading_empty_at_once_close_nothing(self):
        ids = self.open_boards(6)
        report = self.empty_tick([f"e{i}" for i in range(6)], 1)
        self.assertEqual((report.empty, report.deferred, report.closed), (0, 6, 0))
        self.assertEqual(self.open_postings(), 18)
        self.assertEqual(self.count("review_item"), 0)
        for bid in ids:
            row = self.row(bid)
            self.assertEqual((row["last_read"], row["empty_streak"], row["next_read_at"]),
                             ("ok", 0, at(FLOOR + self.cfg.defer_s)))
        self.assertIn("greenhouse.io", report.tripped)
        # Twice in a row changes nothing either.
        self.empty_tick([f"e{i}" for i in range(6)], 2)
        self.assertEqual(self.open_postings(), 18)
        self.assertEqual(self.count("review_item"), 0)

    def test_one_board_reading_empty_follows_the_two_empties_rule(self):
        bid, = self.open_boards(1)
        report = self.empty_tick(["e0"], 1)
        self.assertEqual((report.empty, report.deferred), (1, 0))
        self.assertEqual(self.open_postings(), 3)
        item, = self.conn.execute("SELECT * FROM review_item").fetchall()
        self.assertEqual(item["kind"], "board_empty")
        report = self.empty_tick(["e0"], 3)
        self.assertEqual((report.empty, report.closed), (1, 3))
        self.assertEqual(self.open_postings(), 0)
        self.assertEqual(self.row(bid)["empty_streak"], 2)

    def test_four_boards_reading_empty_are_four_empties(self):
        self.open_boards(4)
        report = self.empty_tick([f"e{i}" for i in range(4)], 1)
        self.assertEqual((report.empty, report.deferred), (4, 0))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM review_item WHERE kind = 'board_empty'")
                         .fetchone()[0], 4)

    def test_boards_with_no_open_postings_never_count(self):
        for i in range(6):
            self.read_before(self.board(f"e{i}"), 3600, next_in=-60)  # read ok, but nothing listed
        report = self.empty_tick([f"e{i}" for i in range(6)], 0)
        self.assertEqual((report.empty, report.deferred), (6, 0))


class HostBlockTests(TickCase):
    """The host ruling: an ATS that refuses the crawler for days raises one item, not one per board."""

    def items(self, kind):
        return self.conn.execute("SELECT * FROM review_item WHERE kind = ? ORDER BY review_id", (kind,)).fetchall()

    def run_ticks(self, routes, n, *, start=0, limit=None):
        for k in range(start, start + n):
            self.client = FakeClient(routes)
            run_tick(self.conn, self.client, cfg=self.cfg, now_fn=lambda k=k: at(k * 1800), clock=self.clock,
                     sleep=self.clock.sleep, limit=limit)
            self.assertEqual(self.client.unrouted, [])

    def test_a_host_refusing_for_days_raises_one_item(self):
        ids = []
        for i in range(30):
            bid = self.board(f"g{i}")
            self.read_before(bid, 86400, next_in=i * 1800 - 1)  # one board comes due each tick
            ids.append(bid)
        self.run_ticks({gh(f"g{i}"): status_response(403) for i in range(30)}, 3 * 48, limit=1)
        self.assertGreaterEqual(max(self.row(b)["error_streak"] for b in ids), 3)
        self.assertEqual(self.items("board_unreadable"), [])
        item, = self.items("flagged")
        self.assertEqual(item["state"], "open")
        self.assertTrue(item["evidence_key"].startswith("host-blocked:greenhouse.io"), item["evidence_key"])
        self.assertTrue(item["reason"].startswith("greenhouse.io refused the crawler for "), item["reason"])
        self.assertIn(f"boards since {T0[:10]}", item["reason"])
        self.assertIn(item["board_id"], ids)
        # The bucket's next good read clears it (the host is back; the next board due reads ok).
        self.run_ticks({gh(f"g{i}"): ok() for i in range(30)}, 12, start=3 * 48, limit=1)
        self.assertTrue(any(self.row(b)["last_read"] == "ok" for b in ids))
        item, = self.items("flagged")
        self.assertEqual((item["state"], item["resolution"]), ("resolved", "cleared"))
        self.assertEqual(self.items("board_unreadable"), [])

    def test_two_failing_boards_still_get_their_own_items(self):
        bad = [self.board(f"bad{i}") for i in range(2)]
        for bid in bad:
            self.read_before(bid, 86400, next_in=-60)
        # The only boards of the bucket, so no good read separates them: two is still under the host threshold.
        routes = {gh(f"bad{i}"): status_response(403) for i in range(2)}
        self.run_ticks(routes, 48)  # a day: 6, 12 hours of backoff, so a third error by hour 18
        self.assertEqual(sorted(i["board_id"] for i in self.items("board_unreadable")), bad)
        self.assertEqual(self.items("flagged"), [])

    def test_a_good_read_in_the_bucket_keeps_per_board_items(self):
        # Three boards failing, but another board of the bucket read fine after their streaks began: these are three
        # broken boards, not a blocked host.
        bad = [self.board(f"bad{i}") for i in range(3)]
        for bid in bad:
            self.read_before(bid, 86400, next_in=-60)
        self.read_before(self.board("fine"), 86400, next_in=3600)
        routes = {**{gh(f"bad{i}"): status_response(403) for i in range(3)}, gh("fine"): ok()}
        self.run_ticks(routes, 48)
        self.assertEqual(sorted(i["board_id"] for i in self.items("board_unreadable")), bad)
        self.assertEqual(self.items("flagged"), [])


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


def probe_404s(*slugs):
    routes = {}
    for slug in slugs:
        routes[gh(slug)] = status_response(404)
        routes[lever(slug)] = status_response(404)
        routes[f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"] = status_response(404)
    return routes


class ProbeStepTests(TickCase):
    """The daily slug probe (Task 26a): after the boards, before the watch pass, once per New York day."""
    stub_probe = False

    def setUp(self):
        super().setUp()
        self.company = seed_company(self.conn, "Acme Robotics")  # no board: a candidate for probing

    def runs(self):
        return self.conn.execute("SELECT * FROM event WHERE kind = 'slug_probe_run'").fetchall()

    def test_the_tick_probes_once_a_day_with_the_counts_in_the_report(self):
        routes = probe_404s("acmerobotics", "acme-robotics")
        report = self.tick(routes)
        self.assertEqual((report.probe.considered, report.probe.probed), (1, 1))
        self.assertEqual(report.probe_error, "")
        self.assertEqual(len(self.client.requests), 6)
        run, = self.runs()
        self.assertEqual(run["idempotency_key"], f"slug-probe-run:{TODAY}")
        # The same day: no probe pass at all.
        again = self.tick({})
        self.assertIsNone(again.probe)
        self.assertEqual(self.client.requests, [])
        # The next New York day: the pass runs again (this company is remembered, so it asks nobody).
        later = self.tick({}, now_fn=lambda: at(86400))
        self.assertEqual(later.probe, ProbeReport(0, 0, 0, 0, 0, 0))
        self.assertEqual(len(self.runs()), 2)

    def test_the_job_summary_carries_the_probe_and_watch_passes(self):
        self.board("acme")
        report = self.tick({gh("acme"): ok(), **probe_404s("acmerobotics", "acme-robotics")})
        summary = json.loads(self.jobs("read_boards")[0]["summary"])
        self.assertEqual(summary["probe"], dataclasses.asdict(report.probe))
        self.assertEqual(summary["watch"], dataclasses.asdict(report.watch))
        self.assertEqual((summary["probe_error"], summary["watch_error"]), ("", ""))
        self.assertEqual(summary["ok"], 1)
        # A failed pass is in the summary as its error, with no counts.
        with mock.patch.object(tick, "watch_pages", side_effect=RuntimeError("watch bug")):
            self.tick({}, now_fn=lambda: at(60))
        summary = json.loads(self.jobs("read_boards")[1]["summary"])
        self.assertIsNone(summary["probe"])  # the same New York day: no probe pass
        self.assertIsNone(summary["watch"])
        self.assertIn("watch bug", summary["watch_error"])

    def test_the_probe_runs_after_the_boards_and_before_the_watch_pass(self):
        self.board("acme")
        self.conn.execute("INSERT INTO watched_page(company_id, url) VALUES (?, ?)", (self.company, PAGE))
        routes = {gh("acme"): ok(), PAGE: text_response(CAREERS), **probe_404s("acmerobotics", "acme-robotics")}
        self.tick(routes)
        reqs = self.client.requests
        self.assertEqual(reqs[0], gh("acme"))
        self.assertEqual(reqs[-1], PAGE)
        self.assertIn(gh("acmerobotics"), reqs[1:-1])

    def test_the_probe_goes_through_the_tick_breaker(self):
        # Two Greenhouse boards fail and trip that host: the probe asks Lever and Ashby, never Greenhouse.
        self.board("g0")
        self.board("g1")
        cfg = dataclasses.replace(self.cfg, breaker_failures=2)
        routes = {gh("g0"): status_response(503), gh("g1"): status_response(503),
                  **{k: v for k, v in probe_404s("acmerobotics", "acme-robotics").items() if "greenhouse" not in k}}
        report = self.tick(routes, cfg=cfg)
        self.assertIn("greenhouse.io", report.tripped)
        self.assertEqual(report.probe.probed, 1)
        self.assertNotIn(gh("acmerobotics"), self.client.requests)
        self.assertIn(lever("acmerobotics"), self.client.requests)

    def test_a_probe_failure_is_reported_and_the_board_reads_stay(self):
        self.board("acme")
        with mock.patch.object(tick, "probe_slugs", side_effect=RuntimeError("probe bug")):
            report = self.tick({gh("acme"): ok()})
        self.assertEqual(report.ok, 1)
        self.assertIsNone(report.probe)
        self.assertIn("probe bug", report.probe_error)
        self.assertEqual(self.jobs("read_boards")[0]["status"], "ok")
        self.assertEqual(self.runs(), [])  # not recorded: the next tick tries again
        self.assertEqual(report.watch.baseline + report.watch.unchanged + report.watch.error, 0)  # watch pass still ran

    def test_the_probe_gets_the_limit_the_tick_clients_and_all_company_ids(self):
        seen = {}

        def spy(conn, ids, client, **kw):
            seen.update(ids=sorted(ids), limit=kw["limit"], now=kw["now"], cfg=kw["cfg"])
            return ProbeReport(1, 1, 0, 0, 0, 0)

        with mock.patch.object(tick, "probe_slugs", spy):
            report = self.tick({})
        self.assertEqual(seen["ids"], [self.company])
        self.assertEqual((seen["limit"], seen["now"]), (50, T0))
        self.assertEqual(report.probe.probed, 1)

    def test_no_probe_once_the_tick_budget_is_spent(self):
        cfg = dataclasses.replace(self.cfg, tick_budget_s=0)
        report = self.tick({}, cfg=cfg)
        self.assertIsNone(report.probe)
        self.assertEqual(self.runs(), [])

    def test_read_company_boards_runs_no_probe(self):
        self.client = FakeClient({})
        report = read_company_boards(self.conn, self.client, [self.company], cfg=self.cfg, now_fn=lambda: T0)
        self.assertIsNone(report.probe)
        self.assertEqual(self.client.requests, [])


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

    def test_the_lock_is_released_after_an_exception_inside_it(self):
        folder = tempfile.mkdtemp(prefix="outreach-tick-lock-")
        self.addCleanup(shutil.rmtree, folder, True)
        store = Path(folder) / "outreach.db"
        with self.assertRaises(RuntimeError):
            with tick_lock(store):
                raise RuntimeError("tick crashed")
        with tick_lock(store):
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

    def test_read_company_boards_takes_the_crawl_lock(self):
        company = seed_company(self.conn, "Acme")
        self.board("acme", company=company)
        self.client = FakeClient({gh("acme"): ok()})
        store = self.conn.execute("PRAGMA database_list").fetchone()["file"]
        with tick_lock(store):
            with self.assertRaises(AlreadyRunning):
                read_company_boards(self.conn, self.client, [company], cfg=self.cfg, now_fn=lambda: T0)
        self.assertEqual(self.client.requests, [])
        self.assertEqual(self.jobs("read_boards"), [])

    def test_no_company_ids_reads_nothing(self):
        self.board("acme")
        self.client = FakeClient({})
        report = read_company_boards(self.conn, self.client, [], cfg=self.cfg, now_fn=lambda: T0)
        self.assertEqual((report.read, self.client.requests), (0, []))


if __name__ == "__main__":
    unittest.main()
