# Docs map

The app is called Knock. Its internal names stay `outreach` on purpose (the Python package, launchd labels, the store path, `OUTREACH_*` variables, the MCP server name), so nothing running has to move.

Decisions live in GitHub issue #9. After the switch (#17) the live system is the `outreach/` package, `web/` and the launchd jobs in `launchd/`; `mail/`, `refresh/` and `dashboard/` are the retired old system, kept for rollback. This folder holds the design, plans, and the research behind them.

## Current

| File | Use it for |
|---|---|
| `superpowers/specs/2026-10-18-outreach-platform-design.md` | The architecture around #9: store, rules, fetching, sending, MCP, API and app, switch-over |
| `superpowers/plans/2026-10-18-fetch-ok-empty-error.md` | Plan for #3 (done, merged in PR #19); a worked example of the plan format |
| `superpowers/plans/2026-10-19-monday-readiness.md` | Plan for #10's drafting half: `mail/check_draft.py`, `mail/me.md`, the cold-email skill |
| `superpowers/plans/2026-10-19-store-and-rules.md` | Plan for #4: the SQLite schema, store safety, the rules module, and which tables each later issue uses |
| `design/README.md` and `design/*.html` | #12's UI design: the seven views, keyboard map, visual system, and the data each view needs (mockups approved by Devanshu) |
| `superpowers/plans/2026-10-19-web-app.md` | Plan for #12 phase 1: the React app in `web/` against a fixture client, swapped for #11's API in phase 2 |
| `outreach/api/views.py` (the heavy views' cache and queue, part of #11) | The heavy reads (`/api/shell`, `/plan`, `/plan/bench`, `/needs-you`, `/progress`, `/wins`, `/watch`, `/outcomes/prompt`, `/search/index`, `/linkedin`, `/outbox`) go through `views.serve`. An answer is kept by its key: the path and query string, the store's version (`PRAGMA data_version` on a read-only connection of its own, which moves when any other connection commits: the API's writes, the sender, the crawler, the jobs, the MCP server, the judge; plus a counter `run_write` bumps after every write), the API config, whether the old mailer is loaded (shell and Outbox), the New York day, and the slice of the clock `cache_seconds` long that the request falls in. Identical requests while one computes share it. The numbers are `[api]` in `outreach/api/config.toml`: `cache_seconds` (15: the longest an answer is reused, so a clock-driven line such as "6 hours ago" or a late job is at most that old; a lower number recomputes more often), `max_heavy` (3: heavy computations at once; more uses more cores under a burst), `queue_wait_seconds` (25: a request that has no answer by then is 503 `busy` with `Retry-After: 2`, never a hang; keep it under the page's 30 second read timeout in `web/src/api/http/transport.ts`). A change to a config file on disk (`outreach/plan/config.toml`, the sending config) shows within `cache_seconds`; restart the API to see it at once. Not cached: `/api/health` and `/api/pulse` (never queued, so they answer while heavy views wait), `/api/today` (its first weekday read may build the lean list, a write), the company page, `/findings`, `/done`, `/no-go` and every write. The real `launchctl` check of the old mailer is asked at most once per `cache_seconds`. The page reuses a view read in the last 10 seconds and tries a failed read once more after 2 to 4 seconds (`web/src/app/query-client.ts`); a pulse change still refetches at once |
| `superpowers/specs/2026-10-20-opportunity-pipeline-design.md` | Approved 2026-10-21: finding every reachable company, diff-only reads, reading postings through Claude Code, ranking, the daily people plan, outcomes, how the app should feel; #9 edited to match |
| `superpowers/plans/2026-10-21-pipeline-foundation.md` | #30: the one schema change, shared contracts and formats, contract checks and the pipeline flow test |
| `superpowers/plans/2026-10-21-sources-and-identity.md` | #31: DOL LCA, stapply, Form D, HN and by-hand sources; one company per domain; work tiers |
| `outreach/sources/` (how to run) | `uv run python -m outreach.sources --help` lists the commands: `fetch lca\|stapply\|formd`, `discover lca\|formd\|hn`, `resolve`, `find-boards`, `add` and `run-due` (what launchd runs daily: this month's HN thread once it is out, Form D when SEC has a newer set, the stapply lists weekly, resolve, find-boards). LCA is by hand once a quarter: `fetch lca`, `discover lca`. Every command opens the store through `connect()`, so the live store opens only from the live checkout |
| `launchd/com.devanshu.outreach-sources.plist.template` | The daily 06:30 `run-due` job; `{{LIVE_CHECKOUT}}` and `{{LOG_PATH}}` only; installed by #17's deploy tool, never by the repo |
| `tests/test_sources_e2e.py`, `tests/test_sources_cli.py`, `tests/pipeline/stages_sources.py` | #31's three Done-when lines as tests (the store after the production order, a re-run, shared keys), the CLI on fake clients, and the `discover`, `resolve`, `find_boards` stages of the pipeline flow |
| `superpowers/plans/2026-10-21-diff-crawler.md` | #21: board adapters, unchanged reads, bodies, cadence, watched careers pages, stale postings. Built; where the code differs from the plan text, the rulings at the top of the plan and the hand-offs below win |
| `outreach/crawl/` (how to run) | `uv run python -m outreach.crawl --help` lists the commands: `tick` (the 30-minute job: due boards, then the daily slug probe on the first tick of each New York day, then due watched pages), `read --company ID [ID ...]` (read these companies' boards now), `probe ATS TOKEN` (one list request, no store access: how a token or an adapter shape is checked) and `probe-slugs [--limit N]` (guess board slugs for companies with no board, add each hit as a `slug_probe` board). Every command sends real requests, so each runs only from the live checkout. Exit 0 when the run was `ok`, 1 when partial or failed. `tick` and `probe-slugs` take the crawl lock (`crawl.lock` beside the store); a second one prints so and does nothing |
| `outreach/crawl/ats/` | The 14 adapters (Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Workable, Oracle, Rippling, Breezy, Pinpoint, Teamtailor, Recruitee, BambooHR, JazzHR) and `_common.py` (shared request and result helpers). Each one turns a response into a `ReadResult` and never reports a blocked or wrong-shape answer as `empty` |
| `outreach/crawl/` (modules) | `config.toml` and `config.py` (every number, including `stale_after_months`), `polite.py` (per-host spacing and the circuit breaker), `text.py` (HTML to plain text), `streaks.py` and `boardstate.py` (the read contract and the board and `board_read` writes), `postings.py` (the diff, batched writes, closes), `bodies.py` and `decisions.py` (bodies and batched `decide_and_store`), `cadence.py` (`next_read_at`, error backoff, which boards are due), `ingest.py` (`read_board`: the phases for one board), `tick.py` (`run_tick`, `read_company_boards`, the lock, the budget), `hostblock.py` (one Needs-you item for a blocked host), `watch.py` (weekly careers pages), `stale.py` (`load_stale`, `Stale`), `probe.py` (slug guessing) |
| `launchd/com.devanshu.outreach-crawl.plist.template` | The `tick` job, every 30 minutes; `{{LIVE_CHECKOUT}}` and `{{LOG_PATH}}` only; installed by #17's deploy tool, never by the repo. Label `com.devanshu.outreach-crawl` |
| `tests/test_crawl_*.py`, `tests/fixtures/crawl/`, `tests/pipeline/stages_crawl.py` | The crawler's tests on saved responses and `tests/fakes.py` (no network), and the `read_boards` stage of the pipeline flow test with its invariants. `tests/mutation_check.py` carries the crawler's mutation entries |
| `superpowers/plans/2026-10-21-posting-reader.md` | #32: the read queue, quote checks, the night job through Claude Code, the labeled set, eval and the promotion gate. Built; its "Hand-off" section (what #13, #15, #17 and the other streams call) and the ledger rulings win over the task text. Its "Runbook" section is the first real run, step by step: check `--safe-mode`, the side-session proof, one night batch, install the night job, labeling sittings, eval, promote, apply, rollback. Devanshu runs every step |
| `outreach/reader/` (how to run) | `uv run python -m outreach.reader --help` lists the commands: `next [--limit N]` and `submit` (what a side session uses: lease a batch as JSON, pipe readings back), `apply` (re-combine every stored reading with the rules; run after any rebuild or promotion), `night [--max-batches N] [--max-postings N]` (the night job through `claude -p`, live checkout only), `gold sample\|show\|confirm\|coverage` (the labeled set), `eval [--version V] [--against OLD] [--run] [--json]` and `promote --version V [--against OLD]` (the gate; promotion writes one line in `config.toml` and never commits). Only `night` and `eval --run` call a model. Every command that opens the store does so through `connect()`, so the live store opens only from the live checkout |
| `outreach/reader/` (modules) | `config.toml` and `config.py` (model, `promoted_version`, queue and night limits, failure patterns), `version.py` (the reader version hash), `prompts/read_posting.md` (the pinned prompt), `queue.py` (eligibility, priority, leases), `submit.py` (quote checks and refusal), `combine.py` (reader and rules together: the only code that makes a decision final), `night.py` (`Runner`, `RunResult`, `classify_run`, `claude_runner`, `run_night`), `gold.py` (the labeled set and the labeling rules, ported from Job Radar's `label.py` and rubric), `evaluate.py` (scores and the gate, ported from Job Radar's `eval.py` and `gate.py`; every threshold and its source is in its docstring) |
| `~/Library/Application Support/outreach/gold/` (outside the repo) | The real labeled set: `items.jsonl` (Devanshu's confirmed labels) and `readings/<version>.jsonl` beside it. Kept out of git so the live checkout stays fast-forwardable; back it up by hand. `tests/gold/items.jsonl` is an empty copy of the format |
| `launchd/com.devanshu.outreach-reader-night.plist.template` | The night job, daily at 02:30 under `caffeinate -i`; `{{LIVE_CHECKOUT}}` and `{{LOG_PATH}}` only; not installed by the repo (the plan's Runbook step 4 installs it by hand until #17's deploy tool does). Label `com.devanshu.outreach-reader-night` |
| `tests/test_reader_*.py`, `tests/pipeline/stages_reader.py`, `tests/fixtures/pipeline/posting_hide.txt` | The reader's tests on scratch stores with fake runners (no test runs `claude`), synthetic gold items only, and the `read_postings` stage of the pipeline flow test with its invariant (with no promoted version nothing is final; with one, an agreed hide is final and a disputed one is not) |
| `superpowers/plans/2026-10-21-ranking.md` | #6: lanes, hides with evidence, config-weighted signals with reasons, stored scores. Built; where the code differs from the plan text, the rulings in the plan and the hand-offs below win |
| `outreach/ranking/` (how to run) | `uv run python -m outreach.ranking --help` lists the commands: `rank [--dry-run] [--today YYYY-MM-DD] [--diff-with VERSION]` (score every listed company in one transaction; `--dry-run` writes nothing, and `--diff-with` ranks with the current code and config and prints what moved against a stored version, so a weight change is judged before it is committed) and `diff [--old VERSION] [--new VERSION]` (what moved between two stored versions). Every command opens the store through `connect()`, so the live store opens only from the live checkout |
| `outreach/ranking/` (modules) | `config.toml` and `config.py` (every weight and number), `wages.toml` and `pay.py` (pay text to a wage level), `common.py` (canonical ids, doubtful boards), `confidence.py` (grades and the rank confidence), `roles.py` (lanes and the best posting; stale postings last), `facts.py` (`RankFacts` loader), `hide.py` (the hide policy and the one `no_go` row ranking writes), `signals/` (the eleven signals), `score.py` (points and the stored JSON), `scores.py` (`company_score` reads and writes), `review.py` (size and sponsorship-conflict items), `version.py` (the ranker version hash), `job.py` (`compute_rank`, `run_rank`, the `weights_changed` event), `diff.py` (`what_moved`) |
| `tests/test_ranking_*.py`, `tests/ranking_*.py`, `tests/fixtures/ranking/`, `tests/pipeline/stages_ranking.py` | The ranker's tests on seeded stores (no network), 21 fixture employers with 44 postings, and the `rank` stage of the pipeline flow test with its invariants. `tests/mutation_check.py` carries the ranker's mutation entries |
| `outreach/insights/` (how to run) | #81, PR 1: `uv run python -m outreach.insights --help` lists the commands: `summary` (the counts, the interview companies, what they share in counts with "too few to tell" below 20, and the possible duplicates; company names only), `export --format csv\|json [--out PATH] [--with-people] [--force]` (the outcome dataset, one row per canonical company he reached; company level unless `--with-people`, which adds names only, never an address or a LinkedIn URL; written under `~/Desktop/outreach-scratch/insights/` or the path he names, never inside the repo) and `duplicates [--raise]` (possible duplicate companies; `--raise` makes Needs-you merge items, the only write). Modules: `bands.toml` and `bands.py` (size, stage, level and board bands), `config.toml` and `config.py` (the screen's numbers), `dataset.py` (`load_outcome_rows`), `duplicates.py`, `findings.py`, `export.py`. Every command opens the store through `connect()`, so the live store opens only from the live checkout; from a worktree use a scratch `OUTREACH_DB` |
| The size gate (2026-10-27) | Why: Twitch (Amazon) was a Today pick and Intel, GLG and ALTEN were on the bench, because the over-300 hide fired only on a size fact and most companies have none. `outreach/ranking/hide.py` `decide_hide` now also hides as `over_size`: a company on the giants list, `outreach/ranking/giants.toml` (names under their parent, matched by `name_key` on the company's name, name aliases and name keys across its merged family; loaded by `giants.py`; a size he set by hand at or under 300 puts it back), and a company with no size on file that has more than `[hide] likely_large_open_roles` (100) open roles or more than `[hide] likely_large_lca_software` (100) software H-1B filings in one fiscal year. A hide keeps the company off the ranked list, so off the plan, the fresh lane and the bench, and on the No-go page's hidden list; nothing is deleted and no status changes. The profile's headcount says "Not checked" when no size is on file. Tests: `tests/test_ranking_size_gate.py` |
| `outreach/ranking/config.toml` `[live_role.tier_values]` (level priority, part of #6 and #15) | The `level_role` lane's base by the best posting's level tier: new grad, early career and SWE I 0.8, SWE II and an open level 0.45 (he asked to focus on early career, new grad and SDE I). A title with no level whose decision asks 2 years or fewer (`years_asked`) counts as early career. `outreach.ranking.roles.early_career_fit(level_tier, level_basis, years_asked)` is the pure check the plan can import (`Best` carries all three). Inside the `level_role` lane the best posting is an early career fit at most `[live_role] early_career_max_age_days` (90) old before a fresher SWE II or open level role (`role_sort_key`), then the freshest. Only values move: no hide, no no-go row, no weight |
| The profile judge (Part of #6 and #15, 2026-10-27) | Why: the ranking knew titles, places, pay and company facts but never what a company builds, so a sensor or chip company could rank above an agent company. The judge reads each top company's own pages and asks his Claude Code seat once what it builds and how well it fits him; the answer is checked in code and stored as one `event` (entity `company`, kind `judged`, actor `claude`, `detail.by` `judge`; no migration, no fact row). It only ever adds ranking points and a visible line: it never writes a decision, a hide, a no-go, an email or a note, and never calls the API. Ranking reads it as `profile_fit` (weight 10.0, value = how far the fit is above `[profile_fit] floor` (50) as a share of the way to 100, so a fit of 50 or less earns nothing, 75 half and 100 all; capped at `max_weight_share` 0.1 of the top score without the bottom push and both lifts, so at most 12; 0.0 switches it off; a judgment older than `[profile_fit] max_age_days` (30) counts as none, "Not judged yet"). Today shows "Judge: builds X. Fit 72. Risk: Y." in Why today and "Judge fit 72" on the rail; the Score tab shows the row "Fits what you build" |
| `outreach/judge/` (how to run, and the runbook) | `python -m outreach.judge run [--limit N] [--dry-run] [--companies NAME,...]` (the 06:10 job), `show [--top N]` (the top of the ranking, each with its judge line) and `eval [--file PATH]` (agreement with `golden.toml`, the 13 verdicts of the 2026-10-26 research pass: yes/no agreement, a fit of `[eval] yes_at` (60) or more is the judge's yes, and Spearman's rank correlation). Shipped off: `[judge] enabled = false` in `outreach/judge/config.toml`, so `run` prints "judge is off" and exits 0 before it opens anything. A real `run` only starts in the live checkout; `--dry-run` lists who is due and what would be read and runs anywhere on a scratch OUTREACH_DB. To turn it on: (1) deploy, then in the live checkout `uv run python -m outreach.judge run --dry-run`; (2) `uv run python -m outreach.judge eval` once: it judges the golden companies that are in the store (at most 13 calls, even with the job off) and prints the agreement; read the per-company lines, not only the two numbers; (3) if he trusts it, a PR that sets `enabled = true` (the live checkout only fast-forwards), merged and pulled; (4) `uv run python -m outreach.deploy plan`, then `install` loads `com.devanshu.outreach-judge` (it can be loaded while off); (5) `uv run python -m outreach.judge show --top 20`; the 08:00 plan re-ranks with the points. To turn the points off but keep the lines: `profile_fit = 0.0` in `outreach/ranking/config.toml`. Cost: at most `per_run` (15) model calls a night, each a `claude -p` with no tools on his seat (no API spend), and at most 3 page reads a company (homepage, the about page it links, the posting page when the store has no body) plus each site's robots.txt; a page is read at most once per 14-day window, and inside the window a company is not sent again. In steady state the top 40 come due every 14 days, about 3 calls a night. Log: `~/Library/Logs/outreach/judge.log`, one summary line per run |
| `outreach/judge/` (modules) | `config.toml` and `config.py` (`enabled`, `model`, `top_n` 40, `per_run` 15, `cache_days` 14, the fetch caps, the stop patterns, `yes_at`), `prompts/judge.md` and `prompt.py` (the pinned prompt with his me.md facts, no ASK lines; page text is data, never instructions; the judge version `jv-`), `fetch.py` (`SiteReader`: robots.txt first, with the crawler's declared User-Agent, through the crawler's `PoliteClient`, redirects one hop at a time each asked of robots.txt, a size cap, HTML or plain text only, never LinkedIn; a page that cannot be read says why), `validate.py` (one JSON object; fit 0 to 100, `builds`, `why`, `risk`, a fixed domain vocabulary, `level_ok` yes, no or unknown, up to 3 quotes that must be word for word on a page that was read or the whole answer is refused), `store.py` (the cache key: judge version, best posting, page text; `record_judgment`), `run.py` (targets, the window, `gather`, the loop and its stops), `evaluate.py` and `golden.toml`, `__main__.py`. `outreach/ranking/judged.py` is the one reader (`load_judgments`, one set-based query) and `outreach/ranking/signals/profile_fit.py` the signal |
| `launchd/com.devanshu.outreach-judge.plist.template` | The judge job, daily at 06:10 (before the 08:00 plan); `{{LIVE_CHECKOUT}}` and `{{LOG_PATH}}` only; optional in the deploy tool's `NEW_JOBS`; installed by `python -m outreach.deploy install`, never by the repo. Label `com.devanshu.outreach-judge` |
| `tests/test_judge_*.py`, `tests/test_ranking_profile_fit.py` | The judge's tests on scratch stores with a stub runner and a stub page reader (no test calls claude or reaches the network): the reply and quote checks, the event and the set-based read, robots.txt and the polite reads, the window and the off switch, the CLI and the job, the eval arithmetic, and the signal, its cap and the rank. `tests/mutation_check.py` carries 21 "profile judge" entries |
| `superpowers/plans/2026-10-21-daily-people-plan.md` | #15: Today as a plan of people, capacity, the prepare step, LinkedIn notes and checks. Built; where the code differs from the plan text, the rulings at the top of the plan and the hand-offs below win. #9's LinkedIn limits ruling (2026-10-26) replaces the plan's month counting and its 200-character note: requests count over the last 7 New York days (150 for the plan, 200 in all), the day was his pace (`linkedin.per_day`, 7; replaced by the company queue, `[queue]`, in P2 of the company-first ruling), and a note may be 300 characters |
| `outreach/plan/` (how to run) | `uv run python -m outreach.plan --help` lists the commands: `build` (the day's plan, once per date; `--dry-run`, `--no-read-boards`, `--replace` for today's untouched plan: see the plan runbook below), `show` (a stored plan, read only) and `prepare` (the morning step through `claude -p`, live checkout only; off while `prepare.enabled` is false, as shipped). From a worktree use a scratch `OUTREACH_DB` |
| `superpowers/plans/2026-10-21-daily-people-plan.md` | #15: Today as a plan of people, capacity, the prepare step, LinkedIn notes and checks. Built; where the code differs from the plan text, the rulings at the top of the plan and the hand-offs below win. #9's LinkedIn limits ruling (2026-10-26) replaces the plan's month counting and its 200-character note: requests count over the last 7 New York days (150 for the plan, 200 in all), the day was his pace (`linkedin.per_day`, 7; replaced by the company queue, `[queue]`, in P2 of the company-first ruling), and a note may be 300 characters. #9's note authorship ruling (2026-10-26): a note he saved is checked as his text (`check_note(author='user')`), on honesty and hard limits only, with no reason line or fact rule, its facts' `in_note` read from his text, and the app no longer asks him to confirm a line |
| `outreach/plan/` (how to run) | `uv run python -m outreach.plan --help` lists the commands: `build` (the day's plan, once per date; `--dry-run`, `--no-read-boards`), `show` (a stored plan, read only) and `prepare` (the morning step through `claude -p`, live checkout only). From a worktree use a scratch `OUTREACH_DB` |
| `outreach/plan/` (modules) | `config.toml` and `config.py` (every number), `dates.py`, `outbound.py` (one read of outbound history), `capacity.py` (the last 7 days of requests against both limits, then the day's queue: `companies_today`), `contact.py` (the 14-day rule and the wave: `wave_people`, `wave_room`, `free_on`), `people.py` (candidates, channel, `add_candidate`, `people_search_url`), `careers.py` (`careers_url` and `pick_careers_url`), `notecheck.py` and `drafts.py` (the one note checker and draft writer), `select.py` (pure choice of rows), `prospects.py` (loaders, carry-forward, the `why` keys), `boards.py` and `build.py` (the plan job), `prepare_targets.py`, `prepare_store.py`, `claude_run.py`, `prepare.py` and `prompts/prepare.md` (the prepare step) |
| `launchd/com.devanshu.outreach-plan.plist.template`, `launchd/com.devanshu.outreach-prepare.plist.template` | The plan job (Monday to Friday 08:00) and the prepare job (Monday to Friday 05:30); `{{LIVE_CHECKOUT}}` and `{{LOG_PATH}}` only; installed by `python -m outreach.deploy install`, never by the repo |
| `tests/test_plan_*.py`, `tests/plan_seed.py`, `tests/fixtures/plan/`, `tests/pipeline/stages_plan.py` | The plan's tests on scratch stores (no network, no Claude: the prepare runner, the boards reader and the rank runner are fakes), the `plan` stage of the pipeline flow test with its invariants (capacity, one person per company, the 14-day boundary, the lane share, people before find-someone, only laned and visible companies, a second run changes nothing). `tests/mutation_check.py` carries the plan's mutation entries |
| `superpowers/plans/2026-10-21-api.md` | #11: FastAPI, generated types, idempotent writes, outcomes, the HTTP client that replaces the fixtures |
| `superpowers/plans/2026-10-21-web-experience.md` | #33: the people plan, outcomes, progress, wins and the finish line (mockups first) |
| `superpowers/plans/2026-10-21-integration-review.md` | Cross-plan review of the eight plans and how each finding was resolved |
| `superpowers/plans/2026-10-22-sending-engine.md` | #14: the new sender: ramp, spacing, follow-up, holds, stop rules, the two-week simulation (migrations 0002 and 0003) |
| `superpowers/plans/2026-10-22-import-and-switch-over.md` | #17: import all history (interviews included), the rehearsal, the deploy tool and the switch runbook (migration 0004). Part A (the importer, `outreach/importer/`) and B1 (the deploy tool, `outreach/deploy/`) are built; B2 (CLAUDE.md, this map and the cold-email skill) merges on switch day. The runbook in this plan is the switch: rehearsal (R1), switch day (S1 to S14) and rollback (R-back) |
| `superpowers/plans/2026-10-22-retire-job-radar.md` | #5: retire Job Radar after its history is imported |
| `superpowers/plans/2026-10-22-needs-you.md` | #16: the five Needs-you triggers, the sweep, one-click fixes, merge and unmerge. Built: `outreach/review/` (Tasks 1 to 12), the API (Task 13) and the web view in `web/src/routes/needs-you/` (Tasks 15 to 17: a section for every kind, the side-by-side merge confirm, Undo from Resolved, the plain progress line and the All settled panel). `?scenario=review` shows every kind on sample data |
| Today as a company queue (P2 of the #9 company-first ruling, 2026-10-26) | `outreach/plan/config.toml` `[queue]` is the one source for the day's size and the wave: `companies_today = min(queue.companies_per_day, floor(remaining / queue.requests_per_company))` (8 and 4), where `remaining` is what the 7-day windows leave (150 plan, 200 in all, whichever is lower). It replaced `linkedin.per_day` and `route.wave_size`. One wave of up to 4 people per company, then `linkedin.rotate_days` (14); up to `linkedin.max_people_per_company` (8) over time. `GET /api/plan` gives each company row a `queue` (`outreach/api/queries/queue.py`: size and stage, role and its age, new engineering roles in 30 days, sponsorship and E-Verify, up to 3 why reasons, where it stands, last touch, email hint, people on record, the people search and careers links, the terminal line) and the view an `inFlight` list. Writes: `POST /api/plan/{entryId}/people` (the pasted `Name \| Title \| note` fallback, through `record_request`) and `POST /api/plan/{entryId}/search` (his saved LinkedIn search as an `event` of kind `linkedin_search_saved`; a `currentCompany` id becomes a `company_alias` of source `linkedin`, key `linkedin:<id>`). The page: `web/src/routes/today/company-panel.tsx` and `in-flight-list.tsx`; the sample is `?scenario=queue` |
| Today as the top picks (the owner's spec and rulings, 2026-10-26; Part of #15 and #33) | `outreach/plan/fresh.py`: the fresh lane's rules (`Opening`, `opened`, `is_fresh`, `visa_clear`, `early_career_fit` with a TODO to use the shared helper once the level-priority branch merges, `age_words`) and its reads (`load_openings`, `load_visa_facts`, `load_passes`, `load_applied`). `select.py`: the fresh lane first, up to `[queue] fresh_slots` (3) of the day, for early career openings within `fresh_hours` (72) that clear the visa gate, at companies within the top `fresh_pool_top_n` (300) of the stored ranking (the quality floor); `held_back` keeps a company out of the picks after Not now for `pass_cooldown_days` (14), or after an application, each until an early career opening is first seen after it; `bench` (`bench_size`, 12). `picks.py`: `bench_picks` and `write_pick`. API: a company row's `queue` adds `opening` (level, age, fresh, the "New opening, 6 hours ago" tag, the applied note), `whyToday`, `websiteUrl`, `linkedinCompanyUrl`; picks with a new opening come first (`in_pick_order`); the view adds `passed`. Not now is `/api/plan/{entryId}/pass`: the entry goes to `removed` with `decided_at` (on that table it means only "off this day's list": the company row and the no-go list are never touched), the bench head takes its rank as a new row with an id above every row, and one `pick_passed` event (entity company, actor devanshu) is what the cooldown reads. The bench is `/api/plan/bench` and Add to today `/api/plan/bench/{companyId}/add`, which appends while the 7-day limit has room (`outreach/api/services/picks.py`, `queries/bench.py`). The page: `web/src/routes/today/why-today.tsx`, `pass-chips.tsx`, `bench-drawer.tsx`, `picks.ts`; the design is 16.1b and the keys 16.6 in `docs/design/README.md` |
| The Today redesign (the owner's ruling, 2026-10-27; Part of #33) | Why: the page looked dull and the right side was empty. Three columns from 1280 px (rail, company, profile; the profile folds under the company below that), the profile of what does not change with each fact's source and "Not known" where nothing is, reasons as chips, Fit chip bands, Log people and Save this search closed until opened (L opens), monograms and a day bar on the rail. The profile is `queue.profile` (`outreach/api/queries/profile.py`: facts with their source, the last two fiscal years of LCA filings, up to 5 open roles at his level from `load_level_roles`, people, his history; no migration). The page: `web/src/routes/today/profile-panel.tsx`, `company-panel.tsx`, `why-today.tsx`, `web/src/components/monogram.tsx`, `fit-chip.tsx`; the design is 16.1c in `docs/design/README.md` |
| The email flag and the email lane (2026-10-27, his ask: "flag me which company to send emails to") | Why: the Outbox sat at 0 of 20 a day while the ramp was not the limit, because nothing said which companies are worth a cold email. `outreach/plan/email_fit.py`: `email_fit` gives each company `email` or `linkedin_only` with short reasons (every blocker when LinkedIn only), in this order: no-go, removed or closed; paused; a reply holds it; `[email] people_per_company` (2) people already emailed; a full wave in flight; no website domain on file (the best `domain` fact across the merged family, shared keys and free mail, payroll and platform hosts left out, a guess counts and says so); the cached mail server check says none; best size (lower end, as the size gate reads it) over `[email] email_max_headcount` (200; no size on file is fine). `load_email_facts` reads it in one query per table. Each company row's `queue` has `emailFit` (verdict, label, reasons, the website with its source); `GET /api/plan` adds `emailCount` and `emailText` ("5 of today's 8 can be emailed"); `GET /api/plan/bench` adds `emailLane` and `emailLaneText`: up to `[email] email_companies_per_day` (6, 0 turns it off) companies not on today's list that the plan could pick today (`select.eligibility`) and that are `email`, by the stored ranking, read with the bench from one pool (`picks.bench_and_lane`), so the page asks for no extra heavy view. It only flags and lists: nothing drafts, approves or sends, and the ramp in `outreach/sending/config.toml` is unchanged. The page: `web/src/components/email-chip.tsx` (rail, panel, bench), the panel's Email row, the profile's Website, the header count, `web/src/routes/today/email-lane.tsx` (Email today, collapsed). Tests: `tests/test_plan_email_fit.py`, `tests/test_api_plan_email.py`, `web/src/routes/today/email-flag.test.tsx` |
| `outreach/review/scope.py` | #16, the company-first ruling in #9: Needs you lists only items for companies he is working (`attention`: in play, emailed or held by a reply; a merge question when either side is), every other open item is a Data gap (`dataGaps` on `GET /api/needs-you`, by rank, never in the badge). The careers pass raises a missing-domain item only for those or the top `[needs_you] crawler_scope_top` ranked, at most `crawler_cap_per_run` a run; the sweep clears the rest (`clear_out_of_scope`) |
| `superpowers/plans/2026-10-22-mcp-server.md` | #13: the local MCP server for the Claude side session, with enforced limits |
| `outreach/mcp/` (tools) | Server version 2, 19 tools. Reads: `ranked_companies`, `company`, `person`, `followups_due`, `prepare_targets`, `knock_status`, `contact_check`. Writes: `record_research`, `flag_review`, `mark_no_go`, `save_draft`, `save_followup`, `save_linkedin_note`, `add_people`, `reader_next`, `reader_submit`, `submit_candidates`, `intake`, `log_sent`. The terminal bridge (#9 company-first ruling, 2026-10-26) is `outreach/mcp/tools/bridge.py`: `knock_status` (the 7-day windows, companies in flight, today's queue, contacted.txt gaps), `contact_check` (company state, touches and a route per person; an unknown company is "not in Knock: add it?" with the by-hand `sources add` command), `intake` (records the people he pasted through `add_people`'s rules, with a route from `outreach/plan/route.py`) and `log_sent` (after he says sent, each LinkedIn request through `outreach/outcomes/requests.py`, the write the plan row and the LinkedIn page use). The guard (`guard.py`) allows exactly one write on `linkedin_log` and `outreach_touch`: inserting a `request_sent` row and its `linkedin_request` touch under a `bridge:` key, with no outcome and no undo. Every update and delete of both tables, any reply, accept, interview, referral, email touch or undo stays refused. `GET /api/pulse` (the newest event id) lets an open page refetch within 5 seconds of such a write |
| `superpowers/plans/2026-10-26-insights.md` | #81: the outcome dataset per canonical company, what the interview companies share (counts, too few to tell below 20), lookalikes, the one-way `interview_like` ranking lift, the CSV and JSON export, and the Insights screen under Results. Four PRs: dataset and mockup, tilt, API, web |
| `superpowers/plans/2026-10-22-integration-review.md` | Cross-plan review of the five new plans; every finding was applied in them |
| `web/` (how to run) | Needs Node 20.19 (see `web/.nvmrc`; `pnpm install` fails on other majors, engines are strict). `cd web && pnpm install && pnpm dev`, then open http://127.0.0.1:5173. Add `?scenario=hard_block` (or `signed_out`, `empty`, `weekend`, `job_failed`, `error`, `many_sent`, `review`) to see each state |

Every other issue gets its own plan here before coding starts.

## Crawler hand-offs (#21)

What the crawler built, and what each later issue should rely on. The plan's task text is history where this
differs; the rulings at the top of `superpowers/plans/2026-10-21-diff-crawler.md` and this section win.

**Who owns what.** #31 creates boards and watched pages; the crawler never inserts or deletes either and never
changes `active`, `token` or `url`. The one exception is `probe-slugs`, which calls #31's `add_board` with
`found_by = 'slug_probe'`. The crawler writes no `read_queue` row and has no reader version. It calls `classify_title`,
`decide_and_store` and `rebuild` and never classifies anything itself. The `job_run.job` values it writes are
`read_boards` and `watch_pages`, not `crawl`. The slug probe writes no `job_run`: it appends a `slug_probe_run` event
(entity `source`, keyed `slug-probe-run:<New York date>`) once a day's pass completes, and one `slug_probed` event
per company it tried (keyed `slug-probed:<company_id>:<date>`; the company is not probed again for 30 days).

**#6 ranking.**
- Stale: `from outreach.crawl.stale import load_stale, Stale`. `load_stale(conn, today, *, max_age_months=None,
  posting_ids=None, company_ids=None)` returns `{posting_id: Stale}` for open postings only. `Stale.kinds` holds
  `"old"`, `"not_on_board"` or both; `Stale.reason` is the text to show. Leave `max_age_months` unset so the config's
  `stale_after_months` applies. Do not re-derive the rule and do not read the config value yourself.
- The reason texts are "posted 2016-03-02, over 12 months" and "not on the board at its latest read (last listed
  2026-10-18 or later)". Use the second one in #6's tests.
- A stale posting is not a live role, is shown last with the flag and its reason, and is never a hide and never a close.
  A failed read never makes a posting stale.
- A posting on a board the platform has never read has no absence signal, so only the age rule can flag it. Grade such a
  role `low` confidence until the first crawler read.
- Doubtful boards: a board with `found_by` of `stapply_name` or `slug_probe` is doubtful until it has had a good read
  (`ok`, `empty` or `unchanged`) and its `flagged` item `board-doubtful:<board_id>` is resolved. Ignore such a board for
  ranking until then. The crawler raises the item on the first good read when the org name the board reports is none of
  the company's names, or when a company with LCA evidence has a board of over 300 jobs. A `slug_probe` board whose
  first good read reports no org name (every Lever and Ashby guess, and a Greenhouse guess with no `company_name`)
  always raises it, so Devanshu confirms every guessed board. A `stapply_name` board with no org name raises it only
  on the 300-job test.

**#32 reader.**
- The crawler stores each body and its `body_sha256` and nothing else. #32's sweep queues every stored body with no row
  for the current reader version. Nothing waits on a crawler queue write.
- A changed body sets `decision.provisional = 1, reader_version = NULL` in the same unit as the new body.
- Combiner contract: before `outreach.reader.combine` ships, `rules.decide_and_store` and `rules.rebuild` must accept a
  `combiner=` keyword. The tick imports `outreach.reader.combine` lazily and, once it exists, passes
  `reader_combiner(conn)` as `combiner=` to every `decide_and_store` and to `rebuild`. Without the keyword, every tick
  fails with `TypeError` the day the module appears. Today `decide_and_store(conn, posting_id, *, cfg=None)` and
  `rebuild(conn, *, cfg, version, batch_size, progress)` have no such argument.
- With no combiner given, the upsert must write `provisional = 1` and `reader_version = NULL`. Only a combiner may leave a
  row final. The crawler resets the current-ruleset row itself as a second guard.
- "No combiner means `provisional = 1`" must cover `rules.rebuild` too. Today rebuild upserts every open posting and
  keeps a row's old `provisional = 0` and `reader_version`; after a ruleset revert (a rebuild back to an earlier
  version) the old rows come back final with no reader having agreed under that ruleset again. The crawler cannot
  guard this one: rebuild writes the rows itself.
- A posting that is stale by age may be skipped or read last. It is never a reason to refuse a reading.

**#11 API and health view.**
- `TickReport` (`outreach.crawl.tick`): `run_id`, `rebuilt`, `read`, `ok`, `empty`, `error`, `unchanged`, `deferred`,
  `failed`, `new`, `closed`, `switched_off` (boards left alone because their reader is off, #46), `seconds`, `tripped` (host buckets the breaker stopped or that read a mass empty, sorted),
  `watch` (a `WatchReport` with `baseline`, `unchanged`, `changed`, `unstable`, `error`, `deferred`), `watch_error`,
  `probe` (a `ProbeReport` with `considered`, `probed`, `added`, `conflicts`, `name_mismatch`, `errors`; None when the
  day's pass already ran or the budget was spent) and `probe_error`. `read` counts boards whose read was recorded;
  `deferred` counts boards put off with no read recorded. A failed probe or watch pass is its `*_error` text and
  never undoes the board reads.
- `job_run` `read_boards`: one row per tick and one per `read_company_boards` call. The summary holds every
  `TickReport` field (`tripped` a list; `probe` and `watch` objects, or null when the pass did not run or failed, with
  `probe_error` and `watch_error` beside them; a `read_company_boards` row has no probe or watch keys), plus
  `failed_boards` (board ids) when a board's ingest raised. Status is `ok`, `partial` (a board failed or the run
  stopped) or `failed`. `error` names at most 20 failures.
- `job_run` `watch_pages`: its own row per tick, summary from `WatchReport`. `probe-slugs` writes no `job_run`.
- Needs-you items the crawler raises, all with `raised_by = 'crawler'`: `board_unreadable` (three errors in a row),
  `board_empty` (second empty read in a row, #9; keyed on the run's first empty read), `careers_changed`, and `flagged` for a doubtful board (`board-doubtful:<board_id>`),
  an unreadable careers page (`careers-unreadable:<company_id>:<hash>`) and a blocked host.
- Host-blocked item: `flagged`, `evidence_key` `host-blocked:<bucket>:<date the oldest streak began>`, set on the board
  with the oldest streak. It replaces the per-board `board_unreadable` items when three or more boards of one ATS are in
  error streaks that all began after that host's last good read. The reason names the host and how many boards are
  unread since when. The host's next good read clears it. Show it from the same place as the `tripped` list.
- A failed read never closes a posting. A deferred board has no `board_read` row and no streak change.
- Outages: a board whose last good read is under `outage_grace_s` (a day) old is held as a deferral when its host trips
  the breaker, and when five or more boards of one host with open postings read a well-formed empty in one tick (a
  mass empty closes nothing and raises no `board_empty`). After the grace the reads are written as they are, so a host
  that blocks for good reaches Needs you as the one host-blocked item.
- The weekly full read: a board none of whose postings has been listed for 7 days is read with no validators, so a
  retitle under the same ids or a change past the first page shows up within a week.

**#17 deploy.**
- `launchd/com.devanshu.outreach-crawl.plist.template` (label `com.devanshu.outreach-crawl`, `StartInterval` 1800,
  runs `{{LIVE_CHECKOUT}}/.venv/bin/python -m outreach.crawl tick`). Fill `{{LIVE_CHECKOUT}}` and `{{LOG_PATH}}`; nothing
  else. Install is `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.devanshu.outreach-crawl.plist`; rollback is
  `launchctl bootout` and `git revert`. No agent session runs either.
- Imported boards write `source_key` as `"<ats>:<token>:<native_id>"` so an imported and a crawled posting are one row.
  Workday tokens are `tenant.wdN/site`, Oracle tokens `host/siteNumber`.
- Staging check, on a scratch copy of a part A import (`OUTREACH_DB=<scratch>`, never the live store; the commands
  still refuse outside the live checkout, so run them from there). Run
  `python -m outreach.crawl probe <ats> <token>` once per ATS in use, then `tick`, and compare the counts with the
  refresh's 788 companies of 2026-10-18. Several fixtures come from memory or from other scrapers, so confirm these
  against a live board:
  - Workday: what `startDate` means on the detail page, and that the page total comes from page 1.
  - Workable: the `.md` posting URL.
  - Rippling: whether the list is paged.
  - Oracle: the real cap on `limit`, and what a wrong `siteNumber` returns.
  - Breezy: the `/p/{id}` detail fallback.
  - Teamtailor: the location fields and the offset paging.
  - BambooHR: that `/jobs/embed2.php` answers (the research table lists `/careers/list`, which the reference adapter says died in 2024), and the detail shape.
  - JazzHR: the list rows and detail page.
  - Ashby: what a nonexistent token returns. If it is a 200 with no jobs, slug probing must not read that as a board.
  - Also check how long the first tick takes to decide (the first Greenhouse pass decides about 13,000 postings, roughly 70 s of CPU in batches of 100), and that the host-blocked item and `tripped` look right when one host is made to fail on a scratch store.

**Switching a source off (#46).**
- A crawler reader is switched off by a line under `[ats_off]` in `outreach/crawl/config.toml`: the ATS name and the
  reason. SmartRecruiters and Pinpoint are off (their robots.txt forbids the reads, checked 2026-10-23). Deleting the
  line turns the reader back on; nothing else changes. A name that is not one of the 14 readers fails at load.
- A board on a switched-off ATS is never due, read, probed or slug-probed. Its row, postings, decisions and items stay
  as they are: no close, no error, no streak change, no host-blocked or outage item. The tick counts such boards in
  `switched_off`. `read --company` names each one with the reason and exits 1; `probe <ats>` refuses and sends
  nothing. The adapters and their tests stay.
- The polite client refuses any request, redirect hops included, to a switched-off ATS's host (`HostOff`, a
  `HostTripped`), so a watched page on such a host is deferred, never fetched. #31's careers check follows its
  redirects itself through the same block (`CrawlConfig.off_host`): a hop to such a host is not sent and the check is
  an `error`, never a definite `none`.
- #31 may still create boards on a switched-off ATS (stapply lists, careers pages, `ats_detect`); the rows are harmless.
  find_boards reads the same `[ats_off]` list (`outreach.crawl.config`, standard library only), so a company whose
  only boards are switched off gets its careers page watched, like a company with no readable board.
  stapply's `wellfound` and `ycombinator` lists are never downloaded or read; `fetch stapply` reports them in
  `excluded_lists` and `excluded_rows`.
- The old refresh (`refresh/crawl_more.py`) no longer reads SmartRecruiters or Dover's `/api/`; `--only` or `--skip`
  naming either prints one line and the run goes on.

**Known gaps at hand-off.**
- Body edits on detail-request ATSs (Workday, Oracle, SmartRecruiters, Rippling, BambooHR, JazzHR) go unseen after the
  first fetch, except through ruling 6's refetch (a changed title or date, and the monthly lane 1 refetch).
- A retitle under the same ids on a fingerprint adapter is seen at the weekly full read, not at once.
- A mass empty that lasts past the outage grace is written as empty reads: a day later its boards raise `board_empty`
  items one by one and the second empty closes their postings.
- A board's host bucket is not stored, so the host ruling groups boards by ATS (true for every adapter here).

## Daily plan hand-offs (#15)

What the plan built and where each other stream meets it. The plan's task text is history where this differs; the
rulings at the top of `superpowers/plans/2026-10-21-daily-people-plan.md` and its "Hand-off" section win.

- **#13 MCP** calls `add_candidate(added_by='claude')`, `save_linkedin_draft(saved_by='claude')`,
  `select_prepare_targets` and `record_attempt(found=0)`. Claude cannot confirm a reason line (`DraftLocked` and a
  table CHECK), approve, send or log.
- **#11 API** reads `daily_list` and `daily_list_entry` with the `why` keys exactly as the plan lists them, `linkedin_draft`
  for notes, `linkedin_capacity` and `plan_capacity`. It writes through `save_linkedin_draft(saved_by='devanshu')`,
  `confirm_reason` and `add_candidate(added_by='devanshu')`. If no plan exists for a weekday it may call
  `build_plan(conn, today=..., read_boards=False)`, which is safe to repeat.
- **#33 web** maps `why` to its plan rows and derives the row state from touches and entry state; the plan stores no UI
  text except `why.find_someone.handoff`.
- **#6 ranking, #21 crawler, #31 sources, #14 sending, #17 import.** The plan reads `ranked_list(laned_only=True)` and
  never scores or hides. The plan job asks the crawler to read the top boards and runs `rank` when its hook exists;
  neither can fail the plan. It never writes `company_post`, `send_day`, messages or touches. Imported LinkedIn
  history must reach `outreach_touch` or `linkedin_log`, and imported emailed people keep their `touch` rows.
- **Launchd.** The two templates are shipped and installed by `python -m outreach.deploy install`, never by the repo.

### Plan runbook: replacing today's plan

The plan builds once per date, so a config change after 08:00 shows only from the next weekday. To rebuild today's list
with the current config, run `uv run python -m outreach.plan build --replace --dry-run` in the live checkout first (add
`--no-read-boards` to skip the crawler and the ranker): it prints the rows it would delete and the new list and writes
nothing. Then run it without `--dry-run`, optionally with `--reason "..."`. It works only while nothing on today's list
was acted on: a logged request or email, an application, a skip, carry, pick or remove, a row written to (a saved
search), a note he saved or confirmed, a person he added, or any touch or event of his today at a listed company makes
it refuse (exit 1, the reasons on stderr, nothing written). On success one transaction deletes today's
`daily_list_entry` rows and its `daily_list` row (the only things a plan build writes besides events and job runs;
people, notes, touches, logs, applications and review items are never touched), builds the day again and writes a
`plan_replaced` event (actor `system`) with the row count and the reason. Rows carried from an earlier plan come back
the same way (the carry reads the earlier plans again, with their dates). New rows get new ids, never an old one: an
open Today tab refetches on the new event (`GET /api/pulse`), and a write sent with an old row id finds no row. `--replace` names only today (New York); a weekend still needs `--force-weekend`.

### Clear-reach runbook: emptying the old LinkedIn list (Devanshu runs it once)

The new system starts LinkedIn outreach from nobody, so `uv run python -m outreach.people clear-reach` in the live
checkout removes the LinkedIn people nobody ever contacted: the person, the prepare step's draft for them (saved by
`claude`), their `person_source` rows, and their name on past undecided plan rows. Run it first as is: it is a dry run
that prints who would go, who stays and why, and the row counts, and writes nothing. Then run it with `--apply`: it
writes a full copy of the store to `backups/clear-reach-<UTC time>.db` next to the store
(`~/Library/Application Support/outreach/backups/`), prints the path, and stops with exit 1 if the copy fails; then
one `BEGIN IMMEDIATE` transaction decides again (a person added, touched or changed meanwhile stays), deletes, writes
one `people_cleared` event (actor `system`, every id, name and company in its detail) and prints the counts before
and after. A second run removes nothing. It never removes anyone with history: a touch of any kind (an undone one
too), a LinkedIn log row, an email thread, a no-go, a Needs-you item, a person he added, changed or wrote a note for,
or a plan row of today or one he decided. They stay in the store and simply stop showing, since the LinkedIn page
lists only people who replied. To undo, open the backup read-only and insert the rows the event lists back into
`person`, `person_source` and `linkedin_draft`, and set `daily_list_entry.person_id` again; nothing else changed.
`--apply` runs only in the live checkout, or anywhere with `OUTREACH_DB` naming a scratch store.

What it does with each table that has a `person_id` column:

| Table | For a removed person | Why |
|---|---|---|
| `person` | deleted, last | the row being cleared |
| `linkedin_draft` | deleted when saved by `claude` and still `proposed`; his note keeps the person | the prepare step's draft exists only for that person |
| `person_source` | deleted | the found pages exist only for that person |
| `daily_list_entry` | past `proposed` or `backlog` rows: `person_id` set to NULL (the column allows it), the row stays; a row of today or later, or one he picked or removed, keeps the person | plan history stays, without a person who is gone |
| `outreach_touch` | never touched; a touch keeps the person | a trigger forbids deleting a touch, and the touch feeds dedup and the weekly count |
| `linkedin_log` | never touched; a row keeps the person | append-only (a trigger) |
| `touch` | never touched; an email thread keeps the person | the sender's history |
| `no_go` | never touched; a no-go keeps the person | the row stops anyone adding them again |
| `review_item` | never touched; any item, open or settled, keeps the person | his decision or a pending one |
| `event` | never touched | append-only history |

### Prepare runbook (Devanshu runs it once)

**The prepare step is off (company first, #9, 2026-10-26).** Devanshu finds the people on LinkedIn himself and writes
his own notes, so `[prepare] enabled = false` in `outreach/plan/config.toml`: the 05:30 job prints `prepare is off ...`,
exits 0 and touches nothing (no `claude -p` call, no job run, no event). The 08:00 plan proposes company rows only
(`[plan] propose_people = false`). The code and the job stay until a later cleanup deletes them; `deny_rule_checked`
below still records the deny-rule check and still gates the step if it is ever turned back on. The rest of this
runbook applies only then.

The prepare step was built without ever running `claude -p`, so nobody has seen the LinkedIn deny rule in
`outreach/plan/claude_run.py` take effect on his seat. It is passed both ways at once (`--settings` and
`--disallowedTools`) and names `linkedin.com`, `*.linkedin.com`, `www.linkedin.com`, `lnkd.in` and `*.lnkd.in`. The
step shipped **off** and was switched on on 2026-10-25, after the check below passed (both calls DENIED, 0 web fetches,
with the exact command `claude_command` builds): while `prepare.deny_rule_checked` is false in
`outreach/plan/config.toml`, `python -m outreach.plan prepare` prints one line and exits 0, touching nothing.
`prepare.enabled` is the on and off switch.

1. Run `claude --help` and confirm it still lists `--tools`, `--allowedTools`, `--disallowedTools`, `--settings` and
   `--permission-mode` (with `dontAsk`).
2. Make two calls from the neutral folder with the command `claude_command` builds, every `linkedin.com` in the deny
   rules replaced by `example.com`: one asks the model to fetch `https://example.com`, one `https://uk.example.com`
   (the module docstring has the exact steps). Both must say the fetch was denied.
3. If the subdomain call fetched, the `*.` pattern is not honoured: add the LinkedIn hosts by name to `LINKEDIN_DENY`
   and repeat. If the plain call fetched, leave the step off and say so.
4. Only when both are denied, set `prepare.deny_rule_checked = true` in `outreach/plan/config.toml` (a commit, then
   the usual deploy).

Code refuses a LinkedIn source whatever the model returns, so a wrong form never stores a LinkedIn page; the deny rule
only stops the model from fetching one.

## Evidence only

Research reports. Their facts and measurements stand; their **Recommendation** sections do not decide anything.
Where one disagrees with #9, #9 wins. Each file opens with a note listing what #9 overruled.

| File | Still useful for |
|---|---|
| `research/2026-10-18-cold-email-deliverability.md` | Gmail limits, how filters work, bounce and stop signals |
| `research/2026-10-17-current-system-audit.md` | How the old code works and where it misfires (rules regression cases for #4) |
| `research/2026-10-17-data-lifecycle-and-reliability.md` | SQLite settings and measured data volumes (#4) |
| `research/2026-10-17-crawl-and-scheduling.md` | ATS endpoints, ETags, launchd behavior across sleep (#21) |
| `research/2026-10-17-growth-signals.md` | Form D, YC, HN and wage-level sources (deferred, #7) |
| `research/2026-10-17-outreach-and-tracking.md` | Gmail and LinkedIn rules, CRM options considered |
| `research/2026-10-20-outreach-history.md` | His recorded outreach April to September 2026: who replied, by stage, role and channel |
| `research/2026-10-20-lca-small-junior-employers.md` | Small employers that filed junior software H-1Bs in FY2025, the staffing filter, coverage of his lists |
| `research/2026-10-20-visa-rules-and-employer-targeting.md` | OPT, STEM OPT, the weighted lottery, fee rules and cap-exempt employers as of October 2026 |
| `research/2026-10-20-company-discovery-and-job-data.md` | Which job boards small sponsors use, board discovery, ETag and diff support, free and paid sources |
| `research/2026-10-20-llm-posting-extraction.md` | Reading postings with a model: Claude features, measured volumes and cost, accuracy evidence, schema |
| `research/2026-10-20-people-and-channels.md` | Referral evidence, who to contact, LinkedIn rules and limits, finding emails safely |

## Superseded (history only, do not build from)

- `superpowers/specs/2026-10-17-outreach-system-design.md`
- `superpowers/plans/2026-10-17-track-a-outreach-live.md`
