"""The Ashby adapter (issue #21, Task 6)."""
import json
import unittest
from pathlib import Path

from outreach.crawl.ats import ADAPTERS, adapter_for, supported_ats
from outreach.crawl.ats.ashby import AshbyAdapter
from outreach.crawl.base import Validators, ids_fingerprint
from outreach.net import FetchError, Response
from tests.contracts import check_adapter
from tests.fakes import FakeClient, json_response, status_response, text_response

FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
URL = "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"
JOBS = (FIXTURES / "ashby_jobs.json").read_bytes()
EMPTY = (FIXTURES / "ashby_empty.json").read_bytes()
ETAG = 'W/"2fd4e1c67a2d28fced849ee1bb76e739"'
ID1, ID2, ID3 = ("b7a1c3e2-5d44-4f0a-8a31-0c2e9d7a000%d" % n for n in (1, 2, 3))


def ok(body=JOBS, etag=ETAG):
    return Response(200, "", body, {"etag": etag} if etag else {})


def fixture():
    return json.loads(JOBS)


def board(jobs):
    return json_response({"apiVersion": "1", "jobs": jobs})


def read(routes, token="acme", validators=Validators()):
    client = FakeClient(routes)
    result = AshbyAdapter(client).list(token, validators)
    return result, client


class AshbyTests(unittest.TestCase):
    def test_check_adapter(self):
        client = FakeClient({URL: [ok(), status_response(304), status_response(500)]})
        check_adapter(self, AshbyAdapter(client), "acme")

    def test_registered(self):
        self.assertIs(ADAPTERS["ashby"], AshbyAdapter)
        self.assertIn("ashby", supported_ats())
        self.assertIsInstance(adapter_for("ashby", FakeClient({})), AshbyAdapter)

    def test_list_ok_parses_jobs(self):
        r, client = read({URL: ok()})
        self.assertEqual(client.requests, [URL])
        self.assertTrue(client.requests[0].endswith("?includeCompensation=true"))
        self.assertEqual(r.result, "ok")
        self.assertEqual(
            [(j.native_id, j.title, j.location, j.url, j.posted_on, j.country) for j in r.jobs],
            [(ID1, "Software Engineer, New Grad", "San Francisco, CA", "https://jobs.ashbyhq.com/acme/" + ID1,
              "2026-09-15", "United States"),
             (ID2, "Account Executive", "Remote - US", "https://jobs.ashbyhq.com/acme/" + ID2, "2026-10-02", None)])
        self.assertEqual(r.validators, Validators(etag=ETAG, ids_fingerprint=ids_fingerprint([ID1, ID2])))

    def test_unlisted_job_is_skipped(self):
        r, _ = read({URL: ok()})
        self.assertNotIn(ID3, [j.native_id for j in r.jobs])
        self.assertEqual(len(r.jobs), 2)
        self.assertNotEqual(r.validators.ids_fingerprint, ids_fingerprint([ID1, ID2, ID3]))

    def test_only_unlisted_jobs_is_empty(self):
        jobs = [dict(j, isListed=False) for j in fixture()["jobs"]]
        r, _ = read({URL: board(jobs)})
        self.assertEqual((r.result, r.jobs, r.validators), ("empty", (), Validators()))

    def test_a_job_without_isListed_is_public(self):
        jobs = fixture()["jobs"][:1]
        del jobs[0]["isListed"]
        r, _ = read({URL: board(jobs)})
        self.assertEqual([j.native_id for j in r.jobs], [ID1])

    def test_body_is_plain_text(self):
        r, _ = read({URL: ok()})
        self.assertEqual(r.jobs[0].body, "We are hiring a new grad engineer.\n\nBuild things\nShip & learn")
        self.assertEqual(r.jobs[1].body, "Own the full sales cycle.")
        for j in r.jobs:
            self.assertNotIn("<", j.body)
            self.assertNotIn("&amp;", j.body)
        self.assertIsNone(AshbyAdapter(FakeClient({})).body("acme", r.jobs[0]))

    def test_a_job_with_no_description_has_no_body(self):
        jobs = fixture()["jobs"][:1]
        del jobs[0]["descriptionPlain"], jobs[0]["descriptionHtml"]
        r, _ = read({URL: board(jobs)})
        self.assertIsNone(r.jobs[0].body)

    def test_odd_location_country_and_date_fields(self):
        jobs = fixture()["jobs"][:1]
        jobs[0]["location"] = None
        jobs[0]["address"] = {"postalAddress": None}
        jobs[0]["publishedAt"] = None
        jobs[0]["updatedAt"] = "2026-10-05T01:02:03.000+00:00"
        r, _ = read({URL: board(jobs)})
        self.assertEqual((r.jobs[0].location, r.jobs[0].country, r.jobs[0].posted_on), ("", None, "2026-10-05"))
        for address in (None, "x", {}, {"postalAddress": {"addressCountry": 5}}, {"postalAddress": {"addressCountry": ""}}):
            jobs[0]["address"] = address
            r, _ = read({URL: board(jobs)})
            self.assertIsNone(r.jobs[0].country, address)

    def test_empty_board_is_empty(self):
        r, _ = read({URL: ok(EMPTY)})
        self.assertEqual((r.result, r.jobs, r.validators), ("empty", (), Validators()))

    def test_html_or_wrong_shape_is_error(self):
        bodies = {
            "cloudflare": text_response("<html><title>Just a moment...</title><body>Checking</body></html>"),
            "other shape": json_response({"offers": [], "meta": {}}),
            "missing key": json_response({"apiVersion": "1"}),
            "null list": json_response({"jobs": None}),
            "object list": json_response({"jobs": {"a": 1}}),
            "list top level": json_response([{"id": "a", "title": "x"}]),
            "null": json_response(None),
            "list of non objects": json_response({"jobs": ["a", 3]}),
        }
        for name, resp in bodies.items():
            r, _ = read({URL: resp})
            self.assertEqual(r.result, "error", name)
            self.assertEqual(r.jobs, (), name)

    def test_http_failures_are_errors(self):
        for status in (404, 403, 429, 500, 302):
            r, client = read({URL: status_response(status)})
            self.assertEqual((r.result, r.http_status), ("error", status), status)
            self.assertEqual(client.requests, [URL], "no EU retry for Ashby")
        r, _ = read({URL: FetchError("network", "down")})
        self.assertEqual((r.result, r.http_status), ("error", None))

    def test_validators(self):
        r, client = read({URL: ok()}, validators=Validators())
        self.assertNotIn("If-None-Match", client.calls[0][2])
        again, client = read({URL: status_response(304)}, validators=r.validators)
        self.assertEqual(client.calls[0][2]["If-None-Match"], ETAG)
        self.assertEqual((again.result, again.jobs, again.validators), ("unchanged", (), r.validators))

    def test_a_200_with_the_same_ids_is_ok_not_unchanged(self):
        r, _ = read({URL: ok()})
        again, _ = read({URL: ok(etag='W/"new"')}, validators=r.validators)
        self.assertEqual(again.result, "ok")
        self.assertEqual(again.validators.etag, 'W/"new"')

    def test_listed_job_without_id_or_title_is_error(self):
        for key in ("id", "title"):
            jobs = fixture()["jobs"]
            del jobs[1][key]
            r, _ = read({URL: board(jobs)})
            self.assertEqual(r.result, "error", key)
            self.assertEqual(r.jobs, (), key)
        for key, value in (("id", None), ("title", ""), ("title", "  "), ("id", ""), ("title", 7), ("id", 7)):
            jobs = fixture()["jobs"]
            jobs[0][key] = value
            r, _ = read({URL: board(jobs)})
            self.assertEqual(r.result, "error", f"{key}={value!r}")

    def test_an_unlisted_job_may_lack_an_id_or_title(self):
        jobs = fixture()["jobs"]
        del jobs[2]["id"], jobs[2]["title"]
        r, _ = read({URL: board(jobs)})
        self.assertEqual([j.native_id for j in r.jobs], [ID1, ID2])

    def test_a_job_that_is_not_an_object_is_error_even_beside_unlisted_ones(self):
        r, _ = read({URL: board([fixture()["jobs"][2], None])})
        self.assertEqual(r.result, "error")

    def test_duplicate_ids_are_kept_once(self):
        jobs = fixture()["jobs"]
        jobs.append(dict(jobs[0], title="Copy"))
        r, _ = read({URL: board(jobs)})
        self.assertEqual([j.native_id for j in r.jobs], [ID1, ID2])
        self.assertEqual(r.jobs[0].title, "Software Engineer, New Grad")

    def test_bad_token_makes_no_request(self):
        for token in ("a/b", "A", "", "a b", "a%20b"):
            r, client = read({}, token=token)
            self.assertEqual((r.result, client.calls), ("error", []), token)
