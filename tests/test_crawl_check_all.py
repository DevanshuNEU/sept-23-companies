"""Check now (#194, design 4.3): the crawl job reads every Greenhouse and Ashby board when a request waits.

A fresh store, the tick's `FakeClient` routes and fake clock (tests/test_crawl_tick.py). Nothing here touches the
network or the live store.
"""
import dataclasses
import json
import sqlite3
from unittest import mock

from outreach.crawl import check_all
from outreach.crawl.check_all import select_check
from outreach.pipeline import check_state
from outreach.store import append_event
from tests.fakes import status_response
from tests.storetest import seed_company
from tests.test_crawl_tick import ASHBY_JOBS, JOBS, T0, TickCase, ashby, at, gh, lever, lever_ok, ok
from outreach.net import Response
from outreach.pipeline.joblist import JobFilters, query

_GH = json.loads(JOBS)["jobs"]
ENGINEER = next(j for j in _GH if j["title"] == "Software Engineer, New Grad")
SALES = next(j for j in _GH if j["title"] == "Account Executive")


def board_of(*jobs):
    return Response(200, "", json.dumps({"jobs": list(jobs)}).encode(), {"etag": '"N"'})


def many_engineers(n):
    return [{**ENGINEER, "id": 5_000_000 + i, "absolute_url": f"https://job-boards.greenhouse.io/acme/jobs/{5_000_000 + i}",
             "title": f"Software Engineer {i}"} for i in range(n)]


def ashby_ok():
    return Response(200, "", ASHBY_JOBS, {"etag": '"S"'})


class CheckCase(TickCase):
    def request(self, when=T0):
        return append_event(self.conn, entity="source", kind=check_state.REQUEST_KIND, actor="devanshu", at=when,
                            detail={})

    def selected(self, now=T0):
        return [b.board_id for b in select_check(self.conn, now, self.cfg)]

    def check_runs(self):
        return [r for r in self.jobs("read_boards") if json.loads(r["summary"]).get("mode") == "check_all"]

    def progress(self):
        return check_state.progress(self.conn)


class SelectTests(CheckCase):
    def test_check_reads_every_greenhouse_and_ashby_board(self):
        old = self.board("old")
        self.read_before(old, 3 * 3600, next_in=86400)  # read 3 hours ago, not due for a day
        older = self.board("older", ats="ashby")
        self.read_before(older, 5 * 3600, next_in=86400)
        never = self.board("never")
        lever_board = self.board("lev", ats="lever")
        self.read_before(lever_board, 3600, next_in=86400)  # another ATS, not due: not in the check
        inactive = self.board("inactive", active=0)
        closed = self.board("closed", company=seed_company(self.conn, "Gone Inc"))
        self.conn.execute("UPDATE company SET status = 'closed', close_reason = 'no_referrals' WHERE company_id = ?", (self.company_of(closed),))
        self.assertEqual(self.selected(), [never, older, old])  # never read, then the oldest read first
        self.assertNotIn(inactive, self.selected())

    def test_check_skips_board_read_within_60s(self):
        just = self.board("just")
        self.read_before(just, 60 - 1, next_in=86400)
        edge = self.board("edge")
        self.read_before(edge, 60, next_in=86400)
        self.assertEqual(self.selected(), [edge])

    def test_check_skips_boards_in_error_backoff(self):
        backing_off = self.board("backoff")
        self.read_before(backing_off, 3600, result="error", next_in=3600, error_streak=1)
        waited = self.board("waited")
        self.read_before(waited, 30000, result="error", next_in=-60, error_streak=1)
        deferred = self.board("deferred")  # put off by the breaker: its last read was good, so the check reads it
        self.read_before(deferred, 3600, next_in=1800)
        self.assertEqual(self.selected(), [waited, deferred])

    def test_check_includes_due_boards_of_other_ats(self):
        due = self.board("due-lever", ats="lever")
        self.read_before(due, 30000, next_in=-60)
        later = self.board("later-lever", ats="lever")
        self.read_before(later, 3600, next_in=3600)
        green = self.board("green")
        self.read_before(green, 3600, next_in=86400)
        self.assertEqual(self.selected(), [green, due])  # the check ATSs first, then the other ATSs' due boards

    def test_a_switched_off_check_ats_is_not_read(self):
        self.board("gh")
        cfg = dataclasses.replace(self.cfg, ats_off={**self.cfg.ats_off, "greenhouse": "test"})
        self.assertEqual(select_check(self.conn, T0, cfg), [])


