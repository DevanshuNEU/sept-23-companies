"""The HN "Who is hiring" source (#31 Task 12): header parsing, the US filter, domains and ATS keys, the Algolia calls,
errors, and the fixture thread end to end through run_discover after the LCA fixtures."""
import json
import unittest
from pathlib import Path

from outreach.net import FetchError
from outreach.sources.base import Candidate
from outreach.sources.capabilities import PostRecord
from outreach.sources.hn import HnError, HnSource, HnThreadNotOut, Post, is_us, parse_post
from outreach.sources.jobs import run_discover
from outreach.sources.lca import LcaSource
from outreach.store import name_key
from tests.contracts import check_source
from tests.fakes import FakeClient, json_response, status_response
from tests.storetest import fresh_store, table_marks
from tests.test_lca_source import FILES

FIXTURES = Path(__file__).parent / "fixtures" / "hn"
SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=20"
SEP, OCT, NOV = 60000001, 61000001, 62000001
EM, EN = chr(0x2014), chr(0x2013)  # no dash character in this file


def item_url(thread_id):
    return f"https://hn.algolia.com/api/v1/items/{thread_id}"


def post_url(comment_id):
    return f"https://news.ycombinator.com/item?id={comment_id}"


def fixture_client(*thread_ids):
    routes = {SEARCH_URL: FIXTURES / "search_whoishiring.json"}
    for tid in thread_ids or (OCT,):
        routes[item_url(tid)] = FIXTURES / f"item_{tid}.json"
    return FakeClient(routes)


def november_client():
    search = json.loads((FIXTURES / "search_whoishiring.json").read_text(encoding="utf-8"))
    search["hits"].insert(0, {"objectID": str(NOV), "title": "Ask HN: Who is hiring? (November 2026)",
                              "created_at": "2026-11-02T15:00:00.000Z"})
    return FakeClient({SEARCH_URL: json_response(search), item_url(NOV): FIXTURES / f"item_{NOV}.json"})


def thread_client(comments, *, thread_id=OCT, title="Ask HN: Who is hiring? (October 2026)"):
    """A one-thread client whose top-level comments are the given HTML strings (None is a deleted comment)."""
    search = {"hits": [{"objectID": str(thread_id), "title": title, "created_at": "2026-10-01T15:00:00.000Z"}]}
    children = [{"id": 70000000 + i, "created_at": "2026-10-01T15:00:00.000Z", "text": text, "children": []}
                for i, text in enumerate(comments)]
    item = {"id": thread_id, "type": "story", "title": title, "children": children}
    return FakeClient({SEARCH_URL: json_response(search), item_url(thread_id): json_response(item)})


def a(url):
    return f'<a href="{url}" rel="nofollow">{url}</a>'


class ParsePostTest(unittest.TestCase):
    def test_parse_post_real_header_shapes(self):
        p = parse_post("Orchard (YC S21) | Backend Engineer | NYC | REMOTE | " + a("https://orchard.io/jobs"))
        self.assertIsInstance(p, Post)
        self.assertEqual((p.name, p.stage), ("Orchard", "yc"))
        self.assertEqual(p.links, ("https://orchard.io/jobs",))
        self.assertEqual(p.location, "NYC")
        self.assertEqual(parse_post(f"PrairieLearn (Remote US) {EM} Full-Stack Software Engineer").name, "PrairieLearn")
        self.assertEqual(parse_post(f"PrairieLearn (Remote US) {EN} Full-Stack Software Engineer").name, "PrairieLearn")
        self.assertEqual(parse_post("Thunder Compute (YC S24)").name, "Thunder Compute")
        self.assertEqual(parse_post("Shepherd (Series B)").stage, "series_b")
        self.assertIsNone(parse_post("Thanks for the thread, great month everyone"))

    def test_the_header_is_the_first_paragraph_and_links_are_hrefs(self):
        html = ("Acme Robotics | Engineer | " + a("https://acme.io") + "<p>Body text " + a("https://jobs.lever.co/acme")
                + "<p>More")
        p = parse_post(html)
        self.assertEqual(p.header, "Acme Robotics | Engineer | https://acme.io")
        self.assertEqual(p.links, ("https://acme.io",))
        self.assertEqual(p.more_links, ("https://jobs.lever.co/acme",))

    def test_anchor_text_is_never_the_link(self):
        # HN shortens the shown text of a long URL; the href is the real one
        p = parse_post('Acme | Eng | <a href="https:&#x2F;&#x2F;jobs.lever.co&#x2F;acmelabs&#x2F;12345" rel="nofollow">'
                       'https:&#x2F;&#x2F;jobs.lever.co&#x2F;acme...</a>')
        self.assertEqual(p.links, ("https://jobs.lever.co/acmelabs/12345",))

    def test_separators(self):
        for header in ("Acme | Eng", f"Acme {EM} Eng", f"Acme {EN} Eng", "Acme - Eng"):
            self.assertEqual(parse_post(header).name, "Acme", header)
        # no spaces around the hyphen: it is part of the name
        self.assertEqual(parse_post("Acme-Corp | Eng").name, "Acme-Corp")

    def test_name_rules(self):
        self.assertIsNone(parse_post("One Two Three Four Five Six | Eng"))  # over 5 words
        self.assertEqual(parse_post("One Two Three Four Five | Eng").name, "One Two Three Four Five")
        for bad in ("Hiring: Acme | Eng", "Is anyone hiring? | Eng", "Wow! | Eng", " | Eng", ""):
            self.assertIsNone(parse_post(bad), bad)
        self.assertEqual(parse_post("Acme https://acme.io | Eng").name, "Acme")  # a URL in the name is removed
        self.assertEqual(parse_post("Acme (YC W26) (hiring) | Eng").name, "Acme")

    def test_a_header_without_a_separator_over_five_words_is_dropped(self):
        self.assertIsNone(parse_post("We are hiring a lot of people this month"))
        self.assertEqual(parse_post("Thunder Compute (YC S24)").name, "Thunder Compute")

    def test_a_bare_header_needs_a_second_signal(self):
        # I1: a header that is one segment, with no parenthetical, link or "is hiring", is chatter, title case or not
        for remark in ("Great thread, thank you all", "thanks everyone", "Is anyone hiring", "Yes", "Hi",
                       "Thanks A Lot", "Looking For Work", "Thunder Compute", "iRobot"):
            self.assertIsNone(parse_post(remark), remark)
        self.assertEqual(parse_post("acme labs (YC S24)").name, "acme labs")  # a parenthetical vouches for a name
        self.assertEqual(parse_post("Acme Labs " + a("https://acme.io")).name, "Acme Labs")  # so does a link
        self.assertEqual(parse_post("Acme Labs is hiring").name, "Acme Labs")  # and so does "is hiring"

    def test_stages(self):
        cases = {"(YC S24)": "yc", "(YC W26)": "yc", "(YC)": "yc", "(Seed)": "seed", "(Series A)": "series_a",
                 "(Series B)": "series_b", "(Series C)": "series_c", "(Series D)": "series_d",
                 "(Series E)": "series_e_plus", "(series a)": "series_a", "(YC W20, Series B)": "series_b",
                 "(Remote US)": None, "": None}
        for paren, stage in cases.items():
            self.assertEqual(parse_post(f"Acme {paren} | Eng").stage, stage, paren)

    def test_location_is_the_first_place_segment_and_remote_alone_is_not_one(self):
        self.assertEqual(parse_post("Acme | Eng | REMOTE | NYC").location, "NYC")
        self.assertEqual(parse_post("Acme | Eng | San Francisco, CA | Remote OK").location, "San Francisco, CA")
        self.assertEqual(parse_post("Acme | Eng | London, UK").location, "London, UK")
        self.assertIsNone(parse_post("Acme | Eng | Remote (US) | full time").location)
        self.assertIsNone(parse_post("Acme | Eng | Python").location)


