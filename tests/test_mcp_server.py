"""The server (#13, plan Task 5 on the official SDK): tool registration, the one call path every tool goes through,
startup refusals, `--check` and `--print-registration`."""
import io
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from pydantic import Field

import live_checkout
from outreach.mcp import __main__ as cli
from outreach.mcp import server
from outreach.mcp.guard import Grant
from outreach.mcp.tools import Input, ToolError, ToolSpec
from outreach.store import DEFAULT_PATH
from tests import apiseed as seed
from tests.mcptest import McpCase, call, fingerprint, list_tools, store_path
from tests.storetest import fresh_store
from tests.test_mcp_boundary import forbidden

ROOT = Path(__file__).resolve().parent.parent
PINNED_TOOLS: set[str] = set()
FORBIDDEN_PROPERTIES = frozenset({
    "state", "approved", "approved_at", "approve", "send", "sent", "sent_at", "skip", "unapprove", "pause", "paused",
    "resume", "confirm", "confirmed", "reason_state", "saved_by", "added_by", "raised_by", "actor", "source",
    "locked", "lift", "resolve", "sql", "query", "path", "db"})
FORBIDDEN_VERBS = ("approve", "send", "skip", "pause", "unapprove", "resume", "confirm", "sql")


class EchoInput(Input):
    n: int = Field(1, ge=1, le=5)
    word: str = Field("hi", max_length=10)


CALLS = []


def echo(ctx, args):
    CALLS.append(args)
    return {"n": args.n, "word": args.word, "today": ctx.today.isoformat(), "now": ctx.now}


def fails(ctx, args):
    raise ToolError("not_found", "No company 7.", "Use an id from ranked_companies.")


def crashes(ctx, args):
    raise ValueError("boom")


def locked(ctx, args):
    raise sqlite3.OperationalError("database is locked")


def writes(ctx, args):
    ctx.conn.execute("UPDATE message SET state = 'approved', approved_at = ?", (ctx.now,))
    return {"done": True}


def huge(ctx, args):
    return {"text": "x" * (server.MAX_RESULT_CHARS + 1)}


FAKES = [
    ToolSpec("echo", "Echo the input.", EchoInput, echo),
    ToolSpec("fails", "Always a plain error.", Input, fails),
    ToolSpec("crashes", "Raises.", Input, crashes),
    ToolSpec("locked", "The store is busy.", Input, locked),
    ToolSpec("writes", "Tries to approve with no grant.", Input, writes),
    ToolSpec("huge", "Too big.", Input, huge),
    ToolSpec("granted", "Declares a grant.", EchoInput, echo, {"event": Grant(insert=True)}),
]


class CallPathTest(McpCase):
    specs = FAKES

    def test_a_call_returns_one_text_block_of_json_and_the_clock_s_new_york_day(self):
        self.assertEqual(self.ok("echo", {"n": 3}), {"n": 3, "word": "hi", "today": "2026-11-10",
                                                     "now": "2026-11-10T15:14:00Z"})

    def test_bad_input_is_a_plain_error_and_the_handler_never_runs(self):
        CALLS.clear()
        for args, field in (({"n": 3, "saved_by": "devanshu"}, "saved_by"), ({"n": "3"}, "n"), ({"n": 6}, "n"),
                            ({"n": True}, "n"), ({"word": "x" * 11}, "word"), ({"word": None}, "word")):
            err = self.error("echo", args)
            self.assertEqual(err["code"], "bad_input", args)
            self.assertIn(field, err["message"])
            self.assertNotIn("devanshu", err["message"])  # the rejected value is not echoed back
        self.assertEqual(CALLS, [])

    def test_tool_error_becomes_code_message_and_fix(self):
        self.assertEqual(self.error("fails"), {"code": "not_found", "message": "No company 7.",
                                               "fix": "Use an id from ranked_companies."})

    def test_an_unexpected_exception_is_internal_and_its_traceback_goes_to_stderr_only(self):
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            err = self.error("crashes")
        self.assertEqual(err["code"], "internal")
        self.assertIn("ValueError: boom", err["message"])
        self.assertNotIn("Traceback", err["message"])
        self.assertIn("Traceback", stderr.getvalue())

    def test_a_locked_store_is_busy_with_a_fix(self):
        err = self.error("locked")
        self.assertEqual(err["code"], "busy")
        self.assertTrue(err["fix"])

    def test_a_write_the_tool_did_not_declare_is_refused_and_nothing_changes(self):
        mid = seed.message(self.db, seed.touch(self.db, seed.person(self.db, seed.company(self.db))), 0)
        before = fingerprint(self.db)
        err = self.error("writes")
        self.assertEqual(err["code"], "refused")
        self.assertEqual(fingerprint(self.db), before)
        self.assertEqual(self.db.execute("SELECT state FROM message WHERE message_id = ?", (mid,)).fetchone()[0],
                         "drafted")

    def test_an_oversize_result_is_an_error_not_a_truncation(self):
        self.assertEqual(self.error("huge")["code"], "result_too_large")

    def test_each_call_gets_its_own_guarded_connection_with_the_tools_grants_and_closes_it(self):
        opened = []
        real = server.open_guarded

        def spy(grants, *, path):
            conn = real(grants, path=path)
            opened.append((dict(grants), path, conn))
            return conn

        with mock.patch.object(server, "open_guarded", spy):
            self.ok("echo")
            self.ok("granted")
        self.assertEqual([(g, p) for g, p, _ in opened], [({}, self.path), ({"event": Grant(insert=True)}, self.path)])
        for _g, _p, conn in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

    def test_a_missing_store_is_a_plain_error_not_an_empty_result(self):
        missing = server.build_server(path=os.path.join(os.path.dirname(self.path), "gone", "outreach.db"),
                                      clock=lambda: "2026-11-10T15:14:00Z", specs=FAKES)
        is_error, err = call(missing, "echo", {})
        self.assertTrue(is_error)
        self.assertEqual(err["code"], "store_unavailable")
        self.assertIn("outreach.install", err["message"])

    def test_tools_list_has_strict_schemas_and_honest_annotations(self):
        tools = {t.name: t for t in list_tools(self.server)}
        self.assertEqual(set(tools), {s.name for s in FAKES})
        echo_tool = tools["echo"]
        self.assertFalse(echo_tool.input_schema["additionalProperties"])
        self.assertEqual(echo_tool.input_schema["properties"]["n"]["maximum"], 5)
        self.assertTrue(echo_tool.annotations.read_only_hint)
        self.assertFalse(echo_tool.annotations.destructive_hint)
        self.assertFalse(echo_tool.annotations.open_world_hint)
        self.assertFalse(tools["granted"].annotations.read_only_hint)


