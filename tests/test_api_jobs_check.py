"""Check now (#194, design 4.3): `POST /api/jobs/check-now` records a request and starts the crawl job;
`GET /api/jobs/check` reports the check. Scratch store; the kicker is a fake and `launchctl` is never run."""
import json
import os
from unittest import mock

from outreach.crawl import kick
from outreach.pipeline import check_state
from tests.apitest import ApiCase


class CheckCase(ApiCase):
    def setUp(self):
        super().setUp()
        self.kicks = []

        def fake_kick():
            self.kicks.append(self.now)
            return "started"

        self.app.state.kick_crawl = fake_kick

    def check_now(self, **kw):
        return self.post("/api/jobs/check-now", {}, **kw)

    def requests(self):
        return self.conn.execute("SELECT * FROM event WHERE kind = 'check_now_requested'").fetchall()

    def progress(self, **fields):
        row = {"run_id": 7, "checked": 0, "total": 0, "new": 0, "started_at": "2026-11-10T15:00:00Z",
               "finished_at": None, "paused_hosts": []}
        row.update(fields)
        self.conn.execute("INSERT INTO pipeline_meta (key, value) VALUES (?, ?) "
                          "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                          (check_state.PROGRESS_KEY, json.dumps(row)))


class CheckNowTest(CheckCase):
    def test_check_now_records_request_and_kicks(self):
        res = self.check_now()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"requestedAt": self.now, "state": "started"})
        rows = self.requests()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["entity"], rows[0]["actor"], rows[0]["at"]), ("source", "devanshu", self.now))
        self.assertEqual(len(self.kicks), 1)

    def test_a_second_click_while_queued_kicks_again_and_records_nothing(self):
        first = self.check_now()
        second = self.check_now()
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json(), {"requestedAt": first.json()["requestedAt"], "state": "running"})
        self.assertEqual(len(self.requests()), 1)
        self.assertEqual(len(self.kicks), 2)

    def test_a_queued_click_stays_running_when_the_kick_runs_nothing(self):
        self.check_now()
        self.app.state.kick_crawl = lambda: "not_live"
        self.assertEqual(self.check_now().json()["state"], "running")
        self.assertEqual(len(self.requests()), 1)

    def test_check_now_while_a_check_runs_records_and_kicks_nothing(self):
        self.progress(checked=10, total=100)
        res = self.check_now()
        self.assertEqual(res.json()["state"], "running")
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.kicks, [])

    def test_check_now_after_a_finished_check_asks_again(self):
        self.progress(finished_at="2026-11-10T15:10:00Z", checked=5, total=5)
        res = self.check_now()
        self.assertEqual(res.json()["state"], "started")
        self.assertEqual(len(self.kicks), 1)

    def test_a_progress_row_1201_s_old_with_no_end_is_stale_and_a_click_starts_a_check(self):
        self.progress(started_at="2026-11-10T14:54:00Z")  # 1200 s old: the edge, still running
        self.assertEqual(self.check_now().json()["state"], "running")
        self.assertEqual((len(self.requests()), len(self.kicks)), (0, 0))
        self.progress(started_at="2026-11-10T14:53:59Z")  # 1201 s
        res = self.check_now()
        self.assertEqual(res.json()["state"], "started")
        self.assertEqual((len(self.requests()), len(self.kicks)), (1, 1))

    def test_a_replayed_key_kicks_once(self):
        self.check_now(key="check-key-0001")
        again = self.check_now(key="check-key-0001")
        self.assertEqual(again.headers["Idempotent-Replay"], "true")
        self.assertEqual(len(self.requests()), 1)
        self.assertEqual(len(self.kicks), 1)

    def test_the_answer_says_not_live_when_the_kicker_ran_nothing(self):
        self.app.state.kick_crawl = lambda: "not_live"
        res = self.check_now()
        self.assertEqual((res.status_code, res.json()["state"]), (200, "not_live"))
        self.assertEqual(len(self.requests()), 1)

    def test_a_kicker_that_fails_is_not_a_server_error(self):
        def boom():
            raise OSError("no launchctl")

        self.app.state.kick_crawl = boom
        with self.assertLogs("outreach.api", "ERROR"):
            res = self.check_now()
        self.assertEqual((res.status_code, res.json()["state"]), (200, "not_live"))
        self.assertEqual(len(self.requests()), 1)  # the crawl tick still picks the request up

    def test_a_body_with_a_stray_field_is_a_400(self):
        self.assertEqual(self.post("/api/jobs/check-now", {"x": 1}).status_code, 400)
        self.assertEqual(self.kicks, [])


