"""The sources CLI (#31 Task 16): every command through `main`, on scratch settings and fake HTTP clients.

`CliCase` is the base the end-to-end test builds on: a scratch store, a scratch sources folder holding the stapply
fixture, and one `FakeClient` that stands for every host (HN, SEC, DOL, careers pages). Nothing here touches the
network, the live store or the real sources folder.
"""
import contextlib
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
from outreach.net import FetchError
from outreach.sources import __main__ as cli
from outreach.sources.formd import SEC_USER_AGENT
from outreach.store import connect, finish_job, start_job, writes
from tests.fakes import FakeClient, status_response, text_response
from tests.test_formd import DATASET, routes as formd_routes
from tests.test_hn import NOV, OCT, fixture_client, november_client
from tests.test_jobs_find_boards import STAPPLY, careers_routes
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
    """What every host the CLI talks to answers: HN's Algolia, SEC (page, data set, submissions), careers pages.
    Greenfield's site answers 404, so a clean run is `ok`; `careers_overrides` replace a careers route."""
    routes = dict(fixture_client().routes)
    routes.update(formd_routes())
    routes.update({FORMD_PAGE: text_response(PAGE_HTML), FORMD_Q3: formd_zip()})
    routes.update(careers_routes(**{"https://greenfield.ai/careers": status_response(404),
                                    "https://greenfield.ai/": status_response(404), **careers_overrides}))
    return routes


class CliCase(unittest.TestCase):
    def setUp(self):
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


class RunDueTest(CliCase):
    def setUp(self):
        super().setUp()
        self.ok("discover", "lca", "--files", *FILES)  # the quarterly by-hand step run-due never does

    def due(self, *, code=0):
        got, out, err = self.cli("run-due")
        self.assertEqual(got, code, f"{out} {err}")
        return json.loads(out)

    def test_run_due_runs_hn_formd_resolve_and_find_boards_in_order(self):
        report = self.due()
        self.assertEqual(list(report["steps"]), ["hn", "formd", "resolve", "find_boards"])
        self.assertEqual({k: v["status"] for k, v in report["steps"].items()},
                         {"hn": "ok", "formd": "ok", "resolve": "ok", "find_boards": "ok"})
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
                         {"formd": "ok", "resolve": "ok", "find_boards": "ok"})
        self.assertEqual(report["status"], "failed")

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
