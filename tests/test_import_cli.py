"""The import command line (#17 Task 13), on the fixture sources and scratch stores only."""
import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import live_checkout
from outreach.importer.__main__ import main
from outreach.importer.records import source_digest
from outreach.install import install
from outreach.store import db
from tests.importtest import all_marks, fixture_manifest

NOW = "2026-10-20T12:00:00Z"


class CliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.manifest = fixture_manifest(self.tmp)
        self.sources = self.tmp / "sources.toml"
        lines = ["[paths]"] + [f'{k} = "{v}"' for k, v in self.manifest.paths.items()]
        self.sources.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.db = str(self.tmp / "store" / "outreach.db")
        os.makedirs(os.path.dirname(self.db))
        install(self.db)
        env = mock.patch.dict(os.environ, {"OUTREACH_DB": self.db})
        env.start()
        self.addCleanup(env.stop)

    def cli(self, *args) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main([args[0], "--manifest", str(self.sources), "--now", NOW, *args[1:]])
        return code, out.getvalue()

    def store(self) -> sqlite3.Connection:
        conn = db.connect(self.db)
        self.addCleanup(conn.close)
        return conn

    def set_status(self, contact_id, status):
        path = self.manifest.paths["contacts_json"]
        data = json.loads(path.read_text(encoding="utf-8"))
        for c in data["contacts"]:
            if c["id"] == contact_id:
                c["status"] = status
        path.write_text(json.dumps(data), encoding="utf-8")

    # -- the plan's tests --

    def test_preflight_passes_on_the_base_fixture(self):
        code, out = self.cli("preflight")
        self.assertEqual(code, 0, out)
        self.assertIn("outreach-backup-2026-10-22.json", out)
        self.assertIn("contacted_txt: found", out)
        self.assertIn("Unclear sends: 0", out)

    def test_preflight_flags_the_unclear_send(self):
        self.set_status("c05", "sending")
        code, out = self.cli("preflight")
        self.assertEqual(code, 1)
        self.assertIn("c05", out)
        self.assertIn("Emil Strand", out)

    def test_preflight_names_a_missing_source(self):
        self.manifest.paths["contacted_txt"].unlink()
        code, out = self.cli("preflight")
        self.assertEqual(code, 1)
        self.assertIn("contacted_txt: missing", out)

    def test_dry_run_writes_nothing(self):
        before = all_marks(self.store())
        code, out = self.cli("dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("Unexplained losses: 0", out)
        self.assertEqual(all_marks(self.store()), before)

    def test_run_refuses_when_held(self):
        self.set_status("c05", "send_unknown")
        code, out = self.cli("run")
        self.assertEqual(code, 1)
        self.assertIn("unclear_send", out)
        self.assertEqual(self.store().execute("SELECT count(*) FROM import_record").fetchone()[0], 0)

    def test_run_on_the_base_fixture_passes_and_the_second_run_changes_nothing(self):
        code, out = self.cli("run")
        self.assertEqual(code, 0, out)
        self.assertIn("Unexplained losses: 0", out)
        self.assertIn("Second run changed nothing", out)
        conn = self.store()
        runs = conn.execute("SELECT status, summary FROM job_run WHERE job = 'import' ORDER BY run_id").fetchall()
        self.assertEqual([r["status"] for r in runs], ["ok", "ok"])
        stored = json.loads(runs[0]["summary"])
        self.assertIn("report", stored)  # the count report, for `report` and #5's retire guard
        self.assertIn("radar_tables", stored["report"])
        code, out = self.cli("report")
        self.assertEqual(code, 0, out)
        self.assertIn("Unexplained losses: 0", out)
        self.assertIn("contacts_json imported", out)

    def test_reconcile_counts_match_on_the_fixture(self):
        code, out = self.cli("reconcile")
        self.assertEqual(code, 1)  # nothing imported yet: every sent email is missing
        self.assertIn("Sent in the store: 0", out)
        self.assertEqual(self.cli("run")[0], 0)
        code, out = self.cli("reconcile")
        self.assertEqual(code, 0, out)
        self.assertIn("Sent in the old mailer: 8", out)
        self.assertIn("Sent in the store: 8", out)
        self.assertIn("Sent lines in send.log: 7", out)
        self.assertIn("Missing: 0", out)

    def test_rollback_check_lists_a_person_sent_after_the_switch(self):
        self.assertEqual(self.cli("run")[0], 0)
        code, out = self.cli("rollback-check", "--since", "2026-10-25T00:00:00Z")
        self.assertEqual(code, 0)
        self.assertIn("Nothing to check", out)
        conn = self.store()
        gus = conn.execute("SELECT m.message_id FROM message m JOIN touch t USING(touch_id) JOIN person p "
                           "USING(person_id) WHERE p.email = 'gus.albrecht@example.com'").fetchone()[0]
        at = "2026-10-26T14:00:00Z"
        conn.execute("UPDATE message SET state = 'approved', approved_at = ? WHERE message_id = ?", (at, gus))
        conn.execute("UPDATE message SET state = 'sending', pending_rfc_id = '<new-1@example.com>', claimed_at = ? "
                     "WHERE message_id = ?", (at, gus))
        conn.execute("UPDATE message SET state = 'sent', pending_rfc_id = NULL, claimed_at = NULL, sent_at = ?, "
                     "rfc_message_id = '<new-1@example.com>' WHERE message_id = ?", (at, gus))
        code, out = self.cli("rollback-check", "--since", "2026-10-25T00:00:00Z")
        self.assertEqual(code, 1)
        self.assertIn("gus.albrecht@example.com", out)
        self.assertIn("approved", out)

    def test_rollback_check_lists_a_message_still_in_flight(self):
        self.assertEqual(self.cli("run")[0], 0)
        conn = self.store()
        gus = conn.execute("SELECT m.message_id FROM message m JOIN touch t USING(touch_id) JOIN person p "
                           "USING(person_id) WHERE p.email = 'gus.albrecht@example.com'").fetchone()[0]
        at = "2026-10-26T14:00:00Z"
        conn.execute("UPDATE message SET state = 'approved', approved_at = ? WHERE message_id = ?", (at, gus))
        conn.execute("UPDATE message SET state = 'sending', pending_rfc_id = '<new-2@example.com>', claimed_at = ? "
                     "WHERE message_id = ?", (at, gus))
        code, out = self.cli("rollback-check", "--since", "2026-10-25T00:00:00Z")
        self.assertEqual(code, 1)
        self.assertIn("gus.albrecht@example.com", out)
        self.assertIn("Check Gmail Sent", out)

    def test_run_on_the_live_path_from_a_worktree_exits_through_the_guard(self):
        live = self.tmp / "live" / "outreach.db"
        live.parent.mkdir()
        live.write_bytes(b"not a store")
        before = source_digest(live)
        env = {k: v for k, v in os.environ.items() if k != "OUTREACH_DB"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(db, "DEFAULT_PATH", str(live)), \
                mock.patch.object(live_checkout, "is_live", return_value=False):
            for command in ("dry-run", "run", "report", "reconcile"):
                with self.subTest(command=command):
                    with self.assertRaises(SystemExit) as cm:
                        self.cli(command)
                    self.assertIn("live checkout", str(cm.exception))
        self.assertEqual(source_digest(live), before)
        self.assertEqual(os.listdir(live.parent), ["outreach.db"])

    def test_run_on_real_files_from_a_worktree_exits_before_reading_or_writing(self):
        before = self.store().execute("SELECT count(*) FROM import_record").fetchone()[0]
        with mock.patch.object(live_checkout, "is_live", return_value=False):
            with self.assertRaises(SystemExit) as cm:  # no --manifest: the committed manifest, his real files
                main(["run", "--now", NOW])
            self.assertIn("live checkout", str(cm.exception))
            with mock.patch("outreach.importer.__main__.is_real", return_value=True):
                with self.assertRaises(SystemExit) as cm:  # a manifest that names his real paths
                    self.cli("run")
                self.assertIn("live checkout", str(cm.exception))
        self.assertEqual(self.store().execute("SELECT count(*) FROM import_record").fetchone()[0], before)


if __name__ == "__main__":
    unittest.main()
