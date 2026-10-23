"""Claude must never be able to approve or send through the API.

Two layers keep that true: deny patterns in .claude/settings.json and the PreToolUse hook
.claude/hooks/block_api_writes.py. These tests run sample commands through a small emulator of the pattern syntax and
through the hook, so a deleted rule, a rule that stopped matching and a hook that stopped refusing all fail.

The emulator follows Claude Code's Bash rule syntax as documented: `*` matches any run of characters, a pattern ending
in `:*` is a prefix match, and a compound command (`a && b`, `a; b`, `a | b`) is checked part by part as well as whole.
It is an approximation of the real matcher, which is why the hook exists.
"""
import importlib.util
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
HOOK_PATH = ROOT / ".claude" / "hooks" / "block_api_writes.py"

_spec = importlib.util.spec_from_file_location("block_api_writes", HOOK_PATH)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)

BASH_RULES = [
    rule[len("Bash("):-1] for rule in SETTINGS["permissions"]["deny"] if rule.startswith("Bash(") and rule.endswith(")")
]


def pattern_matches(pattern: str, command: str) -> bool:
    if pattern.endswith(":*"):
        return command.startswith(pattern[:-2])
    regex = ".*".join(re.escape(piece) for piece in pattern.split("*"))
    return re.fullmatch(regex, command, re.DOTALL) is not None


def denied_by_patterns(command: str) -> bool:
    parts = [command] + [p.strip() for p in re.split(r"&&|\|\||;|\||\n", command) if p.strip()]
    return any(pattern_matches(rule, part) for rule in BASH_RULES for part in parts)


def denied_by_hook(command: str) -> bool:
    return hook.decide(command) is not None


# Commands a deny pattern must catch on its own (the hook is a second layer, tested below).
DENIED_BY_PATTERNS = [
    "curl -s -X POST http://127.0.0.1:3100/api/outbox/5/approve -d '{}'",
    "curl -X POST http://127.0.0.1:3100/api/outbox/5/unapprove",
    "curl http://localhost:55111/api/outbox/5/skip",
    "curl -X PUT http://127.0.0.1:3100/api/notes/4/save",
    "curl -X POST http://127.0.0.1:3100/api/sending/pause",
    "curl -X POST http://127.0.0.1:3100/api/sending/resume",
    "curl -X POST http://127.0.0.1:3100/api/sending/release",
    "curl -X POST http://127.0.0.1:3100/api/outbox/5/send",
    "curl -X POST http://[::1]:3100/api/outbox/5/approve",
    "curl -X POST http://0.0.0.0:3100/api/outbox/5/approve",
    "curl -X POST http://127.1:3100/api/outbox/5/approve",
    "curl -X POST http://LOCALHOST:3100/api/outbox/5/approve",
    'curl -X POST "http://$H:3100/api/outbox/5/approve"',
    "cd /tmp && curl -X POST http://127.0.0.1:3100/api/outbox/5/approve",
    "wget --post-data=x http://127.0.0.1:3100/api/outbox/5/approve",
    "http POST 127.0.0.1:3100/api/outbox/5/approve",
    "https POST localhost:3100/api/outbox/5/approve",
    "xh post localhost:3100/api/sending/pause",
    "curlie -X POST localhost:3100/api/sending/resume",
    "python3 -c \"import urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:3100/api/outbox/5/approve', data=b'{}'))\"",
    "python -c \"import httpx; httpx.post('http://127.0.0.1:3100/api/outbox/1/approve')\"",
    "uv run python -c \"import httpx; httpx.post('http://127.0.0.1:3100/api/outbox/1/approve')\"",
    "uv run python3 -c \"import httpx; httpx.post('http://localhost:3100/api/sending/resume')\"",
    "node -e \"fetch('http://127.0.0.1:3100/api/outbox/5/approve', {method: 'POST'})\"",
    "bun -e \"await fetch('http://127.0.0.1:3100/api/sending/release', {method: 'POST'})\"",
    "deno eval \"await fetch('http://127.0.0.1:3100/api/outbox/5/skip', {method: 'POST'})\"",
    "H=127.0.0.1; curl --json '{}' http://$H:3100/api/outbox/5/approve",
    "BASE=http://127.0.0.1:3100; curl -X POST \"$BASE/api/outbox/5/approve\"",
    "curl -X POST \"$API/api/outbox/5/approve\"",
    "curl -X POST ${API_BASE}/api/sending/resume",
    "curl -X POST `cat host.txt`/api/sending/release",
    # the old mail API, as before
    "curl -s http://127.0.0.1:3002/api/approve",
    "curl -s -X POST http://localhost:3002/api/pause",
]

