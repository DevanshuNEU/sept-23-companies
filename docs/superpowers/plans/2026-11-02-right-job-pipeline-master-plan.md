# Right job, right company, right time: master plan

> **For agentic workers:** this is the master plan. It fixes the architecture, the order, the boundaries and the
> acceptance for every PR. Before a PR starts, write its own step-by-step plan with superpowers:writing-plans from
> the PR's section here, then build it with superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Knock finds every tech job at Devanshu's level at the companies he can work for on OPT, sees it within
minutes of posting (before SimplifyJobs), and puts it in front of him with a true date.

**Architecture:** Every source of companies and boards feeds one funnel that checks a board before it joins the
crawl. A parallel crawler reads boards by tier (hot boards every 15 minutes in US working hours) and stores each
posting with where its date came from. Rules sort each posting into fit, reject or uncertain; Claude reads the fresh
uncertain ones inside the crawl tick. A job list and Mac alerts show the result; a daily scorecard, a miss log and
alarms prove it keeps working.

**Tech Stack:** Python 3.14 (Homebrew), SQLite (WAL, STRICT tables), standard library HTTP (`outreach/net.py`),
FastAPI, React with TanStack Query, launchd, Claude Code (`claude -p`) through his seat.

**Spec:** `docs/superpowers/specs/2026-11-01-fresh-jobs-quiet-needs-you-design.md` (sections 5 to 7 are the core).
Evidence: `.context/research/` in the `sydney` workspace (round 4 `r4-*.md`, round 5 `arkenos-round5-results.md`).

## Global Constraints

- His one rule decides every open question: the right job, at the right company, at the right time.
- Two pipelines matter: the Outbox (done, left alone) and this one (first priority). Keep the system simple: no
  referral features, no LinkedIn data, nothing that touches LinkedIn.
- Every rule in `CLAUDE.md` holds: Claude never calls the API's write routes; crawls, source fetches, reader runs and
  anything on the live store run only in the live checkout; tests use scratch settings and never reach the network.
- Test first. Every bug fix starts with a test that reproduces it.
- One issue, one branch from `origin/master`, one PR to `master`. No stacked PRs: a PR that needs an unmerged one
  stays local until the first is merged, then rebases.
- Migrations are numbered with no gaps. Each PR below owns the migration number listed; if PRs merge in a different
  order, renumber before merging. Prefer `ALTER TABLE ... ADD COLUMN` over table rebuilds; rebuild only small tables
  (`board`, about 2,000 rows) and only when a CHECK must change.
- Every new outside source has its terms and robots.txt checked and noted in its module docstring. SimplifyJobs:
  board links only, never its postings (its repo has no license). Not used: LinkedIn, Indeed, Glassdoor scraping,
  GitHub commit emails, JobSpy.
- No em dashes or en dashes in code comments, UI copy, docs, commit messages or PR bodies.
- Decisions he made on 2026-11-01 and 2026-11-02: hide only citizenship, clearance and ITAR "US person" roles
  (posting level); "no sponsorship", "no H-1B" and future sponsorship wording are flags; big companies shown with a
  tag; tech only; ML, AI and data engineering count, Data Scientist and AI or ML Researcher count when the posting
  asks for engineering work and no PhD, Data Analyst and BI do not; internships out; default window 30 days; Go ahead
  always adds the company to today's list unless it is a real duplicate; Pass hides one posting only.

## Review Focus

1. **A job posted while a tick is running** must be seen by the next tick, never skipped because its board's
   `next_read_at` moved past it. Test in PR 5: a board read at 10:00 with a posting that appears at 10:01 is due
   again by 10:15 on a weekday.
2. **The reader is unavailable** (session limit, auth error, `claude` missing): the tick must finish its reads,
   uncertain postings stay listed as "level not stated", and nothing is hidden. Test in PR 6.
3. **The same job reaches Knock from two places** (a SimplifyJobs link and the board read): one posting, one alert.
   Test in PR 4 (alerts dedupe on posting id) and PR 7 (the funnel never creates a second board for a known
   `(ats, token)`).
4. **A huge board** (Workday over 2,000 rows, Amazon or TikTok portals) must not starve the hot tier or fill the store
   with non-tech rows. Tests in PR 5 (per-host workers keep other hosts moving) and PR 8 (the gate stores no posting
   for a board with no US engineering role).
