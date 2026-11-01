# Outbox scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every approved email in the Outbox shows when it will really send (an estimate), and the Approved group is split by send day.

**Architecture:** A pure projection walks the approved queue in order on a virtual clock and asks the sender's own `_decide` rule at each step, so the display cannot drift from what sends. The queue's DB reads happen once (`prepare`); the walk is memory only. The API puts the result on each row's `schedule`; the web groups by it.

**Tech Stack:** Python 3.14 (`outreach/sending`, `outreach/api`), React + Vite (`web/`), unittest, vitest.

**Spec:** GitHub issue #145 (this plan's issue), rules from #9. Decisions of 2026-11-01 from Devanshu: estimated time on each row, Approved grouped by send day, later days use today's limit held flat, one PR.

## Global Constraints

- Closes #145. One PR from `outbox-scheduler`, base `master`.
- Read only: nothing in the projection writes or opens a transaction. It never changes `verdicts` results.
- #9's numbers stay: per company 3 a day, 60 minutes apart (`per_company_daily`, `per_company_gap_minutes`); spacing is the mean of `gap_minutes` (4 to 9, so 6.5); window 08:30 to 16:30 recipient time on weekdays; ramp limit not raised for future days.
- Times shown in ET with the word "about" (an estimate). No dashes of any kind in copy or code comments.
- A row with no possible send keeps the queue's own sentence (`REASON_TEXT`). `ScheduleReason` gains no value.
- Web copy follows `~/.claude/skills/craft/words.md`: sentence case, no codes on screen.
- The old ready/verdict machinery (`queue.verdicts`, `status.queue_schedule`) stays and keeps its tests; the tick still uses `verdicts`.

## Review Focus

- 10 approved emails at one company: 3 today, 3 on each next business day, one left; none within 60 minutes of another at that company.
- Friday afternoon after the window closes, or a Saturday: the first projection is Monday at the window start.
- The sender is paused (a hard block or by him): rows still project, from now, and say it is after he resumes.
- A follow-up due on a later day projects no earlier than its due day's window start.
- A recruiter whose company has other queued emails projects after the last of them; a company paused or held, or a person with no address, gets no projection.
- Recipient in another time zone (Pacific): projected inside their own 08:30 to 16:30.
- Nothing approved: no work, no error. More approved emails than 30 business days can hold: the rest get no projection.
- The first projected email equals the sender's verdict for it right now (ready now, or the same wait end).

---

### Task 1: Split `verdicts` into `prepare` and `decide_all`

**Files:**
- Modify: `outreach/sending/queue.py` (the body of `verdicts`, lines 169 to 215)
- Test: `tests/test_sending_queue.py` (the existing tests are the guard; add one)

