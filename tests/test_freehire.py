"""freehire.me as a lead to US early-career boards (#115). Every request goes to a fake client; no test reaches the
network."""
import dataclasses
import tomllib
import unittest
from pathlib import Path

from outreach.crawl.ats import ADAPTERS
from outreach.crawl.config import load_crawl_config
from outreach.sources.freehire import (CONFIG_PATH, FreehireError, FreehireSource, load_freehire_config,
                                       search_url)
from outreach.sources.jobs import run_discover, run_find_boards, store_board_known
from outreach.sources.stapply import TERMS_EXCLUDED
from outreach.store import add_alias
from tests.fakes import FakeClient, json_response, status_response, text_response
from tests.storetest import fresh_store, seed_board, seed_company

FIRST_PAGE = ("https://freehire.me/api/v1/jobs/search?countries=us&seniority=junior&seniority=intern"
              "&category=software_engineering&category=fullstack&category=backend&category=frontend&category=mobile"
              "&category=devops&category=sre&category=qa&category=security&category=data_engineering"
              "&category=data_science&category=ml_ai&category=ai_engineering"
              "&source=ashby&source=bamboohr&source=breezy&source=greenhouse&source=jazzhr&source=lever"
              "&source=oracle&source=recruitee&source=rippling&source=teamtailor&source=workable&source=workday"
              "&posted_within_days=3&limit=100&offset=0")


def job(company, url, source="greenhouse", external_id="", company_type=None):
    return {"company": company, "url": url, "source": source, "external_id": external_id, "title": "Software Engineer",
            "enrichment": {"company_type": company_type} if company_type else {}}


DOORDASH = job("DoorDash", "https://job-boards.greenhouse.io/doordashusa/jobs/1?utm_source=freehire.me",
               external_id="doordashusa:1")
STRIPE = job("Stripe", "https://stripe.com/jobs/search?gh_jid=2&utm_source=freehire.me", external_id="stripe:2")
CIENA = job("Ciena", "https://ciena.wd5.myworkdayjobs.com/en-US/Careers/job/Atlanta/SE_R1?utm_source=freehire.me",
            source="workday", external_id="ciena.wd5.myworkdayjobs.com/Careers:/job/Atlanta/SE_R1")
MUON = job("Muon Space", "https://jobs.ashbyhq.com/muon/abc", source="ashby", external_id="muon:abc",
           company_type="startup")
PROSYNC = job("Prosync", "https://apply.workable.com/j/E93CBA35F0", source="workable", external_id="prosync:E93CBA35F0",
              company_type="outsource")


def page(*jobs, total=None):
    return json_response({"data": list(jobs), "meta": {"limit": 100, "offset": 0,
                                                       "total": len(jobs) if total is None else total}})


def source(routes, **cfg_over):
    cfg = dataclasses.replace(load_freehire_config(), **cfg_over)
    client = FakeClient(routes)
    return FreehireSource(client, cfg), client


class RequestTest(unittest.TestCase):
    def test_the_first_request_asks_for_us_junior_and_intern_engineering_on_boards_knock_reads(self):
        src, client = source({FIRST_PAGE: page()})
        self.assertEqual(list(src.discover()), [])
        self.assertEqual(client.requests, [FIRST_PAGE])
        self.assertEqual(src.summary()["pages"], 1)

    def test_a_switched_off_reader_is_never_asked_for(self):
        # SmartRecruiters and Pinpoint are off in crawl/config.toml (#46): their robots.txt says no.
        off = load_crawl_config().ats_off
        self.assertIn("smartrecruiters", off)
        self.assertNotIn("source=smartrecruiters", FIRST_PAGE)
        self.assertNotIn("source=pinpoint", FIRST_PAGE)

    def test_days_is_the_posted_within_window(self):
        url = search_url(load_freehire_config(), offset=0, days=30, reads=load_crawl_config().reads)
        self.assertIn("&posted_within_days=30&", url)

    def test_the_platform_map_holds_only_crawler_readers_and_never_a_terms_excluded_site(self):
        cfg = load_freehire_config()
        self.assertTrue(set(cfg.platforms.values()) <= set(ADAPTERS), cfg.platforms)
        for name in TERMS_EXCLUDED:
            self.assertNotIn(name, cfg.platforms)

    def test_the_version_follows_the_toml(self):
        src, _ = source({})
        self.assertRegex(src.version, r"^freehire-1-[0-9a-f]{12}$")
        self.assertEqual(load_freehire_config().digest, load_freehire_config(CONFIG_PATH).digest)

    def test_since_is_refused(self):
        src, _ = source({})
        with self.assertRaises(ValueError):
            list(src.discover("2026-10"))


