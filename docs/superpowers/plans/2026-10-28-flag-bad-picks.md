# Flag a bad pick Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When Devanshu says Not now, he can say the pick was wrong because of the company match or the location, or type why in his own words, and the system acts on the one posting or board that was wrong and puts his flag in front of him.

**Architecture:** Two new Not now reasons (`wrong_company`, `wrong_location`) and an optional note on `other` and `role` ride on the existing `POST /api/plan/{entryId}/pass`. The effect lands on the posting or board, never on the company: a `role` pass ("the position does not match my skills", the UHIN Integration Engineer case) remembers that posting and never offers it as a pick again; a wrong location closes the posting by hand (the crawler never reopens a hand close); a wrong company detaches the board. Each flag is one event plus one `flagged` review item for Needs you. Two root causes behind the Cadence pick are fixed alongside: the doubtful board check ignores big name-only boards, and the location rules do not know Brazilian cities. No schema change.

**Tech Stack:** Python 3.14 (`outreach/`, `unittest`, SQLite), FastAPI models, React and TypeScript (`web/`, vitest).

**Spec:** the conversation of 2026-10-28 (this plan's "Rulings" below), GitHub issue #9 (Not now is a cooldown, never a no-go), `docs/superpowers/plans/2026-10-27-browse-companies.md` (the pick and swap code this extends).

## Rulings (Devanshu, 2026-10-28)

- A false positive is cheap, so the rules stay loose: a posting whose location is unknown stays visible. His flag is the signal, not tighter up-front hiding.
- The flag attaches to the posting (or the board), not the company. Cadence may have US openings, so the company stays eligible.
- `wrong_company` and `wrong_location` are data errors: no 14 day cooldown for the company.
- A role that does not fit his skills (second case, 2026-10-28: UHIN's Integration Engineer asks for HL7, Mirth Connect and EHR work) is a Not now on that posting. It is not a data error, so it raises no Needs-you item. The posting stays visible on the company page but is never a pick again, and the typed note and the posting's title are kept so the later PR can learn which titles and skills he turns down.
- The system proposes rules from his flags later (a separate PR). It never acts on its own and never marks a no-go.
- Claude's calls (check them): `role` (key 4) also opens the note field, with Enter passing straight away if he types nothing; chip keys `9` and `0` for the two new chips, note limit 280 characters, review items raised by `daily_list`, the doubtful check widened to every name-only board over 300 jobs.

## Global Constraints

- Not now is never a no-go: no `no_go` row, no company status change (`outreach/api/services/picks.py` header).
- Every write path takes `idempotencyKey`; the same key again answers the first answer and writes nothing.
- Never call the API write routes from the agent's shell. Tests use the test client, scratch `OUTREACH_DB`.
- No em dashes or en dashes anywhere (copy, docs, commit messages).
- Tests first: red for the right reason, then green. Run: `uv run python -m unittest discover -s tests -t .` and `cd web && pnpm test`.
- One branch, one PR, base `master`. File the issue first and put "Closes #N" in the PR body (ask Devanshu before creating the issue).

## Review Focus

1. A pass with `other` and a blank or whitespace-only note stores no note and still passes (Task 3).
2. The same pass key sent twice (a retry) closes the posting once and raises one review item (Task 3).
3. A pick with no opening (company-first row) passed as `wrong_location` or `wrong_company` still passes, records the flag on the company, and closes nothing (Task 3).
4. A board detached by `wrong_company` is not turned back on by the next `find-boards` or stapply run (Task 3).
5. A location the rules cannot place (`Remote`, an unlisted city) stays `unknown` and visible; no new rule hides it (Task 2).

---

## File Structure

- Modify `outreach/crawl/ingest.py`: `_check_doubtful` (widen the size rule), add `recheck_big_boards`.
- Modify `outreach/rules/config.toml`: non-US city list.
- Modify `outreach/plan/fresh.py`: `PASS_REASONS`, `load_passes`.
- Modify `outreach/api/models/plan.py`, `outreach/api/models/plan_writes.py`: reason literal, `note`.
- Create `outreach/api/services/pick_flags.py`: what a flag does (close posting, detach board, raise item, one event detail).
- Modify `outreach/api/services/picks.py`: `pass_pick` calls `pick_flags`.
- Modify `outreach/review/boardfix.py`: `detach_board`.
- Modify `outreach/api/queries/profile.py`: `PASS_WORDS`.
- Modify `web/openapi.json`, `web/src/api/schema.ts` (generated), `web/src/api/types.ts`, `web/src/api/client.ts`, `web/src/api/http/plan.ts`, fixtures, `web/src/routes/today/picks.ts`, `pass-chips.tsx`, `use-plan-actions.ts`.
- Modify `docs/README.md`.

---

### Task 1: The doubtful board check catches a big name-only board

Why Cadence got through: board 807 (`cadence.wd1`, Cadence Design Systems) was attached to `cadence.care` by `stapply_name`. Workday names no org, and the size rule only applies to companies with LCA evidence, so no item was raised.

**Files:**
- Modify: `outreach/crawl/ingest.py:233-256` (`_check_doubtful`), add `recheck_big_boards` beside it
- Modify: `outreach/crawl/__main__.py` (a `recheck-boards` command, store only, no requests)
- Test: `tests/test_crawl_ingest.py` (`DoubtfulBoardTests`)

**Interfaces:**
- Produces: `recheck_big_boards(conn: sqlite3.Connection, now: str) -> int` raises the same `board-doubtful:<id>` item for every active board with `found_by` in `GUESSED_BOARDS` and more than `DOUBTFUL_JOBS` open postings, returns how many it raised.

- [ ] **Step 1: Write the failing tests** in `DoubtfulBoardTests`:
  - `test_a_name_only_board_of_over_300_jobs_is_doubtful_without_lca_evidence`: `set_found_by("stapply_name")`, `self.read(ok(many_jobs(301)))`, assert one `flagged` item, reason starts with `board match doubtful`, contains `301`.
  - `test_a_board_found_by_hand_is_not_held_to_the_size_rule`: `found_by` `hand`, 400 jobs, assert no item.
  - `test_recheck_big_boards_raises_once_and_a_repeat_is_a_noop`: seed a stapply_name board with 301 open postings and no item; `recheck_big_boards(conn, now)` returns 1, a second call returns 0.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_ingest.DoubtfulBoardTests -v`. Expected: the three new tests FAIL (the existing `Gamma` subtest of `test_first_read_of_a_small_lca_company_with_over_300_jobs_raises_it` also fails once Step 3 lands, because it asserts a no-LCA company with 400 jobs raises nothing: change that subtest to expect an item, since the rule now covers it).
- [ ] **Step 3: Implement.** In `_check_doubtful`, the size clause applies when the company has LCA evidence **or** `board.found_by in GUESSED_BOARDS`; reword the reason to "for a company matched by name only" in that case. Implement `recheck_big_boards` with one grouped count over `posting` joined to `board`, reusing `_raise_doubtful` (its `ON CONFLICT DO NOTHING` makes a repeat a no-op; count rows actually inserted). Add the CLI command printing the count.
- [ ] **Step 4: Run** the same command plus `uv run python -m unittest tests.test_crawl_ingest -v`. Expected: PASS.
- [ ] **Step 5: Commit** `Crawl: a big board matched by name only is doubtful (Cadence)`.

---

### Task 2: The rules know Brazilian and nearby non-US cities

**Files:**
- Modify: `outreach/rules/config.toml:228-237` (`non_us_cities`), and `shared_city_names` for any name that is also a US place
- Test: `tests/test_rules_location.py` (use the file the existing `us_status` tests live in; `grep -l us_status tests`)

**Interfaces:** none new. `us_status(location, country, cfg, title=None) -> "yes"|"no"|"unknown"` is unchanged.

- [ ] **Step 1: Write the failing tests:**
  - `test_brazilian_cities_read_no`: for `BELO HORIZONTE`, `Sao Paulo`, `São Paulo, Brazil`, `Rio de Janeiro`, `Curitiba`, `Porto Alegre`, `Campinas`, `Florianopolis`, `Buenos Aires`, `Montevideo`, `Mexico City`: `us_status(loc, None, cfg) == "no"`.
  - `test_us_places_next_to_the_new_cities_still_read_yes`: `Lima, OH` stays `yes`, `Remote - US or Sao Paulo` stays `yes`.
  - `test_a_place_the_lists_do_not_know_stays_unknown`: `Remote` and `Wakanda City` read `unknown` (Review Focus 5: no new hiding rule).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_rules_location -v` (adjust module name). Expected: the first test FAILS on `BELO HORIZONTE` (`unknown`).
- [ ] **Step 3: Add** those city names to `non_us_cities` (lower case, both spellings with and without accents where the file already does so). Do not add `lima` there: a state code does not beat a non-US city, so `Lima, OH` would break; if it is not already in `shared_city_names`, add it there.
- [ ] **Step 4: Run** the test file and `uv run python -m unittest tests.test_rules -v`. Expected: PASS. Ruleset version changes with this config, so a deploy runs `uv run python -m outreach.rules.rebuild` (Task 6).
- [ ] **Step 5: Commit** `Rules: Brazilian and nearby non-US cities read as outside the US`.

---

### Task 3: Not now takes wrong company, wrong location and a note

**Files:**
- Modify: `outreach/api/models/plan.py:209` (`PassReason`), `outreach/api/models/plan_writes.py:80-88` (`PassPickBody`)
- Modify: `outreach/plan/fresh.py:45` (`PASS_REASONS`), `outreach/plan/fresh.py:278-290` (`load_passes`)
- Modify: `outreach/api/queries/profile.py:58` (`PASS_WORDS`)
- Create: `outreach/api/services/pick_flags.py`
- Modify: `outreach/review/boardfix.py` (`detach_board`)
- Modify: `outreach/api/services/picks.py` (`pass_pick`, `detail`)
- Test: `tests/test_api_plan_picks.py` (follow its fixtures), `tests/test_plan_picks_only.py` for `load_passes`

**Interfaces:**
- Produces: `PassPickBody.note: str | None = Field(default=None, max_length=280)`; reasons `wrong_company`, `wrong_location` added to the `Literal` in both models and to `PASS_REASONS` (order: the eight existing, then the two new).
- Produces: `detach_board(conn, board_id: int, *, now: str, actor: str = "devanshu") -> int` in `outreach/review/boardfix.py`: sets `board.active = 0`, closes the board's open postings with `close_reason = 'hand'`, appends a `board_detached` event, returns how many postings it closed.
- Produces: `apply_flag(conn, *, reason: str, note: str | None, company_id: int, opening_posting_id: int | None, now: str) -> dict` in `pick_flags.py`: does the effect for `wrong_location` (close that posting by hand), `wrong_company` (`detach_board` on the posting's board), and for all three flag kinds (`wrong_*`, and `other` with a non-blank note) raises one `flagged` review item (`raised_by='daily_list'`, `evidence_key=f"pick-flag:{reason}:{posting_id or company_id}"`, reason text starting `You flagged`, with the title, the location, and the note). Returns `{"flag": reason, "note": cleaned_note, "posting_id": ..., "board_id": ..., "closed": n}` for `pass_pick` to merge into the `pick_passed` event detail.
- Produces: `load_passed_postings(conn: sqlite3.Connection) -> frozenset[int]` in `outreach/plan/fresh.py`: the `posting_id` of every `pick_passed` event whose `detail.reason` is `role`; `_build` skips those postings, so a role he turned down is never an opening again (not for the cooldown's end, not for the fresh lane).
- Produces: every `pick_passed` event detail now also carries `posting_id` (the opening the pick named, or null) and `title`, for all reasons, so the learning PR has them.
- Consumes: `row.queue.opening.posting_id` (`QueueOpening`), `append_event` from `outreach.store`.

- [ ] **Step 1: Write the failing tests** (names and assertions):
  - `test_wrong_location_closes_that_posting_by_hand_and_keeps_the_company_eligible`: pass with `reason="wrong_location"`; posting row has `closed_at` set and `close_reason == 'hand'`; the company's other open postings are untouched; no `no_go` row; `load_passes(...)` does not list the company.
  - `test_wrong_company_detaches_the_board`: `board.active == 0`, every open posting of the board is closed by hand; a `board_detached` event exists.
  - `test_a_flag_raises_one_flagged_review_item_with_the_note`: `review_item` has one open `flagged` row whose reason starts `You flagged` and contains the note; `raised_by == 'daily_list'`.
  - `test_other_keeps_a_trimmed_note_and_a_blank_note_is_none`: `note="  Brazil only  "` stored as `Brazil only` in the event detail; `note="   "` stored as `None` and raises no review item; a 281 character note is a 422.
  - `test_the_same_key_twice_flags_once` (Review Focus 2): second call returns the first answer; one review item, one closed posting.
  - `test_a_pick_with_no_opening_passes_and_closes_nothing` (Review Focus 3): `wrong_location` on a row with `queue.opening is None`; the pass lands, the review item names the company, no posting changes.
  - `test_role_not_for_me_hides_that_posting_from_every_later_pick_but_not_the_company`: pass with `reason="role"` and `note="HL7 and Mirth, not my skills"`; after the cooldown the company can be picked again, but its `load_openings` result no longer contains that posting; the posting is still open (`closed_at IS NULL`) and no review item is raised.
  - `test_an_ordinary_reason_still_gives_the_14_day_cooldown`: `reason="visa"` still appears in `load_passes`.
  - `test_a_detached_board_stays_off_when_the_board_is_found_again` (Review Focus 4): after `detach_board`, running `add_board` with the same ats and token (what stapply and `find-boards` call) leaves `active == 0`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_api_plan_picks tests.test_plan_picks_only -v`. Expected: FAIL (422 on the new reasons, `detach_board` and `pick_flags` missing).
- [ ] **Step 3: Implement** the interfaces above. `pass_pick` calls `apply_flag` after `bump` and before `append_event`, when `body.reason` is `wrong_*` or `other` with a non-blank note, and merges its dict into `detail`; for `role` it only keeps the note, `posting_id` and `title` in `detail` (no effect, no item). `_build` in `fresh.py` takes `load_passed_postings` once per call. `load_passes` skips events whose `detail.reason` is `wrong_company` or `wrong_location` (`json_extract(detail, '$.reason')`). `PASS_WORDS` gains `wrong company` and `wrong location`. If `add_board` re-activates a detached board, change it to leave `active` alone for a board that already exists.
- [ ] **Step 4: Regenerate the contract:** `uv run python -m outreach.api.openapi --write && cd web && pnpm gen:api`. Run `uv run python -m unittest tests.test_api_openapi tests.test_api_plan_picks tests.test_plan_picks_only -v`. Expected: PASS.
- [ ] **Step 5: Commit** `Not now: wrong company, wrong location and a note act on the posting, not the company`.

---

### Task 4: The Not now panel takes the new reasons and a note

**Files:**
- Modify: `web/src/routes/today/picks.ts:4-13` (`PASS_REASONS`), `web/src/api/types.ts:375` (`PassReason`)
- Modify: `web/src/routes/today/pass-chips.tsx`
- Modify: `web/src/api/client.ts:80-82`, `web/src/api/http/plan.ts:27-28`, `web/src/api/fixture/client.ts` (pass the `note`)
- Modify: `web/src/routes/today/use-plan-actions.ts:468-520` (`passPick(row, reason, note?)`)
- Test: `web/src/routes/today/plan.test.tsx` (the Not now tests live there), `web/src/api/fixture/client.write.test.ts`

**Interfaces:**
- Consumes: `PassPickBody.note` and the two reasons from Task 3 (`web/src/api/schema.ts` regenerated).
- Produces: `PASS_REASONS` gains `{ reason: 'wrong_company', label: 'Wrong company', key: '9' }` and `{ reason: 'wrong_location', label: 'Wrong location', key: '0' }`; `PassChips` prop `onPass: (reason: PassReason | null, note?: string) => void`; `passPick(row: PlanRow, reason: PassReason | null, note?: string): boolean`.

- [ ] **Step 1: Write the failing tests:**
  - `Not now offers Wrong company and Wrong location`: render the panel, both chips are in the group, keys `9` and `0`.
  - `pressing 9 passes at once with wrong_company`, `pressing 0 passes at once with wrong_location` (the client mock receives `{ reason, note: undefined }`).
  - `Role not for me and Other open a note field instead of passing`: click Other (key `8`): a text input labelled `Why` appears, nothing is sent; Enter with `Brazil only` sends `reason: 'other', note: 'Brazil only'`; Enter on an empty field sends `reason: 'other'` with no note; Esc goes back to the chips without passing. The same three checks for Role not for me (key `4`).
  - `the panel copy says what the two new chips do`: text contains "Wrong company and Wrong location fix the data and are not a Not now."
- [ ] **Step 2: Run** `cd web && pnpm test -- plan.test.tsx`. Expected: FAIL (no such chips).
- [ ] **Step 3: Implement.** In `PassChips`, local state `askingNote: boolean`; while true the hotkey bindings for 1 to 0 and Enter/Esc are replaced by the input's own Enter (pass with the trimmed note) and Esc (back to the chips). Add a line to the panel copy. Thread `note` through `passPick` into `client.passPick(entryId, { reason, note, idempotencyKey }, revision, o)`; the idempotency key stays `plan:<entryId>:pass`.
- [ ] **Step 4: Run** `cd web && pnpm test && pnpm typecheck && pnpm lint`. Expected: PASS. Run `WEB_PORT=$CONDUCTOR_PORT pnpm e2e` for `today-queue.spec.ts` and update the chip count there (8 to 10).
- [ ] **Step 5: Commit** `Today: Wrong company, Wrong location and a typed reason on Not now`.

---

### Task 5: Docs and the deploy notes

**Files:**
- Modify: `docs/README.md` (one row for this plan in the Current table)
- Modify: this plan's "Deploy" section if a step changed while building

- [ ] **Step 1:** Add the README row: `superpowers/plans/2026-10-28-flag-bad-picks.md | #N: Not now takes Wrong company, Wrong location and a note...` (one sentence each for the effect and the two root-cause fixes).
- [ ] **Step 2: Run** the full suites: `uv run python -m unittest discover -s tests -t .` and `cd web && pnpm test && pnpm check:api`. Expected: PASS.
- [ ] **Step 3: Commit** `Docs: map the flag-a-bad-pick plan`.

## Deploy (Devanshu merges; run in the live checkout)

1. `git pull --ff-only https://github.com/DevanshuNEU/sept-23-companies.git master`, `uv sync`, run the tests.
2. `uv run python -m outreach.rules.rebuild` (the ruleset changed: new non-US cities). Read the flip report.
3. `uv run python -m outreach.crawl recheck-boards` once: raises the doubtful item for every big name-only board already stored (Cadence's board 807 among them). Open Needs you and settle each: use the board fix to paste the right board, or mark ok.
4. `cd web && pnpm install --frozen-lockfile && pnpm build`, then `launchctl kickstart -k gui/$(id -u)/com.devanshu.outreach-api`.
5. No schema change, so no `outreach.install`.

## Next PR (not in this plan)

Reason counts and proposed rules: group the role passes by posting title and note (UHIN's HL7 and Mirth work is the first example) to propose title or skill words to turn down, and group the `pick_passed` events by `reason` and the flagged locations, show the top repeats, and propose a rule (for example a city to add to `non_us_cities`) that he confirms. The ups count too (Devanshu, 2026-10-28, on Tavern Research's Engineer I: early career, 0 to 2 years, Python and LLM work, E-Verify enrolled, Chicago): logging a request or an application is already a positive signal, and an optional "why this one" note on those would give the learner both sides. Proposed, not yet agreed. Waits until there are flags to learn from.
