"""The sources CLI (#31 Task 16): every command through `main`, on scratch settings and fake HTTP clients.

`CliCase` is the base the end-to-end test builds on: a scratch store, a scratch sources folder holding the stapply
fixture, and one `FakeClient` that stands for every host (HN, SEC, DOL, careers pages). Nothing here touches the
network, the live store or the real sources folder.
"""
import contextlib
import dataclasses
import io
import json
import os
import plistlib
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import live_checkout
from outreach.install import install
from outreach.crawl.config import load_crawl_config
from outreach.crawl.tick import tick_lock
from outreach.net import FetchError
from outreach.sources import __main__ as cli
from outreach.sources.ats_seed import load_seed_config
from outreach.sources.formd import SEC_USER_AGENT
from outreach.sources.freehire import load_freehire_config
from outreach.sources.stapply import MANIFEST_URL
from outreach.store import connect, finish_job, start_job, writes
from tests.fakes import FakeClient, json_response, status_response, text_response
from tests.storetest import planned_adapters, table_marks
from tests.test_formd import DATASET, routes as formd_routes
from tests.test_freehire import FIRST_PAGE as FREEHIRE_PAGE
from tests.test_hn import NOV, OCT, fixture_client, november_client
from tests.test_jobs_find_boards import CAREERS, STAPPLY, careers_routes
from tests.test_lca_source import FILES

ROOT = Path(__file__).resolve().parent.parent
HN_SEARCH = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=20"
FORMD_PAGE = "https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets"
FORMD_Q3 = "https://www.sec.gov/files/datastandardsinnovation/data/form-d-data-sets/2026q3_d.zip"
PAGE_HTML = """<html><body><ul>
<li><a href="/files/datastandardsinnovation/data/form-d-data-sets/2026q2_d.zip">2026 Q2</a></li>
<li><a href="/files/datastandardsinnovation/data/form-d-data-sets/2026q3_d.zip">2026 Q3</a></li>
<li><a href="/files/structureddata/data/form-d-data-sets/2026q1_d.zip">2026 Q1</a></li>
<li><a href="/files/structureddata/data/form-d-data-sets/2025q4_d.zip">2025 Q4</a></li>
</ul></body></html>"""
NOW = "2026-10-22T06:30:00Z"


