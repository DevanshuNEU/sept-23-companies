import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from outreach.importer import read_data_js as module
from outreach.importer.read_data_js import read_data_js
from outreach.importer.records import SourceError, SourceRecord, source_digest
from tests.importtest import fixture_manifest

NODE = shutil.which("node") or (module.NVM_NODE if os.path.exists(module.NVM_NODE) else None)


@unittest.skipUnless(NODE, "Node is not installed here (looked on PATH and in ~/.nvm); the data.js reader needs it")
class ReadDataJsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.paths = fixture_manifest(self.tmp).paths

    def js(self, text):
        path = self.tmp / "own.js"
        path.write_text(text, encoding="utf-8")
        return path

    def test_fixture_data_js_gives_five_companies_two_ruled_out_and_meta(self):
        records = list(read_data_js(self.paths["data_js"]))
        for record in records:
            self.assertIsInstance(record, SourceRecord)
            self.assertEqual(record.source, "data_js")
        keys = [r.source_key for r in records]
        self.assertEqual(keys, [
            "company:kestrel", "company:lumen", "company:orbital", "company:quarry", "company:tidewater",
            "ruled_out:1", "ruled_out:2", "meta", "reference"])
        raw = {r.source_key: r.raw for r in records}
        self.assertEqual(raw["company:kestrel"]["name"], "Kestrel Robotics")
        self.assertEqual(raw["company:kestrel"]["seed"], "Interviewing")
        self.assertEqual(raw["company:kestrel"]["outreach"]["domain"], "kestrel.example")
        self.assertTrue(raw["company:lumen"]["visaDenied"])
        self.assertEqual(raw["company:orbital"]["sizeClass"], "cleared")
        self.assertIn("well over 500 people", raw["company:quarry"]["sizeWhy"])
        self.assertEqual(raw["ruled_out:1"]["n"], "Saltmarsh Bio")
        self.assertEqual(raw["ruled_out:2"]["n"], "Ferrous Grid / Tidewater AI")
        self.assertEqual(raw["meta"], {"refreshedAt": "2026-10-15T20:25"})
        self.assertEqual(raw["reference"]["floor"], 15.29)

    def test_data_js_cannot_require(self):
        path = self.js("window.COMPANIES = []; window.RULED_OUT = []; window.META = {}; window.REFERENCE = {};\n"
                       "require('fs').writeFileSync('should-not-exist.txt', 'x');\n")
        with self.assertRaises(SourceError) as cm:
            list(read_data_js(path))
        self.assertIn("data_js", str(cm.exception))
        self.assertIn("require", str(cm.exception))
        self.assertFalse(Path("should-not-exist.txt").exists())

    def test_data_js_cannot_reach_process_through_the_global_object(self):
        path = self.js("window.COMPANIES = []; window.RULED_OUT = []; window.META = {}; window.REFERENCE = {};\n"
                       "this.constructor.constructor('return process')().exit(0);\n")
        with self.assertRaises(SourceError):
            list(read_data_js(path))

    def test_a_script_that_never_ends_is_a_source_error(self):
        path = self.js("while (true) {}\n")
        with mock.patch.object(module, "VM_TIMEOUT_MS", 300):
            with self.assertRaises(SourceError) as cm:
                list(read_data_js(path))
        self.assertIn("data_js", str(cm.exception))

    def test_a_syntax_error_is_a_source_error_with_nodes_message(self):
        with self.assertRaises(SourceError) as cm:
            list(read_data_js(self.js("window.COMPANIES = [ {\n")))
        self.assertIn("SyntaxError", str(cm.exception))

    def test_missing_node_is_a_source_error(self):
        with self.assertRaises(SourceError) as cm:
            list(read_data_js(self.paths["data_js"], node=str(self.tmp / "no-node")))
        self.assertIn("data_js", str(cm.exception))
        self.assertIn("no-node", str(cm.exception))
        with mock.patch.object(module.shutil, "which", return_value=None), \
                mock.patch.object(module, "NVM_NODE", str(self.tmp / "no-nvm-node")):
            with self.assertRaises(SourceError) as cm:
                list(read_data_js(self.paths["data_js"]))
        self.assertIn("Node", str(cm.exception))

    def test_node_that_hangs_is_a_source_error(self):
        slow = self.tmp / "slow-node"
        slow.write_text("#!/bin/sh\nexec sleep 5\n")
        slow.chmod(slow.stat().st_mode | stat.S_IXUSR)
        with mock.patch.object(module, "NODE_TIMEOUT_S", 0.3):
            with self.assertRaises(SourceError) as cm:
                list(read_data_js(self.paths["data_js"], node=str(slow)))
        self.assertIn("timed out", str(cm.exception))

    def test_a_missing_file_is_a_named_source_error(self):
        missing = self.tmp / "nowhere.js"
        with self.assertRaises(SourceError) as cm:
            list(read_data_js(missing))
        self.assertIn("data_js", str(cm.exception))
        self.assertIn(str(missing), str(cm.exception))

    def test_a_file_without_the_four_lists_is_a_source_error_not_zero_records(self):
        for text in ("window.COMPANIES = [];\n",
                     "window.COMPANIES = []; window.RULED_OUT = []; window.META = {};\n",
                     "window.COMPANIES = {}; window.RULED_OUT = []; window.META = {}; window.REFERENCE = {};\n",
                     "// empty\n"):
            with self.subTest(text=text):
                with self.assertRaises(SourceError):
                    list(read_data_js(self.js(text)))

    def test_a_company_without_an_id_or_with_a_repeated_one_is_a_source_error(self):
        head = "window.RULED_OUT = []; window.META = {}; window.REFERENCE = {};\n"
        for companies in ('[{name: "No Id"}]', '[{id: "a"}, {id: "a"}]', '[{id: ""}]'):
            with self.subTest(companies=companies):
                with self.assertRaises(SourceError):
                    list(read_data_js(self.js(head + f"window.COMPANIES = {companies};\n")))

    def test_a_ruled_out_entry_without_a_name_is_a_source_error(self):
        text = ("window.COMPANIES = []; window.META = {}; window.REFERENCE = {};\n"
                'window.RULED_OUT = [{why: "no name"}];\n')
        with self.assertRaises(SourceError):
            list(read_data_js(self.js(text)))

    def test_data_js_file_is_unchanged_after_reading(self):
        before = source_digest(self.paths["data_js"])
        before_dir = sorted(p.name for p in self.paths["data_js"].parent.iterdir())
        self.assertEqual(len(list(read_data_js(self.paths["data_js"]))), 9)
        self.assertEqual(source_digest(self.paths["data_js"]), before)
        self.assertEqual(sorted(p.name for p in self.paths["data_js"].parent.iterdir()), before_dir)

    def test_the_raw_record_round_trips_as_json(self):
        for record in read_data_js(self.paths["data_js"]):
            self.assertEqual(json.loads(record.raw_json()), record.raw)


if __name__ == "__main__":
    unittest.main()
