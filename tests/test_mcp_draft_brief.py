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
        self.assertEqual(got["limits"]["words"], [80, 120])
        self.assertEqual(got["limits"]["max_links"], 2)
        self.assertIn("can_draft_email", got)

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

    def test_recruiter_words_limit_is_80_to_100(self):
        rec = seed.person(self.db, self.acme, "Rae Recruiter", email="rae@acme.example", role_kind="recruiter")
        self.assertEqual(self.ok("draft_brief", {"person_id": rec})["limits"]["words"], [80, 100])

    def test_unknown_person_refuses_like_the_person_tool(self):
        _, via_person = self.call("person", {"person_id": 99999})
        _, via_brief = self.call("draft_brief", {"person_id": 99999})
        self.assertEqual(via_brief["code"], via_person["code"])


if __name__ == "__main__":
    unittest.main()
