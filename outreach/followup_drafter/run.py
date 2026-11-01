"""The drafter's run: draft every follow-up due by the next business day, one model call each (#142).

What is due comes from the MCP tool `followups_due` (planned rows only, so a draft he saved or one already drafted is
never touched; a reply, a hold, a no-go or a closed company leaves a row off the list). For each one, in list order
(oldest due first, so a thread's follow-ups go in order), the run asks `draft_brief` for the person's brief (his voice
files, me.md's evidence, the person and the thread so far), gives it to the model with which follow-up this is, and
saves the reply with `save_followup` and the brief's id. Those are the MCP server's own handlers, called in process
through `run_tool`: the same guarded connection, refusals and #9 checks as a draft written in his terminal. The list
is read again after each one, so the second follow-up of a thread is drafted from a brief that shows the first.

The model is text in, text out (`claude -p` with no tools, no MCP, no skills and no CLAUDE.md, from the reader's
neutral folder, as the judge runs it). Its reply must be exactly one JSON object `{"body": "..."}`.

How each follow-up ends:
- drafted: saved as `drafted` by `claude`, for his click in the Outbox. A save with a problem the model can fix
  (`FIXABLE`: a block in the text, or a number with no source) or that repeats an earlier email of the thread
  (`_repeats` against `said`, the earlier emails sent or waiting to go) gets one more call with the problems shown; the
  better of the two drafts is kept (a block still saves, so he sees it), counted `with_blocks` when blocks remain.
- refused: `save_followup` or `draft_brief` refused it (a reply came in, the company closed): nothing is written.
- failed: the call failed or the reply was not one JSON body. The row stays `planned`; the next run tries again,
  and the sender's lapse rule ends it 5 business days after it was due.
A session limit or a login problem stops the run at once; `max_consecutive_failures` failed calls in a row stop it
too, and so does a store that cannot list what is due (busy, unreadable). Unreadable voice files stop it (nothing
can be drafted without them). Off touches nothing; a dry run lists what is due and calls and writes nothing.
"""
from __future__ import annotations

import difflib
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from outreach.drafts.checks import FOLLOWUP_MAX_WORDS
from outreach.followup_drafter.config import DrafterConfig
from outreach.judge.validate import Refused, parse_reply
from outreach.mcp.server import run_tool, tool_specs
from outreach.reader.night import READER_CWD, RunResult, Runner, _claude_binary, classify_run, claude_command
from outreach.sending import calendar as cal
from outreach.sending.config import load_config as load_send_config
from outreach.store.timefmt import ISO_FMT

__all__ = ["OFF_LINE", "PROMPT_PATH", "DrafterSummary", "load_prompt", "days_to_next_business_day", "run_drafter",
           "drafter_runner"]

PROMPT_PATH = Path(__file__).with_name("prompt.md")
OFF_LINE = ("followup drafter is off; set [drafter] enabled = true in outreach/followup_drafter/config.toml "
            "to turn it on")
_SPECS = {spec.name: spec for spec in tool_specs()}
_MAX_DETAIL = 300
# The checks a second call can fix: blocks in the text itself, and a number with no source in me.md or his paste. A
# block about the store (no mail server, a no-go, the banned list unreadable) is the same whatever the model writes.
FIXABLE = frozenset({"placeholder", "link_shortener", "too_many_links", "near_identical", "voice_ungrounded"})
# What the model is held to: a follow-up's length, not a first email's (the brief's own limits are a first email's).
FOLLOWUP_LIMITS = {"words": [25, FOLLOWUP_MAX_WORDS], "max_links": 2}


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


@dataclass
class DrafterSummary:
    status: str  # off, dry_run, nothing_due, done, session_limit, auth, failed
    due: int = 0
    drafted: int = 0
    with_blocks: int = 0
    refused: int = 0
    failed: int = 0
    calls: int = 0
    reason: str = ""
    lines: list[str] = field(default_factory=list)

    def line(self) -> str:
        text = (f"followup drafter: {self.status}: {self.due} due, {self.drafted} drafted "
                f"({self.with_blocks} with blocks), {self.refused} refused, {self.failed} failed, {self.calls} calls")
        return f"{text}; {self.reason}" if self.reason else text


