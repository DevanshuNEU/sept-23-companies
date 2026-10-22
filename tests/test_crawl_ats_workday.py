"""The Workday adapter (issue #21, Task 18)."""
import json
import unittest
from datetime import date
from pathlib import Path

from outreach.crawl.ats import ADAPTERS, adapter_for, supported_ats
from outreach.crawl.ats.workday import WorkdayAdapter, posted_on_from_relative
from outreach.crawl.base import JobRef, Validators, ids_fingerprint
from outreach.net import FetchError, Response
from tests.contracts import check_adapter
from tests.fakes import FakeClient, json_response, status_response, text_response

FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
HOST = "https://acme.wd5.myworkdayjobs.com"
LIST_KEY = ("POST", HOST + "/wday/cxs/acme/Careers/jobs")
TOKEN = "acme.wd5/Careers"
TODAY = date(2026, 10, 22)
ENG = "c2a7f1e0b3d94a6f8e5d4c3b2a190001"
SALES = "c2a7f1e0b3d94a6f8e5d4c3b2a190002"
PATHS = ["/job/Boston-MA/Software-Engineer-I_R-1001", "/job/Remote-US/Account-Executive_R-1002",
         "/job/New-York-NY/Data-Engineer_R-1003"]


def fx(name):
    return json.loads((FIXTURES / name).read_text())


LIST = (FIXTURES / "workday_list.json").read_bytes()
DETAIL = (FIXTURES / "workday_detail.json").read_bytes()


def make(client):
    return WorkdayAdapter(client, today=TODAY)


def read(routes, token=TOKEN, validators=Validators()):
    client = FakeClient(routes)
    return make(client).list(token, validators), client


def posting(n, family=""):
    return {"title": f"Engineer {family}{n}", "externalPath": f"/job/Boston-MA/Engineer-{family}{n}_R-{n}",
            "locationsText": "Boston, MA", "postedOn": "Posted 3 Days Ago", "bulletFields": [f"R-{n}"]}


def tenant(groups, *, facets=None, fail=None):
    """A fake Workday tenant. `groups` maps a family id (None is the unfiltered list) to its postings. The first page
    of a list carries `total`; later pages carry 0, as Workday does. A request with a limit above 20 is a 400."""
    seen = []

    def route(method, url, headers, body):
        req = json.loads(body)
        seen.append(req)
        if req["limit"] > 20:
            return status_response(400)
        applied = req.get("appliedFacets") or {}
        key = applied["jobFamilyGroup"][0] if applied.get("jobFamilyGroup") else None
        if fail and fail(req, key):
            return status_response(500)
        rows = groups[key]
        offset = req["offset"]
        out = {"total": len(rows) if offset == 0 else 0, "jobPostings": rows[offset:offset + 20]}
        if facets is not None and offset == 0 and key is None:
            out["facets"] = facets
        return json_response(out)

    route.seen = seen
    return route


def families(eng=1200, sales=800):
    return {ENG: [posting(n, "E") for n in range(eng)], SALES: [posting(n, "S") for n in range(sales)]}