def walk_schema(schema: dict, defs: dict, where: str):
    """Yields (where, schema) for every sub-schema, following $ref into $defs."""
    if "$ref" in schema:
        schema = defs[schema["$ref"].split("/")[-1]]
    yield where, schema
    for name, sub in schema.get("properties", {}).items():
        yield from walk_schema(sub, defs, f"{where}.{name}")
    if "items" in schema:
        yield from walk_schema(schema["items"], defs, f"{where}[]")
    for sub in schema.get("anyOf", []):
        yield from walk_schema(sub, defs, where)


class RealToolsTest(unittest.TestCase):
    def test_tool_list_is_pinned(self):
        self.assertEqual({s.name for s in server.tool_specs()}, PINNED_TOOLS)

    def test_every_real_tool_is_read_only_until_the_write_batch(self):
        self.assertEqual([s.name for s in server.tool_specs() if s.grants], [])

    def test_every_input_schema_is_closed_and_bounded(self):
        for spec in server.tool_specs():
            schema = spec.input_model.model_json_schema()
            for where, sub in walk_schema(schema, schema.get("$defs", {}), spec.name):
                kind = sub.get("type")
                if kind == "object":
                    self.assertIs(sub.get("additionalProperties"), False, where)
                if kind == "string":
                    self.assertIn("maxLength", sub, where)
                if kind == "array":
                    self.assertIn("maxItems", sub, where)
                if kind == "integer":
                    self.assertIn("maximum", sub, where)
                    self.assertIn("minimum", sub, where)

    def test_no_tool_name_or_property_holds_a_forbidden_verb(self):
        for spec in server.tool_specs():
            self.assertFalse(any(v in spec.name for v in FORBIDDEN_VERBS), spec.name)
            schema = spec.input_model.model_json_schema()
            for where, _sub in walk_schema(schema, schema.get("$defs", {}), spec.name):
                self.assertNotIn(where.split(".")[-1].rstrip("[]"), FORBIDDEN_PROPERTIES, where)

    def test_descriptions_are_short_and_dash_free(self):
        for spec in server.tool_specs():
            self.assertLessEqual(len(spec.description), 600, spec.name)
            self.assertFalse(any(c in spec.description for c in (chr(0x2013), chr(0x2014))), spec.name)

    def test_instructions_state_the_limits_point_at_the_skill_and_use_no_dashes(self):
        text = server.INSTRUCTIONS
        self.assertLessEqual(len(text), 1500)
        for phrase in ("never approves", "Outbox", ".claude/skills/cold-email/SKILL.md", "data", "refusal"):
            self.assertIn(phrase, text)
        self.assertFalse(any(c in text for c in (chr(0x2013), chr(0x2014))))

    def test_the_server_carries_the_instructions_and_a_name(self):
        srv = server.build_server(path="unused.db")
        self.assertEqual(srv.name, "outreach")
        self.assertEqual(srv.instructions, server.INSTRUCTIONS)


