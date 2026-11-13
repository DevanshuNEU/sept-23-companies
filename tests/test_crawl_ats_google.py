"""The Google adapter (#263): the server-rendered results page of Google Careers, page 1 only, never a close."""
import json
import unittest
import urllib.parse
from pathlib import Path

from outreach.crawl.ats import ADAPTERS, adapter_for, supported_ats
from outreach.crawl.ats.google import PRESETS, GoogleAdapter, results_url
from outreach.crawl.base import Validators
from outreach.net import FetchError
from tests.fakes import FakeClient, status_response, text_response

FIXTURE = Path(__file__).parent / "fixtures" / "crawl" / "google_results.html"
PAGE = FIXTURE.read_text(encoding="utf-8")
URL = results_url("early-software")
SWE, RTL, DCT = "109116638069957318", "84385896404525766", "89657015277626054"


def block(data) -> str:
    """A results page whose ds:1 block holds `data`."""
    return ("<html><script>AF_initDataCallback({key: 'ds:0', hash: '1', data:[[]], sideChannel: {}});</script>"
            f"<script>AF_initDataCallback({{key: 'ds:1', hash: '2', data:{json.dumps(data)}, sideChannel: {{}}}});"
            "</script></html>")


def fixture_data() -> list:
    i = PAGE.index("key: 'ds:1'")
    data, _ = json.JSONDecoder().raw_decode(PAGE, PAGE.index("data:", i) + len("data:"))
    return data


def read(routes, token="early-software", head=False):
    client = FakeClient(routes)
    adapter = GoogleAdapter(client)
    return (adapter.head(token) if head else adapter.list(token, Validators())), client


