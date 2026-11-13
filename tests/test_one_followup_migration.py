"""Migration 0018 (#9 multi-sender ruling 2026-11-12): one follow-up, 7 business days after the first email.

The store is built to version 17, seeded as the four-follow-up store was, and then migrated to the latest."""
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from outreach.sending.config import load_config
from outreach.sending.followups import sync_followups
from outreach.store.migrate import migrate, migration_files

T = "2026-11-12T05:00:00Z"
DAY = "2026-11-20"


def store_at_17(case: unittest.TestCase) -> sqlite3.Connection:
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    old = Path(tmp.name)
    for version, _name, path in migration_files():
        if version <= 17:
            shutil.copy(path, old / path.name)
    conn = sqlite3.connect(":memory:", isolation_level=None)
    case.addCleanup(conn.close)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    assert migrate(conn, old) == 17
    return conn


class OneFollowupMigrationTest(unittest.TestCase):
    def setUp(self):
        self.db = store_at_17(self)
        for account_id, address in ((1, "a@gmail.com"), (2, "b@northeastern.edu")):
            self.db.execute("INSERT INTO sender_account(account_id, address, kind, created_at, updated_at) "
                            "VALUES (?, ?, 'gmail', ?, ?)", (account_id, address, T, T))
        self.company = self.db.execute("INSERT INTO company(name, status_at, created_at) VALUES ('Acme', ?, ?)",
                                       (T, T)).lastrowid

    def touch(self, account_id, n):
        person = self.db.execute(
            "INSERT INTO person(company_id, name, role_kind, email, channel, added_by, created_at, updated_at) "
            "VALUES (?, ?, 'engineer', ?, 'email', 'devanshu', ?, ?)",
            (self.company, f"P{n}", f"p{n}@acme.test", T, T)).lastrowid
        return self.db.execute(
            "INSERT INTO touch(person_id, origin, first_sent_at, first_rfc_id, gmail_thread_id, account_id, "
            "created_at, updated_at) VALUES (?, 'sender', ?, ?, ?, ?, ?, ?)",
            (person, T, f"<r{n}@x>", f"th{n}", account_id, T, T)).lastrowid

    def message(self, touch_id, seq, state):
        if state == "sent":
            return self.db.execute(
                "INSERT INTO message(touch_id, seq, state, subject, body, due_on, sent_at, rfc_message_id, created_at, "
                "updated_at) VALUES (?, ?, 'sent', 's', 'b', ?, ?, ?, ?, ?)",
                (touch_id, seq, DAY if seq else None, T, f"<m{touch_id}.{seq}@x>", T, T)).lastrowid
        mid = self.db.execute(
            "INSERT INTO message(touch_id, seq, state, due_on, created_at, updated_at) "
            "VALUES (?, ?, 'planned', ?, ?, ?)", (touch_id, seq, DAY, T, T)).lastrowid
        if state in ("drafted", "approved"):
            self.db.execute("UPDATE message SET state = 'drafted', subject = 's', body = 'b' WHERE message_id = ?",
                            (mid,))
        if state == "approved":
            self.db.execute("UPDATE message SET state = 'approved', approved_at = ? WHERE message_id = ?", (T, mid))
        return mid

    def state(self, mid):
        return self.db.execute("SELECT state, revision FROM message WHERE message_id = ?", (mid,)).fetchone()

    def test_seq2_to_4_skipped(self):
        t = self.touch(2, 1)
        ids = [self.message(t, 2, "planned"), self.message(t, 3, "drafted"), self.message(t, 4, "approved")]
        keep = self.message(t, 1, "planned")
        migrate(self.db)
        for mid in ids:
            self.assertEqual(tuple(self.state(mid)), ("skipped", 2))
        self.assertEqual(tuple(self.state(keep)), ("planned", 1))

    def test_resting_account_followups_skipped(self):
        t1, t2 = self.touch(1, 1), self.touch(2, 2)
        a = self.message(t1, 1, "approved")
        b = self.message(t2, 1, "planned")
        migrate(self.db)
        self.assertEqual(self.state(a)["state"], "skipped")
        self.assertEqual(self.state(b)["state"], "planned")

    def test_sent_untouched(self):
        t = self.touch(1, 1)
        sent = self.message(t, 2, "sent")
        migrate(self.db)
        self.assertEqual(tuple(self.state(sent)), ("sent", 1))

    def test_event_written_per_message(self):
        t = self.touch(1, 1)
        ids = [self.message(t, 1, "planned"), self.message(t, 2, "drafted")]
        migrate(self.db)
        for mid in ids:
            rows = self.db.execute("SELECT * FROM event WHERE entity = 'message' AND entity_id = ?", (mid,)).fetchall()
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual((row["kind"], row["company_id"]), ("skipped", self.company))
            self.assertEqual(json.loads(row["detail"]),
                             {"reason": "rule_changed", "ruling": "multi-sender 2026-11-12"})

    def test_one_sync_after_the_migration_plans_nothing_for_the_skipped_follow_ups(self):
        # A skipped row keeps its (touch, seq) slot, so the shipped one-follow-up schedule brings none of them back.
        resting, sending = self.touch(1, 1), self.touch(2, 2)
        skipped = [self.message(resting, seq, "planned") for seq in (1, 2, 3, 4)]
        skipped += [self.message(sending, seq, "drafted") for seq in (2, 3, 4)]
        kept = self.message(sending, 1, "planned")
        migrate(self.db)
        before = self.db.execute("SELECT count(*) FROM message").fetchone()[0]
        sync_followups(self.db, load_config(), now="2026-11-13T15:00:00Z")
        self.assertEqual(self.db.execute("SELECT count(*) FROM message").fetchone()[0], before)
        self.assertEqual(self.db.execute("SELECT count(*) FROM event WHERE kind = 'followup_planned'").fetchone()[0], 0)
        for mid in skipped:
            self.assertEqual(self.state(mid)["state"], "skipped")
        self.assertEqual(self.state(kept)["state"], "planned")

    def test_shipped_config_is_one_followup(self):
        self.assertEqual(load_config().followup_business_days, (7,))


if __name__ == "__main__":
    unittest.main()
