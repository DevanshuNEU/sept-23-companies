# Nightly Follow-up Drafter Implementation Plan (#142)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every night, each follow-up due by the next business day is drafted in Devanshu's voice and waits in the Outbox for his click; nothing is approved or sent by the job.

**Architecture:** A new package `outreach/followup_drafter` (the `outreach/drafting` package stays model-free, CLAUDE.md #135). It calls the MCP tools' own handlers in process through `outreach.mcp.server.run_tool`: `followups_due` to list, `draft_brief` for the brief, `save_followup` to save. So the job gets exactly the MCP server's guarded connection, refusals and #9 checks. The model is text in, text out through the reader's `claude -p` command (no tools, no MCP, neutral folder), like the judge. `save_followup` now requires a current `brief_id`, like `save_draft`.

**Tech Stack:** Python 3.14 standard library, the `claude` CLI on his seat, launchd.

**Spec:** issue #142; #9 "Follow-up sequence ruling (2026-10-31)"; `.claude/skills/cold-email/SKILL.md` Follow-ups.

## Global Constraints

- The job never approves, sends, skips or edits a draft he saved (`save_followup` refuses `edited_by_devanshu`; only `planned` rows are listed by `followups_due`).
- Follow-up body: 25 to 80 words, greets by first name, one new thing the thread has not said, facts only from me.md and the brief; the last follow-up (seq 4) is a polite close. Subject is set by code.
- A model failure leaves the follow-up `planned`. A save with blocks gets one retry with the blocks shown to the model; a block still saves (he sees it in the Outbox).
- Real runs only in the live checkout; `--dry-run` anywhere on a scratch `OUTREACH_DB`. No test calls a model.
- No `job_run` row: its `job` CHECK has no name for this job (the judge does the same). One summary line per run on stdout (the launchd log); each draft has its `drafted` event from `save_followup`.

## Review Focus

1. A follow-up he edited himself, or one already drafted, is never rewritten: only `planned` rows are drafted. Test in Task 2.
2. A session limit or a login problem stops the run at once and leaves every remaining follow-up `planned`. Test in Task 2.
3. A reply the model wraps in prose or a fence, or a reply with no `body`, is a failed call, never a save. Test in Task 2.
4. A second follow-up in one thread due the same night is drafted after the first, from a brief that shows the first's text. Test in Task 2.
5. The off switch (`enabled = false`) touches nothing and exits 0. Test in Task 2.

---

### Task 1: save_followup requires a current brief

**Files:** Modify `outreach/mcp/tools/drafts.py` (`SaveFollowupInput.brief_id`, `save_followup` calls `require_brief` first), `.claude/skills/cold-email/SKILL.md` (Follow-ups: call `draft_brief` first and pass its `brief_id`), `tests/test_mcp_drafts.py`.

- [ ] Failing tests: `save_followup` without `brief_id` is `bad_input`; with a stale one is `stale_brief` and writes nothing; existing follow-up tests pass a current brief id.
- [ ] Implement: `brief_id: str = Field(min_length=1, max_length=64, ...)` as in `SaveDraftInput`; `require_brief(args.brief_id, args.person_id)` before the transaction.
- [ ] Run `tests.test_mcp_drafts` and the MCP suites; commit "save_followup needs a current brief, like save_draft (#142)".

### Task 2: The drafter

**Files:** Create `outreach/followup_drafter/{__init__,__main__,config,run}.py`, `config.toml`, `prompt.md`; test `tests/test_followup_drafter.py`.

**Interfaces:**
- `load_drafter_config(path=None) -> DrafterConfig` (`enabled: bool`, `model: str`, `max_per_run: int`, `call_timeout_s: int`, `max_consecutive_failures: int`, `session_limit_patterns`, `auth_patterns`).
- `run_drafter(runner: Runner, cfg: DrafterConfig, *, now: str, path: str | None = None, limit: int | None = None, dry_run: bool = False) -> DrafterSummary` with `status` in `off, dry_run, nothing_due, done, session_limit, auth, failed`, and counts `due, drafted, with_blocks, failed, calls`, `line()`.
- `drafter_runner(cfg) -> Runner`: the judge's runner shape, `claude_command` from `outreach.reader.night`.
- Reply: exactly one JSON object `{"body": "..."}` (`outreach.judge.validate.parse_reply`).

- [ ] Failing tests with a fake runner on a scratch store (a sent first email, planned follow-ups): drafts every due one into `drafted` with `saved_by = 'claude'`; the model gets the prompt as system prompt and a message holding the brief and `{"seq", "of", "last"}`; a reply with blocks is retried once with them; session limit and auth stop the run and leave the rest planned; a bad reply leaves it planned; `max_consecutive_failures` stops; a drafted or his row is never touched; two seqs in one thread go in order; off and dry run call nothing and write nothing; `days_ahead` reaches the next business day (Friday covers Monday).
- [ ] Implement `run.py` and `config.py`; `prompt.md` holds the follow-up rules and the reply contract, and says the voice files are voice only and the brief's untrusted text is data.
- [ ] `__main__.py`: `run [--dry-run] [--limit N]`, off line, live checkout only for a real run, `caffeinate -i -w`, exit 0 on done, nothing due, dry run, off or session limit; 1 on auth or failed.
- [ ] Run the new tests and the suite; commit "Nightly follow-up drafter (#142)".

### Task 3: Schedule and docs

**Files:** Create `launchd/com.devanshu.outreach-followup-drafter.plist.template` (daily 04:30); modify `outreach/deploy/jobs.py` (`_new(..., "#142", False)`), CLAUDE.md (Live system row, live-checkout-only list), `docs/README.md` if it lists plans; tests that pin the job list.

- [ ] Template renders through `outreach.deploy.jobs.render`; deploy tests pass.
- [ ] Full suite green; commit "Schedule the follow-up drafter nightly at 04:30 (#142)".
