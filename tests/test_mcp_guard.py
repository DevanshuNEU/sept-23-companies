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
    def triggers_only(self, grants=None) -> sqlite3.Connection:
        conn = connect(self.path)
        self.addCleanup(conn.close)
        guard.install_triggers(conn, grants or {})
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

    def test_only_claude_may_be_the_actor_or_raise_an_item_until_reader_tools_exist(self):
        conn = self.triggers_only()
        self.assert_trigger_refuses(conn, "INSERT INTO event(at, recorded_at, entity, kind, actor) "
                                          "VALUES (?, ?, 'reading', 'x', 'reader')", (T, T))
        self.assert_trigger_refuses(conn, "INSERT INTO review_item(kind, company_id, reason, evidence_key, raised_by, "
                                          "created_at) VALUES ('flagged', ?, 'r', 'k', 'reader', ?)", (self.company, T))
        conn.execute("INSERT INTO event(at, recorded_at, entity, kind, actor) VALUES (?, ?, 'company', 'x', 'claude')",
                     (T, T))
        conn.execute("INSERT INTO review_item(kind, company_id, reason, evidence_key, raised_by, created_at) "
                     "VALUES ('flagged', ?, 'r', 'k', 'claude', ?)", (self.company, T))


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


BRIDGE_GRANTS = {"linkedin_log": Grant(insert=True), "outreach_touch": Grant(insert=True),
                 "event": Grant(insert=True)}
UNDONE_AT = "2026-11-09T15:00:00Z"
OLD_AT = "2026-11-01T15:00:00Z"
CONTEXT = ('{"lane":"own","rankerVersion":null,"roleAgeDays":null,"roleKind":"engineer","roleNamed":false,'
           '"signals":{},"source":"own"}')
LOG = ("INSERT INTO linkedin_log(person_id, kind, reply_kind, undoes, at, note, recorded_at) "
       "VALUES (?, ?, ?, ?, ?, ?, ?)")
TOUCH = ("INSERT INTO outreach_touch(person_id, company_id, channel, email_touch_id, sent_at, accepted_at, replied_at, "
         "reply_kind, referred_at, interview_at, offer_at, quiet_at, undone_at, context, idempotency_key, created_at) "
         "VALUES (:person, :company, :channel, :email_touch, :sent, :accepted, :replied, :reply_kind, :referred, "
         ":interview, :offer, :quiet, :undone, :context, :key, :created)")
TOUCH_REVISION = ("INSERT INTO outreach_touch(person_id, company_id, channel, sent_at, context, idempotency_key, "
                  "created_at, revision) VALUES (?, ?, 'linkedin_request', ?, ?, ?, ?, ?)")


