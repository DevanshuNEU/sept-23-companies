import json
import sqlite3
import unittest

from outreach.store import append_event, finish_job, name_key, start_job
from tests.storetest import fresh_store, seed_company


class NameKeyTests(unittest.TestCase):
    def test_name_key(self):
        self.assertEqual(name_key("Retell AI, Inc."), "retellai")
        self.assertEqual(name_key("Acme Corp"), "acme")
        self.assertEqual(name_key("Corpay"), "corpay")

    def test_suffix_is_dropped_only_as_a_whole_trailing_word(self):
        self.assertEqual(name_key("Coinbase"), "coinbase")
        self.assertEqual(name_key("Inc Studio"), "incstudio")
        self.assertEqual(name_key("Foo Co., Ltd."), "foo")
        self.assertEqual(name_key("Siemens GmbH"), "siemens")
        self.assertEqual(name_key("Acme LLC"), "acme")

    def test_a_name_that_is_only_a_suffix_is_kept(self):
        self.assertEqual(name_key("Inc."), "inc")

    def test_empty_and_none(self):
        self.assertEqual(name_key(""), "")
        self.assertEqual(name_key(None), "")


class EventTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def test_event_is_written_with_canonical_times(self):
        company_id = seed_company(self.conn)
        event_id = append_event(
            self.conn, entity="company", kind="created", actor="devanshu",
            entity_id=company_id, company_id=company_id, detail={"why": "test"},
            at="2026-10-19T08:00:00-04:00",
        )
        row = self.conn.execute("SELECT * FROM event WHERE event_id = ?", (event_id,)).fetchone()
        self.assertEqual(row["at"], "2026-10-19T12:00:00Z")
        self.assertRegex(row["recorded_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(json.loads(row["detail"]), {"why": "test"})
        self.assertEqual((row["entity"], row["kind"], row["actor"]), ("company", "created", "devanshu"))
        self.assertEqual((row["entity_id"], row["company_id"]), (company_id, company_id))
        self.assertIsNone(row["idempotency_key"])

    def test_defaults_are_now_and_empty_detail(self):
        event_id = append_event(self.conn, entity="sender", kind="tick", actor="system")
        row = self.conn.execute("SELECT * FROM event WHERE event_id = ?", (event_id,)).fetchone()
        self.assertEqual(row["at"], row["recorded_at"])
        self.assertRegex(row["at"], r"Z$")
        self.assertEqual(row["detail"], "{}")
        self.assertIsNone(row["entity_id"])
        self.assertIsNone(row["company_id"])

    def test_same_idempotency_key_is_a_no_op_even_with_a_different_time(self):
        first = append_event(
            self.conn, entity="sender", kind="tick", actor="sender",
            at="2026-10-19T12:00:00Z", idempotency_key="tick-1",
        )
        second = append_event(
            self.conn, entity="sender", kind="tick", actor="sender",
            at="2026-10-20T12:00:00Z", idempotency_key="tick-1", detail={"later": True},
        )
        self.assertIsInstance(first, int)
        self.assertIsNone(second)
        rows = self.conn.execute("SELECT at, detail FROM event WHERE idempotency_key = 'tick-1'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["at"], "2026-10-19T12:00:00Z")
        self.assertEqual(rows[0]["detail"], "{}")

    def test_different_keys_and_no_key_each_insert(self):
        a = append_event(self.conn, entity="sender", kind="k", actor="sender", idempotency_key="a")
        b = append_event(self.conn, entity="sender", kind="k", actor="sender", idempotency_key="b")
        c = append_event(self.conn, entity="sender", kind="k", actor="sender")
        d = append_event(self.conn, entity="sender", kind="k", actor="sender")
        self.assertEqual(len({a, b, c, d}), 4)

    def test_unknown_actor_is_refused(self):
        with self.assertRaises(sqlite3.IntegrityError):
            append_event(self.conn, entity="sender", kind="k", actor="robot")

    def test_unreadable_time_is_refused_before_writing(self):
        with self.assertRaises(ValueError):
            append_event(self.conn, entity="sender", kind="k", actor="sender", at="not a time")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM event").fetchone()[0], 0)


class JobRunTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def test_job_run_round_trip(self):
        run_id = start_job(self.conn, "crawl")
        row = self.conn.execute("SELECT * FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
        self.assertEqual(row["status"], "running")
        self.assertIsNone(row["finished_at"])
        self.assertEqual(row["job"], "crawl")
        self.assertIsNone(row["ruleset_version"])
        finish_job(self.conn, run_id, "ok", {"n": 3})
        row = self.conn.execute("SELECT * FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
        self.assertEqual(row["status"], "ok")
        self.assertRegex(row["finished_at"], r"Z$")
        self.assertEqual(json.loads(row["summary"]), {"n": 3})
        self.assertEqual(row["error"], "")

    def test_failed_run_keeps_its_error(self):
        run_id = start_job(self.conn, "rebuild")
        finish_job(self.conn, run_id, "failed", error="boom")
        row = self.conn.execute("SELECT * FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
        self.assertEqual((row["status"], row["error"], row["summary"]), ("failed", "boom", "{}"))

    def test_ruleset_version_is_stored(self):
        self.conn.execute(
            "INSERT INTO ruleset(ruleset_version, config_toml, created_at) "
            "VALUES ('r-0123456789ab', '', '2026-10-19T12:00:00Z')"
        )
        run_id = start_job(self.conn, "rebuild", "r-0123456789ab")
        row = self.conn.execute("SELECT ruleset_version FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
        self.assertEqual(row["ruleset_version"], "r-0123456789ab")

    def test_unknown_job_is_refused(self):
        with self.assertRaises(sqlite3.IntegrityError):
            start_job(self.conn, "mystery")

    def test_finishing_as_running_is_refused(self):
        run_id = start_job(self.conn, "crawl")
        with self.assertRaises(sqlite3.IntegrityError):
            finish_job(self.conn, run_id, "running")

    def test_finishing_a_missing_run_raises(self):
        with self.assertRaises(LookupError):
            finish_job(self.conn, 999, "ok")


if __name__ == "__main__":
    unittest.main()
