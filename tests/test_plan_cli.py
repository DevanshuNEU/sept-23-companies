"""Task 11: the plan commands and the 08:00 launchd template (#15). Scratch stores, fake hooks, nothing is loaded."""
import contextlib
import io
import os
import plistlib
import re
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from outreach.plan import __main__ as cli
from tests.plan_seed import seed_person, seed_scored_company
from tests.storetest import fresh_store

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "launchd" / "com.devanshu.outreach-plan.plist.template"
DAY = "2026-11-10"
TEAM = ("team", "https://acme.example/team", "an engineer on the team")


class CliBase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        path = self.conn.execute("PRAGMA database_list").fetchone()["file"]
        patch = mock.patch.dict(os.environ, {"OUTREACH_DB": path})
        patch.start()
        self.addCleanup(patch.stop)

    def world(self):
        ids = [seed_scored_company(self.conn, f"Co{i:02}", 90 - i, ["level_role"]) for i in range(4)]
        seed_person(self.conn, ids[0], "Ada Lovelace", "engineer", sources=[TEAM])
        return ids

    def run_main(self, *argv, **kw):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            code = cli.main(list(argv), stdout=out, **kw)
        return code, out.getvalue(), err.getvalue()

    def count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class BuildCommandTest(CliBase):
    def test_build_refuses_outside_the_live_checkout_when_reading_boards(self):
        elsewhere = tempfile.mkdtemp(prefix="outreach-plan-cli-")
        self.addCleanup(shutil.rmtree, elsewhere, True)
        with mock.patch("sqlite3.connect") as opened, mock.patch.object(cli, "crawler_boards_reader") as hook:
            with self.assertRaises(SystemExit) as caught:
                cli.main(["build", "--date", DAY], root=str(ROOT), live_root=elsewhere)
        self.assertIn("the daily plan build", str(caught.exception.code))
        opened.assert_not_called()
        hook.assert_not_called()
        self.assertEqual(self.count("daily_list"), 0)

    def test_build_without_board_reads_runs_on_a_scratch_store(self):
        self.world()
        code, out, err = self.run_main("build", "--date", DAY, "--no-read-boards", root=str(ROOT),
                                       live_root="/nowhere")
        self.assertEqual((code, err), (0, ""))
        self.assertIn(f"Plan {DAY}: built", out)
        self.assertIn("boards skipped", out)
        # Company first (plan.propose_people false, shipped): Ada is on file, but Co00 is a company row too.
        self.assertIn("Co00 | find someone | linkedin_request | level_role", out)
        self.assertNotIn("Ada Lovelace", out)
        self.assertIn("capacity 8 (companies 8, email 0)", out)  # the plan writes no email rows
        self.assertEqual(self.count("daily_list_entry"), 4)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM daily_list_entry WHERE person_id IS NOT NULL "
                                           "OR channel = 'email'").fetchone()[0], 0)
        code, out, _ = self.run_main("build", "--date", DAY, "--no-read-boards")
        self.assertEqual(code, 0)
        self.assertIn("already_built", out)
        self.assertEqual(self.count("daily_list_entry"), 4)

    def test_build_in_the_live_checkout_asks_the_crawler_and_the_ranker(self):
        ids = self.world()
        seen = []
        with mock.patch.object(cli, "crawler_boards_reader", return_value=lambda c: seen.append(list(c))), \
                mock.patch.object(cli, "rank_runner", return_value=lambda conn: seen.append("rank")), \
                mock.patch.object(cli, "_start_caffeinate") as awake:
            code, out, _ = self.run_main("build", "--date", DAY, root=str(ROOT), live_root=str(ROOT))
        self.assertEqual(code, 0)
        self.assertEqual(seen, [ids, "rank"])
        self.assertIn("boards read", out)
        awake.assert_called_once()

    def test_dry_run_prints_and_writes_nothing(self):
        self.world()
        jobs, events = self.count("job_run"), self.count("event")
        code, out, _ = self.run_main("build", "--date", DAY, "--no-read-boards", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("dry run, nothing written", out)
        self.assertIn("Co00 | find someone", out)
        self.assertEqual((self.count("daily_list"), self.count("daily_list_entry"), self.count("job_run"),
                          self.count("event")), (0, 0, jobs, events))

    def test_a_weekend_builds_nothing_unless_forced(self):
        self.world()
        code, out, _ = self.run_main("build", "--date", "2026-11-14", "--no-read-boards")
        self.assertEqual((code, self.count("daily_list")), (0, 0))
        self.assertIn("weekend", out)
        code, out, _ = self.run_main("build", "--date", "2026-11-14", "--no-read-boards", "--force-weekend")
        self.assertEqual(code, 0)
        self.assertEqual(self.count("daily_list"), 1)

    def test_a_missing_store_is_exit_one_with_the_reason(self):
        missing = str(Path(os.environ["OUTREACH_DB"]).parent / "nothing-here.db")
        with mock.patch.dict(os.environ, {"OUTREACH_DB": missing}):
            code, out, err = self.run_main("build", "--date", DAY, "--no-read-boards")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no store", err)


class ShowCommandTest(CliBase):
    def test_show_prints_find_someone_with_the_search_url(self):
        self.world()
        self.run_main("build", "--date", DAY, "--no-read-boards")
        code, out, _ = self.run_main("show", "--date", DAY)
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith(f"Plan {DAY}: capacity "))
        self.assertIn(" 1 Co00 | find someone | linkedin_request | level_role", lines[1])  # company first
        find = [i for i, line in enumerate(lines) if "| find someone |" in line]
        self.assertEqual(len(find), 4)
        for i in find:
            self.assertTrue(lines[i + 1].strip().startswith(
                "search: https://www.linkedin.com/search/results/people/?keywords=Co"))

    def test_show_with_no_plan_says_so_and_writes_nothing(self):
        code, out, _ = self.run_main("show", "--date", DAY)
        self.assertEqual(code, 0)
        self.assertIn("none built", out)
        self.assertEqual(self.count("daily_list"), 0)


