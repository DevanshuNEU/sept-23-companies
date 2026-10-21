# Sources and identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Find every company Devanshu could reach from the DOL LCA files, Form D, HN, stapply lists and by-hand adds, store one record per company with its aliases and evidence, and find a board or a watched careers page for each, all rerunnable and never merging on a shared key.

**Architecture:** Each source is a module in `outreach/sources/` that yields `Candidate`s (or, for Form D, only enriches companies already stored). `outreach/identity.py` resolves each candidate to one company or raises a `merge_candidate` review item, and three jobs (`discover`, `resolve`, `find_boards`, each a `job_run`) drive them. Every fetch goes through an injected `HttpClient`, so tests run on fixture files and a fake client.

**Tech Stack:** Python 3.14 (Homebrew), standard library only (`zipfile` + `xml.etree.ElementTree` for xlsx, `csv`, `tomllib`, `html.parser`, `urllib`), SQLite STRICT tables, `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-20-opportunity-pipeline-design.md` (sections 3, 4, 5.1, 5.2, 5.3, 5.4, 12, 14, 17). Foundation: `docs/superpowers/plans/2026-10-21-pipeline-foundation.md` (its tables, `Candidate`, `Source`, `add_alias`, `mark_shared_key`, `is_shared_key` are used exactly as named, never redefined). Evidence: `docs/research/2026-10-20-lca-small-junior-employers.md`, `docs/research/2026-10-20-company-discovery-and-job-data.md` (sections 3, 5, 7).

**Shared formats and harness (foundation Tasks 1 and 6):** board tokens and aliases follow the foundation's "Shared formats" section exactly (see Task 10). Every `Source` here gets a `check_source` test from `tests/contracts.py`, and this stream registers the `discover`, `resolve` and `find_boards` stages in `tests/pipeline/` (Task 16).

**Branch and issue:** `DevanshuNEU/sources-and-identity`, from the foundation branch once #30 is merged. Issue #31. The PR body says it closes #31.

**Reference code (read, never import):** `~/Desktop/outreach-scratch/recon/lca/analyze.py` (LCA filters, staffing rules, tiers), `~/Desktop/outreach-scratch/interim-list/step1_universe.py` (stapply matching), `~/Desktop/outreach-scratch/research/work/careers_probe.py` (ATS host patterns), `.../sec_fein.py` (EDGAR `ein`). The product code is new, tested, standard library only.

## Controller rulings (2026-10-21). These override the task text below where they disagree.

1. **No migration in this plan.** The work tier (`work_tier` table), `company_post`, the domain and source-ref
   indexes, the `'source'` event actor, the `record_evidence(actor=...)` keyword and the widened `FACTS` and
   `SOURCE_KINDS` all move into #30's Task 1 (one schema owner). Ignore every mention of `0002_work_tier.sql`;
   Task 1 here keeps only what is not schema.
2. **HN posts** are stored in `company_post` (url, posted_at, text, asks_email), not only as evidence refs.
3. **stapply name-only board matches** create the board with no confirmation step. #21's first read checks it:
   a board whose org name differs from the company's names, or a small LCA filer (25 LCAs or fewer) whose board
   lists over 300 jobs, raises a `flagged` review item "board match doubtful", and ranking ignores that board's
   postings for the live-role signal until he confirms. About 500 confirmations up front would cost him more.
4. **Evidence from sources** is written with `record_evidence(..., actor='source')`.

## Global Constraints

- Standard library only in `outreach/`. No pandas, no calamine, no requests.
- Tests never touch the network, the live store or a real crawl. A fixture file or `tests/fakes.py::FakeClient` stands in. `tests/__init__.py` already points every test at scratch settings; this plan adds `OUTREACH_SOURCES_DIR` to it (Task 2).
- Merge automatically only on the same domain, the same non-shared FEIN, or an exact normalized name plus the same state (design 5.2). Keys seen on more than one LCA employer are recorded with `mark_shared_key` and never merge. A subsidiary is its own company and never inherits a parent's size.
- Anything weaker raises a `merge_candidate` review item. Nothing is hidden on a guess: unknown size is shown and scored neutral, and no source here writes `size` or `headcount` evidence (design 5.4).
- Staffing-flagged LCA employers are stored with `staffing_risk`, never dropped (design 5.1). Large filers (over 25 LCAs in the fiscal year) are not imported.
- Writers use `INSERT ... ON CONFLICT DO NOTHING`, never `INSERT OR IGNORE` or `INSERT OR REPLACE`. A CHECK failure is counted and reported, never swallowed.
- Timestamps are canonical UTC; every table is STRICT; every derived row names its version (source version in the `source` table and the job summary).
- Error is not empty: a fetch or parse failure is never stored as "no board", "no careers page" or "no employers".
- Board rows follow the foundation's Shared formats: `board.token` is the ATS slug lower case (Greenhouse, Lever, Ashby and the rest), Workday `<tenant>.wd<N>/<site>`, Oracle `<host>/<siteNumber>`, iCIMS the board host; the board's alias has `source='ats'` and key `<ats>:<token>`. `job_run.job` values written here: `discover`, `resolve`, `find_boards`. This plan never touches `read_queue`.
- Requests: a declared User-Agent; DOL needs browser-like headers; SEC at most 10 requests a second; careers pages at most 2 requests per company, 1.5 seconds apart per host.
- No em dashes or en dashes in code, comments, docs or commit messages.
- Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

Failure modes the spec implies that no single happy-path test covers. Each has a named test in the owning task.

1. A shared key arriving from a different source than the one that marked it (a Form D `ein` equal to a PEO's FEIN; an HN link on a parent's domain) never merges. Task 7 `test_shared_fein_never_merges`, Task 11 `test_shared_ein_never_attaches`, Task 12 `test_parent_domain_post_never_attaches`.
2. A weaker match makes its own company and exactly one item, and a re-run or next month's HN post makes neither a second company nor a second item. Task 7 `test_same_name_other_state_one_company_one_item`, Task 12 `test_next_month_post_is_not_a_second_company`.
3. LCA file oddities: renamed headers (`H-1B_DEPENDENT` vs `H_1B_DEPENDENT`), blank xlsx cells, dates as serial numbers, FEIN `000000000`, the same CASE_NUMBER in two quarters, a truncated xlsx. A file that parses to nothing raises; it never reads as "no employers". Task 3, Task 4.
4. Unknown size never turns into a size or a hide: a freshly discovered company has no `size` or `headcount` row, and the staffing-excluded ones are stored. Task 9 `test_no_source_writes_size_evidence`.
5. A fetch that fails (503, timeout, 429) never records "no careers page" or "no board"; only a definite answer sets the recheck marker. Task 15 `test_error_leaves_no_marker`.

## File Structure

| File | Responsibility |
|---|---|
| `outreach/rules/facts.py` | Modify: widen `FACTS` and `SOURCE_KINDS` to the foundation schema, validate the new values (Task 1) |
| `outreach/store/migrations/0002_work_tier.sql` | Create: `company_work_tier`, `evidence_domain` and `company_source_ref` indexes (Task 1) |
| `outreach/net.py` | Create: `Response`, `FetchError`, `HttpClient`, `UrllibClient`, `PoliteClient` |
| `outreach/sources/paths.py` | Create: `sources_dir()`, the cache folder for downloaded files |
| `outreach/sources/tabular.py` | Create: `read_rows` for CSV and xlsx |
| `outreach/sources/keys.py` | Create: `normalize_fein`, `registrable_domain`, `classify_domain` |
| `outreach/sources/capabilities.py` | Create: `SharedKey`, `SharedKeySource`, `TieredSource` protocols |
| `outreach/sources/lca.py` | Create: LCA aggregation (`load_employers`) and `LcaSource` |
| `outreach/sources/lca_rules.py`, `lca.toml` | Create: staffing filter, cap-exempt, tiers, `classify` |
| `outreach/identity.py` | Create: `resolve_candidate`, `find_company`, `resolve_store`, merge items |
| `outreach/sources/tiers.py` | Create: `set_work_tier`, `effective_tier`, `promote` |
| `outreach/sources/jobs.py` | Create: `run_discover`, `run_resolve`, `run_find_boards`, `register_source` |
| `outreach/sources/boards.py`, `ats_detect.py` | Create: `add_board`, `detect_ats` |
| `outreach/sources/formd.py`, `hn.py`, `byhand.py` | Create: the three remaining sources |
| `outreach/sources/stapply.py` | Create: `StapplyDirectory` (board lists by name and host) |
| `outreach/sources/careers.py` | Create: `CareersChecker` (careers page to boards or a watched page) |
| `outreach/sources/__main__.py` | Create: CLI (`fetch`, `discover`, `resolve`, `find-boards`, `add`) |
| `tests/fixtures/{lca,formd,hn,stapply,careers}/` | Create: small fixture files |
| `tests/fakes.py`, `tests/xlsxbuild.py` | Create: `FakeClient`, a tiny xlsx writer for tests |
| `tests/pipeline/sources_stages.py`, `tests/pipeline/test_flow.py`, `tests/fixtures/pipeline/` | Modify/Create: register the `discover`, `resolve`, `find_boards` stages and their invariants in the foundation's harness (Task 16) |
| `tests/test_*.py` | One test module per task, named in each task |

---

## Task 1: Evidence vocabulary and migration 0002

**Files:**
- Modify: `outreach/rules/facts.py`
- Create: `outreach/store/migrations/0002_work_tier.sql`
- Test: `tests/test_facts_vocab.py`, `tests/test_store_schema.py`

**Interfaces:**
- Consumes: the foundation's 0001 (new facts, source kinds, `company_source`).
- Produces:
  - `FACTS` gains `lca`, `cap_exempt`, `staffing_risk`, `funding`, `headcount`; `SOURCE_KINDS` gains `stapply`, `commoncrawl`, `hn`, `simplify`. `EvidenceDraft` validates the values the schema CHECKs: `lca` JSON with exactly the keys `fy, software, jr, level1, newemp_jr`; `cap_exempt` in `likely|confirmed|no`; `staffing_risk` in `low|medium|high`; `funding` JSON with keys `filed_on, amount_usd, kind`; `headcount` takes `num_low` like `size`.
  - Table `company_work_tier(company_id PK REFERENCES company, tier INTEGER CHECK IN (1,2,3), reason TEXT NOT NULL, promoted INTEGER DEFAULT 0 CHECK IN (0,1), source_version TEXT NOT NULL, updated_at)`.
  - `CREATE INDEX evidence_domain ON evidence(value) WHERE fact = 'domain'` and `CREATE INDEX company_source_ref ON company_source(source_name, source_ref)`.

Why a new migration: the foundation owns 0001 and has no home for the work tier or a promote flag, and identity looks domains up by value (18,000 candidates against 100,000 evidence rows needs the index). The number is the next free one at merge time; renumber on rebase.