class IsUsTest(unittest.TestCase):
    def test_is_us(self):
        self.assertFalse(is_us("Quasar Games | Unity dev | London, UK"))
        self.assertTrue(is_us("Tidepool Apps | Full-stack | Remote (US)"))
        self.assertTrue(is_us("Mesh Systems | SWE | San Francisco"))
        self.assertTrue(is_us("Acme | Backend engineer"))  # no location: never grounds to hide

    def test_non_us_places(self):
        for header in ("Acme | Eng | Berlin", "Acme | Eng | Remote (Europe)", "Acme | Eng | Toronto, Canada",
                       "Acme | Eng | Bangalore, India", "Acme | Eng | EU", "Acme | Eng | Remote (EU)"):
            self.assertFalse(is_us(header), header)

    def test_a_us_marker_wins_over_a_non_us_place(self):
        for header in ("Acme | Eng | London or NYC", "Acme | Eng | Berlin, USA", "Acme | Eng | Remote (US or Canada)",
                       "Acme | Eng | Toronto | Boston, MA", "Acme | Eng | United States and Europe",
                       "Acme | Eng | SF or Berlin"):
            self.assertTrue(is_us(header), header)

    def test_an_ambiguous_state_code_is_not_a_us_marker_next_to_a_non_us_place(self):
        self.assertFalse(is_us("Acme | Eng | Toronto, CA"))
        self.assertFalse(is_us("Acme | Eng | Berlin, DE"))
        self.assertTrue(is_us("Acme | Eng | Oakland, CA"))
        self.assertTrue(is_us("Acme | Eng | Wilmington, DE"))

    def test_short_codes_do_not_match_inside_words(self):
        self.assertTrue(is_us("Acme | Eng | Join us in Houston"))
        self.assertTrue(is_us("Acme | We are focused on EUV optics research"))  # EU inside EUV
        self.assertTrue(is_us("Acme | Eng | Remote OK"))


class IsUsProbesTest(unittest.TestCase):
    """Fix round 1, I3: a post is hidden only on a clear non-US place after the name. When in doubt, keep."""

    def test_a_place_word_in_the_company_name_is_not_a_location(self):
        for header in ("Canada Drives | Eng", "UK Power | Eng", "Berlin Packaging | Eng", "India Ink | Eng",
                       "Toronto Dominion | Eng | Remote"):
            self.assertTrue(is_us(header), header)

    def test_a_us_state_name_and_city_codes_keep_the_post(self):
        for header in ("Acme | Eng | Santa Fe, New Mexico", "Acme | Eng | LA or London", "Acme | Eng | La Jolla, CA",
                       "Acme | Eng | LA", "Acme | Eng | Bay area or Toronto", "Acme | Eng | united states or Canada"):
            self.assertTrue(is_us(header), header)

    def test_a_state_code_in_another_segment_or_after_a_us_place_keeps_the_post(self):
        for header in ("Acme | Eng | Culver City, CA | London, UK", "Acme | Eng | Cambridge, MA or London",
                       "Acme | Somerville, MA or Berlin", "Acme | Eng | Irvine, CA or London",
                       "Acme | Eng | Remote (GMT-8 to GMT-5)", "Acme | Eng | CET or GMT"):
            self.assertTrue(is_us(header), header)

    def test_an_ambiguous_code_right_after_a_non_us_place_is_still_non_us(self):
        for header in ("Acme | Eng | Toronto, CA", "Acme | Eng | Berlin, DE", "Acme | Eng | Bangalore, IN"):
            self.assertFalse(is_us(header), header)

    def test_the_name_segment_can_be_any_segment(self):
        self.assertTrue(is_us("Senior Engineer | Canada Drives | Remote", name_index=1))
        self.assertFalse(is_us("Senior Engineer | Canada Drives | Berlin", name_index=1))
        self.assertFalse(is_us("Berlin | Acme | Eng", name_index=1))


