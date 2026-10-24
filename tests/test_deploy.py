"""The deploy tool (#17): job tables, template rendering, install, retire, status and rollback.

Every test runs on temp folders with a FakeRunner. No test constructs SystemRunner, runs launchctl or plutil, or
reads or writes ~/Library/LaunchAgents.
"""
import contextlib
import io
import json
import os
import plistlib
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from outreach.deploy import launchctl
from outreach.deploy.jobs import NEW_JOBS, OLD_JOBS, DeployError, render

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



# Task 18: install, retire-old, status and rollback, on temp folders with a FakeRunner.

REQUIRED = [job for job in NEW_JOBS if job.required]
OPTIONAL = [job for job in NEW_JOBS if not job.required]
REMINDER = ("The old sender is paused in the old dashboard. Run python -m outreach.importer rollback-check before "
            "you unpause it.")


class FakeRunner:
    """Records every call and keeps a loaded set the way launchd would: bootstrap of a loaded label fails."""

    def __init__(self, loaded=()):
        self.loaded_labels = set(loaded)
        self.calls: list[tuple[str, str]] = []
        self.fail_bootstrap: set[str] = set()
        self.fail_bootout: set[str] = set()
        self.fail_lint: set[str] = set()

    def bootstrap(self, plist: Path) -> None:
        label = plistlib.loads(Path(plist).read_bytes())["Label"]
        self.calls.append(("bootstrap", Path(plist).name))
        if label in self.fail_bootstrap:
            raise DeployError(f"Bootstrap failed: 5: Input/output error ({label})")
        if label in self.loaded_labels:
            raise DeployError(f"Bootstrap failed: {label} is already loaded")
        self.loaded_labels.add(label)

    def bootout(self, label: str) -> None:
        self.calls.append(("bootout", label))
        if label in self.fail_bootout:
            raise DeployError(f"Boot-out failed: 5: Input/output error ({label})")
        if label not in self.loaded_labels:
            raise DeployError(f"Boot-out failed: 3: No such process ({label})")
        self.loaded_labels.discard(label)

    def loaded(self, label: str) -> bool:
        self.calls.append(("loaded", label))
        return label in self.loaded_labels

    def lint(self, plist: Path) -> None:
        self.calls.append(("lint", Path(plist).name))
        if plistlib.loads(Path(plist).read_bytes())["Label"] in self.fail_lint:
            raise DeployError(f"{plist}: invalid")

    def changes(self) -> list[tuple[str, str]]:
        return [call for call in self.calls if call[0] != "loaded"]


def snapshot(folder: Path) -> dict[str, bytes]:
    if not folder.exists():
        return {}
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


class DeployCase(unittest.TestCase):
    def setUp(self):
        base = temp_dir(self)
        self.live = base / "Sept 23 Companies"
        (self.live / "launchd").mkdir(parents=True)
        for job in REQUIRED:
            shutil.copy(ROOT / job.template, self.live / job.template)
        (self.live / ".venv" / "bin").mkdir(parents=True)
        (self.live / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
        (self.live / "web" / "dist").mkdir(parents=True)
        (self.live / "web" / "dist" / "index.html").write_text("<!doctype html>", encoding="utf-8")
        (self.live / "mail").mkdir()
        self.set_old_config({"paused": True, "pausedWhy": "paused by you"})  # S1: paused in the old dashboard
        self.agents = base / "LaunchAgents"
        self.agents.mkdir()
        self.logs = base / "Logs" / "outreach"
        for job in OLD_JOBS:
            (self.agents / job.plist).write_text(minimal(job.label).replace("outreach.example", job.label),
                                                encoding="utf-8")
        (self.agents / "com.example.unrelated.plist").write_text(minimal("com.example.unrelated"), encoding="utf-8")
        self.runner = FakeRunner(loaded=[job.label for job in OLD_JOBS] + ["com.example.unrelated"])

    def main(self, *argv, root: Path | None = None):
        from outreach.deploy.__main__ import main
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv), runner=self.runner, agents_dir=self.agents, root=str(root or self.live),
                        live_root=str(self.live), log_dir=self.logs)
        return code, out.getvalue(), err.getvalue()

    def set_old_config(self, config) -> None:
        path = self.live / "mail" / "config.json"
        if config is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(config if isinstance(config, str) else json.dumps(config), encoding="utf-8")

    def elsewhere(self) -> Path:
        other = temp_dir(self, "outreach-deploy-worktree-")
        shutil.copytree(self.live / "launchd", other / "launchd")
        return other

    def rendered(self, job) -> bytes:
        return render((self.live / job.template).read_text(encoding="utf-8"), live_root=str(self.live),
                      log_path=str(self.logs / f"{job.label.removeprefix('com.devanshu.outreach-')}.log"),
                      label=job.label).encode("utf-8")

    def add_optional_template(self, job) -> None:
        (self.live / job.template).write_text(minimal(job.label), encoding="utf-8")


