# Right job, right company, right time: master plan

> **For agentic workers:** this is the master plan. It fixes the architecture, the order, the boundaries and the
> acceptance for every PR. Before a PR starts, write its own step-by-step plan with superpowers:writing-plans from
> the PR's section here, then build it with superpowers:subagent-driven-development.

**Goal:** Knock puts Devanshu in front of the right company and the right job: every tech job at his level at
companies he can work for on OPT, seen soon after it is posted, with a true date.

**Architecture:** First make the crawler healthy and measured. Then fix what a posting is (level, date), show the
jobs in one list with alerts, and only then add speed (parallel reads, tiers), a background reader, and coverage
(one funnel for every board finder). A daily scorecard shows each step's effect.

**Tech Stack:** Python 3.14 (Homebrew), SQLite (WAL, STRICT tables, foreign keys on), standard library HTTP
(`outreach/net.py`), FastAPI, React with TanStack Query, launchd, Claude Code (`claude -p`) through his seat.

**Spec:** `docs/superpowers/specs/2026-11-01-fresh-jobs-quiet-needs-you-design.md` (sections 5 to 7). Where this plan
and the spec differ, this plan wins (see "Corrections to the spec").

**Revision 2 (2026-11-02):** rewritten after live measurements and an independent review against the code. What
changed and why is in "What the checks found" below.

## Global Constraints

- His one rule: the right job, at the right company, at the right time. Only work that serves it is in this plan.
  The Outbox is done and is not touched. No referral features, no LinkedIn data, nothing that touches LinkedIn.
- Every rule in `CLAUDE.md` holds: Claude never calls the API's write routes; crawls, source fetches, reader runs and
  anything on the live store run only in the live checkout; tests use scratch settings and never reach the network.
- Test first. Every bug fix starts with a test that reproduces it.
- One issue, one branch from `origin/master`, one PR to `master`. No stacked PRs.
- Migrations are numbered with no gaps; renumber before merging if order changes. Use `ALTER TABLE ... ADD COLUMN`
  or new side tables. Never rebuild a table that other tables reference (`board`, `posting`, `company`): foreign keys
  are on during migrations (`install.py:30`, `migrate.py:93`) and the commit fails. Allowed values are never added
  to a STRICT CHECK list by a rebuild in this plan; new kinds of values go in new columns or side tables.
- `level_tier` keeps its seven allowed values (`0001_initial.sql:203`); `level_ok` keeps today's meaning, because
  ranking, Today's lanes, reader priority and body refetch read it. New behavior reads new columns.
- Every new outside source has its terms and robots.txt checked and noted in its module docstring. SimplifyJobs:
  board links only, never its postings. Not used: LinkedIn, Indeed, Glassdoor scraping, GitHub commit emails, JobSpy.
- No em dashes or en dashes in code comments, UI copy, docs, commit messages or PR bodies.
- His decisions: hide only citizenship, clearance and ITAR "US person" roles (posting level); "no sponsorship", "no
  H-1B" and future sponsorship wording are flags; big companies shown with a tag; tech only; ML, AI and data
  engineering count, Data Scientist and AI or ML Researcher count when the posting asks for engineering work and no
  PhD, Data Analyst and BI do not; internships out; default window 30 days; Go ahead always adds the company to today's
  list unless it is a real duplicate; Pass hides one posting only.

## What the checks found (2026-11-02)

Measured read-only on the live store:

- **The crawler is already over capacity.** Every tick (`read_boards`) uses its whole 1,500 second budget, reads 104
  to 381 boards, and 477 boards were overdue at 17:55 UTC. A full pass over 2,000 boards takes about 4 hours, not
  21 minutes. Hot boards every 15 minutes would need about 10 times today's capacity.
- **The reader is slow.** Night runs store about 94 readings in 135 minutes, about 1.4 minutes a posting. New US
  engineering postings with no stated level: 24 to 2,816 a day. A reader inside the tick cannot keep up.
- **Posting churn.** Each tick closes 250 to 850 postings and finds 235 to 790 new ones. Some "new" jobs may be old
  ones coming back under new ids, which would make "posted today" alerts wrong.
- **About 1,331 boards** have had an engineering posting at his level, or with no stated level, in the last 12 months
  (the size of a "hot" tier before any new source).

