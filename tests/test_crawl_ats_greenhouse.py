"""The Greenhouse adapter (issue #21, Task 4)."""
import json
import unittest
from pathlib import Path

from outreach.crawl.ats import ADAPTERS, adapter_for, supported_ats
from outreach.crawl.ats.greenhouse import GreenhouseAdapter
from outreach.crawl.base import Validators, ids_fingerprint
from outreach.crawl.config import load_crawl_config
from outreach.crawl.polite import HostTripped, bucket_for
from outreach.net import FetchError, Response
from tests.contracts import check_adapter
from tests.fakes import FakeClient, json_response, status_response, text_response

FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
US = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
EU = "https://boards-api.eu.greenhouse.io/v1/boards/acme/jobs?content=true"
JOBS = (FIXTURES / "greenhouse_jobs.json").read_bytes()
EMPTY = (FIXTURES / "greenhouse_empty.json").read_bytes()
ETAG = 'W/"7147bb1aa914061391564153d73b13e4"'
IDS = ["4012345001", "4012399001", "4020001001"]


def ok(body=JOBS, etag=ETAG):
    return Response(200, "", body, {"etag": etag} if etag else {})


def fixture_jobs():
    return json.loads(JOBS)["jobs"]


def read(routes, token="acme", validators=Validators()):
    client = FakeClient(routes)
    result = GreenhouseAdapter(client).list(token, validators)
    return result, client


