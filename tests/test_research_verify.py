import unittest

from outreach.net import FetchError, Response
from outreach.research.store import Fact
from outreach.research.verify import check_facts, page_text, quote_on_page, refused_url


class FakeClient:
    def __init__(self, pages, final=None):
        self.pages = pages
        self.final = final or {}
        self.calls = []

    def get(self, url, headers=None, *, follow_redirects=False, max_bytes=None):
        self.calls.append(url)
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        status, body, ctype = page
        headers = ctype if isinstance(ctype, dict) else {"content-type": ctype}
        return Response(status=status, url=self.final.get(url, url),
                        body=body.encode() if isinstance(body, str) else body, headers=headers)


HTML = "text/html; charset=utf-8"


class PageCheckTest(unittest.TestCase):
    def test_page_text_strips_tags_scripts_and_entities(self):
        html = ("<html><head><style>p{color:red}</style><script>var x='hidden';</script></head>"
                "<body><p>We  build<br>tools &amp; more</p></body></html>")
        self.assertEqual(page_text(html), "We build tools & more")

    def test_quote_across_tags_and_lines_matches(self):
        text = page_text("<p>We raised a <b>Series\n A</b> in  2024. It’s “big”.</p>")
        self.assertTrue(quote_on_page(text, "series a in 2024"))
        self.assertTrue(quote_on_page(text, "It's \"big\""))
        self.assertFalse(quote_on_page(text, "series b"))

    def test_quote_not_on_page_is_rejected(self):
        c = FakeClient({"https://a.example/x": (200, "<p>hello world</p>", HTML)})
        kept, rejected = check_facts(c, [Fact("f", "goodbye", "https://a.example/x")])
        self.assertEqual(kept, [])
        self.assertEqual(rejected, [{"url": "https://a.example/x", "quote": "goodbye",
                                     "reason": "quote not found on the page"}])

    def test_unreachable_page_rejects_only_that_fact(self):
        c = FakeClient({
            "https://a.example/ok": (200, "<p>we ship daily</p>", HTML),
            "https://b.example/down": FetchError("timeout"),
            "https://c.example/no": (403, "", HTML),
            "https://d.example/pdf": (200, "we ship daily", "application/pdf"),
        })
        good = Fact("g", "we ship daily", "https://a.example/ok")
        facts = [good, Fact("t", "q", "https://b.example/down"), Fact("n", "q", "https://c.example/no"),
                 Fact("p", "we ship daily", "https://d.example/pdf")]
        kept, rejected = check_facts(c, facts)
        self.assertEqual(kept, [good])
        reasons = {r["url"]: r["reason"] for r in rejected}
        self.assertEqual(reasons["https://b.example/down"], "page could not be fetched: timeout")
        self.assertEqual(reasons["https://c.example/no"], "page answered 403")
        self.assertIn("not a text page", reasons["https://d.example/pdf"])

    def test_refused_urls(self):
        self.assertEqual(refused_url("https://www.linkedin.com/company/x"), "LinkedIn pages are not used")
        self.assertEqual(refused_url("https://lnkd.in/abc"), "LinkedIn pages are not used")
        self.assertEqual(refused_url("https://rocketreach.co/x"), "contact-data sites are not used")
        self.assertEqual(refused_url("https://app.apollo.io/x"), "contact-data sites are not used")
        self.assertEqual(refused_url("http://a.example/x"), "only https pages")
        self.assertIsNone(refused_url("https://a.example/x"))

    def test_refused_url_is_never_fetched(self):
        c = FakeClient({})
        kept, rejected = check_facts(c, [Fact("f", "q", "https://lnkd.in/a")])
        self.assertEqual((kept, c.calls), ([], []))
        self.assertEqual(rejected[0]["reason"], "LinkedIn pages are not used")

    def test_private_hosts_are_refused_without_a_fetch(self):
        hosts = ["https://10.0.0.5/x", "https://localhost:3100/x", "https://192.168.1.2/x", "https://[::1]/x",
                 "https://printer.local/x", "https://db.internal/x", "https://127.0.0.1/x",
                 "https://2130706433/", "https://127.1/", "https://0x7f.1/", "https://localhost./",
                 "https://foo.local./", "https://db.internal./"]
        for url in hosts:
            with self.subTest(url=url):
                self.assertEqual(refused_url(url), "private or local addresses are not used")
                c = FakeClient({})
                kept, rejected = check_facts(c, [Fact("f", "q", url)])
                self.assertEqual((kept, c.calls), ([], []))
                self.assertEqual(rejected[0]["reason"], "private or local addresses are not used")

    def test_a_public_host_still_passes(self):
        for url in ("https://a.example/x", "https://example.com./x", "https://sub.acme.io/about"):
            self.assertIsNone(refused_url(url))

    def test_a_redirect_to_a_private_or_refused_target_is_not_fetched(self):
        for target, reason in (("http://127.0.0.1:3100/", "only https pages"),
                               ("https://127.0.0.1:3100/", "private or local addresses are not used"),
                               ("https://www.linkedin.com/x", "LinkedIn pages are not used")):
            with self.subTest(target=target):
                c = FakeClient({"https://a.example/r": (302, "", {"location": target})})
                kept, rejected = check_facts(c, [Fact("a", "q", "https://a.example/r")])
                self.assertEqual((kept, c.calls), ([], ["https://a.example/r"]))
                self.assertEqual(rejected[0]["reason"], reason)

    def test_a_normal_redirect_is_followed_and_the_quote_found_there(self):
        c = FakeClient({"https://a.example/old": (301, "", {"location": "/new"}),
                        "https://a.example/new": (200, "<p>we ship daily</p>", HTML)})
        kept, rejected = check_facts(c, [Fact("a", "we ship daily", "https://a.example/old")])
        self.assertEqual((len(kept), rejected), (1, []))
        self.assertEqual(c.calls, ["https://a.example/old", "https://a.example/new"])

    def test_more_than_three_hops_is_rejected(self):
        pages = {f"https://a.example/{n}": (302, "", {"location": f"/{n + 1}"}) for n in range(6)}
        c = FakeClient(pages)
        kept, rejected = check_facts(c, [Fact("a", "q", "https://a.example/0")])
        self.assertEqual(kept, [])
        self.assertEqual(rejected[0]["reason"], "page redirected too many times")
        self.assertEqual(len(c.calls), 4)

    def test_one_fetch_per_url(self):
        c = FakeClient({"https://a.example/x": (200, "<p>one two three</p>", HTML)})
        kept, _ = check_facts(c, [Fact("a", "one", "https://a.example/x"), Fact("b", "two", "https://a.example/x"),
                                  Fact("c", "three", "https://a.example/x")])
        self.assertEqual(len(kept), 3)
        self.assertEqual(c.calls, ["https://a.example/x"])


if __name__ == "__main__":
    unittest.main()