Independent review against the code found 11 problems in revision 1; all are fixed below. The three that would have
failed on deploy: a new `level3` tier the `decision` CHECK refuses; a `board` table rebuild that cannot commit with
foreign keys on; a reader whose level is ignored while no reader version is promoted.

## Review Focus

1. **A job posted while a tick runs** is seen by a later tick, never skipped. Test in PR 7.
2. **The reader is unavailable** (session limit, auth error): crawling continues, uncertain postings stay listed as
   "level not stated", nothing is hidden. Test in PR 8.
3. **The same job reaches Knock from two places** (a SimplifyJobs link and the board read): one posting, one alert.
   Tests in PR 5 (alerts dedupe on posting id) and PR 9 (the funnel never makes a second board for a known board).
4. **A huge board** (Workday over 2,000 rows) never starves other hosts or fills the store with non-tech rows. Tests
   in PR 6 (Workday delta read) and PR 7 (other hosts keep moving).
5. **The rules version changes** while the crawler runs: the deploy for PR 3 stops the crawl job, runs `install` once
   (one rebuild), then restarts it. Test in PR 3: a tick that meets a stale ruleset skips deciding and does not fail
   the board.

## The architecture after this plan

```
 board finders                       funnel                       crawler (launchd tick)
 stapply lists, SimplifyJobs   ->  board_candidate  ->  gate  ->  board  (+ board_tier side table)
 links, YC, VC lists, careers      (every finder       (one list     |   workers per host bucket, each with
 pages, by hand                     writes here)        read)        v   its own connection, polite per host
                                                                  posting (+ date_source, req_id, repost_of, flags)
                                                                     |
                                       rules: decision.level_state = fit / reject / uncertain
                                                                     |
                                 job list (GET /api/jobs), Mac alerts, scorecard, miss log, alarms
                                                                     ^
                          background reader (its own launchd job) reads fresh uncertain postings at hot
                          boards and records a level hint that moves a row between views, never hides
```

## Data model changes

| Migration | PR | Change |
|---|---|---|
| `0006_yardstick.sql` | PR 2 | `feed_listing(source, listing_id, url, ats, token, native_id, company_name, title, posted_hint, first_seen_at, last_seen_at, active, backfill INTEGER)` unique `(source, listing_id)`; `miss(miss_id, url, note, pasted_at, cause, detail, posting_id, company_id, board_id, resolved_at)` |
| `0007_level_state.sql` | PR 3 | `decision.level_state TEXT CHECK (level_state IN ('fit','reject','uncertain'))`, `decision.years_ms INTEGER`; recreate the `posting_decision` and `current_decision` views with the new columns; `rebuild.py` `_COLUMNS` updated |
| `0008_date_source.sql` | PR 4 | `posting.date_source TEXT CHECK (date_source IN ('ats','watch','lower_bound','unknown'))`, `posting.req_id TEXT`, `posting.repost_of INTEGER REFERENCES posting(posting_id)`, `posting.flags TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(flags))`, `board.first_read_at TEXT`; a backfill statement sets `board.first_read_at` from each board's oldest `posting.first_seen_at` and `date_source` on existing rows that can be judged from stored data |
| `0009_board_tier.sql` | PR 7 | `board_tier(board_id INTEGER PRIMARY KEY REFERENCES board(board_id), tier TEXT NOT NULL CHECK (tier IN ('hot','active','quiet','dead')), reason TEXT NOT NULL, since TEXT NOT NULL, until TEXT)`; no row means active |
| `0010_reader_hint.sql` | PR 8 | `level_hint(posting_id INTEGER PRIMARY KEY REFERENCES posting(posting_id), reader_version TEXT NOT NULL, state TEXT CHECK (state IN ('fit','higher','senior')), ms_years INTEGER, quote TEXT, read_at TEXT)` |
| `0011_board_candidate.sql` | PR 9 | `board_candidate(candidate_id, ats, token, name, url, domain, source, source_ref, priority, found_at, gated_at, gate_result CHECK IN ('promoted','no_us_engineering','dead','error','known'), open_us_engineering INTEGER, board_id REFERENCES board)` unique `(ats, token)`; the board's origin source lives here, so `board.found_by` is not widened |

