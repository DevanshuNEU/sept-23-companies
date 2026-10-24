"""The reader's night job: read queued postings through Devanshu's Claude Code seat, one `claude -p` call a batch (#32).

The model is a text-in, text-out function here. It gets the pinned prompt as its system prompt and one batch as the
user message, with every built-in tool off, no MCP server, no skills, no hooks, no CLAUDE.md and no saved session,
run from a neutral folder; it never sees the store. Code does the rest: it leases the batch, fills in the reader
version the model does not copy back, sends each reading through `submit` with the version and the attempt it leased,
and combines what came back with the rules.

The Runner is injected (`claude_runner` builds the real one), so tests pass a fake and nothing here is tied to the
CLI. Whatever the CLI does, the night never crashes on it and never leaves a batch half written. Each call ends one
of four ways (`classify_run`):

- ok: the readings are parsed (a Markdown fence or prose around the JSON is skipped) and submitted. A leased posting
  the reply gave no reading for goes back to the queue with its attempt used.
- session_limit or auth: the batch is released (no attempt counted) and the night stops, job `partial` for a limit
  and `failed` for a login. The next night starts where this one stopped.
- failed (a non-zero exit, a timeout, a missing binary, a reply with no readable JSON): a call with several postings
  in it cannot say which one broke it, so the batch is released (no attempt counted) and its postings are read one
  per call; a failed call with one posting in it uses that posting's attempt. A poison posting (a body that always
  breaks the call) so runs out of leases and ends refused, which shows it, flagged, instead of blocking the queue
  every night. `max_consecutive_failures` failed calls in a row stop the night.

The night also stops at `max_postings_per_night` postings handed out or `max_batches` calls. However it stops, it
then runs `apply_all`, so every posting with a stored reading or an ended-refused row has the combined decision,
whatever wrote its decision row last. The job row is always closed; an error that is not the model's or the CLI's
(the store, a changed ruleset) closes it `failed`, gives back the leases it holds, and only a store error is raised.
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import shutil
import sqlite3
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from outreach.reader.combine import apply_all, apply_reading
from outreach.reader.config import ReaderConfig, load_reader_config
from outreach.reader.queue import batch_payload, enqueue_missing, next_batch, release, requeue
from outreach.reader.submit import submit
from outreach.reader.types import ReadRequest
from outreach.reader.version import reader_version
from outreach.rules import RulesetNotCurrent
from outreach.store import describe_error, finish_job, start_job, utcnow

__all__ = ["APPLIED", "READER_CWD", "RunResult", "Runner", "NightSummary", "classify_run", "parse_reply",
           "claude_command", "claude_runner", "run_night"]

# Where `claude -p` runs: a folder of its own, outside every repo, so no project CLAUDE.md is found from it.
READER_CWD = Path.home() / "Library" / "Application Support" / "outreach" / "reader-cwd"
# Submit results that are combined with the rules at once: a stored or partial reading, and a refusal (a posting
# that ended refused is shown, flagged, now rather than at the next apply).
APPLIED = ("stored", "partial", "refused")

Outcome = Literal["ok", "session_limit", "auth", "failed"]
Status = Literal["queue_empty", "cap_reached", "session_limit", "auth", "failed"]
_JOB_STATUS = {"queue_empty": "ok", "cap_reached": "ok", "session_limit": "partial", "auth": "failed",
               "failed": "failed"}
_DECODER = json.JSONDecoder()
_MAX_DETAIL = 300


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str


# (system_prompt, user_message, model) -> what the CLI printed.
Runner = Callable[[str, str, str], RunResult]


@dataclass(frozen=True)
class NightSummary:
    status: Status
    batches: int
    stored: int
    partial: int
    requeued: int    # postings put back in the queue: released, requeued by submit or skipped by the model
    applied: int     # postings combined with the rules: after each submit and by the closing apply_all
    stopped_reason: str

    def line(self) -> str:
        text = (f"night: {self.status}, {self.batches} batches, {self.stored} stored, {self.partial} partial, "
                f"{self.requeued} back in the queue, {self.applied} applied")
        return text + (f"; {self.stopped_reason}" if self.stopped_reason else "")


def _first(text: str, pick: Callable[[Any], Any]) -> Any:
    """The first JSON value in `text` that `pick` keeps (returns not None): the whole text, else a value starting at
    any `{` or `[`, so a code fence, a warning line or prose around the JSON is skipped."""
    text = text.strip()
    try:
        kept = pick(json.loads(text))
    except ValueError:
        kept = None
    if kept is not None:
        return kept
    for start, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            value, _ = _DECODER.raw_decode(text, start)
        except ValueError:
            continue
        kept = pick(value)
        if kept is not None:
            return kept
    return None


def _result_object(value: Any) -> dict | None:
    if isinstance(value, list):  # the list form: every message, the result last
        value = next((v for v in reversed(value) if isinstance(v, dict) and v.get("type") == "result"), None)
    return value if isinstance(value, dict) and "result" in value else None


def _envelope(stdout: str) -> dict | None:
    """The result object `claude -p --output-format json` prints, or None when stdout holds none."""
    return _first(stdout or "", _result_object)


def classify_run(result: RunResult, *, session_limit_patterns: Sequence[str],
                 auth_patterns: Sequence[str]) -> Outcome:
    """How a call ended. A call that exited 0 with a result object that is not an error is `ok`, whatever its text
    says (a reading may quote a posting that mentions a usage limit). Otherwise the patterns are matched, ignoring
    case, against stdout and stderr: `auth` first, then `session_limit`, else `failed`. Stdout that holds no result
    object at all counts as an error."""
    envelope = _envelope(result.stdout)
    if result.returncode == 0 and envelope is not None and envelope.get("is_error") is not True:
        return "ok"
    text = f"{result.stdout}\n{result.stderr}".lower()
    if any(p.lower() in text for p in auth_patterns):
        return "auth"
    if any(p.lower() in text for p in session_limit_patterns):
        return "session_limit"
    return "failed"


def _readings_object(value: Any) -> dict | None:
    return value if isinstance(value, dict) and "readings" in value else None


def parse_reply(stdout: str) -> list[dict]:
    """The `readings` list from a `claude -p --output-format json` envelope (`{"result": "<text>", ...}`). The text
    may wrap the JSON in a Markdown fence or prose. ValueError on anything else: no envelope, an error result, a
    result that is not text, no `{"readings": [...]}` object, or readings that are not a list."""
    envelope = _envelope(stdout)
    if envelope is None:
        raise ValueError("no claude -p result object in the output")
    if envelope.get("is_error") is True:
        raise ValueError(f"the call ended in an error: {_short(str(envelope.get('result')))}")
    text = envelope["result"]
    if not isinstance(text, str):
        raise ValueError("the result is not text")
    reply = _first(text, _readings_object)
    if reply is None:
        raise ValueError(f"no readings object in the reply: {_short(text)}")
    if not isinstance(reply["readings"], list):
        raise ValueError("readings is not a list")
    return reply["readings"]


def claude_command(model: str, system_prompt: str, binary: str = "claude") -> list[str]:
    """The `claude -p` command line, every flag checked against `claude --help` (2.1.310). Not `--bare`: that turns
    off the seat login. The user message goes on stdin."""
    return [binary, "-p", "--model", model, "--output-format", "json",
            "--tools", "",                 # no built-in tools
            "--no-session-persistence",    # nothing saved to resume
            "--disable-slash-commands",    # no skills or commands
            "--strict-mcp-config",         # no MCP servers: none are given with --mcp-config
            "--safe-mode",                 # no CLAUDE.md, hooks, plugins or memory; the login still works
            "--system-prompt", system_prompt]


def _claude_binary() -> str:
    return shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")


def claude_runner(cfg: ReaderConfig, *, cwd: Path | None = None) -> Runner:
    """The real Runner: `claude_command` run with the user message on stdin, from `cwd` (default `READER_CWD`, made on
    first use), killed after `cfg.call_timeout_s` (`subprocess.TimeoutExpired`, which the night counts as a failed
    call). No test runs it."""
    folder = Path(cwd) if cwd is not None else READER_CWD

    def run(system_prompt: str, user_message: str, model: str) -> RunResult:
        folder.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(claude_command(model, system_prompt, _claude_binary()), input=user_message,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=folder,
                              timeout=cfg.call_timeout_s, check=False)
        return RunResult(done.returncode, done.stdout or "", done.stderr or "")

    return run


def _short(text: str) -> str:
    """One line, at most `_MAX_DETAIL` characters, ASCII-safe, for the queue's last_error and the job's error."""
    line = " ".join(str(text).split())
    if len(line) > _MAX_DETAIL:
        line = line[:_MAX_DETAIL - 3] + "..."
    return line.encode("ascii", "backslashreplace").decode("ascii")


class _Night:
    """One night's loop and its counts."""

    def __init__(self, conn: sqlite3.Connection, runner: Runner, cfg: ReaderConfig, now: Callable[[], str]):
        self.conn, self.runner, self.cfg, self.now = conn, runner, cfg, now
        self.version = reader_version(cfg)
        self.prompt = cfg.prompt_path.read_text(encoding="utf-8")
        self.batches = self.handed = self.stored = self.partial = self.requeued = self.applied = 0
        self.leased: list[ReadRequest] = []

    def summary(self, status: Status, reason: str) -> NightSummary:
        return NightSummary(status, self.batches, self.stored, self.partial, self.requeued, self.applied, reason)

    def give_back(self) -> None:
        """Release whatever this night still holds (nothing, between batches)."""
        held, self.leased = self.leased, []
        release(self.conn, held)

    def run(self) -> tuple[Status, str]:
        cfg = self.cfg
        enqueue_missing(self.conn, cfg=cfg, version=self.version)
        failures = 0
        singles = 0  # calls left that read one posting each, after a failed call of several
        while True:
            if self.batches >= cfg.max_batches:
                return "cap_reached", f"{self.batches} batches (max_batches)"
            if self.handed >= cfg.max_postings_per_night:
                return "cap_reached", f"{self.handed} postings (max_postings_per_night)"
            size = min(1 if singles else cfg.batch_size, cfg.max_postings_per_night - self.handed)
            self.leased = next_batch(self.conn, size, cfg=cfg, version=self.version, now=self.now())
            if not self.leased:
                return "queue_empty", ""
            singles = max(singles - 1, 0)
            self.batches += 1
            self.handed += len(self.leased)
            attempts = self._attempts()
            outcome, detail, readings = self._call()
            if outcome in ("session_limit", "auth"):
                self.requeued += len(self.leased)
                self.give_back()
                return outcome, detail
            if outcome == "ok":
                usable = self._usable(readings or [])
                if usable:
                    failures = 0
                    self._submit(usable, attempts)
                    continue
                detail = "the reply had a reading for none of the postings it was sent"
            failures += 1
            if len(self.leased) == 1:
                self.requeued += requeue(self.conn, self.leased, f"call failed: {detail}", attempts=attempts)
                self.leased = []
            else:
                singles = len(self.leased)
                self.requeued += len(self.leased)
                self.give_back()
            if failures >= cfg.max_consecutive_failures:
                return "failed", f"{failures} failed calls in a row; last: {detail}"

    def _attempts(self) -> dict[int, int]:
        """The attempt each leased row is on, read back from the queue (a ReadRequest does not carry it)."""
        out = {}
        for r in self.leased:
            row = self.conn.execute(
                "SELECT attempts FROM read_queue WHERE posting_id = ? AND body_sha256 = ? AND reader_version = ?",
                (r.posting_id, r.body_sha256, r.reader_version)).fetchone()
            out[r.posting_id] = row["attempts"]
        return out

    def _call(self) -> tuple[Outcome, str, list[dict] | None]:
        payload = json.dumps(batch_payload(self.conn, self.leased), ensure_ascii=False)
        try:
            result = self.runner(self.prompt, payload, self.cfg.model)
        except Exception as err:  # noqa: BLE001 - a timeout, a missing binary: a failed call, never the night's end
            return "failed", _short(describe_error(err)), None
        if not isinstance(result, RunResult):
            return "failed", f"the runner returned {type(result).__name__}, not a RunResult", None
        outcome = classify_run(result, session_limit_patterns=self.cfg.session_limit_patterns,
                               auth_patterns=self.cfg.auth_patterns)
        if outcome != "ok":
            detail = _short(f"exit {result.returncode}: {result.stderr.strip() or result.stdout.strip()}")
            return outcome, detail, None
        try:
            return "ok", "", parse_reply(result.stdout)
        except ValueError as err:
            return "failed", _short(f"unreadable reply: {err}"), None

    def _usable(self, readings: list) -> list[dict]:
        """The readings that answer a posting of this batch, by posting id and body hash; the first one per posting.
        Each gets the leased reader version, which the model does not copy back."""
        leased = {r.posting_id: r for r in self.leased}
        out: dict[int, dict] = {}
        for obj in readings:
            if not isinstance(obj, dict):
                continue
            pid = obj.get("posting_id")
            if not isinstance(pid, int) or isinstance(pid, bool) or pid not in leased or pid in out:
                continue
            req = leased[pid]
            if obj.get("body_sha256") != req.body_sha256:
                continue
            out[pid] = {**obj, "reader_version": req.reader_version}
        return list(out.values())

    def _submit(self, usable: list[dict], attempts: dict[int, int]) -> None:
        results = submit(self.conn, usable, cfg=self.cfg, version=self.version, now=self.now(),
                         lease_attempt=attempts)
        for r in results:
            if r.status == "stored":
                self.stored += 1
            elif r.status == "partial":
                self.partial += 1
            elif r.status == "requeued":
                self.requeued += 1
        # A posting the reply skipped (or answered under a key submit did not know) is still leased on this attempt.
        self.requeued += requeue(self.conn, self.leased, "no reading for it in the reply", attempts=attempts)
        self.leased = []
        for r in results:
            if r.status in APPLIED and r.posting_id is not None:
                apply_reading(self.conn, r.posting_id, cfg=self.cfg)
                self.applied += 1


def run_night(conn: sqlite3.Connection, runner: Runner, cfg: ReaderConfig | None = None, *,
              now: Callable[[], str] = utcnow) -> NightSummary:
    """One night of reading: a `read_postings` job run, `enqueue_missing`, batches until the queue is empty or a cap,
    limit or failure stops it, then `apply_all`. Never raises on a model or CLI problem; always closes the job."""
    cfg = cfg if cfg is not None else load_reader_config()
    night = _Night(conn, runner, cfg, now)
    run_id = start_job(conn, "read_postings")
    try:
        try:
            status, reason = night.run()
            night.applied += apply_all(conn, cfg=cfg)
        except RulesetNotCurrent as err:  # the readings stored so far stay; nothing more can be combined tonight
            night.give_back()
            status, reason = "failed", _short(describe_error(err))
    except BaseException as err:
        with contextlib.suppress(Exception):
            night.give_back()
        finish_job(conn, run_id, "failed", dataclasses.asdict(night.summary("failed", "")),
                   _short(describe_error(err)))
        raise
    summary = night.summary(status, reason)
    finish_job(conn, run_id, _JOB_STATUS[status], dataclasses.asdict(summary),
               "" if _JOB_STATUS[status] == "ok" else reason)
    return summary