def days_to_next_business_day(now: str) -> int:
    """Calendar days from today (New York) to the next business day: 1 on a weeknight, 3 on a Friday. Capped at 7,
    the most `followups_due` looks ahead."""
    cfg = load_send_config()
    today = datetime.strptime(now, ISO_FMT).replace(tzinfo=timezone.utc).astimezone(ZoneInfo(cfg.day_tz)).date()
    return min((cal.add_business_days(today, 1, cfg) - today).days, 7)


def _short(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= _MAX_DETAIL else text[:_MAX_DETAIL - 3] + "..."


class _Stop(Exception):
    """The store could not list what is due: the run ends, failed, with what it did so far."""


def _fixable(saved: dict) -> list[dict]:
    """The problems a second call could fix, as the model is shown them."""
    return [{"title": c["title"], "detail": c["detail"]} for c in (*saved["blocks"], *saved["warns"])
            if c["code"] in FIXABLE]


# An earlier email in the thread counts as said when it went out or is waiting to go: he approves it, then the
# queue sends it before this one (`earlier_followup`). A skipped or still-planned one is unsaid.
SAID_STATES = ("sent", "sending", "send_unknown", "approved", "drafted")
# Word-sequence similarity at or above this, against any said email, is a repeat.
REPEAT_RATIO = 0.6


def _said(brief: dict, seq: int) -> list[dict]:
    """The thread's earlier emails that count as said, oldest first, worked out in code so the model never has to
    judge which drafts will go out."""
    thread = brief.get("person", {}).get("email", {}).get("messages", [])
    return [{"seq": m["seq"], "state": m["state"], "body": m["body"]} for m in sorted(thread, key=lambda m: m["seq"])
            if m["seq"] < seq and m["state"] in SAID_STATES and m["body"].strip()]


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _repeats(body: str, said: list[dict]) -> list[dict]:
    """A problem for each said email this draft is too close to (the save checks only compare colleagues)."""
    out = []
    for m in said:
        if difflib.SequenceMatcher(None, _words(body), _words(m["body"])).ratio() >= REPEAT_RATIO:
            which = "the first email" if m["seq"] == 0 else f"follow-up {m['seq']}"
            out.append({"title": "Repeats an earlier email",
                        "detail": f"This draft says nearly what {which} says. Say something new instead."})
    return out


class _Run:
    def __init__(self, runner: Runner, cfg: DrafterConfig, *, now: str, path: str | None):
        self.runner, self.cfg, self.now, self.path = runner, cfg, now, path
        self.prompt = load_prompt()
        self.of = len(load_send_config().followup_business_days)
        self.days_ahead = days_to_next_business_day(now)
        self.summary = DrafterSummary(status="done")
        self.tried: set[int] = set()
        self.in_a_row = 0

    def tool(self, name: str, arguments: dict) -> tuple[bool, dict]:
        return run_tool(_SPECS[name], arguments, path=self.path, clock=lambda: self.now)

    def due(self) -> list[dict]:
        is_error, payload = self.tool("followups_due", {"days_ahead": self.days_ahead})
        if is_error:
            raise _Stop(f"followups_due failed: {payload.get('code')}: {_short(payload.get('message'))}")
        self.summary.due = max(self.summary.due, payload["total"])
        return payload["items"]

    def ask(self, message: dict) -> tuple[str, str | None]:
        """One call. (outcome, body): outcome is ok, failed, session_limit, auth or cli_broken."""
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
        body = reply.get("body")
        if set(reply) != {"body"} or not isinstance(body, str) or not body.strip():
            return "failed", "the reply is not exactly one non-empty body"
        return "ok", body.strip()

    def one(self, item: dict) -> str | None:
        """Draft one follow-up. A stop reason for the whole run, or None to go on."""
        person, seq = item["person"]["id"], item["seq"]
        label = f"{item['person']['name']} at {item['company']['name']}, follow-up {seq}"
        is_error, brief = self.tool("draft_brief", {"person_id": person})
        if is_error:
            if brief.get("code") == "voice_unreadable":
                return f"his voice files cannot be read: {_short(brief.get('message'))}"
            self.summary.refused += 1
            self.summary.lines.append(f"{label}: brief refused ({brief.get('code')})")
            return None
        brief_id = brief.pop("brief_id")
        brief["limits"] = FOLLOWUP_LIMITS
        said = _said(brief, seq)
        message = {"followup": {"seq": seq, "of": self.of, "last": seq >= self.of}, "said": said, "brief": brief}
        saved, first = None, None  # first: the first draft's (body, saved result, problems) while a retry runs
        for attempt in (1, 2):
            outcome, body = self.ask(message)
            if outcome in ("session_limit", "auth", "cli_broken"):
                if saved is not None:  # the first draft is saved, blocks and all
                    self.counted(label, saved)
                self.summary.status = "failed" if outcome == "cli_broken" else outcome
                return f"the claude CLI is broken: {body}" if outcome == "cli_broken" else f"{label}: {body}"
            if outcome == "failed":
                if saved is not None:
                    break  # the retry failed: the first draft, blocks and all, stays saved
                self.summary.failed += 1
                self.in_a_row += 1
                self.summary.lines.append(f"{label}: failed ({body}); left planned")
                if self.in_a_row >= self.cfg.max_consecutive_failures:
                    self.summary.status = "failed"
                    return f"{self.in_a_row} failed calls in a row"
                return None
            self.in_a_row = 0
            is_error, saved_now = self.save(person, seq, body, brief_id)
            if is_error:
                if saved is not None:
                    break
                self.summary.refused += 1
                self.summary.lines.append(f"{label}: save refused ({saved_now.get('code')}); nothing written")
                return None
            problems = _fixable(saved_now) + _repeats(body, said)
            if first is not None and len(problems) >= len(first[2]):
                # The retry is no better: put the first draft back.
                is_error, again = self.save(person, seq, first[0], brief_id)
                saved_now, problems = (first[1] if is_error else again), first[2]
            saved = saved_now
            if attempt == 2 or not problems:
                break
            first = (body, saved, problems)
            message = {**message, "previous_body": body, "blocks": problems}
        self.counted(label, saved)
        return None

    def save(self, person: int, seq: int, body: str, brief_id: str) -> tuple[bool, dict]:
        return self.tool("save_followup", {"person_id": person, "seq": seq, "body": body, "brief_id": brief_id})

    def counted(self, label: str, saved: dict) -> None:
        self.summary.drafted += 1
        if saved["blocks"]:
            self.summary.with_blocks += 1
        self.summary.lines.append(f"{label}: drafted, {saved['word_count']} words"
                                  + (f", {len(saved['blocks'])} block(s) left" if saved["blocks"] else ""))

    def loop(self, cap: int) -> None:
        while self.summary.drafted + self.summary.refused + self.summary.failed < cap:
            try:
                items = self.due()
            except _Stop as err:
                self.summary.status, self.summary.reason = "failed", str(err)
                return
            item = next((i for i in items if i["message_id"] not in self.tried), None)
            if item is None:
                break
            self.tried.add(item["message_id"])
            stop = self.one(item)
            if stop is not None:
                if self.summary.status == "done":
                    self.summary.status = "failed"
                self.summary.reason = stop
                return


def run_drafter(runner: Runner, cfg: DrafterConfig, *, now: str, path: str | None = None, limit: int | None = None,
                dry_run: bool = False) -> DrafterSummary:
    """Draft the follow-ups due by the next business day. `path` is the store (None: the default, OUTREACH_DB or the
    live store). `limit` lowers `max_per_run`. Raises only when the store cannot be read."""
    if not dry_run and cfg.enabled is not True:
        return DrafterSummary(status="off")
    run = _Run(runner, cfg, now=now, path=path)
    cap = min(cfg.max_per_run, limit) if limit else cfg.max_per_run
    try:
        items = run.due()
    except _Stop as err:
        run.summary.status, run.summary.reason = "failed", str(err)
        return run.summary
    if dry_run:
        run.summary.status = "dry_run"
        run.summary.lines = [f"{i['person']['name']} at {i['company']['name']}: follow-up {i['seq']}, due {i['due_on']}"
                             for i in items[:cap]]
        return run.summary
    if not items:
        run.summary.status = "nothing_due"
        return run.summary
    run.loop(cap)
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
