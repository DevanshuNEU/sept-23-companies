import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.sendtest import seed_email_person, seed_message, seed_sent
from tests.storetest import fresh_store, seed_company
from outreach.store.migrate import (
    MIGRATIONS_DIR,
    latest_version,
    migrate,
    migration_files,
    split_sql,
)

SCHEMA_MIGRATION = """\
CREATE TABLE schema_migration (
  version    INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  applied_at TEXT NOT NULL CHECK (applied_at IS strftime('%Y-%m-%dT%H:%M:%SZ', applied_at))
) STRICT;
"""


class MigrateTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(self.conn.close)

    def write(self, name, sql):
        (self.dir / name).write_text(sql)

    def two_good_files(self):
        self.write("0001_init.sql", SCHEMA_MIGRATION + "CREATE TABLE a (x INTEGER) STRICT;\n")
        self.write("0002_more.sql", "CREATE TABLE b (y INTEGER) STRICT;\n")

    def user_version(self):
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def tables(self):
        rows = self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        return {r[0] for r in rows}

    def test_applies_in_order_and_sets_user_version(self):
        self.two_good_files()
        self.assertEqual(migrate(self.conn, self.dir), 2)
        self.assertEqual(self.user_version(), 2)
        rows = self.conn.execute(
            "SELECT version, name, applied_at FROM schema_migration ORDER BY version"
        ).fetchall()
        self.assertEqual([(r[0], r[1]) for r in rows], [(1, "init"), (2, "more")])
        self.assertRegex(rows[0][2], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_second_run_applies_nothing(self):
        self.two_good_files()
        self.assertEqual(migrate(self.conn, self.dir), 2)
        self.assertEqual(migrate(self.conn, self.dir), 2)
        count = self.conn.execute("SELECT count(*) FROM schema_migration").fetchone()[0]
        self.assertEqual(count, 2)

    def test_failed_migration_rolls_back_whole_step(self):
        self.write("0001_init.sql", SCHEMA_MIGRATION)
        self.write("0002_bad.sql", "CREATE TABLE b (y INTEGER) STRICT;\nINSERT INTO nowhere VALUES (1);\n")
        with self.assertRaises(sqlite3.Error):
            migrate(self.conn, self.dir)
        self.assertEqual(self.user_version(), 1)
        self.assertNotIn("b", self.tables())
        self.assertFalse(self.conn.in_transaction)
        count = self.conn.execute("SELECT count(*) FROM schema_migration").fetchone()[0]
        self.assertEqual(count, 1)

    def test_refuses_a_newer_database(self):
        self.two_good_files()
        self.conn.execute("PRAGMA user_version = 5")
        with self.assertRaisesRegex(RuntimeError, "newer"):
            migrate(self.conn, self.dir)
        self.assertNotIn("a", self.tables())

    def test_gap_in_numbers_is_refused(self):
        self.write("0001_init.sql", SCHEMA_MIGRATION)
        self.write("0003_skip.sql", "SELECT 1;")
        with self.assertRaises(ValueError):
            migration_files(self.dir)
        with self.assertRaises(ValueError):
            migrate(self.conn, self.dir)
        self.assertEqual(self.user_version(), 0)

    def test_duplicate_number_is_refused(self):
        self.write("0001_init.sql", SCHEMA_MIGRATION)
        self.write("0001_other.sql", "SELECT 1;")
        with self.assertRaises(ValueError):
            migration_files(self.dir)

    def test_numbering_must_start_at_one(self):
        self.write("0002_init.sql", SCHEMA_MIGRATION)
        with self.assertRaises(ValueError):
            migration_files(self.dir)

    def test_semicolon_inside_a_comment_does_not_split(self):
        stmts = split_sql("CREATE TABLE t (a INT -- x; y\n) STRICT;")
        self.assertEqual(len(stmts), 1)

    def test_split_sql_gives_each_statement_separately(self):
        stmts = split_sql("CREATE TABLE a (x INT) STRICT;\n\nCREATE TABLE b (y INT) STRICT;\n")
        self.assertEqual(len(stmts), 2)

    def test_bad_file_name_is_refused(self):
        self.write("1_init.sql", SCHEMA_MIGRATION)
        with self.assertRaises(ValueError):
            migration_files(self.dir)

    def test_other_extension_is_refused(self):
        self.write("0001_init.sql", SCHEMA_MIGRATION)
        self.write("0002_notes.txt", "hello")
        with self.assertRaises(ValueError):
            migration_files(self.dir)

    def test_dotfiles_are_ignored(self):
        self.write(".gitkeep", "")
        self.write("0001_init.sql", SCHEMA_MIGRATION)
        files = migration_files(self.dir)
        self.assertEqual([(v, n) for v, n, _ in files], [(1, "init")])
        self.assertEqual(latest_version(self.dir), 1)

    def test_latest_version_of_an_empty_directory_is_zero(self):
        self.write(".gitkeep", "")
        self.assertEqual(latest_version(self.dir), 0)

    def test_connection_must_be_in_autocommit_mode(self):
        self.two_good_files()
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        with self.assertRaises(ValueError):
            migrate(conn, self.dir)

    def test_default_directory_exists(self):
        self.assertTrue(MIGRATIONS_DIR.is_dir())
        migration_files()  # the shipped directory is well formed


if __name__ == "__main__":
    unittest.main()


class ImportRecordMigrationTest(unittest.TestCase):
    """0004 rebuilds import_record; the rows a store already holds come through the rebuild unchanged."""

    def test_migration_0004_keeps_existing_import_records(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old = Path(tmp.name)
        for version, _name, path in migration_files():
            if version <= 3:
                shutil.copy(path, old / path.name)
        conn = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(conn.close)
        conn.execute("PRAGMA foreign_keys = ON")
        self.assertEqual(migrate(conn, old), 3)
        at = "2026-10-19T12:00:00Z"
        conn.execute("INSERT INTO job_run(job, started_at) VALUES ('import', ?)", (at,))
        rows = [
            (1, "data_js", "company:1", '{"a":1}', "a" * 64, "skipped", "ruled out", None, None),
            (1, "contacted_txt", "line:2", '{"b":2}', "b" * 64, "imported", "", "touch", 7),
        ]
        for row in rows:
            conn.execute(
                "INSERT INTO import_record(run_id, source, source_key, raw, raw_sha256, outcome, reason, entity,"
                " entity_id, imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (*row, at))
        columns = "import_id, run_id, source, source_key, raw, raw_sha256, outcome, reason, entity, entity_id, imported_at"
        before = [tuple(r) for r in conn.execute(f"SELECT {columns} FROM import_record ORDER BY import_id")]
        self.assertEqual(len(before), 2)
        self.assertEqual(migrate(conn), latest_version())
        after = [tuple(r) for r in conn.execute(f"SELECT {columns} FROM import_record ORDER BY import_id")]
        self.assertEqual(after, before)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE tbl_name = 'import_record'")}
        self.assertTrue({"import_record_no_update", "import_record_no_delete", "import_record_run"} <= names)
        self.assertIn(4, [r[0] for r in conn.execute("SELECT version FROM schema_migration")])


class FourFollowupsMigrationTest(unittest.TestCase):
    """0005 rebuilds message so seq may be 4 (#141); rows, ids, indexes and triggers come through unchanged."""

    def test_migration_0005_keeps_messages_and_allows_seq_4(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old = Path(tmp.name)
        for version, _name, path in migration_files():
            if version <= 4:
                shutil.copy(path, old / path.name)
        conn = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(conn.close)
        conn.execute("PRAGMA foreign_keys = ON")
        self.assertEqual(migrate(conn, old), 4)
        conn.row_factory = sqlite3.Row
        person = seed_email_person(conn, seed_company(conn), "Ana", "ana@acme.co")
        seed_sent(conn, person, at="2026-10-19T14:00:00Z")
        seed_message(conn, person, state="planned", seq=1, due_on="2026-10-22")
        at = "2026-10-19T12:00:00Z"
        objects = "SELECT type, name, sql FROM sqlite_master WHERE tbl_name = 'message' AND type <> 'table' ORDER BY name"
        before_rows = [tuple(r) for r in conn.execute("SELECT * FROM message ORDER BY message_id")]
        before_objects = [tuple(r) for r in conn.execute(objects)]
        self.assertEqual(len([o for o in before_objects if o[0] == "trigger"]), 8)
        self.assertEqual(migrate(conn), latest_version())
        self.assertEqual([tuple(r) for r in conn.execute("SELECT * FROM message ORDER BY message_id")], before_rows)
        self.assertEqual([tuple(r) for r in conn.execute(objects)], before_objects)
        conn.execute("INSERT INTO message(touch_id, seq, state, due_on, saved_by, created_at, updated_at) "
                     "VALUES (1, 4, 'planned', '2026-11-20', 'sender', ?, ?)", (at, at))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO message(touch_id, seq, state, due_on, saved_by, created_at, updated_at) "
                         "VALUES (1, 5, 'planned', '2026-11-27', 'sender', ?, ?)", (at, at))
        self.assertIn(5, [r[0] for r in conn.execute("SELECT version FROM schema_migration")])


class YardstickMigrationTest(unittest.TestCase):
    """0006 only adds the three yardstick tables (#153); nothing is rebuilt."""

    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def test_migration_0006_creates_the_three_tables(self):
        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertTrue({"feed_listing", "miss", "pipeline_meta"} <= tables)
        self.assertIn((6, "yardstick"), [(r[0], r[1]) for r in self.conn.execute("SELECT version, name FROM schema_migration")])
        strict = {r[1]: r[5] for r in self.conn.execute("PRAGMA table_list")}
        for name in ("feed_listing", "miss", "pipeline_meta"):
            self.assertEqual(strict[name], 1, name)

    def test_feed_listing_is_unique_per_source_and_listing_and_checks_its_flags(self):
        at = "2026-11-03T10:00:00Z"
        sql = ("INSERT INTO feed_listing(source, listing_id, url, company_name, title, first_seen_at, last_seen_at, "
               "active, backfill) VALUES ('simplify', ?, 'https://x.co/1', 'Acme', 'SWE', ?, ?, ?, 0)")
        self.conn.execute(sql, ("a", at, at, 1))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(sql, ("a", at, at, 1))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(sql, ("b", at, at, 2))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(sql, ("c", "2026-11-03 10:00:00", at, 1))

    def test_miss_checks_cause_detail_and_timestamps(self):
        at = "2026-11-03T10:00:00Z"
        sql = "INSERT INTO miss(url, pasted_at, cause, detail) VALUES ('https://x.co/1', ?, ?, ?)"
        self.conn.execute(sql, (at, "no_company", "{}"))
        for args in ((at, "nope", "{}"), (at, "found", "not json"), ("yesterday", "found", "{}")):
            with self.subTest(args=args), self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute(sql, args)
        row = self.conn.execute("SELECT note, detail, posting_id, resolved_at FROM miss").fetchone()
        self.assertEqual(tuple(row), ("", "{}", None, None))

    def test_pipeline_meta_is_a_key_value_table(self):
        self.conn.execute("INSERT INTO pipeline_meta(key, value) VALUES ('feed_etag', 'W/\"1\"')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO pipeline_meta(key, value) VALUES ('feed_etag', 'x')")
