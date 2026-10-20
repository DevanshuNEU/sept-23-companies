"""Rules rebuild, decide_and_store and record_evidence, all on a scratch store from storetest.fresh_store()."""
import contextlib
import importlib
import io
import json
import os
import sqlite3
import unittest
from unittest import mock

from outreach.rules import (
    PostingInput, RulesetNotCurrent, decide, decide_and_store, load_config, passes, rebuild, record_evidence,
    ruleset_version,
)
from outreach.rules.facts import EvidenceDraft, from_old_dashboard
from outreach.store import utcnow
from tests.storetest import fresh_store, seed_company, seed_posting

# `from outreach.rules import rebuild` is the function; the module is reached through importlib.
rebuild_module = importlib.import_module("outreach.rules.rebuild")

V1, V2, V3 = "r-000000000001", "r-000000000002", "r-000000000003"
NEW_GRAD = "Software Engineer, New Grad"


def close(conn, posting_id):
    conn.execute("UPDATE posting SET closed_at = ?, close_reason = 'hand' WHERE posting_id = ?",
                 (utcnow(), posting_id))


def current(conn):
    row = conn.execute("SELECT ruleset_version FROM ruleset WHERE is_current = 1").fetchone()
    return row[0] if row else None


def versions_of(conn, posting_id):
    return {r[0] for r in conn.execute("SELECT ruleset_version FROM decision WHERE posting_id = ?", (posting_id,))}


class RebuildTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.company = seed_company(self.conn)

    def posting(self, title=NEW_GRAD, body="", location="Boston, MA"):
        return seed_posting(self.conn, self.company, title, body, location)

    def set_body(self, posting_id, body):
        self.conn.execute("UPDATE posting SET body = ? WHERE posting_id = ?", (body, posting_id))

    def path(self):
        return self.conn.execute("PRAGMA database_list").fetchone()["file"]


