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
| `superpowers/plans/2026-10-21-daily-people-plan.md` | #15: Today as a plan of people, capacity, the prepare step, LinkedIn notes and checks. Built; where the code differs from the plan text, the rulings at the top of the plan and the hand-offs below win. #9's LinkedIn limits ruling (2026-10-26) replaces the plan's month counting and its 200-character note: requests count over the last 7 New York days (150 for the plan, 200 in all), the day is his pace (`linkedin.per_day`, 7), and a note may be 300 characters |
| `outreach/plan/` (how to run) | `uv run python -m outreach.plan --help` lists the commands: `build` (the day's plan, once per date; `--dry-run`, `--no-read-boards`), `show` (a stored plan, read only) and `prepare` (the morning step through `claude -p`, live checkout only; off while `prepare.enabled` is false, as shipped). From a worktree use a scratch `OUTREACH_DB` |
| `superpowers/plans/2026-10-21-daily-people-plan.md` | #15: Today as a plan of people, capacity, the prepare step, LinkedIn notes and checks. Built; where the code differs from the plan text, the rulings at the top of the plan and the hand-offs below win. #9's LinkedIn limits ruling (2026-10-26) replaces the plan's month counting and its 200-character note: requests count over the last 7 New York days (150 for the plan, 200 in all), the day is his pace (`linkedin.per_day`, 7), and a note may be 300 characters. #9's note authorship ruling (2026-10-26): a note he saved is checked as his text (`check_note(author='user')`), on honesty and hard limits only, with no reason line or fact rule, its facts' `in_note` read from his text, and the app no longer asks him to confirm a line |
| `outreach/plan/` (how to run) | `uv run python -m outreach.plan --help` lists the commands: `build` (the day's plan, once per date; `--dry-run`, `--no-read-boards`), `show` (a stored plan, read only) and `prepare` (the morning step through `claude -p`, live checkout only). From a worktree use a scratch `OUTREACH_DB` |
| `outreach/plan/` (modules) | `config.toml` and `config.py` (every number), `dates.py`, `outbound.py` (one read of outbound history), `capacity.py` (the last 7 days of requests against both limits, then his daily pace), `contact.py` (the 14-day rule), `people.py` (candidates, channel, `add_candidate`), `notecheck.py` and `drafts.py` (the one note checker and draft writer), `select.py` (pure choice of rows), `prospects.py` (loaders, carry-forward, the `why` keys), `boards.py` and `build.py` (the plan job), `prepare_targets.py`, `prepare_store.py`, `claude_run.py`, `prepare.py` and `prompts/prepare.md` (the prepare step) |
| `launchd/com.devanshu.outreach-plan.plist.template`, `launchd/com.devanshu.outreach-prepare.plist.template` | The plan job (Monday to Friday 08:00) and the prepare job (Monday to Friday 05:30); `{{LIVE_CHECKOUT}}` and `{{LOG_PATH}}` only; installed by `python -m outreach.deploy install`, never by the repo |
| `tests/test_plan_*.py`, `tests/plan_seed.py`, `tests/fixtures/plan/`, `tests/pipeline/stages_plan.py` | The plan's tests on scratch stores (no network, no Claude: the prepare runner, the boards reader and the rank runner are fakes), the `plan` stage of the pipeline flow test with its invariants (capacity, one person per company, the 14-day boundary, the lane share, people before find-someone, only laned and visible companies, a second run changes nothing). `tests/mutation_check.py` carries the plan's mutation entries |
| `superpowers/plans/2026-10-21-api.md` | #11: FastAPI, generated types, idempotent writes, outcomes, the HTTP client that replaces the fixtures |
| `superpowers/plans/2026-10-21-web-experience.md` | #33: the people plan, outcomes, progress, wins and the finish line (mockups first) |
| `superpowers/plans/2026-10-21-integration-review.md` | Cross-plan review of the eight plans and how each finding was resolved |
| `superpowers/plans/2026-10-22-sending-engine.md` | #14: the new sender: ramp, spacing, follow-up, holds, stop rules, the two-week simulation (migrations 0002 and 0003) |
| `superpowers/plans/2026-10-22-import-and-switch-over.md` | #17: import all history (interviews included), the rehearsal, the deploy tool and the switch runbook (migration 0004). Part A (the importer, `outreach/importer/`) and B1 (the deploy tool, `outreach/deploy/`) are built; B2 (CLAUDE.md, this map and the cold-email skill) merges on switch day. The runbook in this plan is the switch: rehearsal (R1), switch day (S1 to S14) and rollback (R-back) |
| `superpowers/plans/2026-10-22-retire-job-radar.md` | #5: retire Job Radar after its history is imported |
| `superpowers/plans/2026-10-22-needs-you.md` | #16: the five Needs-you triggers, the sweep, one-click fixes, merge and unmerge. Built: `outreach/review/` (Tasks 1 to 12), the API (Task 13) and the web view in `web/src/routes/needs-you/` (Tasks 15 to 17: a section for every kind, the side-by-side merge confirm, Undo from Resolved, the plain progress line and the All settled panel). `?scenario=review` shows every kind on sample data |
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
