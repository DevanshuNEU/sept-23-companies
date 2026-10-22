"""The Breezy adapter (issue #21, Task 22)."""
import json
import unittest
from pathlib import Path

from outreach.crawl.ats import ADAPTERS, adapter_for, supported_ats
from outreach.crawl.ats.breezy import BreezyAdapter
from outreach.crawl.base import JobRef, Validators, ids_fingerprint
from outreach.net import FetchError, Response
from tests.contracts import check_adapter
from tests.fakes import FakeClient, json_response, status_response, text_response

FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
URL = "https://acme.breezy.hr/json"
LIST = (FIXTURES / "breezy_list.json").read_bytes()
PAGE = (FIXTURES / "breezy_detail.html").read_text()
ETAG = 'W/"7a1f9c"'
IDS = ["a1b2c3d4e5f6", "b2c3d4e5f6a1", "c3d4e5f6a1b2"]
JOB_URL = "https://acme.breezy.hr/p/a1b2c3d4e5f6-software-engineer-new-grad"


class FollowClient(FakeClient):
    """A FakeClient that also records whether each GET asked to follow redirects."""

    def __init__(self, routes):
        super().__init__(routes)
        self.follow = []

    def get(self, url, headers=None, *, follow_redirects=False):
        self.follow.append(follow_redirects)
        return super().get(url, headers, follow_redirects=follow_redirects)


def ok(body=LIST, etag=ETAG):
    return Response(200, "", body, {"etag": etag} if etag else {})


def jobs():
    return json.loads(LIST)


def read(routes, token="acme", validators=Validators()):
    client = FollowClient(routes)
    return BreezyAdapter(client).list(token, validators), client


def lines(text):
    """The non-blank lines: how much blank space html_to_text leaves between blocks is its own business."""
    return [line for line in text.splitlines() if line.strip()]


def job_at(url=JOB_URL, job_id=IDS[0]):
    return JobRef(job_id, "x", url, "", None, None, None)


