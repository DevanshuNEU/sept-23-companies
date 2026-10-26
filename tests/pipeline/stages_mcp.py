"""The `draft` stage (#13 owns it): Claude drafts after the plan, through the MCP server's own call path.

The stage seeds its own fixture company, "Draftwell Labs" (working, unscored, so no earlier stage's invariant counts
it), then calls the real tools through `outreach.mcp.server.run_tool` (the registry and the guard, not stdio) on the
harness store with a fixed clock: `add_people` for an emailed engineer and a LinkedIn-only manager, `save_draft` for
the engineer, `save_linkedin_note` for the manager and `flag_review` once. The banned list and me.md are the test
fixtures, set for the stage only. A tool call that fails stops the stage.

Before the calls it keeps a fingerprint of every table no tool may write (`guard.NEVER_TABLES`, and the bridge's
`BRIDGE_TABLES`, which only `log_sent` writes) and the last event id; `register` leaves those and the ids the calls returned on the harness as `harness.mcp` (an `McpStage`).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from unittest import mock

from outreach.mcp import server
from outreach.mcp.guard import BRIDGE_TABLES, NEVER_TABLES

__all__ = ["register", "McpStage", "COMPANY", "never_fingerprint"]

COMPANY = "Draftwell Labs"
CLOCK = "2026-11-10T15:30:00Z"  # 10:30 in New York on the plan stage's day
STAMP = "2026-11-09T12:00:00Z"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
ENGINEER_EMAIL = "eda@draftwell.example"
REASON = "I read your post on the ledger rewrite"
NOTE = f"Hi Mo, {REASON} and built a similar job runner. I would like to hear how your team works."
BODY = ("Hi Eda,\n\nI read your post on how the Draftwell Labs ledger moved off a nightly batch. I build backend "
        "systems in Go and Python, and last year I shipped a queue that handled thousands of requests a minute "
        "without a page. I would like to help your team keep that kind of work calm, and I am looking for a new "
        "grad backend role. My work is at https://www.devanshuchicholikar.com and one project is at "
        "https://github.com/devanshu/queue.\n\nWould you be open to referring me?\n\nThanks,\nDevanshu")


def never_fingerprint(conn) -> str:
    """A hash of every row of every table no MCP tool this stage calls may write (the bridge tables too: only
    `log_sent` writes them, and the stage never calls it)."""
    digest = hashlib.sha256()
    for table in sorted(NEVER_TABLES | BRIDGE_TABLES):
        digest.update(table.encode())
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1"):
            digest.update(repr(tuple(row)).encode())
    return digest.hexdigest()


def _store_path(conn) -> str:
    return next(r["file"] for r in conn.execute("PRAGMA database_list") if r["name"] == "main")


class McpStage:
    def __init__(self, harness):
        self.conn = harness.conn
        self.company: int | None = None
        self.engineer = self.manager = self.message = self.note = self.review = None
        self.before_never: str | None = None
        self.last_event_before = 0
        self.results: dict[str, dict] = {}

    def call(self, name: str, arguments: dict) -> dict:
        spec = next(s for s in server.tool_specs() if s.name == name)
        is_error, payload = server.run_tool(spec, arguments, path=_store_path(self.conn), clock=lambda: CLOCK)
        if is_error:
            raise AssertionError(f"the draft stage's {name} call failed: {payload}")
        self.results[name] = payload
        return payload

    def run(self) -> None:
        conn = self.conn
        self.company = conn.execute("INSERT INTO company(name, status, status_at, created_at) VALUES (?, 'working', "
                                    "?, ?)", (COMPANY, STAMP, STAMP)).lastrowid
        conn.execute("INSERT INTO mx_check(domain, status, checked_at) VALUES ('draftwell.example', 'ok', ?) "
                     "ON CONFLICT(domain) DO UPDATE SET status = excluded.status, checked_at = excluded.checked_at",
                     ("2026-11-10T15:00:00Z",))
        self.before_never = never_fingerprint(conn)
        self.last_event_before = conn.execute("SELECT coalesce(max(event_id), 0) FROM event").fetchone()[0]
        env = {"OUTREACH_BANNED_PHRASES": str(FIXTURES / "banned_phrases.txt"),
               "OUTREACH_MAIL_HOME": str(FIXTURES / "plan")}
        with mock.patch.dict(os.environ, env):
            added = self.call("add_people", {"company_id": self.company, "people": [
                {"name": "Eda Engineer", "title": "Software Engineer", "role_kind": "engineer",
                 "email": ENGINEER_EMAIL,
                 "sources": [{"kind": "team", "url": "https://draftwell.example/team",
                              "quote": "Eda Engineer, Software Engineer"}]},
                {"name": "Mo Manager", "title": "Engineering Manager", "role_kind": "manager",
                 "linkedin_url": "https://www.linkedin.com/in/mo-manager"}]})
            self.engineer, self.manager = (r["person_id"] for r in added["results"])
            self.message = self.call("save_draft", {
                "person_id": self.engineer, "subject": "Your ledger post and a new grad backend role", "body": BODY,
                "hook": "Her post on the ledger rewrite", "hook_on": "2026-11-02", "project": "queue"})["message_id"]
            self.note = self.call("save_linkedin_note", {
                "person_id": self.manager, "body": NOTE, "reason_line": REASON,
                "facts": [{"text": "ledger rewrite", "label": "Blog", "url": "https://draftwell.example/blog/ledger",
                           "in_note": True}]})["draft_id"]
            self.review = self.call("flag_review", {"company_id": self.company,
                                                    "reason": "The careers page lists one backend role."})["review_id"]


def register(harness) -> None:
    stage = McpStage(harness)
    harness.mcp = stage
    harness.stages.append(("draft", stage.run))
