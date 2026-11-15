"""Migration 0019 (#9 outreach fixes ruling 2026-11-14, point 3): Send next and Send now on a message.

The store is built to version 18 with messages in every state, then migrated to the latest."""
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from outreach.store.migrate import migrate, migration_files

T = "2026-11-14T05:00:00Z"


def store_at_18(case: unittest.TestCase) -> sqlite3.Connection:
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    old = Path(tmp.name)
    for version, _name, path in migration_files():
        if version <= 18:
            shutil.copy(path, old / path.name)
    conn = sqlite3.connect(":memory:", isolation_level=None)
    case.addCleanup(conn.close)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    assert migrate(conn, old) == 18
    return conn


class SendPriorityMigrationTest(unittest.TestCase):
    def setUp(self):
        self.db = store_at_18(self)
        self.company = self.db.execute("INSERT INTO company(name, status_at, created_at) VALUES ('Acme', ?, ?)",
                                       (T, T)).lastrowid
        self.n = 0

    def message(self, state):
        self.n += 1
        person = self.db.execute(
            "INSERT INTO person(company_id, name, role_kind, email, channel, added_by, created_at, updated_at) "
            "VALUES (?, ?, 'engineer', ?, 'email', 'devanshu', ?, ?)",
            (self.company, f"P{self.n}", f"p{self.n}@acme.test", T, T)).lastrowid
        touch = self.db.execute("INSERT INTO touch(person_id, origin, created_at, updated_at) "
                                "VALUES (?, 'sender', ?, ?)", (person, T, T)).lastrowid
        if state == "sent":
            return self.db.execute(
                "INSERT INTO message(touch_id, seq, state, subject, body, sent_at, rfc_message_id, created_at, "
                "updated_at) VALUES (?, 0, 'sent', 's', 'b', ?, ?, ?, ?)", (touch, T, f"<m{self.n}@x>", T, T)).lastrowid
        mid = self.db.execute("INSERT INTO message(touch_id, seq, state, subject, body, created_at, updated_at) "
                              "VALUES (?, 0, 'drafted', 's', 'b', ?, ?)", (touch, T, T)).lastrowid
        if state == "approved":
            self.db.execute("UPDATE message SET state = 'approved', approved_at = ? WHERE message_id = ?", (T, mid))
        return mid

    def row(self, mid):
        return self.db.execute("SELECT * FROM message WHERE message_id = ?", (mid,)).fetchone()

    def test_every_existing_message_is_normal_and_kept(self):
        ids = {state: self.message(state) for state in ("drafted", "approved", "sent")}
        before = {mid: dict(self.row(mid)) for mid in ids.values()}
        migrate(self.db)
        for mid, old in before.items():
            row = dict(self.row(mid))
            self.assertEqual((row.pop("priority"), row.pop("urgent_at")), ("normal", None))
            self.assertEqual(row, old)

    def test_the_0005_triggers_survive(self):
        migrate(self.db)
        names = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' "
                                               "AND tbl_name = 'message'")}
        for name in ("message_followup_waits", "message_frozen_once_claimed", "message_insert_state",
                     "message_legal_transition", "message_no_delete_claimed", "message_no_replace_sending"):
            self.assertIn(name, names)
        mid = self.message("drafted")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "illegal message state change"):
            self.db.execute("UPDATE message SET state = 'sent' WHERE message_id = ?", (mid,))

    def test_priority_values_and_urgent_at_go_together(self):
        migrate(self.db)
        mid = self.message("approved")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE message SET priority = 'soon' WHERE message_id = ?", (mid,))
        with self.assertRaises(sqlite3.IntegrityError):  # now needs the moment he asked
            self.db.execute("UPDATE message SET priority = 'now' WHERE message_id = ?", (mid,))
        with self.assertRaises(sqlite3.IntegrityError):  # and only now has one
            self.db.execute("UPDATE message SET priority = 'next', urgent_at = ? WHERE message_id = ?", (T, mid))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE message SET priority = 'now', urgent_at = 'today' WHERE message_id = ?", (mid,))
        self.db.execute("UPDATE message SET priority = 'now', urgent_at = ? WHERE message_id = ?", (T, mid))
        self.db.execute("UPDATE message SET priority = 'next', urgent_at = NULL WHERE message_id = ?", (mid,))
        self.assertEqual(self.row(mid)["priority"], "next")

    def test_only_an_approved_message_is_sent_next_or_now(self):
        migrate(self.db)
        mid = self.message("drafted")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "only an approved message"):
            self.db.execute("UPDATE message SET priority = 'next' WHERE message_id = ?", (mid,))
        # approve and the priority in one write is how the API does it
        self.db.execute("UPDATE message SET state = 'approved', approved_at = ?, priority = 'now', urgent_at = ? "
                        "WHERE message_id = ?", (T, T, mid))
        self.assertEqual(self.row(mid)["priority"], "now")

    def test_a_sent_message_keeps_its_priority(self):
        migrate(self.db)
        mid = self.message("approved")
        self.db.execute("UPDATE message SET priority = 'now', urgent_at = ? WHERE message_id = ?", (T, mid))
        self.db.execute("UPDATE message SET state = 'sending', pending_rfc_id = '<p@x>', claimed_at = ? "
                        "WHERE message_id = ?", (T, mid))
        with self.assertRaisesRegex(sqlite3.IntegrityError, "keeps its priority"):
            self.db.execute("UPDATE message SET priority = 'normal', urgent_at = NULL WHERE message_id = ?", (mid,))

    def test_a_new_message_starts_normal(self):
        migrate(self.db)
        person = self.db.execute(
            "INSERT INTO person(company_id, name, role_kind, email, channel, added_by, created_at, updated_at) "
            "VALUES (?, 'X', 'engineer', 'x@acme.test', 'email', 'devanshu', ?, ?)", (self.company, T, T)).lastrowid
        touch = self.db.execute("INSERT INTO touch(person_id, origin, created_at, updated_at) "
                                "VALUES (?, 'sender', ?, ?)", (person, T, T)).lastrowid
        with self.assertRaisesRegex(sqlite3.IntegrityError, "starts with normal priority"):
            self.db.execute("INSERT INTO message(touch_id, seq, state, subject, body, priority, urgent_at, "
                            "created_at, updated_at) VALUES (?, 0, 'drafted', 's', 'b', 'now', ?, ?, ?)",
                            (touch, T, T, T))


if __name__ == "__main__":
    unittest.main()
