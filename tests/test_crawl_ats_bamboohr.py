"""The BambooHR adapter (issue #21, Task 25)."""
import json
import unittest
from pathlib import Path

from outreach.crawl.ats import ADAPTERS, adapter_for, supported_ats
from outreach.crawl.ats.bamboohr import BambooHRAdapter
from outreach.crawl.base import JobRef, Validators, ids_fingerprint
from outreach.net import FetchError, Response
from tests.contracts import check_adapter
from tests.fakes import FakeClient, json_response, status_response, text_response

FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
URL = "https://acme.bamboohr.com/careers/list"
DETAIL = "https://acme.bamboohr.com/careers/41/detail"
LIST = json.loads((FIXTURES / "bamboohr_list.json").read_text())
DETAIL_JSON = json.loads((FIXTURES / "bamboohr_detail.json").read_text())
LOGIN = ("<!DOCTYPE html><html><head><title>BambooHR Login</title></head><body><form action='/login.php'>"
         "<input name='user'></form></body></html>")


def read(routes, token="acme", validators=Validators()):
    client = FakeClient(routes)
    return BambooHRAdapter(client).list(token, validators), client


def job(native_id="41"):
    return JobRef(native_id, "x", f"https://acme.bamboohr.com/careers/{native_id}", "", None, None, None)


def row(job_id="41", title="Engineer", city="Boston", state="Massachusetts", **extra):
    return {"id": job_id, "jobOpeningName": title, "location": {"city": city, "state": state},
            "atsLocation": {"country": None, "state": None, "province": None, "city": None}, "isRemote": None, **extra}


def answer(*rows, total=None):
    return json_response({"meta": {"totalCount": len(rows) if total is None else total}, "result": list(rows)})


class BambooListTests(unittest.TestCase):
    def test_check_adapter(self):
        client = FakeClient({URL: [json_response(LIST), json_response(LIST), status_response(500)]})
        check_adapter(self, BambooHRAdapter(client), "acme")

    def test_registered(self):
        self.assertIs(ADAPTERS["bamboohr"], BambooHRAdapter)
        self.assertIn("bamboohr", supported_ats())
        self.assertIsInstance(adapter_for("bamboohr", FakeClient({})), BambooHRAdapter)

    def test_list_ok_reads_the_careers_json(self):
        r, client = read({URL: json_response(LIST)})
        self.assertEqual((client.requests, client.unrouted), ([URL], []))
        self.assertEqual(client.calls[0][2].get("Accept"), "application/json")
        self.assertEqual(r.result, "ok")
        self.assertEqual(
            [(j.native_id, j.title, j.url, j.location, j.country, j.posted_on, j.body) for j in r.jobs],
            [("41", "Software Engineer & Tooling", "https://acme.bamboohr.com/careers/41", "Boston, Massachusetts",
              None, None, None),
             ("57", "Account Executive", "https://acme.bamboohr.com/careers/57", "Remote, Austin, Texas",
              "United States", None, None),
             ("63", "Data Engineer", "https://acme.bamboohr.com/careers/63", "", None, None, None)])
        self.assertEqual(r.validators, Validators(etag="", ids_fingerprint=ids_fingerprint(["41", "57", "63"])))

    def test_the_same_ids_are_unchanged_and_no_etag_is_sent(self):
        first, _ = read({URL: json_response(LIST)})
        again, client = read({URL: json_response(LIST, etag='"x"')}, validators=first.validators)
        self.assertEqual((again.result, again.jobs), ("unchanged", ()))
        self.assertNotIn("if-none-match", {k.lower() for k in client.calls[0][2]})
        r, _ = read({URL: status_response(304)}, validators=first.validators)
        self.assertEqual((r.result, r.http_status), ("error", 304))

    def test_anything_but_the_list_json_is_an_error_not_empty(self):
        for resp in (text_response(LOGIN), text_response(""), text_response("<html><title>Just a moment...</title>"),
                     json_response({"error": "nope"}), json_response([]), json_response(None),
                     json_response({"meta": {}, "result": []}), json_response({"meta": {"totalCount": 0}}),
                     json_response({"meta": {"totalCount": "2"}, "result": []}),
                     json_response({"meta": {"totalCount": True}, "result": []}),
                     json_response({"meta": {"totalCount": -1}, "result": []})):
            r, _ = read({URL: resp})
            self.assertEqual((r.result, r.jobs), ("error", ()), resp.body[:40])

    def test_no_jobs_is_empty(self):
        r, _ = read({URL: answer()})
        self.assertEqual((r.result, r.jobs, r.validators), ("empty", (), Validators()))

    def test_http_failures_are_errors(self):
        for status in (301, 302, 403, 404, 429, 500):
            r, _ = read({URL: status_response(status)})
            self.assertEqual((r.result, r.http_status), ("error", status), status)
        r, _ = read({URL: FetchError("network", "down")})
        self.assertEqual((r.result, r.http_status), ("error", None))

    def test_a_row_without_an_id_or_a_title_is_an_error(self):
        for bad in (row(title=""), row(title="  "), row(title=None), row(job_id="a b"), row(job_id="../x"),
                    row(job_id=""), row(job_id=None), row(job_id=True), "x", None):
            r, _ = read({URL: answer(row("1"), bad)})
            self.assertEqual((r.result, r.jobs), ("error", ()), bad)

    def test_a_count_that_does_not_match_is_an_error(self):
        for rows, total in (([row("1"), row("2")], 3), ([row("1"), row("2")], 1), ([row("1"), row("1")], 2),
                            ([], 4)):
            r, _ = read({URL: answer(*rows, total=total)})
            self.assertEqual((r.result, r.jobs), ("error", ()), (len(rows), total))

    def test_ids_as_numbers_and_place_forms(self):
        r, _ = read({URL: answer(row(7, "  Staff   Engineer ", city=" Austin ", state="TX"),
                                 row("8", "Designer", city=None, state=None, isRemote=True),
                                 row("9", "Analyst", city=None, state=None,
                                     atsLocation={"country": "Canada", "province": "Ontario", "city": "Toronto"}))})
        self.assertEqual([(j.native_id, j.title, j.location, j.country) for j in r.jobs],
                         [("7", "Staff Engineer", "Austin, TX", None), ("8", "Designer", "Remote", None),
                          ("9", "Analyst", "Toronto, Ontario", "Canada")])

    def test_bad_token_makes_no_request(self):
        for token in ("a/b", "A", "", "a b", "a%20b", "a.b", "a_b", "-a", "a-", "a@b", "a:80"):
            r, client = read({}, token=token)
            self.assertEqual((r.result, client.calls), ("error", []), token)


