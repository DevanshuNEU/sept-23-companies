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


class LevelStateMigrationTest(unittest.TestCase):
    """0007 adds level_state and years_ms to decision (#154); old rows come through with both empty."""

    VERSION = "r-0123456789ab"
    AT = "2026-11-03T10:00:00Z"
    ROW = ("INSERT INTO decision(posting_id, ruleset_version, decided_at, lane, level_tier, level_ok, level_basis, "
           "us, sponsorship, citizenship, eligibility, start_2027) "
           "VALUES (1, ?, ?, 'engineering', 'new_grad', 1, 'Title says new grad', 'yes', 'silent', 0, 'ok', 0)")

    def old_store(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old = Path(tmp.name)
        for version, _name, path in migration_files():
            if version <= 6:
                shutil.copy(path, old / path.name)
        conn = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(conn.close)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        self.assertEqual(migrate(conn, old), 6)
        company = seed_company(conn)
        conn.execute("INSERT INTO posting(company_id, source_key, title, location, first_seen_at, last_seen_at) "
                     "VALUES (?, 'url:1', 'Software Engineer', '', ?, ?)", (company, self.AT, self.AT))
        conn.execute("INSERT INTO ruleset(ruleset_version, config_toml, created_at, is_current) VALUES (?, '', ?, 1)",
                     (self.VERSION, self.AT))
        conn.execute(self.ROW, (self.VERSION, self.AT))
        return conn

    def test_migration_0007_keeps_decisions_and_adds_empty_columns(self):
        conn = self.old_store()
        before = [tuple(r) for r in conn.execute("SELECT * FROM decision")]
        self.assertEqual(migrate(conn), latest_version())
        self.assertIn((7, "level_state"), [(r[0], r[1]) for r in conn.execute("SELECT version, name FROM schema_migration")])
        columns = [r["name"] for r in conn.execute("PRAGMA table_info(decision)")]
        self.assertEqual(columns[-2:], ["level_state", "years_ms"])
        after = conn.execute("SELECT * FROM decision").fetchone()
        self.assertEqual(tuple(after)[:len(before[0])], before[0])
        self.assertIsNone(after["level_state"])
        self.assertIsNone(after["years_ms"])

    def test_level_state_check_refuses_other_values(self):
        db = fresh_store()
        self.addCleanup(db.close)
        company = seed_company(db)
        db.execute("INSERT INTO posting(company_id, source_key, title, location, first_seen_at, last_seen_at) "
                   "VALUES (?, 'url:1', 'Software Engineer', '', ?, ?)", (company, self.AT, self.AT))
        db.execute("INSERT INTO ruleset(ruleset_version, config_toml, created_at, is_current) VALUES (?, '', ?, 0)",
                   (self.VERSION, self.AT))
        for state in ("fit", "reject", "uncertain", None):
            db.execute("DELETE FROM decision")
            db.execute(self.ROW, (self.VERSION, self.AT))
            db.execute("UPDATE decision SET level_state = ?, years_ms = 2", (state,))
        for bad in ("maybe", "FIT", ""):
            with self.assertRaises(sqlite3.IntegrityError, msg=bad):
                db.execute("UPDATE decision SET level_state = ?", (bad,))

    def test_both_views_expose_the_new_columns(self):
        conn = self.old_store()
        migrate(conn)
        conn.execute("UPDATE decision SET level_state = 'fit', years_ms = 0")
        for view in ("current_decision", "posting_decision"):
            names = [r["name"] for r in conn.execute(f"PRAGMA table_info({view})")]
            self.assertIn("level_state", names, view)
            self.assertIn("years_ms", names, view)
            row = conn.execute(f"SELECT level_state, years_ms FROM {view}").fetchone()
            self.assertEqual((row["level_state"], row["years_ms"]), ("fit", 0), view)


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


class BoardCandidateMigrationTest(unittest.TestCase):
    """0008 only adds `board_candidate` (#192, the shape #160 planned); nothing is rebuilt."""

    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def insert(self, ats="greenhouse", token="acme", **extra):
        cols = {"ats": ats, "token": token, "source": "stapply", "found_at": "2026-11-10T10:00:00Z", **extra}
        self.conn.execute(f"INSERT INTO board_candidate({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                          tuple(cols.values()))

    def test_0008_creates_board_candidate(self):
        names = [(r[0], r[1]) for r in self.conn.execute("SELECT version, name FROM schema_migration")]
        self.assertIn((8, "board_candidate"), names)
        strict = {r[1]: r[5] for r in self.conn.execute("PRAGMA table_list")}
        self.assertEqual(strict["board_candidate"], 1)
        self.insert()
        row = self.conn.execute("SELECT name, url, domain, source_ref, priority, gated_at, gate_result, "
                                "open_us_engineering, board_id FROM board_candidate").fetchone()
        self.assertEqual(tuple(row), ("", "", None, "", 0, None, None, None, None))

    def test_a_board_is_listed_once_per_ats_and_token(self):
        self.insert()
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert()
        self.insert(ats="ashby")

    def test_gate_result_and_times_are_checked(self):
        at = "2026-11-10T11:00:00Z"
        for result in ("promoted", "no_us_engineering", "dead", "error", "known"):
            self.insert(token=result, gated_at=at, gate_result=result)
        for extra in ({"gated_at": at, "gate_result": "maybe"}, {"gated_at": at},
                      {"gate_result": "dead"}, {"found_at": "yesterday"},
                      {"gated_at": "2026-11-10 11:00:00", "gate_result": "dead"},
                      {"gated_at": at, "gate_result": "dead", "open_us_engineering": -1}):
            with self.subTest(extra=extra), self.assertRaises(sqlite3.IntegrityError):
                self.insert(token="x", **extra)


class ListTimeMigrationTest(unittest.TestCase):
    """0009 adds the list time columns (#193): old postings keep their current date, and each board's last good read
    comes from its last read or its kept reads."""

    T1, T2, T3 = "2026-11-01T10:00:00Z", "2026-11-02T10:00:00Z", "2026-11-03T10:00:00Z"

    def old_store(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old = Path(tmp.name)
        for version, _name, path in migration_files():
            if version <= 8:
                shutil.copy(path, old / path.name)
        conn = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(conn.close)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        self.assertEqual(migrate(conn, old), 8)
        return conn

    def board(self, conn, company, token, last_read=None, reads=()):
        streaks = {"ok": (0, 0), "unchanged": (0, 0), "empty": (1, 0), "error": (0, 2), None: (0, 0)}[last_read]
        at = self.T3 if last_read else None
        bid = conn.execute(
            "INSERT INTO board(company_id, ats, token, last_read, last_read_at, empty_streak, error_streak, "
            "streak_since, created_at) VALUES (?, 'greenhouse', ?, ?, ?, ?, ?, ?, ?)",
            (company, token, last_read, at, *streaks, at, self.T1)).lastrowid
        for read_at, result in reads:
            run = conn.execute("INSERT INTO job_run(job, started_at) VALUES ('crawl', ?)", (read_at,)).lastrowid
            conn.execute("INSERT INTO board_read(board_id, run_id, at, result, error) VALUES (?, ?, ?, ?, ?)",
                         (bid, run, read_at, result, "boom" if result == "error" else ""))
        return bid

    def test_0009_backfills_list_at_and_last_good_read(self):
        conn = self.old_store()
        company = seed_company(conn)
        for key, posted_on in (("url:dated", "2026-10-01"), ("url:undated", None), ("url:late", "2026-11-05")):
            conn.execute("INSERT INTO posting(company_id, source_key, title, posted_on, first_seen_at, last_seen_at) "
                         "VALUES (?, ?, 'Software Engineer', ?, ?, ?)", (company, key, posted_on, self.T2, self.T2))
        boards = {
            "ok": self.board(conn, company, "a", "ok"),
            "unchanged": self.board(conn, company, "b", "unchanged"),
            "empty": self.board(conn, company, "c", "empty"),
            "error": self.board(conn, company, "d", "error",
                                reads=((self.T1, "ok"), (self.T2, "unchanged"), (self.T3, "error"))),
            "only_errors": self.board(conn, company, "e", "error", reads=((self.T2, "error"), (self.T3, "error"))),
            "never": self.board(conn, company, "f"),
        }
        self.assertEqual(migrate(conn), latest_version())
        self.assertIn((9, "list_time"), [(r[0], r[1]) for r in conn.execute("SELECT version, name FROM schema_migration")])
        rows = {r["source_key"]: r for r in conn.execute("SELECT * FROM posting")}
        self.assertEqual(rows["url:dated"]["list_at"], "2026-10-01T05:00:00Z")
        self.assertEqual(rows["url:undated"]["list_at"], self.T2)
        # A posted date later than the first sighting is capped there: a row never sorts after it was first seen.
        self.assertEqual(rows["url:late"]["list_at"], self.T2)
        for row in rows.values():
            self.assertEqual((row["posted_at"], row["seen_gap_s"], row["repost_of"]), (None, None, None))
        good = {name: conn.execute("SELECT last_good_read_at FROM board WHERE board_id = ?", (bid,)).fetchone()[0]
                for name, bid in boards.items()}
        self.assertEqual(good, {"ok": self.T3, "unchanged": self.T3, "empty": self.T3, "error": self.T2,
                                "only_errors": None, "never": None})
        index = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'posting_list_at'").fetchone()
        self.assertIsNotNone(index)
        self.assertIn("WHERE closed_at IS NULL", index[0])
        # The new time columns take only canonical UTC, and a repost names a real posting.
        for column in ("posted_at", "list_at"):
            with self.subTest(column=column), self.assertRaises(sqlite3.IntegrityError):
                conn.execute(f"UPDATE posting SET {column} = '2026-11-01T10:00:00+00:00'")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE board SET last_good_read_at = '2026-11-01'")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE posting SET repost_of = 999")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE posting SET seen_gap_s = -1")