class TickRunsCheckTests(CheckCase):
    def test_tick_runs_check_when_requested(self):
        quiet = self.board("quiet")
        self.read_before(quiet, 3 * 3600, next_in=86400)  # not due: only a check reads it
        event_id = self.request()
        self.tick({gh("quiet"): ok()})
        self.assertEqual(self.client.requests, [gh("quiet")])
        run, = self.check_runs()
        summary = json.loads(run["summary"])
        self.assertEqual((summary["mode"], summary["requested_at"], summary["read"]), ("check_all", T0, 1))
        self.assertEqual(self.row(quiet)["last_read_at"], T0)
        self.assertEqual(len(self.jobs("read_boards")), 2)  # the check, then the tick's own pass
        self.assertEqual(self.progress()["request_event_id"], event_id)
        self.assertIsNone(check_state.pending_request(self.conn))

    def test_the_check_runs_before_the_tick_pass_and_ignores_its_limits(self):
        for t in ("a", "b", "c"):
            self.read_before(self.board(t), 3 * 3600, next_in=86400)
        self.request()
        cfg = dataclasses.replace(self.cfg, boards_per_tick=1, tick_budget_s=1)
        report = self.tick({gh(t): ok() for t in ("a", "b", "c")}, cfg=cfg)
        run, = self.check_runs()
        self.assertEqual(json.loads(run["summary"])["read"], 3)
        self.assertLess(run["run_id"], report.run_id)
        self.assertEqual(report.check_runs, (run["run_id"],))

    def test_the_check_budget_stops_it(self):
        tokens = ["b1", "b2", "b3"]
        for t in tokens:
            self.read_before(self.board(t), 3 * 3600, next_in=86400)
        self.request()

        def slow(method, url, headers, body):
            self.clock.t += 100
            return ok()

        self.tick({gh(t): slow for t in tokens}, cfg=dataclasses.replace(self.cfg, check_budget_s=150))
        self.assertEqual(self.client.requests, [gh("b1"), gh("b2")])
        self.assertEqual((self.progress()["checked"], self.progress()["total"]), (2, 3))

    def test_request_during_tick_runs_after_it(self):
        quiet = self.board("quiet")
        self.read_before(quiet, 3 * 3600, next_in=86400)
        self.board("due")  # never read: the tick's own pass reads it

        def due_answer(method, url, headers, body):
            self.request()  # he clicks while the tick runs
            return ok()

        report = self.tick({gh("due"): due_answer, gh("quiet"): ok()})
        # The tick's pass first; the check after it skips the board the pass read a moment ago.
        self.assertEqual(self.client.requests, [gh("due"), gh("quiet")])
        run, = self.check_runs()
        self.assertGreater(run["run_id"], report.run_id)
        self.assertEqual(json.loads(run["summary"])["read"], 1)
        self.assertIsNone(check_state.pending_request(self.conn))

    def test_no_request_no_check(self):
        self.read_before(self.board("quiet"), 3 * 3600, next_in=86400)
        report = self.tick({})
        self.assertEqual(self.client.requests, [])
        self.assertEqual(self.check_runs(), [])
        self.assertIsNone(self.progress())
        self.assertEqual(report.check_runs, ())

    def test_a_claimed_request_is_not_checked_twice(self):
        self.read_before(self.board("quiet"), 3 * 3600, next_in=86400)
        self.request()
        self.tick({gh("quiet"): ok()})
        self.clock.t += 3600
        self.tick({}, now_fn=lambda: at(3600))
        self.assertEqual(len(self.check_runs()), 1)

    def test_a_failed_check_is_reported_ends_its_progress_and_the_tick_goes_on(self):
        self.read_before(self.board("quiet"), 3 * 3600, next_in=86400)
        self.board("due")
        self.request()
        with mock.patch.object(check_all, "select_check", side_effect=RuntimeError("boom")):
            report = self.tick({gh("due"): ok()})
        self.assertIn("boom", report.check_error)
        self.assertEqual(report.read, 1)  # the tick's own pass still read its due board
        last = self.progress()
        self.assertEqual(last["finished_at"], T0)
        self.assertIsNone(check_state.pending_request(self.conn))  # a failing check is not tried again every tick
        summary = json.loads(self.jobs("read_boards")[-1]["summary"])
        self.assertIn("boom", summary["check_error"])