class BambooBodyTests(unittest.TestCase):
    def body(self, resp, token="acme", native_id="41"):
        client = FakeClient({f"https://{token}.bamboohr.com/careers/{native_id}/detail": resp})
        return BambooHRAdapter(client).body(token, job(native_id)), client

    def test_body_from_detail_json(self):
        text, client = self.body(json_response(DETAIL_JSON))
        self.assertEqual(text, "Build internal tools.\nPython\nWe sponsor visas")
        self.assertEqual((client.requests, client.calls[0][2].get("Accept")), ([DETAIL], "application/json"))

    def test_an_empty_description_is_none(self):
        data = json.loads(json.dumps(DETAIL_JSON))
        data["result"]["jobOpening"]["description"] = "<p> </p>"
        self.assertIsNone(self.body(json_response(data))[0])

    def test_error_and_wrong_bodies_raise(self):
        def with_opening(value):
            return json_response({"result": {"jobOpening": value}})
        for resp in (status_response(404), status_response(403), status_response(500), status_response(302),
                     status_response(304), FetchError("network", "down"), text_response(LOGIN),
                     text_response(""), json_response({"error": "not found"}), json_response({"result": None}),
                     json_response({"result": {}}), json_response([]), json_response(None),
                     with_opening(None), with_opening({}), with_opening({"description": None}),
                     with_opening({"description": 5}), with_opening("x")):
            with self.assertRaises((ValueError, FetchError), msg=resp):
                self.body(resp)

    def test_a_redirect_is_never_followed(self):
        client = FakeClient({DETAIL: Response(302, "", b"", {"location": "https://evil.example/x"})})
        with self.assertRaises(ValueError):
            BambooHRAdapter(client).body("acme", job())
        self.assertEqual(client.requests, [DETAIL])

    def test_a_detail_for_another_position_raises(self):
        for opening_id in ("57", 57, "", None):
            data = json.loads(json.dumps(DETAIL_JSON))
            data["result"]["jobOpening"]["id"] = opening_id
            with self.assertRaises(ValueError, msg=opening_id):
                self.body(json_response(data))
        data = json.loads(json.dumps(DETAIL_JSON))
        data["result"]["jobOpening"]["id"] = 41
        self.assertIsNotNone(self.body(json_response(data))[0])

    def test_with_no_id_the_share_link_names_the_position(self):
        self.assertNotIn("id", DETAIL_JSON["result"]["jobOpening"])  # the live answer carries none (2026-11-12)
        for link in ("https://acme.bamboohr.com/careers/57", "", None, "https://acme.bamboohr.com/careers/410"):
            data = json.loads(json.dumps(DETAIL_JSON))
            data["result"]["jobOpening"]["jobOpeningShareUrl"] = link
            with self.assertRaises(ValueError, msg=link):
                self.body(json_response(data))

    def test_the_request_is_built_from_the_token_and_id_never_from_the_posting_url(self):
        client = FakeClient({DETAIL: json_response(DETAIL_JSON)})
        evil = JobRef("41", "x", "https://evil.example/careers/41", "", None, None, None)
        BambooHRAdapter(client).body("acme", evil)
        self.assertEqual(client.requests, [DETAIL])

    def test_a_bad_token_or_id_makes_no_request(self):
        for token, native_id in (("a.b", "41"), ("A", "41"), ("", "41"), ("acme", "../x"), ("acme", "4 1"),
                                 ("acme", ""), ("acme", "41/detail?x=1"), ("acme", "41#")):
            client = FakeClient({})
            with self.assertRaises(ValueError, msg=(token, native_id)):
                BambooHRAdapter(client).body(token, job(native_id))
            self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
