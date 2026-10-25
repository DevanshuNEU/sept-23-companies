"""The careers page check (#31 Task 15): at most a careers page, a homepage and the page the homepage points to."""
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from outreach.net import USER_AGENT, FetchError, Response, UrllibClient
from outreach.sources.boards import BoardRef
from outreach.sources.careers import CareersChecker, CareersResult
from tests.fakes import FakeClient, status_response

FIXTURES = Path(__file__).parent / "fixtures" / "careers"


def page(name):
    return FIXTURES / name


class CareersCheckerTest(unittest.TestCase):
    def check(self, domain, routes):
        client = FakeClient(routes)
        result = CareersChecker(client).check(domain)
        self.assertEqual(client.unrouted, [])
        return result, client

    def test_careers_page_with_ats_embed_gives_boards(self):
        result, client = self.check("embedco.com", {"https://embedco.com/careers": page("ats_embed.html")})
        self.assertEqual(result, CareersResult("boards", (BoardRef("greenhouse", "embedco"),),
                                               "https://embedco.com/careers", (), ""))
        self.assertEqual(client.requests, ["https://embedco.com/careers"])

    def test_404_then_homepage_link_then_page(self):
        result, client = self.check("clearpath.ai", {
            "https://clearpath.ai/careers": status_response(404),
            "https://clearpath.ai/": page("home_links_join.html"),
            "https://clearpath.ai/join-us": page("clearpath_join.html")})
        self.assertEqual((result.kind, result.boards, result.page_url),
                         ("boards", (BoardRef("ashby", "clearpath"),), "https://clearpath.ai/join-us"))
        self.assertEqual(client.requests, ["https://clearpath.ai/careers", "https://clearpath.ai/",
                                           "https://clearpath.ai/join-us"])

    def test_page_without_ats_is_a_page_with_final_url(self):
        final = "https://www.plainco.com/company/careers"
        result, _ = self.check("plainco.com", {
            "https://plainco.com/careers": Response(200, final, page("plain_page.html").read_bytes(), {})})
        self.assertEqual(result, CareersResult("page", (), final, (), ""))

    def test_unsupported_ats_is_a_page_and_reported(self):
        result, _ = self.check("acme.com", {"https://acme.com/careers": page("unsupported_ats.html")})
        self.assertEqual((result.kind, result.boards, result.unsupported, result.page_url),
                         ("page", (), ("jobs.jobvite.com",), "https://acme.com/careers"))

    def test_two_boards_on_one_page_are_both_returned(self):
        result, _ = self.check("twoco.com", {"https://twoco.com/careers": page("two_boards.html")})
        self.assertEqual((result.kind, result.boards, result.page_url),
                         ("boards", (BoardRef("greenhouse", "twoco"), BoardRef("lever", "twoco")),
                          "https://twoco.com/careers"))

    def test_a_redirect_to_an_ats_board_is_seen_in_the_final_url(self):
        result, _ = self.check("redirco.com", {"https://redirco.com/careers": Response(
            200, "https://jobs.lever.co/redirco", b"<html>Loading</html>", {})})
        self.assertEqual((result.kind, result.boards), ("boards", (BoardRef("lever", "redirco"),)))

    def test_both_404_is_none(self):
        result, client = self.check("nothere.com", {"https://nothere.com/careers": status_response(404),
                                                    "https://nothere.com/": status_response(404)})
        self.assertEqual(result, CareersResult("none", (), None, (), ""))
        self.assertEqual(len(client.requests), 2)

    def test_a_homepage_with_no_link_is_none(self):
        result, client = self.check("quill.io", {"https://quill.io/careers": status_response(404),
                                                  "https://quill.io/": page("home_no_link.html")})
        self.assertEqual(result.kind, "none")
        self.assertEqual(len(client.requests), 2)

    def test_503_is_error_not_none(self):
        for status in (500, 503, 429):
            with self.subTest(status=status):
                result, client = self.check("flaky.com", {"https://flaky.com/careers": status_response(status)})
                self.assertEqual(result.kind, "error")
                self.assertIn(str(status), result.error)
                self.assertEqual(client.requests, ["https://flaky.com/careers"])  # nothing concluded, no fallback

    def test_an_error_on_the_homepage_or_the_linked_page_is_error(self):
        result, _ = self.check("flaky.com", {"https://flaky.com/careers": status_response(404),
                                              "https://flaky.com/": status_response(503)})
        self.assertEqual(result.kind, "error")
        result, _ = self.check("flaky.com", {"https://flaky.com/careers": status_response(404),
                                              "https://flaky.com/": page("home_links_join.html"),
                                              "https://flaky.com/join-us": FetchError("timeout", "slow")})
        self.assertEqual(result.kind, "error")

    def test_timeout_is_error(self):
        result, _ = self.check("slow.com", {"https://slow.com/careers": FetchError("timeout", "no answer")})
        self.assertEqual((result.kind, result.boards, result.page_url), ("error", (), None))
        self.assertIn("timeout", result.error)

    def test_an_unexpected_status_is_error(self):
        for status in (204, 301, 302, 400, 401, 403, 408):  # a bot block is not "no careers page"
            with self.subTest(status=status):
                result, client = self.check("odd.com", {"https://odd.com/careers": status_response(status)})
                self.assertEqual(result.kind, "error")
                self.assertIn(str(status), result.error)
                self.assertEqual(client.requests, ["https://odd.com/careers"])

    def test_a_block_on_the_homepage_or_the_linked_page_is_error(self):
        home = {"https://odd.com/careers": status_response(404)}
        result, _ = self.check("odd.com", {**home, "https://odd.com/": status_response(403)})
        self.assertEqual(result.kind, "error")
        result, _ = self.check("odd.com", {**home, "https://odd.com/": page("home_links_join.html"),
                                           "https://odd.com/join-us": status_response(403)})
        self.assertEqual(result.kind, "error")

    def test_410_is_as_definite_as_404(self):
        result, _ = self.check("gone.com", {"https://gone.com/careers": status_response(410),
                                             "https://gone.com/": status_response(410)})
        self.assertEqual(result.kind, "none")
        result, _ = self.check("gone.com", {"https://gone.com/careers": status_response(404),
                                             "https://gone.com/": page("home_links_join.html"),
                                             "https://gone.com/join-us": status_response(410)})
        self.assertEqual(result.kind, "none")

    def test_a_redirect_off_site_ignores_the_body(self):
        body = b'<a href="https://boards.greenhouse.io/parentco">Parent jobs</a> <a href="https://jobs.jobvite.com/p">J</a>'
        final = "https://parentco.com/careers"
        result, client = self.check("acme.com", {"https://acme.com/careers": Response(200, final, body, {})})
        self.assertEqual(result, CareersResult("page", (), final, (), ""))
        self.assertEqual(len(client.requests), 1)

    def test_a_redirect_straight_to_an_ats_host_still_counts_off_site(self):
        result, _ = self.check("acme.com", {"https://acme.com/careers": Response(
            200, "https://boards.greenhouse.io/acme", b"<html>other board links https://jobs.lever.co/zzz</html>", {})})
        self.assertEqual((result.kind, result.boards), ("boards", (BoardRef("greenhouse", "acme"),)))

    def test_a_redirect_to_a_subdomain_of_the_site_keeps_the_body(self):
        body = b'<a href="https://boards.greenhouse.io/acme">Jobs</a>'
        result, _ = self.check("acme.com", {"https://acme.com/careers": Response(
            200, "https://careers.acme.com/", body, {})})
        self.assertEqual((result.kind, result.boards), ("boards", (BoardRef("greenhouse", "acme"),)))

    def test_a_dead_linked_page_is_none(self):
        result, _ = self.check("clearpath.ai", {"https://clearpath.ai/careers": status_response(404),
                                                 "https://clearpath.ai/": page("home_links_join.html"),
                                                 "https://clearpath.ai/join-us": status_response(404)})
        self.assertEqual(result.kind, "none")

    def test_link_to_another_site_is_not_followed(self):
        result, client = self.check("acme.com", {"https://acme.com/careers": status_response(404),
                                                 "https://acme.com/": page("home_external_link.html")})
        self.assertEqual(result.kind, "none")
        self.assertEqual(client.requests, ["https://acme.com/careers", "https://acme.com/"])

    def test_the_first_usable_link_wins_and_odd_hrefs_are_skipped(self):
        result, client = self.check("relco.com", {"https://relco.com/careers": status_response(404),
                                                  "https://relco.com/": page("home_misc_links.html"),
                                                  "https://relco.com/team/jobs.html": page("plain_page.html")})
        self.assertEqual((result.kind, result.page_url), ("page", "https://relco.com/team/jobs.html"))
        self.assertEqual(len(client.requests), 3)  # no mailto, fragment, script or the careers URL again

    def test_a_homepage_link_on_a_subdomain_of_the_site_is_followed(self):
        home = b'<a href="https://jobs.relco.com/open">Careers</a>'
        result, _ = self.check("relco.com", {"https://relco.com/careers": status_response(404),
                                              "https://relco.com/": home,
                                              "https://jobs.relco.com/open": page("plain_page.html")})
        self.assertEqual(result.kind, "page")



