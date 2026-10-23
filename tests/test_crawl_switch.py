"""The per-ATS off switch (#46): a reader whose host's robots.txt forbids it is switched off in config.toml.

A board on a switched-off ATS is never read, probed or slug-probed, and keeps its state, postings and decisions as they
are. The polite client refuses every request to a switched-off ATS's host, a redirect hop included. Fresh stores and
fake clients only: nothing here touches the network or the live store.
"""
import contextlib
import dataclasses
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from outreach.crawl import __main__ as cli
from outreach.crawl import tick
from outreach.crawl.ats import reading_ats, supported_ats
from outreach.crawl.cadence import add_seconds
from outreach.crawl.config import load_crawl_config
from outreach.crawl.polite import HostOff, HostTripped, PoliteClient
from outreach.crawl.probe import ProbeReport, probe_slugs
from outreach.crawl.tick import read_company_boards, run_tick
from outreach.crawl.watch import watch_pages
from outreach.net import Response
from tests.fakes import FakeClient, status_response
from tests.storetest import fresh_store, seed_board, seed_company

FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
GH_JOBS = (FIXTURES / "greenhouse_jobs.json").read_bytes()
PINPOINT_LIST = (FIXTURES / "pinpoint_list.json").read_bytes()
OFF = load_crawl_config()  # the shipped config: SmartRecruiters and Pinpoint off
ON = dataclasses.replace(OFF, ats_off={})  # every reader on, as after the two lines are deleted
T0 = "2026-10-22T08:00:00Z"
DAY = 86400

SR_LIST = "https://api.smartrecruiters.com/v1/companies/acme/postings?limit=100&offset=0"
SR_DETAIL = "https://api.smartrecruiters.com/v1/companies/acme/postings/743999000111222"
SR_PAGE = "https://jobs.smartrecruiters.com/acme/743999000111222"
PP_LIST = "https://acme.pinpointhq.com/postings.json"


def gh(token):
    return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


def ok(body, etag='"A"'):
    return Response(200, "", body, {"etag": etag})


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def polite(routes, cfg=OFF):
    inner = FakeClient(routes)
    clock = Clock()
    return PoliteClient(inner, cfg, clock=clock, sleep=clock.sleep), inner


class ShippedSwitchTests(unittest.TestCase):
    def test_the_adapters_stay_registered_and_only_the_switch_stops_them(self):
        self.assertLessEqual({"smartrecruiters", "pinpoint"}, supported_ats())
        self.assertEqual(reading_ats(OFF), supported_ats() - {"smartrecruiters", "pinpoint"})
        self.assertEqual(reading_ats(ON), supported_ats())


class PoliteGuardTests(unittest.TestCase):
    """The last line: whatever asks, no request reaches a switched-off ATS's host."""

    def test_every_host_of_a_switched_off_ats_is_refused_with_no_request(self):
        for url in (SR_LIST, SR_DETAIL, SR_PAGE, PP_LIST, "https://acme.pinpointhq.com/en/postings/1"):
            client, inner = polite({})
            with self.assertRaises(HostOff) as caught:
                client.get(url)
            self.assertIsInstance(caught.exception, HostTripped)  # every caller already defers on it
            self.assertEqual(inner.requests, [], url)
            self.assertIn("switched off", str(caught.exception))
            self.assertIn("robots.txt", str(caught.exception))
        client, inner = polite({})
        with self.assertRaises(HostOff):
            client.post(SR_LIST, b"{}")
        with self.assertRaises(HostOff):
            client.download(PP_LIST, Path(tempfile.gettempdir()) / "never-written")
        self.assertEqual(inner.requests, [])

    def test_a_redirect_hop_into_a_switched_off_host_is_refused(self):
        careers = "https://acme.example/careers"
        client, inner = polite({careers: Response(302, careers, b"", {"location": "https://acme.pinpointhq.com/"})})
        with self.assertRaises(HostOff):
            client.get(careers, follow_redirects=True)
        self.assertEqual(inner.requests, [careers])

    def test_a_reader_turned_back_on_sends_again(self):
        client, inner = polite({SR_LIST: status_response(404)}, cfg=ON)
        self.assertEqual(client.get(SR_LIST).status, 404)
        self.assertEqual(inner.requests, [SR_LIST])

    def test_other_hosts_are_untouched(self):
        client, inner = polite({gh("acme"): ok(GH_JOBS)})
        self.assertEqual(client.get(gh("acme")).status, 200)
        self.assertEqual(inner.requests, [gh("acme")])


