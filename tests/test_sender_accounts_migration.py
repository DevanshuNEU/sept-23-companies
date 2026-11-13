"""Migration 0017 (#9 multi-sender design): sender accounts, per-account send days, company pins, bounced addresses.

The store is built to version 16, seeded as the single-sender store was, and then migrated to the latest."""
import dataclasses
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from outreach.sending import accounts
from outreach.sending.config import AccountConfig, load_config
from outreach.store.migrate import migrate, migration_files
from tests.mcptest import store_path
from tests.storetest import fresh_store
from outreach.mcp import guard

T = "2026-11-12T05:00:00Z"
GMAIL = "devanshurajesh@gmail.com"


def store_at_16(case: unittest.TestCase) -> sqlite3.Connection:
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    old = Path(tmp.name)
    for version, _name, path in migration_files():
        if version <= 16:
            shutil.copy(path, old / path.name)
    conn = sqlite3.connect(":memory:", isolation_level=None)
    case.addCleanup(conn.close)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    assert migrate(conn, old) == 16
    return conn


def add_person(conn, company_id, email, name="P"):
    channel = "email" if email else "linkedin"
    return conn.execute(
        "INSERT INTO person(company_id, name, role_kind, email, channel, linkedin_reason, added_by, created_at, "
        "updated_at) VALUES (?, ?, 'engineer', ?, ?, ?, 'devanshu', ?, ?)",
        (company_id, name, email, channel, None if email else "no_email", T, T)).lastrowid


def add_company(conn, name):
    return conn.execute("INSERT INTO company(name, status_at, created_at) VALUES (?, ?, ?)", (name, T, T)).lastrowid