class BreezyListTests(unittest.TestCase):
    def test_check_adapter(self):
        client = FakeClient({URL: [ok(), status_response(304), status_response(500)]})
        check_adapter(self, BreezyAdapter(client), "acme")

    def test_registered(self):
        self.assertIs(ADAPTERS["breezy"], BreezyAdapter)
        self.assertIn("breezy", supported_ats())
        self.assertIsInstance(adapter_for("breezy", FakeClient({})), BreezyAdapter)

    def test_list_ok_parses_jobs_without_following_redirects(self):
        r, client = read({URL: ok()})
        self.assertEqual((client.requests, client.follow), ([URL], [False]))
        self.assertEqual(r.result, "ok")
        self.assertEqual(
            [(j.native_id, j.title, j.url, j.location, j.country, j.posted_on, j.body) for j in r.jobs],
            [(IDS[0], "Software Engineer, New Grad", JOB_URL, "San Francisco, CA", "US", "2026-09-14", None),
             (IDS[1], "Account Executive", "https://acme.breezy.hr/p/b2c3d4e5f6a1-account-executive",
              "London, England", "GB", "2026-10-03", None),
             (IDS[2], "Data Engineer", "https://acme.breezy.hr/p/c3d4e5f6a1b2-data-engineer", "Remote", None,
              None, None)])
        self.assertEqual(r.validators, Validators(etag=ETAG, ids_fingerprint=ids_fingerprint(IDS)))

    def test_empty_list_is_empty(self):
        r, _ = read({URL: ok(b"[]")})
        self.assertEqual((r.result, r.jobs, r.validators), ("empty", (), Validators()))

    def test_breezy_redirect_to_marketing_site_is_error(self):
        # A tenant with no careers site answers 302 to breezy.hr: not an empty board.
        moved = Response(302, "", b"", {"location": "https://breezy.hr/"})
        for status in (301, 302, 303, 307, 308):
            r, client = read({URL: Response(status, "", b"", {"location": "https://breezy.hr/"})})
            self.assertEqual((r.result, r.http_status, r.jobs), ("error", status, ()), status)
            self.assertEqual(client.follow, [False])
        r, _ = read({URL: moved})
        self.assertIn("redirect", r.error)

    def test_the_marketing_page_as_a_200_is_error(self):
        r, _ = read({URL: text_response("<html><title>Breezy HR</title></html>")})
        self.assertEqual((r.result, r.jobs), ("error", ()))

    def test_wrong_shapes_are_errors(self):
        for name, resp in {"object": json_response({"positions": jobs()}), "null": json_response(None),
                           "string": json_response("[]"), "empty object": json_response({}),
                           "non objects": json_response([3]), "list with null": json_response(jobs() + [None])}.items():
            r, _ = read({URL: resp})
            self.assertEqual((r.result, r.jobs), ("error", ()), name)

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
        # No etag stored, so nothing was sent: a 304 proves nothing.
        bare, client = read({URL: status_response(304)}, validators=Validators(ids_fingerprint="x"))
        self.assertNotIn("If-None-Match", client.calls[0][2])
        self.assertEqual((bare.result, bare.http_status), ("error", 304))

    def test_a_200_with_the_same_ids_is_ok_not_unchanged(self):
        first, _ = read({URL: ok()})
        again, _ = read({URL: ok(etag='W/"new"')}, validators=first.validators)
        self.assertEqual((again.result, again.validators.etag), ("ok", 'W/"new"'))

    def test_a_posting_without_a_usable_id_or_name_is_an_error(self):
        for key, value in (("id", None), ("id", ""), ("id", "a b"), ("id", "../x"), ("id", "a/b"), ("id", True),
                           ("name", ""), ("name", None), ("name", 7), ("name", "  ")):
            payload = jobs()
            payload[1][key] = value
            r, _ = read({URL: json_response(payload)})
            self.assertEqual((r.result, r.jobs), ("error", ()), f"{key}={value!r}")
        payload = jobs()
        del payload[0]["id"]
        r, _ = read({URL: json_response(payload)})
        self.assertEqual(r.result, "error")

    def test_a_url_off_the_tenant_host_is_replaced_by_the_tenant_position_url(self):
        default = "https://acme.breezy.hr/p/" + IDS[0]
        for value in ("https://evil.example.com/p/1", "http://acme.breezy.hr/p/1", "https://acme.breezy.hr.evil.com/p",
                      "https://acme.breezy.hr@evil.com/p", "https://other.breezy.hr/p/1", "javascript:alert(1)",
                      "", None, 7, "https://acme.breezy.hr:8443/p/1", "//acme.breezy.hr/p/1"):
            payload = jobs()[:1]
            payload[0]["url"] = value
            r, _ = read({URL: json_response(payload)})
            self.assertEqual(r.jobs[0].url, default, value)
        payload = jobs()[:1]
        del payload[0]["url"]
        r, _ = read({URL: json_response(payload)})
        self.assertEqual(r.jobs[0].url, default)

    def test_location_forms(self):
        cases = [
            ({"name": " Boston, MA "}, "Boston, MA"),
            ({"city": "Austin", "state": {"name": "Texas", "id": "TX"}}, "Austin, Texas"),
            ({"city": "Austin"}, "Austin"),
            ({"state": {"name": "Texas"}}, "Texas"),
            ({"city": "Paris", "state": None, "country": {"id": "FR", "name": "France"}}, "Paris"),
            ({"name": 5, "city": ["x"]}, ""), ({}, ""), (None, ""), ("Boston", ""), (["x"], ""),
        ]
        for value, expected in cases:
            payload = jobs()[:1]
            payload[0]["location"] = value
            r, _ = read({URL: json_response(payload)})
            self.assertEqual(r.jobs[0].location, expected, value)

    def test_country_is_a_two_letter_code_or_none(self):
        for value, expected in (({"id": "us"}, "US"), ({"id": "US"}, "US"), ({"name": "United States"}, None),
                                ({"id": "USA"}, None), ({"id": 7}, None), ("US", None), (None, None)):
            payload = jobs()[:1]
            payload[0]["location"] = {"name": "x", "country": value}
            r, _ = read({URL: json_response(payload)})
            self.assertEqual(r.jobs[0].country, expected, value)

    def test_published_date_is_a_real_date_or_none(self):
        for value, expected in (("2026-09-14T17:20:31.000Z", "2026-09-14"), ("2026-09-14", "2026-09-14"),
                                ("2026-02-30", None), ("30+ days ago", None), ("", None), (None, None), (7, None)):
            payload = jobs()[:1]
            payload[0]["published_date"] = value
            r, _ = read({URL: json_response(payload)})
            self.assertEqual(r.jobs[0].posted_on, expected, value)

    def test_duplicate_ids_are_kept_once(self):
        payload = jobs()
        payload.append(dict(payload[0], name="Copy"))
        r, _ = read({URL: json_response(payload)})
        self.assertEqual([j.native_id for j in r.jobs], IDS)
        self.assertEqual(r.jobs[0].title, "Software Engineer, New Grad")

    def test_bad_token_makes_no_request(self):
        # The token is a host label: a dot, an underscore or a slash would reach another host.
        for token in ("a/b", "A", "", "a b", "a%20b", "a.b", "a_b", "-a", "a-", "a@b", "a:80"):
            r, client = read({}, token=token)
            self.assertEqual((r.result, client.calls), ("error", []), token)
        client = FakeClient({})
        with self.assertRaises(ValueError):
            BreezyAdapter(client).body("a.b", job_at("https://a.b.breezy.hr/p/1"))
        self.assertEqual(client.calls, [])


