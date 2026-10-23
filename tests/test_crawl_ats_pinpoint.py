"""The Pinpoint adapter (issue #21, Task 22)."""
import json
import unittest
from pathlib import Path

from outreach.crawl.ats import ADAPTERS, adapter_for, supported_ats
from outreach.crawl.ats.pinpoint import PinpointAdapter
from outreach.crawl.base import JobRef, Validators, ids_fingerprint
from outreach.net import FetchError, Response
from tests.contracts import check_adapter
from tests.fakes import FakeClient, json_response, status_response, text_response

FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
URL = "https://acme.pinpointhq.com/postings.json"
LIST = (FIXTURES / "pinpoint_list.json").read_bytes()
EMPTY = (FIXTURES / "pinpoint_empty.json").read_bytes()
ETAG = 'W/"c0ffee"'
IDS = ["2410001", "2410002", "2410003"]
BASE = "https://acme.pinpointhq.com/en/postings/5a7f0c1e-2b3d-4e5f-8a9b-0c1d2e3f4a0"


class FollowClient(FakeClient):
    def __init__(self, routes):
        super().__init__(routes)
        self.follow = []

    def get(self, url, headers=None, *, follow_redirects=False):
        self.follow.append(follow_redirects)
        return super().get(url, headers, follow_redirects=follow_redirects)


def ok(body=LIST, etag=ETAG):
    return Response(200, "", body, {"etag": etag} if etag else {})


def data():
    return json.loads(LIST)["data"]


def read(routes, token="acme", validators=Validators()):
    client = FollowClient(routes)
    return PinpointAdapter(client).list(token, validators), client


