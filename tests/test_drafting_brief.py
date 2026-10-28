"""outreach.drafting.brief: the evidence read from me.md and the brief id that binds voice, evidence and person."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from outreach.drafting.brief import brief_id, check_brief, read_evidence
from outreach.drafting.voice import VOICE_FILES, load_voice_pack

ME = """# Evidence bank

## Who
- Devanshu Chicholikar, software engineer.

## Projects
- **OpenCodeIntel**: 94% Hit@1 on a 665-query research eval.

## Needs Devanshu
- ASK: a Saar install count, `installs`.
"""


def me_file(text: str = ME) -> Path:
    path = Path(tempfile.mkdtemp(prefix="me-test-")) / "me.md"
    path.write_text(text, encoding="utf-8")
    return path


class EvidenceCase(unittest.TestCase):
    def test_evidence_stops_at_needs_devanshu(self):
        text = read_evidence(me_file())
        self.assertIn("94% Hit@1", text)
        self.assertNotIn("installs", text)
        self.assertNotIn("Needs Devanshu", text)

    def test_evidence_unreadable_raises(self):
        from outreach.drafting.voice import VoiceUnreadable
        with self.assertRaises(VoiceUnreadable):
            read_evidence(Path(tempfile.mkdtemp()) / "none.md")

    def test_empty_evidence_raises(self):
        from outreach.drafting.voice import VoiceUnreadable
        with self.assertRaises(VoiceUnreadable):
            read_evidence(me_file("## Needs Devanshu\n- ASK: x\n"))


class BriefIdCase(unittest.TestCase):
    def setUp(self):
        self.pack = load_voice_pack()
        self.evidence = read_evidence(me_file())

    def test_brief_id_is_16_hex(self):
        bid = brief_id(self.pack, self.evidence, 7)
        self.assertRegex(bid, r"^[0-9a-f]{16}$")

    def test_brief_id_differs_per_person(self):
        self.assertNotEqual(brief_id(self.pack, self.evidence, 7), brief_id(self.pack, self.evidence, 8))

    def test_brief_id_changes_when_evidence_changes(self):
        self.assertNotEqual(brief_id(self.pack, self.evidence, 7), brief_id(self.pack, self.evidence + " more", 7))

    def test_brief_id_changes_when_voice_changes(self):
        other = replace(self.pack, digest="0" * 64)
        self.assertNotEqual(brief_id(self.pack, self.evidence, 7), brief_id(other, self.evidence, 7))

    def test_check_brief_accepts_its_own_id_and_rejects_others(self):
        bid = brief_id(self.pack, self.evidence, 7)
        self.assertTrue(check_brief(bid, self.pack, self.evidence, 7))
        self.assertFalse(check_brief("0" * 16, self.pack, self.evidence, 7))
        self.assertFalse(check_brief("", self.pack, self.evidence, 7))
        self.assertFalse(check_brief(bid, self.pack, self.evidence, 8))


if __name__ == "__main__":
    unittest.main()