class ProgressTests(CheckCase):
    def test_progress_written_and_final(self):
        tokens = [f"b{i}" for i in range(10)]
        for t in tokens:
            self.known(t, 3 * 3600)
        event_id = self.request()
        clock = iter(at(s) for s in range(0, 1000, 1))

        def answer(method, url, headers, body):
            self.clock.t += 0.4
            return ok()

        writes = []
        real = check_all.write_progress

        def spy(conn, value):
            writes.append(dict(value))
            real(conn, value)

        with mock.patch.object(check_all, "write_progress", side_effect=spy):
            self.tick({gh(t): answer for t in tokens}, now_fn=lambda: next(clock))
        # 10 boards at 0.4 s each is about 4 s: at most one write a second, then the final one.
        self.assertLessEqual(len(writes), 1 + 5 + 1)
        self.assertGreaterEqual(len(writes), 3)
        self.assertTrue(all(w["finished_at"] is None for w in writes[:-1]))
        first, final = writes[0], writes[-1]
        self.assertEqual((first["total"], first["request_event_id"]), (10, event_id))
        self.assertRegex(first["started_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        listed = sum(len(query(self.conn, final["finished_at"], JobFilters(view=v, window_days=0), None).rows)
                     for v in ("your_level", "higher"))
        self.assertGreater(listed, 0)
        self.assertEqual((final["checked"], final["total"], final["new"], final["paused_hosts"]),
                         (10, 10, listed, []))
        self.assertRegex(final["finished_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertGreater(final["finished_at"], final["started_at"])
        self.assertEqual(self.progress(), final)
        run, = self.check_runs()
        self.assertEqual(final["run_id"], run["run_id"])
        self.assertEqual(check_state.status(self.conn, now=final["finished_at"]).state, "done")

    def known(self, token, seconds_ago=3600):
        """A board read fine `seconds_ago` with nothing on it, not due for a day: a posting it shows next is new."""
        bid = self.board(token)
        self.read_before(bid, seconds_ago, next_in=86400)
        self.conn.execute("UPDATE board SET last_good_read_at = last_read_at WHERE board_id = ?", (bid,))
        return bid

    def test_a_first_read_in_a_check_is_not_new(self):
        self.board("seeded")  # never read: what the seed adds
        self.request()
        report = self.tick({gh("seeded"): board_of(*many_engineers(300))})
        run, = self.check_runs()
        self.assertEqual(json.loads(run["summary"])["new"], 300)  # the job run still counts every row it inserted
        self.assertEqual(self.progress()["new"], 0)  # but none of them is a new job on the list
        self.assertEqual(report.check_error, "")

    def test_the_new_count_is_what_the_list_marks_new(self):
        self.known("eng")
        self.known("sales")
        self.request()
        self.tick({gh("eng"): board_of(ENGINEER), gh("sales"): board_of(SALES)})
        listed = query(self.conn, T0, JobFilters(view="your_level", window_days=0), None).rows + \
            query(self.conn, T0, JobFilters(view="higher", window_days=0), None).rows
        self.assertEqual([r.title for r in listed], [ENGINEER["title"]])  # the sales posting is never listed
        self.assertEqual(self.progress()["new"], 1)

    def test_an_other_role_is_not_new(self):
        # #250: the header's "N new" counts what the level tabs list, software roles only.
        self.known("eng")
        self.request()
        scientist = {**ENGINEER, "id": 6_000_001, "title": "Data Scientist, New Grad",
                     "absolute_url": "https://job-boards.greenhouse.io/eng/jobs/6000001"}
        self.tick({gh("eng"): board_of(ENGINEER, scientist)})
        self.assertEqual(self.progress()["new"], 1)

    def test_a_repost_is_not_new(self):
        bid = self.known("eng")
        self.request()
        self.tick({gh("eng"): board_of(ENGINEER)})
        posting_id = self.conn.execute("SELECT posting_id FROM posting").fetchone()[0]
        self.conn.execute("UPDATE posting SET repost_of = ? WHERE posting_id = ?", (posting_id, posting_id))
        self.assertEqual(check_all._listed_new(self.conn, 0, check_all.listed_params(self.conn)), 0)

    def test_a_paused_host_waits_out_its_deferral(self):
        for i in range(6):
            self.known(f"g{i}")
        self.read_before(self.board("a0", ats="ashby"), 3600, next_in=86400)
        self.request()
        cfg = dataclasses.replace(self.cfg, breaker_failures=3)
        routes = {gh(f"g{i}"): status_response(429) for i in range(6)}
        routes[ashby("a0")] = ashby_ok()
        self.tick(routes, cfg=cfg)
        self.assertEqual(self.progress()["paused_hosts"], ["greenhouse"])
        tripped_at = self.progress()["finished_at"]

        # A click 20 minutes later: Greenhouse is not asked again, and the line still says it is paused.
        self.request(at(1200))
        self.clock.t += 1200
        self.tick({ashby("a0"): ashby_ok()}, cfg=cfg, now_fn=lambda: at(1200))
        self.assertEqual(self.client.requests, [ashby("a0")])
        self.assertEqual(self.progress()["paused_hosts"], ["greenhouse"])
        self.assertEqual(self.progress()["paused_at"], {"greenhouse": tripped_at})  # a held host keeps its first time

        # Once defer_s has passed since the trip (not since the last click), Greenhouse is read again.
        self.request(at(self.cfg.defer_s + 1))
        self.clock.t += 1200
        later = {gh(f"g{i}"): ok() for i in range(6)}
        later[ashby("a0")] = ashby_ok()
        self.tick(later, cfg=cfg, now_fn=lambda: at(self.cfg.defer_s + 1))
        self.assertEqual(sorted(self.client.requests), sorted(later))
        self.assertEqual(self.progress()["paused_hosts"], [])

    def test_check_reports_tripped_host(self):
        for i in range(6):
            self.read_before(self.board(f"g{i}"), 3600, next_in=86400)
        self.read_before(self.board("a0", ats="ashby"), 3600, next_in=86400)
        self.request()
        cfg = dataclasses.replace(self.cfg, breaker_failures=3)
        routes = {gh(f"g{i}"): status_response(503) for i in range(6)}
        routes[ashby("a0")] = ashby_ok()
        self.tick(routes, cfg=cfg)
        self.assertEqual(self.progress()["paused_hosts"], ["greenhouse"])

    def test_a_tripped_check_never_makes_a_board_due_sooner(self):
        # A deferral writes the later of the stored next_read_at and now + defer_s: a trip early in a check must not
        # make every not-due board of that host due in 30 minutes.
        later = []
        for i in range(6):
            bid = self.board(f"g{i}")
            self.read_before(bid, 3600, next_in=86400)
            later.append(bid)
        due = self.board("due")
        self.read_before(due, 30000, next_in=-60)
        self.request()
        cfg = dataclasses.replace(self.cfg, breaker_failures=3)
        routes = {gh(f"g{i}"): status_response(503) for i in range(6)}
        routes[gh("due")] = status_response(503)
        self.tick(routes, cfg=cfg)
        self.assertEqual(self.progress()["paused_hosts"], ["greenhouse"])
        for bid in later:
            self.assertEqual(self.row(bid)["next_read_at"], at(86400))
        self.assertEqual(self.row(due)["next_read_at"], at(self.cfg.defer_s))

    def test_a_failed_progress_write_mid_run_does_not_stop_the_check(self):
        tokens = [f"b{i}" for i in range(4)]
        for t in tokens:
            self.read_before(self.board(t), 3 * 3600, next_in=86400)
        self.request()

        def answer(method, url, headers, body):
            self.clock.t += 1.5  # every take is past the throttle, so each one tries a write
            return ok()

        real = check_all.write_progress
        calls = []

        def flaky(conn, value):
            calls.append(value["finished_at"])
            if value["finished_at"] is None and len(calls) > 1:
                raise sqlite3.OperationalError("database is locked")
            real(conn, value)

        with mock.patch.object(check_all, "write_progress", side_effect=flaky):
            report = self.tick({gh(t): answer for t in tokens})
        self.assertEqual(report.check_error, "")
        self.assertEqual(sorted(self.client.requests), sorted(gh(t) for t in tokens))
        last = self.progress()
        self.assertEqual((last["checked"], last["total"]), (4, 4))
        self.assertIsNotNone(last["finished_at"])
        self.assertEqual(last["progress_write_errors"], len(calls) - 2)  # all but the first and the final write

    def test_a_failed_final_progress_write_is_the_checks_error(self):
        self.read_before(self.board("quiet"), 3 * 3600, next_in=86400)
        self.request()
        real = check_all.write_progress

        def final_fails(conn, value):
            if value["finished_at"] is not None:
                raise sqlite3.OperationalError("database is locked")
            real(conn, value)

        with mock.patch.object(check_all, "write_progress", side_effect=final_fails):
            report = self.tick({gh("quiet"): ok()})
        self.assertIn("database is locked", report.check_error)

    def test_the_check_lists_through_the_workers(self):
        routes = {}
        for i in range(8):
            self.read_before(self.board(f"g{i}"), 3600, next_in=86400)
            routes[gh(f"g{i}")] = ok()
        for i in range(4):
            self.read_before(self.board(f"a{i}", ats="ashby"), 3600, next_in=86400)
            routes[ashby(f"a{i}")] = ashby_ok()
        self.request()
        report = self.tick(routes, workers=self.cfg.workers)
        self.assertEqual(sorted(self.client.requests), sorted(routes))
        last = self.progress()
        self.assertEqual((last["checked"], last["total"], last["paused_hosts"]), (12, 12, []))
        self.assertEqual(report.check_runs, (last["run_id"],))

    def test_a_lever_board_due_in_the_check_is_read(self):
        due = self.board("lev", ats="lever")
        self.read_before(due, 30000, next_in=-60)
        self.request()
        self.tick({lever("lev"): lever_ok()})
        run, = self.check_runs()
        self.assertEqual(json.loads(run["summary"])["read"], 1)
