"""The Apple adapter (#263): the search pages of jobs.apple.com, US jobs newest first, 20 a page."""
import copy
import json
import re
import unittest
from pathlib import Path

from outreach.crawl.ats import ADAPTERS, adapter_for, supported_ats
from outreach.crawl.ats.apple import MAX_PAGES, AppleAdapter, search_url
from outreach.crawl.base import Validators, ids_fingerprint
from outreach.net import FetchError
from tests.contracts import check_adapter
from tests.fakes import FakeClient, status_response, text_response

FIXTURE = Path(__file__).parent / "fixtures" / "crawl" / "apple_search.html"
PAGE = FIXTURE.read_text(encoding="utf-8")
U1, U2, U3 = search_url("us", 1), search_url("us", 2), search_url("us", 3)


def fixture_results() -> list[dict]:
    m = re.search(r'JSON\.parse\("((?:[^"\\]|\\.)*)"\);', PAGE)
    return json.loads(json.loads('"' + m.group(1) + '"'))["loaderData"]["search"]["searchResults"]


SAMPLE = fixture_results()[1]  # Software Engineer, Infrastructure Applications


def result(n: int, **extra) -> dict:
    out = copy.deepcopy(SAMPLE)
    out.update({"id": f"{200000000 + n}-3956", "positionId": str(200000000 + n), "jobPositionId": f"{200000000 + n}-3956",
                "reqId": f"{200000000 + n}-3956", "postingTitle": f"Software Engineer {n}",
                "transformedPostingTitle": f"software-engineer-{n}"}, **extra)
    return out


def page(total, results) -> str:
    """A search page whose hydration data holds `results` and `totalRecords` `total`, encoded as Apple encodes it."""
    data = {"loaderData": {"search": {"searchResults": results, "totalRecords": total}}}
    literal = json.dumps(json.dumps(data, ensure_ascii=False), ensure_ascii=False)[1:-1]
    return f'<html><script>window.__staticRouterHydrationData = JSON.parse("{literal}");</script></html>'


def read(routes, token="us", validators=Validators(), head=False):
    client = FakeClient(routes)
    adapter = AppleAdapter(client)
    return (adapter.head(token) if head else adapter.list(token, validators)), client


