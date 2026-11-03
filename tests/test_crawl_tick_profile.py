"""The tick summary's per ATS counts and milliseconds (#152, Task 1): what each ATS cost one tick.

A scratch store, a `FakeClient` answering from saved fixtures, and a fake clock that moves 100 ms on every request.
Nothing here touches the network or the live store.
"""
import dataclasses
import json
import unittest
from pathlib import Path

from outreach.api.queries.jobs import counts_of
from outreach.net import Response
from tests.fakes import status_response
from tests.test_crawl_tick import FLOOR, TickCase, at, gh, lever

FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
GH_JOBS = (FIXTURES / "greenhouse_jobs.json").read_bytes()
LEVER_JOBS = (FIXTURES / "lever_list.json").read_bytes()
KEYS = {"boards", "list_requests", "list_ms", "body_requests", "body_ms", "ingest_ms", "new", "closed", "reopened"}


class ProfileCase(TickCase):
    """Every request answered through a route that moves the clock 100 ms first."""

    def setUp(self):
        super().setUp()
        self.gh_count = len(json.loads(GH_JOBS)["jobs"])
        self.lever_count = len(json.loads(LEVER_JOBS))

    def slow(self, response: Response):
        def route(method, url, headers, body):
            self.clock.t += 0.1
            return response
        return route

    def summary(self, report):
        return json.loads(self.conn.execute("SELECT summary FROM job_run WHERE run_id = ?",
                                            (report.run_id,)).fetchone()[0])


class ByAtsTests(ProfileCase):
    def test_summary_has_per_ats_counts(self):
        self.board("g1", ats="greenhouse")
        self.board("l1", ats="lever")
        report = self.tick({gh("g1"): self.slow(Response(200, "", GH_JOBS, {})),
                            lever("l1"): self.slow(Response(200, "", LEVER_JOBS, {}))})
        by_ats = self.summary(report)["by_ats"]
        self.assertEqual(set(by_ats), {"greenhouse", "lever"})
        self.assertEqual(report.by_ats, by_ats)
        for ats, jobs in (("greenhouse", self.gh_count), ("lever", self.lever_count)):
            row = by_ats[ats]
            self.assertEqual(set(row), KEYS)
            self.assertEqual(row["boards"], 1)
            self.assertGreaterEqual(row["list_requests"], 1)
            self.assertGreaterEqual(row["list_ms"], 100)
            self.assertEqual(row["new"], jobs)
            self.assertEqual((row["closed"], row["reopened"]), (0, 0))
            self.assertTrue(all(isinstance(v, int) for v in row.values()))

    def test_list_ms_is_the_list_request_only(self):
        self.board("g1")
        report = self.tick({gh("g1"): self.slow(Response(200, "", GH_JOBS, {}))})
        row = report.by_ats["greenhouse"]
        self.assertEqual(row["list_requests"], 1)
        self.assertEqual(row["list_ms"], 100)
        self.assertEqual((row["body_requests"], row["body_ms"]), (0, 0))

    def test_body_requests_and_ms_are_counted(self):
        self.board("acme", ats="smartrecruiters")
        sr = "https://api.smartrecruiters.com/v1/companies/acme/postings"
        detail = (FIXTURES / "smartrecruiters_detail.json").read_bytes()
        routes = {f"{sr}?limit=100&offset=0": Response(200, "", (FIXTURES / "smartrecruiters_p0.json").read_bytes(), {}),
                  f"{sr}?limit=100&offset=2": Response(200, "", (FIXTURES / "smartrecruiters_p1.json").read_bytes(), {})}
        routes |= {f"{sr}/{i}": self.slow(Response(200, "", detail, {})) for i in
                   ("743999000111222", "743999000111333", "743999000111444")}
        report = self.tick(routes, cfg=dataclasses.replace(self.cfg, ats_off={}))  # the reader is off by default
        row = report.by_ats["smartrecruiters"]
        details = [u for u in self.client.requests if u.startswith(f"{sr}/")]
        self.assertGreaterEqual(len(details), 1)
        self.assertEqual(row["list_requests"], len(self.client.requests) - len(details))
        self.assertEqual(row["body_requests"], len(details))
        self.assertGreaterEqual(row["body_ms"], 100 * len(details))
        self.assertGreater(row["new"], 0)

    def test_held_read_counted_once(self):
        bid = self.board("g1")
        self.read_before(bid, 3600, next_in=-60)  # last read good, so an error read is held for `finish`
        report = self.tick({gh("g1"): self.slow(status_response(500))})
        self.assertEqual(report.error, 1)
        row = report.by_ats["greenhouse"]
        self.assertEqual(row["boards"], 1)
        self.assertEqual(row["list_requests"], 1)
        self.assertEqual(row["list_ms"], 100)

    def test_deferred_board_is_counted_once(self):
        # Five boards with open postings that all read empty are a mass empty: held, then deferred. Each list is
        # counted once and nothing is ingested.
        for i in range(5):
            self.board(f"e{i}")
        self.tick({gh(f"e{i}"): Response(200, "", GH_JOBS, {}) for i in range(5)})
        empty = Response(200, "", (FIXTURES / "greenhouse_empty.json").read_bytes(), {})
        report = self.tick({gh(f"e{i}"): self.slow(empty) for i in range(5)}, now_fn=lambda: at(FLOOR))
        self.assertEqual(report.deferred, 5)
        row = report.by_ats["greenhouse"]
        self.assertEqual((row["boards"], row["list_requests"]), (5, 5))
        self.assertGreaterEqual(row["list_ms"], 500)  # the polite spacing between requests is part of the list time
        self.assertEqual((row["new"], row["ingest_ms"]), (0, 0))

    def test_health_counts_ignore_by_ats(self):
        self.board("g1")
        report = self.tick({gh("g1"): self.slow(Response(200, "", GH_JOBS, {}))})
        raw = self.conn.execute("SELECT summary FROM job_run WHERE run_id = ?", (report.run_id,)).fetchone()[0]
        self.assertIn("by_ats", json.loads(raw))
        counts = counts_of(raw)
        self.assertTrue(counts)
        self.assertTrue(all(isinstance(c.value, int) for c in counts))
        self.assertNotIn("By ats", [c.label for c in counts])

    def test_no_boards_leaves_by_ats_empty(self):
        report = self.tick({})
        self.assertEqual(report.by_ats, {})
        self.assertEqual(self.summary(report)["by_ats"], {})


if __name__ == "__main__":
    unittest.main()
