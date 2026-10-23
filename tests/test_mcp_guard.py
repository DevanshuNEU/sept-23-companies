"""The write guard behind every MCP tool (#13, plan Task 4).

Two layers sit on every connection a tool gets: SQLite temp triggers that refuse a value Claude may never write, and
an authorizer that refuses every write the tool did not declare (and every DELETE, ATTACH, PRAGMA, CREATE and DROP).
Each hostile statement is run against each layer alone, then against the full guard. A third layer is the tool's
input model (test_mcp_reads and test_mcp_server); the foundation's `connect()` stays the store's own check.
"""
import os
import sqlite3
import unittest
from unittest import mock

import live_checkout
from outreach.mcp import guard
from outreach.mcp.guard import NEVER_COLUMNS, NEVER_TABLES, Grant, GrantError
from outreach.store import DEFAULT_PATH, StoreError, connect
from tests import apiseed as seed
from tests.storetest import fresh_store

T = "2026-11-10T15:00:00Z"
APPROVE = "UPDATE message SET state = 'approved', approved_at = '2026-11-10T15:00:00Z' WHERE message_id = ?"


def store_path(conn: sqlite3.Connection) -> str:
    return next(r["file"] for r in conn.execute("PRAGMA database_list") if r["name"] == "main")


class GuardCase(unittest.TestCase):
    """A scratch store with a company, a drafted and an approved first email, a sent first email with its planned
    follow-up, and a draft he edited himself."""

    def setUp(self):
        self.db = fresh_store()
        self.addCleanup(self.db.close)
        self.path = store_path(self.db)
        self.company = seed.company(self.db)
        self.person = seed.person(self.db, self.company)
        self.touch = seed.touch(self.db, self.person)
        self.drafted = seed.message(self.db, self.touch, 0)
        approved_person = seed.person(self.db, self.company, "Ann Approved")
        self.approved = seed.message(self.db, seed.touch(self.db, approved_person), 0, "approved")
        sent_person = seed.person(self.db, self.company, "Sam Sent")
        self.sent_touch = seed.touch(self.db, sent_person)
        self.sent = seed.send_first(self.db, self.sent_touch)
        self.planned = self.db.execute(
            "INSERT INTO message(touch_id, seq, state, due_on, created_at, updated_at) "
            "VALUES (?, 1, 'planned', '2026-11-12', ?, ?)", (self.sent_touch, T, T)).lastrowid
        his_person = seed.person(self.db, self.company, "Dee Edited")
        self.his = seed.message(self.db, seed.touch(self.db, his_person), 0)
        self.db.execute("UPDATE message SET saved_by = 'devanshu' WHERE message_id = ?", (self.his,))

    # Each layer alone, on a plain connection from the foundation.
    def triggers_only(self) -> sqlite3.Connection:
        conn = connect(self.path)
        self.addCleanup(conn.close)
        guard.install_triggers(conn)
        return conn

    def authorizer_only(self, grants=None) -> sqlite3.Connection:
        conn = connect(self.path)
        self.addCleanup(conn.close)
        guard.install_authorizer(conn, grants or {})
        return conn

    def guarded(self, grants=None) -> sqlite3.Connection:
        conn = guard.open_guarded(grants or {}, path=self.path)
        self.addCleanup(conn.close)
        return conn

    def state_of(self, message_id: int) -> str:
        return self.db.execute("SELECT state FROM message WHERE message_id = ?", (message_id,)).fetchone()[0]

    def assert_not_authorized(self, conn, sql, params=()):
        with self.assertRaises(sqlite3.DatabaseError) as caught:
            conn.execute(sql, params)
        self.assertIn("not authorized", str(caught.exception))

    def assert_trigger_refuses(self, conn, sql, params=()):
        with self.assertRaises(sqlite3.IntegrityError) as caught:
            conn.execute(sql, params)
        self.assertIn("mcp guard", str(caught.exception))

    def no_go_insert(self):
        return ("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'said_no', 'claude', ?)",
                (self.person, T))


