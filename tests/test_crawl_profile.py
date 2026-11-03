"""`python -m outreach.crawl profile` (#152, Task 2): where the tick time goes, read from stored `read_boards` runs.

Runs are seeded straight into `job_run` on a scratch store. Nothing here touches the network or the live store.
"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from outreach.crawl import __main__ as cli
from outreach.crawl.profile import profile
from tests.storetest import fresh_store


def row(boards=1, list_ms=0, body_ms=0, ingest_ms=0, list_requests=1, body_requests=0, new=0, closed=0, reopened=0):
    return {"boards": boards, "list_requests": list_requests, "list_ms": list_ms, "body_requests": body_requests,
            "body_ms": body_ms, "ingest_ms": ingest_ms, "new": new, "closed": closed, "reopened": reopened}


class ProfileCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.n = 0

    def seed(self, summary, *, job="read_boards", status="ok"):
        self.n += 1
        started = f"2026-11-01T00:{self.n:02d}:00Z"
        finished = None if status == "running" else f"2026-11-01T00:{self.n:02d}:30Z"
        self.conn.execute("INSERT INTO job_run (job, started_at, finished_at, status, summary) VALUES (?,?,?,?,?)",
                          (job, started, finished, status, json.dumps(summary)))
        self.conn.commit()

    def seed_three(self):
        for _ in range(3):
            self.seed({"seconds": 100.0, "by_ats": {
                "workday": row(boards=2, list_ms=10000, body_ms=20000, ingest_ms=10000, list_requests=2,
                               body_requests=5, new=4, closed=1, reopened=1),
                "greenhouse": row(boards=10, list_ms=1000, ingest_ms=500, list_requests=10, new=2)}})


class ProfileTests(ProfileCase):
    def test_profile_sums_runs(self):
        self.seed_three()
        result = profile(self.conn, 3)
        self.assertEqual(result.runs, 3)
        self.assertEqual(result.seconds, 300.0)
        self.assertEqual([r.ats for r in result.rows], ["workday", "greenhouse"])
        wd, gh = result.rows
        self.assertEqual(wd.boards, 6)
        self.assertEqual(wd.seconds, 120.0)
        self.assertAlmostEqual(wd.share, 0.4)
        self.assertEqual(wd.seconds_per_board, 20.0)
        self.assertEqual((wd.list_requests, wd.body_requests), (6, 15))
        self.assertEqual((wd.new, wd.closed, wd.reopened), (12, 3, 3))
        self.assertEqual(gh.seconds, 4.5)
        self.assertAlmostEqual(gh.share, 0.015)

    def test_profile_takes_only_the_newest_runs(self):
        self.seed_three()
        result = profile(self.conn, 2)
        self.assertEqual(result.runs, 2)
        self.assertEqual(result.seconds, 200.0)
        self.assertEqual(result.rows[0].seconds, 80.0)

    def test_profile_skips_runs_without_by_ats(self):
        self.seed({"seconds": 50.0, "by_ats": {"workday": row(list_ms=1000)}})
        self.seed({"seconds": 70.0})  # an older summary
        self.seed({"seconds": 60.0, "by_ats": {"workday": row(list_ms=3000)}})
        result = profile(self.conn, 3)
        self.assertEqual(result.runs, 2)
        self.assertEqual(result.seconds, 110.0)
        self.assertEqual(result.rows[0].seconds, 4.0)

    def test_skipped_runs_do_not_count_toward_the_limit(self):
        self.seed({"seconds": 10.0, "by_ats": {"workday": row(list_ms=1000)}})
        self.seed({"seconds": 10.0, "by_ats": {"workday": row(list_ms=2000)}})
        self.seed({"seconds": 70.0})
        self.assertEqual(profile(self.conn, 2).runs, 2)

    def test_profile_ignores_running_and_other_jobs(self):
        self.seed({"seconds": 10.0, "by_ats": {"workday": row(list_ms=1000)}})
        self.seed({"seconds": 10.0, "by_ats": {"workday": row(list_ms=9000)}}, status="running")
        self.seed({"seconds": 10.0, "by_ats": {"workday": row(list_ms=9000)}}, job="watch_pages")
        result = profile(self.conn, 5)
        self.assertEqual(result.runs, 1)
        self.assertEqual(result.rows[0].seconds, 1.0)

    def test_profile_with_no_runs_is_empty(self):
        result = profile(self.conn, 5)
        self.assertEqual((result.runs, result.seconds, result.rows), (0, 0.0, []))


class ProfileCliTests(ProfileCase):
    def setUp(self):
        super().setUp()
        path = self.conn.execute("PRAGMA database_list").fetchone()["file"]
        env = mock.patch.dict(os.environ, {"OUTREACH_DB": path})
        env.start()
        self.addCleanup(env.stop)
        self.live = tempfile.mkdtemp(prefix="outreach-crawl-profile-")
        self.addCleanup(shutil.rmtree, self.live, True)

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv, root=self.live, live_root=self.live)
        return code, out.getvalue(), err.getvalue()

    def test_profile_cli_json(self):
        self.seed_three()
        code, out, _ = self.run_cli(["profile", "--runs", "3", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual((data["runs"], data["seconds"]), (3, 300.0))
        self.assertEqual([r["ats"] for r in data["rows"]], ["workday", "greenhouse"])
        wd = data["rows"][0]
        self.assertEqual((wd["boards"], wd["seconds"], wd["body_requests"]), (6, 120.0, 15))
        self.assertAlmostEqual(wd["share"], 0.4)

    def test_profile_cli_table(self):
        self.seed_three()
        code, out, _ = self.run_cli(["profile", "--runs", "3"])
        self.assertEqual(code, 0)
        self.assertIn("workday", out)
        self.assertIn("greenhouse", out)
        self.assertIn("40.0%", out)
        self.assertIn("3 runs", out)

    def test_profile_cli_with_no_runs(self):
        code, out, _ = self.run_cli(["profile"])
        self.assertEqual(code, 0)
        self.assertIn("no read_boards runs", out)

    def test_profile_cli_refuses_outside_the_live_checkout(self):
        elsewhere = tempfile.mkdtemp(prefix="outreach-crawl-profile-")
        self.addCleanup(shutil.rmtree, elsewhere, True)
        with mock.patch("sqlite3.connect") as opened:
            with self.assertRaises(SystemExit):
                cli.main(["profile"], root=self.live, live_root=elsewhere)
        opened.assert_not_called()
