"""Task 14: the morning prepare step (#15). A fake Runner answers from a script over a scratch store: no real
`claude -p`, no model call, no network, no live path, and the launchd template is only rendered, never loaded."""
import contextlib
import dataclasses
import io
import json
import os
import plistlib
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from outreach.deploy.jobs import PLACEHOLDERS, render
from outreach.drafts.checks import load_banned
from outreach.plan import __main__ as cli
from outreach.plan import prepare as prepare_module
from outreach.plan.build import build_plan
from outreach.plan.claude_run import LINKEDIN_DENY, PREPARE_TOOLS, claude_command, parse_prepare_reply, prepare_version
from outreach.plan.config import default_plan_config
from outreach.plan.notecheck import load_unconfirmed
from outreach.plan.prepare import PROMPT_PATH, PrepareSummary, render_prompt, run_prepare, strip_ask_items
from outreach.reader.night import RunResult
from tests.plan_seed import TODAY, seed_scored_company
from tests.storetest import fresh_store

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures" / "plan"
TEMPLATE = ROOT / "launchd" / "com.devanshu.outreach-prepare.plist.template"
CFG = default_plan_config()
# The shipped config has the step switched off (prepare.enabled false, the company-first ruling in #9, 2026-10-26);
# the deny rule was checked on his seat on 2026-10-25 (prepare.deny_rule_checked true). The tests of the step itself
# turn it on (ON); OFF is on but with the deny rule unchecked, so that gate is still tested on its own.
ON = dataclasses.replace(CFG, prepare={**CFG.prepare, "enabled": True, "deny_rule_checked": True})
OFF = dataclasses.replace(CFG, prepare={**CFG.prepare, "enabled": True, "deny_rule_checked": False})
BANNED, _ = load_banned(FIX / "banned_phrases.txt", scopes=("both", "dm"))
UNCONFIRMED = load_unconfirmed(FIX / "me.md")
ME_TEXT = (FIX / "me.md").read_text(encoding="utf-8")
REASON = "I read your post on the queue rewrite"
DASHES = (chr(0x2013), chr(0x2014))


def envelope(text, *, is_error=False):
    return json.dumps({"type": "result", "subtype": "success", "is_error": is_error, "result": text})


def ok(text, code=0):
    return RunResult(code, envelope(text), "")


def reply_for(message, *, quote_extra=""):
    """A valid reply for the company in a user message: one engineer named on the company's team page."""
    company = json.loads(message)["company"]
    name = f"Ada Lovelace{company['company_id']}"
    cand = {"name": name, "title": "Software Engineer", "role_kind": "engineer", "linkedin_url": None, "email": None,
            "sources": [{"kind": "team", "url": f"https://co{company['company_id']}.example/team", "label": "Team",
                         "quote": f"{name}, Software Engineer at {company['name']}.{quote_extra}"}],
            "note": {"body": f"Hi Ada, {REASON} and built a similar job runner.", "reason_line": REASON,
                     "facts": [{"text": "queue rewrite", "label": "Blog", "url": "https://co.example/blog",
                                "in_note": True}]}}
    return json.dumps({"results": [{"company_id": company["company_id"], "candidates": [cand], "none_reason": ""}]})