# Commands only the hook can catch: nothing in the pattern syntax sees inside sh -c, a raw socket, or a shell variable.
DENIED_ONLY_BY_HOOK = [
    "sh -c 'curl -X POST http://127.0.0.1:3100/api/outbox/5/approve'",
    "bash -c \"curl --data x localhost:55111/api/outbox/5/save\"",
    "env HOST=localhost curl -d '{}' http://$HOST:3100/api/outbox/5/save",
    "exec 3<>/dev/tcp/127.0.0.1/3100; printf 'POST /api/outbox/5/approve HTTP/1.0\\r\\n\\r\\n' >&3",
    "printf 'POST /x HTTP/1.0\\r\\n\\r\\n' | nc localhost 3100",
    "curl -X POST http://127.0.0.1:3100/api/outbox/5/appr\"ove\"",
    "curl -X POST http://127.0.0.1:3100/api/outbox/5/appr\\ove",
    "curl -sXPOST http://localhost:3100/api/anything",
    "curl -F file=@x http://127.0.0.1:3100/api/anything",
    "curl -T x http://127.0.0.1:3100/api/anything",
    "python3 -c \"import requests; requests.put('http://localhost:3100/api/anything')\"",
    "python3 -c \"import requests as r; r.request('DELETE', 'http://localhost:3100/api/anything')\"",
    "http localhost:3100/api/anything name=value",
    "xh put localhost:3100/api/anything",
    "curl -X post http://localhost:3100/api/anything",
    "curl --request DELETE http://LOCALHOST:3100/api/anything",
    "wget --method=PUT http://[::1]:3100/api/anything",
    "curl -X POST http://0x7f000001:3100/api/anything",
    "curl -X POST http://2130706433:3100/api/anything",
    "curl -X POST http://127.0.0.1.nip.io:3100/api/anything",
]

# Reads and unrelated commands: neither layer may refuse them.
ALLOWED = [
    "curl -s http://127.0.0.1:3100/api/ping",
    "curl -s http://localhost:55111/api/outbox",
    "curl -sS -D - http://127.0.0.1:3100/api/ping",
    "curl -fsSL http://localhost:3100/api/today",
    "curl -s localhost:5173/",
    "http GET localhost:3100/api/ping",
    "http localhost:3100/api/ping page==2",
    "python3 -c \"import urllib.request as u; print(u.urlopen('http://127.0.0.1:3100/api/ping').read())\"",
    "python3 -c \"import httpx; data = httpx.get('http://127.0.0.1:3100/api/outbox').json(); print(data)\"",
    "node -e \"fetch('http://127.0.0.1:3100/api/ping').then(r => r.json()).then(console.log)\"",
    "docker run -d -p 127.0.0.1:5432:5432 postgres",
    "ssh -N -L 127.0.0.1:8080:localhost:80 host",
    "curl -X POST https://example.com/hook -d '{}'",
    "rg approve outreach/api",
    "rg '/api/sending' web/src",
    "git commit -m 'API: deny approve, skip, save and pause'",
    "uv run python -m unittest tests.test_api_agent_denies",
    "cd web && pnpm typecheck && pnpm lint",
    "python3 -c \"import sys; [print(p) for p in sys.argv[1:]]\" outreach/api/errors.py",
    "ls -d outreach/api",
]


class PatternEmulatorTest(unittest.TestCase):
    def test_the_emulator_follows_the_rule_syntax(self):
        self.assertTrue(pattern_matches("curl *api/*approve*", "curl -X POST http://x/api/outbox/1/approve"))
        self.assertTrue(pattern_matches("curl *api/*approve*", "curl http://x/api/approve"))
        self.assertFalse(pattern_matches("curl *api/*approve*", "curl http://x/api/ping"))
        self.assertFalse(pattern_matches("curl *api/*approve*", "curlx http://x/api/approve"))
        self.assertTrue(pattern_matches("curl http://127.0.0.1:3002:*", "curl http://127.0.0.1:3002/api/save"))
        self.assertFalse(pattern_matches("curl http://127.0.0.1:3002:*", "curl http://127.0.0.1:3003/x"))
        self.assertTrue(denied_by_patterns("cd x && curl http://a/api/approve"))
        self.assertFalse(denied_by_patterns("echo hi"))

    def test_there_are_deny_patterns_to_test(self):
        self.assertGreater(len(BASH_RULES), 50)


