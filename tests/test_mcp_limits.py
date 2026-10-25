"""The limit sweep (#13, plan Task 14): the server's promises, checked over the final tool set in one place.

The fifteen tools and nothing else; a valid call of each leaves every table no tool may write, and every message the
call does not name, byte for byte as it was, over a store with a message in every state; no hostile argument moves a
message (each is bad input before anything opens); nothing here reaches the network, the mailer or Gmail; the live
store is refused before SQLite is reached; a stray print never reaches the protocol stream; and the pipeline's
`draft` stage registers in its place and keeps its invariant."""
import io
import json
import os
import sqlite3
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import live_checkout
from outreach.mcp import __main__ as cli
from outreach.mcp import server
from outreach.mcp.guard import NEVER_TABLES, install_authorizer, install_triggers, open_guarded
from outreach.mcp.tools import Input, ToolSpec
from outreach.reader.types import FIELDS
from outreach.store import DEFAULT_PATH, connect
from tests import apiseed as seed
from tests.mcptest import NOW, McpCase
from tests.pipeline import stages_mcp
from tests.pipeline.harness import STAGE_NAMES, PipelineHarness
from tests.pipeline.test_flow import INVARIANTS
from tests.test_mcp_boundary import FORBIDDEN_IMPORTS, forbidden, imported_names, sources
from tests.test_mcp_write_grants import table_hashes

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
FIFTEEN = ("ranked_companies", "company", "person", "followups_due", "record_research", "flag_review", "mark_no_go",
           "save_draft", "save_followup", "save_linkedin_note", "add_people", "reader_next", "reader_submit",
           "prepare_targets", "submit_candidates")
BODY = ("Hi Lee,\n\nI read your post on the queue rewrite at Acme and how the team moved billing off a nightly batch. "
        "I build backend systems in Go and Python, and last year I shipped a queue that handled thousands of "
        "requests a minute without a page. I would like to help your team keep that kind of work calm, and I am "
        "looking for a new grad backend role. My work is at https://www.devanshuchicholikar.com and one project is "
        "at https://github.com/devanshu/queue.\n\nWould you be open to referring me?\n\nThanks,\nDevanshu")
READER_BODY = "We build tools for small teams and ship every week. " * 12
HOSTILE = ({"state": "approved"}, {"approved": True}, {"approved_at": NOW}, {"saved_by": "devanshu"},
           {"send": True}, {"skip": True}, {"pause": True}, {"reason_state": "confirmed"}, {"actor": "devanshu"},
           {"sql": "UPDATE message SET state = 'approved'"}, {"message_id": 1}, {"path": DEFAULT_PATH})


def spec(name: str) -> ToolSpec:
    return next(s for s in server.tool_specs() if s.name == name)


def never_rows(conn) -> dict[str, str]:
    hashes = table_hashes(conn)
    return {t: hashes[t] for t in NEVER_TABLES}


def messages(conn) -> dict[int, tuple]:
    return {r["message_id"]: tuple(r) for r in conn.execute("SELECT * FROM message ORDER BY message_id")}