class GoogleTests(unittest.TestCase):
    def test_registered(self):
        self.assertIn("google", supported_ats())
        self.assertIs(ADAPTERS["google"], GoogleAdapter)
        self.assertIsInstance(adapter_for("google", FakeClient({})), GoogleAdapter)
        self.assertEqual(GoogleAdapter.ats, "google")

    def test_the_url_asks_for_early_us_software_jobs_newest_first_and_never_a_page(self):
        self.assertEqual(URL, "https://www.google.com/about/careers/applications/jobs/results?location=United%20States"
                              "&target_level=EARLY&sort_by=date&q=software%20engineer")
        self.assertEqual(set(PRESETS), {"early-software"})
        for head in (False, True):
            r, client = read({URL: PAGE}, head=head)
            self.assertEqual(client.requests, [URL])
            for sent in client.requests:
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(sent).query)
                self.assertNotIn("page", query)
                self.assertNotIn("page=", sent)

    def test_a_job_has_its_link_places_times_and_text(self):
        r, _ = read({URL: PAGE})
        self.assertEqual([j.native_id for j in r.jobs], [DCT, RTL, SWE])
        jobs = {j.native_id: j for j in r.jobs}
        swe = jobs[SWE]
        self.assertEqual(swe.title, "Software Engineer II, Early Career")
        self.assertEqual(swe.url, "https://www.google.com/about/careers/applications/jobs/results/"
                                  "109116638069957318-software-engineer-ii-early-career")
        self.assertTrue(swe.location.startswith("Mountain View, CA, USA; Kirkland, WA, USA; "), swe.location)
        self.assertEqual(swe.location.count("; "), 11)
        self.assertEqual(swe.country, "US")
        # [12] is the created time (1793603262 s), not [13], the updated one
        self.assertEqual((swe.posted_at, swe.posted_on), ("2026-11-02T07:07:42Z", "2026-11-02"))
        self.assertIn("Google's software engineers develop", swe.body)
        self.assertIn("Design, develop, test, deploy", swe.body)
        self.assertIn("Minimum qualifications:", swe.body)
        self.assertIn("Preferred qualifications:", swe.body)
        self.assertIn("Master's degree in Computer Science", swe.body)
        self.assertEqual(swe.body.count("1 year of experience with software development"), 1)  # [19] repeats [4]
        self.assertNotIn("<", swe.body)
        self.assertIn("not eligible for U.S. immigration sponsorship", jobs[DCT].body)  # the note in [15]

    def test_a_job_with_no_link_on_the_page_gets_the_id_link(self):
        r, _ = read({URL: PAGE})
        rtl = {j.native_id: j for j in r.jobs}[RTL]
        self.assertEqual(rtl.url, "https://www.google.com/about/careers/applications/jobs/results/84385896404525766")
        self.assertEqual(rtl.location, "Sunnyvale, CA, USA")

    def test_a_job_outside_the_us_has_no_country(self):
        data = fixture_data()
        data[0][0][9] = data[0][0][9] + [["London, UK", ["London, UK"], "London", None, None, "GB"]]
        r, _ = read({URL: block(data)})
        self.assertIsNone(r.jobs[0].country)
        self.assertTrue(r.jobs[0].location.endswith("; London, UK"))

    def test_every_list_read_is_partial_so_nothing_is_ever_closed(self):
        r, _ = read({URL: PAGE})
        self.assertEqual((r.result, r.partial, r.head, len(r.jobs)), ("ok", True, False, 3))
        self.assertEqual(r.validators, Validators())
        self.assertIn("first page only", r.partial_note)
        self.assertIn("3 of 28", r.partial_note)

    def test_a_whole_list_is_still_partial(self):
        data = fixture_data()
        data[2] = 3  # the page holds every posting the search counts
        r, _ = read({URL: block(data)})
        self.assertEqual((r.result, r.partial), ("ok", True))

    def test_the_head_read_is_page_one_partial(self):
        r, client = read({URL: PAGE}, head=True)
        self.assertEqual((r.result, r.partial, r.head, len(r.jobs)), ("ok", True, True, 3))
        self.assertEqual(client.requests, [URL])

    def test_no_jobs_is_unchanged_never_empty(self):
        for jobs in ([], None):
            for head in (False, True):
                r, _ = read({URL: block([jobs, None, 0, 20])}, head=head)
                self.assertEqual((r.result, r.jobs, r.head), ("unchanged", (), head), (jobs, head))

    def test_a_broken_row_is_skipped(self):
        data = fixture_data()
        data[0][1][1] = None  # no title
        r, _ = read({URL: block(data)})
        self.assertEqual(([j.native_id for j in r.jobs], r.partial), ([DCT, SWE], True))
        self.assertIn("1 broken", r.partial_note)

    def test_only_broken_rows_is_an_error(self):
        r, _ = read({URL: block([[["x"], ["y"]], None, 2, 20])})
        self.assertEqual((r.result, r.jobs), ("error", ()))

    def test_a_missing_or_garbled_block_is_an_error(self):
        pages = {"no block": "<html><body>Search Jobs</body></html>",
                 "garbled": "<script>AF_initDataCallback({key: 'ds:1', hash: '2', data:[[[\"1\",</script>",
                 "not a list": block({"jobs": []}),
                 "jobs not a list": block(["x", None, 1, 20]),
                 "no total": block([fixture_data()[0], None, None, 20]),
                 "only ds:0": "<script>AF_initDataCallback({key: 'ds:0', hash: '1', data:[[]], sideChannel: {}});"
                              "</script>"}
        for name, page in pages.items():
            for head in (False, True):
                r, _ = read({URL: text_response(page)}, head=head)
                self.assertEqual((r.result, r.jobs), ("error", ()), name)
                self.assertTrue(r.error)
        r, _ = read({URL: text_response(pages["no block"])})
        self.assertIn("ds:1", r.error)

    def test_failures_are_errors(self):
        for name, resp in {"500": status_response(500), "429": status_response(429), "302": status_response(302),
                           "network": FetchError("network", "down")}.items():
            for head in (False, True):
                r, _ = read({URL: resp}, head=head)
                self.assertEqual((r.result, r.jobs), ("error", ()), name)

    def test_another_token_makes_no_request(self):
        for token in ("google", "early", "../x", "", "early-software/2"):
            for head in (False, True):
                r, client = read({}, token=token, head=head)
                self.assertEqual((r.result, client.requests), ("error", []), token)

    def test_body_is_never_fetched(self):
        client = FakeClient({})
        self.assertIsNone(GoogleAdapter(client).body("early-software", None))
        self.assertEqual(client.requests, [])


if __name__ == "__main__":
    unittest.main()
