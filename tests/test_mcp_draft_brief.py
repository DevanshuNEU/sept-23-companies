"""The draft_brief tool (#135): the voice files, me.md evidence, the person and the limits, built by code, with a brief
id that save_draft later checks. It is read-only."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from outreach.drafting.brief import brief_id, read_evidence
from outreach.drafting.voice import VOICE_FILES, load_voice_pack
from tests import apiseed as seed
from tests.mcptest import McpCase, fingerprint

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
        self.assertEqual([f["path"] for f in got["voice"]["files"]], list(VOICE_FILES))
        self.assertTrue(all(f["text"].strip() for f in got["voice"]["files"]))
        self.assertIn("94% Hit@1", got["evidence"])
        self.assertNotIn("installs", got["evidence"])
        self.assertEqual(got["person"]["person"]["id"], self.pat)
        self.assertEqual(got["limits"]["words"], [0, 120])
        self.assertEqual(got["limits"]["max_links"], 2)
        self.assertIn("can_draft_email", got)

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
        want = brief_id(load_voice_pack(), read_evidence(self.me), self.pat)
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
