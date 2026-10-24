"""Each write tool cannot write beyond its grants (#13, lean v1).

For every write tool: its grants are pinned here; on a connection opened with exactly those grants, the writes no
tool may make (approve, a no_go row, a company or sender change, a delete) and the writes just outside this tool's
grants are refused; and a real call with valid input changes no table outside its grants."""
import hashlib
import os
import sqlite3
import unittest
from unittest import mock

from outreach.mcp import server
from outreach.mcp.guard import Grant, open_guarded
from tests import apiseed as seed
from tests.mcptest import NOW, McpCase
from tests.test_drafts_checks import BODY, FIXTURE_BANNED

INSERT = Grant(insert=True)
PINNED = {
    "record_research": {"evidence": INSERT, "event": INSERT},
    "flag_review": {"review_item": INSERT, "event": INSERT},
    "mark_no_go": {"review_item": INSERT, "event": INSERT},
    "save_draft": {"touch": INSERT, "event": INSERT,
                   "message": Grant(insert=True, update=frozenset({"subject", "body", "hook", "project", "checks",
                                                                   "saved_by", "updated_at", "revision"}))},
    "save_followup": {"event": INSERT,
                      "message": Grant(update=frozenset({"state", "subject", "body", "checks", "saved_by",
                                                         "updated_at", "revision"}))},
    "add_people": {"person": Grant(insert=True, update=frozenset({"profile_text", "notes", "updated_at", "revision"})),
                   "person_source": INSERT, "event": INSERT},
}


def spec(name: str):
    return next(s for s in server.tool_specs() if s.name == name)


def table_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    out = {}
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"):
        digest = hashlib.sha256()
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1"):
            digest.update(repr(tuple(row)).encode())
        out[table] = digest.hexdigest()
    return out


