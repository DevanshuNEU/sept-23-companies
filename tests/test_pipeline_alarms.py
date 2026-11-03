"""The pipeline alarms (#153, Task 5): each kind fires past its threshold and stays quiet below it, and the tick
sends one Mac notice a day. A fresh store and fake notifiers: nothing here reaches the network or the live store."""
import dataclasses
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from outreach.crawl import tick
from outreach.crawl.config import load_crawl_config
from outreach.crawl.probe import ProbeReport
from outreach.crawl.tick import run_tick
from outreach.net import FetchError
from outreach.pipeline import alarms, scorecard
from outreach.pipeline.alarms import Alarm
from outreach.pipeline.config import load_pipeline_config
from outreach.pipeline.scorecard import Scorecard
from tests.fakes import FakeClient
from tests.storetest import fresh_store, seed_board, seed_company

NOW = "2026-11-10T15:00:00Z"
FLOOR_S = load_crawl_config().floor_s
URL = load_pipeline_config().simplify.url


def ago(hours: float = 0, days: float = 0, now: str = NOW) -> str:
    moment = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ") - timedelta(hours=hours, days=days)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def read_board(conn, board_id, result, at):
    conn.execute("UPDATE board SET last_read = ?, last_read_at = ?, streak_since = ?, error_streak = ? "
                 "WHERE board_id = ?", (result, at, at, int(result == "error"), board_id))


def boards(conn, ats, n, *, errors=0, at=None, prefix="b"):
    """`n` boards of one ATS read at `at` (an hour ago by default), the first `errors` of them failed."""
    company = seed_company(conn, f"{ats} {prefix} co")
    ids = []
    for i in range(n):
        board = seed_board(conn, company, ats, f"{prefix}{i}")
        read_board(conn, board, "error" if i < errors else "ok", at or ago(hours=1))
        ids.append(board)
    return ids


def card(coverage=0.5, recall=0.5) -> Scorecard:
    return Scorecard(simplify_listings=100, simplify_coverage=coverage, level_recall=recall, lead_over_simplify=None,
                     detection_p95_hours=None)


def job_run(conn, job, status, at):
    conn.execute("INSERT INTO job_run(job, started_at, finished_at, status) VALUES (?, ?, ?, ?)", (job, at, at, status))


def put_meta(conn, key, value):
    conn.execute("INSERT INTO pipeline_meta(key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = ?",
                 (key, value, value))


def feed_row(conn, n=1):
    for i in range(n):
        conn.execute(
            "INSERT INTO feed_listing(source, listing_id, url, company_name, title, first_seen_at, last_seen_at, "
            "active, backfill) VALUES ('simplify', ?, 'https://example.com/j', 'Acme', 'SWE', ?, ?, 1, 0)",
            (f"L{i}", ago(days=5), ago(days=5)))


class AlarmCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def kinds(self, now=NOW):
        return [a.kind for a in alarms.check(self.conn, now)]


class ReaderFailing(AlarmCase):
    def test_half_the_boards_of_an_ats_failing_fires_and_names_it(self):
        boards(self.conn, "lever", 6, errors=3)
        found = alarms.check(self.conn, NOW)
        self.assertEqual([a.kind for a in found], ["reader_failing"])
        self.assertIn("lever", found[0].text)
        self.assertIn("3 of 6", found[0].text)

    def test_under_half_failing_is_quiet(self):
        boards(self.conn, "lever", 6, errors=2)
        self.assertEqual(self.kinds(), [])

    def test_under_five_boards_is_quiet(self):
        boards(self.conn, "lever", 4, errors=4)
        self.assertEqual(self.kinds(), [])

    def test_boards_read_longer_than_48_hours_ago_do_not_count(self):
        boards(self.conn, "lever", 6, errors=6, at=ago(hours=49))
        self.assertEqual(self.kinds(), [])

    def test_each_ats_is_its_own_count(self):
        boards(self.conn, "lever", 3, errors=3, prefix="x")
        boards(self.conn, "ashby", 3, errors=0, prefix="y")
        self.assertEqual(self.kinds(), [])


class NumberDropped(AlarmCase):
    def record(self, coverage, recall, at):
        scorecard.record(self.conn, card(coverage, recall), at)

    def test_coverage_25_percent_below_a_week_ago_fires(self):
        self.record(0.40, 0.50, ago(days=7))
        self.record(0.30, 0.50, NOW)
        found = alarms.check(self.conn, NOW)
        self.assertEqual([a.kind for a in found], ["number_dropped"])
        self.assertIn("Coverage", found[0].text)

    def test_level_recall_dropping_fires(self):
        self.record(0.40, 0.60, ago(days=8))
        self.record(0.40, 0.40, NOW)
        found = alarms.check(self.conn, NOW)
        self.assertEqual([a.kind for a in found], ["number_dropped"])
        self.assertIn("Level", found[0].text)

    def test_a_smaller_drop_is_quiet(self):
        self.record(0.40, 0.50, ago(days=7))
        self.record(0.31, 0.38, NOW)
        self.assertEqual(self.kinds(), [])

    def test_no_card_6_to_8_days_older_is_quiet(self):
        self.record(0.40, 0.50, ago(days=5))
        self.record(0.10, 0.10, NOW)
        self.assertEqual(self.kinds(), [])
        self.record(0.40, 0.50, ago(days=9, now=NOW))
        self.assertEqual(self.kinds(), [])

    def test_a_missing_number_is_skipped(self):
        self.record(None, None, ago(days=7))
        self.record(0.1, 0.1, NOW)
        self.assertEqual(self.kinds(), [])

    def test_no_scorecard_yet(self):
        self.assertEqual(alarms.check(self.conn, NOW), [])
        self.assertEqual(alarms.scorecard_line(self.conn), "Scorecard not run yet")