5. **The rules version changes** while the crawler runs: `decide_and_store` raises `RulesetNotCurrent` until the
   rebuild finishes (about 11 minutes on 360,000 postings). The deploy steps for PR 2 run the rebuild before the next
   tick, and the tick must skip deciding (not crash) if it meets a stale ruleset. Test in PR 2.

---

## The architecture after this plan

```
 sources (daily or every tick)            funnel                     crawler (every 10 min tick)
 stapply lists, SimplifyJobs links,  ->  board_candidate  ->  gate  ->  board (tier: hot, active, quiet, dead)
 speedyapply, yc-oss, VC portfolios,      (one table, every   (one     |  one worker per host bucket,
 Workable search, HN, LCA, Form D,         source writes      list      |  each with its own connection
 careers pages, by hand                    here)              read)     v
                                                                     posting (+ date_source, req_id, repost_of)
                                                                        |
                                                  rules: fit / reject / uncertain (decision.level_state)
                                                                        |
                                              inline reader on fresh uncertain postings at hot boards
                                                                        |
                                job list (GET /api/jobs) + Mac alerts + scorecard, miss log, alarms
```

What changes in each existing part:

| Part | Today | After |
|---|---|---|
| `outreach/sources/` | Sources create companies; stapply is only a lookup for companies other sources named | Every board finder writes to `board_candidate`; stapply becomes a source; new feeds (SimplifyJobs links, speedyapply, yc-oss, Consider and Getro, Workable search) |
| `outreach/crawl/cadence.py`, `tick.py` | One loop, one board at a time; interval from change age, 6 h floor | Board tiers with fixed intervals and business hours; one worker per host bucket; a gate read for candidates |
| `outreach/crawl/polite.py` | Plain dicts, no locks, blocking sleep | Per-bucket lock; safe across threads; same spacing per host |
| `outreach/crawl/ats/*` | Date falls back to `updated_at`; Workday "30+" is None; Workday fails on capped families; BambooHR and JazzHR broken | Each reader reports a date source; Workday delta poll, second facet split, `myworkdaysite.com`, requisition numbers; BambooHR and JazzHR fixed |
| `outreach/rules/` | Plain titles rejected; "III" is senior; largest years number wins; "scientist" off lane; "no sponsorship" hides | Three states; "III" decided by the body; master's years; wider tech nouns and non-tech list; sponsorship is a flag |
| `outreach/reader/` | Night only; 62,956 queued, 1,868 read; level never used | A fresh slice read inside the tick; the reader's level moves a posting between views (never hides without promotion) |
| `outreach/ranking/hide.py`, `job.py` | Company hidden and a no-go row written for "no sponsorship" | Neither; citizenship, clearance and ITAR hide the posting only |
| `outreach/api`, `web` | Browse lists ranked companies | `GET /api/jobs` lists postings; the Browse page becomes the job list with Pass and Go ahead |
| new `outreach/pipeline/` | none | scorecard, miss log, alarms, alerts, board tiers |

## Data model changes

| Migration | PR | Change |
|---|---|---|
| `0006_pipeline_yardstick.sql` | PR 1 | `feed_listing(source, listing_id, url, ats, token, native_id, company_name, title, posted_hint, first_seen_at, last_seen_at, active)`, unique on `(source, listing_id)`; `miss(miss_id, url, note, pasted_at, cause, detail, posting_id, company_id, board_id, resolved_at)` |
| `0007_level_state.sql` | PR 2 | `decision.level_state TEXT CHECK (level_state IN ('fit','reject','uncertain'))`, `decision.years_ms INTEGER`, `decision.flags TEXT DEFAULT '[]' CHECK (json_valid(flags))` (sponsorship and form-question flags); `rebuild.py` `_COLUMNS` updated |
| `0008_date_source.sql` | PR 3 | `posting.date_source TEXT CHECK (date_source IN ('ats','watch','lower_bound','unknown'))`, `posting.req_id TEXT`, `posting.repost_of INTEGER REFERENCES posting(posting_id)` |
| `0009_board_tier.sql` | PR 5 | Rebuild `board` to add `tier TEXT NOT NULL DEFAULT 'active' CHECK (tier IN ('hot','active','quiet','dead'))`, `tier_reason TEXT`, `tier_until TEXT`, widen `found_by` with `'feed','stapply_bulk','vc','workable_search','yc'`, and add ATS values for new readers (`'tiktok','tesla','microsoft'`); all indexes and triggers recreated |
| `0010_board_candidate.sql` | PR 7 | `board_candidate(candidate_id, ats, token, name, url, domain, source, source_ref, priority, found_at, gate_after, gated_at, gate_result CHECK IN ('promoted','no_us_engineering','dead','error','known'), open_us_engineering INTEGER, board_id)`, unique on `(ats, token)` |