class RefusalTest(DeployCase):
    def test_install_refuses_outside_the_live_checkout_and_writes_nothing(self):
        before, other = snapshot(self.agents), self.elsewhere()
        with self.assertRaises(SystemExit) as caught:
            self.main("install", root=other)
        self.assertIn("live checkout", str(caught.exception))
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(snapshot(self.agents), before)
        self.assertFalse(self.logs.exists())

    def test_retire_old_and_rollback_refuse_outside_the_live_checkout(self):
        before, other = snapshot(self.agents), self.elsewhere()
        for command in ("retire-old", "rollback"):
            with self.subTest(command=command), self.assertRaises(SystemExit):
                self.main(command, root=other)
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(snapshot(self.agents), before)

    def test_no_command_calls_the_mail_api(self):
        for path in sorted((ROOT / "outreach" / "deploy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for word in ("3002", "/api/approve", "/api/pause", "/api/save", "/api/skip", "/api/unapprove", "urllib",
                         "http.client", "socket"):
                with self.subTest(file=path.name, word=word):
                    self.assertNotIn(word, text)

    def test_an_unknown_command_is_refused(self):
        with self.assertRaises(SystemExit):
            self.main("deploy-everything")
        self.assertEqual(self.runner.calls, [])


class InstallTest(DeployCase):
    def test_install_renders_lints_and_bootstraps_each_present_job(self):
        code, out, err = self.main("install")
        self.assertEqual(code, 0, err)
        expected = []
        for job in REQUIRED:
            expected += [("lint", job.plist), ("bootstrap", job.plist)]
            self.assertEqual((self.agents / job.plist).read_bytes(), self.rendered(job))
            self.assertIn(job.label, self.runner.loaded_labels)
        self.assertEqual(self.runner.changes(), expected)
        for job in OPTIONAL:
            self.assertFalse((self.agents / job.plist).exists())
            self.assertIn(f"{job.label}: skipped, no template yet ({job.owner})", out)
        self.assertEqual(len(out.strip().splitlines()), len(NEW_JOBS))
        self.assertTrue(self.logs.is_dir())
        plist = plistlib.loads((self.agents / "com.devanshu.outreach-api.plist").read_bytes())
        self.assertEqual(plist["StandardOutPath"], str(self.logs / "api.log"))
        self.assertEqual(plist["WorkingDirectory"], str(self.live))

    def test_install_installs_an_optional_job_whose_template_exists(self):
        job = OPTIONAL[0]
        self.add_optional_template(job)
        code, out, err = self.main("install")
        self.assertEqual(code, 0, err)
        self.assertIn(("bootstrap", job.plist), self.runner.changes())

    def test_install_skips_an_optional_job_on_request(self):
        job = OPTIONAL[0]
        self.add_optional_template(job)
        code, out, err = self.main("install", "--skip", job.label)
        self.assertEqual(code, 0, err)
        self.assertFalse((self.agents / job.plist).exists())
        self.assertIn(f"{job.label}: skipped on request", out)

    def test_install_refuses_to_skip_a_required_or_unknown_job(self):
        before = snapshot(self.agents)
        for label in ("com.devanshu.outreach-sources", "com.devanshu.outreach-nope"):
            with self.subTest(label=label):
                code, _, err = self.main("install", "--skip", label)
                self.assertEqual(code, 1)
                self.assertIn(label, err)
        self.assertEqual(self.runner.changes(), [])
        self.assertEqual(snapshot(self.agents), before)

    def test_install_refuses_a_missing_required_template(self):
        (self.live / "launchd" / "com.devanshu.outreach-sources.plist.template").unlink()
        before = snapshot(self.agents)
        code, out, err = self.main("install")
        self.assertEqual(code, 1)
        self.assertIn("com.devanshu.outreach-sources", err)
        self.assertIn("#31", err)
        self.assertEqual(self.runner.changes(), [])
        self.assertEqual(snapshot(self.agents), before)
        self.assertFalse(self.logs.exists())

    def test_install_refuses_without_the_built_web_app_or_the_venv(self):
        for missing in (self.live / "web" / "dist" / "index.html", self.live / ".venv" / "bin" / "python"):
            with self.subTest(missing=missing.name):
                saved = missing.read_bytes()
                missing.unlink()
                self.addCleanup(missing.write_bytes, saved)
                before = snapshot(self.agents)
                code, _, err = self.main("install")
                self.assertEqual(code, 1)
                self.assertIn(str(missing.relative_to(self.live)), err)
                self.assertEqual(self.runner.changes(), [])
                self.assertEqual(snapshot(self.agents), before)
                missing.write_bytes(saved)

    def test_install_refuses_a_bad_template_before_writing_anything(self):
        path = self.live / "launchd" / "com.devanshu.outreach-sources.plist.template"
        path.write_text(path.read_text(encoding="utf-8").replace("run-due", "{{COMMAND}}"), encoding="utf-8")
        before = snapshot(self.agents)
        code, _, err = self.main("install")
        self.assertEqual(code, 1)
        self.assertIn("{{COMMAND}}", err)
        self.assertEqual(self.runner.changes(), [])
        self.assertEqual(snapshot(self.agents), before)

    def test_install_keeps_a_differing_plist_as_before_switch(self):
        crawl = self.agents / "com.devanshu.outreach-crawl.plist"
        hand_made = minimal("com.devanshu.outreach-crawl").encode("utf-8")
        crawl.write_bytes(hand_made)
        self.runner.loaded_labels.add("com.devanshu.outreach-crawl")
        code, out, err = self.main("install")
        self.assertEqual(code, 0, err)
        self.assertEqual((self.agents / "com.devanshu.outreach-crawl.plist.before-switch").read_bytes(), hand_made)
        self.assertEqual(crawl.read_bytes(), self.rendered(NEW_JOBS[2]))
        changes = self.runner.changes()
        self.assertLess(changes.index(("bootout", "com.devanshu.outreach-crawl")),
                        changes.index(("bootstrap", crawl.name)))
        self.assertIn("before-switch", out)

    def test_a_second_before_switch_never_overwrites_the_first(self):
        crawl = self.agents / "com.devanshu.outreach-crawl.plist"
        first = self.agents / "com.devanshu.outreach-crawl.plist.before-switch"
        first.write_bytes(b"first")
        crawl.write_bytes(minimal("com.devanshu.outreach-crawl").encode("utf-8"))
        code, _, err = self.main("install")
        self.assertEqual(code, 0, err)
        self.assertEqual(first.read_bytes(), b"first")
        self.assertEqual((self.agents / "com.devanshu.outreach-crawl.plist.before-switch-2").read_bytes(),
                         minimal("com.devanshu.outreach-crawl").encode("utf-8"))

    def test_install_twice_changes_nothing(self):
        self.main("install")
        after_first, calls = snapshot(self.agents), len(self.runner.calls)
        code, out, err = self.main("install")
        self.assertEqual(code, 0, err)
        self.assertEqual(snapshot(self.agents), after_first)
        self.assertNotIn("bootstrap", [name for name, _ in self.runner.calls[calls:]])
        self.assertIn("unchanged, already loaded", out)

    def test_a_lint_failure_stops_before_that_job_loads(self):
        self.runner.fail_lint.add("com.devanshu.outreach-sender")
        code, _, err = self.main("install")
        self.assertEqual(code, 1)
        self.assertIn("invalid", err)
        self.assertNotIn(("bootstrap", "com.devanshu.outreach-sender.plist"), self.runner.changes())
        self.assertNotIn("com.devanshu.outreach-sender", self.runner.loaded_labels)


class RetireOldTest(DeployCase):
    def test_retire_old_boots_out_and_renames_never_deletes(self):
        self.runner.loaded_labels.discard("com.devanshu.outreach-refresh")  # booted out by hand at S3
        before = snapshot(self.agents)
        code, out, err = self.main("retire-old")
        self.assertEqual(code, 0, err)
        for job in OLD_JOBS:
            self.assertFalse((self.agents / job.plist).exists())
            self.assertEqual((self.agents / f"{job.plist}.disabled").read_bytes(), before[job.plist])
            self.assertNotIn(job.label, self.runner.loaded_labels)
        self.assertEqual([c for c in self.runner.changes() if c[0] == "bootout"],
                         [("bootout", job.label) for job in OLD_JOBS if job.label != "com.devanshu.outreach-refresh"])
        after = snapshot(self.agents)
        self.assertEqual(sorted(after.values()), sorted(before.values()))  # every byte is still on disk
        self.assertEqual(after["com.example.unrelated.plist"], before["com.example.unrelated.plist"])
        self.assertIn("com.example.unrelated", self.runner.loaded_labels)
        self.assertEqual(len(out.strip().splitlines()), len(OLD_JOBS))

    def test_retire_old_twice_is_a_no_op(self):
        self.main("retire-old")
        after_first, calls = snapshot(self.agents), len(self.runner.calls)
        code, out, err = self.main("retire-old")
        self.assertEqual(code, 0, err)
        self.assertEqual(snapshot(self.agents), after_first)
        self.assertEqual([c for c in self.runner.calls[calls:] if c[0] != "loaded"], [])
        self.assertIn("already retired", out)

    def test_retire_old_refuses_when_a_plist_and_its_disabled_copy_both_exist(self):
        job = OLD_JOBS[1]
        (self.agents / f"{job.plist}.disabled").write_bytes(b"an older copy")
        before = snapshot(self.agents)
        code, _, err = self.main("retire-old")
        self.assertEqual(code, 1)
        self.assertIn(f"{job.plist}.disabled", err)
        self.assertEqual(self.runner.changes(), [])
        self.assertEqual(snapshot(self.agents), before)

    def test_a_missing_old_plist_is_reported_not_invented(self):
        (self.agents / OLD_JOBS[2].plist).unlink()
        self.runner.loaded_labels.discard(OLD_JOBS[2].label)
        code, out, _ = self.main("retire-old")
        self.assertEqual(code, 0)
        self.assertIn(f"{OLD_JOBS[2].label}: not loaded, no plist found", out)
        self.assertFalse((self.agents / f"{OLD_JOBS[2].plist}.disabled").exists())


class RollbackTest(DeployCase):
    def test_rollback_restores_the_old_jobs_and_disables_the_new(self):
        self.main("install")
        self.main("retire-old")
        code, out, err = self.main("rollback")
        self.assertEqual(code, 0, err)
        for job in REQUIRED:
            self.assertFalse((self.agents / job.plist).exists())
            self.assertEqual((self.agents / f"{job.plist}.disabled").read_bytes(), self.rendered(job))
            self.assertNotIn(job.label, self.runner.loaded_labels)
        for job in OLD_JOBS:
            self.assertTrue((self.agents / job.plist).exists())
            self.assertFalse((self.agents / f"{job.plist}.disabled").exists())
            self.assertIn(job.label, self.runner.loaded_labels)
        self.assertEqual(out.strip().splitlines()[-1], REMINDER)
        # the new sender is booted out before the old one is loaded again
        changes = self.runner.changes()
        self.assertLess(changes.index(("bootout", "com.devanshu.outreach-sender")),
                        changes.index(("bootstrap", "com.devanshu.outreach-mail-sender.plist")))

    def test_full_round_trip_restores_the_starting_folder_byte_for_byte(self):
        start, start_loaded = snapshot(self.agents), set(self.runner.loaded_labels)
        for round_ in (1, 2):  # a second attempt after a rollback must come back the same way
            with self.subTest(round=round_):
                for command in ("install", "retire-old", "rollback"):
                    code, _, err = self.main(command)
                    self.assertEqual(code, 0, f"{command}: {err}")
                final = snapshot(self.agents)
                for name, data in start.items():
                    self.assertEqual(final.get(name), data, name)
                # The only extra files are the new jobs' plists, set aside (never deleted), and launchd ignores them.
                self.assertEqual(set(final) - set(start), {f"{job.plist}.disabled" for job in REQUIRED})
                self.assertEqual({n for n in final if n.endswith(".plist")}, {n for n in start if n.endswith(".plist")})
                self.assertEqual(self.runner.loaded_labels, start_loaded)

    def test_rollback_goes_on_past_a_failure_and_says_so(self):
        self.main("install")
        self.main("retire-old")
        self.runner.fail_bootstrap.add("com.devanshu.outreach-dashboard")
        code, out, err = self.main("rollback")
        self.assertEqual(code, 1)
        self.assertIn("com.devanshu.outreach-dashboard", err)
        for job in OLD_JOBS:
            self.assertTrue((self.agents / job.plist).exists())
            if job.label != "com.devanshu.outreach-dashboard":
                self.assertIn(job.label, self.runner.loaded_labels)
        self.assertIn(REMINDER, out)

    def test_rollback_leaves_the_old_sender_off_until_it_is_paused(self):
        sender = OLD_JOBS[0]
        self.assertEqual(sender.label, "com.devanshu.outreach-mail-sender")
        for config in ({"paused": False}, {}, {"paused": "true"}, None, "not json"):
            with self.subTest(config=config):
                self.main("install")
                self.main("retire-old")
                self.set_old_config(config)
                config_before, calls = snapshot(self.live / "mail"), len(self.runner.calls)
                code, out, err = self.main("rollback")
                self.assertEqual(code, 1)
                self.assertIn("Pause sending in the old dashboard (back on :3001), then run rollback again.", err)
                self.assertFalse((self.agents / sender.plist).exists())
                self.assertTrue((self.agents / f"{sender.plist}.disabled").exists())
                self.assertNotIn(sender.label, self.runner.loaded_labels)
                self.assertNotIn(("bootstrap", sender.plist), self.runner.calls[calls:])
                for job in OLD_JOBS[1:]:
                    self.assertTrue((self.agents / job.plist).exists())
                    self.assertIn(job.label, self.runner.loaded_labels)
                self.assertEqual(snapshot(self.live / "mail"), config_before)  # read, never written
                # He pauses in the old dashboard, then runs rollback again.
                self.set_old_config({"paused": True})
                code, _, err = self.main("rollback")
                self.assertEqual(code, 0, err)
                self.assertTrue((self.agents / sender.plist).exists())
                self.assertIn(sender.label, self.runner.loaded_labels)

    def test_rollback_never_loads_the_old_sender_while_the_new_one_is_loaded(self):
        self.main("install")
        self.main("retire-old")
        self.runner.fail_bootout.add("com.devanshu.outreach-sender")
        calls = len(self.runner.calls)
        code, out, err = self.main("rollback")
        self.assertEqual(code, 1)
        self.assertIn("com.devanshu.outreach-sender is still loaded", err)
        self.assertIn("com.devanshu.outreach-sender", self.runner.loaded_labels)
        self.assertNotIn("com.devanshu.outreach-mail-sender", self.runner.loaded_labels)
        self.assertNotIn(("bootstrap", "com.devanshu.outreach-mail-sender.plist"), self.runner.calls[calls:])
        self.assertTrue((self.agents / "com.devanshu.outreach-mail-sender.plist.disabled").exists())
        for job in OLD_JOBS[1:]:
            self.assertIn(job.label, self.runner.loaded_labels)

    def test_rollback_before_any_install_only_reloads_what_is_not_loaded(self):
        self.runner.loaded_labels.discard("com.devanshu.outreach-mail-server")
        code, out, err = self.main("rollback")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.runner.changes(), [("bootstrap", "com.devanshu.outreach-mail-server.plist")])


