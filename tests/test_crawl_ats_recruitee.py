"""The Recruitee adapter (issue #21, Task 24)."""
import copy
import json
import unittest
from pathlib import Path

from outreach.crawl.ats import ADAPTERS, adapter_for, supported_ats
from outreach.crawl.ats.recruitee import RecruiteeAdapter
from outreach.crawl.base import JobRef, Validators, ids_fingerprint
from outreach.net import FetchError
from tests.contracts import check_adapter
from tests.fakes import FakeClient, json_response, status_response, text_response

FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
URL = "https://acme.recruitee.com/api/offers/"
LIST = json.loads((FIXTURES / "recruitee_list.json").read_text())
NULL = (FIXTURES / "recruitee_null.json").read_bytes()
IDS = ["421001", "421002", "421003"]


def payload(n=3):
    return copy.deepcopy({"offers": LIST["offers"][:n]})


def read(routes, token="acme", validators=Validators()):
    client = FakeClient(routes)
    return RecruiteeAdapter(client).list(token, validators), client


def one(**changes):
    data = payload(1)
    data["offers"][0].update(changes)
    r, _ = read({URL: json_response(data)})
    return r.jobs[0]


class RecruiteeTests(unittest.TestCase):
    def test_check_adapter(self):
        client = FakeClient({URL: [json_response(payload()), json_response(payload()), status_response(500)]})
        check_adapter(self, RecruiteeAdapter(client), "acme")

    def test_registered(self):
        self.assertIs(ADAPTERS["recruitee"], RecruiteeAdapter)
        self.assertIn("recruitee", supported_ats())
        self.assertIsInstance(adapter_for("recruitee", FakeClient({})), RecruiteeAdapter)

    def test_list_ok_parses_offers(self):
        r, client = read({URL: json_response(payload())})
        self.assertEqual((client.requests, client.unrouted), ([URL], []))
        self.assertEqual(r.result, "ok")
        self.assertEqual(
            [(j.native_id, j.title, j.url, j.location, j.country, j.posted_on) for j in r.jobs],
            [("421001", "Software Engineer, Backend", "https://acme.recruitee.com/o/software-engineer-backend",
              "Amsterdam, Netherlands", "NL", "2026-09-14"),
             ("421002", "Account Executive", "https://acme.recruitee.com/o/account-executive", "Austin, United States",
              "US", "2026-10-03"),
             ("421003", "Data Engineer", "https://acme.recruitee.com/o/data-engineer", "Remote", None, None)])
        self.assertEqual(r.jobs[0].body, "Build the core API.\nGo and Postgres\n\n3 years of backend work.\n"
                                         "We sponsor visas for the right person.")
        self.assertEqual(r.jobs[1].body, "Sell to engineers.")
        self.assertEqual(r.jobs[2].body, "SQL.")
        self.assertEqual(r.validators, Validators(etag="", ids_fingerprint=ids_fingerprint(IDS)))

    def test_no_etag_is_asked_or_kept(self):
        first, client = read({URL: json_response(payload(), etag='W/"x"')})
        self.assertEqual(first.validators.etag, "")
        _, client = read({URL: json_response(payload())}, validators=Validators(etag='W/"x"', ids_fingerprint="y"))
        self.assertNotIn("if-none-match", {k.lower() for k in client.calls[0][2]})
        r, _ = read({URL: status_response(304)}, validators=Validators(etag='W/"x"'))
        self.assertEqual((r.result, r.http_status), ("error", 304))

    def test_offers_null_is_error(self):
        for body in (NULL, b'{}', b'{"offers": {}}', b'{"offers": "none"}', b'{"offer": []}', b'[]', b'null'):
            r, _ = read({URL: body})
            self.assertEqual((r.result, r.jobs), ("error", ()), body)

    def test_empty_offers_list_is_empty(self):
        r, _ = read({URL: json_response({"offers": []})})
        self.assertEqual((r.result, r.jobs, r.validators), ("empty", (), Validators()))

    def test_html_and_http_failures_are_errors(self):
        r, _ = read({URL: text_response("<html><title>Recruitee</title></html>")})
        self.assertEqual((r.result, r.jobs), ("error", ()))
        for status in (301, 302, 403, 404, 429, 500):
            r, _ = read({URL: status_response(status)})
            self.assertEqual((r.result, r.http_status), ("error", status), status)
        r, _ = read({URL: FetchError("network", "down")})
        self.assertEqual((r.result, r.http_status), ("error", None))

    def test_raw_bytes_differ_but_ids_equal_is_unchanged(self):
        first, _ = read({URL: json_response(payload())})
        noisy = payload()
        for n, offer in enumerate(noisy["offers"]):
            offer.update(updated_at=f"2026-10-2{n} 00:00:00 UTC", views=1000 + n, candidates_count=99, slug="x-" + str(n))
        self.assertNotEqual(json.dumps(noisy), json.dumps(payload()))
        again, _ = read({URL: json_response(noisy)}, validators=first.validators)
        self.assertEqual((again.result, again.jobs, again.validators), ("unchanged", (), first.validators))
        # Order does not matter, and a changed id set is ok.
        noisy["offers"].reverse()
        self.assertEqual(read({URL: json_response(noisy)}, validators=first.validators)[0].result, "unchanged")
        noisy["offers"][0]["id"] = 999
        self.assertEqual(read({URL: json_response(noisy)}, validators=first.validators)[0].result, "ok")

    def test_an_offer_without_a_usable_id_or_title_is_an_error(self):
        for key, value in (("id", None), ("id", ""), ("id", True), ("id", "a b"), ("id", "../x"), ("id", 1.5),
                           ("id", [1]), ("title", ""), ("title", None), ("title", 5), ("title", "  ")):
            data = payload()
            data["offers"][1][key] = value
            r, _ = read({URL: json_response(data)})
            self.assertEqual((r.result, r.jobs), ("error", ()), f"{key}={value!r}")
        data = payload()
        del data["offers"][0]["id"]
        self.assertEqual(read({URL: json_response(data)})[0].result, "error")
        data = payload()
        data["offers"].append(None)
        self.assertEqual(read({URL: json_response(data)})[0].result, "error")

    def test_string_ids_are_kept(self):
        self.assertEqual(one(id="abc-123").native_id, "abc-123")

    def test_location_falls_back_to_city_and_country(self):
        cases = [({"location": "Berlin, DE"}, "Berlin, DE"), ({"location": " ", "city": "Austin", "country": "US"},
                                                                "Austin, US"),
                 ({"location": None, "city": "Austin", "country": ""}, "Austin"),
                 ({"location": "", "city": "", "country": "Chile"}, "Chile"),
                 ({"location": 5, "city": None, "country": None}, "")]
        for changes, expected in cases:
            self.assertEqual(one(**changes).location, expected, changes)

    def test_country_is_a_two_letter_code_or_none(self):
        for value, expected in (("nl", "NL"), ("US", "US"), ("USA", None), ("", None), (None, None), (5, None),
                                ("N1", None)):
            self.assertEqual(one(country_code=value).country, expected, value)

    def test_created_at_is_a_real_date_or_none(self):
        for value, expected in (("2026-09-14 08:15:02 UTC", "2026-09-14"), ("2026-09-14T08:15:02Z", "2026-09-14"),
                                ("2026-02-30 00:00:00 UTC", None), ("30+ days ago", None), ("", None), (None, None)):
            self.assertEqual(one(created_at=value).posted_on, expected, value)

    def test_url_is_a_web_address_or_the_tenant_offer_page(self):
        default = "https://acme.recruitee.com/o/software-engineer-backend"
        for value in ("javascript:alert(1)", "", None, 5, "/o/x"):
            self.assertEqual(one(careers_url=value).url, default, value)
        self.assertEqual(one(careers_url="https://jobs.acme.example/o/x").url, "https://jobs.acme.example/o/x")
        self.assertEqual(one(careers_url="", slug="a b").url, "")

    def test_body_parts(self):
        for changes, expected in (({"description": None, "requirements": None}, None),
                                  ({"description": " ", "requirements": "<p> </p>"}, None),
                                  ({"description": "<p>A</p>", "requirements": None}, "A"),
                                  ({"description": None, "requirements": "<p>B</p>"}, "B"),
                                  ({"description": "<p>A</p>", "requirements": "<p>B</p>"}, "A\n\nB"),
                                  ({"description": 5, "requirements": ["x"]}, None)):
            self.assertEqual(one(**changes).body, expected, changes)

    def test_duplicate_ids_are_kept_once(self):
        data = payload()
        data["offers"].append(dict(data["offers"][0], title="Copy"))
        r, _ = read({URL: json_response(data)})
        self.assertEqual([j.native_id for j in r.jobs], IDS)
        self.assertEqual(r.jobs[0].title, "Software Engineer, Backend")

    def test_bad_token_makes_no_request(self):
        for token in ("a/b", "A", "", "a b", "a%20b", "a.b", "a_b", "-a", "a-", "a@b", "a:80"):
            r, client = read({}, token=token)
            self.assertEqual((r.result, client.calls), ("error", []), token)

    def test_body_has_nothing_to_fetch(self):
        client = FakeClient({})
        job = JobRef("1", "x", "https://acme.recruitee.com/o/x", "", None, None, None)
        self.assertIsNone(RecruiteeAdapter(client).body("acme", job))
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
