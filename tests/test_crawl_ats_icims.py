"""The iCIMS Jibe adapter (#220): a Jibe careers site's own JSON list, newest first, 10 jobs a page."""
import copy
import json
import unittest
from pathlib import Path

from outreach.crawl.ats import ADAPTERS, adapter_for, supported_ats
from outreach.crawl.ats.icims import MAX_PAGES, IcimsAdapter, list_url
from outreach.crawl.base import Validators, ids_fingerprint
from outreach.net import FetchError
from tests.contracts import check_adapter
from tests.fakes import FakeClient, json_response, status_response, text_response

FIXTURE = Path(__file__).parent / "fixtures" / "crawl" / "icims_jibe_jobs.json"
SAVED = json.loads(FIXTURE.read_text(encoding="utf-8"))  # 3 jobs of careers.jhuapl.edu page 1, 2026-11-13, trimmed
HOST = "careers.jhuapl.edu"
U1, U2, U3 = list_url(HOST, 1), list_url(HOST, 2), list_url(HOST, 3)
SAMPLE = SAVED["jobs"][0]["data"]  # Software Engineer for Networked Sensor Systems


def job(n: int, **extra) -> dict:
    data = copy.deepcopy(SAMPLE)
    data.update({"slug": str(70000 + n), "req_id": str(70000 + n), "title": f"Software Engineer {n}"}, **extra)
    return {"data": data}


def page(total, jobs) -> dict:
    return {"jobs": jobs, "totalCount": total, "count": total}


def read(routes, token=HOST, validators=Validators(), head=False):
    client = FakeClient(routes)
    adapter = IcimsAdapter(client)
    return (adapter.head(token) if head else adapter.list(token, validators)), client


