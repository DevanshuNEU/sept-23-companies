"""The SimplifyJobs feed poll (#153, Task 2): what it keeps, what it stores, and that it never fails a tick.

A fresh store and a `FakeClient`: nothing here reaches the network or the live store.
"""
import dataclasses
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from outreach.crawl import tick
from outreach.crawl.config import load_crawl_config
from outreach.crawl.probe import ProbeReport
from outreach.crawl.tick import run_tick
from outreach.net import FetchError, Response
from outreach.pipeline import simplify_feed
from outreach.pipeline.config import PipelineConfig, SimplifyConfig, load_pipeline_config
from outreach.pipeline.simplify_feed import MAX_BYTES, FeedPoll, is_us_location, note_streak, poll
from tests.fakes import FakeClient, json_response, status_response
from tests.storetest import fresh_store

LISTINGS = json.loads((Path(__file__).parent / "fixtures" / "pipeline" / "simplify_listings.json").read_text())
URL = load_pipeline_config().simplify.url
T1 = "2026-11-03T08:00:00Z"
T2 = "2026-11-03T08:30:00Z"
KEPT = {"L-keep-gh", "L-keep-lever", "L-keep-site", "L-keep-multi", "L-keep-dc"}


def rows(conn):
    cur = conn.execute("SELECT * FROM feed_listing WHERE source = 'simplify' ORDER BY listing_id")
    return {r["listing_id"]: dict(r) for r in cur}


def meta(conn, key):
    r = conn.execute("SELECT value FROM pipeline_meta WHERE key = ?", (key,)).fetchone()
    return r[0] if r else None


class PollCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def poll(self, listings=LISTINGS, now=T1, etag='"v1"'):
        client = FakeClient({URL: json_response(listings, etag=etag)})
        result = poll(self.conn, client, now)
        self.assertEqual(client.unrouted, [])
        return result, client


