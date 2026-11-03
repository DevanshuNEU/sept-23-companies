# Rules that find his level Implementation Plan (#154)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each posting's decision says fit, reject or uncertain for his level, tech roles are told apart from
non-tech ones, and "no sponsorship" becomes a flag instead of a hide, so the job list shows the right jobs.

**Architecture:** One migration adds `decision.level_state` and `decision.years_ms` and recreates the two decision
views. The rules (`outreach/rules/`) compute the new fields beside today's fields; `level_tier` keeps its seven values
and `level_ok` its meaning. Ranking and the reader stop hiding companies for sponsorship. The job list and its alerts
read `level_state`.

**Tech Stack:** Python 3.14, SQLite (STRICT, foreign keys on), `unittest`.

**Spec:** `docs/superpowers/plans/2026-11-02-right-job-pipeline-master-plan.md` PR 3, issue #154, #9 level and visa
rulings (2026-11-01, 2026-11-02).

## Global Constraints

- `level_tier` keeps its seven CHECK values (`0001_initial.sql:203`); "III" and "3" titles get tier `swe2` with basis
  "Title says III". `level_ok` is computed exactly as today except where a tier changes on purpose (the "III" case).
- `level_state` values: `fit`, `reject`, `uncertain`. Years for his level use the master's figure first, else the
  general figure. Rules:
  - fit: tier `new_grad`, `early_career` or `swe1`; or years 2 or fewer; or entry wording.
  - reject: tier `senior`; or years over 3; or a scientist or researcher title whose body requires a PhD.
  - uncertain: every other tech posting (a plain title with no years, `open` tier with no years, scientist titles
    without a PhD requirement, years exactly 3 for a non-titled role).
  - non-tech lanes: `reject`.
