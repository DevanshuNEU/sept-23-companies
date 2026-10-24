import tempfile
import unittest
from pathlib import Path

from outreach.importer.read_notes import parse_frontmatter, read_devvault_notes, read_interview_prep
from outreach.importer.records import SourceError, SourceRecord, source_digest
from tests.importtest import fixture_manifest


def doc(*lines):
    return "\n".join(["---", *lines, "---", "", "Body text.", ""])


class FrontmatterTest(unittest.TestCase):
    def test_frontmatter_people_list_of_maps(self):
        fm = parse_frontmatter(doc(
            "company: Acme Test",
            "people:",
            "  - name: Jo Example",
            "    role: engineer",
            "    email: jo@example.com",
            "    sent: null",
            "  - name: Sam Sample",
            "    role: founder",
            "status: reached-out",
        ))
        self.assertEqual(fm["company"], "Acme Test")
        self.assertEqual(fm["people"], [
            {"name": "Jo Example", "role": "engineer", "email": "jo@example.com", "sent": None},
            {"name": "Sam Sample", "role": "founder"},
        ])
        self.assertEqual(fm["status"], "reached-out")

    def test_quoted_value_with_a_colon_is_one_value(self):
        fm = parse_frontmatter(doc(
            'title: "Staff Engineer: Perception"',
            "note: 'single: quoted'",
            "plain: VP: Engineering",
            'hash: "kept # inside the quotes"  # dropped',
            'quoted_null: "null"',
        ))
        self.assertEqual(fm["title"], "Staff Engineer: Perception")
        self.assertEqual(fm["note"], "single: quoted")
        self.assertEqual(fm["plain"], "VP: Engineering")
        self.assertEqual(fm["hash"], "kept # inside the quotes")
        self.assertEqual(fm["quoted_null"], "null")

    def test_null_and_comment_are_handled(self):
        fm = parse_frontmatter(doc(
            "a: null", "b: ~", "c: Null", "d:", "e: kept   # trailing comment", "# a whole-line comment",
            "f: C# and F#", "g: 2026-10-06",
        ))
        self.assertIsNone(fm["a"])
        self.assertIsNone(fm["b"])
        self.assertEqual(fm["c"], "Null")
        self.assertIsNone(fm["d"])
        self.assertEqual(fm["e"], "kept")
        self.assertEqual(fm["f"], "C# and F#")
        self.assertEqual(fm["g"], "2026-10-06")
        self.assertNotIn("# a whole-line comment", fm)

    def test_unknown_nested_shape_is_kept_as_text(self):
        fm = parse_frontmatter(doc(
            "person_research:",
            '  - "RECRUITER PENDING: look on the team page"',
            "  - second note",
            "tags: [a, b]",
            "deep:",
            "  - name: Jo",
            "    links:",
            "      - one",
            "after: here",
        ))
        self.assertEqual(fm["person_research"], '  - "RECRUITER PENDING: look on the team page"\n  - second note')
        self.assertEqual(fm["tags"], "[a, b]")
        self.assertEqual(fm["deep"], "  - name: Jo\n    links:\n      - one")
        self.assertEqual(fm["after"], "here")

    def test_a_line_that_is_not_a_key_is_kept_not_dropped(self):
        fm = parse_frontmatter(doc("company: Acme Test", "this is not yaml", "status: todo"))
        self.assertEqual(fm["_unparsed"], "this is not yaml")
        self.assertEqual(fm["status"], "todo")

    def test_text_without_frontmatter_gives_none(self):
        self.assertIsNone(parse_frontmatter("# Just a heading\n\nBody.\n"))
        self.assertIsNone(parse_frontmatter(""))
        self.assertIsNone(parse_frontmatter("---\ncompany: never closed\n"))
        self.assertEqual(parse_frontmatter("﻿---\na: b\n---\n"), {"a": "b"})
        self.assertEqual(parse_frontmatter("---\r\na: b\r\n---\r\nBody\r\n"), {"a": "b"})
        self.assertEqual(parse_frontmatter("---\n---\nBody\n"), {})


class ReadNotesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.paths = fixture_manifest(self.tmp).paths

    def write(self, relative, text):
        path = self.tmp / "own" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_company_files_are_records_with_folder_and_frontmatter(self):
        records = list(read_devvault_notes(self.paths["devvault_notes"]))
        for record in records:
            self.assertIsInstance(record, SourceRecord)
        raw = {r.source_key: r.raw for r in records}
        self.assertEqual(set(raw), {
            "file:reached-out/kestrel-robotics.md", "file:reached-out/marlow-dynamics.md", "file:todo/fathom-systems.md"})
        kestrel = raw["file:reached-out/kestrel-robotics.md"]
        self.assertEqual((kestrel["path"], kestrel["folder"]), ("reached-out/kestrel-robotics.md", "reached-out"))
        fm = kestrel["frontmatter"]
        self.assertEqual((fm["company"], fm["domain"], fm["status"]), ("Kestrel Robotics", "kestrel.example", "reached-out"))
        self.assertEqual(fm["people"][0]["title"], "Staff Engineer: Perception")
        self.assertEqual(fm["people"][0]["reply_date"], "2026-10-08")
        self.assertIsNone(fm["people"][1]["sent"])
        self.assertEqual(raw["file:reached-out/marlow-dynamics.md"]["frontmatter"]["company"], "Marlow Dynamics")
        self.assertEqual(raw["file:todo/fathom-systems.md"]["frontmatter"]["person_research"],
                         '  - "RECRUITER PENDING: look for an engineer on the team page"')

    def test_file_without_frontmatter_is_still_a_record(self):
        self.write("_archive/old-note.md", "# Old note\n\nNo frontmatter here.\n")
        self.write("sponsor-viable/README.md", "# not a company\n")
        self.write("loose.md", "---\ncompany: Loose Test\n---\n")
        raw = {r.source_key: r.raw for r in read_devvault_notes(self.tmp / "own")}
        self.assertEqual(set(raw), {"file:_archive/old-note.md", "file:loose.md"})
        self.assertEqual(raw["file:_archive/old-note.md"],
                         {"path": "_archive/old-note.md", "folder": "_archive", "frontmatter": None})
        self.assertEqual(raw["file:loose.md"]["folder"], "")

    def test_readme_is_not_a_company_file(self):
        keys = {r.source_key for r in read_devvault_notes(self.paths["devvault_notes"])}
        self.assertNotIn("file:README.md", keys)

    def test_interview_folder_dates_come_from_session_names(self):
        raw = {r.source_key: r.raw for r in read_interview_prep(self.paths["interview_prep"])}
        self.assertEqual(set(raw), {"folder:kestrel-robotics", "folder:ember-analytics", "folder:general"})
        kestrel = raw["folder:kestrel-robotics"]
        self.assertEqual(kestrel["slug"], "kestrel-robotics")
        self.assertTrue(kestrel["has_brief"])
        self.assertEqual(kestrel["session_dates"], ["2026-10-12", "2026-10-14"])
        self.assertEqual(kestrel["brief"]["company"], "Kestrel Robotics")
        self.assertEqual(kestrel["brief"]["interviewer"], "Ada Fenwick")
        self.assertEqual(kestrel["brief"]["verified_on"], "2026-10-12")

    def test_brief_without_frontmatter_keeps_the_slug(self):
        raw = {r.source_key: r.raw for r in read_interview_prep(self.paths["interview_prep"])}
        self.assertEqual(raw["folder:ember-analytics"],
                         {"slug": "ember-analytics", "brief": None, "session_dates": [], "has_brief": True})
        self.assertEqual(raw["folder:general"],
                         {"slug": "general", "brief": None, "session_dates": [], "has_brief": False})

    def test_session_names_that_are_not_dates_give_no_date(self):
        self.write("prep/acme-test/sessions/2026-10-03-1.md", "x")
        self.write("prep/acme-test/sessions/README.md", "x")
        self.write("prep/acme-test/sessions/notes-2026-10-09.md", "x")
        self.write("prep/acme-test/sessions/2026-10-05.md", "x")
        self.write("prep/stray-file.md", "x")
        raw = {r.source_key: r.raw for r in read_interview_prep(self.tmp / "own" / "prep")}
        self.assertEqual(set(raw), {"folder:acme-test"})
        self.assertEqual(raw["folder:acme-test"]["session_dates"], ["2026-10-03", "2026-10-05"])

    def test_missing_or_undecodable_sources_are_named_errors(self):
        missing = self.tmp / "nowhere"
        for reader, name in ((read_devvault_notes, "devvault_notes"), (read_interview_prep, "interview_prep")):
            with self.subTest(source=name):
                with self.assertRaises(SourceError) as cm:
                    list(reader(missing))
                self.assertIn(name, str(cm.exception))
                self.assertIn(str(missing), str(cm.exception))
        bad = self.tmp / "own" / "bad"
        bad.mkdir(parents=True)
        (bad / "x.md").write_bytes(b"---\ncompany: \xff\n---\n")
        with self.assertRaises(SourceError) as cm:
            list(read_devvault_notes(bad))
        self.assertIn("devvault_notes", str(cm.exception))

    def test_reading_leaves_the_folders_unchanged(self):
        before = {n: source_digest(self.paths[n]) for n in ("devvault_notes", "interview_prep")}
        self.assertGreater(len(list(read_devvault_notes(self.paths["devvault_notes"]))), 0)
        self.assertGreater(len(list(read_interview_prep(self.paths["interview_prep"]))), 0)
        self.assertEqual({n: source_digest(self.paths[n]) for n in before}, before)


if __name__ == "__main__":
    unittest.main()