class BreezyBodyTests(unittest.TestCase):
    def body(self, resp, url=JOB_URL, token="acme"):
        client = FollowClient({url: resp})
        return BreezyAdapter(client).body(token, job_at(url)), client

    def test_breezy_body_from_description_block(self):
        text, client = self.body(text_response(PAGE))
        self.assertEqual(client.requests, [JOB_URL])
        self.assertEqual(client.follow, [True])
        self.assertEqual(lines(text), ["We are hiring a new grad engineer.", "Build services", "Ship & learn",
                                       "We sponsor visas for the right person."])
        for other in ("Acme Robotics", "Apply", "Powered by", "<"):
            self.assertNotIn(other, text)

    def test_the_block_ends_at_its_own_closing_tag_and_the_first_block_wins(self):
        page = ('<div class="x"><div class="description"><div><p>Inner</p></div><div>More</div></div></div>'
                '<div class="description"><p>Second block</p></div><p>Outside</p>')
        self.assertEqual(self.body(text_response(page))[0], "Inner\nMore")

    def test_class_lists_and_case_and_other_tags(self):
        page = '<section class="job-body Description  wide"><p>Text</p></section><p>Outside</p>'
        self.assertEqual(self.body(text_response(page))[0], "Text")
        page = '<div class="job-description"><p>Not this</p></div><p>Outside</p>'
        with self.assertRaises(ValueError):
            self.body(text_response(page))

    def test_an_unclosed_block_runs_to_the_end(self):
        self.assertEqual(self.body(text_response('<div class="description"><p>Cut off'))[0], "Cut off")

    def test_an_empty_block_is_none_and_a_missing_block_raises(self):
        self.assertIsNone(self.body(text_response('<div class="description"> <p> </p> </div>'))[0])
        for page in ("<html><body><p>Nothing here</p></body></html>", "", '{"error": "x"}',
                     "<html><title>Breezy HR</title></html>"):
            with self.assertRaises(ValueError, msg=page):
                self.body(text_response(page))

    def test_a_failed_page_raises(self):
        for resp in (status_response(500), status_response(404), status_response(403), status_response(302),
                     status_response(304), FetchError("timeout")):
            with self.assertRaises((ValueError, FetchError), msg=resp):
                self.body(resp)

    def test_a_multi_line_page_keeps_the_block_bounds(self):
        page = "\n".join(["<html>", "<body>", "", '<div class="description">', "<p>Line one</p>", "<p>Line two</p>",
                          "</div>", "<p>After</p>", "</body>", "</html>"])
        self.assertEqual(lines(self.body(text_response(page.replace("\n", "\r\n")))[0]), ["Line one", "Line two"])
        self.assertEqual(lines(self.body(text_response(page))[0]), ["Line one", "Line two"])

    def test_a_url_off_the_tenant_host_makes_no_request(self):
        for url in ("https://evil.example.com/p/1", "http://acme.breezy.hr/p/1", "https://acme.breezy.hr.evil.com/p",
                    "https://acme.breezy.hr@evil.com/p", "https://other.breezy.hr/p/1", "", "file:///etc/passwd",
                    "https://acme.breezy.hr:8443/p/1"):
            client = FollowClient({})
            with self.assertRaises(ValueError, msg=url):
                BreezyAdapter(client).body("acme", job_at(url))
            self.assertEqual(client.calls, [], url)


if __name__ == "__main__":
    unittest.main()
