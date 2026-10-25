import sqlite3
import tempfile
import unittest
from pathlib import Path

from outreach.importer.read_radar import (
    RADAR_ROW_TABLES,
    RADAR_TABLE_REASONS,
    radar_body,
    read_radar,
)
from outreach.importer.records import SourceError, SourceRecord, open_radar, source_digest
from tests.importtest import fixture_manifest


class ReadRadarTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.path = fixture_manifest(self.tmp).paths["radar_db"]

    def records(self):
        records = list(read_radar(self.path))
        for record in records:
            self.assertIsInstance(record, SourceRecord)
            self.assertEqual(record.source, "radar_db")
        keys = [r.source_key for r in records]
        self.assertEqual(len(keys), len(set(keys)))
        return records

    def raw(self):
        return {r.source_key: r.raw for r in self.records()}

    def edit(self, *statements):
        """Change the fixture copy (never a real radar.db) before the reader runs."""
        db = sqlite3.connect(self.path, isolation_level=None)
        try:
            for sql in statements:
                db.execute(sql)
        finally:
            db.close()

    def tables_in_file(self):
        conn = open_radar(self.path)
        try:
            return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            conn.close()

    def test_every_radar_table_is_a_row_record_or_a_table_record(self):
        keys = [r.source_key for r in self.records()]
        row_tables = {k.split(":")[0] for k in keys if not k.startswith("table:")}
        table_records = {k.split(":", 1)[1] for k in keys if k.startswith("table:")}
        self.assertEqual(row_tables, set(RADAR_ROW_TABLES) | set())
        self.assertEqual(row_tables | table_records, self.tables_in_file())
        self.assertFalse(row_tables & table_records)
        self.assertEqual(table_records, set(RADAR_TABLE_REASONS))
        self.assertEqual(RADAR_ROW_TABLES, ("board", "posting", "app_event"))

    def test_table_records_say_how_many_rows_and_why_they_are_not_per_row(self):
        raw = self.raw()
        self.assertEqual(raw["table:poll_run"], {"rows": 3, "reason": "run_history"})
        self.assertEqual(raw["table:board_fetch"], {"rows": 9, "reason": "run_history"})
        self.assertEqual(raw["table:decision"], {"rows": 3, "reason": "derived_decision"})
        self.assertEqual(raw["table:outbox"], {"rows": 1, "reason": "outbox_delivery"})
        self.assertEqual(raw["table:label"], {"rows": 1, "reason": "label_rubric"})
        self.assertEqual(raw["table:body_attempt"], {"rows": 1, "reason": "run_history"})
        self.assertEqual(raw["table:meta"], {"rows": 1, "reason": "schema_meta"})
        self.assertEqual(raw["table:schema_migration"], {"rows": 5, "reason": "schema_meta"})
        # 8 versions, 6 of them read as a posting's latest: 2 are not read
        self.assertEqual(raw["table:posting_version"], {"rows": 2, "reason": "older_version"})

    def test_unknown_radar_table_is_a_source_error(self):
        self.edit("CREATE TABLE mystery_notes (x INTEGER)")
        with self.assertRaises(SourceError) as cm:
            list(read_radar(self.path))
        self.assertIn("mystery_notes", str(cm.exception))
        self.assertIn("radar_db", str(cm.exception))

    def test_unknown_table_stops_the_reader_before_any_record(self):
        self.edit("CREATE TABLE mystery_notes (x INTEGER)")
        reader = read_radar(self.path)
        with self.assertRaises(SourceError):
            next(reader)

    def test_board_and_app_event_records_are_the_rows(self):
        raw = self.raw()
        self.assertEqual({k for k in raw if k.startswith("board:")}, {"board:1", "board:2", "board:3"})
        self.assertEqual(raw["board:1"]["company"], "Kestrel Robotics")
        self.assertEqual(raw["board:1"]["ats"], "greenhouse")
        self.assertEqual(raw["board:2"]["token"], "marlow")
        self.assertEqual(len([k for k in raw if k.startswith("app_event:")]), 4)
        event = raw["app_event:2"]
        self.assertEqual((event["kind"], event["posting_id"], event["resume_variant"], event["client_event_id"]),
                         ("applied", 1, "resume-a", "fixture-ev-2"))

    def test_posting_record_carries_closed_and_latest_version(self):
        raw = self.raw()
        self.assertEqual(len([k for k in raw if k.startswith("posting:")]), 6)
        first = raw["posting:1"]
        self.assertEqual(first["source_key"], "greenhouse:kestrel:1001")
        self.assertEqual(first["closed"], 0)
        self.assertEqual(first["latest"]["content_hash"], "h2-1-b")
        self.assertEqual(first["latest"]["observed_at"], "2026-10-20T08:00:00Z")
        self.assertEqual(set(first["latest"]), {"version_id", "content_hash", "observed_at"})
        self.assertEqual(raw["posting:2"]["latest"]["content_hash"], "h2-2-a")
        self.assertEqual(raw["posting:5"]["latest"]["content_hash"], "h2-5-b")

    def test_closed_comes_from_posting_live(self):
        self.edit("UPDATE posting SET last_seen_at = '2026-10-01T08:00:00Z' WHERE posting_id = 2")
        raw = self.raw()
        self.assertEqual(raw["posting:2"]["closed"], 1)
        self.assertEqual(raw["posting:1"]["closed"], 0)

    def test_a_merged_posting_is_still_a_record_with_no_closed_flag(self):
        self.edit("UPDATE posting SET merged_into = 1 WHERE posting_id = 2")
        raw = self.raw()
        self.assertIn("posting:2", raw)
        self.assertEqual(raw["posting:2"]["merged_into"], 1)
        self.assertIsNone(raw["posting:2"]["closed"])

    def test_a_posting_with_no_version_has_no_latest(self):
        self.edit("DELETE FROM posting_version WHERE posting_id = 3")
        records = {r.source_key: r for r in self.records()}
        self.assertIsNone(records["posting:3"].raw["latest"])
        self.assertIsNone(radar_body(records["posting:3"]))
        self.assertEqual(records["table:posting_version"].raw["rows"], 2)

    def test_newest_version_is_by_observed_at_then_version_id(self):
        self.edit(
            "INSERT INTO posting_version(posting_id, run_id, observed_at, content_hash, payload, body_text) "
            "VALUES (2, 1, '2026-10-20T08:00:00Z', 'h2-2-late', '{}', 'late body')",
            "INSERT INTO posting_version(posting_id, run_id, observed_at, content_hash, payload, body_text) "
            "VALUES (2, 1, '2026-10-20T08:00:00Z', 'h2-2-tie', '{}', 'tie body')")
        records = {r.source_key: r for r in self.records()}
        self.assertEqual(records["posting:2"].raw["latest"]["content_hash"], "h2-2-tie")
        self.assertEqual(radar_body(records["posting:2"]), "tie body")

    def test_body_is_not_in_the_raw_record(self):
        records = {r.source_key: r for r in self.records()}
        for key, record in records.items():
            self.assertNotIn("Fixture job description", record.raw_json(), key)
            self.assertNotIn("body_text", record.raw_json(), key)
        self.assertEqual(radar_body(records["posting:1"]), "Fixture job description 1, edited.")
        self.assertEqual(radar_body(records["posting:6"]), "Fixture job description 6.")
        self.assertIsNone(radar_body(records["posting:5"]))  # its newest version has no body
        self.assertIsNone(radar_body(records["board:1"]))
        self.assertIsNone(radar_body(SourceRecord("radar_db", "posting:99", {"a": 1})))

    def test_the_body_does_not_change_the_hash(self):
        first = {r.source_key: r.raw_sha256() for r in self.records()}
        again = {r.source_key: r.raw_sha256() for r in self.records()}
        self.assertEqual(first, again)

    def test_reader_never_writes_radar(self):
        before = source_digest(self.path)
        self.assertGreater(len(self.records()), 20)
        self.assertEqual(source_digest(self.path), before)
        self.assertFalse(Path(f"{self.path}-wal").exists())
        self.assertFalse(Path(f"{self.path}-shm").exists())
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir() if p.name.startswith("radar.db")),
                         ["radar.db"])

    def test_a_wal_database_is_read_without_blocking_its_writer_or_changing_the_file(self):
        self.edit("PRAGMA journal_mode = WAL")
        self.assertFalse(Path(f"{self.path}-wal").exists())
        before = source_digest(self.path)
        self.assertGreater(len(self.records()), 20)
        self.assertEqual(source_digest(self.path), before)
        # While a reader is part-way through, Job Radar can still write.
        reader = read_radar(self.path)
        next(reader)
        writer = sqlite3.connect(self.path, isolation_level=None, timeout=0.2)
        try:
            writer.execute("INSERT INTO meta(key, value) VALUES ('written-during-read', 'yes')")
        finally:
            writer.close()
        remaining = list(reader)
        self.assertGreater(len(remaining), 10)
        meta = [r for r in remaining if r.source_key == "table:meta"][0]
        self.assertEqual(meta.raw["rows"], 1)  # the reader saw one snapshot, not the later write

    def test_a_finished_reader_leaves_no_connection_holding_the_file(self):
        reader = read_radar(self.path)
        next(reader)
        reader.close()
        rw = sqlite3.connect(self.path, isolation_level=None, timeout=0.2)
        try:
            rw.execute("BEGIN IMMEDIATE")
            rw.execute("INSERT INTO meta(key, value) VALUES ('after-close', 'yes')")
            rw.execute("COMMIT")
        finally:
            rw.close()

    def many_postings(self, count=200):
        """Add `count` postings on board 1: every third one closed, every fifth merged, some with no version."""
        statements = []
        for i in range(count):
            posting_id = 100 + i
            last_seen = "2026-10-01T08:00:00Z" if i % 3 == 0 else "2026-10-24T08:00:00Z"
            merged = "1" if i % 5 == 0 else "NULL"
            statements.append(
                "INSERT INTO posting(posting_id, board_id, source_key, fingerprint, apply_url, title, merged_into, "
                f"first_seen_at, last_seen_at, first_run_id) VALUES ({posting_id}, 1, 'bulk-{i}', 'fp-{i}', "
                f"'https://example.test/{i}', 'Bulk {i}', {merged}, '2026-09-01T08:00:00Z', '{last_seen}', 1)")
            if i % 7 != 0:
                statements.append(
                    "INSERT INTO posting_version(posting_id, run_id, observed_at, content_hash, payload, body_text) "
                    f"VALUES ({posting_id}, 1, '2026-10-0{1 + i % 3}T08:00:00Z', 'bulk-h-{i}', '{{}}', 'body {i}')")
        self.edit(*statements)

    def traced(self):
        """Read the radar through a connection that records every statement it runs; returns (records, statements)."""
        import outreach.importer.read_radar as module
        statements: list[str] = []
        real_open = module.open_radar

        def tracing_open(path):
            conn = real_open(path)
            conn.set_trace_callback(statements.append)
            return conn

        module.open_radar = tracing_open
        try:
            records = list(read_radar(self.path))
        finally:
            module.open_radar = real_open
        return records, statements

    def test_the_posting_live_view_is_read_once_not_once_per_posting(self):
        self.many_postings(200)
        records, statements = self.traced()
        self.assertEqual(len([r for r in records if r.source_key.startswith("posting:")]), 206)
        view_reads = [s for s in statements if "posting_live" in s and "sqlite_master" not in s]
        self.assertLessEqual(len(view_reads), 1)
        # one lookup of the newest version per posting, plus a small constant for everything else
        self.assertLessEqual(len(statements), 206 + 30)

    def test_posting_records_equal_a_per_row_read_of_the_view(self):
        self.many_postings(200)
        self.edit("UPDATE posting SET last_seen_at = '2026-10-01T08:00:00Z' WHERE posting_id = 2",
                  "UPDATE posting SET merged_into = 1 WHERE posting_id = 3")
        records = [r for r in read_radar(self.path) if r.source_key.startswith("posting:")]
        conn = open_radar(self.path)
        conn.row_factory = sqlite3.Row
        try:
            expected = []
            for (posting_id,) in conn.execute("SELECT posting_id FROM posting ORDER BY posting_id").fetchall():
                row = conn.execute(
                    "SELECT p.*, l.closed AS closed FROM posting p LEFT JOIN posting_live l "
                    "ON l.posting_id = p.posting_id WHERE p.posting_id = ?", (posting_id,)).fetchone()
                latest = conn.execute(
                    "SELECT version_id, content_hash, observed_at, body_text FROM posting_version "
                    "WHERE posting_id = ? ORDER BY observed_at DESC, version_id DESC LIMIT 1", (posting_id,)).fetchone()
                raw = {k: row[k] for k in row.keys()}
                raw["latest"] = None if latest is None else {
                    "version_id": latest["version_id"], "content_hash": latest["content_hash"],
                    "observed_at": latest["observed_at"]}
                expected.append((f"posting:{posting_id}", raw, None if latest is None else latest["body_text"]))
        finally:
            conn.close()
        self.assertEqual([(r.source_key, r.raw, radar_body(r)) for r in records], expected)
        closed = [raw["closed"] for _, raw, _ in expected]
        self.assertEqual({0, 1, None}, set(closed))  # the fixture covers open, closed and merged postings
        by_key = {key: raw for key, raw, _ in expected}
        self.assertEqual(by_key["posting:2"]["closed"], 1)
        self.assertIsNone(by_key["posting:3"]["closed"])
        self.assertEqual([list(raw) for _, raw, _ in expected][0][-2:], ["closed", "latest"])

    def test_a_missing_file_a_non_database_and_a_missing_table_are_source_errors(self):
        with self.assertRaises(SourceError) as cm:
            list(read_radar(self.tmp / "nowhere.db"))
        self.assertIn("radar_db", str(cm.exception))
        junk = self.tmp / "junk.db"
        junk.write_bytes(b"this is not a database" * 100)
        with self.assertRaises(SourceError):
            list(read_radar(junk))
        empty = self.tmp / "empty.db"
        sqlite3.connect(empty).close()
        with self.assertRaises(SourceError) as cm:
            list(read_radar(empty))
        self.assertIn("board", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
