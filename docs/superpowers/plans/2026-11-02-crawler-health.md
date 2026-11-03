# Crawler health Implementation Plan (#152)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every tick says where its time went, giant boards with few relevant jobs stop eating the budget, and no
board stays overdue.

**Architecture:** The tick already times itself as a whole; this adds per ATS counts and milliseconds to the
`read_boards` job summary, a read-only `profile` command and a `churn` report over stored data. Then three cost cuts:
a low-yield big board waits at least a day between reads, bodies are fetched only for US engineering postings, and
Greenhouse lists stop carrying every job's full text.

**Tech Stack:** Python 3.14, SQLite, `unittest`, the crawler's fakes (`tests/fakes.py` `FakeClient`, fake clocks).

**Spec:** `docs/superpowers/plans/2026-11-02-right-job-pipeline-master-plan.md` PR 1 and issue #152.

## Global Constraints

- Every rule in `CLAUDE.md`: tests use scratch settings and never reach the network; `crawl` commands that send
  requests run only in the live checkout; `profile` and `churn` read the store only.
- Per-host spacing (`host_interval_s`) does not change. No change to any ATS's request rate except Greenhouse
  sending one detail request per new engineering posting instead of a larger list.
- A posting and its decision stay in one write unit (`ingest._apply_changes`): deciding stays inside it.
- `job_run.summary` stays a flat object of counts plus the existing nested `probe` and `watch`; the new `by_ats` is one
  more nested object. `api/queries/jobs.py` `counts_of` keeps reading integer keys only.
- No em dashes or en dashes in comments, docs, commit messages or the PR body.
- One branch (`DevanshuNEU/crawler-health-152`), one PR to `master`, body says `Part of #152` (the issue closes after
  the 2 day live check in Task 7).

## Review Focus

1. **A plan company's board on a giant low-yield board** must keep today's floor (`due_boards` reads plan companies at
   `floor_s`). Test in Task 4: `test_plan_company_board_never_slowed`.
2. **A giant board that posts a new grad role** waits at most one day before Knock sees it, then returns to its normal
   interval on that read. Test in Task 4: `test_fresh_fit_posting_restores_interval`.
3. **Greenhouse bodies on a big board:** with bodies no longer in the list, `detail_per_read` (150) caps body fetches
   per read; the newest engineering postings must come first (`missing_body_ids` orders newest first). Test in Task 6:
   `test_greenhouse_new_engineering_bodies_first`.
4. **`profile` over runs written before this change** (no `by_ats` key) must skip them, not fail. Test in Task 2.
5. **A held error or empty read** (read in `_Pass.read`, ingested later in `finish`) must still count its list time
   once and its ingest time once. Test in Task 1: `test_held_read_counted_once`.

---

### Task 1: Per ATS counts and milliseconds in the tick summary

**Files:**
- Modify: `outreach/crawl/tick.py` (`_Pass`, `_TickClient`, `_read_boards`, `TickReport`)
- Modify: `outreach/crawl/ingest.py` (`read_board`, `_backfill`, `ReadOutcome`)
- Test: `tests/test_crawl_tick_profile.py` (new)

**Interfaces:**
- Produces: `ReadOutcome.body_requests: int = 0` and `ReadOutcome.body_ms: int = 0`; `read_board(..., clock:
  Callable[[], float] = time.monotonic)`.
- Produces: `_TickClient.requests: int` (incremented on every `get`, `post` and `download`).
- Produces: `TickReport.by_ats: dict[str, dict[str, int]]` (default empty), written into the summary as `by_ats` with,
  per ATS: `boards`, `list_requests`, `list_ms`, `body_requests`, `body_ms`, `ingest_ms`, `new`, `closed`,
  `reopened`.

- [ ] **Step 1: Write the failing tests** in `tests/test_crawl_tick_profile.py`, on a scratch store with one Greenhouse
  and one Lever board, a `FakeClient` with saved fixtures, and a fake clock that advances 100 ms per request:
  `test_summary_has_per_ats_counts` (both ATSs present, `boards == 1` each, `list_requests >= 1`, `list_ms >= 100`,
  `new` equals the fixture's job count), `test_held_read_counted_once` (an error read held for `finish` has
  `boards == 1` and its `list_ms` counted once), `test_health_counts_ignore_by_ats` (`counts_of` on the new summary
  returns only integer keys).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_tick_profile -v`. Expected: FAIL (no `by_ats`).
- [ ] **Step 3: Implement.** `_Pass` takes `clock` (passed from `_read_boards`), times `list_board` in `read` and
  `read_board` in `_ingest`, reads `view.requests`, and adds into `self.by_ats[board.ats]`. `_backfill` counts the
  bodies it asked for and their milliseconds with the passed clock.
- [ ] **Step 4: Run** the new tests and `uv run python -m unittest tests.test_crawl_tick tests.test_crawl_ingest -v`.
  Expected: PASS.
- [ ] **Step 5: Commit** `Crawl: per ATS counts and milliseconds in each tick's summary (#152)`.