- [ ] **Step 1: Check the gap.** `grep -n "FACTS =" outreach/rules/facts.py`. The foundation plan re-exports `EvidenceDraft` but lists no change to `facts.py`, whose `FACTS` and `SOURCE_KINDS` tuples mirror the CHECKs and would reject `lca`. If the foundation branch already widened them, skip steps 3 and 4 for `facts.py`.
- [ ] **Step 2: Write the failing tests.**
  - `test_vocab_matches_schema` (in `test_facts_vocab.py`): reads the `evidence` CREATE statement from `sqlite_master` of a fresh store and asserts every `FACTS` and `SOURCE_KINDS` value appears in it, so the tuples cannot drift again.
  - `test_draft_accepts_lca_json`: `EvidenceDraft("lca", '{"fy":2025,"software":5,"jr":3,"level1":2,"newemp_jr":2}', "confirmed", "lca")` constructs; the same with a missing key raises `ValueError`.
  - `test_draft_rejects_bad_cap_exempt_and_staffing_values`: `cap_exempt` value `maybe` and `staffing_risk` value `none` raise.
  - `test_draft_accepts_hn_and_stapply_source_kinds`.
  - `test_headcount_needs_num_low`.
  - In `test_store_schema.py`: `test_work_tier_rejects_tier_4`, `test_work_tier_is_one_row_per_company`, `test_domain_lookup_uses_evidence_domain_index` and `test_company_source_lookup_uses_ref_index` (both assert the index name appears in `EXPLAIN QUERY PLAN` output).
- [ ] **Step 3: Run** `uv run python -m unittest tests.test_facts_vocab tests.test_store_schema -v`. Expected: the new tests FAIL.
- [ ] **Step 4: Implement** the widened tuples and value checks in `facts.py` (keep the existing style: problems list, one `ValueError`), and write `0002_work_tier.sql` in the 0001 file's style (comments, STRICT, canonical timestamp CHECK).
- [ ] **Step 5: Run** the full suite `uv run python -m unittest discover -s tests -t .` and `uv run python tests/mutation_check.py`. Expected: all OK. `facts.py` is in `RULE_FILES`, so `ruleset_version()` changes; the version tests derive it, so nothing else should need an edit. If a test pins a literal version, update it.
- [ ] **Step 6: Commit** `Store and facts: evidence vocabulary and the work tier table for sources (#31)`.

## Task 2: Network layer and fakes

**Files:**
- Create: `outreach/net.py`, `outreach/sources/paths.py`, `tests/fakes.py`
- Modify: `tests/__init__.py` (scratch `OUTREACH_SOURCES_DIR`)
- Test: `tests/test_net.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Response: status: int; url: str; body: bytes; headers: dict[str, str]`; `Response.text() -> str` (UTF-8, errors replaced); `Response.json() -> object`.
  - `class FetchError(Exception)`: transport failure only (DNS, timeout, TLS, reset). Any HTTP status, including 404 and 503, is a `Response`.
  - `class HttpClient(Protocol): def get(self, url: str, *, headers: dict[str, str] | None = None) -> Response; def download(self, url: str, dest: Path, *, headers: dict[str, str] | None = None) -> int`.
  - `class UrllibClient(profile: Literal["declared", "browser"] = "declared", timeout: float = 30.0)`. `declared` sends `User-Agent: outreach-pipeline (personal job search; chicholikar.d@northeastern.edu)` unless `OUTREACH_USER_AGENT` is set. `browser` sends the Chrome header set from `recon/lca/dl.sh` (Accept, Accept-Language, `sec-ch-ua*`, `Sec-Fetch-*`, `Upgrade-Insecure-Requests`) and keeps cookies in a `http.cookiejar.CookieJar` (DOL returns 403 to bare clients). `download` streams to `dest.part`, then renames, so a failure leaves no `dest`.
  - `class PoliteClient(inner: HttpClient, *, min_interval_s: float = 1.5, per_host: dict[str, float] | None = None, clock=time.monotonic, sleep=time.sleep)`: waits so two requests to one host are at least the interval apart (`data.sec.gov` and `www.sec.gov` get 0.15 from the default `per_host`, which keeps SEC under 10 a second).
  - `sources_dir() -> Path`: `OUTREACH_SOURCES_DIR` or `~/Library/Application Support/outreach/sources`.
  - `FakeClient(routes: dict[str, Response | bytes | str | Exception | Path])` in `tests/fakes.py`: a `bytes`, `str` or `Path` value is a 200 body; an unknown URL raises `UnexpectedRequest(AssertionError)` so a test can never pass by accident; `requests` records every URL asked.

- [ ] **Step 1: Write the failing tests.**
  - `test_polite_client_spaces_requests_per_host`: fake clock and sleep; two gets to `a.com` sleep 1.5 in total, a get to `b.com` sleeps 0.
  - `test_polite_client_sec_hosts_use_short_interval`: two gets to `data.sec.gov` sleep 0.15.
  - `test_fake_client_unknown_url_fails_the_test`.
  - `test_fake_client_exception_route_raises_fetch_error`.
  - `test_response_text_replaces_bad_bytes`.
  - `test_download_leaves_no_file_on_failure` (FakeClient that raises mid-download; `dest` does not exist, no `.part` left).
  - `test_browser_profile_sends_sec_fetch_headers` (builds the `urllib.request.Request` without sending it; asserts `Sec-Fetch-Mode` and a Chrome `User-Agent`).
  - `test_declared_profile_user_agent_has_contact`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_net -v`. Expected: FAIL (module not found).
- [ ] **Step 3: Implement** the above. `UrllibClient` has a private `_request(url, headers) -> urllib.request.Request` the header tests call.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Net: injected HTTP client, polite wrapper and fakes (#31)`.

## Task 3: Tabular reader (CSV and xlsx)

**Files:**
- Create: `outreach/sources/tabular.py`, `tests/xlsxbuild.py`
- Test: `tests/test_tabular.py`

**Decision (xlsx):** read it with `zipfile` plus `xml.etree.ElementTree.iterparse`, in the product code. Measured on 2026-10-21 on the real `FY2025_Q4.xlsx` (79 MB zipped, 501 MB sheet XML, 446,803 shared strings): shared strings parse in 0.9 s and 20,000 rows in 4.4 s, so a 437,000-row cumulative file takes about 2 minutes. DOL publishes xlsx only, and the unattended launchd job runs Homebrew Python with no extras, so a converter (python-calamine, as the spike used) would add a dependency to the live path. The reader is about 80 lines and streams rows, keeping only the wanted columns. A CSV export of the same file is read by the same function, so if Task 16's measured run is too slow or too large (limits there), converting once with a dev-time tool costs no code change.

**Interfaces:**
- Produces:
  - `read_rows(path: Path, wanted: Sequence[str]) -> Iterator[dict[str, str]]`: one dict per data row with exactly the wanted keys (blank or absent cell is `''`; numbers come back as their text, so a date cell is its serial such as `'45930'`). Headers are normalized by `normalize_header(h) -> str` (strip, upper case, `-` to `_`), so `H-1B_DEPENDENT` and `H_1B_DEPENDENT` are one column. Format by suffix: `.csv` via `csv`, `.xlsx` via the stdlib reader. Handles shared strings (including rich-text runs), `t="inlineStr"`, and rows that omit blank cells (columns are placed by the cell reference such as `AB12`).
  - `class TabularError(Exception)`, `class MissingColumns(TabularError)` (names the missing wanted columns), `class EmptyTable(TabularError)` (header but no data rows). A truncated or corrupt zip raises `TabularError`.

- [ ] **Step 1: Write `tests/xlsxbuild.py`:** `write_xlsx(path, header, rows, *, inline=False, sparse=False)` builds a valid minimal workbook with `zipfile` (`[Content_Types].xml`, `xl/workbook.xml`, `xl/_rels/workbook.xml.rels`, `xl/worksheets/sheet1.xml`, `xl/sharedStrings.xml`); `sparse=True` omits empty cells the way Excel does; numeric values are written as `<v>` without `t`.
- [ ] **Step 2: Write the failing tests.**
  - `test_xlsx_roundtrip`, `test_xlsx_sparse_rows_place_cells_by_reference` (a row with only column A and column C returns B as `''`), `test_inline_strings`, `test_rich_text_shared_string`.
  - `test_serial_date_is_returned_as_text` (`'45930'`).
  - `test_header_variants_are_one_column`.
  - `test_csv_and_xlsx_agree` (same header and rows written both ways give equal output).
  - `test_only_wanted_columns_returned`.
  - `test_missing_wanted_column_raises_naming_it` (`MissingColumns` message contains `SOC_CODE`).
  - `test_zero_data_rows_raises_empty_table` (Review Focus 3).
  - `test_truncated_zip_raises_tabular_error` (first 100 bytes of a real workbook).
- [ ] **Step 3: Run** `uv run python -m unittest tests.test_tabular -v`. Expected: FAIL.
- [ ] **Step 4: Implement** `read_rows`: load `sharedStrings.xml` once (join every `<t>` under an `<si>`), stream `sheet1.xml` (the first sheet named in `workbook.xml`), `el.clear()` after each `<row>`, map column letters to header positions.
- [ ] **Step 5: Run** the same command. Expected: PASS.
- [ ] **Step 6: Commit** `Sources: CSV and streaming xlsx reader (#31)`.

## Task 4: Keys, domains and LCA aggregation

**Files:**
- Create: `outreach/sources/keys.py`, `outreach/sources/capabilities.py`, `outreach/sources/lca.py` (aggregation half), `tests/fixtures/lca/LCA_Disclosure_Data_FY2025_Q1.csv`, `tests/fixtures/lca/LCA_Disclosure_Data_FY2025_Q2.csv`
- Test: `tests/test_keys.py`, `tests/test_lca_aggregate.py`

**Interfaces:**
- Produces in `keys.py`:
  - `normalize_fein(raw: str | None) -> str | None`: digits only; `None` unless exactly 9 digits and not all zeros (`'46-3517853'` and `'463517853'` both give `'463517853'`; `'000000000'` gives `None`).
  - `registrable_domain(host_or_email: str) -> str | None`: lower case, drops a leading `www.` and anything before an `@`, keeps the last two labels, or three when the second-to-last is in `co, com, org, net, ac, gov, edu` and the last is two letters (`eng.nimbus.com` gives `nimbus.com`, `acme.co.uk` gives `acme.co.uk`); `None` without a dot.
  - `classify_domain(domain: str) -> Literal["company", "free", "peo", "platform"]`: `free` (gmail.com, outlook.com, yahoo.com, hotmail.com, icloud.com, proton.me, aol.com and similar), `peo` (trinet.com, deel.com, justworks.com, insperity.com, velocityglobal.com, adp.com, gusto.com, rippling.com, oysterhr.com, remote.com), `platform` (github.com, linkedin.com, ycombinator.com, notion.site, medium.com, x.com, twitter.com, google.com, lever.co, greenhouse.io, ashbyhq.com, workable.com, bamboohr.com and the other ATS hosts), else `company`.