class RebuildTests(RebuildTestCase):
    def test_install_registers_the_current_ruleset(self):
        rows = self.conn.execute("SELECT * FROM ruleset WHERE is_current = 1").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ruleset_version"], ruleset_version())
        self.assertEqual(rows[0]["config_toml"], load_config().raw.decode("utf-8"))

    def test_open_and_undecided_postings_get_a_decision(self):
        open_id, closed_id = self.posting(), self.posting()
        close(self.conn, closed_id)
        report = rebuild(self.conn, version=V1)
        self.assertEqual(report.decided, 2)
        self.assertEqual(versions_of(self.conn, open_id), {V1})
        self.assertEqual(versions_of(self.conn, closed_id), {V1})
        report = rebuild(self.conn, version=V2)
        self.assertEqual(report.decided, 1)
        self.assertEqual(versions_of(self.conn, open_id), {V1, V2})
        self.assertEqual(versions_of(self.conn, closed_id), {V1})
        shown = self.conn.execute("SELECT ruleset_version FROM posting_decision WHERE posting_id = ?",
                                  (closed_id,)).fetchone()
        self.assertEqual(shown[0], V1)

    def test_flip_to_hidden_is_listed(self):
        pid = self.posting(body="")
        rebuild(self.conn, version=V1)
        self.assertTrue(passes(decide(PostingInput(NEW_GRAD, "", "Boston, MA"))))
        self.set_body(pid, "We do not sponsor.")
        report = rebuild(self.conn, version=V2)
        self.assertEqual([f.posting_id for f in report.to_hidden], [pid])
        flip = report.to_hidden[0]
        self.assertEqual((flip.company, flip.title), ("Acme", NEW_GRAD))
        self.assertIn("sponsorship", flip.change)
        self.assertIn("silent -> no", flip.change)
        self.assertEqual((report.to_shown, report.level_lost), ([], []))
        self.assertEqual((report.version, report.previous), (V2, V1))
        run = self.conn.execute("SELECT * FROM job_run WHERE job = 'rebuild' ORDER BY run_id DESC").fetchone()
        summary = json.loads(run["summary"])
        self.assertEqual(summary["to_hidden"], [pid])
        self.assertEqual((summary["decided"], summary["previous"]), (1, V1))
        self.assertIn("pruned", summary)
        self.assertEqual((run["status"], run["ruleset_version"]), ("ok", V2))

    def test_flip_to_shown_and_level_lost_are_listed(self):
        hidden = self.posting(body="We do not sponsor.")
        senior_later = self.posting(body="")
        rebuild(self.conn, version=V1)
        self.set_body(hidden, "")
        self.set_body(senior_later, "5+ years of experience required.")
        report = rebuild(self.conn, version=V2)
        self.assertEqual([f.posting_id for f in report.to_shown], [hidden])
        self.assertIn("sponsorship no -> silent", report.to_shown[0].change)
        self.assertEqual([f.posting_id for f in report.level_lost], [senior_later])
        self.assertIn("Asks 5 years", report.level_lost[0].change)
        self.assertEqual(report.to_hidden, [])

    def test_same_rules_twice_flip_nothing(self):
        self.posting()
        self.posting(body="We do not sponsor.")
        rebuild(self.conn)
        report = rebuild(self.conn)
        self.assertEqual((report.to_hidden, report.to_shown, report.level_lost), ([], [], []))
        self.assertEqual(report.decided, 2)
        self.assertEqual(report.version, ruleset_version())

    def test_only_current_and_previous_versions_are_kept(self):
        open_id, closed_id = self.posting(), self.posting()
        close(self.conn, closed_id)
        rebuild(self.conn, version=V1)
        for version in (V2, V3):
            report = rebuild(self.conn, version=version)
        self.assertEqual(versions_of(self.conn, open_id), {V2, V3})
        self.assertEqual(versions_of(self.conn, closed_id), {V1})
        self.assertEqual(current(self.conn), V3)
        run = self.conn.execute("SELECT summary FROM job_run WHERE job = 'rebuild' ORDER BY run_id DESC").fetchone()
        self.assertEqual(json.loads(run["summary"])["pruned"], 1)
        self.assertEqual(report.previous, V2)

    def test_revert_to_an_older_ruleset(self):
        pid = self.posting(body="")
        rebuild(self.conn, version=V1)
        self.set_body(pid, "We do not sponsor.")
        rebuild(self.conn, version=V2)
        self.set_body(pid, "")
        report = rebuild(self.conn, version=V1)
        self.assertEqual(current(self.conn), V1)
        self.assertEqual(report.previous, V2)
        self.assertEqual([f.posting_id for f in report.to_shown], [pid])
        self.assertEqual(versions_of(self.conn, pid), {V1, V2})
        self.assertEqual(self.conn.execute("SELECT count(*) FROM ruleset WHERE is_current = 1").fetchone()[0], 1)

    def test_failed_rebuild_keeps_the_previous_version(self):
        for _ in range(4):
            self.posting()
        rebuild(self.conn, version=V1)
        real, calls = decide, []

        def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) == 3:
                raise RuntimeError("boom")
            return real(*args, **kwargs)

        with mock.patch.object(rebuild_module, "decide", flaky):
            with self.assertRaises(RuntimeError):
                rebuild(self.conn, version=V2, batch_size=2)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(current(self.conn), V1)
        run = self.conn.execute("SELECT * FROM job_run WHERE job = 'rebuild' ORDER BY run_id DESC").fetchone()
        self.assertEqual((run["status"], run["ruleset_version"]), ("failed", V2))
        self.assertIn("boom", run["error"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM decision WHERE ruleset_version = ?",
                                           (V2,)).fetchone()[0], 2)  # the first batch committed
        # the next run overwrites those rows and switches
        report = rebuild(self.conn, version=V2, batch_size=2)
        self.assertEqual(current(self.conn), V2)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM decision WHERE ruleset_version = ?",
                                           (V2,)).fetchone()[0], 4)
        self.assertEqual(report.decided, 4)

    def test_batches_release_the_lock(self):
        for _ in range(5):
            self.posting()
        seen = []

        def progress(done):
            other = sqlite3.connect(self.path(), timeout=0)
            try:
                other.execute("INSERT INTO company(name, status_at, created_at) VALUES ('Other', ?, ?)",
                              (utcnow(), utcnow()))
                other.commit()
            finally:
                other.close()
            seen.append(done)

        rebuild(self.conn, version=V1, batch_size=2, progress=progress)
        self.assertEqual(seen, [2, 4, 5])

    def test_a_batch_holds_the_lock_while_it_runs(self):
        self.posting()
        real, outcomes = decide, []

        def probing(*args, **kwargs):
            other = sqlite3.connect(self.path(), timeout=0)
            try:
                other.execute("INSERT INTO company(name, status_at, created_at) VALUES ('Other', ?, ?)",
                              (utcnow(), utcnow()))
                outcomes.append("wrote")
            except sqlite3.OperationalError:
                outcomes.append("locked")
            finally:
                other.close()
            return real(*args, **kwargs)

        with mock.patch.object(rebuild_module, "decide", probing):
            rebuild(self.conn, version=V1)
        self.assertEqual(outcomes, ["locked"])

    def test_empty_store_report_lines(self):
        report = rebuild(self.conn, version=V1)
        self.assertEqual(report.decided, 0)
        lines = report.lines()
        self.assertTrue(any("shown to hidden" in line and "0" in line for line in lines))
        self.assertEqual(sum(1 for line in lines if line == "None."), 3)

    def test_report_lines_list_each_flip(self):
        pid = self.posting(body="")
        rebuild(self.conn, version=V1)
        self.set_body(pid, "We do not sponsor.")
        lines = rebuild(self.conn, version=V2).lines()
        flip_line = next(line for line in lines if NEW_GRAD in line)
        self.assertIn(f"#{pid}", flip_line)
        self.assertIn("Acme", flip_line)
        self.assertEqual(sum(1 for line in lines if line == "None."), 2)
        for line in lines:
            self.assertNotIn(chr(0x2014), line)
            self.assertNotIn(chr(0x2013), line)

    def test_concurrent_status_change_does_not_break_a_batch(self):
        # a posting closed after the list was read is still decided (it has no row yet)
        pid = self.posting()
        real = rebuild_module.decide

        def closing(*args, **kwargs):
            close(self.conn, pid)
            return real(*args, **kwargs)

        with mock.patch.object(rebuild_module, "decide", closing):
            rebuild(self.conn, version=V1)
        self.assertEqual(versions_of(self.conn, pid), {V1})


