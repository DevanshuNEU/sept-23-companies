"""The Rippling adapter (issue #21, Task 21).

The fixtures are hand-written, with their shape from the ats-scrapers Rippling scraper (the list is a top-level list;
the detail has `description.company` and `description.role`).
"""
import json
import unittest
from pathlib import Path

from outreach.crawl.ats import ADAPTERS, adapter_for, supported_ats
from outreach.crawl.ats.rippling import RipplingAdapter
from outreach.crawl.base import JobRef, Validators, ids_fingerprint
from outreach.net import FetchError
from tests.contracts import check_adapter
from tests.fakes import FakeClient, json_response, status_response, text_response

FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
URL = "https://api.rippling.com/platform/api/ats/v1/board/acme/jobs"
LIST = (FIXTURES / "rippling_list.json").read_bytes()
DETAIL = (FIXTURES / "rippling_detail.json").read_bytes()
IDS = [f"9d3b1c52-6a0e-4b9e-8f10-1a2b3c4d5e0{n}" for n in (1, 2, 3)]


def jobs():
    return json.loads(LIST)


def read(routes, token="acme", validators=Validators()):
    client = FakeClient(routes)
    return RipplingAdapter(client).list(token, validators), client


class RipplingListTests(unittest.TestCase):
    def test_check_adapter(self):
        client = FakeClient({URL: [LIST, LIST, status_response(500)]})
        check_adapter(self, RipplingAdapter(client), "acme")

    def test_registered(self):
        self.assertIs(ADAPTERS["rippling"], RipplingAdapter)
        self.assertIn("rippling", supported_ats())
        self.assertIsInstance(adapter_for("rippling", FakeClient({})), RipplingAdapter)

    def test_list_ok_parses_jobs(self):
        r, client = read({URL: LIST})
        self.assertEqual(client.requests, [URL])
        self.assertEqual(r.result, "ok")
        self.assertEqual(
            [(j.native_id, j.title, j.url, j.location, j.country, j.posted_on, j.body) for j in r.jobs],
            [(IDS[0], "Software Engineer, New Grad", "https://ats.rippling.com/acme/jobs/" + IDS[0],
              "San Francisco, CA", None, None, None),
             (IDS[1], "Account Executive", "https://ats.rippling.com/acme/jobs/" + IDS[1],
              "Remote (United States)", None, None, None),
             (IDS[2], "Data Engineer", "https://ats.rippling.com/acme/jobs/" + IDS[2], "", None, None, None)])
        self.assertEqual(r.validators, Validators(etag="", ids_fingerprint=ids_fingerprint(IDS)))

    def test_object_with_items_and_list_forms_both_parse(self):
        forms = {"list": jobs(), "items": {"items": jobs()}, "jobs": {"jobs": jobs()},
                 "items null, jobs list": {"items": None, "jobs": jobs()}}
        for name, payload in forms.items():
            r, _ = read({URL: json_response(payload)})
            self.assertEqual((r.result, [j.native_id for j in r.jobs]), ("ok", IDS), name)

    def test_object_without_items_or_jobs_is_error(self):
        for name, payload in {"empty object": {}, "offers": {"offers": jobs()}, "null": {"items": None},
                              "items object": {"items": {"a": 1}}, "jobs string": {"jobs": "[]"},
                              "both wrong": {"items": 3, "jobs": None}, "string": "[]", "number": 3,
                              "null body": None}.items():
            r, _ = read({URL: json_response(payload)})
            self.assertEqual((r.result, r.jobs), ("error", ()), name)

    def test_empty_list_forms_are_empty(self):
        for payload in ([], {"items": []}, {"jobs": []}):
            r, _ = read({URL: json_response(payload)})
            self.assertEqual((r.result, r.jobs, r.validators), ("empty", (), Validators()), payload)

    def test_html_is_error(self):
        r, _ = read({URL: text_response("<html><title>Just a moment...</title></html>")})
        self.assertEqual((r.result, r.jobs), ("error", ()))

    def test_http_failures_are_errors(self):
        for status in (403, 404, 429, 500, 302):
            r, _ = read({URL: status_response(status)})
            self.assertEqual((r.result, r.http_status), ("error", status), status)
        r, _ = read({URL: FetchError("network", "down")})
        self.assertEqual((r.result, r.http_status), ("error", None))

    def test_no_etag_is_sent_and_a_304_is_an_error(self):
        first, _ = read({URL: LIST})
        again, client = read({URL: LIST}, validators=first.validators)
        self.assertEqual((again.result, again.jobs, again.validators), ("unchanged", (), first.validators))
        self.assertNotIn("if-none-match", {k.lower() for _m, _u, h, _b in client.calls for k in h})
        r, _ = read({URL: status_response(304)}, validators=first.validators)
        self.assertEqual((r.result, r.http_status), ("error", 304))

    def test_a_posting_without_a_usable_id_or_title_is_an_error(self):
        for key, value in (("id", None), ("id", ""), ("id", "a b"), ("id", "a/b"), ("id", "../x"), ("id", True),
                           ("id", 7.5), ("name", ""), ("name", None), ("name", 7), ("name", "  ")):
            payload = jobs()
            payload[0][key] = value
            if key == "name":
                payload[0].pop("title", None)
            r, _ = read({URL: json_response(payload)})
            self.assertEqual((r.result, r.jobs), ("error", ()), f"{key}={value!r}")
        for bad in (None, "x", 3, []):
            r, _ = read({URL: json_response(jobs() + [bad])})
            self.assertEqual(r.result, "error", bad)
        payload = jobs()
        del payload[1]["id"]
        r, _ = read({URL: json_response(payload)})
        self.assertEqual(r.result, "error")

    def test_the_id_is_the_uuid_first_then_id_and_the_title_falls_back_to_title(self):
        r, _ = read({URL: json_response([{"uuid": "abc-1", "id": "other-9", "title": "Engineer"}])})
        self.assertEqual((r.jobs[0].native_id, r.jobs[0].title), ("abc-1", "Engineer"))
        self.assertEqual(r.jobs[0].url, "https://ats.rippling.com/acme/jobs/abc-1")
        r, _ = read({URL: json_response([{"id": "other-9", "name": "Engineer"}])})
        self.assertEqual(r.jobs[0].native_id, "other-9")
        # A uuid that is present but unsafe is an error, not a reason to fall back to the id.
        r, _ = read({URL: json_response([{"uuid": "a b", "id": "other-9", "name": "Engineer"}])})
        self.assertEqual(r.result, "error")

    def test_an_object_that_says_more_postings_exist_is_an_error(self):
        two = jobs()[:2]
        for name, extra in {"totalItems": {"totalItems": 3}, "total": {"total": 40}, "count": {"count": 3},
                            "totalPages": {"totalPages": 2}, "totalPages many": {"totalPages": 9},
                            "hasMore": {"hasMore": True}, "next": {"next": "https://api.rippling.com/x?page=2"},
                            "next cursor": {"next": "abc"}}.items():
            for key in ("items", "jobs"):
                r, _ = read({URL: json_response(dict({key: two}, **extra))})
                self.assertEqual((r.result, r.jobs), ("error", ()), f"{key} {name}")

    def test_an_object_whose_counts_match_is_ok(self):
        two = jobs()[:2]
        for extra in ({"totalItems": 2, "total": 2, "count": 2, "totalPages": 1, "hasMore": False, "next": None},
                      {"totalItems": 1, "next": ""}, {"totalPages": 0}, {"total": "many", "next": []}):
            r, _ = read({URL: json_response(dict({"items": two}, **extra))})
            self.assertEqual((r.result, len(r.jobs)), ("ok", 2), extra)

    def test_a_numeric_id_is_text(self):
        r, _ = read({URL: json_response([{"id": 42, "name": "Engineer"}])})
        self.assertEqual(r.jobs[0].native_id, "42")

    def test_the_url_field_is_used_only_when_it_is_a_web_address(self):
        for value, expected in (("https://careers.example.com/j/1", "https://careers.example.com/j/1"),
                                ("javascript:alert(1)", "https://ats.rippling.com/acme/jobs/1"),
                                ("", "https://ats.rippling.com/acme/jobs/1"),
                                (None, "https://ats.rippling.com/acme/jobs/1"),
                                (7, "https://ats.rippling.com/acme/jobs/1")):
            r, _ = read({URL: json_response([{"id": "1", "name": "E", "url": value}])})
            self.assertEqual(r.jobs[0].url, expected, value)
        r, _ = read({URL: json_response([{"id": "1", "name": "E", "hostedUrl": "https://x.example.com/1"}])})
        self.assertEqual(r.jobs[0].url, "https://x.example.com/1")

    def test_location_forms(self):
        for value, expected in (("Boston, MA", "Boston, MA"), ({"label": " Remote "}, "Remote"),
                                ({"displayName": "Austin"}, "Austin"), ({"label": 3}, ""), (["x"], ""), (None, ""),
                                ("  ", ""), ({}, "")):
            r, _ = read({URL: json_response([{"id": "1", "name": "E", "workLocation": value}])})
            self.assertEqual(r.jobs[0].location, expected, value)

    def test_duplicate_ids_are_kept_once(self):
        payload = jobs()
        payload.append(dict(payload[0], name="Copy"))
        r, _ = read({URL: json_response(payload)})
        self.assertEqual([j.native_id for j in r.jobs], IDS)
        self.assertEqual(r.jobs[0].title, "Software Engineer, New Grad")

    def test_bad_token_makes_no_request(self):
        for token in ("a/b", "A", "", "a b", "a%20b", "../x"):
            r, client = read({}, token=token)
            self.assertEqual((r.result, client.calls), ("error", []), token)


