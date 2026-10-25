"""The `claude -p` call of the morning prepare step (#15, Task 14): the command line, the real Runner, the version
and the reply parser.

`RunResult`, `Runner` and `classify_run` are the reader night job's (#32, `outreach.reader.night`): one definition,
so a limit or login phrase counts only on a non-zero exit or an `is_error` envelope, never in a good reply. The prepare
call differs from the reader's in its tools: web search and web fetch, nothing else (no Bash, no file tools, no MCP,
no skills, no CLAUDE.md), with LinkedIn denied. The deny rule is a second line of defence; the real guard is code:
`prepare_store` refuses a LinkedIn source whatever the model returns.

RUNBOOK (Devanshu, once, before installing the prepare job): this module was built without ever running `claude -p`.
The flags below were read from `claude --help` (2.1.310). Check on your seat which form of the LinkedIn deny rule
takes effect, `--settings` (the plan's preferred form, `DENY_FORM = "settings"`) or `--disallowedTools`:
  1. `claude --help` still lists `--tools`, `--allowedTools`, `--disallowedTools`, `--settings`, `--permission-mode`
     (with `dontAsk`), `--safe-mode`, `--strict-mcp-config`, `--disable-slash-commands`, `--no-session-persistence`.
  2. One safe call that never goes near LinkedIn: from the neutral folder, run the command `claude_command` builds
     with `linkedin.com` in the deny rule replaced by `example.com`, the system prompt "Fetch https://example.com and
     say only FETCHED or DENIED." and any short user message. The reply must say DENIED (or report the tool as
     denied). If it fetched, set `DENY_FORM = "disallowedTools"` and try again; if neither form denies, say so
     before installing the job. `--safe-mode` may ignore `--settings`; that is what this step finds out.
Whatever the answer, a LinkedIn source is still refused in code.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from outreach.plan.config import PlanConfig
from outreach.reader.night import (READER_CWD, RunResult, Runner, _claude_binary, _envelope, _first,
                                   classify_run)

__all__ = ["RunResult", "Runner", "classify_run", "PREPARE_TOOLS", "LINKEDIN_DENY", "DENY_FORM", "PREPARE_CWD",
           "prepare_version", "parse_prepare_reply", "claude_command", "claude_runner"]

# The only built-in tools the session has, and the ones it may use without asking.
PREPARE_TOOLS = ("WebSearch", "WebFetch")
# Never fetched, in either deny form.
LINKEDIN_DENY = ("WebFetch(domain:linkedin.com)", "WebFetch(domain:www.linkedin.com)", "WebFetch(domain:lnkd.in)")
# How the deny rule is passed: "settings" (`--settings` with a permissions deny list) or "disallowedTools". See the
# runbook above: confirm on the seat once.
DENY_FORM = "settings"
# The same neutral folder as the reader's night job: outside every repo, so no project CLAUDE.md is found from it.
PREPARE_CWD = READER_CWD


def prepare_version(prompt: bytes, model: str, schema_version: int) -> str:
    """`pv-` plus 12 hex of sha256 over the rendered prompt, the model name and the reply schema version."""
    digest = hashlib.sha256(prompt + b"\0" + model.encode("utf-8") + b"\0" + str(schema_version).encode("ascii"))
    return "pv-" + digest.hexdigest()[:12]


def _results_object(value: Any) -> dict | None:
    return value if isinstance(value, dict) and "results" in value else None


def parse_prepare_reply(stdout: str) -> dict:
    """The `{"results": [...]}` object from a `claude -p --output-format json` envelope. The reply text may wrap the
    JSON in a Markdown fence or prose. `ValueError` on anything else: no envelope, an error result, a result that is
    not text, no results object, or results that are not a list."""
    envelope = _envelope(stdout or "")
    if envelope is None:
        raise ValueError("no claude -p result object in the output")
    if envelope.get("is_error") is True:
        raise ValueError("the call ended in an error")
    text = envelope["result"]
    if not isinstance(text, str):
        raise ValueError("the result is not text")
    reply = _first(text, _results_object)
    if reply is None:
        raise ValueError("no results object in the reply")
    if not isinstance(reply["results"], list):
        raise ValueError("results is not a list")
    return reply


def claude_command(model: str, system_prompt: str, binary: str = "claude", *, deny_form: str = DENY_FORM) -> list[str]:
    """The `claude -p` command line (flags from `claude --help`, 2.1.310). Not `--bare`: that turns off the seat
    login. The user message goes on stdin."""
    tools = ",".join(PREPARE_TOOLS)
    cmd = [binary, "-p", "--model", model, "--output-format", "json",
           "--tools", tools,                  # the only built-in tools in the session
           "--allowedTools", tools,           # used without a prompt; deny rules still win
           "--permission-mode", "dontAsk",    # anything that would ask is refused, never waited on
           "--no-session-persistence",        # nothing saved to resume
           "--disable-slash-commands",        # no skills or commands
           "--strict-mcp-config",             # no MCP servers: none are given with --mcp-config
           "--safe-mode"]                     # no CLAUDE.md, hooks, plugins or memory; the login still works
    if deny_form == "settings":
        cmd += ["--settings", json.dumps({"permissions": {"deny": list(LINKEDIN_DENY)}})]
    elif deny_form == "disallowedTools":
        cmd += ["--disallowedTools", ",".join(LINKEDIN_DENY)]
    else:
        raise ValueError(f"deny_form must be 'settings' or 'disallowedTools', got {deny_form!r}")
    return cmd + ["--system-prompt", system_prompt]


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