class SweepCase(McpCase):
    """A store with a message in every state the store allows at once (one message may be sending or send_unknown
    at a time, so send_unknown is passed through on the way to failed): drafted, a sent first email with its planned
    follow-up, his own edit, approved, sending, failed and skipped; plus an open review item, a LinkedIn contact, a
    posting the reader may lease and a company the plan wants someone found at."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"OUTREACH_BANNED_PHRASES": str(FIX / "banned_phrases.txt"),
                                               "OUTREACH_MAIL_HOME": str(FIX / "plan")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.acme = seed.company(self.db, "Acme", "working")
        self.pat = seed.person(self.db, self.acme, "Pat Lee", email="pat@acme.example")
        self.drafted = seed.message(self.db, seed.touch(self.db, self.pat), 0, subject="Pat and payments",
                                    body="Hi Pat")
        self.sam = seed.person(self.db, self.acme, "Sam Sent", email="sam@acme.example")
        sent_touch = seed.touch(self.db, self.sam)
        seed.send_first(self.db, sent_touch)
        self.planned = self.db.execute(
            "INSERT INTO message(touch_id, seq, state, due_on, created_at, updated_at) "
            "VALUES (?, 1, 'planned', '2026-11-10', ?, ?)", (sent_touch, NOW, NOW)).lastrowid
        his = seed.message(self.db, seed.touch(self.db, seed.person(self.db, self.acme, "Dee Edited")), 0)
        self.db.execute("UPDATE message SET saved_by = 'devanshu' WHERE message_id = ?", (his,))
        self.db.execute("INSERT INTO review_item(kind, company_id, reason, evidence_key, raised_by, created_at) "
                        "VALUES ('flagged', ?, 'Look', 'k1', 'claude', ?)", (self.acme, NOW))
        self.states = {"drafted": self.drafted, "planned": self.planned, "his_edit": his}
        for state in ("failed", "approved", "sending", "skipped"):
            person = seed.person(self.db, self.acme, f"Pat {state.title()}")
            mid = seed.message(self.db, seed.touch(self.db, person), 0, "drafted")
            path = {"approved": ["approved"], "sending": ["approved", "sending"],
                    "failed": ["approved", "sending", "send_unknown", "failed"], "skipped": ["skipped"]}[state]
            for step in path:
                extra = {"approved": ", approved_at = ?", "sending": ", pending_rfc_id = '<p@x>', claimed_at = ?",
                         "send_unknown": "", "failed": ", pending_rfc_id = NULL, claimed_at = NULL",
                         "skipped": ""}[step]
                params = (step, NOW, mid) if "?" in extra else (step, mid)
                self.db.execute(f"UPDATE message SET state = ?{extra} WHERE message_id = ?", params)
            self.states[state] = mid
        self.states["sent"] = self.db.execute("SELECT message_id FROM message WHERE state = 'sent'").fetchone()[0]
        self.lin = seed.person(self.db, self.acme, "Lin Kedin", channel="linkedin")
        self.lee = seed.person(self.db, self.acme, "Lee New", email="lee@acme.example")
        self.reader_posting = self.db.execute(
            "INSERT INTO posting(company_id, source_key, title, location, first_seen_at, last_seen_at) "
            "VALUES (?, 'url:sweep-1', 'Software Engineer', 'Austin, TX', ?, ?)", (self.acme, NOW, NOW)).lastrowid
        self.db.execute("INSERT INTO posting_body(posting_id, body) VALUES (?, ?)", (self.reader_posting, READER_BODY))
        seed.decision(self.db, self.reader_posting)
        self.bolt = seed.company(self.db, "Bolt Robotics")
        seed.score(self.db, self.bolt, {"role": 5.0})

    def valid(self) -> dict[str, tuple[dict, int | None]]:
        """Tool name to (a valid call, the message it names or None)."""
        blank = {n: {"value": None if n.startswith("years") else "" if n in ("start_date", "location", "pay")
                     else "unknown", "quote": ""} for n in FIELDS}
        return {
            "ranked_companies": ({}, None),
            "company": ({"company_id": self.acme}, None),
            "person": ({"person_id": self.pat}, None),
            "followups_due": ({"days_ahead": 7}, None),
            "prepare_targets": ({}, None),
            "record_research": ({"company_id": self.acme, "items": [
                {"fact": "size", "value": "40", "num_low": 40, "source_kind": "website",
                 "source_ref": "https://acme.example/about", "quote": "A team of 40."}]}, None),
            "flag_review": ({"company_id": self.acme, "reason": "Only senior roles on the careers page."}, None),
            "mark_no_go": ({"person_id": self.pat, "reason": "not_a_fit", "note": "Works on hardware only."}, None),
            "add_people": ({"company_id": self.acme, "people": [{"name": "Ned New", "role_kind": "engineer",
                                                                 "email": "ned@acme.example"}]}, None),
            "save_draft": ({"person_id": self.lee, "subject": "Your queue rewrite", "body": BODY,
                            "hook": "His post on the queue rewrite", "project": "queue"}, None),
            "save_followup": ({"person_id": self.sam, "seq": 1, "body": "Hi Sam, one more note on the ledger idea. "
                                                                        "Thanks, Devanshu"}, self.planned),
            "save_linkedin_note": ({"person_id": self.lin, "body": "Hi Lin, I read your post on the queue rewrite.",
                                    "reason_line": "I read your post on the queue rewrite"}, None),
            "reader_next": ({"limit": 1}, None),
            "reader_submit": ({"readings": [{"posting_id": self.reader_posting, "body_sha256": self.leased_sha(),
                                             "fields": blank}]}, None),
            "submit_candidates": ({"company_id": self.bolt, "candidates": [{
                "name": "Grace Hopper", "role_kind": "engineer",
                "sources": [{"kind": "team", "url": "https://bolt.example/team",
                             "quote": "Grace Hopper, Software Engineer at Bolt Robotics"}]}]}, None),
        }

    def leased_sha(self) -> str:
        row = self.db.execute("SELECT body_sha256 FROM read_queue WHERE posting_id = ? AND state = 'leased'",
                              (self.reader_posting,)).fetchone()
        if row is not None:
            return row[0]
        (item,) = self.ok("reader_next", {"limit": 1})["postings"]
        return item["body_sha256"]


class ToolListTest(unittest.TestCase):
    def test_the_tool_list_is_exactly_the_fifteen(self):
        names = [s.name for s in server.tool_specs()]
        self.assertEqual(sorted(names), sorted(FIFTEEN))
        self.assertEqual(len(names), 15)


class SweepTest(SweepCase):
    def test_every_tool_run_with_valid_input_leaves_the_never_surface_untouched(self):
        self.assertEqual({self.db.execute("SELECT state FROM message WHERE message_id = ?", (m,)).fetchone()[0]
                          for m in self.states.values()},
                         {"planned", "drafted", "approved", "sending", "sent", "failed", "skipped"})
        calls = self.valid()
        self.assertEqual(set(calls), set(FIFTEEN))
        for name in FIFTEEN:
            args, named = calls[name] if name != "reader_submit" else self.valid()["reader_submit"]
            with self.subTest(tool=name):
                never, before = never_rows(self.db), messages(self.db)
                self.ok(name, args)
                self.assertEqual(never_rows(self.db), never, f"{name} changed a table no tool may write")
                after = messages(self.db)
                for mid, row in before.items():
                    if mid != named:
                        self.assertEqual(after[mid], row, f"{name} changed message {mid}")
                touched = [mid for mid in after if mid not in before] + ([named] if named else [])
                for mid in touched:  # what the call wrote is a draft or a planned row, never approved
                    row = self.db.execute("SELECT state, approved_at, saved_by FROM message WHERE message_id = ?",
                                          (mid,)).fetchone()
                    self.assertIn(row["state"], ("drafted", "planned"), f"{name} left message {mid} {row['state']}")
                    self.assertIsNone(row["approved_at"], f"{name} approved message {mid}")
                    self.assertIn(row["saved_by"], ("claude", "sender"), f"{name} saved message {mid} as another")

    def test_no_message_leaves_drafted_through_any_tool_with_any_input(self):
        before = table_hashes(self.db)
        states = {mid: self.db.execute("SELECT state FROM message WHERE message_id = ?", (mid,)).fetchone()[0]
                  for mid in self.states.values()}
        calls = self.valid()
        before = table_hashes(self.db)  # the reader_next inside valid() leased a posting; start after it
        for name in FIFTEEN:
            args, _named = calls[name]
            for extra in HOSTILE:
                with self.subTest(tool=name, extra=extra):
                    err = self.error(name, {**args, **extra})
                    self.assertEqual(err["code"], "bad_input")
        self.assertEqual(table_hashes(self.db), before)
        for mid, state in states.items():
            self.assertEqual(self.db.execute("SELECT state FROM message WHERE message_id = ?", (mid,)).fetchone()[0],
                             state)


class RawStoreTest(SweepCase):
    """Per write tool: a schema-valid call, then raw statements that would approve, skip, send or insert an approved
    email, run on a connection opened the way `run_tool` opens it for that tool (`open_guarded` with its grants), and
    on the authorizer and the triggers alone. The triggers refuse every one; the authorizer refuses every one the
    tool's grants do not cover; together nothing moves."""

    def statements(self) -> list[tuple[str, tuple, str, frozenset[str]]]:
        """(sql, params, table, the columns it updates or {'<insert>'})."""
        tia = seed.person(self.db, self.acme, "Tia Touchless", email="tia@acme.example")
        touch = seed.touch(self.db, tia)
        return [
            ("UPDATE message SET state = 'approved', approved_at = ? WHERE message_id = ?", (NOW, self.drafted),
             "message", frozenset({"state", "approved_at"})),
            ("UPDATE message SET state = 'skipped' WHERE message_id = ?", (self.drafted,), "message",
             frozenset({"state"})),
            ("UPDATE message SET state = 'drafted' WHERE message_id = ?", (self.states["approved"],), "message",
             frozenset({"state"})),
            ("UPDATE message SET sent_at = ? WHERE message_id = ?", (NOW, self.drafted), "message",
             frozenset({"sent_at"})),
            ("INSERT INTO message(touch_id, seq, state, subject, body, approved_at, created_at, updated_at) "
             "VALUES (?, 0, 'approved', 'Hi', 'Hello', ?, ?, ?)", (touch, NOW, NOW, NOW), "message",
             frozenset({"<insert>"})),
        ]

    def test_no_write_tools_connection_can_approve_skip_or_send_on_the_raw_store(self):
        calls = self.valid()
        statements = self.statements()
        writers = [name for name in FIFTEEN if spec(name).grants]
        self.assertEqual(len(writers), 10)
        for name in writers:
            args, _named = calls[name] if name != "reader_submit" else self.valid()["reader_submit"]
            self.ok(name, args)
            grants = spec(name).grants
            layers = {"triggers": lambda: self._triggers_only(), "authorizer": lambda: self._authorizer_only(grants),
                      "guard": lambda: self._open(grants)}
            for sql, params, table, columns in statements:
                grant = grants.get(table)
                covered = grant is not None and (grant.insert if columns == {"<insert>"} else columns <= grant.update)
                for layer, connect_layer in layers.items():
                    if layer == "authorizer" and covered:
                        continue  # the tool may write those columns; the triggers hold the values
                    with self.subTest(tool=name, layer=layer, sql=sql[:50]):
                        conn = connect_layer()
                        before = messages(self.db)
                        with self.assertRaises(sqlite3.DatabaseError) as caught:
                            conn.execute(sql, params)
                        want = "mcp guard" if layer == "triggers" else ("not authorized", "mcp guard")
                        self.assertTrue(any(w in str(caught.exception) for w in
                                            ((want,) if isinstance(want, str) else want)), str(caught.exception))
                        self.assertEqual(messages(self.db), before)

    def _open(self, grants):
        conn = open_guarded(grants, path=self.path)
        self.addCleanup(conn.close)
        return conn

    def _triggers_only(self):
        conn = connect(self.path)
        self.addCleanup(conn.close)
        install_triggers(conn)
        return conn

    def _authorizer_only(self, grants):
        conn = connect(self.path)
        self.addCleanup(conn.close)
        install_authorizer(conn, grants)
        return conn