class DecideAndStoreTests(RebuildTestCase):
    def test_decide_and_store_refuses_a_stale_ruleset(self):
        pid = self.posting()
        rebuild(self.conn, version=V1)
        with self.assertRaises(RulesetNotCurrent) as caught:
            decide_and_store(self.conn, pid)
        self.assertIn("uv run python -m outreach.rules.rebuild", str(caught.exception))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM decision WHERE ruleset_version = ?",
                                           (ruleset_version(),)).fetchone()[0], 0)

    def test_decide_and_store_writes_under_current(self):
        pid = self.posting(body="We do not sponsor.")
        decision = decide_and_store(self.conn, pid)
        self.assertEqual(decision.sponsorship, "no")
        row = self.conn.execute("SELECT * FROM current_decision WHERE posting_id = ?", (pid,)).fetchone()
        self.assertEqual(row["ruleset_version"], ruleset_version())
        self.assertEqual(row["sponsorship"], "no")
        self.set_body(pid, "")
        decide_and_store(self.conn, pid)  # a second call replaces the row
        rows = self.conn.execute("SELECT sponsorship FROM decision WHERE posting_id = ?", (pid,)).fetchall()
        self.assertEqual([r[0] for r in rows], ["silent"])
        self.assertFalse(self.conn.in_transaction)

    def test_decide_and_store_joins_the_callers_transaction(self):
        pid = self.posting()
        self.conn.execute("BEGIN IMMEDIATE")
        decide_and_store(self.conn, pid)
        self.assertTrue(self.conn.in_transaction)
        self.conn.execute("ROLLBACK")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM decision").fetchone()[0], 0)

    def test_decide_and_store_on_a_missing_posting(self):
        with self.assertRaises(LookupError):
            decide_and_store(self.conn, 999)
        self.assertFalse(self.conn.in_transaction)