class IcimsTests(unittest.TestCase):
    def test_registered_and_contract(self):
        self.assertIn("icims", supported_ats())
        self.assertIs(ADAPTERS["icims"], IcimsAdapter)
        rows = [job(1), job(2)]
        client = FakeClient({U1: [json_response(page(2, rows)), json_response(page(2, rows)), status_response(500)]})
        check_adapter(self, adapter_for("icims", client), HOST, client=client)

    def test_the_list_asks_for_public_jobs_newest_first_by_page(self):
        self.assertEqual(U1, "https://careers.jhuapl.edu/api/jobs?page=1&sortBy=posted_date&descending=true"
                             "&internal=false")
        self.assertEqual(U3, "https://careers.jhuapl.edu/api/jobs?page=3&sortBy=posted_date&descending=true"
                             "&internal=false")
        self.assertGreaterEqual(MAX_PAGES * 10, 2000)

    def test_a_job_from_the_saved_page_has_its_link_place_dates_and_text(self):
        r, client = read({U1: json_response(SAVED)}, head=True)
        self.assertEqual(client.requests, [U1])
        jobs = {j.native_id: j for j in r.jobs}
        self.assertEqual(set(jobs), {"60930", "60868", "59297"})
        swe = jobs["60930"]
        self.assertEqual((swe.title, swe.url, swe.location, swe.country, swe.posted_on, swe.posted_at),
                         ("Software Engineer for Networked Sensor Systems",
                          "https://careers.jhuapl.edu/jobs/60930?icims=1&lang=en-us", "Laurel, Maryland", "US",
                          "2026-11-13", "2026-11-13T17:06:00Z"))
        self.assertTrue(swe.body.startswith("Description\nAre you a software engineer"))
        self.assertNotIn("<", swe.body)
        # The saved description already holds the responsibilities, so they are not repeated; the trimmed description
        # stops before the qualifications, so those are added.
        self.assertEqual(swe.body.count("Are you a software engineer interested"), 1)
        self.assertIn("You meet our minimum qualifications", swe.body)

    def test_a_reposted_job_keeps_its_date_but_not_a_posting_time(self):
        # 59297 has been on the site since 2026-07-01 (create_date); its posted_date moved to 2026-11-13 on a repost.
        r, _ = read({U1: json_response(SAVED)}, head=True)
        old = {j.native_id: j for j in r.jobs}["59297"]
        self.assertEqual((old.posted_on, old.posted_at), ("2026-11-13", None))

    def test_the_link_is_the_job_page_and_round_trips_to_its_posting_key(self):
        from outreach.crawl.postings import source_key
        from outreach.pipeline.urls import board_key, posting_key
        r, _ = read({U1: json_response(SAVED)}, head=True)
        for j in r.jobs:
            self.assertEqual(posting_key(board_key(j.url)), source_key("icims", HOST, j.native_id).lower())

    def test_text_split_across_fields_is_joined_once(self):
        row = job(1, description="<p>We build radar.</p>", responsibilities="<ul><li>Write C++</li></ul>",
                  qualifications="<p>A BS in CS.</p>")
        r, _ = read({U1: json_response(page(1, [row]))})
        self.assertEqual(r.jobs[0].body, "We build radar.\n\nWrite C++\n\nA BS in CS.")
        same = job(2, description="<p>We build radar.</p>", responsibilities="<p>We build radar.</p>",
                   qualifications="")
        r, _ = read({U1: json_response(page(1, [same]))})
        self.assertEqual(r.jobs[0].body, "We build radar.")

    def test_the_slug_is_the_id_and_req_id_stands_in(self):
        r, _ = read({U1: json_response(page(1, [job(1, slug=None, req_id="R-99")]))})
        self.assertEqual((r.jobs[0].native_id, r.jobs[0].url), ("R-99", f"https://{HOST}/jobs/R-99?icims=1&lang=en-us"))

    def test_place_falls_back_to_city_and_state_and_many_places_have_no_country(self):
        r, _ = read({U1: json_response(page(1, [job(1, full_location="")]))})
        self.assertEqual((r.jobs[0].location, r.jobs[0].country), ("Laurel, Maryland", "US"))
        r, _ = read({U1: json_response(page(1, [job(1, multipleLocations=True)]))})
        self.assertIsNone(r.jobs[0].country)
        r, _ = read({U1: json_response(page(1, [job(1, country_code="GB", full_location="London")]))})
        self.assertEqual((r.jobs[0].location, r.jobs[0].country), ("London", "GB"))

    def test_pages_until_the_total_is_read(self):
        r, client = read({U1: json_response(page(25, [job(n) for n in range(10)])),
                          U2: json_response(page(25, [job(n) for n in range(10, 20)])),
                          U3: json_response(page(25, [job(n) for n in range(20, 25)]))})
        self.assertEqual((r.result, len(r.jobs), r.partial, r.head), ("ok", 25, False, False))
        self.assertEqual(client.requests, [U1, U2, U3])
        self.assertTrue(r.validators.ids_fingerprint)

    def test_paging_stops_at_the_cap(self):
        routes = {list_url(HOST, n): json_response(page(10 * MAX_PAGES + 500, [job(10 * n + k) for k in range(10)]))
                  for n in range(1, MAX_PAGES + 1)}
        r, client = read(routes)
        self.assertEqual(len(client.requests), MAX_PAGES)
        # 500 short of the total: our own cap stopped the read, so what was read is kept and nothing is closed
        self.assertEqual((r.result, r.partial, len(r.jobs)), ("ok", True, 10 * MAX_PAGES))

    def test_a_job_seen_twice_while_paging_is_kept_once(self):
        r, _ = read({U1: json_response(page(11, [job(n) for n in range(10)])),
                     U2: json_response(page(11, [job(9), job(10)]))})
        self.assertEqual((r.result, len(r.jobs), r.partial), ("ok", 11, False))

    def test_a_list_a_little_short_or_far_short_is_partial(self):
        r, _ = read({U1: json_response(page(12, [job(n) for n in range(10)])), U2: json_response(page(12, []))})
        self.assertEqual((r.result, r.partial, len(r.jobs), r.validators), ("ok", True, 10, Validators()))
        self.assertIn("10 of 12", r.partial_note)
        r, _ = read({U1: json_response(page(300, [job(n) for n in range(10)])), U2: json_response(page(300, []))})
        self.assertEqual((r.result, r.partial, len(r.jobs), r.validators), ("ok", True, 10, Validators()))
        self.assertIn("10 of 300", r.partial_note)

    def test_a_site_that_stops_listing_early_keeps_the_jobs_it_listed(self):
        # careers.rivian.com (2026-11-14): 360 jobs listed, totalCount 953, so every full read was an error and the
        # board never held a posting. A partial ok closes nothing, so keeping the 360 is safe.
        pages = {list_url(HOST, n): json_response(page(953, [job(10 * n + k) for k in range(10)])) for n in range(1, 37)}
        pages[list_url(HOST, 37)] = json_response(page(953, [job(10 * n + k) for n in (1, 2) for k in range(10)][:10]))
        r, _ = read(pages)
        self.assertEqual((r.result, r.partial, len(r.jobs)), ("ok", True, 360))
        self.assertIn("360 of 953", r.partial_note)

    def test_a_later_page_that_fails_keeps_what_was_read_and_stops(self):
        for name, resp in {"500": status_response(500), "429": status_response(429),
                           "network": FetchError("network", "down"), "not json": text_response("<html></html>"),
                           "wrong shape": json_response({"error": "x"})}.items():
            r, client = read({U1: json_response(page(30, [job(n) for n in range(10)])), U2: resp,
                              U3: json_response(page(30, [job(n) for n in range(20, 30)]))})
            self.assertEqual((r.result, r.partial, len(r.jobs)), ("ok", True, 10), name)
            self.assertIn("stopped at page 2", r.partial_note)
            self.assertEqual(client.requests, [U1, U2], name)  # no further page, no retry

    def test_a_first_page_that_fails_is_an_error(self):
        for resp in (status_response(429), status_response(404), FetchError("network", "down")):
            r, client = read({U1: resp})
            self.assertEqual((r.result, r.jobs), ("error", ()))
            self.assertEqual(client.requests, [U1])

    def test_a_broken_row_is_skipped_and_the_read_is_partial(self):
        r, _ = read({U1: json_response(page(3, [job(1), job(2, title=""), job(3)]))})
        self.assertEqual((r.result, r.partial, len(r.jobs)), ("ok", True, 2))
        self.assertIn("1 broken", r.partial_note)

    def test_same_ids_are_unchanged_and_zero_is_empty(self):
        rows = [job(1), job(2)]
        fp = ids_fingerprint(["70001", "70002"])
        r, _ = read({U1: json_response(page(2, rows))}, validators=Validators(ids_fingerprint=fp))
        self.assertEqual(r.result, "unchanged")
        r, _ = read({U1: json_response(page(0, []))})
        self.assertEqual(r.result, "empty")

    def test_the_head_read_is_page_one_partial(self):
        r, client = read({U1: json_response(page(539, [job(n) for n in range(10)]))}, head=True)
        self.assertEqual((r.result, r.partial, r.head, len(r.jobs)), ("ok", True, True, 10))
        self.assertIn("10 of 539", r.partial_note)
        self.assertEqual(client.requests, [U1])

    def test_an_empty_head_page_is_unchanged(self):
        r, _ = read({U1: json_response(page(0, []))}, head=True)
        self.assertEqual((r.result, r.head, r.jobs), ("unchanged", True, ()))

    def test_wrong_shapes_and_failures_are_errors(self):
        bad = {"not json": text_response("<html>Not found</html>"), "no jobs": json_response({"totalCount": 1}),
               "jobs not a list": json_response({"jobs": {"a": 1}, "totalCount": 1}),
               "no total": json_response({"jobs": [job(1)]}), "bool total": json_response(page(True, [job(1)])),
               "500": status_response(500), "302": status_response(302), "network": FetchError("network", "down")}
        for name, resp in bad.items():
            for head in (False, True):
                r, _ = read({U1: resp}, head=head)
                self.assertEqual((r.result, r.jobs), ("error", ()), (name, head))
                self.assertTrue(r.error)

    def test_a_plain_icims_site_is_refused_with_no_request(self):
        for head in (False, True):
            r, client = read({}, token="careers-acme.icims.com", head=head)
            self.assertEqual((r.result, client.requests), ("error", []))
            self.assertIn("a plain iCIMS site, not a Jibe site, has no reader yet", r.error)

    def test_a_token_that_is_not_a_host_makes_no_request(self):
        for token in ("jhuapl", "Careers.jhuapl.edu", "careers.jhuapl.edu/jobs", "", "../x"):
            for head in (False, True):
                r, client = read({}, token=token, head=head)
                self.assertEqual((r.result, client.requests), ("error", []), token)

    def test_body_is_never_fetched(self):
        client = FakeClient({})
        self.assertIsNone(IcimsAdapter(client).body(HOST, None))
        self.assertEqual(client.requests, [])