- Produces in `capabilities.py`: `@dataclass(frozen=True) class SharedKey: kind: Literal["fein","domain"]; value: str; reason: str`; `class SharedKeySource(Protocol): def shared_keys(self) -> list[SharedKey]`; `class TieredSource(Protocol): def work_tier(self, source_ref: str) -> tuple[int, str] | None`.
- Produces in `lca.py`:
  - `fiscal_year_of(path: Path) -> int`: `FY(\d{4})` from the file name, `ValueError` without one.
  - `norm_employer(name: str) -> str`: the research's `norm_name` (lower case, `&` to `and`, parentheses and `dba` tails dropped, punctuation and the words incorporated, inc, llc, l l c, llp, lp, ltd, limited, corp, corporation, co, company, plc, pllc, pc, pbc, gmbh, ag, sa, bv, nv, pty, usa, us, america, the removed). It groups filings into employers exactly as `analyze.py` does, so counts reproduce.
  - `parse_decision_date(raw: str) -> str | None`: an Excel serial (`'45930'` gives `'2025-09-30'`, epoch 1899-12-30) or an ISO date or datetime prefix; else `None`.
  - `@dataclass(frozen=True) class FyStats: fy: int; total: int; software: int; jr: int; level1: int; newemp_jr: int; last_decision: str; computer_share: float; jr_share_soft: float; newemp_share_soft: float; multi_worksite_share: float; n_worksite_states: int; h1b_dependent: bool; willful: bool; secondary: int`.
  - `@dataclass(frozen=True) class Employer: key: str; legal_name: str; dba: str; fein: str | None; city: str; state: str; naics: str; domain: str | None; domain_kind: str; fy: dict[int, FyStats]` (legal name, city, state, NAICS, FEIN and domain are the most common value over all rows; ties break alphabetically).
  - `load_employers(paths: Sequence[Path]) -> dict[str, Employer]`: per fiscal year, keep H-1B rows with status `Certified` or `Certified - Withdrawn` (also `Certified-Withdrawn`), drop duplicate `CASE_NUMBER`s keeping the latest `DECISION_DATE`, count software rows by SOC (15-1252, 15-1253, 15-1211, 15-1221, 15-1242, 15-1243, 15-1244, 15-1299, 15-2051), `jr` is wage level I or II, `newemp_jr` is `NEW_EMPLOYMENT > 0` among those. The domain is the most common `EMPLOYER_POC_EMAIL` domain that classifies `company` and is not the row's `AGENT_ATTORNEY_EMAIL_ADDRESS` or `PREPARER_EMAIL` domain; `domain_kind` is `company`, `free`, `peo`, `attorney` or `none`. All employers are returned, large ones included (identity needs them to see shared keys).

Fixture roster (rows are H-1B unless noted; columns are the 15 the loader reads plus identity columns; write the CSVs by hand, one row per filing):

| Employer (state, NAICS) | Filings | Purpose |
|---|---|---|
| Nimbus Robotics, Inc. (CA, 541715, FEIN 11-1111111, poc `x@nimbusrobotics.com`) | N1 15-1252 level I new=1; N2 15-1252 II new=1; N3 15-1252 II new=0; N4 15-1252 III new=1; N5 SOC 11-2021 II; N6 15-1252 I `Certified - Withdrawn`; N7 15-1252 II `Withdrawn`; N8 15-1252 I visa class `E-3`; N9 15-1252 I `Denied` | counts and filters |
| Orchard Labs LLC dba Orchard (NY, 513210, FEIN 22-2222222, `orchard.io`) | 3 software I/II | DBA, domain |
| Riverside University (MA, 611310, FEIN 44-4444444, `riverside.edu`) | 2 software II | cap-exempt |
| Bridgeway Staffing Solutions LLC (NJ, 561320) | 4 | hard exclusion NAICS 5613 |
| Depco Systems LLC (TX, 541511) | 3, `H-1B_DEPENDENT` Yes | hard exclusion |
| Lumen Apps Inc (CA, 513210) | 1, `SECONDARY_ENTITY` Yes | hard exclusion |
| Clearpath Inc, Greenfield AI Inc, Sundial Health Inc (CA, WA, MA; 513210, 541511, 513210; all FEIN 33-3333333; `clearpath.ai`, `greenfield.ai`, `sundial.health`) | 2 software I/II each | PEO shared FEIN |
| Veltrix Pharma Inc, Veltrix Diagnostics Inc (NJ, 541715; both poc `@veltrix.com`) and Veltrix Corp (NJ, 541715; `@veltrix.com`, 30 filings, 3 software II) | 2 software II each | parent domain shared, large filer |
| Mesh Systems Inc (CA, 511210, FEIN 55-5555555, `meshsys.com`) | 2 software I | name collisions |
| Quill Data Services Inc (WA, 541512, `quilldata.io`) | 2 software I/II | medium risk |
| Solo Dev Studio Inc (OR, 513210, poc `@gmail.com`) | 1 software I | free-mail domain |

Q2 repeats `N3`'s CASE_NUMBER with a later decision date (2025-04-02) and level III.

