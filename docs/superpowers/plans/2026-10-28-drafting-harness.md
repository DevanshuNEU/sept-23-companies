# Drafting Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Code, not the model, decides what the drafter reads (his DevVault voice files, me.md, the person) and verifies what it writes; a draft made without that brief cannot be saved.

**Architecture:** A new pure package `outreach/drafting/` holds the voice loader, the brief and its id, and the voice checks. It makes no model calls. A read-only MCP tool `draft_brief` hands the model its input. `save_draft` refuses a missing or stale `brief_id`. The voice checks plug into the existing `run_checks`.

**Tech Stack:** Python 3.14, stdlib `unittest`, pydantic (the MCP `Input` model), the existing `outreach.mcp` tool registry.

**Spec:** GitHub issue #135, and the design agreed in chat on 2026-10-28 (refuse outright without a brief).

## Global Constraints

- Tests first: write the test, watch it fail for the right reason, then make it pass (CLAUDE.md "Building").
- Run tests with `uv run python -m unittest discover -s tests -t .`; a single module with `uv run python -m unittest tests.test_x -v`.
- Tests use scratch settings only (`tests/__init__.py`); no test reads the real DevVault, so the voice folder comes from `OUTREACH_VOICE_DIR`.
- No em or en dashes in any file, comment or message.
- A voice or style check is a `warn`; only a precondition refuses. Content checks never refuse (existing contract of `save_draft`).
- `outreach/drafting/` must not import `outreach.mcp` and must not call a model or the network.
- One issue, one branch, one PR to `master` (#135). Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Follow-ups (`save_followup`) are out of scope for the brief; they only gain the new warns through `run_checks`.

## Review Focus

- The voice folder is missing or a file is unreadable: `draft_brief` and `save_draft` refuse with a clear code, never pass silently.
- A brief issued for person A is used to save person B: refused.
- A voice file or me.md changes between `draft_brief` and `save_draft`: refused as stale.
- A body with no digits, or a recruiter body that legitimately carries the OPT line: no false warn.
- The Outbox editor's Save (no brief, no `ground` text) still works: it skips the grounding check and never needs a brief.

---

## File Structure

- Create `outreach/drafting/__init__.py`: exports.
- Create `outreach/drafting/voice.py`: load the voice pack from DevVault.
- Create `outreach/drafting/brief.py`: evidence reader, `brief_id`, and `build_brief`.
- Create `outreach/drafting/voice_checks.py`: the pure voice checks.
- Modify `outreach/drafts/checks.py`: add the new codes and call `voice_checks`.
- Modify `outreach/mcp/tools/drafts.py`: `SaveDraftInput.brief_id`, the gate, the new `draft_brief` tool.
- Create `tests/fixtures/devvault/{skills/human-voice.md,lens/voice.md,lens/contexts/context_outreach.md}`.
- Create `tests/test_drafting_voice.py`, `tests/test_drafting_brief.py`, `tests/test_drafting_voice_checks.py`, `tests/test_mcp_draft_brief.py`.
- Modify `tests/__init__.py`, `tests/test_mcp_drafts.py` (shared helper), `tests/test_mcp_server.py` (tool list), `CLAUDE.md`, `.claude/skills/cold-email/SKILL.md`.

---

### Task 1: Voice pack loader

**Files:**
- Create: `outreach/drafting/__init__.py`, `outreach/drafting/voice.py`
- Create: `tests/fixtures/devvault/skills/human-voice.md`, `tests/fixtures/devvault/lens/voice.md`, `tests/fixtures/devvault/lens/contexts/context_outreach.md` (a few lines each, distinct text)
- Modify: `tests/__init__.py` (set `OUTREACH_VOICE_DIR` to the fixture folder)
- Test: `tests/test_drafting_voice.py`

**Interfaces:**
- Produces: `VOICE_FILES: tuple[str, ...]` = the three relative paths above, in that order.
- Produces: `@dataclass(frozen=True) class VoicePack: files: tuple[tuple[str, str], ...]; digest: str` where `files` is (relative path, text) and `digest` is the sha256 hex of the paths and texts joined in order.
- Produces: `class VoiceUnreadable(Exception)` with `.path`.
- Produces: `load_voice_pack(root: Path | str | None = None) -> VoicePack`; `root` defaults to `OUTREACH_VOICE_DIR`, else `~/Desktop/DevVault`. Raises `VoiceUnreadable` if any file is missing, unreadable or empty.

- [ ] **Step 1: Write the failing tests** in `tests/test_drafting_voice.py`: `test_loads_the_three_files_in_order` (paths equal `VOICE_FILES`, text non-empty); `test_digest_changes_when_a_file_changes` (copy the fixture to a temp dir, record the digest, append a character to one file, digest differs); `test_digest_is_stable_for_equal_content`; `test_missing_file_raises_voice_unreadable` (temp dir without one file; `.path` names it); `test_empty_file_raises`; `test_default_root_reads_env` (patch `OUTREACH_VOICE_DIR`).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_drafting_voice -v`. Expected: FAIL, `No module named outreach.drafting`.
- [ ] **Step 3: Implement** `voice.py` as specified; add the fixtures and the `OUTREACH_VOICE_DIR` line to `tests/__init__.py` (path of `tests/fixtures/devvault`, resolved from `__file__`).
- [ ] **Step 4: Run** the module again. Expected: all pass.
- [ ] **Step 5: Commit** `Drafting: load the voice pack from DevVault (#135)`.

### Task 2: Brief id and evidence

**Files:**
- Create: `outreach/drafting/brief.py`
- Modify: `outreach/drafting/__init__.py`
- Test: `tests/test_drafting_brief.py`

**Interfaces:**
- Consumes: `VoicePack`, `load_voice_pack` from Task 1; `outreach.plan.drafts` resolves me.md at `Path(os.environ.get("OUTREACH_MAIL_HOME") or repo mail dir) / "me.md"` (reuse that rule; copy the 2-line resolution into `brief.py` as `me_path() -> Path`, do not import a private name).
- Produces: `read_evidence(path: Path | None = None) -> str`: me.md text from the top up to, not including, the line starting `## Needs Devanshu`; raises `VoiceUnreadable` if unreadable or empty.
- Produces: `brief_id(pack: VoicePack, evidence: str, person_id: int) -> str`: first 16 hex chars of sha256 over `pack.digest`, the evidence and the person id.
- Produces: `check_brief(brief: str, pack: VoicePack, evidence: str, person_id: int) -> bool` (constant-time compare via `hmac.compare_digest`).

- [ ] **Step 1: Write the failing tests**: `test_evidence_stops_at_needs_devanshu` (tmp me.md with a fact above and an `ASK:` line below; the ask text is absent); `test_evidence_unreadable_raises`; `test_brief_id_is_16_hex`; `test_brief_id_differs_per_person`; `test_brief_id_changes_when_evidence_changes`; `test_brief_id_changes_when_voice_changes`; `test_check_brief_accepts_its_own_id_and_rejects_others` (wrong id, empty string, another person's id).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_drafting_brief -v`. Expected: FAIL, names not defined.
- [ ] **Step 3: Implement** the three functions above in `brief.py`.
- [ ] **Step 4: Run** the module. Expected: pass.
- [ ] **Step 5: Commit** `Drafting: a brief id binds the voice pack, the evidence and the person (#135)`.

### Task 3: Voice checks

**Files:**
- Create: `outreach/drafting/voice_checks.py`
- Modify: `outreach/drafts/checks.py` (add codes; call the new function from `run_checks`; new optional keyword `ground: str | None = None`)
- Test: `tests/test_drafting_voice_checks.py`; extend `tests/test_drafts_checks.py` only where an existing assertion counts all checks (fix counts, do not weaken)

**Interfaces:**
- Produces: `voice_checks(body: str, role_kind: str, *, ground: str | None = None) -> list[dict]` returning `Check` dicts (same keys as `outreach.drafts.checks._check`: code, severity, title, detail, fix, spans). Every returned check has severity `warn` or `pass`, never `block`.
- Codes added to `WARN_CODES` in `checks.py`, after `recruiter`: `voice_eager`, `voice_house_word`, `voice_system_word`, `voice_recital`, `voice_rhythm`, `voice_opt_line`, `voice_ungrounded`.
- `run_checks(conn, d, *, banned, now, ground=None)` adds `*voice_checks(d.body, d.role_kind, ground=ground)` to its list.

Rules (each a regex or count on the body with links removed, case-insensitive, `\b` boundaries):
- `voice_eager`: glad to, happy to, I'd love, I would love, looking forward, excited, thrilled, confident I can, passionate, thank you for being open.
- `voice_house_word`: genuinely, curious, no agenda, either way, leverage.
- `voice_system_word`: lane, wave, req ID.
- `voice_recital`: `your (resume|profile|linkedin|bio) (lists|says|shows|mentions)` or `I (saw|noticed|read) that you`.
- `voice_rhythm`: either the "not X, it's Y" shape `\bnot (just |only )?[^.,]{1,50}, (it's|its|but)\b`, or at least 4 sentences whose word counts all lie within 2 of their mean.
- `voice_opt_line`: role_kind is not `recruiter` and the body matches `F-1|\bOPT\b|STEM OPT|sponsor`.
- `voice_ungrounded`: only when `ground` is not None; every number token (`\d[\d,.]*%?`) in the body, years `19xx` and `20xx` excepted, must appear as a substring of `ground`; the detail lists the missing ones.

- [ ] **Step 1: Write the failing tests**, one pair per code, name pattern `test_<code>_flags_<case>` and `test_<code>_passes_<case>`; include: `test_recruiter_may_carry_the_opt_line`, `test_no_digits_passes_ungrounded`, `test_ground_none_skips_ungrounded`, `test_short_body_skips_rhythm`, and `test_no_check_is_a_block`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_drafting_voice_checks -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `voice_checks.py`; one small function per code, each returning one check (a warn listing every hit, or a pass). Add the codes to `WARN_CODES`; wire `run_checks`.
- [ ] **Step 4: Run** the new module, then `tests.test_drafts_checks` and `tests.test_check_draft`. Expected: pass (adjust only counts that now include the new passes).
- [ ] **Step 5: Commit** `Drafts: voice checks join the draft checks (#135)`.

### Task 4: The draft_brief tool

**Files:**
- Modify: `outreach/mcp/tools/drafts.py` (new `DraftBriefInput`, `draft_brief` handler, `ToolSpec` with no grants)
- Test: `tests/test_mcp_draft_brief.py`

**Interfaces:**
- Consumes: Tasks 1 and 2; the existing `person` read handler in `outreach/mcp/tools/reads.py` (find its function; call it, do not copy its queries).
- Produces: tool `draft_brief(person_id: int) -> dict` with keys `brief_id`, `person` (the `person` tool's dict), `voice` = `{"digest": str, "files": [{"path": str, "text": str}]}`, `evidence` (str), `limits` = `{"words": [lo, hi], "max_links": 2, "banned_phrases_path": str}` using `outreach.drafts.checks.WORDS`, `DEFAULT_WORDS`, `MAX_LINKS`, and `can_draft_email` copied through from `person`.
- Errors: `voice_unreadable` (a `ToolError` with the unreadable path and the fix "Check OUTREACH_VOICE_DIR or the DevVault folder; nothing was drafted").

- [ ] **Step 1: Write the failing tests** using `McpCase` like `tests/test_mcp_drafts.py`: `test_brief_has_voice_files_evidence_limits_and_id`; `test_brief_id_matches_check_brief`; `test_brief_writes_nothing` (store fingerprint unchanged, using `fingerprint`); `test_unreadable_voice_folder_refuses` (patch `OUTREACH_VOICE_DIR` to an empty temp dir, expect `voice_unreadable`); `test_recruiter_words_limit_is_80_100`; `test_unknown_person_refuses_like_the_person_tool`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_mcp_draft_brief -v`. Expected: FAIL (unknown tool).
- [ ] **Step 3: Implement** the handler and register it in `TOOLS`.
- [ ] **Step 4: Run** the module. Expected: pass.
- [ ] **Step 5: Commit** `MCP: draft_brief hands the model its voice, evidence and limits (#135)`.

### Task 5: save_draft requires the brief

**Files:**
- Modify: `outreach/mcp/tools/drafts.py` (`SaveDraftInput.brief_id: str = Field(min_length=1, max_length=64)`; gate at the top of `save_draft`; pass `ground` to the checks; store a `voice_brief` check entry)
- Modify: `tests/test_mcp_drafts.py` (shared `draft_args` helper gets a valid `brief_id`; add the new tests)
- Test: `tests/test_mcp_drafts.py`

**Interfaces:**
- Consumes: `load_voice_pack`, `read_evidence`, `check_brief` (Tasks 1, 2); `voice_checks` through `run_checks(..., ground=...)` (Task 3).
- Gate order inside `save_draft`, before the transaction opens: load the pack and evidence (`voice_unreadable` on failure); a missing `brief_id` is already refused by the input schema; a `brief_id` that `check_brief` rejects raises `ToolError("stale_brief", ...)` with the fix "Call draft_brief for this person again, then save." (this covers a wrong id, another person's id and a voice file or me.md that changed since the brief).
- `ground` text passed to `checks_of`: evidence + the person's `profile_text` and notes + the hook.
- The saved `checks` JSON gains one entry `{"code": "voice_brief", "severity": "pass", "title": "Voice pack read", "detail": "brief <id>, voice <first 12 of digest>", "fix": null, "spans": []}`.

- [ ] **Step 1: Write the failing tests** in `tests/test_mcp_drafts.py`: `test_save_without_brief_id_is_refused_by_the_schema`; `test_save_with_a_wrong_brief_id_is_refused_and_stores_nothing` (code `stale_brief`, fingerprint unchanged); `test_save_with_another_persons_brief_is_refused`; `test_save_after_a_voice_file_changes_is_refused` (rewrite a temp voice copy between brief and save); `test_save_with_unreadable_voice_folder_refuses_with_voice_unreadable`; `test_saved_checks_record_the_brief`; `test_voice_warns_are_returned_but_still_save` (a body with "I'd love to" saves drafted with a `voice_eager` warn); `test_outbox_style_check_without_ground_skips_ungrounded` (call `run_checks` directly with `ground=None`). Update the helper so every other existing test sends a valid brief (compute with `build`-less path: call the `draft_brief` tool through the same client, or compute `brief_id` with Tasks 1 and 2 functions).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_mcp_drafts -v`. Expected: the new tests FAIL, the old ones fail with a missing `brief_id` until the helper changes.
- [ ] **Step 3: Implement** the field, the gate and the `ground` and `voice_brief` plumbing. Add `brief_id` to the tool description ("Call draft_brief first; a draft without a current brief_id is refused").
- [ ] **Step 4: Run** `tests.test_mcp_drafts`, then the whole MCP set `uv run python -m unittest discover -s tests -t . -p "test_mcp_*.py"`. Expected: pass.
- [ ] **Step 5: Commit** `MCP: save_draft refuses a draft made without a current brief (#135)`.

### Task 6: Pin the harness rule and update the docs

**Files:**
- Modify: `tests/test_mcp_server.py` (tool list now contains `draft_brief`; description mentions brief)
- Modify: `outreach/mcp/server.py` (the instructions text names `draft_brief` before `save_draft`)
- Modify: `CLAUDE.md` (new "Drafting harness" paragraph under "Building")
- Modify: `.claude/skills/cold-email/SKILL.md` (step 0 calls `draft_brief` and uses the returned voice files and evidence; step 6 passes `brief_id`) **only after PR #133 is merged into master**; rebase this branch onto master first.

**Interfaces:** none new.

- [ ] **Step 1: Write the failing test**: `test_instructions_name_draft_brief_before_save_draft` and the tool-list update in `tests/test_mcp_server.py`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_mcp_server -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the instruction text. CLAUDE.md paragraph (plain words): the drafting harness is code before and after the model; the model never chooses what it reads; `draft_brief` builds the input from DevVault, me.md and the store; `save_draft` refuses a draft without a current `brief_id`; voice rules live in DevVault and `outreach/drafting/`, never copied into the skill; a change that lets a draft skip the brief breaks this rule.
- [ ] **Step 4: Edit the skill** (after #133 merges): replace the step 0 file list with "call `draft_brief` for each person and write from what it returns", and add `brief_id` to step 6's `save_draft` arguments.
- [ ] **Step 5: Run the whole suite** `uv run python -m unittest discover -s tests -t .`. Expected: OK.
- [ ] **Step 6: Commit** `Drafting harness: document it and pin it in the MCP instructions (#135)`, then push the branch and open the PR (`Closes #135`, base `master`).
