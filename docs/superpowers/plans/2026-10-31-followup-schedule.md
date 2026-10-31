# Follow-up Schedule Implementation Plan (#141)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An email thread gets up to 4 follow-ups, at 3, 7, 14 and 21 business days after its first email, in order, each approved by Devanshu; a follow-up nobody acts on lapses; the thread then closes as no response.

**Architecture:** The schedule stays in `outreach/sending/config.toml`. A migration widens `message.seq` to 0..4. A new tick step `sync_followups` makes every open thread's `planned` rows match the config (planned rows hold no text, so re-dating them is safe) and never revives a stopped or lapsed one. `lapse_followups` also lapses drafted rows. The queue gets one wait reason, `earlier_followup`, so follow-ups go out in order. The nightly drafter is #142 and is not in this plan.

**Tech Stack:** Python 3.14 standard library, SQLite (STRICT tables), FastAPI models, React + TypeScript (openapi-typescript).

**Spec:** #9, "Follow-up sequence ruling (2026-10-31)"; issue #141's "Done when".

## Global Constraints

- Follow-ups: `[3, 7, 14, 21]` business days after the first email's New York date. At most 4 (`message.seq` 0 to 4).
- A follow-up nobody approves or skips lapses `followup_lapse_business_days` (5) business days after its due date. An `approved` one never lapses.
- After the last sent message and nothing waiting, the touch closes `no_reply` after `close_after_business_days` (7). Unchanged code.
- Per company: at most 3 a day, 60 minutes apart. Unchanged.
- Migrations 0001 to 0004 are never edited. Forward only.
- No em or en dashes in any copy, comment or commit message.
- Tests use scratch stores only (`tests/storetest.fresh_store`); never the live store.

## Review Focus

1. A thread whose seq 1 was stopped (reply elsewhere at the company, sweep) or lapsed must not get seq 1 back from `sync_followups`. Test in Task 3.
2. A touch that is not `open` (replied, bounced, closed) gets no new rows from `sync_followups`. Test in Task 3.
3. A drafted or approved seq 1 keeps its `due_on`; only `planned` rows are re-dated. Test in Task 3.
4. Seq 2 approved while seq 1 is still `approved` but blocked by the per-company limit waits as `earlier_followup`, not `ready`. Test in Task 2.
5. The migration keeps message ids, all 8 `message_*` triggers and 5 indexes; a `sending` row stays protected by its triggers afterwards. Test in Task 1.

---

### Task 1: Store and config allow 4 follow-ups

**Files:**
- Create: `outreach/store/migrations/0005_four_followups.sql`
- Modify: `outreach/sending/config.py:13` (`MAX_FOLLOWUPS = 4`), `outreach/sending/config.toml` (`followup_business_days = [3, 7, 14, 21]`, comment cites the 2026-10-31 ruling)
- Modify: `outreach/api/models/outbox.py` (`Seq = Literal[0, 1, 2, 3, 4]`, `FollowupSeq = Literal[1, 2, 3, 4]`), `web/src/api/types.ts` (every `0 | 1 | 2 | 3` seq to include 4), regenerate `web/openapi.json` and `web/src/api/schema.ts`
- Modify: `web/src/routes/outbox/outbox-list.tsx:44`, `outbox-detail.tsx:114,139` ("Follow-up N of 4" from one exported constant `FOLLOWUP_COUNT = 4` in `outbox-list.tsx`), `outbox-list.test.tsx:141`
- Modify: `.claude/skills/cold-email/SKILL.md` Follow-ups section (4 at 3, 7, 14, 21; each adds one new thing; the 4th is a polite close)
- Test: `tests/test_store_migrate.py`, `tests/test_sending_config.py` (or wherever config validation is tested)

**Interfaces:**
- Produces: `message.seq` 0..4; `MAX_FOLLOWUPS == 4`; `load_config().followup_business_days == (3, 7, 14, 21)`.

