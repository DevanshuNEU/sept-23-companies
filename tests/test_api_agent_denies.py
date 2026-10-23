"""Claude must never be able to approve through the API: the deny rules stay in .claude/settings.json."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class AgentDeniesTest(unittest.TestCase):
    def test_write_routes_are_denied_for_curl_wget_and_fetch_on_any_loopback_port(self):
        deny = set(json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))["permissions"]["deny"])
        for tool in ("curl", "wget"):
            for host in ("127.0.0.1", "localhost"):
                for verb in ("approve", "skip", "save", "pause"):  # "approve" also matches unapprove
                    self.assertIn(f"Bash({tool} *{host}:*/api/*{verb}*)", deny)
        for verb in ("approve", "skip", "save", "pause"):
            self.assertIn(f"Bash(node *fetch*/api/*{verb}*)", deny)
        self.assertLessEqual({"WebFetch(domain:127.0.0.1)", "WebFetch(domain:localhost)"}, deny)

    def test_the_old_mail_api_denies_are_kept(self):
        deny = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))["permissions"]["deny"]
        self.assertIn("Bash(curl -s http://127.0.0.1:3002/api/approve:*)", deny)

    def test_claude_md_says_so(self):
        text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("OUTREACH_API_PORT", text)
        self.assertIn("A workspace never binds 3100", text)


if __name__ == "__main__":
    unittest.main()