class ReplaceCommandTest(CliBase):
    """`build --replace`: today's untouched plan rebuilt with the current config. `_today` is pinned to DAY."""

    def setUp(self):
        super().setUp()
        patch = mock.patch.object(cli, "_today", return_value=date.fromisoformat(DAY))
        patch.start()
        self.addCleanup(patch.stop)

    def old_plan(self):
        from outreach.plan.build import build_plan
        from tests.plan_seed import PEOPLE_ON
        self.world()
        build_plan(self.conn, today=date.fromisoformat(DAY), cfg=PEOPLE_ON)
        rows = self.conn.execute("SELECT * FROM daily_list_entry ORDER BY rank").fetchall()
        self.assertEqual(rows[0]["person_id"] is not None, True)  # Ada's person row, the old config
        return rows

    def test_replace_deletes_the_old_rows_and_prints_the_new_list(self):
        old = self.old_plan()
        code, out, err = self.run_main("build", "--replace", "--no-read-boards", "--reason", "company rows now")
        self.assertEqual((code, err), (0, ""))
        self.assertIn(f"Replaced {len(old)} rows of the plan for {DAY}", out)
        self.assertIn(" 1 Co00 | Ada Lovelace | linkedin_request | level_role | proposed", out)  # an old row
        self.assertIn(f"Plan {DAY}: built", out)
        self.assertIn(" 1 Co00 | find someone | linkedin_request | level_role", out)  # the new one
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM daily_list_entry WHERE person_id IS NOT NULL"
                                           ).fetchone()[0], 0)
        detail = self.conn.execute("SELECT detail FROM event WHERE kind = 'plan_replaced'").fetchone()[0]
        self.assertIn("company rows now", detail)

    def test_dry_run_replace_prints_what_would_go_and_writes_nothing(self):
        old = self.old_plan()
        tables = ("daily_list", "daily_list_entry", "job_run", "event")
        before = [self.count(t) for t in tables]
        code, out, err = self.run_main("build", "--replace", "--dry-run", "--no-read-boards")
        self.assertEqual((code, err), (0, ""))
        self.assertIn(f"Would replace {len(old)} rows of the plan for {DAY}", out)
        self.assertIn("dry run, nothing written", out)
        self.assertIn(" 1 Co00 | find someone | linkedin_request | level_role", out)
        self.assertEqual([self.count(t) for t in tables], before)
        self.assertEqual(self.conn.execute("SELECT person_id FROM daily_list_entry WHERE rank = 1").fetchone()[0],
                         old[0]["person_id"])

    def test_a_touched_plan_is_refused_with_exit_one_and_nothing_written(self):
        self.old_plan()
        self.conn.execute("UPDATE daily_list_entry SET state = 'backlog', decided_at = '2026-11-10T15:00:00Z', "
                          "revision = 2 WHERE rank = 1")
        tables = ("daily_list", "daily_list_entry", "job_run", "event")
        before = [self.count(t) for t in tables]
        code, out, err = self.run_main("build", "--replace", "--no-read-boards")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("was not replaced", err)
        self.assertIn("decision by you", err)
        self.assertEqual([self.count(t) for t in tables], before)

    def test_replace_is_for_today_only(self):
        self.old_plan()
        tables = ("daily_list", "daily_list_entry", "job_run", "event")
        before = [self.count(t) for t in tables]
        code, out, err = self.run_main("build", "--replace", "--no-read-boards", "--date", "2026-11-09")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("only today's plan", err)
        self.assertEqual([self.count(t) for t in tables], before)
        code, _, _ = self.run_main("build", "--replace", "--no-read-boards", "--date", DAY)
        self.assertEqual(code, 0)

    def test_replace_with_no_plan_yet_builds_and_says_so(self):
        self.world()
        code, out, _ = self.run_main("build", "--replace", "--no-read-boards")
        self.assertEqual(code, 0)
        self.assertIn(f"No plan for {DAY} to replace", out)
        self.assertIn(f"Plan {DAY}: built", out)

    def test_reason_needs_replace(self):
        self.world()
        code, _, err = self.run_main("build", "--no-read-boards", "--reason", "why")
        self.assertEqual(code, 1)
        self.assertIn("--reason", err)
        self.assertEqual(self.count("daily_list"), 0)