class PollTests(PollCase):
    def test_poll_keeps_us_tech_only(self):
        result, _ = self.poll()
        self.assertEqual(result, FeedPoll(changed=True, seen=5, new=5, closed=0))
        stored = rows(self.conn)
        self.assertEqual(set(stored), KEPT)
        gh = stored["L-keep-gh"]
        self.assertEqual((gh["url"], gh["company_name"], gh["title"], gh["active"]),
                         ("https://job-boards.greenhouse.io/acme/jobs/4012345", "Acme",
                          "Software Engineer, New Grad", 1))
        self.assertEqual((gh["ats"], gh["token"], gh["native_id"]), ("greenhouse", "acme", "4012345"))
        self.assertEqual(gh["posted_hint"], "2026-10-26T07:33:20Z")
        site = stored["L-keep-site"]  # a link that names no known board keeps its row with no board key
        self.assertEqual((site["ats"], site["token"], site["native_id"]), (None, None, None))
        self.assertEqual(meta(self.conn, "simplify_etag"), '"v1"')

    def test_poll_sets_first_seen_once(self):
        self.poll(now=T1)
        self.poll(now=T2, etag='"v2"')
        for r in rows(self.conn).values():
            self.assertEqual((r["first_seen_at"], r["last_seen_at"]), (T1, T2))

    def test_second_changed_poll_counts_no_new_rows(self):
        self.poll(now=T1)
        result, _ = self.poll(now=T2, etag='"v2"')
        self.assertEqual(result, FeedPoll(changed=True, seen=5, new=0, closed=0))

    def test_poll_unchanged_etag_writes_nothing(self):
        first, client1 = self.poll(now=T1)
        self.assertNotIn("if-none-match", {k.lower() for k in client1.calls[0][2]})
        before = rows(self.conn)

        def not_modified(method, url, headers, body):
            self.assertEqual(headers.get("If-None-Match"), '"v1"')
            return status_response(304)

        client = FakeClient({URL: not_modified})
        result = poll(self.conn, client, T2)
        self.assertEqual(result, FeedPoll(changed=False, seen=0, new=0, closed=0))
        self.assertEqual(rows(self.conn), before)
        self.assertEqual(meta(self.conn, "simplify_etag"), '"v1"')

    def test_poll_without_an_etag_still_works_and_stores_none(self):
        client = FakeClient({URL: json_response(LISTINGS)})
        result = poll(self.conn, client, T1)
        self.assertEqual(result.seen, 5)
        self.assertIsNone(meta(self.conn, "simplify_etag"))
        self.poll(now=T2, etag='"v2"')
        client = FakeClient({URL: json_response(LISTINGS)})  # the feed stopped sending one: no stale tag is kept
        poll(self.conn, client, T2)
        self.assertIsNone(meta(self.conn, "simplify_etag"))

    def test_first_poll_rows_are_backfill(self):
        self.poll(now=T1, listings=LISTINGS[:3])
        self.assertEqual({r["backfill"] for r in rows(self.conn).values()}, {1})
        self.poll(now=T2, etag='"v2"')  # the file now also carries two listings it did not have
        stored = rows(self.conn)
        self.assertEqual({k: v["backfill"] for k, v in stored.items()},
                         {"L-keep-gh": 1, "L-keep-lever": 1, "L-keep-site": 1, "L-keep-multi": 0, "L-keep-dc": 0})

    def test_listing_gone_from_file_is_inactive(self):
        self.poll(now=T1)
        result, _ = self.poll(now=T2, etag='"v2"', listings=[x for x in LISTINGS if x["id"] != "L-keep-lever"])
        self.assertEqual((result.seen, result.new, result.closed), (4, 0, 1))
        stored = rows(self.conn)
        self.assertEqual({k: v["active"] for k, v in stored.items()}, {k: int(k != "L-keep-lever") for k in KEPT})
        self.assertEqual(stored["L-keep-lever"]["last_seen_at"], T1)
        # a listing the file still carries but no longer keeps (turned inactive) closes too, and a return reopens it
        gone = [dict(x, active=False) if x["id"] == "L-keep-gh" else x for x in LISTINGS]
        result, _ = self.poll(now=T2, etag='"v3"', listings=gone)
        self.assertEqual(result.closed, 1)  # L-keep-lever returned, L-keep-gh closed
        stored = rows(self.conn)
        self.assertEqual((stored["L-keep-gh"]["active"], stored["L-keep-lever"]["active"]), (0, 1))
        self.assertEqual(stored["L-keep-lever"]["first_seen_at"], T1)

    def test_the_feed_request_has_its_own_60_mb_cap(self):
        self.assertEqual(MAX_BYTES, 60_000_000)
        _, client = self.poll()
        self.assertEqual(client.max_bytes_seen, [60_000_000])
        big = Response(200, "", b"[" + b" " * 100 + b"]", {})
        with mock.patch.object(simplify_feed, "MAX_BYTES", 50), self.assertRaises(FetchError):
            poll(self.conn, FakeClient({URL: big}), T1)

    def test_poll_updates_a_changed_listing(self):
        self.poll(now=T1)
        moved = [dict(x, title="Software Engineer II", url="https://jobs.lever.co/acme/c1b2c3d4-0000-4111-8222-333344445555")
                 if x["id"] == "L-keep-gh" else x for x in LISTINGS]
        self.poll(now=T2, etag='"v2"', listings=moved)
        r = rows(self.conn)["L-keep-gh"]
        self.assertEqual((r["title"], r["ats"], r["token"], r["native_id"]),
                         ("Software Engineer II", "lever", "acme", "c1b2c3d4-0000-4111-8222-333344445555"))

    def test_a_row_with_a_missing_date_keeps_no_hint(self):
        odd = [dict(LISTINGS[0], date_posted=None), dict(LISTINGS[1], date_posted="soon")]
        self.poll(listings=odd)
        self.assertEqual({r["posted_hint"] for r in rows(self.conn).values()}, {None})

    def test_an_error_status_raises_and_writes_nothing(self):
        for response in (status_response(500), status_response(404), Response(200, "", b"<html>", {})):
            with self.subTest(status=response.status):
                with self.assertRaises(Exception):
                    poll(self.conn, FakeClient({URL: response}), T1)
        with self.assertRaises(FetchError):
            poll(self.conn, FakeClient({URL: FetchError("timeout", "slow")}), T1)
        self.assertEqual(rows(self.conn), {})
        self.assertIsNone(meta(self.conn, "simplify_etag"))

    def test_a_file_that_is_not_a_list_or_is_empty_raises_and_closes_nothing(self):
        self.poll()
        for body in ({"listings": []}, []):
            with self.subTest(body=body), self.assertRaises(ValueError):
                poll(self.conn, FakeClient({URL: json_response(body, etag='"v2"')}), T2)
        self.assertEqual({r["active"] for r in rows(self.conn).values()}, {1})
        self.assertEqual(meta(self.conn, "simplify_etag"), '"v1"')