Events, not tables, for: alerts sent (`entity='posting'`, kind `alerted`, key `alert:posting:<id>`), job list passes
(`entity='posting'`, kind `posting_passed`, actor `devanshu`), the daily scorecard (`entity='score'`, kind
`pipeline_scorecard`, detail holds the numbers), tier changes (`entity='board'`, kind `tier_changed`).

---

## PR 0: Edit #9, open the issues

No code. In issue #9, record the rulings listed in the spec's section 10, plus the 2026-11-02 ones: data and ML
titles as above, stapply boards imported through a gate, no referral or LinkedIn data features, the Outbox items
(smarter sending, spam signals) parked. Open one GitHub issue per PR below with its "Done when" lines and its
"Depends on". Devanshu reviews the #9 edit before PR 2 starts.

## PR 1: Scorecard, yardstick feed, miss log, alarms

**Why first:** every later PR is judged by these numbers; today the baseline lives in scratch scripts.

**Files:** create `outreach/pipeline/__init__.py`, `__main__.py`, `config.toml`, `config.py`, `simplify_feed.py`,
`scorecard.py`, `misses.py`, `alarms.py`; migration `0006`; MCP tool `log_miss` in `outreach/mcp/`; API
`GET /api/pipeline/scorecard` (router, model, query) and a status line in the web sidebar; crawl tick calls
`simplify_feed.poll` once per tick.

**Interfaces:**
- `simplify_feed.poll(conn, client, now) -> FeedPoll(changed: bool, seen: int, new: int)`: one conditional GET of
  `raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json` (ETag stored in a
  small key value row or `feed_listing` meta row), upserts US software and AI/ML/Data rows into `feed_listing`,
  setting `first_seen_at` only on insert. Parses `(ats, token, native_id)` from each URL with one shared function
  `pipeline.urls.board_key(url) -> BoardKey | None` (Greenhouse incl. `gh_jid=` and custom domains with
  `boards.greenhouse.io/embed`, Ashby, Lever, Workday incl. `myworkdaysite.com`, Workable, Rippling, iCIMS,
  JazzHR `applytojob.com`). PR 7 reuses `board_key`.
- `scorecard.compute(conn, today) -> Scorecard` with fields `simplify_coverage`, `small_company_coverage` (None until
  PR 10), `detection_p95_hours`, `lead_over_simplify`, `level_recall`, `date_truth` (None until PR 3), and
  `gap_top: list[GapCompany]`. Definitions are the spec's 5.0 table; coverage counts a listing as covered when a
  posting with the same `(ats, token, native_id)` exists.
- `python -m outreach.pipeline scorecard [--json]` prints it and records the `pipeline_scorecard` event; the daily
  sources job (`run-due`) runs it last.
- `misses.log_miss(conn, url, note, now) -> Miss` and `misses.diagnose(conn, url) -> MissCause` where the cause is one
  of `no_company`, `no_board`, `not_read_yet`, `read_wrong_level`, `read_not_tech`, `hidden_visa`, `wrong_date`,
  `found` (Knock has it), `unknown_site`.
- `alarms.check(conn, now) -> list[Alarm]`: a reader (ATS) whose reads errored on 50% or more of its boards over 48
  hours; a scorecard number down 25% or more week over week; a source failing twice in a row. Each alarm shows on the
  sidebar status line and sends at most one Mac notice a day (`sending.notify.notify_once`, kind `pipeline_alarm`).

**Done when** (tests on scratch stores with a saved `listings.json` slice):
- `test_board_key_parses_every_shape`: one case per URL shape above, including a `gh_jid` custom domain and a
  `myworkdaysite.com` URL.