class Script:
    """A fake Runner: each call takes the next step. A step is a callable of the user message giving a RunResult
    (or raising), and the last step repeats."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = []

    def __call__(self, system_prompt, user_message, model):
        self.calls.append((system_prompt, user_message, model))
        step = self.steps[min(len(self.calls), len(self.steps)) - 1]
        return step(user_message)


def good(message):
    return ok(reply_for(message))


class PrepareCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.ids = [seed_scored_company(self.conn, f"Co{i:02}", 90 - i, ["level_role"]) for i in range(14)]

    def run_prepare(self, runner, **kw):
        kw.setdefault("count", 14)
        kw.setdefault("cfg", ON)
        return run_prepare(self.conn, runner, today=TODAY, me_text=ME_TEXT, banned=BANNED, unconfirmed=UNCONFIRMED,
                           **kw)

    def jobs(self):
        return self.conn.execute("SELECT status, summary, error FROM job_run WHERE job = 'prepare'").fetchall()

    def attempts(self):
        return [r[0] for r in self.conn.execute(
            "SELECT company_id FROM event WHERE kind = 'prepare_attempt' ORDER BY event_id")]

    def count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class RunTest(PrepareCase):
    def test_prepare_off_does_nothing(self):
        # Company first: the shipped config switches the step off, whatever the deny rule says.
        self.assertIs(CFG.get("prepare", "enabled"), False)
        for cfg in (CFG, dataclasses.replace(CFG, prepare={**CFG.prepare, "deny_rule_checked": False})):
            runner = Script(good)
            events = self.count("event")
            with mock.patch.object(prepare_module, "start_job") as started, \
                    mock.patch.object(prepare_module, "select_prepare_targets") as targets:
                summary = self.run_prepare(runner, cfg=cfg)
            self.assertEqual(summary, PrepareSummary("off", 0, 0, 0, 0, 0))
            self.assertEqual(runner.calls, [])
            started.assert_not_called()
            targets.assert_not_called()
            self.assertEqual((self.jobs(), self.count("event"), self.count("person"), self.count("linkedin_draft")),
                             ([], events, 0, 0))
            self.assertTrue(summary.line().startswith("prepare is off"), summary.line())

    def test_prepare_does_nothing_until_the_deny_rule_is_checked(self):
        self.assertIs(CFG.get("prepare", "deny_rule_checked"), True)  # checked on his seat on 2026-10-25
        runner = Script(good)
        events = self.count("event")
        summary = self.run_prepare(runner, cfg=OFF)
        self.assertEqual(summary, PrepareSummary("deny_rule_unchecked", 0, 0, 0, 0, 0))
        self.assertEqual(runner.calls, [])
        self.assertEqual((self.jobs(), self.count("event"), self.count("person")), ([], events, 0))
        self.assertIn("deny_rule_checked", summary.line())

    def test_prepare_stores_candidates_and_drafts_and_records_a_job(self):
        runner = Script(good)
        summary = self.run_prepare(runner)
        self.assertEqual(summary, PrepareSummary("done", 14, 14, 14, 0, 0))
        self.assertEqual(len(runner.calls), 14)
        self.assertEqual((self.count("person"), self.count("linkedin_draft")), (14, 14))
        self.assertEqual(self.attempts(), self.ids)
        (job,) = self.jobs()
        self.assertEqual(job["status"], "ok")
        detail = json.loads(job["summary"])
        self.assertEqual((detail["status"], detail["companies"]), ("done", 14))
        self.assertTrue(detail["version"].startswith("pv-"))
        versions = {r[0] for r in self.conn.execute("SELECT prepared_by_version FROM linkedin_draft")}
        self.assertEqual(versions, {detail["version"]})
        system_prompt, _, model = runner.calls[0]
        self.assertEqual(model, CFG.get("prepare", "model"))
        self.assertEqual(system_prompt, render_prompt(CFG))

    def test_count_defaults_to_twice_capacity_and_zero_capacity_does_nothing(self):
        runner = Script(good)
        with mock.patch.object(prepare_module, "linkedin_capacity", return_value=SimpleNamespace(today=3)):
            summary = self.run_prepare(runner, count=None)
        self.assertEqual((summary.status, summary.companies, len(runner.calls)), ("done", 6, 6))
        runner = Script(good)
        with mock.patch.object(prepare_module, "linkedin_capacity", return_value=SimpleNamespace(today=0)):
            summary = self.run_prepare(runner, count=None)
        self.assertEqual(summary, PrepareSummary("no_capacity", 0, 0, 0, 0, 0))
        self.assertEqual(runner.calls, [])
        self.assertEqual([j["status"] for j in self.jobs()], ["ok", "ok"])

    def test_no_targets_calls_nobody(self):
        runner = Script(good)
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'test'")
        summary = self.run_prepare(runner)
        self.assertEqual((summary.status, runner.calls), ("no_targets", []))

    def test_session_limit_stops_cleanly_without_attempts(self):
        limit = lambda m: RunResult(1, "", "Claude usage limit reached. Your limit resets at 9am.")  # noqa: E731
        runner = Script(good, good, good, limit)
        summary = self.run_prepare(runner)
        self.assertEqual((summary.status, summary.companies, len(runner.calls)), ("session_limit", 3, 4))
        self.assertEqual(self.attempts(), self.ids[:3])  # the earlier companies are kept; nothing after
        self.assertEqual(self.count("person"), 3)
        (job,) = self.jobs()
        self.assertEqual(job["status"], "partial")
        self.assertIn("usage limit", job["error"].lower())

    def test_auth_error_fails_the_job(self):
        runner = Script(lambda m: RunResult(1, envelope("Not logged in. Please run /login", is_error=True), ""))
        summary = self.run_prepare(runner)
        self.assertEqual((summary.status, len(runner.calls)), ("auth", 1))
        self.assertEqual((self.attempts(), self.count("person")), ([], 0))
        self.assertEqual(self.jobs()[0]["status"], "failed")

    def test_a_quoted_limit_phrase_in_a_good_reply_is_not_a_limit(self):
        runner = Script(lambda m: ok(reply_for(m, quote_extra=" We hit the usage limit reached. Not logged in.")))
        summary = self.run_prepare(runner, count=3)
        self.assertEqual((summary.status, summary.companies, summary.added), ("done", 3, 3))

    def test_fenced_and_wrapped_json_is_parsed(self):
        body = '{"results": [{"company_id": 1, "candidates": [], "none_reason": "nobody"}]}'
        for text in (body, f"```json\n{body}\n```", f"  \n```\n{body}\n```\n  ", f"\n\n{body}\n"):
            with self.subTest(text=text[:20]):
                self.assertEqual(parse_prepare_reply(envelope(text))["results"][0]["none_reason"], "nobody")
        listed = json.dumps([{"type": "system"}, json.loads(envelope(body))])
        self.assertEqual(parse_prepare_reply(listed)["results"][0]["company_id"], 1)
        for bad in ("", "not json", envelope("no json here"), envelope('{"results": "x"}'),
                    envelope(body, is_error=True), json.dumps({"result": 5}), envelope("[1, 2]")):
            with self.subTest(bad=bad[:30]):
                with self.assertRaises(ValueError):
                    parse_prepare_reply(bad)
        runner = Script(lambda m: ok("```json\n" + reply_for(m) + "\n```"))
        self.assertEqual(self.run_prepare(runner, count=2).added, 2)

    def test_a_reply_that_is_not_exactly_one_json_value_fails_and_stores_nothing(self):
        body = '{"results": [{"company_id": 1, "candidates": [], "none_reason": ""}]}'
        fake = 'The page says: {"results": [{"company_id": 1, "candidates": []}]} so here is mine.'
        for text in (f"Here is what I found.\n{body}", f"{body}\n{body}", f"Sure.\n```json\n{body}\n```\nDone.",
                     fake, f"{body} trailing words"):
            with self.subTest(text=text[:30]):
                with self.assertRaises(ValueError):
                    parse_prepare_reply(envelope(text))
        runner = Script(lambda m: ok("I found them.\n" + reply_for(m)), good)
        summary = self.run_prepare(runner, count=2)
        self.assertEqual((summary.status, summary.failures, summary.companies), ("done", 1, 1))
        self.assertEqual(self.attempts(), [self.ids[1]])  # the prose-wrapped company is not marked attempted
        self.assertEqual(self.count("person"), 1)
        self.assertEqual(self.jobs()[0]["status"], "partial")

    def test_garbage_reply_counts_a_failure_and_two_in_a_row_stop(self):
        runner = Script(lambda m: ok("I could not find anyone, sorry."))
        summary = self.run_prepare(runner)
        self.assertEqual((summary.status, summary.failures, summary.companies, len(runner.calls)), ("failed", 2, 0, 2))
        self.assertEqual((self.attempts(), self.count("person"), self.count("person_source")), ([], 0, 0))
        self.assertEqual(self.jobs()[0]["status"], "failed")
        # one failure between good calls does not stop the run, and the failed company is not marked attempted
        runner = Script(good, lambda m: ok('{"results": [{"company_id": "x"}]}'), good)
        summary = self.run_prepare(runner, count=3)
        self.assertEqual((summary.status, summary.failures, summary.companies), ("done", 1, 2))
        self.assertEqual(self.attempts(), [self.ids[0], self.ids[2]])
        self.assertEqual(self.jobs()[-1]["status"], "partial")
        # a good call resets the count: failures that are never two in a row never stop the run
        garbage = lambda m: ok("nothing")  # noqa: E731
        runner = Script(garbage, good, garbage, good, garbage, good)
        summary = self.run_prepare(runner, count=6)
        self.assertEqual((summary.status, summary.failures, len(runner.calls)), ("done", 3, 6))

    def test_a_reply_that_breaks_validation_is_a_failure_with_nothing_written(self):
        def broken(message):
            cid = json.loads(message)["company"]["company_id"]
            return ok(json.dumps({"results": [{"company_id": cid, "candidates": "Ada"}]}))
        summary = self.run_prepare(Script(broken))
        self.assertEqual((summary.status, summary.failures), ("failed", 2))
        self.assertEqual((self.attempts(), self.count("person")), ([], 0))

    def test_runner_timeout_is_a_failed_call(self):
        def slow(message):
            raise subprocess.TimeoutExpired(["claude"], 600)
        runner = Script(slow, good)
        summary = self.run_prepare(runner, count=2)
        self.assertEqual((summary.status, summary.failures, summary.companies), ("done", 1, 1))
        self.assertEqual(self.attempts(), [self.ids[1]])
        summary = self.run_prepare(Script(slow))
        self.assertEqual((summary.status, summary.failures), ("failed", 2))

    def test_a_missing_claude_binary_is_a_failed_call_and_the_job_closes(self):
        def missing(message):
            raise FileNotFoundError("claude")
        summary = self.run_prepare(Script(missing))
        self.assertEqual(summary.status, "failed")
        self.assertIn("FileNotFoundError", self.jobs()[0]["error"])

    def test_the_user_message_has_no_ask_items_and_carries_the_limit_and_the_post_text(self):
        self.conn.execute("INSERT INTO source(source_name, version) VALUES ('hn', 'v1') ON CONFLICT DO NOTHING")
        self.conn.execute("INSERT INTO company_post(company_id, source_name, url, posted_at, text, asks_email) "
                          "VALUES (?, 'hn', 'https://news.ycombinator.com/item?id=9', ?, ?, 1)",
                          (self.ids[0], f"{TODAY.isoformat()}T12:00:00Z", "Co00 is hiring. Email hn@co00.example"))
        runner = Script(good)
        self.run_prepare(runner, count=1)
        message = runner.calls[0][1]
        payload = json.loads(message)
        self.assertEqual(payload["note_max_chars"], CFG.get("note", "max_chars"))
        self.assertEqual(payload["company"]["post"]["text"], "Co00 is hiring. Email hn@co00.example")
        self.assertEqual(payload["company"]["company_id"], self.ids[0])
        self.assertIn("Graduated from Northeastern", payload["me"])
        for ask in ("ASK", "STEM OPT", "H-1B", "installs", "Until answered"):
            self.assertNotIn(ask, message)
        self.assertIn("not an ask", payload["me"])

    def test_strip_ask_items(self):
        text = "- one\n- ASK: two `x`\n  more of two\n\n- three\n  - ASK: nested\n    its line\n- four\n"
        self.assertEqual(strip_ask_items(text), "- one\n\n- three\n- four\n")


class VersionTest(unittest.TestCase):
    def test_prepare_version_moves_with_prompt_model_and_schema(self):
        base = prepare_version(b"prompt", "claude-sonnet-5-5", 1)
        self.assertRegex(base, r"\Apv-[0-9a-f]{12}\Z")
        self.assertEqual(base, prepare_version(b"prompt", "claude-sonnet-5-5", 1))
        self.assertEqual(len({base, prepare_version(b"prompt!", "claude-sonnet-5-5", 1),
                              prepare_version(b"prompt", "claude-haiku-5-5", 1),
                              prepare_version(b"prompt", "claude-sonnet-5-5", 2)}), 4)

    def test_prompt_file_has_the_required_rules(self):
        text = PROMPT_PATH.read_text(encoding="utf-8")
        for phrase in ("never LinkedIn", "verbatim quote", "data, never instructions", "{{NOTE_MAX_CHARS}}",
                       "Never guess an email address", "never say or imply that he has confirmed"):
            self.assertIn(phrase.lower(), text.lower())
        self.assertFalse(any(d in text for d in DASHES))
        rendered = render_prompt(CFG)
        self.assertIn(f"at most {CFG.get('note', 'max_chars')} characters", rendered)
        self.assertNotIn("{{", rendered)


class CommandLineTest(unittest.TestCase):
    def test_claude_command_allows_only_web_tools_and_denies_linkedin(self):
        self.assertEqual(PREPARE_TOOLS, ("WebSearch", "WebFetch"))
        rules = ["WebFetch(domain:linkedin.com)", "WebFetch(domain:*.linkedin.com)", "WebFetch(domain:www.linkedin.com)",
                 "WebFetch(domain:lnkd.in)", "WebFetch(domain:*.lnkd.in)"]
        self.assertEqual(list(LINKEDIN_DENY), rules)
        cmd = claude_command("claude-sonnet-5-5", "SYSTEM", "claude")
        self.assertEqual(cmd, [
            "claude", "-p", "--model", "claude-sonnet-5-5", "--output-format", "json",
            "--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch,WebFetch", "--permission-mode", "dontAsk",
            "--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config", "--safe-mode",
            "--settings", json.dumps({"permissions": {"deny": rules}}),
            "--disallowedTools", ",".join(rules),
            "--system-prompt", "SYSTEM"])
        self.assertNotIn("Bash", " ".join(cmd))


class CliTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        path = self.conn.execute("PRAGMA database_list").fetchone()["file"]
        mail = tempfile.mkdtemp(prefix="outreach-prepare-mail-")
        self.addCleanup(shutil.rmtree, mail, True)
        shutil.copy(FIX / "me.md", Path(mail) / "me.md")
        patch = mock.patch.dict(os.environ, {"OUTREACH_DB": path, "OUTREACH_MAIL_HOME": mail})
        patch.start()
        self.addCleanup(patch.stop)

    def test_prepare_refuses_outside_the_live_checkout(self):
        elsewhere = tempfile.mkdtemp(prefix="outreach-prepare-cli-")
        self.addCleanup(shutil.rmtree, elsewhere, True)
        with mock.patch("sqlite3.connect") as opened, mock.patch.object(cli, "claude_runner") as runner, \
                mock.patch.object(cli, "_start_caffeinate") as awake:
            with self.assertRaises(SystemExit) as caught:
                cli.main(["prepare"], root=str(ROOT), live_root=elsewhere)
        self.assertIn("the daily prepare step", str(caught.exception.code))
        opened.assert_not_called()
        runner.assert_not_called()
        awake.assert_not_called()

    def test_prepare_command_in_the_live_checkout_runs_the_given_runner(self):
        seed_scored_company(self.conn, "Co00", 90, ["level_role"])
        runner = Script(good)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "claude_runner", return_value=runner) as made, \
                mock.patch.object(cli, "default_plan_config", return_value=ON), \
                mock.patch.object(cli, "_start_caffeinate"), mock.patch.object(cli, "_today", return_value=TODAY), \
                mock.patch.object(cli, "load_banned", return_value=(BANNED, True)), contextlib.redirect_stderr(err):
            code = cli.main(["prepare", "--count", "1"], root=str(ROOT), live_root=str(ROOT), stdout=out)
        self.assertEqual(code, 0)
        # stderr may hold a ResourceWarning from another test's connection, collected now: only our own line counts
        self.assertNotIn("prepare:", err.getvalue())
        made.assert_called_once()
        self.assertEqual(len(runner.calls), 1)
        self.assertIn("prepare: done, 1 companies, 1 added, 1 drafts", out.getvalue())

    def test_prepare_command_exits_1_when_me_md_is_missing(self):
        os.remove(Path(os.environ["OUTREACH_MAIL_HOME"]) / "me.md")
        err = io.StringIO()
        with mock.patch.object(cli, "claude_runner") as made, mock.patch.object(cli, "_start_caffeinate"), \
                mock.patch.object(cli, "default_plan_config", return_value=ON), contextlib.redirect_stderr(err):
            code = cli.main(["prepare"], root=str(ROOT), live_root=str(ROOT), stdout=io.StringIO())
        self.assertEqual(code, 1)
        self.assertIn("me.md", err.getvalue())
        made.assert_not_called()


    def test_prepare_command_with_the_shipped_config_is_off_and_never_calls_claude(self):
        # The 05:30 job (`python -m outreach.plan prepare`) with the config as shipped: one plain line, exit 0, and no
        # runner, no claude command line, no store, no caffeinate.
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "claude_runner") as made, mock.patch.object(cli, "_start_caffeinate") as awake, \
                mock.patch("outreach.plan.claude_run.claude_command") as command, \
                mock.patch("subprocess.run") as ran, mock.patch.object(cli, "run_prepare") as runs, \
                mock.patch("sqlite3.connect") as opened, contextlib.redirect_stderr(err):
            code = cli.main(["prepare"], root=str(ROOT), live_root=str(ROOT), stdout=out)
        self.assertEqual(code, 0)
        self.assertNotIn("prepare", err.getvalue())
        self.assertTrue(out.getvalue().startswith("prepare is off"), out.getvalue())
        self.assertEqual(out.getvalue().count("\n"), 1)
        for fake in (made, awake, command, ran, runs, opened):
            fake.assert_not_called()

    def test_prepare_command_is_off_until_the_deny_rule_is_checked(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "claude_runner") as made, mock.patch.object(cli, "_start_caffeinate") as awake, \
                mock.patch.object(cli, "default_plan_config", return_value=OFF), \
                mock.patch("sqlite3.connect") as opened, contextlib.redirect_stderr(err):
            code = cli.main(["prepare"], root=str(ROOT), live_root=str(ROOT), stdout=out)
        self.assertEqual(code, 0)
        # stderr may hold a ResourceWarning from another test's connection, collected now: only our own line counts
        self.assertNotIn("prepare:", err.getvalue())
        self.assertIn("prepare: off", out.getvalue())
        self.assertIn("deny_rule_checked", out.getvalue())
        made.assert_not_called()
        awake.assert_not_called()
        opened.assert_not_called()


class AfterAFailedPrepareTest(PrepareCase):
    def test_plan_still_builds_after_a_failed_prepare(self):
        for runner in (Script(lambda m: RunResult(1, "", "usage limit reached")),
                       Script(lambda m: ok("garbage"))):
            self.run_prepare(runner)
        self.assertEqual(self.count("person"), 0)
        result = build_plan(self.conn, today=TODAY)
        self.assertEqual(result.status, "built")
        rows = self.conn.execute("SELECT person_id, why FROM daily_list_entry").fetchall()
        self.assertTrue(rows)
        self.assertTrue(all(r["person_id"] is None and json.loads(r["why"])["find_someone"] for r in rows))


class TemplateTest(unittest.TestCase):
    def test_prepare_plist_template_is_complete(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        self.assertEqual(set(re.findall(r"\{\{[^{}]*\}\}", text)), set(PLACEHOLDERS))
        self.assertFalse(any(d in text for d in DASHES))
        self.assertIn("prepare.deny_rule_checked", text)  # the gate is named where he installs from
        rendered = render(text, live_root="/live", log_path="/logs/prepare.log", label="com.devanshu.outreach-prepare")
        plist = plistlib.loads(rendered.encode("utf-8"))
        self.assertEqual(plist["Label"], "com.devanshu.outreach-prepare")
        self.assertEqual(plist["ProgramArguments"], ["/live/.venv/bin/python", "-m", "outreach.plan", "prepare"])
        self.assertEqual(plist["WorkingDirectory"], "/live")
        self.assertEqual((plist["StandardOutPath"], plist["StandardErrorPath"]), ("/logs/prepare.log",) * 2)
        self.assertEqual(plist["StartCalendarInterval"],
                         [{"Weekday": d, "Hour": 5, "Minute": 30} for d in range(1, 6)])
        self.assertNotIn("EnvironmentVariables", plist)


if __name__ == "__main__":
    unittest.main()