def formd_zip() -> bytes:
    """The fixture data set as the quarterly zip SEC publishes (the three files at the top level)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in ("FORMDSUBMISSION.tsv", "ISSUERS.tsv", "OFFERING.tsv"):
            z.write(DATASET / name, name)
    return buf.getvalue()


def sec_down(routes: dict) -> dict:
    """`routes` with every data.sec.gov submissions lookup answering 503."""
    return {url: (status_response(503) if url.startswith("https://data.sec.gov/") else value)
            for url, value in routes.items()}


def all_routes(**careers_overrides) -> dict:
    """What every host the CLI talks to answers: HN's Algolia, freehire (an empty page, so no other count moves), SEC
    (page, data set, submissions), careers pages. Greenfield's site answers 404, so a clean run is `ok`;
    `careers_overrides` replace a careers route."""
    routes = dict(fixture_client().routes)
    routes[FREEHIRE_PAGE] = json_response({"data": [], "meta": {"total": 0}})
    routes.update(formd_routes())
    routes.update({FORMD_PAGE: text_response(PAGE_HTML), FORMD_Q3: formd_zip()})
    routes.update(careers_routes(**{"https://greenfield.ai/careers": status_response(404),
                                    "https://greenfield.ai/": status_response(404), **careers_overrides}))
    return routes


GH_LIST = "https://boards-api.greenhouse.io/v1/boards/{}/jobs"
ASHBY_LIST = "https://api.ashbyhq.com/posting-api/job-board/{}?includeCompensation=true"


def gh_list(*jobs):
    return json_response({"jobs": [{"id": n, "title": title, "location": {"name": where},
                                    "absolute_url": f"https://boards.greenhouse.io/x/jobs/{n}"}
                                   for n, (title, where) in enumerate(jobs, 1)], "meta": {"total": len(jobs)}})


def seed_routes() -> dict:
    """The list answers for the fixture's Greenhouse and Ashby rows: Nimbus and Orchard have a US engineering role,
    Lumen only a London one, Mesh no jobs (Quartz Cat's slug is a URL, so it is no board)."""
    return {GH_LIST.format("nimbusrobotics"): gh_list(("Software Engineer", "Boston, MA")),
            GH_LIST.format("lumenapps"): gh_list(("Software Engineer", "London, UK")),
            GH_LIST.format("meshsystems"): gh_list(),
            ASHBY_LIST.format("orchard"): json_response({"jobs": [{"id": "o1", "title": "Backend Engineer",
                                                                   "location": "Remote", "isListed": True}]})}


class CliCase(unittest.TestCase):
    def setUp(self):
        planned_adapters(self)
        folder = Path(tempfile.mkdtemp(prefix="outreach-cli-test-"))
        self.addCleanup(shutil.rmtree, folder, True)
        self.db = folder / "outreach.db"
        install(str(self.db))
        self.sources = folder / "sources"
        shutil.copytree(STAPPLY, self.sources / "stapply")
        env = mock.patch.dict(os.environ, {"OUTREACH_DB": str(self.db), "OUTREACH_SOURCES_DIR": str(self.sources)})
        env.start()
        self.addCleanup(env.stop)
        # One fixed clock for run-due's month test and for the job_run rows it reads, so no test depends on today.
        for target, name, value in ((cli, "SEC_INTERVAL_S", 0.0), (cli, "utcnow", lambda: NOW),
                                    (writes, "utcnow", lambda: NOW)):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.client = FakeClient(all_routes())
        self.factory_calls: list[tuple[str, dict]] = []

    def factory(self, profile="declared", **kw):
        self.factory_calls.append((profile, kw))
        return self.client

    def cli(self, *argv):
        """Run `main` and return (exit code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main([str(a) for a in argv], client_factory=self.factory)
        return code, out.getvalue(), err.getvalue()

    def ok(self, *argv):
        code, out, err = self.cli(*argv)
        self.assertEqual(code, 0, f"{argv} gave {code}: {out} {err}")
        return json.loads(out)

    def conn(self):
        conn = connect(str(self.db))
        self.addCleanup(conn.close)
        return conn


def count(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


class DiscoverCommandTest(CliCase):
    def test_discover_lca_prints_the_report_and_exits_0(self):
        report = self.ok("discover", "lca", "--files", *FILES)
        self.assertEqual((report["job"], report["status"]), ("discover", "ok"))
        self.assertEqual(report["reports"]["lca"]["created"], 14)
        self.assertEqual(count(self.conn(), "SELECT count(*) FROM company"), 14)

    def test_a_bad_file_exits_1_and_names_the_column(self):
        bad = self.sources / "LCA_Disclosure_Data_FY2025_Q1.csv"
        bad.write_text("CASE_NUMBER,CASE_STATUS\nI-200-1,Certified\n", encoding="utf-8")
        code, out, err = self.cli("discover", "lca", "--files", bad)
        self.assertEqual(code, 1)
        self.assertIn("EMPLOYER_NAME", err)  # the message names a missing column
        self.assertEqual(json.loads(out)["status"], "failed")
        self.assertEqual(count(self.conn(), "SELECT count(*) FROM company"), 0)  # an error is never an empty answer

    def test_a_missing_file_exits_1(self):
        code, out, err = self.cli("discover", "lca", "--files", self.sources / "nope" / "FY2025_Q1.xlsx")
        self.assertEqual(code, 1)
        self.assertIn("nope", err)

    def test_discover_lca_without_files_reads_the_downloaded_folder(self):
        code, out, err = self.cli("discover", "lca")
        self.assertEqual(code, 1)
        self.assertIn("fetch lca", err)  # no files yet: it says what to run, and opens nothing
        folder = self.sources / "lca"
        folder.mkdir()
        for path in FILES:
            shutil.copy(path, folder / path.name)
        self.assertEqual(self.ok("discover", "lca")["reports"]["lca"]["created"], 14)

    def test_discover_hn_and_since(self):
        report = self.ok("discover", "hn")
        self.assertEqual(report["reports"]["hn"]["candidates"], 5)  # Orchard, Tidepool, Mesh, Veltrix Bio, PrairieLearn
        code, _, err = self.cli("discover", "hn", "--since", "2026-13")
        self.assertEqual(code, 1)
        self.assertIn("2026-13", err)

    def test_discover_freehire_and_days(self):
        report = self.ok("discover", "freehire")
        self.assertEqual(report["reports"]["freehire"]["candidates"], 0)
        self.assertEqual(report["reports"]["freehire"]["notes"]["pages"], 1)
        wide = FREEHIRE_PAGE.replace("posted_within_days=3", "posted_within_days=30")
        self.client.routes[wide] = json_response({"data": [], "meta": {"total": 0}})
        self.ok("discover", "freehire", "--days", "30")
        self.assertEqual(self.client.requests[-1], wide)
        self.assertEqual(self.cli("discover", "freehire", "--days", "0")[0], 1)

    def test_discover_formd_reads_a_dataset(self):
        self.ok("discover", "lca", "--files", *FILES)
        report = self.ok("discover", "formd", "--dataset", DATASET)
        self.assertEqual(report["reports"]["formd"]["attached"], 2)  # Orchard and Nimbus; Form D never creates
        self.assertEqual(self.client.unrouted, [])

    def test_a_partial_run_exits_1(self):
        self.client = FakeClient(sec_down(all_routes()))
        self.ok("discover", "lca", "--files", *FILES)
        code, out, err = self.cli("discover", "formd", "--dataset", DATASET)
        self.assertEqual((code, json.loads(out)["status"]), (1, "partial"))


class OtherCommandsTest(CliCase):
    def test_resolve_and_add(self):
        self.ok("discover", "lca", "--files", *FILES)
        added = self.ok("add", "--name", "Zephyr Works", "--reason", "met the founder at a meetup",
                        "--domain", "zephyr.dev", "--ats", "lever:zephyr")
        self.assertTrue(added["created"])
        conn = self.conn()
        self.assertEqual(count(conn, "SELECT count(*) FROM company_alias WHERE alias_key = 'lever:zephyr' "
                                     "AND source = 'ats'"), 1)
        again = self.ok("add", "--name", "Zephyr Works", "--reason", "met the founder at a meetup",
                        "--domain", "zephyr.dev", "--ats", "lever:zephyr")
        self.assertFalse(again["created"])
        resolved = self.ok("resolve")
        self.assertEqual((resolved["job"], resolved["status"]), ("resolve", "ok"))

    def test_add_with_a_bad_ats_exits_1_and_writes_nothing(self):
        code, _, err = self.cli("add", "--name", "Zephyr Works", "--reason", "x", "--ats", "nonesuch:zephyr")
        self.assertEqual(code, 1)
        self.assertIn("nonesuch", err)
        self.assertEqual(count(self.conn(), "SELECT count(*) FROM company"), 0)

    def test_find_boards_without_a_stapply_folder_is_partial_and_says_so(self):
        shutil.rmtree(self.sources / "stapply")
        self.ok("discover", "lca", "--files", *FILES)
        code, out, err = self.cli("find-boards", "--max-careers", 0)
        self.assertEqual((code, json.loads(out)["status"]), (1, "partial"))
        self.assertIn("fetch stapply", err)

    def test_max_careers_zero_fetches_no_careers_page(self):
        self.ok("discover", "lca", "--files", *FILES)
        report = self.ok("find-boards", "--max-careers", 0)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["report"]["careers_checked"], 0)
        self.assertEqual(self.client.requests, [])
        self.assertGreater(report["report"]["from_stapply"], 0)

    def test_careers_requests_use_the_declared_client_and_set_no_header_of_their_own(self):
        self.ok("discover", "lca", "--files", *FILES)
        self.ok("find-boards", "--max-careers", 5)
        self.assertEqual({profile for profile, _ in self.factory_calls}, {"declared"})  # the shared User-Agent only
        careers = [(url, headers) for _, url, headers, _ in self.client.calls if "/careers" in url]
        self.assertTrue(careers)
        for url, headers in careers:
            self.assertEqual(headers, {}, url)  # no caller User-Agent, so no contact address can ride along

    def test_find_boards_rerun_with_an_unread_board_writes_nothing_new(self):
        # Gem has no adapter in the plan, so the last pass gives Gemco a watched page, once (#31 rule 9).
        self.ok("add", "--name", "Gemco Labs", "--reason", "a friend works there", "--domain", "gemco.example",
                "--ats", "gem:gemco")
        self.client.routes["https://gemco.example/careers"] = CAREERS / "plain_page.html"
        first = self.ok("find-boards")
        self.assertEqual(first["report"]["watched_pages"], 1)
        conn = self.conn()
        marks = table_marks(conn)
        second = self.ok("find-boards")
        self.assertEqual((second["status"], second["report"]["watched_pages"]), ("ok", 0))
        self.assertEqual(table_marks(conn), marks)
        self.assertEqual(conn.execute("SELECT count(*) FROM watched_page").fetchone()[0], 1)
        self.assertEqual(self.client.requests.count("https://gemco.example/careers"), 1)  # not fetched again
        self.assertEqual(self.client.unrouted, [])

    def test_store_missing_exits_1_with_the_install_command(self):
        self.db.unlink()
        code, _, err = self.cli("resolve")
        self.assertEqual(code, 1)
        self.assertIn("outreach.install", err)


class FetchCommandTest(CliCase):
    def test_fetch_stapply_downloads_the_changed_lists_with_the_declared_client(self):
        manifest = json.loads((STAPPLY / "manifest.json").read_text(encoding="utf-8"))
        routes = {"https://storage.stapply.ai/jobhive/v1/manifest.json": STAPPLY / "manifest.json"}
        for name, entry in manifest["by_ats_companies"].items():
            routes[entry["csv"]] = STAPPLY / f"{name}.csv"
        self.client = FakeClient(routes)
        dest = self.sources / "fresh"
        report = self.ok("fetch", "stapply", "--dest", dest)
        self.assertEqual(sorted(report["updated"]), sorted(f"{n}.csv" for n in manifest["by_ats_companies"]))
        self.assertTrue((dest / "stapply" / "manifest.json").is_file())
        self.assertEqual(self.ok("fetch", "stapply", "--dest", dest)["updated"], [])  # nothing changed
        self.assertEqual(self.factory_calls[0][0], "declared")

    def test_fetch_stapply_reports_the_excluded_slices_and_their_rows(self):
        manifest = json.loads((STAPPLY / "manifest.json").read_text(encoding="utf-8"))
        routes = {entry["csv"]: STAPPLY / f"{name}.csv" for name, entry in manifest["by_ats_companies"].items()}
        manifest["by_ats_companies"]["wellfound"] = {"csv": "https://storage.stapply.ai/jobhive/v1/wellfound/x.csv",
                                                     "sha256": "ab" * 32, "rows": 7}
        routes["https://storage.stapply.ai/jobhive/v1/manifest.json"] = json.dumps(manifest).encode()
        self.client = FakeClient(routes)
        report = self.ok("fetch", "stapply", "--dest", self.sources / "fresh")
        self.assertEqual((report["excluded_lists"], report["excluded_rows"]), (["wellfound"], 7))
        self.assertFalse(any("wellfound" in u for u in self.client.requests))

    def test_fetch_lca_uses_the_browser_profile_and_the_urls_in_lca_toml(self):
        urls = cli.lca_download_urls()
        self.assertGreaterEqual(len(urls), 5)
        for url in urls:
            self.assertTrue(url.startswith("https://www.dol.gov/"), url)
            self.assertRegex(url.rsplit("/", 1)[-1], r"^LCA_Disclosure_Data_FY\d{4}_Q[1-4]\.xlsx$")
        self.client = FakeClient({url: b"xlsx bytes" for url in urls})
        dest = self.sources / "fresh"
        report = self.ok("fetch", "lca", "--dest", dest)
        self.assertEqual(sorted(report["downloaded"]), sorted(u.rsplit("/", 1)[-1] for u in urls))
        self.assertEqual(sorted(p.name for p in (dest / "lca").iterdir()), sorted(report["downloaded"]))
        profile, kw = self.factory_calls[0]
        self.assertEqual(profile, "browser")
        self.assertGreaterEqual(kw["max_bytes"], 500_000_000)  # the quarterly files are 80 to 250 MB
        requests = len(self.client.requests)
        again = self.ok("fetch", "lca", "--dest", dest)
        self.assertEqual((again["downloaded"], len(self.client.requests)), ([], requests))  # kept, not fetched again

    def test_fetch_lca_with_a_failed_download_exits_1_and_keeps_the_rest(self):
        urls = cli.lca_download_urls()
        routes = {url: b"xlsx bytes" for url in urls}
        routes[urls[1]] = status_response(403)
        self.client = FakeClient(routes)
        dest = self.sources / "fresh"
        code, out, err = self.cli("fetch", "lca", "--dest", dest)
        self.assertEqual(code, 1)
        self.assertIn(urls[1].rsplit("/", 1)[-1], err)
        self.assertEqual(len(list((dest / "lca").iterdir())), len(urls) - 1)

    def test_fetch_formd_finds_the_newest_set_and_sends_the_sec_agent_to_sec_only(self):
        dest = self.sources / "fresh"
        first = self.ok("fetch", "formd", "--dest", dest)
        self.assertEqual((first["latest"], first["downloaded"], first["newer_than_last_read"]),
                         ("2026q3", True, True))
        self.assertEqual(Path(first["path"]), dest / "formd" / "2026q3_d.zip")
        self.assertTrue(Path(first["path"]).is_file())
        self.assertEqual(len(self.client.calls), 2)  # the page and the data set, both on sec.gov
        for _, url, headers, _ in self.client.calls:
            self.assertIn("www.sec.gov", url)
            self.assertEqual(headers.get("User-Agent"), SEC_USER_AGENT, url)
        second = self.ok("fetch", "formd", "--dest", dest)
        self.assertFalse(second["downloaded"])  # the file is there already
        self.assertEqual(self.client.requests.count(FORMD_Q3), 1)

    def test_fetch_stapply_and_hn_calls_never_carry_the_sec_agent(self):
        self.ok("discover", "hn")
        for _, url, headers, _ in self.client.calls:
            self.assertNotEqual(headers.get("User-Agent"), SEC_USER_AGENT, url)

    def test_fetch_formd_page_with_no_data_set_is_an_error(self):
        self.client = FakeClient({FORMD_PAGE: text_response("<html>moved</html>")})
        code, _, err = self.cli("fetch", "formd", "--dest", self.sources / "fresh")
        self.assertEqual(code, 1)
        self.assertIn("form-d-data-sets", err)

    def test_fetch_formd_transport_failure_is_an_error(self):
        self.client = FakeClient({FORMD_PAGE: FetchError("timeout", "slow")})
        code, _, err = self.cli("fetch", "formd", "--dest", self.sources / "fresh")
        self.assertEqual(code, 1)
        self.assertIn("slow", err)


class SeedAtsCommandTest(CliCase):
    def setUp(self):
        super().setUp()
        self.client.routes.update(seed_routes())

    def list_requests(self):
        return sorted(u for u in self.client.requests if u in seed_routes())

    def test_seed_ats_dry_run_prints_the_counts_and_sends_nothing(self):
        code, out, err = self.cli("seed-ats", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("seed-ats: 4 due (greenhouse 3, ashby 1) of 4 rows read, 0 known; dry run", err)
        payload = json.loads(out)
        self.assertEqual((payload["job"], payload["status"], payload["dry_run"]), ("seed-ats", "ok", True))
        self.assertEqual(payload["report"], {"read": 4, "promoted": 0, "rejected": 0, "dead": 0, "errors": 0,
                                             "known": 0, "left": 4, "tripped": []})
        self.assertEqual(self.client.requests, [])
        conn = self.conn()
        self.assertEqual(count(conn, "SELECT count(*) FROM board_candidate"), 0)
        self.assertEqual(count(conn, "SELECT count(*) FROM event WHERE kind = 'ats_seed_run'"), 0)

    def test_seed_ats_gates_every_row_through_the_crawl_client(self):
        code, out, err = self.cli("seed-ats")
        self.assertEqual(code, 0, err)
        self.assertIn("seed-ats: 4 due (greenhouse 3, ashby 1) of 4 rows read, 0 known; gating them all", err)
        payload = json.loads(out)
        self.assertEqual(payload["report"], {"read": 4, "promoted": 2, "rejected": 1, "dead": 1, "errors": 0,
                                             "known": 0, "left": 0, "tripped": []})
        self.assertEqual(self.list_requests(), sorted(seed_routes()))
        self.assertEqual(self.client.unrouted, [])
        crawl = load_crawl_config()
        self.assertIn(("declared", {"timeout_s": crawl.timeout_s, "max_bytes": crawl.max_bytes,
                                    "max_redirects": crawl.max_redirects, "spaced": False}), self.factory_calls)
        agents = {c[2].get("User-Agent") for c in self.client.calls if c[1] in seed_routes()}
        self.assertEqual(agents, {crawl.user_agent})
        conn = self.conn()
        self.assertEqual(sorted(r["token"] for r in conn.execute("SELECT token FROM board")),
                         ["nimbusrobotics", "orchard"])
        self.assertEqual(count(conn, "SELECT count(*) FROM event WHERE kind = 'ats_seed_run'"), 1)

    def test_seed_ats_reads_one_list_and_stops_at_the_limit(self):
        payload = self.ok("seed-ats", "--ats", "ashby")
        self.assertEqual((payload["report"]["read"], payload["report"]["promoted"]), (1, 1))
        self.assertEqual(self.list_requests(), [ASHBY_LIST.format("orchard")])
        payload = self.ok("seed-ats", "--ats", "greenhouse", "--limit", "1")
        self.assertEqual((payload["limit"], payload["report"]["left"]), (1, 2))
        self.assertEqual(len(self.list_requests()), 2)

    def test_a_bad_limit_exits_1(self):
        code, _, err = self.cli("seed-ats", "--limit", "0")
        self.assertEqual(code, 1)
        self.assertIn("limit", err)

    def test_the_crawler_running_exits_1_and_says_so(self):
        with tick_lock(self.db):
            code, out, err = self.cli("seed-ats")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("the crawler is running", err)
        self.assertEqual(self.client.requests, [])
        self.assertEqual(count(self.conn(), "SELECT count(*) FROM board_candidate"), 0)

    def test_a_tripped_bucket_exits_1(self):
        for token in ("nimbusrobotics", "lumenapps", "meshsystems"):  # whichever the workers send first trips it
            self.client.routes[GH_LIST.format(token)] = status_response(429)
        code, out, _ = self.cli("seed-ats", "--ats", "greenhouse")
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual((payload["status"], payload["report"]["tripped"]), ("partial", ["greenhouse.io"]))
        self.assertEqual((payload["report"]["errors"], payload["report"]["left"]), (1, 2))
        self.assertEqual(count(self.conn(), "SELECT count(*) FROM board_candidate WHERE gated_at IS NULL"), 2)

    def test_seed_ats_without_the_lists_exits_1_and_says_fetch(self):
        shutil.rmtree(self.sources / "stapply")
        code, _, err = self.cli("seed-ats", "--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("fetch stapply", err)


class RunDueTest(CliCase):
    def setUp(self):
        super().setUp()
        self.ok("discover", "lca", "--files", *FILES)  # the quarterly by-hand step run-due never does
        self.marker = self.sources / "stapply" / "refreshed_at.txt"
        self.marker.write_text("2026-10-20T06:30:00Z\n", encoding="utf-8")  # refreshed 2 days ago: not due

    def due(self, *, code=0):
        got, out, err = self.cli("run-due")
        self.assertEqual(got, code, f"{out} {err}")
        return json.loads(out)

    def test_run_due_runs_hn_freehire_formd_stapply_resolve_find_boards_and_the_scorecard_in_order(self):
        report = self.due()
        self.assertEqual(list(report["steps"]),
                         ["hn", "freehire", "formd", "stapply", "resolve", "find_boards", "ats_seed", "scorecard"])
        self.assertEqual({k: v["status"] for k, v in report["steps"].items()},
                         {"hn": "ok", "freehire": "ok", "formd": "ok", "stapply": "skipped", "ats_seed": "skipped",
                          "resolve": "ok", "find_boards": "ok", "scorecard": "ok"})
        self.assertEqual(count(self.conn(), "SELECT count(*) FROM event WHERE kind = 'pipeline_scorecard'"), 1)
        self.assertIn(FREEHIRE_PAGE, self.client.requests)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(self.client.unrouted, [])
        conn = self.conn()
        jobs = [(r["job"], r["status"]) for r in conn.execute("SELECT job, status FROM job_run ORDER BY run_id")]
        self.assertEqual(jobs[-4:], [("discover", "ok"), ("discover", "ok"), ("resolve", "ok"), ("find_boards", "ok")])
        self.assertEqual(count(conn, "SELECT count(*) FROM evidence WHERE fact = 'funding'"), 3)
        self.assertEqual(count(conn, "SELECT count(*) FROM board"), 6)  # careers boards included; Zephyr not added
        self.assertEqual((self.sources / "formd" / "last_read.txt").read_text(encoding="utf-8").strip(), "2026q3")
        self.assertTrue([u for u in self.client.requests if u.endswith("/careers")])  # careers checks were on

    def test_a_later_run_the_same_month_rereads_hn_and_writes_nothing_new(self):
        self.due()
        conn = self.conn()
        before = (count(conn, "SELECT count(*) FROM company"), count(conn, "SELECT count(*) FROM evidence"),
                  count(conn, "SELECT count(*) FROM company_source WHERE source_name = 'hn'"),
                  count(conn, "SELECT count(*) FROM event"))
        hn_calls = sum(u.startswith("https://hn.algolia.com") for u in self.client.requests)
        formd_zips = self.client.requests.count(FORMD_Q3)
        again = self.due()
        self.assertEqual(again["steps"]["hn"]["status"], "ok")
        self.assertGreater(sum(u.startswith("https://hn.algolia.com") for u in self.client.requests), hn_calls)
        self.assertEqual(again["steps"]["formd"]["status"], "skipped")
        self.assertEqual((again["steps"]["resolve"]["status"], again["steps"]["find_boards"]["status"]),
                         ("ok", "ok"))
        self.assertEqual(self.client.requests.count(FORMD_Q3), formd_zips)  # not downloaded again
        after = (count(conn, "SELECT count(*) FROM company"), count(conn, "SELECT count(*) FROM evidence"),
                 count(conn, "SELECT count(*) FROM company_source WHERE source_name = 'hn'"),
                 count(conn, "SELECT count(*) FROM event"))
        self.assertEqual(after, before)

    def test_last_months_thread_is_never_read_as_this_months(self):
        # The fixture listing holds September and October only. On the 2nd of November the new thread is not out:
        # the latest thread (October's) must not be read as November's.
        self.marker.write_text("2026-11-01T06:30:00Z\n", encoding="utf-8")  # the stapply lists are not due then
        with mock.patch.object(cli, "utcnow", lambda: "2026-11-02T06:30:00Z"):
            report = self.due()
        step = report["steps"]["hn"]
        self.assertEqual(step["status"], "skipped")
        self.assertIn("2026-11", step["reason"])
        self.assertNotIn("run_id", step)
        self.assertEqual([u for u in self.client.requests if "/items/" in u], [])  # no thread was read
        conn = self.conn()
        self.assertEqual(count(conn, "SELECT count(*) FROM job_run WHERE job = 'discover' AND "
                                     "json_extract(summary, '$.hn') IS NOT NULL"), 0)
        self.assertEqual(report["status"], "ok")  # a skipped step is not a failure

    def test_once_the_new_thread_exists_it_is_read(self):
        routes = all_routes()
        routes.update(november_client().routes)
        self.client = FakeClient(routes)
        self.marker.write_text("2026-11-01T06:30:00Z\n", encoding="utf-8")  # the stapply lists are not due then
        with mock.patch.object(cli, "utcnow", lambda: "2026-11-02T06:30:00Z"):
            report = self.due()
            self.assertEqual(report["steps"]["hn"]["status"], "ok")
            self.assertTrue([u for u in self.client.requests if u.endswith(f"/items/{NOV}")])
            self.assertFalse([u for u in self.client.requests if u.endswith(f"/items/{OCT}")])  # only this month's
            conn = self.conn()
            posts = count(conn, "SELECT count(*) FROM company_source WHERE source_name = 'hn'")
            self.assertGreater(posts, 0)
            again = self.due()  # a later run the same month reads it again and finds nothing new
        self.assertEqual(again["steps"]["hn"]["status"], "ok")
        self.assertEqual(count(conn, "SELECT count(*) FROM company_source WHERE source_name = 'hn'"), posts)

    def test_a_failed_hn_run_this_month_is_tried_again(self):
        conn = self.conn()
        run_id = start_job(conn, "discover")
        finish_job(conn, run_id, "failed", {"hn": {"error": "x"}}, "hn: x")
        self.assertEqual(self.due()["steps"]["hn"]["status"], "ok")

    def test_another_source_this_month_is_not_an_hn_run(self):
        self.assertEqual(self.due()["steps"]["hn"]["status"], "ok")  # the LCA discover run in setUp is not HN's

    def stapply_routes(self, **extra):
        stapply = ROOT / "tests" / "fixtures" / "stapply"
        routes = all_routes()
        routes[MANIFEST_URL] = stapply / "manifest.json"
        routes["https://storage.stapply.ai/jobhive/v1/ashby/companies.csv"] = stapply / "ashby.csv"
        routes.update(extra)
        self.client = FakeClient(routes)

    def downloads(self):
        return [u for u in self.client.requests if u.startswith("https://storage.stapply.ai/")]

    def test_stapply_is_refreshed_when_the_last_refresh_is_7_days_old(self):
        self.stapply_routes()
        self.marker.write_text("2026-10-15T06:30:00Z\n", encoding="utf-8")  # exactly 7 days before NOW
        report = self.due()
        self.assertEqual(report["steps"]["stapply"]["status"], "ok")
        self.assertEqual(self.downloads(), [MANIFEST_URL])  # every list is current: nothing else is downloaded
        self.assertEqual(self.marker.read_text(encoding="utf-8").strip(), NOW)
        self.assertEqual(self.due()["steps"]["stapply"]["status"], "skipped")  # and not again the next day

    def test_stapply_is_skipped_when_refreshed_less_than_7_days_ago(self):
        self.stapply_routes()
        self.marker.write_text("2026-10-15T06:30:01Z\n", encoding="utf-8")
        self.assertEqual(self.due()["steps"]["stapply"]["status"], "skipped")
        self.assertEqual(self.downloads(), [])

    def test_stapply_with_no_marker_is_due_and_downloads_only_a_changed_file(self):
        self.stapply_routes()
        self.marker.unlink()
        (self.sources / "stapply" / "ashby.csv").write_bytes(b"name,slug,url\nold,old,u\n")  # stale
        report = self.due()
        self.assertEqual(report["steps"]["stapply"]["updated"], ["ashby.csv"])
        self.assertEqual(sorted(self.downloads()), sorted([MANIFEST_URL,
                                                            "https://storage.stapply.ai/jobhive/v1/ashby/companies.csv"]))
        self.assertTrue(self.marker.is_file())

    def test_a_malformed_manifest_entry_is_counted_and_never_stops_the_refresh(self):
        stapply = ROOT / "tests" / "fixtures" / "stapply"
        manifest = json.loads((stapply / "manifest.json").read_text(encoding="utf-8"))
        manifest["by_ats_companies"]["broken"] = {"rows": 3}
        self.stapply_routes(**{MANIFEST_URL: json.dumps(manifest).encode()})
        self.marker.unlink()
        report = self.due()
        self.assertEqual((report["steps"]["stapply"]["status"], report["steps"]["stapply"]["skipped_entries"]),
                         ("ok", 1))
        self.assertEqual(report["status"], "ok")
        self.assertTrue(self.marker.is_file())

    def test_a_failed_stapply_refresh_leaves_the_marker_and_every_other_step_runs(self):
        self.stapply_routes(**{MANIFEST_URL: FetchError("timeout", "slow")})
        self.marker.write_text("2026-10-01T06:30:00Z\n", encoding="utf-8")
        report = self.due(code=1)
        self.assertEqual(report["steps"]["stapply"]["status"], "failed")
        self.assertEqual(self.marker.read_text(encoding="utf-8").strip(), "2026-10-01T06:30:00Z")  # asked again
        self.assertEqual({k: v["status"] for k, v in report["steps"].items() if k != "stapply"},
                         {"hn": "ok", "freehire": "ok", "formd": "ok", "ats_seed": "skipped", "resolve": "ok",
                          "find_boards": "ok", "scorecard": "ok"})

    def test_formd_is_skipped_until_a_newer_set_is_out(self):
        folder = self.sources / "formd"
        folder.mkdir()
        (folder / "last_read.txt").write_text("2026q3\n", encoding="utf-8")
        report = self.due()
        self.assertEqual(report["steps"]["formd"]["status"], "skipped")
        self.assertEqual(self.client.requests.count(FORMD_Q3), 0)  # not downloaded: nothing newer
        (folder / "last_read.txt").write_text("2026q2\n", encoding="utf-8")
        self.assertEqual(self.due()["steps"]["formd"]["status"], "ok")

    def test_a_failed_step_exits_1_and_every_other_step_still_runs(self):
        routes = all_routes()
        routes[HN_SEARCH] = status_response(503)
        self.client = FakeClient(routes)
        report = self.due(code=1)
        self.assertEqual(report["steps"]["hn"]["status"], "failed")
        self.assertEqual({k: v["status"] for k, v in report["steps"].items() if k != "hn"},
                         {"freehire": "ok", "formd": "ok", "stapply": "skipped", "ats_seed": "skipped",
                          "resolve": "ok", "find_boards": "ok", "scorecard": "ok"})
        self.assertEqual(report["status"], "failed")

    def test_a_failed_freehire_step_exits_1_and_every_other_step_still_runs(self):
        routes = all_routes()
        routes[FREEHIRE_PAGE] = status_response(429)
        self.client = FakeClient(routes)
        report = self.due(code=1)
        self.assertEqual(report["steps"]["freehire"]["status"], "failed")
        self.assertEqual({k: v["status"] for k, v in report["steps"].items() if k != "freehire"},
                         {"hn": "ok", "formd": "ok", "stapply": "skipped", "ats_seed": "skipped", "resolve": "ok",
                          "find_boards": "ok", "scorecard": "ok"})

    def test_freehire_off_is_skipped_and_never_asked(self):
        off = dataclasses.replace(load_freehire_config(), enabled=False)
        with mock.patch("outreach.sources.freehire.load_freehire_config", return_value=off):
            report = self.due()
        self.assertEqual(report["steps"]["freehire"]["status"], "skipped")
        self.assertNotIn(FREEHIRE_PAGE, self.client.requests)

    def test_a_partial_step_exits_1_and_the_run_still_finishes(self):
        self.client = FakeClient(all_routes(**{"https://greenfield.ai/careers": status_response(503)}))
        report = self.due(code=1)
        self.assertEqual(report["steps"]["find_boards"]["status"], "partial")
        self.assertEqual(report["steps"]["hn"]["status"], "ok")
        self.assertEqual(report["status"], "partial")

    def test_a_formd_fetch_error_does_not_stop_the_rest(self):
        routes = all_routes()
        routes[FORMD_PAGE] = status_response(503)
        self.client = FakeClient(routes)
        report = self.due(code=1)
        self.assertEqual(report["steps"]["formd"]["status"], "failed")
        self.assertIn("503", report["steps"]["formd"]["error"])
        self.assertEqual((report["steps"]["resolve"]["status"], report["steps"]["find_boards"]["status"]),
                         ("ok", "ok"))

    def test_run_due_seeds_once_a_week(self):
        self.client.routes.update(seed_routes())
        conn = self.conn()

        def seed_run(at, limit, ats=("greenhouse", "ashby")):
            conn.execute("INSERT INTO event(at, recorded_at, entity, kind, actor, detail) "
                         "VALUES (?, ?, 'source', 'ats_seed_run', 'source', ?)",
                         (at, at, json.dumps({"limit": limit, "ats": list(ats)})))

        step = self.due()["steps"]["ats_seed"]
        self.assertEqual(step["status"], "skipped")
        self.assertIn("seed-ats", step["reason"])  # the first full seed is by hand
        seed_run("2026-10-01T06:30:00Z", 10)  # a sample run is no full seed
        seed_run("2026-10-01T06:30:00Z", None, ats=("ashby",))  # nor is one list
        self.assertEqual(self.due()["steps"]["ats_seed"]["status"], "skipped")
        self.assertFalse([u for u in self.client.requests if u in seed_routes()])

        seed_run("2026-10-15T06:30:00Z", None)  # a full seed, 7 days before NOW
        step = self.due()["steps"]["ats_seed"]
        self.assertEqual(step["status"], "ok")
        self.assertEqual(step["report"]["read"], 4)
        self.assertTrue([u for u in self.client.requests if u in seed_routes()])
        self.assertEqual(self.client.unrouted, [])
        last = conn.execute("SELECT at, detail FROM event WHERE kind = 'ats_seed_run' "
                            "ORDER BY event_id DESC").fetchone()
        self.assertEqual((last["at"], json.loads(last["detail"])["limit"]), (NOW, load_seed_config().weekly_limit))

        sent = len(self.client.requests)
        step = self.due()["steps"]["ats_seed"]
        self.assertEqual(step["status"], "skipped")  # once a week
        self.assertIn(NOW, step["reason"])
        self.assertFalse([u for u in self.client.requests[sent:] if u in seed_routes()])

    def full_seed_before(self, at="2026-10-15T06:30:00Z"):
        self.client.routes.update(seed_routes())
        self.conn().execute("INSERT INTO event(at, recorded_at, entity, kind, actor, detail) "
                            "VALUES (?, ?, 'source', 'ats_seed_run', 'source', ?)",
                            (at, at, json.dumps({"limit": None, "ats": ["greenhouse", "ashby"]})))

    def test_the_weekly_limit_bounds_the_top_up(self):
        self.full_seed_before()
        with open(self.sources / "stapply" / "greenhouse.csv", "a", encoding="utf-8") as f:  # boards no one has yet
            f.write("Ytterby Labs,ytterby,https://boards.greenhouse.io/ytterby\n"
                    "Zircon Works,zircon,https://boards.greenhouse.io/zircon\n")
        extra = {GH_LIST.format(t): gh_list(("Software Engineer", "Boston, MA")) for t in ("ytterby", "zircon")}
        self.client.routes.update(extra)
        one = dataclasses.replace(load_seed_config(), weekly_limit=1)
        with mock.patch("outreach.sources.__main__.load_seed_config", return_value=one):
            step = self.due()["steps"]["ats_seed"]
        report = step["report"]
        due = report["read"] - report["known"]
        self.assertGreaterEqual(due, 2, report)
        self.assertEqual(len([u for u in self.client.requests if u in seed_routes() or u in extra]), 1)
        self.assertEqual((report["left"], report["promoted"] + report["rejected"] + report["dead"]), (due - 1, 1))

    def test_the_seed_runs_after_find_boards_so_a_board_found_today_is_known(self):
        self.full_seed_before()
        report = self.due()
        conn = self.conn()
        boards = {(r["ats"], r["token"]) for r in conn.execute("SELECT ats, token FROM board")}
        rows = {(r["ats"], r["token"]): r["gate_result"] for r in conn.execute("SELECT * FROM board_candidate")}
        found_today = {key for key in rows if key in boards and rows[key] == "known"}
        self.assertTrue(found_today, (boards, rows))  # find-boards gave these companies their board first
        self.assertEqual(report["steps"]["ats_seed"]["report"]["known"], len(found_today))
        self.assertEqual(count(conn, "SELECT count(*) FROM review_item WHERE kind = 'merge_candidate' "
                                     "AND reason LIKE '%stapply%'"), 0)

    def test_a_running_crawler_skips_the_top_up_and_the_run_goes_on(self):
        self.full_seed_before()
        with tick_lock(self.db):
            report = self.due()
        step = report["steps"]["ats_seed"]
        self.assertEqual(step["status"], "skipped")
        self.assertIn("the crawler is running", step["reason"])
        self.assertEqual(report["steps"]["scorecard"]["status"], "ok")
        self.assertFalse([u for u in self.client.requests if u in seed_routes()])
        self.assertIsNone(json.loads(self.conn().execute(  # no run recorded: the next run-due tries again
            "SELECT detail FROM event WHERE kind = 'ats_seed_run' ORDER BY event_id DESC").fetchone()[0])["limit"])

    def test_the_weekly_top_up_off_is_skipped(self):
        off = dataclasses.replace(load_seed_config(), enabled=False)
        with mock.patch("outreach.sources.__main__.load_seed_config", return_value=off):
            step = self.due()["steps"]["ats_seed"]
        self.assertEqual(step["status"], "skipped")
        self.assertIn("enabled = false", step["reason"])

    def test_a_partial_formd_run_is_not_marked_read(self):
        self.client = FakeClient(sec_down(all_routes()))
        report = self.due(code=1)
        self.assertEqual(report["steps"]["formd"]["status"], "partial")
        self.assertFalse((self.sources / "formd" / "last_read.txt").exists())  # asked again tomorrow


class LiveGuardTest(unittest.TestCase):
    def test_cli_refuses_the_live_store_outside_the_live_checkout(self):
        env = {k: v for k, v in os.environ.items() if k != "OUTREACH_DB"}
        for argv in (["resolve"], ["find-boards"], ["run-due"], ["discover", "hn"],
                     ["add", "--name", "X Labs", "--reason", "r"]):
            with self.subTest(argv=argv):
                with mock.patch.dict(os.environ, env, clear=True), \
                        mock.patch.object(live_checkout, "is_live", return_value=False), \
                        mock.patch("sqlite3.connect") as opened:
                    with self.assertRaises(SystemExit) as caught:
                        cli.main(argv, client_factory=lambda *a, **k: FakeClient({}))
                self.assertIn("live checkout", str(caught.exception.code))
                opened.assert_not_called()  # nothing was opened


    def test_seed_ats_command_refuses_outside_live_checkout(self):
        env = {k: v for k, v in os.environ.items() if k != "OUTREACH_DB"}  # the default store path
        for argv in (["seed-ats"], ["seed-ats", "--dry-run"], ["seed-ats", "--ats", "ashby", "--limit", "5"]):
            with self.subTest(argv=argv):
                client = FakeClient({})
                with mock.patch.dict(os.environ, env, clear=True), \
                        mock.patch.object(live_checkout, "is_live", return_value=False), \
                        mock.patch("sqlite3.connect") as opened:
                    with self.assertRaises(SystemExit) as caught:
                        cli.main(argv, client_factory=lambda *a, **k: client)
                self.assertIn("live checkout", str(caught.exception.code))
                opened.assert_not_called()
                self.assertEqual(client.requests, [])


class LaunchdTemplateTest(unittest.TestCase):
    PATH = ROOT / "launchd" / "com.devanshu.outreach-sources.plist.template"

    def filled(self):
        text = self.PATH.read_text(encoding="utf-8")
        text = text.replace("{{LIVE_CHECKOUT}}", "/live/checkout").replace("{{LOG_PATH}}", "/logs/sources.log")
        self.assertNotIn("{{", text)  # no placeholder other than the two
        return text

    def test_template_parses_with_the_one_convention(self):
        plist = plistlib.loads(self.filled().encode("utf-8"))
        self.assertEqual(plist["Label"], "com.devanshu.outreach-sources")
        self.assertEqual(plist["WorkingDirectory"], "/live/checkout")
        self.assertEqual(plist["ProgramArguments"],
                         ["/live/checkout/.venv/bin/python", "-m", "outreach.sources", "run-due"])
        self.assertEqual((plist["StandardOutPath"], plist["StandardErrorPath"]),
                         ("/logs/sources.log", "/logs/sources.log"))
        self.assertEqual(plist["StartCalendarInterval"], {"Hour": 6, "Minute": 30})

    def test_template_has_no_environment_secret_or_store_path(self):
        text = self.filled()
        plist = plistlib.loads(text.encode("utf-8"))
        self.assertNotIn("EnvironmentVariables", plist)
        for word in ("OUTREACH_", "token", "secret", "Library/Application Support", ".db"):
            self.assertNotIn(word, text)
        self.assertEqual(sorted(plist), ["Label", "ProgramArguments", "StandardErrorPath", "StandardOutPath",
                                         "StartCalendarInterval", "WorkingDirectory"])


if __name__ == "__main__":
    unittest.main()
