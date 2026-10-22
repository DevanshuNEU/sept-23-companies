"""The crawl CLI and its launchd template (#21, Task 13), on scratch stores and fake clients only.

Nothing here touches the network or the live store; the template is parsed, never loaded.
"""
import contextlib
import io
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from outreach.crawl import __main__ as cli
from outreach.crawl.tick import tick_lock
from outreach.net import Response
from tests.fakes import FakeClient, status_response
from tests.storetest import fresh_store, seed_board, seed_company

ROOT = Path(__file__).resolve().parent.parent
JOBS = (ROOT / "tests" / "fixtures" / "crawl" / "greenhouse_jobs.json").read_bytes()
ACME = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"


def temp_dir(case: unittest.TestCase) -> str:
    folder = tempfile.mkdtemp(prefix="outreach-crawl-cli-")
    case.addCleanup(shutil.rmtree, folder, True)
    return folder


class RefusalTests(unittest.TestCase):
    """Outside the live checkout every command stops before it opens the store or builds a client."""

    def refuses(self, argv):
        elsewhere = temp_dir(self)
        with mock.patch("sqlite3.connect") as opened, mock.patch.object(cli, "UrllibClient") as built:
            with self.assertRaises(SystemExit) as caught:
                cli.main(argv, root=str(ROOT), live_root=elsewhere)
        self.assertIn("live checkout", str(caught.exception.code))
        opened.assert_not_called()
        built.assert_not_called()

    def test_tick_refuses_outside_the_live_checkout(self):
        self.refuses(["tick"])

    def test_read_refuses_outside_the_live_checkout(self):
        self.refuses(["read", "--company", "1"])

    def test_probe_refuses_outside_the_live_checkout(self):
        self.refuses(["probe", "greenhouse", "acme"])


class CommandTests(unittest.TestCase):
    """The commands run when `root` is the live root: here a temp folder stands for both, on a scratch store."""

    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.path = self.conn.execute("PRAGMA database_list").fetchone()["file"]
        env = mock.patch.dict(os.environ, {"OUTREACH_DB": self.path})
        env.start()
        self.addCleanup(env.stop)
        self.live = temp_dir(self)

    def run_cli(self, argv, client):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv, root=self.live, live_root=self.live, client=client)
        return code, out.getvalue(), err.getvalue()

    def test_tick_reads_the_due_boards_and_prints_one_line(self):
        seed_board(self.conn, seed_company(self.conn, "Acme"))
        client = FakeClient({ACME: Response(200, "", JOBS, {"etag": '"A"'})})
        code, out, _ = self.run_cli(["tick"], client)
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertIn("ok 1", out)
        self.assertIn("pages:", out)
        self.assertEqual(client.requests, [ACME])
        self.assertEqual(self.conn.execute("SELECT last_read FROM board").fetchone()[0], "ok")

    def test_tick_exits_0_when_another_tick_holds_the_lock(self):
        seed_board(self.conn, seed_company(self.conn, "Acme"))
        client = FakeClient({})
        with tick_lock(self.path):
            code, out, _ = self.run_cli(["tick"], client)
        self.assertEqual(code, 0)
        self.assertIn("another crawl holds", out)
        self.assertEqual(client.requests, [])

    def test_read_reads_only_the_named_companies(self):
        acme = seed_company(self.conn, "Acme")
        seed_board(self.conn, acme)
        seed_board(self.conn, seed_company(self.conn, "Other"), token="other")
        client = FakeClient({ACME: Response(200, "", JOBS, {"etag": '"A"'})})
        code, out, _ = self.run_cli(["read", "--company", str(acme)], client)
        self.assertEqual(code, 0, out)
        self.assertEqual(client.requests, [ACME])

    def test_probe_prints_the_result_and_touches_no_store(self):
        client = FakeClient({ACME: Response(200, "", JOBS, {"etag": '"A"'})})
        with mock.patch.object(cli, "connect") as opened:
            code, out, _ = self.run_cli(["probe", "greenhouse", "acme"], client)
        opened.assert_not_called()
        self.assertEqual(code, 0)
        self.assertIn("greenhouse acme: ok, 3 jobs", out)
        self.assertIn('etag "A"', out)
        self.assertIn("Software Engineer, New Grad", out)

    def test_probe_of_an_error_exits_1(self):
        client = FakeClient({ACME: status_response(500)})
        code, out, _ = self.run_cli(["probe", "greenhouse", "acme"], client)
        self.assertEqual(code, 1)
        self.assertIn("error", out)

    def test_probe_of_an_ats_with_no_adapter_exits_1(self):
        code, _, err = self.run_cli(["probe", "icims", "acme.icims.com"], FakeClient({}))
        self.assertEqual(code, 1)
        self.assertIn("no adapter", err)


class LazyPackageTests(unittest.TestCase):
    def test_importing_stale_loads_no_adapter_polite_or_http_module(self):
        # Ranking imports outreach.crawl.stale; the package must not drag the crawler's HTTP side in with it.
        code = ("import sys, outreach.crawl.stale; "
                "bad = [m for m in ('outreach.crawl.ats', 'outreach.crawl.polite', 'outreach.net', "
                "'outreach.crawl.tick') if m in sys.modules]; print(bad)")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.strip(), "[]", out.stderr)

    def test_run_tick_and_read_company_boards_come_from_the_package(self):
        import outreach.crawl as crawl
        from outreach.crawl import tick
        self.assertIs(crawl.run_tick, tick.run_tick)
        self.assertIs(crawl.read_company_boards, tick.read_company_boards)
        with self.assertRaises(AttributeError):
            crawl.no_such_name


class LaunchdTemplateTest(unittest.TestCase):
    PATH = ROOT / "launchd" / "com.devanshu.outreach-crawl.plist.template"

    def filled(self):
        text = self.PATH.read_text(encoding="utf-8")
        text = text.replace("{{LIVE_CHECKOUT}}", "/live/checkout").replace("{{LOG_PATH}}", "/logs/crawl.log")
        self.assertNotIn("{{", text)  # no placeholder other than the two
        return text

    def test_plist_is_valid_and_runs_every_1800_seconds(self):
        plist = plistlib.loads(self.filled().encode("utf-8"))
        self.assertEqual(plist["Label"], "com.devanshu.outreach-crawl")
        self.assertEqual(plist["StartInterval"], 1800)
        self.assertEqual(plist["WorkingDirectory"], "/live/checkout")
        self.assertEqual(plist["ProgramArguments"],
                         ["/live/checkout/.venv/bin/python", "-m", "outreach.crawl", "tick"])
        self.assertEqual((plist["StandardOutPath"], plist["StandardErrorPath"]),
                         ("/logs/crawl.log", "/logs/crawl.log"))
        self.assertEqual(sorted(plist), ["Label", "ProgramArguments", "StandardErrorPath", "StandardOutPath",
                                         "StartInterval", "WorkingDirectory"])

    def test_plist_has_no_secrets_or_email(self):
        text = self.filled()
        self.assertNotIn("@", text)
        for word in ("OUTREACH_", "token", "secret", "Library/Application Support", ".db", "EnvironmentVariables"):
            self.assertNotIn(word, text)

    def test_the_template_says_a_missed_tick_is_safe(self):
        self.assertIn("sleeps", self.PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
