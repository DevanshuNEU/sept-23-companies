"""Task 14: the sender's command line, its live guard and its launchd template.

Nothing here builds a real Gmail client, runs a real tick or loads a launchd job: the client and `connect` are
patched where a command would reach them, the template is parsed and never loaded.
"""
import contextlib
import importlib.util
import io
import os
import plistlib
import re
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from outreach.sending import __main__ as cli
from outreach.sending.config import load_config
from outreach.sending.fakegmail import FakeClock, FakeGmail
from outreach.sending.tick import TickReport
from tests.sendtest import MON, seed_email_person, seed_message
from tests.storetest import fresh_store, seed_company

ROOT = Path(__file__).resolve().parent.parent
SENDING = ROOT / "outreach" / "sending"
TEMPLATE = ROOT / "launchd" / "com.devanshu.outreach-sender.plist.template"


def _has_deploy_jobs() -> bool:
    try:
        return importlib.util.find_spec("outreach.deploy.jobs") is not None
    except ModuleNotFoundError:  # the parent package does not exist yet
        return False


DEPLOY_JOBS = _has_deploy_jobs()


def temp_dir(case: unittest.TestCase) -> str:
    folder = tempfile.mkdtemp(prefix="outreach-sending-cli-")
    case.addCleanup(shutil.rmtree, folder, True)
    return folder


def run(argv, **kw) -> tuple[int | str, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv, **kw)
        except SystemExit as exit_:
            code = exit_.code
    return code, out.getvalue(), err.getvalue()


class RefusalTest(unittest.TestCase):
    """Outside the live checkout `run`, `whoami` and `auth` stop before they open a store or build a client."""

    def refuses(self, argv):
        elsewhere = temp_dir(self)
        folder = Path(os.environ["OUTREACH_DB"]).parent
        before = sorted(p.name for p in folder.iterdir()) if folder.exists() else None
        with mock.patch.object(cli, "GmailClient") as client, mock.patch.object(cli, "connect") as opened, \
                mock.patch.object(cli, "load_config") as config, mock.patch("sqlite3.connect") as sqlite, \
                mock.patch("outreach.sending.gmail.main") as signed_in:
            with self.assertRaises(SystemExit) as caught:
                cli.main(argv, root=str(ROOT), live_root=elsewhere)
        self.assertIn("live checkout", str(caught.exception.code))
        for fake in (client, opened, config, sqlite, signed_in):
            fake.assert_not_called()
        self.assertEqual(sorted(p.name for p in folder.iterdir()) if folder.exists() else None, before,
                         "a file was created")

    def test_run_refuses_outside_the_live_checkout(self):
        self.refuses(["run"])

    def test_dry_run_refuses_outside_the_live_checkout_too(self):
        self.refuses(["run", "--dry-run"])

    def test_whoami_refuses_outside_the_live_checkout(self):
        self.refuses(["whoami"])

    def test_auth_refuses_outside_the_live_checkout(self):
        self.refuses(["auth"])

    def test_scratch_settings_do_not_lift_the_guard(self):
        # OUTREACH_DB, OUTREACH_GMAIL_DIR and OUTREACH_MAIL_HOME are all scratch in every test process.
        self.assertIn("outreach-db-test-", os.environ["OUTREACH_DB"])
        self.refuses(["run"])

    def test_no_bypass_exists(self):
        for path in SENDING.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for word in ("allow-non-live", "--force", "OUTREACH_LIVE"):
                self.assertNotIn(word, text, f"{word} in {path.name}")

    def test_the_guard_is_the_first_statement_of_main_after_parsing(self):
        source = (SENDING / "__main__.py").read_text(encoding="utf-8")
        body = source[source.index("def main("):]
        self.assertLess(body.index("require_live"), body.index("_run("))
        self.assertLess(body.index("require_live"), body.index("_auth()"))


class CommandSetTest(unittest.TestCase):
    def test_there_is_no_resume_or_approve_command(self):
        for word in ("resume", "approve", "unpause", "pause"):
            code, _, err = run([word])
            self.assertEqual(code, 2, word)
            self.assertIn("invalid choice", err)

    def test_the_commands_are_exactly_run_status_whoami_auth_simulate(self):
        sub = next(a for a in cli._parser()._actions if a.dest == "command")
        self.assertEqual(sorted(sub.choices), ["auth", "run", "simulate", "status", "whoami"])

    def test_simulate_defaults_to_ten_days(self):
        self.assertEqual(cli._parser().parse_args(["simulate"]).days, 10)


