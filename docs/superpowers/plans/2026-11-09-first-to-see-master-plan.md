# First to See: Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> One part is one issue, one branch from the current `origin/master`, one PR into `master` (CLAUDE.md, Branching).

**Goal:** Devanshu sees every new posting at his level within 5 minutes of a company putting it up, on a job list
that is Knock's home page, and Knock stops choosing companies for him.

**Architecture:** The crawler's list requests run on worker threads per ATS while one thread writes the store, so
boards are read side by side instead of one after another. Boards get a tier (hot, warm, cold) that caps their
read interval at 5 minutes, 1 hour, or today's cadence, and the crawl job runs every 60 seconds. Each new posting
stores how long Knock had not been looking (its seen gap), which decides whether it is fresh, found late, a first
read or a repost, and the time it sorts by. Check now records a request and kicks the crawl job, which checks every
board. Today, Proposals and Email today go; Go ahead becomes Done, his mark that a company has been reached.

**Tech Stack:** Python 3.14 (Homebrew), SQLite (WAL), FastAPI, `threading` and `queue` from the standard library,
React 19 + Vite + TanStack Query + shadcn/ui, vitest, Playwright, launchd.

**Spec:** `docs/superpowers/specs/2026-11-09-first-to-see-jobs-design.md`. Evidence:
`docs/research/2026-11-09-first-to-see-new-postings.md`. Decisions: issue #9 (the ruling in the spec's section 10
goes into #9 first, Part 0).

## Global Constraints

- #9 wins over every doc. Edit #9 before code that changes a decision (Part 0).
- Python is `/opt/homebrew/bin/python3` (3.14). Tests: `uv run python -m unittest discover -s tests -t .` from the
  repo root. Web: `cd web && pnpm typecheck && pnpm lint && pnpm test`, and `pnpm check:api` after any API model
  change (regenerate with `uv run python -m outreach.api.openapi` then `pnpm gen:api`).
- No new dependency. Threads from the standard library; the existing `outreach.net` client; no httpx in the crawler.
- Tests never send a real request and never open the live store (`tests/__init__.py` scratch settings). Every
  network test uses a fake `HttpClient` (see `tests/fakes.py` and `test_crawl_tick.py`).
- Test first: write the test, watch it fail for the right reason, then make it pass. Every bug fix starts with a
  test that reproduces it.
- One writer: worker threads only send list requests; every store write happens on the thread that owns the
  connection.
- Politeness: per-host spacing never drops below `[host_interval_s]`; Lever is 1.0 s (`Crawl-delay: 1`); the
  breaker, outage shield, host block item and `[ats_off]` keep their keys and behavior.
- Claude never calls an API write route (Check now, Done, Pass included), never runs the crawler outside the live
  checkout, and asks Devanshu before the first live run of Parts 1, 2 and 4 (memory: heavy runs on his laptop).
- Plain words, no em dashes or en dashes in anything a person reads: UI copy, docs, commit messages, PR bodies.
- Every UI change goes through the `craft` skill and its gate before UI code.
- Numbers from the spec, verbatim: hot 300 s; hot Lever 600 s; hot Workday and Oracle 1800 s until Part 6; warm
  3600 s; fresh when the seen gap is at most 7200 s; repost window 120 days; Check now skips a board read in the
  last 60 s; alert gap 5 minutes; quiet 22:00 to 08:00 New York; crawl job every 60 s.

## Review Focus

These five conditions are implied by the spec and no happy-path test covers them. Each has a test in the task that
owns the code (named below).

1. **The Mac wakes after a night asleep.** Hundreds of hot boards are overdue at once and every posting found then
   went up sometime in the night. Expected: hot boards read first, nothing found marked fresh, no alert flood.
   Tests: `test_wake_after_8_hours_marks_finds_late` (Task 3.2), `test_hot_boards_first` (Task 2.3).
2. **A host starts failing while workers are mid-flight.** Workers on that ATS keep sending. Expected: once the
   bucket trips, no worker sends to it again in that tick, and its shielded boards are deferred, exactly as in a
   serial tick. Test: `test_trip_stops_other_workers_on_that_bucket` (Task 1.4).
3. **A brand-new board with 300 postings is read for the first time.** Expected: none land at the top of the list,
   none are new, no alert; dated ones sort by their date, the rest go to Date unknown. Tests:
   `test_first_read_rows_never_fresh` (Task 3.2), `test_first_read_never_alerts` (Task 3.4).
4. **Check now is clicked twice, or while the minute tick is running.** Expected: one check-every-board pass, the
   second click shows the running one, a request made during a tick runs right after it. Tests:
   `test_check_now_twice_kicks_once` (Task 4.1), `test_request_during_tick_runs_after_it` (Task 4.2).
5. **The list is scrolled or a row is open when new rows arrive.** Expected: nothing moves under his cursor; a "N
   new" button appears. Test: `holds new rows while scrolled` (Task 4.4).

---

## Part 0: #9 ruling and issues (no code)

Ask Devanshu before posting anything to GitHub.

- [ ] Add the spec's section 10 ruling to #9 as a new bullet at the end of its body.
- [ ] Reshape #158 into Parts 1 and 2 (edit its body: link this plan, the new intervals, one writer thread), and
  note on #155 that Part 3 takes `repost_of` and the 120-day window, the rest stays in #155.
