# Diff crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read every board only when it is due and only for what changed, write boards, postings, bodies and decisions into the store with the #3 read contract, queue changed engineering bodies for the reader, and watch careers pages for companies with no board, all from one 30-minute scheduler tick.

**Architecture:** Fourteen small ATS adapters sit behind the `Adapter` Protocol from #30 and talk to an injected HTTP client (a polite wrapper with a declared User-Agent, per-host spacing and a host circuit breaker). One `read_board` function turns a `ReadResult` into store writes in short phases (no transaction across a request, 2,000-row batches, board state committed last so every phase is idempotent). A cadence module sets each board's `next_read_at`; `run_tick` reads what is due, under a time budget, inside one lock, and checkpoints the WAL at the end.

**Tech Stack:** Python 3.14 (Homebrew), standard library only (`urllib`, `html.parser`, `xml.etree`, `hashlib`, `fcntl`), SQLite STRICT tables, `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-20-opportunity-pipeline-design.md` (sections 3, 4, 6, 12, 14, 17; section 7.3 for queue priority). Issue #21 and its 2026-10-21 scope-change comment. Contracts, schema, `check_adapter` and the `PipelineHarness`: `docs/superpowers/plans/2026-10-21-pipeline-foundation.md` (#30, Tasks 1, 2 and 6). Evidence: `docs/research/2026-10-17-crawl-and-scheduling.md`, `docs/research/2026-10-20-company-discovery-and-job-data.md` (sections 2, 2.1, 6).

**Branch:** `DevanshuNEU/diff-crawler`, from master once #30 has merged (this plan imports `Validators`, `JobRef`, `ReadResult`, `Adapter`, `ids_fingerprint` and needs the board and `read_queue` columns). Issue: #21. Also depends on #4 (rules, `decide_and_store`) and #31 (it creates the boards and watched pages this crawler reads; #17 part A's import only adds history).

## Controller ruling (2026-10-22, Devanshu). It overrides the task text and the 2026-10-21 rulings below where they disagree.

An interim list showed postings from 2016 and felt low-confidence. Devanshu ruled:

