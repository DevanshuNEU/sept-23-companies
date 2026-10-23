"""Health: job runs, the read queue, boards, watched pages and the sender, read from a scratch store."""
import json
import re
import unittest
from datetime import datetime, timedelta, timezone

from outreach.api.models.health import JobKind
from outreach.api.queries.jobs import SCHEDULED, UNSCHEDULED, expected_next_at, humanize, is_late
from tests.apitest import ApiCase, NOW
from tests.storetest import seed_company, seed_posting

SHA = "a" * 64


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class JobRuns(ApiCase):
    def setUp(self):
        super().setUp()
        self.conn.execute("DELETE FROM job_run")  # install leaves its own `rebuild` run; each test seeds its own

    def check_jobs(self) -> list[str]:
        sql = self.conn.execute("SELECT sql FROM sqlite_master WHERE name = 'job_run'").fetchone()[0]
        clause = re.search(r"CHECK \(job IN \(([^)]*)\)", sql).group(1)
        return re.findall(r"'([a-z_]+)'", clause)

    def add_run(self, job, started, status="ok", summary="{}", error=""):
        finished = None if status == "running" else iso(datetime.fromisoformat(started.replace("Z", "+00:00")) + timedelta(minutes=1))
        cur = self.conn.execute(
            "INSERT INTO job_run(job, started_at, finished_at, status, summary, error) VALUES (?,?,?,?,?,?)",
            (job, started, finished, status, summary if isinstance(summary, str) else json.dumps(summary), error),
        )
        return cur.lastrowid

    def health(self) -> dict:
        res = self.get("/api/health")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_empty_store_is_a_valid_health_view(self):
        body = self.health()
        self.assertEqual(body["asOf"], NOW)
        self.assertEqual(body["jobs"], [])
        self.assertEqual(
            body["readQueue"],
            {"queued": 0, "leased": 0, "leaseExpired": 0, "done": 0, "refused": 0, "oldestQueuedAgeHours": None, "lastNight": None},
        )
        self.assertEqual(body["boards"], {"active": 0, "dueNow": 0, "erroring": 0})
        self.assertEqual(body["watchedPages"], {"total": 0, "changedLast7Days": 0})
        self.assertEqual(body["sender"]["sentToday"], 0)
        self.assertIsInstance(body["store"]["version"], int)
        self.assertRegex(body["store"]["rulesetVersion"], r"^r-[0-9a-f]{12}$")

    def test_last_run_per_job_includes_every_new_job_name(self):
        names = self.check_jobs()
        self.assertEqual(len(names), 15)
        base = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)
        for i, job in enumerate(names):
            self.add_run(job, iso(base + timedelta(minutes=i)))
            self.add_run(job, iso(base - timedelta(days=1, minutes=-i)))  # an older run of the same job
        jobs = self.health()["jobs"]
        self.assertEqual(sorted(j["job"] for j in jobs), sorted(names))
        self.assertEqual(len(jobs), len(names))
        starts = [j["startedAt"] for j in jobs]
        self.assertEqual(starts, sorted(starts, reverse=True))
        self.assertTrue(all(s.startswith("2026-11-10") for s in starts))

    def test_job_kind_matches_the_store_check(self):
        self.assertEqual(set(JobKind.__args__), set(self.check_jobs()))

    def test_every_job_has_a_cadence_or_is_listed_unscheduled(self):
        self.assertEqual(set(self.check_jobs()) - set(SCHEDULED), set(UNSCHEDULED))
        self.assertFalse(set(SCHEDULED) & set(UNSCHEDULED))
        self.assertTrue(set(SCHEDULED) <= set(self.check_jobs()))

    def test_expected_next_for_interval_daily_and_weekday_jobs(self):
        self.assertEqual(expected_next_at("sender", "2026-11-10T15:00:00Z", NOW), "2026-11-10T15:10:00Z")
        self.assertEqual(expected_next_at("read_boards", "2026-11-10T15:00:00Z", NOW), "2026-11-10T15:30:00Z")
        self.assertEqual(expected_next_at("watch_pages", "2026-11-10T15:00:00Z", NOW), "2026-11-10T15:30:00Z")
        # A Friday 08:00 plan run (13:00Z in November) expects Monday 08:00 ET.
        self.assertEqual(expected_next_at("plan", "2026-11-06T13:00:05Z", NOW), "2026-11-09T13:00:00Z")
        # A Monday 08:00 plan run expects Tuesday 08:00.
        self.assertEqual(expected_next_at("plan", "2026-11-09T13:00:05Z", NOW), "2026-11-10T13:00:00Z")
        self.assertEqual(expected_next_at("prepare", "2026-11-06T10:30:02Z", NOW), "2026-11-09T10:30:00Z")
        # A daily 02:30 run on a Saturday still expects Sunday (read_postings is every day).
        self.assertEqual(expected_next_at("read_postings", "2026-11-07T07:30:01Z", NOW), "2026-11-08T07:30:00Z")

    def test_daily_time_stays_0230_local_across_the_november_change(self):
        # 2026-10-31 02:30 EDT is 06:30Z; the clocks go back on 2026-11-01, so 02:30 that day is EST, 07:30Z.
        self.assertEqual(expected_next_at("read_postings", "2026-10-31T06:30:03Z", NOW), "2026-11-01T07:30:00Z")
        self.assertEqual(expected_next_at("read_postings", "2026-11-01T07:30:03Z", NOW), "2026-11-02T07:30:00Z")
        # And the weekday 08:00 plan: Friday 2026-10-30 08:00 EDT expects Monday 2026-11-02 08:00 EST (13:00Z).
        self.assertEqual(expected_next_at("plan", "2026-10-30T12:00:04Z", NOW), "2026-11-02T13:00:00Z")

    def test_a_job_that_never_ran_expects_its_next_slot_or_nothing(self):
        self.assertEqual(expected_next_at("plan", None, "2026-11-10T15:14:00Z"), "2026-11-11T13:00:00Z")
        self.assertIsNone(expected_next_at("sender", None, NOW))

    def test_unscheduled_jobs_never_expect_a_run_and_are_never_late(self):
        for job in UNSCHEDULED:
            self.assertIsNone(expected_next_at(job, "2026-01-01T00:00:00Z", NOW), job)
        self.assertIsNone(expected_next_at("not_a_job", "2026-11-10T15:00:00Z", NOW))
        self.assertFalse(is_late("rank", None, "2030-01-01T00:00:00Z"))

    def test_late_only_after_the_grace(self):
        # An interval job: grace is max(2 * interval, 1 hour), so 1 hour for the sender.
        expected = "2026-11-10T15:10:00Z"
        self.assertFalse(is_late("sender", expected, "2026-11-10T15:10:00Z"))
        self.assertFalse(is_late("sender", expected, "2026-11-10T16:10:00Z"))  # exactly the grace
        self.assertTrue(is_late("sender", expected, "2026-11-10T16:10:01Z"))
        # A daily job: 2 hours.
        self.assertFalse(is_late("read_postings", "2026-11-10T07:30:00Z", "2026-11-10T09:30:00Z"))
        self.assertTrue(is_late("read_postings", "2026-11-10T07:30:00Z", "2026-11-10T09:30:01Z"))

    def test_late_flag_comes_through_the_view(self):
        self.add_run("sender", "2026-11-10T13:00:00Z")  # expected 13:10, now 15:14: more than an hour over
        self.add_run("read_boards", "2026-11-10T15:00:00Z")  # expected 15:30, not yet
        self.add_run("rank", "2026-11-01T00:00:00Z")  # unscheduled
        by = {j["job"]: j for j in self.health()["jobs"]}
        self.assertTrue(by["sender"]["late"])
        self.assertEqual(by["sender"]["expectedNextAt"], "2026-11-10T13:10:00Z")
        self.assertFalse(by["read_boards"]["late"])
        self.assertEqual(by["read_boards"]["expectedNextAt"], "2026-11-10T15:30:00Z")
        self.assertFalse(by["rank"]["late"])
        self.assertIsNone(by["rank"]["expectedNextAt"])

    def test_summary_counts_keep_integers_only(self):
        summary = {"boards_read": 12, "postings_seen": 340, "ratio": 0.5, "note": "ok", "flag": True,
                   "nested": {"a": 1}, "list": [1], "nothing": None, "zero": 0}
        self.add_run("read_boards", "2026-11-10T15:00:00Z", summary=summary)
        counts = self.health()["jobs"][0]["counts"]
        self.assertEqual(counts, [{"label": "Boards read", "value": 12}, {"label": "Postings seen", "value": 340},
                                  {"label": "Zero", "value": 0}])
        self.assertEqual(humanize("boards_read"), "Boards read")
        self.assertEqual(humanize("x"), "X")

    def test_a_summary_that_is_not_an_object_has_no_counts(self):
        self.add_run("import", "2026-11-10T15:00:00Z", summary="[1, 2]")
        self.assertEqual(self.health()["jobs"][0]["counts"], [])

    def test_read_queue_counts_and_expired_leases(self):
        company = seed_company(self.conn)
        rows = [("queued", None, "2026-11-10T12:00:00Z"), ("queued", None, "2026-11-10T13:44:00Z"),
                ("leased", "2026-11-10T16:00:00Z", "2026-11-10T10:00:00Z"),
                ("leased", "2026-11-10T14:00:00Z", "2026-11-10T10:00:00Z"),  # lease ran out
                ("done", None, "2026-11-09T10:00:00Z"), ("refused", None, "2026-11-09T10:00:00Z")]
        for i, (state, lease, enq) in enumerate(rows):
            posting = seed_posting(self.conn, company, f"Engineer {i}")
            self.conn.execute(
                "INSERT INTO read_queue(posting_id, body_sha256, reader_version, priority, state, lease_until, enqueued_at)"
                " VALUES (?,?,?,?,?,?,?)", (posting, SHA, "v1", 1, state, lease, enq))
        queue = self.health()["readQueue"]
        self.assertEqual((queue["queued"], queue["leased"], queue["leaseExpired"], queue["done"], queue["refused"]),
                         (2, 1, 1, 1, 1))
        # The oldest queued row went in at 12:00; it is 15:14 now.
        self.assertAlmostEqual(queue["oldestQueuedAgeHours"], 3.2, places=1)

    def test_last_night_is_the_newest_read_postings_run(self):
        self.add_run("read_postings", "2026-11-08T07:30:00Z", summary={"read": 5})
        self.add_run("read_postings", "2026-11-10T07:30:00Z", status="partial", summary={"read": 9})
        self.add_run("read_boards", "2026-11-10T15:00:00Z")
        night = self.health()["readQueue"]["lastNight"]
        self.assertEqual(night["job"], "read_postings")
        self.assertEqual(night["startedAt"], "2026-11-10T07:30:00Z")
        self.assertEqual(night["status"], "partial")
        self.assertEqual(night["counts"], [{"label": "Read", "value": 9}])

    def test_boards_and_watched_pages(self):
        def board(token, active, last_read, error_streak=0, next_read_at=None):
            company = seed_company(self.conn, token)
            at = "2026-11-10T10:00:00Z" if last_read else None
            self.conn.execute(
                "INSERT INTO board(company_id, ats, token, active, last_read, last_read_at, error_streak, streak_since,"
                " next_read_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (company, "ashby", token, active, last_read, at, error_streak, at, next_read_at, "2026-10-01T00:00:00Z"))
            return company

        board("never", 1, None)  # never read: due now
        board("later", 1, "ok", next_read_at="2026-11-10T16:00:00Z")
        board("due", 1, "ok", next_read_at="2026-11-10T15:14:00Z")
        board("broken", 1, "error", error_streak=2, next_read_at="2026-11-10T20:00:00Z")
        board("off", 0, "error", error_streak=3)  # inactive boards count nowhere
        board("off2", 0, None)

        def page(name, last_change):
            company = seed_company(self.conn, name)
            self.conn.execute("INSERT INTO watched_page(company_id, url, last_change_at) VALUES (?,?,?)",
                              (company, f"https://{name}.example/careers", last_change))

        page("p1", "2026-11-04T15:14:00Z")  # exactly 7 days ago
        page("p2", "2026-11-09T00:00:00Z")
        page("p3", "2026-10-01T00:00:00Z")
        page("p4", None)
        body = self.health()
        self.assertEqual(body["boards"], {"active": 4, "dueNow": 2, "erroring": 1})
        self.assertEqual(body["watchedPages"], {"total": 4, "changedLast7Days": 2})

    def sender(self, **cols):
        sets = ", ".join(f"{k} = ?" for k in cols)
        self.conn.execute(f"UPDATE sender SET {sets} WHERE sender_id = 1", tuple(cols.values()))

    def test_sender_runs_in_the_window_on_a_weekday(self):
        sender = self.health()["sender"]
        self.assertEqual(sender["state"], "running")  # Tuesday 10:14 in New York
        self.assertEqual((sender["sentToday"], sender["limitToday"]), (0, 20))

    def test_sender_states(self):
        self.set_now("2026-11-14T15:14:00Z")  # a Saturday
        self.assertEqual(self.health()["sender"]["state"], "weekend")
        self.assertEqual(self.health()["sender"]["limitToday"], 0)
        self.set_now("2026-11-10T23:30:00Z")  # Tuesday 18:30 in New York
        self.assertEqual(self.health()["sender"]["state"], "outside_window")
        self.set_now(NOW)
        self.sender(auth_state="signed_out")
        self.assertEqual(self.health()["sender"]["state"], "signed_out")
        self.sender(paused=1, pause_kind="by_you", paused_at=NOW)
        self.assertEqual(self.health()["sender"]["state"], "paused_by_you")
        self.sender(pause_kind="hard_block")
        self.assertEqual(self.health()["sender"]["state"], "paused_hard_block")

    def test_job_detail_lists_runs_with_error_text(self):
        for day in range(1, 26):
            self.add_run("read_boards", f"2026-10-{day:02d}T10:00:00Z")
        self.add_run("read_boards", "2026-11-09T10:00:00Z", status="failed", error="Greenhouse said 503", summary={"boards_read": 3})
        self.add_run("sender", "2026-11-10T15:00:00Z")
        res = self.get("/api/health/jobs/read_boards")
        self.assertEqual(res.status_code, 200)
        runs = res.json()
        self.assertEqual(len(runs), 20)
        self.assertTrue(all(r["job"] == "read_boards" for r in runs))
        self.assertEqual(runs[0]["startedAt"], "2026-11-09T10:00:00Z")
        self.assertEqual(runs[0]["status"], "failed")
        self.assertEqual(runs[0]["error"], "Greenhouse said 503")
        self.assertEqual(runs[0]["counts"], [{"label": "Boards read", "value": 3}])
        starts = [r["startedAt"] for r in runs]
        self.assertEqual(starts, sorted(starts, reverse=True))

    def test_job_detail_for_a_job_that_never_ran_is_empty(self):
        res = self.get("/api/health/jobs/plan")
        self.assertEqual((res.status_code, res.json()), (200, []))

    def test_unknown_job_is_not_found(self):
        res = self.get("/api/health/jobs/nope")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["code"], "not_found")

    def test_a_running_job_has_no_finish_time(self):
        self.add_run("sender", "2026-11-10T15:10:00Z", status="running")
        job = self.health()["jobs"][0]
        self.assertEqual((job["status"], job["finishedAt"]), ("running", None))


if __name__ == "__main__":
    unittest.main()