class SourceFailing(AlarmCase):
    def test_a_stage_that_failed_twice_in_a_row_fires(self):
        for stage in ("discover", "resolve", "find_boards"):
            with self.subTest(stage=stage):
                self.conn.execute("DELETE FROM job_run WHERE job IN ('discover', 'resolve', 'find_boards')")
                job_run(self.conn, stage, "ok", ago(days=3))
                job_run(self.conn, stage, "failed", ago(days=2))
                job_run(self.conn, stage, "failed", ago(days=1))
                found = alarms.check(self.conn, NOW)
                self.assertEqual([a.kind for a in found], ["source_failing"])
                self.assertIn(stage, found[0].text)

    def test_one_failure_after_a_good_run_is_quiet(self):
        job_run(self.conn, "discover", "failed", ago(days=2))
        job_run(self.conn, "discover", "ok", ago(days=1))
        self.assertEqual(self.kinds(), [])
        job_run(self.conn, "discover", "failed", ago(hours=1))
        self.assertEqual(self.kinds(), [])

    def test_a_single_run_is_quiet(self):
        job_run(self.conn, "resolve", "failed", ago(days=1))
        self.assertEqual(self.kinds(), [])

    def test_the_feed_failing_two_polls_in_a_row_fires(self):
        put_meta(self.conn, "simplify_fail_streak", "2")
        put_meta(self.conn, "simplify_last_ok_at", ago(hours=1))
        found = alarms.check(self.conn, NOW)
        self.assertEqual([a.kind for a in found], ["source_failing"])
        self.assertIn("SimplifyJobs", found[0].text)

    def test_one_failed_poll_is_quiet(self):
        put_meta(self.conn, "simplify_fail_streak", "1")
        put_meta(self.conn, "simplify_last_ok_at", ago(hours=1))
        self.assertEqual(self.kinds(), [])

    def test_no_good_poll_for_24_hours_fires(self):
        put_meta(self.conn, "simplify_fail_streak", "0")
        put_meta(self.conn, "simplify_last_ok_at", ago(hours=25))
        found = alarms.check(self.conn, NOW)
        self.assertEqual([a.kind for a in found], ["source_failing"])
        self.assertIn("24 hours", found[0].text)

    def test_a_good_poll_within_24_hours_is_quiet(self):
        put_meta(self.conn, "simplify_last_ok_at", ago(hours=23))
        self.assertEqual(self.kinds(), [])

    def test_a_fresh_store_with_no_poll_and_no_rows_is_quiet(self):
        self.assertEqual(self.kinds(), [])

    def test_feed_rows_but_no_good_poll_on_record_fires(self):
        feed_row(self.conn)
        self.assertEqual(self.kinds(), ["source_failing"])

    def test_a_switched_off_feed_never_fires(self):
        off = dataclasses.replace(load_pipeline_config(),
                                  simplify=dataclasses.replace(load_pipeline_config().simplify, enabled=False))
        feed_row(self.conn)
        put_meta(self.conn, "simplify_fail_streak", "5")
        with mock.patch.object(alarms, "load_pipeline_config", return_value=off):
            self.assertEqual(self.kinds(), [])


