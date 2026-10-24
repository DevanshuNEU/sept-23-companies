# Import all history and run the switch-over Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring every company, person, touch, reply, referral, application and interview Devanshu has on record into
the new store with a dry run that counts and lists conflicts first, then switch the live system to the new platform in
one reversible Sunday move.

**Architecture:** Part A is a standard-library package `outreach/importer/`: one reader per source turns files into
verbatim `SourceRecord`s, a pure planner links them into one `ImportPlan` (companies, people, touches, outcomes,
applications, boards, postings, issues) and decides an outcome for every record, and a writer applies the plan through
the foundation's write helpers and #31's identity, recording every record in `import_record`. The dry run is the
planner plus the count report, with no writes. Part B is a small `outreach/deploy/` tool (render the launchd
templates, retire the old jobs, roll back) and the runbook below; every live step is marked "Devanshu runs this" and
has a check he can read.

**Tech Stack:** Python 3.14 (Homebrew), standard library only (`sqlite3`, `json`, `csv`, `tomllib`, `zoneinfo`,
`plistlib`, `subprocess`), Node 20.19.5 only to evaluate `dashboard/data.js` in a sandbox, `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-20-opportunity-pipeline-design.md` (sections 3, 5.1 item 3, 5.4, 11, 16),
issue #17 (and its 2026-10-21 comment), issue #9 (Build, Switch, Changes 2026-10-21), and the #17 notes in
`docs/superpowers/plans/2026-10-19-store-and-rules.md` ("For downstream issues", #17 Import).

**Branches and PRs:** three PRs, each "Part of #17". **Part A** `DevanshuNEU/import-history` (Tasks 1 to 16).
**PR B1** `DevanshuNEU/deploy-tool` (Tasks 17 and 18, the deploy tool): it merges before the rehearsal (R1), because
the runbook uses `outreach.deploy` at S3 and S11 and #5 branches from it. **PR B2** `DevanshuNEU/switch-over` (Task 19:
CLAUDE.md, docs and the cold-email skill rewrite that #13 Task 16 writes; docs only): it is the one that merges on
switch day, at S5, and it closes #17 after the Monday check (S14).

**Depends on:** #30 (merged). Part A also needs #31 Tasks 7, 8 and 10 (`outreach/identity.py`,
`outreach/sources/jobs.py::register_source`, `outreach/sources/boards.py`) and #11 Task 9
(`outreach/outcomes/context.py::build_context`) and #14 Task 2 (`outreach/sending/config.py::load_config`,
`outreach/sending/calendar.py::add_business_days`) and #14 Task 1 (migration 0002 on master, since this plan's 0003
follows it) and #16 PR 1 (`outreach.review.raise_item`, Task 11); a builder who finds them missing stops and says so,
and never writes a local copy. PR B1 needs nothing beyond master (templates that do not exist yet are skipped by
name). The switch (PR B2 and the runbook) needs #11 (including Task 19's release-hold action), #12 (with #33), #13,
#14, #16 and #21 merged, per the issue, and #31 Task 16's `launchd/com.devanshu.outreach-sources.plist.template` (B8 of the
integration review): the switch must not retire the old discovery without its replacement.

## Global Constraints

