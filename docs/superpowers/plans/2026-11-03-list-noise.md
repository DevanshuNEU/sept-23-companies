# Job list noise Implementation Plan (#172)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Jobs page's "your level" tab shows software and tech postings in the US that he can take, not every
title with "Engineer" in it at any company anywhere.

**Architecture:** Two rule changes in `outreach/rules/` (a new ruleset version, so deploy runs the one rebuild) and
one change to the job list query (`outreach/pipeline/joblist.py`, read time only, no migration).

**Tech Stack:** Python 3.14, SQLite, `unittest`; one line of web copy.

**Spec:** issue #172 (sized on the live list 2026-11-03), #9 level and visa rulings, #154 plan
(`docs/superpowers/plans/2026-11-03-rules-level-state.md`).

## Global Constraints

- **Lane rule.** In `outreach/rules/title.py` `_lane`, after the `off_lane` and `off_lane_unless_software` checks: a
  title that names software work (`title.software_titles`) is `engineering`. Any other engineering title is decided
  by its body with the existing scorer (`_software_lead(body) >= title.ambiguous_margin`, today 2): software lead
  keeps it `engineering`, else `off_lane`. A body shorter than `title.min_body_chars` (300) after strip counts as no
  body. No body keeps the title `engineering` (the crawler fetches bodies only for the engineering lane, and the
  posting is decided again when its body arrives). The `ambiguous` list stays; its basis wording stays
  ("Title says Systems Engineer and the posting is not clearly software work"); any other title's basis is
  "Title names no software work and the posting is not clearly software work".
- **`software_titles` (config.toml) after the change:** remove `developer` and `programmer` (a bare "Developer" goes
  to the body: "Materials Developer II, Jordan Footwear" is not software). Keep every other current entry. Add:
  `founding engineer`, `member of technical staff`, `mts`, `forward deployed`, `forward deploy`, `fde`, `cloud`,
  `security`, `cyber`, `cybersecurity`, `web`, `mobile`, `ios`, `android`, `ai`, `algorithm`, `algorithms`,
  `computer vision`, `nlp`, `llm`, `genai`, `mlops`, `devsecops`, `python`, `java`, `golang`, `typescript`,
  `javascript`, `react`, `gpu`, `research engineer`, `database`, `data platform`, `data infrastructure`,
  `analytics engineer`, `software developer`, `web developer`, `full stack developer`, `frontend developer`,
  `backend developer`, `mobile developer`, `ios developer`, `android developer`, `application developer`,
  `applications developer`.
- **`off_lane` add (hard, even with a software word):** `bi developer`, `bi engineer`, `bi report`, `bi analyst`,
  `power bi`, `business intelligence`, `report developer`, `reporting developer`, `tableau`. Move
  `business intelligence` out of `off_lane_unless_software` (he ruled BI is not tech). **`off_lane_unless_software`
  add:** `data center`.
- **Country codes.** In `outreach/rules/location.py` `_read_country`: a structured country that is exactly two
  letters (after strip, any case) reads `yes` for `US`, `PR`, `GU`, `VI`, `AS`, `MP`, `UM` and `no` for any other
  two-letter code. Everything else in `us_status` keeps its order (a US place in the location text still beats a
  non-US country: "Austin, TX" with country `GB` reads yes).
- **Big when no size.** `outreach/pipeline/joblist.py`: a company is big when its size is over `BIG_OVER` (300), or
  its size is unknown and either it has more than `BIG_BY_POSTINGS` (50) open postings read as US (`d.us = 'yes'`
  under the current ruleset, counted over the company and every company merged into it) or it has an active board
  with `ats` in `ENTERPRISE_ATS` (`workday`, `oracle_orc`). Call this `big_guess`.
- **Your level after the change:** `(state = 'fit' OR NOT big) AND (us = 'yes' OR NOT big)`, where big covers known
  and guessed. So a `fit` posting at a big company needs a US read of yes; an `uncertain` posting is at his level only
  at a company that is not big. Every other listed posting is in the other tab.