def redirect(location, status=302):
    return Response(status, "", b"", {"location": location})


class SwitchedOffHostTest(unittest.TestCase):
    """#46: every request and redirect hop of the check goes through the crawler's `[ats_off]` host block, so a
    careers page that redirects to a switched-off ATS's host sends nothing there. A refused hop is an `error`, never
    a definite `none`."""

    def check(self, domain, routes, cfg=None):
        client = FakeClient(routes)
        result = CareersChecker(client, **({"cfg": cfg} if cfg is not None else {})).check(domain)
        self.assertEqual(client.unrouted, [])
        return result, client

    def test_a_careers_page_redirecting_to_smartrecruiters_is_refused_and_an_error(self):
        result, client = self.check("acme.com", {
            "https://acme.com/careers": redirect("https://jobs.smartrecruiters.com/Acme")})
        self.assertEqual(result.kind, "error")
        self.assertIn("smartrecruiters reader is switched off", result.error)
        self.assertEqual(client.requests, ["https://acme.com/careers"])

    def test_a_careers_page_redirecting_to_pinpoint_is_refused_and_an_error(self):
        result, client = self.check("acme.com", {
            "https://acme.com/careers": redirect("https://acme.com/jobs", 301),
            "https://acme.com/jobs": redirect("https://acme.pinpointhq.com/")})
        self.assertEqual(result.kind, "error")
        self.assertIn("pinpoint reader is switched off", result.error)
        self.assertEqual(client.requests, ["https://acme.com/careers", "https://acme.com/jobs"])

    def test_a_refused_hop_on_the_homepage_or_the_linked_page_is_an_error_not_none(self):
        result, client = self.check("acme.com", {
            "https://acme.com/careers": status_response(404),
            "https://acme.com/": redirect("https://careers.smartrecruiters.com/Acme")})
        self.assertEqual(result.kind, "error")
        self.assertEqual(client.requests, ["https://acme.com/careers", "https://acme.com/"])
        result, client = self.check("clearpath.ai", {
            "https://clearpath.ai/careers": status_response(404),
            "https://clearpath.ai/": page("home_links_join.html"),
            "https://clearpath.ai/join-us": redirect("https://clearpath.pinpointhq.com/en/postings")})
        self.assertEqual(result.kind, "error")
        self.assertNotIn("pinpointhq", " ".join(client.requests))

    def test_a_final_url_on_a_switched_off_host_is_an_error(self):
        # A client that followed the redirect itself: the page is not read, whatever it says.
        result, _ = self.check("acme.com", {"https://acme.com/careers": Response(
            200, "https://acme.pinpointhq.com/", b"<html>Jobs</html>", {})})
        self.assertEqual(result.kind, "error")

    def test_redirects_are_followed_hop_by_hop_up_to_the_limit(self):
        result, client = self.check("acme.com", {
            "https://acme.com/careers": redirect("/company/careers", 301),
            "https://acme.com/company/careers": Response(200, "", page("plain_page.html").read_bytes(), {})})
        self.assertEqual((result.kind, result.page_url), ("page", "https://acme.com/company/careers"))
        loop = {"https://loop.com/careers": redirect("https://loop.com/a"), "https://loop.com/a": redirect("https://loop.com/b"),
                "https://loop.com/b": redirect("https://loop.com/c"), "https://loop.com/c": redirect("https://loop.com/d")}
        result, client = self.check("loop.com", loop)
        self.assertEqual(result.kind, "error")  # the fourth hop is not sent: a 302 left is an unexpected status
        self.assertEqual(len(client.requests), 4)

    def test_a_reader_turned_back_on_is_followed_again(self):
        from dataclasses import replace
        from outreach.crawl.config import load_crawl_config
        on = replace(load_crawl_config(), ats_off={})
        result, client = self.check("acme.com", {
            "https://acme.com/careers": redirect("https://jobs.smartrecruiters.com/acme"),
            "https://jobs.smartrecruiters.com/acme": Response(200, "", b"<html>Jobs</html>", {})}, cfg=on)
        self.assertEqual((result.kind, result.boards), ("boards", (BoardRef("smartrecruiters", "acme"),)))
        self.assertEqual(len(client.requests), 2)


