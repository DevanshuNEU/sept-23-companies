# First to See: Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> One part is one issue, one branch from the current `origin/master`, one PR into `master` (CLAUDE.md, Branching).

**Goal:** Knock is a job board for Devanshu alone: one click reads every Greenhouse and Ashby board, the list shows
his level's jobs sorted by when each was posted, and Knock stops choosing companies for him.

**Architecture:** The crawler's list requests run on worker threads per ATS while one thread writes the store, so a
check of thousands of boards takes minutes. A seed job adds every Greenhouse and Ashby company with an open US
engineering role from stapply's lists through a one-request gate. Each new posting stores a list time (the board's
posting time, never later than Knock's first sighting) and is sorted by it. Check now records a request and kicks
the existing crawl job, which reads every Greenhouse and Ashby board. Today, Proposals and Email today go; Go ahead
becomes Done, his mark that a company has been reached. The background schedule does not change.

**Tech Stack:** Python 3.14 (Homebrew), SQLite (WAL), FastAPI, `threading` and `queue` from the standard library,
React 19 + Vite + TanStack Query + shadcn/ui, vitest, Playwright, launchd.

**Spec:** `docs/superpowers/specs/2026-11-09-first-to-see-jobs-design.md`. Evidence:
`docs/research/2026-11-09-first-to-see-new-postings.md`. Decisions: issue #9 (the spec's section 10 ruling goes
into #9 first, Part 0).

## Global Constraints

- #9 wins over every doc. Edit #9 before code that changes a decision (Part 0).
- Python is `/opt/homebrew/bin/python3` (3.14). Tests: `uv run python -m unittest discover -s tests -t .` from the
  repo root. Web: `cd web && pnpm typecheck && pnpm lint && pnpm test`, and `pnpm check:api` after any API model
  change (regenerate with `uv run python -m outreach.api.openapi`, then `pnpm gen:api`).
- No new dependency. Threads from the standard library; the existing `outreach.net` client.
- No new background load: no new launchd job, no shorter `StartInterval`, the crawl tick's cadence unchanged.
- Tests never send a real request and never open the live store (`tests/__init__.py` scratch settings). Every
  network test uses a fake `HttpClient` (see `tests/fakes.py` and `tests/test_crawl_tick.py`).
- Test first: write the test, watch it fail for the right reason, then make it pass.
- One writer: worker threads only send list requests; every store write happens on the thread that owns the
  connection.
- Politeness: spacing never drops below `[host_interval_s]`: Greenhouse 0.05 s, Ashby 0.1 s, Lever 1.0 s. The
  breaker (trips at once on 429), outage shield, host block item and `[ats_off]` keep their keys and behavior.
- Claude never calls an API write route (Check now, Done, Pass included), never runs the crawler or the seed outside
  the live checkout, and asks Devanshu before the first live run of Parts 1, 2 and 4.
- Plain words, no em dashes or en dashes in anything a person reads: UI copy, docs, commit messages, PR bodies.
- Every UI change goes through the `craft` skill and its gate before UI code.
- Numbers from the spec, verbatim: repost window 120 days; Check now skips a board read in the last 60 s; a failed
  gate is tried again after 30 days; the "found after a gap" tag shows when the gap is over 7200 s; quiet hours
  22:00 to 08:00 New York.

## Review Focus

Five conditions the spec implies that no happy-path test covers, each pinned by a test in the task that owns it.

1. **The seed adds a board with 300 postings, some from March.** Expected: none reach the top, none are new, no
   alert; each sorts by its own posting time. Tests: `test_first_read_rows_sort_by_their_posting_time` and
   `test_first_read_rows_never_new` (Task 3.2), `test_first_read_never_alerts` (Task 3.4).
2. **Ashby republishes a job Knock already has.** `publishedAt` jumps to now. Expected: the row does not move.
   Test: `test_ashby_republish_does_not_move_known_job` (Task 3.2).
3. **Greenhouse answers 429 halfway through a check, with workers mid-flight.** Expected: the bucket trips, no worker
   sends to it again in that run, its boards are deferred as in a serial tick, and the progress line says Greenhouse
   paused. Tests: `test_trip_stops_other_workers_on_that_bucket` (Task 1.4), `test_check_reports_tripped_host`
   (Task 4.2).
4. **Check now clicked twice, or while the 30-minute tick runs.** Expected: one check, the second click shows the
   running one, a request made during a tick runs right after it. Tests: `test_check_now_twice_kicks_once`
   (Task 4.1), `test_request_during_tick_runs_after_it` (Task 4.2).
5. **The list is scrolled or a row is open when new rows arrive.** Expected: nothing moves; a "N new" button
   appears. Test: `holds new rows while scrolled` (Task 4.4).

---

## Part 0: #9 ruling and issues (no code)

Ask Devanshu before posting anything to GitHub.

- [ ] Add the spec's section 10 ruling to #9 as a new bullet at the end of its body.
- [ ] Edit #158 to say Part 1 takes its parallel reads (one writer thread) and the tiers are dropped; note on #161
  and #160 that Part 2 takes the Greenhouse and Ashby lists with a direct gate; note on #155 that Part 3 takes
  `repost_of` and the 120-day window.
- [ ] Open issues for Part 2 (every Greenhouse and Ashby company), Part 3 (sorted by posting time), Part 4 (Check now
  and the self-updating list), Part 5 (Today goes away, Done replaces Go ahead). Each body: what, "Done when" from
  this plan, "Depends on", the plan link.
- [ ] Close #169 and #33 as moot, each with a one-line comment linking the ruling.

---

## Part 1: Many boards at once (#158)

Depends on: nothing. Deploy: Python only (`git pull`, `uv sync`, tests); the crawl job picks it up on its next run.
Ask before the first live run.