class GreenhouseTests(unittest.TestCase):
    def test_check_adapter(self):
        client = FakeClient({US: [ok(), status_response(304), status_response(500)]})
        check_adapter(self, GreenhouseAdapter(client), "acme")

    def test_registered(self):
        self.assertIs(ADAPTERS["greenhouse"], GreenhouseAdapter)
        self.assertIn("greenhouse", supported_ats())
        client = FakeClient({})
        self.assertIsInstance(adapter_for("greenhouse", client), GreenhouseAdapter)
        self.assertIsNone(adapter_for("nope", client))

    def test_list_ok_parses_jobs(self):
        r, _ = read({US: ok()})
        self.assertEqual(r.result, "ok")
        self.assertEqual(
            [(j.native_id, j.title, j.location, j.url, j.posted_on, j.country) for j in r.jobs],
            [("4012345001", "Software Engineer, New Grad", "San Francisco, CA",
              "https://job-boards.greenhouse.io/acme/jobs/4012345001", "2026-09-15", None),
             ("4012399001", "Account Executive", "Remote - US",
              "https://job-boards.greenhouse.io/acme/jobs/4012399001", "2026-10-02", None),
             ("4020001001", "Data Engineer", "New York, NY",
              "https://job-boards.greenhouse.io/acme/jobs/4020001001", "2026-08-30", None)])
        self.assertEqual(r.validators, Validators(etag=ETAG, ids_fingerprint=ids_fingerprint(IDS)))
        self.assertEqual(r.board_name, "Acme Robotics")

    def test_requests_content_true(self):
        _, client = read({US: ok()})
        self.assertEqual(client.requests, [US])
        self.assertTrue(client.requests[0].endswith("?content=true"))

    def test_content_is_plain_text(self):
        r, _ = read({US: ok()})
        for j in r.jobs:
            if j.body is not None:
                self.assertNotIn("<", j.body)
                self.assertNotIn("&lt;", j.body)
                self.assertNotIn("&amp;", j.body)
        self.assertEqual(r.jobs[0].body, "We are hiring a new grad engineer.\nBuild things\nShip & learn")
        self.assertEqual(r.jobs[1].body, "Own the full sales cycle.")

    def test_a_job_without_content_has_no_body(self):
        r, _ = read({US: ok()})
        self.assertIsNone(r.jobs[2].body)
        self.assertIsNone(GreenhouseAdapter(FakeClient({})).body("acme", r.jobs[2]))

    def test_a_job_whose_content_is_blank_has_no_body(self):
        jobs = fixture_jobs()[:1]
        jobs[0]["content"] = ""
        r, _ = read({US: json_response({"jobs": jobs})})
        self.assertIsNone(r.jobs[0].body)

    def test_location_may_be_a_string_and_the_date_falls_back_to_updated_at(self):
        jobs = fixture_jobs()[1:2]
        jobs[0]["location"] = "Austin, TX"
        r, _ = read({US: json_response({"jobs": jobs})})
        self.assertEqual((r.jobs[0].location, r.jobs[0].posted_on), ("Austin, TX", "2026-10-02"))
        jobs[0]["location"] = None
        r, _ = read({US: json_response({"jobs": jobs})})
        self.assertEqual(r.jobs[0].location, "")

    def test_empty_board_is_empty(self):
        r, _ = read({US: ok(EMPTY)})
        self.assertEqual((r.result, r.jobs, r.validators), ("empty", (), Validators()))

    def test_html_or_wrong_shape_is_error(self):
        bodies = {
            "cloudflare": text_response("<html><title>Just a moment...</title><body>Checking</body></html>"),
            "other shape": json_response({"offers": [], "meta": {}}),
            "missing key": json_response({"meta": {"total": 0}}),
            "null list": json_response({"jobs": None}),
            "object list": json_response({"jobs": {"a": 1}}),
            "list top level": json_response([{"id": 1, "title": "x"}]),
            "null": json_response(None),
            "list of non objects": json_response({"jobs": ["a", 3]}),
        }
        for name, resp in bodies.items():
            r, _ = read({US: resp})
            self.assertEqual(r.result, "error", name)
            self.assertEqual(r.jobs, (), name)

    def test_http_failures_are_errors(self):
        for status in (403, 429, 500, 302):
            r, _ = read({US: status_response(status)})
            self.assertEqual((r.result, r.http_status), ("error", status), status)
        r, _ = read({US: FetchError("network", "down")})
        self.assertEqual((r.result, r.http_status), ("error", None))

    def test_validators(self):
        r, client = read({US: ok()}, validators=Validators())
        self.assertNotIn("If-None-Match", client.calls[0][2])
        again, client = read({US: status_response(304)}, validators=r.validators)
        self.assertEqual(client.calls[0][2]["If-None-Match"], ETAG)
        self.assertEqual((again.result, again.jobs, again.validators), ("unchanged", (), r.validators))

    def test_a_200_with_the_same_ids_is_ok_not_unchanged(self):
        r, _ = read({US: ok()})
        again, _ = read({US: ok(etag='W/"new"')}, validators=r.validators)
        self.assertEqual(again.result, "ok")
        self.assertEqual(again.validators.etag, 'W/"new"')

    def test_job_without_id_or_title_is_error(self):
        for key in ("id", "title"):
            jobs = fixture_jobs()
            del jobs[1][key]
            r, _ = read({US: json_response({"jobs": jobs})})
            self.assertEqual(r.result, "error", key)
            self.assertEqual(r.jobs, (), key)
        for key, value in (("id", None), ("title", ""), ("title", "  "), ("id", ""), ("title", 7)):
            jobs = fixture_jobs()
            jobs[0][key] = value
            r, _ = read({US: json_response({"jobs": jobs})})
            self.assertEqual(r.result, "error", f"{key}={value!r}")

    def test_duplicate_ids_are_kept_once(self):
        jobs = fixture_jobs()
        jobs.append(dict(jobs[0], title="Copy"))
        r, _ = read({US: json_response({"jobs": jobs})})
        self.assertEqual([j.native_id for j in r.jobs], IDS)
        self.assertEqual(r.jobs[0].title, "Software Engineer, New Grad")

    def test_bad_token_makes_no_request(self):
        for token in ("a/b", "A", "", "a b", "a%20b"):
            r, client = read({}, token=token)
            self.assertEqual((r.result, client.calls), ("error", []), token)

    def test_a_404_from_the_us_host_tries_the_eu_host_and_reads_it(self):
        r, client = read({US: status_response(404), EU: ok()})
        self.assertEqual(client.requests, [US, EU])
        self.assertEqual(r.result, "ok")
        self.assertEqual([j.native_id for j in r.jobs], IDS)
        self.assertEqual(client.unrouted, [])

    def test_the_eu_host_gets_the_etag_and_can_answer_unchanged(self):
        r, _ = read({US: ok()})
        again, client = read({US: status_response(404), EU: status_response(304)}, validators=r.validators)
        self.assertEqual(again.result, "unchanged")
        self.assertEqual(client.calls[1][2]["If-None-Match"], ETAG)

    def test_a_404_from_both_hosts_is_an_error_not_empty(self):
        r, client = read({US: status_response(404), EU: status_response(404)})
        self.assertEqual(client.requests, [US, EU])
        self.assertEqual((r.result, r.jobs, r.http_status), ("error", (), 404))

    def test_the_eu_host_is_tried_only_for_a_404(self):
        for status in (403, 429, 500):
            r, client = read({US: status_response(status)})
            self.assertEqual(client.requests, [US], status)
            self.assertEqual((r.result, r.http_status), ("error", status))

    def test_an_eu_failure_after_a_us_404_is_that_failure(self):
        r, _ = read({US: status_response(404), EU: status_response(500)})
        self.assertEqual((r.result, r.http_status), ("error", 500))
        r, _ = read({US: status_response(404), EU: text_response("<html>blocked</html>")})
        self.assertEqual(r.result, "error")
        r, _ = read({US: status_response(404), EU: FetchError("timeout")})
        self.assertEqual((r.result, r.http_status), ("error", None))

    def test_a_us_fetch_error_does_not_try_the_eu_host(self):
        r, client = read({US: FetchError("network")})
        self.assertEqual((r.result, client.requests), ("error", [US]))

    def test_a_good_eu_board_that_is_empty_is_empty(self):
        r, _ = read({US: status_response(404), EU: ok(EMPTY)})
        self.assertEqual(r.result, "empty")

    def test_a_list_that_does_not_match_meta_total_is_an_error(self):
        # A short list would close real postings as gone_from_board, so a count that disagrees is never ok or empty.
        r, _ = read({US: json_response({"jobs": [], "meta": {"total": 50}})})
        self.assertEqual((r.result, r.http_status, r.error), ("error", 200, "list length does not match meta.total"))
        r, _ = read({US: json_response({"jobs": fixture_jobs()[:1], "meta": {"total": 50}})})
        self.assertEqual((r.result, r.http_status, r.jobs), ("error", 200, ()))

    def test_meta_total_that_is_missing_or_not_a_number_is_not_checked(self):
        for meta in (None, {}, {"total": None}, {"total": "3"}, {"total": True}, "x"):
            r, _ = read({US: json_response({"jobs": fixture_jobs(), "meta": meta})})
            self.assertEqual(r.result, "ok", repr(meta))
        r, _ = read({US: json_response({"jobs": fixture_jobs()})})
        self.assertEqual(r.result, "ok")

    def test_the_total_is_compared_with_the_list_as_sent_not_after_dedupe(self):
        jobs = fixture_jobs()
        jobs.append(dict(jobs[0]))
        r, _ = read({US: json_response({"jobs": jobs, "meta": {"total": 4}})})
        self.assertEqual(r.result, "ok")

    def test_a_bad_first_published_falls_back_to_updated_at(self):
        jobs = fixture_jobs()[:1]
        jobs[0]["first_published"] = "2026-02-30T00:00:00Z"
        r, _ = read({US: json_response({"jobs": jobs})})
        self.assertEqual(r.jobs[0].posted_on, "2026-10-01")

    def test_a_tripped_host_propagates_from_the_us_request_and_from_the_eu_hop(self):
        client = FakeClient({US: HostTripped("greenhouse.io")})
        with self.assertRaises(HostTripped):
            GreenhouseAdapter(client).list("acme", Validators())
        client = FakeClient({US: status_response(404), EU: HostTripped("greenhouse.io")})
        with self.assertRaises(HostTripped):
            GreenhouseAdapter(client).list("acme", Validators())
        self.assertEqual(client.requests, [US, EU])

    def test_both_greenhouse_api_hosts_share_one_polite_bucket(self):
        cfg = load_crawl_config()
        self.assertEqual(bucket_for("boards-api.greenhouse.io", cfg), "greenhouse.io")
        self.assertEqual(bucket_for("boards-api.eu.greenhouse.io", cfg), "greenhouse.io")
