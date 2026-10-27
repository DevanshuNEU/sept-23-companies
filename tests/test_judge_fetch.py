"""The judge's page reads: robots.txt first, the crawler's polite client, caps, and honest failures (Part of #6 and
#15). Every request goes to a fake client; no test reaches the network."""
import dataclasses
import unittest

from outreach.crawl.config import load_crawl_config
from outreach.crawl.polite import PoliteClient
from outreach.judge.config import load_judge_config
from outreach.judge.fetch import SiteReader
from outreach.net import FetchError, Response
from tests.fakes import FakeClient, status_response, text_response

HTML = {"content-type": "text/html; charset=utf-8"}
BODY = ("<html><head><script>var x = 'not text';</script></head><body><h1>Acme</h1><p>Acme builds email inboxes "
        "for AI agents, so an agent can send, receive and search mail on its own.</p>"
        "<p>We are a small team in San Francisco and we hire new grads who ship.</p>"
        "<p>Our customers are developers building agents, and they read our docs before they talk to us.</p>"
        "<a href='/about'>About us</a><a href='https://other.example/about'>Partner</a></body></html>")
ALLOW = "User-agent: *\nDisallow: /private\n"
NOW = "2026-10-27T10:10:00Z"


def page(text: str = BODY, *, status: int = 200, headers=None) -> Response:
    return text_response(text, status=status, headers=HTML if headers is None else headers)