class TriggerLayerTest(GuardCase):
    def test_a_temp_trigger_on_a_main_table_fires(self):
        conn = self.triggers_only()
        names = {r[0] for r in conn.execute("SELECT name FROM temp.sqlite_master WHERE type = 'trigger'")}
        self.assertIn("mcp_message_state", names)
        self.assert_trigger_refuses(conn, APPROVE, (self.drafted,))

    def test_approve_is_refused_by_the_trigger_alone(self):
        self.assert_trigger_refuses(self.triggers_only(), APPROVE, (self.drafted,))
        self.assertEqual(self.state_of(self.drafted), "drafted")

    def test_unapprove_skip_and_revive_are_refused_by_the_trigger_alone(self):
        conn = self.triggers_only()
        self.assert_trigger_refuses(conn, "UPDATE message SET state = 'drafted' WHERE message_id = ?",
                                    (self.approved,))
        self.assert_trigger_refuses(conn, "UPDATE message SET state = 'skipped' WHERE message_id = ?",
                                    (self.drafted,))
        self.db.execute("UPDATE message SET state = 'skipped' WHERE message_id = ?", (self.drafted,))
        self.assert_trigger_refuses(conn, "UPDATE message SET state = 'drafted' WHERE message_id = ?",
                                    (self.drafted,))

    def test_a_no_go_write_is_refused_by_the_trigger_alone(self):
        conn = self.triggers_only()
        self.assert_trigger_refuses(conn, *self.no_go_insert())
        self.db.execute("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'said_no', 'devanshu', ?)",
                        (self.person, T))
        self.assert_trigger_refuses(conn, "UPDATE no_go SET lifted_at = ?, lift_reason = 'x'", (T,))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM no_go").fetchone()[0], 1)

    def test_a_delete_is_refused_by_the_trigger_alone(self):
        conn = self.triggers_only()
        self.assert_trigger_refuses(conn, "DELETE FROM message WHERE message_id = ?", (self.drafted,))
        self.assert_trigger_refuses(conn, "DELETE FROM person WHERE person_id = ?", (self.person,))
        self.assertEqual(self.state_of(self.drafted), "drafted")

    def test_sender_company_and_touch_writes_are_refused_by_the_trigger_alone(self):
        conn = self.triggers_only()
        self.assert_trigger_refuses(conn, "UPDATE sender SET paused = 1, pause_kind = 'by_you', paused_at = ?", (T,))
        self.assert_trigger_refuses(conn, "UPDATE company SET status = 'closed', close_reason = 'other'")
        self.assert_trigger_refuses(conn, "UPDATE touch SET state = 'closed', close_reason = 'skipped', closed_at = ?",
                                    (T,))
        self.assert_trigger_refuses(conn, "INSERT INTO touch(person_id, state, created_at, updated_at) "
                                          "VALUES (?, 'replied', ?, ?)", (self.person, T, T))

    def test_message_inserts_in_any_other_state_seq_or_author_are_refused(self):
        conn = self.triggers_only()
        fresh = seed.person(self.db, self.company, "New Person")
        touch = self.db.execute("INSERT INTO touch(person_id, created_at, updated_at) VALUES (?, ?, ?)",
                                (fresh, T, T)).lastrowid
        insert = ("INSERT INTO message(touch_id, seq, state, subject, body, saved_by, created_at, updated_at) "
                  "VALUES (?, ?, ?, 'Hi', 'Hello', ?, ?, ?)")
        for seq, state, by in ((0, "planned", "claude"), (0, "drafted", "devanshu"), (0, "sent", "claude")):
            self.assert_trigger_refuses(conn, insert, (touch, seq, state, by, T, T))

    def test_a_message_he_saved_or_approved_cannot_be_edited(self):
        conn = self.triggers_only()
        self.assert_trigger_refuses(conn, "UPDATE message SET body = 'x', saved_by = 'claude' WHERE message_id = ?",
                                    (self.his,))
        self.assert_trigger_refuses(conn, "UPDATE message SET body = 'x' WHERE message_id = ?", (self.approved,))

    def test_evidence_that_is_locked_hand_confirmed_or_a_stronger_size_or_domain_is_refused(self):
        conn = self.triggers_only()
        insert = ("INSERT INTO evidence(company_id, fact, value, num_low, confidence, locked, source_kind, recorded_at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
        for fact, value, low, confidence, locked, kind in (
                ("stage", "seed", None, "estimate", 1, "hand"), ("stage", "seed", None, "estimate", 0, "hand"),
                ("stage", "seed", None, "confirmed", 0, "website"), ("size", "40", 40, "estimate", 0, "website"),
                ("domain", "acme.example", None, "estimate", 0, "website")):
            self.assert_trigger_refuses(conn, insert, (self.company, fact, value, low, confidence, locked, kind, T))
        conn.execute(insert, (self.company, "size", "40", 40, "guess", 0, "website", T))

    def test_note_confirmation_is_refused_by_the_trigger_and_by_the_foundation_check(self):
        conn = self.triggers_only()
        self.assert_trigger_refuses(conn, "INSERT INTO linkedin_draft(person_id, body, reason_state, saved_by, "
                                          "created_at, updated_at) VALUES (?, 'Hi', 'proposed', 'devanshu', ?, ?)",
                                    (self.person, T, T))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO linkedin_draft(person_id, body, reason_state, saved_by, created_at, updated_at)"
                            " VALUES (?, 'Hi', 'confirmed', 'claude', ?, ?)", (self.person, T, T))

    def test_events_and_review_items_by_another_actor_are_refused(self):
        conn = self.triggers_only()
        self.assert_trigger_refuses(conn, "INSERT INTO event(at, recorded_at, entity, kind, actor) "
                                          "VALUES (?, ?, 'company', 'x', 'devanshu')", (T, T))
        self.assert_trigger_refuses(conn, "INSERT INTO review_item(kind, company_id, reason, evidence_key, raised_by, "
                                          "created_at) VALUES ('flagged', ?, 'r', 'k', 'rules', ?)", (self.company, T))


