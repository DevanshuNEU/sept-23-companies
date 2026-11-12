"""The boards the SimplifyJobs feed names (#239): gated with the ATS seed's one-read gate, on a scratch store and a fake
client; and the tick step that runs it after the feed poll."""
import dataclasses
import json
import types
import unittest
from datetime import datetime, timedelta
from unittest import mock

from outreach.crawl import tick
from outreach.crawl.ats import reading_ats
from outreach.crawl.config import load_crawl_config
from outreach.crawl.polite import PoliteClient
from outreach.crawl.probe import ProbeReport
from outreach.crawl.tick import run_tick
from outreach.pipeline.config import load_pipeline_config
from outreach.sources.ats_seed import SeedConfig, SeedReport, load_seed_config
from outreach.sources.feed_seed import EVENT_KIND, run_feed_seed
from tests.fakes import FakeClient, json_response, status_response
from tests.storetest import fresh_store, seed_board, seed_company
from tests.test_sources_ats_seed import ASHBY, GH, UK_ENG, US_ENG, ashby_answer, gh_answer

NOW = "2026-11-12T14:00:00Z"
LEVER = "https://api.lever.co/v0/postings/{}?mode=json"
FEED_URL = load_pipeline_config().simplify.url


def later(days: float, now: str = NOW) -> str:
    return (datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ") + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def lever_answer(*jobs):
    return json_response([{"id": f"lv-{n}", "text": title, "categories": {"location": location},
                           "hostedUrl": f"https://jobs.lever.co/x/lv-{n}", "createdAt": 1762900000000}
                          for n, (title, location) in enumerate(jobs)])


class FeedSeedTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.n = 0
        self.cfg = load_crawl_config()
        self.routes = {}

    def listing(self, ats, token, name, *, seen=NOW, active=1):
        self.n += 1
        self.conn.execute(
            "INSERT INTO feed_listing(source, listing_id, url, ats, token, native_id, company_name, title, posted_hint, "
            "first_seen_at, last_seen_at, active, backfill) VALUES ('simplify', ?, ?, ?, ?, ?, ?, 'Software Engineer',"
            " ?, ?, ?, ?, 0)",
            (f"L{self.n}", f"https://example.test/{ats}/{token}/{self.n}", ats, token, str(self.n), name, seen, seen,
             seen, active))

    def seed(self, now=NOW, limit=None, config=None, ats=None):
        self.client = FakeClient(self.routes)
        polite = PoliteClient(self.client, self.cfg, sleep=lambda s: None)
        report = run_feed_seed(self.conn, polite, now=now, ats=reading_ats(self.cfg) if ats is None else ats,
                               limit=limit, config=config)
        self.assertEqual(self.client.unrouted, [])
        return report

    def one(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()

    def test_a_board_knock_lacks_is_gated_and_added(self):
        self.listing("greenhouse", "acme", "Acme")
        self.listing("ashby", "gamma", "Gamma")
        self.routes = {GH.format("acme"): gh_answer(US_ENG), ASHBY.format("gamma"): ashby_answer(UK_ENG)}
        report = self.seed()
        self.assertEqual(report, SeedReport(read=2, promoted=1, rejected=1, dead=0, errors=0, known=0, left=0))
        board = self.one("SELECT * FROM board WHERE ats = 'greenhouse' AND token = 'acme'")
        self.assertIsNotNone(board)
        self.assertIsNone(board["next_read_at"])  # the next tick reads it in full
        cand = self.one("SELECT * FROM board_candidate WHERE token = 'acme'")
        self.assertEqual((cand["source"], cand["gate_result"], cand["board_id"]), ("simplify", "promoted",
                                                                                  board["board_id"]))
        self.assertIsNone(self.one("SELECT 1 FROM board WHERE token = 'gamma'"))
        self.assertEqual(self.one("SELECT source_name FROM company_source WHERE company_id = ?",
                                  board["company_id"])[0], "simplify")
        event = self.one("SELECT detail FROM event WHERE entity = 'source' AND kind = ?", EVENT_KIND)
        self.assertEqual(json.loads(event[0])["promoted"], 1)

    def test_a_known_board_sends_nothing_whatever_the_letter_case(self):
        company = seed_company(self.conn, "Acme")
        seed_board(self.conn, company, "workday", "acme.wd5/External")
        seed_board(self.conn, company, "greenhouse", "acme")
        self.listing("workday", "acme.wd5/external", "Acme")
        self.listing("greenhouse", "ACME", "Acme")
        self.assertIsNone(self.seed())  # nothing due: no request, no run event
        self.assertEqual(self.client.requests, [])
        self.assertIsNone(self.one("SELECT 1 FROM event WHERE kind = ?", EVENT_KIND))
        self.assertEqual(self.one("SELECT count(*) FROM board")[0], 2)

    def test_an_ats_the_crawler_does_not_read_is_never_asked(self):
        self.listing("smartrecruiters", "acme", "Acme")  # switched off for robots.txt
        self.listing("icims", "careers-acme.icims.com", "Acme")  # no reader
        self.assertNotIn("smartrecruiters", reading_ats(self.cfg))
        self.assertIsNone(self.seed())
        self.assertEqual(self.client.requests, [])

    def test_inactive_and_tokenless_listings_are_skipped(self):
        self.listing("greenhouse", "acme", "Acme", active=0)
        self.listing("greenhouse", None, "Bare")
        self.listing("greenhouse", "not a token!", "Odd")
        self.assertIsNone(self.seed())
        self.assertEqual(self.client.requests, [])

    def test_at_most_the_limit_a_run_newest_listing_first(self):
        self.listing("greenhouse", "old", "Old", seen=later(-3))
        self.listing("greenhouse", "mid", "Mid", seen=later(-2))
        self.listing("greenhouse", "new", "New", seen=later(-1))
        self.listing("greenhouse", "new", "New", seen=later(-1))  # two listings, one board
        self.routes = {GH.format(t): gh_answer(US_ENG, name=t.title()) for t in ("old", "mid", "new")}
        report = self.seed(limit=2)
        self.assertEqual((report.read, report.promoted, report.left), (3, 2, 1))
        self.assertEqual(self.client.requests, [GH.format("new"), GH.format("mid")])
        report = self.seed(limit=2)
        self.assertEqual((report.promoted, report.known, report.left), (1, 2, 0))
        self.assertEqual(self.client.requests, [GH.format("old")])

    def test_the_limit_comes_from_the_seed_config_and_zero_is_off(self):
        self.assertEqual(load_seed_config().feed_per_tick, 10)
        self.listing("greenhouse", "acme", "Acme")
        self.routes = {GH.format("acme"): gh_answer(US_ENG)}
        self.assertIsNone(self.seed(config=SeedConfig(enabled=True, weekly_limit=10, retry_days=30, feed_per_tick=0)))
        self.assertEqual(self.client.requests, [])
        with self.assertRaises(ValueError):
            SeedConfig(enabled=True, weekly_limit=10, retry_days=30, feed_per_tick=-1)

    def test_a_rejected_board_waits_the_retry_days_and_an_error_is_tried_next_run(self):
        self.listing("lever", "birch", "Birch")
        self.listing("greenhouse", "beta", "Beta")
        self.routes = {LEVER.format("birch"): lever_answer(("Account Executive", "Austin, TX")),
                       GH.format("beta"): status_response(503)}
        report = self.seed()
        self.assertEqual((report.rejected, report.errors), (1, 1))
        self.routes[GH.format("beta")] = gh_answer(US_ENG)
        report = self.seed(now=later(1))
        self.assertEqual(self.client.requests, [GH.format("beta")])
        self.assertEqual(report.promoted, 1)
        self.assertIsNone(self.seed(now=later(29)))
        self.seed(now=later(31))
        self.assertEqual(self.client.requests, [LEVER.format("birch")])

    def test_a_board_the_stapply_seed_already_rejected_keeps_its_wait(self):
        self.conn.execute("INSERT INTO board_candidate(ats, token, name, source, found_at, gated_at, gate_result, "
                          "open_us_engineering) VALUES ('greenhouse', 'acme', 'Acme', 'stapply', ?, ?, "
                          "'no_us_engineering', 0)", (later(-5), later(-5)))
        self.listing("greenhouse", "acme", "Acme")
        self.assertIsNone(self.seed())
        self.assertEqual(self.client.requests, [])
        self.assertEqual(self.one("SELECT source FROM board_candidate WHERE token = 'acme'")[0], "stapply")

    def test_a_name_held_by_one_company_joins_it(self):
        company = seed_company(self.conn, "Acme Robotics")
        self.listing("greenhouse", "acmerobotics", "Acme Robotics")
        self.routes = {GH.format("acmerobotics"): gh_answer(US_ENG, name="Acme Robotics")}
        self.assertEqual(self.seed().promoted, 1)
        board = self.one("SELECT company_id, found_by FROM board WHERE token = 'acmerobotics'")
        self.assertEqual((board["company_id"], board["found_by"]), (company, "stapply_name"))
        self.assertEqual(self.one("SELECT count(*) FROM company")[0], 1)

    def test_should_stop_ends_the_run_and_the_rest_are_left(self):
        self.listing("greenhouse", "a1", "A1")
        self.listing("greenhouse", "a2", "A2")
        self.routes = {GH.format("a2"): gh_answer(US_ENG), GH.format("a1"): gh_answer(US_ENG)}
        asked = iter([False, True])
        self.client = FakeClient(self.routes)
        report = run_feed_seed(self.conn, PoliteClient(self.client, self.cfg, sleep=lambda s: None), now=NOW,
                               ats=reading_ats(self.cfg), should_stop=lambda: next(asked))
        self.assertEqual((report.promoted, report.left), (1, 1))
        self.assertEqual(len(self.client.requests), 1)


class TickFeedBoardsTest(unittest.TestCase):
    """The tick gates the feed's boards after its poll, through its own client, and a failure never fails it."""

    def setUp(self):
        patch = mock.patch.object(tick, "probe_slugs", return_value=ProbeReport(0, 0, 0, 0, 0, 0))
        patch.start()
        self.addCleanup(patch.stop)
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.cfg = dataclasses.replace(load_crawl_config(), detail_per_read=0)

    def feed(self):
        return json_response([{"source": "Simplify", "id": "L1", "url": "https://job-boards.greenhouse.io/acme/jobs/4012345",
                               "company_name": "Acme", "title": "Software Engineer, New Grad",
                               "locations": ["New York, NY"], "category": "Software", "active": True,
                               "is_visible": True, "date_posted": 1762950000, "date_updated": 1762950000}],
                             etag='"v1"')

    def tick(self, routes, now=NOW):
        client = FakeClient(routes)
        report = run_tick(self.conn, client, cfg=self.cfg, now_fn=lambda: now, clock=lambda: 0.0, sleep=lambda s: None)
        self.assertEqual(client.unrouted, [])
        return report, client

    def test_the_tick_adds_the_board_the_feed_names(self):
        report, client = self.tick({FEED_URL: self.feed(), GH.format("acme"): gh_answer(US_ENG)})
        self.assertEqual(client.requests, [FEED_URL, GH.format("acme")])
        self.assertEqual(report.feed_boards, {"promoted": 1, "rejected": 0, "dead": 0, "errors": 0, "known": 0,
                                              "left": 0})
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM board WHERE token = 'acme'").fetchone())
        summary = json.loads(self.conn.execute("SELECT summary FROM job_run WHERE run_id = ?",
                                               (report.run_id,)).fetchone()[0])
        self.assertEqual((summary["feed_boards"]["promoted"], summary["feed_boards_error"]), (1, ""))
        report, client = self.tick({FEED_URL: status_response(304), GH.format("acme"): gh_answer(US_ENG)},
                                   now=later(0.05))
        self.assertIsNone(report.feed_boards)  # nothing due any more

    def test_a_failure_is_text_and_the_tick_goes_on(self):
        with mock.patch("outreach.sources.feed_seed.run_feed_seed", side_effect=RuntimeError("boom")):
            report, _ = self.tick({FEED_URL: self.feed()})
        self.assertIsNone(report.feed_boards)
        self.assertIn("boom", report.feed_boards_error)
        self.assertEqual(self.conn.execute("SELECT status FROM job_run WHERE job = 'read_boards'").fetchone()[0], "ok")

    def test_skipped_when_the_pass_stopped(self):
        client = FakeClient({})
        run = types.SimpleNamespace(stopped=True, client=lambda: client)
        self.assertEqual(tick._feed_boards_step(self.conn, run, self.cfg, lambda: NOW, lambda: 0.0, 0.0), (None, ""))
        self.assertEqual(client.requests, [])


if __name__ == "__main__":
    unittest.main()