class UsLocationTests(unittest.TestCase):
    def check(self, yes=(), no=()):
        for loc in yes:
            self.assertTrue(is_us_location(loc), loc)
        for loc in no:
            self.assertFalse(is_us_location(loc), loc)

    def test_us_words_and_state_codes(self):
        self.check(yes=["Remote in USA", "San Francisco, CA", "Washington, DC", "New York, United States", "USA",
                        "Remote, US", "Austin, TX ", "Remote - US"],
                   no=["London, UK", "Toronto, ON, Canada", "Remote in Canada", "Berlin, Germany", "Australia",
                       "Cairo, Egypt", "Bus Station", "Sydney, NSW", "Join us", ""])

    def test_full_state_names(self):
        self.check(yes=["California", "Texas", "New Mexico", "Albuquerque, New Mexico", "West Virginia", "Alaska",
                        "District of Columbia", "texas"])

    def test_remote(self):
        self.check(yes=["Remote", "remote", " Remote "], no=["Remote in Canada", "Remote in UK", "Remote in Germany",
                                                              "Remote, Europe"])

    def test_hub_names(self):
        self.check(yes=["San Francisco", "SF", "Bay Area", "San Francisco Bay Area", "New York", "NYC",
                        "Los Angeles", "LA", "Seattle", "Boston", "Austin", "Chicago", "Denver", "Washington DC",
                        "Washington D.C.", "Greater Boston Area", "Remote, SF"],
                   no=["sf", "la", "Bristol, UK", "Lausanne"])

    def test_a_state_code_beside_a_non_us_country_is_not_us(self):
        self.check(no=["Tel Aviv, IL", "Toronto, ON, Canada", "Tel Aviv, IL, Israel", "Bangalore, KA, India",
                       "Dublin, Ireland", "Mexico City, Mexico", "Berlin, DE, Germany", "Austin, Australia",
                       "London, United Kingdom", "Remote in Mexico"])
        self.check(yes=["New York, NY, USA", "Remote in USA", "Albuquerque, NM", "Boston, MA"])


