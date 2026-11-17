"""The drafter's run: first emails for the loaded people, at the pace the accounts can send, one model call each (#298).

Who and how many come from code (`outreach.first_drafter.pick`): the run reads the store once, through a guarded
connection with no grants, and takes the night's queue (the Outbox topped up to `outbox_target`, at most
`max_per_run` and `--limit`, at most `per_company` at one company, companies in turn, the #9 size order inside one).
For each person, in queue order, the run asks `draft_brief` for both parts of the brief (the person, the posting, the
company facts, me.md, what to avoid, the signature and the limits; his voice files and stories), gives it to the model,
and saves the reply with `save_draft` and the brief's id. Those are the MCP server's own handlers, called in process
through `run_tool`: the same guarded connection, refusals and #9 checks as a draft written in his terminal.

The model is text in, text out (`claude -p` with no tools, no MCP, no skills and no CLAUDE.md, from the reader's
neutral folder, as the judge runs it). Its reply must be exactly one JSON object
`{"subject": "...", "body": "...", "hook": "...", "project": "..."}`.

How each person ends:
- drafted: saved as `drafted` by `claude`, for his click in the Outbox. A save with a problem the model can fix
  (`FIXABLE`: a block in the text, or a first-email rule the save warns on) gets one more call with the problems
  shown; the better of the two drafts is kept (a block still saves, so he sees it), counted `with_blocks` when blocks
  remain.
- refused: `draft_brief` or `save_draft` refused, or the brief says no first email may be drafted (`can_draft_email`
  false): nothing is written. Refusals are reported, never worked around.
- failed: the call failed or the reply was not one JSON email. Nothing is written; a later night tries again.
A session limit, a login problem or a broken CLI stops the run at once; `max_consecutive_failures` failed calls in a
row stop it too, and so do unreadable voice files (nothing can be drafted without them). Off touches nothing; a dry
run lists who would be drafted and calls and writes nothing.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from outreach.first_drafter.config import DrafterConfig
from outreach.first_drafter.pick import Person, how_many, queue, read_pool
from outreach.judge.validate import Refused, parse_reply
from outreach.mcp.guard import open_guarded
from outreach.mcp.server import run_tool, tool_specs
from outreach.mcp.tools.reads import HOOK_CHARS, PROJECT_CHARS
from outreach.reader.night import READER_CWD, RunResult, Runner, _claude_binary, classify_run, claude_command
from outreach.store.db import StoreError

__all__ = ["FIXABLE", "OFF_LINE", "PROMPT_PATH", "DrafterSummary", "load_prompt", "run_drafter", "drafter_runner"]

PROMPT_PATH = Path(__file__).with_name("prompt.md")
OFF_LINE = "first drafter is off; set [drafter] enabled = true in outreach/first_drafter/config.toml to turn it on"
_SPECS = {spec.name: spec for spec in tool_specs()}
_MAX_DETAIL = 300
# The checks a second call can fix: blocks in the text itself (the follow-up drafter's set), and the first-email
# rules the save warns on (2 links with the portfolio signed, at most 120 words, the signature, no dashes, a number
# with a source, the banned list, no line about the email, a short subject, a fresh opening and fresh sentences). A
# block about the store (no mail server, a no-go, the banned list unreadable) or a warn the text cannot change (no
# fact on file about the person, an unconfirmed mail server) is the same whatever the model writes.
FIXABLE = frozenset({"placeholder", "link_shortener", "too_many_links", "near_identical", "voice_ungrounded",
                     "visa_line", "markdown_link", "link_count", "length", "signature", "dash", "banned_phrase",
                     "narrated_email", "subject_length", "same_opening", "reused_sentence"})
REPLY_KEYS = {"subject": 200, "body": 5000, "hook": HOOK_CHARS, "project": PROJECT_CHARS}  # save_draft's caps
BRIEF_PLUMBING = ("brief_id", "part", "parts", "next", "pack_id", "can_draft_email")


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


@dataclass
class DrafterSummary:
    status: str  # off, dry_run, nothing_to_do, done, session_limit, auth, failed
    waiting: int = 0   # first emails waiting in the Outbox when the run began
    eligible: int = 0  # people who may get a first email
    wanted: int = 0    # people this run tries
    drafted: int = 0
    with_blocks: int = 0
    refused: int = 0
    failed: int = 0
    calls: int = 0
    reason: str = ""
    lines: list[str] = field(default_factory=list)

    def line(self) -> str:
        text = (f"first drafter: {self.status}: {self.waiting} waiting, {self.eligible} to draft, {self.wanted} this "
                f"run, {self.drafted} drafted ({self.with_blocks} with blocks), {self.refused} refused, "
                f"{self.failed} failed, {self.calls} calls")
        return f"{text}; {self.reason}" if self.reason else text


def _short(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= _MAX_DETAIL else text[:_MAX_DETAIL - 3] + "..."


def _fixable(saved: dict) -> list[dict]:
    """The problems a second call could fix, as the model is shown them."""
    return [{"title": c["title"], "detail": c["detail"]} for c in (*saved["blocks"], *saved["warns"])
            if c["code"] in FIXABLE]


def _score(saved: dict, problems: list[dict]) -> tuple[int, int]:
    """Lower is better: blocks first, then every problem the model could have fixed."""
    return len(saved["blocks"]), len(problems)


class _Run:
    def __init__(self, runner: Runner, cfg: DrafterConfig, *, now: str, path: str | None):
        self.runner, self.cfg, self.now, self.path = runner, cfg, now, path
        self.prompt = load_prompt()
        self.summary = DrafterSummary(status="done")
        self.in_a_row = 0

    def tool(self, name: str, arguments: dict) -> tuple[bool, dict]:
        return run_tool(_SPECS[name], arguments, path=self.path, clock=lambda: self.now)

    def ask(self, message: dict) -> tuple[str, dict | str]:
        """One call. (outcome, the draft or what went wrong): outcome is ok, failed, session_limit, auth or
        cli_broken."""
        self.summary.calls += 1
        try:
            result = self.runner(self.prompt, json.dumps(message, ensure_ascii=False), self.cfg.model)
        except FileNotFoundError as err:
            return "cli_broken", _short(err)
        except (subprocess.TimeoutExpired, OSError) as err:
            return "failed", _short(err)
        outcome = classify_run(result, session_limit_patterns=self.cfg.session_limit_patterns,
                               auth_patterns=self.cfg.auth_patterns)
        if outcome != "ok":
            return outcome, _short(result.stderr or result.stdout)
        try:
            reply = parse_reply(result.stdout)
        except Refused as err:
            return "failed", str(err)
        if set(reply) != set(REPLY_KEYS):
            return "failed", "the reply is not exactly one subject, body, hook and project"
        draft = {}
        for key, cap in REPLY_KEYS.items():
            value = reply[key]
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > cap:
                return "failed", f"the reply's {key} is not text of 1 to {cap} characters"
            draft[key] = value.strip()
        return "ok", draft

    def brief(self, p: Person, label: str) -> tuple[str | None, dict | None]:
        """(a stop reason for the run, the brief and its id) for one person; (None, None) when it was refused."""
        brief: dict = {}
        for part in (1, 2):  # the brief comes in two parts, each under the tool result size
            is_error, got = self.tool("draft_brief", {"person_id": p.person_id, "part": part})
            if is_error:
                if got.get("code") == "voice_unreadable":
                    self.summary.status = "failed"
                    return f"his voice files cannot be read: {_short(got.get('message'))}", None
                self.summary.refused += 1
                self.summary.lines.append(f"{label}: brief refused ({got.get('code')}); nothing written")
                return None, None
            if part == 1 and got.get("can_draft_email") is not True:
                why = (got.get("person") or {}).get("why_not")
                self.summary.refused += 1
                self.summary.lines.append(f"{label}: cannot be emailed ({why}); nothing written")
                return None, None
            brief.update(got)
        return None, brief

    def one(self, p: Person) -> str | None:
        """Draft one first email. A stop reason for the whole run, or None to go on."""
        label = f"{p.name} at {p.company}"
        stop, brief = self.brief(p, label)
        if brief is None:
            return stop
        brief_id = brief["brief_id"]
        for key in BRIEF_PLUMBING:
            brief.pop(key, None)
        message: dict = {"brief": brief}
        saved, first = None, None  # first: the first draft's (draft, saved result, problems) while a retry runs
        for attempt in (1, 2):
            outcome, draft = self.ask(message)
            if outcome in ("session_limit", "auth", "cli_broken"):
                if saved is not None:  # the first draft is saved, blocks and all
                    self.counted(label, saved)
                self.summary.status = "failed" if outcome == "cli_broken" else outcome
                return f"the claude CLI is broken: {draft}" if outcome == "cli_broken" else f"{label}: {draft}"
            if outcome == "failed":
                if saved is not None:
                    break  # the retry failed: the first draft, blocks and all, stays saved
                self.summary.failed += 1
                self.in_a_row += 1
                self.summary.lines.append(f"{label}: failed ({draft}); nothing written")
                if self.in_a_row >= self.cfg.max_consecutive_failures:
                    self.summary.status = "failed"
                    return f"{self.in_a_row} failed calls in a row"
                return None
            self.in_a_row = 0
            is_error, saved_now = self.save(p.person_id, brief_id, draft)
            if is_error:
                if saved is not None:
                    break  # the first draft stays saved
                if saved_now.get("code") == "voice_unreadable":
                    self.summary.status = "failed"
                    return f"his voice files cannot be read: {_short(saved_now.get('message'))}"
                self.summary.refused += 1
                self.summary.lines.append(f"{label}: save refused ({saved_now.get('code')}); nothing written")
                return None
            problems = _fixable(saved_now)
            if first is not None and _score(saved_now, problems) >= _score(first[1], first[2]):
                # The retry is no better: put the first draft back.
                is_error, again = self.save(p.person_id, brief_id, first[0])
                if not is_error:
                    saved_now, problems = again, first[2]
            saved = saved_now
            if attempt == 2 or not problems:
                break
            first = (draft, saved, problems)
            message = {"brief": brief, "previous": draft, "blocks": problems}
        self.counted(label, saved)
        return None

    def save(self, person_id: int, brief_id: str, draft: dict) -> tuple[bool, dict]:
        return self.tool("save_draft", {"person_id": person_id, "brief_id": brief_id, **draft})

    def counted(self, label: str, saved: dict) -> None:
        self.summary.drafted += 1
        if saved["blocks"]:
            self.summary.with_blocks += 1
        self.summary.lines.append(f"{label}: drafted, {saved['word_count']} words"
                                  + (f", {len(saved['blocks'])} block(s) left" if saved["blocks"] else ""))


def _tonight(cfg: DrafterConfig, path: str | None, limit: int | None, summary: DrafterSummary) -> list[Person]:
    """The night's queue, read once through a guarded connection with no grants."""
    conn = open_guarded({}, path=path)
    try:
        pool = read_pool(conn)
    finally:
        conn.close()
    summary.waiting, summary.eligible = pool.waiting, len(pool.people)
    wanted = how_many(outbox_target=cfg.outbox_target, waiting=pool.waiting, max_per_run=cfg.max_per_run,
                      limit=limit)
    chosen = queue(pool.people, pool.companies, per_company=cfg.per_company)[:wanted]
    summary.wanted = len(chosen)
    return chosen


