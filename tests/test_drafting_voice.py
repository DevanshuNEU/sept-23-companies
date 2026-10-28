"""outreach.drafting.voice: the voice pack read from DevVault, on the fixture folder in `tests/fixtures/devvault`."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from outreach.drafting.voice import VOICE_FILES, VoicePack, VoiceUnreadable, load_voice_pack

FIXTURE = Path(__file__).parent / "fixtures" / "devvault"


class VoicePackCase(unittest.TestCase):
    def copy(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="voice-test-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        shutil.copytree(FIXTURE, root, dirs_exist_ok=True)
        return root

    def test_loads_the_three_files_in_order(self):
        pack = load_voice_pack(FIXTURE)
        self.assertIsInstance(pack, VoicePack)
        self.assertEqual(tuple(path for path, _ in pack.files), VOICE_FILES)
        self.assertTrue(all(text.strip() for _, text in pack.files))

    def test_digest_changes_when_a_file_changes(self):
        root = self.copy()
        before = load_voice_pack(root).digest
        with open(root / VOICE_FILES[1], "a", encoding="utf-8") as f:
            f.write("x")
        self.assertNotEqual(load_voice_pack(root).digest, before)

    def test_digest_is_stable_for_equal_content(self):
        self.assertEqual(load_voice_pack(FIXTURE).digest, load_voice_pack(self.copy()).digest)

    def test_missing_file_raises_voice_unreadable(self):
        root = self.copy()
        (root / VOICE_FILES[2]).unlink()
        with self.assertRaises(VoiceUnreadable) as caught:
            load_voice_pack(root)
        self.assertIn(VOICE_FILES[2], str(caught.exception.path))

    def test_empty_file_raises(self):
        root = self.copy()
        (root / VOICE_FILES[0]).write_text("  \n", encoding="utf-8")
        with self.assertRaises(VoiceUnreadable):
            load_voice_pack(root)

    def test_default_root_reads_env(self):
        root = self.copy()
        with mock.patch.dict(os.environ, {"OUTREACH_VOICE_DIR": str(root)}):
            self.assertEqual(load_voice_pack().digest, load_voice_pack(root).digest)


if __name__ == "__main__":
    unittest.main()
