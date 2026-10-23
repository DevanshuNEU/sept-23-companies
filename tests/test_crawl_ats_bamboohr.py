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
URL = "https://acme.bamboohr.com/jobs/embed2.php"
DETAIL = "https://acme.bamboohr.com/careers/41/detail"
EMBED = (FIXTURES / "bamboohr_embed.html").read_text()
EMPTY = (FIXTURES / "bamboohr_embed_empty.html").read_text()
DETAIL_JSON = json.loads((FIXTURES / "bamboohr_detail.json").read_text())
LOGIN = ("<!DOCTYPE html><html><head><title>BambooHR Login</title></head><body><form action='/login.php'>"
         "<input name='user'></form></body></html>")


def read(routes, token="acme", validators=Validators()):
    client = FakeClient(routes)
    return BambooHRAdapter(client).list(token, validators), client


def job(native_id="41"):
    return JobRef(native_id, "x", f"https://acme.bamboohr.com/careers/{native_id}", "", None, None, None)


def item(job_id="41", title="Engineer", location="Boston"):
    return (f'<li id="bhrPositionID_{job_id}" class="BambooHR-ATS-Jobs-Item"><a href="//x/careers/{job_id}">{title}</a>'
            f'<span class="BambooHR-ATS-Location">{location}</span></li>')


def widget(*items):
    return ('<div class="BambooHR-ATS-board"><div class="BambooHR-ATS-Department-Wrapper">'
            '<div class="BambooHR-ATS-Department-Item"><ul class="BambooHR-ATS-Jobs-List">'
            + "".join(items) + "</ul></div></div></div>")