- Claude never approves or sends an email. Approval is Devanshu's click, sending is the sender's job. Nothing here calls the mail API on 127.0.0.1:3002 (`/api/approve`, `/api/save`, `/api/skip`, `/api/unapprove`, `/api/pause`) or the new API's write routes, by any means. An email approved in the old mailer is imported as a draft that he approves again.
- Never open the live store `~/Library/Application Support/outreach/outreach.db` outside the live checkout. Every store is opened with `outreach.store.connect()` (its guard applies). Tests use `tests/storetest.fresh_store()` and the scratch `OUTREACH_DB`, `OUTREACH_MAIL_HOME`, `OUTREACH_GMAIL_DIR` that `tests/__init__.py` sets. Gmail secrets are never read or printed: nothing here opens `~/Library/Application Support/outreach-mail/`.
- Sources are read, never written. `radar.db` is opened with `file:<path>?mode=ro` only. A test hashes every fixture source before and after a run and fails on any change.
- Tests use synthetic fixtures under `tests/fixtures/import/` only (made-up companies and people, as in `tests/fixtures/pipeline/`). No test, fixture, plan step or commit carries a record from his real history. Tests never run a real refresh, a real send or a crawl, and never call `launchctl` or `plutil` for real.
- Standard library only in `outreach/importer/` and `outreach/deploy/` (the #11 boundary scan covers them).
- The import never writes the `sender` row (so `sender.history_id` stays `''` and #14's first tick reads recent threads) and never writes `send_day` (the new ramp starts at 20, #9).
- Writers use `INSERT ... ON CONFLICT DO NOTHING` (or `DO UPDATE` where a row must change), never `INSERT OR IGNORE` or `INSERT OR REPLACE`. Timestamps are canonical UTC (`iso_utc`); dates are `date(x)`.
- Nothing is hidden on import. An ambiguous record is imported and raises a review item; a record that cannot be placed at all is `held`, and a held record blocks the real run. A skip is allowed only with a reason from `EXPLAINED_SKIPS` (Task 12). Error is not empty: a missing or unreadable required source fails the run with its name, never reads as zero records.
- The only new hides the import writes are the ones Devanshu already ruled (2026-10-19 in #9): the old dashboard's 41 cleared defence contractors and 1 agency become `no_go` rows; "well over 500 people" becomes size evidence. Every other old verdict is evidence plus a review item, shown.
- Migration `0003_import_history.sql` (version 3) is this plan's only schema change (Task 1); #14's `0002_sending.sql` is version 2 and lands first. `0001_initial.sql` is frozen: #31's in-place `company_name_key` edit (with its strong column) is the last in-place edit of 0001; every later schema change is a numbered migration, renumbered on rebase.
- Leave `dashboard/data.js`, `dashboard/index.html` and `dashboard/app.js` untouched: the old dashboard's existing "Download a backup of your tracker" button (`dashboard/app.js:546`, export at `:712`) already exports every tracker field as `outreach.v1`. Settled (integration review A1): only #9's Build line, which asks for an "Export tracking" button, is edited to say so.
- Light load: an import of the real data is one process; no parallel runs, no stress tests on his Mac.
- Never use ports 3001, 3002, 3011, 3013 or 5173 from a workspace.
- Rollback is `git revert` plus re-enabling the old launchd jobs (Task 18 `rollback`); never `git reset`, never deleting the new store.
- No em dashes or en dashes in code, comments, fixtures, docs or commit messages. Check: `python3 -c "import sys; [print(p) for p in sys.argv[1:] if any(c in open(p,encoding='utf-8').read() for c in chr(0x2013)+chr(0x2014))]" <files>`.
- Commit trailer: the one the executing session is given.

## Review Focus

1. **Two sources describe one touch with different spellings** ("Acme AI" in one file, "Acme" in another; a first name only in `contacted.txt`, the full name in the company file): the dry run shows one touch and a `near_name` or `same_person` line, never two silent touches or a silent merge (Tasks 8 and 9: `test_near_names_raise_one_merge_item_and_stay_two_companies`, `test_first_name_joins_the_company_file_person_once`).
2. **A send whose outcome the old mailer never settled** (`sending` or `send_unknown`) or a `send.log` "sent" line with no contact: the real run refuses with the record named, so a sent email can never vanish or be sent again (Task 10 `test_unclear_send_is_held_and_blocks_the_run`, Task 12 `test_held_records_fail_the_check`).
3. **Re-running the import after a source changed** (a later tracker backup with a new stage): only the new facts are added, a disagreement raises `outcome_conflict`, nothing stored is overwritten (Task 11 `test_changed_backup_adds_the_new_outcome_and_overwrites_nothing`).
4. **Imported wins arriving all at once on switch day**: they are in the win log but never announced as new (Task 11 `test_imported_wins_are_marked_seen`), and a months-old email touch never gets an automatic follow-up from the new sender (Task 10 `test_historic_email_touch_is_closed_and_has_no_message`).
5. **An interview with no recorded way in** (a brief with no touch, no application): it is still imported on a touch, with a review item asking where it came from, and the company joins the done set (Task 9 `test_interview_without_touch_gets_an_other_touch_and_an_item`).

---

## Sources and their shape

The importer reads ten sources. Paths come from `outreach/importer/sources.toml` (committed, real default paths) or a
manifest the rehearsal writes. `import_record.source` values after migration 0003 are in the first column.

| `source` | File (default path) | One record is | Notes |
|---|---|---|---|
| `data_js` | `<live checkout>/dashboard/data.js` | one `COMPANIES` entry (key `company:<id>`), one `RULED_OUT` entry (`ruled_out:<n>`), `META` and `REFERENCE` (`meta`, `reference`) | JavaScript, evaluated by Node in a `vm` sandbox with only `window` defined (Task 5) |
| `local_storage` | newest `outreach-backup-*.json` in `~/Desktop/outreach-scratch/import/` | one company id entry `{stage, contact, channel, reached, repliedOn, next, notes, skipped?}` (`company:<id>`) | the old dashboard's backup export |
| `contacts_json` | `$OUTREACH_MAIL_HOME/contacts.json`, else `<live checkout>/mail/contacts.json` | one contact (`contact:<id>`); `firstSendDate` (`first_send_date`) | statuses and fields as in `mail/store.py` and `mail/sender.py` |
| `send_log` | same folder, `send.log` | one line (`line:<n>`) | used to reconcile sends |
| `radar_db` | `~/Desktop/DevVault/job-search/pipeline/radar.db` | a row of `board`, `posting`, `app_event` (`board:<id>`, `posting:<id>`, `app_event:<id>`); every other table is one table record (`table:<name>`) | schema in that folder's `store.py` (migrations 1 to 5) |
| `devboard` | `~/Desktop/devboard/job-state.json` | one key (`job:<key>`) with `{company, title, url, posted, status, updated}` | keys `ats:token:id`, Workday `workday:tenant:wdN:site:id`, plus `hcafe` and `apify` keys |
| `contacted_txt` | `~/Desktop/DevVault/job-search/outreach/contacted.txt` | one line (`line:<n>`): `YYYY-MM-DD - <company> - <rest>` | comment lines start with `#`; a few lines have a dash inside the company or rest |
| `devvault_notes` | `~/Desktop/DevVault/job-search/outreach/companies/**/*.md` | one file (`file:<relative path>`) | YAML-like frontmatter: `company`, `domain`, `status`, `stage`, `location`, `people:` list with `name, title, role, email, email_confidence, linkedin, sent, reply, reply_date, touch_count, last_sent, employer_verified` |
| `interview_prep` | `~/Desktop/DevVault/job-search/interview-prep/companies/*/` | one folder (`folder:<slug>`) | `brief.md` frontmatter `company, role, round, interviewer, type, source, verified_on` when present; `sessions/YYYY-MM-DD-N.md` names give dates; some briefs have no frontmatter |
| `curated_touches` | `~/Desktop/outreach-scratch/recon/curated_touches.csv` | one row (`row:<n>`): `date, company, band, role, channel, outcome, src, fu, req` | annotations on touches from the three DevVault sources (outcome `none, ooo, bounce, no, soft, routed, call`) |

Excluded on purpose: `~/Desktop/DevVault/tool-crowding/outreach/contacted.txt` (another project, per the issue),
`mail/config.json` (old mailer settings; #9 sets the new ramp).

## File Structure

| File | Responsibility |
|---|---|
| `outreach/store/migrations/0003_import_history.sql` | Create: rebuild `import_record` with the new sources and the `held` outcome (Task 1) |
| `outreach/importer/__init__.py` | Create: `IMPORTER_VERSION`, `__all__` |
| `outreach/importer/records.py` | Create: `SourceRecord`, `SourceError`, source hashing, the read-only radar open |
| `outreach/importer/manifest.py`, `outreach/importer/sources.toml` | Create: source paths and the overrides file |
| `outreach/importer/read_text.py` | Create: `contacted_txt`, `curated_touches`, `devboard`, `local_storage`, `contacts_json`, `send_log` readers |
| `outreach/importer/read_notes.py` | Create: the frontmatter parser and the `devvault_notes`, `interview_prep` readers |
| `outreach/importer/read_data_js.py`, `outreach/importer/data_js_dump.mjs` | Create: `data_js` reader through Node |
| `outreach/importer/read_radar.py` | Create: `radar_db` reader, per-row and per-table records |
| `outreach/importer/vocab.py` | Create: every value mapping (stages, reply words, roles, statuses, channels) |
| `outreach/importer/model.py` | Create: the plan dataclasses |
| `outreach/importer/link.py` | Create: company and person linking, touch identity, the curated join, the interview origin rule |
| `outreach/importer/plan.py` | Create: `build_plan` |
| `outreach/importer/write.py` | Create: `apply_plan` |
| `outreach/importer/report.py` | Create: the count report, `EXPLAINED_SKIPS`, `check_report`, `render_report` |
| `outreach/importer/rehearse.py` | Create: copies to a scratch folder and a full scratch run |
| `outreach/importer/__main__.py` | Create: the CLI |
| `outreach/deploy/__init__.py`, `jobs.py`, `launchctl.py`, `__main__.py` | Create: job tables, the injectable launchctl and plutil runner, install, retire, status, rollback |
| `tests/fixtures/import/` | Create: synthetic sources (see Task 2) and `build_radar.py` (builds a fixture radar.db from Job Radar's schema) |
| `tests/importtest.py` | Create: `fixture_manifest(tmp) -> Manifest`, `all_marks(conn) -> dict[str, tuple[int, int]]` |
| `tests/test_import_*.py`, `tests/test_deploy.py` | Create: one file per task |
| `tests/pipeline/stages_importer.py`, `tests/pipeline/harness.py`, `tests/pipeline/test_flow.py` | Create and modify: the `import_history` stage (Task 15) |
| `tests/mutation_check.py` | Modify: importer mutations (Task 16) |
| `CLAUDE.md`, `docs/README.md`, `.claude/skills/cold-email/SKILL.md` | Modify: in PR B2 only (Task 19; the skill text is #13 Task 16's) |

---

## Task 1: Migration 0003 for the import record

**Files:**
- Create: `outreach/store/migrations/0003_import_history.sql`
- Test: `tests/test_store_schema.py` (add), `tests/test_store_migrate.py` (add)

**Interfaces:**
- Produces: `import_record` rebuilt with the same columns and:
  - `source` CHECK adds `'interview_prep'`, `'curated_touches'`, `'send_log'` to the seven existing values.
  - `outcome` CHECK is `('imported','merged','skipped','held')`; `CHECK (outcome = 'imported' OR length(reason) > 0)`;
    `CHECK ((outcome IN ('skipped','held')) = (entity_id IS NULL))`.
  - `UNIQUE (source, source_key, raw_sha256)` kept; the two append-only triggers recreated; a new index
    `import_record_run ON import_record(run_id, source, outcome)`.
  - The file is the SQLite table rebuild: `CREATE TABLE import_record_new`, `INSERT INTO import_record_new SELECT * FROM
    import_record`, `DROP TABLE import_record`, `ALTER TABLE import_record_new RENAME TO import_record`, triggers, index.
    (`DROP TABLE` does not fire the delete trigger; checked on SQLite 3.51.2.)

- [ ] **Step 1: Write the failing tests.**
  - `test_import_record_accepts_the_new_sources` (`interview_prep`, `curated_touches`, `send_log` each insert).
  - `test_import_record_held_has_no_entity_and_a_reason` (`held` with `entity_id` set raises; with no reason raises; with a reason and no entity inserts).
  - `test_import_record_is_still_append_only_after_0003` (UPDATE and DELETE raise).
  - `test_migration_0003_keeps_existing_import_records`: build a store at version 2 (`migrate` with a directory holding only 0001 and 0002), insert two `import_record` rows, migrate a version 2 store to 3, both rows are there unchanged. Assertions that do not depend on the number use `latest_version()` (`outreach.store.migrate`), so a later migration does not break them.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_store_schema tests.test_store_migrate -v`. Expected: the new tests FAIL (CHECK, no 0003).
- [ ] **Step 3: Write the migration.**
- [ ] **Step 4: Run** the full suite `uv run python -m unittest discover -s tests -t .` and `uv run python tests/mutation_check.py`. Expected: all OK, `missed 0`.
- [ ] **Step 5: Commit** `Store: migration 0003, import records gain three sources and the held outcome (#17)`.

## Task 2: Records, manifest, overrides and the fixture set

**Files:**
- Create: `outreach/importer/__init__.py`, `records.py`, `manifest.py`, `sources.toml`, `tests/importtest.py`, `tests/fixtures/import/**`
- Test: `tests/test_import_records.py`

**Interfaces:**
- Produces:
  - `IMPORTER_VERSION = "import-1"`; `SOURCES = ('data_js', 'local_storage', 'contacts_json', 'send_log', 'radar_db', 'devboard', 'contacted_txt', 'devvault_notes', 'interview_prep', 'curated_touches')`.
  - `@dataclass(frozen=True) class SourceRecord: source: str; source_key: str; raw: object` with `raw_json() -> str`
    (`json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(',', ':'))`) and `raw_sha256() -> str`;
    `__post_init__` refuses a source outside `SOURCES` or an empty key.
  - `class SourceError(Exception)`: names the source and the path ("contacted_txt: no file at ..."), raised for a
    missing required source or an unreadable file.
  - `source_digest(path: Path) -> str`: sha256 of a file, or of every file under a folder in sorted relative-path order.
  - `open_radar(path: Path) -> sqlite3.Connection`: `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` with
    `PRAGMA query_only = ON`; raises `SourceError` when the file is missing.
  - `@dataclass(frozen=True) class Manifest: paths: dict[str, Path]; overrides: Path | None` and
    `load_manifest(path: Path | None = None, *, env=os.environ) -> Manifest`: default is `sources.toml` next to the
    module; `~` is expanded, `{live}` is `live_checkout.LIVE_ROOT`, `{mail_home}` is `OUTREACH_MAIL_HOME` when set,
    else `{live}/mail`; `local_storage` names a glob and resolves to its newest match by name.
  - `@dataclass(frozen=True) class Overrides: same_company: dict[str, str]; not_same_company: frozenset[frozenset[str]]; person_name: dict[str, str]; done_add: frozenset[str]; done_remove: frozenset[str]; touch_for: dict[str, str]` and
    `load_overrides(path: Path | None) -> Overrides` (`tomllib`; a missing path gives empty overrides; an unknown table
    raises `ValueError`). Keys are `name_key`s or record ids `<source>:<source_key>`. The file lives outside the repo
    (`~/Desktop/outreach-scratch/import/overrides.toml`) because it holds his data.
  - `tests/importtest.py`: `fixture_manifest(tmp: Path) -> Manifest` (copies `tests/fixtures/import/` into `tmp`,
    builds `radar.db` there with `build_radar.py`, returns a manifest of those paths) and `all_marks(conn) ->
    dict[str, tuple[int, int]]` (`(count, max rowid)` for every table in `sqlite_master`).
  - The fixture set (all synthetic): a `data.js` with five companies (`Kestrel Robotics` with `seed: "Interviewing"`
    and an `outreach.domain`, `Lumen Freight` with `visaDenied: true` and a quote, `Orbital Foods` with `sizeClass:
    "cleared"`, `Quarry Labs` with a "well over 500 people" `sizeWhy`, `Tidewater AI` plain) and two `RULED_OUT` entries (one
    plain, `Saltmarsh Bio`; one slash list, `Ferrous Grid / Tidewater AI`, whose second name is also a `COMPANIES` row); a
    tracker backup; a `contacts.json` with one contact in each of `drafted, approved, sent, replied, bounced,
    followup_ready, followup_sent, closed, skipped, failed`; a `send.log`; `build_radar.py` (Job Radar's `SCHEMA_V1`,
    views and migrations 3 to 5 copied as schema, with 3 boards, 6 postings with versions, app events `opened`,
    `applied`, `ignored`, `screen`); a `job-state.json`; a `contacted.txt` with comments, a 4-field line, a two-person
    line and one line that does not parse; three company files; `interview-prep/companies/` with a frontmatter brief
    plus sessions, a brief with no frontmatter, and `general/`; a `curated_touches.csv` whose rows match, annotate and
    miss the touches above. The base set has no held record; a test that needs one (an unclear send, a `send.log`
    "sent" line with no contact) adds it to its own copy.

- [ ] **Step 1: Write the failing tests.** `test_record_hash_is_order_free_json` (two dicts with the same items in another order give the same `raw_sha256`); `test_record_refuses_unknown_source_and_empty_key`; `test_manifest_expands_live_and_mail_home` (`OUTREACH_MAIL_HOME` wins over `{live}/mail`); `test_manifest_picks_the_newest_backup_by_name`; `test_missing_source_is_a_named_error_not_zero_records`; `test_open_radar_is_read_only` (an INSERT raises `sqlite3.OperationalError`; the file digest is unchanged); `test_overrides_refuse_an_unknown_table`; `test_fixture_set_has_no_real_names` (every fixture company name is in a fixed synthetic list in the test).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_import_records -v`. Expected: FAIL (module not found).
- [ ] **Step 3: Implement** the modules and the fixtures.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Import: source records, manifest, overrides and synthetic fixtures (#17)`.

## Task 3: Text readers

**Files:**
- Create: `outreach/importer/read_text.py`
- Test: `tests/test_import_read_text.py`

**Interfaces:**
- Consumes: Task 2.
- Produces (each a generator of `SourceRecord`, raising `SourceError` on a missing or undecodable file):
  - `read_contacted_txt(path) -> Iterator[SourceRecord]`: every line, raw `{"line": text}` plus, when it parses,
    `date`, `company`, `rest`, `people: [{"name", "note"}]`. Parse: `^(\d{4}-\d{2}-\d{2}) - (.+?) - (.+)$`; people are
    `rest` split on ` + `, each with a trailing `(...)` taken as `note`. Comment and blank lines carry `"kind":
    "comment"` or `"blank"`; a line that does not match carries `"kind": "unparsed"`.
  - `read_curated_touches(path)`: one record per CSV row with the nine columns as text.
  - `read_devboard(path)`: one record per key; raw adds `parsed_key`: `{"ats", "token", "native_id"}` for
    `greenhouse`, `ashby`, `lever` (3 parts) and Workday (`workday:<tenant>:<wdN>:<site>:<id>` becomes token
    `<tenant>.<wdN>/<site>`), else `null`.
  - `read_local_storage(path)`: one record per company id; raw is the entry as exported.
  - `read_contacts_json(path)`: one record per contact plus `first_send_date`.
  - `read_send_log(path)`: one record per line; raw `{"line", "at", "kind"}` with `kind` `sent` for
    `sent [follow-up ]to <name> <<email>> at <company>`, `unclear`, `found_in_sent`, `bounced`, `reply`, else `log`.

- [ ] **Step 1: Write the failing tests** on the fixtures: `test_contacted_line_with_two_people_gives_two_names_and_notes`; `test_contacted_line_with_a_dash_in_the_company_keeps_the_first_split` (the 4-field line: company is the second field, the rest keeps the dash); `test_comment_blank_and_unparsed_lines_are_records_with_their_kind`; `test_curated_row_keeps_every_column_as_text`; `test_devboard_workday_key_becomes_the_shared_token_format` (`workday:acme:wd5:External:R-1` gives `acme.wd5/External`); `test_devboard_hcafe_key_has_no_parsed_key`; `test_send_log_sent_line_names_the_email`; `test_unreadable_json_is_a_source_error`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_import_read_text -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Import: readers for the tracker backup, contacts, send log, devboard, contacted.txt and curated rows (#17)`.

## Task 4: Frontmatter readers for company files and interview prep

**Files:**
- Create: `outreach/importer/read_notes.py`
- Test: `tests/test_import_read_notes.py`

**Interfaces:**
- Produces:
  - `parse_frontmatter(text: str) -> dict | None`: `None` when the text does not start with `---`. Reads the subset the
    files use: `key: scalar` lines (quotes stripped, `null` and `~` to None, a trailing ` # comment` dropped) and one
    level of list of maps (`people:` then `  - key: value` and `    key: value`). Anything else under a key is kept as
    the raw text of that key, never dropped.
  - `read_devvault_notes(folder) -> Iterator[SourceRecord]`: one record per `*.md` under `folder` (recursive, except
    `README.md`), raw `{"path", "folder", "frontmatter"}` (`folder` is the first path part: `reached-out`, `todo`,
    `sponsor-viable`, `_archive`); a file with no frontmatter has `"frontmatter": null`.
  - `read_interview_prep(folder) -> Iterator[SourceRecord]`: one record per subfolder, raw `{"slug", "brief":
    frontmatter or null, "session_dates": [sorted ISO dates from sessions/ file names], "has_brief": bool}`.

- [ ] **Step 1: Write the failing tests:** `test_frontmatter_people_list_of_maps`; `test_quoted_value_with_a_colon_is_one_value`; `test_null_and_comment_are_handled`; `test_unknown_nested_shape_is_kept_as_text`; `test_file_without_frontmatter_is_still_a_record`; `test_interview_folder_dates_come_from_session_names`; `test_brief_without_frontmatter_keeps_the_slug`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_import_read_notes -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Import: frontmatter readers for company files and interview prep (#17)`.

## Task 5: The data.js reader

**Files:**
- Create: `outreach/importer/read_data_js.py`, `outreach/importer/data_js_dump.mjs`
- Test: `tests/test_import_read_data_js.py`

**Interfaces:**
- Produces:
  - `data_js_dump.mjs`: reads the file named in `argv[2]`, runs it with `vm.runInNewContext(text, {window: {}},
    {timeout: 5000})`, prints `JSON.stringify({COMPANIES, RULED_OUT, META, REFERENCE})` from that `window`. No
    `require`, no file writes, no network.
  - `read_data_js(path, *, node: str | None = None) -> Iterator[SourceRecord]`: runs `node` (the given path, else
    `shutil.which('node')`, else `~/.nvm/versions/node/v20.19.5/bin/node`) with a 30 second timeout; a non-zero exit,
    a timeout or no Node raises `SourceError` with Node's stderr. Records: `company:<id>`, `ruled_out:<n>`, `meta`,
    `reference`.

- [ ] **Step 1: Write the failing tests:** `test_fixture_data_js_gives_five_companies_two_ruled_out_and_meta`; `test_data_js_cannot_require` (a fixture that calls `require('fs')` raises `SourceError`); `test_missing_node_is_a_source_error`; `test_data_js_file_is_unchanged_after_reading` (digest before and after). Skip the tests with a clear message when no Node is found.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_import_read_data_js -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Import: read data.js through a Node sandbox (#17)`.

## Task 6: The radar.db reader

**Files:**
- Create: `outreach/importer/read_radar.py`
- Test: `tests/test_import_read_radar.py`

**Interfaces:**
- Consumes: `open_radar` (Task 2).
- Produces:
  - `RADAR_ROW_TABLES = ('board', 'posting', 'app_event')` (one record per row) and `RADAR_TABLE_REASONS: dict[str,
    str]` for every other table in Job Radar's schema: `poll_run` and `board_fetch` `run_history`, `posting_version`
    `older_version` (the newest version per posting is read with its posting), `decision` `derived_decision`, `outbox`
    `outbox_delivery`, `label` `label_rubric`, `body_attempt` `run_history`, `meta` and `schema_migration`
    `schema_meta`.
  - `read_radar(path) -> Iterator[SourceRecord]`: one read transaction. `board:<id>` raw is the row. `posting:<id>`
    raw is the row plus `closed` from `posting_live` and `latest` (the newest `posting_version` by `observed_at`:
    `version_id`, `content_hash`, `observed_at`, and `body_text` kept out of the raw and returned beside it, see
    below); `app_event:<id>` raw is the row. For each other table, one record `table:<name>` with raw `{"rows": N,
    "reason": <reason>}`; `posting_version` counts the versions not read. A table in the file that is in neither list
    raises `SourceError` naming it (a new radar table must be decided, not dropped).
  - `radar_body(record: SourceRecord) -> str | None`: the body text read beside a posting record (kept out of
    `import_record.raw`, because `radar.db` stays as the archive and the body goes into `posting_body`).

- [ ] **Step 1: Write the failing tests:** `test_every_radar_table_is_a_row_record_or_a_table_record` (the union of record tables equals `SELECT name FROM sqlite_master WHERE type='table'` of the fixture); `test_unknown_radar_table_is_a_source_error`; `test_posting_record_carries_closed_and_latest_version`; `test_body_is_not_in_the_raw_record`; `test_reader_never_writes_radar` (digest before and after; `-wal` and `-shm` absent or unchanged).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_import_read_radar -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Import: read radar.db read-only, every table accounted for (#17)`.

## Task 7: Vocabulary

**Files:**
- Create: `outreach/importer/vocab.py`
- Test: `tests/test_import_vocab.py`

**Interfaces:**
- Produces pure mappings (each returns `None` for an unknown value, so the planner can raise `role_unmapped` or
  `reply_text` instead of guessing):
  - `role_kind(text) -> str | None`: `founder` founder, `recruiter` recruiter, `hiring-manager` and `hiring manager`
    manager, `engineer` engineer, `alum` alum, `invalid` other; contacts.json `kind` values map to themselves.
  - `contact_status_plan(status) -> ContactPlan` (`message0`, `message1`, `touch_state`, `held`):
    `drafted` (draft, none, open), `approved` (draft, none, open; an event notes the old approval), `failed` (draft,
    none, open), `skipped` (skipped, none, closed `skipped`), `sent` (sent, none, open), `replied` (sent, sent when
    `followupSentAt`, replied), `bounced` (sent, none, bounced), `followup_ready` (sent, draft, open),
    `followup_approved` (sent, draft, open), `followup_sent` (sent, sent, open), `closed` (sent, sent when
    `followupSentAt`, closed `no_reply`), `sending` and `send_unknown` (held `unclear_send`).
  - `note_reply(text) -> tuple[list[OutcomeKind], str | None] | None` for company file `reply` words: `null`, `n/a`
    no outcome; `replied` replied `unsorted`; `polite-no` replied `said_no`; `soft-positive` replied `interested`;
    `scheduled-call`, `take-home` replied `interested` plus interview; `linkedin-referral` (any word starting
    `linkedin-refer`) replied `interested` plus referred (Pending Devanshu Q14, with `routed` below); `bounced` bounce. Anything else is `None` (free text).
  - `curated_outcome(text) -> ...`: `none` nothing, `ooo` auto reply, `bounce` bounce, `no` replied `said_no`, `soft`
    replied `unsorted`, `routed` replied `interested` plus referred, `call` replied `interested` plus interview.
  - `tracker_stage(stage) -> list[OutcomeKind]` for the old tracker: `Not started`, `Researching` nothing;
    `Referral asked` a touch; `Applied` an application; `Replied` replied `unsorted`; `Interviewing` interview;
    `Offer` offer; `Closed` closes the company (`other`, note from `notes`).
  - `tracker_channel(text) -> str`: `LinkedIn` `linkedin_request`, `Email` `email`, empty `other`.
  - `curated_channel(text)`: `email`, `linkedin` (`linkedin_request`), `imessage` (`other`).
  - `radar_event(kind) -> RadarEffect | None`: `applied` application; `reply` replied `unsorted`; `screen` application
    outcome `screen` plus replied `interested`; `onsite` application `interview` plus interview; `offer` application
    `offer` plus offer; `rejected` application `rejected`; `withdrawn` application `closed`; `opened` skip
    `opened_only`; `ignored`, `unignored` skip `triage_ignore`.
  - `band_stage(band) -> str | None`: `seed` `seed`, `A` `series_a`, `B` `series_b`; `growth`, `large`, `agency`
    `None` (no matching stage value).
  - `FROZEN_ATS = frozenset({'amazon', 'apple', 'google', 'eightfold'})` and `MAYBE_FROZEN_ATS =
    frozenset({'workday', 'oracle_orc'})` (frozen when the company has size evidence with `num_low > 300`).
  - `NOON_NY`: a date-only time becomes 12:00 America/New_York, converted to UTC, so the New York day never moves.

- [ ] **Step 1: Write the failing tests:** one table test per mapping (`test_role_kind_table`, `test_contact_status_table`, `test_note_reply_table_and_free_text_is_none`, `test_curated_outcome_table`, `test_tracker_stage_table`, `test_radar_event_table`, `test_band_stage_table`), and `test_date_only_is_noon_new_york` (`2026-11-02` gives `2026-11-02T17:00:00Z`, `2026-10-30` gives `2026-10-30T16:00:00Z`).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_import_vocab -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Import: one table for every old value (#17)`.

## Task 8: Linking companies and people

**Files:**
- Create: `outreach/importer/model.py`, `outreach/importer/link.py`
- Test: `tests/test_import_link.py`

**Interfaces:**
- Consumes: `name_key` (`outreach.store.writes`), `Overrides`, Task 7.
- Produces in `model.py` (frozen dataclasses; every one carries `records: tuple[str, ...]`, the record ids
  `<source>:<source_key>` it came from):
  - `CompanyIn(key: str, name: str, names: frozenset[str], domain: str | None, boards: tuple[tuple[str, str], ...],
    evidence: tuple[EvidenceDraft, ...], hand: tuple[EvidenceDraft, ...], no_go: tuple[tuple[str, str], ...],
    close: tuple[str, str] | None, notes: tuple[tuple[str, str], ...], records)`. `key` is the canonical `name_key`.
    `hand` drafts use `source_kind 'hand'` and are written locked by the writer. `no_go` pairs are `(reason, quote)`.
    `notes` pairs are `(record id, text)`: the old tracker's `notes` and `next` and the old dashboard's `RULED_OUT`
    reason. There is no `removed`: the import never removes a company (integration review B7).
  - `PersonIn(key: str, company_key: str, name: str, placeholder: bool, title: str, role_kind: str, email: str | None,
    linkedin_url: str | None, profile_text: str, tz: str, records)`. `key` is `<company_key>/<email or linkedin or
    name_key(name)>`.
  - `OutcomeIn(kind: str, at: str, reply_kind: str | None, estimated: bool, record: str)`.
  - `EmailIn(contact_id: str | None, subject0, body0, rfc0, thread_id, gmail_id0, sent0_at, subject1, body1, rfc1,
    sent1_at, due1_on, touch_state, close_reason, bounced_at, auto_reply_at, history: tuple[tuple[str, str], ...])`
    (fields absent for a historic email are None).
  - `TouchIn(key: str, company_key: str, person_key: str | None, channel: str, sent_at: str, posting_key: str | None,
    role_named: bool, role_named_known: bool, outcomes: tuple[OutcomeIn, ...], email: EmailIn | None,
    followups_sent: int, records)`. `key` is `<company_key>|<person_key or ->|<channel>|<New York date>`.
  - `ApplicationIn(key, company_key, posting_key: str | None, applied_on: str, url: str, outcome: str, outcome_at:
    str | None, records)`, `BoardIn(ats, token, company_key, active: bool, records)`, `PostingIn(source_key: str,
    company_key, board: tuple[str, str] | None, title, location, url, posted_on, first_seen_at, last_seen_at,
    closed: bool, body: str | None, records)`.
  - `IssueIn(code: str, company_key: str | None, record: str, detail: str, holds: bool)`.
  - `RecordOutcome(outcome: str, reason: str, entity: str | None, entity_key: str | None)`.
- Produces in `link.py`:
  - `class CompanyIndex(overrides: Overrides)`: `add(name: str, record: str, *, base: bool = False) -> str` returns
    the canonical key; `same_company` overrides map a key to another; data.js companies are the base (`base=True`);
    every other source links by exact `name_key`. `near_names() -> list[tuple[str, str]]`: pairs of keys where one is
    the other plus or minus a trailing word from `ai, labs, inc, technologies, systems, hq, app` or a parenthetical, or
    one key starts with the other and the shorter has at least 5 characters, and the pair is not in
    `not_same_company`.
  - `parse_person_name(text: str) -> str | None`: the name when the text has 1 to 4 words, each starting with a
    letter, and is not a role word (`founder`, `founders`, `cto`, `ceo`, `recruiter`, `team`, `hiring`); else `None`.
  - `class PersonIndex`: `add(company_key, name, *, email=None, linkedin=None, record) -> str`: joins an existing
    person at the company on email, then LinkedIn URL, then `name_key`, then a first name that is the first word of
    exactly one known person there (the `contacted.txt` habit); a first-name match to two people does not join.

- [ ] **Step 1: Write the failing tests:**
  - `test_exact_name_key_links_sources_to_the_data_js_company` (`Kestrel Robotics, Inc.` in a company file joins `Kestrel Robotics`).
  - `test_near_names_raise_one_merge_item_and_stay_two_companies` (`Tidewater` and `Tidewater AI`: two keys, one pair from `near_names`; with `not_same_company` set, none; with `same_company`, one key).
  - `test_short_prefix_is_not_a_near_name` (`Arc` and `Arcadia`).
  - `test_first_name_joins_the_company_file_person_once` (`Dana` in contacted.txt joins `Dana Whitfield`; with two Danas at the company it makes a new person).
  - `test_role_word_is_not_a_name` (`founders` gives `None`).
  - `test_email_joins_before_name` (same email, different spelling: one person).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_import_link -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Import: link companies and people across sources, near names surfaced (#17)`.

## Task 9: Touches, the curated join and where each interview came from

**Files:**
- Modify: `outreach/importer/link.py`
- Test: `tests/test_import_touches.py`

**Interfaces:**
- Produces in `link.py`:
  - `join_curated(touches: list[TouchIn], rows: list[SourceRecord], companies: CompanyIndex) -> tuple[list[TouchIn],
    list[IssueIn], dict[str, RecordOutcome]]`: a curated row joins the touch at the same company and New York date with
    the same role kind; with several, rows and touches pair in record order. A joined row adds its outcome (Task 7),
    `role_named` from `req` (`True` and `False` known; empty leaves `role_named False`, `role_named_known False`) and
    `followups_sent` from `fu`; record outcome `merged`, reason `annotation`. A row with no touch becomes its own touch
    with a placeholder person and an issue `touch_unmatched`; record outcome `imported`.
  - `origin_touch(company_key, interview_at, touches, applications) -> tuple[str, str | None]`: the touch key an
    interview is recorded on and the issue code, by this order: (1) the only touch at the company sent on or before the
    date with a reply or a positive outcome; (2) the only touch before the date; (3) the latest touch before the date,
    issue `interview_origin`; (4) the latest application touch before the date; (5) none: the caller makes a
    `channel 'other'` touch with a placeholder person at the interview date, issue `interview_origin`. Overrides
    `touch_for` (record id to touch key) win.
  - `interview_date(record) -> tuple[str, bool]`: the earliest session date, else the brief's `verified_on`, else the
    origin touch's date with `estimated True` and issue `date_unknown`.

- [ ] **Step 1: Write the failing tests:**
  - `test_curated_row_annotates_the_matching_touch_and_is_merged`.
  - `test_three_curated_rows_pair_with_three_touches_in_order`.
  - `test_unmatched_curated_row_is_its_own_touch_with_an_item`.
  - `test_unknown_req_is_not_a_known_role_named`.
  - `test_interview_goes_to_the_touch_that_got_a_reply`.
  - `test_interview_without_touch_gets_an_other_touch_and_an_item`.
  - `test_interview_falls_back_to_the_application_touch`.
  - `test_touch_for_override_wins`.
  - `test_interview_date_prefers_sessions_then_verified_on`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_import_touches -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Import: curated annotations and the origin of each interview (#17)`.

## Task 10: The plan builder

**Files:**
- Create: `outreach/importer/plan.py`
- Test: `tests/test_import_plan.py`

**Interfaces:**
- Consumes: Tasks 2 to 9; `from_old_dashboard`, `old_dashboard_signals`, `parse_stage`, `EvidenceDraft`
  (`outreach.rules.facts`); `SendConfig`, `add_business_days` (#14, `outreach.sending.config` and `.calendar`).
- Produces:
  - `@dataclass(frozen=True) class ImportPlan: companies: dict[str, CompanyIn]; people: dict[str, PersonIn]; touches:
    dict[str, TouchIn]; applications: dict[str, ApplicationIn]; boards: dict[tuple[str, str], BoardIn]; postings:
    dict[str, PostingIn]; issues: tuple[IssueIn, ...]; records: dict[str, tuple[SourceRecord, RecordOutcome]];
    done_set: tuple[str, ...]`.
  - `read_all(manifest: Manifest) -> list[SourceRecord]` (every reader; one `SourceError` stops it).
  - `build_plan(records: Sequence[SourceRecord], overrides: Overrides, *, now: str, send_cfg: SendConfig) -> ImportPlan`:
    pure (`send_cfg` is #14's `load_config()`, passed in). Every record
    gets exactly one `RecordOutcome`. Per source:
    - `data_js` companies: `CompanyIn` (base), domain from `outreach.domain` when it is a bare host, a board from
      `board`, postings from `reqs` (`url:<u>` keys, open), evidence from `from_old_dashboard`; `visaConfirmed`
      `True` hand `sponsorship yes`, `visaDenied` `True` hand `sponsorship no` plus issue `old_no_sponsorship` (no
      `no_go`; Pending Devanshu Q6), `optOnly` hand `opt_only`, each quoting `visa`; `old_dashboard_signals` kinds `defence_contractor`
      and `agency` become `no_go` with the `sizeWhy` quote, `over_500` size evidence `estimate` `num_low 501`
      `source_kind old_dashboard`, `big_by_filings` and `sec_filings` issue `old_size_signal`; `seed` becomes an
      interview or a reply on the company. `meta` and `reference` skip `reference_data`.
    - `data_js` `RULED_OUT` (Pending Devanshu Q4): an entry's name splits on ` / ` into one part per name (17 entries
      name about 30 companies, and several are also `COMPANIES` rows). Each part links like any other name
      (`CompanyIndex.add`), and its company stays visible: the entry's `why` becomes a company note, plus one issue
      `ruled_out` per part: `review_item` `kind 'flagged'`, `raised_by 'import'`, key
      `import:ruled_out:<n>:<name_key>`, reason `The old dashboard ruled this out: <why>. Remove it if that still
      holds.` Nothing is `removed` on import: the old reasons are research judgments, not #9 evidence, and a removed
      company is never proposed again. The record's outcome is `imported` (entity the first part's company); every
      part's company lists the record id.
    - `local_storage`: an entry equal to the default (`Not started` and every field empty, no `skipped`) skips
      `untouched_default`; `skipped` alone skips `old_queue_skip`; otherwise a touch (`tracker_channel`, `reached`,
      the `contact` name or a placeholder) with outcomes from `tracker_stage` and `repliedOn`, and `notes` and `next`
      as company notes.
    - `contacts_json`: a person (name, email, title, LinkedIn, profile, kind, tz) and an email touch from
      `contact_status_plan`, with the history entries; a `sent` contact with no follow-up yet gets one `planned`
      follow-up per entry of #14's `followup_business_days`, `due_on = add_business_days(<New York send date>, n,
      cfg)` (#14 never plans one for an imported touch), but only when that `due_on` is on or after the New York date
      of `now` (the import date); an earlier one is not planned and counts in the report line `follow-ups not
      planned: due before the switch` (so the first Monday never asks Claude for a stale follow-up); a
      `followup_ready` or `followup_approved` contact's draft gets the same `due_on`; `role.title` or `role.url` makes
      `role_named` known true.
      `sending` and `send_unknown` give issue `unclear_send` with `holds True` and outcome `held`.
      `first_send_date` skips `old_mailer_setting`.
    - `send_log`: a `sent` line merges (`same_touch`) with the contact whose email and date match; with none, issue
      `sent_not_in_contacts`, `held`. Other lines skip `log_line`.
    - `contacted_txt`: comment and blank lines skip `comment_line` and `blank_line`; an unparsed line with a date and
      a company becomes a touch with a placeholder person and issue `name_unparsed`; with no company it is `held`
      (`unparsed_record`). Each person is an email touch (`origin import`, no message rows) on that date.
    - `devvault_notes`: the company (domain, stage through `parse_stage`, location as `hq`), each person, and a touch
      per person with a `sent` date; `reply` through `note_reply` (free text: replied `unsorted` plus issue
      `reply_text` quoting it); `reply_date` dates the outcome, else the touch date with `estimated True`.
    - `interview_prep`: `general` skips `not_a_company`; every other folder is an interview on the company named in
      the brief (else the slug), placed with `origin_touch`, dated with `interview_date`.
    - `curated_touches`: `join_curated`; `band_stage` adds a `stage` `guess` draft (`source_kind other`, quote
      `curated band`).
    - `radar_db`: each board a `BoardIn` (frozen per `FROZEN_ATS` and `MAYBE_FROZEN_ATS`; Pending Devanshu Q18), each posting a `PostingIn`
      (closed when radar says closed or the board is frozen; `posted_on` only from `exact` or `day` precision), each
      app event through `radar_event` into an application on that posting; table records skip with their reason.
    - `devboard`: `applied` becomes an application (merged with a radar application on the same `source_key`);
      `ignored` skips `triage_ignore`.
    - A historic email touch (no `EmailIn` from contacts.json) gets `touch_state 'closed'`, `close_reason
      'no_reply'` unless it has a reply or a bounce, so the new sender never follows it up (Pending Devanshu Q25).
    - One email touch per person (the store's `touch.person_id` is unique): email records for one person merge into
      the earliest (`same_touch`); a later date adds to `followups_sent`, and a contacts.json touch is the one kept
      when there is one.
    - The done set (Pending Devanshu Q5): every company with an interview or offer outcome, plus `done_add`, minus `done_remove`; each
      gets `close ('interview', 'Done: interviewed (imported)')`.

- [ ] **Step 1: Write the failing tests** (on the fixture records):
  - `test_every_record_gets_exactly_one_outcome` (`len(plan.records) == len(records)`).
  - `test_visa_denied_is_hand_evidence_and_an_item_not_a_no_go`.
  - `test_cleared_and_agency_become_no_go_and_over_500_becomes_size_evidence`.
  - `test_ruled_out_is_a_note_and_an_item_not_a_removal` (`Saltmarsh Bio`: no `removed`, one note with the `why`,
    one `ruled_out` issue with key `import:ruled_out:0:<name_key>` and the exact reason).
  - `test_ruled_out_slash_names_split` (`Ferrous Grid / Tidewater AI`: two parts, `Tidewater AI` links to its
    `COMPANIES` row, `Ferrous Grid` is a new visible company, one item each, one record outcome `imported`).
  - `test_untouched_tracker_entry_is_an_explained_skip`.
  - `test_unclear_send_is_held_and_blocks_the_run` (`holds True`; `plan.records[...]` outcome `held`).
  - `test_sent_log_line_without_contact_is_held`.
  - `test_approved_contact_becomes_a_draft_not_an_approval` (no plan field can express approved).
  - `test_sent_contact_gets_one_planned_follow_up_seven_business_days_later` (sent Friday 2026-10-16 with `now` 2026-10-20 gives `due_on` 2026-10-27; a replied contact gets none).
  - `test_follow_up_due_before_the_import_is_not_planned_and_counted` (the same contact with `now` 2026-11-01: no planned row; the count is 1).
  - `test_historic_email_touch_is_closed_and_has_no_message`.
  - `test_free_text_reply_is_unsorted_with_the_text_in_an_item`.
  - `test_radar_frozen_board_closes_its_postings_and_keeps_the_application`.
  - `test_devboard_and_radar_application_on_one_posting_is_one_application`.
  - `test_two_email_records_for_one_person_are_one_touch` (contacted.txt and a company file `sent` on two dates: one touch at the earlier date, `followups_sent 1`).
  - `test_done_set_is_interviews_plus_overrides` (`Kestrel Robotics` in, `done_remove` takes it out).
  - `test_build_plan_is_deterministic` (two runs give equal plans).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_import_plan -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `read_all` and `build_plan`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Import: one plan from every source, an outcome for every record (#17)`.

## Task 11: The writer

**Files:**
- Create: `outreach/importer/write.py`
- Test: `tests/test_import_write.py`

**Interfaces:**
- Consumes: `ImportPlan`; `connect`, `transaction` (`outreach.store.db`); `append_event`, `start_job`, `finish_job`,
  `add_alias`, `log_touch`, `record_outcome`, `body_sha256` (`outreach.store.writes`); `record_evidence`, `rebuild`
  (`outreach.rules.rebuild`); `Candidate` (`outreach.sources.base`); `resolve_candidate`, `raise_merge_candidate`,
  `follow_merged` (`outreach.identity`, #31 Task 7); `register_source` (`outreach.sources.jobs`, #31 Task 8);
  `add_board`, `BoardRef` (`outreach.sources.boards`, #31 Task 10); `build_context`
  (`outreach.outcomes.context`, #11 Task 9); `raise_item` (`outreach.review`, #16 PR 1, the one helper that raises a
  review item).
- Produces:
  - `@dataclass(frozen=True) class WriteResult: run_id: int; ids: dict[str, int]; written: dict[str, int]`
    (`written` is rows added per table).
  - `apply_plan(conn, plan: ImportPlan, *, now: str) -> WriteResult`: refuses a plan with a held record
    (`ValueError` naming the first). One `job_run` `import`; `register_source(conn, 'import', IMPORTER_VERSION,
    run_id)`. Writes in this order, one `transaction` per company (its people, touches, applications and records), so a
    failure keeps every finished company and the run ends `partial` with the error, never `ok`:
    1. Company: `resolve_candidate(conn, Candidate(name=..., domain=..., state=<the old record's US state when it has one, else None>,
       keys={'ats': '<ats>:<token>'} (the first board, or `{}`), evidence=<the plan's drafts>, source_ref=<first record id>), source_name='import',
       actor='import')`; every other
       spelling through `add_alias(..., 'import')` (an alias owned elsewhere raises an `import` review item, never a
       move); hand drafts inserted `locked = 1` once per `(company, fact, value, source_ref)`; `no_go` rows
       (`source 'import'`, `source_ref 'import:<record id>'`) unless one is active; `close` set with an event
       (`entity company`, `actor import`); each note as one company event `note` (actor `import`, detail `{text,
       record}`, key `import:note:<record id>:<n>`); a company with any touch or application and no close is
       `working`. The import never sets `removed`.
    2. Boards through `add_board(conn, company_id, BoardRef(ats, token), via='import', matched_on='import',
       found_by='import', actor='import')` (`add_board` takes `actor=`, so the board event does not read `crawler`), then `active = 0` for frozen ones. Postings `INSERT ... ON CONFLICT(source_key) DO NOTHING`
       (`source_key` `<ats>:<token>:<native_id>`, or `url:<url>`), closed ones with `close_reason 'imported_closed'`;
       bodies into `posting_body` with `body_sha256(body)` and `fetched_at NULL`.
    3. People: `person` rows `added_by 'import'`; `channel 'email'` with or without an address (the schema allows a
       NULL address for imports), `channel 'linkedin'` and `linkedin_reason 'no_email'` when only a LinkedIn URL is
       known; a placeholder is named `Unnamed <role kind> (imported)` and its notes say which record it came from. An
       email or LinkedIn URL owned by another person raises `person_conflict` and the person is written without it.
    4. Touches: `log_touch(person_id, company_id, channel, sent_at, context=build_context(source='own', lane=None,
       role_kind=<person role>, role_age_days=None, ranker_version=None, signals={}, role_named=<bool>), posting_id,
       email_touch_id, idempotency_key='import:' + touch key, actor='import')`. For email: a `touch` row (`origin
       'import'`, the state and dates of `EmailIn`) first, `outreach_touch.email_touch_id` pointing at it; message
       rows only when `EmailIn` has text: `sent` inserted directly (subject, body, `rfc_message_id`, `sent_at`,
       `saved_by 'import'`), a draft inserted `drafted`, a skipped one inserted `drafted` then moved to `skipped`. A
       sent follow-up with no Message-ID gets `<import-<contact id>-1@outreach.invalid>` and an event saying so. For
       `linkedin_request`, a `linkedin_log` `request_sent` row in the same transaction (and `accepted`, `replied` rows
       for those outcomes). Outcomes through `record_outcome(..., actor='import')` in date order (a date before the
       touch's `sent_at` is raised to `sent_at` and counted as estimated), skipping one already set to the same value; a different stored value raises `outcome_conflict` and is not overwritten. Auto
       replies and bounces go on the email `touch` only. A `said_no` reply adds a person `no_go` (`said_no`, `source
       'import'`). Each touch gets one `outreach_touch` event `imported` (actor `import`, detail `importSource`,
       `records`, `estimatedDates`, `roleNamedKnown`, `followupsSent`).
    5. Applications: `application` row plus `log_touch(person_id=None, channel='application', ...)`.
    6. Every win imported (accepted, interested reply, referred, interview, offer) gets a `win_seen` row at `now`.
    7. Review items: `near_name` pairs through `raise_merge_candidate(conn, a, b, 'import_near_name', detail,
       confidence='weak')` (a near name is never a strong match; #31's `_ITEM_CONFIDENCE` has no entry for this basis,
       so without `confidence=` it raises `ValueError`; `weak` is in `ITEM_CONFIDENCES`); every other issue through
       `raise_item(conn, kind='flagged', company_id=..., reason=<one plain line>, evidence_key='import:<code>:<record
       id>', raised_by='import', actor='import')` (`ruled_out` keeps its own key, Task 10). There is no inline insert.
       Every import item's key starts `import:`, so the Needs-you view can group them.
    8. `import_record` for each record not stored before under the same `(source, source_key, raw_sha256)`, with its
       outcome, reason and the entity (`company`, `person`, `outreach_touch`, `application`, `posting`, `board`) and id.
    9. The rules: `rebuild(conn)` (with the reader's combiner and `apply_all` when `outreach.reader.combine` imports,
       as `tests/pipeline/stages_rules.py` does).
    10. `finish_job(conn, run_id, 'ok', summary=<the count report as a dict>)`.

- [ ] **Step 1: Write the failing tests** (fixture plan on `fresh_store()`):
  - `test_apply_writes_every_record_once` (`import_record` count equals the records not held).
  - `test_second_apply_changes_nothing_but_the_job_run` (`all_marks` of every table except `job_run` are equal before and after the second apply; `job_run` gains exactly one row).
  - `test_touches_use_the_write_helpers_and_context_source_own` (every `outreach_touch.context` has `source 'own'`; every row has `idempotency_key` starting `import:`).
  - `test_email_history_has_touch_and_outreach_touch_and_the_sent_message`.
  - `test_linkedin_touch_has_a_matching_linkedin_log_row`.
  - `test_imported_wins_are_marked_seen`.
  - `test_hand_evidence_is_locked_and_written_once` (second apply adds no hand row).
  - `test_held_plan_is_refused_before_any_write`.
  - `test_changed_backup_adds_the_new_outcome_and_overwrites_nothing` (apply, then a backup where a stage moved from `Replied` to `Interviewing` and the reply date changed: the interview is added, the reply date stays, one `outcome_conflict` item).
  - `test_failure_mid_run_keeps_finished_companies_and_ends_partial` (patch the second company's write to raise).
  - `test_done_set_companies_are_closed_interview`.
  - `test_near_name_item_has_weak_confidence_and_basis_import_near_name` (the `merge_candidate` reason names basis `import_near_name` and confidence `weak`; no `ValueError`).
  - `test_issues_are_raised_through_raise_item_with_actor_import` (each `flagged` item has `raised_by 'import'` and a `raised` event with actor `import`; a second apply raises none).
  - `test_ruled_out_company_stays_visible_with_its_note` (status not `removed`; one `note` event; one open `flagged` item).
  - `test_sources_are_unchanged_after_apply` (`source_digest` of every fixture source before and after).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_import_write -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `apply_plan`.
- [ ] **Step 4: Run** the same command and the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `Import: write the plan through the foundation helpers and identity (#17)`.

## Task 12: The count report and its check

**Files:**
- Create: `outreach/importer/report.py`
- Test: `tests/test_import_report.py`

**Interfaces:**
- Produces:
  - `EXPLAINED_SKIPS = frozenset({'comment_line', 'blank_line', 'untouched_default', 'old_queue_skip',
    'reference_data', 'old_mailer_setting', 'log_line', 'triage_ignore', 'opened_only', 'not_a_company', 'run_history',
    'older_version', 'derived_decision', 'outbox_delivery', 'label_rubric', 'schema_meta'})` and `MERGE_REASONS =
    frozenset({'same_company', 'same_person', 'same_touch', 'same_application', 'same_posting', 'annotation'})`.
  - `@dataclass(frozen=True) class SourceCount: read: int; imported: int; merged: dict[str, int]; skipped: dict[str,
    int]; held: dict[str, int]` and `@dataclass(frozen=True) class CountReport: sources: dict[str, SourceCount];
    radar_tables: dict[str, tuple[int, str]]; would_write: dict[str, int]; issues: dict[str, int]; done_set:
    tuple[str, ...]; followups_not_planned: int; first_issues: tuple[str, ...]` (the first 50 issue lines, plain
    text; `followups_not_planned` counts Task 10's follow-ups due before the import date).
  - `count_report(plan: ImportPlan, records: Sequence[SourceRecord], *, written: dict[str, int] | None = None) ->
    CountReport`. `radar_tables` names every table of the radar file: `board`, `posting` and `app_event` with their
    record count and `rows`, every other table with its `rows` and reason. A table record also counts as that many
    skipped rows of the `radar_db` source. (#5's retire guard reads `radar_tables` from the stored summary.)
  - `check_report(report) -> list[str]`: one plain line per failure: a source whose `read` is not `imported + merged +
    skipped + held`; a skip reason outside `EXPLAINED_SKIPS`; a merge reason outside `MERGE_REASONS`; any held record;
    a required source with zero records (error is not empty). Empty list means pass.
  - `render_report(report) -> str`: plain lines, one block per source (`read`, `imported`, `merged by reason`,
    `skipped by reason`, `held by code`), the radar tables, the done set, the issue counts, `Follow-ups not planned:
    due before the switch: N`, then `Unexplained losses: 0` or the failures, then the first issues. No dashes as separators.
  - `report_to_json(report) -> dict` (stored in `job_run.summary`) and `report_from_json(obj) -> CountReport`.

- [ ] **Step 1: Write the failing tests:** `test_counts_add_up_for_every_fixture_source`; `test_unknown_skip_reason_fails_the_check`; `test_held_records_fail_the_check`; `test_empty_required_source_fails_the_check`; `test_radar_tables_list_every_table_with_its_rows`; `test_render_says_unexplained_losses_zero_on_a_clean_plan`; `test_report_round_trips_through_json`; `test_render_has_no_em_or_en_dash`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_import_report -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Import: the count report and the zero-unexplained-losses check (#17)`.

## Task 13: The command line

**Files:**
- Create: `outreach/importer/__main__.py`
- Test: `tests/test_import_cli.py`

**Interfaces:**
- Consumes: Tasks 2 to 12.
- Produces `main(argv: list[str] | None = None) -> int` for `uv run python -m outreach.importer <command> [--manifest
  PATH] [--overrides PATH]`:
  - `preflight`: reads every source, no store. Prints per source the path, found or missing, and record count; the
    contacts by status; unclear sends; the newest tracker backup's name. Exit 1 when a source is missing or a send is
    unclear.
  - `dry-run`: reads sources, opens the store with `connect()` read only in effect (no write statement runs; the test
    asserts `conn.total_changes == 0`), builds the plan, prints `render_report`, exits 0 when `check_report` is empty,
    else 1.
  - `run`: the dry run first; refuses (exit 1, nothing written) when the check fails; then `apply_plan`, prints the
    report, then applies the same plan again and prints `Second run changed nothing` when every table but `job_run`
    has equal `all_marks`-style counts, else exits 1 with the tables that changed.
  - `report [--run-id N]`: prints the stored report of the newest (or given) `import` run plus live `import_record`
    counts by source and outcome.
  - `reconcile`: prints `Sent in the old mailer: N`, `Sent in the store: N`, `Sent lines in send.log: N`, `Missing:
    0` or each missing contact (counts first sends and follow-ups by `rfc_message_id`, else by contact and seq).
    Exit 1 when anything is missing.
  - `rollback-check --since ISO`: lists every person the new sender emailed after `--since` (a `message` `sent` with
    `saved_by` other than `import`) whose address is in `contacts.json` with a status that is not sent, so the old
    mailer never emails them again after a rollback. Prints `Nothing to check` when the list is empty.

- [ ] **Step 1: Write the failing tests** (`main([...])` on the fixture manifest and a `fresh_store()` path): `test_preflight_flags_the_unclear_send` (exit 1, the contact named); `test_dry_run_writes_nothing` (`all_marks` equal before and after); `test_run_refuses_when_held` (exit 1, no `import_record`); `test_run_on_the_base_fixture_passes_and_the_second_run_changes_nothing`; `test_reconcile_counts_match_on_the_fixture`; `test_rollback_check_lists_a_person_sent_after_the_switch`; `test_run_on_the_live_path_from_a_worktree_exits_through_the_guard` (`OUTREACH_DB` unset and `DEFAULT_PATH` patched to a temp file: `SystemExit` naming the live checkout, the file untouched).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_import_cli -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Import: preflight, dry run, run, report, reconcile and rollback check commands (#17)`.

## Task 14: The rehearsal command

**Files:**
- Create: `outreach/importer/rehearse.py`; Modify: `outreach/importer/__main__.py` (add `rehearse`)
- Test: `tests/test_import_rehearse.py`

**Interfaces:**
- Produces `rehearse(manifest: Manifest, into: Path, *, now: str) -> tuple[CountReport, list[str]]` and the command
  `rehearse [--into DIR]` (default `~/Desktop/outreach-scratch/import-rehearsal/<YYYY-MM-DD>/`):
  - with the default manifest (his real files), the first statement is `live_checkout.require_live("the rehearsal on
    real data")`, so an agent in a worktree can never copy his full history into a scratch folder; a fixture
    manifest (`--manifest PATH`) runs anywhere;
  - refuses when `into` is inside the live checkout, under `~/Library/Application Support/outreach`, or is a path
    `is_live_path` accepts;
  - copies each source into `into/sources/` (files with `shutil.copy2`, folders with `copytree`, `radar.db` with the
    SQLite backup API from a `mode=ro` connection), writes `into/manifest.toml` pointing at the copies and the same
    overrides file, installs a store at `into/outreach.db` with `outreach.install.install`, then runs the dry run, the
    run and the second run against it, and writes `into/report.txt`;
  - returns the report and the check failures; prints `Rehearsal passed` or `Rehearsal failed:` with each failure.
- [ ] **Step 1: Write the failing tests:** `test_rehearsal_copies_and_never_touches_the_originals` (fixture digests unchanged; `into/sources` has every source); `test_rehearsal_refuses_a_live_target`; `test_rehearsal_on_real_data_refuses_outside_the_live_checkout` (default manifest, `is_live` patched false: `SystemExit` naming the live checkout, nothing copied, `into` not created); `test_rehearsal_on_the_fixture_passes`; `test_rehearsal_report_file_is_written`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_import_rehearse -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Import: one command rehearses the whole import on copies (#17)`.

## Task 15: The `import_history` flow stage

**Files:**
- Create: `tests/pipeline/stages_importer.py`, `tests/fixtures/pipeline/import/` (a copy of the smallest fixture set: two companies, one touch with a reply, one interview brief)
- Modify: `tests/pipeline/harness.py` (`STREAM_STAGES` and its docstring table, only if they do not yet hold the final map below), `tests/pipeline/test_flow.py` (one invariant, and the agreed-map test only with the map)

**The final wave 2 map** (integration review, section 5). The first wave 2 PR to merge (#14, #16, #17 or #13) adds all
four wave 2 streams, their docstring rows and `test_streams_and_their_stages_are_the_agreed_map` at once, in this
order; the harness skips a stream whose `stages_<stream>` module does not exist yet. A later PR finds the map in place
and adds only its stage file and invariant:

```python
STREAM_STAGES = {
    "importer": ("import_history",),                   # #17, first: history before discovery
    "sources": ("discover", "resolve", "find_boards"),  # #31
    "crawl": ("read_boards",),                         # #21
    "reader": ("read_postings",),                      # #32
    "rules": ("rules",),                               # #30
    "ranking": ("rank",),                              # #6
    "plan": ("plan",),                                 # #15
    "mcp": ("draft",),                                 # #13: Claude drafts after the plan
    "sending": ("send",),                              # #14: only what was approved
    "review": ("needs_you",),                          # #16: last, sweeps what every stage left
}
```

**Interfaces:**
- Produces: `stages_importer.register(harness)` appends `("import_history", ...)`, which runs `build_plan` and
  `apply_plan` on `harness.conn` from the pipeline import fixtures. `@invariant("import_history")`: the two fixture
  companies exist once each; the interview company is `closed` `interview`; its touch has `interview_at` set and a
  `win_seen` row; every `import_record` of the run has an outcome; a second `apply_plan` leaves every table but
  `job_run` unchanged. Counts are over its own fixture companies only.

- [ ] **Step 1: Write the failing test:** the invariant above, plus `test_streams_and_their_stages_are_the_agreed_map` updated to the final map when master does not have it yet.
- [ ] **Step 2: Run** `uv run python -m unittest tests.pipeline.test_flow -v`. Expected: FAIL (no stage).
- [ ] **Step 3: Implement** the stage, and the map change when it is still needed.
- [ ] **Step 4: Run** the full suite. Expected: PASS, the flow test green with every registered stage.
- [ ] **Step 5: Commit** `Tests: the import_history stage runs first in the pipeline flow (#17)`.

## Task 16: Mutation check, docs and the Part A PR

**Files:**
- Modify: `tests/mutation_check.py`, `docs/README.md` (this plan's row), `docs/superpowers/plans/2026-10-19-store-and-rules.md` (one line under "#17 Import" pointing here)

- [ ] **Step 1: Add mutations**, each with the test that must catch it: drop the `held` refusal in `apply_plan` (`test_held_plan_is_refused_before_any_write`); count a skip with any reason as explained (`test_unknown_skip_reason_fails_the_check`); write `no_go` for `visaDenied` (`test_visa_denied_is_hand_evidence_and_an_item_not_a_no_go`); open radar without `mode=ro` (`test_open_radar_is_read_only`); import an `approved` contact as `approved` (`test_approved_contact_becomes_a_draft_not_an_approval`); skip `win_seen` (`test_imported_wins_are_marked_seen`); in `write.py`, set status `removed` on a company that has a `ruled_out` issue (`test_ruled_out_company_stays_visible_with_its_note`); drop `confidence='weak'` from the near-name call (`test_near_name_item_has_weak_confidence_and_basis_import_near_name`); plan a follow-up whose `due_on` is before the import date (`test_follow_up_due_before_the_import_is_not_planned_and_counted`).
- [ ] **Step 2: Run** `uv run python tests/mutation_check.py`. Expected: `missed 0`.
- [ ] **Step 3: Run** the dash check over `outreach/importer`, `tests/test_import_*.py`, `tests/fixtures/import`, the two docs. Expected: no file printed.
- [ ] **Step 4: Commit** `Import: mutation checks and docs (#17)`; open the PR ("Part of #17, Part A"), body listing the Done-when lines below.

**Part A done when:** every task's tests pass; the rehearsal on copies (Runbook R1, Devanshu runs it) prints
`Rehearsal passed` with `Unexplained losses: 0`, `Second run changed nothing`, and the done set containing Crusoe,
Retell and Distyl.

---

## Task 17: Deploy job tables and the launchctl runner

**Files:**
- Create: `outreach/deploy/__init__.py`, `outreach/deploy/jobs.py`, `outreach/deploy/launchctl.py`
- Test: `tests/test_deploy.py`

**Interfaces:**
- Produces:
  - **The one launchd convention** every plan's template follows (#11 Task 23, #14 Task 14, #16 Task 7, #21 Task 13,
    #31 Task 16): file `launchd/<label>.plist.template`; `Label` the job's label; `WorkingDirectory`
    `{{LIVE_CHECKOUT}}`; `ProgramArguments` `{{LIVE_CHECKOUT}}/.venv/bin/python -m <module> <command>` (an absolute
    path: launchd's `PATH` has no `uv`); `StandardOutPath` and `StandardErrorPath` `{{LOG_PATH}}`; no other
    placeholder; no secret; no environment variable that points at the store or the Gmail folder (the defaults are
    the live ones). Each owning plan has a test that renders its template with this task's `render` when
    `outreach.deploy.jobs` imports.
  - `@dataclass(frozen=True) class Job: label: str; template: str | None; plist: str; owner: str; required: bool`.
    `NEW_JOBS`, each template at `launchd/<label>.plist.template`: `com.devanshu.outreach-api` (#11, required),
    `com.devanshu.outreach-sender` (#14, required), `com.devanshu.outreach-crawl` (#21, required; #21 renames its
    file to this name and drops its own install step), `com.devanshu.outreach-sources` (#31 Task 16: each source when
    due, then resolve and board finding; required, because the switch retires the old discovery at S12 and new
    companies must keep arriving; Pending Devanshu Q3), `com.devanshu.outreach-needs-you` (#16; the sweep is
    the only producer of triggers 3 to 5, but it is optional until #16 lands, ruling 2026-10-24), `com.devanshu.outreach-reader-night` (#32),
    `com.devanshu.outreach-prepare` and `com.devanshu.outreach-plan` (#15's two templates). `OLD_JOBS`:
    `com.devanshu.outreach-mail-sender`, `com.devanshu.outreach-mail-server`, `com.devanshu.outreach-dashboard`,
    `com.devanshu.outreach-refresh`. (#5 adds `com.devanshu.jobradar`.)
  - `class Runner(Protocol)`: `bootstrap(plist: Path) -> None`, `bootout(label: str) -> None`, `loaded(label: str) ->
    bool`, `lint(plist: Path) -> None` (raise `DeployError` with the tool's output on failure).
    `SystemRunner(uid: int | None = None)` runs `launchctl bootstrap gui/<uid> <plist>`, `launchctl bootout
    gui/<uid>/<label>`, `launchctl print gui/<uid>/<label>` and `plutil -lint`. Tests never construct it.
  - `render(template_text: str, *, live_root: str, log_path: str) -> str`: fills `{{LIVE_CHECKOUT}}` and
    `{{LOG_PATH}}`; any other `{{...}}` left raises `DeployError`; the result must parse with `plistlib.loads` and its
    `Label` must equal the job's label.

- [ ] **Step 1: Write the failing tests:** `test_render_fills_both_placeholders_and_parses`; `test_unknown_placeholder_is_refused`; `test_label_mismatch_is_refused`; `test_new_and_old_labels_never_overlap`; `test_every_template_named_in_the_repo_renders` (for each `NEW_JOBS` template that exists in `launchd/`, render with temp paths; skip with the owner named for one that does not exist yet); `test_every_template_follows_the_one_convention` (each present template: `WorkingDirectory` is the live checkout, the program is `<live>/.venv/bin/python` with `-m`, both log paths are `{{LOG_PATH}}`, no `EnvironmentVariables` key names `OUTREACH_DB`, `OUTREACH_GMAIL_DIR` or `OUTREACH_MAIL_HOME`); `test_sources_and_needs_you_are_required_jobs`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_deploy -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Deploy: job tables, template rendering and an injectable launchctl (#17)`.

## Task 18: Install, retire, status and rollback

**Files:**
- Create: `outreach/deploy/__main__.py`
- Test: `tests/test_deploy.py` (add)

**Interfaces:**
- Consumes: Task 17; `live_checkout.require_live`.
- Produces `main(argv=None, *, runner: Runner | None = None, agents_dir: Path | None = None, root: str | None = None,
  live_root: str | None = None) -> int` for `uv run python -m outreach.deploy <command>`:
  - `plan`: prints, per new job, the template, the target plist and whether it is present; per old job, the plist and
    whether it is loaded. Writes nothing. Runs anywhere.
  - `install [--skip LABEL ...]`: `require_live("installing the new launchd jobs")` first; refuses when a required
    template is missing (unless skipped, which is refused for required jobs); for each job: render into `agents_dir`
    (default `~/Library/LaunchAgents`), `lint`, `bootstrap`; an existing plist with different content is moved to
    `<name>.before-switch` first. Prints one line per job.
  - `retire-old`: `require_live`; for each old job: `bootout` when loaded, then rename `<plist>` to
    `<plist>.disabled` (never deleted). Idempotent.
  - `status`: one line per new and old label: loaded or not, plist present or `.disabled`.
  - `rollback`: `require_live`; for each new job: `bootout` when loaded and rename its plist to `.disabled`; for each
    old job: rename `.disabled` back and `bootstrap`. Prints the old mailer reminder: `The old sender is paused in
    the old dashboard. Run python -m outreach.importer rollback-check before you unpause it.`

- [ ] **Step 1: Write the failing tests** (a `FakeRunner` recording calls, a temp `agents_dir`, `root` and `live_root` temp folders): `test_install_refuses_outside_the_live_checkout_and_writes_nothing`; `test_install_renders_lints_and_bootstraps_each_present_job`; `test_install_refuses_a_missing_required_template`; `test_install_keeps_a_differing_plist_as_before_switch`; `test_retire_old_boots_out_and_renames_never_deletes`; `test_retire_old_twice_is_a_no_op`; `test_rollback_restores_the_old_jobs_and_disables_the_new`; `test_plan_writes_nothing`; `test_no_command_calls_the_mail_api` (scan `outreach/deploy` for `3002`, `/api/approve`, `/api/pause`: none).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_deploy -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command and the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `Deploy: install, retire the old jobs, status and rollback (#17)`.
- [ ] **Step 6: Open PR B1** ("Part of #17, the deploy tool"). It merges before the rehearsal (R1); #5 branches from master after it.

## Task 19: PR B2, the switch PR: CLAUDE.md, docs and the cold-email skill

**Files:**
- Modify: `CLAUDE.md` ("Live system" table: the new labels and what each runs; the old labels listed as retired with
  their `.disabled` plists; "Deploy": the new platform's steps from this plan's runbook: `uv sync`, `pnpm build`,
  `python -m outreach.install`, `python -m outreach.deploy install`), `docs/README.md`,
  `.claude/skills/cold-email/SKILL.md` (the text #13 Task 16 writes, committed on this branch so the skill flips to
  the MCP tools with the system)

PR B2 is docs only. It rebases last: #14 Task 14 and #13 Task 15 have already added their own CLAUDE.md and
`.claude/settings.json` lines in their own commits, and this task keeps every one of them. The CLAUDE.md edit is its own
commit, shown as a separate diff for Devanshu's review.

- [ ] **Step 1: Edit `CLAUDE.md` and `docs/README.md`.** No em or en dashes.
- [ ] **Step 2: Commit the skill text** from #13 Task 16 (its own commit, `Cold-email skill: drafts go through the MCP tools after the switch (#13, #17)`).
- [ ] **Step 3: Run** the dash check on the three files. Expected: no file printed.
- [ ] **Step 4: Commit** `Switch-over: CLAUDE.md and docs describe the new live system (#17)`; open PR B2 ("Part of #17, Part B2"). It merges on switch day, step S5; reverting it is part of the rollback.

---

## Runbook (Part B). Run on a Sunday, when nothing sends.

Every step below is "Devanshu runs this" unless it says otherwise. Each has a check he can read. "Live" means the live
checkout `~/Desktop/Sept 23 Companies`. If a check fails, stop and roll back (R-back); nothing below deletes data.

### R1. Rehearsal (the Friday before, and again after any fix). Devanshu runs this, live checkout, scratch store.

1. `cd ~/Desktop/'Sept 23 Companies' && git pull --ff-only https://github.com/DevanshuNEU/sept-23-companies.git master && uv sync && uv run python -m unittest discover -s tests -t .`
   Check: the last line says `OK`.
2. In the old dashboard (http://127.0.0.1:3001, "In conversation"), click "Download a backup of your tracker" and move
   the file to `~/Desktop/outreach-scratch/import/`.
   Check: `ls ~/Desktop/outreach-scratch/import/` shows `outreach-backup-<today>.json`.
3. `uv run python -m outreach.importer preflight`
   Check: every source says `found` with a record count; `Unclear sends: 0`.
4. `uv run python -m outreach.importer rehearse`
   Check: the last lines say `Unexplained losses: 0`, `Second run changed nothing`, `Rehearsal passed`; the done set
   lists Crusoe, Retell and Distyl; read the issue list and fix what needs a decision in
   `~/Desktop/outreach-scratch/import/overrides.toml` (`same_company`, `not_same_company`, `person_name`,
   `touch_for`, `done_add`, `done_remove`), then repeat step 4.
5. Optional staging for #12 and #21: point `OUTREACH_DB` at the rehearsal's `outreach.db` and open the new app
   against it (#11's `python -m outreach.api` with that `OUTREACH_DB` on `CONDUCTOR_PORT + 1` of a workspace, as #11's
   Q13 says; never 3001, 3002, 3011, 3013, 5173 or 3100).

### S. Switch day (Sunday)

- **S1. Pause and stop the old mailer.** In the old dashboard, click "Pause sending" (his click; Claude never calls
  `/api/pause`). Then:
  `launchctl bootout gui/$(id -u)/com.devanshu.outreach-mail-sender`
  Do not log out or restart the Mac until S12: the old plist stays on disk until then, and a login would load the old
  sender again (it fails safe, since #14's sender refuses to claim while the old label is loaded, but it costs the day).
  Check: `mail/config.json` shows paused (`grep '"paused"' mail/config.json` prints `"paused": true`; R-back's
  rollback leaves the old sender off until it does); `launchctl print gui/$(id -u)/com.devanshu.outreach-mail-sender`
  says it could not find the service;
  `uv run python -m outreach.importer preflight` says `Unclear sends: 0`. If not: wait 10 minutes after the last send
  attempt (the old sender settles an unclear send only 10 minutes after it, `NOT_SENT_AFTER_MIN`), then run
  `python3 mail/sender.py --dry-run` once in the live checkout (his step: it reads Gmail and saves contacts), then
  repeat the check.
- **S2. Final tracker export.** Repeat R1 step 2.
  Check: the newest backup file has today's date.
- **S3. Stop the rest of the old system.**
  `launchctl bootout gui/$(id -u)/com.devanshu.outreach-mail-server`, `launchctl bootout
  gui/$(id -u)/com.devanshu.outreach-refresh`, and Job Radar per the #5 plan step 1.
  Check: `uv run python -m outreach.deploy status` shows the three as not loaded.
- **S4. Backup.** `mkdir -p ~/Library/Application\ Support/outreach/switch-backup-<date>` and copy `mail/contacts.json`,
  `mail/config.json`, `mail/send.log`, `dashboard/data.js`, the tracker backup and the overrides file into it;
  `sqlite3 -readonly ~/Desktop/DevVault/job-search/pipeline/radar.db ".backup '<that folder>/radar.db'"` (read
  only, so the archive gets no `-wal` or `-shm` file and is never written, as #5 requires).
  Check: `shasum -a 256 <that folder>/*` lists every file.
- **S5. Merge PR B2** (Task 19: CLAUDE.md, docs and the cold-email skill, docs only) and pull: `git pull --ff-only https://github.com/DevanshuNEU/sept-23-companies.git master && uv sync && uv run python -m unittest discover -s tests -t .`
  Check: `OK`, and `git log -1` names PR B2.
- **S6. Install the new store.** `uv run python -m outreach.install`
  Check: it prints the store path under `~/Library/Application Support/outreach/`, `store at version 3` (or the
  latest), and a ruleset line.
- **S7. Import, dry run.** `uv run python -m outreach.importer dry-run`
  Check: `Unexplained losses: 0`; the counts match the last rehearsal plus what changed since (a new tracker export,
  sends since Friday).
- **S8. Import.** `uv run python -m outreach.importer run`
  Check: `Unexplained losses: 0`, `Second run changed nothing`, the done set as in S7.
- **S9. Reconcile Sent.** `uv run python -m outreach.importer reconcile`
  Check: `Missing: 0`. After S11, #14's sender also checks each imported sent message against Gmail by its Gmail id
  (`verify_imported`, #14 Task 10); its status shows `imported_unverified` until every one is checked, and it must
  reach 0 with no `import_not_in_sent`.
- **S10. Build the web app.** `cd web && pnpm install --frozen-lockfile && pnpm build && cd ..`
  Check: `ls web/dist/index.html` exists.
- **S11. Install the new jobs.** `uv run python -m outreach.api --check` (it must print ok before the API job is
  installed: it opens the store S6 made, and install also refuses while that store is missing), then
  `uv run python -m outreach.deploy plan`, read it, then `uv run python -m outreach.deploy install`
  Check: `uv run python -m outreach.deploy status` shows the four required labels loaded
  (`com.devanshu.outreach-api`, `-sender`, `-crawl` and `-sources`); each optional label (`-needs-you`,
  `-reader-night`, `-prepare`, `-plan`) is loaded only when its template is in the repo, and until #16, #32 and #15
  land, install prints it as skipped and status shows it not loaded with its plist missing (ruling 2026-10-24);
  http://127.0.0.1:3100 opens Today and the health view lists the jobs. Register the MCP server with the command #13
  puts in CLAUDE.md (`python -m outreach.mcp --print-registration` prints it; no launchd job). The cold-email skill
  merged at S5 already drafts through it.
- **S12. Retire the old jobs.** `uv run python -m outreach.deploy retire-old`, then Job Radar per the #5 plan step 3.
  Check: `status` shows the old labels not loaded and their plists as `.disabled`; http://127.0.0.1:3001 no longer
  answers.
- **S13. Approve Monday's emails.** In the new app's Outbox, read and approve the drafts he wants sent (imported old
  approvals arrive as drafts). His clicks only.
  Check: the Outbox shows the approved count he expects; the sender's status line shows the ramp level.
- **S14. Monday check** (after 09:00 New York). Check: the health view shows the 08:00 `plan` run ok and `sender`
  runs; the first sends appear in the Outbox's sent list; `mail/send.log` has no new lines. Then close #17.

### R-back. Rollback (any time, as often as needed)

1. `uv run python -m outreach.deploy rollback` (boots out the new jobs, renames their plists `.disabled`, restores and
   loads the old ones; Job Radar per the #5 plan's rollback).
2. `git revert <the PR B2 merge commit>` and push through a PR; in the live checkout pull it.
3. `uv run python -m outreach.importer rollback-check --since <switch time>`; in the old dashboard, skip or fix each
   person it lists, then click "Resume sending" there.
   Check: `deploy status` shows the old labels loaded; http://127.0.0.1:3001 answers; `rollback-check` says `Nothing to
   check` or each listed person is handled.
4. The new store is never deleted. For the next attempt, rename it to `outreach.db.attempt-<n>` (with its `-wal`
   and `-shm` files), then start again at S1; S6 creates a fresh store.

---

## Out of scope, with reasons

- **Changing the old dashboard.** Its backup button already exports every tracker field (settled, integration review
  A1); an edit to `dashboard/` risks the live :3001 page for no gain.
- **Reading Gmail during the import.** Sent and reply state comes from `contacts.json` and `send.log`; the Gmail Sent
  check is #14's first run. The importer never opens the Gmail token.
- **Writing the discovery job.** New companies after the switch come from #31's source modules (settled, A11). #31
  Task 16 ships `launchd/com.devanshu.outreach-sources.plist.template` and `python -m outreach.sources run-due` (not `tick`; (from #31's final review, 2026-10-22)); this plan
  only lists that job as required in `NEW_JOBS` (Task 17) and installs it at S11, before the old refresh retires at
  S12.
- **Writing the cold-email skill text.** #13 Task 16 writes it; this plan commits it in PR B2 (Task 19) so the skill
  flips with the system at S5.
- **Old `voice/authored.jsonl`, `wins.md`, `playbook.md`.** Not touch records; the drafting skill reads them where
  they are.
- **Radar's labels as the reader's labeled set.** A different rubric (`apply`, `maybe`, `no`); counted and kept in
  `radar.db` (design question 1).

## Design questions a builder could not settle alone

1. **Radar labels (`label` table).** Counted, not imported. Default: as written; #32 can sample them later.
2. **Unknown "named a live role".** The schema's `roleNamed` is true or false; unknown is written false, with
   `roleNamedKnown false` in the touch's import event. Default: as written.

## Pending Devanshu

Question numbers are the integration review's (section 10). Each line names the default this plan builds and where it
lives, so a later answer is a small edit there.

- **Q3. Schedule for finding new companies after the switch.** Builds: `com.devanshu.outreach-sources` (#31 Task 16)
  is a required job in `NEW_JOBS` (Task 17), installed at S11. Where: Task 17 `NEW_JOBS`; the job itself is #31's.
- **Q4. The old dashboard's 17 "ruled out" entries (about 30 companies).** Builds: each name imported visible, the old
  reason as a company note and one Needs-you item each; he removes the ones that still hold. Where: Task 10 (`data_js`
  `RULED_OUT`), Task 11 step 1.
- **Q5. Done set.** Builds: every company with an interview or later, including ones that ended in rejection, closed
  as done; `done_remove` in the overrides file takes any out during the rehearsal. Where: Task 10 (the done set).
- **Q6. Old "no sponsorship" verdicts (22 companies).** Builds: hand evidence `sponsorship no` plus one Needs-you item
  each (`old_no_sponsorship`), shown until he confirms a no-go. Where: Task 10 (`visaDenied`).
- **Q14. "Routed" and "LinkedIn referral" replies.** Builds: an interested reply plus a referral. The other choice is a
  reply only. Where: Task 7 `note_reply` and `curated_outcome`.
- **Q18. Workday and Oracle radar boards.** Builds: frozen only with size evidence over 300; Amazon, Apple, Google and
  Eightfold always frozen, so a small company on Workday keeps being read by #21. Where: Task 7 `FROZEN_ATS`,
  `MAYBE_FROZEN_ATS`; Task 10 `radar_db`.
- **Q25. Old email touches, April to September.** Builds: closed "no reply", no follow-ups, outcomes empty. Where:
  Task 10 (historic email touch).

Answered, not open (integration review "Already answered"): the export button (A1: the existing backup button; edit
#9's Build line), old approvals (A5: imported as drafts he approves again), the old "well over 500", cleared, agency
and big-by-filings verdicts (A10: size evidence and hidden, no-go, Needs you), which discovery runs after the switch
(A11: source modules).

## Cross-plan effects

- **#31 (sources and identity):** the importer calls `resolve_candidate(..., source_name='import', actor='import')`
  (it takes `actor`), `raise_merge_candidate(..., 'import_near_name', detail, confidence='weak')` (an unlisted basis
  passes `confidence=`, so no #31 change), `register_source(conn, 'import', 'import-1', run_id)` and
  `add_board(..., via='import', matched_on='import', found_by='import', actor='import')`. Part A waits for #31 Tasks 7, 8 and 10. Part B
  needs #31 Task 16's template `com.devanshu.outreach-sources` (placeholders `{{LIVE_CHECKOUT}}` and `{{LOG_PATH}}`
  only), which this plan's deploy tool installs as required.
- **#11 (API):** Task 9 types `build_context(lane: str | None)` with `test_context_lane_may_be_null` (imported touches
  have no lane; the schema allows JSON null). Task 21 owns `ImportSource` in `web/src/api/types.ts` and the API's
  `importSource`, which gain `interview_prep`, `curated_touches`, `send_log`. `importSource` is read from `source_ref`
  `import:<source>:<key>` on `no_go` and evidence and from the `imported` event's `importSource` on touches.
  `base_rates` (median days to reply) should skip touches whose import event lists `estimatedDates`.
  `DoneCompany.imported` and `howStarted` can read the touch's `imported` event. The sweeper will mark old imported
  touches quiet at day 21; that is expected. Task 19's release-hold action must be on master before the switch (one
  "Not now" reply would otherwise hold a company for good). Part A waits for #11 Task 9.
- **#14 (sender):** its plan matches this one: it never plans a follow-up for an imported touch (this plan plans only
  the ones due on or after the import date, Task 10), skips `@outreach.invalid` Message-IDs, checks imported sent mail
  with `verify_imported`, refuses to send while the old job is loaded, and ships
  `launchd/com.devanshu.outreach-sender.plist.template` under the one convention (Task 17). This plan consumes its
  `load_config` and `add_business_days` (Task 2 there) and needs its migration 0002 (Task 1 there) on master, since
  0003 follows it.
- **#21 (crawler):** its template is renamed `launchd/com.devanshu.outreach-crawl.plist.template` under the one
  convention (no hard-coded checkout or Python path), and its own Deploy step 4 (copy and bootstrap) is dropped: S11
  installs it, and before the switch there is no live store. Imported postings use `<ats>:<token>:<native_id>` and
  `posting_body.body_sha256` filled, as #21 expects.
- **#15 (people plan):** imported LinkedIn history reaches `outreach_touch` and `linkedin_log`, emailed people keep
  their `touch` rows (its Hand-off asked for both). Placeholder people always have a touch, so they are never
  candidates.
- **#30 harness:** the final wave 2 `STREAM_STAGES` map (Task 15) is added once, by whichever of #14, #16, #17 and #13
  merges first; the others add only their stage file and invariant.
- **#16 (Needs you):** this plan raises every import item through `outreach.review.raise_item(raised_by='import',
  actor='import')` (its PR 1 merges before Part A's Task 11) and installs `com.devanshu.outreach-needs-you` (optional
  until #16 lands, ruling 2026-10-24). Every import item's key starts `import:`, so the Needs-you view can group them (M11).
- **#13 (MCP):** the switch needs #13 merged: its Task 16 writes the cold-email skill text that PR B2 carries, and S11
  registers the server in the live checkout.
- **#5 (retire Job Radar):** branches after PR B1 (Tasks 17 and 18) merges; S3, S8 and S12 call its steps.
- **#9:** the Build line's "Export tracking" button is edited to name the existing backup button (A1).
- **Schema:** migration `0003_import_history.sql` (rebuilds `import_record`), version 3, after #14's 0002.

## Self-review

- **Spec coverage.** Issue part A sources: data.js (Tasks 5, 10), localStorage (Tasks 3, 10), contacts.json (3, 10),
  radar.db with every table (6, 10, 12), devboard (3, 10), contacted.txt and company files (3, 4, 10), and the
  2026-10-21 comment's interview-prep companies and curated touches (4, 9, 10). Verbatim records: `import_record` raw
  (11). Runs against any `OUTREACH_DB` (13, 14). Part B: done set (10), stop old sender and server (S1, S3), final
  export (S2), import with the count report and zero unexplained losses (12, S8), second run changes nothing (13,
  S8), reconcile Sent (13, S9), new services (17, 18, S11), retire old dashboard, refresh and its publish step (S12),
  Job Radar (#5 plan), Sunday (runbook). Deploy steps for the new platform (Task 19, S6, S10, S11). Done when:
  rehearsal (R1) and Monday (S14). Devanshu's rulings: interview history (Tasks 4, 9, 10), the write helpers (11),
  dry run first (13), reversible (18, R-back), live steps marked with checks (runbook), ambiguous to review items
  (10, 11).
- **Integration review.** B1 migration 0003 (Task 1, S6); B5 three PRs, the deploy tool before the rehearsal (Branches,
  Tasks 18, 19, S5); B6 `confidence='weak'` (Task 11); B7 `RULED_OUT` kept visible (Tasks 2, 10, 11, 16); B8 the
  sources job required (Task 17, S11); B9 the skill in PR B2 (Task 19); I5 one launchd convention (Task 17); I17
  follow-ups only when due after the import (Tasks 10, 12); S4, S5, S9, S10 runbook wording (R1, S1, S4); S6
  `require_live` for a rehearsal on real data (Task 14); M2, M4, M6, M8, M9, M11, M13 in their tasks and Cross-plan
  effects. Every review item goes through `outreach.review.raise_item`; nothing writes `review_item` inline.
- **Steps.** Each code step names the function, file and pinned values; mapping tables are the decisions. Task 11's
  order list decides write order and transaction scope, which the tests alone do not.
- **Names.** `SourceRecord`, `Manifest`, `Overrides`, `ImportPlan`, `CompanyIn`, `PersonIn`, `TouchIn`, `OutcomeIn`,
  `EmailIn`, `ApplicationIn`, `BoardIn`, `PostingIn`, `IssueIn`, `RecordOutcome`, `build_plan`, `apply_plan`,
  `count_report`, `check_report`, `render_report`, `rehearse`, `Job`, `Runner`, `render` are each defined once in the
  task that produces them. Foundation and #31 and #11 names are used as merged or planned.
- **Review Focus.** Each line has its test in the owning task.
- **Proportion.** No function bodies; the mapping tables and the write order are the only long parts.
