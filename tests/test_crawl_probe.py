"""Slug probing: companies with no board get a guessed slug tried on Greenhouse, Lever and Ashby (#21, Task 26a).

A fresh store and one `FakeClient` per test. Nothing here touches the network or the live store.
"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from outreach.crawl import __main__ as cli
from outreach.crawl import probe
from outreach.crawl.ats.greenhouse import GreenhouseAdapter
from outreach.crawl.base import ReadResult, Validators, valid_token
from outreach.crawl.polite import HostTripped
from outreach.crawl.probe import ProbeReport, probe_slugs, slug_candidates
from outreach.net import Response
from outreach.sources.tiers import promote, set_work_tier
from outreach.store import add_alias
from tests.fakes import FakeClient, json_response, status_response, text_response
from tests.storetest import fresh_store, seed_board, seed_company

FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
ROOT = Path(__file__).resolve().parent.parent
LEVER_OK = (FIXTURES / "lever_list.json").read_bytes()
ASHBY_OK = (FIXTURES / "ashby_jobs.json").read_bytes()
GH_EMPTY = (FIXTURES / "greenhouse_empty.json").read_bytes()
LEVER_EMPTY = (FIXTURES / "lever_empty.json").read_bytes()
ASHBY_EMPTY = (FIXTURES / "ashby_empty.json").read_bytes()


def gh(slug):
    return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


def gh_eu(slug):
    return f"https://boards-api.eu.greenhouse.io/v1/boards/{slug}/jobs?content=true"


def lever(slug):
    return f"https://api.lever.co/v0/postings/{slug}?mode=json"


def lever_eu(slug):
    return f"https://api.eu.lever.co/v0/postings/{slug}?mode=json"


def ashby(slug):
    return f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"


def gh_board(company_name="Acme Robotics", n=1):
    jobs = [{"id": 100 + i, "title": f"Software Engineer {i}", "absolute_url": f"https://x.example/{i}",
             "location": {"name": "Remote"}, "company_name": company_name, "content": ""} for i in range(n)]
    return json.dumps({"jobs": jobs, "meta": {"total": n}}).encode("utf-8")


def ok(body):
    return Response(200, "", body, {"etag": '"A"'})


def all_404(*slugs):
    """Every probe URL (US hosts only: probing makes no EU request) answers 404 for these slugs."""
    routes = {}
    for s in slugs:
        routes[gh(s)] = status_response(404)
        routes[lever(s)] = status_response(404)
        routes[ashby(s)] = status_response(404)
    return routes


class SlugCandidateTests(unittest.TestCase):
    def test_slug_candidates_are_lower_case_valid_and_at_most_four(self):
        got = slug_candidates(["Retell AI, Inc."])
        self.assertEqual(got, ["retellai", "retell-ai", "retellaiinc", "retell-ai-inc"])
        many = slug_candidates(["Acme Robotics Inc", "ACME Labs, Ltd", "Zed One"])
        self.assertEqual(len(many), 4)
        self.assertEqual(many[:2], ["acmerobotics", "acme-robotics"])
        for slug in many + got:
            self.assertEqual(slug, slug.lower())
            for ats in ("greenhouse", "lever", "ashby"):
                self.assertTrue(valid_token(ats, slug), slug)
        self.assertEqual(slug_candidates(["Acme", "ACME"]), ["acme"])  # deduplicated
        self.assertEqual(slug_candidates(["Café Lab"]), ["cafelab", "cafe-lab"])  # accents fold to ascii
        self.assertEqual(slug_candidates(["Acme & Sons, Co."]), ["acmesons", "acme-sons", "acmesonsco", "acme-sons-co"])

    def test_no_candidate_is_shorter_than_four_characters(self):
        self.assertEqual(slug_candidates(["IBM"]), [])
        self.assertEqual(slug_candidates(["Ox", "A B", "!!!", ""]), [])
        # "ab-cd" is 5 long and passes; "abc" and the joined "ab" never do.
        self.assertEqual(slug_candidates(["Ab Cd"]), ["abcd", "ab-cd"])
        for slug in slug_candidates(["Ai Labs", "Qq Zz Inc"]):
            self.assertGreaterEqual(len(slug), 4)


class ProbeCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.company = seed_company(self.conn, "Acme Robotics")

    def probe(self, routes, ids=None, *, limit=10, client=None, now="2026-10-22T08:00:00Z"):
        self.client = client or FakeClient(routes)
        ids = [self.company] if ids is None else ids
        return probe_slugs(self.conn, ids, self.client, limit=limit, now=now)

    def boards(self, company=None):
        return self.conn.execute("SELECT * FROM board WHERE company_id = ? ORDER BY board_id",
                                 (company or self.company,)).fetchall()

    def count(self, table):
        return self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def snapshot(self):
        return {t: self.count(t) for t in ("board", "event", "review_item", "company_alias", "company")}

    def eu_requests(self):
        return [u for u in self.client.requests if ".eu." in u]


class ProbeTests(ProbeCase):
    def test_an_ok_read_adds_a_board_with_found_by_slug_probe(self):
        routes = {gh("acmerobotics"): ok(gh_board())}
        report = self.probe(routes)
        self.assertEqual(report, ProbeReport(considered=1, probed=1, added=1, conflicts=0, name_mismatch=0, errors=0))
        board, = self.boards()
        self.assertEqual((board["ats"], board["token"], board["found_by"]), ("greenhouse", "acmerobotics", "slug_probe"))
        self.assertIsNone(board["last_read"])  # the probe is a guess, not a read: the crawler reads it next
        event = self.conn.execute("SELECT * FROM event WHERE entity = 'board' AND kind = 'found'").fetchone()
        self.assertEqual(json.loads(event["detail"]), {"via": "probe", "matched_on": "slug"})
        self.assertEqual(self.client.unrouted, [])

    def test_lever_and_ashby_hits_are_added_too(self):
        # One company per ATS, each with its own slug, so the three adapters are each exercised.
        for name, slug, ats, url, body in (("Beta Works", "betaworks", "lever", lever("betaworks"), LEVER_OK),
                                           ("Gamma Works", "gammaworks", "ashby", ashby("gammaworks"), ASHBY_OK)):
            cid = seed_company(self.conn, name)
            routes = all_404(slug, "beta-works", "gamma-works")
            routes[url] = ok(body)
            report = self.probe(routes, [cid])
            self.assertEqual(report.added, 1, name)
            board, = self.boards(cid)
            self.assertEqual((board["ats"], board["token"], board["found_by"]), (ats, slug, "slug_probe"))

    def test_probing_never_makes_the_eu_hop(self):
        # Every guessed slug 404s on the US host; the EU hosts are not routed and must not be asked.
        report = self.probe(all_404("acmerobotics", "acme-robotics"))
        self.assertEqual(report.probed, 1)
        self.assertEqual((report.added, report.errors), (0, 0))  # a plain 404 is the usual answer, not an error
        self.assertEqual(self.client.unrouted, [])
        self.assertEqual(self.eu_requests(), [])
        self.assertEqual(len(self.client.requests), 6)  # two slugs, three ATSs, one request each

    def test_a_normal_greenhouse_read_still_hops_to_the_eu_host(self):
        client = FakeClient({gh("acme"): status_response(404), gh_eu("acme"): ok(gh_board("Acme"))})
        self.assertEqual(GreenhouseAdapter(client).list("acme", Validators()).result, "ok")
        self.assertEqual(client.requests, [gh("acme"), gh_eu("acme")])

    def test_empty_error_and_unchanged_add_nothing(self):
        routes = all_404("acmerobotics", "acme-robotics")
        routes[gh("acmerobotics")] = ok(GH_EMPTY)
        routes[lever("acmerobotics")] = ok(LEVER_EMPTY)
        routes[ashby("acmerobotics")] = ok(ASHBY_EMPTY)
        routes[gh("acme-robotics")] = status_response(500)
        routes[lever("acme-robotics")] = text_response("<html>blocked</html>")
        routes[ashby("acme-robotics")] = json_response({"unexpected": True})
        report = self.probe(routes)
        self.assertEqual((report.added, report.name_mismatch, report.conflicts), (0, 0, 0))
        self.assertEqual(report.errors, 3)  # the 500, the HTML page and the wrong shape; a plain 404 is no news
        self.assertEqual(self.boards(), [])
        self.assertEqual(self.count("board"), 0)

        class Unchanged:
            ats = "greenhouse"

            def __init__(self, client):
                pass

            def list(self, token, validators):
                return ReadResult("unchanged", (), Validators(), http_status=304)

            def body(self, token, job):
                return None

        with mock.patch.object(probe, "adapter_for", lambda ats, client: Unchanged(client)):
            report = self.probe({})
        self.assertEqual(report.added, 0)
        self.assertEqual(self.count("board"), 0)

    def test_an_adapter_crash_counts_as_an_error_and_the_pass_goes_on(self):
        class Crashes:
            ats = "greenhouse"

            def __init__(self, client):
                pass

            def list(self, token, validators):
                raise RuntimeError("adapter bug")

            def body(self, token, job):
                return None

        with mock.patch.object(probe, "adapter_for", lambda ats, client: Crashes(client)):
            report = self.probe({})
        self.assertEqual((report.probed, report.added), (1, 0))
        self.assertEqual(report.errors, 6)  # two slugs on three ATSs, none a 404

    def test_one_board_per_company_per_run(self):
        routes = all_404("acmerobotics", "acme-robotics")
        routes[gh("acmerobotics")] = ok(gh_board())
        routes[lever("acmerobotics")] = ok(LEVER_OK)
        routes[ashby("acme-robotics")] = ok(ASHBY_OK)
        report = self.probe(routes)
        self.assertEqual(report.added, 1)
        board, = self.boards()
        self.assertEqual(board["ats"], "greenhouse")
        self.assertNotIn(lever("acmerobotics"), self.client.requests)  # the first hit ends the company

    def test_a_board_owned_by_another_company_is_a_conflict_not_a_move(self):
        other = seed_company(self.conn, "Different Name Corp")
        seed_board(self.conn, other, "greenhouse", "acmerobotics")
        routes = all_404("acmerobotics", "acme-robotics")
        routes[gh("acmerobotics")] = ok(gh_board())
        report = self.probe(routes)
        self.assertEqual((report.added, report.conflicts), (0, 1))
        self.assertEqual(self.boards(), [])
        self.assertEqual(len(self.boards(other)), 1)
        item, = self.conn.execute("SELECT * FROM review_item WHERE kind = 'merge_candidate'").fetchall()
        self.assertIn("same_board", item["reason"])

    def test_a_name_mismatch_stops_the_other_atss_for_that_slug_and_counts_once(self):
        routes = all_404("acmerobotics", "acme-robotics")
        routes[gh("acmerobotics")] = ok(gh_board("Someone Else Entirely"))
        routes[lever("acmerobotics")] = ok(LEVER_OK)  # would be added if Lever were asked about this slug
        report = self.probe(routes)
        self.assertEqual((report.added, report.name_mismatch), (0, 1))
        self.assertEqual(self.count("board"), 0)
        self.assertNotIn(lever("acmerobotics"), self.client.requests)
        self.assertNotIn(ashby("acmerobotics"), self.client.requests)
        self.assertIn(lever("acme-robotics"), self.client.requests)  # the next slug is still tried

    def test_a_name_mismatch_is_counted_once_per_company(self):
        routes = all_404("acmerobotics", "acme-robotics")
        routes[gh("acmerobotics")] = ok(gh_board("Someone Else Entirely"))
        routes[gh("acme-robotics")] = ok(gh_board("Someone Else Entirely"))
        report = self.probe(routes)
        self.assertEqual((report.added, report.name_mismatch), (0, 1))

    def test_name_mismatch_is_not_added(self):
        routes = all_404("acmerobotics", "acme-robotics")
        routes[gh("acmerobotics")] = ok(gh_board("Someone Else Entirely"))
        report = self.probe(routes)
        self.assertEqual((report.added, report.name_mismatch), (0, 1))
        self.assertEqual(self.count("board"), 0)
        # An alias of the company is one of its names: the same org name is then a match.
        add_alias(self.conn, self.company, "Someone Else Entirely", "dba")
        report = self.probe(routes, now="2026-12-01T08:00:00Z")  # past the 30 days the first probe is remembered
        self.assertEqual((report.added, report.name_mismatch), (1, 0))
        self.assertEqual(self.boards()[0]["found_by"], "slug_probe")

    def test_a_name_with_a_legal_suffix_still_matches_the_board_name(self):
        routes = all_404("acmerobotics", "acme-robotics")
        routes[gh("acmerobotics")] = ok(gh_board("ACME Robotics, Inc."))
        self.assertEqual(self.probe(routes).added, 1)

    def test_tier_3_is_skipped_unless_promoted(self):
        low = seed_company(self.conn, "Staffing Holdings")
        promoted = seed_company(self.conn, "Promoted Staffing")
        set_work_tier(self.conn, low, 3, "staffing", "v1")
        set_work_tier(self.conn, promoted, 3, "staffing", "v1")
        promote(self.conn, promoted, "devanshu")
        report = self.probe(all_404("stafflingholdings", "staffing-holdings", "promotedstaffing", "promoted-staffing",
                                    "acmerobotics", "acme-robotics"), [low, promoted])
        self.assertEqual((report.considered, report.probed), (1, 1))
        self.assertTrue(all("staffing-holdings" not in u and "staffingholdings" not in u
                            for u in self.client.requests))
        self.assertTrue(any("promotedstaffing" in u for u in self.client.requests))

    def test_the_limit_counts_companies_in_work_tier_order(self):
        tier2 = seed_company(self.conn, "Second Tier")
        set_work_tier(self.conn, tier2, 2, "reason", "v1")
        tier1 = seed_company(self.conn, "First Tier")
        no_row = self.company  # no tier row counts as tier 1
        routes = all_404("secondtier", "second-tier", "firsttier", "first-tier", "acmerobotics", "acme-robotics")
        report = self.probe(routes, [tier2, tier1, no_row], limit=2)
        self.assertEqual((report.considered, report.probed), (3, 2))
        self.assertFalse(any("secondtier" in u or "second-tier" in u for u in self.client.requests))
        self.assertTrue(any("firsttier" in u for u in self.client.requests))
        self.assertTrue(any("acmerobotics" in u for u in self.client.requests))
        with self.assertRaises(ValueError):
            probe_slugs(self.conn, [no_row], self.client, limit=0)

    def test_a_tripped_host_ends_the_pass_for_that_host_only(self):
        second = seed_company(self.conn, "Beta Works")
        routes = all_404("acmerobotics", "acme-robotics", "betaworks", "beta-works")
        routes[gh("acmerobotics")] = HostTripped("greenhouse.io")
        routes[lever("betaworks")] = ok(LEVER_OK)
        report = self.probe(routes, [self.company, second])
        greenhouse = [u for u in self.client.requests if "greenhouse" in u]
        self.assertEqual(greenhouse, [gh("acmerobotics")])  # one request, then Greenhouse is left alone
        self.assertEqual(report.added, 1)  # Lever and Ashby went on, and Beta Works was still probed
        self.assertEqual(self.boards(second)[0]["ats"], "lever")
        self.assertIn(lever("acme-robotics"), self.client.requests)
        self.assertIn(ashby("acme-robotics"), self.client.requests)
        self.assertEqual(report.probed, 2)

    def test_every_host_tripped_stops_the_whole_pass(self):
        second = seed_company(self.conn, "Beta Works")
        trip = lambda host: HostTripped(host)  # noqa: E731
        routes = {gh("acmerobotics"): trip("greenhouse.io"), lever("acmerobotics"): trip("lever.co"),
                  ashby("acmerobotics"): trip("ashbyhq.com")}
        report = self.probe(routes, [self.company, second])
        self.assertEqual(len(self.client.requests), 3)
        self.assertEqual(report.added, 0)
        self.assertEqual(report.probed, 1)

    def events(self, company=None):
        sql = "SELECT * FROM event WHERE kind = 'slug_probed'" + (" AND company_id = ?" if company else "")
        return self.conn.execute(sql, (company,) if company else ()).fetchall()

    def test_rerun_writes_nothing_new(self):
        other = seed_company(self.conn, "Different Name Corp")
        seed_board(self.conn, other, "greenhouse", "betaworks")
        beta = seed_company(self.conn, "Beta Works")
        mismatch = seed_company(self.conn, "Gamma Works")
        ids = [self.company, beta, mismatch]
        routes = all_404("acmerobotics", "acme-robotics", "betaworks", "beta-works", "gammaworks", "gamma-works")
        routes[gh("acmerobotics")] = [ok(gh_board())] * 2
        routes[gh("betaworks")] = [ok(gh_board("Beta Works"))] * 2
        routes[gh("gammaworks")] = [ok(gh_board("Nobody"))] * 2
        first = self.probe(routes, ids)
        self.assertEqual((first.added, first.conflicts, first.name_mismatch), (1, 1, 1))
        self.assertEqual(len(self.events()), 3)  # one slug_probed event per probed company
        before = self.snapshot()
        second = self.probe(routes, ids)
        self.assertEqual(second, ProbeReport(0, 0, 0, 0, 0, 0))  # probed within 30 days: not asked again
        self.assertEqual(self.client.requests, [])
        self.assertEqual(self.snapshot(), before)
        for company in ids:
            self.assertEqual(len(self.events(company)), 1)

    def test_the_probe_event_names_the_company_and_the_new_york_day(self):
        self.probe({gh("acmerobotics"): ok(gh_board())}, now="2026-10-23T02:00:00Z")  # 22:00 on the 22nd in New York
        event, = self.events()
        self.assertEqual((event["entity"], event["entity_id"], event["company_id"], event["actor"]),
                         ("company", self.company, self.company, "crawler"))
        self.assertEqual(event["idempotency_key"], f"slug-probed:{self.company}:2026-10-22")
        self.assertEqual(event["at"], "2026-10-23T02:00:00Z")

    def test_a_company_is_probed_again_after_30_days(self):
        routes = all_404("acmerobotics", "acme-robotics")
        self.probe(routes, now="2026-10-01T12:00:00Z")
        self.assertEqual(self.probe(routes, now="2026-10-30T12:00:00Z").probed, 0)  # 29 days
        self.assertEqual(self.probe(routes, now="2026-10-31T12:00:01Z").probed, 1)  # over 30 days
        self.assertEqual(len(self.events()), 2)  # a new New York day, a new key

    def test_the_next_companies_are_probed_on_the_next_run(self):
        names = ["Alpha Works", "Beta Works", "Gamma Works", "Delta Works"]
        ids = [seed_company(self.conn, n) for n in names]
        routes = all_404(*[n.lower().replace(" ", "") for n in names], *[n.lower().replace(" ", "-") for n in names])
        first = self.probe(routes, ids, limit=3)
        self.assertEqual((first.considered, first.probed), (4, 3))
        second = self.probe(routes, ids, limit=3)
        self.assertEqual((second.considered, second.probed), (1, 1))
        self.assertTrue(any("deltaworks" in u for u in self.client.requests))
        self.assertEqual(len(self.events()), 4)

    def test_a_company_whose_hosts_were_all_tripped_is_not_remembered(self):
        routes = {gh("acmerobotics"): HostTripped("greenhouse.io"), lever("acmerobotics"): HostTripped("lever.co"),
                  ashby("acmerobotics"): HostTripped("ashbyhq.com")}
        self.probe(routes)
        self.assertEqual(self.events(), [])  # nothing was asked, so the next run asks

    def test_no_request_for_a_company_with_a_board(self):
        seed_board(self.conn, self.company, "workday", "acme.wd5/external")
        report = self.probe({})
        self.assertEqual((report.considered, report.probed), (0, 0))
        self.assertEqual(self.client.requests, [])

    def test_a_company_with_an_inactive_board_row_is_not_probed(self):
        bid = seed_board(self.conn, self.company, "lever", "oldacme")
        self.conn.execute("UPDATE board SET active = 0 WHERE board_id = ?", (bid,))
        report = self.probe({})
        self.assertEqual((report.considered, report.probed), (0, 0))
        self.assertEqual(self.client.requests, [])

    def test_a_board_that_appears_during_the_probe_is_not_joined_by_a_second(self):
        def other_writer_adds_a_board(method, url, headers, body):
            self.conn.execute("INSERT INTO board(company_id, ats, token, created_at) VALUES (?, 'workday', "
                              "'acme.wd5/external', '2026-10-22T08:00:00Z')", (self.company,))
            return ok(gh_board())

        report = self.probe({gh("acmerobotics"): other_writer_adds_a_board})
        self.assertEqual(report.added, 0)
        self.assertEqual([b["ats"] for b in self.boards()], ["workday"])

    def test_a_company_with_only_a_watched_page_is_probed(self):
        self.conn.execute("INSERT INTO watched_page(company_id, url) VALUES (?, 'https://acme.example/careers')",
                          (self.company,))
        routes = {gh("acmerobotics"): ok(gh_board())}
        report = self.probe(routes)
        self.assertEqual(report.added, 1)
        self.assertEqual(self.boards()[0]["found_by"], "slug_probe")

    def test_closed_removed_and_merged_companies_are_not_probed(self):
        closed = seed_company(self.conn, "Closed Works")
        self.conn.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?", (closed,))
        removed = seed_company(self.conn, "Removed Works")
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'junk' WHERE company_id = ?", (removed,))
        merged = seed_company(self.conn, "Merged Works")
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                          "WHERE company_id = ?", (self.company, merged))
        report = self.probe(all_404("acmerobotics", "acme-robotics"), [closed, removed, merged, self.company])
        self.assertEqual((report.considered, report.probed), (1, 1))
        self.assertTrue(all("works" not in u for u in self.client.requests))

    def test_a_company_with_no_usable_slug_is_not_probed_and_costs_no_limit(self):
        tiny = seed_company(self.conn, "IBM")
        report = self.probe(all_404("acmerobotics", "acme-robotics"), [tiny, self.company], limit=1)
        self.assertEqual((report.considered, report.probed), (1, 1))

    def test_at_most_twelve_requests_per_company(self):
        self.conn.execute("UPDATE company SET name = 'Retell AI, Inc.' WHERE company_id = ?", (self.company,))
        add_alias(self.conn, self.company, "Retell Voice Labs", "dba")
        self.probe(all_404("retellai", "retell-ai", "retellaiinc", "retell-ai-inc"))
        self.assertEqual(len(self.client.requests), 12)

    def test_aliases_of_a_company_supply_candidates(self):
        add_alias(self.conn, self.company, "Roboco Systems", "dba")
        self.conn.execute("UPDATE company SET name = 'Tiny Co' WHERE company_id = ?", (self.company,))
        report = self.probe(all_404("roboco", "roboco-systems", "robocosystems"))
        self.assertGreater(len(self.client.requests), 0)
        self.assertTrue(any("robocosystems" in u for u in self.client.requests))
        self.assertEqual(report.probed, 1)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.path = self.conn.execute("PRAGMA database_list").fetchone()["file"]
        env = mock.patch.dict(os.environ, {"OUTREACH_DB": self.path})
        env.start()
        self.addCleanup(env.stop)
        self.live = tempfile.mkdtemp(prefix="outreach-probe-cli-")
        self.addCleanup(shutil.rmtree, self.live, True)

    def run_cli(self, argv, client):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv, root=self.live, live_root=self.live, client=client)
        return code, out.getvalue(), err.getvalue()

    def test_probe_slugs_refuses_outside_the_live_checkout(self):
        with mock.patch("sqlite3.connect") as opened, mock.patch.object(cli, "UrllibClient") as built:
            with self.assertRaises(SystemExit) as caught:
                cli.main(["probe-slugs"], root=str(ROOT), live_root=tempfile.mkdtemp(prefix="elsewhere-"))
        self.assertIn("live checkout", str(caught.exception.code))
        opened.assert_not_called()
        built.assert_not_called()

    def test_probe_slugs_prints_one_line_and_adds_the_board(self):
        cid = seed_company(self.conn, "Acme Robotics")
        routes = all_404("acmerobotics", "acme-robotics")
        routes[gh("acmerobotics")] = ok(gh_board())
        client = FakeClient(routes)
        code, out, _ = self.run_cli(["probe-slugs", "--limit", "5"], client)
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertIn("added 1", out)
        row = self.conn.execute("SELECT found_by FROM board WHERE company_id = ?", (cid,)).fetchone()
        self.assertEqual(row["found_by"], "slug_probe")

    def test_probe_slugs_limit_must_be_positive(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["probe-slugs", "--limit", "0"], root=self.live, live_root=self.live, client=FakeClient({}))
