# Pipeline scorecard Implementation Plan (#153)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Knock measures itself every day against SimplifyJobs' new grad list, records any job Devanshu found that
Knock missed with the reason, and raises an alarm when a reader or a number breaks.

**Architecture:** A new `outreach/pipeline/` package. One pure URL parser turns any job link into a board key. A feed
poller keeps SimplifyJobs' listings in a `feed_listing` table (one conditional request per crawl tick). A scorecard
reads that table against Knock's postings and decisions. A miss log diagnoses pasted links. Alarms read the store
and show on the sidebar; one Mac notice a day at most.

**Tech Stack:** Python 3.14, SQLite, FastAPI, React, `unittest`, the crawler's fakes (`tests/fakes.py`).

**Spec:** `docs/superpowers/plans/2026-11-02-right-job-pipeline-master-plan.md` PR 2 and issue #153.

## Global Constraints

- Every rule in `CLAUDE.md`. Tests use scratch settings and never reach the network. The feed poll sends a real
  request, so it runs only inside the live crawl tick; the scorecard command reads the store only.
- SimplifyJobs: Knock stores the listing's link, company name, title, posted hint and first sight to measure
  itself; it never shows a SimplifyJobs listing as a job and never takes a date from it for a posting.
- The poll is one conditional GET (`If-None-Match`) per tick to
  `https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json`, through the
  crawler's polite client; a failure never fails the tick.
- Kept listings: `active` and `is_visible` true, `category` in `Software`, `Software Engineering`, `AI/ML/Data`,
  `Data Science, AI & Machine Learning`, and a US location (a location naming the US, USA or United States, or ending
  in a US state code).
- Migration `0006_yardstick.sql` only adds tables. No table rebuild.
- Claude never calls the API's write routes. The new `POST /api/pipeline/misses` is tested with the test client only.
- No em dashes or en dashes in comments, UI copy, docs, commit messages or the PR body.
- One branch (`DevanshuNEU/scorecard-153`), one PR to `master`, body `Closes #153`.

## Review Focus

1. **The first poll** inserts about 3,000 rows that were not first seen by Knock's clock: they are `backfill = 1` and
   never count toward "lead over SimplifyJobs". Test in Task 2.
2. **A listing that leaves the file** stays in `feed_listing` with `active = 0`; it is not deleted and stops counting
   toward coverage. Test in Task 2.
3. **A `gh_jid` link on a company's own site** has no Greenhouse token; it matches a posting by native id alone
   (`source_key LIKE 'greenhouse:%:<id>'`). Test in Task 3.
4. **A pasted link Knock cannot parse** (a careers page with no ATS) is stored with cause `unknown_site`, never
   rejected. Test in Task 4.
5. **No scorecard yet** (fresh store): the sidebar line says the scorecard has not run, and no alarm compares against a
   missing week. Test in Task 5.

---

### Task 1: The yardstick tables and the board key parser

**Files:**
- Create: `outreach/store/migrations/0006_yardstick.sql`, `outreach/pipeline/__init__.py`, `outreach/pipeline/urls.py`
- Test: `tests/test_pipeline_urls.py` (new), `tests/test_store_migrations.py` or the existing migration test (add)

**Interfaces:**
- Produces tables: `feed_listing(feed_id INTEGER PRIMARY KEY, source TEXT NOT NULL, listing_id TEXT NOT NULL, url TEXT
  NOT NULL, ats TEXT, token TEXT, native_id TEXT, company_name TEXT NOT NULL, title TEXT NOT NULL, posted_hint TEXT,
  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, active INTEGER NOT NULL CHECK (active IN (0,1)), backfill
  INTEGER NOT NULL CHECK (backfill IN (0,1)), UNIQUE (source, listing_id)) STRICT`; `miss(miss_id INTEGER PRIMARY KEY,
  url TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', pasted_at TEXT NOT NULL, cause TEXT NOT NULL CHECK (cause IN
  ('no_company','no_board','not_read_yet','read_wrong_level','read_not_tech','hidden_visa','wrong_date','found',
  'unknown_site')), detail TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(detail)), posting_id INTEGER REFERENCES
  posting(posting_id), company_id INTEGER REFERENCES company(company_id), board_id INTEGER REFERENCES
  board(board_id), resolved_at TEXT) STRICT`; `pipeline_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT`.
  Timestamps use the store's canonical UTC CHECK like other tables.