class DomainAndKeyTest(unittest.TestCase):
    def candidate(self, *comments):
        return list(HnSource(thread_client(comments)).discover())

    def test_platform_links_are_not_domains(self):
        (c,) = self.candidate("Acme Labs | Eng | " + a("https://github.com/acme"))
        self.assertIsNone(c.domain)
        self.assertEqual([e for e in c.evidence if e.fact == "domain"], [])

    def test_hosting_platform_links_are_not_domains(self):
        (c,) = self.candidate("Acme Labs | Eng | " + a("https://acme-labs.vercel.app/jobs"))
        self.assertIsNone(c.domain)

    def test_the_first_company_link_wins_and_www_is_stripped(self):
        (c,) = self.candidate("Acme Labs | Eng | " + a("https://github.com/acme") + " | " + a("https://www.acme.io/x")
                              + " | " + a("https://other.com"))
        self.assertEqual(c.domain, "acme.io")

    def test_a_subdomain_is_reduced_to_the_registrable_domain_like_lca_does(self):
        (c,) = self.candidate("Acme Labs | Eng | " + a("https://jobs.acme.com/open"))
        self.assertEqual(c.domain, "acme.com")
        (c,) = self.candidate("Acme Labs | Eng | " + a("https://www.acme.co.uk"))
        self.assertEqual(c.domain, "acme.co.uk")

    def test_a_mailto_link_is_not_a_domain(self):
        (c,) = self.candidate("Acme Labs | Eng | " + a("mailto:jobs@acme.io"))
        self.assertIsNone(c.domain)

    def test_a_body_link_is_a_domain_only_when_its_name_matches_the_company(self):
        (c,) = self.candidate("Acme Labs | Eng<p>Press: " + a("https://www.nytimes.com/story"))
        self.assertIsNone(c.domain)
        (c,) = self.candidate("Acme Labs | Eng<p>See " + a("https://acmelabs.io/careers"))
        self.assertEqual(c.domain, "acmelabs.io")

    def test_domain_evidence_is_an_estimate_from_the_post(self):
        (c,) = self.candidate("Acme Labs | Eng | " + a("https://acme.io"))
        (d,) = [e for e in c.evidence if e.fact == "domain"]
        self.assertEqual((d.value, d.confidence, d.source_kind), ("acme.io", "estimate", "hn"))
        self.assertEqual(d.source_ref, post_url(70000000))
        self.assertEqual(d.quote, "Acme Labs | Eng | https://acme.io")

    def test_ats_link_becomes_a_key(self):
        (c,) = self.candidate("Tidepool Apps | Full-stack | " + a("https://tidepoolapps.com")
                              + "<p>Apply: " + a("https://jobs.lever.co/tidepoolapps"))
        self.assertEqual(c.keys["ats"], "lever:tidepoolapps")

    def test_the_first_supported_board_is_the_key_and_extra_boards_are_counted(self):
        source = HnSource(thread_client([
            "Acme Labs | Eng<p>" + a("https://jobs.lever.co/acme") + " " + a("https://boards.greenhouse.io/acme"),
            "Birch Co | Eng<p>" + a("https://jobs.lever.co/birch")]))
        cands = list(source.discover())
        self.assertEqual([c.keys.get("ats") for c in cands], ["lever:acme", "lever:birch"])
        self.assertEqual(source.summary()["multi_board"], 1)

    def test_no_ats_link_means_no_ats_key(self):
        (c,) = self.candidate("Acme Labs | Eng | " + a("https://acme.io"))
        self.assertNotIn("ats", c.keys)

    def test_candidate_shape(self):
        (c,) = self.candidate("Acme Labs (YC S24) | Eng | Austin, TX | " + a("https://acme.io"))
        self.assertIsInstance(c, Candidate)
        self.assertEqual((c.name, c.source_ref), ("Acme Labs", "hn:acmelabs@acme.io"))
        by_fact = {e.fact: e for e in c.evidence}
        self.assertEqual((by_fact["stage"].value, by_fact["stage"].confidence, by_fact["stage"].source_kind),
                         ("yc", "estimate", "hn"))
        self.assertEqual((by_fact["hq"].value, by_fact["hq"].confidence), ("Austin, TX", "guess"))
        self.assertEqual(by_fact["hq"].source_ref, post_url(70000000))

    def test_no_size_or_headcount_evidence(self):
        for c in self.candidate("Acme Labs (Series A) | Eng | Austin, TX | 11-50 people | " + a("https://acme.io")):
            self.assertFalse({e.fact for e in c.evidence} & {"size", "headcount"})

    def test_one_candidate_per_name_across_posts(self):
        cands = self.candidate("Acme Labs | Eng | " + a("https://acme.io"), "ACME LABS, Inc. | Ops | " + a("https://acme.io"))
        self.assertEqual(len(cands), 1)

    def test_a_second_post_with_another_domain_is_another_candidate(self):
        # fix round 1, I6: the domain is part of the ref, so two domains under one name are two companies
        source = HnSource(thread_client(["Acme | Eng | " + a("https://acme.io"), "Acme | Ops | " + a("https://acme.dev")]))
        cands = list(source.discover())
        self.assertEqual([c.domain for c in cands], ["acme.io", "acme.dev"])
        for c in cands:
            self.assertEqual({e.value for e in c.evidence if e.fact == "domain"}, {c.domain})
        self.assertEqual(source.summary()["name_conflicts"], 0)
        self.assertEqual(len(source.posts()), 2)


class CallsTest(unittest.TestCase):
    def test_algolia_calls_are_one_search_and_one_item_per_thread(self):
        client = fixture_client()
        list(HnSource(client).discover())
        self.assertEqual(client.requests, [SEARCH_URL, item_url(OCT)])
        self.assertEqual(client.unrouted, [])

    def test_since_fetches_every_thread_from_that_month_oldest_first(self):
        client = fixture_client(SEP, OCT)
        cands = list(HnSource(client).discover("2026-09"))
        self.assertEqual(client.requests, [SEARCH_URL, item_url(SEP), item_url(OCT)])
        refs = [c.source_ref for c in cands]
        self.assertEqual(len(refs), len(set(refs)), "one candidate per name even across two months")
        self.assertEqual(refs.count("hn:tidepoolapps@tidepoolapps.com"), 1)
        self.assertIn("hn:fernwoodlabs@fernwood.dev", refs)

    def test_since_the_latest_month_fetches_only_it(self):
        client = fixture_client()
        list(HnSource(client).discover("2026-10"))
        self.assertEqual(client.requests, [SEARCH_URL, item_url(OCT)])

    def test_who_wants_to_be_hired_and_freelancer_threads_are_ignored(self):
        client = fixture_client(SEP, OCT)
        list(HnSource(client).discover("2026-01"))
        self.assertEqual(sorted(client.requests), sorted([SEARCH_URL, item_url(SEP), item_url(OCT)]))

    def test_no_request_carries_an_email_address_or_a_contact_header(self):
        client = fixture_client(SEP, OCT)
        list(HnSource(client).discover("2026-09"))
        for method, url, headers, body in client.calls:
            self.assertEqual((method, headers, body), ("GET", {}, None))
            self.assertNotIn("@", url)