class Reader(unittest.TestCase):
    def reader(self, routes, **cfg_over) -> tuple[SiteReader, FakeClient]:
        fake = FakeClient(routes)
        crawl = load_crawl_config()
        polite = PoliteClient(fake, crawl, clock=lambda: 0.0, sleep=lambda s: None)
        cfg = dataclasses.replace(load_judge_config(), **cfg_over)
        return SiteReader(polite, cfg=cfg, user_agent=crawl.user_agent, max_redirects=3, now=lambda: NOW), fake

    def test_a_page_robots_allows_is_read_as_text_and_capped(self):
        r, fake = self.reader({"https://acme.example/robots.txt": text_response(ALLOW),
                               "https://acme.example/": page()}, page_max_chars=60)
        got = r.read("https://acme.example/", "home")
        self.assertEqual("ok", got.page["status"])
        self.assertTrue(got.page["text"].startswith("Acme\nAcme builds email inboxes"))
        self.assertEqual(60, len(got.page["text"]))
        self.assertNotIn("not text", got.page["text"])
        self.assertEqual(NOW, got.page["fetched_at"])
        self.assertEqual([], fake.unrouted)

    def test_robots_disallow_is_honoured_and_the_page_is_never_requested(self):
        r, fake = self.reader({"https://acme.example/robots.txt": text_response("User-agent: *\nDisallow: /\n")})
        got = r.read("https://acme.example/", "home")
        self.assertEqual("unavailable", got.page["status"])
        self.assertIn("robots.txt", got.page["why"])
        self.assertEqual(["https://acme.example/robots.txt"], fake.requests)

    def test_robots_is_read_with_the_crawler_user_agent_rules(self):
        rules = "User-agent: outreach-crawler\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
        r, fake = self.reader({"https://acme.example/robots.txt": text_response(rules)})
        self.assertEqual("unavailable", r.read("https://acme.example/", "home").page["status"])
        self.assertEqual(["https://acme.example/robots.txt"], fake.requests)

    def test_robots_is_read_once_per_site_and_every_request_says_who_it_is(self):
        r, fake = self.reader({"https://acme.example/robots.txt": text_response(ALLOW),
                               "https://acme.example/": page(), "https://acme.example/about": page()})
        r.read("https://acme.example/", "home")
        r.read("https://acme.example/about", "about")
        self.assertEqual(1, fake.requests.count("https://acme.example/robots.txt"))
        agents = {h.get("User-Agent") for _m, _u, h, _b in fake.calls}
        self.assertEqual({load_crawl_config().user_agent}, agents)

    def test_a_missing_robots_allows_and_a_forbidden_or_broken_one_does_not(self):
        cases = ((status_response(404), "ok"), (status_response(410), "ok"), (status_response(401), "unavailable"),
                 (status_response(403), "unavailable"), (status_response(503), "unavailable"),
                 (FetchError("timeout", "slow"), "unavailable"))
        for robots, want in cases:
            with self.subTest(robots=getattr(robots, "status", robots)):
                r, fake = self.reader({"https://acme.example/robots.txt": robots, "https://acme.example/": page()})
                self.assertEqual(want, r.read("https://acme.example/", "home").page["status"])
                if want == "unavailable":
                    self.assertNotIn("https://acme.example/", fake.requests)

    def test_a_redirect_to_another_site_asks_that_site_first(self):
        moved = Response(301, "", b"", {"location": "https://new.example/"})
        r, fake = self.reader({"https://acme.example/robots.txt": text_response(ALLOW), "https://acme.example/": moved,
                               "https://new.example/robots.txt": text_response("User-agent: *\nDisallow: /\n")})
        got = r.read("https://acme.example/", "home")
        self.assertEqual("unavailable", got.page["status"])
        self.assertNotIn("https://new.example/", fake.requests)

    def test_a_followed_redirect_is_read(self):
        moved = Response(302, "", b"", {"location": "/home"})
        r, _ = self.reader({"https://acme.example/robots.txt": text_response(ALLOW), "https://acme.example/": moved,
                            "https://acme.example/home": page()})
        got = r.read("https://acme.example/", "home")
        self.assertEqual(("ok", "https://acme.example/home"), (got.page["status"], got.page["url"]))

    def test_too_large_slow_or_failing_pages_say_so(self):
        for answer, why in ((FetchError("too_large", "over"), "too_large"), (FetchError("timeout", "x"), "timeout"),
                            (status_response(500), "HTTP 500"), (page("x" * 3000), "too large")):
            with self.subTest(why=why):
                r, _ = self.reader({"https://acme.example/robots.txt": text_response(ALLOW),
                                    "https://acme.example/": answer}, max_bytes=2000)
                got = r.read("https://acme.example/", "home")
                self.assertEqual("unavailable", got.page["status"])
                self.assertIn(why, got.page["why"])
                self.assertEqual("", got.page["text"])

    def test_a_javascript_shell_a_challenge_or_a_file_is_not_read(self):
        for answer in (page("<html><body><div id=root></div></body></html>"),
                       page("<html><body>Just a moment... checking your browser</body></html>"),
                       page(BODY, headers={"content-type": "application/pdf"})):
            with self.subTest(answer=answer.body[:30]):
                r, _ = self.reader({"https://acme.example/robots.txt": text_response(ALLOW),
                                    "https://acme.example/": answer})
                self.assertEqual("unavailable", r.read("https://acme.example/", "home").page["status"])

    def test_linkedin_and_non_web_urls_are_never_requested(self):
        r, fake = self.reader({})
        for url in ("https://www.linkedin.com/company/acme", "https://lnkd.in/x", "ftp://acme.example/",
                    "file:///etc/passwd", "javascript:alert(1)"):
            with self.subTest(url=url):
                self.assertEqual("unavailable", r.read(url, "home").page["status"])
        self.assertEqual([], fake.requests)

    def test_a_switched_off_ats_host_is_never_requested(self):
        r, fake = self.reader({})
        got = r.read("https://jobs.smartrecruiters.com/acme/123", "posting")
        self.assertEqual("unavailable", got.page["status"])
        self.assertEqual([], fake.requests)

    def test_the_about_link_is_found_on_the_same_site_only(self):
        r, _ = self.reader({"https://acme.example/robots.txt": text_response(ALLOW), "https://acme.example/": page()})
        got = r.read("https://acme.example/", "home")
        self.assertEqual("https://acme.example/about", r.about_url(got))
        self.assertIsNone(r.about_url(r.read("https://www.linkedin.com/x", "home")))


if __name__ == "__main__":
    unittest.main()