class ConfigTests(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text(text)
            return load_pipeline_config(p)

    GOOD = 'url = "https://example.com/x.json"\ncategories = ["Software"]\nenabled = true\n'

    def test_shipped_config(self):
        cfg = load_pipeline_config()
        self.assertEqual(cfg.simplify.url, "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/"
                                           ".github/scripts/listings.json")
        self.assertEqual(cfg.simplify.categories, ("Software", "Software Engineering", "AI/ML/Data",
                                                   "Data Science, AI & Machine Learning"))
        self.assertTrue(cfg.simplify.enabled)

    def test_refuses_bad_values(self):
        self.assertTrue(self.load("[simplify]\n" + self.GOOD).simplify.enabled)
        for text, error in [
            ("[simplify]\n" + self.GOOD + "extra = 1\n", ValueError),
            ("[simplify]\n" + self.GOOD + "\n[other]\nx = 1\n", ValueError),
            ("[simplify]\n" + self.GOOD.replace("true", '"yes"'), ValueError),
            ("[simplify]\n" + self.GOOD.replace('["Software"]', "[]"), ValueError),
            ("[simplify]\n" + self.GOOD.replace('["Software"]', '"Software"'), ValueError),
            ("[simplify]\n" + self.GOOD.replace("https", "http"), ValueError),
            ("[simplify]\n" + self.GOOD.replace("url", "uri"), (KeyError, ValueError)),
            ("", KeyError),
        ]:
            with self.subTest(text=text), self.assertRaises(error):
                self.load(text)


class TickFeedTests(unittest.TestCase):
    """The tick calls the poll once after the board reads, and a failure never fails the tick."""

    def setUp(self):
        patch = mock.patch.object(tick, "probe_slugs", return_value=ProbeReport(0, 0, 0, 0, 0, 0))
        patch.start()
        self.addCleanup(patch.stop)
        boards = mock.patch("outreach.sources.feed_seed.run_feed_seed", return_value=None)  # #239's business
        boards.start()
        self.addCleanup(boards.stop)
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.cfg = dataclasses.replace(load_crawl_config(), detail_per_read=0)

    def tick(self, route, *, now="2026-11-03T08:00:00Z"):
        client = FakeClient({URL: route})
        report = run_tick(self.conn, client, cfg=self.cfg, now_fn=lambda: now, clock=lambda: 0.0, sleep=lambda s: None)
        self.assertEqual(client.unrouted, [])
        return report, client

    def summary(self, report):
        return json.loads(self.conn.execute("SELECT summary FROM job_run WHERE run_id = ?",
                                            (report.run_id,)).fetchone()[0])

    def test_tick_survives_feed_error(self):
        report, client = self.tick(FetchError("network", "feed host down"))
        self.assertEqual(client.requests, [URL])
        self.assertIsNone(report.feed)
        self.assertIn("feed host down", report.feed_error)
        s = self.summary(report)
        self.assertIn("feed host down", s["feed_error"])
        self.assertIsNone(s["feed"])
        self.assertEqual(meta(self.conn, "simplify_fail_streak"), "1")
        self.tick(status_response(503))
        self.assertEqual(meta(self.conn, "simplify_fail_streak"), "2")
        self.assertEqual(self.conn.execute("SELECT status FROM job_run WHERE job = 'read_boards'").fetchone()[0], "ok")

    def test_tick_records_the_feed_and_resets_the_streak(self):
        self.tick(FetchError("network", "down"))
        report, _ = self.tick(json_response(LISTINGS, etag='"v1"'))
        self.assertEqual(report.feed, {"changed": 1, "seen": 5, "new": 5})
        self.assertEqual(report.feed_error, "")
        s = self.summary(report)
        self.assertEqual((s["feed"], s["feed_error"]), ({"changed": 1, "seen": 5, "new": 5}, ""))
        self.assertEqual(meta(self.conn, "simplify_fail_streak"), "0")

    def test_a_304_counts_as_success(self):
        self.tick(json_response(LISTINGS, etag='"v1"'))
        self.tick(FetchError("network", "down"))
        report, _ = self.tick(status_response(304))
        self.assertEqual(report.feed, {"changed": 0, "seen": 0, "new": 0})
        self.assertEqual(meta(self.conn, "simplify_fail_streak"), "0")

    def test_the_feed_is_skipped_once_the_tick_budget_is_used_up(self):
        ticks = iter(range(0, 10_000_000, 1_000_000))  # every reading of the clock is a million seconds later
        cfg = dataclasses.replace(self.cfg, tick_budget_s=60)
        client = FakeClient({})
        report = run_tick(self.conn, client, cfg=cfg, now_fn=lambda: T1, clock=lambda: next(ticks),
                          sleep=lambda s: None)
        self.assertEqual(client.requests, [])
        self.assertIsNone(report.feed)
        self.assertEqual(report.feed_error, "")
        self.assertIsNone(meta(self.conn, "simplify_fail_streak"))

    def test_the_feed_is_skipped_when_the_pass_stopped(self):
        client = FakeClient({})
        run = types.SimpleNamespace(stopped=True, client=lambda: client)
        result = tick._feed_step(self.conn, run, self.cfg, lambda: T1, lambda: 0.0, 0.0)
        self.assertEqual(result, (None, ""))
        self.assertEqual(client.requests, [])

    def test_the_tick_client_passes_the_cap_on(self):
        _, client = self.tick(json_response(LISTINGS, etag='"v1"'))
        self.assertEqual(client.max_bytes_seen, [60_000_000])

    def test_a_disabled_feed_sends_nothing(self):
        off = PipelineConfig(SimplifyConfig(url=URL, categories=("Software",), enabled=False))
        client = FakeClient({})
        with mock.patch.object(tick, "load_pipeline_config", return_value=off):
            report = run_tick(self.conn, client, cfg=self.cfg, now_fn=lambda: T1, clock=lambda: 0.0,
                              sleep=lambda s: None)
        self.assertEqual(client.requests, [])
        self.assertIsNone(report.feed)
        self.assertEqual(report.feed_error, "")
        self.assertIsNone(meta(self.conn, "simplify_fail_streak"))

    def test_note_streak(self):
        note_streak(self.conn, ok=False)
        note_streak(self.conn, ok=False)
        self.assertEqual(meta(self.conn, "simplify_fail_streak"), "2")
        note_streak(self.conn, ok=True)
        self.assertEqual(meta(self.conn, "simplify_fail_streak"), "0")


if __name__ == "__main__":
    unittest.main()