class CandidateTest(unittest.TestCase):
    def found(self, *jobs):
        src, _ = source({FIRST_PAGE: page(*jobs)})
        return src, {c.source_ref: c for c in src.discover()}

    def test_a_board_url_gives_the_board_and_no_domain(self):
        _, got = self.found(DOORDASH)
        c = got["freehire:greenhouse:doordashusa"]
        self.assertEqual((c.name, c.domain, c.keys, c.evidence),
                         ("DoorDash", None, {"ats": "greenhouse:doordashusa"}, ()))

    def test_a_posting_on_the_company_site_takes_the_board_from_external_id_and_the_domain_from_the_url(self):
        _, got = self.found(STRIPE)
        c = got["freehire:greenhouse:stripe"]
        self.assertEqual((c.domain, c.keys), ("stripe.com", {"ats": "greenhouse:stripe"}))

    def test_workday_and_ashby_boards_come_from_the_url(self):
        _, got = self.found(CIENA, MUON)
        self.assertEqual(sorted(got), ["freehire:ashby:muon", "freehire:workday:ciena.wd5/Careers"])

    def test_workday_never_takes_external_id(self):
        _, got = self.found(job("Acme", "https://acme.com/careers/1", source="workday", external_id="acme.wd1/Ext:1"))
        self.assertEqual(got, {})

    def test_skips_are_counted(self):
        src, got = self.found(
            DOORDASH, DOORDASH,  # the second is a repeat of a board already yielded
            job("Xcede", "https://www.whatjobs.com/pub_api__cpl__1", source="whatjobs"),
            job("Oro", "https://wellfound.com/jobs/1-software-engineer", source="wellfound"),
            job("Acme", "https://jobs.smartrecruiters.com/Acme/1", source="smartrecruiters", external_id="acme:1"),
            job("jda.wd5.myworkdayjobs.com/university", "https://jda.wd5.myworkdayjobs.com/university/job/X_1",
                source="workday"),
            job("  ", "https://jobs.lever.co/blank/1", source="lever"),
            job("Brain Corp", "https://www.braincorp.com/open-positions", external_id=""),
        )
        self.assertEqual(list(got), ["freehire:greenhouse:doordashusa"])
        s = src.summary()
        self.assertEqual({k: s[k] for k in ("postings", "candidates", "repeat", "not_read", "bad_name", "no_board")},
                         {"postings": 8, "candidates": 1, "repeat": 1, "not_read": 3, "bad_name": 2, "no_board": 1})

    def test_one_board_per_company_name_in_a_pass(self):
        # RTX lists two Workday sites; two candidates of one name would make two RTX companies.
        src, got = self.found(
            job("RTX", "https://globalhr.wd5.myworkdayjobs.com/REC_RTX_Ext_Gateway/job/X_1", source="workday"),
            job("RTX Corp.", "https://globalhr.wd5.myworkdayjobs.com/Private_Posting/job/X_2", source="workday"))
        self.assertEqual(list(got), ["freehire:workday:globalhr.wd5/REC_RTX_Ext_Gateway"])
        self.assertEqual(src.summary()["same_name"], 1)

    def test_staffing_and_outsourcing_firms_are_tier_3_and_others_have_no_tier(self):
        src, got = self.found(PROSYNC, MUON)
        self.assertEqual(src.work_tier("freehire:workable:prosync"),
                         (3, "staffing or outsourcing firm (freehire company type: outsource)"))
        self.assertIsNone(src.work_tier("freehire:ashby:muon"))
        self.assertEqual(src.summary()["staffing"], 1)

    def test_a_board_the_store_already_has_is_skipped(self):
        src, _ = source({FIRST_PAGE: page(DOORDASH, MUON)})
        src.bind_board_known(lambda ats, token: (ats, token) == ("greenhouse", "doordashusa"))
        self.assertEqual([c.source_ref for c in src.discover()], ["freehire:ashby:muon"])
        self.assertEqual(src.summary()["known_board"], 1)


class PagingTest(unittest.TestCase):
    def url(self, offset, size):
        return search_url(dataclasses.replace(load_freehire_config(), page_size=size), offset=offset, days=3,
                          reads=load_crawl_config().reads)

    def test_pages_are_read_until_a_short_page(self):
        src, client = source({self.url(0, 2): page(DOORDASH, MUON, total=9), self.url(2, 2): page(CIENA, total=9)},
                             page_size=2)
        self.assertEqual(len(list(src.discover())), 3)
        self.assertEqual(client.requests, [self.url(0, 2), self.url(2, 2)])
        self.assertEqual(src.summary()["capped"], 0)

    def test_paging_stops_at_the_total(self):
        src, client = source({self.url(0, 2): page(DOORDASH, MUON, total=2)}, page_size=2)
        self.assertEqual(len(list(src.discover())), 2)
        self.assertEqual(client.requests, [self.url(0, 2)])

    def test_max_pages_stops_the_pass_and_is_counted(self):
        src, client = source({self.url(0, 2): page(DOORDASH, MUON, total=50)}, page_size=2, max_pages=1)
        self.assertEqual(len(list(src.discover())), 2)
        self.assertEqual(src.summary()["capped"], 1)


