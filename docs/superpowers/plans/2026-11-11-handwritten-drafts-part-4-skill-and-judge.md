# Handwritten drafts, part 4: the version 5 skill and the blind judge

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The cold-email skill writes the way the lab's winning version 5 did, and any later change to it can be
scored blind against the old one by GPT before it ships.

**Architecture:** A new `outreach/draft_lab` package holds the judge: set files in, a shuffled blind prompt per
recipient, GPT through the Codex CLI (no web search, read-only), a markdown report out. Two modes, both from the lab:
`body` (which version reads more human and gets a reply) and `inbox` (which subject and first line get opened among
template decoys). An `export` command writes a read-only sample of stored first emails to a set file. The skill is
rewritten with the writing-for-agents skill.

**Tech Stack:** Python 3.14 standard library, `unittest`, the Codex CLI.

**Spec:** `docs/superpowers/specs/2026-11-09-handwritten-drafts-design.md` (Part 4 and Part 5, and "When a skill change
ships"), `docs/research/2026-11-09-drafting-lab.md` (the lab the judge repeats), #9's rulings of 2026-11-09 and
2026-11-10.

**Depends on:** part 3 (#205, PR #210). This branch stays local until #210 is in master.

## Global Constraints

- The judge never writes drafts and never touches the store's drafts; `export` only reads.
- Real judge runs and `export` run only in the live checkout (they send names, titles and drafts to OpenAI, or read
  the live store); `--dry-run` prints the prompt and calls nothing. Never run by a launchd job.
- The judge prompts are files in the package, copied from the lab, so scores stay comparable across runs.
- A set file is JSON: a list of `{"id": str, "recipient": str, "subject": str, "body": str}`; two sets are compared
  item by item on `id`; ids present in only one set are reported and skipped.
- Versions are shuffled per recipient with a seed that the report prints, so a run can be repeated.
- A skill change ships when, on a sample of 12, the judge picks the new set in at least 8 and the new set does not lose
  on either average score (`human`, `reply`). The report says PASS or FAIL against that bar.
- No em or en dashes. No recipient names in committed files: tests use made-up people.

## Review Focus

- GPT replies with prose around the JSON, or a reply missing some items: parse what is there, report the missing ids. Test: `test_reply_with_prose_and_missing_items`.
- Two sets with different ids: compare the shared ones, list the rest. Test: `test_sets_with_different_ids`.
- Identical bodies in both sets for an item: counted as a tie, not a win. Test: `test_identical_versions_are_ties`.
- A seed gives the same shuffle every time. Test: `test_seed_repeats_the_shuffle`.
- The inbox mode never shows the real sender's name or which set a row came from. Test: `test_inbox_rows_hide_the_source`.

---

### Task 1: The judge library

**Files:**
- Create: `outreach/draft_lab/__init__.py`, `outreach/draft_lab/judge.py`, `outreach/draft_lab/prompt_body.md`,
  `outreach/draft_lab/prompt_inbox.md`, `outreach/draft_lab/decoys.json`
- Test: `tests/test_draft_lab_judge.py`

**Interfaces:**
- Produces: `Draft` (frozen dataclass: id, recipient, subject, body); `load_set(path) -> list[Draft]`;
  `pair_sets(a, b) -> tuple[list[tuple[Draft, Draft]], list[str]]` (shared pairs, ids in one set only);
  `body_prompt(pairs, *, seed) -> tuple[str, dict]` (prompt text and the key mapping each item's A and B to "a" or
  "b"); `inbox_prompt(pairs, *, seed, decoys) -> tuple[str, dict]` (each item: the person, rows of made-up sender
  name, subject and preview, that is the first 110 characters of the body after the greeting line, both sets' rows
  plus 4 decoys, shuffled); `parse_reply(text) -> dict` (the first JSON object); `score_body(reply, key, pairs) ->
  BodyResult` and `score_inbox(reply, key) -> InboxResult` (averages per set, wins, ties, first picks, opened,
  archived, missing ids, the judge's "why" and "repeats" lines); `verdict(result) -> str` ("PASS" or "FAIL" against
  the bar in Global Constraints, for the body mode); `report(result, *, seed, mode) -> str` (markdown).
- The prompts: `prompt_body.md` is the lab's two-version body judge (human, personal, warm, reply, best, why,
  too_much, repeats); `prompt_inbox.md` is the lab's inbox test (open at most 3, never, why). Both say "Do not run
  commands or read files. Reply with one JSON object only." and end with "ITEMS" followed by the JSON the code appends.
  `decoys.json` holds the lab's 7 template decoys (made-up senders).

- [ ] **Step 1: Write the failing tests**: `test_load_set_and_pair`, `test_sets_with_different_ids`,
  `test_seed_repeats_the_shuffle`, `test_body_scoring_counts_wins_and_averages`, `test_identical_versions_are_ties`,
  `test_reply_with_prose_and_missing_items`, `test_inbox_rows_hide_the_source`, `test_inbox_scoring`,
  `test_verdict_pass_and_fail`, `test_report_names_the_seed_and_the_verdict`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_draft_lab_judge -v`. Expected: FAIL (no module).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** it again. Expected: OK.
- [ ] **Step 5: Commit** `Draft lab: a blind GPT judge for two sets of drafts (#211)`

### Task 2: The commands

**Files:**
- Create: `outreach/draft_lab/__main__.py`
- Modify: `outreach/research/gpt.py` (`CodexRunner(search: bool = True)` so the judge can run without web search)
- Test: `tests/test_draft_lab_cli.py`, `tests/test_research_gpt.py`

**Interfaces:**
- Consumes: Task 1's functions; `CodexRunner`.
- Produces:
  - `python -m outreach.draft_lab judge --a A.json --b B.json [--mode body|inbox] [--seed N] [--out FILE] [--dry-run]`:
    prints one summary line ("b wins 9 of 12, human 7.5 vs 6.8, reply 7.0 vs 6.4: PASS" in body mode; first picks
    and opened counts in inbox mode) and writes the report to `--out` (default `.context/draft-lab/<timestamp>.md`
    in the repo, which git ignores).
  - `python -m outreach.draft_lab export --sample N [--state approved|drafted|sent] [--seed N] --out FILE`: a random
    sample of stored first emails as a set file (`id` = message id, `recipient` = "title at company"), read only.
  - Real `judge` runs and `export` only in the live checkout; `--dry-run` prints the prompt and calls nothing.
  - `CodexRunner(search=False)` drops `--search` from the command.

- [ ] **Step 1: Write the failing tests**: `test_judge_dry_run_prints_the_prompt_and_calls_nothing`,
  `test_judge_outside_the_live_checkout_is_refused`, `test_judge_writes_the_report_and_summary_line`,
  `test_export_reads_only_and_writes_a_set_file` (scratch store, fingerprint unchanged), `test_export_outside_the_live_checkout_is_refused`,
  `test_codex_runner_without_search`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_draft_lab_cli tests.test_research_gpt -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Follow `outreach/research/__main__.py` for the guard and the store opening.
- [ ] **Step 4: Run** it again. Expected: OK.
- [ ] **Step 5: Commit** `Draft lab: judge and export commands, live checkout only (#211)`

### Task 3: The version 5 skill

**Files:**
- Modify: `.claude/skills/cold-email/SKILL.md` (rewritten with the writing-for-agents skill)
- Test: none (a skill file); the full suite must stay green.

**What the skill must say** (the lab's version 5 and #9's rulings; keep every hard rule and the MCP steps already there):
- Order of work per person: read the posting (`posting` in the brief); company facts (`company_facts`, run the research
  command or `save_research` when `research_needed`); research the person (`save_research` with `person_id`); find
  where he fits (the one project that does the kind of work they do); write; subject last; `check_draft`; `save_draft`.
- The first line states the company's fact plainly, in the source's own words, or the true tie to the person; never a
  bridge line about himself ("that's the problem I keep coming back to"); never a line about the email itself.
- His projects lead (Overhear, OpenCodeIntel, CallBudget, Saar, Mem Machines, saar CLI, tool-crowding as being
  built); his two jobs in India are at most one supporting line, used when the role is plainly backend or the email
  argues experience.
- A story from `stories` only when it fits the reader, told plainly; never add a feeling he did not write.
- The subject, written last, personal to the pair, at most about 40 characters: a shared past, his small true story,
  a lesson, a result, an honest gap, or a plain question about the role. Not the reader's company news, not warm but
  vague, not a project name with specs, not "interested in". Examples from the lab may be quoted as patterns.
- One easy ask, worded fresh each time; no visa, OPT or sponsorship, to anyone; at most 120 words; one body link, the
  portfolio in the signature; vary the sign-off word.
- Follow-ups: a short plain nudge is allowed; it stays on the first email's role.
- A skill change ships only after `python -m outreach.draft_lab judge` picks it in at least 8 of 12.
- Remove what version 5 replaced: the fixed "Shape", the "Explore at least five hooks" and "Compete with three drafts"
  steps if they contradict the order above, and "every hook is a fact about the person" (the first line may be a
  company fact).

- [ ] **Step 1: Load the writing-for-agents skill and follow it.**
- [ ] **Step 2: Rewrite** `.claude/skills/cold-email/SKILL.md`. Keep its MCP tool names, refusals and hard rules accurate to the code.
- [ ] **Step 3: Grep** the file for em and en dashes and for any statement that contradicts another section.
- [ ] **Step 4: Run** `uv run python -m unittest discover -s tests -t .`. Expected: OK.
- [ ] **Step 5: Commit** `Skill: version 5, research first, projects first, personal subjects (#211)`

### Task 4: Docs

**Files:**
- Modify: `CLAUDE.md` (the judge and export commands in the live-checkout-only list),
  `docs/superpowers/specs/2026-11-09-handwritten-drafts-design.md` (Parts 4 and 5 as built)
- Test: the full suite stays green.

- [ ] **Step 1: Edit** both files in their existing style.
- [ ] **Step 2: Run** `uv run python -m unittest discover -s tests -t .`. Expected: OK.
- [ ] **Step 3: Commit** `Docs: the draft lab and the version 5 skill (#211)`

## Ship gate (after #210 is merged and this branch is rebased, before this PR is opened)

In the live checkout, with the outreach MCP tools: `python -m outreach.draft_lab export --sample 12 --state approved
--out <a>`; draft the same 12 people with the new skill through `draft_brief` and `check_draft` (nothing saved) into
`<b>`; `python -m outreach.draft_lab judge --a <a> --b <b>`; then the inbox mode. Put both summary lines in the PR. If the
body verdict is FAIL, tune the skill and run again, at most twice, then bring the results to Devanshu.