class LiveRunTest(unittest.TestCase):
    """With `root` standing for the live checkout, `run` builds its tick from the right parts."""

    def setUp(self):
        self.live = temp_dir(self)
        self.db = fresh_store()
        self.client = mock.Mock(name="GmailClient")
        self.seen = {}

        def fake_tick(conn, deps, cfg, *, dry_run=False):
            self.seen.update(conn=conn, deps=deps, cfg=cfg, dry_run=dry_run)
            return TickReport(auth="ok", day=None, reconciled={}, inbound={}, sent=(1, 2), would_send=None,
                              waiting={}, dry_run=dry_run, lines=("sent message 1", "sent message 2"))

        patches = [mock.patch.object(cli, "GmailClient", return_value=self.client),
                   mock.patch.object(cli, "connect", return_value=self.db), mock.patch.object(cli, "run_tick", fake_tick),
                   mock.patch.object(cli, "launchctl_loaded", side_effect=lambda label: (lambda: label == "gone"))]
        self.connect = patches[1].start()
        for p in (patches[0], patches[2], patches[3]):
            p.start()
        for p in patches:
            self.addCleanup(p.stop)

    def go(self, *argv):
        return run(list(argv), root=self.live, live_root=self.live)

    def test_run_wires_the_tick_for_launchd(self):
        self.db.execute("INSERT INTO job_run(job, started_at, finished_at, status) VALUES ('sender', '2026-11-02T14:00:00Z', "
                        "'2026-11-02T14:00:01Z', 'ok')")
        code, out, _ = self.go("run")
        self.assertEqual(code, 0)
        self.connect.assert_called_once_with(fullfsync=True)
        deps = self.seen["deps"]
        self.assertIs(deps.client, self.client)
        self.assertIs(deps.notify, cli.mac_notifier)
        self.assertEqual(self.seen["cfg"], load_config())
        self.assertFalse(self.seen["dry_run"])
        self.assertFalse(deps.old_sender_loaded())  # label is com.devanshu.outreach-mail-sender, not "gone"
        self.assertEqual(deps.clock().tzinfo, timezone.utc)
        self.assertIn("sent 2", out)
        self.assertIn("sent message 1", out)

    def test_run_passes_the_dry_run_flag(self):
        code, out, _ = self.go("run", "--dry-run")
        self.assertEqual(code, 0)
        self.assertTrue(self.seen["dry_run"])
        self.assertIn("dry run", out)

    def test_a_partial_tick_exits_1(self):
        self.db.execute("INSERT INTO job_run(job, started_at, finished_at, status, error) VALUES ('sender', "
                        "'2026-11-02T14:00:00Z', '2026-11-02T14:00:01Z', 'partial', 'x')")
        self.assertEqual(self.go("run")[0], 1)

    def test_a_signed_out_tick_exits_1(self):
        with mock.patch.object(cli, "run_tick", return_value=TickReport(
                auth="signed_out", day=None, reconciled={}, inbound={}, sent=(), would_send=None, waiting={},
                dry_run=False, lines=("Gmail is signed out",))):
            self.assertEqual(self.go("run")[0], 1)

    def test_a_store_error_is_one_line_on_stderr_and_exit_1(self):
        self.connect.side_effect = cli.StoreError("no store at x; run: python -m outreach.install")
        code, _, err = self.go("run")
        self.assertEqual(code, 1)
        self.assertIn("error:", err)
        self.assertNotIn("Traceback", err)

    def test_whoami_prints_the_address(self):
        self.client.profile.return_value = mock.Mock(email="devanshurajesh@gmail.com")
        code, out, _ = self.go("whoami")
        self.assertEqual((code, out.strip()), (0, "devanshurajesh@gmail.com"))

    def test_whoami_with_a_dead_token_says_how_to_sign_in(self):
        self.client.profile.side_effect = cli.GmailError("auth", 401, "invalid_grant")
        code, _, err = self.go("whoami")
        self.assertEqual(code, 1)
        self.assertIn("outreach.sending auth", err)

    def test_auth_delegates_to_the_gmail_sign_in(self):
        with mock.patch("outreach.sending.gmail.main", return_value=0) as signed_in:
            self.assertEqual(self.go("auth")[0], 0)
        signed_in.assert_called_once_with(["auth"])


