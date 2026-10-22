"""The SmartRecruiters adapter (issue #21, Task 17)."""
import json
import unittest
from pathlib import Path

from outreach.crawl.ats import ADAPTERS, adapter_for, supported_ats
from outreach.crawl.ats.smartrecruiters import SmartRecruitersAdapter
from outreach.crawl.base import JobRef, Validators, ids_fingerprint
from outreach.net import FetchError, Response
from tests.contracts import check_adapter
from tests.fakes import FakeClient, json_response, status_response, text_response

FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
P0_URL = "https://api.smartrecruiters.com/v1/companies/acme/postings?limit=100&offset=0"
P1_URL = "https://api.smartrecruiters.com/v1/companies/acme/postings?limit=100&offset=2"
P3_URL = "https://api.smartrecruiters.com/v1/companies/acme/postings?limit=100&offset=3"
DETAIL_URL = "https://api.smartrecruiters.com/v1/companies/acme/postings/743999000111222"
P0 = (FIXTURES / "smartrecruiters_p0.json").read_bytes()
P1 = (FIXTURES / "smartrecruiters_p1.json").read_bytes()
DETAIL = (FIXTURES / "smartrecruiters_detail.json").read_bytes()
ETAG = 'W/"sr-etag-1"'
IDS = ["743999000111222", "743999000111333", "743999000111444"]


def page(body, etag=ETAG):
    return Response(200, "", body, {"etag": etag} if etag else {})


def p0():
    return json.loads(P0)


def p1():
    return json.loads(P1)


def read(routes, token="acme", validators=Validators()):
    client = FakeClient(routes)
    result = SmartRecruitersAdapter(client).list(token, validators)
    return result, client


def good_routes():
    return {P0_URL: page(P0), P1_URL: page(P1, etag="")}


