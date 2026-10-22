"""The JazzHR adapter (issue #21, Task 26)."""
import json
import unittest
from pathlib import Path

from outreach.crawl.ats import ADAPTERS, adapter_for, supported_ats
from outreach.crawl.ats.jazzhr import JazzHRAdapter
from outreach.crawl.base import JobRef, Validators, ids_fingerprint
from outreach.net import FetchError, Response
from tests.contracts import check_adapter
from tests.fakes import FakeClient, status_response, text_response

FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
URL = "https://acme.applytojob.com/apply/jobs"
DETAIL = "https://acme.applytojob.com/apply/jobs/details/AbC123xYz"
LIST = (FIXTURES / "jazzhr_list.html").read_text()
PAGE = (FIXTURES / "jazzhr_detail.html").read_text()
IDS = ["AbC123xYz", "Qw9Er8tYu"]
CHALLENGE = ("<!DOCTYPE html><html><head><title>Just a moment...</title></head><body><div id='cf-challenge'>"
             "Checking your browser before accessing the site.</div></body></html>")


def read(routes, token="acme", validators=Validators()):
    client = FakeClient(routes)
    return JazzHRAdapter(client).list(token, validators), client


def row(job_id="AbC123xYz", title="Engineer", location="Boston", href=None):
    href = href if href is not None else f"https://acme.applytojob.com/apply/jobs/details/{job_id}"
    return (f'<tr><td><a class="job_title_link" href="{href}">{title}</a><span>Dept</span></td>'
            f'<td>{location}</td></tr>')


def listing(*rows):
    return ('<html><body><div class="resumator-jobs"><table>' + "".join(rows) + "</table></div></body></html>")


def job(native_id="AbC123xYz"):
    return JobRef(native_id, "x", f"https://acme.applytojob.com/apply/jobs/details/{native_id}", "", None, None, None)


def ld_page(*blocks):
    scripts = "".join(f'<script type="application/ld+json">{json.dumps(b)}</script>' for b in blocks)
    return f"<html><head>{scripts}</head><body></body></html>"