class AppleTests(unittest.TestCase):
    def test_registered_and_contract(self):
        self.assertIn("apple", supported_ats())
        self.assertIs(ADAPTERS["apple"], AppleAdapter)
        rows = [result(1), result(2)]
        client = FakeClient({U1: [page(2, rows), page(2, rows), status_response(500)]})
        check_adapter(self, adapter_for("apple", client), "us", client=client)

    def test_the_search_asks_for_us_jobs_newest_first_by_page(self):
        self.assertEqual(U1, "https://jobs.apple.com/en-us/search?location=united-states-USA&sort=newest&page=1")
        self.assertEqual(U3, "https://jobs.apple.com/en-us/search?location=united-states-USA&sort=newest&page=3")
        self.assertGreaterEqual(MAX_PAGES * 20, 4551 * 1.2)  # 4,551 US jobs on 2026-11-13, with room to grow

    def test_a_job_from_the_saved_page_has_its_link_place_dates_and_text(self):
        r, client = read({U1: PAGE}, head=True)
        self.assertEqual(client.requests, [U1])
        jobs = {j.native_id: j for j in r.jobs}
        self.assertEqual(set(jobs), {"PIPE-114438158", "200693202-3956", "200694842-0836"})
        swe = jobs["200693202-3956"]
        self.assertEqual((swe.title, swe.url, swe.location, swe.country, swe.posted_on, swe.posted_at),
                         ("Software Engineer, Infrastructure Applications",
                          "https://jobs.apple.com/en-us/details/200693202-3956/software-engineer-infrastructure-"
                          "applications?team=SFTWR",
                          "Sunnyvale", "US", "2026-11-13", "2026-11-13T16:13:21Z"))
        self.assertTrue(swe.body)
        retail = jobs["PIPE-114438158"]
        # A pipeline role's page is under its position id, as the search page links it.
        self.assertEqual(retail.url, "https://jobs.apple.com/en-us/details/114438158/us-specialist-seasonal-part-time"
                                     "?team=APPST")
        self.assertEqual(retail.location, "United States")
        self.assertEqual(retail.posted_at, "2026-11-13T17:39:15Z")

    def test_utf8_text_is_decoded_right(self):
        r, _ = read({U1: PAGE}, head=True)
        retail = {j.native_id: j for j in r.jobs}["PIPE-114438158"]
        self.assertIn("You’re dedicated", retail.body)
        self.assertNotIn("â", retail.body)
        self.assertIn("\n\nAs a Specialist", retail.body)

    def test_a_job_outside_the_us_has_no_country(self):
        other = result(1, locations=[dict(SAMPLE["locations"][0], name="London", countryID="iso-country-GBR")])
        r, _ = read({U1: page(1, [other])})
        self.assertEqual((r.jobs[0].location, r.jobs[0].country), ("London", None))

    def test_pages_until_the_total_is_read(self):
        r, client = read({U1: page(45, [result(n) for n in range(20)]),
                          U2: page(45, [result(n) for n in range(20, 40)]),
                          U3: page(45, [result(n) for n in range(40, 45)])})
        self.assertEqual((r.result, len(r.jobs), r.partial, r.head), ("ok", 45, False, False))
        self.assertEqual(client.requests, [U1, U2, U3])
        self.assertTrue(r.validators.ids_fingerprint)

    def test_paging_stops_at_the_cap(self):
        routes = {search_url("us", n): page(20 * MAX_PAGES + 500, [result(20 * n + k) for k in range(20)])
                  for n in range(1, MAX_PAGES + 1)}
        r, client = read(routes)
        self.assertEqual(len(client.requests), MAX_PAGES)
        self.assertEqual(r.result, "error")  # 500 short of the total is far short

    def test_a_job_seen_twice_while_paging_is_kept_once(self):
        r, _ = read({U1: page(21, [result(n) for n in range(20)]),
                     U2: page(21, [result(19), result(20)])})
        self.assertEqual((r.result, len(r.jobs), r.partial), ("ok", 21, False))

    def test_a_list_a_little_short_is_partial_and_far_short_is_an_error(self):
        r, _ = read({U1: page(22, [result(n) for n in range(20)]), U2: page(22, [])})
        self.assertEqual((r.result, r.partial, len(r.jobs), r.validators), ("ok", True, 20, Validators()))
        self.assertIn("20 of 22", r.partial_note)
        r, _ = read({U1: page(300, [result(n) for n in range(20)]), U2: page(300, [])})
        self.assertEqual((r.result, r.jobs), ("error", ()))

    def test_a_later_page_that_fails_keeps_what_was_read_and_stops(self):
        # 2026-11-13 live: page 158 of about 228 timed out and the whole read was an error, so Apple never got its
        # first full read and its head reads never began. Now the pages read are a partial ok that closes nothing.
        for name, resp in {"500": status_response(500), "429": status_response(429),
                           "network": FetchError("network", "down"), "no data": text_response("<html></html>")}.items():
            r, client = read({U1: page(60, [result(n) for n in range(20)]), U2: resp,
                              U3: page(60, [result(n) for n in range(40, 60)])})
            self.assertEqual((r.result, r.partial, len(r.jobs)), ("ok", True, 20), name)
            self.assertIn("stopped at page 2", r.partial_note)
            self.assertEqual(client.requests, [U1, U2], name)  # no further page after the failure

    def test_a_first_page_that_fails_is_an_error(self):
        r, _ = read({U1: status_response(429)})
        self.assertEqual((r.result, r.jobs), ("error", ()))

    def test_a_broken_row_is_skipped_and_the_read_is_partial(self):
        broken = result(2)
        del broken["postingTitle"]
        r, _ = read({U1: page(3, [result(1), broken, result(3)])})
        self.assertEqual((r.result, r.partial, len(r.jobs)), ("ok", True, 2))
        self.assertIn("1 broken", r.partial_note)

    def test_same_ids_are_unchanged_and_zero_is_empty(self):
        rows = [result(1), result(2)]
        fp = ids_fingerprint([rows[0]["id"], rows[1]["id"]])
        r, _ = read({U1: page(2, rows)}, validators=Validators(ids_fingerprint=fp))
        self.assertEqual(r.result, "unchanged")
        r, _ = read({U1: page(0, [])})
        self.assertEqual(r.result, "empty")

    def test_the_head_read_is_page_one_partial(self):
        r, client = read({U1: page(4551, [result(n) for n in range(20)])}, head=True)
        self.assertEqual((r.result, r.partial, r.head, len(r.jobs)), ("ok", True, True, 20))
        self.assertIn("20 of 4551", r.partial_note)
        self.assertEqual(client.requests, [U1])

    def test_an_empty_head_page_is_unchanged(self):
        r, _ = read({U1: page(0, [])}, head=True)
        self.assertEqual((r.result, r.head, r.jobs), ("unchanged", True, ()))

    def test_wrong_shapes_and_failures_are_errors(self):
        bad = {"no data": text_response("<html>Not found</html>"),
               "garbled": text_response('<script>window.__staticRouterHydrationData = JSON.parse("{\\"loa");</script>'),
               "no search": text_response('<script>window.__staticRouterHydrationData = JSON.parse("{}");</script>'),
               "no total": text_response(page(None, [result(1)])),
               "results not a list": text_response(page(1, {"a": 1})),
               "500": status_response(500), "302": status_response(302), "network": FetchError("network", "down")}
        for name, resp in bad.items():
            for head in (False, True):
                r, _ = read({U1: resp}, head=head)
                self.assertEqual((r.result, r.jobs), ("error", ()), (name, head))
                self.assertTrue(r.error)

    def test_another_token_makes_no_request(self):
        for token in ("apple", "united-states-USA", "../x", "", "us/2"):
            for head in (False, True):
                r, client = read({}, token=token, head=head)
                self.assertEqual((r.result, client.requests), ("error", []), token)

    def test_its_host_is_spaced_two_seconds(self):
        from outreach.crawl.config import ATS_HOSTS, load_crawl_config
        from outreach.crawl.polite import bucket_for
        cfg = load_crawl_config()
        self.assertEqual(ATS_HOSTS["apple"], "jobs.apple.com")
        self.assertEqual(bucket_for("jobs.apple.com", cfg), "jobs.apple.com")
        self.assertEqual(cfg.host_interval_s["jobs.apple.com"], 2.0)
        self.assertIn("apple", cfg.tiers.head_ats)

    def test_body_is_never_fetched(self):
        client = FakeClient({})
        self.assertIsNone(AppleAdapter(client).body("us", None))
        self.assertEqual(client.requests, [])


if __name__ == "__main__":
    unittest.main()
