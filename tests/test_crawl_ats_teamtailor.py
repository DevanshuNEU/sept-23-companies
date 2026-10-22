"""The Teamtailor adapter (issue #21, Task 23)."""
import re
import unittest
from pathlib import Path
from unittest import mock

from outreach.crawl.ats import ADAPTERS, adapter_for, supported_ats
from outreach.crawl.ats import teamtailor
from outreach.crawl.ats.teamtailor import TeamtailorAdapter
from outreach.crawl.base import Validators, ids_fingerprint
from outreach.net import FetchError, Response
from tests.contracts import check_adapter
from tests.fakes import FakeClient, status_response, text_response

FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
TEMPLATE = (FIXTURES / "teamtailor_p0.xml").read_text()
HTML = (FIXTURES / "teamtailor_html.html").read_text()
ETAG = 'W/"tt-1"'


def url(offset=0, size=100, token="acme"):
    return f"https://{token}.teamtailor.com/jobs.rss?per_page={size}&offset={offset}"


def page(first=1001, second=1002):
    """The 2-item template with its two job numbers replaced."""
    return TEMPLATE.replace("1001", str(first)).replace("1002", str(second))


def one_item(job=1001):
    head, rest = TEMPLATE.split("<item>", 1)
    item = "<item>" + rest.split("</item>", 1)[0] + "</item>"
    return head + item.replace("1001", str(job)) + "</channel></rss>"


def feed(body, etag=ETAG):
    return Response(200, "", body.encode("utf-8"), {"etag": etag} if etag else {})


def read(routes, token="acme", validators=Validators(), size=100):
    client = FakeClient(routes)
    with mock.patch.object(teamtailor, "PAGE_SIZE", size):
        return TeamtailorAdapter(client).list(token, validators), client


def with_item_field(field, value):
    return one_item().replace(field, value)


