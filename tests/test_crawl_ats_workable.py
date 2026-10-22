"""The Workable adapter (issue #21, Task 19)."""
import json
import unittest
from pathlib import Path

from outreach.crawl.ats import ADAPTERS, adapter_for, supported_ats
from outreach.crawl.ats.workable import WorkableAdapter
from outreach.crawl.base import JobRef, Validators, ids_fingerprint
from outreach.net import FetchError
from tests.contracts import check_adapter
from tests.fakes import FakeClient, json_response, status_response, text_response

FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
URL = "https://apply.workable.com/api/v1/widget/accounts/acme?details=true"
MD_URL = "https://apply.workable.com/acme/jobs/view/A1B2C3D4E5.md"
LIST = (FIXTURES / "workable_list.json").read_bytes()
EMPTY = (FIXTURES / "workable_empty.json").read_bytes()
MD = (FIXTURES / "workable_md.txt").read_text()
CODES = ["A1B2C3D4E5", "F6G7H8I9J0", "K1L2M3N4O5"]


def jobs():
    return json.loads(LIST)


def read(routes, token="acme", validators=Validators()):
    client = FakeClient(routes)
    return WorkableAdapter(client).list(token, validators), client


class WorkableTests(unittest.TestCase):
    def test_check_adapter(self):
        client = FakeClient({URL: [LIST, LIST, status_response(500)]})
        check_adapter(self, WorkableAdapter(client), "acme")

    def test_registered(self):
        self.assertIs(ADAPTERS["workable"], WorkableAdapter)
        self.assertIn("workable", supported_ats())
        self.assertIsInstance(adapter_for("workable", FakeClient({})), WorkableAdapter)

    def test_list_ok_parses_jobs(self):
        r, client = read({URL: LIST})
        self.assertEqual(client.requests, [URL])
        self.assertEqual(r.result, "ok")
        self.assertEqual(
            [(j.native_id, j.title, j.url, j.location, j.country, j.posted_on) for j in r.jobs],
            [(CODES[0], "Software Engineer, New Grad", "https://apply.workable.com/j/A1B2C3D4E5",
              "Boston, Massachusetts, US", "US", "2026-09-30"),
             (CODES[1], "Account Executive", "https://apply.workable.com/j/F6G7H8I9J0", "United Kingdom", None,
              "2026-10-03"),
             (CODES[2], "Data Engineer", "https://apply.workable.com/j/K1L2M3N4O5", "Toronto, Ontario, CA", "CA",
              "2026-08-29")])
        self.assertEqual(r.validators, Validators(etag="", ids_fingerprint=ids_fingerprint(CODES)))
        self.assertEqual(r.board_name, "Acme Robotics")

    def test_list_bodies(self):
        r, _ = read({URL: LIST})
        self.assertEqual(r.jobs[0].body, "Build services & ship them.\nWrite code\nReview code\n"
                                         "We sponsor visas for the right person.")
        self.assertIsNone(r.jobs[1].body)
        self.assertEqual(r.jobs[2].body, "Move data around.")

    def test_url_falls_back_to_the_shortlink_then_the_public_page(self):
        data = jobs()
        del data["jobs"][0]["url"]
        del data["jobs"][1]["url"]
        del data["jobs"][1]["shortlink"]
        r, _ = read({URL: json_response(data)})
        self.assertEqual(r.jobs[0].url, "https://apply.workable.com/j/A1B2C3D4E5")
        self.assertEqual(r.jobs[1].url, "https://apply.workable.com/acme/j/F6G7H8I9J0/")

    def test_zero_jobs_200_is_empty(self):
        r, _ = read({URL: EMPTY})
        self.assertEqual((r.result, r.jobs, r.validators), ("empty", (), Validators()))
        r, _ = read({URL: json_response({"jobs": []})})
        self.assertEqual(r.result, "empty")

    def test_html_or_wrong_shape_is_error(self):
        bodies = {
            "cloudflare": text_response("<html><title>Just a moment...</title><body>Checking</body></html>"),
            "empty object": json_response({}),
            "null jobs": json_response({"name": "Acme", "jobs": None}),
            "jobs object": json_response({"jobs": {"a": 1}}),
            "other shape": json_response({"results": []}),
            "top level list": json_response(jobs()["jobs"]),
            "null": json_response(None),
            "string": json_response("{}"),
            "not found document": json_response({"error": "Not found"}),
            "jobs of non objects": json_response({"jobs": [None]}),
            "jobs of strings": json_response({"jobs": ["a"]}),
        }
        for name, resp in bodies.items():
            r, _ = read({URL: resp})
            self.assertEqual((r.result, r.jobs), ("error", ()), name)

    def test_http_failures_are_errors(self):
        for status in (403, 404, 429, 500, 302):
            r, _ = read({URL: status_response(status)})
            self.assertEqual((r.result, r.http_status), ("error", status), status)
        r, _ = read({URL: FetchError("timeout", "slow")})
        self.assertEqual((r.result, r.http_status), ("error", None))

    def test_a_304_is_an_error_since_no_etag_is_sent(self):
        r, client = read({URL: status_response(304)})
        self.assertEqual(r.result, "error")
        self.assertNotIn("if-none-match", {k.lower() for k in client.calls[0][2]})

    def test_job_without_shortcode_or_title_is_error(self):
        for key in ("shortcode", "title"):
            data = jobs()
            del data["jobs"][1][key]
            r, _ = read({URL: json_response(data)})
            self.assertEqual((r.result, r.jobs), ("error", ()), key)

    def test_odd_optional_fields_do_not_break_the_read(self):
        data = jobs()
        data["jobs"][0]["city"] = None
        data["jobs"][0]["state"] = 5
        data["jobs"][0]["country"] = ["US"]
        data["jobs"][0]["published_on"] = "soon"
        data["jobs"][0]["created_at"] = None
        data["jobs"][1]["description"] = 7
        data["jobs"][2]["country"] = "usa"
        data["name"] = None
        r, _ = read({URL: json_response(data)})
        self.assertEqual(r.result, "ok")
        self.assertEqual((r.jobs[0].location, r.jobs[0].country, r.jobs[0].posted_on), ("", None, None))
        self.assertIsNone(r.jobs[1].body)
        self.assertEqual((r.jobs[2].location, r.jobs[2].country), ("Toronto, Ontario, usa", None))
        self.assertEqual(r.board_name, "")

    def test_a_lower_case_country_code_is_upper_cased(self):
        data = jobs()
        data["jobs"][0]["country"] = "us"
        r, _ = read({URL: json_response(data)})
        self.assertEqual(r.jobs[0].country, "US")

    def test_posted_on_falls_back_to_created_at(self):
        data = jobs()
        del data["jobs"][0]["published_on"]
        r, _ = read({URL: json_response(data)})
        self.assertEqual(r.jobs[0].posted_on, "2026-09-28")

    def test_validators_are_a_fingerprint_not_an_etag(self):
        r, _ = read({URL: LIST})
        again, client = read({URL: LIST}, validators=r.validators)
        self.assertEqual((again.result, again.jobs, again.validators), ("unchanged", (), r.validators))
        self.assertNotIn("if-none-match", {k.lower() for k in client.calls[0][2]})

    def test_a_new_shortcode_is_ok(self):
        r, _ = read({URL: LIST})
        data = jobs()
        data["jobs"].append({"title": "Designer", "shortcode": "P1Q2R3S4T5"})
        again, _ = read({URL: json_response(data)}, validators=r.validators)
        self.assertEqual((again.result, len(again.jobs)), ("ok", 4))

    def test_duplicate_shortcodes_are_kept_once(self):
        data = jobs()
        data["jobs"].append(dict(data["jobs"][0]))
        r, _ = read({URL: json_response(data)})
        self.assertEqual([j.native_id for j in r.jobs], CODES)

    def test_bad_token_makes_no_request(self):
        for token in ("", "Acme Corp", "a/b", "acme%2F", "../x", "acme?details=false", "ACME"):
            r, client = read({}, token=token)
            self.assertEqual((r.result, client.requests), ("error", []), token)

    def test_markdown_body_fallback(self):
        client = FakeClient({MD_URL: text_response(MD)})
        job = JobRef(CODES[0], "Software Engineer, New Grad", "", "", None, None, None)
        text = WorkableAdapter(client).body("acme", job)
        self.assertEqual(client.requests, [MD_URL])
        self.assertEqual(text, MD.strip())
        self.assertIn("We sponsor visas for the right person.", text)

    def test_an_empty_markdown_body_is_none(self):
        client = FakeClient({MD_URL: text_response("  \n")})
        self.assertIsNone(WorkableAdapter(client).body("acme", JobRef(CODES[0], "t", "", "", None, None, None)))

    def test_a_failed_body_fetch_raises_so_the_caller_counts_it(self):
        job = JobRef(CODES[0], "t", "", "", None, None, None)
        for name, resp in {"500": status_response(500), "404": status_response(404), "302": status_response(302),
                           "html": text_response("<!DOCTYPE html><html><body>Just a moment...</body></html>"),
                           "transport": FetchError("network", "down")}.items():
            with self.assertRaises(Exception, msg=name):
                WorkableAdapter(FakeClient({MD_URL: resp})).body("acme", job)

    def test_body_makes_no_request_for_an_unsafe_shortcode_or_token(self):
        client = FakeClient({})
        adapter = WorkableAdapter(client)
        for token, code in (("acme", ""), ("acme", "../x"), ("acme", "a b"), ("acme", "A1?x=1"),
                            ("Bad Token", CODES[0])):
            with self.assertRaises(Exception, msg=code):
                adapter.body(token, JobRef(code, "t", "", "", None, None, None))
        self.assertEqual(client.requests, [])


if __name__ == "__main__":
    unittest.main()