class GrantsCase(McpCase):
    """A company with a posting, a person with a drafted first email, a sent first email with its planned
    follow-up, and an open review item."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"OUTREACH_BANNED_PHRASES": str(FIXTURE_BANNED)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.acme = seed.company(self.db, "Acme", "working")
        self.posting = seed.posting(self.db, self.acme)
        self.pat = seed.person(self.db, self.acme, "Pat Lee", email="pat@acme.example")
        self.touch = seed.touch(self.db, self.pat)
        self.drafted = seed.message(self.db, self.touch, 0, subject="Pat and payments", body="Hi Pat")
        self.sam = seed.person(self.db, self.acme, "Sam Sent", email="sam@acme.example")
        self.sent_touch = seed.touch(self.db, self.sam)
        seed.send_first(self.db, self.sent_touch)
        self.planned = self.db.execute(
            "INSERT INTO message(touch_id, seq, state, due_on, created_at, updated_at) "
            "VALUES (?, 1, 'planned', '2026-11-10', ?, ?)", (self.sent_touch, NOW, NOW)).lastrowid
        self.his = seed.message(self.db, seed.touch(self.db, seed.person(self.db, self.acme, "Dee Edited")), 0)
        self.db.execute("UPDATE message SET saved_by = 'devanshu' WHERE message_id = ?", (self.his,))
        self.review = self.db.execute(
            "INSERT INTO review_item(kind, company_id, reason, evidence_key, raised_by, created_at) "
            "VALUES ('flagged', ?, 'Look', 'k1', 'claude', ?)", (self.acme, NOW)).lastrowid

    def never(self) -> list[tuple[str, tuple]]:
        """Writes no tool may make, whatever its grants."""
        return [
            ("UPDATE message SET state = 'approved', approved_at = ? WHERE message_id = ?", (NOW, self.drafted)),
            ("UPDATE message SET sent_at = ? WHERE message_id = ?", (NOW, self.drafted)),
            ("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'said_no', 'claude', ?)",
             (self.pat, NOW)),
            ("INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'other', 'claude', ?)",
             (self.acme, NOW)),
            ("UPDATE company SET paused_at = ? WHERE company_id = ?", (NOW, self.acme)),
            ("UPDATE sender SET paused = 1, pause_kind = 'by_you', paused_at = ?", (NOW,)),
            ("UPDATE touch SET state = 'closed', closed_at = ?, close_reason = 'skipped' WHERE touch_id = ?",
             (NOW, self.touch)),
            ("DELETE FROM review_item WHERE review_id = ?", (self.review,)),
            ("DELETE FROM message WHERE message_id = ?", (self.drafted,)),
            ("UPDATE review_item SET state = 'resolved', resolution = 'marked_no_go', resolved_at = ? "
             "WHERE review_id = ?", (NOW, self.review)),
            ("INSERT INTO event(at, recorded_at, entity, kind, actor) VALUES (?, ?, 'message', 'approved', "
             "'devanshu')", (NOW, NOW)),
        ]

    def check_tool(self, name: str, outside: list[tuple[str, tuple]], valid: dict) -> None:
        tool = spec(name)
        self.assertEqual(dict(tool.grants), PINNED[name])
        conn = open_guarded(tool.grants, path=self.path)
        self.addCleanup(conn.close)
        before = table_hashes(self.db)
        for sql, params in self.never() + outside:
            with self.subTest(tool=name, sql=sql):
                with self.assertRaises(sqlite3.DatabaseError) as caught:
                    conn.execute(sql, params)
                self.assertTrue("not authorized" in str(caught.exception) or "mcp guard" in str(caught.exception),
                                str(caught.exception))
        self.assertEqual(table_hashes(self.db), before)
        self.ok(name, valid)
        after = table_hashes(self.db)
        changed = {t for t in after if after[t] != before[t]}
        self.assertTrue(changed, f"{name} wrote nothing")
        self.assertLessEqual(changed, set(tool.grants), name)

    def test_every_write_tool_is_pinned_here(self):
        self.assertEqual({s.name for s in server.tool_specs() if s.grants}, set(PINNED))

    def test_record_research_cannot_write_beyond_its_grants(self):
        self.check_tool("record_research", [
            ("INSERT INTO review_item(kind, company_id, reason, evidence_key, raised_by, created_at) "
             "VALUES ('flagged', ?, 'x', 'k2', 'claude', ?)", (self.acme, NOW)),
            ("UPDATE person SET notes = 'x' WHERE person_id = ?", (self.pat,)),
            ("INSERT INTO evidence(company_id, fact, value, confidence, locked, source_kind, recorded_at) "
             "VALUES (?, 'sponsorship', 'yes', 'confirmed', 1, 'hand', ?)", (self.acme, NOW)),
            ("INSERT INTO evidence(company_id, fact, value, num_low, confidence, source_kind, recorded_at) "
             "VALUES (?, 'size', '900', 900, 'estimate', 'website', ?)", (self.acme, NOW)),
        ], {"company_id": self.acme, "items": [{"fact": "size", "value": "40", "num_low": 40, "source_kind":
                                                "website", "source_ref": "https://acme.example/about",
                                                "quote": "A team of 40."}]})

    def test_flag_review_cannot_write_beyond_its_grants(self):
        self.check_tool("flag_review", [
            ("INSERT INTO evidence(company_id, fact, value, num_low, confidence, source_kind, recorded_at) "
             "VALUES (?, 'size', '40', 40, 'guess', 'website', ?)", (self.acme, NOW)),
            ("UPDATE review_item SET reason = 'changed' WHERE review_id = ?", (self.review,)),
            ("INSERT INTO review_item(kind, company_id, reason, evidence_key, raised_by, created_at) "
             "VALUES ('flagged', ?, 'x', 'k3', 'rules', ?)", (self.acme, NOW)),
            ("UPDATE person SET notes = 'x' WHERE person_id = ?", (self.pat,)),
        ], {"company_id": self.acme, "reason": "Careers page lists only senior roles.", "posting_id": self.posting})

    def test_mark_no_go_cannot_write_beyond_its_grants(self):
        self.check_tool("mark_no_go", [
            ("UPDATE no_go SET lifted_at = ?, lift_reason = 'x'", (NOW,)),
            ("UPDATE review_item SET state = 'resolved', resolution = 'marked_ok', resolved_at = ? "
             "WHERE review_id = ?", (NOW, self.review)),
            ("UPDATE person SET notes = 'no-go' WHERE person_id = ?", (self.pat,)),
            ("UPDATE touch SET reply_kind = 'said_no' WHERE touch_id = ?", (self.touch,)),
        ], {"person_id": self.pat, "reason": "said_no", "quote": "Please stop emailing me."})

    def test_save_draft_cannot_write_beyond_its_grants(self):
        lee = seed.person(self.db, self.acme, "Lee New", email="lee@acme.example")
        self.check_tool("save_draft", [
            ("UPDATE message SET state = 'drafted', subject = 'Re: x', body = 'y' WHERE message_id = ?",
             (self.planned,)),
            ("UPDATE message SET due_on = '2026-11-11' WHERE message_id = ?", (self.planned,)),
            ("UPDATE message SET body = 'his text was here' WHERE message_id = ?", (self.his,)),
            ("INSERT INTO message(touch_id, seq, state, subject, body, created_at, updated_at) "
             "VALUES (?, 0, 'approved', 'Hi', 'Hello', ?, ?)", (self.touch, NOW, NOW)),
            ("INSERT INTO message(touch_id, seq, state, due_on, created_at, updated_at) "
             "VALUES (?, 2, 'planned', '2026-11-20', ?, ?)", (self.sent_touch, NOW, NOW)),
            ("INSERT INTO touch(person_id, state, created_at, updated_at, first_sent_at, first_rfc_id, "
             "gmail_thread_id) VALUES (?, 'open', ?, ?, ?, '<r@x>', 't')", (lee, NOW, NOW, NOW)),
            ("UPDATE person SET email = 'other@acme.example' WHERE person_id = ?", (self.pat,)),
            ("INSERT INTO review_item(kind, company_id, reason, evidence_key, raised_by, created_at) "
             "VALUES ('flagged', ?, 'x', 'k4', 'claude', ?)", (self.acme, NOW)),
        ], {"person_id": lee, "subject": "Your queue rewrite", "body": BODY, "hook": "His post on the queue",
            "project": "runner"})

    def test_save_followup_cannot_write_beyond_its_grants(self):
        self.check_tool("save_followup", [
            ("INSERT INTO message(touch_id, seq, state, subject, body, created_at, updated_at) "
             "VALUES (?, 0, 'drafted', 'Hi', 'Hello', ?, ?)", (self.touch, NOW, NOW)),
            ("INSERT INTO touch(person_id, created_at, updated_at) VALUES (?, ?, ?)",
             (seed.person(self.db, self.acme, "Tia Touchless"), NOW, NOW)),
            ("UPDATE message SET due_on = '2026-11-11' WHERE message_id = ?", (self.planned,)),
            ("UPDATE message SET hook = 'x', project = 'y' WHERE message_id = ?", (self.planned,)),
            ("UPDATE message SET state = 'skipped' WHERE message_id = ?", (self.planned,)),
            ("UPDATE message SET state = 'drafted', body = 'y' WHERE message_id = ?", (self.his,)),
            ("UPDATE person SET notes = 'x' WHERE person_id = ?", (self.sam,)),
        ], {"person_id": self.sam, "seq": 1, "body": "Hi Sam, one more note on the ledger idea. Thanks, Devanshu"})

    def test_add_people_cannot_write_beyond_its_grants(self):
        self.check_tool("add_people", [
            ("UPDATE person SET email = 'new@acme.example' WHERE person_id = ?", (self.pat,)),
            ("UPDATE person SET company_id = ? WHERE person_id = ?", (seed.company(self.db, "Elsewhere"), self.pat)),
            ("UPDATE person SET added_by = 'claude' WHERE person_id = ?", (self.pat,)),
            ("UPDATE person SET role_kind = 'founder', title = 'CEO' WHERE person_id = ?", (self.pat,)),
            ("INSERT INTO person(company_id, name, role_kind, email, channel, added_by, created_at, updated_at) "
             "VALUES (?, 'Dev Anshu', 'engineer', 'dev@acme.example', 'email', 'devanshu', ?, ?)",
             (self.acme, NOW, NOW)),
            ("INSERT INTO touch(person_id, created_at, updated_at) VALUES (?, ?, ?)",
             (seed.person(self.db, self.acme, "Tia Touchless"), NOW, NOW)),
            ("INSERT INTO message(touch_id, seq, state, subject, body, created_at, updated_at) "
             "VALUES (?, 0, 'drafted', 'Hi', 'Hello', ?, ?)", (self.touch, NOW, NOW)),
            ("UPDATE person_source SET url = 'https://acme.example/x'", ()),
            ("INSERT INTO company_alias(alias_key, company_id, alias, source, created_at) "
             "VALUES ('acmeinc', ?, 'Acme Inc', 'hand', ?)", (self.acme, NOW)),
        ], {"company_id": self.acme, "people": [{"name": "Lee New", "role_kind": "engineer",
                                                 "email": "lee@acme.example",
                                                 "sources": [{"kind": "team", "url": "https://acme.example/team"}]}]})


if __name__ == "__main__":
    unittest.main()