class FreshProcessTest(unittest.TestCase):
    def test_a_fresh_process_loading_the_server_loads_no_api_sender_mailer_or_refresh_module(self):
        code = (
            "import json, sys\n"
            "import outreach.mcp.server, outreach.mcp.__main__\n"
            "outreach.mcp.server.build_server(path='unused.db')\n"
            "print(json.dumps(sorted(sys.modules)))\n"
        )
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual([m for m in json.loads(out.stdout) if forbidden(m)], [])

    def test_check_runs_on_a_scratch_store_and_prints_ok_path_and_tool_count(self):
        db = fresh_store()
        path = store_path(db)
        db.close()
        out = subprocess.run([sys.executable, "-m", "outreach.mcp", "--check"], cwd=ROOT, capture_output=True,
                             text=True, timeout=120, env={**os.environ, "OUTREACH_DB": path}, stdin=subprocess.DEVNULL)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), f"ok {os.path.realpath(path)} {len(PINNED_TOOLS)} tools")


class StartupTest(unittest.TestCase):
    def run_main(self, argv, env_db: str | None):
        env = {k: v for k, v in os.environ.items() if k != "OUTREACH_DB"}
        if env_db is not None:
            env["OUTREACH_DB"] = env_db
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), redirect_stdout(stdout), redirect_stderr(stderr), \
                mock.patch.object(server.MCPServer, "run", side_effect=AssertionError("served")):
            code = cli.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_serve_refuses_the_default_store_outside_the_live_checkout(self):
        reached = mock.Mock(side_effect=AssertionError("sqlite3.connect was reached"))
        with mock.patch.object(live_checkout, "is_live", return_value=False), \
                mock.patch("outreach.store.db.sqlite3.connect", reached):
            for env_db in (None, DEFAULT_PATH.lower()):
                code, out, err = self.run_main([], env_db)
                self.assertEqual((code, out), (1, ""))
                self.assertIn("live checkout", err)
                self.assertEqual(len(err.strip().splitlines()), 1)
        reached.assert_not_called()

    def test_a_missing_store_exits_with_the_install_command_and_nothing_on_stdout(self):
        missing = str(ROOT / ".no-such-folder" / "outreach.db")
        for argv in ([], ["--check"]):
            code, out, err = self.run_main(argv, missing)
            self.assertEqual((code, out), (1, ""))
            self.assertIn("outreach.install", err)

    def test_a_store_newer_than_the_code_exits_plainly(self):
        db = fresh_store()
        db.execute("PRAGMA user_version = 999")
        path = store_path(db)
        db.close()
        code, out, err = self.run_main([], path)
        self.assertEqual((code, out), (1, ""))
        self.assertIn("newer", err)

    def test_serve_with_a_scratch_store_runs_the_server(self):
        db = fresh_store()
        path = store_path(db)
        db.close()
        with mock.patch.object(server.MCPServer, "run") as run, mock.patch.dict(os.environ, {"OUTREACH_DB": path}):
            self.assertEqual(cli.main([]), 0)
        run.assert_called_once_with("stdio")


class RegistrationTest(unittest.TestCase):
    def printed(self) -> str:
        stdout = io.StringIO()
        reached = mock.Mock(side_effect=AssertionError("the store was opened"))
        with redirect_stdout(stdout), mock.patch("outreach.store.db.sqlite3.connect", reached):
            self.assertEqual(cli.main(["--print-registration"]), 0)
        reached.assert_not_called()
        return stdout.getvalue().strip()

    def test_print_registration_is_one_add_json_command_for_the_live_checkout(self):
        text = self.printed()
        self.assertEqual(len(text.splitlines()), 1)
        words = shlex.split(text)
        self.assertEqual(words[:3], ["cd", live_checkout.LIVE_ROOT, "&&"])
        self.assertEqual(words[3:7], ["claude", "mcp", "add-json", "outreach"])
        self.assertEqual(words[8:], ["--scope", "local"])
        config = json.loads(words[7])
        self.assertEqual(config["type"], "stdio")
        self.assertTrue(os.path.basename(config["command"]) == "uv")
        self.assertEqual(config["args"], ["run", "--directory", live_checkout.LIVE_ROOT, "--frozen", "python", "-m",
                                          "outreach.mcp"])

    def test_registration_has_no_store_override_and_quotes_the_path_with_spaces(self):
        text = self.printed()
        self.assertNotIn("OUTREACH_DB", text)
        self.assertIn(" ", live_checkout.LIVE_ROOT)
        self.assertIn(shlex.quote(live_checkout.LIVE_ROOT), text)


if __name__ == "__main__":
    unittest.main()