class ErrorsTest(unittest.TestCase):
    """Error is not empty: a failed fetch, a bad body or a thread with nothing in it is never an empty month."""

    def discover(self, client, since=None):
        return list(HnSource(client).discover(since))

    def test_error_status_raises(self):
        client = FakeClient({SEARCH_URL: FIXTURES / "search_whoishiring.json", item_url(OCT): status_response(503)})
        with self.assertRaises(HnError) as ctx:
            self.discover(client)
        self.assertNotIsInstance(ctx.exception, FetchError, "not a transport failure: the message says what it is")
        self.assertTrue(str(ctx.exception).startswith("https://hn.algolia.com/api/v1/items/"), str(ctx.exception))
        self.assertIn("503", str(ctx.exception))

    def test_search_error_status_raises(self):
        with self.assertRaises(HnError):
            self.discover(FakeClient({SEARCH_URL: status_response(500)}))

    def test_malformed_json_raises(self):
        with self.assertRaises(HnError):
            self.discover(FakeClient({SEARCH_URL: b"{not json"}))
        client = FakeClient({SEARCH_URL: FIXTURES / "search_whoishiring.json", item_url(OCT): b"<html>"})
        with self.assertRaises(HnError):
            self.discover(client)

    def test_a_payload_of_the_wrong_shape_raises(self):
        for bad in ([], {"nope": 1}, {"hits": "x"}, {"hits": [1, 2]}):
            with self.assertRaises(HnError, msg=repr(bad)):
                self.discover(FakeClient({SEARCH_URL: json_response(bad)}))
        client = FakeClient({SEARCH_URL: FIXTURES / "search_whoishiring.json", item_url(OCT): json_response([1])})
        with self.assertRaises(HnError):
            self.discover(client)

    def test_a_transport_failure_propagates(self):
        client = FakeClient({SEARCH_URL: FetchError("timeout", "slow")})
        with self.assertRaises(FetchError):
            self.discover(client)

    def test_no_who_is_hiring_thread_raises(self):
        search = {"hits": [{"objectID": "1", "title": "Ask HN: Who wants to be hired? (October 2026)",
                            "created_at": "2026-10-01T15:00:00.000Z"}]}
        with self.assertRaises(HnError):
            self.discover(FakeClient({SEARCH_URL: json_response(search)}))

    def test_since_with_no_thread_raises(self):
        with self.assertRaises(HnError) as ctx:
            self.discover(fixture_client(), "2027-01")
        self.assertIn("2027-01", str(ctx.exception))

    def test_this_months_thread_missing_is_not_out_yet_and_never_last_months(self):
        client = fixture_client()  # the listing ends with October
        source = HnSource(client)
        self.assertTrue(source.thread_out("2026-10"))
        self.assertFalse(source.thread_out("2026-11"))
        with self.assertRaises(HnThreadNotOut):
            self.discover(client, "2026-11")
        self.assertEqual([u for u in client.requests if "/items/" in u], [])

    def test_since_older_than_the_listing_when_the_page_is_full_raises(self):
        search = {"hits": [{"objectID": str(OCT - i), "title": "Ask HN: Freelancer? Seeking freelancer? (October 2026)",
                            "created_at": "2026-10-01T15:00:00.000Z"} for i in range(19)]
                  + [{"objectID": str(OCT), "title": "Ask HN: Who is hiring? (October 2026)",
                      "created_at": "2026-10-01T15:00:00.000Z"}]}
        client = FakeClient({SEARCH_URL: json_response(search), item_url(OCT): FIXTURES / f"item_{OCT}.json"})
        with self.assertRaises(HnError) as ctx:
            self.discover(client, "2026-06")
        self.assertIn("20", str(ctx.exception))

    def test_a_bad_since_is_a_value_error(self):
        for bad in ("2026", "2026-13", "October", "2026-1"):
            with self.assertRaises(ValueError, msg=bad):
                self.discover(fixture_client(), bad)

    def test_a_thread_with_no_comments_raises(self):
        with self.assertRaises(HnError) as ctx:
            self.discover(thread_client([]))
        self.assertIn("no comments", str(ctx.exception))

    def test_a_thread_of_only_deleted_comments_raises(self):
        with self.assertRaises(HnError):
            self.discover(thread_client([None, None]))

    def test_one_failing_thread_fails_the_whole_pass_before_any_candidate(self):
        client = FakeClient({SEARCH_URL: FIXTURES / "search_whoishiring.json", item_url(SEP): status_response(503),
                             item_url(OCT): FIXTURES / f"item_{OCT}.json"})
        source = HnSource(client)
        gen = source.discover("2026-09")
        with self.assertRaises(HnError):
            next(gen)
        self.assertEqual(source.summary(), {})
        with self.assertRaises(RuntimeError):
            source.posts()

    def test_comments_but_no_parsable_post_is_ok_and_counted(self):
        source = HnSource(thread_client(["Thanks everyone, what a month this has been", "Great thread, thank you all",
                                         "Remote (EU) | Acme | Berlin"]))
        self.assertEqual(list(source.discover()), [])
        s = source.summary()
        self.assertEqual((s["comments"], s["kept"], s["unparsed"], s["non_us"]), (3, 0, 2, 1))
        self.assertEqual(source.posts(), [])

    def test_deleted_comments_are_counted_apart(self):
        source = HnSource(thread_client([None, "Acme | Eng"]))
        self.assertEqual(len(list(source.discover())), 1)
        s = source.summary()
        self.assertEqual((s["dead"], s["comments"], s["kept"], s["unparsed"]), (1, 2, 1, 0))

    def test_errors_reach_the_job_as_a_failed_source_and_write_nothing(self):
        conn = fresh_store()
        client = FakeClient({SEARCH_URL: FIXTURES / "search_whoishiring.json", item_url(OCT): status_response(503)})
        run_id, reports = run_discover(conn, [HnSource(client)])
        self.assertIn("HnError", reports["hn"].error)
        row = conn.execute("SELECT status FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM company").fetchone()[0], 0)