class LaunchctlTest(unittest.TestCase):
    def test_loaded_is_launchctl_print_exiting_zero(self):
        with mock.patch.object(cli.subprocess, "run", return_value=mock.Mock(returncode=0)) as ran:
            self.assertTrue(cli.launchctl_loaded("com.example.job")())
        command = ran.call_args.args[0]
        self.assertEqual(command[:2], ["launchctl", "print"])
        self.assertEqual(command[2], f"gui/{os.getuid()}/com.example.job")
        with mock.patch.object(cli.subprocess, "run", return_value=mock.Mock(returncode=113)):
            self.assertFalse(cli.launchctl_loaded("com.example.job")())

    def test_a_launchctl_that_cannot_run_raises_so_the_tick_takes_the_job_as_loaded(self):
        with mock.patch.object(cli.subprocess, "run", side_effect=FileNotFoundError("launchctl")):
            with self.assertRaises(FileNotFoundError):
                cli.launchctl_loaded("com.example.job")()


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()
        self.path = self.db.execute("PRAGMA database_list").fetchone()["file"]
        patch = mock.patch.dict(os.environ, {"OUTREACH_DB": self.path})
        patch.start()
        self.addCleanup(patch.stop)

    def test_status_on_a_scratch_store_prints_the_state(self):
        company = seed_company(self.db, "Acme")
        seed_message(self.db, seed_email_person(self.db, company, "Ann", "ann@acme.co"))
        code, out, _ = run(["status"], now=MON, old_sender_loaded=lambda: False)
        self.assertEqual(code, 0)
        self.assertIn("state: running", out)
        self.assertIn("sent today: 0 of 20", out)
        self.assertIn("ramp: starts at 20", out)
        self.assertIn("1 message_id_unproven", out)
        self.assertIn("next send: none", out)

    def test_status_shows_a_loaded_old_sender(self):
        _, out, _ = run(["status"], now=MON, old_sender_loaded=lambda: True)
        self.assertIn("state: paused_by_you", out)
        self.assertIn("old mail sender", out)

    def test_status_writes_nothing(self):
        before = self.db.total_changes
        run(["status"], now=MON, old_sender_loaded=lambda: False)
        self.assertEqual(self.db.total_changes, before)

    def test_status_refuses_the_live_store_outside_the_live_checkout(self):
        with mock.patch.dict(os.environ, {"OUTREACH_DB": ""}), mock.patch("live_checkout.is_live", return_value=False), \
                mock.patch("sqlite3.connect") as opened:
            code, _, err = run(["status"], now=MON, old_sender_loaded=lambda: False)
        self.assertIn("live checkout", str(code))
        opened.assert_not_called()


class SimulateTest(unittest.TestCase):
    def test_simulate_never_opens_outreach_db(self):
        folder = Path(temp_dir(self)) / "missing"
        target = folder / "outreach.db"
        with mock.patch.dict(os.environ, {"OUTREACH_DB": str(target)}), \
                mock.patch.object(cli, "GmailClient") as client, mock.patch.object(cli, "connect") as opened:
            code, out, _ = run(["simulate", "--days", "2"])
        self.assertEqual(code, 0)
        self.assertFalse(folder.exists(), "simulate touched the OUTREACH_DB folder")
        client.assert_not_called()
        opened.assert_not_called()
        self.assertEqual(len([line for line in out.splitlines() if line.startswith("2026-")]), 2)

    def test_simulate_removes_its_temporary_store(self):
        before = {p for p in Path(tempfile.gettempdir()).glob("outreach-sim-*")}
        run(["simulate", "--days", "1"])
        self.assertEqual({p for p in Path(tempfile.gettempdir()).glob("outreach-sim-*")}, before)

    def test_simulate_refuses_no_days(self):
        code, _, err = run(["simulate", "--days", "0"])
        self.assertEqual(code, 1)
        self.assertIn("--days", err)


class TemplateTest(unittest.TestCase):
    def filled(self) -> str:
        text = TEMPLATE.read_text(encoding="utf-8")
        found = set(re.findall(r"\{\{[A-Z_]+\}\}", text))
        self.assertEqual(found, {"{{LIVE_CHECKOUT}}", "{{LOG_PATH}}"})
        text = text.replace("{{LIVE_CHECKOUT}}", "/live/checkout").replace("{{LOG_PATH}}", "/logs/sender.log")
        self.assertNotIn("{{", text)
        return text

    def test_template_runs_every_120_seconds_with_only_the_two_placeholders_and_no_secret(self):
        text = self.filled()
        plist = plistlib.loads(text.encode("utf-8"))
        self.assertEqual(plist["Label"], "com.devanshu.outreach-sender")
        self.assertEqual(plist["StartInterval"], 120)
        self.assertEqual(plist["WorkingDirectory"], "/live/checkout")
        self.assertEqual(plist["ProgramArguments"],
                         ["/live/checkout/.venv/bin/python", "-m", "outreach.sending", "run"])
        self.assertEqual((plist["StandardOutPath"], plist["StandardErrorPath"]),
                         ("/logs/sender.log", "/logs/sender.log"))
        self.assertEqual(sorted(plist), ["Label", "ProgramArguments", "StandardErrorPath", "StandardOutPath",
                                         "StartInterval", "WorkingDirectory"])
        self.assertNotIn("@", text)
        for word in ("OUTREACH_", "token", "secret", "Library/Application Support", ".db", "EnvironmentVariables",
                     "--dry-run"):
            self.assertNotIn(word, text)

    def test_template_says_it_is_not_installed_here(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("Not installed by the repo", text)
        self.assertIn("sleeps", text)

    @unittest.skipUnless(DEPLOY_JOBS, "needs #17's outreach.deploy.jobs")
    def test_template_renders_with_the_deploy_tool(self):
        from outreach.deploy import jobs
        live, logs = temp_dir(self), temp_dir(self)
        plist = plistlib.loads(jobs.render(TEMPLATE.read_text(encoding="utf-8"), live_root=live,
                                           log_path=os.path.join(logs, "sender.log")).encode("utf-8"))
        self.assertEqual(plist["Label"], "com.devanshu.outreach-sender")


if __name__ == "__main__":
    unittest.main()