class BambooListTests(unittest.TestCase):
    def test_check_adapter(self):
        client = FakeClient({URL: [text_response(EMBED), text_response(EMBED), status_response(500)]})
        check_adapter(self, BambooHRAdapter(client), "acme")

    def test_registered(self):
        self.assertIs(ADAPTERS["bamboohr"], BambooHRAdapter)
        self.assertIn("bamboohr", supported_ats())
        self.assertIsInstance(adapter_for("bamboohr", FakeClient({})), BambooHRAdapter)

    def test_list_ok_parses_the_widget(self):
        r, client = read({URL: text_response(EMBED)})
        self.assertEqual((client.requests, client.unrouted), ([URL], []))
        self.assertEqual(r.result, "ok")
        self.assertEqual(
            [(j.native_id, j.title, j.url, j.location, j.country, j.posted_on, j.body) for j in r.jobs],
            [("41", "Software Engineer & Tooling", "https://acme.bamboohr.com/careers/41", "Boston, MA", None, None,
              None),
             ("57", "Account Executive", "https://acme.bamboohr.com/careers/57", "Remote", None, None, None)])
        self.assertEqual(r.validators, Validators(etag="", ids_fingerprint=ids_fingerprint(["41", "57"])))

    def test_the_same_ids_are_unchanged_and_no_etag_is_sent(self):
        first, _ = read({URL: text_response(EMBED)})
        again, client = read({URL: text_response(EMBED + "<!-- served at 10:01 -->")}, validators=first.validators)
        self.assertEqual((again.result, again.jobs), ("unchanged", ()))
        self.assertNotIn("if-none-match", {k.lower() for k in client.calls[0][2]})
        r, _ = read({URL: status_response(304)}, validators=first.validators)
        self.assertEqual((r.result, r.http_status), ("error", 304))

    def test_login_page_is_error_not_empty(self):
        for body in (LOGIN, "", "<html><body>Access denied</body></html>", "<html><title>Just a moment...</title>",
                     '{"error": "nope"}', "<script>var s = 'BambooHR-ATS-board';</script>",
                     "<p>BambooHR-ATS-board</p>"):
            r, _ = read({URL: text_response(body)})
            self.assertEqual((r.result, r.jobs), ("error", ()), body[:40])

    def test_empty_widget_is_empty(self):
        r, _ = read({URL: text_response(EMPTY)})
        self.assertEqual((r.result, r.jobs, r.validators), ("empty", (), Validators()))
        r, _ = read({URL: text_response('<div class="BambooHR-ATS-board"></div>')})
        self.assertEqual(r.result, "empty")

    def test_http_failures_are_errors(self):
        for status in (301, 302, 403, 404, 429, 500):
            r, _ = read({URL: status_response(status)})
            self.assertEqual((r.result, r.http_status), ("error", status), status)
        r, _ = read({URL: FetchError("network", "down")})
        self.assertEqual((r.result, r.http_status), ("error", None))

    def test_an_item_without_an_id_or_a_title_is_an_error(self):
        bad = [item(title=""), item(title="  "), item(job_id="a b"), item(job_id="../x"), item(job_id=""),
               '<li class="BambooHR-ATS-Jobs-Item"><a href="/careers/9">No id</a></li>',
               '<li id="bhrPositionID_9" class="BambooHR-ATS-Jobs-Item"><span>No link</span></li>']
        for markup in bad:
            r, _ = read({URL: text_response(widget(item("1"), markup))})
            self.assertEqual((r.result, r.jobs), ("error", ()), markup)

    def test_a_cut_off_widget_is_an_error(self):
        for cut in (EMBED[:EMBED.index("</li>") + 5], EMBED[:EMBED.index("Account Executive") + 5], EMBED[:EMBED.index("Boston, MA") + 3],
                    widget(item())[:widget(item()).index("<a ") + 6]):
            r, _ = read({URL: text_response(cut)})
            self.assertEqual((r.result, r.jobs), ("error", ()), cut[-30:])

    def test_position_elements_under_another_class_name_are_an_error(self):
        # Every bhrPositionID_ element must be a row read: the widget's item class may have been renamed.
        renamed = item("2").replace("BambooHR-ATS-Jobs-Item", "BambooHR-ATS-Job-Row")
        for body in (widget(item("1"), renamed), widget(renamed),
                     widget(item("1"), '<div id="bhrPositionID_3"><a href="/careers/3">Plain</a></div>')):
            r, _ = read({URL: text_response(body)})
            self.assertEqual((r.result, r.jobs), ("error", ()), body[-80:])

    def test_the_board_root_must_be_seen_and_closed(self):
        no_root = ('<ul class="BambooHR-ATS-Jobs-List">' + item("1") + item("2") + "</ul>")
        cut = widget(item("1"), item("2"))
        for body in (no_root, no_root[:no_root.index("</li>") + 5], cut[:cut.index("</li>") + 5],
                     '<div class="BambooHR-ATS-Department-Wrapper"></div>'):
            r, _ = read({URL: text_response(body)})
            self.assertEqual((r.result, r.jobs), ("error", ()), body[-60:])

    def test_an_error_page_with_one_ats_class_is_error(self):
        for body in ('<html><body><div class="BambooHR-ATS-Message">Service unavailable</div></body></html>',
                     '<html><body><p class="BambooHR-ATS-Location">Down</p></body></html>'):
            r, _ = read({URL: text_response(body)})
            self.assertEqual((r.result, r.jobs), ("error", ()), body)

    def test_title_and_location_forms(self):
        markup = ('<li id="bhrPositionID_5" class="BambooHR-ATS-Jobs-Item"><a href="/x"> <b>Staff</b>  Engineer </a>'
                  '<span class="foo BambooHR-ATS-Location extra"> Austin,   TX </span></li>')
        r, _ = read({URL: text_response(widget(markup))})
        self.assertEqual((r.jobs[0].title, r.jobs[0].location), ("Staff Engineer", "Austin, TX"))
        no_location = '<li id="bhrPositionID_6" class="BambooHR-ATS-Jobs-Item"><a href="/x">Designer</a></li>'
        r, _ = read({URL: text_response(widget(no_location))})
        self.assertEqual((r.result, r.jobs[0].location), ("ok", ""))

    def test_duplicate_ids_are_kept_once(self):
        r, _ = read({URL: text_response(widget(item("1", "First"), item("1", "Copy"), item("2")))})
        self.assertEqual([(j.native_id, j.title) for j in r.jobs], [("1", "First"), ("2", "Engineer")])

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