- [ ] **Step 1: Failing tests.** `test_migration_0005_keeps_messages_and_allows_seq_4`: migrate a store to 4, seed a touch with messages seq 0 (sent) and seq 1 (planned), migrate to latest; rows identical by `SELECT *`; the set of `sqlite_schema` trigger and index names on `message` is unchanged; inserting a seq 4 planned row works; seq 5 raises `sqlite3.IntegrityError`. Config: the shipped file loads `(3, 7, 14, 21)`; five entries raise `ValueError` naming "at most 4".
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_store_migrate` and the config test. Expected: FAIL (seq 4 refused, config still `(7,)`).
- [ ] **Step 3: Write 0005** the way 0004 rebuilds `import_record`: `message_new` copied from 0001 with `CHECK (seq BETWEEN 0 AND 4)` and the comment "1 to 4 the follow-ups (#9, 2026-10-31: 3, 7, 14 and 21 business days)", `INSERT INTO message_new SELECT * FROM message`, `DROP TABLE message`, rename, then every `message_*` index and trigger exactly as the live schema has them (take the text from `sqlite_schema` of a store migrated to 4, not from memory). Nothing references `message` by foreign key.
- [ ] **Step 4: Config, API literals, web types and labels, skill text.** Regenerate with `uv run python -m outreach.api.openapi --write && cd web && pnpm gen:api`.
- [ ] **Step 5: Run** the full Python suite and `cd web && pnpm test && pnpm typecheck && pnpm check:api`. Existing follow-up tests that assumed one follow-up get `dataclasses.replace(CFG, followup_business_days=(7,))` where the test is about something else; tests about the schedule assert the new one.
- [ ] **Step 6: Commit** "Store and config allow 4 follow-ups at 3, 7, 14 and 21 business days (#141)".

### Task 2: Follow-ups go out in order

**Files:**
- Modify: `outreach/sending/queue.py` (`REASONS`, `REASON_TEXT`, `verdicts`, `_decide`), `outreach/api/models/outbox.py` (`ScheduleReason`), `web/src/api/types.ts`, regenerated API files
- Test: `tests/test_sending_queue.py`

**Interfaces:**
- Produces: reason `"earlier_followup"`, text "An earlier follow-up in this thread has not gone out yet. This one goes after it." Not in `TIME_REASONS`.

- [ ] **Step 1: Failing tests** in a new `FollowupOrderTest`: seq 1 and seq 2 both approved and due, seq 1 waits on `per_company_limit` -> seq 2's verdict is `earlier_followup`; seq 1 `skipped` -> seq 2 is `ready`; seq 1 `sent` -> seq 2 is `ready`; seq 1 `planned` (never drafted) -> seq 2 waits.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sending_queue`. Expected: FAIL (seq 2 is `ready`).
- [ ] **Step 3: Implement.** In `verdicts`, one query for the approved messages that have an earlier seq on the same touch in `planned, drafted, approved, sending, send_unknown, failed`; `_decide` returns `earlier_followup` right after the `followup_not_due` check. A message blocked this way does not count in `live_others` for the recruiter rule.
- [ ] **Step 4: Run** Python suite and `pnpm check:api`. Expected: PASS (the API test that pins `ScheduleReason` to `REASONS` passes).
- [ ] **Step 5: Commit** "Follow-ups wait for the earlier one in their thread (#141)".

### Task 3: Every open thread follows the schedule

**Files:**
- Modify: `outreach/sending/followups.py` (new `sync_followups`; `lapse_followups` also lapses `drafted`), `outreach/sending/tick.py:502` (call `sync_followups` before `lapse_followups`, count `"planned"`), `outreach/sending/status.py` if it lists tick counts
- Test: `tests/test_sending_followups.py`, `tests/test_sending_tick.py` (count key)

**Interfaces:**
- Consumes: `plan_followups(conn, touch_id, first_sent_on, cfg, *, now) -> int` (unchanged), `stop_planned` events (`followup_lapsed`, `skipped_by_sender`, detail `seq`).
- Produces: `sync_followups(conn, cfg, *, now: str) -> int` returns rows added plus rows re-dated. Re-dating appends a touch event `followup_replanned`, detail `{seq, from, to}`, key `followup-replanned:{touch_id}:{seq}:{to}`.

- [ ] **Step 1: Failing tests** (`SyncTest`): an open touch sent Monday 2026-11-02 with seq 1 planned at 11-11 -> after sync, planned is `[(1, "2026-11-05"), (2, "2026-11-11"), (3, "2026-11-20"), (4, "2026-12-01")]`; a second sync returns 0; a touch with a `followup_lapsed` event for seq 1 gets seqs 2 to 4 only; same for `skipped_by_sender`; a `replied`, `bounced` or `closed` touch gets nothing; a drafted seq 1 keeps its `due_on` and seqs 2 to 4 are added. Lapse: a drafted follow-up lapses 5 business days after due (state `skipped`, message event `followup_lapsed` with `{seq, due_on, reason: "lapsed"}`); an approved one never lapses. Replace `test_a_drafted_follow_up_never_lapses`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sending_followups`. Expected: FAIL (`sync_followups` missing; drafted row stays drafted).
- [ ] **Step 3: Implement.** `sync_followups` reads open touches with `first_sent_at` and `gmail_thread_id`; first date is `cal.ny_date(first_sent_at)`; re-dates `planned` rows whose `due_on` differs; inserts a missing seq through `plan_followups` unless the touch has a `followup_lapsed` or `skipped_by_sender` touch event with that seq. Lapse of a drafted row: `UPDATE ... state = 'skipped', revision + 1` guarded on `state = 'drafted'`, message event `followup_lapsed`, key `followup-lapsed:{message_id}:{revision + 1}`.
- [ ] **Step 4: Run** the full Python suite. Expected: PASS.
- [ ] **Step 5: Commit** "Open threads follow the 4-follow-up schedule; unanswered drafts lapse (#141)".

### Task 4: Ship

- [ ] Full suite, web checks, `git push`, PR to `master` closing #141; `gh pr view --json baseRefName,mergeStateStatus` shows `master` and `CLEAN`.
- [ ] Deploy after his merge (CLAUDE.md "Deploy"): pull, `uv sync`, tests, `uv run python -m outreach.install` (schema changed), web build, kickstart the API. The sender picks up the new config on its next run; the first tick re-plans the 23 open threads.
