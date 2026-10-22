import sqlite3
import unittest

from outreach.install import install
from outreach.store.migrate import latest_version
from tests.storetest import fresh_store, seed_company, seed_touch

NOW = "2026-11-02T08:00:00Z"


class SendingSchemaTest(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()

    def sender(self):
        return self.db.execute("SELECT * FROM sender WHERE sender_id = 1").fetchone()

    def test_sender_has_cursor_gap_and_auth_columns(self):
        row = self.sender()
        self.assertEqual(row["history_id"], "")
        self.assertIsNone(row["next_send_at"])
        self.assertEqual(row["auth_state"], "unknown")
        self.assertIsNone(row["auth_checked_at"])
        self.assertEqual(row["auth_error"], "")
        self.assertEqual(row["account"], "")

    def test_sender_auth_state_check(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE sender SET auth_state = 'bogus' WHERE sender_id = 1")
        for state in ("unknown", "ok", "signed_out", "wrong_account"):
            with self.subTest(state=state):
                self.db.execute("UPDATE sender SET auth_state = ? WHERE sender_id = 1", (state,))
                self.assertEqual(self.sender()["auth_state"], state)

    def test_sender_times_are_canonical_utc(self):
        for column in ("next_send_at", "auth_checked_at"):
            with self.subTest(column=column):
                self.db.execute(f"UPDATE sender SET {column} = ? WHERE sender_id = 1", (NOW,))
                self.assertEqual(self.sender()[column], NOW)
                with self.assertRaises(sqlite3.IntegrityError):
                    self.db.execute(f"UPDATE sender SET {column} = '2026-11-02 08:00' WHERE sender_id = 1")

    def test_send_day_closed_at_is_canonical_utc(self):
        self.db.execute("INSERT INTO send_day(day, send_limit, created_at) VALUES ('2026-11-02', 20, ?)", (NOW,))
        self.assertIsNone(self.db.execute("SELECT closed_at FROM send_day").fetchone()[0])
        self.db.execute("UPDATE send_day SET closed_at = '2026-11-02T08:00:00Z'")
        self.assertEqual(self.db.execute("SELECT closed_at FROM send_day").fetchone()[0], NOW)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE send_day SET closed_at = '2026-11-02 08:00'")

    def test_inbound_dsn_class_values(self):
        touch = seed_touch(self.db, seed_company(self.db), "a@acme.co")

        def add(n, dsn_class):
            self.db.execute(
                "INSERT INTO inbound_message(touch_id, gmail_message_id, from_addr, kind, received_at, recorded_at, "
                "dsn_class) VALUES (?, ?, 'mailer-daemon@x.co', 'bounce', ?, ?, ?)",
                (touch, f"g{n}", NOW, NOW, dsn_class))

        add(1, "policy")
        add(2, None)
        with self.assertRaises(sqlite3.IntegrityError):
            add(3, "soft")
        for n, value in enumerate(("hard", "limit", "unknown"), start=4):
            add(n, value)
        self.assertEqual(self.db.execute("SELECT count(*) FROM inbound_message").fetchone()[0], 5)

    def test_migration_is_version_2_and_reinstall_changes_nothing(self):
        versions = [r[0] for r in self.db.execute("SELECT version FROM schema_migration ORDER BY version")]
        self.assertIn(2, versions)
        self.assertEqual(self.db.execute("PRAGMA user_version").fetchone()[0], latest_version())
        before = [tuple(r) for r in self.db.execute("SELECT * FROM schema_migration ORDER BY version")]
        path = self.db.execute("PRAGMA database_list").fetchone()["file"]
        install(path)
        install(path)
        after = [tuple(r) for r in self.db.execute("SELECT * FROM schema_migration ORDER BY version")]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