class TeamtailorTests(unittest.TestCase):
    def test_check_adapter(self):
        client = FakeClient({url(): [feed(page()), status_response(304), status_response(500)]})
        check_adapter(self, TeamtailorAdapter(client), "acme")

    def test_registered(self):
        self.assertIs(ADAPTERS["teamtailor"], TeamtailorAdapter)
        self.assertIn("teamtailor", supported_ats())
        self.assertIsInstance(adapter_for("teamtailor", FakeClient({})), TeamtailorAdapter)

    def test_list_ok_parses_items(self):
        r, client = read({url(): feed(page())})
        self.assertEqual((client.requests, client.unrouted), ([url()], []))
        self.assertEqual(r.result, "ok")
        first, second = r.jobs
        self.assertEqual((first.native_id, first.title, first.url, first.location, first.country, first.posted_on),
                         ("1001", "Software Engineer", "https://acme.teamtailor.com/jobs/1001-software-engineer",
                          "Stockholm HQ", None, "2026-09-14"))
        self.assertEqual(first.body, "Build things.\nShip & learn\nWe sponsor visas")
        self.assertEqual((second.native_id, second.location, second.posted_on, second.body),
                         ("1002", "Berlin, Germany; Remote", "2026-10-03", "Analyse data.\nRemote friendly."))
        self.assertEqual(r.validators, Validators(etag=ETAG, ids_fingerprint=ids_fingerprint(["1001", "1002"])))

    def test_html_instead_of_rss_is_error(self):
        for body in (HTML, "<html><body><p>Not a feed</p></body></html>", "",
                     '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body/></html>',
                     '{"jobs": []}'):
            r, _ = read({url(): feed(body)})
            self.assertEqual((r.result, r.jobs), ("error", ()), body[:40])

    def test_truncated_or_hostile_xml_is_error(self):
        cut = page()[:len(page()) // 2]
        entity = '<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x "boom">]>' + one_item().split("?>", 1)[1]
        for body in (cut, entity, one_item().replace("</channel>", "")):
            r, _ = read({url(): feed(body)})
            self.assertEqual((r.result, r.jobs), ("error", ()), body[:60])

    def test_an_rss_with_no_items_is_empty(self):
        empty = "<?xml version='1.0'?><rss version='2.0'><channel><title>Acme</title></channel></rss>"
        r, _ = read({url(): feed(empty)})
        self.assertEqual((r.result, r.jobs, r.validators), ("empty", (), Validators()))

    def test_an_rss_without_exactly_one_channel_is_error(self):
        item = one_item().split("<item>", 1)[1].split("</item>", 1)[0]
        for body in ("<?xml version='1.0'?><rss version='2.0'></rss>",
                     "<?xml version='1.0'?><rss version='2.0'><error>Rate limited</error></rss>",
                     "<?xml version='1.0'?><rss version='2.0'><item>" + item + "</item></rss>",
                     "<?xml version='1.0'?><rss version='2.0'><channel><title>A</title></channel>"
                     "<channel><title>B</title></channel></rss>"):
            r, _ = read({url(): feed(body)})
            self.assertEqual((r.result, r.jobs), ("error", ()), body[:70])

    def test_a_doctype_inside_cdata_is_text_not_a_declaration(self):
        body = one_item().replace("<![CDATA[", "").replace("]]>", "")
        body = body.replace("Build things.", "Build things. <![CDATA[ <!DOCTYPE x> ]]>")
        r, _ = read({url(): feed(body)})
        self.assertEqual(r.result, "ok")

    def test_a_declared_encoding_other_than_utf_8_is_error(self):
        for decl in ("ISO-8859-1", "utf-16", "windows-1252"):
            body = one_item().replace('encoding="UTF-8"', f'encoding="{decl}"')
            self.assertIn(decl, body)
            r, _ = read({url(): feed(body)})
            self.assertEqual((r.result, r.jobs), ("error", ()), decl)
        for decl in ("UTF-8", "utf-8"):
            r, _ = read({url(): feed(one_item().replace('encoding="UTF-8"', f'encoding="{decl}"'))})
            self.assertEqual(r.result, "ok", decl)

    def test_a_description_is_decoded_once(self):
        # ElementTree already decoded the feed's escapes: an escaped "<b>" in the posting is text, not a tag to strip.
        body = one_item().replace("Build things.", "Use &amp;lt;b&amp;gt; for bold.")
        r, _ = read({url(): feed(body)})
        self.assertIn("Use <b> for bold.", r.jobs[0].body)

    def test_full_page_then_empty_page_is_complete(self):
        empty = "<?xml version='1.0'?><rss version='2.0'><channel><title>Acme</title></channel></rss>"
        r, client = read({url(0, 2): feed(page()), url(2, 2): feed(empty)}, size=2)
        self.assertEqual((r.result, [j.native_id for j in r.jobs]), ("ok", ["1001", "1002"]))
        self.assertEqual(client.requests, [url(0, 2), url(2, 2)])

    def test_pages_are_read_until_a_short_page(self):
        routes = {url(0, 2): feed(page(1, 2)), url(2, 2): feed(page(3, 4), etag=""), url(4, 2): feed(one_item(5))}
        r, client = read(routes, size=2)
        self.assertEqual((r.result, [j.native_id for j in r.jobs]), ("ok", ["1", "2", "3", "4", "5"]))
        self.assertEqual(client.requests, [url(0, 2), url(2, 2), url(4, 2)])

    def test_offset_ignored_is_an_error(self):
        # The API answers the first page for every offset: reading on would loop, and stopping would be a guess.
        r, client = read({url(0, 2): feed(page()), url(2, 2): feed(page())}, size=2)
        self.assertEqual((r.result, r.jobs), ("error", ()))
        self.assertIn("pagination not honored", r.error)
        self.assertEqual(len(client.requests), 2)
        # One repeated id among new ones is the same fault.
        r, _ = read({url(0, 2): feed(page(1, 2)), url(2, 2): feed(page(2, 3))}, size=2)
        self.assertEqual((r.result, r.jobs), ("error", ()))
        self.assertIn("pagination not honored", r.error)

    def test_a_page_cap_makes_an_unfinished_list_an_error(self):
        routes = {url(2 * n, 2): feed(page(2 * n + 1, 2 * n + 2)) for n in range(5)}
        with mock.patch.object(teamtailor, "MAX_PAGES", 3):
            r, client = read(routes, size=2)
        self.assertEqual((r.result, r.jobs), ("error", ()))
        self.assertEqual(len(client.requests), 3)

    def test_a_later_page_that_fails_makes_the_read_an_error(self):
        for second in (status_response(500), status_response(403), status_response(304), status_response(302),
                       feed(HTML), FetchError("network", "down")):
            r, _ = read({url(0, 2): feed(page()), url(2, 2): second}, size=2)
            self.assertEqual((r.result, r.jobs), ("error", ()), second)

    def test_a_multi_page_read_never_stores_the_first_page_etag(self):
        # The etag covers page one only: a change on page two would hide behind a 304.
        routes = {url(0, 2): [feed(page(1, 2)), feed(page(1, 2))], url(2, 2): [feed(one_item(3)), feed(one_item(3))]}
        first, _ = read(routes, size=2)
        self.assertEqual(first.validators, Validators(etag="", ids_fingerprint=ids_fingerprint(["1", "2", "3"])))
        again, client = read(routes, size=2, validators=first.validators)
        self.assertEqual(again.result, "unchanged")
        self.assertNotIn("If-None-Match", client.calls[0][2])

    def test_a_single_page_etag_is_sent_on_the_first_page_only(self):
        first, _ = read({url(): feed(page())})
        again, client = read({url(): status_response(304)}, validators=first.validators)
        self.assertEqual(client.calls[0][2]["If-None-Match"], ETAG)
        self.assertEqual((again.result, again.jobs, again.validators), ("unchanged", (), first.validators))
        routes = {url(0, 2): feed(page()), url(2, 2): feed(one_item(3))}
        _, client = read(routes, size=2, validators=Validators(etag=ETAG))
        self.assertEqual(["If-None-Match" in c[2] for c in client.calls], [True, False])

    def test_a_304_without_if_none_match_is_an_error(self):
        r, client = read({url(): status_response(304)}, validators=Validators(ids_fingerprint="x"))
        self.assertNotIn("If-None-Match", client.calls[0][2])
        self.assertEqual((r.result, r.http_status), ("error", 304))

    def test_last_modified_is_not_needed(self):
        # No ETag and no Last-Modified: the ids decide, and no conditional header is ever sent.
        routes = {url(): [feed(page(), etag=""), feed(page(), etag="")]}
        first, _ = read(routes)
        self.assertEqual(first.validators, Validators(etag="", ids_fingerprint=ids_fingerprint(["1001", "1002"])))
        again, client = read(routes, validators=first.validators)
        self.assertEqual((again.result, again.jobs), ("unchanged", ()))
        sent = {k.lower() for _m, _u, h, _b in client.calls for k in h}
        self.assertFalse(sent & {"if-none-match", "if-modified-since"})

    def test_http_failures_are_errors(self):
        for status in (403, 404, 429, 500, 301, 302):
            r, _ = read({url(): status_response(status)})
            self.assertEqual((r.result, r.http_status, r.jobs), ("error", status, ()), status)
        r, _ = read({url(): FetchError("network", "down")})
        self.assertEqual((r.result, r.http_status), ("error", None))

    def test_an_item_without_a_usable_id_or_title_is_an_error(self):
        link = "https://acme.teamtailor.com/jobs/1001-software-engineer"
        cases = {"no title": one_item().replace("<title>Software Engineer</title>", "<title> </title>"),
                 "no id anywhere": one_item().replace(link, "https://acme.teamtailor.com/careers/x").replace(
                     "<guid isPermaLink=\"true\">https://acme.teamtailor.com/careers/x</guid>", "<guid></guid>")}
        for name, body in cases.items():
            r, _ = read({url(): feed(body)})
            self.assertEqual((r.result, r.jobs), ("error", ()), name)

    def test_the_guid_is_the_id_when_the_link_has_no_job_number(self):
        body = one_item().replace("<link>https://acme.teamtailor.com/jobs/1001-software-engineer</link>",
                                  "<link>https://careers.acme.example/open-roles</link>")
        r, _ = read({url(): feed(body)})
        self.assertEqual((r.result, r.jobs[0].native_id, r.jobs[0].url), ("ok", "1001",
                                                                          "https://careers.acme.example/open-roles"))

    def test_a_link_that_is_not_a_web_address_is_an_empty_url(self):
        for value in ("javascript:alert(1)", "/jobs/1001-x", ""):
            body = one_item().replace("<link>https://acme.teamtailor.com/jobs/1001-software-engineer</link>",
                                      f"<link>{value}</link>")
            r, _ = read({url(): feed(body)})
            self.assertEqual((r.result, r.jobs[0].url), ("ok", ""), value)

    def test_pub_date_is_a_real_date_or_none(self):
        stamp = "Mon, 14 Sep 2026 09:12:00 +0200"
        for value, expected in ((stamp, "2026-09-14"), ("Sat, 03 Oct 2026 23:59:00 -0700", "2026-10-03"),
                                ("yesterday", None), ("", None), ("Mon, 31 Feb 2026 09:12:00 +0000", None),
                                ("2026-09-14", None)):
            body = one_item().replace(stamp, value)
            r, _ = read({url(): feed(body)})
            self.assertEqual(r.jobs[0].posted_on, expected, value)

    def test_missing_description_and_locations_are_empty_not_errors(self):
        body = re.sub(r"<description>.*?</description>|<tt:locations>.*?</tt:locations>", "", one_item(), flags=re.S)
        r, _ = read({url(): feed(body)})
        self.assertEqual((r.result, r.jobs[0].location, r.jobs[0].body), ("ok", "", None))

    def test_duplicate_ids_are_kept_once(self):
        r, _ = read({url(): feed(page(1001, 1001))})
        self.assertEqual([j.native_id for j in r.jobs], ["1001"])

    def test_bad_token_makes_no_request(self):
        for token in ("a/b", "A", "", "a b", "a%20b", "a.b", "a_b", "-a", "a-", "a@b", "a:80"):
            r, client = read({}, token=token)
            self.assertEqual((r.result, client.calls), ("error", []), token)

    def test_body_has_nothing_to_fetch(self):
        client = FakeClient({})
        self.assertIsNone(TeamtailorAdapter(client).body("acme", mock.Mock()))
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