class RipplingBodyTests(unittest.TestCase):
    DETAIL_URL = URL + "/" + IDS[0]

    def job(self, job_id=IDS[0]):
        return JobRef(job_id, "x", "", "", None, None, None)

    def test_body_puts_the_role_before_the_company(self):
        client = FakeClient({self.DETAIL_URL: DETAIL})
        text = RipplingAdapter(client).body("acme", self.job())
        self.assertEqual(client.requests, [self.DETAIL_URL])
        self.assertEqual(text, "You will build services.\nShip & learn\nWe sponsor visas for the right person.\n"
                               "Acme builds warehouse robots.")
        self.assertNotIn("<", text)

    def test_body_with_one_part_or_none(self):
        one = {"description": {"company": None, "role": "<p>Only the role.</p>"}}
        client = FakeClient({self.DETAIL_URL: json_response(one)})
        self.assertEqual(RipplingAdapter(client).body("acme", self.job()), "Only the role.")
        none = {"description": {"company": "", "role": " "}}
        client = FakeClient({self.DETAIL_URL: json_response(none)})
        self.assertIsNone(RipplingAdapter(client).body("acme", self.job()))

    def test_a_failed_or_wrong_body_raises(self):
        for resp in (status_response(500), status_response(404), status_response(302), status_response(304),
                     text_response("<html>blocked</html>"), json_response({}), json_response([]),
                     json_response(None), json_response({"description": None}),
                     json_response({"description": "<p>text</p>"}), json_response({"description": {}}),
                     json_response({"description": {"company": 3, "role": None}}), FetchError("timeout")):
            client = FakeClient({self.DETAIL_URL: resp})
            with self.assertRaises((ValueError, FetchError), msg=resp):
                RipplingAdapter(client).body("acme", self.job())

    def test_a_bad_id_or_token_makes_no_request(self):
        for token, job_id in (("acme", ""), ("acme", "a b"), ("acme", "../x"), ("acme", "a/b"), ("acme", "1?x=2"),
                              ("a/b", "1"), ("A", "1")):
            client = FakeClient({})
            with self.assertRaises(ValueError):
                RipplingAdapter(client).body(token, self.job(job_id))
            self.assertEqual(client.calls, [], (token, job_id))


if __name__ == "__main__":
    unittest.main()