Events, not tables: alerts (`entity='posting'`, kind `alerted`, key `alert:posting:<id>`), job list passes
(`entity='posting'`, kind `posting_passed`, actor `devanshu`), Check 20 verdicts (`entity='posting'`, kind
`level_verdict`), early Go ahead picks (`entity='company'`, kind `own_pick_requested`), the daily scorecard
(`entity='score'`, kind `pipeline_scorecard`). All of these entities are already allowed by the `event` CHECK.

---

## PR 0: Edit #9, open the issues

No code. Record in #9 the rulings in the spec's section 10 and the 2026-11-02 ones (data and ML titles, stapply boards
through a gate, no referral or LinkedIn features, Outbox work parked). One GitHub issue per PR below with its "Done
when" and "Depends on". Devanshu reviews the #9 edit before PR 3 starts.

## PR 1: A healthy crawler (measure first, then cut cost)

**Why first:** nothing about speed or coverage can be promised while every tick runs out of time.

**Files:** `outreach/crawl/tick.py`, `crawl/ingest.py`, `crawl/bodies.py`, `crawl/ats/greenhouse.py`, `crawl/
__main__.py` (a `profile` command), `crawl/config.toml`.

**Changes:**
1. **Measure.** Each tick's `job_run.summary` gains per ATS: boards read, list requests, list seconds, body requests,
   body seconds, decide seconds, and postings new, closed and reopened. `python -m outreach.crawl profile --runs N`
   prints them from stored runs (read only, runs anywhere on a scratch store).
2. **Explain the churn.** From stored data, find which ATS and boards close and re-add postings every tick, and why
   (a listing that is not complete, ids that change, pagination that is not stable). Fix the cause in that reader, or
   mark such a read as partial so it closes nothing. The finding is written into the PR body.
3. **Cut the cost the measurement shows.** Expected, to confirm by measurement: Greenhouse lists without
   `content=true` and one detail call only for new engineering postings; bodies only for new US engineering postings,
   never for a posting already holding a body unless `body_refetch_days` says so; the 100-posting decide step runs
   before the write transaction opens, not inside it (`ingest.py:337-344` holds the write lock while deciding).

**Done when:** `test_profile_reports_per_ats_seconds`, `test_greenhouse_list_without_content_fetches_new_bodies_only`,
`test_decide_runs_outside_write_transaction`, a test that reproduces the churn cause found in step 2, and in the live
checkout over 2 days: no tick ends with boards overdue by more than their interval, and a full pass over today's
boards takes under 60 minutes with today's per-host spacing.

## PR 2: Scorecard, yardstick, miss log, alarms

**Files:** new `outreach/pipeline/` (`__init__.py`, `__main__.py`, `config.toml`, `config.py`, `urls.py`,
`simplify_feed.py`, `scorecard.py`, `misses.py`, `alarms.py`); migration `0006`; MCP tool `log_miss`; API
`GET /api/pipeline/scorecard` and `POST /api/pipeline/misses`; a status line in the web sidebar; the crawl tick calls
`simplify_feed.poll` once per tick.

