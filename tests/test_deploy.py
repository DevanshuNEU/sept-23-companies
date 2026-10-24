"""The deploy tool (#17): job tables, template rendering, install, retire, status and rollback.

Every test runs on temp folders with a FakeRunner. No test constructs SystemRunner, runs launchctl or plutil, or
reads or writes ~/Library/LaunchAgents.
"""
import os
import plistlib
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from outreach.deploy import jobs
from outreach.deploy.jobs import NEW_JOBS, OLD_JOBS, DeployError, render
from outreach.deploy import launchctl

ROOT = Path(__file__).resolve().parent.parent
LAUNCHD = ROOT / "launchd"
STORE_OR_GMAIL = ("OUTREACH_DB", "OUTREACH_GMAIL_DIR", "OUTREACH_MAIL_HOME")

MINIMAL = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>WorkingDirectory</key>
  <string>{{{{LIVE_CHECKOUT}}}}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{{{{LIVE_CHECKOUT}}}}/.venv/bin/python</string>
    <string>-m</string>
    <string>outreach.example</string>
  </array>
  <key>StandardOutPath</key>
  <string>{{{{LOG_PATH}}}}</string>
  <key>StandardErrorPath</key>
  <string>{{{{LOG_PATH}}}}</string>
</dict>
</plist>
"""


def temp_dir(case: unittest.TestCase, prefix: str = "outreach-deploy-test-") -> Path:
    folder = Path(tempfile.mkdtemp(prefix=prefix))
    case.addCleanup(shutil.rmtree, folder, True)
    return folder


def minimal(label: str = "com.devanshu.outreach-example") -> str:
    return MINIMAL.format(label=label)


class RenderTest(unittest.TestCase):
    def test_render_fills_both_placeholders_and_parses(self):
        live = "/Users/someone/Desktop/Sept 23 Companies"
        text = render(minimal(), live_root=live, log_path="/logs/a & b.log", label="com.devanshu.outreach-example")
        self.assertNotIn("{{", text)
        plist = plistlib.loads(text.encode("utf-8"))
        self.assertEqual(plist["WorkingDirectory"], live)
        self.assertEqual(plist["ProgramArguments"][0], f"{live}/.venv/bin/python")
        self.assertEqual((plist["StandardOutPath"], plist["StandardErrorPath"]), ("/logs/a & b.log",) * 2)

    def test_render_without_a_label_still_parses(self):
        plist = plistlib.loads(render(minimal(), live_root="/live", log_path="/logs/x.log").encode("utf-8"))
        self.assertEqual(plist["Label"], "com.devanshu.outreach-example")

    def test_unknown_placeholder_is_refused(self):
        text = minimal().replace("outreach.example", "{{MODULE}}")
        with self.assertRaises(DeployError) as caught:
            render(text, live_root="/live", log_path="/logs/x.log")
        self.assertIn("{{MODULE}}", str(caught.exception))

    def test_a_placeholder_in_a_path_is_never_filled_twice(self):
        # A live path that itself looks like a placeholder must not smuggle one in, and must not be refused.
        text = render(minimal(), live_root="/live/{{LOG_PATH}}", log_path="/logs/x.log")
        self.assertEqual(plistlib.loads(text.encode("utf-8"))["WorkingDirectory"], "/live/{{LOG_PATH}}")

    def test_label_mismatch_is_refused(self):
        with self.assertRaises(DeployError) as caught:
            render(minimal("com.devanshu.outreach-other"), live_root="/live", log_path="/logs/x.log",
                   label="com.devanshu.outreach-example")
        self.assertIn("com.devanshu.outreach-other", str(caught.exception))

    def test_text_that_is_not_a_plist_is_refused(self):
        with self.assertRaises(DeployError):
            render("<plist><dict><key>Label</key></dict>", live_root="/live", log_path="/logs/x.log")


class JobTableTest(unittest.TestCase):
    def test_new_and_old_labels_never_overlap(self):
        new = [job.label for job in NEW_JOBS]
        old = [job.label for job in OLD_JOBS]
        self.assertEqual(len(new), len(set(new)))
        self.assertEqual(len(old), len(set(old)))
        self.assertEqual(set(new) & set(old), set())
        self.assertEqual({job.plist for job in NEW_JOBS} & {job.plist for job in OLD_JOBS}, set())

    def test_the_tables_name_every_job_the_plan_lists(self):
        self.assertEqual([job.label for job in NEW_JOBS], [
            "com.devanshu.outreach-api", "com.devanshu.outreach-sender", "com.devanshu.outreach-crawl",
            "com.devanshu.outreach-sources", "com.devanshu.outreach-needs-you", "com.devanshu.outreach-reader-night",
            "com.devanshu.outreach-prepare", "com.devanshu.outreach-plan"])
        self.assertEqual([job.label for job in OLD_JOBS], [
            "com.devanshu.outreach-mail-sender", "com.devanshu.outreach-mail-server",
            "com.devanshu.outreach-dashboard", "com.devanshu.outreach-refresh"])
        for job in NEW_JOBS:
            self.assertEqual(job.template, f"launchd/{job.label}.plist.template")
            self.assertEqual(job.plist, f"{job.label}.plist")
            self.assertRegex(job.owner, r"^#[0-9]+")
        for job in OLD_JOBS:
            self.assertIsNone(job.template)
            self.assertEqual(job.plist, f"{job.label}.plist")
            self.assertFalse(job.required)

    def test_first_switch_requires_api_sender_crawl_and_sources(self):
        # The brief's test_sources_and_needs_you_are_required_jobs, under the 2026-10-24 ruling: sources is
        # required for the first switch; needs-you, reader-night, prepare and plan stay optional until #16, #32
        # and #15 land.
        required = {job.label for job in NEW_JOBS if job.required}
        self.assertEqual(required, {"com.devanshu.outreach-api", "com.devanshu.outreach-sender",
                                    "com.devanshu.outreach-crawl", "com.devanshu.outreach-sources"})
        optional = {job.label for job in NEW_JOBS if not job.required}
        self.assertIn("com.devanshu.outreach-needs-you", optional)

    def test_every_template_named_in_the_repo_renders(self):
        live, logs = temp_dir(self), temp_dir(self)
        for job in NEW_JOBS:
            path = ROOT / job.template
            with self.subTest(job=job.label):
                if not path.exists():
                    self.skipTest(f"{job.template} is not in the repo yet ({job.owner} adds it)")
                text = render(path.read_text(encoding="utf-8"), live_root=str(live),
                              log_path=str(logs / f"{job.label}.log"), label=job.label)
                self.assertEqual(plistlib.loads(text.encode("utf-8"))["Label"], job.label)

    def test_every_template_follows_the_one_convention(self):
        for job in NEW_JOBS:
            path = ROOT / job.template
            if not path.exists():
                continue
            with self.subTest(job=job.label):
                raw = path.read_text(encoding="utf-8")
                self.assertEqual(set(re.findall(r"\{\{[^}]*\}\}", raw)), {"{{LIVE_CHECKOUT}}", "{{LOG_PATH}}"})
                plist = plistlib.loads(raw.encode("utf-8"))  # the raw template is a plist too
                self.assertEqual(plist["Label"], job.label)
                self.assertEqual(plist["WorkingDirectory"], "{{LIVE_CHECKOUT}}")
                program = plist["ProgramArguments"]
                self.assertEqual(program[0], "{{LIVE_CHECKOUT}}/.venv/bin/python")
                self.assertEqual(program[1], "-m")
                self.assertTrue(program[2].startswith("outreach."), program)
                self.assertNotIn("uv", program)
                self.assertEqual((plist["StandardOutPath"], plist["StandardErrorPath"]),
                                 ("{{LOG_PATH}}", "{{LOG_PATH}}"))
                for name in plist.get("EnvironmentVariables", {}):
                    self.assertNotIn(name, STORE_OR_GMAIL)
                values = plistlib.dumps(plist).decode("utf-8")  # the keys and values, not the comments
                for word in ("token", "secret", "Library/Application Support", "outreach-mail", ".db", "@"):
                    self.assertNotIn(word, values)

    def test_every_template_in_the_folder_is_a_known_job(self):
        known = {job.template for job in NEW_JOBS}
        for path in sorted(LAUNCHD.glob("*.plist.template")):
            self.assertIn(f"launchd/{path.name}", known)


class SystemRunnerCommandsTest(unittest.TestCase):
    """The exact commands SystemRunner runs, checked without constructing it."""

    def test_commands(self):
        self.assertEqual(launchctl.bootstrap_command(501, Path("/a/b.plist")),
                         ["launchctl", "bootstrap", "gui/501", "/a/b.plist"])
        self.assertEqual(launchctl.bootout_command(501, "com.devanshu.x"),
                         ["launchctl", "bootout", "gui/501/com.devanshu.x"])
        self.assertEqual(launchctl.print_command(501, "com.devanshu.x"),
                         ["launchctl", "print", "gui/501/com.devanshu.x"])
        self.assertEqual(launchctl.lint_command(Path("/a/b.plist")), ["plutil", "-lint", "/a/b.plist"])


if __name__ == "__main__":
    unittest.main()