class SenderAccountsMigrationTest(unittest.TestCase):
    def setUp(self):
        self.db = store_at_16(self)

    def migrate(self):
        migrate(self.db)

    def seed_sender(self):
        self.db.execute("UPDATE sender SET account = ?, history_id = '268958' WHERE sender_id = 1", (GMAIL,))

    def test_a_store_at_master_version_16_applies_17_and_18(self):
        self.assertEqual(self.db.execute("PRAGMA user_version").fetchone()[0], 16)
        self.assertEqual([r[0] for r in self.db.execute("SELECT version FROM schema_migration ORDER BY version")][-2:], [15, 16])
        self.assertEqual(migrate(self.db), 18)
        rows = self.db.execute("SELECT version, name FROM schema_migration WHERE version > 16 ORDER BY version").fetchall()
        self.assertEqual([(r[0], r[1]) for r in rows], [(17, "sender_accounts"), (18, "one_followup")])
        self.assertEqual(migrate(self.db), 18)

    def test_account_one_copies_sender_row(self):
        self.seed_sender()
        self.migrate()
        row = self.db.execute("SELECT * FROM sender_account").fetchall()
        self.assertEqual(len(row), 1)
        row = row[0]
        self.assertEqual((row["account_id"], row["address"], row["history_id"], row["kind"]),
                         (1, GMAIL, "268958", "gmail"))
        self.assertEqual(row["paused"], 0)
        self.assertEqual(self.db.execute("SELECT sender_id FROM sender").fetchone()[0], 1)

    def test_no_account_row_when_the_sender_never_signed_in(self):
        self.migrate()
        self.assertEqual(self.db.execute("SELECT count(*) FROM sender_account").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM send_day").fetchone()[0], 0)

    def test_send_day_keyed_by_account(self):
        self.seed_sender()
        self.db.execute("INSERT INTO send_day(day, send_limit, outcome, outcome_note, created_at, closed_at) "
                        "VALUES ('2026-11-12', 150, 'warning', 'bounce rate', ?, ?)", (T, T))
        self.migrate()
        rows = [tuple(r) for r in self.db.execute(
            "SELECT account_id, day, send_limit, outcome, outcome_note, created_at, closed_at FROM send_day")]
        self.assertEqual(rows, [(1, "2026-11-12", 150, "warning", "bounce rate", T, T)])
        self.db.execute("INSERT INTO sender_account(account_id, address, kind, created_at, updated_at) "
                        "VALUES (2, 'me@northeastern.edu', 'mailapp', ?, ?)", (T, T))
        self.db.execute("INSERT INTO send_day(account_id, day, send_limit, created_at) "
                        "VALUES (2, '2026-11-12', 15, ?)", (T,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO send_day(account_id, day, send_limit, created_at) "
                            "VALUES (1, '2026-11-12', 20, ?)", (T,))

    def test_sent_touches_and_companies_pinned_to_one(self):
        self.seed_sender()
        sent_co, open_co = add_company(self.db, "Sent Co"), add_company(self.db, "Open Co")
        sent_p, open_p = add_person(self.db, sent_co, "s@sent.co"), add_person(self.db, open_co, "o@open.co")
        sent = self.db.execute("INSERT INTO touch(person_id, created_at, updated_at, first_sent_at, first_rfc_id, gmail_thread_id) "
                               "VALUES (?, ?, ?, ?, '<a@sent.co>', 'th1')", (sent_p, T, T, T)).lastrowid
        unsent = self.db.execute("INSERT INTO touch(person_id, created_at, updated_at) VALUES (?, ?, ?)",
                                 (open_p, T, T)).lastrowid
        self.migrate()
        acct = lambda t: self.db.execute("SELECT account_id FROM touch WHERE touch_id = ?", (t,)).fetchone()[0]
        self.assertEqual(acct(sent), 1)
        self.assertIsNone(acct(unsent))
        pins = [tuple(r) for r in self.db.execute("SELECT company_id, account_id, pinned_by FROM company_sender")]
        self.assertEqual(pins, [(sent_co, 1, "import")])

    def imported(self, name, *, sent_at=None, thread=None, message_state=None):
        co = add_company(self.db, name)
        person = add_person(self.db, co, f"x@{name.lower().replace(' ', '')}.co")
        touch = self.db.execute(
            "INSERT INTO touch(person_id, origin, first_sent_at, gmail_thread_id, created_at, updated_at) "
            "VALUES (?, 'import', ?, ?, ?, ?)", (person, sent_at, thread, T, T)).lastrowid
        if message_state == "drafted":
            self.db.execute("INSERT INTO message(touch_id, seq, state, subject, body, saved_by, created_at, "
                            "updated_at) VALUES (?, 0, 'drafted', 's', 'b', 'import', ?, ?)", (touch, T, T))
        elif message_state is not None:
            self.db.execute("INSERT INTO message(touch_id, seq, state, subject, body, saved_by, rfc_message_id, "
                            "sent_at, created_at, updated_at) VALUES (?, 0, 'sent', 's', 'b', 'import', ?, ?, ?, ?)",
                            (touch, f"<{touch}@x>", T, T, T))
        return co, touch

    def test_an_imported_touch_that_never_sent_keeps_no_account_and_routes_by_lane(self):
        # The importer puts the old mailer's unsent approvals on such a touch as a seq 0 draft: it must route by lane,
        # not to the resting account 1 (final review, item 1).
        self.seed_sender()
        unsent_co, unsent = self.imported("Unsent Co", message_state="drafted")
        sent_co, sent = self.imported("Sent Co", sent_at=T)
        thread_co, threaded = self.imported("Thread Co", thread="th9")
        msg_co, by_message = self.imported("Message Co", message_state="sent")
        self.migrate()
        acct = lambda t: self.db.execute("SELECT account_id FROM touch WHERE touch_id = ?", (t,)).fetchone()[0]
        self.assertIsNone(acct(unsent))
        self.assertEqual((acct(sent), acct(threaded), acct(by_message)), (1, 1, 1))
        pins = {r[0] for r in self.db.execute("SELECT company_id FROM company_sender")}
        self.assertEqual(pins, {sent_co, thread_co, msg_co})
        general = "devanshuchi9@gmail.com"
        cfg = dataclasses.replace(load_config(), accounts=(
            AccountConfig(GMAIL, "gmail", None, False, 15, 3, 15, 100, None, None),
            AccountConfig(general, "gmail", "general", True, 15, 3, 15, 100, None, None)))
        rows = accounts.sync(self.db, cfg, T)
        found, why = accounts.route(self.db, cfg, unsent, rows)
        self.assertEqual((found.cfg.address, why), (general, "lane"))
        self.assertEqual(accounts.route(self.db, cfg, sent, rows)[1], "thread")

    def test_bounced_addresses_backfilled(self):
        co = add_company(self.db, "Bounce Co")
        a = add_person(self.db, co, "a@x.com", "A")
        self.db.execute("INSERT INTO touch(person_id, created_at, updated_at, state, bounced_at) "
                        "VALUES (?, ?, ?, 'bounced', ?)", (a, T, T, T))
        b = add_person(self.db, co, None, "B")
        # The shape `mark_email_bounced` writes through record_edit: {"email": {"old": ..., "new": ...}, "reason": ...}
        self.db.execute("INSERT INTO event(at, recorded_at, entity, entity_id, company_id, kind, actor, detail) "
                        "VALUES (?, ?, 'person', ?, ?, 'email_bounced', 'claude', ?)",
                        (T, T, b, co, json.dumps({"email": {"old": "b@y.com", "new": None}, "reason": "bounced"})))
        self.migrate()
        rows = [tuple(r) for r in self.db.execute(
            "SELECT address, source FROM bounced_address ORDER BY address")]
        self.assertEqual(rows, [("a@x.com", "backfill"), ("b@y.com", "backfill")])

    def test_the_same_address_from_a_touch_and_an_event_is_one_row(self):
        co = add_company(self.db, "Dupe Co")
        p = add_person(self.db, co, "a@x.com")
        self.db.execute("INSERT INTO touch(person_id, created_at, updated_at, state, bounced_at) "
                        "VALUES (?, ?, ?, 'bounced', ?)", (p, T, T, T))
        self.db.execute("INSERT INTO event(at, recorded_at, entity, entity_id, company_id, kind, actor, detail) "
                        "VALUES (?, ?, 'person', ?, ?, 'email_bounced', 'claude', ?)",
                        (T, T, p, co, json.dumps({"email": {"old": "a@x.com", "new": None}})))
        self.migrate()
        self.assertEqual(self.db.execute("SELECT count(*) FROM bounced_address").fetchone()[0], 1)


class GuardRefusesNewTablesTest(unittest.TestCase):
    def test_guard_refuses_new_tables(self):
        db = fresh_store()
        self.addCleanup(db.close)
        company = add_company(db, "Guard Co")
        person = add_person(db, company, "g@guard.co")
        touch = db.execute("INSERT INTO touch(person_id, created_at, updated_at) VALUES (?, ?, ?)",
                           (person, T, T)).lastrowid
        conn = guard.open_guarded({}, path=store_path(db))
        self.addCleanup(conn.close)
        for sql, args in (
            ("INSERT INTO company_sender(company_id, account_id, pinned_by, pinned_at) VALUES (?, 1, 'rule', ?)",
             (company, T)),
            ("INSERT INTO bounced_address(address, first_seen_at, source) VALUES ('z@z.co', ?, 'bounce')", (T,)),
            ("UPDATE sender_account SET paused_reason = 'x' WHERE account_id = 1", ()),
            ("UPDATE touch SET account_id = 1 WHERE touch_id = ?", (touch,)),
        ):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                conn.execute(sql, args)


if __name__ == "__main__":
    unittest.main()
