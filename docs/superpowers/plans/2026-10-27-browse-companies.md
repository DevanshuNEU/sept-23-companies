# Browse companies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Browse companies" drawer on Today (key F) lists every company the plan could pick, in stored rank order and paged, and lets Devanshu swap one in for any of today's picks.

**Architecture:** One read (`GET /api/plan/browse`) built from the plan's own prospects and the bench's compact row shape, so a browse row is a pick row. One write (`POST /api/plan/{entryId}/swap`) that follows the same eligibility function as a pick and replaces the pick's row. The page has one new drawer next to `bench-drawer.tsx` and one new action in `use-plan-actions.ts`.

**Tech Stack:** FastAPI and SQLite (`outreach/api`, `outreach/plan`), React, TanStack Query, vitest, Playwright (`web/`).

**Spec:** GitHub issue #120 (Done when lines) and #9 (the decision record). Design context: `docs/design/README.md` section 16.1b (More options) and 16.6 (keys).

## Global Constraints

- Company first (#9, 2026-10-26): Today is a queue of companies. Nothing here hides a company or changes a hide rule. It only lets him choose.
- Never listed: a no-go company, a company on today's list (any state), a company in flight (`waiting` or `engaged`). A company held back (Not now, application) is listed with its reason and cannot be swapped in.
- A swap uses no room in the 7-day limit and is not a Not now: no `pick_passed` event, no cooldown.
- Claude never calls the Knock API's write routes. Tests use the test client on scratch settings (`tests/apitest.py`, `OUTREACH_DB`). Ports 3001, 3002, 3011, 3013 and 3100 are never used. E2E runs as `WEB_PORT=$CONDUCTOR_PORT pnpm e2e`.
- No em dashes or en dashes in code comments, UI copy, docs, commit messages or the PR body.
- One branch, one PR to `master`, body says `Closes #120`. No merge, no deploy.

## Decisions

**Browse pool.** Every company in the laned, non-hidden ranked list (`ranked_list(laned_only=True)`), in stored order (score, then company id). `plan.scan_limit` (400) does not apply: `load_prospects` gets an `only` argument that scans exactly the named survivors, so the page builds full prospects for one page of rows, not the whole list.

**Paging.** A keyset cursor: `after` is the stored position (1-based place in the ranked list) of the last row seen, `nextAfter` is the last row's position or null. A swap that changes the list never skips or repeats a row, and the page needs no offset. `total` is the count that matches the search and filters.

**Search.** `name_key(q)` (lower case, legal suffix dropped, `[a-z0-9]` only) is matched as a substring against the `company_name_key` rows (name, legal name, dba) and against `name_key(company.name)`, so "retell inc" and a 2 letter name both work. An empty key lists everything.

**Filters.** `levelRole`: the company has the `level_role` lane. `visaOk`: the company's `sponsorship` fact is not `no` (`fresh.load_visa_facts`). `unworked`: no outbound touch (`load_outbound`) and no application (`load_applied`). Filters narrow the list; they never add a company.

**Recording a swap.** The replaced pick's `daily_list_entry` row is deleted and the chosen company gets a new row at the same rank (`write_pick`, a new rowid above every row, so a stale tab never reaches it). Why delete rather than mark `removed`: the bench excludes every company with a row on today's list in any state, so a `removed` row would keep the replaced pick off the bench for the day and would make a swap back hit the `(list_date, company_id)` key. `replace.py` already deletes untouched rows, so there is precedent. History is kept as events:

- `pick_swapped` on the chosen company (entity company, actor devanshu), detail `text`, `list_date`, `rank`, `entry_id` (the new row), `replaced_entry_id`, `replaced_company_id`, `replaced_company`.
- `pick_swapped_out` on the replaced company with the same detail and a `text` of its own, so the line shows on both companies' pages.

Neither kind is read by the cooldown (`load_passes` reads only `pick_passed`). Only a row still to do with no step logged is replaceable (`state == "todo"`, no step has `at`): a reached, done or carried pick stays.

**Refusals** (`ApiProblem("not_allowed")`, each a plain sentence): the pick's row is gone or not today's; the pick was reached or is not to do; the company is a no-go; it is already on today's list; it is in flight (the contact reason); held back by Not now (with the return date) or an application; below the rotation cutoff or at the person limit; not in the ranked list. The idempotency key `pick-swap:<entryId>:<companyId>` answers a repeat with the first answer and writes nothing. A stale `revision` on the pick conflicts.

**Key.** F ("Find a company"), free in section 16.6. Inside the drawer: arrows move through rows, Enter opens "Use instead of...", Escape closes (one step back: the chooser first, then the drawer).

## Review Focus

- A merged-away company id sent as the swap target answers as its survivor (and a survivor already on the list is refused).
- Swapping A in for B and then B in for A on the same day works (the row key is free again).
- A search of punctuation only (`"..."`) or an empty result lists nothing and does not error; a cursor past the end returns an empty page with `nextAfter` null.
- A pick that was reached between opening the chooser and pressing Use is refused, and nothing is written.
- A stale `revision` on the replaced pick is a conflict.

## Task 1: Scan named companies, and one bench pick per prospect

**Files:**
- Modify: `outreach/plan/prospects.py` (`load_prospects`), `outreach/plan/select.py` (`bench`), `outreach/plan/picks.py`
- Test: `tests/test_plan_picks_only.py` (new)

**Interfaces:**
- Produces: `load_prospects(..., only: Collection[int] | None = None)`: when given, scans exactly those survivors from the ranked list (the scan limit and the fresh-pool rule do not apply; no-go, pause, hold and status rules still do); `position` is still the place in the full `ranked`. `select.bench_pick_of(p: Prospect, cfg: PlanConfig) -> BenchPick` (the opening and `early_fit` flag `bench` uses today; `bench` calls it). `picks.pool(conn, today, now, cfg, only=None)` public name for `_pool` with the `only` pass-through.

- [ ] **Step 1: Write the failing tests** in `tests/test_plan_picks_only.py` on the scratch seed `tests/plan_seed.py`: `test_only_scans_a_company_past_the_scan_limit` (cfg with `plan.scan_limit = 1`, a 3 company seed; `only={third}` returns that prospect with `position == 3`), `test_only_still_drops_a_no_go_company`, `test_bench_pick_of_matches_bench` (`bench(...)[0] == bench_pick_of(prospect, cfg)`).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_plan_picks_only -v`. Expected: FAIL (`only` unknown).
- [ ] **Step 3: Implement** the `only` argument and `bench_pick_of`; keep `bench`'s order and output byte for byte.
- [ ] **Step 4: Run** the new tests and `tests.test_plan_select tests.test_api_plan_picks`. Expected: PASS.
- [ ] **Step 5: Commit** `Plan: scan named companies past the scan limit (#120)`.

## Task 2: The browse read

**Files:**
- Create: `outreach/plan/browse.py` (pool, exclusions, filters, cursor), `outreach/api/queries/browse.py` (rows in the pick shape)
- Modify: `outreach/api/models/plan.py` (`BrowseCompany`, `BrowseView`), `outreach/api/routers/plan.py` (`GET /plan/browse`), `outreach/api/queries/bench.py` (`bench_rows` accepts a position per pick)
- Test: `tests/test_api_plan_browse.py` (new; seed as `tests/test_api_plan_picks.py`)

**Interfaces:**
- Produces in `outreach/plan/browse.py`: `BrowseFilters(q: str = "", level_role: bool = False, visa_ok: bool = False, unworked: bool = False)`; `browse_page(conn, today: date, now: datetime, cfg, f: BrowseFilters, after: int, limit: int) -> BrowsePage(prospects: list[Prospect], total: int, next_after: int | None, stored: dict[int, StoredScore])`. Exclusions run before the filters and before paging: a no-go company (own line, not left to `load_prospects`), a paused or held company, a status outside `KEPT_STATUSES`, `listed_today`, and a contact state of `waiting` or `engaged`.
- Produces in `outreach/api/models/plan.py`: `BrowseCompany(BenchCompany)` plus `blocked: str | None` (the reason text when the company cannot be swapped in, from `select.eligibility` and `held_back`, else null) and `worked: bool`; `BrowseView(as_of, date, companies: list[BrowseCompany], total: int, next_after: int | None)`. `position` on a row is the stored position.
- Route: `GET /api/plan/browse?q=&levelRole=&visaOk=&unworked=&after=0&limit=25` (limit 1 to 50), served through `views.serve` like the bench.

- [ ] **Step 1: Write the failing tests** in `tests/test_api_plan_browse.py`: `test_lists_in_stored_rank_order`, `test_search_matches_name_keys` (`"retell inc"` finds Retell AI; a 2 letter query finds a 2 letter name; `"..."` lists nothing), `test_level_role_filter`, `test_visa_filter_drops_a_sponsorship_no_company`, `test_unworked_filter_drops_touched_and_applied`, `test_paging_by_cursor` (limit 2 over 5 companies: pages are disjoint, `nextAfter` chains, the last page has `nextAfter` null, a cursor past the end is empty), `test_a_no_go_company_is_never_listed`, `test_todays_list_and_in_flight_are_never_listed`, `test_a_held_back_company_is_listed_with_its_reason` (`blocked` names the Not now date), `test_rows_carry_the_pick_facts` (`queue.whyToday`, `sponsorshipLine`, `sizeLine`, `opening`, `websiteUrl` present), `test_a_company_past_the_scan_limit_is_listed`, `test_the_read_writes_nothing` (event and row counts equal before and after).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_api_plan_browse -v`. Expected: FAIL (404 on the route).
- [ ] **Step 3: Implement** `browse_page`, the query that turns a page into `BrowseCompany` rows via `bench_rows(conn, [bench_pick_of(p, cfg) ...], stored, day, now, cfg)`, and the route.
- [ ] **Step 4: Run** the new tests and `tests.test_api_plan_picks`. Expected: PASS.
- [ ] **Step 5: Commit** `API: GET /api/plan/browse lists every company, paged (#120)`.

## Task 3: The swap write

**Files:**
- Modify: `outreach/api/services/picks.py` (`swap_pick`, the shared refusal text), `outreach/api/models/plan_writes.py` (`SwapPickBody`), `outreach/api/models/plan.py` (`SwapResult`), `outreach/api/routers/plan.py`
- Test: `tests/test_api_plan_swap.py` (new)

**Interfaces:**
- Consumes: `picks.pool(..., only={survivor})`, `select.eligibility`, `select.bench_pick_of`, `picks.write_pick`, `bump` from `outreach.api.revisions`.
- Produces: `SwapPickBody(company_id: int, idempotency_key: str (TOUCH_KEY), revision: int)`; `SwapResult(replaced_entry_id: int, replaced: CompanyRef, row: PlanRow, text: str)` where `text` reads "Swapped in Aster for Beacon."; `swap_pick(ctx: WriteCtx, entry_id: int, body: SwapPickBody, cfg: PlanConfig) -> SwapResult`; route `POST /api/plan/{entry_id}/swap`. Order inside the one `run_write`: key replay, row checks, company checks, `bump` the replaced row (revision check), `write_pick` the new row at the old rank into a spare rank, delete the old row, then both events. The shared refusal function also serves `add_from_bench`'s `_not_on_bench`, with the same sentences it has today plus a no-go sentence.

- [ ] **Step 1: Write the failing tests** in `tests/test_api_plan_swap.py`: `test_swap_takes_the_picks_rank_and_returns_the_pick_to_the_bench` (new row has the old rank, the old company is on `/api/plan/bench`, not on the plan), `test_swap_writes_no_pick_passed_and_no_cooldown` (zero `pick_passed` rows; the replaced company is eligible on the bench the same minute; `load_passes` is empty), `test_swap_works_when_there_is_no_room` (fill the 7-day window so `add` refuses with "No room", then the swap succeeds and `room` is unchanged), `test_swap_records_both_events` (`pick_swapped` on the new company with `replacedCompany`, `pick_swapped_out` on the old, both with `text`, and both show in `GET /api/company/{id}` events), `test_swap_back_works`, `test_refuses_a_no_go_company`, `test_refuses_a_company_in_flight` (message holds the contact reason), `test_refuses_a_held_back_company` (Not now date and return date), `test_refuses_a_company_already_on_the_list`, `test_refuses_a_reached_pick` (a logged request), `test_refuses_a_carried_pick`, `test_a_refusal_writes_nothing` (row and event counts equal), `test_the_same_key_answers_the_same_and_writes_once`, `test_stale_revision_conflicts`, `test_a_merged_company_answers_as_its_survivor`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_api_plan_swap -v`. Expected: FAIL (404).
- [ ] **Step 3: Implement** `swap_pick` and the route; the answer's `row` is `queries.build_row` of the new entry.
- [ ] **Step 4: Run** the new tests and `tests.test_api_plan_picks tests.test_api_plan_writes`. Expected: PASS.
- [ ] **Step 5: Commit** `API: swap a pick for any company, no Not now (#120)`.

## Task 4: OpenAPI, types and the clients

**Files:**
- Modify: `web/openapi.json`, `web/src/api/schema.ts` (generated: `pnpm gen:api` after `uv run python -m outreach.api.openapi`), `web/src/api/types.ts` (`BrowseCompany`, `BrowseView`, `SwapResult`), `web/src/api/schema.parity.ts`, `web/src/api/client.ts`, `web/src/api/http/plan.ts`, `web/src/api/http/views.ts`, `web/src/api/keys.ts`, `web/src/api/fixture/client.ts`, `web/src/api/fixture/queue-data.ts`
- Test: `web/src/api/fixture/client.write.test.ts`, the schema drift test

**Interfaces:**
- Produces: `client.browse(params: { q: string; levelRole: boolean; visaOk: boolean; unworked: boolean; after: number }): Promise<BrowseView>`; `client.swapPick(entryId: Id, input: { companyId: Id; idempotencyKey: string }, revision: Revision, o: WriteOptions): Promise<SwapResult>`; `queryKeys.browse(params)` is `['plan', 'browse', params]` so a plan write refetches it; `views.ts` maps `browse` and `swapPick` to `'plan'`. The fixture client answers the same way the API does (the same filters, cursor and refusals) over the `?scenario=queue` data (about 40 browse companies, one no-go, one held back, one in flight, so the e2e has paging).

- [ ] **Step 1: Write the failing fixture tests**: `browse pages by cursor and filters like the API`, `swap puts the company in the pick's slot and the pick back on the bench`, `swap refuses a held back company and works with no room`.
- [ ] **Step 2: Run** `cd web && pnpm test src/api/fixture/client.write.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement** the generated schema, types, both clients and the fixture data.
- [ ] **Step 4: Run** `pnpm test`, `pnpm typecheck`, `pnpm check:api`. Expected: PASS.
- [ ] **Step 5: Commit** `Web: browse and swap on the client and the fixture (#120)`.

## Task 5: The drawer, the chooser and the key

**Files:**
- Create: `web/src/routes/today/browse-drawer.tsx`, `web/src/routes/today/browse-drawer.test.tsx`
- Modify: `web/src/routes/today/use-plan-actions.ts` (`swapPick`), `web/src/routes/today/index.tsx` (button, key F, drawer), `web/src/routes/today/bench-drawer.tsx` (export the row body so both drawers draw one row shape)

**Interfaces:**
- Consumes: `client.browse`, `queryKeys.browse`, `BenchRow` body, `Sheet`, `Kbd`, `Input`, `Checkbox`.
- Produces: `<BrowseDrawer open onOpenChange rows={plan.rows} onSwap={(entryId, company) => boolean} />`; `actions.swapPick(row: PlanRow, b: BrowseCompany): boolean` queued as a pending write with Undo like Not now (patch: the plan row becomes the chosen company's pick, the bench loses it and gains the replaced pick; toast "Swapped in Aster for Beacon."; refusals toast their sentence). Design: the same look as the bench drawer (the 440 px sheet widened to 480 px for the filter row), the search box and three toggle chips under the header (At my level, Visa not a no, Not worked yet), rows are the pick row with the monogram, fit chip and facts, a row with `blocked` shows its reason in muted text and no button. Enter or the row's "Use instead of..." button turns the row into a list of today's replaceable picks (the chooser, 1 to 9 pick a row by number, arrows and Enter too); choosing one swaps. Escape closes the chooser, then the drawer. Rows load 25 at a time (a "Show more" button and an intersection observer both call the next cursor). Built with the frontend-design skill.

- [ ] **Step 1: Write the failing tests** in `browse-drawer.test.tsx`: `F opens the drawer and Escape closes it`, `typing searches and a filter chip narrows`, `arrows move through rows and Enter opens Use instead of`, `choosing a pick queues the swap and updates the rail`, `a held back company shows its reason and has no button`, `it works when Add to today is disabled`, `Show more loads the next page`, `Escape closes the chooser before the drawer`; and in `use-plan-actions.test.tsx`: `swapPick refuses a reached pick and says why`, `Undo puts the day back and nothing reaches the API`.
- [ ] **Step 2: Run** `pnpm test src/routes/today`. Expected: FAIL.
- [ ] **Step 3: Implement** the drawer and action with the frontend-design skill (look matches `bench-drawer.tsx`).
- [ ] **Step 4: Run** `pnpm test`, `pnpm typecheck`, `pnpm lint`. Expected: PASS.
- [ ] **Step 5: Commit** `Web: the Browse companies drawer (F) (#120)`.

## Task 6: The e2e spec

**Files:**
- Create: `web/e2e/browse.spec.ts` (next to `today-queue.spec.ts`; same fixtures)

- [ ] **Step 1: Write the spec**: open with F, search a name, toggle each filter, scroll to a second page, check a no-go and an in-flight company are absent, open "Use instead of..." with Enter, choose a pick with a digit, see the rail change and the replaced pick on More options (B) with no Not now toast, Escape closes. Run `WEB_PORT=$CONDUCTOR_PORT pnpm e2e browse.spec.ts`. Expected: PASS.
- [ ] **Step 2: Commit** `E2E: browse and swap (#120)`.

## Task 7: Mutation entries and docs

**Files:**
- Modify: `tests/mutation_check.py`, `docs/README.md`, `docs/design/README.md` (the 16.1b More options entry gets a Browse companies entry, the 16.6 keys table gets `F`), `outreach/api/routers/plan.py` docstring
- Test: `uv run python tests/mutation_check.py --list`

- [ ] **Step 1: Add mutations** (each `find` is exact text from the new code): `outreach/plan/browse.py` with the no-go exclusion removed (`expect` the `test_a_no_go_company_is_never_listed` test); `outreach/api/services/picks.py` with a swap that also counts against room (a room check added to `swap_pick`; expect `test_swap_works_when_there_is_no_room`); `outreach/api/services/picks.py` with a swap that appends a `pick_passed` event (expect `test_swap_writes_no_pick_passed_and_no_cooldown`).
- [ ] **Step 2: Run** the three entries by name from the runner (a `--only` filter if it has one, else the shard that holds them). Expected: each CAUGHT by the named test.
- [ ] **Step 3: Docs**: add this plan and the browse route and drawer to `docs/README.md`; the drawer, its keys and the swap rule to `docs/design/README.md`.
- [ ] **Step 4: Run** the full suite `uv run python -m unittest discover -s tests -t .` and `cd web && pnpm test && pnpm typecheck && pnpm lint`. Expected: all pass.
- [ ] **Step 5: Commit** `Docs and mutation check for Browse companies (#120)`, push, open the PR (`Closes #120`, base master), check `gh pr view <n> --json baseRefName,mergeStateStatus`.