1. **Stale postings.** An open posting is stale when its posted date is older than 12 months, or when it was not
   seen live on its board in the latest successful read of that board. A stale posting is shown last, flagged
   "stale" with the reason (for example "posted 2016-03-02, over 12 months" or "not on the board since
   2026-10-18"), and is never counted as a live role for ranking, the daily plan or any "hiring now" signal.
2. **Stale is never a hide and never a close.** The company stays visible. The crawler closes nothing because a
   posting is stale: closing keeps its own rules (`gone_from_board` after an `ok` read, `board_empty_twice` after
   the second empty).
3. **A failed read never makes a posting stale** (error is not empty). Only a successful read that lacks the
   posting does. An `error` read changes neither input of the rule in Task 16a.
4. **The 12 months is a config value:** `stale_after_months = 12` in `outreach/crawl/config.toml` (Task 1).
5. **The crawler derives "stale", in one place.** `outreach/crawl/stale.py` (Task 16a) computes it from columns
   that already exist: no schema change, no new column. Ranking (#6) imports `load_stale` and `Stale` from it by
   those names; #15, #12 and #32 do the same and never re-derive the rule. Why here: the crawler owns the inputs
   and what they mean (`last_seen_at` is touched only by an `ok` read that lists the posting; an `unchanged` read
   touches nothing; the first `empty` read leaves postings open), so a change to the read contract changes the rule
   in the same PR. A stored flag would also go out of date between crawls, because the age rule moves with the
   calendar. The rule runs at read time with `today` as a parameter.

## Controller rulings (2026-10-21). These override the task text below where they disagree.

1. **No migration in this plan.** `posting_body.body_sha256` moves into #30's Task 1 (one schema owner). Task 1
   here keeps only the config files; ignore every mention of `0002_posting_body_hash.sql`.
2. **The crawler never writes `read_queue`.** #32 owns every queue write, including priority (its `enqueue_missing`
   sweep runs before each night batch and on `next`). Drop `queue.py`'s `enqueue_reads`, `read_priority`,
   `queue_tier` and `top_company_ids` from Task 9 and their tests; drop the "lands in `read_queue`" flow
   invariant from Task 15 (it moves to #32's flow stage). The crawler still stores the body and its hash, and on a
   changed body sets `decision.provisional = 1, reader_version = NULL`.
3. **Key and token formats** are #30's "Shared formats", as this plan already proposes.
4. **User-Agent:** `outreach-crawler/1.0 (personal job search)`, no contact address, no URL.
5. **Backoff** stays 6, 12, 24 hours; `board_unreadable` after three errors is a Needs-you item, never a close.
6. **Bodies on detail-request ATSs** are fetched once per new posting, and again when the list shows a changed
   title or updated date; lane 1 postings are re-fetched monthly.
7. **A watched page that is unreadable** (JavaScript only, or three fetch errors) raises a `flagged` review item
   with the reason "careers page unreadable", once per evidence key.
8. **First read of a board created from a name-only match** (#31 ruling 3). A board's first read is the one made
   while `board.last_read IS NULL`. When that board has `found_by = 'stapply_name'` and either (a) the org name the
   adapter reports (`ReadResult.board_name`, non-empty) has a `name_key` that is none of the company's name alias
   keys, or (b) the company has `lca` evidence (every LCA company is a filer of 25 or fewer, by construction in #31)
   and the board lists over 300 jobs, raise one `flagged` review item: `board_id` set, `raised_by 'crawler'`,
   `evidence_key 'board-doubtful:<board_id>'`, reason starting "board match doubtful" (assert that prefix in the test).
   The item is raised before any posting of that read is written (Task 11 phase 2b), and `board.last_read` is set
   last, so "first read has happened" always means "the check ran". No evidence row: ranking ignores a
   `stapply_name` board until its first read has happened and the open item is resolved (#6).
9. **One `body_sha256`.** The crawler imports `outreach/store/writes.py::body_sha256` (#30 Task 4: whitespace runs
   collapsed to one space, ends stripped, case kept) and defines none. `outreach/crawl/text.py` has `html_to_text`
   only.
10. **Decisions go through the reader's combiner.** Every `decide_and_store` and `rebuild` call the crawler makes (new,
    changed or body-updated postings, undecided postings, the ruleset-change rebuild at the start of a tick) passes
    `combiner=outreach.reader.combine.reader_combiner(conn)` when `outreach.reader` imports (a lazy import; `None`
    when the package is absent), so a rules hide that a verified reading overturned never comes back without a new
    verified reading. #32's night job runs `apply_all` after its batches as the second guard.
11. **One HTTP layer.** `Response`, `FetchError`, `HttpClient`, `UrllibClient` and the one `FakeClient` are #30's
    (`outreach/net.py`, `tests/fakes.py`). This plan keeps only `PoliteClient` (host buckets and the breaker), which
    wraps any `HttpClient`. The User-Agent is #30's `USER_AGENT`, `outreach-crawler/1.0 (personal job search)`.
12. **Token grammar** is #30's Shared formats (`valid_token`); `safe_token` here calls it.
13. **No reader version in the crawler.** The crawler writes no queue rows (ruling 2), so it has no `reader_version`
    and no top-company list; ignore every remaining mention of either in Tasks 9 to 13 and 15.

## Global Constraints

- Nothing here talks to the network in a test. Tests use saved responses under `tests/fixtures/crawl/` and the injected `FakeClient` (`tests/fakes.py`, #30); an unrouted URL raises. Never run a crawl, a refresh, `refresh/finalcheck.py` or the launchd job from an agent session.
- No live store: tests use `tests/storetest.fresh_store()`. The `tick`, `read` and `probe` commands call `live_checkout.require_live` before anything else.
- Read contract (#3, design 6.1): every read is `ok`, `empty`, `error` or `unchanged`. Error never closes a posting. Empty closes only on the second in a row (`board_empty_twice`). Three errors in a row raise `board_unreadable`. `unchanged` counts as ok for the streaks and touches no posting.
- A 200 counts as `ok` only when the body has the shape the ATS returns. A non-200, a redirect, a transport failure, a non-JSON page or a wrong shape is `error`, never `empty`. A list that may be incomplete (capped, a failed page, pagination ignored) is `error`, never `ok`.
- The 20,000,000-byte read limit of `refresh/finalcheck.py` stays (`max_bytes`).
- Stale (ruling 2026-10-22): `outreach/crawl/stale.py` only reads. Nothing in the crawler closes, hides, re-decides or re-queues a posting because it is stale, and a failed read never makes one stale.
- Every adapter has a `check_adapter` test (foundation Task 6, `tests/contracts.py`), and the `read_boards` stage is registered in the pipeline flow test (`tests/pipeline/`); the flow test stays green on every PR of this plan from Task 15 on.
- The crawler never classifies a title or reads a body for meaning: it calls `outreach.rules` (`classify_title`, `decide_and_store`, `passes`) and nothing else.
- Store rules from the 2026-10-20 scale audit (store plan, "For downstream issues"): no transaction is open across an HTTP request; write batches are 2,000 to 5,000 rows (`last_seen_at` writes one 4 KB page per row); decisions are CPU-bound (about 5.5 ms each) so they run in batches of 100; `board_read` keeps the last 10 rows per board; `PRAGMA wal_checkpoint(PASSIVE)` after every tick; a posting is never deleted; the posting text is `posting_body` plain text (HTML stripped first).
- Writers use `INSERT ... ON CONFLICT DO NOTHING`, never `INSERT OR IGNORE` or `INSERT OR REPLACE`. Timestamps are canonical UTC `strftime('%Y-%m-%dT%H:%M:%SZ', x)`.
- The User-Agent is declared and contains no email address. A host that blocks it is not worked around: the board lands in Needs you.
- Rebuild and the crawler never overlap: a tick runs `rebuild` first when the store's current ruleset is not this code's, then crawls; two ticks never run at once (file lock).
- No em dashes or en dashes in code, comments, docs, fixtures or commit messages. Commit messages end with the trailer the executing session is given.

## Review Focus

Behaviors the spec implies that no one asked a test for; each has a test in the named task.

1. **A truncated list reads `error`, never `ok`.** Workday's 2,000-row cap, a second page that fails, Teamtailor ignoring `offset`, an Oracle `TotalJobsCount` that does not match: an `ok` here would close real postings as `gone_from_board` (Tasks 18, 20, 23; the closing rule in Task 8).
2. **A blocked or wrong 200 reads `error`, never `empty`.** A Cloudflare challenge page, an HTML 200 on a JSON endpoint, JSON of another shape, `{"offers": null}`. The ats-scrapers references write `payload.get("offers") or []`, which turns all of these into "no jobs" (Task 4 helpers, every adapter).
3. **One ATS outage must not raise dozens of Needs-you items.** A host that fails repeatedly trips a breaker; its remaining boards are deferred with no streak change (Tasks 2 and 12).
4. **A board that goes empty twice and comes back must reopen its postings.** Validators are cleared on an empty read so `unchanged` cannot hide the return; a gone posting that reappears reopens (Tasks 7, 8, 11).
5. **A Mac asleep for days must not stampede.** After a long gap, a tick reads boards in a fixed order (today's plan first, never-read next, oldest due after), stops starting boards when the budget is spent, and leaves the rest due (Task 12).
6. **A failed read never makes a posting stale, and a 2016 posting is stale but never closed.** A 500, a 403, an HTML 200 and a transport failure after a good read leave `load_stale` empty; a 2016 posting that the board still lists stays open and is reported stale with its date; a first empty read makes postings stale and the second closes them (Task 16a, Task 15).

## Done when (issue #21 and its comment)

| Line | Where |
|---|---|
| Fake-network tests cover ok, empty, error, two-empties rule, three-errors item, malformed posting | Tasks 4, 7, 11 |
| A run against a scratch store with saved responses writes the expected postings and decisions | Task 11 (`test_greenhouse_fixture_end_to_end`) |
| `unchanged` with ETags (Greenhouse, Lever, Ashby, SmartRecruiters, Breezy, Pinpoint, Teamtailor) and id fingerprints elsewhere | Tasks 4 to 6, 17 to 26 |
| Bodies fetched once per new or changed engineering posting (Greenhouse content, Workday and Oracle detail) | Tasks 4, 9, 11, 18, 20 |
| Cadence with `next_read_at`, floor 6 hours, ceiling 7 days; 30-minute tick with per-host limits | Tasks 2, 10, 12, 13 |
| Weekly watched pages and `careers_changed` | Task 14 |
| Stale postings: older than 12 months (config) or absent from the latest successful read; never after an error; never a hide or a close; one derivation the other streams import (ruling 2026-10-22) | Tasks 1, 16a (`test_a_failed_read_never_makes_a_posting_stale`, `test_a_2016_posting_still_listed_is_stale_by_age_and_stays_open`) |
| Every adapter passes `check_adapter`; the `read_boards` stage is registered in the pipeline flow test with three invariants | Tasks 4 (recipe), 15 |
| After deploy: a staging run on a scratch copy of a #17 part A import reports counts comparable to the refresh (788 companies on 2026-10-18) | Staging check, end of this plan |

Board discovery moved to #31 (sources). The old text about running `crawl_boards.py` after the switch no longer applies; this crawler only reads boards that are already in the store.

---

## File Structure

| File | Responsibility |
|---|---|
| `outreach/crawl/config.toml`, `config.py` | Create: cadence, HTTP, host spacing, batch and tick limits, `stale_after_months`; `CrawlConfig`, `load_crawl_config` |
| `outreach/crawl/polite.py` | Create: `PoliteClient`, `HostTripped`, `bucket_for` |
| `outreach/crawl/text.py` | Create: `html_to_text` (`body_sha256` is #30's) |
| `outreach/crawl/ats/__init__.py`, `_common.py` | Create: adapter registry, shared request and result helpers |
| `outreach/crawl/ats/<ats>.py` | Create: one adapter per ATS (14) |
| `outreach/crawl/streaks.py`, `boardstate.py` | Create: the pure streak rule; board and `board_read` writes, review items |
| `outreach/crawl/postings.py` | Create: `source_key`, diff plan, batched posting writes and closes |
| `outreach/crawl/bodies.py`, `decisions.py`, `queue.py` | Create: body storage and detail backfill; batched `decide_and_store`; queue priority and enqueue |
| `outreach/crawl/cadence.py` | Create: interval rule, error backoff, `due_boards`, today's plan companies |
| `outreach/crawl/ingest.py` | Create: `read_board`, the phases for one board |
| `outreach/crawl/tick.py` | Create: `run_tick`, `read_company_boards`, lock, ruleset check, budget |
| `outreach/crawl/watch.py` | Create: page fingerprint and the weekly watched-page read |
| `outreach/crawl/stale.py` | Create (Task 16a): `Stale`, `months_before`, `stale_reason`, `load_stale`; the one derivation of "stale", standard library only |
| `outreach/crawl/__main__.py` | Create: `tick`, `read`, `probe`, `probe-slugs` |
| `outreach/crawl/probe.py` | Create: `slug_candidates`, `probe_slugs` (Task 26a) |
| `launchd/com.devanshu.outreach-crawl.plist` | Create: template, `StartInterval` 1800 |
| `tests/fixtures/crawl/` | Create: saved responses per ATS |
| `tests/storetest.py` | Modify: `seed_board` |
| `tests/test_crawl_*.py` | Create: one file per module above |
| `tests/pipeline/stages_crawl.py`, `tests/pipeline/test_flow.py` | Create and modify: the `read_boards` stage (`register(harness)`, foundation Task 6 convention) and its invariants |
| `tests/mutation_check.py` | Modify: crawl guarantees |
| `docs/README.md`, `docs/superpowers/plans/2026-10-19-store-and-rules.md` | Modify: Task 27 |

Token grammar: #30's Shared formats (`valid_token`, `outreach/crawl/base.py`): lower-case slugs `^[a-z0-9][a-z0-9._-]*$`; `workday`: `tenant.wdN/site` with `site` as the ATS spells it (for example `adobe.wd5/external_experienced`); `oracle_orc`: `host/siteNumber` with host ending `.oraclecloud.com` and `siteNumber` case kept (for example `eeho.fa.us2.oraclecloud.com/CX_1`). #31 and #17 write exactly this; adapters refuse anything else without a request. `posting.source_key` is `"<ats>:<token>:<native_id>"`.

---

## Task 1: Config (the body hash column moved to #30 Task 1)

**Files:**
- Create: `outreach/crawl/config.toml`, `outreach/crawl/config.py`
- Test: `tests/test_crawl_config.py`

**Interfaces:**
- Consumes: `posting_body.body_sha256` with its CHECK (#30 Task 1; `''` means not hashed yet, as in rows from the #17 import; its schema tests are #30's).
- Produces: `@dataclass(frozen=True) class CrawlConfig` with `floor_s: int`, `ceiling_s: int`, `age_divisor: int`, `defer_s: int`, `user_agent: str`, `timeout_s: float`, `max_bytes: int`, `max_redirects: int`, `default_host_interval_s: float`, `host_interval_s: dict[str, float]`, `breaker_failures: int`, `batch_rows: int`, `decide_batch: int`, `detail_per_read: int`, `boards_per_tick: int`, `tick_budget_s: int`, `top_companies: int`, `watch_interval_s: int`, `watch_per_tick: int`, `watch_min_text_chars: int`, `stale_after_months: int`; `load_crawl_config(path: Path = CONFIG_PATH) -> CrawlConfig`.
- Config values (exact): `floor_s = 21600`, `ceiling_s = 604800`, `age_divisor = 8` (research 6.4), `defer_s = 1800`, `timeout_s = 30`, `max_bytes = 20000000`, `max_redirects = 3`, `default_host_interval_s = 1.0`, `breaker_failures = 5`, `batch_rows = 2000`, `decide_batch = 100`, `detail_per_read = 150`, `boards_per_tick = 500`, `tick_budget_s = 1500`, `top_companies = 300`, `watch_interval_s = 604800`, `watch_per_tick = 40`, `watch_min_text_chars = 200`, `stale_after_months = 12` (ruling 2026-10-22; Task 16a reads it), `user_agent = "outreach-crawler/1.0 (personal job search)"` (equal to #30's `USER_AGENT`). Host spacing in seconds, keyed by domain suffix: `greenhouse.io` 0.25, `lever.co` 0.25, `ashbyhq.com` 0.5, `smartrecruiters.com` 0.2, `workable.com` 2.0, `rippling.com` 0.5, `bamboohr.com` 1.0, `breezy.hr` 1.0, `pinpointhq.com` 0.5, `teamtailor.com` 0.5, `recruitee.com` 0.5, `applytojob.com` 2.0, `myworkdayjobs.com` 1.0, `oraclecloud.com` 1.0.

- [ ] **Step 1: Write the failing tests.** `test_crawl_config.py`: `test_shipped_config_has_the_design_values` (floor 21600, ceiling 604800, age_divisor 8), `test_floor_above_ceiling_is_refused` (`ValueError`), `test_batch_rows_outside_2000_to_5000_is_refused` (1999 and 5001 raise `ValueError`), `test_user_agent_with_an_email_is_refused` (a UA containing `@`), `test_missing_key_is_named` (`KeyError` message names the key), `test_shipped_user_agent_equals_the_shared_one` (`cfg.user_agent == outreach.net.USER_AGENT`), `test_shipped_stale_after_months_is_twelve` (`cfg.stale_after_months == 12`), `test_stale_after_months_outside_1_to_60_is_refused` (0 and 61 raise `ValueError`).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_config -v`. Expected: FAIL (module not found).
- [ ] **Step 3: Create the config.** `load_crawl_config` validates as the tests say and raises `KeyError` naming the missing key (same style as `outreach/rules/config.py`).
- [ ] **Step 4: Run the full suite** `uv run python -m unittest discover -s tests -t .`. Expected: OK (update any test that asserts the store version is 1).
- [ ] **Step 5: Commit** `Crawler: config (#21)`.

## Task 2: Polite client with host buckets and a breaker (the HTTP layer and the fake moved to #30)

**Files:**
- Create: `outreach/crawl/polite.py`
- Test: `tests/test_crawl_polite.py`

**Interfaces:**
- Consumes: `CrawlConfig` (Task 1); from #30 Task 2 (defined there, never here): `Response`, `FetchError`, `HttpClient`, `UrllibClient`, `USER_AGENT` (`outreach/net.py`) and `tests/fakes.py::FakeClient` with `json_response`, `text_response`, `status_response`. The crawler builds `UrllibClient(timeout_s=cfg.timeout_s, max_bytes=cfg.max_bytes, max_redirects=cfg.max_redirects)`.
- Produces in `polite.py`: `class HostTripped(Exception)` (not a `FetchError`, so adapters cannot swallow it; `.bucket: str`); `bucket_for(host: str, cfg: CrawlConfig) -> str` (the longest configured suffix that matches, else the host); `class PoliteClient(inner: HttpClient, cfg: CrawlConfig, *, clock=time.monotonic, sleep=time.sleep)` implementing `HttpClient`: sets `User-Agent` to `cfg.user_agent`, waits so two requests to one bucket start at least that bucket's interval apart, counts failures per bucket (a `FetchError`, status 403, status 429, status 500 or above; any status below 400, 404 or 410 resets the count), raises `HostTripped` before sending once a bucket has `breaker_failures` in a row, and a 429 trips its bucket at once. `tripped(bucket: str) -> bool`.

- [ ] **Step 1: Write the failing tests.** `test_polite_*` with a stub inner client and a fake clock: `sets_the_declared_user_agent` (header equals `cfg.user_agent`, no `@`), `spaces_requests_to_one_bucket` (second call sleeps the configured interval), `buckets_share_by_suffix` (`a.bamboohr.com` and `b.bamboohr.com` share a bucket), `breaker_trips_after_n_failures` (5 failures then `HostTripped` with no request sent), `a_success_resets_the_failure_count`, `a_404_is_not_a_failure`, `a_429_trips_at_once`, `no_request_to_a_tripped_bucket_even_for_another_url`. (The `UrllibClient` tests are #30's.)
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_polite -v`. Expected: FAIL (module not found).
- [ ] **Step 3: Implement** `polite.py` to the interface above.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Crawler: polite wrapper with per-host spacing and a breaker (#21)`.

## Task 3: Body text helpers

**Files:**
- Create: `outreach/crawl/text.py`
- Test: `tests/test_crawl_text.py`

**Interfaces:**
- Produces: `html_to_text(markup: str) -> str` (uses `html.parser.HTMLParser`: drops `script`, `style`, `noscript` and comment content; turns `p`, `br`, `li`, `div`, `tr`, `h1` to `h6`, `ul`, `ol`, `section` boundaries into line breaks so the rules' sentence splitter sees a stop between list items; decodes entities; removes NUL; collapses runs of spaces and tabs inside a line and runs of 3 or more line breaks to 2; strips). The body hash is not defined here: import `body_sha256` from `outreach.store.writes` (#30 Task 4, which owns its tests).

- [ ] **Step 1: Write the failing tests.** `test_list_items_become_lines` (`"<ul><li>We do not sponsor visas</li><li>Remote</li></ul>"` gives `"We do not sponsor visas\nRemote"`), `test_script_style_and_comments_are_dropped`, `test_entities_are_decoded` (`"R&amp;D &nbsp;team"` gives `"R&D team"`), `test_nul_is_removed`, `test_greenhouse_double_escaped_content` (`html_to_text(html.unescape("&lt;p&gt;Hi&lt;/p&gt;"))` is `"Hi"`).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_text -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `html_to_text`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Crawler: plain-text bodies (#21)`.

## Task 4: Adapter plumbing and Greenhouse

**Files:**
- Create: `outreach/crawl/ats/__init__.py`, `outreach/crawl/ats/_common.py`, `outreach/crawl/ats/greenhouse.py`
- Create fixtures: `tests/fixtures/crawl/greenhouse_jobs.json` (3 jobs: one `Software Engineer, New Grad` with HTML-escaped `content`, one `Account Executive`, one with no `content`), `greenhouse_empty.json` (`{"jobs": [], "meta": {"total": 0}}`)
- Test: `tests/test_crawl_ats_common.py`, `tests/test_crawl_ats_greenhouse.py`

**Interfaces:**
- Consumes: `Validators`, `JobRef`, `ReadResult`, `Adapter`, `ids_fingerprint` (`outreach/crawl/base.py`, #30); `HttpClient`, `Response`, `FetchError` (`outreach/net.py`, #30 Task 2); `html_to_text` (Task 3).
- Produces in `_common.py`:
  - `safe_token(ats: str, token: str) -> bool`: `valid_token` from #30 (one grammar, foundation Shared formats; no local regex).
  - `read_error(message: str, *, status: int | None = None) -> ReadResult`.
  - `fetch_list(client: HttpClient, url: str, validators: Validators, *, headers=None, use_etag: bool = False, method: str = "GET", body: bytes | None = None) -> Response | ReadResult`: sends `If-None-Match` when `use_etag` and `validators.etag`; returns `unchanged` (jobs `()`, same validators, `http_status=304`) on 304; returns an `error` result for `FetchError`, a 3xx, or any status other than 200 (with `http_status` and the status in `error`); otherwise the `Response`. `HostTripped` is not caught.
  - `json_body(resp: Response) -> object | ReadResult` (an `error` result when the body is not JSON, for example an HTML page).
  - `finish_list(jobs: Iterable[JobRef], resp: Response, validators: Validators, *, use_etag: bool = False, fingerprint_ids: Iterable[str] | None = None, board_name: str = '') -> ReadResult` (`board_name` is copied into `ReadResult.board_name`): drops repeated `native_id` (first wins); no jobs gives `empty` with empty `Validators()`; when not `use_etag` and the fingerprint (`ids_fingerprint(fingerprint_ids or native ids)`) equals `validators.ids_fingerprint` gives `unchanged`; otherwise `ok` with `Validators(etag=resp.headers.get("etag", "") if use_etag else "", ids_fingerprint=...)`.
  - `d10(value) -> str | None` (the old `finalcheck.d10`: ISO prefix, or epoch seconds or milliseconds, to `YYYY-MM-DD`).
- Produces in `ats/__init__.py`: `ADAPTERS: dict[str, Callable[[HttpClient], Adapter]]` (an explicit dict; each adapter task adds one line); `adapter_for(ats: str, client: HttpClient) -> Adapter | None`; `supported_ats() -> frozenset[str]`.
- Produces `GreenhouseAdapter(client)` (`ats = "greenhouse"`): `list` requests `https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` with `use_etag=True`; the body must be an object whose `jobs` is a list of objects, else `error`; a job without `id` or `title` makes the read `error` (the old code's rule: one malformed job makes the board read `error`, never a partial `ok`); `JobRef`: `native_id=str(id)`, `title`, `url=absolute_url`, `location` from `location.name` (dict) or the string, `country=None`, `posted_on=d10(first_published or updated_at)`, `body=html_to_text(html.unescape(content))` or `None` when `content` is absent; `body()` returns `None` (the list carries it). EU boards: #31 stores a board found at `boards.eu.greenhouse.io` as a plain `greenhouse` token, so when the US host answers 404 for a token the adapter tries the EU API host once, and a 404 from both is `error`, never `empty`; tests `test_a_404_from_the_us_host_tries_the_eu_host_and_reads_it` and `test_a_404_from_both_hosts_is_an_error_not_empty` (from #31's final review, 2026-10-22).

**Adapter test recipe** (every adapter task from here uses it; a task lists only what differs). Each adapter has `tests/test_crawl_ats_<ats>.py` with these tests, built on saved fixtures and `FakeClient`:
0. `test_check_adapter`: script the `FakeClient` with per-URL response lists in call order (read 1 ok, read 2 unchanged: a 304 for an ETag adapter, the same ids for a fingerprint adapter, read 3 a 500; a read that makes more requests lists each in order) and call `check_adapter(self, adapter, token)` from `tests/contracts.py`. If the merged `check_adapter` differs from foundation Task 6's description, follow the merged code.
1. `test_list_ok_parses_jobs`: exact `native_id`, `title`, `location`, `url`, `posted_on` and `country` of every fixture job, and `validators.ids_fingerprint == ids_fingerprint([...])`.
2. `test_empty_board_is_empty`: a valid shape with zero jobs gives `empty`, not `ok` and not `error`.
3. `test_html_or_wrong_shape_is_error`: an HTML 200, a JSON object of another shape, a missing top-level key, a `null` list: each is `error`, never `empty` (Review Focus 2).
4. `test_http_failures_are_errors`: 404, 403, 429, 500, a 302 and a `FetchError` each give `error` with `http_status` set where there is one.
5. `test_validators`: for an ETag adapter, the request carries `If-None-Match`, a 304 gives `unchanged`, an `ok` carries the response ETag; for a fingerprint adapter, a second read with the same ids gives `unchanged` and one added id gives `ok`.
6. `test_job_without_id_or_title_is_error`, `test_duplicate_ids_are_kept_once`, `test_bad_token_makes_no_request` (`FakeClient.calls == []`).
7. `test_body_*` for adapters that have a detail request: plain text on success, `None` (never an exception) on a 404, bad JSON or `FetchError`.
8. For paginated adapters, `test_a_failed_page_is_an_error_not_a_partial_ok` (Review Focus 1).

**Org name.** An adapter fills `ReadResult.board_name` (through `finish_list(..., board_name=...)`) only when its saved fixture shows the org name in the list response; otherwise it stays `''` and the first-read check of ruling 8 falls back to the size rule alone. Never invent a field for it.

Fixtures are trimmed to 2 to 4 jobs and are built from the documented field lists in the research and from `~/Desktop/outreach-scratch/research/work/` (`cond_results.json`, `html/`); an implementer never fetches a live endpoint. Where a shape comes only from the ats-scrapers source (Pinpoint, Rippling), the fixture says so in a leading comment key and Task 13's `probe` is how Devanshu confirms it.

- [ ] **Step 1: Write the failing tests.** `test_crawl_ats_common.py`: `safe_token("greenhouse", ...)` accepts `acme-inc_2.0` and refuses `a/b`, `a b`, `''`, `Acme` and `a%20b`; `fetch_list` 304, 500, 302, `FetchError` and HTML cases; `finish_list` dedupe, empty, fingerprint unchanged, ok validators; `d10` for an ISO string, 1760000000 and 1760000000000. `test_crawl_ats_greenhouse.py`: the recipe items 0 to 6, plus `test_requests_content_true` (URL ends `?content=true`), `test_content_is_plain_text` (no `<`, no `&lt;` in `body`), `test_a_job_without_content_has_no_body`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_ats_common tests.test_crawl_ats_greenhouse -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `_common.py`, the registry and `GreenhouseAdapter`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Crawler: adapter helpers and the Greenhouse adapter with content and ETag (#21)`.

## Task 5: Lever

**Files:**
- Create: `outreach/crawl/ats/lever.py`, `tests/fixtures/crawl/lever_list.json` (a top-level list of 3 postings with `lists` and `additionalPlain`), `lever_empty.json` (`[]`)
- Test: `tests/test_crawl_ats_lever.py`; modify `outreach/crawl/ats/__init__.py` (one line)

**Interfaces:**
- Produces `LeverAdapter` (`ats = "lever"`): `GET https://api.lever.co/v0/postings/{token}?mode=json`, `use_etag=True`; the body must be a list of objects (an object, `null` or an HTML page is `error`); `JobRef`: `native_id=id`, `title=text`, `url=hostedUrl`, `location=categories.location` (or `''`), `country=country` when present, `posted_on=d10(createdAt)` (milliseconds); `body` = `descriptionPlain` (else `html_to_text(description)`), then each `lists[]` item as `text` plus `html_to_text(content)`, then `additionalPlain`, joined by newlines (the old `finalcheck.board` order); `body()` returns `None`. EU boards: #31 stores a board found at `jobs.eu.lever.co` as a plain `lever` token, so a 404 from `api.lever.co` for a token is retried once on the EU API host (`api.eu.lever.co`), and a 404 from both is `error`, never `empty` (from #31's final review, 2026-10-22).

- [ ] **Step 1: Write the failing tests** from the recipe (items 1 to 6) plus `test_a_404_from_the_us_host_tries_the_eu_host_and_reads_it`, `test_a_404_from_both_hosts_is_an_error_not_empty`, `test_body_joins_lists_and_additional` (the three parts appear in order) and `test_a_top_level_object_is_error`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_ats_lever -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `LeverAdapter` and register it.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Crawler: Lever adapter (#21)`.

## Task 6: Ashby

**Files:**
- Create: `outreach/crawl/ats/ashby.py`, `tests/fixtures/crawl/ashby_jobs.json` (3 jobs, one with `isListed: false`), `ashby_empty.json`
- Test: `tests/test_crawl_ats_ashby.py`; modify the registry

**Interfaces:**
- Produces `AshbyAdapter` (`ats = "ashby"`): `GET https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true`, `use_etag=True`; the body must be an object whose `jobs` is a list; jobs with `isListed` false are skipped (not public); `JobRef`: `native_id=id`, `title`, `url=jobUrl`, `location=location` (string), `country=address.postalAddress.addressCountry` when present, `posted_on=d10(publishedAt or updatedAt)`, `body=descriptionPlain` or `html_to_text(descriptionHtml)`; `body()` returns `None`.

- [ ] **Step 1: Write the failing tests** from the recipe plus `test_unlisted_job_is_skipped` and `test_only_unlisted_jobs_is_empty`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_ats_ashby -v`. Expected: FAIL.
- [ ] **Step 3: Implement** and register.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Crawler: Ashby adapter (#21)`.

## Task 7: Streak rule and board state writes

**Files:**
- Create: `outreach/crawl/streaks.py`, `outreach/crawl/boardstate.py`
- Modify: `tests/storetest.py` (add `seed_board(conn, company_id, ats="greenhouse", token="acme") -> int`)
- Test: `tests/test_crawl_streaks.py`, `tests/test_crawl_boardstate.py`

**Interfaces:**
- Produces in `streaks.py` (port of `refresh/board_status.update`, `action`, `needs_review`):
  - `@dataclass(frozen=True) class Streaks: last_read: str; empty_streak: int; error_streak: int; streak_since: str`.
  - `next_streaks(prev_last: str | None, prev_empty: int, prev_error: int, prev_since: str | None, read: str, now: str) -> Streaks`: `ok` and `unchanged` clear both counts; `empty` adds 1 to `empty_streak` and clears `error_streak`; `error` adds 1 to `error_streak` and leaves `empty_streak` alone; `streak_since` is the previous value when `read == prev_last`, else `now`; an unknown `read` raises `ValueError`.
  - `closes_postings(read: str, empty_streak: int) -> bool` (`read == 'empty' and empty_streak >= 2`); `needs_review(error_streak: int) -> bool` (`>= 3`).
- Produces in `boardstate.py`:
  - `@dataclass(frozen=True) class BoardRow: board_id: int; company_id: int; ats: str; token: str; last_read: str | None; empty_streak: int; error_streak: int; streak_since: str | None; etag: str; ids_fingerprint: str; change_interval_s: int | None; next_read_at: str | None; last_read_at: str | None; created_at: str`; `load_board(conn, board_id: int) -> BoardRow`.
  - `write_board_state(conn, board: BoardRow, result: ReadResult, *, run_id: int, now: str, open_postings: int, next_read_at: str, interval_s: int | None) -> Streaks`, one `BEGIN IMMEDIATE` transaction: updates the board (`last_read`, `last_read_at`, the streaks, `streak_since`, `last_error` = the error text or `''`, `next_read_at`, `change_interval_s` when `interval_s` is not None); validators: `ok` stores `etag` and `ids_fingerprint`, `unchanged` keeps them, `empty` clears both (so `unchanged` can never hide a board that came back, Review Focus 4), `error` keeps them; inserts the `board_read` row (`postings` = jobs returned, for `unchanged` the `open_postings` given) with `ON CONFLICT DO NOTHING`, then trims to the 10 newest per board by `(at, run_id)`; raises review items with `raised_by 'crawler'` and `evidence_key = streak_since`: `board_unreadable` when `needs_review`, `board_empty` when the read is `empty`; marks open `board_unreadable` and `board_empty` items `resolved` with `resolution 'cleared'` when the read is `ok` or `unchanged` (an `empty` read clears only `board_unreadable`); appends an `event` (entity `board`, kind `read_state`, actor `crawler`) only when `last_read` changed.

- [ ] **Step 1: Write the failing tests.** `test_crawl_streaks.py` ports every case of `tests/test_board_status.py` (ok resets, first empty kept, second empty closes, error never closes, three errors need review, an error between empties keeps the empty count, unknown read raises) and adds `test_unchanged_clears_like_ok` and `test_since_holds_while_the_read_repeats`. `test_crawl_boardstate.py` (fresh store): `test_ok_stores_validators`, `test_unchanged_keeps_validators_and_clears_streaks`, `test_empty_clears_validators`, `test_error_keeps_validators_and_last_error_is_cleared_by_ok`, `test_board_read_keeps_the_last_ten`, `test_two_errors_raise_no_item_the_third_does` (the item has `kind 'board_unreadable'`, `evidence_key` equal to `streak_since`), `test_an_ok_read_clears_the_open_items`, `test_a_resolved_item_stays_resolved_while_the_same_streak_continues` (marked `marked_ok`, another error in the same streak: no new row), `test_first_empty_raises_board_empty`, `test_event_only_when_the_state_changed`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_streaks tests.test_crawl_boardstate -v`. Expected: FAIL.
- [ ] **Step 3: Implement** both modules.
- [ ] **Step 4: Run** the same command, then the full suite. Expected: OK.
- [ ] **Step 5: Commit** `Crawler: streak rule, board state and board_read writes (#21)`.

## Task 8: Posting diff and writes

**Files:**
- Create: `outreach/crawl/postings.py`
- Test: `tests/test_crawl_postings.py`

**Interfaces:**
- Consumes: `BoardRow` (Task 7), `JobRef`.
- Produces:
  - `source_key(ats: str, token: str, native_id: str) -> str` (`"<ats>:<token>:<native_id>"`); `native_id_of(source_key: str, ats: str, token: str) -> str` (strips the prefix).
  - `@dataclass(frozen=True) class Existing: posting_id: int; title: str; location: str; country: str | None; url: str; closed_at: str | None; close_reason: str | None`; `load_existing(conn, board_id: int) -> dict[str, Existing]` keyed by `source_key`, open and closed rows, read with no transaction open.
  - `@dataclass(frozen=True) class DiffPlan: new: tuple[JobRef, ...]; changed: tuple[tuple[int, JobRef], ...]; present: tuple[int, ...]; reopen: tuple[int, ...]; gone: tuple[int, ...]`; `plan_diff(existing: dict[str, Existing], jobs: Sequence[JobRef], ats: str, token: str) -> DiffPlan`, pure. `changed`: title, location, country or url differs. `reopen`: closed with `close_reason` `gone_from_board` or `board_empty_twice` and listed again (a posting closed by `hand`, `page_closed` or `imported_closed` stays closed and is not `present`). `gone`: open and not listed.
  - `write_postings(conn, board: BoardRow, plan: DiffPlan, now: str, *, batch_rows: int) -> dict[str, int]` (native_id to posting_id for every job in the plan): inserts new rows with `ON CONFLICT(source_key) DO NOTHING` then re-selects ids, updates changed fields, sets `posted_on` only when it is NULL, sets `last_seen_at = max(first_seen_at, now)` for `present`, clears `closed_at` and `close_reason` for `reopen`; one `BEGIN IMMEDIATE` transaction per `batch_rows` rows.
  - `close_postings(conn, posting_ids: Sequence[int], reason: str, now: str, *, batch_rows: int) -> int`: sets `closed_at` and `close_reason` where still open, in batches; never deletes.

- [ ] **Step 1: Write the failing tests** (fresh store, `seed_company`, `seed_board`): `test_new_posting_is_inserted_with_board_and_company`, `test_present_posting_last_seen_is_touched`, `test_changed_title_updates_the_row_and_is_listed_changed`, `test_gone_posting_is_in_gone_and_close_sets_reason`, `test_hand_closed_posting_stays_closed_when_listed`, `test_gone_posting_that_returns_is_reopened`, `test_posted_on_is_not_overwritten`, `test_last_seen_never_before_first_seen` (a `now` earlier than `first_seen_at`), `test_postings_are_never_deleted` (the `posting_never_deleted` trigger still raises on a direct DELETE), `test_batches_commit_separately` (5 postings, `batch_rows=2`: a `set_trace_callback` sees 3 `COMMIT`s), `test_no_transaction_is_left_open`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_postings -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the module.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Crawler: posting diff, batched writes, close and reopen (#21)`.

## Task 9: Bodies, decisions and the reader queue

**Files:**
- Create: `outreach/crawl/bodies.py`, `outreach/crawl/decisions.py`, `outreach/crawl/queue.py`
- Test: `tests/test_crawl_bodies.py`, `tests/test_crawl_decisions.py`, `tests/test_crawl_queue.py`

**Interfaces:**
- Consumes: `body_sha256` (`outreach.store.writes`, #30 Task 4), `decide_and_store`, `passes`, `Decision` from `outreach.rules` (`outreach.rules.decide` for `Decision`), `read_queue` and `company_score` (#30), `BoardRow`, `native_id_of`.
- Produces in `bodies.py`: `stored_sha(conn, posting_id: int) -> str | None` (the stored `body_sha256`; when it is `''` and a body exists, the hash of the stored text; `None` when there is no body); `save_body(conn, posting_id: int, text: str) -> str` (upsert `posting_body(body, body_sha256)`, returns the sha; one statement); `missing_body_ids(conn, board: BoardRow, limit: int) -> list[tuple[int, str]]` (open postings of the board whose current decision has `lane IN ('engineering','intern')` and that have no `posting_body` row, newest first by `posted_on` then `first_seen_at`, as `(posting_id, native_id)`); `fetch_missing_bodies(conn, adapter: Adapter, board: BoardRow, *, limit: int) -> dict[int, str]` (runs with no transaction open; builds a `JobRef` shell from the row; calls `adapter.body`; keeps only non-empty text; a failure for one posting leaves it missing for the next read).
- Produces in `decisions.py`: `decide_postings(conn, posting_ids: Sequence[int], *, batch: int, rules_cfg=None, combiner=None) -> dict[int, Decision]` (each batch in one `BEGIN IMMEDIATE`, calling `decide_and_store(..., combiner=combiner)` inside it; `RulesetNotCurrent` propagates); `reset_provisional(conn, posting_ids: Sequence[int]) -> int` (sets `provisional = 1`, `reader_version = NULL` on the current-ruleset decision rows: a changed body invalidates an earlier reader agreement).
- Produces in `queue.py`: `TIER_PROVISIONAL_HIDE = 3`, `TIER_TOP_LANE1 = 2`, `TIER_REST = 1`; `read_priority(tier: int, age_days: int) -> int` (`tier * 10_000 + 9_999 - min(max(age_days, 0), 9_999)`, so `read_queue`'s `(state, priority DESC, enqueued_at)` index gives design 7.3's order: provisional hides, then lane 1 at top companies, then the rest, newest first inside each); `queue_tier(decision: Decision, *, company_id: int, top_companies: Collection[int]) -> int` (`TIER_PROVISIONAL_HIDE` when `not passes(decision)`, else `TIER_TOP_LANE1` when `decision.level_ok` and the company is in `top_companies`, else `TIER_REST`); `top_company_ids(conn, n: int) -> frozenset[int]` (the `n` highest `company_score.score` rows of the newest `ranker_version`; empty when there are none); `enqueue_reads(conn, items: Iterable[tuple[int, str, int]], reader_version: str | None) -> int` (items are `(posting_id, body_sha256, priority)`; `INSERT ... ON CONFLICT DO NOTHING` with `state 'queued'`; returns rows added; does nothing and returns 0 when `reader_version` is `None`, because #32 owns the version and sweeps stored bodies that have no queue row).

- [ ] **Step 1: Write the failing tests.** Bodies: `test_stored_sha_hashes_an_imported_body_lazily` (row with `body_sha256 = ''`: equals `body_sha256(text)`), `test_save_body_replaces_text_and_hash`, `test_missing_body_ids_only_engineering_without_a_body_newest_first`, `test_fetch_missing_bodies_respects_limit_and_skips_failures`, `test_no_transaction_is_open_during_a_body_request` (the fake adapter asserts `not conn.in_transaction`). Decisions: `test_decide_postings_writes_current_ruleset_rows`, `test_decide_postings_batches` (batch 2 over 5 ids: 3 commits), `test_ruleset_not_current_propagates`, `test_decide_postings_passes_the_combiner_through` (a fake combiner is called once per posting and its `provisional` and `reader_version` are what is stored; with no combiner a posting keeps `provisional = 1`), `test_reset_provisional_after_a_final_decision` (a row set to `provisional 0, reader_version 'v1'` returns to `1` and `NULL`). Queue: `test_read_priority_orders_tiers_then_newest` (tier 3 age 400 beats tier 2 age 0; tier 1 age 1 beats tier 1 age 5), `test_provisional_hide_is_tier_3`, `test_level_ok_at_a_top_company_is_tier_2`, `test_top_company_ids_uses_the_newest_ranker_version_and_is_empty_without_scores`, `test_enqueue_is_idempotent_per_posting_sha_version`, `test_enqueue_without_a_reader_version_writes_nothing`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_bodies tests.test_crawl_decisions tests.test_crawl_queue -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the three modules.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Crawler: bodies, batched decisions and the read queue (#21)`.

## Task 10: Cadence and due boards

**Files:**
- Create: `outreach/crawl/cadence.py`
- Test: `tests/test_crawl_cadence.py`

**Interfaces:**
- Consumes: `CrawlConfig`, `BoardRow`.
- Produces:
  - `next_interval_s(age_since_new_s: float, prev_interval_s: int | None, changed: bool, cfg: CrawlConfig) -> int`: `base = clamp(age_since_new_s / cfg.age_divisor, cfg.floor_s, cfg.ceiling_s)`; when `changed` and `prev_interval_s` is set, `max(cfg.floor_s, min(base, prev_interval_s // 2))`; else `base`. (Grows with time since the last new posting, shrinks on a change.)
  - `error_backoff_s(error_streak: int, cfg: CrawlConfig) -> int`: `min(cfg.ceiling_s, cfg.floor_s * 2 ** (error_streak - 1))`.
  - `add_seconds(ts: str, seconds: int) -> str` (canonical UTC).
  - `last_new_age_s(conn, board: BoardRow, now: str) -> float`: seconds since `max(first_seen_at)` of the board's postings, or since `board.created_at` when it has none.
  - `plan_company_ids(conn, today: str) -> frozenset[int]`: companies with a `daily_list_entry` for `list_date = today` in state `proposed` or `picked`. This is the one place that reads "today's plan"; #15's people-plan rework changes only this function.
  - `due_boards(conn, now: str, *, supported: Collection[str], plan_ids: Collection[int], cfg: CrawlConfig, limit: int, only_company_ids: Collection[int] | None = None, force: bool = False) -> list[BoardRow]`: active boards of companies with `status NOT IN ('closed', 'removed')` and `merged_into IS NULL` (#31's hand-off table says read live companies only (from #31's final review, 2026-10-22)), `ats` in `supported`. A board is due when `next_read_at IS NULL` or `<= now`; a board of a plan company is also due when `last_read_at` is NULL or older than `floor_s` and its `last_read` is not `error` (an erroring board keeps its backoff). `force` (used by `read_company_boards`) ignores `next_read_at` but keeps the floor and the error backoff. Order: plan companies first, then never-read (`next_read_at IS NULL`), then `next_read_at` ascending; at most `limit`.

- [ ] **Step 1: Write the failing tests.** `test_interval_is_floor_for_a_fresh_board` (age 0 and age 2 days both give 21600), `test_interval_grows_with_quiet_time` (14 days gives 151200 = 42 hours), `test_interval_is_capped_at_the_ceiling` (100 days gives 604800), `test_a_change_shrinks_the_interval` (prev 3 days, changed, base 5 days gives 129600; never below the floor), `test_backoff_doubles_to_the_ceiling` (1: 21600, 2: 43200, 3: 86400, 8: 604800), `test_last_new_age_uses_newest_first_seen_or_board_created`, `test_due_boards_filters` (inactive, closed company, removed company, merged company and an `ats` outside `supported` are excluded), `test_never_read_boards_come_before_due_ones`, `test_plan_company_board_is_due_after_the_floor_even_if_next_read_is_later`, `test_plan_company_board_read_an_hour_ago_is_not_forced`, `test_erroring_plan_board_keeps_its_backoff`, `test_plan_boards_are_listed_first`, `test_limit_applies_after_ordering`, `test_force_ignores_next_read_at_but_not_the_floor`, `test_plan_company_ids_reads_todays_proposed_and_picked_only`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_cadence -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the module.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Crawler: cadence, backoff and due-board selection (#21)`.

## Task 11: Read one board

**Files:**
- Create: `outreach/crawl/ingest.py`
- Create fixtures: `tests/fixtures/crawl/greenhouse_changed.json` (the Task 4 board with one body edited, one job gone, one added)
- Test: `tests/test_crawl_ingest.py`

**Interfaces:**
- Consumes: everything from Tasks 3 to 10, `classify_title` (`outreach.rules.title`), `default_config` (`outreach.rules.config`).
- Produces: `@dataclass(frozen=True) class ReadOutcome: board_id: int; result: str; new: int; changed: int; reopened: int; closed: int; decided: int; bodies: int; queued: int; interval_s: int | None`, and `read_board(conn, board: BoardRow, adapter: Adapter, *, run_id: int, now: str, cfg: CrawlConfig, rules_cfg=None, combiner=None) -> ReadOutcome` (`combiner` as in ruling 10). `HostTripped` and `RulesetNotCurrent` propagate; everything else from `adapter.list` becomes an `error` result (the old code's company-level crash safety).
- Phases, each idempotent, none holding a transaction across a request, the board state last:
  1. `adapter.list(board.token, Validators(board.etag, board.ids_fingerprint))`.
  2. For `ok`: `load_existing`, `plan_diff`. For each job with a `body` whose title's lane (`classify_title(...).lane`) is `engineering` or `intern`: hash it; it is a body update when `stored_sha` differs or is `None`.
  2b. **First-read check (ruling 8), before anything is written.** When `board.last_read IS NULL` and `board.found_by = 'stapply_name'` and the result is `ok` or `empty`: apply the name rule and the size rule; on a hit `raise_review_item` `flagged` with `board_id`, `raised_by 'crawler'`, `evidence_key 'board-doubtful:<board_id>'`, reason "board match doubtful: ..." naming both names or the job count (`ON CONFLICT DO NOTHING`, so a re-run raises nothing new). A board with any other `found_by`, or a second read, skips this phase.
  3. `write_postings` (batches of `cfg.batch_rows`), then `save_body` for each body update.
  4. `decide_postings(..., combiner=combiner)` over new, changed, body-updated and every open posting of this board with no current decision (self-healing after a crash); then `reset_provisional` for body-updated existing postings.
  5. `close_postings`: `gone` with `gone_from_board` after an `ok` read only; every open posting of the board with `board_empty_twice` when `closes_postings` (after `next_streaks`); never after `unchanged` or `error`.
  6. Body backfill for all results except `error`: `fetch_missing_bodies` (limit `cfg.detail_per_read`), `save_body`, `decide_postings`, `reset_provisional`. This is the one path for detail-request ATSs (Workday, Oracle, SmartRecruiters and the rest): a new engineering posting is decided on its title, then its body arrives here.
  7. `enqueue_reads` for new engineering and intern postings that have a body and for every posting whose body changed, with `read_priority(queue_tier(...), age_days)` (age from `posted_on`, else `first_seen_at`).
  8. `write_board_state` with `interval_s` from `next_interval_s(last_new_age_s(...), board.change_interval_s, changed, cfg)` on `ok`, `empty` and `unchanged`, and `next_read_at = add_seconds(now, error_backoff_s(streak, cfg))` on `error` (interval left alone). `changed` is true when anything was new, gone, changed, reopened or body-updated. A new engineering posting resets the age because its `first_seen_at` is now.

- [ ] **Step 1: Write the failing tests** (fresh store, `FakeClient`, the real Greenhouse adapter and fixtures, a fixed `now`): `test_first_read_of_a_stapply_name_board_with_another_org_name_raises_the_doubtful_item` (item has `board_id`, `raised_by 'crawler'`, `evidence_key 'board-doubtful:<id>'`, reason prefix "board match doubtful"; the item exists before any posting row does, checked by crashing the read after phase 3); `test_first_read_of_a_small_lca_company_with_over_300_jobs_raises_it`; `test_a_host_matched_board_or_a_second_read_or_a_matching_name_raises_nothing`; `test_the_doubtful_item_is_raised_once`; `test_read_board_passes_the_combiner_to_decisions`; `test_greenhouse_fixture_end_to_end` (postings, bodies only for engineering titles, a decision row under the current ruleset for every posting, board `ok`, one `board_read`, queue rows for the engineering body with the right priority); `test_second_read_with_304_is_unchanged_and_touches_no_posting` (`last_seen_at` equal, no decide, `next_read_at` moved); `test_a_changed_body_is_redecided_requeued_and_made_provisional` (decision pre-set to `provisional 0`; new sha in the queue); `test_a_whitespace_only_body_change_does_nothing`; `test_gone_posting_closes_after_ok_only` (an `error` read and an `unchanged` read close nothing); `test_error_never_closes_and_three_errors_raise_the_item` (Review Focus 2: a 200 HTML page and a 500 both count); `test_first_empty_keeps_postings_second_closes_them` (`board_empty_twice`; an `ok` between resets the count; Task 16a reads the same state as "stale"); `test_empty_twice_then_the_same_board_returns_reopens_postings` (ok A, empty, empty, ok A with the same ETag: the postings are open again, Review Focus 4); `test_a_malformed_posting_makes_the_read_an_error_and_writes_no_posting`; `test_adapter_exception_becomes_an_error_read` (an adapter raising `KeyError`); `test_undecided_posting_is_decided_on_the_next_ok_read` (delete one decision row; the next `ok` read decides it again); `test_detail_backfill_fetches_bodies_for_new_engineering_postings_only` (a fake detail-request adapter; limit respected; remainder fetched on the next read even when that read is `unchanged`); `test_no_queue_rows_without_a_reader_version`; `test_no_transaction_is_open_during_any_request`; `test_new_engineering_posting_resets_the_interval_to_the_floor`; `test_error_sets_next_read_at_by_backoff`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_ingest -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `read_board` in the phase order above.
- [ ] **Step 4: Run** the same command, then the full suite. Expected: OK.
- [ ] **Step 5: Commit** `Crawler: read one board into the store, with rules, bodies and the read queue (#21)`.

## Task 12: The tick

**Files:**
- Create: `outreach/crawl/tick.py`
- Test: `tests/test_crawl_tick.py`

**Interfaces:**
- Consumes: `read_board`, `due_boards`, `plan_company_ids`, `PoliteClient`, `HostTripped`, `adapter_for`, `supported_ats`, `rebuild`, `ruleset_version` (`from outreach.rules import rebuild, ruleset_version`), `start_job`, `finish_job`.
- Produces:
  - `@dataclass(frozen=True) class TickReport: run_id: int | None; rebuilt: bool; read: int; ok: int; empty: int; error: int; unchanged: int; deferred: int; failed: int; new: int; closed: int; queued: int; seconds: float` (also stored as the `job_run` summary).
  - `tick_lock(path: Path)`: context manager over `fcntl.flock(LOCK_EX | LOCK_NB)` on `crawl.lock` beside the store file; raises `AlreadyRunning` (a plain `Exception` subclass defined here) when held.
  - `ensure_ruleset_current(conn, combiner=None) -> bool` (runs `rebuild(conn, combiner=combiner)` first when the store's current ruleset is not `ruleset_version()`; returns whether it did).
  - `resolve_combiner(conn)`: `outreach.reader.combine.reader_combiner(conn)` through a lazy import, `None` when `outreach.reader` is absent (ruling 10).
  - `run_tick(conn, client: HttpClient, *, cfg: CrawlConfig | None = None, rules_cfg=None, combiner=None, now_fn=utcnow, clock=time.monotonic, limit: int | None = None) -> TickReport` (`combiner=None` means `resolve_combiner(conn)`): `ensure_ruleset_current(conn, combiner)`; `start_job(conn, 'read_boards', ruleset_version())`; wrap `client` in `PoliteClient` (a client that is already a `PoliteClient` is used as is); `due_boards(..., limit=limit or cfg.boards_per_tick)`; for each board until `cfg.tick_budget_s` has passed on `clock`: build the adapter (`adapter_for`; a board whose `ats` has no adapter is never selected), call `read_board`; on `HostTripped` defer the board (`next_read_at = now + defer_s`, no `board_read`, no streak change); later boards of that bucket raise it again at their first request, which sends nothing, so they are deferred the same way; on any other exception record the board in the summary as `failed`, set its `next_read_at = now + floor_s`, change no streak (our bug is not the board's error) and carry on; finish the job `ok`, or `partial` when anything failed; always `PRAGMA wal_checkpoint(PASSIVE)` in a `finally`. The watched-page pass (Task 14) is called from the CLI after this, as its own job run.
  - `read_company_boards(conn, client, company_ids: Collection[int], *, cfg=None, rules_cfg=None, combiner=None, now_fn=utcnow) -> TickReport`: the same loop over `due_boards(..., only_company_ids=company_ids, force=True)`, for #15 and #13 to call before a plan is built ("a board whose company is in today's plan is read before the plan is built"); records its own `read_boards` run.

- [ ] **Step 1: Write the failing tests** (fresh store, `FakeClient` routed per board, a fake clock): `test_tick_reads_only_due_boards`; `test_tick_records_a_read_boards_job_run_with_counts`; `test_ruleset_change_rebuild_uses_the_combiner` (a reading-overturned hide stays shown after the rebuild; a tick with `outreach.reader` absent still runs), `test_ruleset_change_rebuilds_before_crawling` (set another `ruleset` row current; after the tick the code's version is current and the job order is `rebuild` then `read_boards`); `test_plan_company_boards_are_read_first`; `test_budget_stops_the_tick_and_leaves_the_rest_due` (each request advances the fake clock 1000 s with budget 1500: 2 of 5 boards read, the others unchanged and still due; Review Focus 5); `test_after_a_long_gap_the_order_is_plan_then_never_read_then_oldest` (Review Focus 5); `test_host_outage_defers_the_rest_without_streaks` (6 Greenhouse boards all 503, `breaker_failures=3`: 3 `error` reads, 3 deferred with `error_streak` still 0 and no `board_read`, and no review item raised; Review Focus 3); `test_a_board_that_crashes_ingest_is_failed_not_errored` (patch `read_board` to raise `sqlite3.IntegrityError` for one board: job `partial`, others read, that board's streaks unchanged, `next_read_at` = now + floor); `test_a_board_with_no_adapter_is_not_touched`; `test_second_tick_while_locked_does_nothing` (`AlreadyRunning`); `test_wal_checkpoint_runs_even_after_an_exception`; `test_no_connection_transaction_is_open_during_a_request` (a `FakeClient` callable asserts `not conn.in_transaction`); `test_read_company_boards_forces_the_floor_not_the_backoff`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_tick -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `tick.py`.
- [ ] **Step 4: Run** the same command, then the full suite. Expected: OK.
- [ ] **Step 5: Commit** `Crawler: the 30-minute tick with a budget, a lock, a ruleset check and a host breaker (#21)`.

## Task 13: Command line and the launchd template

**Files:**
- Create: `outreach/crawl/__main__.py`, `launchd/com.devanshu.outreach-crawl.plist`
- Modify: `outreach/crawl/__init__.py` (export `run_tick` and `read_company_boards` lazily, through a module-level `__getattr__`, so that `import outreach.crawl.stale` loads no adapter, `polite` or `outreach.net` module: ranking imports it, Task 16a)
- Test: `tests/test_crawl_cli.py`

**Interfaces:**
- Produces: `main(argv: list[str] | None = None, *, root: str | None = None, live_root: str | None = None) -> int` with subcommands `tick` (open the store with `connect()`, take `tick_lock`, `run_tick`, then the watched-page pass of Task 14, print a one-line report; exit 0 when another tick holds the lock), `read --company ID [ID ...]` (`read_company_boards`), `probe ATS TOKEN` (one `adapter.list` through a `PoliteClient`, no store access, prints result kind, job count, ETag and the first 5 titles; this is how Devanshu confirms a token or an adapter shape on first use). Every subcommand calls `live_checkout.require_live(<what>, root, live_root)` first, before opening anything. The combiner comes from `resolve_combiner` (Task 12; the reader's `reader_combiner` through a lazy import, none when `outreach.reader` is absent); there is no reader version lookup, because the crawler writes no queue rows (ruling 2).
- Produces the plist template: label `com.devanshu.outreach-crawl`, `StartInterval` 1800, program `/opt/homebrew/bin/python3 -m outreach.crawl tick` run from `/Users/devanshu/Desktop/Sept 23 Companies` in the same `zsh -c 'cd ... && PATH=... command >> log 2>&1'` shape as the other outreach jobs, logging to `~/Library/Application Support/outreach/crawl.out`. A comment says `StartInterval` misses ticks while the Mac sleeps and that is safe: the next tick finds more due.

- [ ] **Step 1: Write the failing tests.** `test_tick_refuses_outside_the_live_checkout` (`main(["tick"], root=ROOT, live_root=<temp dir>)` raises `SystemExit` whose message contains "live checkout", and nothing is opened), `test_probe_refuses_outside_the_live_checkout`, `test_read_refuses_outside_the_live_checkout`, `test_plist_is_valid_and_runs_every_1800_seconds` (parse with `plistlib`; `StartInterval == 1800`; label as above), `test_plist_has_no_secrets_or_email` (no `@`).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_cli -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `__main__.py` and the template.
- [ ] **Step 4: Run** the same command. Expected: PASS. Installing the plist into `~/Library/LaunchAgents` is a deploy step Devanshu approves (see Deploy); do not run `launchctl`.
- [ ] **Step 5: Commit** `Crawler: tick, read and probe commands, and the launchd template (#21)`.

## Task 14: Watched careers pages

**Files:**
- Create: `outreach/crawl/watch.py`, `tests/fixtures/crawl/careers_a.html`, `careers_a_changed.html` (one new opening, a different copyright year and date), `careers_challenge.html` (a "Just a moment" page)
- Test: `tests/test_crawl_watch.py`
- Modify: `outreach/crawl/__main__.py` (call the pass after the board tick)

**Interfaces:**
- Consumes: `watched_page` (`company_id`, `url`, `fingerprint`, `last_fetch_at`, `last_change_at`, `next_fetch_at`, `error_streak`; #31 also creates one for a company whose only boards have no adapter here, with a careers page or an iCIMS host as `url`, and those are read like any other), `review_item` kind `careers_changed`, job `watch_pages` (#30), `html_to_text`.
- Produces: `page_fingerprint(markup: str) -> str` (sha256 of the text after `html_to_text`, lower-cased, with dates removed: ISO dates, `Month D, YYYY`, `D Month YYYY`, `M/D/YYYY`, `N minutes|hours|days|weeks|months ago`, and `(c)`, `©` or `copyright` followed by a year; years standing alone are kept, because "2027 new grad" is content); `usable_text(markup: str, min_chars: int) -> bool` (false for text shorter than `min_chars` or a challenge page: "just a moment", "enable javascript", "access denied", "captcha"); `watch_pages(conn, client: HttpClient, *, cfg=None, now_fn=utcnow, limit: int | None = None) -> WatchReport` where `WatchReport` counts `baseline`, `unchanged`, `changed`, `unstable`, `error`. Due rows: `next_fetch_at` NULL or past, company not `closed`, not `removed` and not merged, at most `cfg.watch_per_tick`. One request per page with `follow_redirects=True`; a non-200 or an unusable page is `error` and never changes the fingerprint; `next_fetch_at = now + watch_interval_s` after a read, `now + min(watch_interval_s, 86400 * 2 ** (error_streak - 1))` after an error. First usable read stores the fingerprint and raises nothing (`baseline`). A different fingerprint is fetched once more at once; if the two fetches disagree the page is `unstable` (nothing stored, nothing raised); if they agree, store it, set `last_change_at`, and raise a `careers_changed` review item (`raised_by 'crawler'`, `evidence_key` the first 16 hex of the new fingerprint, `reason` "Careers page changed: <url>"). One `job_run` of job `watch_pages`.

- [ ] **Step 1: Write the failing tests.** `test_fingerprint_ignores_dates_and_copyright_year`, `test_fingerprint_ignores_scripts_and_styles`, `test_fingerprint_sees_a_new_opening`, `test_fingerprint_keeps_a_standalone_year`, `test_challenge_page_is_not_usable`, `test_short_page_is_not_usable`, `test_first_read_is_a_baseline_with_no_item`, `test_same_page_is_unchanged_and_not_due_for_7_days`, `test_changed_page_raises_one_careers_changed_item`, `test_the_same_change_twice_raises_no_second_item` (same `evidence_key`), `test_unstable_page_raises_nothing` (the second fetch differs from the first), `test_403_and_challenge_never_raise_a_change` (fingerprint and `last_change_at` untouched, `error_streak` +1), `test_error_backoff_doubles_to_7_days`, `test_closed_and_removed_companies_are_skipped`, `test_no_transaction_is_open_during_a_request`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_watch -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `watch.py` and the call in `__main__.py`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Crawler: weekly watched careers pages and the careers_changed item (#21)`.

## Task 15: The `read_boards` flow stage and its invariants

Foundation Task 6 gave every stream a `PipelineHarness` (`.conn`, `.fixture(name)`, ordered `.stages`) and one flow test. This task registers the crawler's stage so a break between the crawler and its neighbors fails here.

**Files:**
- Create: `tests/pipeline/stages_crawl.py`
- Modify: `tests/pipeline/test_flow.py`, `tests/fixtures/pipeline/` (a Greenhouse-shaped board response with one new-grad engineering posting and one non-engineering posting, if the foundation fixture is not already that)

**Interfaces:**
- Consumes: `PipelineHarness` and `test_flow_runs_every_registered_stage` (foundation Task 6; follow the merged names if they differ), `run_tick`, `FakeClient` (`tests/fakes.py`).
- Produces: `register(h: PipelineHarness) -> None` appending the stage named `read_boards` (its name and position are fixed by foundation Task 6: after `find_boards`, before `read_postings`). The stage seeds one company and one active Greenhouse board in `h.conn` only when none exists (a stream that runs earlier, #31, may have created them: the crawler never creates board rows), runs `run_tick` with a `FakeClient` serving the fixture, and records the run in `h.conn`. The flow test imports it through `harness.py` (foundation Task 6 convention); an invariant counts only the postings this stage's own board made.

- [ ] **Step 1: Write the failing tests** in `tests/pipeline/test_flow.py`, each running the registered stages (the `read_boards` stage twice or three times with a changed `FakeClient` route where noted):
  - `test_flow_read_boards_second_read_is_unchanged_and_writes_no_posting_rows`: the first run reads `ok`; the second, with the fake answering 304 to the stored ETag, records `board_read.result = 'unchanged'` and a snapshot of `SELECT posting_id, title, last_seen_at, closed_at FROM posting ORDER BY posting_id` is identical before and after.
  - `test_flow_read_boards_new_engineering_posting_is_queued_with_the_right_priority`: exactly one `read_queue` row, for the engineering posting, `state = 'queued'`, `reader_version = 'flow-test'`, `body_sha256` equal to `body_sha256(posting_body.body)`, `priority == read_priority(TIER_REST, age_days)`; no row for the non-engineering posting.
  - `test_flow_read_boards_error_never_closes`: a third run with the fake answering 500 leaves both postings open (`closed_at IS NULL`), `board.last_read = 'error'`, `error_streak = 1`, no `read_queue` row is added, and `load_stale(h.conn, today)` is empty (a failed read never makes a posting stale).
  - `test_flow_read_boards_a_2016_posting_is_stale_and_stays_open`: a board whose fake serves `tests/fixtures/crawl/greenhouse_2016.json` (Task 16a) reads `ok`; the posting has `posted_on = '2016-03-02'`, is open with a current decision, and `load_stale` returns `Stale(('old',), 'posted 2016-03-02, over 12 months')` for it; the company's other posting from the normal fixture is not stale.
  - `test_flow_read_boards_a_first_empty_read_makes_postings_stale_and_the_second_closes_them`: ok, then an empty answer: both postings open and stale with reason `not on the board at its latest read (last listed <date of the ok read> or later)`; a second empty answer closes both with `board_empty_twice` and `load_stale` is empty.
- [ ] **Step 2: Run** `uv run python -m unittest tests.pipeline.test_flow -v`. Expected: the three new tests FAIL (stage not registered).
- [ ] **Step 3: Implement** `register` in `stages_crawl.py`; `harness.py` imports it in stage order (foundation Task 6).
- [ ] **Step 4: Run** the same command, then the full suite. Expected: OK.
- [ ] **Step 5: Commit** `Tests: read_boards stage and flow invariants for the crawler (#21)`.

## Task 16: Mutation check entries

**Files:**
- Modify: `tests/mutation_check.py`

**Interfaces:**
- Consumes: the `MUTATIONS` tuple format `(file, guarantee broken, find, replace, expect)` and its anchor rules (exact text, first occurrence, a missing anchor fails the run).

- [ ] **Step 1: Add entries** that each break one crawler guarantee, with `expect` naming the test that must fail: `streaks.closes_postings` threshold 2 changed to 1 (`test_first_empty_keeps_postings_second_closes_them`), `needs_review` threshold 3 changed to 2, `write_board_state` no longer clearing validators on `empty` (`test_empty_twice_then_the_same_board_returns_reopens_postings`), `close_postings` called after an `error` read in `read_board` (`test_error_never_closes`), `finish_list` returning `ok` for an empty list, `PoliteClient` no longer counting a 403 as a failure, `read_priority` tier weight reversed, `watch_pages` raising on a non-200, `stale.stale_reason` using `<=` for the age cutoff (`test_exactly_twelve_months_is_not_stale_and_a_day_more_is`), `stale.stale_reason` treating `error_streak >= 1` as absence in place of `empty_streak >= 1` (`test_a_failed_read_never_makes_a_posting_stale`), `stale.load_stale` returning closed postings (`test_closed_posting_is_not_stale`).
- [ ] **Step 2: Run** `uv run python tests/mutation_check.py` (not from the live checkout). Expected: every mutation CAUGHT, `missed 0`.
- [ ] **Step 3: Commit** `Mutation check: crawler guarantees have a test that notices (#21)`.

## Task 16a: Stale postings (derived from `posted_on`, `last_seen_at` and the board read state; no schema change)

Devanshu's ruling of 2026-10-22 (top of this plan). Build order: Steps 1 to 4 need only Task 1 (config) and Task 7's `seed_board`, so this task can ship right after Task 8 and unblock #6, #15 and #12. The tests marked "(through `read_board`)" need Task 11 and are added in Step 5 when it lands.

**Files:**
- Create: `outreach/crawl/stale.py`, `tests/fixtures/crawl/greenhouse_2016.json` (the Task 4 Greenhouse shape with one `Software Engineer, New Grad` whose `first_published` is `2016-03-02T09:00:00-05:00`, with `content`, still listed by the board)
- Test: `tests/test_crawl_stale.py`

**Interfaces:**
- Consumes: `CrawlConfig.stale_after_months` and `load_crawl_config` (Task 1); `board.empty_streak` (written by Task 7); `posting.posted_on`, `last_seen_at`, `closed_at` (written by Task 8).
- Produces in `stale.py` (standard library plus `outreach.crawl.config` only; it imports nothing from `outreach.crawl.ats`, `polite` or `outreach.net`):
  - `STALE_OLD = "old"`, `STALE_NOT_ON_BOARD = "not_on_board"`.
  - `@dataclass(frozen=True) class Stale: kinds: tuple[str, ...]; reason: str` (`kinds` holds `old`, `not_on_board` or both, in that order; `reason` is each kind's text joined by `"; "`).
  - `months_before(day: date, months: int) -> date`: calendar months back, the day clamped to the month's end (2026-03-31 minus 1 month is 2026-02-28).
  - `stale_reason(*, posted_on: str | None, last_seen_at: str, closed_at: str | None, board_empty_streak: int | None, today: date, max_age_months: int) -> Stale | None`, pure:
    - A closed posting (`closed_at` set) is never stale: closed is its own state.
    - **Old:** `posted_on` is set and `posted_on < months_before(today, max_age_months)` (exactly 12 months old is not stale). Text: `posted <posted_on>, over <max_age_months> months` (`month` when 1).
    - **Not on the board:** `board_empty_streak is not None and board_empty_streak >= 1`. Text: `not on the board at its latest read (last listed <date(last_seen_at)> or later)`. Why this input is complete: after an `ok` read the crawler closes every posting the board no longer lists (`gone_from_board`), after the second empty it closes them all (`board_empty_twice`), and `unchanged` means the same ids, so the only state in which an open posting is absent from the latest successful read is a first `empty` read (`empty_streak = 1`). `last_seen_at` is touched only by an `ok` read that lists the posting, so its date is the last day an `ok` read listed it, and the board may have listed it on later `unchanged` reads: hence the text says "last listed <date> or later". An `error` read leaves `empty_streak` and `last_seen_at` alone (Task 7), so an error never adds the flag, and an error after an empty keeps it (the latest successful read still lacks the posting).
    - No board (`board_empty_streak is None`, a `url:` posting) and a board that was never read (streak 0) have no absence signal: only the age rule can apply (open question 12).
  - `load_stale(conn, today: date, *, max_age_months: int | None = None, posting_ids: Collection[int] | None = None, company_ids: Collection[int] | None = None) -> dict[int, Stale]`: one read-only query over open postings left-joined to `board` (`empty_streak`), `stale_reason` per row, only stale ones returned, keyed by `posting_id`. `max_age_months` defaults to `load_crawl_config().stale_after_months`. It writes nothing and opens no transaction.

- [ ] **Step 1: Write the failing tests** (fresh store, `seed_company`, `seed_board`, postings inserted directly with chosen `posted_on`, `last_seen_at` and board `empty_streak`; today is `date(2026, 10, 22)`):
  - `test_months_before_clamps_to_month_end` (2026-03-31 minus 1 gives 2026-02-28; 2024-02-29 minus 12 gives 2023-02-28; 2026-10-22 minus 12 gives 2025-10-22).
  - `test_exactly_twelve_months_is_not_stale_and_a_day_more_is` (`posted_on` 2025-10-22 gives `None`; 2025-10-21 gives `Stale(('old',), 'posted 2025-10-21, over 12 months')`).
  - `test_a_2016_posting_is_stale_by_age_with_its_date` (`posted_on` 2016-03-02 gives reason exactly `posted 2016-03-02, over 12 months`).
  - `test_the_months_come_from_the_argument_and_the_config` (`max_age_months=6` marks a 7-month-old posting and not a 5-month-old one, with the text `over 6 months`; `load_stale` with no argument uses the shipped 12; a `CrawlConfig` patched to 18 leaves a 14-month-old posting live).
  - `test_no_posted_on_is_not_stale_by_age` (`None` and a Workday posting whose date is unknown).
  - `test_first_empty_read_state_is_not_on_the_board_with_the_last_seen_date` (`board_empty_streak=1`, `last_seen_at` 2026-10-18T08:00:00Z gives reason exactly `not on the board at its latest read (last listed 2026-10-18 or later)`).
  - `test_error_streak_alone_never_makes_a_posting_stale` (a board with `last_read 'error'`, `error_streak 3`, `empty_streak 0` and a `last_seen_at` six months old: `load_stale` returns nothing).
  - `test_an_error_after_an_empty_keeps_the_postings_stale` (`last_read 'error'`, `empty_streak 1`: still stale, still open).
  - `test_unchanged_reads_do_not_make_postings_stale` (board `last_read 'unchanged'`, `last_seen_at` 40 days old: none stale).
  - `test_posting_on_a_never_read_board_or_with_no_board_is_not_stale_by_absence` (`last_read NULL`, and a `url:` posting; both stale only through the age rule).
  - `test_closed_posting_is_not_stale` (closed for any `close_reason`, including a 2016 one: not returned).
  - `test_both_reasons_are_joined_in_order` (a 2016 posting on a board with `empty_streak 1`: `kinds == ('old', 'not_on_board')`, reason `posted 2016-03-02, over 12 months; not on the board at its latest read (last listed 2026-10-18 or later)`).
  - `test_load_stale_filters_by_posting_and_company_and_returns_only_stale` (the two filter arguments narrow the result; live postings are absent from the dict).
  - `test_load_stale_is_one_read_only_query` (a `set_trace_callback` sees one `SELECT`, no `BEGIN`, and the store's row counts are unchanged).
  - `test_importing_stale_loads_no_adapter_or_http_module` (a subprocess `python -c "import sys, outreach.crawl.stale; print([m for m in sys.modules if m.startswith(('outreach.crawl.ats', 'outreach.crawl.polite', 'outreach.net'))])"` prints `[]`).
  - `test_reasons_have_no_dashes`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_stale -v`. Expected: FAIL (module not found).
- [ ] **Step 3: Implement** `stale.py` to the interfaces above.
- [ ] **Step 4: Run** the same command, then the full suite. Expected: PASS and OK.
- [ ] **Step 5: When Task 11 has landed, add the tests (through `read_board`)**, with the real Greenhouse adapter and `FakeClient`, one fixed `now` per read:
  - `test_a_2016_posting_still_listed_is_stale_by_age_and_stays_open` (serves `greenhouse_2016.json`: the read is `ok`, `posted_on` is `2016-03-02`, `closed_at` is NULL, the posting has a current decision, and `load_stale` returns `Stale(('old',), 'posted 2016-03-02, over 12 months')`; the crawler closed and hid nothing).
  - `test_first_empty_read_makes_open_postings_stale_and_the_second_closes_them` (ok on 2026-10-18, empty on 2026-10-20: both open, `load_stale` reason `not on the board at its latest read (last listed 2026-10-18 or later)`; a second empty closes them with `board_empty_twice` and `load_stale` is empty).
  - `test_a_failed_read_never_makes_a_posting_stale` (ok, then in turn a 500, a 403, a 429, an HTML 200 and a `FetchError`, and three errors in a row so `board_unreadable` is raised: after each, `load_stale` is empty and both postings are open).
  - `test_a_listed_posting_after_an_empty_read_is_live_again` (ok A, empty, ok A with the same ETag as before: not stale, `last_seen_at` is the third read's).
  - `test_stale_never_changes_what_the_crawler_writes` (the same board read once with the 2016 fixture and once with a 2026 copy writes the same rows apart from `posted_on`: the same decision, body and `board_read` result, no extra review item).
- [ ] **Step 6: Commit** `Crawler: stale postings, derived from posted date and the board read state (#21)`.

---

The adapters below are independent of each other and of Tasks 7 to 16 once `_common.py` exists; each is its own commit and can ship alone. They are in measured order (Simplify new-grad list companies, then the stapply and LCA-sample mix in research 1.3 and 1.4). Once #31 has matched boards, run `SELECT ats, count(*) FROM board GROUP BY ats ORDER BY 2 DESC` on the staging store and move tasks 17 to 26 into that order. Every task follows the Adapter test recipe in Task 4 and ends with: run `uv run python -m unittest tests.test_crawl_ats_<ats> -v` red, implement and register in `ats/__init__.py`, run green, commit `Crawler: <ATS> adapter (#21)`.

## Task 17: SmartRecruiters

**Files:** Create `outreach/crawl/ats/smartrecruiters.py`, fixtures `smartrecruiters_p0.json` (a page with `totalFound` 3 and 2 postings), `smartrecruiters_p1.json`, `smartrecruiters_detail.json`; test `tests/test_crawl_ats_smartrecruiters.py`.

**Interfaces:**
- `SmartRecruitersAdapter` (`ats = "smartrecruiters"`, token the company id): list pages `GET https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100&offset=N` until `totalFound` is reached; `use_etag=True` on the first page only (a 304 there is `unchanged`; `totalFound` is in that page, so a count change flips its ETag); the collected distinct ids must equal `totalFound`, else `error` (Review Focus 1); `JobRef`: `native_id=id`, `title=name`, `url=https://jobs.smartrecruiters.com/{token}/{id}`, `location` from `location.city` and `location.region`, `country` = `location.country` upper case, `posted_on=d10(releasedDate)`, `body=None`. `body()`: `GET .../postings/{id}`, joined `jobAd.sections.{companyDescription,jobDescription,qualifications,additionalInformation}.text` through `html_to_text`.
- Extra tests: `test_two_pages_are_joined`, `test_count_mismatch_is_an_error`, `test_second_page_failure_is_an_error_not_partial` (recipe 8), `test_etag_only_on_the_first_page`.

## Task 18: Workday

**Files:** Create `outreach/crawl/ats/workday.py`, fixtures `workday_list.json` (`total` 3, 3 `jobPostings`, one `postedOn` "Posted Today", one "Posted 5 Days Ago", one "Posted 30+ Days Ago"), `workday_capped.json` (`total` 2000 with a `jobFamilyGroup` facet), `workday_family.json`, `workday_detail.json` (with `jobPostingInfo.jobDescription` and `startDate`); test `tests/test_crawl_ats_workday.py`.

**Interfaces:**
- `WorkdayAdapter` (`ats = "workday"`, token `tenant.wdN/site`, validated by `^[a-z0-9-]+\.wd\d+/[A-Za-z0-9_-]+$`): `POST https://{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` with `{"limit": 20, "offset": N, "searchText": "", "appliedFacets": {}}` (Workday refuses a limit above 20); pages until `total`. `total == 2000` is the cap (offsets past it wrap to page 1): subdivide by the `jobFamilyGroup` facet values from the first response (`appliedFacets: {"jobFamilyGroup": [id]}`), union by `externalPath`; if any family itself reports 2000, or any page fails, the read is `error` ("list capped", Review Focus 1). `JobRef`: `native_id=externalPath`, `title`, `url=https://{tenant}.{wdN}.myworkdayjobs.com/{site}{externalPath}`, `location=locationsText`, `country=None`, `posted_on` from `postedOn` via `posted_on_from_relative(text: str, today: date) -> str | None` ("Posted Today" is today, "Posted Yesterday", "Posted N Days Ago" is today minus N, "Posted 30+ Days Ago" is `None`), `body=None`. No ETag: fingerprint of `externalPath` ids (`use_etag=False`). `body()`: `GET https://{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{externalPath}` -> `html_to_text(jobPostingInfo.jobDescription)`, with a first line `Start date: <startDate>` when `startDate` is present (an exact date the reader can quote).
- Extra tests: `test_posted_on_from_relative_cases`, `test_capped_list_subdivides_by_family`, `test_family_still_capped_is_an_error`, `test_page_failure_mid_list_is_an_error`, `test_limit_is_20`, `test_body_has_start_date_line`.

## Task 19: Workable

**Files:** Create `outreach/crawl/ats/workable.py`, fixtures `workable_list.json` (`jobs[]` with `shortcode`, `title`, `city`, `state`, `country`, `url`, `published_on`, `description`), `workable_empty.json`, `workable_md.txt`; test `tests/test_crawl_ats_workable.py`.

**Interfaces:**
- `WorkableAdapter` (`ats = "workable"`): `GET https://apply.workable.com/api/v1/widget/accounts/{token}?details=true`; the body must be an object with a `jobs` list; no ETag, fingerprint on `shortcode`s; `JobRef`: `native_id=shortcode`, `title`, `url`, `location` from `city`, `state`, `country`, `country=country` when it is a 2-letter code, `posted_on=d10(published_on or created_at)`, `body=html_to_text(description)` or `None`. `body()`: `GET https://apply.workable.com/{token}/jobs/view/{shortcode}.md` as text. A 200 with zero jobs is `empty` (the widget answers for unrelated slugs; the `board_empty` item is the right outcome).
- Extra tests: `test_zero_jobs_200_is_empty`, `test_markdown_body_fallback`.

## Task 20: Oracle (Oracle Recruiting Cloud)

**Files:** Create `outreach/crawl/ats/oracle_orc.py`, fixtures `oracle_p0.json` (`items[0].TotalJobsCount` 3, `requisitionList` of 2), `oracle_p1.json`, `oracle_detail.json` (the three external fields); test `tests/test_crawl_ats_oracle_orc.py`.

**Interfaces:**
- `OracleAdapter` (`ats = "oracle_orc"`, token `host/siteNumber`, host must end `.oraclecloud.com`): pages of 200 through `GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList&finder=findReqs;siteNumber={site},limit=200,offset=N` (Oracle ignores a top-level `limit`); jobs are `items[0].requisitionList`, the total is `items[0].TotalJobsCount`; distinct ids collected must equal the total, else `error` (Review Focus 1); `JobRef`: `native_id=Id`, `title=Title`, `url=https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{Id}`, `location=PrimaryLocation`, `country=PrimaryLocationCountry`, `posted_on=d10(PostedDate)`, `body=None`; no ETag, fingerprint. `body()`: `GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id="{id}",siteNumber={site}` -> `ExternalDescriptionStr`, `ExternalResponsibilitiesStr`, `ExternalQualificationsStr` through `html_to_text`, joined by newlines.
- Extra tests: `test_count_mismatch_is_an_error`, `test_host_outside_oraclecloud_makes_no_request`, `test_body_joins_the_three_fields`.

## Task 21: Rippling

**Files:** Create `outreach/crawl/ats/rippling.py`, fixtures `rippling_list.json`, `rippling_detail.json` (shapes from `~/Desktop/outreach-scratch/research/work/ats_src/rippling.py` and the research field list; mark them "shape from ats-scrapers" in the file); test `tests/test_crawl_ats_rippling.py`.

**Interfaces:**
- `RipplingAdapter` (`ats = "rippling"`): `GET https://api.rippling.com/platform/api/ats/v1/board/{token}/jobs`; accepts a top-level list, or an object with `items` or `jobs` as a list, and refuses anything else (a missing key is `error`, not empty); no ETag (`no-store`), fingerprint; `JobRef`: `native_id=id`, `title=name` (else `title`), `url` the job's own URL field or `https://ats.rippling.com/{token}/jobs/{id}`, `location` from `workLocation` (label or string), `country=None`, `posted_on=None`, `body=None`. `body()`: `GET .../board/{token}/jobs/{id}` -> `html_to_text(description.company + description.role)`.
- Extra tests: `test_object_with_items_and_list_forms_both_parse`, `test_object_without_items_or_jobs_is_error`.

## Task 22: Breezy and Pinpoint

**Files:** Create `outreach/crawl/ats/breezy.py`, `pinpoint.py`, fixtures `breezy_list.json`, `breezy_detail.html` (a `<div class="description">` block), `pinpoint_list.json` (`{"data": [...]}`), `pinpoint_empty.json`; tests `tests/test_crawl_ats_breezy.py`, `tests/test_crawl_ats_pinpoint.py`.

**Interfaces:**
- `BreezyAdapter` (`ats = "breezy"`): `GET https://{token}.breezy.hr/json`, `use_etag=True`, no redirect followed: a 302 (a tenant with no site goes to the marketing page) is `error`; the body must be a list; `JobRef`: `native_id=id`, `title=name`, `url`, `location` from the structured `location` (`name`, else `city` and `state.name`), `country=location.country.id` when present, `posted_on=d10(published_date)`, `body=None`. `body()`: `GET job.url` with `follow_redirects=True` -> the `class="description"` block through `html_to_text` (parse with `HTMLParser`, no third-party package).
- `PinpointAdapter` (`ats = "pinpoint"`): `GET https://{token}.pinpointhq.com/postings.json`, `use_etag=True`, no redirect; the body must be an object whose `data` is a list; `JobRef`: `native_id=id`, `title`, `url`, `location=location.name` (else `city`, `province`), `country=None`, `posted_on=None`, `body=html_to_text(html.unescape(description))`; `body()` returns `None`.
- Extra tests: `test_breezy_redirect_to_marketing_site_is_error`, `test_breezy_body_from_description_block`, `test_pinpoint_data_not_a_list_is_error`.

## Task 23: Teamtailor

**Files:** Create `outreach/crawl/ats/teamtailor.py`, fixtures `teamtailor_p0.xml` (an RSS feed of 100 `item`s is too long: the test builds the pages from a 2-item template with `per_page` set to 2 through a module constant `PAGE_SIZE`), `teamtailor_html.html`; test `tests/test_crawl_ats_teamtailor.py`.

**Interfaces:**
- `TeamtailorAdapter` (`ats = "teamtailor"`): `GET https://{token}.teamtailor.com/jobs.rss?per_page={PAGE_SIZE}&offset=N` with `PAGE_SIZE = 100`, `use_etag=True` on the first page only; parse with `xml.etree.ElementTree`; the root must be `rss` (an HTML page is `error`, never empty); `JobRef`: `native_id` the number in `/jobs/(\d+)` of `link` (else `guid`), `title`, `url=link`, `location` from `tt:locations`, `posted_on=pubDate` as a date, `body=html_to_text(html.unescape(description))`. Paging stops when a page has fewer than `PAGE_SIZE` items. A full first page followed by a page that repeats ids already seen means `offset` was ignored: the read is `error` ("pagination not honored", Review Focus 1). A full page followed by an empty page is complete.
- Extra tests: `test_html_instead_of_rss_is_error`, `test_full_page_then_empty_page_is_complete`, `test_offset_ignored_is_an_error`, `test_last_modified_is_not_needed`.

## Task 24: Recruitee

**Files:** Create `outreach/crawl/ats/recruitee.py`, fixtures `recruitee_list.json`, `recruitee_null.json` (`{"offers": null}`); test `tests/test_crawl_ats_recruitee.py`.

**Interfaces:**
- `RecruiteeAdapter` (`ats = "recruitee"`): `GET https://{token}.recruitee.com/api/offers/`; the body must be an object whose `offers` is a list (`null` or a missing key is `error`, not empty: Review Focus 2); the raw body changes on every request, so no ETag and the fingerprint is over the sorted `id`s only (`use_etag=False`); `JobRef`: `native_id=str(id)`, `title`, `url=careers_url`, `location` from `location` or `city` and `country`, `country=country_code` when present, `posted_on=d10(created_at)`, `body` = `html_to_text(description)` then `html_to_text(requirements)` joined by a blank line; `body()` returns `None`.
- Extra tests: `test_offers_null_is_error`, `test_raw_bytes_differ_but_ids_equal_is_unchanged` (two responses with different noise fields, same ids: second read `unchanged`).

## Task 25: BambooHR

**Files:** Create `outreach/crawl/ats/bamboohr.py`, fixtures `bamboohr_embed.html` (a `BambooHR-ATS-Department-Item` widget with 2 jobs), `bamboohr_embed_empty.html` (the ~270-byte empty widget), `bamboohr_detail.json` (`result.jobOpening.description`); test `tests/test_crawl_ats_bamboohr.py`.

**Interfaces:**
- `BambooHRAdapter` (`ats = "bamboohr"`): `GET https://{token}.bamboohr.com/jobs/embed2.php` (the `/careers/list` JSON of the research table was retired in 2024 per `ats_src/bamboohr.py`; confirm with `probe` before trusting it); parse `BambooHR-ATS-Jobs-Item` entries with `HTMLParser` (id from `bhrPositionID_{id}`, title from the link, location from `BambooHR-ATS-Location`); an HTML page that is not the widget (no `BambooHR-ATS` marker, for example a login or error page) is `error`; the empty widget (the marker present, no jobs) is `empty`; no ETag, fingerprint; `url=https://{token}.bamboohr.com/careers/{id}`, `country=None`, `posted_on=None`, `body=None`. `body()`: `GET https://{token}.bamboohr.com/careers/{id}/detail` with `Accept: application/json` -> `html_to_text(result.jobOpening.description)`.
- Extra tests: `test_login_page_is_error_not_empty`, `test_empty_widget_is_empty`.

## Task 26: JazzHR

**Files:** Create `outreach/crawl/ats/jazzhr.py`, fixtures `jazzhr_list.html` (rows with `job_title_link`, department span and location cell), `jazzhr_detail.html` (a `application/ld+json` `JobPosting` block); test `tests/test_crawl_ats_jazzhr.py`.

**Interfaces:**
- `JazzHRAdapter` (`ats = "jazzhr"`): `GET https://{token}.applytojob.com/apply/jobs` (HTML); parse rows with `HTMLParser` (id from `/apply/jobs/details/{id}`, title from `job_title_link`, location from the second cell); the page must contain the listing marker (`resumator`), else `error` (a Cloudflare 403 and a challenge page are errors, and no UA spoofing is added); raw bytes change per request, so the fingerprint is over ids only (`use_etag=False`); `url=https://{token}.applytojob.com/apply/jobs/details/{id}`, `country=None`, `posted_on=None`, `body=None`. `body()`: `GET` the detail page, read the `application/ld+json` block whose `@type` is `JobPosting`, return `html_to_text(description)`.
- Extra tests: `test_challenge_page_is_error`, `test_body_from_json_ld`, `test_bytes_vary_ids_equal_is_unchanged`.

## Task 26a: Slug probing (design 5.3 step 2)

**Files:**
- Create: `outreach/crawl/probe.py`; Modify: `outreach/crawl/__main__.py` (a `probe-slugs` subcommand)
- Test: `tests/test_crawl_probe.py`

**Interfaces:**
- Consumes: the Greenhouse, Lever and Ashby adapters (Tasks 4 to 6), `PoliteClient`, `HostTripped`, #31's `add_board` and `BoardRef`, `company_alias`, `work_tier`.
- Produces:
  - `slug_candidates(name_aliases: Iterable[str]) -> list[str]`: from a company's name aliases, the lower-case slugs the ATSs use (words joined with nothing, then with `-`), each passing `valid_token`, deduplicated, at most 4 per company.
  - `@dataclass(frozen=True) class ProbeReport: considered: int; probed: int; added: int; conflicts: int; name_mismatch: int; errors: int`.
  - `probe_slugs(conn, company_ids: Collection[int], client: HttpClient, *, limit: int, cfg=None) -> ProbeReport`: for each live company with no active board (a company with a `watched_page` but no board is still probed: a watched page is not a board) (tier 3 skipped unless promoted, in `work_tier` order, at most `limit` companies), try each candidate slug on Greenhouse, Lever and Ashby with one `adapter.list` request each through `PoliteClient` (at most 12 requests per company). Only a result `ok` is a hit; `empty`, `unchanged` and `error` are never a board (an empty board proves nothing, an error is not an answer). A hit whose reported `board_name` is non-empty and whose `name_key` is none of the company's name keys is counted `name_mismatch` and not added (a probed slug is a guess). Otherwise call `add_board(conn, company_id, BoardRef(ats, slug), via="probe", matched_on="slug", found_by="slug_probe")`; at most one board per company per run; a `conflict` is counted, never moved. A `HostTripped` ends the pass for that host only. #21 owns calling it: #31's `run_find_boards` has no hook for it, so it runs as its own step of the crawler job and by hand as `python -m outreach.crawl probe-slugs [--limit N]`; #31 only provides `add_board(found_by="slug_probe")` (from #31's final review, 2026-10-22).

- [ ] **Step 1: Write the failing tests.** `test_slug_candidates_are_lower_case_valid_and_at_most_four`; `test_an_ok_read_adds_a_board_with_found_by_slug_probe`; `test_empty_error_and_unchanged_add_nothing`; `test_one_board_per_company_per_run`; `test_a_board_owned_by_another_company_is_a_conflict_not_a_move`; `test_name_mismatch_is_not_added`; `test_tier_3_is_skipped_unless_promoted`; `test_limit_and_a_tripped_host_stop_the_pass`; `test_rerun_writes_nothing_new`; `test_no_request_for_a_company_with_a_board`; `test_a_company_with_only_a_watched_page_is_probed`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_crawl_probe -v`. Expected: FAIL (module not found).
- [ ] **Step 3: Implement** `probe.py` and the subcommand (`live_checkout.require_live` first, like the other commands).
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Crawler: slug probing for companies with no board (#21)`.

## Task 27: Docs and hand-off

**Files:**
- Modify: `docs/README.md` (a row for this plan under Current)
- Modify: `docs/superpowers/plans/2026-10-19-store-and-rules.md` (the "#21 Crawler" paragraph in "For downstream issues": point at this plan, name `read_boards` and `watch_pages` as the `job_run.job` values the crawler writes instead of `crawl`)
- Modify: `docs/README.md` also gets the hand-off boundaries below as a short paragraph under the new row.

**Hand-off boundaries (written into the docs in this task):**
- **#31 (sources and identity) owns board and watched-page creation.** It inserts `board` rows (with the token grammar in File Structure) and `watched_page` rows, and sets `board.active` and `watched_page.url`. The crawler never inserts or deletes either (the one exception is `probe_slugs`, Task 26a, which calls #31's `add_board` with `found_by='slug_probe'`), never changes `active`, `token` or `url`, and only updates operational columns: on `board` the read state (`last_read`, `last_read_at`, the streaks, `streak_since`, `last_error`, `etag`, `ids_fingerprint`, `next_read_at`, `change_interval_s`); on `watched_page` (`fingerprint`, `last_fetch_at`, `last_change_at`, `next_fetch_at`, `error_streak`). A board that has no adapter is left alone, not errored.
- **#32 (reader) owns every consumer of `read_queue`.** The crawler only enqueues: `INSERT ... ON CONFLICT DO NOTHING` with `state 'queued'` and a `priority`, and only when it has a `reader_version`. Lease, `attempts`, `done`, `refused` and re-queue on a reader version change are #32's. The crawler's one other touch on the reader's data is `decision.provisional = 1, reader_version = NULL` when a stored body changes (an earlier reader agreement no longer applies); setting `provisional = 0` is the reader's.
- **#4 and #6 own the rules.** The crawler calls `classify_title`, `decide_and_store` and `passes` and never classifies a title or a body itself.
- **#6, #15, #12 and #32 call `outreach.crawl.stale.load_stale`** (and import `Stale`) for "is this posting stale, and why". They never re-derive the rule and never read `stale_after_months` for their own cutoff: #6 passes the config value through `load_stale`'s default. A stale posting is not a live role for ranking, the daily plan or any "hiring now" signal, is shown last with the flag "stale" and its `reason`, and is never a hide. #32 may skip or deprioritize reading a posting that is stale by age.
- **#15 and #13 call `read_company_boards`** before building a plan; `plan_company_ids` is the single place that defines "today's plan".
- **#17** writes `source_key` as `"<ats>:<token>:<native_id>"` for imported boards so imported and crawled postings are one row.

- [ ] **Step 1: Edit both docs.** No em or en dashes.
- [ ] **Step 2: Run** `LC_ALL=C grep -c "$(printf '\xe2\x80\x94')\|$(printf '\xe2\x80\x93')" docs/README.md docs/superpowers/plans/2026-10-19-store-and-rules.md docs/superpowers/plans/2026-10-21-diff-crawler.md`. Expected: `0` for each.
- [ ] **Step 3: Commit** `Docs: diff crawler plan and hand-off (#21)`.

---

## Deploy and staging check (Devanshu approves each step; none runs from an agent session)

1. Merge the PR, then in the live checkout `git pull --ff-only https://github.com/DevanshuNEU/sept-23-companies.git master` and run the tests there.
2. The #30 schema change on a populated store outlasts the sender's 5 s busy timeout: stop the sender and API, run `uv run python -m outreach.install`, restart them (#17 part B runbook). Before #17 part B there is no live store and this step is empty.
3. **Staging check (the #21 "after deploy" line):** copy a #17 part A import to a scratch path, then `OUTREACH_DB=<scratch> python -m outreach.crawl probe greenhouse <a known token>` once per ATS in use, then `OUTREACH_DB=<scratch> python -m outreach.crawl tick` and compare the report with the refresh's 788 companies of 2026-10-18 (boards read, `ok`, `empty`, `error` counts). Check that the first tick's decide time is acceptable (the first Greenhouse pass decides about 13,000 postings that only had 10% bodies: roughly 70 s of CPU, in batches of 100).
4. Install the launchd job: copy `launchd/com.devanshu.outreach-crawl.plist` to `~/Library/LaunchAgents/`, `launchctl bootstrap gui/$(id -u) <path>`. Rollback is `launchctl bootout` and `git revert`.

## Open questions for Devanshu

Each has the default this plan already uses; say if you want it changed.

1. **Token and key formats.** `source_key = "<ats>:<token>:<native_id>"`, Workday token `tenant.wdN/site`, Oracle token `host/siteNumber`. #31 (board discovery) and #17 (Job Radar import) must write the same, or an imported posting and a crawled one become two rows. Default: this grammar, written into both issues.
2. **Reader version and queue order (#32).** The crawler enqueues only when it is given a `reader_version` (from `outreach.reader.version.current_reader_version()` once #32 has it), and encodes design 7.3 in `read_queue.priority` (`tier * 10000 + recency`, tiers 3, 2, 1). Default: #32 owns a sweep that queues any stored body with no row for the current version, so nothing is lost while the version is missing. Needs #32 to confirm both the function name and the priority encoding.
3. **Declared User-Agent.** Default has no contact address at all (no personal email). Some hosts want a contact URL; the repo URL would be fine if it is public. A host that blocks the declared agent is not spoofed: it lands in Needs you after three errors.
4. **Which job names the crawler writes.** `read_boards` and `watch_pages` (added by #30), not `crawl`. The store plan's health read for #11 should look for these.
5. **Which boards are read.** Active boards of companies that are not `closed`, not `removed` and not merged; a `closed` company is not read, the same as `removed` (aligned with #31's hand-off table (from #31's final review, 2026-10-22)). Boards with `active = 0` (Job Radar history) are never read.
6. **Three errors can come in 18 hours.** Backoff is 6, 12, 24 hours, so a board raises `board_unreadable` after about 18 hours of failure, faster than the old twice-weekly rhythm. A longer window is one config change; the breaker already stops a whole-host outage from raising items. Default: leave as is.
7. **Body edits on detail-request ATSs go unseen.** Workday, Oracle, SmartRecruiters, Rippling, BambooHR and JazzHR bodies are fetched once per new posting; there is no cheap edit signal. Greenhouse, Lever, Ashby, Recruitee, Workable, Pinpoint and Teamtailor bodies come with the list and are re-hashed. Default: accept.
8. **BambooHR endpoint.** The research table lists `/careers/list`; the reference adapter says that endpoint died in 2024 and uses `/jobs/embed2.php`. The adapter uses the latter. Please run `probe bamboohr <token>` once before trusting it.
9. **Today's plan.** "Today's plan companies" means today's `daily_list_entry` rows in `proposed` or `picked`. The people-plan rework (#15) changes only `plan_company_ids`. #15 and #13 call `read_company_boards` before building a plan.
10. **Fixtures are not verified live.** Pinpoint and Rippling shapes come from ats-scrapers only (the research board for Pinpoint had zero jobs). Adapters refuse an unexpected shape, so a wrong guess shows as an error and a Needs-you item, never as data loss.
11. **Watched pages that are JS-only** never change and never alert; a page that errors repeatedly only backs off. There is no review kind for "page unreadable". Default: leave.
12. **Stale needs no column, but one case has no signal (design question, 2026-10-22).** The derived rule uses existing columns only (`posted_on`, `last_seen_at`, `closed_at`, `board.empty_streak`). A posting that was imported and sits on a board the platform has never read (`last_read IS NULL`, for example an `active = 0` Job Radar board) has no absence evidence, so only the 12-month rule can flag it; a 2026 imported posting that closed long ago on the real board looks live until the first read. Default: do not guess; the first crawler read closes it or confirms it, and ranking grades such a role `low` confidence until then (#6). If he wants a rule such as "imported, never read, last seen over N days ago", it needs a second config value (not added).
13. **An empty read counts as a successful read for stale.** A first `empty` read makes open postings stale ("not on the board at its latest read ..."); the second closes them. The alternative, only `ok` counts, would leave a vanished board's postings looking live until the second empty. Default: empty counts.
14. **A live posting can look old.** Some boards keep the first publish date on a reposted role (Greenhouse `first_published`), so a live posting can be flagged "posted ..., over 12 months". Stale is never a hide and the company stays visible, so the cost is a lower role credit. Default: accept, and tune `stale_after_months` if the outcome data shows it.
