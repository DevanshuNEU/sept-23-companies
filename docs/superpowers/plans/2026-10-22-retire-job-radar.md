# Retire Job Radar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** At the switch, after #17 has imported `radar.db`, stop and unload `com.devanshu.jobradar`, keep `radar.db`
as an untouched read-only archive, and prove nothing in the new platform reads it.

**Architecture:** Two small additions to #17's `outreach/deploy/` tool: Job Radar joins the old jobs it can stop,
retire and roll back, and `retire-old` refuses to retire it until a finished `import` run in the store has accounted for
every table in `radar.db`. A scan test keeps every other part of the new platform off `radar.db`. The live steps slot
into #17's runbook (S3, S8, S12) and are marked "Devanshu runs this".

**Tech Stack:** Python 3.14 (Homebrew), standard library only, `unittest`.

**Spec:** issue #5; issue #9 ("Job Radar retires at the switch after its history is imported"); issue #17 Part B;
`docs/superpowers/plans/2026-10-22-import-and-switch-over.md` (this plan runs after its Part A and inside its runbook).

**Branch:** `DevanshuNEU/retire-job-radar`, from master after #17 Part A (Tasks 1 to 16) and #17 PR B1 (Tasks 17 and
18, the deploy tool) have merged. PR B1 merges before the rehearsal, well before switch day, so this branch never waits
for #17's switch PR (B2). Closes #5.

## Global Constraints

- `radar.db` is never written: only `outreach.importer.records.open_radar` (`mode=ro`, `query_only`) opens it, and no
  step changes its bytes, mode or location. Retirement records its sha256; a later `status` compares.
- The plist is renamed to `com.devanshu.jobradar.plist.disabled`, never deleted (the same pattern as the existing
  `com.devanshu.freshjobs.plist.disabled`). Rollback renames it back and bootstraps it.
