"""The find_boards job (#31 Tasks 14 and 15): alias boards, the stapply lookup, tiers and the report, on the LCA and HN
fixtures after run_discover."""
import dataclasses
import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from outreach.crawl.base import BOARD_ATS
from outreach.review.config import ReviewConfig
from outreach.review.items import resolve_item
from outreach.review.play import ny_date
from outreach.review.scope import clear_out_of_scope
from outreach.sources.boards import BoardRef, add_board
from outreach.sources.byhand import add_by_hand
from outreach.sources.careers import CareersChecker
from outreach.sources.hn import HnSource
from outreach.sources.jobs import FindBoardsReport, run_discover, run_find_boards
from outreach.sources.lca import LcaSource
from outreach.sources.stapply import StapplyDirectory
from outreach.sources.tiers import promote
from outreach.store import add_alias, append_event, utcnow
from tests import review_seed
from tests.fakes import FakeClient, status_response
from tests.storetest import planned_adapters, fresh_store, seed_company, table_marks
from tests.test_hn import fixture_client
from tests.test_lca_source import FILES

STAPPLY = Path(__file__).parent / "fixtures" / "stapply"
CAREERS = Path(__file__).parent / "fixtures" / "careers"


def count(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


class FindBoardsBase(unittest.TestCase):
    def setUp(self):
        planned_adapters(self)
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        run_discover(self.conn, [LcaSource(FILES), HnSource(fixture_client())])
        self.directory = StapplyDirectory.load(STAPPLY)

    def company(self, name):
        return self.conn.execute("SELECT company_id FROM company WHERE name = ?", (name,)).fetchone()[0]

    def boards(self):
        return {(r["ats"], r["token"]): dict(r) for r in self.conn.execute(
            "SELECT b.*, c.name AS company FROM board b JOIN company c USING (company_id)")}

    def run_job(self, **kw):
        kw.setdefault("directory", self.directory)
        return run_find_boards(self.conn, **kw)


class StapplyStepTest(FindBoardsBase):
    def test_nimbus_and_orchard_get_boards(self):
        run_id, report = self.run_job()
        boards = self.boards()
        nimbus, orchard = boards[("greenhouse", "nimbusrobotics")], boards[("ashby", "orchard")]
        self.assertEqual((nimbus["company"], orchard["company"]), ("Nimbus Robotics, Inc.", "Orchard"))
        for b in (nimbus, orchard):  # due now for #21
            self.assertEqual((b["active"], b["last_read"], b["next_read_at"], b["found_by"]),
                             (1, None, None, "stapply_name"))
        for alias in ("greenhouse:nimbusrobotics", "ashby:orchard"):
            row = self.conn.execute("SELECT company_id, source FROM company_alias WHERE alias_key = ?",
                                    (alias,)).fetchone()
            self.assertEqual(row["source"], "ats", alias)
        links = {(r["company_id"], r["source_ref"]) for r in self.conn.execute(
            "SELECT * FROM company_source WHERE source_name = 'stapply'")}
        self.assertEqual(links, {(self.company("Nimbus Robotics, Inc."), "greenhouse:nimbusrobotics"),
                                 (self.company("Orchard"), "ashby:orchard"),
                                 (self.company("Mesh Systems Inc"), "greenhouse:meshsystems")})
        event = self.conn.execute("SELECT * FROM event WHERE entity = 'board' AND entity_id = ?",
                                  (nimbus["board_id"],)).fetchone()
        self.assertEqual((event["kind"], event["actor"], event["company_id"]),
                         ("found", "crawler", self.company("Nimbus Robotics, Inc.")))
        self.assertEqual(json.loads(event["detail"]), {"via": "stapply", "matched_on": "name"})
        source = self.conn.execute("SELECT version, last_run_id FROM source WHERE source_name = 'stapply'").fetchone()
        self.assertEqual(tuple(source), (self.directory.version, run_id))
        self.assertEqual((report.from_stapply, report.from_alias, report.version),
                         (3, 1, self.directory.version))  # 3 by stapply, Tidepool's lever alias
        self.assertEqual(report.conflicts, 1)  # the HN Mesh Systems: the LCA Mesh Systems holds the board
        job = self.conn.execute("SELECT job, status, error, summary FROM job_run WHERE run_id = ?",
                                (run_id,)).fetchone()
        self.assertEqual((job["job"], job["status"], job["error"]), ("find_boards", "ok", ""))
        self.assertEqual(json.loads(job["summary"])["from_stapply"], 3)

    def test_a_host_match_is_found_by_import(self):
        hand = add_by_hand(self.conn, name="Wholly Different Name", reason="a friend works there",
                           domain="plumehealth.io").company_id
        self.run_job()
        board = self.boards()[("lever", "plumehealth")]
        self.assertEqual((board["company_id"], board["found_by"]), (hand, "import"))
        event = self.conn.execute("SELECT detail FROM event WHERE entity = 'board' AND entity_id = ?",
                                  (board["board_id"],)).fetchone()
        self.assertEqual(json.loads(event["detail"]), {"via": "stapply", "matched_on": "host"})

    def test_a_slug_match_is_found_by_stapply_name(self):
        seed_company(self.conn, "Granite Peak Inc")
        self.run_job()
        self.assertEqual(self.boards()[("lever", "granite-peak")]["found_by"], "stapply_name")

    def test_the_legal_name_and_dba_aliases_are_looked_up_too(self):
        cid = seed_company(self.conn, "Some Brand")
        add_alias(self.conn, cid, "GP Labs LLC", "legal_name")
        self.run_job()
        self.assertEqual(self.boards()[("lever", "granite-peak")]["company_id"], cid)

    def test_tier_3_company_is_skipped(self):
        _, report = self.run_job()
        self.assertNotIn(("greenhouse", "lumenapps"), self.boards())  # a Greenhouse row exists in the fixture
        self.assertEqual(report.skipped_tier3, 4)  # Bridgeway, Depco, Lumen Apps, Quill
        self.assertEqual(count(self.conn, "SELECT count(*) FROM board WHERE company_id = ?",
                               self.company("Lumen Apps Inc")), 0)

    def test_promoted_tier_3_company_gets_its_board(self):
        promote(self.conn, self.company("Lumen Apps Inc"), "devanshu")
        _, report = self.run_job()
        self.assertEqual(self.boards()[("greenhouse", "lumenapps")]["company"], "Lumen Apps Inc")
        self.assertEqual(report.skipped_tier3, 3)

    def test_alias_becomes_a_board(self):
        hand = add_by_hand(self.conn, name="Zephyr Labs", reason="a friend works there", ats="lever:zephyr")
        _, report = self.run_job()
        board = self.boards()[("lever", "zephyr")]
        self.assertEqual((board["company_id"], board["found_by"]), (hand.company_id, "hand"))
        tidepool = self.boards()[("lever", "tidepoolapps")]  # an HN company's alias: not by hand
        self.assertEqual((tidepool["company"], tidepool["found_by"]), ("Tidepool Apps", "import"))
        event = self.conn.execute("SELECT detail FROM event WHERE entity = 'board' AND entity_id = ?",
                                  (board["board_id"],)).fetchone()
        self.assertEqual(json.loads(event["detail"])["via"], "alias")
        self.assertEqual(report.from_alias, 2)

    def test_the_stapply_lookup_runs_only_when_the_alias_gave_no_board(self):
        cid = seed_company(self.conn, "Granite Peak Inc")
        add_alias(self.conn, cid, "lever:zephyr", "ats")
        _, report = self.run_job()
        self.assertEqual(self.boards()[("lever", "zephyr")]["company_id"], cid)
        self.assertNotIn(("lever", "granite-peak"), self.boards())  # the name match would have been a second board

    def test_a_company_with_a_watched_page_but_no_board_still_gets_the_stapply_lookup(self):
        granite = add_by_hand(self.conn, name="Granite Peak Inc", reason="a friend", domain="granitepeak.example")
        orchid = add_by_hand(self.conn, name="Orchid Works", reason="a friend", domain="orchidworks.example")
        for add in (granite, orchid):
            self.conn.execute("INSERT INTO watched_page(company_id, url) VALUES (?, 'https://example.test/jobs')",
                              (add.company_id,))
        client = FakeClient(careers_routes())
        _, report = self.run_job(careers=CareersChecker(client))
        self.assertEqual(self.boards()[("lever", "granite-peak")]["company_id"], granite.company_id)
        self.assertEqual([u for u in client.requests if "example" in u], [])  # never the careers page again
        self.assertEqual(client.unrouted, [])

    def test_bad_alias_token_is_counted_not_fatal(self):
        cid = seed_company(self.conn, "Broken Alias Co")
        add_alias(self.conn, cid, "greenhouse:Bad%Token", "ats")
        run_id, report = self.run_job()
        self.assertEqual(report.bad_aliases, [f"{cid}: greenhouse:Bad%Token"])
        self.assertIn(("greenhouse", "nimbusrobotics"), self.boards())  # the run went on
        job = self.conn.execute("SELECT status, error FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
        self.assertEqual(job["status"], "partial")
        self.assertIn("greenhouse:Bad%Token", job["error"])

    def test_board_on_another_company_raises_one_item(self):
        self.run_job()
        copy = seed_company(self.conn, "Nimbus Robotics")  # the same name, another company
        owner = self.company("Nimbus Robotics, Inc.")
        _, report = self.run_job()
        self.assertEqual(report.conflicts, 2)  # the copy, and the HN Mesh Systems whose board the LCA one holds
        self.assertEqual(count(self.conn, "SELECT count(*) FROM board WHERE company_id = ?", copy), 0)
        items = [dict(r) for r in self.conn.execute(
            "SELECT * FROM review_item WHERE kind = 'merge_candidate' AND company_id = ?", (owner,))]
        self.assertEqual(len(items), 1)
        self.assertIn("same_board", items[0]["reason"])
        _, again = self.run_job()
        self.assertEqual(again.conflicts, 2)  # still seen, still no second item
        self.assertEqual(count(self.conn, "SELECT count(*) FROM review_item WHERE kind = 'merge_candidate' AND "
                                          "company_id = ?", owner), 1)

    def test_rerun_writes_nothing_new(self):
        self.run_job()
        before = table_marks(self.conn)
        _, report = self.run_job()
        self.assertEqual(table_marks(self.conn), before)
        self.assertEqual((report.from_alias, report.from_stapply), (0, 0))

    def test_companies_with_a_board_are_not_considered_again(self):
        _, first = self.run_job()
        _, second = self.run_job()
        self.assertEqual(first.considered, 14)  # 18 companies, 4 of tier 3
        self.assertEqual(second.considered, first.considered - 4)  # Tidepool, Nimbus, Orchard, LCA Mesh have boards
        self.assertEqual(second.skipped_tier3, 4)

    def test_no_directory_still_makes_alias_boards_and_says_the_lookup_did_not_run(self):
        run_id, report = self.run_job(directory=None)
        self.assertIn(("lever", "tidepoolapps"), self.boards())
        self.assertNotIn(("greenhouse", "nimbusrobotics"), self.boards())
        job = self.conn.execute("SELECT status, error FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
        self.assertEqual(job["status"], "partial")
        self.assertIn("no stapply directory", job["error"])
        self.assertIsNone(self.conn.execute("SELECT 1 FROM source WHERE source_name = 'stapply'").fetchone())
        self.assertIsInstance(report, FindBoardsReport)

    def test_removed_and_closed_companies_are_not_considered(self):
        for status, extra in (("removed", ", remove_reason = 'dupe'"), ("closed", ", close_reason = 'not_now'")):
            cid = seed_company(self.conn, "Nimbus Robotics")
            self.conn.execute(f"UPDATE company SET status = '{status}'{extra} WHERE company_id = ?", (cid,))
        self.run_job()
        self.assertEqual(self.boards()[("greenhouse", "nimbusrobotics")]["company"], "Nimbus Robotics, Inc.")
        self.assertEqual(count(self.conn, "SELECT count(*) FROM review_item WHERE kind = 'merge_candidate'"), 1)


def careers_routes(**overrides):
    """What the fixture companies' sites answer. Veltrix, Solo Dev and PrairieLearn are never asked."""
    routes = {
        "https://clearpath.ai/careers": status_response(404),
        "https://clearpath.ai/": CAREERS / "home_links_join.html",
        "https://clearpath.ai/join-us": CAREERS / "clearpath_join.html",
        "https://riverside.edu/careers": CAREERS / "riverside_careers.html",
        "https://meshsystems.dev/careers": CAREERS / "plain_page.html",
        "https://sundial.health/careers": status_response(404),
        "https://sundial.health/": status_response(404),
        "https://quilldata.io/careers": status_response(404),
        "https://quilldata.io/": CAREERS / "home_no_link.html",
        "https://greenfield.ai/careers": status_response(503),
    }
    routes.update(overrides)
    return routes


class CareersStepTest(FindBoardsBase):
    def setUp(self):
        super().setUp()
        promote(self.conn, self.company("Quill Data Services Inc"), "devanshu")  # the LCA gives Quill tier 3

    def run_careers(self, routes=None, **kw):
        self.client = FakeClient(careers_routes() if routes is None else routes)
        out = self.run_job(careers=CareersChecker(self.client), **kw)
        self.assertEqual(self.client.unrouted, [])
        return out

    def events(self, kind, company=None):
        sql, args = "SELECT * FROM event WHERE kind = ?", [kind]
        if company is not None:
            sql, args = sql + " AND company_id = ?", [kind, self.company(company)]
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY event_id", args)]

    def watched(self):
        return {r["name"]: dict(r) for r in self.conn.execute(
            "SELECT w.*, c.name FROM watched_page w JOIN company c USING (company_id)")}

    def test_careers_step_results(self):
        _, report = self.run_careers()
        boards = self.boards()
        clearpath = boards[("ashby", "clearpath")]
        riverside = boards[("workday", "riverside.wd5/External")]  # the host and tenant lower, the site as written
        self.assertEqual((clearpath["company"], clearpath["found_by"]), ("Clearpath Inc", "careers_page"))
        self.assertEqual((riverside["company"], riverside["found_by"]), ("Riverside University", "careers_page"))
        for b in (clearpath, riverside):  # due now for #21
            self.assertEqual((b["active"], b["last_read"], b["next_read_at"]), (1, None, None))
        event = self.conn.execute("SELECT detail FROM event WHERE entity = 'board' AND entity_id = ?",
                                  (clearpath["board_id"],)).fetchone()
        self.assertEqual(json.loads(event["detail"]),
                         {"via": "careers", "matched_on": "https://clearpath.ai/join-us"})
        watched = self.watched()  # the HN Mesh Systems: the LCA company holds the stapply board
        self.assertEqual(list(watched), ["Mesh Systems"])
        page = watched["Mesh Systems"]
        self.assertEqual((page["url"], page["fingerprint"], page["last_fetch_at"], page["next_fetch_at"],
                          page["error_streak"]), ("https://meshsystems.dev/careers", "", None, None, 0))
        for name in ("Sundial Health Inc", "Quill Data Services Inc"):
            (marker,) = self.events("careers_checked", name)
            self.assertEqual(json.loads(marker["detail"]), {"result": "none"})
            self.assertEqual(marker["idempotency_key"], f"careers-checked:{self.company(name)}:{marker['at'][:10]}")
            self.assertEqual(marker["actor"], "crawler")
        greenfield = self.company("Greenfield AI Inc")
        self.assertEqual(count(self.conn, "SELECT count(*) FROM board WHERE company_id = ?", greenfield), 0)
        self.assertEqual(self.events("careers_checked", "Greenfield AI Inc"), [])
        self.assertEqual((report.from_careers, report.careers_none, report.careers_errors, report.watched_pages),
                         (2, 2, 1, 1))
        self.assertEqual(report.careers_checked, 6)  # Clearpath, Greenfield, Riverside, Quill, Sundial, HN Mesh
        status = self.conn.execute("SELECT status, error FROM job_run WHERE job = 'find_boards'").fetchone()
        self.assertEqual(status["status"], "partial")  # the one error is said, not hidden
        self.assertIn("1 careers checks errored", status["error"])

    def test_careers_boards_write_company_source_with_a_registered_source(self):
        run_id, _ = self.run_careers()
        source = self.conn.execute("SELECT version, last_run_id FROM source WHERE source_name = 'careers'").fetchone()
        self.assertEqual(tuple(source), ("careers-1", run_id))
        links = {(r["company_id"], r["source_ref"]) for r in self.conn.execute(
            "SELECT * FROM company_source WHERE source_name = 'careers'")}
        self.assertEqual(links, {(self.company("Clearpath Inc"), "ashby:clearpath"),
                                 (self.company("Riverside University"), "workday:riverside.wd5/External")})

    def test_without_a_checker_there_is_no_careers_step_and_no_source(self):
        self.run_job()
        self.assertEqual(self.watched(), {})
        self.assertEqual(count(self.conn, "SELECT count(*) FROM review_item WHERE kind = 'domain_missing'"), 0)
        self.assertEqual(self.events("careers_skipped"), [])
        self.assertIsNone(self.conn.execute("SELECT 1 FROM source WHERE source_name = 'careers'").fetchone())

    def test_two_boards_on_a_page_add_none_and_the_page_is_watched(self):
        _, report = self.run_careers(careers_routes(**{"https://meshsystems.dev/careers": CAREERS / "two_boards.html"}))
        self.assertEqual(report.ambiguous_boards, 1)
        self.assertEqual(self.watched()["Mesh Systems"]["url"], "https://meshsystems.dev/careers")
        self.assertNotIn(("greenhouse", "twoco"), self.boards())
        self.assertNotIn(("lever", "twoco"), self.boards())

    def test_a_board_on_the_page_that_another_company_holds_is_watched_not_moved(self):
        page = b'<a href="https://job-boards.greenhouse.io/meshsystems">Jobs</a>'
        _, report = self.run_careers(careers_routes(**{"https://meshsystems.dev/careers": page}))
        self.assertEqual(self.boards()[("greenhouse", "meshsystems")]["company"], "Mesh Systems Inc")
        self.assertEqual(self.watched()["Mesh Systems"]["url"], "https://meshsystems.dev/careers")
        self.assertEqual((report.from_careers, report.ambiguous_boards), (2, 0))  # Clearpath and Riverside only

    def test_company_with_only_unsupported_boards_gets_a_watched_page(self):
        add_by_hand(self.conn, name="Gemco Labs", reason="a friend works there", domain="gemco.example",
                    ats="gem:gemco")
        routes = careers_routes(**{"https://gemco.example/careers": CAREERS / "plain_page.html"})
        _, report = self.run_careers(routes)  # the pinned set: Gem has no adapter in the plan
        watched = self.watched()
        self.assertEqual(watched["Gemco Labs"]["url"], "https://gemco.example/careers")  # Gem has no adapter
        self.assertEqual(sorted(watched), ["Gemco Labs", "Mesh Systems"])  # a Greenhouse board gets none
        self.assertEqual((report.adapter_unknown, report.unreadable_boards), (0, 0))

    def test_a_company_whose_only_board_is_on_a_switched_off_ats_gets_a_watched_page(self):
        # #46: the crawler's [ats_off] list is the one source. A board it never reads is no reader for the company, so
        # the company is watched like one with no readable board, never silently dropped.
        add_by_hand(self.conn, name="Smartco Labs", reason="a friend works there", domain="smartco.example",
                    ats="smartrecruiters:smartco")
        add_by_hand(self.conn, name="Pinco Labs", reason="a friend works there", domain="pinco.example",
                    ats="pinpoint:pinco")
        routes = careers_routes(**{"https://smartco.example/careers": CAREERS / "plain_page.html",
                                   "https://pinco.example/careers": CAREERS / "plain_page.html"})
        self.run_careers(routes)
        watched = self.watched()
        self.assertEqual(watched["Smartco Labs"]["url"], "https://smartco.example/careers")
        self.assertEqual(watched["Pinco Labs"]["url"], "https://pinco.example/careers")

    def test_a_reader_turned_back_on_reads_the_company_again_and_no_page_is_added(self):
        from outreach.crawl import config as crawl_config
        on = dataclasses.replace(crawl_config.load_crawl_config(), ats_off={})
        add_by_hand(self.conn, name="Smartco Labs", reason="a friend works there", domain="smartco.example",
                    ats="smartrecruiters:smartco")
        with mock.patch.object(crawl_config, "load_crawl_config", return_value=on):
            self.run_careers()  # no route for smartco.example: a request to it would fail the test
        self.assertNotIn("Smartco Labs", self.watched())

    def test_the_real_adapter_set_decides_which_boards_are_unread(self):
        # No stand-in: whatever `outreach.crawl.ats.supported_ats()` says today. Rule 9 of #31 follows it.
        sys.modules.pop("outreach.crawl.ats")  # drop the stand-in setUp pinned; the real package imports fresh
        supported = importlib.import_module("outreach.crawl.ats").supported_ats()
        slug_atses = BOARD_ATS - {"workday", "oracle_orc", "icims"}
        unread = sorted(slug_atses - supported)[0]  # an ATS with no adapter yet
        self.assertIn("greenhouse", supported)
        add_by_hand(self.conn, name="Unread Co", reason="a friend works there", domain="unread.example",
                    ats=f"{unread}:unreadco")
        add_by_hand(self.conn, name="Read Co", reason="a friend works there", domain="read.example",
                    ats="greenhouse:readco")
        # Every fixture company whose board has no adapter is checked too; their sites answer 404 here.
        routes = careers_routes(**{"https://unread.example/careers": CAREERS / "plain_page.html"})
        for host in ("orchard.io", "tidepoolapps.com"):
            routes.setdefault(f"https://{host}/careers", status_response(404))
            routes.setdefault(f"https://{host}/", status_response(404))
        _, report = self.run_careers(routes)
        watched = self.watched()
        self.assertEqual(watched["Unread Co"]["url"], "https://unread.example/careers")
        self.assertNotIn("Read Co", watched)  # its Greenhouse board has an adapter: no page, no request
        self.assertNotIn("https://read.example/careers", self.client.requests)
        self.assertEqual(report.adapter_unknown, 0)

    def test_adapter_module_absent_skips_the_unread_board_step_and_counts_it(self):
        add_by_hand(self.conn, name="Gemco Labs", reason="a friend works there", domain="gemco.example",
                    ats="gem:gemco")
        with mock.patch.dict(sys.modules, {"outreach.crawl.ats": None}):  # an import of it fails
            _, report = self.run_careers()  # no route for gemco.example: a request to it would fail the test
        self.assertNotIn("Gemco Labs", self.watched())
        # Gemco, Nimbus, Orchard, LCA Mesh, Tidepool and the two the careers step gave a board
        self.assertEqual((report.adapter_unknown, report.unreadable_boards), (7, 0))

    def test_unread_boards_fall_back_to_the_icims_host_then_to_a_count(self):
        add_by_hand(self.conn, name="Icims Works", reason="a friend works there",
                    ats="icims:careers-icimsworks.icims.com")
        add_by_hand(self.conn, name="Corner Stone Works", reason="a friend works there", ats="cornerstone:cornerworks")
        _, report = self.run_careers()
        self.assertEqual(self.watched()["Icims Works"]["url"], "https://careers-icimsworks.icims.com")
        self.assertNotIn("Corner Stone Works", self.watched())
        self.assertEqual(report.unreadable_boards, 1)

    def test_a_careers_page_is_fetched_once_for_both_steps(self):
        add_by_hand(self.conn, name="Gemco Labs", reason="a friend works there", domain="gemco.example",
                    ats="gem:gemco")
        adapters = types.SimpleNamespace(supported_ats=lambda: {"greenhouse", "lever", "ashby"})  # not workday
        routes = careers_routes(**{"https://gemco.example/careers": CAREERS / "plain_page.html"})
        with mock.patch.dict(sys.modules, {"outreach.crawl.ats": adapters}):
            self.run_careers(routes)
        # Riverside got a Workday board from its careers page, which has no adapter here: its page is watched
        self.assertEqual(self.client.requests.count("https://riverside.edu/careers"), 1)
        self.assertEqual(self.watched()["Riverside University"]["url"], "https://riverside.edu/careers")

    def test_error_leaves_no_marker(self):
        routes = careers_routes(**{"https://greenfield.ai/careers": [status_response(503), CAREERS / "plain_page.html"]})
        self.client = FakeClient(routes)
        self.run_job(careers=CareersChecker(self.client))
        self.assertEqual(self.events("careers_checked", "Greenfield AI Inc"), [])
        self.assertNotIn("Greenfield AI Inc", self.watched())
        self.run_job(careers=CareersChecker(self.client))
        self.assertEqual(self.client.requests.count("https://greenfield.ai/careers"), 2)  # the next run asked again
        self.assertEqual(self.events("careers_checked", "Greenfield AI Inc"), [])
        self.assertEqual(self.watched()["Greenfield AI Inc"]["url"], "https://greenfield.ai/careers")
        self.assertEqual(len(self.events("careers_checked", "Sundial Health Inc")), 1)  # a definite 404 is marked

    def test_a_timeout_or_429_leaves_no_marker_either(self):
        from outreach.net import FetchError
        routes = careers_routes(**{"https://sundial.health/careers": FetchError("timeout", "slow"),
                                   "https://quilldata.io/careers": status_response(429)})
        _, report = self.run_careers(routes)
        self.assertEqual(report.careers_errors, 3)  # Greenfield, Sundial and Quill
        self.assertEqual(self.events("careers_checked"), [])

    def test_none_marker_blocks_refetch_for_the_month(self):
        self.run_careers()
        _, report = self.run_careers()
        self.assertNotIn("https://sundial.health/careers", self.client.requests)
        self.assertNotIn("https://quilldata.io/careers", self.client.requests)
        self.assertEqual(report.careers_none, 0)
        self.assertEqual(len(self.events("careers_checked", "Sundial Health Inc")), 1)

    def test_a_marker_older_than_30_days_is_checked_again(self):
        sundial = self.company("Sundial Health Inc")
        quill = self.company("Quill Data Services Inc")
        for company, at in ((sundial, "2026-09-01T00:00:00Z"), (quill, "2026-10-20T00:00:00Z")):
            append_event(self.conn, entity="company", kind="careers_checked", actor="crawler", entity_id=company,
                         company_id=company, detail={"result": "none"}, at=at, idempotency_key=f"seed:{company}")
        self.run_careers()
        self.assertIn("https://sundial.health/careers", self.client.requests)  # a stale marker
        self.assertNotIn("https://quilldata.io/careers", self.client.requests)  # a marker from this month

    def test_shared_domain_company_is_not_fetched(self):
        _, report = self.run_careers()
        self.assertFalse([u for u in self.client.requests if "veltrix.com" in u])
        events = self.events("careers_skipped")
        self.assertEqual(len(events), 3)  # Veltrix Pharma, Veltrix Diagnostics and the HN Veltrix Bio
        self.assertEqual({json.loads(e["detail"])["domain"] for e in events}, {"veltrix.com"})
        self.assertEqual(report.careers_skipped, 3)

    def ranked(self, *names):
        """The ranked list holds these companies, in this order (every live company when none is named)."""
        if names:
            ids = [self.company(n) for n in names]
        else:
            ids = [r[0] for r in self.conn.execute("SELECT company_id FROM company WHERE merged_into IS NULL "
                                                   "ORDER BY company_id")]
        rows = [types.SimpleNamespace(company_id=c) for c in ids]
        return mock.patch("outreach.ranking.scores.ranked_list",
                          side_effect=lambda conn, *a, limit=None, **kw: rows[:limit])

    def gaps(self):
        return {self.conn.execute("SELECT name FROM company WHERE company_id = ?", (r["company_id"],)).fetchone()[0]:
                r["state"] for r in self.conn.execute(
                    "SELECT company_id, state FROM review_item WHERE kind = 'domain_missing' AND raised_by = 'crawler'")}

    def test_free_mail_tier_1_company_raises_domain_missing(self):
        with self.ranked():
            _, report = self.run_careers()
        rows = {self.conn.execute("SELECT name FROM company WHERE company_id = ?", (r["company_id"],)).fetchone()[0]:
                dict(r) for r in self.conn.execute("SELECT * FROM review_item WHERE kind = 'domain_missing'")}
        # a gmail contact, a github link, and the three companies whose only domain is shared (next test)
        self.assertEqual(sorted(rows), ["PrairieLearn", "Solo Dev Studio Inc", "Veltrix Bio", "Veltrix Diagnostics Inc",
                                        "Veltrix Pharma Inc"])
        for item in rows.values():
            self.assertEqual((item["raised_by"], item["evidence_key"], item["state"]),
                             ("crawler", "no-usable-domain", "open"))
        self.assertEqual(report.domain_missing, 5)
        with self.ranked():
            _, again = self.run_careers()
        self.assertEqual(again.domain_missing, 0)  # the same item is not raised twice
        self.assertEqual(count(self.conn, "SELECT count(*) FROM review_item WHERE kind = 'domain_missing'"), 5)

    def test_a_tier_1_company_with_only_a_shared_domain_raises_domain_missing_too(self):
        ids = {r["name"]: r["company_id"] for r in self.conn.execute("SELECT * FROM company WHERE name LIKE 'Veltrix%'")}
        self.assertEqual(self.conn.execute("UPDATE work_tier SET tier = 2 WHERE company_id = ?",
                                           (ids["Veltrix Diagnostics Inc"],)).rowcount, 1)
        with self.ranked():
            _, report = self.run_careers()
        rows = {r["name"]: dict(r) for r in self.conn.execute(
            "SELECT c.name, i.reason, i.evidence_key, i.raised_by, i.state FROM review_item i "
            "JOIN company c USING (company_id) WHERE i.kind = 'domain_missing' AND c.name LIKE 'Veltrix%'")}
        self.assertEqual(sorted(rows), ["Veltrix Bio", "Veltrix Pharma Inc"])  # tier 1 only; the tier 2 one is quiet
        for row in rows.values():
            self.assertIn("veltrix.com", row["reason"])
            self.assertEqual((row["evidence_key"], row["raised_by"], row["state"]), ("no-usable-domain", "crawler", "open"))
        self.assertEqual(len(self.events("careers_skipped")), 3)  # the event stays for all three
        self.assertEqual(report.careers_skipped, 3)
        with self.ranked():
            _, again = self.run_careers()
        self.assertEqual(again.domain_missing, 0)  # the same item is not raised twice

    def test_a_lower_tier_company_with_no_domain_raises_no_item(self):
        cid = self.company("Solo Dev Studio Inc")
        self.assertEqual(self.conn.execute("UPDATE work_tier SET tier = 2 WHERE company_id = ?", (cid,)).rowcount, 1)
        with self.ranked():
            self.run_careers()
        self.assertEqual(count(self.conn, "SELECT count(*) FROM review_item WHERE kind = 'domain_missing' AND "
                                          "company_id = ?", cid), 0)

    # The company-first ruling (#9): a crawler data gap asks only for a company in play or near the top.

    def test_a_company_neither_in_play_nor_in_the_top_raises_no_domain_item(self):
        with self.ranked("Solo Dev Studio Inc"):
            _, report = self.run_careers()
        self.assertEqual(self.gaps(), {"Solo Dev Studio Inc": "open"})
        self.assertEqual((report.domain_missing, report.domain_missing_quiet), (1, 4))
        self.assertEqual(len(self.events("careers_skipped")), 3)  # the shared-domain events stay for all three
        _, none_ranked = self.run_careers()  # no ranked list and nobody in play: nothing new asks
        self.assertEqual((none_ranked.domain_missing, none_ranked.domain_missing_quiet), (0, 5))

    def test_a_company_in_play_raises_its_domain_item_outside_the_top(self):
        cid = self.company("PrairieLearn")
        review_seed.seed_plan_row(self.conn, cid, ny_date(utcnow()), "picked")
        _, report = self.run_careers()
        self.assertEqual(self.gaps(), {"PrairieLearn": "open"})
        self.assertEqual(report.domain_missing, 1)

    def test_at_most_the_cap_of_new_domain_items_a_run_in_play_first_then_by_rank(self):
        cfg = ReviewConfig(150, 4, 14, crawler_scope_top=150, crawler_cap_per_run=2)
        review_seed.seed_plan_row(self.conn, self.company("Veltrix Bio"), ny_date(utcnow()), "picked")
        order = ("Veltrix Pharma Inc", "Solo Dev Studio Inc", "PrairieLearn", "Veltrix Diagnostics Inc")
        with self.ranked(*order):
            _, report = self.run_careers(review_cfg=cfg)
        self.assertEqual(sorted(self.gaps()), ["Veltrix Bio", "Veltrix Pharma Inc"])
        self.assertEqual((report.domain_missing, report.domain_missing_quiet), (2, 3))
        with self.ranked(*order):
            _, report = self.run_careers(review_cfg=cfg)  # the next run asks the next two
        self.assertEqual(sorted(self.gaps()), ["PrairieLearn", "Solo Dev Studio Inc", "Veltrix Bio",
                                               "Veltrix Pharma Inc"])  # the open two take no place in the cap
        self.assertEqual(report.domain_missing, 2)

    def test_a_cleared_out_of_scope_item_comes_back_when_the_company_does(self):
        with self.ranked("PrairieLearn"):
            self.run_careers()
        item = self.conn.execute("SELECT review_id FROM review_item WHERE kind = 'domain_missing'").fetchone()[0]
        with self.ranked("Orchard"):
            self.assertEqual(clear_out_of_scope(self.conn, now=utcnow()), 1)
        self.assertEqual(self.gaps(), {"PrairieLearn": "resolved"})
        with self.ranked("PrairieLearn"):
            _, report = self.run_careers()
        self.assertEqual(self.gaps(), {"PrairieLearn": "open"})
        self.assertEqual(report.domain_missing, 1)
        self.assertEqual(self.conn.execute("SELECT review_id FROM review_item WHERE kind = 'domain_missing'"
                                           ).fetchone()[0], item)

    def test_a_domain_item_he_settled_is_never_raised_again(self):
        with self.ranked("PrairieLearn"):
            self.run_careers()
        item = self.conn.execute("SELECT review_id FROM review_item WHERE kind = 'domain_missing'").fetchone()[0]
        resolve_item(self.conn, item, resolution="marked_ok", note="Marked ok by you", revision=1)
        with self.ranked("PrairieLearn"):
            _, report = self.run_careers()
        self.assertEqual((self.gaps(), report.domain_missing), ({"PrairieLearn": "resolved"}, 0))

    def flagged(self, name=None):
        sql, args = "SELECT i.*, c.name FROM review_item i JOIN company c USING (company_id) WHERE i.kind = 'flagged'", []
        if name is not None:
            sql, args = sql + " AND c.name = ?", [name]
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY i.review_id", args)]

    def demote_quill(self):  # the LCA's tier 3, no longer promoted: the one tier 1 "none" company is Sundial
        self.conn.execute("UPDATE work_tier SET promoted = 0, tier = 2 WHERE company_id = ?",
                          (self.company("Quill Data Services Inc"),))

    def test_a_tier_1_company_with_no_careers_page_raises_one_item_and_a_rerun_adds_none(self):
        self.demote_quill()
        _, report = self.run_careers()
        (item,) = self.flagged()
        self.assertEqual((item["name"], item["raised_by"], item["evidence_key"], item["state"]),
                         ("Sundial Health Inc", "crawler", "no-board-no-careers", "open"))
        self.assertEqual(item["reason"], "No job board or careers page found at sundial.health. "
                                         "Add a board or a careers link by hand.")
        self.assertEqual(report.no_careers_flagged, 1)
        with mock.patch("outreach.sources.jobs.CAREERS_RECHECK_DAYS", 0):  # the marker has lapsed: asked again
            _, again = self.run_careers()
        self.assertIn("https://sundial.health/careers", self.client.requests)
        self.assertEqual(again.no_careers_flagged, 0)  # the same item is not raised twice
        self.assertEqual(len(self.flagged()), 1)
        _, third = self.run_careers()  # the marker holds: nothing is fetched, nothing is raised
        self.assertNotIn("https://sundial.health/careers", self.client.requests)
        self.assertEqual((third.no_careers_flagged, len(self.flagged())), (0, 1))

    def test_a_tier_2_company_with_no_careers_page_raises_no_item(self):
        self.demote_quill()
        self.conn.execute("UPDATE work_tier SET tier = 2 WHERE company_id = ?", (self.company("Sundial Health Inc"),))
        _, report = self.run_careers()
        self.assertEqual(self.flagged(), [])
        self.assertEqual(report.no_careers_flagged, 0)
        self.assertEqual(len(self.events("careers_checked", "Sundial Health Inc")), 1)  # today's marker, as before
        self.assertEqual(len(self.events("careers_checked", "Quill Data Services Inc")), 1)

    def test_a_careers_error_raises_no_item(self):
        routes = careers_routes(**{"https://sundial.health/careers": status_response(503)})
        self.demote_quill()
        _, report = self.run_careers(routes)
        self.assertEqual(self.flagged(), [])  # Greenfield (503) and Sundial (503) both errored
        self.assertEqual(report.no_careers_flagged, 0)

    def test_a_company_that_later_gets_a_board_keeps_the_item(self):
        self.demote_quill()
        self.run_careers()
        sundial = self.company("Sundial Health Inc")
        add_board(self.conn, sundial, BoardRef("lever", "sundial"), via="hand", matched_on="", found_by="hand")
        self.assertEqual(count(self.conn, "SELECT count(*) FROM board WHERE company_id = ?", sundial), 1)
        self.run_careers()
        (item,) = self.flagged("Sundial Health Inc")
        self.assertEqual(item["state"], "open")  # #16 settles it

    def test_max_careers_limit(self):
        _, report = self.run_careers(max_careers=2)
        self.assertEqual(report.careers_checked, 2)
        hosts = {u.split("/")[2] for u in self.client.requests}
        self.assertEqual(hosts, {"clearpath.ai", "greenfield.ai"})  # tier 1 in company order
        self.run_careers(max_careers=2)  # next run: the never-tried first (Quill, promoted, then Riverside)
        self.assertEqual({u.split("/")[2] for u in self.client.requests}, {"quilldata.io", "riverside.edu"})

    def test_a_site_that_always_errors_moves_behind_the_others(self):
        self.run_careers(max_careers=1)
        self.assertEqual({u.split("/")[2] for u in self.client.requests}, {"clearpath.ai"})
        self.run_careers(max_careers=1)  # Greenfield (id 4) is next in id order, and errors
        self.assertEqual({u.split("/")[2] for u in self.client.requests}, {"greenfield.ai"})
        self.run_careers(max_careers=1)  # now it has an attempt, so the never-tried come first
        self.assertEqual({u.split("/")[2] for u in self.client.requests}, {"quilldata.io"})
        errors = self.events("careers_error", "Greenfield AI Inc")
        self.assertEqual(len(errors), 1)
        self.assertEqual((errors[0]["actor"], json.loads(errors[0]["detail"])["error"]),
                         ("crawler", "HTTP 503 for https://greenfield.ai/careers"))
        self.assertEqual(self.events("careers_checked", "Greenfield AI Inc"), [])  # an error never blocks a retry

    def test_the_none_marker_and_the_30_day_recheck_agree_at_the_end_of_a_month(self):
        sundial = self.company("Sundial Health Inc")
        # the marker an earlier month-keyed scheme wrote on the 1st; 30.5 days later is due again
        append_event(self.conn, entity="company", kind="careers_checked", actor="crawler", entity_id=sundial,
                     company_id=sundial, detail={"result": "none"}, at="2026-10-01T00:00:00Z",
                     idempotency_key=f"careers-checked:{sundial}:2026-10")
        with mock.patch("outreach.sources.jobs.utcnow", return_value="2026-10-31T12:00:00Z"):
            self.run_careers()
            self.assertIn("https://sundial.health/careers", self.client.requests)
            markers = self.events("careers_checked", "Sundial Health Inc")
            self.assertEqual(len(markers), 2)  # the new check is recorded, not lost to the old key
            self.assertEqual(markers[1]["at"], "2026-10-31T12:00:00Z")
            self.run_careers()
            self.assertNotIn("https://sundial.health/careers", self.client.requests)  # and now it blocks again

    def test_rerun_writes_nothing_new_but_the_attempt_record_of_a_failing_site(self):
        self.run_careers()
        before = table_marks(self.conn)
        errors = len(self.events("careers_error"))
        self.run_careers()
        after = table_marks(self.conn)
        self.assertEqual(len(self.events("careers_error")), errors + 1)  # Greenfield, tried again
        self.assertEqual({t: m for t, m in after.items() if t != "event"},
                         {t: m for t, m in before.items() if t != "event"})
        self.assertEqual(after["event"][0], before["event"][0] + 1)


if __name__ == "__main__":
    unittest.main()