def run_drafter(runner: Runner, cfg: DrafterConfig, *, now: str, path: str | None = None, limit: int | None = None,
                dry_run: bool = False) -> DrafterSummary:
    """Draft tonight's first emails. `path` is the store (None: the default, OUTREACH_DB or the live store). `limit`
    lowers `max_per_run`. A store that cannot be read ends the run, failed."""
    if not dry_run and cfg.enabled is not True:
        return DrafterSummary(status="off")
    run = _Run(runner, cfg, now=now, path=path)
    try:
        chosen = _tonight(cfg, path, limit, run.summary)
    except (StoreError, sqlite3.Error) as err:
        run.summary.status, run.summary.reason = "failed", f"the store cannot be read: {_short(err)}"
        return run.summary
    if dry_run:
        run.summary.status = "dry_run"
        run.summary.lines = [f"{p.name} at {p.company}: {p.title or 'no title'}" for p in chosen]
        return run.summary
    if not chosen:
        run.summary.status = "nothing_to_do"
        return run.summary
    for p in chosen:
        stop = run.one(p)
        if stop is not None:
            if run.summary.status == "done":
                run.summary.status = "failed"
            run.summary.reason = stop
            break
    return run.summary


def drafter_runner(cfg: DrafterConfig, *, cwd: Path | None = None) -> Runner:
    """The real Runner: the reader's `claude -p` command (no tools, no MCP, no skills, no CLAUDE.md, no saved session:
    the brief is in the message) from the reader's neutral folder, killed after `call_timeout_s`. No test runs it."""
    folder = Path(cwd) if cwd is not None else READER_CWD

    def run(system_prompt: str, message: str, model: str) -> RunResult:
        folder.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(claude_command(model, system_prompt, _claude_binary()), input=message,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=folder,
                              timeout=cfg.call_timeout_s, check=False)
        return RunResult(done.returncode, done.stdout or "", done.stderr or "")

    return run