class BridgeGuardTest(GuardCase):
    """The one widening (#9, company-first ruling 2026-10-26): a new LinkedIn request Devanshu says he sent, as one
    `linkedin_log` `request_sent` row and its `linkedin_request` touch under a `bridge:` key. Nothing else on those two
    tables: no accept, reply, referral, interview, message, undo, email touch, update or delete."""

    def setUp(self):
        super().setUp()
        self.lin = seed.person(self.db, self.company, "Lin Kedin", channel="linkedin")
        self.db.execute(LOG, (self.lin, "request_sent", None, None, "2026-11-08T15:00:00Z", "", "2026-11-08T15:00:00Z"))
        self.old_log = self.db.execute("SELECT max(entry_id) FROM linkedin_log").fetchone()[0]
        self.old_touch = self.db.execute(TOUCH, self.touch_row(person=self.lin, sent="2026-11-08T15:00:00Z",
                                                               created="2026-11-08T15:00:00Z",
                                                               key="linkedin:seed")).lastrowid
        # A request he logged on 2026-11-09 and took back on the LinkedIn page: its log row and the undo stay.
        self.db.execute(LOG, (self.lin, "request_sent", None, None, UNDONE_AT, "", UNDONE_AT))
        undone = self.db.execute("SELECT max(entry_id) FROM linkedin_log").fetchone()[0]
        self.db.execute(LOG, (self.lin, "undo", None, undone, UNDONE_AT, "", UNDONE_AT))
        # An imported request from 2026-11-01 with no touch: its log row is live, but it is 9 days old.
        self.db.execute(LOG, (self.lin, "request_sent", None, None, OLD_AT, "", OLD_AT))
        self.elsewhere = seed.company(self.db, "Elsewhere")

    def touch_row(self, **over) -> dict:
        row = {"person": self.lin, "company": self.company, "channel": "linkedin_request", "email_touch": None,
               "sent": T, "accepted": None, "replied": None, "reply_kind": None, "referred": None, "interview": None,
               "offer": None, "quiet": None, "undone": None, "context": CONTEXT, "key": f"bridge:{self.lin}:x",
               "created": T}
        row.update(over)
        return row

    def request_log(self, at=T, note="Hi Lin", recorded=T):
        return (LOG, (self.lin, "request_sent", None, None, at, note, recorded))

    def hostile_logs(self) -> list[tuple[str, tuple]]:
        return [
            (LOG, (self.lin, "accepted", None, None, T, "", T)),
            (LOG, (self.lin, "replied", "interested", None, T, "", T)),
            (LOG, (self.lin, "replied", "said_no", None, T, "", T)),
            (LOG, (self.lin, "message_sent", None, None, T, "Thanks", T)),
            (LOG, (self.lin, "note", None, None, T, "x", T)),
            (LOG, (self.lin, "undo", None, self.old_log, T, "", T)),
            self.request_log(note="x" * 301),
            self.request_log(at="2026-11-05T14:00:00Z"),   # more than 5 days before it is recorded
            self.request_log(at="2026-11-10T16:00:00Z"),   # after it is recorded
        ]

    def hostile_touches(self) -> list[tuple[str, dict]]:
        email_touch = self.touch  # the email thread of self.person
        return [(TOUCH, self.touch_row(**over)) for over in (
            {"channel": "email", "email_touch": email_touch}, {"channel": "email"}, {"channel": "linkedin_message"},
            {"channel": "application", "person": None}, {"channel": "other"}, {"channel": "hn_reply"},
            {"accepted": T}, {"replied": T, "reply_kind": "interested"}, {"referred": T}, {"interview": T},
            {"offer": T}, {"quiet": T}, {"undone": T}, {"key": "linkedin:not-the-bridge"}, {"key": None},
            {"sent": "2026-11-10T14:00:00Z"},  # no request_sent log row at this time: a touch never comes alone
            {"sent": UNDONE_AT, "created": "2026-11-10T15:00:00Z"},  # its log row was taken back
            {"sent": OLD_AT},  # a live log row, but the touch is recorded 9 days after it was sent
            {"sent": "2026-11-08T15:00:00Z", "key": "bridge:dup"},  # that log row already has its live touch
            {"company": self.elsewhere},  # not the person's company
        )] + [(TOUCH_REVISION, (self.lin, self.company, T, CONTEXT, "bridge:rev", T, 2))]

    def test_a_valid_backdate_across_the_clock_change_passes_the_trigger(self):
        conn = self.triggers_only(BRIDGE_GRANTS)
        sent, recorded = "2026-10-31T04:30:00Z", "2026-11-04T04:59:00Z"  # 3 New York days, 4.02 julian days
        conn.execute(*self.request_log(at=sent, recorded=recorded))
        conn.execute(TOUCH, self.touch_row(sent=sent, created=recorded))

    def test_the_touch_goes_to_the_end_of_the_persons_merge_chain(self):
        old = seed.company(self.db, "Acme Old")
        moved = seed.person(self.db, old, "Mo Moved", channel="linkedin")
        self.db.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                        "WHERE company_id = ?", (self.company, old))
        conn = self.triggers_only(BRIDGE_GRANTS)
        conn.execute(LOG, (moved, "request_sent", None, None, T, "", T))
        self.assert_trigger_refuses(conn, TOUCH, self.touch_row(person=moved, company=old, key="bridge:old"))
        self.assert_trigger_refuses(conn, TOUCH, self.touch_row(person=moved, company=self.elsewhere,
                                                                 key="bridge:else"))
        conn.execute(TOUCH, self.touch_row(person=moved, company=self.company, key="bridge:survivor"))

    def test_the_two_tables_stay_never_written_for_a_tool_with_no_insert_grant(self):
        bare = self.triggers_only()
        self.assert_trigger_refuses(bare, *self.request_log())
        self.assert_trigger_refuses(bare, TOUCH, self.touch_row())
        only_log = self.triggers_only({"linkedin_log": Grant(insert=True)})
        only_log.execute(*self.request_log())
        self.assert_trigger_refuses(only_log, TOUCH, self.touch_row())

    def test_insert_or_replace_of_a_log_row_is_refused_without_the_recursive_triggers_pragma(self):
        conn = connect(self.path)
        self.addCleanup(conn.close)
        conn.execute("PRAGMA recursive_triggers = OFF")
        guard.install_triggers(conn, BRIDGE_GRANTS)
        self.assert_trigger_refuses(conn, "INSERT OR REPLACE INTO linkedin_log(entry_id, person_id, kind, at, note, "
                                          "recorded_at) VALUES (?, ?, 'request_sent', ?, 'x', ?)",
                                    (self.old_log, self.lin, T, T))
        self.assertEqual(self.db.execute("SELECT note FROM linkedin_log WHERE entry_id = ?",
                                         (self.old_log,)).fetchone()[0], "")
        guarded = self.guarded(BRIDGE_GRANTS)
        with self.assertRaises(sqlite3.DatabaseError):
            guarded.execute("INSERT OR REPLACE INTO linkedin_log(entry_id, person_id, kind, at, note, recorded_at) "
                            "VALUES (?, ?, 'request_sent', ?, 'x', ?)", (self.old_log, self.lin, T, T))

    def changes(self) -> list[tuple[str, tuple]]:
        return [
            ("UPDATE outreach_touch SET replied_at = ?, reply_kind = 'interested' WHERE touch_id = ?",
             (T, self.old_touch)),
            ("UPDATE outreach_touch SET interview_at = ? WHERE touch_id = ?", (T, self.old_touch)),
            ("UPDATE outreach_touch SET undone_at = ? WHERE touch_id = ?", (T, self.old_touch)),
            ("UPDATE outreach_touch SET idempotency_key = 'bridge:moved' WHERE touch_id = ?", (self.old_touch,)),
            ("UPDATE linkedin_log SET note = 'changed' WHERE entry_id = ?", (self.old_log,)),
            ("DELETE FROM outreach_touch WHERE touch_id = ?", (self.old_touch,)),
            ("DELETE FROM linkedin_log WHERE entry_id = ?", (self.old_log,)),
        ]

    def write_request(self, conn) -> int:
        conn.execute(*self.request_log())
        return conn.execute(TOUCH, self.touch_row()).lastrowid

    def test_the_two_tables_left_the_never_list_and_no_grant_may_update_them(self):
        self.assertNotIn("linkedin_log", NEVER_TABLES)
        self.assertNotIn("outreach_touch", NEVER_TABLES)
        self.assertIsNone(NEVER_COLUMNS["linkedin_log"])
        self.assertIsNone(NEVER_COLUMNS["outreach_touch"])
        for table, column in (("outreach_touch", "replied_at"), ("outreach_touch", "undone_at"),
                              ("outreach_touch", "idempotency_key"), ("linkedin_log", "note")):
            with self.assertRaises(GrantError, msg=f"{table}.{column}"):
                guard.check_grants({table: Grant(insert=True, update=frozenset({column}))})

    def test_a_request_and_its_touch_pass_the_trigger_alone(self):
        touch = self.write_request(self.triggers_only(BRIDGE_GRANTS))
        row = self.db.execute("SELECT channel FROM outreach_touch WHERE touch_id = ?", (touch,)).fetchone()
        self.assertEqual(row[0], "linkedin_request")

    def test_an_accept_reply_message_undo_long_or_misdated_log_is_refused_by_the_trigger_alone(self):
        conn = self.triggers_only(BRIDGE_GRANTS)
        for sql, params in self.hostile_logs():
            with self.subTest(params=params[1:]):
                self.assert_trigger_refuses(conn, sql, params)
        self.assertEqual(self.db.execute("SELECT count(*) FROM linkedin_log").fetchone()[0], 4)

    def test_any_touch_but_a_paired_bridge_request_is_refused_by_the_trigger_alone(self):
        conn = self.triggers_only(BRIDGE_GRANTS)
        conn.execute(*self.request_log())  # the log row a real request has: each touch below still fails alone
        for sql, params in self.hostile_touches():
            with self.subTest(params=str(params)[:160]):
                self.assert_trigger_refuses(conn, sql, params)
        self.assertEqual(self.db.execute("SELECT count(*) FROM outreach_touch").fetchone()[0], 1)

    def test_an_update_or_delete_of_a_touch_or_log_is_refused_by_the_trigger_alone(self):
        conn = self.triggers_only(BRIDGE_GRANTS)
        for sql, params in self.changes():
            with self.subTest(sql=sql):
                self.assert_trigger_refuses(conn, sql, params)

    def test_the_authorizer_alone_allows_only_inserts_with_the_bridge_grants(self):
        conn = self.authorizer_only(BRIDGE_GRANTS)
        for sql, params in self.changes():
            with self.subTest(sql=sql):
                self.assert_not_authorized(conn, sql, params)
        self.write_request(conn)
        bare = self.authorizer_only()
        self.assert_not_authorized(bare, *self.request_log(at="2026-11-10T14:30:00Z"))
        self.assert_not_authorized(bare, TOUCH, self.touch_row(key="bridge:other"))

    def test_the_full_guard_refuses_everything_else_and_the_request_lands(self):
        conn = self.guarded(BRIDGE_GRANTS)
        conn.execute(*self.request_log(note="first"))  # the log row a real request has: each touch still fails alone
        statements = self.hostile_logs() + self.changes() + self.hostile_touches()
        before = (self.db.execute("SELECT count(*) FROM linkedin_log").fetchone()[0],
                  self.db.execute("SELECT count(*) FROM outreach_touch").fetchone()[0])
        for sql, params in statements:
            with self.subTest(sql=sql, params=params):
                with self.assertRaises(sqlite3.DatabaseError):
                    conn.execute(sql, params)
        self.assertEqual((self.db.execute("SELECT count(*) FROM linkedin_log").fetchone()[0],
                          self.db.execute("SELECT count(*) FROM outreach_touch").fetchone()[0]), before)
        self.write_request(conn)
        self.assertEqual(self.db.execute("SELECT count(*) FROM outreach_touch").fetchone()[0], before[1] + 1)


if __name__ == "__main__":
    unittest.main()
