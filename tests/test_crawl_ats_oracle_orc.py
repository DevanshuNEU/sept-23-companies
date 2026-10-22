"""The Oracle Recruiting Cloud adapter (issue #21, Task 20)."""
import json
import unittest
from pathlib import Path

from outreach.crawl.ats import ADAPTERS, adapter_for, supported_ats
from outreach.crawl.ats.oracle_orc import OracleAdapter
from outreach.crawl.base import JobRef, Validators, ids_fingerprint
from outreach.net import FetchError
from tests.contracts import check_adapter
from tests.fakes import FakeClient, json_response, status_response, text_response

FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
TOKEN = "acme.fa.us2.oraclecloud.com/CX_1001"
P0 = (FIXTURES / "oracle_p0.json").read_bytes()
P1 = (FIXTURES / "oracle_p1.json").read_bytes()
EMPTY = (FIXTURES / "oracle_empty.json").read_bytes()
DETAIL = (FIXTURES / "oracle_detail.json").read_bytes()
IDS = ["300000123456789", "300000123456790", "300000123456791"]


def page_url(offset, host="acme.fa.us2.oraclecloud.com", site="CX_1001"):
    return (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
            f"&expand=requisitionList&finder=findReqs;siteNumber={site},limit=200,offset={offset}")


def detail_url(job_id, host="acme.fa.us2.oraclecloud.com", site="CX_1001"):
    return (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all"
            f"&onlyData=true&finder=ById;Id=\"{job_id}\",siteNumber={site}")


U0, U2, U3 = page_url(0), page_url(2), page_url(3)


def envelope(total, rows):
    return {"items": [{"TotalJobsCount": total, "Offset": 0, "Limit": 200, "requisitionList": rows}]}


def row(job_id, title="Engineer", **extra):
    return dict({"Id": job_id, "Title": title, "PostedDate": "2026-09-21"}, **extra)


def rows(data):
    return json.loads(data)["items"][0]["requisitionList"]


def read(routes, token=TOKEN, validators=Validators()):
    client = FakeClient(routes)
    return OracleAdapter(client).list(token, validators), client