class CheckStatusTest(CheckCase):
    def status(self):
        res = self.get("/api/jobs/check")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_check_status_idle_when_never_requested(self):
        self.assertEqual(self.status(), {"state": "idle", "checked": 0, "total": 0, "new": 0, "startedAt": None,
                                         "finishedAt": None, "pausedHosts": []})

    def test_check_status_queued_after_a_request(self):
        self.check_now()
        body = self.status()
        self.assertEqual((body["state"], body["checked"], body["total"]), ("queued", 0, 0))

    def test_check_status_reads_progress(self):
        self.progress(checked=1240, total=4013, new=3, paused_hosts=["boards.greenhouse.io"])
        self.assertEqual(self.status(), {"state": "running", "checked": 1240, "total": 4013, "new": 3,
                                         "startedAt": "2026-11-10T15:00:00Z", "finishedAt": None,
                                         "pausedHosts": ["boards.greenhouse.io"]})

    def test_check_status_stale_progress_reads_idle_until_a_request_comes(self):
        self.progress(checked=3, total=9, started_at="2026-11-10T14:53:59Z")  # 1201 s old, no end
        self.assertEqual(self.status()["state"], "idle")
        self.progress(checked=3, total=9, started_at="2026-11-10T14:54:01Z")  # 1199 s old
        self.assertEqual(self.status()["state"], "running")

    def test_check_status_done_when_finished(self):
        self.progress(checked=4013, total=4013, new=6, finished_at="2026-11-10T15:03:10Z")
        body = self.status()
        self.assertEqual((body["state"], body["new"], body["finishedAt"]), ("done", 6, "2026-11-10T15:03:10Z"))

    def test_a_request_after_a_finished_check_is_queued_again(self):
        self.progress(checked=9, total=9, finished_at="2026-11-10T15:03:10Z")
        self.set_now("2026-11-10T16:00:00Z")
        self.check_now()
        self.assertEqual(self.status()["state"], "queued")

    def test_the_pending_request_is_the_newest_above_the_last_claimed_one(self):
        self.assertIsNone(check_state.pending_request(self.conn))
        self.check_now()
        request = check_state.pending_request(self.conn)
        self.assertEqual(request.at, self.now)
        self.progress(finished_at="2026-11-10T15:20:00Z", request_event_id=request.event_id)
        self.assertIsNone(check_state.pending_request(self.conn))

    def test_a_request_in_the_same_second_as_the_finish_is_still_pending(self):
        # Claimed by event id, not by time: a check that claimed one request and ended within the second the next
        # request came in has not taken that one.
        self.check_now()
        first = check_state.pending_request(self.conn)
        self.progress(started_at=self.now, finished_at=self.now, request_event_id=first.event_id)
        self.assertEqual(self.status()["state"], "done")
        self.check_now()
        second = check_state.pending_request(self.conn)
        self.assertIsNotNone(second)
        self.assertGreater(second.event_id, first.event_id)
        self.assertEqual(second.at, first.at)
        self.assertEqual(self.status()["state"], "queued")

    def test_a_running_check_that_claimed_a_request_leaves_it_claimed_when_stale(self):
        self.check_now()
        request = check_state.pending_request(self.conn)
        self.progress(started_at="2026-11-10T14:53:59Z", request_event_id=request.event_id)  # 1201 s old, no end
        self.assertIsNone(check_state.pending_request(self.conn))
        self.assertEqual(self.status()["state"], "idle")


class KickCrawlTest(CheckCase):
    def test_outside_the_live_checkout_nothing_runs(self):
        with mock.patch.object(kick, "is_live", return_value=False), \
                mock.patch.object(kick.subprocess, "run") as run:
            self.assertEqual(kick.kick_crawl(), "not_live")
        run.assert_not_called()

    def test_inside_the_live_checkout_it_kickstarts_without_k(self):
        with mock.patch.object(kick, "is_live", return_value=True), \
                mock.patch.object(kick.subprocess, "run") as run:
            self.assertEqual(kick.kick_crawl(), "started")
        run.assert_called_once()
        cmd = run.call_args.args[0]
        self.assertEqual(cmd, ["launchctl", "kickstart", f"gui/{os.getuid()}/com.devanshu.outreach-crawl"])
        self.assertNotIn("-k", cmd)

    def test_the_app_starts_with_the_real_kicker(self):
        self.build()
        self.assertIs(self.app.state.kick_crawl, kick.kick_crawl)