### Task 1.1: Thread-safe polite client, per-host spacing on tenant hosts, new rates

**Files:**
- Modify: `outreach/crawl/polite.py`, `outreach/crawl/config.py`, `outreach/crawl/config.toml`
- Test: `tests/test_crawl_polite.py`, `tests/test_crawl_config.py`

**Interfaces:**
- Produces: `CrawlConfig.split_hosts: frozenset[str]` (bucket suffixes whose hosts are spaced one by one);
  `polite.spacing_key(host: str, cfg: CrawlConfig) -> str` (the host when its bucket is in `split_hosts`, else the
  bucket); a `PoliteClient` safe to share between threads.

- [ ] **Step 1: Write the failing tests**

```python
def test_split_host_spaced_per_host(self):
    # cfg: myworkdayjobs.com interval 1.0, in split_hosts. Fake clock and sleep record waits.
    polite.get("https://a.wd5.myworkdayjobs.com/x"); polite.get("https://b.wd5.myworkdayjobs.com/x")
    self.assertEqual(self.waits, [])            # two tenants do not wait on each other
    polite.get("https://a.wd5.myworkdayjobs.com/y")
    self.assertEqual(self.waits, [1.0])         # the same tenant waits its interval

def test_breaker_still_per_bucket_for_split_hosts(self):
    # 3 failures on tenant a, 2 on tenant b (breaker_failures = 5)
    self.assertTrue(polite.tripped("myworkdayjobs.com"))

def test_spacing_holds_under_threads(self):
    # real clock and sleep, one bucket at 0.05 s, 8 threads x 5 requests through one PoliteClient
    starts = sorted(fake_inner.start_times)
    self.assertTrue(all(b - a >= 0.045 for a, b in zip(starts, starts[1:])))
```

  In `test_crawl_config.py`: `test_rates` (`greenhouse.io` 0.05, `ashbyhq.com` 0.1, `lever.co` 1.0),
  `test_split_hosts_are_known_buckets` (an entry that is not a `host_interval_s` key fails at load).

- [ ] **Step 2: Run them and see them fail** (`uv run python -m unittest tests.test_crawl_polite
  tests.test_crawl_config`; expected: `AttributeError` on `split_hosts`, the old rates).
- [ ] **Step 3: Implement.** `config.toml`: `split_hosts = ["myworkdayjobs.com", "oraclecloud.com", "bamboohr.com",
  "breezy.hr", "teamtailor.com", "recruitee.com", "applytojob.com"]`; the three rates above; the rest unchanged.
  `PoliteClient._begin` keys `_next_start` by `spacing_key`, keeps `_failures` by bucket, and holds one
  `threading.Lock` only while it reads and writes those two dicts: reserve the start slot under the lock, sleep
  outside it.
- [ ] **Step 4: Run the tests and see them pass**, then every `tests/test_crawl_*.py`.
- [ ] **Step 5: Commit** `Crawler: per-host spacing on tenant hosts, faster Greenhouse and Ashby, Lever at 1 a second`.

### Task 1.2: An ETag adapter whose 200 has no ETag falls back to the id fingerprint

**Files:**
- Modify: `outreach/crawl/ats/_common.py` (`finish_list`)
- Test: `tests/test_crawl_ats_common.py`, `tests/test_crawl_ats_ashby.py`