- [ ] **Step 1: Write the failing tests.**
  - `test_keys.py`: `test_normalize_fein` (hyphenated, plain, `000000000`, 8 digits, `None`); `test_registrable_domain` (the two examples above, `www.x.com`, `a@mail.x.com`); `test_classify_domain` (one of each kind).
  - `test_lca_aggregate.py`:
    - `test_nimbus_counts_with_q1_only`: `fy[2025]` has `total=6, software=5, jr=4, level1=2, newemp_jr=2` (N6 counts, N7 to N9 do not).
    - `test_duplicate_case_keeps_latest_decision`: with Q1 and Q2, N3 is level III, so `jr=3, level1=2, software=5, last_decision='2025-04-02'`.
    - `test_employers_group_by_normalized_name_and_keep_all_sizes`: Veltrix Corp is present with `fy[2025].total=30`.
    - `test_domain_choice`: Nimbus `nimbusrobotics.com`; Solo Dev `domain is None, domain_kind == 'free'`; a constructed row whose poc domain equals the attorney domain gives `'attorney'`.
    - `test_fein_is_none_when_all_zero`.
    - `test_renamed_header_variants_load` (a copy of Q1 with `H_1B_DEPENDENT` as the header gives Depco `h1b_dependent`).
    - `test_serial_and_iso_dates` (`'45930'` and `'2025-09-30 00:00:00'` give `'2025-09-30'`).
    - `test_missing_required_column_raises` and `test_empty_file_raises` (Review Focus 3: `MissingColumns` and `EmptyTable` propagate, never an empty dict).
    - `test_fiscal_year_from_name` (`LCA_Disclosure_Data_FY2025_Q4.xlsx` gives 2025; `data.csv` raises).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_keys tests.test_lca_aggregate -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `keys.py`, `capabilities.py` and the aggregation half of `lca.py`. Port the filters and the share definitions from `analyze.py` (`computer_share`: share of the employer's H-1B filings whose SOC starts `15-` or is `17-2061` or `17-2072`; `multi_worksite_share`: filings with `TOTAL_WORKSITE_LOCATIONS > 1`). Process one fiscal year at a time (a `dict[case_number, row-tuple]`) so memory is one year, not five.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Sources: key normalizers and LCA aggregation (#31)`.

## Task 5: LCA classification, staffing filter and work tier

**Files:**
- Create: `outreach/sources/lca_rules.py`, `outreach/sources/lca.toml`
- Test: `tests/test_lca_rules.py`

**Interfaces:**
- Consumes: `Employer`, `FyStats` (Task 4).
- Produces:
  - `@dataclass(frozen=True) class LcaConfig`; `load_lca_config(path: Path | None = None) -> LcaConfig` reading `lca.toml`: `max_small_filings = 25`; `staffing_name_regex` and `generic_name_regex` (ported verbatim from `analyze.py` `STAFFING_NAME` and `GENERIC_NAME`); `it_services_naics = ["541511","541512","541513","541519"]`; `staffing_naics_prefix = "5613"`; `cap_exempt_naics_prefixes` and `cap_exempt_name_regex` (as in `analyze.py`); `naics_tier_a_prefixes` and `naics_tier_b_prefix = "5415"`.
  - `qualifies(stats: FyStats, cfg) -> bool`: `1 <= total <= max_small_filings and jr >= 1`.
  - `@dataclass(frozen=True) class Classification: excluded: bool; staffing_risk: Literal["low","medium","high"]; staffing_reason: str; cap_exempt: str | None; cap_reason: str; naics_tier: Literal["A","B","C"]; work_tier: int; work_reason: str`.
  - `classify(emp: Employer, fy: int, cfg) -> Classification`, using that fiscal year's `FyStats`. Hard exclusions in `analyze.py` order, first hit wins: NAICS 5613, H-1B dependent, willful violator, any secondary entity, staffing word in the name, body-shop profile (IT-services NAICS and generic name and `computer_share >= 0.9` and `jr_share_soft >= 0.8` and (`multi_worksite_share >= 0.5` or `total >= 5`)). An excluded employer is `staffing_risk="high"` with `staffing_reason="excluded: <rule>"`. Otherwise risk points (IT-services NAICS, generic name, `computer_share >= 0.9 and total >= 3`, `jr_share_soft >= 1 and newemp_share_soft >= 0.7 and software >= 4`, `n_worksite_states >= 3 and total <= 10`, `multi_worksite_share >= 0.5`): 0 to 1 low, 2 medium, 3 or more high, with the fired signals as the reason.
  - Work tier (design 5.1): tier 3 if excluded; tier 1 if (risk low and not cap-exempt and naics tier A or B) or (cap-exempt and risk not high); else tier 2.
  - `source_fy(emp: Employer, cfg) -> int | None`: the latest fiscal year in which the employer qualifies.

- [ ] **Step 1: Write the failing tests** (employers built directly as `Employer`/`FyStats`, no files):
  - `test_each_hard_exclusion_names_its_rule` (five cases, reason text `excluded: NAICS 5613`, `excluded: H-1B dependent`, `excluded: secondary entity`, `excluded: staffing word in name`, `excluded: body-shop profile`).
  - `test_body_shop_needs_every_condition` (drop each condition in turn; none excludes).
  - `test_risk_points_map_to_levels` (0, 1 low; 2 medium; 3 high and not excluded).
  - `test_cap_exempt_by_naics_and_by_name`.
  - `test_naics_tier` (513210 A, 541511 B, 722511 C).
  - `test_work_tier_table` (the roster: Nimbus 1, Orchard 1, Riverside 1, Clearpath 1, Veltrix Pharma 1, Quill 2, a naics-C low-risk employer 2, a high-risk kept employer 2, a cap-exempt high-risk kept employer 2, Bridgeway 3, Depco 3, Lumen 3).
  - `test_qualifies_boundaries` (25 filings with 1 jr true; 26 false; 0 jr false).
  - `test_source_fy_picks_latest_qualifying_year`.
  - `test_config_regexes_match_the_research_samples` (`"Acme Staffing Solutions"` and `"Bluepeak Infotech"` match the staffing regex; `"Nimbus Robotics"` does not).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_lca_rules -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `lca_rules.py` and `lca.toml`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Sources: LCA staffing filter, cap-exempt tag and work tiers (#31)`.

## Task 6: LcaSource

**Files:**
- Modify: `outreach/sources/lca.py`
- Test: `tests/test_lca_source.py`

**Interfaces:**
- Consumes: Tasks 4 and 5; `Candidate`, `EvidenceDraft` (foundation).
- Produces: `class LcaSource(paths: Sequence[Path], config: LcaConfig | None = None)` with `name = "lca"`, `schedule = "quarterly"`, `version = "lca-1-<first 8 hex of sha256(lca.toml)>"` (a config edit changes it), `discover(since=None)` (`since` is ignored: the files are the input), `shared_keys() -> list[SharedKey]` (sorted), `work_tier(source_ref) -> tuple[int, str] | None`.
  - One `Candidate` per employer with a qualifying fiscal year, including staffing-excluded ones. `name` is the DBA if any, else the legal name. `keys`: `legal_name`, `dba` (if any), `fein` (only when `normalize_fein` is valid and not in `shared_keys()`). `domain` only when `domain_kind == "company"`. `source_ref = "lca:" + employer.key`.
  - Evidence per candidate: `lca` for every fiscal year where the employer had at least one software LCA (value `json.dumps({"fy","software","jr","level1","newemp_jr"}, sort_keys=True, separators=(",", ":"))`, confidence `confirmed`, `source_kind="lca"`, `source_ref="lca:FY<yy>"`, `observed_on` the year's `last_decision`); `staffing_risk` (confidence `estimate`, quote the reason); `cap_exempt="likely"` when tagged (quote the basis); `hq` as `"City, ST"` (city title-cased, confidence `estimate`); `domain` (confidence `estimate`, or `guess` when the domain is in `shared_keys()`). Classification uses `source_fy`.
  - `shared_keys()`: every FEIN used by more than one employer group, and every company-kind domain used by more than one group, counted over all employers including large ones; reason `"<n> employers: <up to 5 legal names>"`.

- [ ] **Step 1: Write the failing tests** (fixture CSVs from Task 4):
  - `test_yields_the_qualifying_employers`: 14 candidates (Veltrix Corp, with 30 filings, is not one); names include `Orchard` (DBA) and `Bridgeway Staffing Solutions LLC`.
  - `test_nimbus_candidate_exactly`: `name == "Nimbus Robotics, Inc."`, `domain == "nimbusrobotics.com"`, `keys == {"legal_name": "Nimbus Robotics, Inc.", "fein": "111111111"}`, `source_ref == "lca:nimbus robotics"`, evidence facts `{lca, staffing_risk, hq, domain}` with `hq` value `"San Francisco, CA"` and the `lca` value `{"fy":2025,"jr":3,"level1":2,"newemp_jr":2,"software":5}` (Q1 and Q2 loaded).
  - `test_excluded_employer_is_stored_with_high_risk`: Bridgeway has `staffing_risk` `high` and quote `excluded: NAICS 5613`, and `work_tier("lca:bridgeway staffing solutions") == (3, ...)`.
  - `test_cap_exempt_tag`: Riverside has `cap_exempt` `likely`.
  - `test_shared_fein_and_domain`: `shared_keys()` is `[SharedKey("domain","veltrix.com",...), SharedKey("fein","333333333",...)]`; the three PEO candidates have no `fein` key; the two Veltrix candidates keep `Candidate.domain == "veltrix.com"` (so identity can see and refuse it) and their `domain` evidence has confidence `guess`.
  - `test_free_mail_employer_has_no_domain`.
  - `test_no_size_or_headcount_evidence_anywhere` (Review Focus 4).
  - `test_version_changes_with_config` (a config copy with `max_small_filings = 30` gives another version).
  - `test_bad_file_raises_on_first_item` (a file missing `SOC_CODE`: `next(source.discover())` raises `MissingColumns`).
  - `test_source_has_name_version_schedule`.
  - `test_lca_source_passes_check_source`: `check_source(self, LcaSource(fixtures))` from `tests/contracts.py` returns the 14 candidates and a second `discover` is equal.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_lca_source -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `LcaSource`; load once and cache so `shared_keys()`, `work_tier` and `discover` share one pass.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Sources: LCA source with evidence, shared keys and work tiers (#31)`.

## Task 7: Identity

**Files:**
- Create: `outreach/identity.py`
- Modify: `tests/storetest.py` (`table_marks`)
- Test: `tests/test_identity.py`

**Interfaces:**
- Consumes: `Candidate`, `Source` name strings, `add_alias`, `mark_shared_key`, `is_shared_key` (foundation), `name_key`, `append_event`, `record_evidence`, `normalize_fein`, `classify_domain`.
- Produces:
  - `tests/storetest.py::table_marks(conn) -> dict[str, tuple[int, int]]`: `(row count, max rowid)` for `company, company_alias, company_source, evidence, board, review_item, event, watched_page, company_work_tier, shared_key`. Idempotency tests compare two snapshots.
  - `@dataclass(frozen=True) class Resolution: company_id: int; created: bool; basis: str; aliases_added: int; evidence_added: int; items_raised: int`. `basis` is one of `source_ref, fein, domain, name_state, created`.
  - `follow_merged(conn, company_id: int) -> int`: follows `merged_into` to the live company.
  - `company_state(conn, company_id: int) -> str | None`: the two-letter state from the company's non-`guess` `hq` evidence (`"City, ST"` or bare `"ST"`); `None` when absent or when two different states are recorded.
  - `find_company(conn, cand: Candidate, *, source_name: str) -> Match` (read only), `@dataclass(frozen=True) class Match: strong: tuple[tuple[int, str], ...]; weak: tuple[tuple[int, str], ...]` (company id and basis, in priority order).
  - `resolve_candidate(conn, cand: Candidate, *, source_name: str, creates: bool = True, actor: str = "system") -> Resolution | None`: `None` only when `creates` is false and nothing matched strongly.
  - `raise_merge_candidate(conn, a: int, b: int, basis: str, detail: str) -> bool`: one open `merge_candidate` item per pair: `company_id = min(a, b)`, `evidence_key = str(max(a, b))`, `raised_by = 'source'`, `reason` names both companies, the basis and `detail`. Returns whether a row was new. This is the contract for the Needs-you view: both records are read by id (`company_id` and `int(evidence_key)`).

**Algorithm (the part the tests do not determine):**
1. A `company_source` row for `(source_name, cand.source_ref)` is the candidate's company (followed through `follow_merged`); basis `source_ref`, no matching.
2. Otherwise strong matches, in this priority, each skipped when its key is shared (`is_shared_key`) or unusable: `fein` (alias key = the 9 digits), `domain` (`classify_domain == "company"`, looked up in non-`guess` `domain` evidence through the `evidence_domain` index, and a shared domain never matches whatever its confidence), `name_state` (a `name_key` of at least 4 characters from the name, `legal_name` or `dba` that is an alias of a company whose `company_state` equals the candidate's state, read from its `hq` draft). The first hit is the company; every other distinct company found raises an item (`basis` names the pair of keys).
3. No strong match: weak matches are companies owning the name alias where the state is unknown on either side or differs. If `creates`, make the company (status `candidate`, an event `created`, actor as given) and raise one item per weak match; else return `None`.
4. Write aliases through `add_alias` (`name`, `legal_name`, `dba`, `fein` unless shared, `ats`, `formd`, `linkedin`). An alias owned by another company (`ValueError`) raises an item against the owner and the rest continue. Write evidence through `record_evidence`, dropping `domain` drafts whose domain is not `company` class. Upsert `company_source` (`ON CONFLICT(company_id, source_name, source_ref) DO UPDATE SET last_seen_at`). Everything for one candidate is one `transaction`.
5. Never copy evidence between companies: a subsidiary or a weak match gets only its own candidate's evidence.

- [ ] **Step 1: Write the failing tests** (candidates built by hand; stores from `fresh_store()`; each source name registered in `source` first):
  - `test_new_candidate_creates_company_aliases_evidence_source`: after resolving `Candidate(name="Acme, Inc.", domain="acme.com", keys={"fein":"123456789","legal_name":"Acme, Inc."}, evidence=(hq "Austin, TX", domain), source_ref="t:acme")` the store has one `candidate` company, aliases `acme` (source `name`), `123456789` (source `fein`), one `company_source`, one event `created`.
  - `test_rerun_writes_nothing_new`: `table_marks` equal before and after a second resolve.
  - `test_same_domain_attaches` and `test_same_fein_attaches` (second candidate from another source, different name: same `company_id`, `basis` `domain`/`fein`, `created=False`).
  - `test_shared_fein_never_merges` (Review Focus 1):
    ```python
    mark_shared_key(conn, "fein", "333333333", "3 employers")
    a = resolve_candidate(conn, cand("Clearpath", fein="333333333"), source_name="lca")
    b = resolve_candidate(conn, cand("Greenfield AI", fein="333333333"), source_name="lca")
    assert a.company_id != b.company_id
    assert conn.execute("SELECT COUNT(*) FROM company_alias WHERE source='fein'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM review_item").fetchone()[0] == 0
    ```
  - `test_shared_domain_never_merges` (the same for a domain, plus: two candidates sharing a shared domain and the same exact name and state still merge by `name_state`, because that rule does not use the key).
  - `test_name_and_state_attach` (Austin TX company, candidate "ACME Inc" Dallas TX: basis `name_state`).
  - `test_same_name_other_state_one_company_one_item` (Review Focus 2): CA then NY gives two companies, one open `merge_candidate` with `company_id` the lower id and `evidence_key` the higher id as text, the name alias stays with the first company; a re-run adds no company and no item.
  - `test_name_without_state_is_weak`.
  - `test_short_name_never_matches_by_name` (`"Ace"`).
  - `test_name_with_no_ascii_letters_still_stores` (`name_key` is empty: the company is created, no name alias, no crash).
  - `test_unusable_domain_never_matches_or_stores` (`gmail.com`, `github.com`: two candidates stay separate; no `domain` evidence rows).
  - `test_conflicting_strong_keys_attach_first_and_raise_item` (fein points at A, domain at B: attaches to A, one item for the pair).
  - `test_alias_owned_elsewhere_raises_item`.
  - `test_non_creating_source_returns_none_when_unmatched_and_attaches_when_matched`.
  - `test_merged_company_is_followed`.
  - `test_subsidiary_never_inherits_size`: a parent with `domain` `p.com` and `size` evidence 5000, two subsidiary candidates on `p.com` (marked shared): two new companies, zero `size` rows on them.
  - `test_hand_evidence_draft_is_refused_by_identity` (a `hand` draft raises `ValueError`; hand evidence is written by Task 13).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_identity -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `identity.py` as above, plus `table_marks`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Identity: one company per key, shared keys never merge, weaker matches raise items (#31)`.

## Task 8: Work tiers and the discover job

**Files:**
- Create: `outreach/sources/tiers.py`, `outreach/sources/jobs.py`
- Test: `tests/test_tiers.py`, `tests/test_jobs_discover.py`

**Interfaces:**
- Consumes: Tasks 6 and 7; `start_job`, `finish_job`, `mark_shared_key`.
- Produces:
  - `set_work_tier(conn, company_id: int, tier: int, reason: str, source_version: str) -> bool`: upsert that writes only when `tier` or `reason` changed; returns whether it did. `effective_tier(conn, company_id) -> int`: 1 when `promoted`, the stored tier otherwise, 1 when there is no row (HN, by-hand and other sources). `promote(conn, company_id: int, actor: str) -> None`: sets `promoted = 1`, appends an event; a later `set_work_tier` never clears it.
  - `register_source(conn, name: str, version: str, run_id: int) -> None`: upsert into `source` (`ON CONFLICT(source_name) DO UPDATE SET version, last_run_id`).
  - `@dataclass class SourceReport: candidates: int = 0; created: int = 0; attached: int = 0; skipped_unmatched: int = 0; items_raised: int = 0; evidence_added: int = 0; failed: list[str] = field(default_factory=list); error: str = ""`.
  - `run_discover(conn, sources: Sequence[Source], *, creates: dict[str, bool] | None = None, since: str | None = None) -> tuple[int, dict[str, SourceReport]]`: one `job_run` `discover`; per source: register it, mark `shared_keys()` (if the source has them) before any candidate, then resolve each candidate with `resolve_candidate(creates=source.creates_companies)` (default `True`), set the work tier from `work_tier()` when the source has it, count. A source that raises is recorded in `error` and the others still run; a candidate that raises `sqlite3.Error` or `ValueError` is listed in `failed` with its message and the run continues. Status `ok`, or `partial` when any source errored or any candidate failed, or `failed` when every source errored. The summary is the reports as JSON.

- [ ] **Step 1: Write the failing tests.**
  - `test_tiers.py`: `test_set_work_tier_writes_only_on_change`, `test_effective_tier_defaults_to_1_without_a_row`, `test_promote_survives_a_later_rerun_that_says_tier_3`.
  - `test_jobs_discover.py` with a `FakeSource`:
    - `test_discover_registers_source_version_and_job_run`: `source.version` stored, `job_run.status == 'ok'`, `last_run_id` set.
    - `test_shared_keys_are_marked_before_the_first_candidate` (the fake records the order).
    - `test_one_failing_source_is_partial_and_the_others_land` (first source yields 2 candidates then raises; second yields 1: 3 companies stored, status `partial`, the error text in the summary).
    - `test_candidate_failure_is_counted_not_swallowed` (monkeypatch `identity.resolve_candidate` to raise `sqlite3.IntegrityError` for one candidate: it is in `failed` with the message, others stored, status `partial`).
    - `test_lca_fixtures_store_the_expected_companies` (14 companies, name aliases for each, `fein` aliases for Nimbus, Orchard and Riverside and none for the three PEO clients, `lca` evidence rows per employer, work tiers Nimbus 1, Quill 2, Bridgeway 3).
    - `test_rerun_writes_nothing_new` (`table_marks` equal after a second `run_discover` on the same files; only `job_run` and `company_source.last_seen_at` change).
    - `test_shared_fein_clients_stay_separate_companies` (Review Focus 1, end to end).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_tiers tests.test_jobs_discover -v`. Expected: FAIL.
- [ ] **Step 3: Implement** both modules. Sources opt in to `creates_companies = False` (default `True` when the attribute is absent).
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Sources: work tiers and the discover job (#31)`.

## Task 9: Resolve job and size flags

**Files:**
- Modify: `outreach/identity.py` (add `resolve_store`), `outreach/sources/jobs.py` (add `run_resolve`)
- Test: `tests/test_resolve.py`

**Interfaces:**
- Produces:
  - `resolve_store(conn) -> StoreResolveReport` (`pairs_raised: int`, `size_checks_raised: int`): a pass over stored companies with no source input. (a) Two live companies (not merged, not removed) holding the same non-`guess` `domain` evidence that is not a shared key raise one `merge_candidate` (basis `domain`). (b) Each live company holding a domain that is a shared key (any confidence) raises one `size_check` item, `raised_by 'source'`, `evidence_key = "shared-domain:<domain>"`, reason `shares <domain> with <n> other employers: it may be a subsidiary; size unknown`. It never changes `merged_into`, never moves a row, never hides: merging two stored companies belongs to the Needs-you confirm action (#16).
  - `run_resolve(conn) -> tuple[int, StoreResolveReport]`: one `job_run` `resolve`.

- [ ] **Step 1: Write the failing tests.**
  - `test_two_companies_with_the_same_unshared_domain_raise_one_item` (created by two sources before either knew the domain; then evidence added to the second).
  - `test_shared_domain_raises_size_check_not_merge_for_each_holder` (after `run_discover` on the LCA fixtures: exactly two `size_check` items, for Veltrix Pharma and Veltrix Diagnostics; still two companies).
  - `test_resolve_is_idempotent` (`table_marks` equal after a second run).
  - `test_resolve_ignores_merged_and_removed_companies`.
  - `test_resolve_changes_no_company_row` (a hash of the `company` table is equal before and after).
  - `test_no_source_writes_size_evidence` (Review Focus 4): after discover and resolve on the LCA fixtures, `SELECT COUNT(*) FROM evidence WHERE fact IN ('size','headcount')` is 0, and every company is `status = 'candidate'` (nothing is hidden).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_resolve -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `resolve_store` and `run_resolve`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Identity: resolve job, duplicate-domain items and size checks (#31)`.

## Task 10: Board references

**Files:**
- Create: `outreach/sources/boards.py`, `outreach/sources/ats_detect.py`
- Test: `tests/test_boards.py`, `tests/test_ats_detect.py`

**Interfaces:**
- Produces:
  - `SUPPORTED_ATS: frozenset[str]`: the `board.ats` values in the schema (the 0001 set plus the foundation's: `pinpoint, teamtailor, jazzhr, paycom, icims, personio, join_com, cornerstone, successfactors`).
  - `@dataclass(frozen=True) class BoardRef: ats: str; token: str`, with `token` in the foundation's Shared format: lower case; Workday `<tenant>.wd<N>/<site>` (`riverside.wd5/external`); Oracle `<host>/<siteNumber>` (`ebwg.fa.us2.oraclecloud.com/cx`); iCIMS the board host (`careers-acme.icims.com`); every other ATS the slug. `normalize_token(ats: str, token: str) -> str` lower-cases and validates (Workday `^[a-z0-9-]+\.wd\d+/[a-z0-9_-]+$`, Oracle `^[a-z0-9.-]+/[a-z0-9_-]+$`, iCIMS `^[a-z0-9.-]+\.icims\.com$`, others `^[a-z0-9][a-z0-9._%-]*$`) and raises `ValueError` otherwise; `BoardRef.__post_init__` calls it, so no malformed token reaches a row.
  - `detect_ats(text: str) -> list[BoardRef]`: finds ATS hosts and embeds in HTML or text, in first-seen order, at most 3, without duplicates, ignoring tokens `embed, js, api, www, jobs, careers, static, assets, v1, widget`. Patterns ported from `careers_probe.py` with a named `token` group: Greenhouse (`boards|job-boards(.eu).greenhouse.io/<token>` and `embed/job_board(/js)?\?for=<token>`), Lever, Ashby, Workable (`apply.workable.com/<token>`), SmartRecruiters, Rippling (`ats.rippling.com/<token>`), BambooHR, JazzHR (`<token>.applytojob.com`), Breezy, Recruitee, Teamtailor, Pinpoint, Gem, Workday (`<tenant>.wd<N>.myworkdayjobs.com/<site>`, an optional `en-US` locale segment skipped, giving `<tenant>.wd<N>/<site>`), Oracle (`<host>.oraclecloud.com/hcmUI/CandidateExperience/<lang>/sites/<site>`, giving `<host>/<site>`), iCIMS (`<name>.icims.com`, giving the host). Hosts seen but not in `SUPPORTED_ATS` (Jobvite, Paylocity, ADP, UKG, Notion, LinkedIn) are returned by `detect_unsupported(text) -> list[str]` (host names) and never as boards.
  - `add_board(conn, company_id: int, ref: BoardRef, *, via: str, matched_on: str) -> Literal["added", "exists", "conflict"]`: `INSERT INTO board ... ON CONFLICT(ats, token) DO NOTHING`; adds the alias `"<ats>:<token>"` (source `ats`); appends event `board` / `found` (actor `crawler`, detail `via`, `matched_on`, idempotency key `board-found:<ats>:<token>`). If the `(ats, token)` already belongs to another company it returns `conflict` and raises a `merge_candidate` for the pair (basis `same_board`) instead of moving the board.

- [ ] **Step 1: Write the failing tests.**
  - `test_ats_detect.py`, one HTML snippet per ATS taken from `careers_probe.py` samples: `test_greenhouse_embed_and_board_urls`, `test_lever`, `test_ashby`, `test_workday_token_is_tenant_wd_n_slash_site` (`https://riverside.wd5.myworkdayjobs.com/en-US/External` gives `riverside.wd5/external`), `test_oracle_and_icims_tokens`, `test_rippling`, `test_generic_tokens_are_ignored` (`greenhouse.io/embed` gives nothing), `test_cap_of_three`, `test_unsupported_hosts_are_reported_not_returned`.
  - `test_boards.py`: `test_add_board_inserts_row_alias_and_event`, `test_add_board_twice_is_exists_and_writes_nothing`, `test_board_owned_by_another_company_is_conflict_and_one_item`, `test_unsupported_ats_raises_value_error`, `test_tokens_are_lower_cased_and_validated` (`Acme` becomes `acme`; a Workday token without `.wd<N>` raises), `test_alias_uses_source_ats_and_key_ats_colon_token` (`company_alias.source == 'ats'`, key `greenhouse:acme`).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_boards tests.test_ats_detect -v`. Expected: FAIL.
- [ ] **Step 3: Implement** both modules.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Sources: board references and ATS detection (#31)`.

## Task 11: SEC Form D source

**Files:**
- Create: `outreach/sources/formd.py`, `tests/fixtures/formd/{FORMDSUBMISSION,ISSUERS,OFFERING}.tsv`, `tests/fixtures/formd/submissions/CIK*.json`
- Test: `tests/test_formd.py`

**Interfaces:**
- Consumes: `HttpClient`, `normalize_fein`, `find_company` (Task 7), `Candidate`.
- Produces: `class FormDSource(dataset: Path, client: HttpClient)` with `name = "formd"`, `schedule = "daily"`, `version = "formd-1"`, `creates_companies = False`, `discover(since=None)`. `dataset` is the quarterly data set zip or a folder of its three TSV files. For each non-fund issuer (primary issuer of a filing whose `INDUSTRYGROUPTYPE` is not a pooled investment fund) filed on or after `since`, yield a `Candidate(name=issuer, domain=None, keys={"formd_cik": cik}, source_ref="formd:<cik>", evidence=(funding,), hq state from the issuer address)`. The `funding` draft: value `{"amount_usd": <TOTALAMOUNTSOLD or null>, "filed_on": "<date>", "kind": "form_d" or "form_d_amendment"}` (sorted keys), confidence `confirmed`, `source_kind="formd"`, `source_ref` the accession number, `observed_on` the filing date.
  - Before yielding, the source asks the store nothing; matching is `run_discover`'s job with `creates=False`. To use the `ein` path, `FormDSource` fetches `https://data.sec.gov/submissions/CIK<10 digits>.json` once per issuer whose `name_key` equals a stored company's (a `name_lookup: Callable[[str], bool]` argument defaults to "always"; `run_discover` passes a store-backed one), through a `PoliteClient`-wrapped client, and adds `keys["fein"] = normalize_fein(ein)` when valid. An SEC `FetchError` or non-200 for one issuer drops the `fein` key for that issuer and counts it in `fetch_errors`; it never drops the filing.
  - Per-source policy (documented in the module docstring): Form D never creates a company, never writes `stage` (money raised is not a round, design 5.4), and never raises merge items; an issuer matched weakly or not at all is counted (`skipped_unmatched`) and dropped. SPV issuers (investor vehicles named after a brand) are not matched: exact names only, since fuzzy matching was right 0 of 10 times.

- [ ] **Step 1: Read the real layout once.** Download the 2026 Q3 Form D data set by hand (`https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets`, one request with a declared User-Agent) into `~/Desktop/outreach-scratch/`, read its readme and header rows, and confirm the column names the source reads: `ACCESSIONNUMBER`, `FILING_DATE`, `SUBMISSIONTYPE`, `CIK`, `ENTITYNAME`, `ENTITYTYPE`, `STATEORCOUNTRY`, `CITY`, `INDUSTRYGROUPTYPE`, `TOTALOFFERINGAMOUNT`, `TOTALAMOUNTSOLD`, `IS_PRIMARYISSUER_FLAG`. Fix any name that differs, and build the fixture TSVs from the real header rows plus invented rows (never copy more than a header and three real rows). Not part of the tests.
- [ ] **Step 2: Write the failing tests** (fixture issuers: "Orchard Labs, Inc." NY D and a D/A; "Nimbus Robotics Holdings Inc" TX, whose fixture submissions JSON has `ein` `111111111`; "Mesh Systems Inc" TX; "Clearpath Holdings LLC" with `ein` `333333333`; "Acme Growth Fund LP" pooled investment fund; "Unknown Startup Inc"). A `FakeClient` serves the submissions JSON.
  - `test_funds_are_skipped`.
  - `test_funding_value_and_kind` (the D row: `{"amount_usd":4500000,"filed_on":"2026-08-14","kind":"form_d"}`; the D/A row kind `form_d_amendment`).
  - `test_date_formats` (`14-AUG-2026` and `2026-08-14` both read).
  - `test_since_filters_by_filing_date`.
  - End to end with `run_discover` over the LCA fixtures first: `test_orchard_attaches_by_name_and_state` (two `funding` rows on Orchard), `test_nimbus_attaches_by_fein_from_ein` (the issuer name differs; the SEC `ein` `111111111` equals the LCA FEIN), `test_weak_name_other_state_is_dropped` (Mesh Systems TX: no evidence on the CA company, no item, `skipped_unmatched` counts it), `test_shared_ein_never_attaches` (Review Focus 1: `ein` `333333333` is a marked shared key, so "Clearpath Holdings LLC" attaches to none of the three PEO clients), `test_blank_ein_is_ignored` (`000000000`), `test_unmatched_issuer_creates_no_company`.
  - `test_sec_fetch_error_keeps_the_filing_without_fein` (the client raises `FetchError`: the by-name path still works, `fetch_errors == 1`, and the run is not `failed`).
  - `test_rerun_writes_nothing_new`.
  - `test_formd_source_passes_check_source` (`check_source` over the fixture data set with the fake SEC client: deterministic, every item a `Candidate`).
- [ ] **Step 3: Run** `uv run python -m unittest tests.test_formd -v`. Expected: FAIL.
- [ ] **Step 4: Implement** `FormDSource`; extend `run_discover` (Task 8) to pass the `name_lookup` and to read `creates_companies`.
- [ ] **Step 5: Run** the same command and `tests.test_jobs_discover`. Expected: PASS.
- [ ] **Step 6: Commit** `Sources: SEC Form D funding evidence (#31)`.

## Task 12: HN "Who is hiring" source

**Files:**
- Create: `outreach/sources/hn.py`, `tests/fixtures/hn/search_whoishiring.json`, `tests/fixtures/hn/item_<id>.json`
- Test: `tests/test_hn.py`

**Interfaces:**
- Produces: `class HnSource(client: HttpClient)` with `name = "hn"`, `schedule = "monthly"`, `version = "hn-1"`, `discover(since=None)` (`since` is `"YYYY-MM"`; default the latest thread). It lists threads with one Algolia call (`https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=20`, titles `Ask HN: Who is hiring? (Month YYYY)`), fetches each wanted thread with one call (`https://hn.algolia.com/api/v1/items/<id>`), and reads top-level comments.
  - `parse_post(html: str) -> Post | None` (`Post(name, header, links, stage, location)`): the header is the first paragraph (text before the first `<p>`), tags stripped with `html.parser`, hrefs collected. Split the header on `|`, ` U+2014 ` (spaced), ` U+2013 ` (spaced) and ` - `; the first segment is the name: parentheses removed (a `YC S24`, `YC W26` or `YC` parenthetical gives stage `yc`, `Series A` to `Series E` give `series_a` to `series_e_plus`, `Seed` gives `seed`), a URL in it removed, `None` when empty, over 5 words, or containing `:`, `?` or `!`. Headers with no separator and more than 5 words are dropped (`unparsed`).
  - `is_us(header: str) -> bool`: false only when the header names a non-US place (config list: UK, London, Berlin, Europe, EU, Canada, Toronto, India, and so on) and no US marker (`US`, `USA`, `U.S.`, `United States`, `, <state code>`, a major US city, `NYC`, `SF`, `Remote (US`); no marker either way keeps the post (unknown location is never grounds to hide).
  - The `Candidate`: `name`, `domain` from the first link whose `classify_domain` is `company` (platform links such as `github.com` are not domains), `keys["ats"]` from `detect_ats` on the links (first supported), `source_ref = "hn:" + name_key(name)` (stable across months, so one company per name), evidence per post: `domain` (confidence `estimate`, `source_kind="hn"`, `source_ref` the post URL `https://news.ycombinator.com/item?id=<id>`, `quote` the header), `stage` when parsed (confidence `estimate`), `hq` as the location text when a location segment parses (confidence `guess`).

- [ ] **Step 1: Write the failing tests** (fixture thread, 7 top-level comments):
  - `test_parse_post_real_header_shapes`: `"Orchard (YC S21) | Backend Engineer | NYC | REMOTE | https://orchard.io/jobs"` gives name `Orchard`, stage `yc`; `"PrairieLearn (Remote US) <U+2014> Full-Stack Software Engineer" (the fixture file holds the real character)` gives name `PrairieLearn`; `"Thunder Compute (YC S24)"` gives `Thunder Compute`; `"Shepherd (Series B)"` gives stage `series_b`; `"Thanks for the thread, great month everyone"` gives `None`.
  - `test_is_us`: `"Quasar Games | Unity dev | London, UK"` false; `"Tidepool Apps | Full-stack | Remote (US)"` true; `"Mesh Systems | SWE | San Francisco"` true; a header with no location true.
  - `test_platform_links_are_not_domains` (`github.com/acme` gives no domain).
  - `test_ats_link_becomes_a_key` (`https://jobs.lever.co/tidepoolapps` gives `keys["ats"] == "lever:tidepoolapps"`).
  - `test_algolia_calls_are_one_search_and_one_item_per_thread` (`client.requests` has 2 URLs for the latest thread).
  - `test_error_status_raises` (a 503 on the item call raises `FetchError`-derived `SourceError`, never an empty result).
  - With `run_discover` after the LCA run: `test_orchard_post_attaches_by_domain_and_adds_yc_stage`; `test_tidepool_is_a_new_company_with_domain_and_ats_alias`; `test_non_us_post_is_dropped_and_counted`; `test_weak_name_collision_makes_company_and_one_item` (HN "Mesh Systems" on `meshsystems.dev` while the LCA company `Mesh Systems Inc` (CA) owns the name: a new company, one `merge_candidate`, the name alias stays with the LCA company); `test_next_month_post_is_not_a_second_company` (Review Focus 2: a November thread with the same Mesh Systems post adds no company and no item, only a `company_source.last_seen_at` change); `test_parent_domain_post_never_attaches` (Review Focus 1: a post linking `https://veltrix.com/careers` attaches to neither Veltrix company); `test_rerun_writes_nothing_new`; `test_hn_source_passes_check_source` (`check_source` with the fixture client).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_hn -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `HnSource`. Region markers and non-US places live in a small `hn.toml` read with `tomllib`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Sources: HN Who is hiring (#31)`.

## Task 13: By-hand source

**Files:**
- Create: `outreach/sources/byhand.py`
- Test: `tests/test_byhand.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class HandEntry: name: str; reason: str; domain: str | None = None; state: str | None = None; ats: str | None = None` and `class ByHandSource(entries: Sequence[HandEntry])` with `name = "hand"`, `schedule = "on_demand"`, `version = "hand-1"`, `discover(since=None)` yielding one `Candidate` per entry (`source_ref = "hand:" + name_key(name)`); an `ats` that fails `BoardRef` validation (Task 10) raises `ValueError` when the entry is built. `add_by_hand(conn, *, name: str, reason: str, domain: str | None = None, state: str | None = None, ats: str | None = None, actor: Literal["devanshu", "claude"] = "devanshu") -> HandAdd` and `@dataclass(frozen=True) class HandAdd: company_id: int; created: bool; basis: str; items_raised: int`. `reason` is required (a blank one raises `ValueError`). It runs one `HandEntry` through `ByHandSource` and `resolve_candidate(source_name="hand")` (registered in `source` as version `hand-1`). Domain, `hq` (`state` as a bare two-letter value) and the reason are stored as locked hand evidence written here, not through `record_evidence` (which refuses hand drafts): `INSERT INTO evidence ... locked = 1, source_kind = 'hand', confidence = 'confirmed'` with `ON CONFLICT DO NOTHING`, the reason as the `quote` of the `domain` row when a domain is given. The reason also goes in an event `company` / `added_by_hand` (actor as given, detail `{"reason": ...}`, idempotency key `hand:<name_key>:<sha256(reason)[:12]>`), which is where the company page reads it. The work tier is promoted to 1 (`reason "by hand"`). An `ats` such as `greenhouse:acme` becomes an alias, and `find_boards` (Task 14) turns it into a board row. A name or domain already known returns the existing company with `created=False`; a weaker match creates the company and raises the usual item.

- [ ] **Step 1: Write the failing tests.**
  - `test_add_creates_company_with_locked_domain_and_event`.
  - `test_blank_reason_raises`.
  - `test_existing_domain_returns_the_existing_company` (`created=False`, `basis="domain"`).
  - `test_weak_name_creates_company_and_item`.
  - `test_hand_company_gets_effective_tier_1`.
  - `test_adding_twice_writes_nothing_new` (`table_marks`).
  - `test_shared_domain_hand_add_never_merges`.
  - `test_ats_key_is_an_alias_not_yet_a_board`.
  - `test_bad_ats_token_raises_before_anything_is_written` (`workday:acme/x`).
  - `test_by_hand_source_passes_check_source` (`check_source` over two `HandEntry`s).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_byhand -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `add_by_hand`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Sources: add a company by hand with a reason (#31)`.

## Task 14: stapply directory and the find_boards job (stapply step)

**Files:**
- Create: `outreach/sources/stapply.py`, `tests/fixtures/stapply/{manifest.json,greenhouse.csv,ashby.csv,lever.csv,workday.csv,jobvite.csv}`
- Modify: `outreach/sources/jobs.py` (add `run_find_boards`)
- Test: `tests/test_stapply.py`, `tests/test_jobs_find_boards.py`

**Interfaces:**
- Produces in `stapply.py`:
  - `STAPPLY_ATS: dict[str, str]`: stapply list name to `board.ats` (`oracle` to `oracle_orc`; the names that match the schema map to themselves; lists with no schema ATS, such as `jobvite`, `adp`, `ukg`, `taleo`, `paylocity`, are absent and ignored).
  - `board_token(list_name: str, slug: str, url: str) -> str | None`: the foundation's token format from a stapply row. Workday from the URL (`https://23andme.wd5.myworkdayjobs.com/23` gives `23andme.wd5/23`; the CSV slug `23andme/23` is not used), Oracle `<url host>/<site after /sites/>` lower case (`https://ebwg.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX` gives `ebwg.fa.us2.oraclecloud.com/cx`), iCIMS the URL host, every other list the lower-cased slug. A row whose token fails `normalize_token` (for example a Cornerstone slug that is itself a URL) is skipped and counted.
  - `board_match_key(name: str) -> str`: the exact normalized key the research measured (lower case, parentheses and `dba` tails dropped, punctuation removed, and `inc, llc, l l c, corp, corporation, co, ltd, limited, the, usa, us, incorporated, company, holdings, group, pbc, plc, lp, llp` removed, spaces removed); keys shorter than 4 characters never match.
  - `@dataclass(frozen=True) class BoardHit: ref: BoardRef; url: str; matched_on: Literal["name", "slug", "host"]`.
  - `class StapplyDirectory`: `StapplyDirectory.load(folder: Path) -> StapplyDirectory` (reads `manifest.json` and the `<ats>.csv` files with `name,slug,url`; `version = "stapply-1-" + manifest["generated_at"]`); `lookup(names: Iterable[str], domain: str | None) -> list[BoardHit]`: a hit when `board_match_key(name)` equals the key of a row's name (`name`) or its slug with `-` as spaces (`slug`), or when the row's URL host equals or ends with `.` plus the company's registrable domain (`host`). Exact matches only; at most 3 hits per company (more than 3 returns none: too ambiguous to trust). `refresh(client: HttpClient, folder: Path) -> list[str]`: downloads `manifest.json` (`https://storage.stapply.ai/jobhive/v1/manifest.json`, declared User-Agent: a bare client gets 403) and each `by_ats_companies` CSV whose sha256 differs from the manifest's, verifying the hash after download; returns the files updated.
- Produces in `jobs.py`: `run_find_boards(conn, *, directory: StapplyDirectory | None, careers: CareersChecker | None = None, max_careers: int = 100) -> tuple[int, FindBoardsReport]`: one `job_run` `find_boards`. Candidates are live companies (status `candidate`, `backlog` or `working`, not merged) with no active board and no `watched_page`, ordered by `effective_tier` then `company_id`; tier 3 is skipped unless promoted. Steps per company: (0) any `ats` alias with no board becomes a board (`add_board`, via `alias`); (1) the stapply lookup over the company's `name`, `legal_name` and `dba` aliases and its non-shared domain, each hit added with `add_board(via="stapply", matched_on=...)` and a `company_source` row (`source_name="stapply"`, `source_ref="<ats>:<slug>"`; `stapply` is registered in `source` with the directory's version); (2) the careers step (Task 15) only when steps 0 and 1 found nothing. `FindBoardsReport` counts `considered, from_alias, from_stapply, skipped_tier3, conflicts`.
- The match is by name, which design 5.2 treats as weaker than domain or FEIN for company identity; for a board it is the design's own step 1 (5.3) and was right about 95% of the time on exact normalized names (research section 3.1). The board's first read in #21 is where a wrong match shows (empty or odd postings).

- [ ] **Step 1: Write the failing tests.**
  - `test_stapply.py`: `test_board_match_key_matches_the_research_normalization` (`"Stryder Corporation"`, `"Nimbus Robotics, Inc."`, a `dba` tail, a short key), `test_exact_name_match_and_slug_match_and_host_match`, `test_board_token_formats` (the Workday, Oracle and iCIMS examples above, a Greenhouse slug `NimbusRobotics` giving `nimbusrobotics`, a URL slug giving `None`), `test_unsupported_ats_lists_are_ignored` (Jobvite row never returned), `test_more_than_three_hits_returns_none`, `test_version_comes_from_the_manifest`, `test_refresh_downloads_only_changed_files_and_checks_sha256` (FakeClient serving the manifest and two CSVs; a corrupted body raises), `test_refresh_uses_a_declared_user_agent`.
  - `test_jobs_find_boards.py` (after `run_discover` on the LCA fixtures): `test_nimbus_and_orchard_get_boards` (tokens `nimbusrobotics` and `orchard`, aliases `greenhouse:nimbusrobotics` and `ashby:orchard` with `source='ats'`, `company_source` rows, events, and the new rows are due now for #21: `last_read IS NULL`, `next_read_at IS NULL`, `active = 1`), `test_tier_3_company_is_skipped` (Lumen Apps has a Greenhouse row in the fixture; no board), `test_promoted_tier_3_company_gets_its_board`, `test_alias_becomes_a_board` (by-hand `ats="lever:zephyr"`), `test_bad_alias_token_is_counted_not_fatal` (an `ats` alias whose token fails validation is listed in the report and the run continues), `test_board_on_another_company_raises_one_item`, `test_rerun_writes_nothing_new`, `test_companies_with_a_board_are_not_considered_again`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_stapply tests.test_jobs_find_boards -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `StapplyDirectory` and `run_find_boards` (steps 0 and 1).
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Sources: stapply board lists and the find_boards job (#31)`.

## Task 15: Careers page check and watched pages

**Files:**
- Create: `outreach/sources/careers.py`, `tests/fixtures/careers/*.html`
- Modify: `outreach/sources/jobs.py` (the careers step of `run_find_boards`)
- Test: `tests/test_careers.py`, `tests/test_jobs_find_boards.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class CareersResult: kind: Literal["boards", "page", "none", "error"]; boards: tuple[BoardRef, ...]; page_url: str | None; unsupported: tuple[str, ...]; error: str` and `class CareersChecker(client: HttpClient)` with `check(domain: str) -> CareersResult`. Flow (design 5.3 step 3, at most 2 requests): `GET https://<domain>/careers`; on 200, `detect_ats` over the body gives `boards`, else `page` with the final URL. On 404 or another 4xx, `GET https://<domain>/` and follow the first link whose href contains `career`, `jobs` or `join` (relative links resolved, same site only); detect on that page. Both 404, or a homepage with no such link, is `none`. A `FetchError`, a 5xx or a 429 on any request is `error` (nothing is concluded). A detected ATS outside `SUPPORTED_ATS` is listed in `unsupported` and treated as `page`.
- `run_find_boards` careers step (at most `max_careers` companies per run, only when `careers` is given): skipped when the company's domain is a shared key (a parent's portal: event `careers_skipped`, detail the domain) or has no usable domain (a tier 1 company then raises a `domain_missing` item, `raised_by 'crawler'`, `evidence_key "no-usable-domain"`). `boards` are added (`via="careers"`, `company_source` `careers`); `page` inserts `watched_page(company_id, url)` with `ON CONFLICT(company_id) DO NOTHING` (fingerprint empty, due now); `none` appends event `careers_checked` (detail `{"result": "none"}`, idempotency key `careers-checked:<company_id>:<YYYY-MM>`) and the company is not fetched again for 30 days; `error` only counts (`careers_errors`) and leaves no marker.

- [ ] **Step 1: Write the failing tests.**
  - `test_careers.py` (FakeClient routes to HTML fixtures): `test_careers_page_with_ats_embed_gives_boards`, `test_404_then_homepage_link_then_page` (Clearpath: `/careers` 404, `/` links `/join-us`, which embeds an Ashby URL: 3 requests at most... exactly 3: careers, homepage, join page; assert `client.requests`), `test_page_without_ats_is_a_page_with_final_url`, `test_unsupported_ats_is_a_page_and_reported`, `test_both_404_is_none`, `test_503_is_error_not_none`, `test_timeout_is_error`, `test_link_to_another_site_is_not_followed`.
  - `test_jobs_find_boards.py` additions after discover, HN and stapply: `test_careers_step_results` (Clearpath gets `ashby:clearpath`; Riverside gets `workday:riverside.wd5/external` (the fixture page links `https://riverside.wd5.myworkdayjobs.com/en-US/External`); the HN-created Mesh Systems company, whose stapply board is owned by the LCA Mesh company, gets a `watched_page` that is due now for #21: `fingerprint = ''`, `last_fetch_at IS NULL`, `next_fetch_at IS NULL`, `error_streak = 0`; Sundial and Quill get the `none` marker; Greenfield errors), `test_error_leaves_no_marker` (Review Focus 5: Greenfield's `/careers` is 503; no `careers_checked` event, and the next run fetches it again), `test_none_marker_blocks_refetch_for_the_month` (a second run makes no request for Sundial), `test_shared_domain_company_is_not_fetched` (Veltrix: no request to `veltrix.com`), `test_free_mail_tier_1_company_raises_domain_missing` (Solo Dev), `test_max_careers_limit`, `test_rerun_writes_nothing_new`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_careers tests.test_jobs_find_boards -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `CareersChecker` (links via `html.parser`) and the careers step.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Sources: careers page check and watched pages (#31)`.

## Task 16: CLI, end-to-end run, real-data check and hand-off

**Files:**
- Create: `outreach/sources/__main__.py`, `tests/test_sources_e2e.py`, `tests/pipeline/sources_stages.py`, `tests/fixtures/pipeline/stapply/greenhouse.csv`
- Modify: `tests/pipeline/test_flow.py` (call `sources_stages.register(harness)` and add the invariants below), `tests/fixtures/pipeline/` LCA extract (only if it lacks the shape below)
- Modify: `CLAUDE.md` (add the `OUTREACH_SOURCES_DIR` row to the scratch table), `docs/README.md` (rows for this plan and the new modules), `docs/superpowers/plans/2026-10-21-pipeline-foundation.md` (one line under Task 1 noting `facts.py` was widened here, if Task 1 of this plan did it)
- Test: `tests/test_sources_e2e.py`

**Interfaces:**
- Produces the CLI `uv run python -m outreach.sources <command>`, all through `outreach.store.connect()` (so the live-store guard applies):
  - `fetch lca|stapply [--dest DIR]`: downloads (DOL with the `browser` profile and the explicit URLs in `lca.toml`, stapply through `StapplyDirectory.refresh`) into `sources_dir()`.
  - `discover lca --files F... | formd --dataset PATH | hn [--since YYYY-MM]`: builds the source and runs `run_discover`; prints the report as JSON.
  - `resolve`, `find-boards [--max-careers N]`, `add --name N --reason R [--domain D] [--state ST] [--ats A:T]`.
  - Exit code 0 on `ok`, 1 on `partial` or `failed`.

- [ ] **Step 1: Write the end-to-end test** `tests/test_sources_e2e.py`, one scratch store, fixtures only, in the production order: LCA discover, Form D discover, HN discover, one by-hand add (`Zephyr Works`, domain `zephyr.dev`, reason `met the founder at a meetup`, `ats="lever:zephyr"`), resolve, find_boards.
  - `test_expected_store` (the issue's first Done-when line): 17 companies (14 LCA, Tidepool, HN Mesh Systems, Zephyr); every LCA company has its `lca` evidence and name alias; `fein` aliases only for Nimbus, Orchard, Riverside and Mesh; two `funding` rows on Orchard and one on Nimbus; Orchard has a `stage` `yc` row; open items: exactly one `merge_candidate` (the two Mesh companies), two `size_check` (the Veltrix pair), one `domain_missing` (Solo Dev); boards: Nimbus, Orchard, Mesh (LCA), Tidepool, Zephyr, Clearpath, Riverside (every token in the foundation's format, every alias `source='ats'`); one `watched_page` (HN Mesh); every `job_run` is `ok` except none.
  - `test_rerun_writes_nothing_new` (the second Done-when line): run the whole sequence again; `table_marks` equal; only `job_run` rows and `last_seen_at` differ.
  - `test_shared_keys_never_merge` (the third line): the three PEO clients and the two Veltrix companies are five distinct live companies; `company.merged_into` is `NULL` for every company; no alias has source `fein` for the shared FEIN.
  - `test_cli_discover_prints_report_and_sets_exit_code` (calls `main([...])` with the fixture files and a scratch `OUTREACH_DB`; a bad file gives exit 1 and a message naming the column).
  - `test_cli_refuses_the_live_store_outside_the_live_checkout` (the existing guard: with `OUTREACH_DB` unset, `main` exits before opening anything; follow `tests/test_live_guards.py`).
- [ ] **Step 1b: Register the stages in the foundation's harness** (`tests/pipeline/harness.py::PipelineHarness`, foundation Task 6). `sources_stages.register(harness)` appends `("discover", ...)`, `("resolve", ...)` and `("find_boards", ...)` to `harness.stages`, in that order and before any later stage. They run on `harness.conn` and the fixtures: `discover` runs `run_discover` with `LcaSource([harness.fixture("lca_extract.csv")])`; `resolve` runs `run_resolve`; `find_boards` runs `run_find_boards` with `StapplyDirectory.load(harness.fixture("stapply"))` and no careers checker (no network). The extract is 3 small software employers, one with its own FEIN and two with a shared FEIN and different names and states; if the foundation's extract has another shape, edit it to this and tell the #30 owner. The stapply fixture has one Greenhouse row matching the own-FEIN employer, whose board list response is the fixture the crawler stage reads. In `test_flow.py`, `test_sources_flow_invariants` asserts after each stage:
  - after `discover`: exactly 3 companies; exactly 1 `company_alias` with source `fein` (the own-FEIN employer; none for the two sharing); `shared_key` holds the shared FEIN (`is_shared_key(conn, "fein", <value>)` is true); one `lca` evidence row per company;
  - after `resolve`: still 3 live companies, every `merged_into` is `NULL`, no `merge_candidate` item (the shared FEIN raised none);
  - after `find_boards`: exactly one `board`, `ats = 'greenhouse'`, `token` lower case, alias `greenhouse:<token>` with source `ats`, and the row is due now (`last_read IS NULL`, `next_read_at IS NULL`, `active = 1`).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sources_e2e tests.pipeline.test_flow -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `__main__.py` (argparse, no logic beyond wiring) and `sources_stages.py`.
- [ ] **Step 4: Run the whole suite** `uv run python -m unittest discover -s tests -t .` and `uv run python tests/mutation_check.py`. Expected: all OK.
- [ ] **Step 5: Real-data check (a manual run, not a test).** With a scratch store `OUTREACH_DB=~/Desktop/outreach-scratch/sources-check/outreach.db` (run `OUTREACH_DB=... uv run python -m outreach.install` first; never the live path) and `OUTREACH_SOURCES_DIR` beside it, run `/usr/bin/time -l uv run python -m outreach.sources discover lca --files ~/Desktop/outreach-scratch/recon/lca/FY2025_Q{1,2,3,4}.xlsx`. Compare with `recon/lca/stats.json` and `small_junior_employers.csv`: 18,273 companies stored; work tier 3 is 6,781; tiers 1 and 2 together 11,492; kept staffing risk low 8,392, medium 2,359, high 741; `cap_exempt` `likely` on 487 of the kept; work tier 1 is the 2,902 product employers plus the cap-exempt ones whose risk is not high. Record wall time and peak memory in the PR body. Limits: over 15 minutes or over 3 GB means converting once to CSV (the spike's `convert.py`, `uv run --with python-calamine`) and passing the CSVs, and the PR says so. Any count off by more than 1% is a bug in Task 4 or 5, not a tolerance. Then `find-boards` with stapply lists downloaded by `fetch stapply` and `--max-careers 0`: report how many tier 1 companies got a board (the research measured 521 of the 2,902 by exact name).
- [ ] **Step 6: Edit the docs** (the `CLAUDE.md` row, `docs/README.md`, the foundation note). `grep -cP "\x{2014}|\x{2013}"` over each edited file prints 0.
- [ ] **Step 7: Check every Done-when line of #31** against the three named tests and the real-data check, then commit `Sources: CLI, end-to-end fixtures and hand-off notes (#31)`.

---

## Hand-off to #21 (crawler)

#21 only updates read state on rows this plan creates; it never inserts a `board` or a `watched_page`. It reads:

| Table and columns | Meaning |
|---|---|
| `board`: `board_id, company_id, ats, token, active` | the board to read; `token` in the foundation's Shared format, `ats` in the schema's set. New rows have `last_read IS NULL`, `next_read_at IS NULL` (due now), `etag = ''`, `ids_fingerprint = ''`, no streaks |
| `watched_page`: `company_id, url` | the careers page to fetch; new rows have `fingerprint = ''` and `next_fetch_at IS NULL` (due now) |
| `company_alias` with `source = 'ats'` | key `<ats>:<token>`; the same board seen from the company |
| `company.status`, `company.merged_into` | read only live companies (`merged_into IS NULL`, status not `closed` or `removed`) |
| `company_work_tier` (this plan's 0002): `tier, promoted` | optional: a crawler that must ration reads can order by effective tier (1, then 2); tier 3 has no boards unless promoted |

#21 owns and writes these columns: on `board`, `last_read, last_read_at, empty_streak, error_streak, streak_since, last_error, etag, ids_fingerprint, next_read_at, change_interval_s, active`; on `watched_page`, `fingerprint, last_fetch_at, last_change_at, next_fetch_at, error_streak`. A board this plan finds twice for one company changes nothing (`ON CONFLICT(ats, token) DO NOTHING`); a board found for a second company raises a `merge_candidate` instead of moving it, so #21 never sees a board change owner.

## Out of scope, with reasons

- **Common Crawl** (design 5.1 source 6): it yields only ATS slugs, no company identity or evidence, and each slug needs an adapter read to verify (#21) before it can be a `board`. The index server failed four times in a row in the research and needs retry and resume machinery. Its measured gain (283 slugs from half of one host) matters only after `find_boards` runs. Follow-up issue: a monthly `commoncrawl` board-directory module beside `StapplyDirectory`, feeding the same `lookup`.
- **Slug probing** (design 5.3 step 2): verification needs the #21 adapters (a hit counts only when the board's company name or posting location fits).
- **Reading boards and watched pages** (#21): this plan only creates `board` and `watched_page` rows. **Merging two stored companies** (the Needs-you confirm and its undo through `merged_into`): #16 owns the action. **Size and stage lookups for top-ranked companies**, E-Verify checks, **ranking and tiers' use in ranking**: later streams. **SPV and fuzzy Form D matching**, the bulk EIN-to-CIK join over `submissions.zip`: not run in the research at scale. **launchd jobs** for these commands: #17's deploy steps.

## Design questions a builder could not settle alone

1. **Who widens the evidence vocabulary?** `outreach/rules/facts.py` mirrors the CHECK vocabularies and rejects `lca`, `hn` and the rest, and the foundation plan does not list it. Task 1 widens it here, which changes `ruleset_version()` (the file is in `RULE_FILES`). If the #30 owner prefers to do it in the foundation branch, Task 1 shrinks to the migration.
2. **Migration 0002.** The work tier and its promote flag, and two lookup indexes, have no home in the foundation's 0001. The plan adds `0002_work_tier.sql`; if another stream also adds a 0002, one renumbers on rebase. The alternative is folding these three statements into 0001 before #30 merges.
3. **HN post link.** A post with no usable site, no parsed stage and no location leaves only `company_source (hn, hn:<name key>)`; the post URL is kept only as the `source_ref` of the evidence rows that exist. If the people stream needs every post link ("HN posts that ask for email", design 10), a small `company_post` table is the fix.
4. **Name-only stapply boards.** Board creation by exact normalized name has no state to check (stapply has none), so it is weaker than the identity rule in 5.2. The plan follows 5.3 and relies on the first read to expose a wrong board. If Devanshu wants a confirmation step, it is a review item per hit (about 500 on the real data).
5. **Source evidence is labelled as rules.** `record_evidence` appends its event with actor `rules`, so LCA, HN and Form D evidence events read as rule-made. The plan reuses it unchanged; a `source` actor would need a schema change to `event.actor`.

## Self-review

- **Spec coverage.** 5.1 sources: LCA (Tasks 4 to 6), stapply (14), Form D (11), HN (12), by hand (13), Common Crawl (deferred with reasons), the radar and dashboard import (#17, not here). 5.1 tiering: Tasks 5 and 8. 5.2 identity: Tasks 7 and 9, with shared keys, subsidiaries and items each having a named test. 5.3: Tasks 10, 14, 15 (slug probing deferred). 5.4: Task 9 (`test_no_source_writes_size_evidence`), Task 7 (`test_subsidiary_never_inherits_size`), the `size_check` item. Section 12 and 14 step 5: Tasks 1 and 16. Issue #31's three Done-when lines: Task 16's three named tests. Foundation Task 6: `check_source` runs for `LcaSource`, `FormDSource`, `HnSource` and `ByHandSource` (Tasks 6, 11, 12, 13); `StapplyDirectory` is a board directory, not a `Source`, so it has no `check_source`; the three stages are registered in Task 16. Shared formats: Task 10 (`normalize_token`, alias `source='ats'`) and Task 14 (`board_token`).
- **Step scan.** Every code step names its file, signature and the choice the tests leave open; the only algorithm bodies are the three the tests do not fix (identity matching order, LCA filters ported from `analyze.py`, the careers flow).
- **Type consistency.** `Candidate`, `EvidenceDraft`, `Source`, `add_alias`, `mark_shared_key`, `is_shared_key` are the foundation's. `SharedKey` (Task 4) is used in Tasks 6 and 8; `BoardRef` (Task 10) in 14 and 15; `Resolution` and `raise_merge_candidate` (Task 7) in 8 to 15; `effective_tier` (Task 8) in 13 and 14; `table_marks` (Task 7) in every idempotency test; `creates_companies` (Task 8) in Task 11.
- **Review Focus.** Each of the five has a named test (listed under Review Focus).
- **Proportion.** About 600 long lines against a spec section of about 100 lines, because this is eight modules with fixtures; most code blocks are tests' key assertions and signatures, not bodies.