class ErrorTest(unittest.TestCase):
    def assert_error(self, routes, **cfg_over):
        src, _ = source(routes, **cfg_over)
        with self.assertRaises(FreehireError):
            list(src.discover())

    def test_an_http_error_is_an_error(self):
        self.assert_error({FIRST_PAGE: status_response(429)})

    def test_a_body_that_is_not_json_is_an_error(self):
        self.assert_error({FIRST_PAGE: text_response("<html>challenge</html>")})

    def test_a_body_without_a_data_list_is_an_error(self):
        self.assert_error({FIRST_PAGE: json_response({"error": "bad facet"})})

    def test_a_failed_later_page_yields_nothing(self):
        cfg = dataclasses.replace(load_freehire_config(), page_size=2)
        first = search_url(cfg, offset=0, days=3, reads=load_crawl_config().reads)
        second = search_url(cfg, offset=2, days=3, reads=load_crawl_config().reads)
        src, _ = source({first: page(DOORDASH, MUON, total=9), second: status_response(503)}, page_size=2)
        yielded = []
        with self.assertRaises(FreehireError):
            for c in src.discover():
                yielded.append(c)
        self.assertEqual(yielded, [])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def test_discover_makes_new_companies_with_their_board_and_skips_a_board_the_store_has(self):
        known = seed_company(self.conn, "DoorDash")
        seed_board(self.conn, known, "greenhouse", "doordashusa")
        src, _ = source({FIRST_PAGE: page(DOORDASH, MUON, PROSYNC)})
        run_id, reports = run_discover(self.conn, [src])
        report = reports["freehire"]
        self.assertEqual((report.candidates, report.created, report.error), (2, 2, ""))
        self.assertEqual(report.notes["known_board"], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM company WHERE name = 'DoorDash'").fetchone()[0], 1)
        muon = self.conn.execute("SELECT company_id FROM company WHERE name = 'Muon Space'").fetchone()[0]
        self.assertEqual(self.conn.execute("SELECT alias FROM company_alias WHERE company_id = ? AND source = 'ats'",
                                           (muon,)).fetchone()[0], "ashby:muon")
        prosync = self.conn.execute("SELECT company_id FROM company WHERE name = 'Prosync'").fetchone()[0]
        tier = self.conn.execute("SELECT tier FROM work_tier WHERE company_id = ?", (prosync,)).fetchone()[0]
        self.assertEqual(tier, 3)
        run_find_boards(self.conn, directory=None, max_careers=0)
        board = self.conn.execute("SELECT ats, token FROM board WHERE company_id = ?", (muon,)).fetchone()
        self.assertEqual(tuple(board), ("ashby", "muon"))
        self.assertIsNone(self.conn.execute("SELECT 1 FROM board WHERE company_id = ?", (prosync,)).fetchone())

    def test_a_rerun_attaches_to_the_same_company(self):
        src, _ = source({FIRST_PAGE: [page(MUON), page(MUON)]})
        run_discover(self.conn, [src])
        _, reports = run_discover(self.conn, [src])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM company").fetchone()[0], 1)
        self.assertEqual(reports["freehire"].created, 0)

    def test_store_board_known_reads_boards_and_ats_aliases(self):
        known = store_board_known(self.conn)
        company = seed_company(self.conn, "Acme")
        seed_board(self.conn, company, "lever", "acme")
        add_alias(self.conn, company, "ashby:acme-labs", "ats")
        self.assertTrue(known("lever", "acme"))
        self.assertTrue(known("ashby", "acme-labs"))
        self.assertFalse(known("greenhouse", "acme"))


class ConfigTest(unittest.TestCase):
    def test_the_shipped_toml_loads_and_is_on(self):
        cfg = load_freehire_config()
        raw = tomllib.loads(Path(CONFIG_PATH).read_text(encoding="utf-8"))
        self.assertTrue(cfg.enabled)
        self.assertEqual((cfg.page_size, cfg.days, cfg.countries), (100, 3, ("us",)))
        self.assertEqual(cfg.platforms, raw["platforms"])

    def test_a_page_size_over_the_api_cap_is_refused(self):
        with self.assertRaises(ValueError):
            dataclasses.replace(load_freehire_config(), page_size=101)


if __name__ == "__main__":
    unittest.main()