class FixtureThreadTest(unittest.TestCase):
    def test_hn_source_passes_check_source(self):
        client = fixture_client()
        cands = check_source(self, HnSource(client))
        self.assertEqual(client.unrouted, [])
        self.assertEqual(len(cands), 5)

    def test_identity_fields(self):
        source = HnSource(fixture_client())
        self.assertEqual((source.name, source.schedule), ("hn", "monthly"))
        self.assertTrue(source.version.startswith("hn-1"))

    def test_summary_counts_the_thread(self):
        source = HnSource(fixture_client())
        list(source.discover())
        s = source.summary()
        self.assertEqual((s["threads"], s["comments"], s["kept"], s["unparsed"], s["non_us"], s["dead"]),
                         (1, 7, 5, 1, 1, 0))

    def test_posts_carry_text_url_time_and_asks_email(self):
        source = HnSource(fixture_client())
        list(source.discover())
        posts = source.posts()
        self.assertTrue(all(isinstance(p, PostRecord) for p in posts))
        by_ref = {p.source_ref: p for p in posts}
        self.assertEqual(sorted(by_ref), ["hn:meshsystems@meshsystems.dev", "hn:orchard@orchard.io", "hn:prairielearn",
                                          "hn:tidepoolapps@tidepoolapps.com", "hn:veltrixbio@veltrix.com"])
        orchard = by_ref["hn:orchard@orchard.io"]
        self.assertEqual(orchard.url, post_url(61000101))
        self.assertEqual(orchard.posted_at, "2026-10-01T15:04:11Z")
        self.assertIn("We run a small team building scheduling software", orchard.text)
        self.assertIn("hiring@orchard.io", orchard.text)
        for junk in ("<p>", "<a ", "&#x2F;"):
            self.assertNotIn(junk, orchard.text)
        self.assertTrue(orchard.text.startswith("Orchard (YC S21) | Backend Engineer | NYC | REMOTE | https://orchard.io/jobs"))
        self.assertNotIn("new grads", orchard.text, "a reply is not part of the post")
        self.assertTrue(orchard.asks_email, "an address in the text")
        self.assertTrue(by_ref["hn:tidepoolapps@tidepoolapps.com"].asks_email, "the phrase 'email me' with no address")
        self.assertFalse(by_ref["hn:meshsystems@meshsystems.dev"].asks_email)
        self.assertFalse(by_ref["hn:veltrixbio@veltrix.com"].asks_email)

    def test_asks_email_phrases(self):
        for text, expected in (("Acme | Eng<p>Send your resume to our team", True), ("Acme | Eng<p>Send your CV to us", True),
                               ("Acme | Eng<p>Reach out at the link", False), ("Acme | Eng<p>EMAIL ME please", True),
                               ("Acme | Eng<p>write to jobs@acme.io", True), ("Acme | Eng<p>Apply on our site", False)):
            source = HnSource(thread_client([text]))
            list(source.discover())
            self.assertEqual(source.posts()[0].asks_email, expected, text)

    def test_posts_before_discover_is_an_error(self):
        with self.assertRaises(RuntimeError):
            HnSource(fixture_client()).posts()


class NameProbesTest(unittest.TestCase):
    """Fix round 1, I1: titles, remarks and places are not company names."""

    def test_a_first_segment_that_is_not_a_name_is_skipped_or_unparsed(self):
        for header in ("Remote | Eng", "REMOTE | Eng | NYC", "Hybrid | Eng", "Full-time | Eng",
                       "Part-time | Eng", "INTERN | Eng", "Hiring | Eng", "We're hiring | Eng", "We are hiring | Eng",
                       "NYC | Backend", "SF | Eng", "London | Eng", "San Francisco | Eng", "Yes | Eng", "Hi | Eng"):
            self.assertIsNone(parse_post(header), header)

    def test_a_reject_listed_first_segment_lets_a_later_segment_be_the_name(self):
        p = parse_post("Remote | Acme Labs | Backend Engineer")
        self.assertEqual((p.name, p.name_index), ("Acme Labs", 1))
        self.assertEqual(parse_post("NYC | Acme Labs | Eng").name, "Acme Labs")

    def test_role_at_company_takes_the_company(self):
        for header, name in (("Senior Backend Engineer at Acme Labs | Remote", "Acme Labs"),
                             ("Head of Design at Acme | NYC", "Acme"), ("Intern at Birch Co | Remote", "Birch Co"),
                             ("Engineering Manager at Cedar | SF", "Cedar"), ("Data Scientist at Dune | Boston", "Dune")):
            self.assertEqual(parse_post(header).name, name, header)
        # no role word before "at": the whole segment is the name
        self.assertEqual(parse_post("Meet at Noon Labs | Eng").name, "Meet at Noon Labs")

    def test_a_trailing_is_hiring_is_stripped(self):
        self.assertEqual(parse_post("Acme Labs is hiring | Eng").name, "Acme Labs")
        self.assertEqual(parse_post("Acme Labs IS HIRING! | Eng").name, "Acme Labs")

    def test_a_role_phrase_first_takes_the_next_segment_that_looks_like_a_name(self):
        p = parse_post("Senior Software Engineer | Acme Labs | NYC | Remote")
        self.assertEqual((p.name, p.name_index, p.location), ("Acme Labs", 1, "NYC"))
        p = parse_post("Backend Engineer | Full-time | Python | Birch Co | Austin, TX")
        self.assertEqual((p.name, p.name_index), ("Birch Co", 3))
        self.assertEqual(parse_post("Staff Engineer | Cedar (YC W24) | Remote").stage, "yc")

    def test_a_role_phrase_with_no_name_segment_is_unparsed(self):
        for header in ("Senior Software Engineer | Remote | NYC", "Backend Engineer | Eng | python | $150k",
                       "Frontend Developer | Full-time | Austin, TX"):
            self.assertIsNone(parse_post(header), header)

    def test_a_company_name_with_a_role_word_is_a_name(self):
        for header, name in (("Lead Bank | Eng", "Lead Bank"), ("Head Space | Eng", "Head Space"),
                             ("Intern Labs | Eng", "Intern Labs")):
            self.assertEqual(parse_post(header).name, name, header)

    def test_location_segments_are_not_names_in_the_fallback(self):
        self.assertIsNone(parse_post("Backend Engineer | Austin, TX | Remote"))

    def test_an_is_us_check_sees_the_segments_around_the_name(self):
        p = parse_post("Senior Engineer | Canada Drives | Remote")
        self.assertEqual(p.name, "Canada Drives")
        self.assertTrue(is_us(p.header, name_index=p.name_index))