class AuthorizerLayerTest(GuardCase):
    def test_reads_are_allowed(self):
        conn = self.authorizer_only()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM message").fetchone()[0], 5)
        conn.execute("SELECT * FROM company_fact").fetchall()
        conn.execute("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 3) SELECT * FROM n")

    def test_approve_is_refused_by_the_authorizer_alone(self):
        self.assert_not_authorized(self.authorizer_only(), APPROVE, (self.drafted,))
        self.assertEqual(self.state_of(self.drafted), "drafted")

    def test_a_no_go_write_is_refused_by_the_authorizer_alone(self):
        self.assert_not_authorized(self.authorizer_only(), *self.no_go_insert())

    def test_a_delete_is_refused_by_the_authorizer_alone_even_with_every_grant(self):
        grants = {"message": Grant(insert=True, update=frozenset({"subject", "body"}))}
        self.assert_not_authorized(self.authorizer_only(grants), "DELETE FROM message WHERE message_id = ?",
                                   (self.drafted,))

    def test_attach_is_refused_by_the_authorizer_alone(self):
        self.assert_not_authorized(self.authorizer_only(), "ATTACH DATABASE ':memory:' AS other")

    def test_pragma_is_refused_by_the_authorizer_alone(self):
        conn = self.authorizer_only()
        for pragma in ("PRAGMA foreign_keys = OFF", "PRAGMA writable_schema = ON", "PRAGMA recursive_triggers = OFF",
                       "PRAGMA user_version = 1", "PRAGMA table_info(message)"):
            self.assert_not_authorized(conn, pragma)

    def test_create_drop_and_alter_are_refused_by_the_authorizer_alone(self):
        conn = self.authorizer_only()
        for sql in ("CREATE TABLE x(a)", "CREATE TEMP TABLE x(a)", "CREATE INDEX i ON message(body)",
                    "CREATE TEMP TRIGGER t BEFORE INSERT ON main.message BEGIN SELECT 1; END",
                    "DROP TRIGGER message_legal_transition", "DROP VIEW company_fact", "DROP TABLE mx_check",
                    "ALTER TABLE message ADD COLUMN x TEXT", "REINDEX", "ANALYZE"):
            self.assert_not_authorized(conn, sql)

    def test_a_write_the_tool_did_not_declare_is_refused(self):
        conn = self.authorizer_only({"message": Grant(update=frozenset({"body"}))})
        conn.execute("UPDATE message SET body = 'new' WHERE message_id = ?", (self.drafted,))
        self.assert_not_authorized(conn, "UPDATE message SET subject = 'x' WHERE message_id = ?", (self.drafted,))
        self.assert_not_authorized(conn, "INSERT INTO event(at, recorded_at, entity, kind, actor) "
                                         "VALUES (?, ?, 'company', 'x', 'claude')", (T, T))
        self.assert_not_authorized(conn, "UPDATE person SET notes = 'x'")