class WorkdayTests(unittest.TestCase):
    def test_check_adapter(self):
        client = FakeClient({LIST_KEY: [json_response(json.loads(LIST)), json_response(json.loads(LIST)),
                                        status_response(500)]})
        check_adapter(self, make(client), TOKEN)

    def test_registered(self):
        self.assertIs(ADAPTERS["workday"], WorkdayAdapter)
        self.assertIn("workday", supported_ats())
        self.assertIsInstance(adapter_for("workday", FakeClient({})), WorkdayAdapter)

    def test_posted_on_from_relative_cases(self):
        cases = {
            "Posted Today": "2026-10-22",
            "Posted Yesterday": "2026-10-21",
            "Posted 1 Day Ago": "2026-10-21",
            "Posted 5 Days Ago": "2026-10-17",
            "Posted 29 Days Ago": "2026-09-23",
            "Posted 30 Days Ago": None,
            "Posted 100 Days Ago": None,
            "Posted 999 Days Ago": None,
            "  posted today ": "2026-10-22",
            "Posted 30+ Days Ago": None,
            "Posted 2 Months Ago": None,
            "Posted 0 Days Ago": "2026-10-22",
            "Posted 99999999 Days Ago": None,
            "Posted 5000000 Days Ago": None,
            "": None,
            "Closed": None,
            "Posted Tomorrow": None,
        }
        for text, want in cases.items():
            self.assertEqual(posted_on_from_relative(text, TODAY), want, text)
        self.assertIsNone(posted_on_from_relative(None, TODAY))
        self.assertIsNone(posted_on_from_relative(5, TODAY))

    def test_list_ok_parses_jobs(self):
        r, client = read({LIST_KEY: json_response(json.loads(LIST))})
        self.assertEqual(r.result, "ok")
        self.assertEqual(
            [(j.native_id, j.title, j.url, j.location, j.country, j.posted_on, j.body) for j in r.jobs],
            [(PATHS[0], "Software Engineer I", HOST + "/Careers" + PATHS[0], "Boston, MA", None, "2026-10-22", None),
             (PATHS[1], "Account Executive", HOST + "/Careers" + PATHS[1], "Remote, US", None, "2026-10-17", None),
             (PATHS[2], "Data Engineer", HOST + "/Careers" + PATHS[2], "2 Locations", None, None, None)])
        self.assertEqual(r.validators, Validators(etag="", ids_fingerprint=ids_fingerprint(PATHS)))
        self.assertEqual(client.requests, [LIST_KEY[1]])

    def test_the_request_is_a_json_post(self):
        _, client = read({LIST_KEY: json_response(json.loads(LIST))})
        method, url, headers, body = client.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(json.loads(body), {"limit": 20, "offset": 0, "searchText": "", "appliedFacets": {}})
        self.assertEqual({k.lower(): v for k, v in headers.items()}["content-type"], "application/json")

    def test_limit_is_20(self):
        route = tenant({None: [posting(n) for n in range(45)]})
        r, _ = read({LIST_KEY: route})
        self.assertEqual((r.result, len(r.jobs)), ("ok", 45))
        self.assertEqual([q["offset"] for q in route.seen], [0, 20, 40])
        self.assertEqual({q["limit"] for q in route.seen}, {20})

    def test_later_pages_report_total_zero_and_that_is_fine(self):
        route = tenant({None: [posting(n) for n in range(21)]})
        r, _ = read({LIST_KEY: route})
        self.assertEqual((r.result, len(r.jobs)), ("ok", 21))

    def test_capped_list_subdivides_by_family(self):
        groups = families()
        groups[None] = (groups[ENG] + groups[SALES])[:2000]
        route = tenant(groups, facets=fx("workday_capped.json")["facets"])
        # The unfiltered answer is the capped fixture shape: total 2000.
        r, _ = read({LIST_KEY: route})
        self.assertEqual(r.result, "ok")
        self.assertEqual(len(r.jobs), 2000)
        self.assertEqual(len({j.native_id for j in r.jobs}), 2000)
        unfiltered = [q for q in route.seen if not q["appliedFacets"]]
        self.assertEqual([q["offset"] for q in unfiltered], [0])
        self.assertEqual({q["limit"] for q in route.seen}, {20})
        self.assertEqual({tuple(q["appliedFacets"]["jobFamilyGroup"]) for q in route.seen if q["appliedFacets"]},
                         {(ENG,), (SALES,)})
        self.assertEqual(max(q["offset"] for q in route.seen), 1180)
        self.assertEqual(r.validators.ids_fingerprint, ids_fingerprint([j.native_id for j in r.jobs]))

    def test_capped_fixture_shape_is_what_the_adapter_reads(self):
        capped = fx("workday_capped.json")
        self.assertEqual(capped["total"], 2000)
        route = {LIST_KEY: [json_response(capped)]}
        # Only the first request is routed: the adapter must ask for the first family next, and that is unrouted.
        client = FakeClient(route)
        with self.assertRaises(AssertionError):
            make(client).list(TOKEN, Validators())
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(json.loads(client.calls[1][3])["appliedFacets"], {"jobFamilyGroup": [ENG]})

    def test_family_still_capped_is_an_error(self):
        groups = {None: [posting(n) for n in range(2000)], ENG: [posting(n, "E") for n in range(2000)],
                  SALES: [posting(n, "S") for n in range(50)]}
        route = tenant(groups, facets=fx("workday_capped.json")["facets"])
        r, _ = read({LIST_KEY: route})
        self.assertEqual((r.result, r.jobs, r.validators), ("error", (), Validators()))
        self.assertIn("capped", r.error)

    def test_a_list_over_the_cap_is_capped_too(self):
        groups = families(1500, 1100)
        groups[None] = (groups[ENG] + groups[SALES])[:20]

        def route(method, url, headers, body):
            got = tenant(groups, facets=fx("workday_capped.json")["facets"])(method, url, headers, body)
            data = json.loads(got.body)
            if data["total"] == 20:
                data["total"] = 2600
                return json_response(data)
            return got
        # The unfiltered total says 2600; the families sum to 2600.
        r, _ = read({LIST_KEY: route})
        self.assertEqual((r.result, len(r.jobs)), ("ok", 2600))

    def test_overlapping_families_that_sum_to_the_total_are_an_error(self):
        # A holds 0-999 and B holds 980-1979: the totals add up to 2000 but the union is 1980, so 20 are missing.
        pool = [posting(n) for n in range(1980)]
        groups = {None: pool + [posting(n, 'X') for n in range(20)], ENG: pool[:1000], SALES: pool[980:]}
        route = tenant(groups, facets=fx("workday_capped.json")["facets"])
        r, _ = read({LIST_KEY: route})
        self.assertEqual((r.result, r.jobs, r.validators), ("error", (), Validators()))
        self.assertIn("capped", r.error)
        self.assertIn("1980", r.error)

    def test_families_that_do_not_cover_the_total_are_an_error(self):
        # Postings with no family would be missing: 5 listed in the families, 2000 in the tenant.
        groups = {None: [posting(n) for n in range(20)], ENG: fx("workday_family.json")["jobPostings"][:2],
                  SALES: fx("workday_family.json")["jobPostings"][2:]}
        capped = fx("workday_capped.json")

        def route(method, url, headers, body):
            got = tenant(groups)(method, url, headers, body)
            data = json.loads(got.body)
            if not json.loads(body)["appliedFacets"]:
                return json_response(capped)
            return got
        r, _ = read({LIST_KEY: route})
        self.assertEqual((r.result, r.jobs), ("error", ()))
        self.assertIn("capped", r.error)

    def test_a_capped_list_with_no_family_facet_is_an_error(self):
        capped = fx("workday_capped.json")
        for name, facets in {"missing": [capped["facets"][0]], "no values": [{"facetParameter": "jobFamilyGroup",
                                                                              "values": []}],
                             "no ids": [{"facetParameter": "jobFamilyGroup", "values": [{"descriptor": "x"}]}],
                             "not a list": "nope"}.items():
            body = dict(capped, facets=facets)
            r, client = read({LIST_KEY: json_response(body)})
            self.assertEqual((r.result, r.jobs), ("error", ()), name)
            self.assertEqual(len(client.calls), 1, name)

    def test_a_family_page_failing_is_an_error(self):
        groups = families()
        groups[None] = (groups[ENG] + groups[SALES])[:2000]
        for name, fail in {
            "second family": lambda req, key: key == SALES and req["offset"] == 20,
            "first family first page": lambda req, key: key == ENG and req["offset"] == 0,
            "last page of the last family": lambda req, key: key == SALES and req["offset"] == 780,
        }.items():
            route = tenant(groups, facets=fx("workday_capped.json")["facets"], fail=fail)
            r, _ = read({LIST_KEY: route})
            self.assertEqual((r.result, r.jobs, r.validators), ("error", (), Validators()), name)

    def test_page_failure_mid_list_is_an_error(self):
        rows = [posting(n) for n in range(45)]

        def page(offset, total):
            return json_response({"total": total, "jobPostings": rows[offset:offset + 20]})

        failures = {
            "500": status_response(500),
            "429": status_response(429),
            "redirect": status_response(302),
            "transport": FetchError("timeout", "slow"),
            "html": text_response("<html>Just a moment...</html>"),
            "wrong shape": json_response({"jobPostings": None}),
            "empty page": json_response({"total": 0, "jobPostings": []}),
        }
        for name, failure in failures.items():
            r, client = read({LIST_KEY: [page(0, 45), failure]})
            self.assertEqual((r.result, r.jobs, r.validators), ("error", (), Validators()), name)
            self.assertEqual(len(client.calls), 2, name)
        r, _ = read({LIST_KEY: [page(0, 45), page(20, 0), status_response(503)]})
        self.assertEqual((r.result, r.jobs), ("error", ()))

    def test_count_mismatch_is_an_error(self):
        rows = [posting(n) for n in range(40)]
        short = tenant({None: rows})

        def lying(method, url, headers, body):
            got = short(method, url, headers, body)
            data = json.loads(got.body)
            if data["total"]:
                data["total"] = 45
            return json_response(data)
        r, _ = read({LIST_KEY: lying})
        self.assertEqual((r.result, r.jobs), ("error", ()))
        self.assertIn("total", r.error)

    def test_a_page_with_more_postings_than_the_total_is_an_error(self):
        data = json.loads(LIST)
        data["total"] = 2
        r, _ = read({LIST_KEY: json_response(data)})
        self.assertEqual((r.result, r.jobs), ("error", ()))

    def test_a_wrapped_page_is_an_error_not_a_loop(self):
        rows = [posting(n) for n in range(20)]
        r, client = read({LIST_KEY: lambda m, u, h, b: json_response({"total": 45, "jobPostings": rows})})
        self.assertEqual((r.result, r.jobs), ("error", ()))
        self.assertLessEqual(len(client.calls), 3)

    def test_zero_total_is_empty(self):
        r, _ = read({LIST_KEY: json_response({"total": 0, "jobPostings": [], "facets": []})})
        self.assertEqual((r.result, r.jobs, r.validators), ("empty", (), Validators()))

    def test_html_or_wrong_shape_is_error(self):
        listed = json.loads(LIST)
        no_total = dict(listed)
        del no_total["total"]
        bodies = {
            "cloudflare": text_response("<html><title>Just a moment...</title><body>Checking</body></html>"),
            "empty object": json_response({}),
            "null": json_response(None),
            "list": json_response(listed["jobPostings"]),
            "no postings key": json_response({"total": 3}),
            "null postings": json_response({"total": 3, "jobPostings": None}),
            "no total": json_response(no_total),
            "string total": json_response(dict(listed, total="3")),
            "negative total": json_response(dict(listed, total=-1)),
            "zero total with rows": json_response(dict(listed, total=0)),
            "error document": json_response({"errorCode": "HTTP_404", "httpStatus": 404}),
        }
        for name, resp in bodies.items():
            r, _ = read({LIST_KEY: resp})
            self.assertEqual((r.result, r.jobs), ("error", ()), name)

    def test_http_failures_are_errors(self):
        for status in (400, 403, 404, 422, 429, 500, 302):
            r, _ = read({LIST_KEY: status_response(status)})
            self.assertEqual((r.result, r.http_status), ("error", status), status)
        r, _ = read({LIST_KEY: FetchError("network", "down")})
        self.assertEqual((r.result, r.http_status), ("error", None))

    def test_a_path_the_body_fetch_would_refuse_makes_the_read_an_error(self):
        for path in ("/job/x y", "/job/../../admin", "//evil.com/x", "/job/x?y=1", "/"):
            data = json.loads(LIST)
            data["jobPostings"][1]["externalPath"] = path
            r, _ = read({LIST_KEY: json_response(data)})
            self.assertEqual((r.result, r.jobs), ("error", ()), path)

    def test_posting_without_path_or_title_is_error(self):
        for key in ("externalPath", "title"):
            data = json.loads(LIST)
            del data["jobPostings"][1][key]
            r, _ = read({LIST_KEY: json_response(data)})
            self.assertEqual((r.result, r.jobs), ("error", ()), key)
        data = json.loads(LIST)
        data["jobPostings"][0]["externalPath"] = "job/no-leading-slash"
        r, _ = read({LIST_KEY: json_response(data)})
        self.assertEqual(r.result, "error")
        data = json.loads(LIST)
        data["jobPostings"][0] = "nope"
        r, _ = read({LIST_KEY: json_response(data)})
        self.assertEqual(r.result, "error")

    def test_odd_optional_fields_do_not_break_the_read(self):
        data = json.loads(LIST)
        data["jobPostings"][0]["locationsText"] = None
        data["jobPostings"][1]["postedOn"] = 7
        del data["jobPostings"][2]["postedOn"]
        r, _ = read({LIST_KEY: json_response(data)})
        self.assertEqual(r.result, "ok")
        self.assertEqual([(j.location, j.posted_on) for j in r.jobs], [("", "2026-10-22"), ("Remote, US", None),
                                                                      ("2 Locations", None)])

    def test_same_ids_are_unchanged_with_no_etag_sent(self):
        r, _ = read({LIST_KEY: json_response(json.loads(LIST))})
        again, client = read({LIST_KEY: json_response(json.loads(LIST))}, validators=r.validators)
        self.assertEqual((again.result, again.jobs, again.validators), ("unchanged", (), r.validators))
        self.assertNotIn("if-none-match", {k.lower() for k in client.calls[0][2]})

    def test_a_new_id_is_ok(self):
        r, _ = read({LIST_KEY: json_response(json.loads(LIST))})
        data = json.loads(LIST)
        data["total"] = 4
        data["jobPostings"].append(posting(9))
        again, _ = read({LIST_KEY: json_response(data)}, validators=r.validators)
        self.assertEqual((again.result, len(again.jobs)), ("ok", 4))

    def test_bad_token_makes_no_request(self):
        for token in ("", "acme", "acme.wd5", "acme.wd5/", "Acme.wd5/Careers", "acme.wdx/Careers",
                      "acme.wd5/Care ers", "acme.wd5/a/b", "acme.example.com/Careers", "acme.wd5/Careers?x=1",
                      "../acme.wd5/Careers", "evil.com#.wd5/Careers"):
            r, client = read({}, token=token)
            self.assertEqual((r.result, client.requests), ("error", []), token)

    def test_the_site_keeps_its_spelling(self):
        key = ("POST", "https://beta-co.wd12.myworkdayjobs.com/wday/cxs/beta-co/External_Careers/jobs")
        r, client = read({key: json_response(json.loads(LIST))}, token="beta-co.wd12/External_Careers")
        self.assertEqual(r.result, "ok")
        self.assertEqual(r.jobs[0].url, "https://beta-co.wd12.myworkdayjobs.com/External_Careers" + PATHS[0])

    def test_body_has_start_date_line(self):
        url = HOST + "/wday/cxs/acme/Careers" + PATHS[0]
        client = FakeClient({url: Response(200, "", DETAIL, {})})
        job = JobRef(PATHS[0], "Software Engineer I", "", "", None, None, None)
        text = make(client).body(TOKEN, job)
        self.assertEqual(client.requests, [url])
        self.assertEqual(
            text,
            "Start date: 2026-11-02\nLocations: Boston, MA; New York, NY\nBuild services & ship them.\nWrite code\nReview code\n"
            "We sponsor visas for the right person.")
        self.assertNotIn("<", text)

    def test_body_location_line_keeps_the_us_signal_of_a_multi_location_row(self):
        url = HOST + "/wday/cxs/acme/Careers" + PATHS[2]
        job = JobRef(PATHS[2], "Data Engineer", "", "2 Locations", None, None, None)
        cases = {
            "both": ({"location": "Boston, MA", "additionalLocations": ["New York, NY", "Boston, MA", " "]},
                     "Locations: Boston, MA; New York, NY\nHello there."),
            "only extra": ({"additionalLocations": ["Austin, TX"]}, "Locations: Austin, TX\nHello there."),
            "odd types": ({"location": 5, "additionalLocations": "Austin, TX"}, "Hello there."),
            "none": ({}, "Hello there."),
        }
        for name, (extra, want) in cases.items():
            info = dict(extra, jobDescription="<p>Hello there.</p>")
            client = FakeClient({url: json_response({"jobPostingInfo": info})})
            self.assertEqual(make(client).body(TOKEN, job), want, name)
        client = FakeClient({url: json_response({"jobPostingInfo": {"location": "Boston, MA"}})})
        self.assertIsNone(make(client).body(TOKEN, job))

    def test_body_without_a_start_date_is_the_description(self):
        url = HOST + "/wday/cxs/acme/Careers" + PATHS[0]
        for info in ({"jobDescription": "<p>Hello there.</p>"},
                     {"jobDescription": "<p>Hello there.</p>", "startDate": None},
                     {"jobDescription": "<p>Hello there.</p>", "startDate": ""}):
            client = FakeClient({url: json_response({"jobPostingInfo": info})})
            text = make(client).body(TOKEN, JobRef(PATHS[0], "t", "", "", None, None, None))
            self.assertEqual(text, "Hello there.")

    def test_body_with_no_description_is_none(self):
        url = HOST + "/wday/cxs/acme/Careers" + PATHS[0]
        job = JobRef(PATHS[0], "t", "", "", None, None, None)
        for info in ({"startDate": "2026-11-02"}, {"jobDescription": "  "}, {"jobDescription": None}):
            client = FakeClient({url: json_response({"jobPostingInfo": info})})
            self.assertIsNone(make(client).body(TOKEN, job))

    def test_a_failed_body_fetch_raises_so_the_caller_counts_it(self):
        url = HOST + "/wday/cxs/acme/Careers" + PATHS[0]
        job = JobRef(PATHS[0], "t", "", "", None, None, None)
        for name, resp in {"500": status_response(500), "404": status_response(404),
                           "html": text_response("<html>blocked</html>"), "list": json_response([]),
                           "no info": json_response({"other": 1}), "transport": FetchError("network", "down")
                           }.items():
            with self.assertRaises(Exception, msg=name):
                make(FakeClient({url: resp})).body(TOKEN, job)

    def test_body_makes_no_request_for_an_unsafe_path_or_token(self):
        client = FakeClient({})
        adapter = make(client)
        for token, path in ((TOKEN, ""), (TOKEN, "job/x"), (TOKEN, "/job/../../admin"), (TOKEN, "/job/x?y=1"),
                            (TOKEN, "/job/x y"), (TOKEN, "//evil.com/x"), ("bad token", PATHS[0])):
            with self.assertRaises(Exception, msg=path):
                adapter.body(token, JobRef(path, "t", "", "", None, None, None))
        self.assertEqual(client.requests, [])

    def test_encoded_paths_are_kept(self):
        path = "/job/Boston%2C-MA/Software-Engineer_R-1"
        url = HOST + "/wday/cxs/acme/Careers" + path
        client = FakeClient({url: json_response({"jobPostingInfo": {"jobDescription": "<p>Hi</p>"}})})
        self.assertEqual(make(client).body(TOKEN, JobRef(path, "t", "", "", None, None, None)), "Hi")

    def test_the_default_clock_is_used_when_no_date_is_given(self):
        client = FakeClient({LIST_KEY: json_response(json.loads(LIST))})
        r = WorkdayAdapter(client).list(TOKEN, Validators())
        self.assertRegex(r.jobs[0].posted_on, r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
