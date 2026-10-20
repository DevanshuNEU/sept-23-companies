import sqlite3
import tempfile
import unittest
from pathlib import Path

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