class DomainProbesTest(unittest.TestCase):
    """Fix round 1, I2: a link that is not the company's own site is no domain."""

    def domain(self, *comments):
        return list(HnSource(thread_client(comments)).discover())[0].domain

    def test_shorteners_forms_press_and_job_hosts_are_not_domains(self):
        for url in ("https://forms.gle/abc123", "https://t.co/xyz", "https://goo.gl/x", "https://bit.ly/x",
                    "https://lnkd.in/x", "https://acme.typeform.com/to/1", "https://tally.so/r/x",
                    "https://airtable.com/shr1", "https://docs.google.com/forms/d/1", "https://techcrunch.com/a",
                    "https://www.nytimes.com/a", "https://www.bloomberg.com/a", "https://en.wikipedia.org/wiki/A",
                    "https://www.crunchbase.com/organization/a", "https://medium.com/@a", "https://a.substack.com",
                    "https://youtu.be/x", "https://www.youtube.com/watch", "https://x.com/a", "https://twitter.com/a",
                    "https://www.workatastartup.com/jobs/1", "https://www.ycombinator.com/companies/a",
                    "https://wellfound.com/company/a", "https://angel.co/company/a", "https://jobs.gem.com/a",
                    "https://acme.pinpointhq.com", "https://acme.applytojob.com/apply", "https://boards.greenhouse.io/a",
                    "https://jobs.ashbyhq.com/a", "https://apply.workable.com/a"):
            self.assertIsNone(self.domain("Acme Labs | Eng | " + a(url)), url)

    def test_a_platform_link_does_not_stop_a_later_company_link(self):
        self.assertEqual(self.domain("Acme Labs | Eng | " + a("https://forms.gle/abc") + " | " + a("https://acme.io")),
                         "acme.io")

    def test_a_body_link_must_equal_or_start_with_the_company_key(self):
        # "loom" is inside "bloomberg" and "gloomy" but is not their start
        self.assertIsNone(self.domain("Loom | Eng<p>Read " + a("https://www.bloomberg.com/story")))
        self.assertIsNone(self.domain("Loom | Eng<p>Read " + a("https://gloomy.io/story")))
        self.assertEqual(self.domain("Loom | Eng<p>See " + a("https://loom.com/careers")), "loom.com")
        self.assertEqual(self.domain("Acme | Eng<p>See " + a("https://acmerobotics.com/careers")), "acmerobotics.com")
        self.assertEqual(self.domain("Acme Robotics Inc | Eng<p>See " + a("https://acmero.com")), "acmero.com")
        self.assertIsNone(self.domain("Acme Robotics | Eng<p>See " + a("https://acme.com")), "4 letters is not enough")

    def test_the_keys_platform_list_covers_them(self):
        from outreach.sources.keys import classify_domain
        for d in ("t.co", "goo.gl", "lnkd.in", "forms.gle", "typeform.com", "tally.so", "airtable.com",
                  "techcrunch.com", "nytimes.com", "bloomberg.com", "wikipedia.org", "crunchbase.com",
                  "workatastartup.com", "gem.com", "pinpointhq.com", "applytojob.com", "google.com"):
            self.assertEqual(classify_domain(d), "platform", d)


class RefKeyTest(unittest.TestCase):
    """Fix round 1, I4 and I6: what a candidate's source_ref is."""

    def cands(self, *comments):
        source = HnSource(thread_client(comments))
        return list(source.discover()), source

    def test_non_ascii_names_get_a_hash_key(self):
        (muller,), _ = self.cands("M" + chr(0xFC) + "ller Labs (YC S24) | Eng")
        self.assertRegex(muller.source_ref, r"^hn:sha256:[0-9a-f]{16}$")
        (again,), _ = self.cands("M" + chr(0xFC) + "ller Labs (YC S24) | Ops")
        self.assertEqual(again.source_ref, muller.source_ref, "stable")
        (plain,), _ = self.cands("Mller Labs (YC S24) | Eng")
        self.assertEqual(plain.source_ref, "hn:mllerlabs")
        self.assertNotEqual(plain.source_ref, muller.source_ref)
        (cafe,), _ = self.cands("Caf" + chr(0xE9) + " Co (YC S24) | Eng")
        self.assertNotEqual(cafe.source_ref, muller.source_ref)

    def test_a_composed_and_a_decomposed_name_share_a_ref(self):
        (a1,), _ = self.cands("M" + chr(0xFC) + "ller (YC S24) | Eng")
        (a2,), _ = self.cands("Mu" + chr(0x308) + "ller (YC S24) | Eng")
        self.assertEqual(a1.source_ref, a2.source_ref)

    def test_a_company_domain_is_part_of_the_ref(self):
        (c,), _ = self.cands("Acme Labs | Eng | " + a("https://www.acme.io/jobs"))
        self.assertEqual(c.source_ref, "hn:acmelabs@acme.io")
        (c,), _ = self.cands("Acme Labs | Eng | " + a("https://github.com/acme"))
        self.assertEqual(c.source_ref, "hn:acmelabs")

    def test_non_ascii_name_with_a_domain(self):
        (c,), _ = self.cands("M" + chr(0xFC) + "ller | Eng | " + a("https://muller.io"))
        self.assertRegex(c.source_ref, r"^hn:sha256:[0-9a-f]{16}@muller\.io$")

    def test_two_same_name_companies_on_different_domains_are_two_candidates(self):
        cands, source = self.cands("Acme | Eng | " + a("https://acme.io"), "Acme | Ops | " + a("https://acme.dev"))
        self.assertEqual([c.source_ref for c in cands], ["hn:acme@acme.io", "hn:acme@acme.dev"])
        self.assertEqual([c.domain for c in cands], ["acme.io", "acme.dev"])
        self.assertEqual({r.source_ref for r in source.posts()}, {"hn:acme@acme.io", "hn:acme@acme.dev"})
        self.assertEqual(source.summary()["name_conflicts"], 0)

    def test_a_domainless_post_takes_the_one_domain_candidate_of_its_name(self):
        cands, source = self.cands("Acme (YC S24) | Eng | NYC | " + a("https://acme.io"),
                                   "Acme (Series A) | Ops | Austin, TX")
        (c,) = cands
        self.assertEqual(c.source_ref, "hn:acme@acme.io")
        facts = sorted((e.fact, e.value) for e in c.evidence)
        self.assertEqual(facts, [("domain", "acme.io"), ("hq", "Austin, TX"), ("hq", "NYC"), ("stage", "series_a"),
                                 ("stage", "yc")])
        self.assertEqual([r.source_ref for r in source.posts()], ["hn:acme@acme.io"] * 2)
        self.assertEqual(sum(e.fact == "domain" for e in c.evidence), 1, "the domain-less post adds no domain")

    def test_a_domainless_post_before_the_domain_post_takes_it_too(self):
        cands, source = self.cands("Acme | Ops", "Acme | Eng | " + a("https://acme.io"))
        self.assertEqual([c.source_ref for c in cands], ["hn:acme@acme.io"])
        self.assertEqual([r.source_ref for r in source.posts()], ["hn:acme@acme.io"] * 2)

    def test_a_domainless_post_among_several_domains_attaches_to_none(self):
        cands, source = self.cands("Acme | Eng | " + a("https://acme.io"), "Acme | Ops | " + a("https://acme.dev"),
                                   "Acme | Design | Austin, TX")
        self.assertEqual([c.source_ref for c in cands], ["hn:acme@acme.io", "hn:acme@acme.dev"])
        self.assertTrue(all(e.fact != "hq" for c in cands for e in c.evidence))
        self.assertEqual(len(source.posts()), 2, "the domain-less post has no record")
        self.assertEqual(source.summary()["name_conflicts"], 1)

    def test_a_name_with_no_domain_anywhere_is_a_name_only_candidate(self):
        cands, source = self.cands("Acme | Eng", "Acme | Ops")
        self.assertEqual([c.source_ref for c in cands], ["hn:acme"])
        self.assertEqual(len(source.posts()), 2)


