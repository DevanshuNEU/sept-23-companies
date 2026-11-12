"""The Amazon adapter: amazon.jobs' public search JSON, US software development jobs, 100 a page."""
import json
import unittest

from outreach.crawl.ats import ADAPTERS, adapter_for, supported_ats
from outreach.crawl.ats.amazon import AmazonAdapter, posted_on, search_url
from outreach.crawl.base import Validators, ids_fingerprint
from outreach.net import FetchError
from tests.contracts import check_adapter
from tests.fakes import FakeClient, json_response, status_response, text_response


def job(n, title="Software Development Engineer I", posted="November 11, 2026", **extra):
    return dict({"id_icims": str(1000 + n), "title": title, "job_path": f"/en/jobs/{1000 + n}/sde-{n}",
                 "normalized_location": "Seattle, Washington, USA", "country_code": "USA", "posted_date": posted,
                 "description": f"Build services {n}.", "basic_qualifications": "- 1+ years of experience",
                 "preferred_qualifications": "- Bachelor's degree"}, **extra)


def page(hits, rows):
    return json_response({"error": None, "hits": hits, "jobs": rows})


def read(routes, token="amazon", validators=Validators()):
    client = FakeClient(routes)
    return AmazonAdapter(client).list(token, validators), client


U0, U1, U2 = search_url("software-development", 0), search_url("software-development", 100), \
    search_url("software-development", 200)


class AmazonTests(unittest.TestCase):
    def test_registered_and_contract(self):
        self.assertIn("amazon", supported_ats())
        self.assertIs(ADAPTERS["amazon"], AmazonAdapter)
        client = FakeClient({U0: [page(2, [job(1), job(2)]), page(2, [job(1), job(2)]), status_response(500)]})
        check_adapter(self, adapter_for("amazon", client), "amazon", client=client)

    def test_the_token_amazon_reads_us_software_development_newest_first(self):
        url = search_url("software-development", 0)
        self.assertIn("country%5B%5D=USA", url)
        self.assertIn("category%5B%5D=software-development", url)
        self.assertIn("sort=recent", url)
        self.assertIn("result_limit=100", url)
        r, client = read({U0: page(1, [job(1)])})
        self.assertEqual(client.requests, [U0])
        self.assertEqual(r.result, "ok")

    def test_a_job_has_its_link_place_date_and_text(self):
        r, _ = read({U0: page(1, [job(7, title="Software Dev Engineer I, Prime Video", posted="November 9, 2026")])})
        (j,) = r.jobs
        self.assertEqual((j.native_id, j.title, j.url, j.location, j.country, j.posted_on),
                         ("1007", "Software Dev Engineer I, Prime Video", "https://www.amazon.jobs/en/jobs/1007/sde-7",
                          "Seattle, Washington, USA", "US", "2026-11-09"))
        self.assertIn("Build services 7.", j.body)
        self.assertIn("Basic qualifications", j.body)
        self.assertIn("1+ years of experience", j.body)
        self.assertIn("Preferred qualifications", j.body)

    def test_pages_until_the_hits_are_read(self):
        first = [job(n) for n in range(100)]
        second = [job(n) for n in range(100, 200)]
        third = [job(n) for n in range(200, 250)]
        r, client = read({U0: page(250, first), U1: page(250, second), U2: page(250, third)})
        self.assertEqual((r.result, len(r.jobs), r.partial), ("ok", 250, False))
        self.assertEqual(client.requests, [U0, U1, U2])

    def test_a_list_a_little_short_is_partial_and_far_short_is_an_error(self):
        r, _ = read({U0: page(102, [job(n) for n in range(100)]), U1: page(102, [])})
        self.assertEqual((r.result, r.partial, len(r.jobs)), ("ok", True, 100))
        r, _ = read({U0: page(300, [job(n) for n in range(100)]), U1: page(300, [])})
        self.assertEqual((r.result, r.jobs), ("error", ()))

    def test_a_broken_row_is_skipped_and_the_read_is_partial(self):
        broken = job(2)
        del broken["title"]
        r, _ = read({U0: page(3, [job(1), broken, job(3)])})
        self.assertEqual((r.result, r.partial, [j.native_id for j in r.jobs]), ("ok", True, ["1001", "1003"]))

    def test_a_date_it_cannot_read_is_no_date(self):
        self.assertEqual(posted_on("November 11, 2026"), "2026-11-11")
        self.assertEqual(posted_on("Nov 11, 2026"), None)
        self.assertEqual(posted_on(None), None)

    def test_same_ids_are_unchanged(self):
        rows = [job(1), job(2)]
        r, _ = read({U0: page(2, rows)}, validators=Validators(ids_fingerprint=ids_fingerprint(["1001", "1002"])))
        self.assertEqual(r.result, "unchanged")

    def test_zero_hits_is_empty(self):
        r, _ = read({U0: page(0, [])})
        self.assertEqual(r.result, "empty")

    def test_wrong_shapes_and_failures_are_errors(self):
        for name, resp in {"html": text_response("<html>Just a moment</html>"),
                           "no jobs": json_response({"hits": 3}), "no hits": json_response({"jobs": []}),
                           "error field": json_response({"error": "bad request", "hits": 0, "jobs": []}),
                           "500": status_response(500), "429": status_response(429),
                           "network": FetchError("network", "down")}.items():
            r, _ = read({U0: resp})
            self.assertEqual((r.result, r.jobs), ("error", ()), name)

    def test_a_bad_token_makes_no_request(self):
        r, client = read({}, token="../x")
        self.assertEqual((r.result, client.requests), ("error", []))

    def test_body_is_never_fetched(self):
        self.assertIsNone(AmazonAdapter(FakeClient({})).body("amazon", None))


if __name__ == "__main__":
    unittest.main()