- Only the live checkout runs `stop`, `retire-old` and `rollback` (`live_checkout.require_live`, as in #17 Task 18).
  Tests use a fake runner, temp folders, a scratch store and a fixture `radar.db` built by #17's
  `tests/fixtures/import/build_radar.py`; they never call `launchctl`, never open the real `radar.db`.
- Job Radar's big-employer adapters (Amazon, Oracle, Eightfold, Apple, Google) are not ported: those companies are over
  the size cap (#5). Nothing here adds an adapter.
- Claude never approves or sends; nothing here touches the mail API on 127.0.0.1:3002 or the new API's writes.
- No em dashes or en dashes in code, comments, docs or commit messages (the dash check in #17's Global Constraints).
- Commit trailer: the one the executing session is given.

## Review Focus

1. **Retiring before the import finished** (the import failed, or ran before the final radar run): `retire-old` refuses
   and names the radar tables with no count in the newest `ok` import run (Task 2
   `test_retire_refuses_until_every_radar_table_is_counted`).
2. **A radar table added after the import code was written:** the retire check reads the table list from the real file,
   so a new table blocks retirement until #17's reader decides it (Task 2 `test_a_new_radar_table_blocks_retirement`).
3. **Something in the new platform quietly reading the archive** (a new source module that "just checks radar"): the
   scan fails the suite (Task 1 `test_only_the_importer_names_radar`).
4. **A radar run still in progress at stop time:** `stop` reports it and the runbook waits for it (Task 2
   `test_stop_reports_a_running_radar_process`).
5. **The archive changing after retirement** (a stray manual run): `status` says `changed since retirement`, never a
   silent pass (Task 2 `test_status_flags_a_changed_archive`).

---

## File Structure

| File | Responsibility |
|---|---|
| `tests/test_radar_retired.py` | Create: the "nothing reads radar.db" scan (Task 1) |
| `outreach/deploy/radar.py` | Create: `radar_tables`, `missing_radar_counts`, `record_retirement`, `archive_state`, `radar_running` |
| `outreach/deploy/jobs.py` | Modify: `com.devanshu.jobradar` joins `OLD_JOBS` with its plist path |
| `outreach/deploy/__main__.py` | Modify: `stop LABEL`; `retire-old` and `status` call `radar.py` for Job Radar |
| `tests/test_deploy.py` | Modify: Task 2 tests |

## Task 1: Nothing in the new platform reads radar.db

**Files:**
- Create: `tests/test_radar_retired.py`

**Interfaces:**
- Produces: `RADAR_ALLOWED = ('outreach/importer/', 'outreach/deploy/radar.py', 'outreach/deploy/jobs.py',
  'outreach/deploy/__main__.py')` (`__main__.py` names `com.devanshu.jobradar` for `stop`; a path
  is allowed when it equals an entry or starts with one ending in `/`) and `scan_for_radar(root: Path) -> list[str]`:
  every file under `outreach/`, `web/src/`, `launchd/` and `.conductor/` (text files only) whose text contains,
  case-insensitive, `radar.db`, `job-search/pipeline` or `jobradar`, minus the allowed paths, as sorted relative paths.
  `refresh/` is the old platform and retires at the switch, so it is not scanned.

- [ ] **Step 1: Write the tests.** `test_only_the_importer_names_radar` (`scan_for_radar(ROOT) == []`) and `test_the_scan_finds_a_planted_reference` (copy `outreach/` into a temp folder, add `outreach/sources/peek.py` containing `open('radar.db')`, the scan returns `['outreach/sources/peek.py']`).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_radar_retired -v`. Expected: FAIL (`scan_for_radar` is not defined yet).
- [ ] **Step 3: Implement** `scan_for_radar` in the test module.
- [ ] **Step 4: Run** the same command. Expected: PASS. If the clean scan names a file, that is a real finding: stop and report it, do not widen `RADAR_ALLOWED`.
- [ ] **Step 5: Commit** `Tests: nothing in the new platform reads radar.db (#5)`.

## Task 2: Stop, retire after the import, archive check, rollback

**Files:**
- Create: `outreach/deploy/radar.py`
- Modify: `outreach/deploy/jobs.py`, `outreach/deploy/__main__.py`
- Test: `tests/test_deploy.py`

**Interfaces:**
- Consumes: `open_radar` (#17 Task 2); `report_from_json` (#17 Task 12); `connect`, `append_event`, `utcnow` (`outreach.store`); `Job`, `OLD_JOBS`, `Runner`, `main` (#17 Tasks 17, 18).
- Produces:
  - `OLD_JOBS` gains `Job('com.devanshu.jobradar', template=None, plist='com.devanshu.jobradar.plist', owner='#5',
    required=False)`; `RADAR_DB = Path('~/Desktop/DevVault/job-search/pipeline/radar.db').expanduser()`.
  - `radar_tables(path: Path) -> list[str]`: table names from `sqlite_master` through `open_radar`.
  - `missing_radar_counts(conn, path: Path) -> list[str]`: the tables of `radar_tables(path)` that are not keys of
    `radar_tables` in the summary of the newest `import` `job_run` with `status 'ok'` (read with
    `report_from_json`). No `ok` import run gives every table.
  - `record_retirement(conn, path: Path, *, now: str) -> str`: sha256 of the file, appended as one event (`entity
    'source'`, `kind 'radar_retired'`, `actor 'system'`, detail `{path, sha256, size, mtime}`,
    `idempotency_key 'radar-retired:<sha256>'`); returns the hash.
  - `archive_state(conn, path: Path) -> Literal['unchanged', 'changed', 'not_retired', 'missing']`.
  - `radar_running(ps_output: str) -> bool`: true when a line runs `job-search/pipeline/discover.py`
    (the command reads `ps -axo command`; the parser is pure for tests).
  - CLI: `stop LABEL` (`require_live`; only labels in `OLD_JOBS`; `bootout` when loaded; for Job Radar also prints
    `Job Radar is still running a poll; wait and run stop again` when `radar_running` is true, exit 1). `retire-old`
    retires Job Radar last and only when `missing_radar_counts` is empty (else exit 1 naming the tables, the other old
    jobs still retired); then `record_retirement` and prints `radar.db kept as archive, sha256 <hex>`. `status` adds
    `radar.db: <archive_state>`. `rollback` restores Job Radar with the other old jobs.

- [ ] **Step 1: Write the failing tests** (fake runner, temp `agents_dir`, temp live root, `fresh_store()`, a fixture radar.db):
  - `test_retire_refuses_until_every_radar_table_is_counted` (no import run: exit 1, every table named, plist not renamed; after a fixture `apply_plan` run: retired, plist `.disabled`).
  - `test_a_new_radar_table_blocks_retirement` (add `CREATE TABLE extra(x)` to the fixture copy after the import: exit 1 naming `extra`).
  - `test_stop_boots_out_without_renaming` and `test_stop_refuses_a_label_not_in_old_jobs`.
  - `test_stop_reports_a_running_radar_process` (`radar_running` on a fixed `ps` sample with and without the discover line).
  - `test_retirement_records_the_archive_hash_once` (a second retire adds no event).
  - `test_status_flags_a_changed_archive` (append a byte to the fixture copy: `changed`).
  - `test_retire_never_opens_radar_for_writing` (fixture digest unchanged after stop, retire and status).
  - `test_rollback_restores_and_bootstraps_jobradar`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_deploy -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite and `uv run python tests/mutation_check.py`. Expected: all OK, `missed 0`.
- [ ] **Step 5: Commit** `Deploy: Job Radar stops before the import and retires only after it (#5)`.
- [ ] **Step 6: Lessons check (no code).** In the PR body, list the three lessons #5 keeps and the #21 tests that pin
  each: persist everything and filter on read (#21 Tasks 8 and 9: every listed posting is stored and decided by the
  rules); a 304 means unchanged, never empty (`test_second_read_with_304_is_unchanged_and_touches_no_posting`,
  `test_unchanged_clears_like_ok`); closed only on a full successful read (`test_gone_posting_closes_after_ok_only`,
  `test_error_never_closes`, `test_a_failed_page_is_an_error_not_a_partial_ok`). If one is missing on master,
  comment on #21 instead of adding it here. Open the PR ("Closes #5").

## Runbook steps (inside #17's runbook). Devanshu runs these, in the live checkout.

1. **At #17 S3 (before the final import).** `uv run python -m outreach.deploy stop com.devanshu.jobradar`
   Check: it prints `com.devanshu.jobradar: stopped`; `launchctl print gui/$(id -u)/com.devanshu.jobradar` says it
   could not find the service; `tail -1 ~/Desktop/DevVault/job-search/pipeline/radar.log` shows a time before the stop.
2. **At #17 S8 (the import).** Check: the import report's radar block lists every table that
   `sqlite3 -readonly ~/Desktop/DevVault/job-search/pipeline/radar.db .tables` prints, each with a row count.
3. **At #17 S12 (retire the old jobs).** `uv run python -m outreach.deploy retire-old`
   Check: it prints `radar.db kept as archive, sha256 <hex>`; `ls ~/Library/LaunchAgents | grep jobradar` shows only
   `com.devanshu.jobradar.plist.disabled`; `uv run python -m outreach.deploy status` shows `radar.db: unchanged`.
4. **A week later.** `uv run python -m outreach.deploy status`
   Check: `radar.db: unchanged`. Then close #5.

**Rollback:** #17's `uv run python -m outreach.deploy rollback` renames the plist back and bootstraps Job Radar; it
starts writing `radar.db` again, and `status` then says `changed`, which is expected after a rollback.

## Done when (issue #5)

- The job is unloaded (runbook step 3 check).
- The import count report includes every radar table (runbook step 2 check; enforced by `missing_radar_counts`).
- Nothing in the new platform reads radar.db (Task 1 test, green on master).

## Out of scope

- Editing Job Radar's own files (`RADAR.md`, `launchd/README.md`, `targets.yml`) in DevVault: they belong to that
  vault, which has its own rules (Pending Devanshu Q26).
- `refresh/discover_radar.py`, which reads `radar.db`: it is part of the old refresh, which #17 S12 retires.
- `~/Desktop/devboard`, which used Job Radar's data: not part of this system; its applied records are imported by #17.

## Pending Devanshu

Question numbers are the integration review's (section 10). Each line names the default this plan builds and where it
lives.

- **Q16. radar.db on disk.** Builds: not made read-only (`chmod a-w` is not run). Nothing loads it after step 3, the
  archive hash (`record_retirement`, `archive_state`) catches any change, and a read-only mode would make a rollback
  fail on its first poll. Where: Global Constraints, Task 2.
- **Q26. Retired note in DevVault's `RADAR.md` and `launchd/README.md`.** Builds: nothing in DevVault is edited; he
  adds the one-line "retired on <date>, history in the outreach store" note by hand after step 3. Where: Out of
  scope.

## Cross-plan effects

- **#17:** this plan adds one row to `OLD_JOBS`, a `stop` command, and a guard in `retire-old`, on top of #17 PR B1;
  #17's runbook S3 and S12 call these steps, and S4 backs up `radar.db` with `sqlite3 -readonly`. It consumes `open_radar` and `report_from_json` as #17 names them, and the run summary key `radar_tables` (#17 Task 12).
- **#21:** owns the three lessons; this plan only checks they are pinned by tests.

## Self-review

- **Issue coverage.** Unload after import (Task 2 guard, runbook 3), rename to `.disabled` (Task 2), `radar.db`
  untouched as archive (Global Constraints, `record_retirement`, `archive_state`), adapters not ported (Global
  Constraints), lessons (Task 2 step 6), Done when (three lines, each with its check).
- **Names.** `scan_for_radar`, `RADAR_ALLOWED`, `radar_tables`, `missing_radar_counts`, `record_retirement`,
  `archive_state`, `radar_running` are defined once here; #17's names are used as that plan defines them.
- **Proportion.** Two tasks and four runbook steps for a two-line issue plus its Done when.