- Produces: `urls.board_key(url: str) -> BoardKey | None`, `BoardKey(ats: str, token: str | None, native_id: str |
  None)`, frozen dataclass. `ats` uses the crawler's names (`greenhouse`, `ashby`, `lever`, `workday`, `workable`,
  `rippling`, `icims`, `jazzhr`). Workday's token is `<tenant>.<wdN>/<site>` and its native id the path from `/job/`,
  matching the crawler's `source_key` (`workday:<token>:/job/...`). A `gh_jid=<n>` query on any host gives
  `('greenhouse', None, '<n>')`. `myworkdaysite.com` URLs parse like `myworkdayjobs.com`. Locale segments
  (`en-US`) are skipped.
- Produces: `urls.posting_key(key: BoardKey) -> str | None`: the lower-case crawler `source_key` when token and
  native id are both known, else None.

- [ ] **Step 1: Write the failing tests:** `test_board_key_parses_every_shape` (one case per ATS above, including
  `job-boards.greenhouse.io/<t>/jobs/<id>`, `boards.greenhouse.io/embed/job_app?for=<t>&token=<id>`, a company site
  with `?gh_jid=123`, `jobs.ashbyhq.com/<t>/<uuid>`, `jobs.lever.co/<t>/<uuid>`,
  `<tenant>.wd5.myworkdayjobs.com/en-US/<site>/job/<path>`, `<tenant>.wd3.myworkdaysite.com/<site>/job/<path>`,
  `apply.workable.com/<t>/j/<id>`, `ats.rippling.com/<t>/jobs/<uuid>`, `careers-<t>.icims.com/jobs/<id>/...`,
  `<t>.applytojob.com/apply/<id>/...`), `test_unknown_site_is_none`, `test_posting_key_matches_crawler_source_key`
  (compare with `outreach.crawl.postings.source_key` for Greenhouse, Ashby, Lever and Workday), and a migration test
  that `0006` creates the three tables on a scratch store.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_pipeline_urls -v`. Expected: FAIL (no module).
- [ ] **Step 3: Implement** the migration and `urls.py` (standard library `urllib.parse` and `re`).
- [ ] **Step 4: Run** the new tests and the migration tests. Expected: PASS.
- [ ] **Step 5: Commit** `Pipeline: yardstick tables and one parser for job links (#153)`.

### Task 2: The SimplifyJobs feed poll

**Files:**
- Create: `outreach/pipeline/simplify_feed.py`, `outreach/pipeline/config.toml`, `outreach/pipeline/config.py`
- Modify: `outreach/crawl/tick.py` (`run_tick` calls the poll once after the board reads, inside a try that records an
  error in the summary as `feed_error` and never fails the tick)
- Test: `tests/test_pipeline_simplify_feed.py` (new), `tests/fixtures/pipeline/simplify_listings.json` (new: about 12
  listings covering kept and dropped categories, US and non-US, inactive, hidden)

**Interfaces:**
- Consumes: `urls.board_key` (Task 1).
- Produces: `simplify_feed.poll(conn, client, now: str) -> FeedPoll(changed: bool, seen: int, new: int, closed: int)`.
  ETag stored in `pipeline_meta` key `simplify_etag`; a 304 returns `changed=False` and writes nothing else. Kept rows
  upsert by `listing_id` (`id` in the file): `first_seen_at` only on insert, `last_seen_at` and `active=1` every
  changed poll, `posted_hint` from `date_posted` as canonical UTC. Rows absent from a changed file get `active = 0`.
  When `feed_listing` has no `simplify` row before the poll, every inserted row gets `backfill = 1`.
- Produces: config `[simplify] url`, `categories`, `enabled = true` (a false value skips the poll).
- The tick's summary gains `feed` (`{"changed": 0|1, "seen": n, "new": n}`) or `feed_error`.

- [ ] **Step 1: Write the failing tests:** `test_poll_keeps_us_tech_only`, `test_poll_sets_first_seen_once`,
  `test_poll_unchanged_etag_writes_nothing`, `test_first_poll_rows_are_backfill` (and a later poll's new rows are not),
  `test_listing_gone_from_file_is_inactive`, `test_tick_survives_feed_error` (a `FakeClient` route that raises).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_pipeline_simplify_feed -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the poll (one `client.get` with `If-None-Match`, `json.loads`, the filters from Global
  Constraints) and the tick call.
- [ ] **Step 4: Run** the new tests and `tests.test_crawl_tick`. Expected: PASS.
- [ ] **Step 5: Commit** `Pipeline: poll SimplifyJobs' new grad list once per tick (#153)`.

### Task 3: The scorecard and its command

**Files:**
- Create: `outreach/pipeline/scorecard.py`, `outreach/pipeline/__main__.py`
- Modify: `outreach/sources/__main__.py` (`run_due` runs the scorecard last as its own `step`)
- Test: `tests/test_pipeline_scorecard.py` (new)

**Interfaces:**
- Consumes: `feed_listing` (Task 2), `urls.posting_key` (Task 1).
- Produces: `scorecard.compute(conn, now: str) -> Scorecard` with fields `simplify_listings: int`, `simplify_coverage:
  float | None`, `level_recall: float | None`, `lead_over_simplify: float | None`, `detection_p95_hours: float |
  None`, `date_truth: None`, `small_company_coverage: None`, `gap_top: list[GapCompany(company_name, ats, listings,
  knock_knows_company: bool, knock_has_board: bool)]` (top 15 by listings).
  - The window: active rows whose `posted_hint` is in the last 30 days.
  - Covered: a posting exists with the row's `posting_key`, or (token None) a Greenhouse posting whose `source_key`
    ends with `:<native_id>`.
  - Level recall: of covered rows, the share whose posting's current decision has `level_ok = 1` or `level_tier IN
    ('not_stated','open')`.
  - Lead: of covered rows with `backfill = 0`, the share whose posting's `first_seen_at` is earlier than the row's
    `first_seen_at`.
  - Detection p95: over Greenhouse, Ashby and Lever postings with a `posted_on` in the last 7 days on boards created
    more than 2 days before that date, the 95th percentile of hours from `posted_on` (midnight UTC) to `first_seen_at`.
  - A ratio with no rows is None, never 0.
- Produces: `scorecard.record(conn, card: Scorecard, now: str) -> None`: one event, entity `score`, kind
  `pipeline_scorecard`, actor `system`, detail the card's numbers, key `scorecard:<New York day>` (a second run the same
  day writes nothing).
- Produces: `python -m outreach.pipeline scorecard [--json] [--record]` (prints the card; `--record` also stores it);
  `run_due` calls `compute` then `record`.

- [ ] **Step 1: Write the failing tests:** `test_scorecard_on_seeded_store` (10 window rows, 4 covered postings of
  which 2 have `level_ok = 1`, 1 has `level_tier = 'not_stated'`, 1 is `senior`: coverage 0.4, level recall 0.75),
  `test_gh_jid_row_matches_by_native_id`, `test_backfill_rows_do_not_count_toward_lead`, `test_empty_ratios_are_none`,
  `test_detection_p95_on_watched_boards_only`, `test_record_once_a_day`, `test_cli_json`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_pipeline_scorecard -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `compute` with a few grouped queries (no per-row query against `posting`), `record`, the
  command (store through `connect()`, like other commands), and the `run_due` step.
- [ ] **Step 4: Run** the new tests and `tests.test_sources_cli`. Expected: PASS.
- [ ] **Step 5: Commit** `Pipeline: daily scorecard against SimplifyJobs (#153)`.

### Task 4: The miss log

**Files:**
- Create: `outreach/pipeline/misses.py`
- Modify: `outreach/mcp/tools/` (a new `log_miss` tool, registered like the others, with grants `miss` insert only),
  `outreach/api/routers/pipeline.py` (new), `outreach/api/models/pipeline.py` (new), `outreach/api/services/
  pipeline.py` (new)
- Test: `tests/test_pipeline_misses.py` (new), `tests/test_mcp_log_miss.py` (new), `tests/test_api_pipeline.py` (new)

**Interfaces:**
- Consumes: `urls.board_key`, `urls.posting_key` (Task 1).
- Produces: `misses.diagnose(conn, url: str) -> Diagnosis(cause, posting_id, company_id, board_id, detail: dict)` in
  this order: no board key gives `unknown_site`; a matching open or closed posting gives `found` when its current
  decision passes and is at his level or not stated, `read_wrong_level` when `level_ok = 0` and the tier is not
  `not_stated` or `open`, `read_not_tech` when the lane is not engineering, `hidden_visa` when citizenship is required;
  a board with that `(ats, token)` but no posting gives `not_read_yet`; a company whose name or domain matches but no
  board gives `no_board`; else `no_company`. `wrong_date` is set later by a person, not by `diagnose`.
- Produces: `misses.log_miss(conn, url: str, note: str, now: str) -> Miss` (stores the diagnosis; the same url twice
  returns the first row).
- Produces: MCP tool `log_miss(url, note="")` returning the cause in plain words; `POST /api/pipeline/misses` with
  body `{url, note}` through `run_write` (idempotency key header), returning `{missId, cause, causeText}`.

- [ ] **Step 1: Write the failing tests:** `test_diagnose_each_cause` (one seeded case per cause `diagnose` can give),
  `test_unknown_site_is_stored` (Review Focus 4), `test_same_url_twice_keeps_one_row`, the MCP tool's input checks and
  result, and the API route on the test client (`tests/apitest.py`).
- [ ] **Step 2: Run** the three test modules. Expected: FAIL.
- [ ] **Step 3: Implement**, then regenerate `uv run python -m outreach.api.openapi --write` and, in `web/`,
  `pnpm gen:api`.
- [ ] **Step 4: Run** the new tests, `tests.test_mcp_*`, `tests.test_api_*` and `cd web && pnpm typecheck`. Expected:
  PASS.
- [ ] **Step 5: Commit** `Pipeline: miss log through MCP and the API (#153)`.

### Task 5: Alarms and the sidebar line

**Files:**
- Create: `outreach/pipeline/alarms.py`
- Modify: `outreach/crawl/tick.py` (after the feed poll: `alarms.check`, and one Mac notice a day through
  `sending.notify.notify_once` with kind `pipeline_alarm`), `outreach/api/models/shell.py` (`ShellSummary.pipeline`),
  `outreach/api/queries/shell.py`, `web/src/shell/app-sidebar.tsx` (one line under "Boards read"), the API client,
  fixtures and generated types; `GET /api/pipeline/scorecard` in `outreach/api/routers/pipeline.py`
- Test: `tests/test_pipeline_alarms.py` (new), `tests/test_api_shell.py` (add), `web/src/shell/app-sidebar.test.tsx`
  (add)

**Interfaces:**
- Produces: `alarms.check(conn, now: str) -> list[Alarm(kind, text)]`, kinds:
  - `reader_failing`: an ATS with at least 5 boards read in the last 48 hours, half or more of them now `last_read =
    'error'` (text names the ATS and the share);
  - `number_dropped`: the newest `pipeline_scorecard` event's coverage or level recall is at least 25% below the one
    6 to 8 days older (skipped when either is missing);
  - `source_failing`: `discover`, `resolve` or `find_boards` failed on its last two runs, or the feed poll errored on
    the last two ticks (counted in `pipeline_meta` key `simplify_fail_streak`);
  - `boards_overdue`: 50 or more active boards whose `next_read_at` is earlier than now minus their
    `change_interval_s` (or `floor_s` when unknown).
- Produces: `ShellSummary.pipeline: PipelineLine | None` with `text` (for example "Coverage 32%, level 51%" from the
  newest scorecard, or "Scorecard not run yet"), `alarms: list[str]`, `tone` (`bad` when any alarm, else `idle`).
- Produces: `GET /api/pipeline/scorecard` through `views.serve`, returning the newest stored card and its date.

- [ ] **Step 1: Write the failing tests:** one per alarm kind firing and one per kind staying quiet below its
  threshold, `test_no_scorecard_yet` (Review Focus 5), `test_notice_once_a_day` (a fake notifier is called once for two
  ticks the same day), the shell model field, and a sidebar test that shows the line and the alarm text.
- [ ] **Step 2: Run** the new Python tests and `cd web && pnpm test -- app-sidebar`. Expected: FAIL.
- [ ] **Step 3: Implement**, regenerate the OpenAPI file and `schema.ts`, update fixtures and `schema.parity.ts`.
  The sidebar line goes through the `craft` skill's gate: one short line, the alarm in the warning tone, no new colors.
- [ ] **Step 4: Run** the full suite and the web checks (`pnpm typecheck && pnpm test && pnpm build`). Expected: PASS.
- [ ] **Step 5: Commit** `Pipeline: alarms and the sidebar scorecard line (#153)`.

### Task 6: PR and the first live numbers

- [ ] **Step 1:** Push, open the PR to `master` with `Closes #153`; check `gh pr view <n> --json
  baseRefName,mergeStateStatus` shows `master` and `CLEAN`.
- [ ] **Step 2:** After Devanshu merges: deploy per `CLAUDE.md` (pull, `uv sync`, tests, `uv run python -m
  outreach.install` for `0006`, web build, kickstart the API and the crawl job).
- [ ] **Step 3:** After the next tick: `uv run python -m outreach.pipeline scorecard --record` in the live checkout.
  Report the numbers to Devanshu; coverage and level recall should be near the 2026-11-01 baseline (32% and 51%), and
  the BambooHR and JazzHR readers should show as `reader_failing`.
