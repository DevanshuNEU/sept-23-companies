"""His signature (#191): name, portfolio and phone from a local file outside the public repo, with a safe fallback."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from outreach.drafting.signature import FALLBACK, read_signature, signature_lines, signature_path

SIG = "Devanshu Chicholikar\nhttps://www.devanshuchicholikar.com\n(555) 010-0199\n"


class SignatureTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="signature-test-"))

    def write(self, text: str) -> Path:
        p = self.dir / "signature.txt"
        p.write_text(text, encoding="utf-8")
        return p

    def test_read_signature_reads_the_file(self):
        self.assertEqual(read_signature(self.write(SIG)),
                         "Devanshu Chicholikar\nhttps://www.devanshuchicholikar.com\n(555) 010-0199")

    def test_read_signature_drops_blank_lines_and_edge_spaces(self):
        self.assertEqual(read_signature(self.write("\n  Devanshu Chicholikar  \n\nhttps://www.devanshuchicholikar.com\n\n")),
                         "Devanshu Chicholikar\nhttps://www.devanshuchicholikar.com")

    def test_read_signature_falls_back_when_missing(self):
        self.assertEqual(read_signature(self.dir / "none.txt"), FALLBACK)
        self.assertEqual(read_signature(self.write("  \n\n")), FALLBACK)

    def test_fallback_is_name_and_portfolio(self):
        self.assertEqual(FALLBACK, "Devanshu Chicholikar\nhttps://www.devanshuchicholikar.com")

    def test_signature_path_honours_env(self):
        p = self.write(SIG)
        with mock.patch.dict(os.environ, {"OUTREACH_SIGNATURE": str(p)}):
            self.assertEqual(signature_path(), p)
            self.assertEqual(signature_lines(), ["Devanshu Chicholikar", "https://www.devanshuchicholikar.com",
                                                 "(555) 010-0199"])

    def test_default_path_is_outside_the_repo(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OUTREACH_SIGNATURE", None)
            self.assertEqual(signature_path(),
                             Path.home() / "Library" / "Application Support" / "outreach" / "signature.txt")

    def test_tests_never_read_the_real_file(self):
        self.assertNotEqual(Path(os.environ["OUTREACH_SIGNATURE"]),
                            Path.home() / "Library" / "Application Support" / "outreach" / "signature.txt")


if __name__ == "__main__":
    unittest.main()