def snapshot(conn, board_ids):
    """Everything a read could change for these boards: the board rows, their postings and bodies, the decisions,
    the reads, the review items and the events."""
    marks = ",".join("?" * len(board_ids))
    postings = [r[0] for r in conn.execute(f"SELECT posting_id FROM posting WHERE board_id IN ({marks})", board_ids)]
    pmarks = ",".join("?" * len(postings)) or "NULL"
    q = {
        "board": f"SELECT * FROM board WHERE board_id IN ({marks}) ORDER BY board_id",
        "posting": f"SELECT * FROM posting WHERE board_id IN ({marks}) ORDER BY posting_id",
        "body": f"SELECT * FROM posting_body WHERE posting_id IN ({pmarks}) ORDER BY posting_id",
        "decision": f"SELECT * FROM decision WHERE posting_id IN ({pmarks}) ORDER BY rowid",
        "board_read": f"SELECT * FROM board_read WHERE board_id IN ({marks}) ORDER BY rowid",
        "review_item": "SELECT * FROM review_item ORDER BY rowid",
        "event": f"SELECT * FROM event WHERE (entity = 'board' AND entity_id IN ({marks})) "
                 f"OR (entity = 'posting' AND entity_id IN ({pmarks})) ORDER BY rowid",
    }
    args = {"board": board_ids, "posting": board_ids, "body": postings, "decision": postings,
            "board_read": board_ids, "review_item": [], "event": board_ids + postings}
    return {k: [tuple(r) for r in conn.execute(sql, args[k])] for k, sql in q.items()}


class TickTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(tick, "probe_slugs", return_value=ProbeReport(0, 0, 0, 0, 0, 0))
        patch.start()
        self.addCleanup(patch.stop)
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.gh = seed_board(self.conn, seed_company(self.conn, "Greenhouse Co"), "greenhouse", "acme")
        self.pp_company = seed_company(self.conn, "Acme Pinpoint")
        self.pp = seed_board(self.conn, self.pp_company, "pinpoint", "acme")
        self.sr = seed_board(self.conn, seed_company(self.conn, "Acme Smart"), "smartrecruiters", "acme")

    def run_tick(self, routes, cfg, at):
        client = FakeClient(routes)
        clock = Clock()
        report = run_tick(self.conn, client, cfg=cfg, now_fn=lambda: at, clock=clock, sleep=clock.sleep)
        self.assertEqual(client.unrouted, [])
        return report, client

    def was_read_then_switched_off(self):
        """Pinpoint was read while it was on (real postings and decisions); then an empty read set its empty streak,
        and SmartRecruiters is in an error streak one read short of its Needs-you item. Both are due again."""
        self.run_tick({gh("acme"): ok(GH_JOBS), PP_LIST: ok(PINPOINT_LIST)},
                      dataclasses.replace(ON, ats_off={"smartrecruiters": "off for this setup"}), T0)
        self.assertGreater(self.conn.execute("SELECT count(*) FROM posting WHERE board_id = ?", (self.pp,)).fetchone()[0],
                           0)
        self.conn.execute("UPDATE board SET last_read = 'empty', last_read_at = ?, streak_since = ?, empty_streak = 1, "
                          "next_read_at = ? WHERE board_id = ?", (T0, T0, T0, self.pp))
        self.conn.execute("UPDATE board SET last_read = 'error', last_read_at = ?, streak_since = ?, error_streak = 2, "
                          "next_read_at = ? WHERE board_id = ?", (T0, T0, T0, self.sr))

    def test_a_switched_off_board_keeps_its_state_postings_and_decisions_through_a_tick(self):
        self.was_read_then_switched_off()
        before = snapshot(self.conn, [self.pp, self.sr])
        report, client = self.run_tick({gh("acme"): status_response(304)}, OFF, add_seconds(T0, 2 * DAY))
        self.assertEqual(client.requests, [gh("acme")])  # only the board that is on
        self.assertEqual(snapshot(self.conn, [self.pp, self.sr]), before)
        self.assertEqual((report.switched_off, report.read, report.deferred, report.error), (2, 1, 0, 0))
        job = self.conn.execute("SELECT status, summary FROM job_run WHERE run_id = ?", (report.run_id,)).fetchone()
        self.assertEqual((job["status"], json.loads(job["summary"])["switched_off"]), ("ok", 2))
        self.assertEqual(report.tripped, ())

    def test_turning_the_reader_back_on_reads_the_board_again(self):
        self.was_read_then_switched_off()
        self.run_tick({gh("acme"): status_response(304)}, OFF, add_seconds(T0, 2 * DAY))
        report, client = self.run_tick({gh("acme"): status_response(304), PP_LIST: status_response(304),
                                        SR_LIST: status_response(404)}, ON, add_seconds(T0, 3 * DAY))
        self.assertEqual(sorted(client.requests), sorted([gh("acme"), PP_LIST, SR_LIST]))
        self.assertEqual(report.switched_off, 0)

    def test_read_company_boards_skips_a_switched_off_board_and_counts_it(self):
        gh2 = seed_board(self.conn, self.pp_company, "greenhouse", "acmepinpoint")
        client = FakeClient({gh("acmepinpoint"): ok(GH_JOBS)})
        clock = Clock()
        before = snapshot(self.conn, [self.pp])
        report = read_company_boards(self.conn, client, [self.pp_company], cfg=OFF, now_fn=lambda: T0, clock=clock,
                                     sleep=clock.sleep)
        self.assertEqual(client.requests, [gh("acmepinpoint")])
        self.assertEqual((report.switched_off, report.read), (1, 1))
        self.assertEqual(snapshot(self.conn, [self.pp]), before)
        self.assertEqual(self.conn.execute("SELECT last_read FROM board WHERE board_id = ?", (gh2,)).fetchone()[0], "ok")


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.company = seed_company(self.conn, "Zephyrine")

    def probe(self, cfg, routes):
        client, inner = polite(routes, cfg)
        report = probe_slugs(self.conn, [self.company], client, limit=5, cfg=cfg, now=T0)
        return report, inner

    def test_slug_probing_asks_no_switched_off_ats(self):
        cfg = dataclasses.replace(OFF, ats_off={"greenhouse": "a test reason"})
        routes = {"https://api.lever.co/v0/postings/zephyrine?mode=json": status_response(404),
                  "https://api.ashbyhq.com/posting-api/job-board/zephyrine?includeCompensation=true":
                      status_response(404)}
        report, inner = self.probe(cfg, routes)
        self.assertEqual(sorted(inner.requests), sorted(routes))
        self.assertEqual(report.probed, 1)

    def test_with_every_probed_ats_off_slug_probing_sends_nothing(self):
        cfg = dataclasses.replace(OFF, ats_off={a: "a test reason" for a in ("greenhouse", "lever", "ashby")})
        report, inner = self.probe(cfg, {})
        self.assertEqual(inner.requests, [])
        self.assertEqual((report.added, report.errors), (0, 0))
        self.assertIsNone(self.conn.execute("SELECT 1 FROM event WHERE kind = 'slug_probed'").fetchone())