class AsksEmailTest(unittest.TestCase):
    """Fix round 1, I5: phrases match as words, "reach out at" needs an address, obfuscated addresses count."""

    def asks(self, body):
        source = HnSource(thread_client(["Acme | Eng<p>" + body]))
        list(source.discover())
        return source.posts()[0].asks_email

    def test_positives(self):
        for body in ("Email me at the address in my profile", "e-mail me about it", "E-mail us your story",
                     "Email us!", "Send your resume to our team", "send your CV to us", "Write to jobs@acme.io",
                     "reach out at jobs@acme.io", "Reach out at: jobs@acme.io", "jobs at acme dot com",
                     "jobs [at] acme [dot] io", "careers (at) acme (dot) co"):
            self.assertTrue(self.asks(body), body)

    def test_negatives(self):
        for body in ("We track email metrics", "An email messaging platform", "Email meetings are over",
                     "reach out at our site", "Reach out at the link below", "Apply on our site",
                     "We are at acme and we dot every i", "Our emailer is fast"):
            self.assertFalse(self.asks(body), body)


class StoreTest(unittest.TestCase):
    """The fixture thread through run_discover after the LCA fixtures."""

    def setUp(self):
        self.conn = fresh_store()
        run_discover(self.conn, [LcaSource(FILES)])
        self.lca_companies = count(self.conn, "company")

    def run_hn(self, client=None, **kw):
        source = HnSource(client or fixture_client())
        _, reports = run_discover(self.conn, [source], **kw)
        return reports["hn"]

    def company_of(self, ref, source="hn"):
        row = self.conn.execute("SELECT company_id FROM company_source WHERE source_name = ? AND source_ref = ?",
                                (source, ref)).fetchone()
        return row[0] if row else None

    def lca_company(self, name):
        return self.conn.execute("SELECT company_id FROM company WHERE name = ?", (name,)).fetchone()[0]

    def test_the_run_counts(self):
        report = self.run_hn()
        self.assertEqual((report.error, report.failed), ("", []))
        self.assertEqual((report.candidates, report.created, report.attached), (5, 4, 1))
        self.assertEqual(report.posts, 5)
        self.assertEqual(count(self.conn, "company"), self.lca_companies + 4)
        self.assertEqual(report.notes["kept"], 5)

    def test_orchard_post_attaches_by_domain_and_adds_yc_stage(self):
        self.run_hn()
        orchard = self.lca_company("Orchard")
        self.assertEqual(self.company_of("hn:orchard@orchard.io"), orchard)
        stage = self.conn.execute(
            "SELECT value, confidence, source_ref, quote FROM evidence WHERE company_id = ? AND fact = 'stage' "
            "AND source_kind = 'hn'", (orchard,)).fetchall()
        self.assertEqual([tuple(r) for r in stage], [("yc", "estimate", post_url(61000101),
                                                       "Orchard (YC S21) | Backend Engineer | NYC | REMOTE | "
                                                       "https://orchard.io/jobs")])
        # the NYC hq is a guess and never gave Orchard a state
        hq = self.conn.execute("SELECT confidence FROM evidence WHERE company_id = ? AND fact = 'hq' "
                               "AND source_kind = 'hn'", (orchard,)).fetchall()
        self.assertEqual([r[0] for r in hq], ["guess"])

    def test_tidepool_is_a_new_company_with_domain_and_ats_alias(self):
        self.run_hn()
        tidepool = self.company_of("hn:tidepoolapps@tidepoolapps.com")
        self.assertIsNotNone(tidepool)
        self.assertNotIn(tidepool, {r[0] for r in self.conn.execute(
            "SELECT company_id FROM company_source WHERE source_name = 'lca'")})
        domain = self.conn.execute("SELECT value, confidence FROM evidence WHERE company_id = ? AND fact = 'domain'",
                                   (tidepool,)).fetchall()
        self.assertEqual([tuple(r) for r in domain], [("tidepoolapps.com", "estimate")])
        aliases = {r["alias_key"]: r["source"] for r in self.conn.execute(
            "SELECT alias_key, source FROM company_alias WHERE company_id = ?", (tidepool,))}
        self.assertEqual(aliases.get("lever:tidepoolapps"), "ats")
        self.assertIn(name_key("Tidepool Apps"), aliases)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM evidence WHERE company_id = ? AND fact = 'hq'",
                                           (tidepool,)).fetchone()[0], 0)

    def test_non_us_post_is_dropped_and_counted(self):
        report = self.run_hn()
        self.assertEqual(report.notes["non_us"], 1)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM company WHERE name LIKE 'Quasar%'").fetchone())
        self.assertIsNone(self.company_of("hn:quasargames"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM company_post WHERE text LIKE '%Quasar%'").fetchone()[0], 0)

    def test_weak_name_collision_makes_company_and_one_item(self):
        self.run_hn()
        lca_mesh = self.lca_company("Mesh Systems Inc")
        hn_mesh = self.company_of("hn:meshsystems@meshsystems.dev")
        self.assertNotIn(hn_mesh, (None, lca_mesh))
        items = self.conn.execute("SELECT company_id, reason FROM review_item WHERE kind = 'merge_candidate' "
                                  "AND state = 'open'").fetchall()
        mesh_items = [r for r in items if {r["company_id"]} & {lca_mesh, hn_mesh}]
        self.assertEqual(len(mesh_items), 1)
        owner = self.conn.execute("SELECT company_id FROM company_alias WHERE alias_key = ? AND source = 'name'",
                                  (name_key("Mesh Systems"),)).fetchall()
        self.assertEqual([r[0] for r in owner], [lca_mesh], "the name alias stays with the LCA company")

    def test_next_month_post_is_not_a_second_company(self):
        self.run_hn()
        ref = "hn:meshsystems@meshsystems.dev"
        mesh = self.company_of(ref)
        # backdate the link so that "last_seen_at advanced" is visible
        self.conn.execute("UPDATE company_source SET first_seen_at = '2020-01-01T00:00:00Z', "
                          "last_seen_at = '2020-01-01T00:00:00Z' WHERE source_name = 'hn' AND source_ref = ?", (ref,))
        link = lambda: tuple(self.conn.execute(
            "SELECT first_seen_at, last_seen_at FROM company_source WHERE source_name = 'hn' AND source_ref = ?",
            (ref,)).fetchone())
        before = table_marks(self.conn)
        evidence_before = self.conn.execute("SELECT COUNT(*) FROM evidence WHERE company_id = ?", (mesh,)).fetchone()[0]
        report = self.run_hn(november_client())
        after = table_marks(self.conn)
        for table in ("company", "company_alias", "company_name_key", "company_source", "review_item", "board"):
            self.assertEqual(after[table], before[table], table)
        self.assertEqual((report.candidates, report.created, report.attached, report.items_raised), (1, 0, 1, 0))
        self.assertEqual(self.company_of(ref), mesh)
        first_seen, last_seen = link()
        self.assertEqual(first_seen, "2020-01-01T00:00:00Z")
        self.assertGreater(last_seen, "2026-01-01T00:00:00Z", "last_seen_at advanced to now")
        # the new post is new evidence tied to its own URL: its domain and its hq guess, nothing else
        new = [tuple(r) for r in self.conn.execute(
            "SELECT fact, value, confidence, source_ref FROM evidence WHERE company_id = ? ORDER BY evidence_id DESC "
            "LIMIT ?", (mesh, report.evidence_added))]
        self.assertEqual(report.evidence_added, 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM evidence WHERE company_id = ?",
                                           (mesh,)).fetchone()[0], evidence_before + 2)
        self.assertEqual(sorted(new), [("domain", "meshsystems.dev", "estimate", post_url(62000101)),
                                       ("hq", "San Francisco", "guess", post_url(62000101))])

    def test_next_month_post_without_the_link_is_a_name_only_candidate(self):
        # a domain-less repeat has no domain in its ref, so it cannot match the first month's company by ref: it is
        # a name-only candidate, weak against the companies that own the name (a new company and an item to merge)
        self.run_hn()
        companies = count(self.conn, "company")
        client = thread_client(["Mesh Systems | SWE | San Francisco<p>Still hiring."], thread_id=NOV,
                               title="Ask HN: Who is hiring? (November 2026)")
        report = self.run_hn(client)
        self.assertEqual((report.candidates, report.created), (1, 1))
        self.assertEqual(count(self.conn, "company"), companies + 1)
        self.assertGreaterEqual(report.items_raised, 1)
        self.assertIsNotNone(self.company_of("hn:meshsystems"))

    def test_parent_domain_post_never_attaches(self):
        self.run_hn()
        # veltrix.com is on three LCA employers (Veltrix Corp is not small enough to be a company): a shared key
        veltrix_lca = {self.lca_company(n) for n in ("Veltrix Pharma Inc", "Veltrix Diagnostics Inc")}
        hn_veltrix = self.company_of("hn:veltrixbio@veltrix.com")
        self.assertIsNotNone(hn_veltrix)
        self.assertNotIn(hn_veltrix, veltrix_lca)
        self.assertIsNone(self.conn.execute("SELECT merged_into FROM company WHERE company_id = ?",
                                            (hn_veltrix,)).fetchone()[0])
        for lca in veltrix_lca:
            self.assertIsNone(self.conn.execute("SELECT merged_into FROM company WHERE company_id = ?",
                                                (lca,)).fetchone()[0])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM company_source WHERE source_name = 'hn' AND company_id IN (?, ?)",
            tuple(veltrix_lca)).fetchone()[0], 0)

    def test_rerun_writes_nothing_new(self):
        self.run_hn()
        before = table_marks(self.conn)
        report = self.run_hn()
        self.assertEqual(table_marks(self.conn), before)
        self.assertEqual((report.created, report.attached, report.evidence_added, report.items_raised, report.posts),
                         (0, 5, 0, 0, 0))

    def test_hn_writes_no_size_or_headcount(self):
        self.run_hn()
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE source_kind = 'hn' AND fact IN ('size', 'headcount')").fetchone()[0], 0)

    def test_posts_are_stored_in_company_post_once_per_url(self):
        self.run_hn()
        rows = [tuple(r) for r in self.conn.execute(
            "SELECT url, source_name, posted_at, asks_email FROM company_post ORDER BY url")]
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0], (post_url(61000101), "hn", "2026-10-01T15:04:11Z", 1))
        self.assertEqual({r[1] for r in rows}, {"hn"})
        before = count(self.conn, "company_post")
        self.run_hn()
        self.assertEqual(count(self.conn, "company_post"), before, "a re-run adds no row")
        # next month's repeat is a new comment with its own URL: one new row for it, none for the old posts
        self.run_hn(november_client())
        self.assertEqual(count(self.conn, "company_post"), before + 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM company_post WHERE url = ?",
                                           (post_url(62000101),)).fetchone()[0], 1)

    def test_a_post_belongs_to_the_company_its_candidate_resolved_to(self):
        self.run_hn()
        owner = self.conn.execute("SELECT company_id FROM company_post WHERE url = ?", (post_url(61000101),)).fetchone()[0]
        self.assertEqual(owner, self.lca_company("Orchard"))


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


if __name__ == "__main__":
    unittest.main()