**Interfaces:**
- `urls.board_key(url) -> BoardKey(ats, token: str | None, native_id: str | None) | None` for Greenhouse (including
  `gh_jid=` on a company's own site, where `token` is None and matching uses `native_id`), Ashby, Lever, Workday
  (including `myworkdaysite.com`), Workable, Rippling, iCIMS, JazzHR `applytojob.com`. PR 9 reuses it.
- `simplify_feed.poll(conn, client, now) -> FeedPoll(changed, seen, new)`: one conditional GET of SimplifyJobs'
  `listings.json`; US software and AI/ML/Data rows upserted into `feed_listing`, `first_seen_at` set on insert only;
  rows present on the very first poll are marked `backfill = 1`.
- `scorecard.compute(conn, today) -> Scorecard(simplify_coverage, small_company_coverage, detection_p95_hours,
  lead_over_simplify, level_recall, date_truth, gap_top)`. Fields a later PR provides are None until then.
  `lead_over_simplify` leaves out `backfill` rows.
- `python -m outreach.pipeline scorecard [--json]`; `run-due` runs it last and records the `pipeline_scorecard` event.
- `misses.log_miss(conn, url, note, now) -> Miss`, `misses.diagnose(conn, url) -> MissCause` in `no_company`,
  `no_board`, `not_read_yet`, `read_wrong_level`, `read_not_tech`, `hidden_visa`, `wrong_date`, `found`, `unknown_site`.
- `alarms.check(conn, now) -> list[Alarm]`: a reader whose reads errored on half or more of its boards over 48 hours;
  a scorecard number down a quarter week over week; a source failing twice in a row; boards overdue by more than twice
  their interval. Shown on the sidebar line; one Mac notice a day at most (`sending.notify.notify_once`, kind
  `pipeline_alarm`).

**Done when:** `test_board_key_parses_every_shape` (one case per shape, `gh_jid` with token None),
`test_poll_sets_first_seen_once`, `test_poll_unchanged_etag_writes_nothing`, `test_first_poll_rows_are_backfill`,
`test_scorecard_on_seeded_store` (10 listings, 4 postings of which 2 fit, 1 uncertain, 1 reject: coverage 0.4, level
recall 0.75), `test_diagnose_each_cause`, `test_alarm_once_a_day`, `test_no_alarm_below_threshold`. In the live
checkout the BambooHR and JazzHR alarms fire (they fail on every board today).

## PR 3: Rules that find his level

**Depends on:** PR 0.

**Files:** `outreach/rules/title.py`, `years.py`, `decide.py`, `config.toml`, `rules/rebuild.py`; `outreach/ranking/
hide.py`, `ranking/job.py`; `outreach/reader/combine.py`; `outreach/plan/fresh.py:130-134`, `plan/browse.py:109`
(their own sponsorship gates); the No-go page query (rows written by ranking marked "written by the ranker");
migration `0007`. Check every caller of `stored_decision` (`plan/fresh.py`, `review/triggers.py`) against the new
columns.

**Changes:**
1. `level_state` (fit, reject, uncertain) is computed beside today's fields; `level_tier` and `level_ok` are computed
   exactly as today, except where item 2 changes a tier on purpose.
2. Title: "III" and "3" leave `_SENIOR_NUMERALS`; such a title gets tier `swe2` with basis "Title says III", and its
   state comes from the body (fit when the years for a master's are 2 or fewer or there is entry wording, reject when
   over 3, else uncertain). Tech nouns widen (ML, machine learning, AI engineer, applied AI, data engineer, analytics
   engineer, applied scientist, research engineer, data scientist, research scientist, SRE, site reliability);
   "scientist" leaves `off_lane`; the non-tech list widens (supply chain, procurement, logistics, sourcing, buyer,
   planner, thermal, optical, photonics, laser, process engineer, HVAC, chemical, materials, data analyst, business
   intelligence, data annotation). Titles that can be either (Systems, Test, Design, Product, Application, Project
   Engineer) are scored on the body: software words against physical engineering words, software wins by 2 or more.
3. Years: `years_asked` also returns the master's figure from a degree tier clause ("MS and 0 years", "or 1 year with
   an advanced degree"); "up to N years" counts as 0; "N and M years" without "between" counts as the low end; a
   number tied to one technology ("3+ years of experience with Python") does not count.
4. State rules: plain tech title with no years: uncertain. Scientist and researcher titles: reject when the body
   requires a PhD, else uncertain. New grad, early career, SDE 1, or 2 or fewer years (master's figure first): fit.
   Senior titles or more than 3 years: reject.
5. Sponsorship becomes a flag: `passes()` drops the sponsorship test; `hide.py` step 5 and the `write_no_go` call in
   `job.py` go; `combine.py` stops treating sponsorship as a hide; the two plan gates above stop dropping "no
   sponsorship" companies. Citizenship, clearance and ITAR still fail `passes()` (posting only).

**Deploy:** stop the crawl job (`launchctl bootout` of `com.devanshu.outreach-crawl`), `uv run python -m
outreach.install` (applies `0007` and runs the one rebuild, about 11 minutes), then start the crawl job again.

**Done when:** `test_swe_iii_two_years_or_one_with_ms_is_fit`, `test_up_to_five_years_is_zero`,
`test_years_with_python_does_not_count`, `test_plain_title_no_years_is_uncertain`,
`test_research_scientist_phd_required_is_reject`, `test_systems_engineer_python_body_is_tech`,
`test_thermal_supply_chain_optical_not_tech`, `test_no_sponsorship_is_flag_not_hide` (company stays ranked and on
Today, no no-go row), `test_clearance_hides_posting_not_company`, `test_level_ok_unchanged_for_existing_tiers` (a
sample of stored decisions keeps its `level_ok`), `test_tick_skips_deciding_on_stale_ruleset`. After deploy the
scorecard's level recall (fit plus uncertain) is at least 85% of SimplifyJobs listings Knock has.

## PR 4: True posted dates and reposts

**Files:** `outreach/crawl/base.py` (`JobRef.date_source`, `JobRef.req_id`), every reader in `crawl/ats/`,
`crawl/postings.py`, `crawl/ingest.py`, `outreach/ranking/roles.py` and `signals/live_role.py` (freshness only from
`ats`, `watch`, `lower_bound`), migration `0008`.

**Changes:** Greenhouse and Ashby never fall back to update times. Workday "30+ days ago" is `lower_bound` (posted on
or before first sight minus 30 days); a Workday relative date under 30 days is `ats`. Rippling reads `createdOn` from
the detail call for new ids. Workable reads `created_at` from the v1 widget. With no reader date: `watch` when
`board.first_read_at` is more than 1 day before the posting's first sight, else `unknown`. Two dates disagree: the
older wins. Reposts: a new posting with the same company and normalized title as one closed in the last 120 days sets
`repost_of` and takes that posting's `first_seen_at` for age. A Workday `req_id` below the board's 15th percentile
adds `old_requisition` to `posting.flags`. Application form questions: Ashby's form (in the list), Rippling's knockout
questions (in the detail it already fetches), Greenhouse `questions=true` (one detail call, only for new postings in
the fit or uncertain state); a future sponsorship question adds `form_asks_sponsorship` to `posting.flags`, never a
hide. Existing rows: the migration's backfill sets what stored data can prove; the rest fill on the next full read.

**Done when:** `test_greenhouse_old_first_published_new_updated_is_old`, `test_workday_30_plus_is_lower_bound`,
`test_watch_date_only_after_board_watched_a_day`, `test_older_date_wins`, `test_repost_inherits_first_seen`,
`test_form_sponsorship_question_is_flag`, `test_backfill_sets_first_read_at`. The scorecard's date truth appears.

## PR 5: The job list and alerts

**Depends on:** PR 3 and PR 4 merged.

**Files:** `outreach/pipeline/joblist.py`, `pipeline/alerts.py`; `outreach/api/routers/jobs.py`, `api/models/jobs.py`,
`api/queries/jobs.py`, `api/services/jobs.py`; `outreach/plan/picks.py` (`add_own_pick`), `plan/build.py` (reads
early picks); `web/src/routes/browse/` rebuilt as the job list (route `browse`, keys `b`, `g b`, `f` unchanged);
`web/src/api/` client, keys, fixtures; regenerated OpenAPI and `schema.ts`. The page goes through the `craft` skill and
its gate before UI code.

**Interfaces:**
- `joblist.query(conn, today, f: JobFilters, after: str | None, limit: int) -> JobPage` with `JobFilters(view:
  'your_level' | 'higher', window_days: 7 | 30 | 60 | 90 | 0, role_types, size, remote, not_contacted, q)`. The default
  view shows fit rows and uncertain rows (tagged "level not stated"); a level hint from PR 8 moves an uncertain row
  to fit or to higher. Order: real posted date, newest first, then smaller company; `lower_bound` rows only in 60, 90
  and any, after dated rows; "Date unknown" in its own group. Keyset cursor on `(posted_on, size_rank, posting_id)`.
- `GET /api/jobs` through `views.serve`; `POST /api/jobs/{postingId}/pass` and `POST /api/jobs/{postingId}/go-ahead`
  through `run_write` (keys `job-pass:<id>`, `job-go:<id>`). Pass writes `posting_passed`, read by
  `load_passed_postings`, not by `load_passes`.
- `plan.picks.add_own_pick(conn, company_id, today, now) -> OwnPick`: refuses only a no-go company, a company on
  today's list, or one with someone contacted and waiting. When today's list exists, it adds a row at the next free
  rank. When it does not exist yet, it records `own_pick_requested` and `build_plan` adds those companies when it runs.
- `alerts.run(conn, notifier, now) -> int`, called at the end of each tick: one grouped Mac notice for fit rows whose
  real date is today and that entered the default view since the last run; none 22:00 to 08:00 New York; at most one
  notice per 15 minutes; each posting once (`alert:posting:<id>`). Uncertain rows do not alert until a level hint says
  fit.
- Check 20: a mode on the page shows 20 random default-view rows; each right or wrong verdict is a `level_verdict`
  event. The miss field calls `POST /api/pipeline/misses` and shows the cause.

**Done when:** the spec's Part D "Done when" lines, plus `test_pass_is_not_a_company_not_now`,
`test_go_ahead_ignores_size_room_and_cooldown`, `test_go_ahead_refuses_duplicate`,
`test_go_ahead_before_plan_is_kept_by_build`, `test_alert_once_per_posting`, `test_no_alert_in_quiet_hours`,
`test_uncertain_row_does_not_alert`, `test_check20_writes_verdict_events`, `test_miss_route_returns_cause`, and an
e2e run on the workspace port covering pass and go ahead by keyboard.

## PR 6: Workday done right; BambooHR and JazzHR fixed

**Files:** `outreach/crawl/ats/workday.py`, `ats/bamboohr.py`, `ats/jazzhr.py`, `crawl/base.py` (`_GRAMMAR` for
`myworkdaysite.com`), `outreach/sources/ats_detect.py`; fixtures captured with `python -m outreach.crawl probe` in the
live checkout.

**Changes:** Workday: a delta read (page 1 with an empty search, stop at the first `externalPath` already stored),
relied on only after captured responses from at least 5 tenants show page 1 is newest first; a full read weekly; a
capped family splits again by location, then time type, instead of failing; `bulletFields[0]` stored as `req_id`;
`myworkdaysite.com` read. BambooHR reads its JSON job list; JazzHR parses its current layout.

**Done when:** `test_workday_delta_stops_at_known`, `test_workday_capped_family_splits_by_location`,
`test_workday_req_id_stored`, `test_myworkdaysite_url_read`, `test_bamboohr_json_list`, `test_jazzhr_current_layout`;
in the live checkout the BambooHR and JazzHR alarms clear and Workday's share of tick seconds (PR 1's profile) drops.

## PR 7: Parallel reads and tiers

**Depends on:** PR 1, PR 3, PR 6.

**Files:** `outreach/crawl/polite.py`, `crawl/tick.py`, `crawl/cadence.py`, new `outreach/pipeline/tiers.py`,
`crawl/config.toml` (`[workers]`, `[tiers]`), `launchd/com.devanshu.outreach-crawl.plist.template`, migration `0009`.

**Changes:**
1. Workers: at most `[workers] max` (start at 4) threads, one per busy host bucket, each with its own store
   connection. Workers pull the next due board for their bucket from a shared queue until the tick budget ends, so a
   board that comes due during the tick is read in that tick. One shared stop event (set on `RulesetNotCurrent` or a
   tripped breaker policy) and one shared set of tripped buckets. The `PoliteClient` lock guards only its timing
   bookkeeping, never a sleep or a request. Per-worker counts are summed at the end.
2. Tick timing: the crawl job runs every 600 seconds and the tick budget is 540 seconds, so ticks do not overlap.
3. Tiers (`tiers.assign(conn, now)`, cheap SQL at tick start, rows in `board_tier`): hot when the company is not
   no-go and not hidden and it posted a fit role in the last 12 months, any new grad or early career role ever, 3 or
   more new engineering postings in 14 days, or had a pick in the last 14 days. Active: at least one open US
   engineering posting. Quiet: none. Dead: no open posting for 60 days. Later PRs add their own hot reasons (PR 9 feed
   listings, PR 11 Form D and YC). Workday boards use the delta read from PR 6 when hot.
4. Intervals (`[tiers]`, minutes): hot 30 on weekdays 08:00 to 19:00 New York and 90 otherwise; active 120 and 480;
   quiet 1440; dead 10080. Hot goes to 15 minutes only when the scorecard shows the last 7 days of ticks finished with
   at least 30% of their budget unused.

**Done when:** `test_slow_bucket_does_not_delay_other_hosts` (fake clock), `test_bucket_spacing_unchanged_under_threads`,
`test_board_due_mid_tick_is_read_that_tick` (Review Focus 1), `test_stop_event_stops_all_workers`,
`test_sender_write_succeeds_during_tick` (a sender-style write completes within its busy timeout while 4 workers
write), `test_tier_rules`, `test_hot_interval_day_and_night`. Ask Devanshu before the first live run. In the live
checkout the scorecard's detection p95 for hot boards falls under 1 hour.

## PR 8: The background reader

**Depends on:** PR 3, PR 5, PR 7.

**Files:** `outreach/reader/fresh.py` (new), `reader/queue.py` (a `fresh` slice that enqueues its own rows and
refreshes priorities only within the slice), `reader/prompts/read_posting.md` (two added questions), `reader/
config.toml` (`[fresh]`), new `launchd/com.devanshu.outreach-reader-fresh.plist.template` (every 15 minutes, its own
lock), `outreach/deploy/jobs.py` (`NEW_JOBS`), migration `0010`.

**Changes:** the slice: open, US not "no", tech, `level_state = 'uncertain'`, first seen in the last 30 days, at a hot
or active board, newest first. `python -m outreach.reader fresh` reads at most `[fresh] per_run` postings (start at
8, about 12 minutes at the measured speed) and at most `[fresh] per_day` (start at 200, so the seat stays free for his
own work). The prompt adds "Can a master's graduate with no full time experience get this role?" and "How many years
does it ask of a master's holder?". Each answer writes a `level_hint` row (fit, higher or senior), which the job list
reads; it never changes `decision`, never hides, and needs no promoted version. A session limit or runner failure ends
the run and leaves rows uncertain. The night job keeps the rest of the queue as today.

**Done when:** `test_fresh_slice_enqueues_and_orders`, `test_hint_moves_row_between_views_never_hides`,
`test_run_stops_on_session_limit` (Review Focus 2), `test_daily_cap`. Measured against Check 20 verdicts after 4
weeks: rows the hint calls fit are right at least 90% of the time.

## PR 9: One funnel for every board finder, and SimplifyJobs links

**Depends on:** PR 2, PR 7.

**Files:** `outreach/pipeline/funnel.py`, `crawl/gate.py`, `outreach/sources/simplify_links.py`, `sources/jobs.py`,
`crawl/tick.py` (a gate share), migration `0011`.

**Changes:** `funnel.add(conn, BoardCandidate) -> int | None` ignores a known board or candidate. The gate gets its own
share of each tick (`[gate] seconds_per_tick`, start 90, and `[gate] per_tick`, start 200) so hot reads cannot starve
it, highest priority first (feeds 100, VC 80, stapply 50). A gate read is one list read that counts open US tech
titles with the rules' title check and writes nothing else. A passing candidate goes through
`identity.resolve_candidate` with key `ats:<ats>:<token>` and becomes a board (`found_by` stays within today's values;
the source is kept on the candidate row), and the company domain comes from the board payload where the ATS gives it.
A failing candidate keeps its result and is not read again unless a source shows it again after 30 days. SimplifyJobs
links from `feed_listing` become candidates; a feed listing adds the hot reason "in a new grad list in the last 30
days".

**Done when:** `test_known_board_never_duplicated` (Review Focus 3), `test_gate_writes_no_posting_for_failing_board`,
`test_gate_promotes_and_links_domain`, `test_gate_share_not_starved`, `test_simplify_link_becomes_candidate_once`;
in the live checkout the scorecard's SimplifyJobs coverage rises within a week.

## PR 10: stapply boards through the funnel

**Depends on:** PR 9.

**Files:** `outreach/sources/stapply.py` (a `StapplyBoards` source over the CSVs Knock already downloads; the lookup
stays for find-boards), `sources/__main__.py` (`run-due` step and a one-time `seed stapply` command).

**Changes:** every row of every allowed list (not `wellfound`, not `ycombinator`) becomes a candidate with priority
50; weekly runs add only new rows. At 200 gate reads a tick and a tick every 10 minutes, about 75,000 candidates take
about 2.5 days of polite reads, longer if the gate share is used up.

**Done when:** `test_seed_adds_candidates_not_boards`, `test_excluded_lists_skipped`, `test_weekly_run_adds_only_new`;
in the live checkout coverage rises toward the research's measure (stapply held 115 of the 168 missed boards). Tell
Devanshu when the seed starts.

## PR 11: Company feeds, the small-company yardstick, Just funded

**Depends on:** PR 9.

**Files:** `outreach/sources/yc.py`, `sources/vcboards.py` (Consider and Getro company lists, weekly),
`sources/workable_search.py` (only if Workable's terms allow it), `sources/formd.py` (first sale date),
`sources/hn.py` (also the HN job ads), `pipeline/scorecard.py` (small-company coverage), `pipeline/tiers.py` (Form D
and YC hot reasons), the job list's Just funded tab.

**Done when:** one fixture test per feed (facts and candidates added, a second run adds nothing),
`test_formd_uses_first_sale_date`, `test_small_company_coverage`, `test_just_funded_window`,
`test_formd_and_yc_make_hot`. Company domains from LCA employer emails already exist (`sources/lca.py` `_poc`); no work.

## PR 12: New readers where the scorecard shows a gap

iCIMS, SuccessFactors, then company portals (TikTok, Amazon, Tesla, Microsoft) ported from stapply's MIT
`ats-scrapers` with attribution, each added only when the scorecard's gap list names it, each read with new grad and
software search terms only. A new ATS name needs its own migration for the `board.ats` CHECK, which cannot be rebuilt
while foreign keys are on; this PR first adds a reviewed `migrate.py` path that runs a migration with foreign keys off
and `foreign_key_check` after, with its own tests.

## Order

Issues: design and plan #151; PR 1 #152, PR 2 #153, PR 3 #154, PR 4 #155, PR 5 #156, PR 6 #157, PR 7 #158, PR 8 #159,
PR 9 #160, PR 10 #161, PR 11 #162, PR 12 #163 (milestone "Right job pipeline"). PR 0 is done: #9 rulings of
2026-11-01 and 2026-11-02.

| Order | PR | Depends on |
|---|---|---|
| 0 | #9 edits and issues | nothing |
| 1 | A healthy crawler | nothing |
| 2 | Scorecard, yardstick, miss log, alarms | nothing |
| 3 | Rules that find his level | PR 0 |
| 4 | True dates and reposts | nothing |
| 5 | Job list and alerts | PR 3, PR 4 |
| 6 | Workday, BambooHR, JazzHR | nothing |
| 7 | Parallel reads and tiers | PR 1, PR 3, PR 6 |
| 8 | Background reader | PR 3, PR 5, PR 7 |
| 9 | Funnel and SimplifyJobs links | PR 2, PR 7 |
| 10 | stapply through the funnel | PR 9 |
| 11 | Company feeds, small-company yardstick, Just funded | PR 9 |
| 12 | New readers | the scorecard's gap list |

He has a usable job list after PR 5. At most two PRs in flight, each in its own workspace, merged in this order.

## Corrections to the spec

- 5.1b item 3: the reader runs as its own background job, not inside the tick, and only adds a level hint.
- 5.1b item 5: hot boards start at 30 minutes in working hours; 15 minutes only once capacity is shown.
- C1: Workday "30+ days ago" is a lower bound, always.
- Part D: uncertain rows show in the default view tagged "level not stated"; only fit rows alert.
- Section 7 and Part B, Part A: the fast pages and quiet Needs you work leaves this plan; it is not on the path to the
  right company and the right job. Part E stays parked.

## Risks and how each is handled

- **Laptop load:** per-host spacing never rises; PR 7's first live run and PR 10's seed are announced first.
- **The seat's limits:** the background reader has a daily cap and stops on a session limit; nothing waits on it.
- **Store growth (1.08 GB, 360,856 postings):** the gate keeps boards without US tech roles out.
- **A wrong rules change:** level rules sort into views and never hide; the old and new rules run side by side
  (`rules.rebuild` flip report) before every rules deploy; the miss log and Check 20 catch what slips through.
- **A source's terms change:** each source notes its terms; two failures in a row raise an alarm.
