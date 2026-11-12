# Jobs Shows Software Roles Only: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the Jobs list shows only the software roles #9 allows; everything else moves to an "Other roles" tab; postings placed in a non-US region drop off.

**Architecture:** a new pure rule, `role_of(title, cfg) -> "software" | "other"`, reads a new `[role]` section of `outreach/rules/config.toml`. `decide()` stores it on every decision (migration 0014 adds `decision.role`), so it is part of the ruleset version and the existing rebuild re-decides every open posting once. The job list splits its listed rows into three groups (your level, higher levels, other roles) by `role` and the existing level rule. The lane, ranking, the board gate and every other reader of decisions are unchanged.

**Tech Stack:** Python 3.14 standard library, SQLite, FastAPI and pydantic (API models), React + Vite + vitest + Playwright (web).

**Spec:** GitHub issue #9, "Job board ruling (2026-11-12, evening)", points (1) and (2); issue #250.

## Global Constraints

- In (#9): software engineer or developer (SWE, SDE), backend, frontend, full stack, AI engineer (AI, applied AI, LLM), cloud engineer, Forward Deployed Engineer, Product Engineer, Founding Engineer, Member of Technical Staff, Java, Python, TypeScript, JavaScript and Go developers, Web Developer. A software title with a specialty stays in ("Software Engineer, Machine Learning").
- Out (#9): ML engineer, data engineer, data scientist, data analyst, mobile, DevOps, SRE, platform, security, Design Engineer, solutions, sales, professional services, implementation and support engineers, database, Salesforce, ServiceNow, SAP, COBOL and mainframe, SDET and QA.
- Every word list lives in `config.toml` (`decide.py`'s rule); code only combines what the lists match. Match through `phrases()`.
- `decide()` stays pure: no store, clock or network.
- UI copy: plain words, no em or en dashes. Tab label is exactly "Other roles".
- Tests use the scratch settings in `tests/__init__.py`; nothing opens the live store.

## Review Focus

- A title with both an in word and an always-out word ("Software Engineer in Test", "Software Engineer, iOS"): expect `other`. Pinned in Task 1's labeled set.
- A decision row written before 0014 (`role` NULL) while the rebuild has not run: expect it treated as `software`, so the list never goes empty between the migration and the rebuild. Pinned in Task 4.
- Hyphen and spacing variants ("Full-Stack", "Back End", "Front-end", "AI/ML Engineer"): expect `software`. Pinned in Task 1.
- "Other roles" paging: the cursor of one tab must not page another. Pinned in Task 4.
- Check now's "N new" count must count software roles only, so the header and the tabs agree. Pinned in Task 4.

---

### Task 1: The role rule and its labeled set

**Files:**
- Create: `outreach/rules/role.py`
- Modify: `outreach/rules/config.toml` (new `[role]` section), `outreach/rules/config.py` (`REQUIRED["role"]`, `Config.role`), `outreach/rules/version.py` (`RULE_FILES` gains `role.py`)
- Create: `tests/fixtures/role_titles.tsv` (two columns, `title<TAB>software|other`, a `#` line is a comment)
- Create: `tests/test_rules_role.py`
- Modify: `tests/test_rules_config.py` (`test_rule_file_list_is_the_decided_one` tuple)

**Interfaces:**
- Produces: `role_of(title: str, cfg: Config) -> str`, returning `"software"` or `"other"`; constant `ROLES = ("software", "other")` in `role.py`.

`[role]` keys and precedence, first match wins:
1. `out_always` matched: `other`. Holds: in test, sdet, qa, quality assurance, quality engineer, test engineer, test automation, ios, android, mobile, embedded, firmware, hardware, fpga, asic, salesforce, servicenow, sap, pega, cobol, mainframe, data scientist, data analyst, site reliability, sre, devops, devsecops, mlops, design engineer, professional services, support engineer, technical support, sales engineer, solutions engineer, solution engineer, solutions architect, implementation, customer success, database administrator, dba.
2. `software_core` matched: `software`. Holds: software, swe, sde, full stack, fullstack, backend, back end, frontend, front end.
3. `ai` matched: `software`. Holds: ai engineer, ai engineers, ai/ml engineer, applied ai, llm, genai, generative ai, ai developer.
4. `out_unless_core` matched: `other`. Holds: data engineer, data engineering, machine learning, ml engineer, security, platform, infrastructure, database, network.
5. `allowed` matched: `software`. Holds: cloud engineer, cloud developer, forward deployed, fde, product engineer, founding engineer, member of technical staff, mts, java developer, java engineer, python developer, python engineer, typescript developer, typescript engineer, javascript developer, javascript engineer, node developer, react developer, react engineer, go developer, go engineer, golang developer, golang engineer, web developer, web engineer.
6. Otherwise `other`.

- [ ] **Step 1: Write the labeled set.** At least 400 lines from the live job list's distinct titles (read-only `GET /api/jobs`, both tabs, 30 days; saved in `.context/titles/live_titles.json`), each labeled by #9's in and out lists, plus these seven from the 2026-11-12 screenshot: `Professional Services Engineer	other`, `COBOL Developer (All Levels & Internal Paths Available)	other`, `Junior Design Engineer	other`, `Junior Forward Deployed Engineer	software`, `Associate Database Engineer	other`, `Data Scientist I	other`, `Design Engineer	other`. Include the variants `Full-Stack Engineer`, `Front-end Developer`, `AI/ML Engineer`, `Software Engineer, Machine Learning` (software) and `Machine Learning Engineer`, `Software Engineer in Test`, `Software Engineer, iOS`, `Cloud Security Engineer` (other).
- [ ] **Step 2: Write `tests/test_rules_role.py`.** `test_labeled_titles` reads the TSV and, for every line, asserts `role_of(title, default_config()) == label`, collecting every miss into one failure message (title, expected, got). `test_role_section_is_required` asserts `load_config` raises `KeyError` naming `[role]` on a config without it. `test_empty_title_is_other`.
- [ ] **Step 3: Run** `uv run python -m unittest tests.test_rules_role -v`. Expected: FAIL, `role` module missing.
- [ ] **Step 4: Implement** the `[role]` section, `REQUIRED["role"] = ("out_always", "software_core", "ai", "out_unless_core", "allowed")`, `Config.role: dict`, and `role_of` with the precedence above (each list compiled once per config, `functools.lru_cache` as `title.py` does). Add `role.py` to `RULE_FILES` and to the pinned tuple in `tests/test_rules_config.py`.
- [ ] **Step 5: Run** `uv run python -m unittest tests.test_rules_role tests.test_rules_config -v`. Expected: PASS. A labeled title that fails is fixed in `config.toml`, never by editing the label to match, unless #9's lists say the label is wrong.
- [ ] **Step 6: Commit** `Add the software role rule and its labeled titles (#250)`.

### Task 2: Non-US regions

**Files:**
- Modify: `outreach/rules/config.toml` (`[location] non_us_countries` gains latin america, south america, central america, caribbean, middle east, asia pacific, nordics)
- Test: `tests/test_rules_location.py`

- [ ] **Step 1: Write** `test_non_us_regions`: `us_status(loc, None, cfg)` is `"no"` for `"Latin America"`, `"South America"`, `"Remote - Latin America"`, `"Central America"`; and stays `"yes"` for `"Remote - US or Latin America"`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_rules_location -v`. Expected: the new test FAILS (`unknown`).
- [ ] **Step 3: Add the regions** to `non_us_countries`.
- [ ] **Step 4: Run** the location and decide tests. Expected: PASS.
- [ ] **Step 5: Commit** `Read Latin America and other non-US regions as outside the US (#250)`.

### Task 3: `role` on every decision

**Files:**
- Modify: `outreach/rules/decide.py` (`Decision.role: str = "software"`, last field with a default; `as_row()` gains `role`; `decide()` sets `role=role_of(title, cfg)`)
- Create: `outreach/store/migrations/0014_decision_role.sql`: `ALTER TABLE decision ADD COLUMN role TEXT CHECK (role IN ('software','other'))`, then drop and recreate `current_decision` and `posting_decision` exactly as in `0007_level_state.sql`, with `role` added to `posting_decision`'s column list.
- Modify: `outreach/rules/rebuild.py` (`_COLUMNS` gains `role`; `stored_decision` reads a NULL `role` as `"software"`), `outreach/ranking/roles.py:146-155` (`_DECISION_COLUMNS` gains `role`)
- Modify test seeders with fixed columns: `tests/ranking_store.py`, `tests/apiseed.py`, `tests/test_crawl_decisions.py`, `tests/test_rules_decide.py`
- Create: `tests/test_decision_role_migration.py` (pattern: `tests/test_amazon_board_migration.py`)

- [ ] **Step 1: Write tests.** In `tests/test_rules_decide.py`: `decide(PostingInput(title="Data Scientist I")).role == "other"` and `decide(PostingInput(title="Software Engineer I")).role == "software"`. The migration test: on a store migrated to 0013 with one decision row, migrating to 0014 keeps the row, `role` is NULL, `posting_decision` exposes `role`, and `user_version` is 14. A rebuild test: after `rebuild()`, the row's `role` is set.
- [ ] **Step 2: Run** them. Expected: FAIL (no field, no migration).
- [ ] **Step 3: Implement** the field, the migration and the column lists.
- [ ] **Step 4: Run the whole Python suite** `uv run python -m unittest discover -s tests -t .`. Expected: PASS. Any other test that pins the decision columns or the schema version is updated to 14 in this task.
- [ ] **Step 5: Commit** `Store each posting's role with its decision (migration 0014, #250)`.

### Task 4: Three groups in the job list

**Files:**
- Modify: `outreach/pipeline/joblist.py`: `_VIEWS = ("your_level", "higher", "other")`; `JobFilters.view` literal gains `"other"`; `cand` selects `COALESCE(d.role, 'software') AS role`; `rows` gets `grp` = `'other'` when role is `other`, else `'your_level'` when the existing `yl` rule holds, else `'higher'`; the page filters `WHERE grp = :grp`; the counts become three window sums; `query()` returns `counts = {"your_level", "higher", "other"}`. New constant `SOFTWARE_SQL = "COALESCE(d.role, 'software') = 'software'"`.
- Modify: `outreach/crawl/check_all.py:108` (Check now's new count: `LISTED_SQL AND SOFTWARE_SQL`), `outreach/pipeline/alerts.py` only if it reads counts by key
- Modify: `outreach/api/models/jobs.py` (`JobView` gains `"other"`, `JobCounts.other: int`), `outreach/api/routers/jobs.py` docstring
- Test: `tests/test_pipeline_joblist.py`, `tests/test_api_jobs.py`, the check-now count test next to `check_all`

- [ ] **Step 1: Write tests.** Seed one fit software posting, one fit `other` posting, one higher-level software posting and one decision row with `role` NULL. Assert: `your_level` lists the fit software row and the NULL-role row; `higher` lists the higher one; `other` lists only the `other` row; `counts == {"your_level": 2, "higher": 1, "other": 1}` on every view, including an empty page; a cursor from `your_level` used with `view="other"` never returns a `your_level` row; Check now's new count does not count the `other` posting. API: `GET /api/jobs?view=other` returns 200 with `counts.other`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_pipeline_joblist tests.test_api_jobs -v`. Expected: FAIL.
- [ ] **Step 3: Implement** as listed. Keep the query plan test (`explain`) passing; no new full scan.
- [ ] **Step 4: Run** the whole Python suite. Expected: PASS.
- [ ] **Step 5: Commit** `Jobs lists software roles only, with an Other roles group (#250)`.

### Task 5: The "Other roles" tab

Run the craft skill on this change before writing UI code: one more tab in an existing tab row, same counts style.

**Files:**
- Modify: `web/src/api/types.ts` (`JobsTab` gains `"other"`), regenerate `web/src/api/schema.ts` with `pnpm gen:api`, `web/src/api/schema.parity.ts`, `web/src/api/fixture/jobs-data.ts` (an `other` row and `counts.other`)
- Modify: `web/src/routes/browse/index.tsx` (third `TabsTrigger` "Other roles" with its count; the empty state's "Show N" button covers the new tab)
- Test: `web/src/routes/browse/browse.test.tsx`, `web/src/api/http/jobs.test.ts`, `web/e2e/browse.spec.ts`

- [ ] **Step 1: Write tests.** The browse test renders three tabs with labels "Your level", "Higher levels", "Other roles" and their counts from the fixture; clicking "Other roles" requests `view=other` and shows the fixture's other row. The e2e test clicks the tab and sees the row.
- [ ] **Step 2: Run** `cd web && pnpm test -- browse jobs`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `cd web && pnpm typecheck && pnpm lint && pnpm test && WEB_PORT=$CONDUCTOR_PORT pnpm e2e`. Expected: PASS.
- [ ] **Step 5: Commit** `Add the Other roles tab to Jobs (#250)`.

### Task 6: Ship

- [ ] **Step 1:** Whole Python suite and web checks green. Push, open the PR against `master` (body: "Closes #250", what changed, the deploy steps), and check `gh pr view <n> --json baseRefName,mergeStateStatus` shows `master` and `CLEAN`.
- [ ] **Step 2: Deploy note in the PR body**, in this order (migration first, as the deploy notes require): in the live checkout `git pull --ff-only https://github.com/DevanshuNEU/sept-23-companies.git master`, `uv sync`, `uv run python -m outreach.install` (applies 0014 and runs the rebuild once), `cd web && pnpm install --frozen-lockfile && pnpm build`, kickstart `com.devanshu.outreach-api`, then the test run.