class FullGuardTest(GuardCase):
    def test_every_hostile_statement_is_refused_and_the_store_is_unchanged(self):
        before = [tuple(r) for r in self.db.execute("SELECT * FROM message ORDER BY message_id")]
        conn = self.guarded()
        for sql, params in ((APPROVE, (self.drafted,)), self.no_go_insert(),
                            ("DELETE FROM message WHERE message_id = ?", (self.drafted,)),
                            ("ATTACH DATABASE ':memory:' AS other", ()), ("PRAGMA foreign_keys = OFF", ()),
                            ("UPDATE sender SET paused = 1, pause_kind = 'by_you', paused_at = ?", (T,))):
            with self.assertRaises(sqlite3.DatabaseError, msg=sql):
                conn.execute(sql, params)
        self.assertEqual([tuple(r) for r in self.db.execute("SELECT * FROM message ORDER BY message_id")], before)

    def test_the_guards_own_triggers_cannot_be_dropped(self):
        conn = self.guarded()
        names = [r[0] for r in conn.execute("SELECT name FROM temp.sqlite_master WHERE type = 'trigger'")]
        self.assertGreater(len(names), 20)
        self.assertTrue(all(n.startswith("mcp_") for n in names))
        for name in ("mcp_message_state", "mcp_message_insert", names[-1]):
            self.assert_not_authorized(conn, f"DROP TRIGGER temp.{name}")
            self.assert_not_authorized(conn, f"DROP TRIGGER {name}")

    def test_approve_is_refused_by_the_trigger_even_with_a_state_grant(self):
        conn = self.guarded({"message": Grant(update=frozenset({"state"}))})
        self.assert_trigger_refuses(conn, "UPDATE message SET state = 'approved' WHERE message_id = ?",
                                    (self.drafted,))
        self.assert_trigger_refuses(conn, "UPDATE message SET state = 'skipped' WHERE message_id = ?",
                                    (self.planned,))

    def test_a_grant_may_not_name_a_never_table_or_column(self):
        for table in sorted(NEVER_TABLES):
            with self.assertRaises(GrantError, msg=table):
                guard.open_guarded({table: Grant(insert=True)}, path=self.path)
        for table, columns in NEVER_COLUMNS.items():
            for column in sorted(columns or {"state", "first_sent_at", "updated_at"}):
                with self.assertRaises(GrantError, msg=f"{table}.{column}"):
                    guard.open_guarded({table: Grant(update=frozenset({column}))}, path=self.path)
        self.assertIn("no_go", NEVER_TABLES)
        self.assertIn("approved_at", NEVER_COLUMNS["message"])

    def test_granted_writes_work(self):
        fresh = seed.person(self.db, self.company, "New Person")
        conn = self.guarded({"touch": Grant(insert=True), "message": Grant(insert=True, update=frozenset({"body"})),
                             "event": Grant(insert=True)})
        touch = conn.execute("INSERT INTO touch(person_id, created_at, updated_at) VALUES (?, ?, ?)",
                             (fresh, T, T)).lastrowid
        message = conn.execute(
            "INSERT INTO message(touch_id, seq, state, subject, body, created_at, updated_at) "
            "VALUES (?, 0, 'drafted', 'Hi', 'Hello', ?, ?)", (touch, T, T)).lastrowid
        conn.execute("UPDATE message SET body = 'Hello again' WHERE message_id = ?", (message,))
        conn.execute("INSERT INTO event(at, recorded_at, entity, entity_id, kind, actor) "
                     "VALUES (?, ?, 'message', ?, 'drafted', 'claude')", (T, T, message))
        self.assertEqual(tuple(self.db.execute("SELECT body, saved_by FROM message WHERE message_id = ?",
                                               (message,)).fetchone()), ("Hello again", "claude"))

    def test_a_planned_follow_up_may_become_drafted(self):
        conn = self.guarded({"message": Grant(update=frozenset({"state", "subject", "body"}))})
        conn.execute("UPDATE message SET state = 'drafted', subject = 'Re: Hello', body = 'Following up' "
                     "WHERE message_id = ?", (self.planned,))
        self.assertEqual(self.state_of(self.planned), "drafted")

    def test_guard_does_not_change_what_connect_refuses(self):
        missing = os.path.join(os.path.dirname(self.path), "nothing-here", "outreach.db")
        with self.assertRaises(StoreError) as caught:
            guard.open_guarded({}, path=missing)
        self.assertIn("outreach.install", str(caught.exception))
        self.db.execute("PRAGMA user_version = 999")
        with self.assertRaises(StoreError) as caught:
            guard.open_guarded({}, path=self.path)
        self.assertIn("newer", str(caught.exception))

    def test_the_live_store_is_refused_outside_the_live_checkout_before_sqlite_is_reached(self):
        reached = mock.Mock(side_effect=AssertionError("sqlite3.connect was reached"))
        with mock.patch.object(live_checkout, "is_live", return_value=False), \
                mock.patch("outreach.store.db.sqlite3.connect", reached):
            for spelling in (DEFAULT_PATH, DEFAULT_PATH.lower()):
                with self.assertRaises(SystemExit) as caught:
                    guard.open_guarded({}, path=spelling)
                self.assertIn("live checkout", str(caught.exception))
                with self.assertRaises(SystemExit):
                    guard.check_store(spelling)
        reached.assert_not_called()

    def test_check_store_returns_the_normalised_path_and_closes(self):
        self.assertEqual(guard.check_store(self.path), os.path.realpath(self.path))


if __name__ == "__main__":
    unittest.main()