- Years: a degree tier clause gives `years_ms` ("MS and 0 years", "or 1 year with an advanced degree", "BS and 2 years
  or MS and 0"); "up to N years" counts as 0; "N and M years" without "between" counts as the low end; a figure tied
  to one named technology ("3+ years of experience with Python") does not count.
- Tech words added to `[title] engineering`: ml, machine learning, ai engineer, applied ai, data engineer, analytics
  engineer, applied scientist, research engineer, data scientist, research scientist, sre, site reliability.
  `scientist` leaves `off_lane`. Added to `off_lane`: supply chain, procurement, logistics, sourcing, buyer, planner,
  thermal, optical, photonics, laser, process engineer, hvac, chemical, materials, data analyst, business intelligence,
  data annotation. Data Analyst, BI and annotation roles are not tech.
- Ambiguous titles (Systems, Test, Design, Product, Application, Project Engineer): the body decides; count software
  words (python, typescript, javascript, java, golang, rust, c++, react, sql, kubernetes, docker, aws, gcp, pytorch,
  tensorflow, api, backend, frontend, microservices, ci/cd, linux) against physical words (solidworks, autocad, catia,
  ansys, fea, cfd, gd&t, pcb, altium, oscilloscope, soldering, thermodynamics, hvac, plc, scada, bom, six sigma,
  injection molding, sheet metal); tech when software leads by 2 or more, else off lane.
- Sponsorship is a flag: `passes()` drops the sponsorship test; ranking's company hide for a sponsorship refusal and
  its `no_go` write go; the reader's combine stops treating sponsorship as a hide; `plan/fresh.py` and
  `plan/browse.py` stop dropping companies for it. Citizenship, clearance and ITAR "US person" still fail `passes()`,
  hiding the posting only.
- Rule files changing makes a new ruleset version; deploy stops the crawl job, runs `uv run python -m
  outreach.install` once (applies `0007` and the one rebuild, about 11 minutes on 360,000 postings), then starts it.
- The job list (`outreach/pipeline/joblist.py`) and alerts read `level_state`: your level = `fit`, or `uncertain` at a
  company of 300 or fewer or unknown size; higher = other listed postings; tier `senior` never listed.
- Tests use scratch settings; never run `python -m outreach.*` against the live store; never start, stop or signal a
  process except a test run you started, by pid. No em dashes or en dashes. Test first.
- One branch (`DevanshuNEU/rules-154`), one PR to `master`, body `Closes #154`. Devanshu reviews and merges this one.

## Review Focus

1. **Every reader of `level_ok`, `level_tier` and `passes()`** (ranking roles and lanes, plan fresh lanes, reader
   queue priority, body refetch, review triggers, the job list) behaves as today for unchanged tiers. Test in Task 4:
   `test_level_ok_unchanged_for_existing_tiers` over a sample of saved decisions.
2. **The rebuild on 360,000 postings** must not leave postings undecided: every new `level_state` value fits the CHECK.
   Test in Task 1 and Task 4.
3. **A tick that meets a stale ruleset** skips deciding and does not fail the board. Test in Task 4.
4. **A company that says "no sponsorship"** stays ranked, on Today and in the job list with its flag; no `no_go` row is
   written. Test in Task 5.
5. **"Software Engineer III"** asking "2 years, or 1 with an advanced degree" (Google's wording) is fit. Test in Task 3.

---

### Task 1: The migration and the decision fields

**Files:** `outreach/store/migrations/0007_level_state.sql`; `outreach/rules/decide.py` (`Decision.level_state`,
`Decision.years_ms`, `as_row`); `outreach/rules/rebuild.py` (`_COLUMNS`); every module that writes or reads decision
rows by column list (grep `_COLUMNS`, `stored_decision`, `INSERT INTO decision`).
**Tests:** a migration test (the columns exist, the CHECK refuses another value, the two views expose the columns);
`tests/test_rules_decide.py` (the fields are in `as_row`).

- [ ] Write the failing tests; run; implement `ALTER TABLE decision ADD COLUMN level_state TEXT CHECK (level_state IN
  ('fit','reject','uncertain'))`, `ADD COLUMN years_ms INTEGER`, and `DROP VIEW` plus `CREATE VIEW` for
  `posting_decision` and `current_decision` with the new columns; the fields default to the values Task 4 computes
  (until then `level_state` = `fit` when `level_ok` else `uncertain` for tech, `reject` otherwise); run the full suite;
  commit `Rules: level_state and years_ms on each decision (#154)`.

### Task 2: Years for a master's, ranges and technology figures

**Files:** `outreach/rules/years.py`, `outreach/rules/config.toml`. **Tests:** `tests/test_rules_years.py` (add).

**Interfaces:** `years_asked(...)` keeps its current return for callers and gains the master's figure (return a small
frozen `YearsAsked(general, ms, quote)` or add `years_ms_asked(...)`; pick the smaller change and update every caller).

- [ ] Failing tests: `test_ms_tier_gives_zero` ("BS and 2 years or MS and 0"), `test_advanced_degree_one_year`
  ("2 years, or 1 year with an advanced degree" gives general 2, ms 1), `test_up_to_five_years_is_zero`,
  `test_n_and_m_years_is_low_end`, `test_years_with_python_does_not_count`, `test_years_of_professional_experience_counts`;
  implement; run the rules tests; commit `Rules: master's years, ranges and technology figures (#154)`.

### Task 3: Titles: III, tech nouns, non-tech words and ambiguous titles

**Files:** `outreach/rules/title.py`, `outreach/rules/config.toml`. **Tests:** `tests/test_rules_title.py` (add).

- [ ] Failing tests: `test_swe_iii_is_swe2_with_title_basis`, `test_iv_and_v_stay_senior`, one test per added tech
  noun and per added non-tech word, `test_scientist_is_engineering_lane`, `test_systems_engineer_python_body_is_tech`,
  `test_design_engineer_solidworks_body_is_off_lane`, `test_ambiguous_title_with_no_body_is_off_lane`; implement (the
  body scorer lives with the title rules and reads its word lists from `config.toml`); run; commit `Rules: III by the
  body, tech nouns and non-tech words (#154)`.

### Task 4: `level_state`

**Files:** `outreach/rules/decide.py` (`_level` returns the state too). **Tests:** `tests/test_rules_decide.py` (add),
`tests/test_rules_rebuild.py` or the existing rebuild tests (add), `tests/test_crawl_tick.py` (add).

- [ ] Failing tests: `test_swe_iii_two_years_or_one_with_ms_is_fit`, `test_plain_title_no_years_is_uncertain`,
  `test_research_scientist_phd_required_is_reject`, `test_more_than_three_years_is_reject`,
  `test_non_tech_is_reject`, `test_level_ok_unchanged_for_existing_tiers` (decide the saved fixture postings in
  `tests/fixtures/` with the old and new code paths and compare `level_ok` for every tier except the III case),
  `test_rebuild_writes_level_state_for_every_posting`, `test_tick_skips_deciding_on_stale_ruleset`; implement; run the
  full suite; commit `Rules: fit, reject or uncertain for each posting (#154)`.

### Task 5: Sponsorship is a flag

**Files:** `outreach/rules/decide.py` (`passes`), `outreach/ranking/hide.py` (the refusal step), `outreach/ranking/
job.py` (the `write_no_go` call), `outreach/reader/combine.py` (`_hides`, `_relax`), `outreach/plan/fresh.py` and
`outreach/plan/browse.py` (their sponsorship gates), the No-go page query (`outreach/api/queries/` no-go view: rows
with source `rules` say "written by the ranker"). **Tests:** add to `tests/test_ranking_hide.py`,
`tests/test_ranking_job.py`, `tests/test_reader_combine.py`, `tests/test_plan_*`, `tests/test_api_no_go*`.

- [ ] Failing tests: `test_no_sponsorship_is_flag_not_hide` (company stays ranked, no no-go row),
  `test_today_keeps_a_no_sponsorship_company`, `test_clearance_hides_posting_not_company`,
  `test_combine_does_not_hide_for_sponsorship`, `test_no_go_page_marks_ranker_rows`; implement; update the tests that
  pinned the old hide; run the full suite; commit `Rules: no sponsorship is a flag, not a hide (#154)`.

### Task 6: The job list and alerts read `level_state`

**Files:** `outreach/pipeline/joblist.py`, `outreach/pipeline/alerts.py`. **Tests:** `tests/test_pipeline_joblist.py`,
`tests/test_pipeline_alerts.py` (update and add).

- [ ] Failing tests: your level is `fit` plus `uncertain` at a small or unknown-size company; an `uncertain` big
  company posting is in higher; a `reject` from years is in higher; tier `senior` is never listed; the level tag shows
  "level not stated" for `uncertain`; alerts fire for `fit` only; implement; run the full suite and the web checks;
  commit `Job list: read level_state (#154)`.

### Task 7: PR

- [ ] Push and open the PR (`Closes #154`). The body says the deploy needs the crawl job stopped, `outreach.install`
  (one rebuild, about 11 minutes) and the crawl job started again, and that Today will show companies that say "no
  sponsorship" again, with the flag. Do not merge; Devanshu reviews.
