"""The `claude -p` call of the morning prepare step (#15, Task 14): the command line, the real Runner, the version
and the reply parser.

`RunResult`, `Runner` and `classify_run` are the reader night job's (#32, `outreach.reader.night`): one definition,
so a limit or login phrase counts only on a non-zero exit or an `is_error` envelope, never in a good reply. The prepare
call differs from the reader's in its tools: web search and web fetch, nothing else (no Bash, no file tools, no MCP,
no skills, no CLAUDE.md), with LinkedIn denied. The deny rule is a second line of defence; the real guard is code:
`prepare_store` refuses a LinkedIn source whatever the model returns.

The deny rule is passed both ways at once, `--settings` (a permissions deny list) and `--disallowedTools`, so it holds
if either form takes effect. It names `linkedin.com`, `*.linkedin.com`, `www.linkedin.com`, `lnkd.in` and `*.lnkd.in`
for WebFetch. `claude --help` documents only the `Tool(specifier)` shape (its example is "Bash(git *)") and no
domain rule for WebSearch, so WebSearch is not denied by domain: a search result is never a source (code refuses
LinkedIn sources) and the prompt forbids using LinkedIn results.

RUNBOOK (Devanshu, once, before turning the step on): this module was built without ever running `claude -p`. The
step does nothing until `prepare.deny_rule_checked = true` in `outreach/plan/config.toml`; flip it only after:
  1. `claude --help` still lists `--tools`, `--allowedTools`, `--disallowedTools`, `--settings`, `--permission-mode`
     (with `dontAsk`), `--safe-mode`, `--strict-mcp-config`, `--disable-slash-commands`, `--no-session-persistence`.
  2. Two safe calls that never go near LinkedIn: from the neutral folder, run the command `claude_command` builds with
     every `linkedin.com` in the deny rules replaced by `example.com`, the system prompt "Fetch <url> and say only
     FETCHED or DENIED." and any short user message, once with `https://example.com` and once with
     `https://uk.example.com`. Both must say DENIED (or report the tool as denied). If the subdomain call fetched,
     the `*.` pattern is not honoured: list the LinkedIn hosts he cares about by name (`uk.`, `m.`, `de.`) in
     `LINKEDIN_DENY` and repeat. If the plain call fetched, neither form takes effect: leave the gate off and say so.
     `--safe-mode` may ignore `--settings`, and a rule the CLI cannot read may make it drop the settings or fail the
     call; a failing call only makes the step stop after two failures, so it fails closed.
Whatever the answer, a LinkedIn source is still refused in code.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from outreach.plan.config import PlanConfig
from outreach.reader.night import READER_CWD, RunResult, Runner, _claude_binary, _envelope, classify_run

__all__ = ["RunResult", "Runner", "classify_run", "PREPARE_TOOLS", "LINKEDIN_DENY", "PREPARE_CWD",
           "prepare_version", "parse_prepare_reply", "claude_command", "claude_runner"]

# The only built-in tools the session has, and the ones it may use without asking.
PREPARE_TOOLS = ("WebSearch", "WebFetch")
# Never fetched. Passed both as a `--settings` deny list and as `--disallowedTools`.
LINKEDIN_DENY = ("WebFetch(domain:linkedin.com)", "WebFetch(domain:*.linkedin.com)", "WebFetch(domain:www.linkedin.com)",
                 "WebFetch(domain:lnkd.in)", "WebFetch(domain:*.lnkd.in)")
# The same neutral folder as the reader's night job: outside every repo, so no project CLAUDE.md is found from it.
PREPARE_CWD = READER_CWD


def prepare_version(prompt: bytes, model: str, schema_version: int) -> str:
    """`pv-` plus 12 hex of sha256 over the rendered prompt, the model name and the reply schema version."""
    digest = hashlib.sha256(prompt + b"\0" + model.encode("utf-8") + b"\0" + str(schema_version).encode("ascii"))
    return "pv-" + digest.hexdigest()[:12]


_FENCE = re.compile(r"```[A-Za-z]*[ \t]*\n(.*?)\n[ \t]*```", re.S)


def parse_prepare_reply(stdout: str) -> dict:
    """The `{"results": [...]}` object from a `claude -p --output-format json` envelope. The reply text must be exactly
    one JSON object, alone or in one Markdown code fence, with only whitespace around it: prose before or after it, two
    objects, or an object quoted inside other text is refused, so a fake `results` echoed from a page can never be
    taken. `ValueError` on anything else: no envelope, an error result, a result that is not text, or results that are
    not a list."""
    envelope = _envelope(stdout or "")
    if envelope is None:
        raise ValueError("no claude -p result object in the output")
    if envelope.get("is_error") is True:
        raise ValueError("the call ended in an error")
    text = envelope["result"]
    if not isinstance(text, str):
        raise ValueError("the result is not text")
    text = text.strip()
    fenced = _FENCE.fullmatch(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        reply = json.loads(text)
    except ValueError:
        raise ValueError("the reply is not exactly one JSON object") from None
    if not isinstance(reply, dict) or "results" not in reply:
        raise ValueError("no results object in the reply")
    if not isinstance(reply["results"], list):
        raise ValueError("results is not a list")
    return reply


def claude_command(model: str, system_prompt: str, binary: str = "claude") -> list[str]:
    """The `claude -p` command line (flags from `claude --help`, 2.1.310). Not `--bare`: that turns off the seat
    login. The user message goes on stdin."""
    tools = ",".join(PREPARE_TOOLS)
    return [binary, "-p", "--model", model, "--output-format", "json",
            "--tools", tools,                  # the only built-in tools in the session
            "--allowedTools", tools,           # used without a prompt; deny rules still win
            "--permission-mode", "dontAsk",    # anything that would ask is refused, never waited on
            "--no-session-persistence",        # nothing saved to resume
            "--disable-slash-commands",        # no skills or commands
            "--strict-mcp-config",             # no MCP servers: none are given with --mcp-config
            "--safe-mode",                     # no CLAUDE.md, hooks, plugins or memory; the login still works
            "--settings", json.dumps({"permissions": {"deny": list(LINKEDIN_DENY)}}),  # the deny rule, one way
            "--disallowedTools", ",".join(LINKEDIN_DENY),                              # and the other
            "--system-prompt", system_prompt]


def claude_runner(cfg: PlanConfig, *, cwd: Path | None = None) -> Runner:
    """The real Runner: `claude_command` run with the user message on stdin, from `cwd` (default `PREPARE_CWD`, made
    on first use), killed after `prepare.call_timeout_s` (`subprocess.TimeoutExpired`, which the run counts as a
    failed call). No test runs it."""
    folder = Path(cwd) if cwd is not None else PREPARE_CWD
    timeout = cfg.get("prepare", "call_timeout_s")

    def run(system_prompt: str, user_message: str, model: str) -> RunResult:
        folder.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(claude_command(model, system_prompt, _claude_binary()), input=user_message,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=folder,
                              timeout=timeout, check=False)
        return RunResult(done.returncode, done.stdout or "", done.stderr or "")

    return run