**Interfaces:**
- Produces: `@dataclass(frozen=True) class Prepared` with fields `queue`, `stops`, `no_thread`, `behind`, `not_before`, `companies`, `held`, `sends: dict[int, list[tuple[datetime, bool]]]` (the bool is a first email, which is all the per-company limit counts, #9 2026-10-31), `sent_today`, `limit`, `next_send`.
- Produces: `def prepare(conn, now: datetime, cfg: SendConfig, *, day: DayState | None) -> Prepared`.
- Produces: `def decide_all(p: Prepared, now: datetime, cfg: SendConfig, *, gate: str | None, pending: list[Queued], sends: dict[int, list[tuple[datetime, bool]]], sent_by_day: dict[date, int], next_send: datetime | None, limit: int) -> list[Verdict]` (one verdict per `pending`, in order; `sent_today` for a day comes from `sent_by_day.get(ny_date(now), 0)`; `live_others` counts only `pending`).
- Consumes: `verdicts(conn, now, cfg, *, day, gate)` keeps its signature and returns what it returns today.

- [ ] **Step 1: Write the failing test** `test_decide_all_equals_verdicts_for_the_whole_queue` in `tests/test_sending_queue.py`: seed 6 approved messages across two companies (reuse the file's seed helpers), call `queue.verdicts(...)` and `queue.decide_all(queue.prepare(...), now, cfg, gate=None, pending=p.queue, sends=p.sends, sent_by_day={today: p.sent_today}, next_send=p.next_send, limit=p.limit)`, assert the two lists are equal.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sending_queue -k decide_all` - expect AttributeError on `prepare`.
- [ ] **Step 3: Implement** by moving the existing reads of `verdicts` into `prepare` and the loop into `decide_all`; `verdicts` becomes `prepare` plus `decide_all`. The tick's behavior must not change. Replace the single `tomorrow`, `day_begins` and `today` with values computed from the `now` that `decide_all` receives.
- [ ] **Step 4: Run** `uv run python -m unittest tests.test_sending_queue tests.test_sending_status tests.test_sending_tick` - all pass.
- [ ] **Step 5: Commit** `git commit -m "Queue: split verdicts into prepare and decide_all (#145)"`.

### Task 2: The projection

**Files:**
- Create: `outreach/sending/projection.py`
- Test: `tests/test_sending_projection.py`

**Interfaces:**
- Consumes: `queue.prepare`, `queue.decide_all`, `queue.TIME_REASONS`, `queue.pick`'s rule (first `ready` in order).
- Produces: `MAX_DAYS = 30`; `def project(p: queue.Prepared, now: datetime, cfg: SendConfig) -> dict[int, datetime]` (message id to the moment it is expected to send; a message that never becomes ready within `MAX_DAYS` days is absent). The walk: pick the first `ready` at the virtual time, record it, add it to that company's sends and the day's count, set `next_send` to the time plus the mean of `cfg.gap_minutes`; with nothing ready, jump to the smallest `earliest_at` that is later than the virtual time; stop when none or past `MAX_DAYS`. Later days use `limit = p.limit` for every day.

- [ ] **Step 1: Write the failing tests** (each seeds approved messages with the helpers `tests/test_sending_queue.py` uses; `now` fixed to a Friday 13:06 ET):
  - `test_ten_at_one_company_spread_three_a_day`: 10 approved at one company; assert the projected days are `[Fri x3, Mon x3, Tue x3, Wed x1]` and, within a day, consecutive times at that company differ by at least 60 minutes.
  - `test_after_the_window_the_first_goes_on_the_next_business_day_at_open`: now Friday 17:00 ET; first projection is Monday 08:30 ET or later.
  - `test_spacing_separates_different_companies_by_the_mean_gap`: 3 companies, one each; times are `now`, `now + 6.5 min`, `now + 13 min` (or the next ready moment).
  - `test_daily_limit_holds_flat_for_later_days`: limit 4, 9 approved at 9 companies; per New York day counts are `[4, 4, 1]` counting what was already sent today.
  - `test_a_pacific_recipient_projects_inside_their_own_window`: tz `America/Los_Angeles`; the projected moment, converted to LA, is between 08:30 and 16:30.
  - `test_a_follow_up_waits_for_its_due_day`: `due_on` next Wednesday; projected on or after Wednesday's window start.
  - `test_recruiter_goes_after_the_others_at_the_company`: a recruiter and two engineers at one company, recruiter approved first; recruiter's time is after both engineers'.
  - `test_unsendable_messages_are_not_projected`: a paused company, a held company, a person with no address, a stopped one (company closed): all absent from the result.
  - `test_first_projection_matches_the_sender_verdict_now`: `project(...)` for the first `ready` verdict gives `now`; for a first message waiting on `spacing`, gives that verdict's `earliest_at`.
  - `test_nothing_approved_projects_nothing`: empty dict.
  - `test_more_than_the_horizon_leaves_the_rest_unprojected`: 400 messages at one company, only those within 30 business days are projected.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sending_projection` - expect ImportError.
- [ ] **Step 3: Implement `project`** in `outreach/sending/projection.py` per the Interfaces walk. Keep it pure; copy `p.sends` before adding to it.
- [ ] **Step 4: Run** the same command - all pass; then `uv run python -m unittest tests.test_sending_queue tests.test_sending_status` still pass.
- [ ] **Step 5: Commit** `git commit -m "Sending: project when each approved email goes out (#145)"`.

### Task 3: The status function, the wire and the sort

**Files:**
- Modify: `outreach/sending/status.py` (add `queue_projection`; `_gate` gets `ignore_pause`)
- Modify: `outreach/api/models/outbox.py` (`Schedule`)
- Modify: `outreach/api/queries/outbox.py` (`_Outbox.__init__`, `_schedule`, `_at`, `items` sort key, `schedule_text`)
- Modify: `web/openapi.json`, `web/src/api/schema.ts` (regenerate), `web/src/api/types.ts`
- Test: `tests/test_sending_status.py`, `tests/test_api_outbox.py`

**Interfaces:**
- Produces: `status.queue_projection(conn, now, cfg, *, old_sender_loaded: bool) -> tuple[dict[int, datetime], bool]` (the projection, and whether the sender is paused). Returns `({}, paused)` when the gate is `signed_out`, `old_sender_running` or `message_id_unproven`, or while the first email goes alone (`proof.FIRST_SEND`); ignores `sender_paused` and sets the flag.
- Produces: `Schedule.projected_at: str | None` (ISO, UTC, `projectedAt` on the wire) and `Schedule.projected_day: str | None` (New York date, `projectedDay`). `Schedule.text` becomes the projection's sentence when there is one: `Sends about 14:10 ET` (today), `Sends tomorrow about 9:05 ET`, `Sends Mon Nov 2, about 9:05 ET`; while paused the same text ends `once resumed`. `Schedule.projected_clock` (`about 14:10 ET`) is the time part, so the row's right column and the text never disagree. Times round to the nearest 5 minutes. Without a projection the text is the queue's, as now.
- Produces: approved rows sort by `projected_at` (else `earliest_at`, else last), queue order breaking ties; `OutboxItem.at` for an approved row is `projected_at` when set.

- [ ] **Step 1: Write the failing tests:**
  - `tests/test_sending_status.py::test_projection_runs_while_paused_and_flags_it`, `::test_projection_is_empty_when_signed_out`.
  - `tests/test_api_outbox.py::test_approved_rows_carry_a_projected_time_and_day`, `::test_ten_at_one_company_text_reads_three_days`, `::test_paused_rows_say_after_you_resume`, `::test_approved_rows_sort_by_projected_time`, `::test_a_row_with_no_projection_keeps_the_queue_sentence`.
- [ ] **Step 2: Run** them - expect failures on the missing field and function.
- [ ] **Step 3: Implement** `queue_projection`, the `Schedule` fields, `schedule_text` (add a `projection_text(at, now, cfg, paused)` beside it), and the sort; regenerate `web/openapi.json` with `uv run python -m outreach.api.openapi` and `schema.ts` with `cd web && pnpm gen:api`; add the two fields to `Schedule` in `web/src/api/types.ts`.
- [ ] **Step 4: Run** `uv run python -m unittest tests.test_sending_status tests.test_api_outbox tests.test_api_outbox_writes` and `uv run python -m outreach.api.openapi --check` - pass.
- [ ] **Step 5: Commit** `git commit -m "Outbox: each approved row carries its projected send time (#145)"`.

### Task 4: The Outbox groups Approved by send day

**Files:**
- Modify: `web/src/routes/outbox/outbox-list.tsx` (day parts, row time, group note)
- Modify: `web/src/lib/format.ts` (an ET day label and time helper if none fits)
- Modify: `web/src/api/fixture/data.ts` and `web/src/api/fixture/client.ts` (fixtures carry `projectedAt` and `projectedDay`)
- Test: `web/src/routes/outbox/outbox-list.test.tsx`, `web/e2e` (the existing outbox spec)

**Interfaces:**
- Consumes: `OutboxItem.schedule.projectedAt`, `.projectedDay`, `.text`.
- Produces: inside the Approved group, a part head per send day, in date order, then a last part "Not scheduled". Head copy: `Today`, `Tomorrow`, else `Mon Nov 2`, with the row count right after it (tabular numbers, same look as the Blocked subgroup in Drafts to review). The group head note becomes `over 4 days` (`over 1 day`; nothing scheduled gives no note). A row's right-hand time is `about 14:10 ET` (the day is in the head); a "Not scheduled" row shows no time. The selected row keeps its place in its part (the existing held-slot rule).

- [ ] **Step 1: Write the failing tests** in `outbox-list.test.tsx`: `groups approved rows by send day with a count`, `the last part is Not scheduled for rows with no projection`, `each row shows its time in ET and not the day`, `the group note counts the days`, `a part head is not a row for j and k`.
- [ ] **Step 2: Run** `cd web && pnpm vitest run src/routes/outbox/outbox-list.test.tsx` - the new tests fail.
- [ ] **Step 3: Implement** the parts in the `approved` bucket (a pure `byDay(rows)` helper), the row time, the note; update the fixtures so the dev app shows three days. Check light and dark, narrow width, and the empty and one-day states with the craft gate.
- [ ] **Step 4: Run** `cd web && pnpm vitest run && pnpm lint && pnpm tsc -b && pnpm check:api` and `WEB_PORT=$CONDUCTOR_PORT pnpm e2e -g outbox` - pass.
- [ ] **Step 5: Commit** `git commit -m "Outbox: the Approved group splits by send day (#145)"`.

### Task 5: Docs, full run, PR

**Files:**
- Modify: `docs/README.md` (one row for this plan)

- [ ] **Step 1: Add the row** to `docs/README.md` under Current: this plan, #145.
- [ ] **Step 2: Run** `uv run python -m unittest discover -s tests -t .` - OK.
- [ ] **Step 3: Push** `git push https://github.com/DevanshuNEU/sept-23-companies.git outbox-scheduler`, open the PR against `master` with `Closes #145`, check `mergeStateStatus` is `CLEAN`.
- [ ] **Step 4: Deploy after his merge:** pull in the live checkout, `uv sync`, tests, `cd web && pnpm install --frozen-lockfile && pnpm build`, then `launchctl kickstart -k gui/$(id -u)/com.devanshu.outreach-api`.
