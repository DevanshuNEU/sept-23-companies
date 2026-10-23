"""Task 14, Step 6: agents never run the new sender, and CLAUDE.md says where it may run (Pending Devanshu Q12)."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class SettingsTest(unittest.TestCase):
    def test_settings_deny_agent_runs_of_the_sender(self):
        deny = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))["permissions"]["deny"]
        for runner in ("uv run python", "python", ".venv/bin/python", "python3"):
            for command in ("run", "auth"):
                self.assertIn(f"Bash({runner} -m outreach.sending {command}:*)", deny)

    def test_claude_md_lists_the_sender_as_live_checkout_only(self):
        text = " ".join((ROOT / "CLAUDE.md").read_text(encoding="utf-8").split())
        self.assertIn("python -m outreach.sending run", text)
        self.assertIn("`python -m outreach.sending auth`", text)
        self.assertIn("Claude never runs the new sender in any checkout; launchd does.", text)


if __name__ == "__main__":
    unittest.main()