### Task 2: `python -m outreach.crawl profile`

**Files:**
- Create: `outreach/crawl/profile.py`
- Modify: `outreach/crawl/__main__.py` (subcommand `profile --runs N [--json]`)
- Test: `tests/test_crawl_profile.py` (new)

**Interfaces:**
- Consumes: the `by_ats` summary from Task 1.
- Produces: `profile(conn, runs: int) -> Profile` with `Profile(runs: int, seconds: float, rows: list[AtsProfile])` and
  `AtsProfile(ats: str, boards: int, seconds: float, seconds_per_board: float, share: float, list_requests: int,
  body_requests: int, new: int, closed: int, reopened: int)`, rows sorted by `seconds` descending. `share` is the ATS's
  seconds over the summed `seconds` of the runs.

- [ ] **Step 1: Write the failing tests:** `test_profile_sums_runs` (three seeded `read_boards` runs; Workday's
  seconds and share are the sums), `test_profile_skips_runs_without_by_ats` (an older summary is skipped and `runs`
  counts only the used ones), `test_profile_cli_json` (the command prints JSON with the same numbers).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_profile -v`. Expected: FAIL (no module).
- [ ] **Step 3: Implement** `profile` (reads the newest `runs` finished `read_boards` rows) and the subcommand (plain
  table by default: ATS, boards, seconds, seconds per board, share, requests, new, closed).
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `Crawl: profile command reads where tick time goes (#152)`.

### Task 3: `python -m outreach.crawl churn`

**Files:**
- Create: `outreach/crawl/churn.py`
- Modify: `outreach/crawl/__main__.py` (subcommand `churn --days N [--limit N] [--json]`)
- Test: `tests/test_crawl_churn.py` (new)

**Interfaces:**
- Produces: `churn(conn, now: str, days: int, limit: int) -> list[BoardChurn]` with `BoardChurn(board_id, ats, token,
  open: int, closed: int, new: int, relisted: int, us_engineering_open: int)`, sorted by `closed` descending.
  `relisted` counts postings first seen in the window whose board, title and location match a posting of the same
  board closed in the 7 days before their first sight.

- [ ] **Step 1: Write the failing tests:** `test_churn_counts_closed_and_new`, `test_relisted_needs_same_title_and_
  location`, `test_churn_orders_by_closed`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_churn -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with one query per figure over `posting` and `posting_decision` (`us != 'no'` and lane
  engineering for `us_engineering_open`).
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `Crawl: churn report per board (#152)`.

### Task 4: Low-yield big boards wait a day

**Files:**
- Modify: `outreach/crawl/cadence.py` (new `low_yield`), `outreach/crawl/ingest.py` (phase 7), `outreach/crawl/
  config.toml` and `config.py` (`[big_board]`)
- Test: `tests/test_crawl_cadence.py` (add), `tests/test_crawl_ingest.py` (add)

**Interfaces:**
- Produces: `low_yield(conn, board_id: int, now: str, cfg: CrawlConfig) -> bool`: True when the board has at least
  `big_board.min_open` (200) open postings, the share of them that are US engineering (`lane = 'engineering'` and
  `us != 'no'` in the current decision) is under `big_board.max_share` (0.10), and none of its US engineering postings
  at his level or with no stated level (`level_ok = 1` or `level_tier IN ('not_stated','open')`) was first seen in the
  last `big_board.quiet_days` (30).
- Produces: config `[big_board] min_open = 200`, `max_share = 0.10`, `quiet_days = 30`, `interval_s = 86400`, each
  range checked by `config.py` like the other keys.
- Phase 7 of `read_board`: after `next_interval_s`, when `low_yield(...)` is True the interval becomes
  `max(interval_s, cfg.big_board.interval_s)`.

- [ ] **Step 1: Write the failing tests:** `test_low_yield_big_board_waits_a_day` (250 open postings, 10 US
  engineering, none fresh: next read 24 hours out), `test_fresh_fit_posting_restores_interval` (the same board with a
  US engineering posting first seen yesterday keeps the normal interval), `test_small_board_never_low_yield` (150
  open), `test_share_counts_us_engineering_only` (non-US engineering postings do not count toward the share),
  `test_plan_company_board_never_slowed` (a low-yield board of a company on today's list is still due at `floor_s`
  through `due_boards`).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_cadence tests.test_crawl_ingest -v`. Expected: the new
  tests FAIL.
- [ ] **Step 3: Implement** `low_yield` as one query on the board's open postings joined to the current decision (by
  key, as `_undecided` does), the config section, and the phase 7 change.
- [ ] **Step 4: Run** the tests and `uv run python -m unittest tests.test_crawl_config -v`. Expected: PASS.
- [ ] **Step 5: Commit** `Crawl: a big board with few US engineering jobs is read at most daily (#152)`.

### Task 5: Bodies only for US engineering postings

**Files:**
- Modify: `outreach/crawl/bodies.py` (`missing_body_ids`, `refetch_body_ids`)
- Test: `tests/test_crawl_bodies.py` (add)

- [ ] **Step 1: Write the failing tests:** `test_bodies_only_for_us_postings` (a non-US engineering posting with no
  body is not in `missing_body_ids`; a US one and one with `us` unknown are), `test_refetch_skips_non_us`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_bodies -v`. Expected: FAIL.
- [ ] **Step 3: Implement:** add `AND d.us != 'no'` to both queries' conditions (an unknown `us` still fetches, since
  the body may settle it).
- [ ] **Step 4: Run** the tests and `tests.test_crawl_ingest`. Expected: PASS.
- [ ] **Step 5: Commit** `Crawl: fetch bodies only for US engineering postings (#152)`.

### Task 6: Greenhouse lists without full text

**Files:**
- Modify: `outreach/crawl/ats/greenhouse.py` (`_US`, `_EU`, `body`)
- Modify: `tests/fixtures/crawl/greenhouse_jobs.json` and add `tests/fixtures/crawl/greenhouse_detail.json`
- Test: `tests/test_crawl_ats_greenhouse.py` (change `test_requests_content_true`, `test_content_is_plain_text` and the
  body tests to the detail path), `tests/test_crawl_ingest.py` (add)

**Interfaces:**
- Produces: the list URL `https://boards-api.greenhouse.io/v1/boards/{token}/jobs` (EU host the same way) with no
  query; `GreenhouseAdapter.body(token, job) -> str | None` reads
  `https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{native_id}` (the EU host when the list came from it) and
  returns the plain text of `content` as `_parse` does today; a 404 or a missing `content` returns None.

- [ ] **Step 1: Write the failing tests:** `test_list_url_has_no_content` (the requested URL has no query string),
  `test_body_reads_detail` (plain text from the detail fixture), `test_body_404_is_none`, and in the ingest tests
  `test_greenhouse_new_engineering_bodies_first` (a board read lists 3 new postings, 2 engineering; the 2 engineering
  bodies are fetched, newest first, the third is not).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_ats_greenhouse tests.test_crawl_ingest -v`.
  Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the URL change and `body`; `JobRef.body` from the list is now None, so ingest's existing
  `_backfill` path fetches bodies.
- [ ] **Step 4: Run** the full suite: `uv run python -m unittest discover -s tests -t .`. Expected: PASS.
- [ ] **Step 5: Commit** `Crawl: Greenhouse lists without full text; bodies by detail for engineering postings (#152)`.

### Task 7: PR, deploy, measure

- [ ] **Step 1:** Push, open the PR to `master` with `Part of #152`. The body states the measured baseline (every tick
  used its 1,500 second budget, 104 to 381 boards a tick, 477 overdue; 56 boards with 1,000 or more open postings hold
  167,020 of 315,243 open postings, 6% US engineering) and what each task changes. Check `gh pr view <n> --json
  baseRefName,mergeStateStatus` shows `master` and `CLEAN`.
- [ ] **Step 2:** After Devanshu merges: in the live checkout `git pull --ff-only https://github.com/DevanshuNEU/
  sept-23-companies.git master`, `uv sync`, the full test run, then `launchctl kickstart -k
  gui/$(id -u)/com.devanshu.outreach-crawl`.
- [ ] **Step 3:** After 2 days, in the live checkout: `uv run python -m outreach.crawl profile --runs 96` and `uv run
  python -m outreach.crawl churn --days 2`. Report to Devanshu: seconds per ATS, the share Workday and Oracle now take,
  overdue boards (`SELECT count(*) FROM board WHERE active = 1 AND next_read_at < now`), and how long a full pass over
  the boards takes. #152 closes when no tick ends with boards overdue past their interval and a full pass is under
  60 minutes; if not, the profile names the next cost to cut and a follow-up issue holds it.