class BoardsOverdue(AlarmCase):
    def overdue(self, n, *, interval=3600, behind_s=7200, prefix="o", active=1):
        company = seed_company(self.conn, f"Overdue {prefix}")
        for i in range(n):
            board = seed_board(self.conn, company, "greenhouse", f"{prefix}{i}")
            self.conn.execute(
                "UPDATE board SET next_read_at = ?, change_interval_s = ?, active = ? WHERE board_id = ?",
                (ago(hours=behind_s / 3600), interval, active, board))

    def test_50_boards_past_their_interval_fires(self):
        self.overdue(50)
        found = alarms.check(self.conn, NOW)
        self.assertEqual([a.kind for a in found], ["boards_overdue"])
        self.assertIn("50", found[0].text)

    def test_49_is_quiet(self):
        self.overdue(49)
        self.assertEqual(self.kinds(), [])

    def test_a_board_inside_its_interval_is_not_overdue(self):
        self.overdue(50, interval=4 * 3600, behind_s=3600)
        self.assertEqual(self.kinds(), [])

    def test_an_unknown_interval_uses_the_floor(self):
        company = seed_company(self.conn, "Floor")
        for i in range(50):
            board = seed_board(self.conn, company, "greenhouse", f"f{i}")
            self.conn.execute("UPDATE board SET next_read_at = ? WHERE board_id = ?",
                              (ago(hours=FLOOR_S / 3600 + 1), board))
        self.assertEqual(self.kinds(), ["boards_overdue"])
        self.conn.execute("UPDATE board SET next_read_at = ?", (ago(hours=FLOOR_S / 3600 - 1),))
        self.assertEqual(self.kinds(), [])

    def test_inactive_boards_and_boards_never_scheduled_do_not_count(self):
        self.overdue(50, active=0)
        company = seed_company(self.conn, "Never")
        for i in range(50):
            seed_board(self.conn, company, "greenhouse", f"n{i}")
        self.assertEqual(self.kinds(), [])

    def test_boards_of_a_switched_off_ats_do_not_count(self):
        self.overdue(50)
        cfg = dataclasses.replace(load_crawl_config(), ats_off={"greenhouse": "off for the test"})
        self.assertEqual([a.kind for a in alarms.check(self.conn, NOW, cfg=cfg)], [])


class NoticeOnceADay(unittest.TestCase):
    """The tick sends one Mac notice a day through `notify_once`, with the first alarm's text and a count."""

    def setUp(self):
        patch = mock.patch.object(tick, "probe_slugs", return_value=ProbeReport(0, 0, 0, 0, 0, 0))
        patch.start()
        self.addCleanup(patch.stop)
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.cfg = dataclasses.replace(load_crawl_config(), detail_per_read=0)
        self.notices = []

    def tick(self, now):
        client = FakeClient({URL: FetchError("network", "feed host down")})
        return run_tick(self.conn, client, cfg=self.cfg, now_fn=lambda: now, clock=lambda: 0.0,
                        sleep=lambda s: None, notifier=lambda title, text: self.notices.append((title, text)))

    def raise_alarm(self, stages=("discover",)):
        for stage in stages:
            job_run(self.conn, stage, "failed", ago(days=2))
            job_run(self.conn, stage, "failed", ago(days=1))

    def test_notice_once_a_day(self):
        self.raise_alarm()
        self.tick(NOW)
        self.tick(ago(hours=-1))
        self.assertEqual(len(self.notices), 1)
        title, text = self.notices[0]
        self.assertEqual(title, "Knock pipeline")
        self.assertIn("discover", text)
        self.assertNotIn("more", text)
        self.tick("2026-11-11T15:00:00Z")  # the next New York day
        self.assertEqual(len(self.notices), 2)

    def test_several_alarms_say_how_many_more(self):
        self.raise_alarm(("discover", "resolve", "find_boards"))
        self.tick(NOW)
        self.assertEqual(len(self.notices), 1)
        self.assertTrue(self.notices[0][1].endswith("and 2 more"), self.notices[0][1])

    def test_no_alarm_no_notice(self):
        self.tick(NOW)
        self.assertEqual(self.notices, [])

    def test_a_failing_alarm_check_never_fails_the_tick(self):
        with mock.patch.object(tick.alarms, "check", side_effect=RuntimeError("boom")):
            report = self.tick(NOW)
        self.assertIsNotNone(report.run_id)
        self.assertEqual(self.notices, [])

    def test_a_good_poll_records_the_time_for_the_24_hour_alarm(self):
        from tests.fakes import json_response
        client = FakeClient({URL: json_response([{"id": "x"}], etag='"v1"')})
        run_tick(self.conn, client, cfg=self.cfg, now_fn=lambda: NOW, clock=lambda: 0.0, sleep=lambda s: None,
                 notifier=lambda *a: None)
        value = self.conn.execute("SELECT value FROM pipeline_meta WHERE key = 'simplify_last_ok_at'").fetchone()
        self.assertEqual(value[0], NOW)

    def test_a_304_records_the_time_and_an_error_does_not(self):
        from tests.fakes import status_response
        quiet = lambda *a: None  # noqa: E731
        later = "2026-11-10T16:00:00Z"
        run_tick(self.conn, FakeClient({URL: status_response(304)}), cfg=self.cfg, now_fn=lambda: NOW,
                 clock=lambda: 0.0, sleep=lambda s: None, notifier=quiet)
        run_tick(self.conn, FakeClient({URL: FetchError("network", "down")}), cfg=self.cfg, now_fn=lambda: later,
                 clock=lambda: 0.0, sleep=lambda s: None, notifier=quiet)
        value = self.conn.execute("SELECT value FROM pipeline_meta WHERE key = 'simplify_last_ok_at'").fetchone()
        self.assertEqual(value[0], NOW)


if __name__ == "__main__":
    unittest.main()