class OracleListTests(unittest.TestCase):
    def test_check_adapter(self):
        client = FakeClient({U0: [P0, P0, status_response(500)], U2: [P1, P1]})
        check_adapter(self, OracleAdapter(client), TOKEN)

    def test_registered(self):
        self.assertIs(ADAPTERS["oracle_orc"], OracleAdapter)
        self.assertIn("oracle_orc", supported_ats())
        self.assertIsInstance(adapter_for("oracle_orc", FakeClient({})), OracleAdapter)

    def test_list_ok_parses_both_pages(self):
        r, client = read({U0: P0, U2: P1})
        self.assertEqual(client.requests, [U0, U2])
        self.assertEqual(client.unrouted, [])
        self.assertEqual(r.result, "ok")
        base = "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/"
        self.assertEqual(
            [(j.native_id, j.title, j.url, j.location, j.country, j.posted_on, j.body) for j in r.jobs],
            [(IDS[0], "Software Engineer, New Grad", base + IDS[0], "Redwood City, CA, United States", "US",
              "2026-09-21", None),
             (IDS[1], "Account Executive", base + IDS[1], "London, United Kingdom", "GB", "2026-10-03", None),
             (IDS[2], "Data Engineer", base + IDS[2], "Toronto, ON, Canada", "CA", "2026-08-29", None)])
        self.assertEqual(r.validators, Validators(etag="", ids_fingerprint=ids_fingerprint(IDS)))

    def test_site_number_keeps_its_case_in_every_url(self):
        site = "Cx_Ab1"
        r, client = read({page_url(0, site=site): P0, page_url(2, site=site): P1},
                         token=f"acme.fa.us2.oraclecloud.com/{site}")
        self.assertEqual(r.result, "ok")
        self.assertTrue(all(f"siteNumber={site}," in u for u in client.requests))
        self.assertTrue(all(f"/sites/{site}/job/" in j.url for j in r.jobs))

    def test_a_short_first_page_sets_the_next_offset(self):
        """Oracle may answer 198 rows for a limit of 200: the next offset is the rows already read."""
        p0 = envelope(3, [row("1"), row("2")])
        p1 = envelope(3, [row("3")])
        r, client = read({U0: json_response(p0), U2: json_response(p1)})
        self.assertEqual((r.result, [j.native_id for j in r.jobs]), ("ok", ["1", "2", "3"]))
        self.assertEqual(client.requests, [U0, U2])

    def test_empty_board_is_empty(self):
        r, client = read({U0: EMPTY})
        self.assertEqual((r.result, r.jobs, r.validators), ("empty", (), Validators()))
        self.assertEqual(client.requests, [U0])

    def test_count_mismatch_is_an_error(self):
        # The total says 4, the pages hold 3 distinct ids: an ok would close the fourth as gone.
        p0 = json.loads(P0)
        p0["items"][0]["TotalJobsCount"] = 4
        p1 = json.loads(P1)
        p1["items"][0]["TotalJobsCount"] = 4
        r, _ = read({U0: json_response(p0), U2: json_response(p1), U3: json_response(envelope(4, []))})
        self.assertEqual((r.result, r.jobs), ("error", ()))
        self.assertIn("4", r.error)

    def test_a_total_below_the_rows_listed_is_an_error(self):
        r, _ = read({U0: json_response(envelope(1, [row("1"), row("2")]))})
        self.assertEqual((r.result, r.jobs), ("error", ()))

    def test_a_total_of_zero_with_rows_is_an_error(self):
        r, _ = read({U0: json_response(envelope(0, [row("1")]))})
        self.assertEqual(r.result, "error")

    def test_a_total_above_zero_with_no_rows_is_an_error_not_empty(self):
        r, _ = read({U0: json_response(envelope(5, []))})
        self.assertEqual((r.result, r.jobs), ("error", ()))

    def test_a_repeated_page_ends_the_read_as_an_error(self):
        # Page two answers page one again (offset ignored): no new ids, so stop at once.
        r, client = read({U0: P0, U2: P0})
        self.assertEqual((r.result, r.jobs), ("error", ()))
        self.assertEqual(client.requests, [U0, U2])

    def test_a_page_that_repeats_only_some_ids_still_counts_each_id_once(self):
        p0 = envelope(3, [row("1"), row("2")])
        p1 = envelope(3, [row("2"), row("3")])
        r, _ = read({U0: json_response(p0), U2: json_response(p1)})
        self.assertEqual((r.result, [j.native_id for j in r.jobs]), ("ok", ["1", "2", "3"]))

    def test_a_total_that_moves_between_pages_is_an_error(self):
        p1 = json.loads(P1)
        p1["items"][0]["TotalJobsCount"] = 4
        r, _ = read({U0: P0, U2: json_response(p1)})
        self.assertEqual(r.result, "error")

    def test_a_huge_total_is_refused_without_paging(self):
        r, client = read({U0: json_response(envelope(5_000_000, [row("1")]))})
        self.assertEqual((r.result, r.jobs), ("error", ()))
        self.assertEqual(client.requests, [U0])

    def test_the_caps_are_pinned_at_ten_thousand_rows_over_fifty_pages(self):
        def big(total):
            routes = {}
            for n in range(50):
                ids = [str(n * 200 + k) for k in range(200)]
                routes[page_url(n * 200)] = json_response(envelope(total, [row(i) for i in ids]))
            return routes

        r, client = read(big(10_000))
        self.assertEqual((r.result, len(r.jobs), len(client.requests)), ("ok", 10_000, 50))
        r, client = read(big(10_001))
        self.assertEqual((r.result, r.jobs, len(client.requests)), ("error", (), 1))

    def test_the_page_loop_is_bounded(self):
        total = 2_000
        counter = iter(range(10 ** 6))

        def endless(_method, _url, _headers, _body):
            return json_response(envelope(total, [row(str(next(counter)))]))

        routes = {page_url(n): endless for n in range(0, 400)}
        r, client = read(routes)
        self.assertEqual(r.result, "error")
        self.assertLessEqual(len(client.requests), 120)

    def test_a_later_page_that_fails_is_an_error(self):
        for second in (status_response(500), status_response(429), status_response(302), FetchError("timeout"),
                       text_response("<html>blocked</html>"), json_response({"items": []}),
                       json_response(envelope(3, None))):
            r, _ = read({U0: P0, U2: second})
            self.assertEqual((r.result, r.jobs), ("error", ()), second)

    def test_a_later_304_is_an_error_not_unchanged(self):
        r, _ = read({U0: P0, U2: status_response(304)})
        self.assertEqual(r.result, "error")

    def test_html_or_wrong_shape_is_error(self):
        bodies = {
            "cloudflare": text_response("<html><title>Just a moment...</title></html>"),
            "list": json_response([]),
            "no items": json_response({"count": 0}),
            "items null": json_response({"items": None}),
            "items empty": json_response({"items": []}),
            "items not objects": json_response({"items": [3]}),
            "no list": json_response({"items": [{"TotalJobsCount": 0}]}),
            "list null": json_response({"items": [{"TotalJobsCount": 0, "requisitionList": None}]}),
            "no total": json_response({"items": [{"requisitionList": []}]}),
            "total string": json_response({"items": [{"TotalJobsCount": "2", "requisitionList": []}]}),
            "total bool": json_response({"items": [{"TotalJobsCount": True, "requisitionList": []}]}),
            "negative total": json_response({"items": [{"TotalJobsCount": -1, "requisitionList": []}]}),
            "null": json_response(None),
        }
        for name, resp in bodies.items():
            r, _ = read({U0: resp})
            self.assertEqual((r.result, r.jobs), ("error", ()), name)

    def test_http_failures_are_errors(self):
        for status in (403, 404, 429, 500, 302):
            r, _ = read({U0: status_response(status)})
            self.assertEqual((r.result, r.http_status), ("error", status), status)
        r, _ = read({U0: FetchError("network", "down")})
        self.assertEqual((r.result, r.http_status), ("error", None))

    def test_a_row_without_a_usable_id_or_title_is_an_error(self):
        for bad in (row(None), row(""), row("  "), row(True), row("a b"), row("a/b"), row('a"b'), row("1;2"),
                    row("1", title=""), row("1", title=None), row("1", title=7), {"Title": "x"}, "x", None):
            r, _ = read({U0: json_response(envelope(1, [bad]))})
            self.assertEqual((r.result, r.jobs), ("error", ()), bad)

    def test_a_numeric_id_is_text(self):
        r, _ = read({U0: json_response(envelope(1, [row(300000123456789)]))})
        self.assertEqual(r.jobs[0].native_id, "300000123456789")

    def test_vague_or_missing_dates_give_none(self):
        dates = ["30+ days ago", "Posted today", "2026-02-30", "2026", "", None, 7]
        for value in dates:
            r, _ = read({U0: json_response(envelope(1, [row("1", PostedDate=value)]))})
            self.assertEqual((r.result, r.jobs[0].posted_on), ("ok", None), value)
        r, _ = read({U0: json_response(envelope(1, [row("1", PostedDate="2026-10-01T08:00:00Z")]))})
        self.assertEqual(r.jobs[0].posted_on, "2026-10-01")

    def test_location_and_country_are_optional_and_typed(self):
        r, _ = read({U0: json_response(envelope(1, [row("1", PrimaryLocation=["x"], PrimaryLocationCountry=7)]))})
        self.assertEqual((r.jobs[0].location, r.jobs[0].country), ("", None))
        r, _ = read({U0: json_response(envelope(1, [row("1", PrimaryLocationCountry="United States")]))})
        self.assertIsNone(r.jobs[0].country)

    def test_duplicate_ids_are_kept_once(self):
        p = envelope(2, [row("1", "First"), row("1", "Copy"), row("2")])
        r, _ = read({U0: json_response(p)})
        self.assertEqual([(j.native_id, j.title) for j in r.jobs], [("1", "First"), ("2", "Engineer")])

    def test_unchanged_on_the_same_ids_and_no_etag(self):
        first, _ = read({U0: P0, U2: P1})
        again, client = read({U0: P0, U2: P1}, validators=first.validators)
        self.assertEqual((again.result, again.jobs, again.validators), ("unchanged", (), first.validators))
        self.assertNotIn("if-none-match", {k.lower() for _m, _u, h, _b in client.calls for k in h})

    def test_an_unrequested_304_is_an_error(self):
        r, _ = read({U0: status_response(304)})
        self.assertEqual((r.result, r.http_status), ("error", 304))

    def test_host_outside_oraclecloud_makes_no_request(self):
        for token in ("evil.com/CX_1", "acme.oraclecloud.com.evil.com/CX_1", "oraclecloud.com/CX_1",
                      "acme.fa.us2.oraclecloud.com", "acme.fa.us2.oraclecloud.com/", "acme.fa.us2.oraclecloud.com/a/b",
                      "acme.fa.us2.oraclecloud.com/CX 1", "user@acme.oraclecloud.com/CX_1",
                      "ACME.fa.us2.oraclecloud.com/CX_1", "..oraclecloud.com/CX_1", "-.oraclecloud.com/CX_1",
                      "a..b.oraclecloud.com/CX_1", "acme.oraclecloud.com:8080/CX_1", "acme.oraclecloud.com/CX%201",
                      "acme.oraclecloud.com/CX;1", ""):
            r, client = read({}, token=token)
            self.assertEqual((r.result, client.calls), ("error", []), token)
        client = FakeClient({})
        job = JobRef("1", "x", "", "", None, None, None)
        for token in ("evil.com/CX_1", "..oraclecloud.com/CX_1"):
            with self.assertRaises(ValueError):
                OracleAdapter(client).body(token, job)
        self.assertEqual(client.calls, [])