- `test_poll_sets_first_seen_once` and `test_poll_unchanged_etag_writes_nothing`.
- `test_scorecard_on_seeded_store`: a seeded store with 10 feed listings, 4 matching postings (2 fit, 1 uncertain, 1
  reject), gives coverage 0.4 and level recall 0.75 (fit plus uncertain count as recalled).
- `test_diagnose_each_cause`: one seeded case per cause.
- `test_alarm_fires_once_a_day` for a reader at 100% errors; `test_no_alarm_below_threshold`.
- In the live checkout after deploy, the scorecard prints numbers close to the 2026-11-01 baseline (coverage about
  32%, level recall about 51%).

## PR 2: Rules that find his level

**Depends on:** PR 0 (#9 edits 1 and 2).

**Files:** `outreach/rules/title.py`, `years.py`, `decide.py`, `sponsorship.py` (no change to detection, only how
its answer is used), `config.toml`; `outreach/rules/rebuild.py` (`_COLUMNS`); `outreach/ranking/hide.py`,
`ranking/job.py`, `ranking/roles.py`; `outreach/reader/combine.py`; migration `0007`.

**Changes:**
1. `title.py`: "III" and "3" leave `_SENIOR_NUMERALS` and return a new tier `level3`; "IV", "V", "4", "5" stay
   senior. `engineering` gains `ml`, `machine learning`, `ai engineer`, `applied ai`, `data engineer`,
   `analytics engineer`, `applied scientist`, `research engineer`, `data scientist`, `research scientist`,
   `scientist` leaves `off_lane` (its own lane check moves to `level` below), `sre`, `site reliability`. `off_lane`
   gains supply chain, procurement, logistics, sourcing, buyer, planner, thermal, optical, photonics, laser,
   process engineer, HVAC, chemical, materials, data analyst, business intelligence, data annotation.
2. `years.py`: `years_asked` returns `YearsAsked(general: int | None, ms: int | None, quote: str)`. The master's figure
   comes from a degree tier clause ("MS and 0 years", "or 1 year with an advanced degree"); "up to N years" is 0; "N
   and M years" without "between" is the low end.
3. `decide.py`: `_level` returns `(tier, state, basis)` with `state` in fit, reject, uncertain. Years used are
   `ms if ms is not None else general`. Plain engineering title with no years: uncertain. `level3`: fit when years
   are 2 or fewer or entry wording, reject when years are over 3, else uncertain. Scientist and researcher titles:
   reject when the body requires a PhD, else uncertain. `level_ok` stays as `state == 'fit'` for every existing
   reader of it.
4. Sponsorship: `passes()` drops `sponsorship != 'no'`; the stance goes into `decision.flags`. `hide.py` step 5 and
   the `write_no_go` call in `job.py` are removed; `combine.py` `_hides` and `_relax` stop treating sponsorship as a
   hide. Citizenship, clearance and ITAR still fail `passes()` (posting only).
5. `roles.py`: `EARLY_CAREER_TIERS` treats `level3` with fit state as early career.

**Done when:** the spec's C2 and C3 "Done when" cases pass, plus `test_swe_iii_two_years_or_one_with_ms_is_fit`,
`test_up_to_five_years_is_zero`, `test_plain_title_no_years_is_uncertain`,
`test_research_scientist_phd_required_is_reject`, `test_no_sponsorship_is_flag_not_hide` (company stays ranked, no
no-go row), `test_clearance_hides_posting_not_company`, and `test_tick_skips_deciding_on_stale_ruleset` (Review
Focus 5). Deploy: `uv run python -m outreach.install` (migration) then `uv run python -m outreach.rules.rebuild`
before kickstarting the crawl job. Afterward the scorecard's level recall rises from about 51% (target 85% once PR 6
lands).

## PR 3: True posted dates and reposts

**Files:** `outreach/crawl/base.py` (`JobRef.date_source`, `JobRef.req_id`), every reader in `outreach/crawl/ats/`,
`crawl/postings.py` (`write_postings`), `crawl/ingest.py`, `outreach/ranking/roles.py` and `signals/live_role.py`
(freshness only from `ats`, `watch` or `lower_bound`), migration `0008`.