- [ ] Open issues for Part 3 (row clock), Part 4 (Check now and the self-updating list), Part 5 (Today goes away,
  Done replaces Go ahead), Part 6 (Workday and Oracle newest first; link #157 for the Workday delta read). Each
  issue body: what, "Done when" from this plan, "Depends on", the plan link.
- [ ] Close #169 and #33 as moot (Today is gone), with a one-line comment linking the ruling.

---

## Part 1: Many boards at once (#158, first half)

Depends on: nothing. Deploy: Python only, so `git pull`, `uv sync`, tests, then the crawl job picks it up on its next
run (no kickstart needed; it starts fresh each run). Ask before the first live run.

### Task 1.1: Thread-safe polite client, per-host spacing on tenant hosts, new rates

**Files:**
- Modify: `outreach/crawl/polite.py`, `outreach/crawl/config.py`, `outreach/crawl/config.toml`
- Test: `tests/test_crawl_polite.py`, `tests/test_crawl_config.py`

**Interfaces:**
- Produces: `CrawlConfig.split_hosts: frozenset[str]` (bucket suffixes whose hosts are spaced one by one);
  `polite.spacing_key(host: str, cfg: CrawlConfig) -> str` (the host itself when its bucket is in `split_hosts`, else
  the bucket); `PoliteClient` safe to share between threads.

- [ ] **Step 1: Write the failing tests**

```python
def test_split_host_spaced_per_host(self):
    # cfg: myworkdayjobs.com interval 1.0, in split_hosts. Fake clock and sleep record waits.
    polite.get("https://a.wd5.myworkdayjobs.com/x"); polite.get("https://b.wd5.myworkdayjobs.com/x")
    self.assertEqual(self.waits, [])            # two tenants do not wait on each other
    polite.get("https://a.wd5.myworkdayjobs.com/y")
    self.assertEqual(self.waits, [1.0])         # the same tenant waits its interval

def test_breaker_still_per_bucket_for_split_hosts(self):
    # 3 failures on tenant a and 2 on tenant b (breaker_failures = 5)
    self.assertTrue(polite.tripped("myworkdayjobs.com"))

def test_spacing_holds_under_threads(self):
    # real clock and sleep, one bucket at 0.05 s, 8 threads x 5 requests through one PoliteClient
    starts = sorted(fake_inner.start_times)
    self.assertTrue(all(b - a >= 0.045 for a, b in zip(starts, starts[1:])))
```

In `test_crawl_config.py`: `test_lever_is_one_second` (`cfg.host_interval_s["lever.co"] == 1.0`),
`test_split_hosts_are_known_buckets` (every `split_hosts` entry is a key of `host_interval_s`; an unknown one fails
at load).

- [ ] **Step 2: Run them and see them fail** (`uv run python -m unittest tests.test_crawl_polite tests.test_crawl_config`;
  expected: `AttributeError` on `split_hosts`, Lever 0.25 != 1.0).
- [ ] **Step 3: Implement.** `config.toml`: `split_hosts = ["myworkdayjobs.com", "oraclecloud.com", "bamboohr.com",
  "breezy.hr", "teamtailor.com", "recruitee.com", "applytojob.com"]`; `[host_interval_s]` `greenhouse.io = 0.05`,
  `ashbyhq.com = 0.1`, `lever.co = 1.0`, the rest unchanged. `PoliteClient._begin` keys `_next_start` by
  `spacing_key`, keeps `_failures` by bucket, and holds one `threading.Lock` only around reading and writing those
  two dicts (never around `sleep` or the request): reserve the start slot under the lock, sleep outside it.
- [ ] **Step 4: Run the tests and see them pass**, then the whole crawl suite (`uv run python -m unittest discover
  -s tests -t . -p 'test_crawl_*'`).
- [ ] **Step 5: Commit** `Crawler: per-host spacing on tenant hosts, Lever at 1 a second, a thread-safe polite client`.

### Task 1.2: An ETag adapter whose 200 has no ETag falls back to the id fingerprint

**Files:**
- Modify: `outreach/crawl/ats/_common.py` (`finish_list`)
- Test: `tests/test_crawl_ats_common.py`, `tests/test_crawl_ats_ashby.py`

**Interfaces:**
- Produces: `finish_list(..., use_etag=True)` returns `unchanged` when the 200 carries no `etag` header and the ids
  fingerprint equals the stored one; with an `etag` header a 200 is never `unchanged` (today's rule).

- [ ] **Step 1: Write the failing tests:** `test_etag_adapter_without_etag_header_falls_back_to_fingerprint` (same ids
  as `Validators(etag="", ids_fingerprint=fp)`, no etag header: `result == "unchanged"`);
  `test_etag_adapter_with_etag_header_never_unchanged_on_200` (passes today, guards the rule);
  `test_big_board_without_etag_reads_unchanged_second_time` (Ashby fixture served twice with no etag: first `ok`,
  second `unchanged`).
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
    view: object | None          # the per-board client the tick made (its first_bucket and requests)
    result: ReadResult | None    # None when the list request was refused
    tripped: str | None          # the bucket of a HostTripped, else None
    list_ms: int

def list_boards(boards: Sequence[BoardRow], start: Callable[[BoardRow], tuple[Adapter | None, object]],
                full: Callable[[BoardRow], bool], *, workers: Mapping[str, int] | None,
                should_stop: Callable[[], bool], clock: Callable[[], float]) -> Iterator[Listed]
```

  `workers=None` lists inline on the calling thread in board order (the serial mode every existing test uses).
  Otherwise each ATS gets a queue in the given order and `workers.get(ats, workers["default"])` threads; results are
  yielded in the order they finish. A worker checks `should_stop()` before each board; a board never started is
  never yielded. `full(board)` is called on the calling thread before the board is queued (it reads the store).
  `CrawlConfig.workers: dict[str, int]` from `[workers]` (`default = 2`, `greenhouse = 4`, `ashby = 3`, `lever = 1`,
  `workable = 1`, `workday = 6`, `oracle_orc = 3`, `bamboohr = 3`); every value 1 to 16, `default` required.

- [ ] **Step 1: Write the failing tests:** `test_inline_mode_keeps_board_order`; `test_every_board_yielded_once`
  (40 boards over 4 ATSs, threaded); `test_workers_per_ats_bounded` (a fake adapter counts in-flight calls per ATS
  with a lock; max seen for lever is 1 and for greenhouse at most 4); `test_should_stop_starts_no_new_board` (stop
  after 5 yields: fewer than 40 yielded, none started after the flag); `test_host_tripped_is_yielded_not_raised`
  (`tripped == "lever.co"`, `result is None`); `test_full_called_on_calling_thread` (records
  `threading.get_ident()`).
- [ ] **Step 2: Run and see them fail** (no module `outreach.crawl.fanout`).
- [ ] **Step 3: Implement** with `threading.Thread` workers, one `queue.Queue` per ATS for work and one shared queue
  for results; the generator drains the results queue until every worker has finished, then joins them.
- [ ] **Step 4: Run and see them pass.**
- [ ] **Step 5: Commit** `Crawler: list boards on worker threads, one queue per ATS`.

### Task 1.4: The tick reads through the fan-out

**Files:**
- Modify: `outreach/crawl/tick.py` (`_Pass`, `_read_boards`, `run_tick`, `read_company_boards`),
  `outreach/crawl/__main__.py` (the `tick` command passes `workers=cfg.workers`)
- Test: `tests/test_crawl_tick.py`, `tests/test_crawl_cli.py`

**Interfaces:**
- Consumes: `fanout.list_boards`, `fanout.Listed` (Task 1.3).
- Produces: `run_tick(..., workers: Mapping[str, int] | None = None)` and the same keyword on
  `read_company_boards`; `None` keeps the serial path. `_Pass.read(board)` splits into `_Pass.start(board) ->
  tuple[Adapter | None, _TickClient]` and `_Pass.take(listed: Listed) -> None`; `take` holds the exact logic `read`
  has after its `list_board` call (trip, held errors, held empties, ingest, profile). `_Pass.down` is written only on
  the writer thread; workers only test membership through `_TickClient._check`.

- [ ] **Step 1: Write the failing tests:**
  - `test_threaded_tick_matches_serial_outcome`: two scratch stores seeded alike (30 boards on greenhouse, lever,
    ashby, workday; a mix of ok, unchanged, empty, error answers); one `run_tick` serial, one with `workers`; the
    `board` rows (minus timestamps) and the open `posting` source keys are equal.
  - `test_slow_host_does_not_delay_other_hosts`: the fake client sleeps 0.3 s (real) on `api.lever.co`; with
    workers, all 10 greenhouse boards are written before the third lever board.
  - `test_trip_stops_other_workers_on_that_bucket`: 5 greenhouse answers of 503 trip the bucket; the fake client
    records no greenhouse request after the trip; the shielded greenhouse boards are deferred, as serial does.
  - `test_stop_after_budget_starts_no_new_board_threaded`: budget spent after 3 boards; the rest keep their
    `next_read_at`.
  - In `test_crawl_cli.py`: `test_tick_command_passes_configured_workers` (patch `run_tick`, assert
    `workers == load_crawl_config().workers`).
- [ ] **Step 2: Run and see them fail** (`run_tick() got an unexpected keyword argument 'workers'`).
- [ ] **Step 3: Implement.** `_read_boards` takes `workers`, builds `select(now)` once, and loops over
  `fanout.list_boards(boards, run.start, lambda b: full_read_due(conn, b, now), workers=workers, should_stop=lambda:
  run.stopped or clock() - started >= cfg.tick_budget_s, clock=clock)` calling `run.take`. Body fetches stay inside
  `read_board` on the writer thread.
- [ ] **Step 4: Run the tick, cadence, ingest and CLI tests and see them pass**; then the full suite.
- [ ] **Step 5: Commit** `Crawler: the tick lists boards side by side and writes them on one thread`.

**Part 1 done when:** all tests above green; the full suite green; in the live checkout (after Devanshu says go) one
`python -m outreach.crawl tick` finishes and `python -m outreach.crawl profile` shows list time per tick well under
the serial figure, with no tripped bucket.

---

## Part 2: Three speeds and the every-minute tick (#158, second half)

Depends on: Part 1 merged. Deploy: the migration (`uv run python -m outreach.install`), then the launchd template
changed, so `uv run python -m outreach.deploy plan`, read it, `uv run python -m outreach.deploy install`. Ask first.

### Task 2.1: The tier column and the tier rules

**Files:**
- Create: `outreach/store/migrations/0008_board_tier.sql`, `outreach/crawl/tiers.py`
- Modify: `outreach/crawl/boardstate.py` (`BoardRow.tier: str | None = None` as the last field, read by
  `load_board` and `due_boards`), `outreach/crawl/config.py`, `outreach/crawl/config.toml` (`[tiers]`)
- Test: `tests/test_crawl_tiers.py`, `tests/test_store_migrate.py`

**Interfaces:**
- Produces:

```python
Tier = Literal["hot", "warm", "cold"]
def board_tier(conn, board_id: int, company_id: int, now: str, reached: frozenset[int]) -> Tier
def reached_companies(conn, now: str) -> frozenset[int]   # canonical ids reached in the last 365 days
def assign_all(conn, now: str, cfg: CrawlConfig) -> dict[str, int]   # counts per tier; one grouped query
def promote_reached(conn, now: str, reached: frozenset[int]) -> int  # boards made hot
def tier_wait_s(tier: Tier, ats: str, cfg: CrawlConfig) -> int | None   # None for cold
```

  Hot: the company is in `reached`, or the board has a posting (open or closed) first seen in the last 365 days with
  a current decision of lane `engineering`, `us <> 'no'` and level state `fit` or `uncertain` (`joblist.STATE_SQL`).
  Warm: a posting in the same window with lane `engineering` and `us <> 'no'`. Cold: the rest. `reached_companies`
  is the company ids of `plan.outbound.load_outbound(conn, canon, tz="America/New_York", since=<now - 365 days>)`
  (Part 5 adds his Done marks). `assign_all` writes `board.tier` for every active board and sets `next_read_at = now`
  on a board that became hot when its `next_read_at` is later than `now + tier_wait_s`. `promote_reached` does the
  same for boards of reached companies not yet hot. `[tiers]`: `hot_s = 300`, `warm_s = 3600`, `hot_error_s = 600`;
  `[tiers.hot_by_ats]`: `lever = 600`, `workable = 900`, `workday = 1800`, `oracle_orc = 1800`.
  Migration: `ALTER TABLE board ADD COLUMN tier TEXT CHECK (tier IS NULL OR tier IN ('hot','warm','cold'))`. NULL
  means not assigned yet.

- [ ] **Step 1: Write the failing tests:** `test_fit_posting_in_12_months_is_hot`,
  `test_level_not_stated_posting_is_hot`, `test_reached_company_is_hot`, `test_us_engineering_only_is_warm`,
  `test_fit_posting_13_months_old_is_not_hot`, `test_non_us_engineering_is_cold`,
  `test_assign_all_sets_new_hot_boards_due_now`, `test_promote_reached_makes_boards_hot_and_due`,
  `test_tier_wait_by_ats` (`tier_wait_s("hot", "lever", cfg) == 600`, `("hot", "greenhouse") == 300`,
  `("warm", "lever") == 3600`, `("cold", ...) is None`); in `test_store_migrate.py`: `test_0008_adds_board_tier`.
- [ ] **Step 2: Run and see them fail.**
- [ ] **Step 3: Implement** `tiers.py`, the migration and the config keys (validated: every `hot_by_ats` key is a
  reader in `ATS_HOSTS`; every value 60 to `floor_s`).
- [ ] **Step 4: Run and see them pass.**
- [ ] **Step 5: Commit** `Crawler: hot, warm and cold boards`.

### Task 2.2: A read's tier sets its next read

**Files:**
- Modify: `outreach/crawl/ingest.py` (phase 7 of `read_board`), `outreach/crawl/boardstate.py`
  (`write_board_state(..., tier: str | None = None)` writes `board.tier` when given), `outreach/crawl/cadence.py`
  (`error_backoff_s(error_streak, cfg, base_s: int | None = None)`)
- Test: `tests/test_crawl_ingest.py`, `tests/test_crawl_cadence.py`

**Interfaces:**
- Consumes: `tiers.board_tier`, `tiers.tier_wait_s` (Task 2.1). `read_board(..., reached: frozenset[int] =
  frozenset())`: the tick computes `reached_companies` once per tick and passes it down.
- Produces: after a good read, `next_read_at = now + min(wait_s, tier_wait_s(...))` when the tier has a cap; after an
  error on a hot board, the backoff doubles from `hot_error_s` (600, 1200, 2400 ...) up to the existing 24-hour cap.

- [ ] **Step 1: Write the failing tests:** `test_hot_board_next_read_in_5_minutes`,
  `test_hot_lever_board_next_read_in_10_minutes`, `test_warm_board_next_read_in_an_hour`,
  `test_cold_board_keeps_floor` (21600), `test_hot_board_error_waits_10_minutes_then_doubles` (600 then 1200),
  `test_read_writes_board_tier`, `test_low_yield_big_board_that_is_hot_still_reads_in_5_minutes`.
- [ ] **Step 2: Run and see them fail.**
- [ ] **Step 3: Implement** in phase 7: compute the tier after the postings are written, cap `wait_s`, pass `tier`
  to `write_board_state`.
- [ ] **Step 4: Run and see them pass**, then the crawl suite.
- [ ] **Step 5: Commit** `Crawler: a hot board is read every 5 minutes, a warm one every hour`.

### Task 2.3: The tick every minute

**Files:**
- Modify: `outreach/crawl/tick.py` (`run_tick`: assign tiers when any active board has `tier IS NULL`, then
  `promote_reached` every tick, both before `select`), `outreach/crawl/cadence.py` (`due_boards` order),
  `outreach/crawl/config.toml` (`tick_budget_s = 50`, `boards_per_tick = 3000`),
  `launchd/com.devanshu.outreach-crawl.plist.template` (`StartInterval` 60, comment updated),
  `tick._feed_step` (poll at most every `[simplify] every_s = 1800`, a new key in `outreach/pipeline/config.toml`),
- Create: `outreach/crawl/runs.py` (`prune_read_board_runs`)
- Test: `tests/test_crawl_tick.py`, `tests/test_crawl_cadence.py`, `tests/test_deploy.py`

**Interfaces:**
- Produces: `due_boards` orders plan companies, then never-read boards, then `tier = 'hot'`, then `next_read_at`,
  then board id. The tick's summary gains `tiers: {"hot": n, "warm": n, "cold": n}`.
  `prune_read_board_runs(conn, now, keep_days=14) -> int` deletes `read_boards` job runs older than 14 days that no
  `board_read` row references and that did not fail (a tick a minute is 1,440 rows a day).

- [ ] **Step 1: Write the failing tests:** `test_hot_boards_first` (cadence: an overdue hot board sorts before an
  older overdue warm one); `test_tick_assigns_tiers_once` (first tick assigns, second does not rerun `assign_all`:
  patch and count); `test_tick_promotes_reached_company`; `test_feed_poll_every_30_minutes` (two ticks 60 s apart
  poll once; a tick 1,800 s later polls again); `test_prune_keeps_referenced_and_failed_runs`; in `test_deploy.py`:
  `test_crawl_job_runs_every_60_seconds` (the rendered plist has `StartInterval` 60).
- [ ] **Step 2: Run and see them fail.**
- [ ] **Step 3: Implement.** Pruning runs at the end of a tick at most once a New York day (an `event` keyed
  `prune-runs:<date>`, like the slug probe's day key).
- [ ] **Step 4: Run and see them pass**, then the full suite.
- [ ] **Step 5: Commit** `Crawler: tick every minute, hot boards first, the feed every 30 minutes`.

**Part 2 done when:** tests green; after install in the live checkout, `python -m outreach.deploy status` shows the
crawl job loaded with a 60 s interval, the tick summary's `tiers` counts look right (about 1,300 hot), and over one
hour every hot Greenhouse board has a `board_read` at most 6 minutes apart.

---

## Part 3: The clock on each row

Depends on: Part 2 merged. Deploy: migration, then kickstart the API (the job list changed) and `pnpm build` (the
row changed).

### Task 3.1: Migration and the row clock helpers

**Files:**
- Create: `outreach/store/migrations/0009_row_clock.sql`, `outreach/crawl/row_clock.py`
- Modify: `outreach/crawl/config.py`, `outreach/crawl/config.toml` (`[tiers] fresh_gap_s = 7200`)
- Test: `tests/test_crawl_row_clock.py`, `tests/test_store_migrate.py`

**Interfaces:**
- Produces: columns `posting.posted_at TEXT` (canonical UTC, a trustworthy board time only), `posting.seen_gap_s
  INTEGER` (NULL for a first read and for rows from before this migration), `posting.repost_of INTEGER REFERENCES
  posting(posting_id)`, `posting.list_at TEXT` (the time the list sorts by; NULL means Date unknown),
  `board.last_good_read_at TEXT`; index `posting_list_at ON posting(list_at) WHERE closed_at IS NULL`. Backfill:
  `list_at = COALESCE(posted_on || 'T05:00:00Z', first_seen_at)`; `board.last_good_read_at = last_read_at` where
  `last_read IN ('ok','unchanged','empty')`, else the newest non-error `board_read.at`.

```python
FRESH_GAP_S = 7200   # read from [tiers] fresh_gap_s; the constant is the default
REPOST_DAYS = 120
def list_at(first_seen_at: str, posted_at: str | None, posted_on: str | None, seen_gap_s: int | None) -> str | None
def repost_key(title: str) -> str
def find_repost(conn, company_id: int, title: str, now: str) -> int | None
def is_fresh(seen_gap_s: int | None, repost_of: int | None, fresh_gap_s: int = FRESH_GAP_S) -> bool
```

  `list_at`: a known gap gives `first_seen_at`; no gap gives `posted_at`, else `posted_on` at 00:00 New York in UTC,
  else None. `repost_key`: lower case, every run of non-alphanumerics to one space, trimmed. `find_repost`: the newest
  posting at the same company (merged companies included, as `ranking.common.canonical_ids` resolves them) with the
  same `repost_key`, closed within `REPOST_DAYS` of `now`.

- [ ] **Step 1: Write the failing tests:** `test_list_at_uses_first_seen_when_gap_known`,
  `test_list_at_first_read_uses_posted_at`, `test_list_at_first_read_date_only_is_ny_midnight`
  (`list_at("2026-11-09T15:00:00Z", None, "2026-11-09", None) == "2026-11-09T05:00:00Z"`, and `"2026-07-01"` gives
  `"2026-07-01T04:00:00Z"`), `test_list_at_first_read_no_date_is_none`,
  `test_repost_key_ignores_case_and_punctuation` (`"Software Engineer, New Grad (2026)"` and `"software engineer new
  grad 2026"` match), `test_find_repost_within_120_days`, `test_find_repost_ignores_121_days`,
  `test_find_repost_other_company_ignored`, `test_find_repost_open_posting_ignored`,
  `test_is_fresh_rules` (gap 7200 fresh, 7201 not, None not, a repost not); `test_0009_backfills_list_at_and_last_good_read`.
- [ ] **Step 2: Run and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run and see them pass.**
- [ ] **Step 5: Commit** `Postings: first seen, posted time, seen gap and reposts`.

### Task 3.2: Reads record the seen gap, the posted time and reposts

**Files:**
- Modify: `outreach/crawl/base.py` (`JobRef.posted_at: str | None = None`), `outreach/crawl/ats/greenhouse.py`
  (`posted_at` from `first_published` only, converted to UTC, never from `updated_at`; the `posted_on` rule stays as
  it is, #155 owns it), `outreach/crawl/postings.py`
  (`write_postings(..., seen_gap_s: int | None = None)`), `outreach/crawl/ingest.py`, `outreach/crawl/boardstate.py`
  (`last_good_read_at = now` on `ok`, `unchanged`, `empty`; `BoardRow.last_good_read_at`)
- Test: `tests/test_crawl_ats_greenhouse.py`, `tests/test_crawl_postings.py`, `tests/test_crawl_ingest.py`

**Interfaces:**
- Consumes: `row_clock.*` (Task 3.1).
- Produces: every new posting row has `seen_gap_s` (`None` when the board has no `last_good_read_at`, else whole
  seconds from it to `now`), `posted_at`, `list_at`, and `repost_of` (looked up only when `seen_gap_s` is not None).

- [ ] **Step 1: Write the failing tests:** `test_posted_at_from_first_published_in_utc`
  (`"2026-11-09T10:38:00-05:00"` gives `"2026-11-09T15:38:00Z"`), `test_updated_at_is_never_posted_at`,
  `test_seen_gap_is_time_since_last_good_read` (last good read 300 s before: `seen_gap_s == 300`, `list_at ==
  first_seen_at`), `test_error_read_does_not_move_last_good_read`, `test_first_read_rows_never_fresh` (300 postings,
  none fresh, dated ones `list_at` from their date, undated `list_at IS NULL`),
  `test_wake_after_8_hours_marks_finds_late` (gap 28,800: stored, not fresh), `test_repost_links_closed_posting`.
- [ ] **Step 2: Run and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run and see them pass**, then the crawl suite.
- [ ] **Step 5: Commit** `Crawler: every new posting knows how long Knock was not looking`.

### Task 3.3: The job list sorts by the row clock

**Files:**
- Modify: `outreach/pipeline/joblist.py` (`query`, `JobRow`), `outreach/api/models/jobs.py` (`JobRowWire`),
  `outreach/api/queries/jobs_list.py`
- Test: `tests/test_pipeline_joblist.py`, `tests/test_api_jobs.py`

**Interfaces:**
- Produces: `JobRowWire` gains `first_seen_at: str`, `posted_at: str | None`, `list_at: str | None`, `fresh: bool`,
  `seen_gap_s: int | None`, `reposted_from: str | None` (the old posting's `first_seen_at`). `date`, `date_kind` and
  `age_days` stay until Task 3.5 moves the web to the new fields and removes them, so every commit compiles. Order: `list_at` newest first, then the size rank, then posting id;
  rows with `list_at IS NULL` after every dated row, in every window, newest `first_seen_at` first. The cursor is
  `(list_at, size_rank, posting_id)` with NULL `list_at` encoded as its own group. Windows filter on `list_at`
  (Date unknown rows are never filtered out by the window).

- [ ] **Step 1: Write the failing tests:** `test_sorts_by_list_at_to_the_minute` (10:42 before 10:38 on the same
  day), `test_late_find_not_fresh`, `test_first_read_row_without_date_in_unknown_group`,
  `test_unknown_group_shows_in_7_day_window`, `test_window_filters_on_list_at`,
  `test_repost_row_not_fresh_and_carries_old_first_seen`, `test_cursor_pages_across_unknown_group`; in
  `test_api_jobs.py`: `test_jobs_row_has_clock_fields`.
- [ ] **Step 2: Run and see them fail.**
- [ ] **Step 3: Implement**, regenerate the OpenAPI schema and `web/src/api/schema.ts`.
- [ ] **Step 4: Run and see them pass**; `cd web && pnpm check:api`.
- [ ] **Step 5: Commit** `Job list: newest first by first sighting, to the minute`.

### Task 3.4: Alerts only for fresh rows, 5 minutes apart

**Files:**
- Modify: `outreach/pipeline/alerts.py` (`_todays` becomes `_fresh`: fit rows with `fresh` true, first seen in the
  last 24 hours, not announced; `MIN_GAP = timedelta(minutes=5)`)
- Test: `tests/test_pipeline_alerts.py`

- [ ] **Step 1: Write the failing tests:** `test_late_find_never_alerts`, `test_repost_never_alerts`,
  `test_first_read_never_alerts`, `test_second_alert_after_5_minutes` (a notice at 10:00, another fresh row at 10:05
  alerts; one at 10:04 waits), `test_fresh_row_from_yesterday_evening_alerts_after_quiet_hours` (first seen 21:50,
  alerted at 08:00 as it is under 24 hours old).
- [ ] **Step 2: Run and see them fail.** **Step 3: Implement.** **Step 4: Pass.**
- [ ] **Step 5: Commit** `Alerts: only fresh jobs, at most one notice every 5 minutes`.

### Task 3.5: The row shows its clock

**Files:**
- Modify: `web/src/routes/browse/index.tsx` and its row component, `web/src/lib/format.ts` (a `firstSeenLabel`
  helper), the jobs fixtures in `web/src/api/`
- Test: `web/src/routes/browse/browse.test.tsx`

**Interfaces:**
- Consumes: the `JobRowWire` fields from Task 3.3.
- Produces: each row's time cell: "10:42 · 3 min ago" (today), "Nov 8, 16:05" (older), with "posted 10:38" beside it
  when `postedAt` is set; a "found after a 3 day gap" tag when `seenGapS > 7200`; a "reposted, first seen Oct 2" tag
  when `repostedFrom` is set; Date unknown rows under a "Date unknown" heading at the end.

- [ ] **Step 1: Run the `craft` skill for the row and its tags; settle copy and layout before code.**
- [ ] **Step 2: Write the failing tests:** `shows first seen to the minute`, `shows the posted time beside it when
  Greenhouse gave one`, `tags a late find with its gap`, `tags a repost with its old date`, `groups rows without a date
  at the end`.
- [ ] **Step 3: Run and see them fail.** **Step 4: Implement**, then remove `date`, `date_kind` and `age_days` from
  `JobRowWire` and regenerate the schema. **Step 5: Pass** (`pnpm typecheck && pnpm lint && pnpm test && pnpm
  check:api`, and the Python suite).
- [ ] **Step 6: Commit** `Jobs: each row shows when Knock first saw it`.

**Part 3 done when:** tests green, the craft gate passed, and in the live checkout a new Greenhouse posting shows
its first sighting and its posted time a few minutes apart.

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
  - `check_state.PROGRESS_KEY = "check_progress"`, the `pipeline_meta` row the crawler writes (Task 4.2) as JSON
    `{run_id, checked, total, new, started_at, finished_at, still_running}`; `check_state.pending_request(conn) -> str
    | None` (the newest `check_now_requested` time newer than the last finished check pass, else None);
    `check_state.status(conn) -> CheckStatus` (queued when a request is pending and no pass runs, running while the
    progress row has no `finished_at`, done when it has one, idle when nothing was ever asked).
  - `GET /api/jobs/check` returns `CheckStatus(state: Literal["idle", "queued", "running", "done"], checked: int,
    total: int, new: int, started_at: str | None, finished_at: str | None, still_running: list[str])` (still-running
    ATS names).
  - `kick.kick_crawl() -> str`: outside the live checkout (`live_checkout.is_live()` false) returns `"not_live"` and
    runs nothing; inside, runs `launchctl kickstart gui/<uid>/com.devanshu.outreach-crawl` (no `-k`) and returns
    `"started"`.

- [ ] **Step 1: Write the failing tests:** `test_check_now_records_request_and_kicks` (a fake kicker counts calls),
  `test_check_now_twice_kicks_once`, `test_check_now_outside_live_checkout_never_kicks` (the real `kick_crawl` with
  `is_live` false never calls `subprocess`), `test_check_status_idle_when_never_requested`,
  `test_check_status_reads_progress`.
- [ ] **Step 2: Run and see them fail.** **Step 3: Implement.** **Step 4: Pass.**
- [ ] **Step 5: Commit** `API: Check now records a request and starts the crawl job`.

### Task 4.2: The tick runs the check-every-board pass

**Files:**
- Create: `outreach/crawl/check_all.py`
- Modify: `outreach/crawl/tick.py` (`run_tick` runs a pending check before its normal pass and once more after it if a
  request arrived meanwhile), `outreach/crawl/config.toml` (`check_budget_s = 600`, `check_skip_recent_s = 60`)
- Test: `tests/test_crawl_check_all.py`

**Interfaces:**
- Consumes: `fanout.list_boards`, `_Pass`, `_read_boards` (Part 1), `board.tier` (Part 2).
- Consumes: `check_state.pending_request`, `check_state.PROGRESS_KEY` (Task 4.1).
- Produces: `select_all(conn, now, cfg) -> list[BoardRow]` (active boards of live companies on read
  ATSs, not read in the last `check_skip_recent_s`, not inside an error backoff; ordered hot, warm, cold or NULL, then
  `last_read_at` oldest first, never-read first); the pass is a `read_boards` job run whose summary has `"mode":
  "check_all"` and `"requested_at"` (no change to the `job_run.job` CHECK). Progress goes to the `PROGRESS_KEY` row
  at most once a second, and once more with `finished_at` at the end.

- [ ] **Step 1: Write the failing tests:** `test_check_all_orders_hot_warm_cold`,
  `test_check_all_skips_board_read_within_60s`, `test_check_all_skips_boards_in_error_backoff`,
  `test_tick_runs_check_all_when_requested`, `test_request_during_tick_runs_after_it`,
  `test_no_request_no_check_pass`, `test_progress_written_and_final`.
- [ ] **Step 2: Run and see them fail.** **Step 3: Implement.** **Step 4: Pass.**
- [ ] **Step 5: Commit** `Crawler: check every board when he asks`.

### Task 4.3: New postings move the pulse

**Files:**
- Modify: `outreach/crawl/tick.py` (`_Pass._ingest`: when `out.new > 0`, append `event(entity='board',
  kind='postings_new', entity_id=board_id, detail={"new": n, "run_id": run_id}, idempotency_key=
  f"postings-new:{board_id}:{run_id}")`), `web/src/shell/use-pulse.ts` (`PULSE_REFETCHES` gains the jobs and check
  status keys)
- Test: `tests/test_crawl_tick.py`, `web/src/shell/use-pulse.test.ts`

- [ ] **Step 1: Write the failing tests:** `test_new_postings_event_moves_pulse` (pulse revision grows after a tick
  that found a new posting, and does not after an unchanged tick); `refetches the job list when the pulse moves`.
- [ ] **Step 2: Fail. Step 3: Implement. Step 4: Pass. Step 5: Commit** `Jobs: a new posting reaches an open page within seconds`.

### Task 4.4: The page: Check now button, progress line, "N new"

**Files:**
- Create: `web/src/routes/browse/check-now.tsx`, `web/src/routes/browse/new-rows.ts` (the hold-and-reveal logic)
- Modify: `web/src/routes/browse/index.tsx`, `web/src/api/` (client methods `checkNow`, `checkStatus`, keys, fixtures)
- Test: `web/src/routes/browse/check-now.test.tsx`, `web/src/routes/browse/new-rows.test.ts`

**Interfaces:**
- Produces: `heldRows(shown: JobRow[], incoming: JobRow[], atTop: boolean, rowOpen: boolean) -> { rows: JobRow[];
  waiting: number }` (at the top with no row open, incoming rows show at once; otherwise the shown order stays and
  `waiting` counts incoming fresh rows not shown); the last time he opened the list in `localStorage`
  `knock.jobs.lastOpened`, written when the page mounts and when he clicks "N new"; a row is "new" when `fresh` and
  `firstSeenAt > lastOpened`.

- [ ] **Step 1: Run the `craft` skill** for the button, the progress line ("Checked 1,240 of 2,013 boards, 3 new";
  done: "Checked 2,013 boards in 74 s. 6 new."; "Lever: 20 still to go"), the "N new" button and the new marker, and
  pick a free key for Check now.
- [ ] **Step 2: Write the failing tests:** `shows progress while a check runs`, `shows the done line with the count`,
  `a second click while running shows the running check`, `holds new rows while scrolled`, `shows new rows at once at
  the top`, `clicking N new reveals and scrolls to top`, `marks rows first seen since the last visit as new`.
- [ ] **Step 3: Fail. Step 4: Implement** (poll `GET /api/jobs/check` every second only while the state is queued or
  running). **Step 5: Pass.**
- [ ] **Step 6: e2e on the workspace port** (`WEB_PORT=$CONDUCTOR_PORT pnpm e2e`): Check now by keyboard shows the
  progress line against the fixture API.
- [ ] **Step 7: Commit** `Jobs: Check now and new rows that never jump under the cursor`.

**Part 4 done when:** tests and e2e green, the craft gate passed, and in the live checkout Devanshu's Check now
finishes about 2,000 boards in under 2 minutes (Lever tail excepted) with no tripped bucket.

---

## Part 5: Today goes away, Done replaces Go ahead

Depends on: Part 4 merged (it touches the same page). Deploy: kickstart the API, `pnpm build`; the plan job stays
installed and prints one line.

### Task 5.1: The plan job off

**Files:**
- Modify: `outreach/plan/config.toml` (`[plan] enabled = false`), `outreach/plan/__main__.py` (`build` prints "plan
  is off (first to see ruling, #9)" and exits 0 when off, touching nothing), `outreach/plan/config.py` if it validates
  keys
- Test: `tests/test_plan_cli.py` (or the existing plan CLI test module)

- [ ] **Step 1: Failing test** `test_plan_build_off_prints_one_line_and_exits` (exit 0, no `daily_list_entry` row, no
  `job_run` row). **Step 2: Fail. Step 3: Implement. Step 4: Pass. Step 5: Commit** `Plan: the 08:00 plan is off`.

### Task 5.2: Done, his reached mark

**Files:**
- Create: `outreach/pipeline/reached.py`
- Modify: `outreach/api/services/jobs.py` (`go_ahead` goes; `done`, `clear_done` come),
  `outreach/api/routers/jobs.py`, `outreach/api/models/jobs.py`, `outreach/pipeline/joblist.py` (`contacted_on`
  includes marks), `outreach/crawl/tiers.py` (`reached_companies` includes marks)
- Test: `tests/test_pipeline_reached.py`, `tests/test_api_jobs_writes.py`, `tests/test_pipeline_joblist.py`,
  `tests/test_crawl_tiers.py`

**Interfaces:**
- Produces: `reached.marks(conn) -> dict[int, str]` (canonical company id to the New York date of his newest mark
  not cleared after it); events `entity='company', kind='company_reached'` and `kind='company_reached_cleared'`,
  actor `devanshu`. `POST /api/jobs/{postingId}/done` returns `DoneResult(posting_id, company_id, reached_on: str,
  text: str)` (keys `job-done:<id>`); `POST /api/companies/{companyId}/reached/clear` returns `ClearDoneResult(
  company_id, cleared: bool)`. `POST /api/jobs/{postingId}/go-ahead` is removed.

- [ ] **Step 1: Failing tests:** `test_done_records_reached_mark`, `test_done_twice_is_one_mark`,
  `test_clear_mark_removes_reached`, `test_mark_after_clear_counts_again`, `test_go_ahead_route_is_gone`,
  `test_reached_mark_sets_contacted_on`, `test_not_contacted_filter_hides_marked`, `test_reached_mark_makes_boards_hot`,
  `test_done_on_no_go_company_still_records` (no check stops it).
- [ ] **Step 2: Fail. Step 3: Implement**, regenerate the API schema. **Step 4: Pass. Step 5: Commit**
  `Jobs: Done marks a company as reached`.

### Task 5.3: Remove Today, Proposals and Email today from the web app

**Files:**
- Delete: `web/src/routes/today/`, `web/src/routes/proposals/`, `web/src/routes/pipeline/email-today.tsx` and their
  tests and fixtures that nothing else uses
- Modify: `web/src/app/routes.tsx` (index and `*` go to `/browse`; `today` and `proposals` redirect to `/browse`),
  `web/src/shell/app-shell.tsx` (`VIEW_TITLE`, `GO`: no `t`), `web/src/shell/app-sidebar.tsx` (Jobs first, no Today),
  `web/src/routes/pipeline/index.tsx`, `web/src/routes/browse/index.tsx` (the `a` key and button become Done with the
  shared undo window; a "reached Nov 9" tag on every row of a reached company; the one-line "Today: N sent, N
  replies" summary under the header, from the existing shell or progress data)
- Test: `web/src/app/routes.test.tsx` (or the shell tests), `web/src/routes/browse/browse.test.tsx`,
  `web/src/shell/app-sidebar.test.tsx`

- [ ] **Step 1: Run the `craft` skill** for the Done key and the reached tag and the header summary.
- [ ] **Step 2: Failing tests:** `root opens Jobs`, `today and proposals redirect to Jobs`, `sidebar has no Today and
  lists Jobs first`, `Done marks the company and every row of it shows reached`, `Done can be undone in the undo
  window`, `header shows today's outreach line linking to Progress`.
- [ ] **Step 3: Fail. Step 4: Implement. Step 5: Pass** (`pnpm typecheck && pnpm lint && pnpm test`, then e2e on
  the workspace port).
- [ ] **Step 6: Commit** `Knock opens on Jobs; Today, Proposals and Email today are gone`.

### Task 5.4: Nothing on the outreach side moved

**Files:**
- Test only: `tests/test_api_needs_you.py`, `tests/test_sending_*.py`, `tests/test_api_progress.py`

- [ ] **Step 1: Write the tests that pin today's behavior with no daily list:**
  `test_needs_you_lists_emailed_and_reply_companies_without_a_plan`, `test_claim_context_is_own_without_a_plan`,
  `test_progress_counts_unchanged_without_a_plan`. If any fails, fix the code that read the daily list, not the test.
- [ ] **Step 2: Commit** `Tests: the outreach side works with no daily plan`.

**Part 5 done when:** tests and e2e green, the craft gate passed, `python -m outreach.plan build` prints the one
line in the live checkout, and Knock opens on Jobs.

---

## Part 6: Workday and Oracle newest first

Depends on: Part 2 merged (can run beside Parts 3 to 5 in its own workspace). Takes the Workday delta read from
#157; BambooHR and JazzHR fixes stay in #157.

### Task 6.1: Oracle's top five

**Files:**
- Modify: `outreach/crawl/ats/oracle_orc.py`
- Test: `tests/test_crawl_ats_oracle_orc.py` (fixtures from `python -m outreach.crawl probe` in the live checkout)

**Interfaces:**
- Produces: a first request with `sortBy=POSTING_DATES_DESC,limit=5`; when all five ids are stored open postings and
  `TotalJobsCount` equals the stored open count, the read is `unchanged` (fingerprint kept); otherwise the full paged
  read runs. The weekly full read (`full_read_due`) skips the check.

- [ ] Failing tests `test_oracle_top5_known_and_same_total_is_unchanged`, `test_oracle_new_id_in_top5_full_reads`,
  `test_oracle_total_changed_full_reads`, `test_oracle_weekly_full_read_skips_check`; implement; pass; commit
  `Oracle: one request says whether anything is new`.

The check needs the stored open ids, and no adapter opens the store: extend `Validators` with
`known_ids: frozenset[str] = frozenset()`, filled on the writer thread (in `_Pass.start`, before the board is
queued) for Oracle and Workday boards only, from the board's open postings' native ids. The open count is
`len(known_ids)`.

### Task 6.2: Workday page one on tenants that list newest first

**Files:**
- Modify: `outreach/crawl/ats/workday.py`, `outreach/crawl/config.toml` (`[workday] newest_first = [...]`, tenants
  confirmed from captured responses, at least 5 to start)
- Test: `tests/test_crawl_ats_workday.py`

- [ ] Failing tests `test_workday_newest_first_page1_known_is_unchanged`,
  `test_workday_unlisted_tenant_always_full_reads`, `test_workday_new_id_on_page1_full_reads`,
  `test_workday_weekly_full_read_skips_check`; implement; pass; commit `Workday: page one says whether anything is
  new on tenants that list newest first`.

### Task 6.3: Enterprise boards join the 5-minute tier

- [ ] Modify `[tiers.hot_by_ats]`: `oracle_orc` to 300, `workday` to 300 for tenants in `newest_first` (a tenant not
  listed stays 1800: `tier_wait_s` takes the token). Failing test `test_hot_workday_newest_first_tenant_reads_in_5_minutes`;
  implement; pass; commit.

**Part 6 done when:** tests green; in the live checkout the profile shows Workday and Oracle list requests per tick
down, and hot enterprise boards read every 5 minutes.

---

## Order and merge

| Order | Part | Issue | Depends on |
|---|---|---|---|
| 0 | #9 ruling, issues | #9 | nothing |
| 1 | Many boards at once | #158 | nothing |
| 2 | Three speeds, every-minute tick | #158 | Part 1 |
| 3 | The row clock | new | Part 2 |
| 4 | Check now, self-updating list | new | Part 3 |
| 5 | Today goes away, Done | new | Part 4 |
| 6 | Workday and Oracle newest first | new (with #157) | Part 2 |

One open PR per chain; Part 6 may run beside 3 to 5 in its own workspace. He feels the speed after Part 2, sees
the clock after Part 3, and has the finished product after Part 5.

**The week after Part 4 (spec section 7):** read from the live store, report to Devanshu, change nothing: the median
minutes from Greenhouse `first_published` to `first_seen_at` for postings on hot boards (target under 5), Check now
wall times from the `check_all` job runs (target under 2 minutes, Lever tail excepted), any bucket tripped by a 429
or 403, and any row marked fresh that turned out to be a repost or an old job.

## Risks

- **Threads and the tick's outage rules.** `test_threaded_tick_matches_serial_outcome` and the trip test guard it;
  the serial path stays the default for every old test, so a regression shows as a difference between the two.
- **Laptop load from a tick a minute.** Most minutes read a few hundred boards that answer 304. The tick summary's
  `seconds` and the profile show it; raise `hot_s` in config if the Mac feels it.
- **A host that minds 20 requests a second.** The breaker trips on 429 at once; the rates are config; the research
  marks the limits unconfirmed. Watch the first live days.
- **Store growth.** One job run a minute is pruned after 14 days (Task 2.3); posting growth is unchanged.
