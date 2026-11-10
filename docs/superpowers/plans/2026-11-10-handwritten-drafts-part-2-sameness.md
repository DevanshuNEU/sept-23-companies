# Handwritten drafts, part 2: sameness and the dry run

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A draft cannot quietly repeat his other emails: the brief tells the drafter what he has used lately, two
warnings catch reused sentences and openings, and a read-only `check_draft` tool lets the drafter test a draft before
saving it.

**Architecture:** One pure module, `outreach/drafting/sameness.py`, reads his first emails (a "corpus") and answers
the sameness questions. The checks module calls it for two warnings; `draft_brief` calls it for an `avoid` block; the
new MCP tool runs the same checks a save runs and writes nothing.

**Tech Stack:** Python 3.14 standard library, `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-09-handwritten-drafts-design.md` (Part 1's two sameness warnings, Part 2,
and "A dry-run check tool" under Calls made).

**Depends on:** part 1 (#191, PR #197). This branch stays local until #197 is in master.

## Global Constraints

- His first emails = `message` rows with `seq = 0` and `state` in `OPEN_STATES` (drafted, approved, sending,
  send_unknown, sent, failed), excluding the message being checked.
- A sentence counts for sameness when it has 6 or more words after the greeting line and above the signature;
  compared lowercased with punctuation stripped and spaces collapsed.
- `reused_sentence` (warn): a sentence that appears in 3 or more of his other first emails. `same_opening` (warn): the
  first sentence's first 4 words match 5 or more of his last 100 first emails. Both first emails only, both warnings
  only, never blocks.
- The brief's `avoid` lists never change the `brief_id`.
- `check_draft` writes nothing (no grants), refuses the same people `save_draft` refuses, and needs no `brief_id`.
- No dashes in copy.

## Review Focus

- A draft that has not been saved yet (no message id) is checked against all his emails. Test: `test_unsaved_draft_counts_every_email`.
- An email whose body is only a greeting and a signature yields no sentences and no crash. Test: `test_empty_body_has_no_sentences`.
- Skipped and planned messages never count as his emails. Test: `test_skipped_and_planned_do_not_count`.
- `check_draft` on a person the store refuses (no address, no-go) returns the same refusal code as `save_draft`. Test: `test_check_draft_refuses_like_save_draft`.
- 1,000 emails in the store keep one check under a second. Test: `test_thousand_emails_check_quickly`.

---

### Task 1: The sameness module

**Files:**
- Create: `outreach/drafting/sameness.py`
- Test: `tests/test_drafting_sameness.py`

**Interfaces:**
- Produces: `Corpus` (frozen dataclass: `bodies: tuple[str, ...]`, `subjects: tuple[str, ...]`, newest first);
  `load_corpus(conn, *, exclude_message_id: int | None) -> Corpus`; `sentences(body: str) -> list[str]` (normalised);
  `first_sentence(body: str) -> str` (normalised, "" when none); `reused(body: str, corpus: Corpus, min_count: int = 3)
  -> list[tuple[str, int]]`; `opening_count(body: str, corpus: Corpus, last: int = 100, words: int = 4) -> int`;
  `overused(corpus: Corpus, min_count: int = 10) -> list[dict]` (`{"text", "count"}`, most first, sentences and
  6-word runs); `recent_openings(corpus, n=30) -> list[str]`; `recent_subjects(corpus, n=30) -> list[str]`;
  `evidence_used(conn, *, since: str) -> dict[str, int]` (the `project` field of first emails created on or after
  `since`).

- [ ] **Step 1: Write the failing tests**: `test_sentences_skip_greeting_signature_and_short_ones`,
  `test_empty_body_has_no_sentences`, `test_first_sentence_is_after_the_greeting`,
  `test_reused_counts_other_emails_not_repeats_inside_one`, `test_opening_count_uses_first_four_words_of_last_100`,
  `test_overused_lists_sentences_and_runs_seen_10_times`, `test_load_corpus_excludes_the_message_and_newest_first`,
  `test_skipped_and_planned_do_not_count`, `test_unsaved_draft_counts_every_email`, `test_evidence_used_counts_projects_since`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_drafting_sameness -v`. Expected: FAIL (no module).
- [ ] **Step 3: Implement** the module. Signature lines come from `signature_lines()`; the greeting is the first line
  when it matches `hi|hello|hey|dear ... ,`.
- [ ] **Step 4: Run** the same command. Expected: OK.
- [ ] **Step 5: Commit** `Drafting: a sameness module over his first emails (#203)`

### Task 2: The two warnings

**Files:**
- Modify: `outreach/drafts/checks.py` (`WARN_CODES`, new `_reused_sentence`, `_same_opening`, `run_checks`)
- Test: `tests/test_drafts_checks.py`

**Interfaces:**
- Consumes: `load_corpus`, `reused`, `opening_count` (Task 1). One `load_corpus` call per `run_checks`.
- Produces: warn codes `reused_sentence` and `same_opening`, after `narrated_email` in `WARN_CODES`. Detail copy quotes
  the sentence and the count, for example "Used in 4 of your other emails: ...".

- [ ] **Step 1: Write the failing tests**: `test_reused_sentence_warns_at_3_other_emails`, `test_reused_sentence_passes_at_2`,
  `test_same_opening_warns_at_5_of_last_100`, `test_sameness_is_not_checked_on_followups`, `test_thousand_emails_check_quickly`
  (seed 1,000 first emails, one `run_checks` under 1 second), and add both codes to `CLEAN_CODES`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_drafts_checks -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command, then `tests.test_mcp_drafts tests.test_api_approve tests.test_sending_precheck`. Expected: OK.
- [ ] **Step 5: Commit** `Checks: warn when a sentence or an opening repeats his other emails (#203)`

### Task 3: The brief's avoid block

**Files:**
- Modify: `outreach/mcp/tools/drafts.py` (`draft_brief`)
- Test: `tests/test_mcp_draft_brief.py`

**Interfaces:**
- Produces: `brief["avoid"] = {"recent_openings": [...], "recent_subjects": [...], "overused": [...], "evidence_used": {...}}`,
  `evidence_used` over the last 14 days of `ctx.now`.

- [ ] **Step 1: Write the failing tests**: `test_brief_lists_recent_openings_and_subjects`, `test_brief_lists_overused_text`,
  `test_brief_counts_evidence_used_in_14_days`, `test_avoid_does_not_change_the_brief_id`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_mcp_draft_brief -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** it again. Expected: OK.
- [ ] **Step 5: Commit** `Brief: tell the drafter what he has used lately (#203)`

### Task 4: The check_draft tool and the skill

**Files:**
- Modify: `outreach/mcp/tools/drafts.py` (new `CheckDraftInput`, `check_draft`, a `ToolSpec` with no grants),
  `.claude/skills/cold-email/SKILL.md` (step 5: read `avoid`; step 6: run `check_draft` before `save_draft`)
- Test: `tests/test_mcp_drafts.py`

**Interfaces:**
- Produces: tool `check_draft(person_id: int, subject: str, body: str, seq: int = 0, hook: str = "")` returning
  `{"word_count", "blocks", "warns", "passes"}` in the same shape `save_draft` returns them; for `seq >= 1` the subject
  is "Re: " plus the first email's subject.

- [ ] **Step 1: Write the failing tests**: `test_check_draft_returns_save_draft_checks_and_writes_nothing` (fingerprint
  unchanged), `test_check_draft_refuses_like_save_draft`, `test_check_draft_leaves_its_own_message_out_of_sameness`,
  `test_check_draft_is_registered_without_grants`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_mcp_drafts -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Reuse `checks_of`; the store guard needs no new grant.
- [ ] **Step 4: Run** it again, then the whole suite `uv run python -m unittest discover -s tests -t .`. Expected: OK.
- [ ] **Step 5: Commit** `MCP: check_draft runs every save check and stores nothing (#203)`