**Changes:** the spec's C1 table and rules. Greenhouse and Ashby never fall back to update times. Workday "30+ days
ago" sets `posted_on = first_seen_date - 30` with `lower_bound`. Rippling reads `createdOn` from the detail call for
new ids (counts against `detail_per_read`). Workable switches to the v1 widget's `created_at`. In `write_postings`:
when a reader gives no date, `watch` applies if the board's first successful read is more than 1 day older than the
posting's first sight, else `unknown`; when two dates disagree the older wins and `date_source` follows it; a NULL
`date_source` on an existing row is filled on its next read. Reposts: a new posting with the same company and
normalized title as one closed in the last 120 days gets `repost_of` and keeps that posting's `first_seen_at` for
age; a Workday `req_id` below the board's 15th percentile is flagged in `decision.flags` as `old_requisition`. Application form visa questions (spec 5.1b item 10): Ashby's application form comes with the list; Rippling's
knockout questions come with the detail call it already makes; Greenhouse needs one `questions=true` detail call,
made only for new postings in the fit or uncertain state. A "will you need sponsorship now or in the future" question
adds the flag `form_asks_sponsorship`; it never hides.

**Done when:** the spec's C1 and C5 "Done when" cases, plus `test_form_sponsorship_question_is_flag`, `test_greenhouse_old_first_published_new_updated_is_old`,
`test_workday_30_plus_on_first_read_is_lower_bound`, `test_watch_date_only_after_board_watched_a_day`,
`test_older_date_wins`, `test_repost_inherits_first_seen`. The scorecard's date truth number appears.

## PR 4: The job list and alerts

**Depends on:** PR 2 and PR 3 (merged).

**Files:** `outreach/api/routers/jobs.py`, `api/models/jobs.py`, `api/queries/jobs.py`, `api/services/jobs.py` (pass,
go ahead); `outreach/pipeline/joblist.py` (the pure query and view rules), `outreach/pipeline/alerts.py`; `web/src/
routes/browse/` rebuilt as the job list (route name stays `browse`, keys `b`, `g b`, `f` keep working);
`api/client.ts`, `api/http/`, `api/keys.ts`, fixtures; regenerated OpenAPI and `schema.ts`.

**Interfaces:**
- `joblist.query(conn, today, f: JobFilters, after: str | None, limit: int) -> JobPage`; `JobFilters(view:
  Literal['your_level','higher'], window_days: Literal[7,30,60,90,0], role_types, size, remote, not_contacted, q)`.
  Views follow the spec's Part D, with "level not stated" (uncertain) rows in the default view tagged, and reader
  results (PR 6) moving rows between views. Order: real posted date descending, then smaller company first; dated
  rows, then `lower_bound` rows (only in 60, 90, any), then a separate "Date unknown" group. Keyset cursor on
  `(posted_on, size_rank, posting_id)`.
- `GET /api/jobs` through `views.serve`; `POST /api/jobs/{postingId}/pass` and `POST /api/jobs/{postingId}/go-ahead`
  through `run_write` with idempotency keys `job-pass:<id>` and `job-go:<id>`. Pass writes the `posting_passed`
  event, which `load_passed_postings` also reads and `load_passes` does not. Go ahead calls a new
  `plan.picks.add_own_pick(conn, company_id, today, now) -> Pick` that refuses only a no-go company, a company already
  on today's list, or a company with someone contacted and waiting; if today's plan is not built yet it stores the
  pick and the 08:00 build keeps it.
- `alerts.run(conn, notifier, now) -> int`: called at the end of each crawl tick; one grouped Mac notice for postings
  that entered the default view since the last run with a real date of today, none between 22:00 and 08:00 New York,
  at most one notice per 15 minutes, each posting alerted once (event key `alert:posting:<id>`).