class RecordEvidenceTests(RebuildTestCase):
    def test_small_default_leaves_size_unknown(self):
        drafts = from_old_dashboard({"sizeClass": "small", "sizeWhy": "no filing record: most likely an early startup"})
        self.assertEqual(record_evidence(self.conn, self.company, drafts), 0)
        rows = self.conn.execute("SELECT * FROM company_fact WHERE company_id = ? AND fact = 'size'",
                                 (self.company,)).fetchall()
        self.assertEqual(rows, [])

    def test_record_evidence_adds_rows_and_one_event(self):
        pid = self.posting()
        drafts = from_old_dashboard({"sizeWhy": "about 40 people", "funding": "Seed round"})
        self.assertEqual(len(drafts), 2)
        self.assertEqual(record_evidence(self.conn, self.company, drafts, posting_id=pid), 2)
        rows = self.conn.execute("SELECT * FROM evidence WHERE company_id = ?", (self.company,)).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["locked"] == 0 and r["posting_id"] == pid for r in rows))
        events = self.conn.execute("SELECT * FROM event WHERE entity = 'evidence'").fetchall()
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["kind"], events[0]["actor"], events[0]["company_id"]),
                         ("recorded", "rules", self.company))

    def test_record_evidence_ignores_repeats(self):
        drafts = from_old_dashboard({"sizeWhy": "about 40 people"})
        self.assertEqual(record_evidence(self.conn, self.company, drafts), 1)
        self.assertEqual(record_evidence(self.conn, self.company, drafts), 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM event WHERE entity = 'evidence'").fetchone()[0], 1)

    def test_record_evidence_refuses_hand_drafts(self):
        hand = EvidenceDraft("size", "50", "confirmed", "hand", num_low=50, num_high=50)
        with self.assertRaises(ValueError):
            record_evidence(self.conn, self.company, [hand])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM evidence").fetchone()[0], 0)

    def test_record_evidence_raises_on_a_bad_draft(self):
        good = EvidenceDraft("size", "40", "estimate", "posting", num_low=40, num_high=40)
        bad = EvidenceDraft("stage", "seed", "estimate", "posting")
        object.__setattr__(bad, "value", "series_z")  # past the draft's own check, to reach the table's
        with self.assertRaises(sqlite3.IntegrityError):
            record_evidence(self.conn, self.company, [good, bad])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM evidence").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM event WHERE entity = 'evidence'").fetchone()[0], 0)
        self.assertFalse(self.conn.in_transaction)

    def test_record_evidence_in_a_callers_transaction_is_all_or_nothing(self):
        good = EvidenceDraft("size", "40", "estimate", "posting", num_low=40, num_high=40)
        bad = EvidenceDraft("stage", "seed", "estimate", "posting")
        object.__setattr__(bad, "value", "series_z")
        self.conn.execute("BEGIN IMMEDIATE")
        with self.assertRaises(sqlite3.IntegrityError):
            record_evidence(self.conn, self.company, [good, bad])
        self.assertTrue(self.conn.in_transaction)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM evidence").fetchone()[0], 0)
        self.assertEqual(record_evidence(self.conn, self.company, [good]), 1)
        self.conn.execute("COMMIT")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM evidence").fetchone()[0], 1)

    def test_a_hand_row_is_not_swallowed_by_automation_rows(self):
        draft = EvidenceDraft("size", "40", "estimate", "posting", num_low=40, num_high=40)
        record_evidence(self.conn, self.company, [draft])
        self.conn.execute(
            "INSERT INTO evidence(company_id, fact, value, num_low, num_high, confidence, locked, source_kind, "
            "recorded_at) VALUES (?, 'size', '40', 40, 40, 'confirmed', 1, 'hand', ?)", (self.company, utcnow()))
        self.assertEqual(record_evidence(self.conn, self.company, [draft]), 0)


class CliTests(RebuildTestCase):
    def test_cli_prints_the_report(self):
        self.posting()
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"OUTREACH_DB": self.path()}), contextlib.redirect_stdout(out):
            self.assertEqual(rebuild_module.main([]), 0)
        self.assertIn("shown to hidden", out.getvalue())
        self.assertEqual(self.conn.execute("SELECT count(*) FROM decision WHERE ruleset_version = ?",
                                           (ruleset_version(),)).fetchone()[0], 1)


class InstallTests(unittest.TestCase):
    def test_install_rebuilds_only_when_the_ruleset_differs(self):
        from outreach import install
        from outreach.store import connect
        conn = fresh_store()
        self.addCleanup(conn.close)
        path = conn.execute("PRAGMA database_list").fetchone()["file"]
        runs = lambda: conn.execute("SELECT count(*) FROM job_run WHERE job = 'rebuild'").fetchone()[0]
        self.assertEqual(runs(), 1)
        install.install(path)
        self.assertEqual(runs(), 1)  # same ruleset: no new rebuild
        rebuild(conn, version=V1)
        self.assertEqual(runs(), 2)
        install.install(path)  # the code's version differs from the stored one: rebuild
        self.assertEqual(runs(), 3)
        self.assertEqual(current(conn), ruleset_version())

    def test_install_main_prints_the_report(self):
        from outreach import install
        conn = fresh_store()
        self.addCleanup(conn.close)
        path = conn.execute("PRAGMA database_list").fetchone()["file"]
        rebuild(conn, version=V1)
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"OUTREACH_DB": path}), contextlib.redirect_stdout(out):
            self.assertEqual(install.main([]), 0)
        self.assertIn("shown to hidden", out.getvalue())


if __name__ == "__main__":
    unittest.main()
