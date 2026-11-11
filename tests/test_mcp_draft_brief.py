"""The draft_brief tool (#135): the voice files, me.md evidence, the person and the limits, built by code, with a brief
id that save_draft later checks. It is read-only."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from outreach.drafting.brief import brief_id, read_evidence, read_stories
from outreach.drafting.voice import VOICE_FILES, load_voice_pack
from outreach.mcp.server import MAX_RESULT_CHARS
from tests import apiseed as seed
from tests.mcptest import NOW, McpCase, fingerprint

ME = "# Evidence bank\n\n- OpenCodeIntel: 94% Hit@1 on a 665-query research eval.\n\n## Needs Devanshu\n- ASK: `installs`\n"


def write_me() -> Path:
    path = Path(os.environ["OUTREACH_MAIL_HOME"]) / "me.md"
    path.write_text(ME, encoding="utf-8")
    return path


class DraftBriefCase(McpCase):
    def setUp(self):
        super().setUp()
        self.me = write_me()
        self.addCleanup(self.me.unlink, missing_ok=True)
        self.acme = seed.company(self.db, "Acme", "working")
        self.pat = seed.person(self.db, self.acme, "Pat Lee", email="pat@acme.example")

    def test_brief_has_voice_files_evidence_limits_and_id(self):
        got = self.ok("draft_brief", {"person_id": self.pat})
        voice = self.ok("draft_brief", {"person_id": self.pat, "part": 2})["voice"]
        self.assertEqual([f["path"] for f in voice["files"]], list(VOICE_FILES))
        self.assertTrue(all(f["text"].strip() for f in voice["files"]))
        self.assertIn("94% Hit@1", got["evidence"])
        self.assertNotIn("installs", got["evidence"])
        self.assertEqual(got["person"]["person"]["id"], self.pat)
        self.assertEqual(got["limits"]["words"], [0, 120])
        self.assertEqual(got["limits"]["max_links"], 2)
        self.assertIn("can_draft_email", got)

    def test_brief_comes_in_two_parts_with_one_id(self):
        one = self.ok("draft_brief", {"person_id": self.pat})
        two = self.ok("draft_brief", {"person_id": self.pat, "part": 2})
        self.assertEqual((one["part"], one["parts"], two["part"], two["parts"]), (1, 2, 2, 2))
        self.assertEqual(one["brief_id"], two["brief_id"])
        self.assertEqual(self.ok("draft_brief", {"person_id": self.pat, "part": 1}), one)
        for key in ("person", "evidence", "posting", "company_facts", "avoid", "signature", "limits"):
            self.assertIn(key, one)
            self.assertNotIn(key, two)
        for key in ("voice", "stories"):
            self.assertIn(key, two)
            self.assertNotIn(key, one)
        self.assertIn("part 2", one["next"])

    def test_pack_id_is_the_same_for_every_person_and_changes_with_the_stories(self):
        """Part 2 is the same for every person, so the drafter reads it once and again only when pack_id changes."""
        other = seed.person(self.db, self.acme, "Sam Other", email="sam@acme.example")
        mine, theirs = (self.ok("draft_brief", {"person_id": pid}) for pid in (self.pat, other))
        self.assertEqual(mine["pack_id"], theirs["pack_id"])
        self.assertNotEqual(mine["brief_id"], theirs["brief_id"])
        self.assertEqual(self.ok("draft_brief", {"person_id": self.pat, "part": 2})["pack_id"], mine["pack_id"])
        path = Path(tempfile.mkdtemp(prefix="stories-")) / "stories.md"
        path.write_text("A new story.", encoding="utf-8")
        with mock.patch.dict(os.environ, {"OUTREACH_STORIES": str(path)}):
            self.assertNotEqual(self.ok("draft_brief", {"person_id": self.pat})["pack_id"], mine["pack_id"])

    def test_part_must_be_1_or_2(self):
        for part in (0, 3):
            self.assertEqual(self.error("draft_brief", {"person_id": self.pat, "part": part})["code"], "bad_input")

    def test_part_2_refuses_an_unknown_person(self):
        self.assertEqual(self.error("draft_brief", {"person_id": 99999, "part": 2})["code"],
                         self.error("person", {"person_id": 99999})["code"])

    def test_each_part_fits_with_files_at_their_real_size(self):
        """The live files are about 30k of voice, 9k of stories and 8k of me.md: one brief was 66k and the server
        refused it for everyone. Each part must fit with room to grow."""
        voice = Path(tempfile.mkdtemp(prefix="voice-big-"))
        self.addCleanup(shutil.rmtree, voice, ignore_errors=True)
        source = Path(os.environ["OUTREACH_VOICE_DIR"])
        for rel in VOICE_FILES:
            (voice / rel).parent.mkdir(parents=True, exist_ok=True)
            (voice / rel).write_text((source / rel).read_text(encoding="utf-8") + "\nA plain line about voice." * 380,
                                     encoding="utf-8")
        self.me.write_text(ME.replace("## Needs", "- Another true line from me.md here.\n" * 230 + "\n## Needs"),
                           encoding="utf-8")
        stories = Path(tempfile.mkdtemp(prefix="stories-big-")) / "stories.md"
        stories.write_text("The night the queue stalled and I stayed up.\n" * 200, encoding="utf-8")
        self.db.execute("UPDATE person SET profile_text = ? WHERE person_id = ?", ("Builds things. " * 300, self.pat))
        pid = seed.posting(self.db, self.acme, "Backend Engineer")
        self.db.execute("INSERT INTO posting_body(posting_id, body) VALUES (?, ?)", (pid, "We use Go. " * 600))
        with mock.patch.dict(os.environ, {"OUTREACH_VOICE_DIR": str(voice), "OUTREACH_STORIES": str(stories)}):
            parts = [self.ok("draft_brief", {"person_id": self.pat, "part": n}) for n in (1, 2)]
        sizes = [len(json.dumps(p)) for p in parts]
        self.assertGreater(sum(sizes), MAX_RESULT_CHARS * 5 // 6, sizes)  # real scale: about 66k in all
        self.assertTrue(all(size < MAX_RESULT_CHARS * 3 // 4 for size in sizes), sizes)

    def test_brief_limits_words_are_0_to_120(self):
        rec = seed.person(self.db, self.acme, "Rae Recruiter", role_kind="recruiter", email="rae@acme.example")
        for pid in (self.pat, rec):
            self.assertEqual(self.ok("draft_brief", {"person_id": pid})["limits"]["words"], [0, 120])

    def sent_first(self, name: str, subject: str, body: str, project: str = "", created_at: str | None = None) -> int:
        pid = seed.person(self.db, self.acme, name, email=f"{name.lower()}@acme.example")
        mid = seed.message(self.db, seed.touch(self.db, pid), 0, "sent", subject=subject, body=body)
        if project or created_at:
            self.db.execute("UPDATE message SET project = ?, created_at = COALESCE(?, created_at) WHERE message_id = ?",
                            (project, created_at, mid))
        return mid

    def test_brief_lists_recent_openings_and_subjects(self):
        self.sent_first("Ana", "first subject", "Hi Ana,\n\nThe first opening line here.\n\nThanks,\nDevanshu")
        avoid = self.ok("draft_brief", {"person_id": self.pat})["avoid"]
        self.assertEqual(avoid["recent_subjects"], ["first subject"])
        self.assertEqual(avoid["recent_openings"], ["the first opening line here"])

    def test_brief_lists_overused_text(self):
        for n in range(10):
            self.sent_first(f"P{n}", f"s{n}", f"Hi P,\n\nI graduated from Northeastern in May 2026, number {n}.")
        texts = [o["text"] for o in self.ok("draft_brief", {"person_id": self.pat})["avoid"]["overused"]]
        self.assertIn("i graduated from northeastern in may", texts)

    def test_brief_counts_evidence_used_in_14_days(self):
        self.sent_first("Ana", "a", "Hi Ana,\n\nOne.", project="Overhear", created_at="2026-11-05T12:00:00Z")
        self.sent_first("Ben", "b", "Hi Ben,\n\nTwo.", project="Overhear", created_at="2026-10-01T12:00:00Z")
        self.assertEqual(self.ok("draft_brief", {"person_id": self.pat})["avoid"]["evidence_used"], {"Overhear": 1})

    def test_avoid_does_not_change_the_brief_id(self):
        before = self.ok("draft_brief", {"person_id": self.pat})["brief_id"]
        self.sent_first("Ana", "first subject", "Hi Ana,\n\nThe first opening line here.")
        self.assertEqual(self.ok("draft_brief", {"person_id": self.pat})["brief_id"], before)

    def test_brief_carries_the_signature(self):
        sig = Path(tempfile.mkdtemp(prefix="sig-")) / "signature.txt"
        sig.write_text("Devanshu Chicholikar\nhttps://www.devanshuchicholikar.com\n(555) 010-0199\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"OUTREACH_SIGNATURE": str(sig)}):
            got = self.ok("draft_brief", {"person_id": self.pat})
        self.assertEqual(got["signature"], "Devanshu Chicholikar\nhttps://www.devanshuchicholikar.com\n(555) 010-0199")

    def test_brief_id_matches_the_checks_save_draft_will_run(self):
        got = self.ok("draft_brief", {"person_id": self.pat})
        want = brief_id(load_voice_pack(), read_evidence(self.me) + "\n" + read_stories(), self.pat)
        self.assertEqual(got["brief_id"], want)

    def test_brief_writes_nothing(self):
        before = fingerprint(self.db)
        self.ok("draft_brief", {"person_id": self.pat})
        self.assertEqual(fingerprint(self.db), before)

    def test_unreadable_voice_folder_refuses(self):
        empty = tempfile.mkdtemp(prefix="voice-empty-")
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        with mock.patch.dict(os.environ, {"OUTREACH_VOICE_DIR": empty}):
            err = self.error("draft_brief", {"person_id": self.pat})
        self.assertEqual(err["code"], "voice_unreadable")
        self.assertIn("nothing was drafted", err["fix"].lower())

    def test_missing_me_md_refuses(self):
        self.me.unlink()
        self.assertEqual(self.error("draft_brief", {"person_id": self.pat})["code"], "voice_unreadable")

    def test_unknown_person_refuses_like_the_person_tool(self):
        _, via_person = self.call("person", {"person_id": 99999})
        _, via_brief = self.call("draft_brief", {"person_id": 99999})
        self.assertEqual(via_brief["code"], via_person["code"])


if __name__ == "__main__":
    unittest.main()


class BriefResearchCase(DraftBriefCase):
    def fact(self, company_id=None, quote="We build queue tools for small teams.", verified_at=NOW):
        self.db.execute(
            "INSERT INTO research_fact(company_id, fact, quote, url, found_by, verified_at) VALUES (?,?,?,?,?,?)",
            (company_id or self.acme, "Builds queue tools", quote, "https://acme.example/about", "claude", verified_at))

    def stories_file(self, text: str) -> Path:
        path = Path(tempfile.mkdtemp(prefix="stories-")) / "stories.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_brief_has_company_facts_and_research_needed(self):
        got = self.ok("draft_brief", {"person_id": self.pat})
        self.assertEqual((got["company_facts"], got["research_needed"]), ([], True))
        self.fact()
        got = self.ok("draft_brief", {"person_id": self.pat})
        self.assertEqual([f["quote"] for f in got["company_facts"]], ["We build queue tools for small teams."])
        self.assertFalse(got["research_needed"])

    def test_brief_has_the_posting_text(self):
        self.assertIsNone(self.ok("draft_brief", {"person_id": self.pat})["posting"])
        pid = seed.posting(self.db, self.acme, "Backend Engineer")
        self.db.execute("INSERT INTO posting_body(posting_id, body) VALUES (?, ?)", (pid, "We use Go. " * 600))
        got = self.ok("draft_brief", {"person_id": self.pat})["posting"]
        self.assertEqual(got["title"], "Backend Engineer")
        self.assertTrue(got["url"].startswith("https://"))
        self.assertTrue(got["text"].startswith("We use Go."))
        self.assertEqual(len(got["text"]), 4000)

    def test_posting_and_company_facts_are_marked_untrusted(self):
        self.fact()
        pid = seed.posting(self.db, self.acme, "Backend Engineer")
        self.db.execute("INSERT INTO posting_body(posting_id, body) VALUES (?, ?)", (pid, "We use Go."))
        got = self.ok("draft_brief", {"person_id": self.pat})
        self.assertIs(got["posting"]["untrusted"], True)
        self.assertTrue(got["company_facts"] and all(f["untrusted"] is True for f in got["company_facts"]))

    def test_brief_has_stories_and_its_id_changes_with_them(self):
        before = self.ok("draft_brief", {"person_id": self.pat, "part": 2})
        self.assertEqual(before["stories"], "")
        path = self.stories_file("The night the queue stalled.")
        with mock.patch.dict(os.environ, {"OUTREACH_STORIES": str(path)}):
            after = self.ok("draft_brief", {"person_id": self.pat, "part": 2})
        self.assertEqual(after["stories"], "The night the queue stalled.")
        self.assertNotEqual(after["brief_id"], before["brief_id"])

    def test_numbers_from_stories_are_grounded(self):
        path = self.stories_file("I cut the backlog from 4,812 jobs to zero in a night.")
        body = "Hi Pat,\n\nI cut a backlog of 4,812 jobs to zero in one night.\n\nThanks,\nDevanshu"
        with mock.patch.dict(os.environ, {"OUTREACH_STORIES": str(path)}):
            got = self.ok("check_draft", {"person_id": self.pat, "body": body, "subject": "queues"})
        self.assertNotIn("voice_ungrounded", [w["code"] for w in got["warns"]])
        got = self.ok("check_draft", {"person_id": self.pat, "body": body, "subject": "queues"})
        self.assertIn("voice_ungrounded", [w["code"] for w in got["warns"]])

    def test_a_brief_made_before_the_stories_changed_is_stale(self):
        bid = self.ok("draft_brief", {"person_id": self.pat})["brief_id"]
        path = self.stories_file("A new story.")
        with mock.patch.dict(os.environ, {"OUTREACH_STORIES": str(path)}):
            err = self.error("save_draft", {"person_id": self.pat, "subject": "s", "body": "Hi Pat,\n\nHello.",
                                            "hook": "h", "project": "p", "brief_id": bid})
        self.assertEqual(err["code"], "stale_brief")

    def test_numbers_from_company_fact_quotes_are_grounded(self):
        self.fact(quote="Acme serves 7,321 teams.")
        body = "Hi Pat,\n\nAcme serves 7,321 teams and I like that.\n\nThanks,\nDevanshu"
        got = self.ok("check_draft", {"person_id": self.pat, "body": body, "subject": "queues"})
        self.assertNotIn("voice_ungrounded", [w["code"] for w in got["warns"]])