class OfflineTest(unittest.TestCase):
    def test_no_tool_can_reach_the_network_the_mailer_or_gmail(self):
        network = ("socket", "ssl", "http", "urllib", "subprocess", "ctypes", "smtplib", "ftplib", "asyncio")
        for path in sources():
            names = imported_names(path)
            self.assertEqual([n for n in names if forbidden(n)], [], path)
            if path.name != "__main__.py":  # it uses shutil.which for the registration line, never a network module
                self.assertEqual([n for n in names if n.split(".")[0] in network], [], path)
        modules = [m for m in server.GROUPS]
        code = ("import json, sys\n"
                "import outreach.mcp.server, outreach.mcp.__main__\n"
                f"for m in {modules!r}: __import__(m)\n"
                "outreach.mcp.server.build_server(path='unused.db')\n"
                "print(json.dumps(sorted(sys.modules)))\n")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        loaded = json.loads(out.stdout)
        self.assertEqual([m for m in loaded if forbidden(m)], [], FORBIDDEN_IMPORTS)
        self.assertTrue(set(modules) <= set(loaded))

    def test_the_live_store_is_refused_by_serve_check_and_a_tool_call(self):
        reached = mock.Mock(side_effect=AssertionError("sqlite3.connect was reached"))
        env = {k: v for k, v in os.environ.items() if k != "OUTREACH_DB"}
        with mock.patch.object(live_checkout, "is_live", return_value=False), \
                mock.patch("outreach.store.db.sqlite3.connect", reached), mock.patch.dict(os.environ, env, clear=True):
            for argv in ([], ["--check"]):
                stderr = io.StringIO()
                with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(stderr), \
                        mock.patch.object(server.MCPServer, "run", side_effect=AssertionError("served")):
                    self.assertEqual(cli.main(argv), 1, argv)
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn("live checkout", stderr.getvalue())
            for path in (None, DEFAULT_PATH, DEFAULT_PATH.upper()):
                for name in ("ranked_companies", "prepare_targets", "reader_next", "followups_due"):
                    with self.subTest(path=path, tool=name):
                        is_error, payload = server.run_tool(spec(name), {}, path=path, clock=lambda: NOW)
                        self.assertTrue(is_error)
                        self.assertEqual(payload["code"], "store_unavailable")
                        self.assertIn("live checkout", payload["message"])
        reached.assert_not_called()