- **Size filter and order:** `small` = known size 300 or fewer; `big` = known over 300 or `big_guess`; `unknown` =
  size unknown and not `big_guess`. Rank: small by size, then unknown, then guessed big (`_BIG_RANK`), then known big
  (`_BIG_RANK + size`).
- **API row:** `big` is true for guessed big too; `size` stays null for them. Web `sizeLine` in
  `web/src/routes/browse/index.tsx` shows "Big company, size not known" when `big` and `size` is null.
- Performance: `GET /api/jobs` stays one query per page plus nothing per row; the plan test (`explain`) keeps
  passing; add an index only if the plan shows a scan of `posting` per company.
- Tests use scratch settings; never run `python -m outreach.*` against the live store; never start, stop or signal a
  process except a test run you started, by pid. No em dashes or en dashes. Test first.
- One branch (`DevanshuNEU/list-noise`), one PR to `master`, body `Closes #172`. Devanshu merges.

## Review Focus

1. A title that names software work never goes to the body ("Software Engineer, Logistics", "Founding Engineer",
   "Forward Deployed Engineer", "Member of Technical Staff").
2. A non-software title with no body or a thin body stays `engineering` (opportunity-first: never hidden before its
   body is read).
3. Small companies with an unknown US read ("Remote", blank) stay at his level.

---

### Task 1: Rules: software titles count, other titles by their body; country codes

**Files:** `outreach/rules/title.py`, `outreach/rules/config.toml`, `outreach/rules/location.py`. **Tests:**
`tests/test_rules_title.py`, `tests/test_rules_location.py` (or the existing location test file), and any test that
pinned a title now decided differently.

- [ ] Failing tests: `test_non_software_title_with_physical_body_is_off_lane` ("Stationary Engineer" with an HVAC
  and boiler body), `test_non_software_title_with_software_body_stays` ("Nix Engineer" with a python, linux, docker,
  aws body), `test_non_software_title_with_no_body_stays`, `test_thin_body_counts_as_no_body`,
  `test_software_titles_skip_the_body` (Founding Engineer, Forward Deployed Engineer, Member of Technical Staff,
  Research Engineer, Cloud Engineer, Security Engineer with a physical body all stay engineering),
  `test_bare_developer_goes_to_the_body` ("Materials Developer II, Jordan Footwear" with a footwear body is off lane),
  `test_bi_developer_is_off_lane` ("BI Report Developer 1", "Business Intelligence Engineer"),
  `test_data_center_engineer_is_off_lane`, `test_two_letter_country_codes` (GB, VN reads no; US, PR reads yes;
  "Austin, TX" with GB reads yes); implement; run the rules tests and the full suite; commit
  `Rules: only software titles count outright; other titles by their body; country codes (#172)`.

### Task 2: Job list: big when no size, US at big companies

**Files:** `outreach/pipeline/joblist.py`, `web/src/routes/browse/index.tsx` (`sizeLine`), docstring of
`joblist.py`. **Tests:** `tests/test_pipeline_joblist.py`, the web test for the Jobs page row.

- [ ] Failing tests: `test_unknown_size_with_many_us_postings_is_big` (51 open US postings: an uncertain posting is
  in the other tab, the row says big), `test_unknown_size_with_workday_board_is_big`,
  `test_unknown_size_small_board_stays_your_level`, `test_fit_at_big_company_needs_us_yes`,
  `test_unknown_us_at_small_company_stays`, `test_size_filter_uses_big_guess`, `test_order_puts_guessed_big_before_known_big`,
  the merged-company count, and a web test for "Big company, size not known"; implement; run the full suite and the
  web checks (`cd web && pnpm typecheck && pnpm test`); commit `Job list: big when no size, US at big companies (#172)`.

### Task 3: PR

- [ ] Push and open the PR (`Closes #172`). The body gives the simulated counts, the deploy steps (stop the crawl,
  pull, `uv run python -m outreach.install` for the rebuild, tests, web build, kickstart the API, start the crawl), and
  the calls to check. Do not merge.