**Interfaces:**
- Produces: `finish_list(..., use_etag=True)` returns `unchanged` when the 200 carries no `etag` header and the ids
  fingerprint equals the stored one; with an `etag` header a 200 is never `unchanged` (today's rule).

- [ ] **Step 1: Write the failing tests:** `test_etag_adapter_without_etag_header_falls_back_to_fingerprint`;
  `test_etag_adapter_with_etag_header_never_unchanged_on_200` (passes today, guards the rule);
  `test_big_board_without_etag_reads_unchanged_second_time` (an Ashby fixture served twice with no etag: `ok`, then
  `unchanged`).
- [ ] **Step 2: Run and see the first and third fail** (`ok` != `unchanged`).
- [ ] **Step 3: Implement:** in `finish_list`, treat `use_etag` as true only when the response has an `etag` header.
- [ ] **Step 4: Run and see them pass**, plus every `tests/test_crawl_ats_*.py`.
- [ ] **Step 5: Commit** `Crawler: a board with no ETag compares its job ids instead of re-reading everything`.

### Task 1.3: List boards on worker threads

**Files:**
- Create: `outreach/crawl/fanout.py`
- Modify: `outreach/crawl/config.py`, `outreach/crawl/config.toml` (`[workers]`)
- Test: `tests/test_crawl_fanout.py`

**Interfaces:**
- Consumes: `ingest.list_board(adapter, board, full=...) -> ReadResult`; `polite.HostTripped`.
- Produces:

```python
@dataclass(frozen=True)
class Listed:
    board: BoardRow
    adapter: Adapter | None      # None when no adapter reads this ATS
    view: object | None          # the per-board client the caller made (its first_bucket and requests)
    result: ReadResult | None    # None when the list request was refused
    tripped: str | None          # the bucket of a HostTripped, else None
    list_ms: int

def list_boards(boards: Sequence[BoardRow], start: Callable[[BoardRow], tuple[Adapter | None, object]],
                full: Callable[[BoardRow], bool], *, workers: Mapping[str, int] | None,
                should_stop: Callable[[], bool], clock: Callable[[], float]) -> Iterator[Listed]
```

  `workers=None` lists inline on the calling thread in board order (the serial mode every existing test keeps).
  Otherwise each ATS gets a queue in the given order and `workers.get(ats, workers["default"])` threads; results are
  yielded as they finish. A worker checks `should_stop()` before each board; a board never started is never
  yielded. `start(board)` and `full(board)` run on the calling thread before the board is queued (they may read the
  store). `CrawlConfig.workers: dict[str, int]` from `[workers]`: `default = 2`, `greenhouse = 6`, `ashby = 4`,
  `lever = 1`, `workable = 1`; every value 1 to 16, `default` required.

- [ ] **Step 1: Write the failing tests:** `test_inline_mode_keeps_board_order`; `test_every_board_yielded_once`
  (40 boards over 4 ATSs, threaded); `test_workers_per_ats_bounded` (a fake adapter counts in-flight calls per ATS
  under a lock; lever never above 1, greenhouse never above 6); `test_should_stop_starts_no_new_board`;
  `test_host_tripped_is_yielded_not_raised` (`tripped == "lever.co"`, `result is None`);
  `test_start_and_full_called_on_calling_thread` (records `threading.get_ident()`).
- [ ] **Step 2: Run and see them fail** (no module `outreach.crawl.fanout`).
- [ ] **Step 3: Implement** with `threading.Thread` workers, one `queue.Queue` per ATS for work and one shared queue
  for results; the generator drains results until every worker has finished, then joins them.
- [ ] **Step 4: Run and see them pass.**
- [ ] **Step 5: Commit** `Crawler: list boards on worker threads, one queue per ATS`.

### Task 1.4: The tick reads through the fan-out

**Files:**
- Modify: `outreach/crawl/tick.py` (`_Pass`, `_read_boards`, `run_tick`, `read_company_boards`),
  `outreach/crawl/__main__.py` (`tick` and `read` pass `workers=cfg.workers`)
- Test: `tests/test_crawl_tick.py`, `tests/test_crawl_cli.py`

**Interfaces:**
- Consumes: `fanout.list_boards`, `fanout.Listed` (Task 1.3).
- Produces: `run_tick(..., workers: Mapping[str, int] | None = None)` and the same keyword on
  `read_company_boards`; `None` keeps the serial path. `_Pass.read(board)` splits into `_Pass.start(board) ->
  tuple[Adapter | None, _TickClient]` and `_Pass.take(listed: Listed) -> None`; `take` holds exactly the logic `read`
  has after its `list_board` call (trip, held errors, held empties, ingest, profile). `_Pass.down` is written only
  on the writer thread; workers only test membership through `_TickClient._check`.

- [ ] **Step 1: Write the failing tests:**
  - `test_threaded_tick_matches_serial_outcome`: two scratch stores seeded alike (30 boards on greenhouse, lever,
    ashby, workday; a mix of ok, unchanged, empty and error answers); one serial `run_tick`, one with `workers`; the
    `board` rows (timestamps aside) and the open `posting` source keys are equal.
  - `test_slow_host_does_not_delay_other_hosts`: the fake client sleeps 0.3 s (real) on `api.lever.co`; with workers
    all 10 greenhouse boards are written before the third lever board.
  - `test_trip_stops_other_workers_on_that_bucket`: a 429 from greenhouse trips the bucket; the fake client records
    no greenhouse request after it; the shielded greenhouse boards are deferred, as serial does.
  - `test_stop_after_budget_starts_no_new_board_threaded`.
  - In `test_crawl_cli.py`: `test_tick_command_passes_configured_workers` (patch `run_tick`, assert `workers ==
    load_crawl_config().workers`).
- [ ] **Step 2: Run and see them fail** (`run_tick() got an unexpected keyword argument 'workers'`).
- [ ] **Step 3: Implement.** `_read_boards` takes `workers`, builds `select(now)` once, and loops over
  `fanout.list_boards(boards, run.start, lambda b: full_read_due(conn, b, now), workers=workers, should_stop=lambda:
  run.stopped or clock() - started >= cfg.tick_budget_s, clock=clock)`, calling `run.take`. Body fetches stay
  inside `read_board` on the writer thread.
- [ ] **Step 4: Run the tick, cadence, ingest and CLI tests**, then the full suite.
- [ ] **Step 5: Commit** `Crawler: the tick lists boards side by side and writes them on one thread`.

**Part 1 done when:** all tests green; in the live checkout (after Devanshu says go) one `python -m outreach.crawl
tick` finishes with no tripped bucket and `python -m outreach.crawl profile` shows list time per tick well under the
serial figure.

---

## Part 2: Every Greenhouse and Ashby company (from #160 and #161)

Depends on: Part 1 merged (the gate reads through the fan-out). Deploy: the migration (`uv run python -m
outreach.install`), then the seed by hand in the live checkout after Devanshu says go; the weekly part rides on the
existing 06:30 `run-due` job (no new launchd job).

### Task 2.1: The candidate table and the gate

**Files:**
- Create: `outreach/store/migrations/0008_board_candidate.sql`, `outreach/sources/ats_seed.py`
- Test: `tests/test_sources_ats_seed.py`, `tests/test_store_migrate.py`

**Interfaces:**
- Produces: table `board_candidate(candidate_id INTEGER PRIMARY KEY, ats TEXT NOT NULL, token TEXT NOT NULL, name
  TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', domain TEXT, source TEXT NOT NULL, source_ref TEXT NOT NULL
  DEFAULT '', priority INTEGER NOT NULL DEFAULT 0, found_at TEXT NOT NULL, gated_at TEXT, gate_result TEXT CHECK
  (gate_result IN ('promoted','no_us_engineering','dead','error','known')), open_us_engineering INTEGER, board_id
  INTEGER REFERENCES board(board_id), UNIQUE (ats, token)) STRICT` (the shape #160 planned, so #160 builds on it).

```python
SEED_ATS = ("greenhouse", "ashby")
RETRY_DAYS = 30
@dataclass(frozen=True)
class Gate:
    result: Literal["promoted", "no_us_engineering", "dead", "error"]
    open_us_engineering: int
    board_name: str
def gate(result: ReadResult, rules_cfg) -> Gate
```

  `gate` reads one list answer: `error` stays `error` (tried again on the next run, never a rejection); `empty` is
  `dead`; `ok` is `promoted` when at least one job is US engineering by the rules' own lane and US checks on its
  title, location and country (the functions `outreach.rules.decide` uses; no store write), else
  `no_us_engineering`.

- [ ] **Step 1: Write the failing tests:** `test_gate_promotes_board_with_us_engineering_posting`,
  `test_gate_rejects_board_with_only_non_us_engineering`, `test_gate_rejects_board_with_no_engineering`,
  `test_gate_error_is_error_not_rejection`, `test_gate_empty_is_dead`, `test_0008_creates_board_candidate`.
- [ ] **Step 2: Run and see them fail.** **Step 3: Implement.** **Step 4: Pass.**
- [ ] **Step 5: Commit** `Sources: a one-request gate for Greenhouse and Ashby boards`.

### Task 2.2: The seed run and the weekly top-up

**Files:**
- Modify: `outreach/sources/ats_seed.py`, `outreach/sources/__main__.py` (`seed-ats` command; `run-due` calls the
  weekly top-up), `outreach/sources/stapply.py` only if it needs a reader for one list's rows
- Test: `tests/test_sources_ats_seed.py`, `tests/test_sources_cli.py`

**Interfaces:**
- Consumes: the stapply folder `refresh` keeps (`OUTREACH_SOURCES_DIR`), `fanout.list_boards` and the crawler's
  `PoliteClient` and adapters (Part 1), the board-adding path freehire uses (`outreach/sources/freehire.py`: one
  company per domain through the existing identity code, `boards.add_board`).
- Produces: `run_seed(conn, client, *, folder: Path, now: str, ats: Sequence[str] = SEED_ATS, limit: int | None =
  None, dry_run: bool = False, workers: Mapping[str, int] | None = None) -> SeedReport(read: int, promoted: int,
  rejected: int, dead: int, errors: int, known: int, left: int)`. Rows are taken from the Greenhouse and Ashby lists
  only (never `wellfound` or `ycombinator`). A row whose board the store already has is `known` with no request. A
  row gated in the last `RETRY_DAYS` with a result other than `error` is skipped. A promoted row adds its company
  and board (`found_by = 'stapply_name'`) and links `board_candidate.board_id`; the new board has no `next_read_at`,
  so the next tick or Check now reads it in full. `python -m outreach.sources seed-ats [--ats greenhouse|ashby]
  [--limit N] [--dry-run]` runs it and prints the report; it runs only in the live checkout (like every sources
  command that opens the store through `connect()`). `run-due` runs it with `limit` from config once a week.

- [ ] **Step 1: Write the failing tests:** `test_known_board_skipped_without_request`,
  `test_failed_gate_retried_after_30_days` (29 days: skipped; 31 days: gated again), `test_error_gate_retried_next_run`,
  `test_excluded_lists_never_read`, `test_promoted_board_joins_existing_company_by_domain`,
  `test_promoted_board_is_read_on_next_tick`, `test_dry_run_writes_nothing`, `test_run_due_seeds_once_a_week`,
  `test_seed_ats_command_refuses_outside_live_checkout` (with the default store path).
- [ ] **Step 2: Fail. Step 3: Implement. Step 4: Pass**, then the sources suite.
- [ ] **Step 5: Commit** `Sources: every Greenhouse and Ashby company with an open US engineering role`.

**Part 2 done when:** tests green; in the live checkout, `seed-ats --dry-run` shows the row counts, Devanshu says
go, `seed-ats` finishes with no tripped bucket, and the report's `promoted` count is told to him (it sets how long a
Check now takes).

---

## Part 3: Sorted by when the job was posted

Depends on: Part 2 merged (so the seed's first reads already get list times). Deploy: the migration, kickstart the
API, `pnpm build`.

### Task 3.1: Migration and the list time helpers

**Files:**
- Create: `outreach/store/migrations/0009_list_time.sql`, `outreach/crawl/row_clock.py`
- Test: `tests/test_crawl_row_clock.py`, `tests/test_store_migrate.py`

**Interfaces:**
- Produces: columns `posting.posted_at TEXT` (canonical UTC; set once, at insert, from a trustworthy board time),
  `posting.seen_gap_s INTEGER` (seconds since the board's last good read; NULL on a board's first read and for rows
  from before this migration), `posting.repost_of INTEGER REFERENCES posting(posting_id)`, `posting.list_at TEXT`
  (the time the list sorts by; NULL means Date unknown), `board.last_good_read_at TEXT`; index
  `posting_list_at ON posting(list_at) WHERE closed_at IS NULL`. Backfill: `list_at = COALESCE(posted_on ||
  'T05:00:00Z', first_seen_at)`; `board.last_good_read_at = last_read_at` where `last_read IN
  ('ok','unchanged','empty')`, else the newest non-error `board_read.at`.

```python
REPOST_DAYS = 120
GAP_TAG_S = 7200
DATE_TRUSTED = frozenset({"workable", "workday", "oracle_orc", "breezy", "teamtailor", "recruitee", "rippling",
                          "bamboohr", "jazzhr", "smartrecruiters", "pinpoint"})   # date-only posted_on is honest
def list_at(ats: str, first_seen_at: str, posted_at: str | None, posted_on: str | None,
            seen_gap_s: int | None) -> str | None
def repost_key(title: str) -> str
def find_repost(conn, company_id: int, title: str, now: str) -> int | None
```

  `list_at`: `posted_at` when set, capped at `first_seen_at`; else, for an ATS in `DATE_TRUSTED`, `posted_on` at
  00:00 New York in UTC, capped at `first_seen_at`; else `first_seen_at` when `seen_gap_s` is not None; else None.
  (Greenhouse and Ashby give `posted_at`; Lever's `posted_on` comes from `createdAt` and is not trusted.)
  `repost_key`: lower case, every run of non-alphanumerics to one space, trimmed. `find_repost`: the newest posting
  at the same company (merged companies included, through `ranking.common.canonical_ids`) with the same
  `repost_key`, closed within `REPOST_DAYS` of `now`.

- [ ] **Step 1: Write the failing tests:** `test_list_at_uses_posted_at`, `test_list_at_caps_posted_at_at_first_seen`,
  `test_list_at_date_only_is_ny_midnight` (`list_at("workday", "2026-11-09T15:00:00Z", None, "2026-11-09", None) ==
  "2026-11-09T05:00:00Z"`; `"2026-07-01"` gives `"2026-07-01T04:00:00Z"`), `test_list_at_lever_date_not_trusted`
  (gap 300: `first_seen_at`), `test_list_at_first_read_no_time_is_none`,
  `test_repost_key_ignores_case_and_punctuation` (`"Software Engineer, New Grad (2026)"` matches `"software engineer
  new grad 2026"`), `test_find_repost_within_120_days`, `test_find_repost_ignores_121_days`,
  `test_find_repost_other_company_ignored`, `test_find_repost_open_posting_ignored`,
  `test_0009_backfills_list_at_and_last_good_read`.
- [ ] **Step 2: Fail. Step 3: Implement. Step 4: Pass.**
- [ ] **Step 5: Commit** `Postings: a list time from the board's posting time`.

### Task 3.2: Reads record the posting time, the seen gap and reposts

**Files:**
- Modify: `outreach/crawl/base.py` (`JobRef.posted_at: str | None = None`), `outreach/crawl/ats/greenhouse.py`
  (`posted_at` from `first_published` only, in UTC, never from `updated_at`), `outreach/crawl/ats/ashby.py`
  (`posted_at` from `publishedAt`, in UTC), `outreach/crawl/postings.py` (`write_postings(..., seen_gap_s: int | None
  = None)` writes `posted_at`, `seen_gap_s`, `list_at`, `repost_of` on insert only, never on a change),
  `outreach/crawl/ingest.py`, `outreach/crawl/boardstate.py` (`last_good_read_at = now` on `ok`, `unchanged` and
  `empty`; `BoardRow.last_good_read_at`)
- Test: `tests/test_crawl_ats_greenhouse.py`, `tests/test_crawl_ats_ashby.py`, `tests/test_crawl_postings.py`,
  `tests/test_crawl_ingest.py`

**Interfaces:**
- Consumes: `row_clock.*` (Task 3.1).
- Produces: every new posting row has `seen_gap_s` (None when the board has no `last_good_read_at`, else whole seconds
  from it to `now`), `posted_at`, `list_at`, and `repost_of` (looked up only when `seen_gap_s` is not None).

- [ ] **Step 1: Write the failing tests:** `test_posted_at_from_first_published_in_utc`
  (`"2026-11-09T10:38:00-05:00"` gives `"2026-11-09T15:38:00Z"`), `test_updated_at_is_never_posted_at`,
  `test_ashby_posted_at_from_published_at`, `test_ashby_republish_does_not_move_known_job` (second read with a new
  `publishedAt` for the same id: `posted_at` and `list_at` unchanged), `test_seen_gap_is_time_since_last_good_read`,
  `test_error_read_does_not_move_last_good_read`, `test_first_read_rows_sort_by_their_posting_time` (a March posting
  on a first read has a March `list_at`), `test_first_read_rows_never_new` (`seen_gap_s IS NULL` on all 300),
  `test_repost_links_closed_posting`.
- [ ] **Step 2: Fail. Step 3: Implement. Step 4: Pass**, then the crawl suite.
- [ ] **Step 5: Commit** `Crawler: every new posting keeps the time its board says it went up`.

### Task 3.3: The job list sorts by list time

**Files:**
- Modify: `outreach/pipeline/joblist.py` (`query`, `JobRow`), `outreach/api/models/jobs.py` (`JobRowWire`),
  `outreach/api/queries/jobs_list.py`
- Test: `tests/test_pipeline_joblist.py`, `tests/test_api_jobs.py`

**Interfaces:**
- Produces: `JobRowWire` gains `list_at: str | None`, `posted_at: str | None`, `first_seen_at: str`, `seen_gap_s: int
  | None`, `first_read: bool` (`seen_gap_s IS NULL`), `reposted_from: str | None` (the old posting's
  `first_seen_at`). `date`, `date_kind` and `age_days` stay until Task 3.5 moves the web off them, so every commit
  compiles. Order: `list_at` newest first, then the size rank, then posting id; rows with `list_at IS NULL` after
  every dated row, in every window, newest `first_seen_at` first. Cursor `(list_at, size_rank, posting_id)` with the
  NULL group encoded as its own segment. Windows filter on `list_at`; Date unknown rows are never filtered out.

- [ ] **Step 1: Write the failing tests:** `test_sorts_by_list_at_to_the_minute` (10:42 before 10:38 the same day,
  across a Greenhouse and an Ashby board), `test_unknown_group_after_dated_rows`,
  `test_unknown_group_shows_in_7_day_window`, `test_window_filters_on_list_at`,
  `test_repost_row_carries_old_first_seen`, `test_cursor_pages_across_unknown_group`; in `test_api_jobs.py`:
  `test_jobs_row_has_time_fields`.
- [ ] **Step 2: Fail. Step 3: Implement**, regenerate the OpenAPI schema and `web/src/api/schema.ts`. **Step 4: Pass**
  (`cd web && pnpm check:api`).
- [ ] **Step 5: Commit** `Job list: newest first by when each job was posted`.

### Task 3.4: Alerts never for a repost or a first read

**Files:**
- Modify: `outreach/pipeline/alerts.py` (`_todays` keeps rows whose `list_at` is today in New York and that are not a
  repost and not a first read; the 15-minute gap and quiet hours unchanged)
- Test: `tests/test_pipeline_alerts.py`

- [ ] **Step 1: Failing tests:** `test_first_read_never_alerts`, `test_repost_never_alerts`,
  `test_job_posted_today_on_known_board_alerts`. **Step 2: Fail. Step 3: Implement. Step 4: Pass.**
- [ ] **Step 5: Commit** `Alerts: never for a repost or a newly added board's jobs`.

### Task 3.5: The row shows its time

**Files:**
- Modify: `web/src/routes/browse/index.tsx` and its row component, `web/src/lib/format.ts` (a `postedLabel`
  helper), the jobs fixtures in `web/src/api/`; then `outreach/api/models/jobs.py` drops `date`, `date_kind`,
  `age_days`
- Test: `web/src/routes/browse/browse.test.tsx`

**Interfaces:**
- Produces: each row's time cell: "posted 10:38 · 6 min ago" (today, a board time), "posted Nov 8" (a date-only
  board), "first seen 10:42" (no board time), a "found after a 3 day gap" tag when `seenGapS > 7200`, a "reposted,
  first seen Oct 2" tag when `repostedFrom` is set, and Date unknown rows under a "Date unknown" heading at the end.

- [ ] **Step 1: Run the `craft` skill for the row and its tags; settle copy and layout before code.**
- [ ] **Step 2: Failing tests:** `shows the posted time to the minute`, `shows first seen when the board gives no
  time`, `tags a late find with its gap`, `tags a repost with its old date`, `groups rows without a date at the end`.
- [ ] **Step 3: Fail. Step 4: Implement**, then drop the three old fields and regenerate the schema. **Step 5: Pass**
  (`pnpm typecheck && pnpm lint && pnpm test && pnpm check:api`, and the Python suite).
- [ ] **Step 6: Commit** `Jobs: each row shows when it was posted`.

**Part 3 done when:** tests green, the craft gate passed, and in the live checkout the top of the list is the most
recently posted Greenhouse and Ashby jobs at his level, each with its posting time.

---

## Part 4: Check now and the self-updating list

Depends on: Part 3 merged. Deploy: kickstart the API, `pnpm build`. Ask before the first live Check now (Devanshu
clicks it; Claude never does).

### Task 4.1: The Check now route

**Files:**
- Create: `outreach/api/services/check_now.py`, `outreach/crawl/kick.py`, `outreach/pipeline/check_state.py`
- Modify: `outreach/api/routers/jobs.py`, `outreach/api/models/jobs.py`, `outreach/api/app.py` (the kicker on
  `app.state`, replaceable in tests)
- Test: `tests/test_api_jobs_check.py`

**Interfaces:**
- Produces:
  - `POST /api/jobs/check-now` through `run_write`: appends `event(entity='source', kind='check_now_requested',
    actor='devanshu')` with the page's idempotency key, then (after the commit) calls `app.state.kick_crawl()`.
    Returns `CheckNowResult(requested_at: str, state: Literal["started", "running", "not_live"])`. When a check is
    queued or running (`check_state.status`), it records nothing and kicks nothing: `state="running"`.
  - `GET /api/jobs/check` returns `CheckStatus(state: Literal["idle", "queued", "running", "done"], checked: int,
    total: int, new: int, started_at: str | None, finished_at: str | None, paused_hosts: list[str])`.
  - `check_state.PROGRESS_KEY = "check_progress"`: the `pipeline_meta` row the crawler writes (Task 4.2) as JSON
    `{run_id, checked, total, new, started_at, finished_at, paused_hosts}`. `check_state.pending_request(conn) -> str |
    None`: the newest `check_now_requested` time newer than the last finished check, else None.
    `check_state.status(conn) -> CheckStatus`: queued when a request is pending and no check runs, running while the
    progress row has no `finished_at`, done when it has one, idle when nothing was ever asked.
  - `kick.kick_crawl() -> str`: outside the live checkout (`live_checkout.is_live()` false) returns `"not_live"` and
    runs nothing; inside, runs `launchctl kickstart gui/<uid>/com.devanshu.outreach-crawl` (no `-k`) and returns
    `"started"`.

- [ ] **Step 1: Failing tests:** `test_check_now_records_request_and_kicks` (a fake kicker counts calls),
  `test_check_now_twice_kicks_once`, `test_check_now_outside_live_checkout_never_kicks` (the real `kick_crawl` with
  `is_live` false never calls `subprocess`), `test_check_status_idle_when_never_requested`,
  `test_check_status_reads_progress`.
- [ ] **Step 2: Fail. Step 3: Implement. Step 4: Pass.**
- [ ] **Step 5: Commit** `API: Check now records a request and starts the crawl job`.

### Task 4.2: The crawl job runs the check

**Files:**
- Create: `outreach/crawl/check_all.py`
- Modify: `outreach/crawl/tick.py` (`run_tick` runs a pending check before its normal pass, and once more after it
  when a request came in meanwhile), `outreach/crawl/config.toml` (`check_budget_s = 900`,
  `check_skip_recent_s = 60`, `check_ats = ["greenhouse", "ashby"]`)
- Test: `tests/test_crawl_check_all.py`

**Interfaces:**
- Consumes: `check_state.pending_request`, `check_state.PROGRESS_KEY` (Task 4.1); `fanout.list_boards`, `_Pass`,
  `_read_boards` (Part 1).
- Produces: `select_check(conn, now, cfg) -> list[BoardRow]`: every active board on a `check_ats` ATS of a live
  company, not read in the last `check_skip_recent_s`, not inside an error backoff, never-read first, then
  `last_read_at` oldest first; then the boards `due_boards` returns for the other ATSs. The check is a `read_boards`
  job run whose summary has `"mode": "check_all"` and `"requested_at"` (no change to the `job_run.job` CHECK).
  Progress goes to the `PROGRESS_KEY` row at most once a second and once more with `finished_at`; a bucket tripped
  during the check is listed in `paused_hosts` by its ATS name.

- [ ] **Step 1: Failing tests:** `test_check_reads_every_greenhouse_and_ashby_board`,
  `test_check_skips_board_read_within_60s`, `test_check_skips_boards_in_error_backoff`,
  `test_check_includes_due_boards_of_other_ats`, `test_tick_runs_check_when_requested`,
  `test_request_during_tick_runs_after_it`, `test_no_request_no_check`, `test_progress_written_and_final`,
  `test_check_reports_tripped_host`.
- [ ] **Step 2: Fail. Step 3: Implement. Step 4: Pass.**
- [ ] **Step 5: Commit** `Crawler: Check now reads every Greenhouse and Ashby board`.

### Task 4.3: New postings move the pulse

**Files:**
- Modify: `outreach/crawl/tick.py` (`_Pass._ingest`: when `out.new > 0`, append `event(entity='board',
  kind='postings_new', entity_id=board_id, detail={"new": n, "run_id": run_id},
  idempotency_key=f"postings-new:{board_id}:{run_id}")`), `web/src/shell/use-pulse.ts` (`PULSE_REFETCHES` gains the
  jobs and check status keys)
- Test: `tests/test_crawl_tick.py`, `web/src/shell/use-pulse.test.ts`

- [ ] **Step 1: Failing tests:** `test_new_postings_event_moves_pulse` (the pulse revision grows after a read that
  found a new posting, not after an unchanged one); `refetches the job list when the pulse moves`.
- [ ] **Step 2: Fail. Step 3: Implement. Step 4: Pass. Step 5: Commit** `Jobs: a new posting reaches an open page within seconds`.

### Task 4.4: The page: Check now, the progress line, "N new"

**Files:**
- Create: `web/src/routes/browse/check-now.tsx`, `web/src/routes/browse/new-rows.ts`
- Modify: `web/src/routes/browse/index.tsx`, `web/src/api/` (client methods `checkNow`, `checkStatus`, keys,
  fixtures)
- Test: `web/src/routes/browse/check-now.test.tsx`, `web/src/routes/browse/new-rows.test.ts`

**Interfaces:**
- Produces: `heldRows(shown: JobRow[], incoming: JobRow[], atTop: boolean, rowOpen: boolean) -> { rows: JobRow[];
  waiting: number }` (at the top with no row open, incoming rows show at once; otherwise the shown order stays and
  `waiting` counts incoming new rows not shown). The last time he opened the list lives in `localStorage`
  `knock.jobs.lastOpened`, written when the page mounts and when he clicks "N new". A row is new when
  `firstSeenAt > lastOpened`, it is not a first read and not a repost.

- [ ] **Step 1: Run the `craft` skill** for the button, the progress line ("Checked 1,240 of 4,013 boards, 3 new";
  done: "Checked 4,013 boards in 3 min 10 s. 6 new."; "Greenhouse paused: too many requests"), the "N new" button,
  the new marker, and a free key for Check now.
- [ ] **Step 2: Failing tests:** `shows progress while a check runs`, `shows the done line with the count`,
  `a second click while running shows the running check`, `shows a paused host`, `holds new rows while scrolled`,
  `shows new rows at once at the top`, `clicking N new reveals and scrolls to top`, `marks rows first seen since the
  last visit as new`.
- [ ] **Step 3: Fail. Step 4: Implement** (poll `GET /api/jobs/check` every second only while queued or running).
  **Step 5: Pass.**
- [ ] **Step 6: e2e on the workspace port** (`WEB_PORT=$CONDUCTOR_PORT pnpm e2e`): Check now by keyboard shows the
  progress line against the fixture API.
- [ ] **Step 7: Commit** `Jobs: Check now and new rows that never jump under the cursor`.

**Part 4 done when:** tests and e2e green, the craft gate passed, and in the live checkout Devanshu's Check now reads
every Greenhouse and Ashby board in a few minutes with no tripped bucket.

---

## Part 5: Today goes away, Done replaces Go ahead

Depends on: Part 4 merged (same page). Deploy: kickstart the API, `pnpm build`; the plan job stays installed and
prints one line.

### Task 5.1: The plan job off

**Files:**
- Modify: `outreach/plan/config.toml` (`[plan] enabled = false`), `outreach/plan/__main__.py` (`build` prints "plan
  is off (first to see ruling, #9)" and exits 0 when off, touching nothing), `outreach/plan/config.py` if it
  validates keys
- Test: the plan CLI test module

- [ ] **Step 1: Failing test** `test_plan_build_off_prints_one_line_and_exits` (exit 0, no `daily_list_entry` row,
  no `job_run` row). **Step 2: Fail. Step 3: Implement. Step 4: Pass. Step 5: Commit** `Plan: the 08:00 plan is off`.

### Task 5.2: Done, his reached mark

**Files:**
- Create: `outreach/pipeline/reached.py`
- Modify: `outreach/api/services/jobs.py` (`go_ahead` goes; `done` and `clear_done` come),
  `outreach/api/routers/jobs.py`, `outreach/api/routers/company_writes.py`, `outreach/api/models/jobs.py`,
  `outreach/pipeline/joblist.py` (`contacted_on` includes marks)
- Test: `tests/test_pipeline_reached.py`, `tests/test_api_jobs_writes.py`, `tests/test_pipeline_joblist.py`

**Interfaces:**
- Produces: `reached.marks(conn) -> dict[int, str]` (canonical company id to the New York date of his newest mark
  not cleared after it); events `entity='company'`, kinds `company_reached` and `company_reached_cleared`, actor
  `devanshu`. `POST /api/jobs/{postingId}/done` returns `DoneResult(posting_id, company_id, reached_on: str, text:
  str)` (key `job-done:<id>`); `POST /api/companies/{companyId}/reached/clear` returns `ClearDoneResult(company_id,
  cleared: bool)`. `POST /api/jobs/{postingId}/go-ahead` is removed.

- [ ] **Step 1: Failing tests:** `test_done_records_reached_mark`, `test_done_twice_is_one_mark`,
  `test_clear_mark_removes_reached`, `test_mark_after_clear_counts_again`, `test_go_ahead_route_is_gone`,
  `test_reached_mark_sets_contacted_on`, `test_not_contacted_filter_hides_marked`,
  `test_done_on_no_go_company_still_records`.
- [ ] **Step 2: Fail. Step 3: Implement**, regenerate the API schema. **Step 4: Pass. Step 5: Commit**
  `Jobs: Done marks a company as reached`.

### Task 5.3: Remove Today, Proposals and Email today from the web app

**Files:**
- Delete: `web/src/routes/today/`, `web/src/routes/proposals/`, `web/src/routes/pipeline/email-today.tsx`, and their
  tests and fixtures that nothing else uses
- Modify: `web/src/app/routes.tsx` (index and `*` go to `/browse`; `today` and `proposals` redirect to `/browse`),
  `web/src/shell/app-shell.tsx` (`VIEW_TITLE`, `GO` without `t`), `web/src/shell/app-sidebar.tsx` (Jobs first, no
  Today), `web/src/routes/pipeline/index.tsx`, `web/src/routes/browse/index.tsx` (the `a` key and button become Done
  through the shared undo window; a "reached Nov 9" tag on every row of a reached company; the one-line "Today: N
  sent, N replies" summary under the header, from the existing shell or progress data)
- Test: the routes and shell tests, `web/src/routes/browse/browse.test.tsx`, `web/src/shell/app-sidebar.test.tsx`

- [ ] **Step 1: Run the `craft` skill** for the Done key, the reached tag and the header summary.
- [ ] **Step 2: Failing tests:** `root opens Jobs`, `today and proposals redirect to Jobs`, `sidebar has no Today and
  lists Jobs first`, `Done marks the company and every row of it shows reached`, `Done can be undone in the undo
  window`, `header shows today's outreach line linking to Progress`.
- [ ] **Step 3: Fail. Step 4: Implement. Step 5: Pass** (`pnpm typecheck && pnpm lint && pnpm test`, then e2e on the
  workspace port).
- [ ] **Step 6: Commit** `Knock opens on Jobs; Today, Proposals and Email today are gone`.

### Task 5.4: Nothing on the outreach side moved

**Files:**
- Test only: `tests/test_api_needs_you.py`, the sending tests, `tests/test_api_progress.py`

- [ ] **Step 1: Pin today's behavior with no daily list:**
  `test_needs_you_lists_emailed_and_reply_companies_without_a_plan`, `test_claim_context_is_own_without_a_plan`,
  `test_progress_counts_unchanged_without_a_plan`. If one fails, fix the code that read the daily list, not the test.
- [ ] **Step 2: Commit** `Tests: the outreach side works with no daily plan`.

**Part 5 done when:** tests and e2e green, the craft gate passed, `python -m outreach.plan build` prints the one line
in the live checkout, and Knock opens on Jobs.

---

## Order and merge

| Order | Part | Issue | Depends on |
|---|---|---|---|
| 0 | #9 ruling, issues | #9 | nothing |
| 1 | Many boards at once | #158 | nothing |
| 2 | Every Greenhouse and Ashby company | new (from #160, #161) | Part 1 |
| 3 | Sorted by when the job was posted | new | Part 2 |
| 4 | Check now, self-updating list | new | Part 3 |
| 5 | Today goes away, Done | new | Part 4 |

One open PR at a time, merged in this order. He has the full Greenhouse and Ashby coverage after Part 2, the
sorted list after Part 3, the button after Part 4, and the finished product after Part 5.

**The week after Part 4 (spec section 7):** read from the live store, report to Devanshu, change nothing: Check now
wall times from the `check_all` runs; of the Greenhouse and Ashby postings at his level that Simplify listed that
week, how many Knock already had (each miss through the miss log with its cause); any bucket tripped by a 429 or
403; any row shown as new that was a repost or an old job from a newly added board.

## Risks

- **Threads and the tick's outage rules.** `test_threaded_tick_matches_serial_outcome` and the trip test guard them;
  the serial path stays the default for every old test, so a regression shows as a difference between the two.
- **Greenhouse or Ashby minding the rates.** The breaker trips at once on a 429, the check says which host paused,
  and the rates are config. The research marks their limits unconfirmed: watch the seed and the first checks.
- **A bigger store.** The seed may add a few thousand boards and their postings. The gate keeps out boards with no
  US engineering role, and `big_board` already slows boards that yield little.
- **Check now length grows with coverage.** Thousands of boards at 20 and 10 a second is a few minutes. If it feels
  long, the progress line shows the new rows as they land, so he can start reading before it ends.