class TemplateTest(unittest.TestCase):
    def text(self):
        return TEMPLATE.read_text(encoding="utf-8")

    def test_plist_template_is_complete(self):
        text = self.text()
        self.assertEqual(set(re.findall(r"\{\{[^{}]*\}\}", text)), {"{{LIVE_CHECKOUT}}", "{{LOG_PATH}}"})
        plist = plistlib.loads(text.replace("{{LIVE_CHECKOUT}}", "/live/checkout")
                               .replace("{{LOG_PATH}}", "/logs/plan.log").encode("utf-8"))
        self.assertEqual(plist["Label"], "com.devanshu.outreach-plan")
        self.assertEqual(plist["WorkingDirectory"], "/live/checkout")
        self.assertEqual(plist["ProgramArguments"],
                         ["/live/checkout/.venv/bin/python", "-m", "outreach.plan", "build"])
        self.assertEqual((plist["StandardOutPath"], plist["StandardErrorPath"]), ("/logs/plan.log", "/logs/plan.log"))
        self.assertEqual(sorted((d["Weekday"], d["Hour"], d["Minute"]) for d in plist["StartCalendarInterval"]),
                         [(day, 8, 0) for day in range(1, 6)])
        self.assertNotIn("StartInterval", plist)
        self.assertNotIn("@", text)
        for word in ("OUTREACH_DB", "EnvironmentVariables", "token", "secret", "password"):
            self.assertNotIn(word, text)
        self.assertEqual(sum(1 for c in text if c in (chr(0x2013), chr(0x2014))), 0)
        self.assertIn("launchctl bootstrap gui/$(id -u)", text)
        self.assertIn("fires on wake", text)

    def test_the_template_renders_with_the_deploy_tool(self):
        from outreach.deploy import jobs
        label = "com.devanshu.outreach-plan"
        rendered = jobs.render(self.text(), live_root="/tmp/live checkout", log_path="/tmp/logs/plan.log", label=label)
        self.assertEqual(plistlib.loads(rendered.encode("utf-8"))["ProgramArguments"][0],
                         "/tmp/live checkout/.venv/bin/python")
        job = next(j for j in jobs.NEW_JOBS if j.label == label)
        self.assertEqual(job.template, "launchd/com.devanshu.outreach-plan.plist.template")
        self.assertFalse(job.required)


if __name__ == "__main__":
    unittest.main()
