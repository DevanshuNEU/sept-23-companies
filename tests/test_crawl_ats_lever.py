"""The Lever adapter (issue #21, Task 5)."""
import json
import unittest
from pathlib import Path

from outreach.crawl.ats import ADAPTERS, adapter_for, supported_ats
from outreach.crawl.ats.lever import LeverAdapter
from outreach.crawl.base import Validators, ids_fingerprint
from outreach.net import FetchError, Response
from tests.contracts import check_adapter
from tests.fakes import FakeClient, json_response, status_response, text_response

FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
US = "https://api.lever.co/v0/postings/acme?mode=json"
EU = "https://api.eu.lever.co/v0/postings/acme?mode=json"
JOBS = (FIXTURES / "lever_list.json").read_bytes()
EMPTY = (FIXTURES / "lever_empty.json").read_bytes()
ETAG = 'W/"5d41402abc4b2a76b9719d911017c592"'
IDS = ["6f1c2d3a-0b1e-4a55-9a10-7d3b8c1e0001", "6f1c2d3a-0b1e-4a55-9a10-7d3b8c1e0002",
       "6f1c2d3a-0b1e-4a55-9a10-7d3b8c1e0003"]


def ok(body=JOBS, etag=ETAG):
    return Response(200, "", body, {"etag": etag} if etag else {})


def fixture_jobs():
    return json.loads(JOBS)


def read(routes, token="acme", validators=Validators()):
    client = FakeClient(routes)
    result = LeverAdapter(client).list(token, validators)
    return result, client