class JazzListTests(unittest.TestCase):
    def test_check_adapter(self):
        client = FakeClient({URL: [text_response(LIST), text_response(LIST), status_response(500)]})
        check_adapter(self, JazzHRAdapter(client), "acme")

    def test_registered(self):
        self.assertIs(ADAPTERS["jazzhr"], JazzHRAdapter)
        self.assertIn("jazzhr", supported_ats())
        self.assertIsInstance(adapter_for("jazzhr", FakeClient({})), JazzHRAdapter)

    def test_list_ok_parses_rows(self):
        r, client = read({URL: text_response(LIST)})
        self.assertEqual((client.requests, client.unrouted), ([URL], []))
        self.assertEqual(r.result, "ok")
        self.assertEqual(
            [(j.native_id, j.title, j.url, j.location, j.country, j.posted_on, j.body) for j in r.jobs],
            [("AbC123xYz", "Software Engineer & Tooling", DETAIL, "Boston, MA", None, None, None),
             ("Qw9Er8tYu", "Account Executive", "https://acme.applytojob.com/apply/jobs/details/Qw9Er8tYu", "Remote",
              None, None, None)])
        self.assertEqual(r.validators, Validators(etag="", ids_fingerprint=ids_fingerprint(IDS)))

    def test_bytes_vary_ids_equal_is_unchanged(self):
        first, _ = read({URL: text_response(LIST)})
        noisy = LIST.replace("</body>", '<input type="hidden" name="csrf" value="9f3a7c"></body>')
        self.assertNotEqual(noisy.encode(), LIST.encode())
        again, client = read({URL: text_response(noisy)}, validators=first.validators)
        self.assertEqual((again.result, again.jobs, again.validators), ("unchanged", (), first.validators))
        self.assertNotIn("if-none-match", {k.lower() for k in client.calls[0][2]})
        changed, _ = read({URL: text_response(listing(row("AbC123xYz"), row("New0000001")))},
                          validators=first.validators)
        self.assertEqual(changed.result, "ok")
        bare, _ = read({URL: status_response(304)}, validators=first.validators)
        self.assertEqual((bare.result, bare.http_status), ("error", 304))

    def test_challenge_page_is_error(self):
        for resp in (text_response(CHALLENGE), status_response(403), status_response(503), status_response(429),
                     text_response(CHALLENGE, status=403), text_response(""), text_response('{"jobs": []}'),
                     text_response("<html><body>Not found</body></html>")):
            r, _ = read({URL: resp})
            self.assertEqual((r.result, r.jobs), ("error", ()), resp)

    def test_no_user_agent_is_added(self):
        _, client = read({URL: text_response(LIST)})
        self.assertFalse({k.lower() for k in client.calls[0][2]} & {"user-agent", "cookie", "referer"})

    def test_http_failures_are_errors(self):
        for status in (301, 302, 404, 500):
            r, _ = read({URL: status_response(status)})
            self.assertEqual((r.result, r.http_status), ("error", status), status)
        r, _ = read({URL: FetchError("network", "down")})
        self.assertEqual((r.result, r.http_status), ("error", None))

    def test_a_page_with_the_marker_and_no_jobs_is_empty(self):
        page = '<html><body><div class="resumator-jobs"><p>There are no openings.</p></div></body></html>'
        r, _ = read({URL: text_response(page)})
        self.assertEqual((r.result, r.jobs, r.validators), ("empty", (), Validators()))

    def test_job_links_that_did_not_parse_are_not_an_empty_board(self):
        # The page still names its job links but no row yielded one: the markup changed, so it is not "no jobs".
        page = ('<html><body><div class="resumator-jobs"><div><a class="job_title_link" '
                'href="/apply/jobs/details/AbC">Engineer</a></div></div></body></html>')
        r, _ = read({URL: text_response(page)})
        self.assertEqual((r.result, r.jobs), ("error", ()))

    def test_detail_links_of_any_class_must_all_be_rows_read(self):
        # A list-group listing: real detail links, no job_title_link class and no table.
        group = ('<html><body><div class="resumator-jobs"><ul class="list-group">'
                 '<li class="list-group-item"><h4><a href="https://acme.applytojob.com/apply/jobs/details/AbC1">'
                 'Engineer</a></h4></li>'
                 '<li class="list-group-item"><h4><a href="/apply/jobs/details/AbC2">Designer</a></h4></li>'
                 '</ul></div></body></html>')
        r, _ = read({URL: text_response(group)})
        self.assertEqual((r.result, r.jobs), ("error", ()))
        # One row read, another detail link in a different shape.
        extra = listing(row("AbC1")).replace("</table>", '</table><a class="btn" href="/apply/jobs/details/AbC9">x</a>')
        r, _ = read({URL: text_response(extra)})
        self.assertEqual((r.result, r.jobs), ("error", ()))

    def test_a_second_link_to_a_job_already_read_is_fine(self):
        page = listing(row("AbC1").replace("</tr>", '<td><a class="btn" href="/apply/jobs/details/AbC1">Apply</a></td></tr>'))
        r, _ = read({URL: text_response(page)})
        self.assertEqual((r.result, [j.native_id for j in r.jobs]), ("ok", ["AbC1"]))

    def test_the_listing_container_must_be_an_element_class_or_id(self):
        css = ('<html><head><link rel="stylesheet" href="https://acme.applytojob.com/css/resumator.css"></head>'
               "<body><h1>This page is unavailable</h1></body></html>")
        script = '<html><body><script>var s = "resumator";</script><p>resumator</p></body></html>'
        for body in (css, script):
            r, _ = read({URL: text_response(body)})
            self.assertEqual((r.result, r.jobs), ("error", ()), body[:50])
        for container in ('<div id="resumator-wrapper"></div>', '<section class="x Resumator-Jobs"></section>'):
            r, _ = read({URL: text_response(f"<html><body>{container}</body></html>")})
            self.assertEqual(r.result, "empty", container)

    def test_a_row_without_a_usable_id_or_title_is_an_error(self):
        bad = [row(title=""), row(title="  "), row(href=""), row(href="/apply/jobs"), row(href="/careers/x"),
               row(href="/apply/jobs/details/"), row(href="/apply/jobs/details/a b"),
               row(href="/apply/jobs/details/../x"), row(href="/apply/jobs/details/AbC/extra"),
               '<tr><td><a class="job_title_link">No href</a></td><td>X</td></tr>']
        for markup in bad:
            r, _ = read({URL: text_response(listing(row("Good000001"), markup))})
            self.assertEqual((r.result, r.jobs), ("error", ()), markup)

    def test_a_cut_off_page_is_an_error(self):
        for cut in (LIST[:LIST.index("Account Executive") + 5], LIST[:LIST.index("Boston, MA") + 3],
                    LIST[:LIST.index("</tr>") + 5]):
            r, _ = read({URL: text_response(cut)})
            self.assertEqual((r.result, r.jobs), ("error", ()), cut[-30:])

    def test_location_is_the_second_cell(self):
        page = listing('<tr><td><a class="job_title_link" href="/apply/jobs/details/AbC1">T</a>'
                       '<span>Engineering</span></td><td> Austin, <b>TX</b> </td><td>Full time</td></tr>',
                       '<tr><td><a class="job_title_link" href="/apply/jobs/details/AbC2">U</a></td></tr>')
        r, _ = read({URL: text_response(page)})
        self.assertEqual([(j.title, j.location) for j in r.jobs], [("T", "Austin, TX"), ("U", "")])

    def test_rows_without_a_job_link_are_ignored(self):
        page = listing("<tr><th>Title</th><th>Location</th></tr>", row("AbC1"))
        r, _ = read({URL: text_response(page)})
        self.assertEqual([j.native_id for j in r.jobs], ["AbC1"])

    def test_duplicate_ids_are_kept_once(self):
        r, _ = read({URL: text_response(listing(row("AbC1", "First"), row("AbC1", "Copy"), row("AbC2")))})
        self.assertEqual([(j.native_id, j.title) for j in r.jobs], [("AbC1", "First"), ("AbC2", "Engineer")])

    def test_bad_token_makes_no_request(self):
        for token in ("a/b", "A", "", "a b", "a%20b", "a.b", "a_b", "-a", "a-", "a@b", "a:80"):
            r, client = read({}, token=token)
            self.assertEqual((r.result, client.calls), ("error", []), token)