class JibeSpacingTests(unittest.TestCase):
    """robots.txt on careers.jhuapl.edu says crawl-delay: 5 (2026-11-13). Jibe tenants share no host suffix, so the
    polite client spaces every host the icims reader sends to by [ats_interval_s] icims."""

    def polite(self, routes):
        from outreach.crawl.config import load_crawl_config
        from outreach.crawl.polite import PoliteClient
        self.now = 0.0
        self.slept: list[float] = []

        def sleep(s):
            self.slept.append(s)
            self.now += s
        fake = FakeClient(routes)
        return PoliteClient(fake, load_crawl_config(), clock=lambda: self.now, sleep=sleep), fake

    def test_the_config_spaces_icims_five_seconds_and_reads_its_head(self):
        from outreach.crawl.config import load_crawl_config
        cfg = load_crawl_config()
        self.assertEqual(cfg.ats_interval_s, {"icims": 5.0})
        self.assertIn("icims", cfg.tiers.head_ats)

    def test_each_page_of_a_jibe_site_waits_five_seconds(self):
        polite, fake = self.polite({U1: json_response(page(25, [job(n) for n in range(10)])),
                                    U2: json_response(page(25, [job(n) for n in range(10, 20)])),
                                    U3: json_response(page(25, [job(n) for n in range(20, 25)]))})
        r = adapter_for("icims", polite).list(HOST, Validators())
        self.assertEqual((r.result, len(r.jobs)), ("ok", 25))
        self.assertEqual(fake.requests, [U1, U2, U3])
        self.assertEqual(self.slept, [5.0, 5.0])

    def test_two_jibe_hosts_do_not_wait_on_each_other(self):
        other = "careers.garmin.com"
        polite, _ = self.polite({U1: json_response(page(1, [job(1)])),
                                 list_url(other, 1): json_response(page(1, [job(2)]))})
        adapter_for("icims", polite).head(HOST)
        adapter_for("icims", polite).head(other)
        self.assertEqual(self.slept, [])

    def test_a_host_the_reader_used_keeps_five_seconds_for_any_other_request(self):
        # A watched page on the same host must not slip in one second after a list page.
        polite, _ = self.polite({U1: json_response(page(1, [job(1)])),
                                 f"https://{HOST}/": text_response("<html>careers</html>")})
        adapter_for("icims", polite).head(HOST)
        polite.get(f"https://{HOST}/")
        self.assertEqual(self.slept, [5.0])

    def test_other_readers_keep_their_own_spacing(self):
        polite, _ = self.polite({"https://example.org/a": text_response("a"), "https://example.org/b": text_response("b")})
        polite.get("https://example.org/a")
        polite.get("https://example.org/b")
        self.assertEqual(self.slept, [1.0])  # default_host_interval_s


if __name__ == "__main__":
    unittest.main()
