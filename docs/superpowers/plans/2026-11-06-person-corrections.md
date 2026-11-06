# Person corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude can fix a person's fields, clear a bounced address and move a person to another company through three new MCP tools, each logged and raised to Needs-you.

**Architecture:** One new tool module `outreach/mcp/tools/corrections.py` with three handlers and shared helpers. The guard gets one narrow exception so `move_person` alone may change `person.company_id`. Edit notices reuse the `flagged` review kind through a transaction-free variant of `raise_flag`. No schema change, no API or web change, no message or touch write.

**Tech Stack:** Python 3.14, SQLite (STRICT tables, temp-trigger guard), pydantic v2 strict inputs, the official `mcp` SDK, `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-06-person-corrections-design.md` (issue #181). Out of scope: removal (#183), a fresh first email after a bounce (#182).

## Global Constraints

- Claude never approves, sends, skips, unapproves or pauses an email, and never writes a `no_go` row. No tool writes `touch` or `message`.
- No em dashes or en dashes in any text a person reads: tool descriptions, errors, review reasons, docs, commits.
- Every failure is `ToolError(code, message, fix)`; a refusal writes nothing.
- Tests: `uv run python -m unittest discover -s tests -t .` from the repo root. Test first, watch it fail for the right reason.
- `SERVER_VERSION` goes "2" to "3" (`outreach/mcp/tools/__init__.py`).
- Never call the app's API or run the sender. Tests use the scratch store (`tests/mcptest.py`, `tests/apiseed.py`).

## Review Focus

- Clearing an email on a person whose message is already `approved`: the message row stays untouched and the sender's queue verdict for it is `no_address` (the mail is held, not sent).
- A replacement address that already belongs to someone else (unique index): a plain error, nothing written, no review item.
- Replacing the address after a sent, un-bounced first email: refused, even for an import touch with no `first_sent_at`.
- A LinkedIn-channel person given an email flips to `email` and drops `linkedin_reason`; an email-channel person cleared flips to `linkedin` with `no_email`.
- `move_person` to the same company, a merged-away company, a missing company, and a person on a person no-go: no write, and the merged id lands on the survivor.
- The same call twice returns the same review item and one event, not two.

---

### Task 1: The guard lets `move_person` change `company_id`, and nothing else does

**Files:**
- Modify: `outreach/mcp/guard.py` (`NEVER_COLUMNS`, `check_grants`, `_trigger_sql`, the `person_update` entry of `_VALUE_TRIGGERS`)
- Test: `tests/test_mcp_guard.py`

**Interfaces:**
- Produces: `MOVE_ONLY_COLUMNS: frozenset[str] = frozenset({"company_id", "updated_at", "revision"})` exported from `outreach.mcp.guard`. A `person` grant whose `update` names `company_id` passes `check_grants` only when `update <= MOVE_ONLY_COLUMNS`. On a connection opened with such a grant, the `person_update` trigger does not refuse a `company_id` change; on every other connection it still does.

- [ ] **Step 1: Write the failing tests** in `tests/test_mcp_guard.py`, following that file's existing style (open a guarded connection on a scratch store, run SQL, assert refusal text):
  - `test_company_id_grant_is_refused_with_other_person_columns`: `check_grants({"person": Grant(update=frozenset({"company_id", "email"}))})` raises `GrantError`.
  - `test_company_id_grant_alone_is_allowed`: `check_grants({"person": Grant(update=MOVE_ONLY_COLUMNS)})` does not raise.
  - `test_move_connection_changes_company_id_but_not_added_by`: with that grant, `UPDATE person SET company_id = ?, updated_at = ?, revision = revision + 1` succeeds; `UPDATE person SET added_by = 'devanshu'` is refused (not authorized).
  - `test_other_connections_still_refuse_company_id`: a connection with `PERSON_UPDATE`-style grants (no `company_id`) refuses the same update with "mcp guard" or "not authorized".
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_mcp_guard -v`. Expected: the new tests FAIL (`MOVE_ONLY_COLUMNS` missing).
- [ ] **Step 3: Implement** in `outreach/mcp/guard.py`: add `MOVE_ONLY_COLUMNS` to `__all__`; in `check_grants`, for table `person`, compute `bad` as before but drop `company_id` from it when `grant.update <= MOVE_ONLY_COLUMNS`. In `_trigger_sql`, build the `person_update` trigger condition from `grants`: when `grants.get("person")` names `company_id` in `update`, omit the `NEW.company_id IS NOT OLD.company_id` clause (keep `added_by`). Everything else in the trigger set stays.
- [ ] **Step 4: Run** `uv run python -m unittest tests.test_mcp_guard tests.test_mcp_write_grants -v`. Expected: PASS.
- [ ] **Step 5: Commit** `git add outreach/mcp/guard.py tests/test_mcp_guard.py && git commit -m "Guard: only a move-only person grant may change company_id (#181)"`

### Task 2: `update_person`, with the shared helpers

**Files:**
- Create: `outreach/mcp/tools/corrections.py`
- Modify: `outreach/mcp/tools/research.py` (split `raise_flag`), `outreach/mcp/server.py` (add `"outreach.mcp.tools.corrections"` to the tool module list at line 38-40), `outreach/mcp/tools/__init__.py` (`SERVER_VERSION = "3"` and its comment)
- Test: `tests/test_mcp_corrections.py` (create), `tests/test_mcp_write_grants.py`

**Interfaces:**
- Consumes: `Ctx`, `Input`, `ToolError`, `ToolSpec` (`outreach.mcp.tools`); `Grant`; `resolve`, `Family`, `MAX_ID`, `SENT_STATES` (`outreach.mcp.tools.reads`); `EMAIL`, `linkedin_key` (`outreach.sources.keys`); `append_event`, `transaction` (`outreach.store`).
- Produces in `research.py`: `raise_flag_in(ctx, *, company_id, reason, evidence_key, posting_id=None, person_id=None, detail=None) -> dict` (the body of today's `raise_flag` without its `transaction`; the caller holds the write transaction). `raise_flag` becomes `with transaction(ctx.conn): return raise_flag_in(...)`, behavior unchanged.
- Produces in `corrections.py`:
  - `PERSON_EDIT_COLUMNS = frozenset({"name","title","role_kind","email","linkedin_url","profile_text","notes","channel","linkedin_reason","updated_at","revision"})`
  - `already_sent(conn, person_id) -> bool`: true when the person's touch has `first_sent_at`, or `origin = 'import'`, or their seq 0 message is in `SENT_STATES`, and the touch is not `bounced`.
  - `record_edit(ctx, person, changes: dict[str, tuple], *, kind: str, extra: dict | None = None) -> dict`: appends one `event` (`entity="person"`, `actor="claude"`, `kind` as given, detail `{field: {"old": o, "new": n}}` plus `extra`) and calls `raise_flag_in` with `evidence_key = "claude-edit:" + sha256 of person_id and the sorted changes`, company = the person's survivor company, a plain reason such as `Claude changed Pat Lee's email: pat@old -> (none)` (hyphens only). Returns `{"review_id", "review_created"}`.
  - Tool `update_person`, input `UpdatePersonInput(person_id: int, name: str | None, title: str | None, role_kind: Literal[...] | None, linkedin_url: str | None, email: str | None, profile_text: str | None, notes: str | None)`. Use pydantic's `model_fields_set` to tell "sent as null" from "not sent": only `email`, `linkedin_url` may be sent as `null` (clears); null for other fields is `bad_input`. Returns `{"person_id", "changed": [field names], "review_id", "channel", "message"}`.
- Grants: `{"person": Grant(update=PERSON_EDIT_COLUMNS), "review_item": Grant(insert=True), "event": Grant(insert=True)}`.

- [ ] **Step 1: Write failing tests** in `tests/test_mcp_corrections.py` (`CorrectionsCase(McpCase)` with an Acme company and people via `tests.apiseed`):
  - `test_update_changes_only_sent_fields_and_logs_old_values`: change title and name; row updated, `revision` +1, one event `kind="edited"`, `actor="claude"`, detail old and new per field; one open `flagged` review item raised by claude naming the person.
  - `test_email_null_clears_it_and_moves_an_email_person_to_linkedin`: channel becomes `linkedin`, `linkedin_reason = "no_email"`.
  - `test_email_on_a_linkedin_person_flips_to_email`: channel `email`, reason NULL.
  - `test_email_is_lowercased_and_checked`: `" Pat@Acme.Example "` stores `pat@acme.example`; `"nope"` is `bad_input` and writes nothing (`fingerprint` unchanged).
  - `test_an_address_already_on_someone_else_is_refused`: error code `email_in_use`, fix names the other person, no write, no review item.
  - `test_replacing_after_a_sent_first_email_is_refused`: seed `seed.touch` + `seed.send_first`; `email_sent_to` error; a bounced touch (`seed.touch(..., state="bounced", bounced_at=...)`) allows it; an `origin='import'` touch refuses.
  - `test_linkedin_url_is_normalised_and_bad_ones_refused`.
  - `test_same_call_twice_is_one_event_and_one_item`; `test_nothing_changed_writes_nothing` (`changed == []`).
  - `test_a_cleared_email_is_held_by_the_sender`: person with an `approved` message, clear the email via the tool, then call the sender's queue verdict for that message (the function in `outreach/sending/queue.py` that returns `no_address`; read it for the exact name) and assert `no_address`; assert the message row is byte-identical.
  - In `tests/test_mcp_write_grants.py`: add `"update_person": {"person": Grant(update=PERSON_EDIT_COLUMNS), "review_item": INSERT, "event": INSERT}` to `PINNED` and a `check_tool("update_person", outside=[...], valid=...)` test next to the others (outside: `UPDATE person SET company_id`, `UPDATE person SET added_by`; valid: change a title).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_mcp_corrections tests.test_mcp_write_grants -v`. Expected: FAIL (no tool `update_person`).
- [ ] **Step 3: Implement** `raise_flag_in` split in `research.py` (existing research tests must stay green), then `corrections.py` as specified. One transaction wraps the person update, the event and `raise_flag_in`. Catch `sqlite3.IntegrityError` on the unique indexes and return `email_in_use` or `linkedin_in_use`. Registration: add the module name to the list in `server.py`; bump `SERVER_VERSION`.
- [ ] **Step 4: Run** `uv run python -m unittest tests.test_mcp_corrections tests.test_mcp_write_grants tests.test_mcp_research tests.test_mcp_server -v`. Expected: PASS.
- [ ] **Step 5: Commit** `git add -A outreach tests && git commit -m "MCP: update_person fixes a person's fields, logged and raised to Needs-you (#181)"`

### Task 3: `mark_email_bounced`

**Files:**
- Modify: `outreach/mcp/tools/corrections.py`
- Test: `tests/test_mcp_corrections.py`, `tests/test_mcp_write_grants.py`

**Interfaces:**
- Consumes: `record_edit`, `PERSON_EDIT_COLUMNS` from Task 2.
- Produces: tool `mark_email_bounced`, input `BouncedInput(person_id: int, reason: str = Field(min_length=3, max_length=300))`, result `{"person_id", "cleared_email", "channel", "review_id", "message"}`. Grants: `{"person": Grant(update=frozenset({"email","channel","linkedin_reason","updated_at","revision"})), "review_item": INSERT, "event": INSERT}`.

- [ ] **Step 1: Write failing tests:**
  - `test_bounce_clears_the_address_and_records_the_reason`: the Bo case (email-channel person, bounced touch): email NULL, channel `linkedin`/`no_email`, event `kind="email_bounced"` with the old address and reason in detail, touch and messages unchanged (compare rows before and after).
  - `test_person_reads_no_email_after_a_bounce`: call the `person` tool; `can_draft_email` reason is `no_email`, not `already_emailed`.
  - `test_no_address_is_an_error`: `no_email` code, nothing written.
  - `test_works_before_the_sender_saw_the_bounce`: open touch with a sent first email still clears.
  - `test_reason_is_trimmed_and_dashes_become_hyphens` (use `research.clean`).
  - Grant test entry in `test_mcp_write_grants.py` with outside writes `UPDATE person SET name = 'X'` and `UPDATE person SET company_id`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_mcp_corrections tests.test_mcp_write_grants -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `mark_email_bounced(ctx, args) -> dict` in `corrections.py`, using `record_edit(..., kind="email_bounced", extra={"reason": reason})`. Its reason text names the person and the old address.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -am "MCP: mark_email_bounced clears a dead address and logs why (#181)"`

### Task 4: `move_person`

**Files:**
- Modify: `outreach/mcp/tools/corrections.py`
- Test: `tests/test_mcp_corrections.py`, `tests/test_mcp_write_grants.py`

**Interfaces:**
- Consumes: `record_edit`, `MOVE_ONLY_COLUMNS` (Task 1), `resolve(conn, company_id) -> (Family, survivor, note)`.
- Produces: tool `move_person`, input `MoveInput(person_id: int, company_id: int)`, result `{"person_id", "from_company", "to_company", "warnings": [...], "merged_note", "review_id"}`. Grants: `{"person": Grant(update=MOVE_ONLY_COLUMNS), "review_item": INSERT, "event": INSERT}` (the review notice goes on the new company).

- [ ] **Step 1: Write failing tests:**
  - `test_move_changes_company_and_keeps_history_where_it_happened`: person with a sent touch moves; `person.company_id` is the target; `outreach_touch.company_id` and earlier events keep the old company; the move event detail names `from` and `to`.
  - `test_move_to_a_merged_company_lands_on_the_survivor`: use `merge` from `tests/test_mcp_reads.py`.
  - `test_same_company_is_a_no_op_error`, `test_missing_company_and_person_are_not_found`.
  - `test_a_person_on_no_go_is_refused`: use `no_go` helper; code `person_no_go`, no write.
  - `test_closed_or_no_go_target_warns_but_moves`.
  - `test_email_stays_after_a_move`.
  - Grant test entry (outside: `UPDATE person SET email = 'x@y.z'`, `UPDATE person SET added_by = 'devanshu'`).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_mcp_corrections tests.test_mcp_write_grants -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `move_person(ctx, args) -> dict`. Person no-go is read with `no_go_sets` (`outreach.drafts.followups`, as `people.py` does). Warning strings are plain: "Acme is closed" or "Acme is on the no-go list".
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -am "MCP: move_person changes a person's company, history stays put (#181)"`

### Task 5: Say it in the server text and the skill, then run everything

**Files:**
- Modify: `outreach/mcp/server.py` (the instruction block at lines 44-60), `.claude/skills/cold-email/SKILL.md`, `CLAUDE.md` (the MCP guard paragraph under "Live system")
- Test: `tests/test_mcp_server.py` (existing checks on the tool list and instructions)

**Interfaces:** none new.

- [ ] **Step 1: Write the failing test** in `tests/test_mcp_server.py`: the tool list contains `update_person`, `mark_email_bounced`, `move_person`, and the server instructions mention them and still say Claude never approves, sends or marks no-go.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_mcp_server -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the text. Server instructions: one short paragraph, "update_person, mark_email_bounced and move_person correct a person Claude or Devanshu added. They apply at once, log the old value and raise a Needs-you note. A bounced address comes off with mark_email_bounced." `SKILL.md`: add a short "A person is wrong or the address bounced" step using the three tools and saying a new address after a sent email waits for Devanshu. `CLAUDE.md`: one sentence that the guard allows `person.company_id` only for `move_person`'s grant.
- [ ] **Step 4: Run the full suite** `uv run python -m unittest discover -s tests -t .`. Expected: all pass. Grep the new files for dashes: `grep -rnP "\x{2014}|\x{2013}" docs/superpowers/specs/2026-11-06-person-corrections-design.md docs/superpowers/plans/2026-11-06-person-corrections.md outreach/mcp/tools/corrections.py` prints nothing.
- [ ] **Step 5: Commit** `git commit -am "MCP: say what the person corrections do and refuse (#181)"`, then push and open the PR to `master` with "Closes #181" in the body, checking `gh pr view --json baseRefName,mergeStateStatus` shows `master` and `CLEAN` before asking him to merge. After merge: `uv run python -m outreach.deploy` is not needed; the next Claude Code session loads the new tools (no registration change, no schema change).