class WatchTests(unittest.TestCase):
    def test_a_watched_page_on_a_switched_off_host_is_never_fetched(self):
        conn = fresh_store()
        self.addCleanup(conn.close)
        company = seed_company(conn, "Acme")
        conn.execute("INSERT INTO watched_page(company_id, url) VALUES (?, ?)", (company, "https://acme.pinpointhq.com/"))
        client, inner = polite({})
        report = watch_pages(conn, client, cfg=OFF, now_fn=lambda: T0)
        self.assertEqual(inner.requests, [])
        self.assertEqual((report.deferred, report.error), (1, 0))
        self.assertEqual(conn.execute("SELECT count(*) FROM review_item").fetchone()[0], 0)


class CliTests(unittest.TestCase):
    """`read` and `probe` say in plain words that a switched-off reader is not used, and send nothing to it."""

    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        path = self.conn.execute("PRAGMA database_list").fetchone()["file"]
        env = mock.patch.dict(os.environ, {"OUTREACH_DB": path})
        env.start()
        self.addCleanup(env.stop)
        self.live = tempfile.mkdtemp(prefix="outreach-crawl-switch-")
        self.addCleanup(shutil.rmtree, self.live, True)

    def run_cli(self, argv, client):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv, root=self.live, live_root=self.live, client=client)
        return code, out.getvalue(), err.getvalue()

    def test_read_refuses_a_switched_off_board_in_plain_words(self):
        company = seed_company(self.conn, "Acme")
        board = seed_board(self.conn, company, "pinpoint", "acme")
        before = snapshot(self.conn, [board])
        client = FakeClient({})
        code, _, err = self.run_cli(["read", "--company", str(company)], client)
        self.assertEqual(code, 1)
        self.assertEqual(client.requests, [])
        self.assertIn(f"company {company}: not reading its pinpoint board acme", err)
        self.assertIn("the pinpoint reader is switched off (robots.txt", err)
        self.assertEqual(snapshot(self.conn, [board]), before)

    def test_read_still_reads_the_boards_that_are_on(self):
        company = seed_company(self.conn, "Acme")
        seed_board(self.conn, company, "smartrecruiters", "acme")
        seed_board(self.conn, company, "greenhouse", "acme")
        client = FakeClient({gh("acme"): ok(GH_JOBS)})
        code, out, err = self.run_cli(["read", "--company", str(company)], client)
        self.assertEqual(client.requests, [gh("acme")])
        self.assertIn("not reading its smartrecruiters board acme", err)
        self.assertIn("switched off 1", out)
        self.assertEqual(code, 1)  # not everything asked for was read

    def test_probe_refuses_a_switched_off_ats_and_sends_nothing(self):
        for ats in ("smartrecruiters", "pinpoint"):
            client = FakeClient({})
            code, _, err = self.run_cli(["probe", ats, "acme"], client)
            self.assertEqual(code, 1)
            self.assertEqual(client.requests, [])
            self.assertIn(f"the {ats} reader is switched off (robots.txt", err)
            self.assertIn("nothing was sent", err)


if __name__ == "__main__":
    unittest.main()
