"""The Eightfold adapter (#246): an Eightfold careers site's PCSX search JSON, US positions, ten a page."""
import unittest

from outreach.crawl.ats import ADAPTERS, adapter_for, supported_ats
from outreach.crawl.ats.eightfold import EightfoldAdapter, detail_url, search_url, tenant
from outreach.crawl.base import JobRef, Validators, ids_fingerprint
from outreach.crawl.row_clock import DATE_TRUSTED
from outreach.net import FetchError
from tests.contracts import check_adapter
from tests.fakes import FakeClient, json_response, status_response, text_response

HOST, DOMAIN = "careers.micron.com", "micron.com"


def pos(n, name="Software Engineer, New College Grad", posted=1794355200, **extra):
    return dict({"id": 44890000 + n, "displayJobId": f"JR{n}", "name": name,
                 "locations": ["Boise, Idaho, United States of America"], "standardizedLocations": ["Boise, ID, US"],
                 "postedTs": posted, "creationTs": 1792627200, "positionUrl": f"/careers/job/{44890000 + n}"}, **extra)


def page(count, rows):
    return json_response({"status": 200, "error": {"message": "", "body": ""},
                          "data": {"positions": rows, "count": count, "sortBy": "timestamp"}})


def url(start):
    return search_url(HOST, DOMAIN, start)


def read(routes, token="micron", validators=Validators()):
    client = FakeClient(routes)
    return EightfoldAdapter(client).list(token, validators), client


class EightfoldListTests(unittest.TestCase):
    def test_registered_and_contract(self):
        self.assertIn("eightfold", supported_ats())
        self.assertIs(ADAPTERS["eightfold"], EightfoldAdapter)
        client = FakeClient({url(0): [page(2, [pos(1), pos(2)]), page(2, [pos(1), pos(2)]), status_response(500)]})
        check_adapter(self, adapter_for("eightfold", client), "micron", client=client)

    def test_the_search_asks_for_us_positions_newest_first(self):
        u = url(0)
        self.assertTrue(u.startswith("https://careers.micron.com/api/pcsx/search?"))
        for part in ("domain=micron.com", "location=United%20States", "sort_by=timestamp", "start=0", "query="):
            self.assertIn(part, u)
        r, client = read({u: page(1, [pos(1)])})
        self.assertEqual((client.requests, r.result), ([u], "ok"))
        self.assertEqual(client.calls[0][2].get("Accept"), "application/json")

    def test_the_tenant_table_and_the_default(self):
        self.assertEqual(tenant("micron"), ("careers.micron.com", "micron.com"))
        self.assertEqual(tenant("paypal"), ("paypal.eightfold.ai", "paypal.com"))
        self.assertEqual(tenant("acme"), ("acme.eightfold.ai", "acme.com"))
        for bad in ("", "A", "a.b", "a/b", "../x", "a b", None):
            self.assertIsNone(tenant(bad), bad)

    def test_a_position_has_its_link_places_and_date(self):
        r, _ = read({url(0): page(1, [pos(7, locations=["Boise, Idaho, United States of America",
                                                         "Folsom, California, United States of America"])])})
        (j,) = r.jobs
        self.assertEqual((j.native_id, j.title, j.url, j.location, j.country, j.posted_on, j.body),
                         ("44890007", "Software Engineer, New College Grad", "https://careers.micron.com/careers/job/44890007",
                          "Boise, Idaho, United States of America; Folsom, California, United States of America", None,
                          "2026-11-11", None))
        r, _ = read({url(0): page(1, [pos(8, locations=[], standardizedLocations=["Austin, TX, US"])])})
        self.assertEqual(r.jobs[0].location, "Austin, TX, US")

    def test_every_tenant_host_is_spaced_three_seconds(self):
        # 1 a second drew 429s and tripped the breaker on the first live reads (#248).
        from outreach.crawl.config import load_crawl_config
        from outreach.crawl.polite import bucket_for
        from outreach.crawl.ats.eightfold import TENANTS
        cfg = load_crawl_config()
        for host in [h for h, _ in TENANTS.values()] + ["acme.eightfold.ai"]:
            self.assertEqual(cfg.host_interval_s[bucket_for(host, cfg)], 3.0, host)

    def test_its_posted_date_is_kept_but_never_trusted_for_the_list(self):
        self.assertNotIn("eightfold", DATE_TRUSTED)  # postedTs moves when a job is reposted

    def test_pages_by_ten_until_the_count_is_read(self):
        routes = {url(0): page(25, [pos(n) for n in range(10)]), url(10): page(25, [pos(n) for n in range(10, 20)]),
                  url(20): page(25, [pos(n) for n in range(20, 25)])}
        r, client = read(routes)
        self.assertEqual((r.result, len(r.jobs), r.partial), ("ok", 25, False))
        self.assertEqual(client.requests, [url(0), url(10), url(20)])

    def test_a_little_short_is_partial_and_far_short_is_an_error(self):
        r, _ = read({url(0): page(12, [pos(n) for n in range(10)]), url(10): page(12, [])})
        self.assertEqual((r.result, r.partial, len(r.jobs)), ("ok", True, 10))
        r, _ = read({url(0): page(40, [pos(n) for n in range(10)]), url(10): page(40, [])})
        self.assertEqual((r.result, r.jobs), ("error", ()))

    def test_a_page_with_nothing_new_stops_the_read(self):
        r, client = read({url(0): page(30, [pos(n) for n in range(10)]), url(10): page(30, [pos(n) for n in range(10)])})
        self.assertEqual(r.result, "error")
        self.assertEqual(client.requests, [url(0), url(10)])

    def test_a_broken_row_is_skipped_and_the_read_is_partial(self):
        r, _ = read({url(0): page(4, [pos(1), pos(2, name=""), pos(3, id="x1"), pos(4)])})
        self.assertEqual((r.result, r.partial, [j.native_id for j in r.jobs]), ("ok", True, ["44890001", "44890004"]))

    def test_same_ids_are_unchanged_and_zero_is_empty(self):
        fp = ids_fingerprint(["44890001", "44890002"])
        r, _ = read({url(0): page(2, [pos(1), pos(2)])}, validators=Validators(ids_fingerprint=fp))
        self.assertEqual(r.result, "unchanged")
        r, _ = read({url(0): page(0, [])})
        self.assertEqual(r.result, "empty")

    def test_wrong_shapes_and_refusals_are_errors(self):
        for name, resp in {"html": text_response("<html>Just a moment</html>"),
                           "not authorized": json_response({"message": "Not authorized for PCSX"}, status=403),
                           "rate limited": text_response("Please try again later", status=429),
                           "no data": json_response({"status": 200}), "no positions": json_response({"data": {"count": 3}}),
                           "no count": json_response({"data": {"positions": []}}),
                           "bool count": json_response({"data": {"positions": [], "count": True}}),
                           "redirect": status_response(302), "500": status_response(500),
                           "network": FetchError("network", "down")}.items():
            r, _ = read({url(0): resp})
            self.assertEqual((r.result, r.jobs), ("error", ()), name)

    def test_a_bad_token_makes_no_request(self):
        r, client = read({}, token="../x")
        self.assertEqual((r.result, client.requests), ("error", []))