- The weekly accuracy check: a "Check 20" mode on the page shows 20 random rows from the default view; he marks
  each right or wrong on level and tech; labels go to the reader's labeled set (`~/Library/Application Support/
  outreach/gold/items.jsonl`, through `reader.gold`), which PR 6 measures against.
- The miss log field: "Paste a job Knock missed" calls `POST /api/pipeline/misses` (PR 1's `misses.log_miss`) and
  shows the diagnosed cause.
- Row shape and copy per the spec's Part D (title, level tag, company, posted age or "date unknown", size and "big
  company" tag, location, visa flags, momentum, H-1B entry level filings, recent raise, YC batch, contacted). The page
  goes through the `craft` skill and its gate before UI code.

**Done when:** the spec's Part D "Done when" lines, plus `test_check20_writes_gold_labels`, `test_miss_route_returns_cause`, `test_pass_is_not_a_company_not_now`,
`test_go_ahead_ignores_size_room_and_cooldown`, `test_go_ahead_refuses_duplicate`, `test_alert_once_per_posting`
(Review Focus 3), `test_no_alert_in_quiet_hours`, and an e2e run on the workspace port (`WEB_PORT=$CONDUCTOR_PORT
pnpm e2e`) covering pass and go ahead with the keyboard.

## PR 5: A crawler that does not wait on itself, and tiers

**Files:** `outreach/crawl/polite.py` (per-bucket `threading.Lock`, bucket state safe across threads),
`crawl/tick.py` (workers), `crawl/cadence.py` (tier intervals), new `outreach/pipeline/tiers.py`,
`crawl/config.toml` (`[tiers]`), `launchd/com.devanshu.outreach-crawl.plist.template` (`StartInterval` 600),
migration `0009`.

**Changes:**
1. Workers: `run_tick` groups due boards by host bucket and runs one thread per bucket. Each thread opens its own
   store connection (`connect()`; WAL and `busy_timeout = 5000` already set) and runs today's `read_board` for its
   boards in order. `_Pass` state becomes per worker and is summed at the end. The tick budget and the crawl lock are
   unchanged. A redirect into another bucket waits on that bucket's lock.
2. Tiers (`tiers.assign(conn, now) -> TierCounts`, run at the start of each tick, cheap SQL): hot when the company is
   not no-go and not hidden and any of: posted a fit or uncertain role in the last 12 months, any intern or new grad
   role ever, 3 or more new engineering postings in 14 days, a feed listing in the last 30 days, a Form D first sale in
   the last 90 days, a YC hiring flip in the last 30 days, a pick in the last 14 days. Active: at least one open US
   engineering posting. Quiet: none. Dead: no open postings for 60 days or a gate result of dead.
3. Intervals (`[tiers]` in config, minutes): hot 15 on weekdays 08:00 to 19:00 New York and 90 otherwise; active 120
   and 480; quiet 1440; dead 10080. Error backoff unchanged. `floor_s` no longer applies to hot and active boards.
   `cadence.next_interval_s` takes the tier; `due_boards` orders hot first, then never-read, then oldest due.

**Done when:** `test_workers_read_buckets_in_parallel` (a fake clock shows a slow Workday bucket does not delay
Greenhouse reads), `test_bucket_spacing_unchanged_under_threads`, `test_hot_board_due_in_15_minutes_on_weekday`
(Review Focus 1), `test_hot_board_90_minutes_at_night`, `test_tier_rules` (one case per rule), and in the live
checkout the scorecard's detection p95 for hot boards falls under 1 hour within a week. Ask Devanshu before the first
live run (heavier load on his laptop).

## PR 6: The reader inside the tick

**Depends on:** PR 2 (uncertain state).

**Files:** `outreach/reader/queue.py` (a `fresh` priority tier and a slice filter), `reader/inline.py` (new),
`reader/prompts/read_posting.md` (two added questions), `reader/combine.py`, `reader/config.toml` (`[inline]`),
`crawl/tick.py` (one call after the reads).

**Changes:** the fresh slice is open, US not "no", engineering, `level_state = 'uncertain'`, first seen in the last 30
days, at a hot or active board. `next_batch(..., slice='fresh')` refreshes priorities only for that slice (the full
queue refresh over about 63,000 rows stays for the night job). `inline.read_fresh(conn, runner, now, cap=20,
budget_s=240) -> InlineResult` runs at most one batch per tick with `call_timeout_s = 180`. The prompt adds: "Can a
master's graduate with no full time experience get this role?" and "How many years does it ask of a master's
holder?". `combine.py` uses the reader's level for uncertain postings only: new grad, early career, or years for a
master's of 2 or fewer move the posting to fit; senior or more than 3 years move it to the higher levels view. The
reader never hides a posting unless its version is promoted (`promoted_version`), as today. A session limit or any
runner failure ends the inline step for that tick and leaves postings uncertain (Review Focus 2).

**Done when:** `test_fresh_slice_only`, `test_reader_level_moves_uncertain_to_fit`, `test_reader_never_hides_unpromoted`,
`test_tick_survives_session_limit`, and on a labeled set of at least 300 postings (100 at his level, built from the
weekly accuracy check in PR 4's page) precision of 90% or more and recall of 85% or more for the default view. The
night job keeps working the rest of the queue at lower priority.

## PR 7: One funnel for every board finder, and SimplifyJobs links

**Files:** `outreach/pipeline/funnel.py` (add candidate, gate), `crawl/gate.py` (one list read that counts US
engineering titles and writes nothing else), `outreach/sources/simplify_links.py` (reads `feed_listing` from PR 1),
`sources/jobs.py` (find-boards step 0 also reads promoted candidates), migration `0010`.

**Changes:** `funnel.add(conn, BoardCandidate) -> CandidateId` ignores a known `(ats, token)` (board or candidate).
The gate runs inside the crawl tick at low priority after the hot tier: at most `[gate] per_tick` (200) candidates,
highest priority first (feeds 100, VC 80, stapply 50). A passing candidate (at least one open US engineering title by
the rules' title check) goes through `identity.resolve_candidate` with key `ats:<ats>:<token>` and becomes a board
with `found_by` set from its source and tier active; the company's domain comes from the board payload where the ATS
publishes it. A failing candidate is kept with its result, so it is never read again unless its source shows it again
after 30 days. Custom site links resolve through `board_key` (`gh_jid=`, `applytojob.com`, iCIMS).

**Done when:** `test_known_board_never_duplicated` (Review Focus 3), `test_gate_writes_no_posting_for_failing_board`,
`test_gate_promotes_and_links_domain`, `test_simplify_link_becomes_candidate_once`, and in the live checkout the
scorecard's SimplifyJobs coverage rises within a week of deploy.

## PR 8: stapply boards through the funnel

**Depends on:** PR 7.

**Files:** `outreach/sources/stapply.py` (a `StapplyBoards` source over the CSVs Knock already downloads; the lookup
stays for find-boards), `sources/__main__.py` (`run-due` step and a one-time `seed stapply` command).

**Changes:** every row of every allowed list (not `wellfound` or `ycombinator`) becomes a candidate with priority 50.
The first seed adds about 75,000 candidates; at 200 gate reads per tick and a tick every 10 minutes the backlog clears
in about 2.5 days at polite spacing. Weekly runs add only new rows.

**Done when:** `test_seed_adds_candidates_not_boards`, `test_excluded_lists_skipped`, `test_weekly_run_adds_only_new`,
and in the live checkout after the backlog clears, the scorecard's coverage of SimplifyJobs listings reaches the
range the research measured (stapply held 115 of the 168 missed ATS boards). Devanshu agreed on 2026-11-02; say when
the seed starts.

## PR 9: Workday done right; BambooHR and JazzHR fixed

**Files:** `outreach/crawl/ats/workday.py`, `ats/bamboohr.py`, `ats/jazzhr.py`, `crawl/base.py` (`_GRAMMAR` for
`myworkdaysite.com`), `outreach/sources/ats_detect.py`, new fixtures captured with `python -m outreach.crawl probe`
in the live checkout.

**Changes:** Workday: a delta read for hot boards between full reads (page 1 with an empty search, stop at the first
`externalPath` already stored), confirmed newest first on captured responses from at least 5 tenants before it is
relied on; a full read weekly; a capped family splits again by a second facet (location, then time type) instead of
failing; `bulletFields[0]` stored as `req_id`; `myworkdaysite.com` hosts read. BambooHR reads its JSON job list;
JazzHR parses the current page layout.

**Done when:** `test_workday_delta_stops_at_known`, `test_workday_capped_family_splits_by_location`,
`test_workday_req_id_stored`, `test_myworkdaysite_url_read`, `test_bamboohr_json_list`, `test_jazzhr_current_layout`,
and in the live checkout the BambooHR and JazzHR alarms from PR 1 clear.

## PR 10: Company feeds and the small-company yardstick

**Files:** `outreach/sources/yc.py` (yc-oss daily changes and `hiring.json`), `sources/hn.py` (also the Hacker
News job ads, not only the monthly thread), `sources/vcboards.py` (Consider and
Getro company lists, weekly), `sources/workable_search.py` (only if Workable's terms allow it), `sources/formd.py`
(first sale date), `outreach/pipeline/scorecard.py` (small-company coverage), `sources/__main__.py`, the job list's
Just funded tab (API view and page tab).

**Changes:** each feed adds companies with size and stage facts and board candidates; yc-oss flips and Form D first
sales feed the hot tier rule. Small-company coverage: of companies under 300 people in the VC and YC lists that have an
open US engineering role on their board, the share Knock reads; the scorecard shows it first. Just funded per the
spec's Part D.

**Done when:** one fixture test per feed (adds facts and candidates, a second run adds nothing),
`test_formd_uses_first_sale_date`, `test_small_company_coverage`, `test_just_funded_window`.

## PR 11: New readers where the scorecard shows a gap

**Files:** `outreach/crawl/ats/icims.py`, `successfactors.py`, then company portals (`tiktok.py`, `amazon.py`,
`tesla.py`, `microsoft.py`) ported from stapply's MIT `ats-scrapers` with attribution in each docstring; registration
per the crawler's existing steps (ADAPTERS, ATS_HOSTS, host spacing, BOARD_ATS).

**Changes:** big portals read with new grad and software search terms only (never the full portal) and are tagged big
company. Each reader is added only when the scorecard's gap list names its ATS among the top causes.

**Done when:** fixture tests per reader, and the scorecard's gap list no longer names that ATS.

## Side PRs (small, any time, separate workspace)

- **Fast pages** (spec Part A): stale-while-revalidate in `views.serve`, warm after jobs, data gaps paged, profile
  Today.
- **Quiet Needs you** (spec Part B).
- Parked by Devanshu on 2026-11-02 (Outbox is done): smarter sending, spam signals, address checks, the 3 follow-up
  change. They stay in the spec's Part E for later.

## Order and timing

| Order | PR | Depends on | Rough size |
|---|---|---|---|
| 0 | #9 edits and issues | nothing | an hour |
| 1 | Scorecard, yardstick, miss log, alarms | nothing | 2 days |
| 2 | Rules that find his level | PR 0 | 2 days, plus an 11 minute rebuild at deploy |
| 3 | True dates and reposts | nothing | 2 days |
| 4 | Job list and alerts | PR 2, PR 3 | 3 days |
| 5 | Parallel crawler and tiers | nothing (rebases after PR 3) | 2 days |
| 6 | Reader inside the tick | PR 2 | 2 days, plus labeling sittings |
| 7 | Funnel and SimplifyJobs links | PR 1 | 2 days |
| 8 | stapply through the funnel | PR 7 | 1 day, plus about 2.5 days of gate reads |
| 9 | Workday, BambooHR, JazzHR | nothing | 2 days |
| 10 | Company feeds, small-company yardstick, Just funded | PR 7 | 2 days |
| 11 | New readers | scorecard gap list | per reader |

He has a usable job list after PR 4 (about a week of work); the scorecard shows each later PR's effect. Two PRs at a
time at most, each in its own workspace, merged in the order above.

## Risks and how each is handled

- **Laptop load.** Parallel workers and the gate raise request rates per tick but never per host. Each step that adds
  load (PR 5 first live run, PR 8 seed) is announced to him first.
- **The reader's seat hits a session limit** (it did during research on 2026-11-01). The inline step stops for that
  tick; rules-only results stay visible; nothing waits on it.
- **Store growth** (1.08 GB, 360,856 postings). The gate keeps non-US and non-tech boards out; dead boards are read
  weekly and store nothing new.
- **A wrong rules change hides good jobs.** Rules never hide on level, only sort into views; the miss log and the
  weekly accuracy check catch regressions; every rules PR runs the old and new versions on the store and reports what
  moved before deploy (`rules.rebuild` flip report).
- **Terms change at a source.** Each source's module notes its terms; a source failing twice raises an alarm.
