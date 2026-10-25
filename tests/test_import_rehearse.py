"""The rehearsal (#17 Task 14): the whole import on copies, into a scratch folder, never on the live store."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import live_checkout
from outreach.importer.__main__ import main
from outreach.importer.records import SOURCES, source_digest
from outreach.importer.rehearse import rehearse
from outreach.store import db
from tests.importtest import fixture_manifest, make_contacts_empty

NOW = "2026-10-20T12:00:00Z"


class RehearseTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.manifest = fixture_manifest(self.tmp)

    def quiet(self, fn, *args, **kw):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = fn(*args, **kw)
        return result, out.getvalue()

    def test_rehearsal_copies_and_never_touches_the_originals(self):
        before = {s: source_digest(p) for s, p in self.manifest.paths.items()}
        into = self.tmp / "rehearsal"
        self.quiet(rehearse, self.manifest, into, now=NOW)
        self.assertEqual({s: source_digest(p) for s, p in self.manifest.paths.items()}, before)
        copied = into / "sources"
        self.assertEqual(sorted(p.name for p in copied.iterdir()), sorted(SOURCES))
        for source, path in self.manifest.paths.items():
            copy = copied / source / path.name
            self.assertTrue(copy.exists(), copy)
            if source != "radar_db":  # the radar copy is a backup: the same rows, not the same bytes
                self.assertEqual(source_digest(copy), before[source])
        self.assertTrue((into / "manifest.toml").is_file())
        self.assertTrue((into / "outreach.db").is_file())

    def test_rehearsal_on_the_fixture_passes(self):
        (report, failures), out = self.quiet(rehearse, self.manifest, self.tmp / "r", now=NOW)
        self.assertEqual(failures, [])
        self.assertIn("Second run changed nothing", out)
        self.assertTrue(out.rstrip().endswith("Rehearsal passed"), out[-300:])
        self.assertIn("kestrelrobotics", report.done_set)

    def test_rehearsal_report_file_is_written(self):
        into = self.tmp / "r"
        self.quiet(rehearse, self.manifest, into, now=NOW)
        text = (into / "report.txt").read_text(encoding="utf-8")
        self.assertIn("Unexplained losses: 0", text)
        self.assertIn("Second run changed nothing", text)
        self.assertIn("Rehearsal passed", text)

    def test_a_failed_check_fails_the_rehearsal_and_writes_nothing_to_its_store(self):
        contacts = self.manifest.paths["contacts_json"]
        contacts.write_text(contacts.read_text(encoding="utf-8").replace('"status": "followup_ready"',
                                                                         '"status": "sending"'), encoding="utf-8")
        into = self.tmp / "r"
        (_, failures), out = self.quiet(rehearse, self.manifest, into, now=NOW)
        self.assertTrue(failures)
        self.assertIn("Rehearsal failed:", out)
        self.assertIn("unclear_send", out)

    def empty_contacts(self, declare):
        import dataclasses
        make_contacts_empty(self.manifest)
        overrides = self.tmp / "overrides.toml"
        overrides.write_text('[allow_empty]\nsources = ["contacts_json"]\n' if declare else "", encoding="utf-8")
        return dataclasses.replace(self.manifest, overrides=overrides)

    def test_rehearsal_with_an_empty_contacts_file_passes_when_he_declares_it(self):
        manifest = self.empty_contacts(declare=True)
        into = self.tmp / "r"
        (report, failures), out = self.quiet(rehearse, manifest, into, now=NOW)
        self.assertEqual(failures, [], out)
        self.assertEqual(report.empty_on_purpose, ("contacts_json",))
        self.assertIn("contacts_json: empty on purpose (his ruling)", out)
        self.assertIn("Second run changed nothing", out)
        self.assertTrue(out.rstrip().endswith("Rehearsal passed"), out[-300:])
        self.assertIn("empty on purpose", (into / "report.txt").read_text(encoding="utf-8"))

    def test_rehearsal_with_an_empty_contacts_file_still_fails_without_the_declaration(self):
        manifest = self.empty_contacts(declare=False)
        (_, failures), out = self.quiet(rehearse, manifest, self.tmp / "r", now=NOW)
        self.assertTrue(any("contacts_json: no records at all" in f for f in failures), failures)
        self.assertIn("Rehearsal failed:", out)
        self.assertNotIn("empty on purpose", out)

    def unlisted_merge(self, ruling):
        """A radar posting merged into one radar.db does not list, with his [skip_record] for it or without."""
        import dataclasses
        import sqlite3
        conn = sqlite3.connect(self.manifest.paths["radar_db"])
        conn.execute("UPDATE posting SET merged_into = 999 WHERE posting_id = 2")
        conn.commit()
        conn.close()
        overrides = self.tmp / "overrides.toml"
        overrides.write_text('[skip_record]\n"radar_db:posting:2" = "a duplicate he does not track"\n'
                             if ruling else "", encoding="utf-8")
        return dataclasses.replace(self.manifest, overrides=overrides)

    def test_rehearsal_refuses_a_posting_merged_into_an_unlisted_one(self):
        (_, failures), out = self.quiet(rehearse, self.unlisted_merge(ruling=False), self.tmp / "r", now=NOW)
        self.assertTrue(any("radar_db: 1 held (unparsed_record)" in f for f in failures), failures)
        self.assertIn("was merged in radar.db into a posting it does not list", out)

    def test_rehearsal_passes_when_his_skip_record_settles_that_hold(self):
        (report, failures), out = self.quiet(rehearse, self.unlisted_merge(ruling=True), self.tmp / "r", now=NOW)
        self.assertEqual(failures, [], out)
        self.assertEqual(report.sources["radar_db"].skipped["by_override"], 1)
        self.assertNotIn("skip_record_unused", out)
        self.assertIn("Second run changed nothing", out)
        self.assertTrue(out.rstrip().endswith("Rehearsal passed"), out[-300:])

    def test_a_second_rehearsal_on_the_same_day_gets_its_own_folder(self):
        into = self.tmp / "2026-10-20"
        self.quiet(rehearse, self.manifest, into, now=NOW)
        (_, failures), out = self.quiet(rehearse, self.manifest, into, now=NOW)
        self.assertEqual(failures, [])
        self.assertTrue((self.tmp / "2026-10-20-2" / "report.txt").is_file())

    def test_rehearsal_refuses_a_live_target(self):
        live = self.tmp / "live"
        live.mkdir()
        with mock.patch.object(db, "DEFAULT_PATH", str(live / "outreach.db")):
            for into in (live, Path(live_checkout.LIVE_ROOT) / "rehearsal",
                         Path.home() / "Library" / "Application Support" / "outreach" / "rehearsal"):
                with self.subTest(into=str(into)):
                    existed = into.exists()
                    with self.assertRaises(SystemExit):
                        self.quiet(rehearse, self.manifest, into, now=NOW)
                    self.assertEqual(into.exists(), existed)
        self.assertEqual(os.listdir(live), [])

    def test_rehearsal_on_real_data_refuses_outside_the_live_checkout(self):
        from outreach.importer import manifest as manifest_module
        into = self.tmp / "real"
        stand_in = self.tmp / "committed.toml"  # if the guard ever broke, this names no source of his
        stand_in.write_text("[paths]\n", encoding="utf-8")
        with mock.patch.object(manifest_module, "DEFAULT_SOURCES", stand_in), \
                mock.patch.object(live_checkout, "is_live", return_value=False):
            with self.assertRaises(SystemExit) as cm:
                self.quiet(main, ["rehearse", "--into", str(into)])
        self.assertIn("live checkout", str(cm.exception))
        self.assertFalse(into.exists())

    def test_a_manifest_naming_one_real_source_needs_the_live_checkout(self):
        import dataclasses
        from outreach.importer import rehearse as module
        # The committed manifest is swapped for one whose "real" contacted.txt is a fixture copy, so even a broken
        # guard could never reach his files from this test.
        stand_in = self.tmp / "his" / "contacted.txt"
        stand_in.parent.mkdir()
        stand_in.write_text("# a stand-in for his real file\n", encoding="utf-8")
        committed = self.tmp / "committed.toml"
        committed.write_text(f'[paths]\ncontacted_txt = "{stand_in}"\n', encoding="utf-8")
        manifest = dataclasses.replace(self.manifest, paths={**self.manifest.paths, "contacted_txt": stand_in})
        into = self.tmp / "sneaky"
        with mock.patch.object(module, "DEFAULT_SOURCES", committed), \
                mock.patch.object(live_checkout, "is_live", return_value=False):
            with self.assertRaises(SystemExit) as cm:
                self.quiet(rehearse, manifest, into, now=NOW)
        self.assertIn("live checkout", str(cm.exception))
        self.assertFalse(into.exists())

    def test_the_command_runs_a_fixture_manifest_anywhere(self):
        sources = self.tmp / "sources.toml"
        sources.write_text("[paths]\n" + "".join(f'{k} = "{v}"\n' for k, v in self.manifest.paths.items()),
                           encoding="utf-8")
        into = self.tmp / "cli"
        with mock.patch.object(live_checkout, "is_live", return_value=False):
            code, out = self.quiet(main, ["rehearse", "--manifest", str(sources), "--into", str(into),
                                          "--now", NOW])
        self.assertEqual(code, 0, out)
        self.assertIn("Rehearsal passed", out)


if __name__ == "__main__":
    unittest.main()
