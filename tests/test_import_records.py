import hashlib
import json
import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from live_checkout import LIVE_ROOT
from outreach.importer import IMPORTER_VERSION
from outreach.importer.manifest import Manifest, Overrides, load_manifest, load_overrides
from outreach.importer.records import (
    SOURCES,
    SourceError,
    SourceRecord,
    open_radar,
    source_digest,
)
from tests.importtest import FIXTURES, all_marks, fixture_manifest

# Every company the fixture set may name. A real company in a fixture fails the last test.
SYNTHETIC_COMPANIES = {
    "Kestrel Robotics", "Lumen Freight", "Orbital Foods", "Quarry Labs", "Tidewater AI", "Saltmarsh Bio",
    "Ferrous Grid", "Marlow Dynamics", "Pinewood Logistics", "Cobalt Harbor", "Ember Analytics",
    "Fathom Systems", "Juniper Optics", "Granite Peak Software",
}


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class RecordTest(unittest.TestCase):
    def test_importer_version_and_sources(self):
        self.assertEqual(IMPORTER_VERSION, "import-1")
        self.assertEqual(len(SOURCES), 10)
        self.assertEqual(len(set(SOURCES)), 10)

    def test_record_hash_is_order_free_json(self):
        a = SourceRecord("data_js", "company:1", {"a": 1, "b": [1, 2], "c": {"x": 1, "y": 2}})
        b = SourceRecord("data_js", "company:1", {"c": {"y": 2, "x": 1}, "b": [1, 2], "a": 1})
        self.assertEqual(a.raw_json(), b.raw_json())
        self.assertEqual(a.raw_sha256(), b.raw_sha256())
        self.assertEqual(a.raw_json(), '{"a":1,"b":[1,2],"c":{"x":1,"y":2}}')
        self.assertEqual(a.raw_sha256(), hashlib.sha256(a.raw_json().encode("utf-8")).hexdigest())
        c = SourceRecord("data_js", "company:1", {"a": 2})
        self.assertNotEqual(a.raw_sha256(), c.raw_sha256())

    def test_record_keeps_non_ascii_text_unescaped(self):
        record = SourceRecord("contacted_txt", "line:1", {"line": "café"})
        self.assertIn("café", record.raw_json())

    def test_record_refuses_unknown_source_and_empty_key(self):
        with self.assertRaises(ValueError):
            SourceRecord("gmail", "k", {})
        with self.assertRaises(ValueError):
            SourceRecord("data_js", "", {})
        for source in SOURCES:
            SourceRecord(source, "k", {})


class ManifestTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def toml(self, local_storage="{home}/backups/outreach-backup-*.json"):
        return write(self.tmp / "sources.toml", f"""
[paths]
data_js = "{{live}}/dashboard/data.js"
local_storage = "{local_storage}"
contacts_json = "{{mail_home}}/contacts.json"
send_log = "{{mail_home}}/send.log"
radar_db = "~/radar/radar.db"
devboard = "~/devboard/job-state.json"
contacted_txt = "~/notes/contacted.txt"
devvault_notes = "~/notes/companies"
interview_prep = "~/prep/companies"
curated_touches = "~/recon/curated.csv"

[overrides]
path = "~/scratch/overrides.toml"
""")

    def test_manifest_expands_live_and_mail_home(self):
        path = self.toml(local_storage="~/backups/outreach-backup-*.json")
        home = os.path.expanduser("~")
        plain = load_manifest(path, env={})
        self.assertEqual(plain.paths["data_js"], Path(LIVE_ROOT) / "dashboard" / "data.js")
        self.assertEqual(plain.paths["contacts_json"], Path(LIVE_ROOT) / "mail" / "contacts.json")
        self.assertEqual(plain.paths["radar_db"], Path(home) / "radar" / "radar.db")
        self.assertEqual(plain.overrides, Path(home) / "scratch" / "overrides.toml")
        self.assertEqual(set(plain.paths), set(SOURCES))
        scratch = load_manifest(path, env={"OUTREACH_MAIL_HOME": str(self.tmp / "mail")})
        self.assertEqual(scratch.paths["contacts_json"], self.tmp / "mail" / "contacts.json")
        self.assertEqual(scratch.paths["send_log"], self.tmp / "mail" / "send.log")
        self.assertEqual(scratch.paths["data_js"], Path(LIVE_ROOT) / "dashboard" / "data.js")

    def test_manifest_picks_the_newest_backup_by_name(self):
        folder = self.tmp / "backups"
        for day in ("2026-10-02", "2026-10-21", "2026-09-30"):
            write(folder / f"outreach-backup-{day}.json", "{}")
        path = self.toml(local_storage=f"{folder}/outreach-backup-*.json")
        manifest = load_manifest(path, env={})
        self.assertEqual(manifest.paths["local_storage"], folder / "outreach-backup-2026-10-21.json")

    def test_no_backup_leaves_a_path_the_reader_will_name(self):
        path = self.toml(local_storage=f"{self.tmp}/none/outreach-backup-*.json")
        manifest = load_manifest(path, env={})
        self.assertFalse(manifest.paths["local_storage"].exists())

    def test_shipped_manifest_loads_and_names_every_source(self):
        manifest = load_manifest(env={})
        self.assertEqual(set(manifest.paths), set(SOURCES))
        self.assertIsNotNone(manifest.overrides)
        self.assertIsInstance(manifest, Manifest)

    def test_missing_source_is_a_named_error_not_zero_records(self):
        missing = self.tmp / "nothing.db"
        with self.assertRaises(SourceError) as cm:
            open_radar(missing)
        self.assertIn("radar_db", str(cm.exception))
        self.assertIn(str(missing), str(cm.exception))
        with self.assertRaises(SourceError) as cm:
            source_digest(missing)
        self.assertIn(str(missing), str(cm.exception))
        self.assertIsInstance(SourceError("x"), Exception)

    def test_source_digest_of_a_folder_covers_names_and_contents(self):
        folder = self.tmp / "notes"
        write(folder / "a.md", "one")
        write(folder / "sub" / "b.md", "two")
        first = source_digest(folder)
        self.assertEqual(first, source_digest(folder))
        write(folder / "sub" / "b.md", "three")
        self.assertNotEqual(first, source_digest(folder))
        write(folder / "sub" / "b.md", "two")
        self.assertEqual(first, source_digest(folder))
        (folder / "sub" / "b.md").rename(folder / "sub" / "c.md")
        self.assertNotEqual(first, source_digest(folder))
        one = write(self.tmp / "f.txt", "hello")
        self.assertEqual(source_digest(one), hashlib.sha256(b"hello").hexdigest())

    def test_open_radar_is_read_only(self):
        manifest = fixture_manifest(self.tmp)
        radar = manifest.paths["radar_db"]
        before = source_digest(radar)
        conn = open_radar(radar)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO meta(key, value) VALUES ('x', 'y')")
            # the open mode is the guarantee, not the pragma: with query_only off the file still refuses a write
            conn.execute("PRAGMA query_only = OFF")
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO meta(key, value) VALUES ('x', 'y')")
            self.assertGreater(conn.execute("SELECT count(*) FROM posting").fetchone()[0], 0)
        finally:
            conn.close()
        self.assertEqual(source_digest(radar), before)
        self.assertFalse(Path(f"{radar}-wal").exists())
        self.assertFalse(Path(f"{radar}-shm").exists())


class OverridesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_a_missing_path_gives_empty_overrides(self):
        for path in (None, self.tmp / "absent.toml"):
            got = load_overrides(path)
            self.assertEqual(got, Overrides({}, frozenset(), {}, frozenset(), frozenset(), {}))

    def test_overrides_load_every_table(self):
        path = write(self.tmp / "o.toml", """
[same_company]
tidewater = "tidewaterai"
[not_same_company]
pairs = [["marlow", "marlowdynamics"]]
[person_name]
"contacted_txt:line:9" = "Ada Fenwick"
[done_add]
keys = ["quarrylabs"]
[done_remove]
keys = ["kestrelrobotics"]
[touch_for]
"curated_touches:row:3" = "contacted_txt:line:7"
[skip_record]
"contacted_txt:line:12" = "a stray note"
""")
        got = load_overrides(path)
        self.assertEqual(got.same_company, {"tidewater": "tidewaterai"})
        self.assertEqual(got.not_same_company, frozenset({frozenset({"marlow", "marlowdynamics"})}))
        self.assertEqual(got.person_name, {"contacted_txt:line:9": "Ada Fenwick"})
        self.assertEqual(got.done_add, frozenset({"quarrylabs"}))
        self.assertEqual(got.done_remove, frozenset({"kestrelrobotics"}))
        self.assertEqual(got.touch_for, {"curated_touches:row:3": "contacted_txt:line:7"})
        self.assertEqual(got.skip_record, {"contacted_txt:line:12": "a stray note"})
        self.assertEqual(load_overrides(None).skip_record, {})

    def test_overrides_refuse_an_unknown_table(self):
        path = write(self.tmp / "o.toml", '[same_compny]\na = "b"\n')
        with self.assertRaises(ValueError) as cm:
            load_overrides(path)
        self.assertIn("same_compny", str(cm.exception))
        write(self.tmp / "top.toml", 'stray = "x"\n')
        with self.assertRaises(ValueError):
            load_overrides(self.tmp / "top.toml")

    def test_overrides_refuse_a_bad_shape(self):
        path = write(self.tmp / "o.toml", "[not_same_company]\npairs = [[\"only-one\"]]\n")
        with self.assertRaises(ValueError):
            load_overrides(path)
        path = write(self.tmp / "p.toml", "[same_company]\na = 3\n")
        with self.assertRaises(ValueError):
            load_overrides(path)


class FixtureSetTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_fixture_manifest_copies_every_source(self):
        manifest = fixture_manifest(self.tmp)
        self.assertEqual(set(manifest.paths), set(SOURCES))
        for source, path in manifest.paths.items():
            with self.subTest(source=source):
                self.assertTrue(path.exists(), path)
                self.assertTrue(str(path).startswith(str(self.tmp)))
        # the copy is separate from the committed fixtures
        self.assertNotEqual(manifest.paths["data_js"], FIXTURES / "data.js")

    def test_contacts_cover_every_status(self):
        manifest = fixture_manifest(self.tmp)
        db = json.loads(manifest.paths["contacts_json"].read_text(encoding="utf-8"))
        statuses = {c["status"] for c in db["contacts"]}
        self.assertEqual(statuses, {
            "drafted", "approved", "sent", "replied", "bounced", "followup_ready", "followup_sent", "closed",
            "skipped", "failed"})
        self.assertEqual(len(db["contacts"]), 10)
        self.assertTrue(db["firstSendDate"])

    def test_fixture_radar_has_the_planned_rows(self):
        manifest = fixture_manifest(self.tmp)
        conn = open_radar(manifest.paths["radar_db"])
        try:
            one = lambda sql: conn.execute(sql).fetchone()[0]
            self.assertEqual(one("SELECT count(*) FROM board"), 3)
            self.assertEqual(one("SELECT count(*) FROM posting"), 6)
            self.assertGreater(one("SELECT count(*) FROM posting_version"), 6)
            kinds = {r[0] for r in conn.execute("SELECT kind FROM app_event")}
            self.assertEqual(kinds, {"opened", "applied", "ignored", "screen"})
            self.assertEqual(one("SELECT count(*) FROM posting_live"), 6)
            self.assertEqual(one("PRAGMA user_version"), 5)
        finally:
            conn.close()

    def test_all_marks_counts_rows_and_the_highest_rowid(self):
        from tests.storetest import fresh_store
        conn = fresh_store()
        self.addCleanup(conn.close)
        before = all_marks(conn)
        self.assertEqual(before["import_record"], (0, 0))
        self.assertIn("company", before)
        conn.execute("INSERT INTO job_run(job, started_at) VALUES ('import', '2026-10-19T12:00:00Z')")
        after = all_marks(conn)
        self.assertEqual(after["job_run"], (before["job_run"][0] + 1, before["job_run"][1] + 1))
        self.assertEqual({k: v for k, v in after.items() if k != "job_run"},
                         {k: v for k, v in before.items() if k != "job_run"})

    def test_fixture_set_has_no_real_names(self):
        manifest = fixture_manifest(self.tmp)
        found = set()
        text = manifest.paths["data_js"].read_text(encoding="utf-8")
        found |= set(re.findall(r'\bname:\s*"([^"]+)"', text))
        for entry in re.findall(r'\bn:\s*"([^"]+)"', text):
            found |= {part.strip() for part in entry.split("/")}
        db = json.loads(manifest.paths["contacts_json"].read_text(encoding="utf-8"))
        found |= {c["company"] for c in db["contacts"]}
        for line in manifest.paths["contacted_txt"].read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\d{4}-\d{2}-\d{2} - (.+?) - ", line)
            if m:
                found.add(m.group(1))
        for note in manifest.paths["devvault_notes"].rglob("*.md"):
            m = re.search(r"^company:\s*(.+)$", note.read_text(encoding="utf-8"), re.M)
            if m:
                found.add(m.group(1).strip().strip('"'))
        for brief in manifest.paths["interview_prep"].rglob("brief.md"):
            m = re.search(r"^company:\s*(.+)$", brief.read_text(encoding="utf-8"), re.M)
            if m:
                found.add(m.group(1).strip().strip('"'))
        import csv
        with open(manifest.paths["curated_touches"], encoding="utf-8", newline="") as f:
            found |= {row["company"] for row in csv.DictReader(f)}
        for value in json.loads(manifest.paths["devboard"].read_text(encoding="utf-8")).values():
            found.add(value["company"])
        for value in json.loads(manifest.paths["local_storage"].read_text(encoding="utf-8")).values():
            self.assertIsInstance(value, dict)
        conn = open_radar(manifest.paths["radar_db"])
        try:
            found |= {r[0] for r in conn.execute("SELECT company FROM board")}
        finally:
            conn.close()
        self.assertGreater(len(found), 8)
        self.assertEqual(found - SYNTHETIC_COMPANIES, set())

    def test_fixtures_carry_only_example_addresses(self):
        manifest = fixture_manifest(self.tmp)
        text = ""
        for path in self.tmp.rglob("*"):
            if path.is_file() and path.suffix in {".json", ".js", ".md", ".txt", ".csv", ".log", ".toml"}:
                text += path.read_text(encoding="utf-8") + "\n"
        emails = set(re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text))
        self.assertTrue(emails)
        self.assertEqual({e for e in emails if not e.endswith("@example.com")}, set())
        for url in re.findall(r"https?://[^\s\"')>]*linkedin[^\s\"')>]*", text):
            self.assertIn("example", url)
        self.assertIsInstance(manifest, Manifest)
        self.assertNotIn(chr(0x2013), text)
        self.assertNotIn(chr(0x2014), text)


if __name__ == "__main__":
    unittest.main()