class ReadOnlyCommandsTest(DeployCase):
    def test_plan_writes_nothing(self):
        before_agents, before_live = snapshot(self.agents), snapshot(self.live)
        code, out, err = self.main("plan", root=self.elsewhere())  # runs anywhere
        self.assertEqual(code, 0, err)
        self.assertEqual(snapshot(self.agents), before_agents)
        self.assertEqual(snapshot(self.live), before_live)
        self.assertFalse(self.logs.exists())
        self.assertEqual(self.runner.changes(), [])
        for job in NEW_JOBS + OLD_JOBS:
            self.assertIn(job.label, out)
        self.assertIn("com.devanshu.outreach-api [required, #11]", out)
        self.assertIn("com.devanshu.outreach-needs-you [optional, #16]", out)
        self.assertIn("template missing", out)
        self.assertIn(str(self.agents / "com.devanshu.outreach-mail-sender.plist"), out)

    def test_plan_says_what_install_would_refuse(self):
        (self.live / "web" / "dist" / "index.html").unlink()
        code, out, _ = self.main("plan")
        self.assertEqual(code, 0)
        self.assertIn("Install would refuse", out)
        self.assertIn("web/dist/index.html", out)

    def test_status_shows_each_label_and_writes_nothing(self):
        self.main("install")
        self.main("retire-old")
        before = snapshot(self.agents)
        calls = len(self.runner.calls)
        code, out, err = self.main("status", root=self.elsewhere())
        self.assertEqual(code, 0, err)
        self.assertEqual(snapshot(self.agents), before)
        self.assertEqual([c for c in self.runner.calls[calls:] if c[0] != "loaded"], [])
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), len(NEW_JOBS) + len(OLD_JOBS))
        self.assertIn("com.devanshu.outreach-api: loaded, plist present", lines)
        self.assertIn("com.devanshu.outreach-plan: not loaded, plist missing", lines)
        self.assertIn("com.devanshu.outreach-mail-sender: not loaded, plist .disabled", lines)


if __name__ == "__main__":
    unittest.main()