class DenyPatternsTest(unittest.TestCase):
    def test_every_sample_write_is_denied_by_a_pattern(self):
        for command in DENIED_BY_PATTERNS:
            with self.subTest(command=command):
                self.assertTrue(denied_by_patterns(command))

    def test_every_sample_write_is_also_refused_by_the_hook(self):
        # The patterns are the first layer and the hook the second, so a command the patterns catch should not slip
        # past the hook for a gap in its own rules.
        for command in DENIED_BY_PATTERNS:
            with self.subTest(command=command):
                self.assertTrue(denied_by_hook(command))

    def test_samples_only_the_hook_can_catch_get_past_the_patterns(self):
        for command in DENIED_ONLY_BY_HOOK:
            with self.subTest(command=command):
                self.assertFalse(denied_by_patterns(command), "the patterns now catch this: move it to the other list")

    def test_every_hook_only_sample_is_refused_by_the_hook(self):
        for command in DENIED_ONLY_BY_HOOK:
            with self.subTest(command=command):
                self.assertTrue(denied_by_hook(command))

    def test_reads_and_unrelated_commands_are_allowed_by_both(self):
        for command in ALLOWED:
            with self.subTest(command=command):
                self.assertFalse(denied_by_patterns(command), "a deny pattern refuses this")
                self.assertFalse(denied_by_hook(command), hook.decide(command))

    def test_webfetch_to_loopback_is_denied(self):
        deny = set(SETTINGS["permissions"]["deny"])
        for host in ("127.0.0.1", "localhost", "0.0.0.0", "[::1]"):
            self.assertIn(f"WebFetch(domain:{host})", deny)

    def test_resume_and_release_are_denied_for_every_client_family(self):
        for word in ("resume", "release", "send", "approve", "skip", "save", "pause"):
            for command in (
                f"curl http://h/api/x/{word}",
                f"wget http://h/api/x/{word}",
                f"http POST h/api/x/{word}",
                f"python3 -c \"urlopen('http://h/api/x/{word}')\"",
                f"node -e \"fetch('http://h/api/x/{word}')\"",
            ):
                with self.subTest(command=command):
                    self.assertTrue(denied_by_patterns(command))


class HookTest(unittest.TestCase):
    def run_hook(self, stdin: str):
        return subprocess.run(
            [sys.executable, str(HOOK_PATH)], input=stdin, capture_output=True, text=True, timeout=20
        )

    def bash(self, command: str) -> str:
        return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})

    def test_a_write_is_refused_with_exit_2_and_a_plain_reason(self):
        done = self.run_hook(self.bash("curl -X POST http://127.0.0.1:3100/api/outbox/5/approve"))
        self.assertEqual(done.returncode, 2)
        self.assertIn("Refused", done.stderr)
        self.assertIn("Claude never approves or sends", done.stderr)
        self.assertEqual(done.stdout, "")

    def test_a_variable_host_is_refused(self):
        done = self.run_hook(self.bash('curl -X POST "$BASE/api/outbox/5/approve"'))
        self.assertEqual(done.returncode, 2)
        self.assertIn("variable", done.stderr)

    def test_a_read_is_allowed(self):
        done = self.run_hook(self.bash("curl -s http://127.0.0.1:3100/api/ping"))
        self.assertEqual((done.returncode, done.stderr), (0, ""))

    def test_other_tools_and_odd_input_are_allowed(self):
        for stdin in (
            json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/x"}}),
            json.dumps({"tool_name": "Bash", "tool_input": {}}),
            json.dumps({"tool_name": "Bash"}),
            json.dumps(["not", "a", "dict"]),
            "not json",
            "",
        ):
            with self.subTest(stdin=stdin):
                self.assertEqual(self.run_hook(stdin).returncode, 0)

    def test_the_reason_never_echoes_a_request_body(self):
        done = self.run_hook(self.bash("curl -X POST -d 'secret-draft-text' http://localhost:3100/api/anything"))
        self.assertEqual(done.returncode, 2)
        self.assertNotIn("secret-draft-text", done.stderr)


class WiringTest(unittest.TestCase):
    def test_the_hook_is_wired_for_bash_in_settings(self):
        entries = SETTINGS["hooks"]["PreToolUse"]
        commands = [
            h["command"] for entry in entries if entry["matcher"] == "Bash" for h in entry["hooks"] if h["type"] == "command"
        ]
        self.assertEqual(len(commands), 1)
        self.assertIn(".claude/hooks/block_api_writes.py", commands[0])
        self.assertIn("$CLAUDE_PROJECT_DIR", commands[0])
        self.assertTrue(HOOK_PATH.is_file())

    def test_the_old_mail_api_denies_are_kept(self):
        deny = SETTINGS["permissions"]["deny"]
        for rule in (
            "Bash(curl http://127.0.0.1:3002:*)",
            "Bash(curl -s http://127.0.0.1:3002/api/approve:*)",
            "Bash(curl -s -X POST http://localhost:3002:*)",
        ):
            self.assertIn(rule, deny)

    def test_claude_md_says_what_is_really_denied(self):
        text = " ".join((ROOT / "CLAUDE.md").read_text(encoding="utf-8").split())
        for needle in (
            "OUTREACH_API_PORT",
            "A workspace never binds 3100",
            "resume",
            "release",
            "httpie",
            ".claude/hooks/block_api_writes.py",
            "loopback",
        ):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