class _Local(BaseHTTPRequestHandler):
    seen: list[dict] = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.seen.append({k.lower(): v for k, v in self.headers.items()})
        pages = {"/": b'<html><a href="/join-us">Join us</a></html>', "/join-us": b"<html>Open roles</html>"}
        status, body = (200, pages[self.path]) if self.path in pages else (404, b"")
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _ToLocal:
    """The real declared-profile client, with every careers URL sent to a local server instead of the site."""

    def __init__(self, port):
        self.real, self.port = UrllibClient("declared"), port

    def get(self, url, headers=None, *, follow_redirects=False):
        parts = urlsplit(url)
        got = self.real.get(f"http://127.0.0.1:{self.port}{parts.path or '/'}", headers,
                            follow_redirects=follow_redirects)
        return Response(got.status, url, got.body, got.headers)  # as if the site itself had answered


class CareersRequestHeadersTest(unittest.TestCase):
    def test_all_three_requests_carry_the_shared_user_agent_and_no_contact_address(self):
        seen: list[dict] = []
        handler = type("Handler", (_Local,), {"seen": seen})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        result = CareersChecker(_ToLocal(server.server_address[1])).check("acme.com")
        self.assertEqual(result.kind, "page")  # /careers 404, the homepage's link, the page: three requests
        self.assertEqual(len(seen), 3)
        for headers in seen:
            self.assertEqual(headers["user-agent"], USER_AGENT)
            self.assertFalse([v for v in headers.values() if "@" in v], headers)
        self.assertNotIn("@", USER_AGENT)

    def test_the_checker_adds_no_headers_of_its_own(self):
        client = FakeClient({"https://acme.com/careers": status_response(404), "https://acme.com/": status_response(404)})
        CareersChecker(client).check("acme.com")
        self.assertEqual([headers for _, _, headers, _ in client.calls], [{}, {}])  # the client's profile decides



if __name__ == "__main__":
    unittest.main()