class OracleBodyTests(unittest.TestCase):
    def job(self, job_id=IDS[0]):
        return JobRef(job_id, "x", "", "", None, None, None)

    def body(self, resp, job_id=IDS[0], token=TOKEN):
        client = FakeClient({detail_url(job_id): resp})
        return OracleAdapter(client).body(token, self.job(job_id)), client

    def test_body_joins_the_three_fields(self):
        text, client = self.body(DETAIL)
        self.assertEqual(client.requests, [detail_url(IDS[0])])
        self.assertEqual(text, "We are hiring a new grad engineer.\nBuild services\nShip & learn\nA CS degree\n"
                               "We sponsor visas for the right person.")
        self.assertNotIn("About the company", text)
        self.assertNotIn("<", text)

    def test_body_skips_blank_fields_and_returns_none_when_all_are_blank(self):
        one = {"items": [{"Id": IDS[0], "ExternalDescriptionStr": "<p>Only this.</p>",
                          "ExternalResponsibilitiesStr": None, "ExternalQualificationsStr": " "}]}
        self.assertEqual(self.body(json_response(one))[0], "Only this.")
        none = {"items": [{"Id": IDS[0], "ExternalDescriptionStr": None, "ExternalResponsibilitiesStr": "",
                           "ExternalQualificationsStr": None}]}
        self.assertIsNone(self.body(json_response(none))[0])

    def test_a_failed_or_wrong_body_raises_instead_of_storing_nothing(self):
        bad = [status_response(500), status_response(404), status_response(302), status_response(304),
               text_response("<html><title>Just a moment...</title></html>"), json_response({"error": "x"}),
               json_response({"items": []}), json_response({"items": None}), json_response({"items": ["x"]}),
               json_response({"items": [{"Id": IDS[0]}]}), json_response([]), json_response(None),
               json_response({"items": [{"Id": "999", "ExternalDescriptionStr": "<p>Another posting.</p>"}]}),
               FetchError("timeout")]
        for resp in bad:
            client = FakeClient({detail_url(IDS[0]): resp})
            with self.assertRaises((ValueError, FetchError), msg=resp):
                OracleAdapter(client).body(TOKEN, self.job())

    def test_a_bad_id_makes_no_request(self):
        for job_id in ("", "a b", 'a"b', "1,siteNumber=X", "a/b", "1;2", "%22"):
            client = FakeClient({})
            with self.assertRaises(ValueError):
                OracleAdapter(client).body(TOKEN, self.job(job_id))
            self.assertEqual(client.calls, [], job_id)

    def test_the_detail_url_carries_the_site_and_host_of_the_token(self):
        host, site = "other.fa.em2.oraclecloud.com", "CX_7"
        detail = {"items": [{"Id": "5", "ExternalDescriptionStr": "<p>Text.</p>"}]}
        client = FakeClient({detail_url("5", host, site): json_response(detail)})
        self.assertEqual(OracleAdapter(client).body(f"{host}/{site}", self.job("5")), "Text.")
        self.assertEqual(client.unrouted, [])


if __name__ == "__main__":
    unittest.main()