class PinpointTests(unittest.TestCase):
    def test_check_adapter(self):
        client = FakeClient({URL: [ok(), status_response(304), status_response(500)]})
        check_adapter(self, PinpointAdapter(client), "acme")

    def test_registered(self):
        self.assertIs(ADAPTERS["pinpoint"], PinpointAdapter)
        self.assertIn("pinpoint", supported_ats())
        self.assertIsInstance(adapter_for("pinpoint", FakeClient({})), PinpointAdapter)

    def test_list_ok_parses_jobs_and_bodies_without_following_redirects(self):
        r, client = read({URL: ok()})
        self.assertEqual((client.requests, client.follow), ([URL], [False]))
        self.assertEqual(r.result, "ok")
        self.assertEqual(
            [(j.native_id, j.title, j.url, j.location, j.country, j.posted_on) for j in r.jobs],
            [(IDS[0], "Software Engineer, New Grad", BASE + "1", "San Francisco, CA", None, "2026-09-21"),
             (IDS[1], "Account Executive", BASE + "2", "London, England", None, None),
             (IDS[2], "Data Engineer", BASE + "3", "", None, None)])
        self.assertEqual(r.jobs[0].body, "We are hiring a new grad engineer.\nBuild services\nShip & learn\n"
                                         "We sponsor visas for the right person.")
        self.assertEqual(r.jobs[1].body, "Own the full sales cycle.")
        self.assertIsNone(r.jobs[2].body)
        self.assertEqual(r.validators, Validators(etag=ETAG, ids_fingerprint=ids_fingerprint(IDS)))

    def test_body_method_returns_none(self):
        r, _ = read({URL: ok()})
        client = FakeClient({})
        self.assertIsNone(PinpointAdapter(client).body("acme", r.jobs[0]))
        self.assertEqual(client.calls, [])

    def test_empty_data_is_empty(self):
        r, _ = read({URL: ok(EMPTY)})
        self.assertEqual((r.result, r.jobs, r.validators), ("empty", (), Validators()))

    def test_pinpoint_data_not_a_list_is_error(self):
        for name, payload in {"object": {"data": {"a": 1}}, "null": {"data": None}, "string": {"data": "[]"},
                              "missing": {"postings": data()}, "empty object": {}, "list": data(),
                              "empty list": [], "none": None, "number": 3}.items():
            r, _ = read({URL: json_response(payload)})
            self.assertEqual((r.result, r.jobs), ("error", ()), name)

    def test_html_or_a_redirect_is_error(self):
        r, _ = read({URL: text_response("<html><title>Just a moment...</title></html>")})
        self.assertEqual((r.result, r.jobs), ("error", ()))
        for status in (301, 302, 307):
            r, client = read({URL: Response(status, "", b"", {"location": "https://www.pinpointhq.com/"})})
            self.assertEqual((r.result, r.http_status), ("error", status))
            self.assertEqual(client.follow, [False])

    def test_http_failures_are_errors(self):
        for status in (403, 404, 429, 500):
            r, _ = read({URL: status_response(status)})
            self.assertEqual((r.result, r.http_status), ("error", status), status)
        r, _ = read({URL: FetchError("network", "down")})
        self.assertEqual((r.result, r.http_status), ("error", None))

    def test_validators_and_304_only_when_if_none_match_was_sent(self):
        first, client = read({URL: ok()})
        self.assertNotIn("If-None-Match", client.calls[0][2])
        again, client = read({URL: status_response(304)}, validators=first.validators)
        self.assertEqual(client.calls[0][2]["If-None-Match"], ETAG)
        self.assertEqual((again.result, again.jobs, again.validators), ("unchanged", (), first.validators))
        bare, client = read({URL: status_response(304)}, validators=Validators(ids_fingerprint="x"))
        self.assertNotIn("If-None-Match", client.calls[0][2])
        self.assertEqual((bare.result, bare.http_status), ("error", 304))

    def test_a_200_with_the_same_ids_is_ok_not_unchanged(self):
        first, _ = read({URL: ok()})
        again, _ = read({URL: ok(etag='W/"new"')}, validators=first.validators)
        self.assertEqual((again.result, again.validators.etag), ("ok", 'W/"new"'))

    def test_a_posting_without_an_id_or_title_is_an_error(self):
        for key, value in (("id", None), ("id", ""), ("id", "  "), ("id", True), ("id", ["1"]), ("title", ""),
                           ("title", None), ("title", 7), ("title", "  ")):
            payload = data()
            payload[1][key] = value
            r, _ = read({URL: json_response({"data": payload})})
            self.assertEqual((r.result, r.jobs), ("error", ()), f"{key}={value!r}")
        for bad in (None, "x", 3, []):
            r, _ = read({URL: json_response({"data": data() + [bad]})})
            self.assertEqual(r.result, "error", bad)

    def test_a_numeric_id_is_text(self):
        r, _ = read({URL: json_response({"data": [{"id": 77, "title": "Engineer"}]})})
        self.assertEqual(r.jobs[0].native_id, "77")

    def test_location_forms(self):
        cases = [({"name": " Remote "}, "Remote"), ({"city": "Leeds", "province": "England"}, "Leeds, England"),
                 ({"city": "Leeds"}, "Leeds"), ({"province": "England"}, "England"), ({"name": 3, "city": 4}, ""),
                 ({}, ""), (None, ""), ("Leeds", ""), (["x"], "")]
        for value, expected in cases:
            payload = data()[:1]
            payload[0]["location"] = value
            r, _ = read({URL: json_response({"data": payload})})
            self.assertEqual(r.jobs[0].location, expected, value)

    def test_first_published_at_is_a_real_date_or_none(self):
        for value, expected in (("2026-09-21T10:00:00.000Z", "2026-09-21"), ("2026-09-21", "2026-09-21"),
                                ("2026-02-30", None), ("30+ days ago", None), ("", None), (None, None), (7, None)):
            payload = data()[:1]
            payload[0]["first_published_at"] = value
            r, _ = read({URL: json_response({"data": payload})})
            self.assertEqual(r.jobs[0].posted_on, expected, value)
        payload = data()[:1]
        del payload[0]["first_published_at"]
        r, _ = read({URL: json_response({"data": payload})})
        self.assertIsNone(r.jobs[0].posted_on)

    def test_the_posting_url_is_kept_only_as_a_web_address(self):
        fallback = "https://acme.pinpointhq.com/en/postings/2410001"
        for value, expected in ((BASE + "1", BASE + "1"), ("javascript:alert(1)", fallback), ("", fallback),
                                (None, fallback), (7, fallback)):
            payload = data()[:1]
            payload[0]["url"] = value
            r, _ = read({URL: json_response({"data": payload})})
            self.assertEqual(r.jobs[0].url, expected, value)
        payload = data()[:1]
        del payload[0]["url"]
        r, _ = read({URL: json_response({"data": payload})})
        self.assertEqual(r.jobs[0].url, fallback)
        r, _ = read({URL: json_response({"data": [{"id": "a b/../c", "title": "E"}]})})
        self.assertEqual(r.jobs[0].url, "https://acme.pinpointhq.com/en/postings/a%20b%2F..%2Fc")

    def test_description_forms(self):
        cases = [("&lt;p&gt;Plain &amp;amp; simple&lt;/p&gt;", "Plain & simple"), ("<p>Already markup</p>", "Already markup"),
                 ("Just words", "Just words"), ("", None), ("   ", None), ("&lt;p&gt; &lt;/p&gt;", None), (None, None),
                 (5, None), (["x"], None)]
        for value, expected in cases:
            payload = data()[:1]
            payload[0]["description"] = value
            r, _ = read({URL: json_response({"data": payload})})
            self.assertEqual(r.jobs[0].body, expected, value)

    def test_duplicate_ids_are_kept_once(self):
        payload = data()
        payload.append(dict(payload[0], title="Copy"))
        r, _ = read({URL: json_response({"data": payload})})
        self.assertEqual([j.native_id for j in r.jobs], IDS)
        self.assertEqual(r.jobs[0].title, "Software Engineer, New Grad")

    def test_bad_token_makes_no_request(self):
        for token in ("a/b", "A", "", "a b", "a%20b", "a.b", "a_b", "-a", "a-", "a@b", "a:80"):
            r, client = read({}, token=token)
            self.assertEqual((r.result, client.calls), ("error", []), token)


if __name__ == "__main__":
    unittest.main()