class LeverTests(unittest.TestCase):
    def test_check_adapter(self):
        client = FakeClient({US: [ok(), status_response(304), status_response(500)]})
        check_adapter(self, LeverAdapter(client), "acme")

    def test_registered(self):
        self.assertIs(ADAPTERS["lever"], LeverAdapter)
        self.assertIn("lever", supported_ats())
        client = FakeClient({})
        self.assertIsInstance(adapter_for("lever", client), LeverAdapter)

    def test_list_ok_parses_jobs(self):
        r, client = read({US: ok()})
        self.assertEqual(client.requests, [US])
        self.assertEqual(r.result, "ok")
        self.assertEqual(
            [(j.native_id, j.title, j.location, j.url, j.posted_on, j.country) for j in r.jobs],
            [(IDS[0], "Software Engineer, New Grad", "San Francisco, CA",
              "https://jobs.lever.co/acme/" + IDS[0], "2026-09-21", "US"),
             (IDS[1], "Account Executive", "", "https://jobs.lever.co/acme/" + IDS[1], "2026-10-03", None),
             (IDS[2], "Data Engineer", "New York, NY", "https://jobs.lever.co/acme/" + IDS[2], "2026-08-29",
              "US")])
        self.assertEqual(r.validators, Validators(etag=ETAG, ids_fingerprint=ids_fingerprint(IDS)))
        self.assertEqual(r.board_name, "")

    def test_body_joins_lists_and_additional(self):
        r, _ = read({US: ok()})
        body = r.jobs[0].body
        self.assertEqual(
            body,
            "We are hiring a new grad engineer.\nWhat you will do\nBuild services\nShip & learn\n"
            "What we ask\nA CS degree\nCuriosity\nWe sponsor visas for the right person.")
        order = ["new grad engineer", "What you will do", "Ship & learn", "What we ask", "right person"]
        self.assertEqual([body.index(p) for p in order], sorted(body.index(p) for p in order))
        self.assertNotIn("<", body)
        self.assertNotIn("&amp;", body)

    def test_body_falls_back_to_the_html_description(self):
        r, _ = read({US: ok()})
        self.assertEqual(r.jobs[1].body, "Own the full sales cycle.")
        self.assertEqual(r.jobs[2].body, "Move data around.")

    def test_additional_falls_back_to_its_html(self):
        jobs = fixture_jobs()[:1]
        del jobs[0]["additionalPlain"]
        jobs[0]["lists"] = []
        r, _ = read({US: json_response(jobs)})
        self.assertEqual(r.jobs[0].body, "We are hiring a new grad engineer.\nWe sponsor visas for the right person.")

    def test_a_job_with_no_text_at_all_has_no_body(self):
        jobs = fixture_jobs()[1:2]
        for key in ("description", "lists", "additional", "additionalPlain"):
            jobs[0].pop(key, None)
        r, _ = read({US: json_response(jobs)})
        self.assertIsNone(r.jobs[0].body)
        self.assertIsNone(LeverAdapter(FakeClient({})).body("acme", r.jobs[0]))

    def test_odd_lists_do_not_break_the_read(self):
        jobs = fixture_jobs()[:1]
        jobs[0]["lists"] = [None, "x", {"text": "Only a heading"}, {"content": "<li>Only content</li>"}]
        r, _ = read({US: json_response(jobs)})
        self.assertEqual(r.result, "ok")
        self.assertEqual(r.jobs[0].body, "We are hiring a new grad engineer.\nOnly a heading\nOnly content\n"
                                         "We sponsor visas for the right person.")

    def test_location_and_country_are_optional_and_typed(self):
        jobs = fixture_jobs()[:1]
        jobs[0]["categories"] = None
        jobs[0]["country"] = 7
        r, _ = read({US: json_response(jobs)})
        self.assertEqual((r.jobs[0].location, r.jobs[0].country), ("", None))
        jobs[0]["categories"] = {"location": ["a"]}
        jobs[0]["country"] = ""
        r, _ = read({US: json_response(jobs)})
        self.assertEqual((r.jobs[0].location, r.jobs[0].country), ("", None))

    def test_created_at_is_milliseconds(self):
        jobs = fixture_jobs()[:1]
        jobs[0]["createdAt"] = 1760000000000
        r, _ = read({US: json_response(jobs)})
        self.assertEqual(r.jobs[0].posted_on, "2025-10-09")
        jobs[0]["createdAt"] = None
        r, _ = read({US: json_response(jobs)})
        self.assertIsNone(r.jobs[0].posted_on)

    def test_empty_board_is_empty(self):
        r, _ = read({US: ok(EMPTY)})
        self.assertEqual((r.result, r.jobs, r.validators), ("empty", (), Validators()))

    def test_html_or_wrong_shape_is_error(self):
        bodies = {
            "cloudflare": text_response("<html><title>Just a moment...</title><body>Checking</body></html>"),
            "other shape": json_response({"postings": []}),
            "not found document": json_response({"ok": False, "error": "Document not found"}),
            "empty object": json_response({}),
            "null": json_response(None),
            "string": json_response("[]"),
            "list of non objects": json_response([{"id": "a", "text": "x"}, 3]),
            "list of null": json_response([None]),
        }
        for name, resp in bodies.items():
            r, _ = read({US: resp})
            self.assertEqual(r.result, "error", name)
            self.assertEqual(r.jobs, (), name)

    def test_a_top_level_object_is_error(self):
        r, _ = read({US: json_response({"data": fixture_jobs()})})
        self.assertEqual((r.result, r.jobs, r.http_status), ("error", (), 200))

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
        for key in ("id", "text"):
            jobs = fixture_jobs()
            del jobs[1][key]
            r, _ = read({US: json_response(jobs)})
            self.assertEqual(r.result, "error", key)
            self.assertEqual(r.jobs, (), key)
        for key, value in (("id", None), ("text", ""), ("text", "  "), ("id", ""), ("text", 7), ("id", 7)):
            jobs = fixture_jobs()
            jobs[0][key] = value
            r, _ = read({US: json_response(jobs)})
            self.assertEqual(r.result, "error", f"{key}={value!r}")

    def test_duplicate_ids_are_kept_once(self):
        jobs = fixture_jobs()
        jobs.append(dict(jobs[0], text="Copy"))
        r, _ = read({US: json_response(jobs)})
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
        missing = json_response({"ok": False, "error": "Document not found"}, status=404)
        r, client = read({US: status_response(404), EU: missing})
        self.assertEqual(client.requests, [US, EU])
        self.assertEqual((r.result, r.jobs, r.http_status), ("error", (), 404))

    def test_the_eu_host_is_tried_only_for_a_404(self):
        for status in (403, 429, 500, 302):
            r, client = read({US: status_response(status)})
            self.assertEqual(client.requests, [US], status)
            self.assertEqual((r.result, r.http_status), ("error", status))

    def test_an_eu_failure_after_a_us_404_is_that_failure(self):
        r, _ = read({US: status_response(404), EU: status_response(500)})
        self.assertEqual((r.result, r.http_status), ("error", 500))
        r, _ = read({US: status_response(404), EU: text_response("<html>blocked</html>")})
        self.assertEqual(r.result, "error")
        r, _ = read({US: status_response(404), EU: json_response({"data": []})})
        self.assertEqual(r.result, "error")
        r, _ = read({US: status_response(404), EU: FetchError("timeout")})
        self.assertEqual((r.result, r.http_status), ("error", None))

    def test_a_us_fetch_error_does_not_try_the_eu_host(self):
        r, client = read({US: FetchError("network")})
        self.assertEqual((r.result, client.requests), ("error", [US]))

    def test_a_good_eu_board_that_is_empty_is_empty(self):
        r, _ = read({US: status_response(404), EU: ok(EMPTY)})
        self.assertEqual(r.result, "empty")