class SmartRecruitersTests(unittest.TestCase):
    def test_check_adapter(self):
        client = FakeClient({P0_URL: [page(P0), status_response(304), status_response(500)], P1_URL: [page(P1)]})
        check_adapter(self, SmartRecruitersAdapter(client), "acme")

    def test_registered(self):
        self.assertIs(ADAPTERS["smartrecruiters"], SmartRecruitersAdapter)
        self.assertIn("smartrecruiters", supported_ats())
        self.assertIsInstance(adapter_for("smartrecruiters", FakeClient({})), SmartRecruitersAdapter)

    def test_list_ok_parses_jobs(self):
        r, client = read(good_routes())
        self.assertEqual(client.requests, [P0_URL, P1_URL])
        self.assertEqual(r.result, "ok")
        self.assertEqual(
            [(j.native_id, j.title, j.url, j.location, j.country, j.posted_on, j.body) for j in r.jobs],
            [(IDS[0], "Software Engineer, New Grad", "https://jobs.smartrecruiters.com/acme/" + IDS[0],
              "Boston, MA", "US", "2026-09-30", None),
             (IDS[1], "Account Executive", "https://jobs.smartrecruiters.com/acme/" + IDS[1], "", "GB",
              "2026-10-05", None),
             (IDS[2], "Data Engineer", "https://jobs.smartrecruiters.com/acme/" + IDS[2], "Austin, TX", "US",
              "2026-08-29", None)])
        self.assertEqual(r.validators, Validators(etag=ETAG, ids_fingerprint=ids_fingerprint(IDS)))
        self.assertEqual(r.board_name, "Acme Robotics")

    def test_two_pages_are_joined(self):
        r, client = read(good_routes())
        self.assertEqual([j.native_id for j in r.jobs], IDS)
        self.assertEqual(len(client.calls), 2)

    def test_a_single_page_board_makes_one_request(self):
        one = p0()
        one["totalFound"] = 2
        r, client = read({P0_URL: json_response(one, etag=ETAG)})
        self.assertEqual((r.result, len(r.jobs), client.requests), ("ok", 2, [P0_URL]))

    def test_count_mismatch_is_an_error(self):
        short = p1()
        short["totalFound"] = 4
        too_few = p0()
        too_few["totalFound"] = 4
        ended = p1()
        ended["content"], ended["offset"], ended["totalFound"] = [], 3, 4
        shifted = p0()
        shifted["totalFound"] = 1
        cases = {
            "the total says more than the pages carry": {P0_URL: json_response(too_few),
                                                         P1_URL: json_response(short),
                                                         P3_URL: json_response(ended)},
            "the total says fewer than the page carries": {P0_URL: json_response(shifted)},
        }
        for name, routes in cases.items():
            r, _ = read(routes)
            self.assertEqual((r.result, r.jobs), ("error", ()), name)
            self.assertIn("totalFound", r.error, name)

    def test_a_total_that_moves_between_pages_is_an_error(self):
        moved = p1()
        moved["totalFound"] = 4
        r, _ = read({P0_URL: json_response(p0()), P1_URL: json_response(moved)})
        self.assertEqual((r.result, r.jobs), ("error", ()))

    def test_a_page_that_repeats_ids_is_an_error_not_a_loop(self):
        r, client = read({P0_URL: json_response(p0()), P1_URL: json_response(p0())})
        self.assertEqual((r.result, r.jobs), ("error", ()))
        self.assertEqual(len(client.calls), 2)

    def test_an_empty_page_before_the_total_is_an_error(self):
        empty = p1()
        empty["content"] = []
        r, client = read({P0_URL: json_response(p0()), P1_URL: json_response(empty)})
        self.assertEqual((r.result, r.jobs), ("error", ()))
        self.assertEqual(len(client.calls), 2)

    def test_second_page_failure_is_an_error_not_partial(self):
        failures = {
            "500": status_response(500),
            "429": status_response(429),
            "redirect": status_response(302),
            "transport": FetchError("timeout", "slow"),
            "html": text_response("<html><title>Just a moment...</title></html>"),
            "wrong shape": json_response({"content": None}),
            "list": json_response([]),
        }
        for name, failure in failures.items():
            r, client = read({P0_URL: page(P0), P1_URL: failure})
            self.assertEqual((r.result, r.jobs), ("error", ()), name)
            self.assertEqual(r.validators, Validators(), name)
            self.assertEqual(client.requests, [P0_URL, P1_URL], name)

    def test_etag_only_on_the_first_page(self):
        r, client = read(good_routes())
        self.assertNotIn("If-None-Match", client.calls[0][2])
        self.assertNotIn("If-None-Match", client.calls[1][2])
        again, client = read(good_routes(), validators=r.validators)
        self.assertEqual(client.calls[0][2]["If-None-Match"], ETAG)
        self.assertEqual(len(client.calls), 2)
        self.assertNotIn("If-None-Match", client.calls[1][2])
        self.assertEqual(again.result, "ok")

    def test_a_304_on_the_first_page_is_unchanged(self):
        r, _ = read(good_routes())
        again, client = read({P0_URL: status_response(304)}, validators=r.validators)
        self.assertEqual((again.result, again.jobs, again.validators), ("unchanged", (), r.validators))
        self.assertEqual(len(client.calls), 1)

    def test_a_304_on_a_later_page_is_an_error(self):
        r, _ = read({P0_URL: page(P0), P1_URL: status_response(304)}, validators=Validators(etag=ETAG))
        self.assertEqual((r.result, r.http_status), ("error", 304))

    def test_a_200_with_the_same_ids_is_ok_not_unchanged(self):
        r, _ = read(good_routes())
        routes = {P0_URL: page(P0, etag='W/"new"'), P1_URL: page(P1, etag="")}
        again, _ = read(routes, validators=r.validators)
        self.assertEqual(again.result, "ok")
        self.assertEqual(again.validators.etag, 'W/"new"')

    def test_zero_postings_is_empty(self):
        empty = {"offset": 0, "limit": 100, "totalFound": 0, "content": []}
        r, client = read({P0_URL: json_response(empty, etag=ETAG)})
        self.assertEqual((r.result, r.jobs, r.validators), ("empty", (), Validators()))
        self.assertEqual(len(client.calls), 1)

    def test_html_or_wrong_shape_is_error(self):
        bad_total = p0()
        bad_total["totalFound"] = "3"
        negative = p0()
        negative["totalFound"] = -1
        no_total = p0()
        del no_total["totalFound"]
        null_content = p0()
        null_content["content"] = None
        zero_but_rows = p0()
        zero_but_rows["totalFound"] = 0
        bodies = {
            "cloudflare": text_response("<html><title>Just a moment...</title><body>Checking</body></html>"),
            "empty object": json_response({}),
            "null": json_response(None),
            "list": json_response(p0()["content"]),
            "string total": json_response(bad_total),
            "negative total": json_response(negative),
            "no total": json_response(no_total),
            "null content": json_response(null_content),
            "zero total with rows": json_response(zero_but_rows),
            "error document": json_response({"code": "NOT_FOUND", "message": "Company not found"}),
        }
        for name, resp in bodies.items():
            r, _ = read({P0_URL: resp})
            self.assertEqual((r.result, r.jobs), ("error", ()), name)

    def test_http_failures_are_errors(self):
        for status in (403, 404, 429, 500, 302):
            r, _ = read({P0_URL: status_response(status)})
            self.assertEqual((r.result, r.http_status), ("error", status), status)
        r, _ = read({P0_URL: FetchError("network", "down")})
        self.assertEqual((r.result, r.http_status), ("error", None))

    def test_job_without_id_or_name_is_error(self):
        for key in ("id", "name"):
            data = p0()
            del data["content"][1][key]
            r, _ = read({P0_URL: json_response(data), P1_URL: json_response(p1())})
            self.assertEqual((r.result, r.jobs), ("error", ()), key)
        data = p0()
        data["content"][1] = "not a posting"
        r, _ = read({P0_URL: json_response(data), P1_URL: json_response(p1())})
        self.assertEqual(r.result, "error")

    def test_odd_location_and_dates_do_not_break_the_read(self):
        data = p0()
        data["totalFound"] = 2
        data["content"][0]["location"] = "Boston"
        data["content"][0]["releasedDate"] = "last week"
        data["content"][1]["location"] = {"city": None, "region": "CA", "country": "United States"}
        r, _ = read({P0_URL: json_response(data)})
        self.assertEqual(r.result, "ok")
        self.assertEqual((r.jobs[0].location, r.jobs[0].country, r.jobs[0].posted_on), ("", None, None))
        self.assertEqual((r.jobs[1].location, r.jobs[1].country), ("CA", None))

    def test_a_numeric_id_is_kept_as_text(self):
        data = p0()
        data["totalFound"] = 2
        data["content"][0]["id"] = 743999000111222
        r, _ = read({P0_URL: json_response(data)})
        self.assertEqual(r.jobs[0].native_id, IDS[0])

    def test_bad_token_makes_no_request(self):
        for token in ("", "Acme Corp", "a/b", "acme%2F", "../x", "acme?x=1"):
            r, client = read({}, token=token)
            self.assertEqual((r.result, client.requests), ("error", []), token)

    def test_a_company_id_with_capitals_is_not_a_slug(self):
        # The store holds lower-case slugs only (#31 token grammar); the API ids are case-insensitive.
        r, client = read({}, token="AcmeRobotics")
        self.assertEqual((r.result, client.requests), ("error", []))

    def test_body_joins_the_sections(self):
        client = FakeClient({DETAIL_URL: page(DETAIL, etag="")})
        job = JobRef(IDS[0], "Software Engineer, New Grad", "", "", None, None, None)
        text = SmartRecruitersAdapter(client).body("acme", job)
        self.assertEqual(client.requests, [DETAIL_URL])
        self.assertEqual(
            text,
            "We build warehouse robots.\nBuild services & ship them.\nWrite code\nReview code\n"
            "A CS degree\nCuriosity\nWe sponsor visas for the right person.")
        self.assertNotIn("<", text)

    def test_body_skips_missing_sections_and_is_none_without_text(self):
        data = json.loads(DETAIL)
        del data["jobAd"]["sections"]["companyDescription"]
        data["jobAd"]["sections"]["qualifications"] = {"title": "Q", "text": "  "}
        data["jobAd"]["sections"]["additionalInformation"] = None
        job = JobRef(IDS[0], "t", "", "", None, None, None)
        text = SmartRecruitersAdapter(FakeClient({DETAIL_URL: json_response(data)})).body("acme", job)
        self.assertEqual(text, "Build services & ship them.\nWrite code\nReview code")
        none = {"id": IDS[0], "jobAd": {"sections": {}}}
        self.assertIsNone(SmartRecruitersAdapter(FakeClient({DETAIL_URL: json_response(none)})).body("acme", job))

    def test_a_failed_body_fetch_raises_so_the_caller_counts_it(self):
        job = JobRef(IDS[0], "t", "", "", None, None, None)
        for name, resp in {"500": status_response(500), "404": status_response(404),
                           "html": text_response("<html>blocked</html>"), "list": json_response([]),
                           "no jobAd": json_response({"id": IDS[0]}), "transport": FetchError("network", "down")
                           }.items():
            with self.assertRaises(Exception, msg=name):
                SmartRecruitersAdapter(FakeClient({DETAIL_URL: resp})).body("acme", job)

    def test_body_makes_no_request_for_an_unsafe_id_or_token(self):
        client = FakeClient({})
        adapter = SmartRecruitersAdapter(client)
        for token, native_id in (("acme", "../x"), ("acme", ""), ("Bad Token", IDS[0]), ("acme", "a b")):
            with self.assertRaises(Exception):
                adapter.body(token, JobRef(native_id, "t", "", "", None, None, None))
        self.assertEqual(client.requests, [])

    def test_duplicate_ids_across_the_total_are_an_error(self):
        data = p0()
        data["content"][1]["id"] = data["content"][0]["id"]
        r, _ = read({P0_URL: json_response(data), P1_URL: json_response(p1())})
        self.assertEqual((r.result, r.jobs), ("error", ()))


if __name__ == "__main__":
    unittest.main()