class JazzBodyTests(unittest.TestCase):
    def body(self, resp, token="acme", native_id="AbC123xYz"):
        client = FakeClient({f"https://{token}.applytojob.com/apply/jobs/details/{native_id}": resp})
        return JazzHRAdapter(client).body(token, job(native_id)), client

    def test_body_from_json_ld(self):
        text, client = self.body(text_response(PAGE))
        self.assertEqual(text, "Build internal tools.\nPython\nWe sponsor visas")
        self.assertEqual(client.requests, [DETAIL])
        self.assertNotIn("Visible copy", text)

    def test_the_job_posting_block_is_found_in_lists_graphs_and_type_lists(self):
        posting = {"@type": "JobPosting", "description": "<p>Found</p>"}
        for blocks in ([posting], [{"@type": "Organization"}, posting], [[{"@type": "Thing"}, posting]],
                       [{"@graph": [{"@type": "Organization"}, posting]}],
                       [{"@type": ["Thing", "JobPosting"], "description": "<p>Found</p>"}]):
            self.assertEqual(self.body(text_response(ld_page(*blocks)))[0], "Found", blocks)

    def test_a_malformed_block_is_skipped_when_another_is_good(self):
        page = ('<script type="application/ld+json">{not json</script>'
                + ld_page({"@type": "JobPosting", "description": "<p>Found</p>"}))
        self.assertEqual(self.body(text_response(page))[0], "Found")

    def test_an_empty_description_is_none(self):
        self.assertIsNone(self.body(text_response(ld_page({"@type": "JobPosting", "description": "<p> </p>"})))[0])

    def test_failed_and_wrong_pages_raise(self):
        posting = {"@type": "JobPosting"}
        for resp in (status_response(404), status_response(403), status_response(500), status_response(304),
                     status_response(302), FetchError("network", "down"), text_response(CHALLENGE),
                     text_response(""), text_response('{"error": 1}'), text_response(LIST),
                     text_response(ld_page({"@type": "Organization", "description": "x"})),
                     text_response(ld_page(dict(posting))), text_response(ld_page(dict(posting, description=5))),
                     text_response("<script>var x = {\"@type\": \"JobPosting\"};</script>"),
                     text_response('<script type="text/javascript">{"@type": "JobPosting", "description": "x"}'
                                   "</script>")):
            with self.assertRaises((ValueError, FetchError), msg=resp):
                self.body(resp)

    def test_a_redirect_on_the_tenant_host_is_followed_and_one_off_it_is_not(self):
        moved = Response(302, "", b"", {"location": "/apply/AbC123xYz/Software-Engineer"})
        target = "https://acme.applytojob.com/apply/AbC123xYz/Software-Engineer"
        client = FakeClient({DETAIL: moved, target: text_response(PAGE)})
        self.assertEqual(JazzHRAdapter(client).body("acme", job()), "Build internal tools.\nPython\nWe sponsor visas")
        self.assertEqual(client.requests, [DETAIL, target])
        for location in ("https://evil.example/x", "http://acme.applytojob.com/x", "https://acme.applytojob.com.evil.com/x",
                         "https://acme.applytojob.com@evil.com/x", "https://other.applytojob.com/x",
                         "https://acme.applytojob.com:8443/x", "//evil.example/x", "javascript:alert(1)"):
            client = FakeClient({DETAIL: Response(302, "", b"", {"location": location})})
            with self.assertRaises(ValueError, msg=location):
                JazzHRAdapter(client).body("acme", job())
            self.assertEqual(client.requests, [DETAIL], location)

    def test_a_304_is_not_a_redirect(self):
        client = FakeClient({DETAIL: Response(304, "", b"", {"location": "/apply/jobs/details/AbC123xYz"})})
        with self.assertRaises(ValueError):
            JazzHRAdapter(client).body("acme", job())
        self.assertEqual(client.requests, [DETAIL])

    def test_a_redirect_loop_raises(self):
        loop = Response(302, "", b"", {"location": "/apply/jobs/details/AbC123xYz"})
        client = FakeClient({DETAIL: [loop] * 10})
        with self.assertRaises(ValueError):
            JazzHRAdapter(client).body("acme", job())
        self.assertLessEqual(len(client.requests), 5)

    def test_the_request_is_built_from_the_token_and_id_never_from_the_posting_url(self):
        client = FakeClient({DETAIL: text_response(PAGE)})
        evil = JobRef("AbC123xYz", "x", "https://evil.example/apply/jobs/details/AbC123xYz", "", None, None, None)
        JazzHRAdapter(client).body("acme", evil)
        self.assertEqual(client.requests, [DETAIL])

    def test_a_bad_token_or_id_makes_no_request(self):
        for token, native_id in (("a.b", "AbC"), ("A", "AbC"), ("", "AbC"), ("acme", "../x"), ("acme", "a b"),
                                 ("acme", ""), ("acme", "AbC/x"), ("acme", "AbC?x=1"), ("acme", "AbC#")):
            client = FakeClient({})
            with self.assertRaises(ValueError, msg=(token, native_id)):
                JazzHRAdapter(client).body(token, job(native_id))
            self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