class StrayPrintTest(SweepCase):
    def test_a_stray_print_goes_to_stderr_never_stdout(self):
        def noisy(ctx, args):
            print("a stray line that must never reach the protocol stream")
            return {"ok": True}

        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            is_error, payload = server.run_tool(ToolSpec("noisy", "Prints.", Input, noisy), {}, path=self.path,
                                                clock=lambda: NOW)
        self.assertEqual((is_error, payload), (False, {"ok": True}))
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("a stray line", stderr.getvalue())


class FlowStageTest(unittest.TestCase):
    def test_the_flow_stage_registers_and_its_invariant_holds(self):
        full = PipelineHarness()
        self.addCleanup(full.close)
        names = [n for n, _ in full.stages]
        self.assertIn("draft", names)
        self.assertEqual(names, sorted(names, key=STAGE_NAMES.index))
        self.assertLess(names.index("plan"), names.index("draft"))
        self.assertLess(names.index("draft"), names.index("send"))
        h = PipelineHarness(register_streams=False)
        self.addCleanup(h.close)
        stages_mcp.register(h)
        self.assertEqual([n for n, _ in h.stages], ["draft"])
        h.run()
        INVARIANTS["draft"](h)
        self.assertEqual(set(h.mcp.results), {"add_people", "save_draft", "save_linkedin_note", "flag_review"})

    def test_the_invariant_notices_an_approved_draft_a_confirmed_line_or_a_never_table_write(self):
        for breakage in ("UPDATE message SET state = 'approved', approved_at = '2026-11-10T16:00:00Z' "
                         "WHERE message_id = {message}",
                         "UPDATE linkedin_draft SET reason_state = 'confirmed', saved_by = 'devanshu' "
                         "WHERE draft_id = {note}",
                         "UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = {company}",
                         "INSERT INTO event(at, recorded_at, entity, kind, actor) VALUES ('2026-11-10T16:00:00Z', "
                         "'2026-11-10T16:00:00Z', 'message', 'approved', 'devanshu')"):
            with self.subTest(breakage=breakage[:50]):
                h = PipelineHarness(register_streams=False)
                self.addCleanup(h.close)
                stages_mcp.register(h)
                h.run()
                s = h.mcp
                h.conn.execute(breakage.format(message=s.message, note=s.note, company=s.company))
                with self.assertRaises(AssertionError):
                    INVARIANTS["draft"](h)


if __name__ == "__main__":
    unittest.main()
