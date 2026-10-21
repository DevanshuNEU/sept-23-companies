# Pipeline foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the one schema change and the shared Python contracts that every stream of the opportunity
pipeline builds on, so the data, reader, ranking, API and web streams can then run in parallel without touching
each other's files.

**Architecture:** The schema change edits `outreach/store/migrations/0001_initial.sql` in place (no live store
exists until #17 part B; this is the standing #4 ruling). This plan is its only owner: after it merges, any further
schema change is a new numbered migration owned by one stream and renumbered on rebase. The contracts are small
typed modules (dataclasses and Protocols) with no behavior beyond validation, one per stream package.

**Tech Stack:** Python 3.14 (Homebrew), standard library only in `outreach/`, SQLite 3.51 STRICT tables, `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-20-opportunity-pipeline-design.md` (sections 4, 5, 6, 7, 9, 11, 12).

**Branch:** `DevanshuNEU/pipeline-foundation`, from the #4 part 2 branch once its final fix wave is committed
(it rebases on master after #4 part 2 merges). Issue: #30.

## Global Constraints

- No live store: never open `~/Library/Application Support/outreach/outreach.db`; tests use scratch stores (`tests/storetest.py`).
- Timestamps are canonical UTC `strftime('%Y-%m-%dT%H:%M:%SZ', x)`; dates are `date(x)`; every table is STRICT.
- Writers use `INSERT ... ON CONFLICT DO NOTHING`, never `INSERT OR IGNORE` or `INSERT OR REPLACE`.
- Every derived row records the version that produced it (source version, reader version, ruleset version, ranker version).
- Hidden only with evidence (#9): won't sponsor, citizenship or clearance, current student or 2027 graduation required, over 300 people with evidence. Everything uncertain is shown and flagged.
- No em dashes or en dashes in code, comments, docs or commit messages.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Shared formats (every stream and #17 use these exactly)

- `posting.source_key` is `<ats>:<token>:<native_id>`; postings with no board keep `url:<url>`.
- `board.token`: the ATS slug as the ATS spells it, lower case, for Greenhouse, Lever, Ashby and the rest; Workday
  `<tenant>.wd<N>/<site>`; Oracle `<host>/<siteNumber>`; iCIMS the board host.
- `company_alias` for a board uses `source='ats'` and alias key `<ats>:<token>`.
- `job_run.job` values the crawler writes are `read_boards` and `watch_pages` (not `crawl`).
- The read queue belongs to #32 alone: no other stream inserts, updates or deletes `read_queue` rows. #32's
  `enqueue_missing` finds stored bodies with no row for the current reader version, so a stream that stores a new
  or changed body needs to do nothing else.

## Review Focus

1. A re-run of any source or reader over the same input writes no new rows (idempotency through unique keys), and a CHECK failure is never swallowed.
2. A shared FEIN or shared domain (payroll providers, a parent's domain) can be recorded but can never be the key that merges two companies.
3. A reading whose quote is not in the stored body can never be stored as verified.
4. An outcome touch can be logged twice by a double click without creating two touches (idempotency key).
5. Every new `job_run.job` and `review_item.kind` value used by a stream exists in the schema, so no stream fails its first insert in production.

---

## File Structure

| File | Responsibility |
|---|---|
| `outreach/store/migrations/0001_initial.sql` | Modify: new CHECK values and new tables listed in Task 1 |
| `outreach/sources/__init__.py`, `outreach/sources/base.py` | Create: `Candidate`, `EvidenceDraft` reuse, `Source` Protocol |
| `outreach/crawl/__init__.py`, `outreach/crawl/base.py` | Create: `Validators`, `JobRef`, `ReadResult`, `Adapter` Protocol |
| `outreach/reader/__init__.py`, `outreach/reader/types.py` | Create: reading schema constants, `ReadRequest`, `FieldReading`, `PostingReading` |
| `outreach/ranking/__init__.py`, `outreach/ranking/types.py` | Create: `CompanyFacts`, `SignalValue`, `Signal` Protocol |
| `outreach/rules/facts.py`, `outreach/rules/rebuild.py` | Modify: widen `FACTS` and `SOURCE_KINDS`; `record_evidence(..., actor='rules')` |
| `outreach/store/writes.py` | Modify: `add_alias`, `mark_shared_key`, `log_touch` |
| `tests/test_store_schema.py` | Modify: schema tests for Task 1 |
| `tests/test_contracts.py` | Create: contract validation tests |
| `tests/test_store_writes.py` | Create: tests for the new write helpers |
| `tests/contracts.py`, `tests/pipeline/` | Create: contract checks and the flow test every stream extends (Task 6) |
| `docs/superpowers/plans/2026-10-19-store-and-rules.md` | Modify: "For downstream issues" points at this plan for the new tables |

## Task 1: Schema for the pipeline

**Files:**
- Modify: `outreach/store/migrations/0001_initial.sql`
- Test: `tests/test_store_schema.py`

**Interfaces:**
- Produces (tables and values every stream uses, exactly as named):
  - `board.ats` adds `pinpoint`, `teamtailor`, `jazzhr`, `paycom`, `icims`, `personio`, `join_com`, `cornerstone`,
    `successfactors`. `board` adds columns `etag TEXT NOT NULL DEFAULT ''`, `ids_fingerprint TEXT NOT NULL DEFAULT ''`,
    `next_read_at TEXT` (UTC, NULL means due now), `change_interval_s INTEGER CHECK (change_interval_s > 0)`.
    `board.last_read` and `board_read.result` add `'unchanged'` (an unchanged read clears both streaks like ok).
  - `company_alias.source` adds `'fein'`, `'dba'`, `'legal_name'`, `'ats'`, `'formd'`, `'linkedin'`.
  - New table `shared_key(kind TEXT CHECK (kind IN ('fein','domain')), value TEXT, reason TEXT NOT NULL,
    recorded_at, PRIMARY KEY (kind, value))`: a key seen on more than one employer; identity never merges on it.
  - `evidence.fact` adds `'lca'`, `'cap_exempt'`, `'staffing_risk'`, `'funding'`, `'headcount'`.
    `evidence.source_kind` adds `'stapply'`, `'commoncrawl'`, `'hn'`, `'simplify'`.
    CHECKs: `fact='lca'` stores JSON in `value` (`json_valid(value)`) with keys `fy`, `software`, `jr`, `level1`,
    `newemp_jr` and optional `median_jr_wage` (USD per year); `fact='cap_exempt'` value in `('likely','confirmed','no')`; `fact='staffing_risk'` value in
    `('low','medium','high')`; `fact='funding'` value is JSON with `filed_on`, `amount_usd` (nullable), `kind`;
    `fact='headcount'` uses `num_low`/`num_high` like size.
  - New table `source(source_name TEXT PRIMARY KEY, version TEXT NOT NULL, last_run_id INTEGER REFERENCES job_run)`.
  - New table `company_source(company_id, source_name REFERENCES source, source_ref TEXT NOT NULL,
    first_seen_at, last_seen_at, PRIMARY KEY (company_id, source_name, source_ref))`.
  - New table `watched_page(company_id PRIMARY KEY, url TEXT NOT NULL, fingerprint TEXT NOT NULL DEFAULT '',
    last_fetch_at, last_change_at, next_fetch_at, error_streak INTEGER NOT NULL DEFAULT 0)`.
  - New table `read_queue(posting_id, body_sha256 TEXT (64 hex), reader_version TEXT, priority INTEGER NOT NULL,
    state TEXT CHECK (state IN ('queued','leased','done','refused')), lease_until, attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '', enqueued_at, PRIMARY KEY (posting_id, body_sha256, reader_version))`,
    index on `(state, priority DESC, enqueued_at)`.
  - New table `posting_reading(posting_id, body_sha256, reader_version, read_at, fields TEXT CHECK (json_valid(fields)),
    verified INTEGER CHECK (verified IN (0,1)), refused_fields TEXT NOT NULL DEFAULT '[]', PRIMARY KEY (posting_id,
    body_sha256, reader_version))`. `fields` is the `PostingReading.to_json()` of Task 3.
  - New table `work_tier(company_id INTEGER PRIMARY KEY REFERENCES company, tier INTEGER NOT NULL CHECK (tier IN
    (1,2,3)), promoted INTEGER NOT NULL DEFAULT 0 CHECK (promoted IN (0,1)), reason TEXT NOT NULL, set_at)`: which
    companies get board finding first (#31 design 5.1); tier 3 is staffing-flagged, worked only when promoted.
  - New table `company_post(post_id INTEGER PRIMARY KEY, company_id REFERENCES company, source_name REFERENCES
    source, url TEXT NOT NULL, posted_at, text TEXT NOT NULL DEFAULT '', asks_email INTEGER NOT NULL DEFAULT 0
    CHECK (asks_email IN (0,1)), UNIQUE (source_name, url))`: HN and similar posts, kept so the people plan can link
    and reply to them.
  - Indexes: `evidence_domain ON evidence(value) WHERE fact = 'domain'` (identity lookups by domain) and
    `company_source_ref ON company_source(source_name, source_ref)`.
  - `outreach/rules/rebuild.py`: `_stored_decision` becomes public `stored_decision` (ranking and the reader both
    read it).
  - `event.actor` adds `'source'` and `'reader'`; `record_evidence` gains an `actor` keyword (default `'rules'`), so
    LCA, HN and Form D evidence is labeled by its source.
  - `outreach/rules/facts.py` `FACTS` and `SOURCE_KINDS` widen to the new evidence values above, so `EvidenceDraft`
    accepts them. This changes `ruleset_version()` (facts.py is a rule file); expected, and the rebuild after
    install handles it.
  - `posting_body` adds `body_sha256 TEXT NOT NULL DEFAULT ''` (sha256 hex of the body; `''` for rows written
    before it existed, filled lazily by the crawler).
  - `decision` adds `provisional INTEGER NOT NULL DEFAULT 1 CHECK (provisional IN (0,1))` (1 until a verified
    reading agrees) and `reader_version TEXT` (NULL while provisional).
  - New table `application(application_id, company_id, posting_id, applied_on (date), url TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT 'open' CHECK (outcome IN ('open','screen','interview','offer','rejected','closed')),
    outcome_at, revision)`.
  - New table `outreach_touch` (every channel, for outcome learning):
    `touch_id`, `person_id REFERENCES person` (NULL only for channel `application`), `company_id`, `channel CHECK (channel IN ('linkedin_request',
    'linkedin_message','email','application','hn_reply','other'))`, `posting_id`, `email_touch_id REFERENCES touch`
    (only for channel 'email'), `sent_at`, `accepted_at`, `replied_at`, `reply_kind` (the #9 tags), `referred_at`,
    `interview_at`, `offer_at`, `quiet_at`, `context TEXT CHECK (json_valid(context))` (lane, role kind, role age in
    days, ranker version, signal values at send time), `idempotency_key TEXT UNIQUE`, `revision`, `created_at`.
    CHECKs: an outcome date is never before `sent_at`; `quiet_at` and `replied_at` are not both set; `person_id` is NULL
    only when `channel = 'application'`. `outreach_touch` is the one outcome record per touch: the LinkedIn view's
    `linkedin_log` keeps the action history, and the API writes both in one transaction for a LinkedIn action, so
    progress counts each touch once (it reads `outreach_touch` only).
  - New table `company_score(company_id, ranker_version, score REAL, signals TEXT CHECK (json_valid(signals)),
    lanes TEXT CHECK (json_valid(lanes)), scored_at, PRIMARY KEY (company_id, ranker_version))`.
  - `job_run.job` adds `'discover'`, `'resolve'`, `'find_boards'`, `'read_boards'`, `'watch_pages'`, `'read_postings'`,
    `'rank'`, `'plan'`, `'prepare'`.
  - `review_item.kind` adds `'merge_candidate'`, `'reader_disagrees'`, `'careers_changed'`, `'size_check'`;
    `review_item.raised_by` adds `'source'`, `'reader'`, `'ranker'`.
  - `event.entity` adds `'source'`, `'reading'`, `'outreach_touch'`, `'score'`.
  - `daily_list_entry.list_group` adds `'intern_junior'`, `'reason_no_role'`; `daily_list.capacity` CHECK widens to
    `BETWEEN 0 AND 60`; `daily_list_entry` adds `person_id REFERENCES person` and `channel` (nullable, same values as
    `outreach_touch.channel`).

- [ ] **Step 1: Write the failing tests** in `tests/test_store_schema.py`, one per item above, each using the
  scratch store from `tests/storetest.py`. Names and assertions:
  - `test_board_accepts_unchanged_read_and_new_ats`: insert a `teamtailor` board, set `last_read='unchanged'`; passes.
  - `test_shared_key_is_unique_per_kind_value`: second insert of `('fein','12-3456789')` with ON CONFLICT DO NOTHING changes nothing.
  - `test_evidence_lca_value_must_be_json`: `fact='lca', value='x'` raises `sqlite3.IntegrityError`.
  - `test_read_queue_lease_states`: `state='leased'` with `lease_until` set inserts; `state='bogus'` raises.
  - `test_posting_reading_fields_must_be_json`.
  - `test_decision_defaults_provisional`: a decision row inserted without the column reads `provisional = 1`.
  - `test_outreach_touch_outcome_not_before_sent`: `replied_at < sent_at` raises.
  - `test_outreach_touch_idempotency_key_unique`.
  - `test_outreach_touch_email_needs_email_channel`: `email_touch_id` set with `channel='linkedin_request'` raises.
  - `test_new_job_and_review_kinds_insert`: one `job_run` per new job and one `review_item` per new kind insert.
  - `test_daily_list_entry_person_and_channel`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_store_schema -v`. Expected: the new tests FAIL (unknown column, CHECK).
- [ ] **Step 3: Edit `0001_initial.sql`** with the definitions above, keeping comments in the file's style.
- [ ] **Step 4: Run the whole suite** `uv run python -m unittest discover -s tests -t .` and the mutation check
  `uv run python tests/mutation_check.py`. Expected: all OK, mutation `missed 0` (update anchors if lines moved).
- [ ] **Step 5: Commit** `Store: pipeline tables and values for sources, reads, readings, scores and touches (#30)`.

## Task 2: Source and crawl contracts

**Files:**
- Create: `outreach/sources/__init__.py`, `outreach/sources/base.py`, `outreach/crawl/__init__.py`, `outreach/crawl/base.py`
- Test: `tests/test_contracts.py`

**Interfaces:**
- Consumes: `EvidenceDraft` from `outreach/rules/rebuild.py` (re-exported, not redefined).
- Produces:
  - `@dataclass(frozen=True) class Candidate: name: str; domain: str | None; keys: dict[str, str]` (allowed keys
    `fein`, `dba`, `legal_name`, `ats` as `"<ats>:<token>"`, `linkedin`, `formd_cik`); `evidence: tuple[EvidenceDraft, ...]`;
    `source_ref: str`; `__post_init__` raises `ValueError` on an unknown key, an empty name, or a domain with `@`, upper case or no dot.
  - `class Source(Protocol): name: str; version: str; def discover(self, since: str | None) -> Iterator[Candidate]`.
  - `@dataclass(frozen=True) class Validators: etag: str = ''; ids_fingerprint: str = ''`.
  - `@dataclass(frozen=True) class JobRef: native_id: str; title: str; url: str; location: str; country: str | None;
    posted_on: str | None; body: str | None` (body only when the list response carries it).
  - `@dataclass(frozen=True) class ReadResult: result: Literal['ok','empty','error','unchanged']; jobs: tuple[JobRef, ...];
    validators: Validators; http_status: int | None = None; error: str = ''`; `__post_init__`: `unchanged` and
    `error` carry no jobs; `error` needs a non-empty `error`; `empty` has no jobs; `ok` has at least one.
  - `class Adapter(Protocol): ats: str; def list(self, token: str, validators: Validators) -> ReadResult;
    def body(self, token: str, job: JobRef) -> str | None`.
  - `def ids_fingerprint(native_ids: Iterable[str]) -> str`: sha256 hex of the sorted ids joined by `\n`.

- [ ] **Step 1: Write the failing tests** `test_candidate_rejects_unknown_key`, `test_candidate_rejects_bad_domain`,
  `test_read_result_invariants` (each bad combination raises `ValueError`; each good one constructs),
  `test_ids_fingerprint_is_order_free` (`ids_fingerprint(['b','a']) == ids_fingerprint(['a','b'])`, and differs for `['a']`).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_contracts -v`. Expected: FAIL (module not found).
- [ ] **Step 3: Implement** the dataclasses and Protocols above.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Contracts: sources and board adapters (#30)`.

## Task 3: Reader and ranking contracts

**Files:**
- Create: `outreach/reader/__init__.py`, `outreach/reader/types.py`, `outreach/ranking/__init__.py`, `outreach/ranking/types.py`
- Test: `tests/test_contracts.py`

**Interfaces:**
- Produces:
  - `READING_SCHEMA_VERSION = 1`. `FIELDS`: the ordered names `engineering`, `level`, `years_low`, `years_high`,
    `sponsorship`, `citizenship_or_clearance`, `student_or_grad_window`, `start_date`, `location`, `remote`, `pay`.
  - `ALLOWED`: `level` in `('intern','new_grad','early_career','mid','senior_plus','unknown')`; `sponsorship` in
    `('wont_sponsor','will_sponsor','opt_only','silent','unknown')`; `engineering`, `citizenship_or_clearance`,
    `student_or_grad_window`, `remote` in `('yes','no','unknown')`; `years_low`, `years_high` int 0 to 30 or `None`;
    `start_date`, `location`, `pay` free text or `''`.
  - `@dataclass(frozen=True) class FieldReading: value: str | int | None; quote: str` (quote `''` only when value is
    `unknown`, `silent`, `None` or `''`).
  - `@dataclass(frozen=True) class PostingReading: posting_id: int; body_sha256: str; reader_version: str;
    fields: dict[str, FieldReading]`; `from_json(obj) -> PostingReading` raises `ValueError` on a missing field, an
    extra field, a value outside `ALLOWED`, or a quote missing where one is required; `to_json() -> dict`.
  - `def quote_in_body(quote: str, body: str) -> bool`: true when `quote`, after collapsing whitespace and lower
    casing both, is a substring of `body`, and the quote has at least 3 words (a paraphrase or a single word never passes).
  - `@dataclass(frozen=True) class ReadRequest: posting_id: int; body_sha256: str; reader_version: str; title: str; body: str`.
  - `@dataclass(frozen=True) class CompanyFacts`: `company_id: int`, `lanes: tuple[str, ...]`, `best_posting_id: int | None`,
    `best_role_age_days: int | None`, `everify: str | None`, `cap_exempt: str | None`, `staffing_risk: str | None`,
    `lca_jr_recent: int`, `lca_level1_recent: int`, `pay_level: int | None` (1 to 4), `size_low: int | None`,
    `size_high: int | None`, `stage: str | None`, `funded_months_ago: int | None`, `reachable_person: bool`.
  - `@dataclass(frozen=True) class SignalValue: value: float; reason: str` (`value` between -1.0 and 1.0).
  - `class Signal(Protocol): name: str; def score(self, facts: CompanyFacts) -> SignalValue`.

- [ ] **Step 1: Write the failing tests** `test_posting_reading_round_trip`, `test_posting_reading_rejects_extra_field`,
  `test_posting_reading_needs_quote_for_known_value`, `test_quote_in_body_collapses_whitespace`
  (`quote_in_body('we do  not\nsponsor visas', 'We do not sponsor visas.')` is True),
  `test_quote_in_body_rejects_paraphrase` (`quote_in_body('no sponsorship offered', 'We do not sponsor visas.')` is False),
  `test_quote_in_body_rejects_short_quote` (`quote_in_body('sponsor', ...)` is False),
  `test_signal_value_bounds` (`SignalValue(1.5, 'x')` raises).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_contracts -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the types above.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Contracts: posting readings and ranking signals (#30)`.

## Task 4: Shared write helpers

**Files:**
- Modify: `outreach/store/writes.py`
- Create: `tests/test_store_writes.py`

**Interfaces:**
- Consumes: Task 1 tables.
- Produces:
  - `add_alias(conn, company_id: int, alias: str, source: str) -> bool`: writes `company_alias` with `alias_key =
    name_key(alias)` for name-like sources and the raw lower-cased value for `fein`, `ats`, `linkedin`; returns False
    if the key already belongs to this company; raises `ValueError` if it belongs to another company.
  - `mark_shared_key(conn, kind: str, value: str, reason: str) -> None` (idempotent).
  - `is_shared_key(conn, kind: str, value: str) -> bool`.
  - `log_touch(conn, *, person_id: int | None, company_id: int, channel: str, sent_at: str, context: dict,
    posting_id: int | None = None, email_touch_id: int | None = None, idempotency_key: str) -> int`: returns the
    touch id, the existing one when the key was used before; appends an `event` (entity `outreach_touch`, kind `sent`).
  - `record_outcome(conn, touch_id: int, *, kind: str, at: str, reply_kind: str | None = None, revision: int) -> int`:
    `kind` in `('accepted','replied','referred','interview','offer','quiet')`; sets the matching column (`replied` clears `quiet_at`, since a late reply beats quiet); checks the
    revision (raises `LookupError` on a mismatch); returns the new revision; appends an event.

- [ ] **Step 1: Write the failing tests** `test_add_alias_refuses_other_company`, `test_add_alias_fein_kept_raw`,
  `test_shared_key_round_trip`, `test_log_touch_is_idempotent` (same key twice returns the same id and one row),
  `test_record_outcome_checks_revision`, `test_record_outcome_refuses_before_sent`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_store_writes -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the helpers with single statements, as the rest of `writes.py` does.
- [ ] **Step 4: Run** the full suite. Expected: all OK.
- [ ] **Step 5: Commit** `Store: alias, shared-key and touch helpers (#30)`.

## Task 5: Hand-off notes for the streams

**Files:**
- Modify: `docs/superpowers/plans/2026-10-19-store-and-rules.md` ("For downstream issues": one paragraph pointing at this plan's tables and the rule that further schema changes are new numbered migrations, one owner each, renumbered on rebase)
- Modify: `docs/README.md` (rows for this plan and the stream plans)

- [ ] **Step 1: Edit both docs.** No em or en dashes.
- [ ] **Step 2: Run** `grep -c "[—–]" docs/README.md docs/superpowers/plans/2026-10-19-store-and-rules.md`. Expected: `0` for each.
- [ ] **Step 3: Commit** `Docs: foundation hand-off for the pipeline streams (#30)`.

## Task 6: Contract checks and the pipeline flow test

Every stream proves its parts against the same checks, and one flow test runs the whole pipeline on fixtures, so a
break between streams fails at the PR that causes it, not at the switch.

**Files:**
- Create: `tests/contracts.py`, `tests/pipeline/__init__.py`, `tests/pipeline/harness.py`, `tests/pipeline/test_flow.py`
- Create: `tests/fixtures/pipeline/` (a tiny LCA extract of 3 employers, one board list response, one posting body)

**Interfaces:**
- Consumes: Tasks 1 to 4.
- Produces:
  - `check_source(tc: unittest.TestCase, source: Source, since: str | None = None) -> list[Candidate]`: asserts
    `name` and `version` are non-empty, every item is a `Candidate`, and a second `discover` over the same input
    yields equal candidates (deterministic).
  - `check_adapter(tc, adapter: Adapter, token: str) -> None`: given an adapter wired to a fake HTTP client, asserts
    a first read is `ok` or `empty`, a read with the returned validators is `unchanged` when the fake returns 304 or
    the same ids, and a 500 is `error` with no jobs.
  - `check_signal(tc, signal: Signal, facts: CompanyFacts) -> SignalValue`: asserts the value is within -1.0 to 1.0
    and the reason is non-empty and has no em or en dash.
  - `PipelineHarness` in `tests/pipeline/harness.py`: `.conn` (a scratch store), `.fixture(name) -> Path`, and
    `.stages`: an ordered list of `(name, callable)` that streams append to. Stage names, in order: `discover`,
    `resolve`, `find_boards`, `read_boards`, `read_postings`, `rules`, `rank`, `plan`.
  - `tests/pipeline/test_flow.py::test_flow_runs_every_registered_stage`: runs the stages registered so far against
    the fixtures and asserts the invariants each stage declares. Each stream's PR registers its stage and adds its
    invariant, for example sources: "3 companies, one alias per FEIN, the shared FEIN marked shared"; ranking: "the
    employer with a live new-grad posting that says it sponsors ranks first and its reasons name both".

- [ ] **Step 1: Write the failing tests** `tests/test_contracts.py::test_check_source_flags_nondeterminism` (a
  source that yields a random name fails `check_source`), `::test_check_adapter_needs_unchanged` (an adapter that
  ignores validators fails), and `test_flow_runs_every_registered_stage` with no stages registered (passes trivially
  and asserts the store migrated).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_contracts tests.pipeline.test_flow -v`. Expected: the contract tests FAIL (helpers missing).
- [ ] **Step 3: Implement** the helpers and the harness.
- [ ] **Step 4: Run** the full suite. Expected: all OK.
- [ ] **Step 5: Commit** `Tests: contract checks and the pipeline flow test every stream extends (#30)`.

**Rule for every stream plan:** each new Source, Adapter and Signal gets a `check_*` test; each stream registers
its stage in the harness and adds one invariant to the flow test; the flow test must stay green on every PR.