def head(routes, token="micron"):
    client = FakeClient(routes)
    return EightfoldAdapter(client).head(token), client


class EightfoldHeadTests(unittest.TestCase):
    """The head read (#263): page 1 of the search, already newest first, one request."""

    def test_page_one_is_a_partial_head_read(self):
        r, client = head({url(0): page(1159, [pos(n) for n in range(10)])})
        self.assertEqual(client.requests, [url(0)])
        self.assertIn("sort_by=timestamp", client.requests[0])
        self.assertEqual((r.result, r.partial, r.head, len(r.jobs), r.validators), ("ok", True, True, 10, Validators()))
        self.assertIn("10 of 1159", r.partial_note)
        j = r.jobs[0]
        self.assertEqual((j.native_id, j.url, j.title), ("44890000", "https://careers.micron.com/careers/job/44890000",
                                                         "Software Engineer, New College Grad"))

    def test_the_microsoft_tenant_reads_its_own_host(self):
        u = search_url("apply.careers.microsoft.com", "microsoft.com", 0)
        r, client = head({u: page(30, [pos(1)])}, token="microsoft")
        self.assertEqual((r.result, client.requests), ("ok", [u]))

    def test_an_empty_page_is_unchanged(self):
        r, _ = head({url(0): page(0, [])})
        self.assertEqual((r.result, r.head, r.jobs), ("unchanged", True, ()))

    def test_a_broken_row_is_skipped_and_only_broken_rows_is_an_error(self):
        r, _ = head({url(0): page(30, [pos(1), pos(2, name="")])})
        self.assertEqual([j.native_id for j in r.jobs], ["44890001"])
        r, _ = head({url(0): page(30, [pos(2, name="")])})
        self.assertEqual((r.result, r.jobs), ("error", ()))

    def test_wrong_shapes_and_refusals_are_errors(self):
        for name, resp in {"html": text_response("<html>Just a moment</html>"),
                           "rate limited": text_response("Please try again later", status=429),
                           "no positions": json_response({"data": {"count": 3}}), "500": status_response(500),
                           "network": FetchError("network", "down")}.items():
            r, _ = head({url(0): resp})
            self.assertEqual((r.result, r.jobs), ("error", ()), name)

    def test_a_bad_token_makes_no_request(self):
        r, client = head({}, token="../x")
        self.assertEqual((r.result, client.requests), ("error", []))


class EightfoldBodyTests(unittest.TestCase):
    def body(self, resp, native_id="44890001"):
        client = FakeClient({detail_url(HOST, DOMAIN, native_id): resp})
        job = JobRef(native_id, "x", "https://evil.example/x", "", None, None, None)
        return EightfoldAdapter(client).body("micron", job), client

    def test_body_from_position_details(self):
        text, client = self.body(json_response({"status": 200, "data": {
            "id": "44890001", "jobDescription": "<p>Build <b>firmware</b>.</p><ul><li>C++</li></ul>"}}))
        self.assertEqual(text, "Build firmware.\nC++")
        self.assertEqual(client.requests, [detail_url(HOST, DOMAIN, "44890001")])
        self.assertIn("position_id=44890001", client.requests[0])
        self.assertIn("domain=micron.com", client.requests[0])

    def test_an_empty_description_is_none(self):
        text, _ = self.body(json_response({"data": {"id": 44890001, "jobDescription": "<p> </p>"}}))
        self.assertIsNone(text)

    def test_wrong_answers_raise(self):
        for resp in (status_response(404), status_response(429), status_response(302), text_response("<html>"),
                     json_response({"data": {"id": "44890002", "jobDescription": "<p>x</p>"}}),
                     json_response({"data": {"jobDescription": "<p>x</p>"}}),
                     json_response({"data": {"id": "44890001"}}), json_response({"data": None}), json_response([])):
            with self.assertRaises((ValueError, FetchError), msg=resp):
                self.body(resp)

    def test_a_bad_id_or_token_makes_no_request(self):
        client = FakeClient({})
        for token, native_id in (("micron", "../1"), ("micron", ""), ("micron", "12a"), ("A", "1")):
            with self.assertRaises(ValueError):
                EightfoldAdapter(client).body(token, JobRef(native_id, "x", "", "", None, None, None))
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
