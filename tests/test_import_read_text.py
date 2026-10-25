import json
import tempfile
import unittest
from pathlib import Path

from outreach.importer.read_text import (
    read_contacted_txt,
    read_contacts_json,
    read_curated_touches,
    read_devboard,
    read_local_storage,
    read_send_log,
)
from outreach.importer.records import SourceError, SourceRecord, source_digest
from tests.importtest import fixture_manifest


class ReadTextTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.paths = fixture_manifest(self.tmp).paths

    def by_key(self, records):
        records = list(records)
        for record in records:
            self.assertIsInstance(record, SourceRecord)
        keys = [r.source_key for r in records]
        self.assertEqual(len(keys), len(set(keys)), "every record has its own key")
        return {r.source_key: r.raw for r in records}

    def write(self, name, text):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    # ---- contacted.txt ---------------------------------------------------

    def test_contacted_line_with_two_people_gives_two_names_and_notes(self):
        raw = self.by_key(read_contacted_txt(self.paths["contacted_txt"]))
        line = raw["line:7"]
        self.assertEqual(line["date"], "2026-10-07")
        self.assertEqual(line["company"], "Marlow Dynamics")
        self.assertEqual(line["people"], [
            {"name": "Cleo Hartmann", "note": "recruiter"},
            {"name": "Ned Pruitt", "note": "EM"},
        ])
        single = raw["line:5"]
        self.assertEqual(single["people"], [{"name": "Ada Fenwick", "note": "engineer, email"}])
        self.assertEqual(raw["line:6"]["people"], [{"name": "Bram Okafor", "note": None}])
        self.assertEqual(raw["line:9"]["people"], [{"name": "Emil", "note": None}])

    def test_contacted_line_with_a_dash_in_the_company_keeps_the_first_split(self):
        raw = self.by_key(read_contacted_txt(self.paths["contacted_txt"]))
        line = raw["line:8"]
        self.assertEqual(line["company"], "Pinewood Logistics")
        self.assertEqual(line["rest"], "Dara Venn - bounced, tried LinkedIn instead")
        self.assertEqual(line["line"], "2026-10-08 - Pinewood Logistics - Dara Venn - bounced, tried LinkedIn instead")

    def test_comment_blank_and_unparsed_lines_are_records_with_their_kind(self):
        raw = self.by_key(read_contacted_txt(self.paths["contacted_txt"]))
        self.assertEqual(len(raw), 10)
        self.assertEqual({raw[f"line:{n}"]["kind"] for n in (1, 2, 3)}, {"comment"})
        self.assertEqual(raw["line:4"], {"line": "", "kind": "blank"})
        self.assertEqual(raw["line:10"]["kind"], "unparsed")
        self.assertNotIn("date", raw["line:10"])
        self.assertEqual(raw["line:5"]["kind"], "touch")

    def test_contacted_txt_keeps_a_blank_with_spaces_and_a_crlf_ending(self):
        path = self.write("c.txt", "   \r\n2026-10-01 - Acme Test - Jo Example\r\n")
        raw = self.by_key(read_contacted_txt(path))
        self.assertEqual(raw["line:1"]["kind"], "blank")
        self.assertEqual(raw["line:2"]["company"], "Acme Test")
        self.assertEqual(raw["line:2"]["line"], "2026-10-01 - Acme Test - Jo Example")

    # ---- curated touches ------------------------------------------------

    def test_curated_row_keeps_every_column_as_text(self):
        rows = self.by_key(read_curated_touches(self.paths["curated_touches"]))
        self.assertEqual(len(rows), 4)
        first = rows["row:1"]
        self.assertEqual(list(first), ["date", "company", "band", "role", "channel", "outcome", "src", "fu", "req"])
        self.assertEqual(first["req"], "R-100")
        self.assertEqual(first["fu"], "")
        self.assertTrue(all(isinstance(v, str) for row in rows.values() for v in row.values()))
        self.assertEqual(rows["row:2"]["fu"], "y")

    def test_curated_file_without_the_columns_is_a_source_error(self):
        path = self.write("curated.csv", "date,company\n2026-10-06,Acme Test\n")
        with self.assertRaises(SourceError) as cm:
            list(read_curated_touches(path))
        self.assertIn("curated_touches", str(cm.exception))

    # ---- devboard ---------------------------------------------------------

    def test_devboard_records_one_per_key_with_the_parsed_key(self):
        raw = self.by_key(read_devboard(self.paths["devboard"]))
        self.assertEqual(len(raw), 6)
        gh = raw["job:greenhouse:kestrel:1001"]
        self.assertEqual(gh["parsed_key"], {"ats": "greenhouse", "token": "kestrel", "native_id": "1001"})
        self.assertEqual(gh["company"], "Kestrel Robotics")
        self.assertEqual(gh["status"], "applied")
        self.assertEqual(raw["job:ashby:marlow:aa-11"]["parsed_key"],
                         {"ats": "ashby", "token": "marlow", "native_id": "aa-11"})
        self.assertEqual(raw["job:lever:tidewater:6f1c0a52"]["parsed_key"],
                         {"ats": "lever", "token": "tidewater", "native_id": "6f1c0a52"})

    def test_devboard_workday_key_becomes_the_shared_token_format(self):
        raw = self.by_key(read_devboard(self.paths["devboard"]))
        self.assertEqual(raw["job:workday:acme:wd5:External:R-1"]["parsed_key"],
                         {"ats": "workday", "token": "acme.wd5/External", "native_id": "R-1"})

    def test_devboard_hcafe_key_has_no_parsed_key(self):
        raw = self.by_key(read_devboard(self.paths["devboard"]))
        self.assertIsNone(raw["job:hcafe:fixture-9"]["parsed_key"])
        self.assertIsNone(raw["job:apify:fixture-12"]["parsed_key"])

    def test_devboard_key_with_the_wrong_number_of_parts_has_no_parsed_key(self):
        path = self.write("job-state.json", json.dumps({
            "greenhouse:onlytwo": {"company": "Acme Test"},
            "workday:acme:wd5:External": {"company": "Acme Test"},
        }))
        raw = self.by_key(read_devboard(path))
        self.assertTrue(all(v["parsed_key"] is None for v in raw.values()))

    # ---- tracker backup ---------------------------------------------------

    def test_local_storage_gives_one_record_per_company_id_as_exported(self):
        exported = json.loads(self.paths["local_storage"].read_text(encoding="utf-8"))
        raw = self.by_key(read_local_storage(self.paths["local_storage"]))
        self.assertEqual(set(raw), {f"company:{k}" for k in exported})
        self.assertEqual(raw["company:kestrel"], exported["kestrel"])
        self.assertEqual(raw["company:orbital"]["skipped"], "2026-10-03")

    def test_local_storage_that_is_not_an_object_is_a_source_error(self):
        with self.assertRaises(SourceError):
            list(read_local_storage(self.write("backup.json", "[1, 2]")))

    # ---- contacts.json ----------------------------------------------------

    def test_contacts_json_one_record_per_contact_with_first_send_date(self):
        raw = self.by_key(read_contacts_json(self.paths["contacts_json"]))
        self.assertEqual(len(raw), 10)
        replied = raw["contact:c01"]
        self.assertEqual(replied["status"], "replied")
        self.assertEqual(replied["email"], "ada.fenwick@example.com")
        self.assertEqual(replied["first_send_date"], "2026-10-06")
        self.assertEqual({r["first_send_date"] for r in raw.values()}, {"2026-10-06"})
        self.assertEqual(replied["history"][0]["what"], "drafted")

    def test_contacts_json_with_a_repeated_id_is_a_source_error(self):
        path = self.write("contacts.json", json.dumps({"contacts": [{"id": "a"}, {"id": "a"}]}))
        with self.assertRaises(SourceError):
            list(read_contacts_json(path))
        path = self.write("contacts2.json", json.dumps({"contacts": [{"name": "no id"}]}))
        with self.assertRaises(SourceError):
            list(read_contacts_json(path))

    def test_contacts_json_with_no_first_send_date_gives_none(self):
        path = self.write("contacts.json", json.dumps({"contacts": [{"id": "a"}], "firstSendDate": None}))
        self.assertIsNone(self.by_key(read_contacts_json(path))["contact:a"]["first_send_date"])

    # ---- send.log ---------------------------------------------------------

    def test_send_log_sent_line_names_the_email(self):
        raw = self.by_key(read_send_log(self.paths["send_log"]))
        sent = raw["line:2"]
        self.assertEqual(sent["kind"], "sent")
        self.assertEqual(sent["at"], "2026-10-06 09:14")
        self.assertEqual(sent["email"], "ada.fenwick@example.com")
        self.assertEqual(sent["name"], "Ada Fenwick")
        self.assertEqual(sent["company"], "Kestrel Robotics")
        self.assertFalse(sent["follow_up"])
        self.assertTrue(sent["line"].startswith("[2026-10-06 09:14] sent to Ada Fenwick"))
        followup = raw["line:11"]
        self.assertEqual((followup["kind"], followup["follow_up"], followup["email"]),
                         ("sent", True, "cleo.hartmann@example.com"))

    def test_send_log_kinds(self):
        raw = self.by_key(read_send_log(self.paths["send_log"]))
        kinds = {n: raw[f"line:{n}"]["kind"] for n in range(1, 14)}
        self.assertEqual(kinds, {
            1: "log", 2: "sent", 3: "sent", 4: "sent", 5: "sent", 6: "bounced", 7: "reply", 8: "unclear",
            9: "found_in_sent", 10: "sent", 11: "sent", 12: "sent", 13: "log"})
        self.assertEqual(raw["line:6"]["email"], "dara.venn@example.com")
        self.assertEqual(raw["line:7"]["email"], "ada.fenwick@example.com")
        self.assertEqual(raw["line:8"]["email"], "emil.strand@example.com")
        self.assertEqual(raw["line:9"]["email"], "emil.strand@example.com")

    def test_send_log_dry_run_line_is_a_log_line_not_a_send(self):
        raw = self.by_key(read_send_log(self.paths["send_log"]))
        self.assertEqual(raw["line:13"]["kind"], "log")
        path = self.write("send.log", "[2026-10-20 08:30] (dry run) sent to A B <a@example.com> at Acme Test\nno stamp\n")
        both = self.by_key(read_send_log(path))
        self.assertEqual(both["line:1"]["kind"], "log")
        self.assertEqual(both["line:2"], {"line": "no stamp", "at": None, "kind": "log"})

    # ---- errors and the sources stay unchanged -------------------------------

    def test_unreadable_json_is_a_source_error(self):
        broken = self.write("broken.json", '{"kestrel": ')
        for reader, name in ((read_devboard, "devboard"), (read_local_storage, "local_storage"),
                             (read_contacts_json, "contacts_json")):
            with self.subTest(source=name):
                with self.assertRaises(SourceError) as cm:
                    list(reader(broken))
                self.assertIn(name, str(cm.exception))
                self.assertIn(str(broken), str(cm.exception))

    def test_a_missing_file_is_a_named_source_error_not_zero_records(self):
        missing = self.tmp / "nowhere.txt"
        cases = ((read_contacted_txt, "contacted_txt"), (read_curated_touches, "curated_touches"),
                 (read_devboard, "devboard"), (read_local_storage, "local_storage"),
                 (read_contacts_json, "contacts_json"), (read_send_log, "send_log"))
        for reader, name in cases:
            with self.subTest(source=name):
                with self.assertRaises(SourceError) as cm:
                    list(reader(missing))
                self.assertIn(name, str(cm.exception))
                self.assertIn(str(missing), str(cm.exception))

    def test_an_undecodable_text_file_is_a_source_error(self):
        path = self.tmp / "bad.txt"
        path.write_bytes(b"2026-10-01 - Acme - \xff\xfe\n")
        with self.assertRaises(SourceError) as cm:
            list(read_contacted_txt(path))
        self.assertIn("contacted_txt", str(cm.exception))

    def test_reading_leaves_every_source_unchanged(self):
        readers = (("contacted_txt", read_contacted_txt), ("curated_touches", read_curated_touches),
                   ("devboard", read_devboard), ("local_storage", read_local_storage),
                   ("contacts_json", read_contacts_json), ("send_log", read_send_log))
        before = {name: source_digest(self.paths[name]) for name, _ in readers}
        for name, reader in readers:
            self.assertGreater(len(list(reader(self.paths[name]))), 0)
        self.assertEqual({name: source_digest(self.paths[name]) for name, _ in readers}, before)


if __name__ == "__main__":
    unittest.main()
