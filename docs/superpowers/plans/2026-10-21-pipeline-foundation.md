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
- Commit trailer: the one the executing session is given.

## Controller rulings (2026-10-21, integration review). These override the task text below where they disagree.

1. **One `body_sha256`.** `outreach/store/writes.py::body_sha256(text: str) -> str` (Task 4) is sha256 hex of the body
   after collapsing every run of whitespace to one space and stripping the ends (case kept). The crawler (#21) and the
   reader (#32) import it; neither defines its own. Every `body_sha256` column in the store (`posting_body`,
   `read_queue`, `posting_reading`) holds this value.
2. **One work-tier table: `work_tier`** (Task 1). #31 writes it, ranking reads it. It carries `source_version` so a
   rerun of a source can tell which version set a tier.
3. **`outreach_touch.context` has one key set** (Task 1), written only through #11's `build_context`: `source`, `lane`,
   `roleKind`, `roleAgeDays`, `rankerVersion`, `signals`, `roleNamed`. `source` is `plan` for a touch logged from a plan
   row and `own` for everything else; #15's monthly quota counts `source = 'plan'`.
4. **Board provenance.** `board.found_by` records how a board was found (Task 1) and `ReadResult.board_name` carries
   the org name an ATS reports (Task 2), so #21 can raise the "board match doubtful" item on the first read of a
   name-only stapply match, and ranking can hold back hides from that board until the item is resolved.
5. **One HTTP layer and one fake** (Task 2): `outreach/net.py` and `tests/fakes.py`. #31 and #21 import them. One
   User-Agent policy (Shared formats): no personal email or contact address anywhere.
6. **Harness convention** (Task 6): stated once, there. #30 also owns the `rules` stage.
7. **Undone touches** (Task 1, Task 4): `outreach_touch.undone_at`; an undone touch is not a touch for any count, and
   no outcome can be recorded on it.

## Shared formats (every stream and #17 use these exactly)

- `posting.source_key` is `<ats>:<token>:<native_id>`; postings with no board keep `url:<url>`.
- `board.token`: the ATS slug, lower case, for Greenhouse, Lever, Ashby and the rest; Workday `<tenant>.wd<N>/<site>`
  and Oracle `<host>/<siteNumber>` keep the host and tenant lower case but keep `site` and `siteNumber` exactly as the
  ATS spells them (Oracle's finder is case sensitive: `eeho.fa.us2.oraclecloud.com/CX_1`); iCIMS the board host, lower
  case. One grammar for every stream: slug `^[a-z0-9][a-z0-9._-]*$` (no `%`), Workday
  `^[a-z0-9-]+\.wd\d+/[A-Za-z0-9_-]+$`, Oracle `^[a-z0-9.-]+\.oraclecloud\.com/[A-Za-z0-9_-]+$`, iCIMS
  `^[a-z0-9.-]+\.icims\.com$`. `outreach/crawl/base.py::valid_token(ats, token) -> bool` (Task 2) is the one
  implementation; #31's `normalize_token` and #21's `safe_token` call it.
- **User-Agent:** `outreach-crawler/1.0 (personal job search)` for every request any stream makes. No email address, no
  contact URL, no name. (DOL's browser-profile requests send a Chrome header set instead, Task 2.)
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
| `outreach/crawl/__init__.py`, `outreach/crawl/base.py` | Create: `Validators`, `JobRef`, `ReadResult`, `Adapter` Protocol, `valid_token` |
| `outreach/net.py`, `tests/fakes.py` | Create: the one HTTP layer (`Response`, `FetchError`, `HttpClient`, `UrllibClient`, `SpacedClient`) and the one `FakeClient` |
| `outreach/reader/__init__.py`, `outreach/reader/types.py` | Create: reading schema constants, `ReadRequest`, `FieldReading`, `PostingReading` |
| `outreach/ranking/__init__.py`, `outreach/ranking/types.py` | Create: `CompanyFacts`, `SignalValue`, `Signal` Protocol |
| `outreach/rules/facts.py`, `outreach/rules/rebuild.py` | Modify: widen `FACTS` and `SOURCE_KINDS` and validate the new fact values; `record_evidence(..., actor='rules')`; public `stored_decision` |
| `outreach/store/writes.py` | Modify: `body_sha256`, `add_alias`, `mark_shared_key`, `log_touch`, `record_outcome`, `undo_touch` |
| `tests/test_store_schema.py` | Modify: schema tests for Task 1 |
| `tests/test_contracts.py` | Create: contract validation tests |
| `tests/test_store_writes.py` | Create: tests for the new write helpers |
| `tests/contracts.py`, `tests/pipeline/` | Create: contract checks, the harness, the `rules` stage (`stages_rules.py`) and the flow test every stream extends (Task 6) |
| `docs/superpowers/plans/2026-10-19-store-and-rules.md` | Modify: "For downstream issues" points at this plan for the new tables |

## Task 1: Schema for the pipeline

**Files:**
- Modify: `outreach/store/migrations/0001_initial.sql`
- Test: `tests/test_store_schema.py`

**Interfaces:**
- Produces (tables and values every stream uses, exactly as named):
  - `board.ats` adds `pinpoint`, `teamtailor`, `jazzhr`, `paycom`, `icims`, `personio`, `join_com`, `cornerstone`,
    `successfactors`. `board` adds columns `etag TEXT NOT NULL DEFAULT ''`, `ids_fingerprint TEXT NOT NULL DEFAULT ''`,
    `next_read_at TEXT` (UTC, NULL means due now), `change_interval_s INTEGER CHECK (change_interval_s > 0)`,
    `found_by TEXT NOT NULL DEFAULT 'import' CHECK (found_by IN ('stapply_name','slug_probe','careers_page','import','hand'))`
    (how the board was found: #31 sets it, #21 and ranking read it).
    `board.last_read` and `board_read.result` add `'unchanged'` (an unchanged read clears both streaks like ok).
  - `company_alias.source` adds `'fein'`, `'dba'`, `'legal_name'`, `'ats'`, `'formd'`, `'linkedin'`.
  - New table `shared_key(kind TEXT CHECK (kind IN ('fein','domain')), value TEXT, reason TEXT NOT NULL,
    recorded_at, PRIMARY KEY (kind, value))`: a key seen on more than one employer; identity never merges on it.
  - `evidence.fact` adds `'lca'`, `'cap_exempt'`, `'staffing_risk'`, `'funding'`, `'headcount'`.
    `evidence.source_kind` adds `'stapply'`, `'commoncrawl'`, `'hn'`, `'simplify'`.
    CHECKs: `fact='lca'` stores JSON in `value` (`json_valid(value)`) with keys `fy`, `software`, `jr`, `level1`,
    `newemp_jr` and optional `median_jr_wage` (USD per year, written by #31, read by ranking); `fact='cap_exempt'` value in `('likely','confirmed','no')`; `fact='staffing_risk'` value in
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
    (1,2,3)), promoted INTEGER NOT NULL DEFAULT 0 CHECK (promoted IN (0,1)), reason TEXT NOT NULL, source_version TEXT
    NOT NULL DEFAULT '', set_at)` (the only work-tier table; no stream defines another): which
    companies get board finding first (#31 design 5.1); tier 3 is staffing-flagged, worked only when promoted.
  - New table `company_post(post_id INTEGER PRIMARY KEY, company_id REFERENCES company, source_name REFERENCES
    source, url TEXT NOT NULL, posted_at, text TEXT NOT NULL DEFAULT '', asks_email INTEGER NOT NULL DEFAULT 0
    CHECK (asks_email IN (0,1)), UNIQUE (source_name, url))`: HN and similar posts, kept so the people plan can link
    and reply to them.
  - Indexes: `evidence_domain ON evidence(value) WHERE fact = 'domain'` (identity lookups by domain) and
    `company_source_ref ON company_source(source_name, source_ref)`.
  - `outreach/rules/rebuild.py`: the private `_stored` becomes public `stored_decision` (keep `_stored =
    stored_decision` so nothing else breaks); ranking and the reader both read it. Neither of their plans does this
    rename.
  - `event.actor` adds `'source'` and `'reader'`; `record_evidence` gains an `actor` keyword (default `'rules'`), so
    LCA, HN and Form D evidence is labeled by its source.
  - `outreach/rules/facts.py` `FACTS` and `SOURCE_KINDS` widen to the new evidence values above, so `EvidenceDraft`
    accepts them, and `EvidenceDraft` validates the values the schema CHECKs: `lca` JSON with the keys `fy, software,
    jr, level1, newemp_jr` and optional `median_jr_wage`; `cap_exempt` in `likely|confirmed|no`; `staffing_risk` in
    `low|medium|high`; `funding` JSON with keys `filed_on, amount_usd, kind`; `headcount` takes `num_low` like `size`.
    This plan owns those tuples and checks (#31 adds none). This changes `ruleset_version()` (facts.py is a rule
    file); expected, and the rebuild after install handles it.
  - From the #15 plan (Task 1 there, copy its definitions and CHECKs exactly): `person_source` (public source pages
    per person; LinkedIn hosts refused by CHECK), `linkedin_draft` (the one LinkedIn note table; Claude can never
    confirm a reason line), and its two `daily_list_entry` guards.
  - From the #11 plan (Task 4 there, copy exactly): `api_request` (idempotency store), `mx_check`, `win_seen`,
    `watch_dismissal`. Its `linkedin_note` table is dropped: the API reads and writes `linkedin_draft`.
  - `posting_body` adds `fetched_at TEXT` (UTC, when the body was last fetched; NULL for imported bodies;
    integration review M8) and `body_sha256 TEXT NOT NULL DEFAULT '' CHECK (body_sha256 = '' OR length(body_sha256) = 64)`
    (`body_sha256()` of Task 4; `''` for rows written before it existed, filled lazily by the crawler).
  - `decision` adds `provisional INTEGER NOT NULL DEFAULT 1 CHECK (provisional IN (0,1))` (1 until a verified
    reading agrees) and `reader_version TEXT` (NULL while provisional).
  - New table `application(application_id, company_id, posting_id, applied_on (date), url TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT 'open' CHECK (outcome IN ('open','screen','interview','offer','rejected','closed')),
    outcome_at, revision)`.
  - New table `outreach_touch` (every channel, for outcome learning):
    `touch_id`, `person_id REFERENCES person` (NULL only for channel `application`), `company_id`, `channel CHECK (channel IN ('linkedin_request',
    'linkedin_message','email','application','hn_reply','other'))`, `posting_id`, `email_touch_id REFERENCES touch`
    (only for channel 'email'), `sent_at`, `accepted_at`, `replied_at`, `reply_kind` (the #9 tags), `referred_at`,
    `interview_at`, `offer_at`, `quiet_at`, `undone_at` (nullable: the touch was logged by mistake and undone; it is
    kept but counts nowhere), `context TEXT CHECK (json_valid(context))` with exactly these keys, written by
    `outreach.outcomes.context.build_context` (#11) and read by #15 and #11: `source` (`plan` for a touch logged from
    a plan row, `own` otherwise), `lane`, `roleKind`, `roleAgeDays`, `rankerVersion`, `signals` (name to value at send
    time) and `roleNamed`; `idempotency_key TEXT UNIQUE`, `revision`, `created_at`.
    CHECKs: an outcome date is never before `sent_at`; `quiet_at` and `replied_at` are not both set; `undone_at` and any outcome date are not both set; `person_id` is NULL
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
  - `test_outreach_touch_undone_excludes_outcomes`: `undone_at` and `accepted_at` together raise.
  - `test_board_found_by_check`: `found_by='bogus'` raises; the default is `import`; each of the five values inserts.
  - `test_work_tier_one_row_per_company_and_tier_4_refused`.
  - `test_posting_body_hash_must_be_64_chars_or_empty`: a 10 character value raises; `''` and 64 hex insert.
  - In `tests/test_facts_vocab.py`: `test_vocab_matches_schema` (every `FACTS` and `SOURCE_KINDS` value appears in the
    `evidence` CREATE statement), `test_draft_accepts_lca_json_with_and_without_median_wage`, `test_draft_rejects_bad_cap_exempt_and_staffing_values`,
    `test_draft_accepts_hn_and_stapply_source_kinds`, `test_headcount_needs_num_low`.
  - `test_new_job_and_review_kinds_insert`: one `job_run` per new job and one `review_item` per new kind insert.
  - `test_daily_list_entry_person_and_channel`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_store_schema -v`. Expected: the new tests FAIL (unknown column, CHECK).
- [ ] **Step 3: Edit `0001_initial.sql`** with the definitions above, keeping comments in the file's style.
- [ ] **Step 4: Run the whole suite** `uv run python -m unittest discover -s tests -t .` and the mutation check
  `uv run python tests/mutation_check.py`. Expected: all OK, mutation `missed 0` (update anchors if lines moved).
- [ ] **Step 5: Commit** `Store: pipeline tables and values for sources, reads, readings, scores and touches (#30)`.

## Task 2: Source, crawl and HTTP contracts

**Files:**
- Create: `outreach/sources/__init__.py`, `outreach/sources/base.py`, `outreach/crawl/__init__.py`, `outreach/crawl/base.py`, `outreach/net.py`, `tests/fakes.py`
- Test: `tests/test_contracts.py`, `tests/test_net.py`

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
    validators: Validators; http_status: int | None = None; error: str = ''; board_name: str = ''` (`board_name` is
    the org name the ATS reports for the board, when the list response carries it; `''` otherwise); `__post_init__`: `unchanged` and
    `error` carry no jobs; `error` needs a non-empty `error`; `empty` has no jobs; `ok` has at least one.
  - `class Adapter(Protocol): ats: str; def list(self, token: str, validators: Validators) -> ReadResult;
    def body(self, token: str, job: JobRef) -> str | None`.
  - `def ids_fingerprint(native_ids: Iterable[str]) -> str`: sha256 hex of the sorted ids joined by `\n`.
  - `def valid_token(ats: str, token: str) -> bool` in `outreach/crawl/base.py`: the grammar in Shared formats.
  - In `outreach/net.py` (the one HTTP layer, stdlib only; #31 and #21 import it and add no copy):
    `@dataclass(frozen=True) class Response: status: int; url: str; body: bytes; headers: Mapping[str, str]` (keys lower
    case) with `text() -> str` (UTF-8, errors replaced) and `json() -> object` (`ValueError` when not JSON);
    `class FetchError(Exception)` with `kind: Literal['network', 'timeout', 'too_large']` (transport failure only: any
    HTTP status, 404 and 503 included, is a `Response`); `class HttpClient(Protocol)`: `get(url, headers=None, *,
    follow_redirects=False) -> Response`, `post(url, body: bytes, headers=None) -> Response`, `download(url, dest: Path,
    headers=None) -> int` (streams to `dest.part`, then renames, so a failure leaves no `dest`);
    `USER_AGENT = "outreach-crawler/1.0 (personal job search)"`; `class UrllibClient(profile: Literal['declared',
    'browser'] = 'declared', timeout_s: float = 30.0, max_bytes: int = 20_000_000, max_redirects: int = 3)`: redirects
    are not followed unless `follow_redirects`, a body over `max_bytes` raises `FetchError('too_large')`, socket and URL
    errors raise `FetchError`; `declared` sends `USER_AGENT` and nothing else identifying; `browser` sends the Chrome
    header set from `recon/lca/dl.sh` (Accept, Accept-Language, `sec-ch-ua*`, `Sec-Fetch-*`,
    `Upgrade-Insecure-Requests`) and keeps cookies in a `CookieJar` (DOL answers 403 to a bare client); a private
    `_request(url, headers) -> urllib.request.Request` is what the header tests call;
    `class SpacedClient(inner: HttpClient, *, min_interval_s: float = 1.5, per_host: dict[str, float] | None = None,
    clock=time.monotonic, sleep=time.sleep)`: waits so two requests to one host start at least the interval apart
    (`data.sec.gov` and `www.sec.gov` get 0.15 from the default `per_host`, which keeps SEC under 10 a second).
    #21's `PoliteClient` (host buckets and a breaker) is its own wrapper over any `HttpClient` and does not use
    `SpacedClient`.
  - In `tests/fakes.py` (the one fake): `FakeClient(routes)` where a route key is a URL or a `("POST", url)` pair and
    the value is a `Response`, `bytes`, `str` or `Path` (a 200 body), an `Exception` (raised; a `FetchError` stands
    for a transport failure), a callable `(method, url, headers, body) -> Response`, or a list consumed in order; an
    unrouted request raises `UnexpectedRequest(AssertionError)` so a test can never pass by accident; `calls:
    list[tuple[str, str, dict, bytes | None]]` and `requests: list[str]` (every URL asked, in order); helpers
    `json_response(obj, *, status=200, etag=None)`, `text_response(text, *, status=200, headers=None)`,
    `status_response(code)`.

- [ ] **Step 1: Write the failing tests** `test_candidate_rejects_unknown_key`, `test_candidate_rejects_bad_domain`,
  `test_read_result_invariants` (each bad combination raises `ValueError`; each good one constructs),
  `test_ids_fingerprint_is_order_free` (`ids_fingerprint(['b','a']) == ids_fingerprint(['a','b'])`, and differs for `['a']`),
  `test_read_result_board_name_defaults_empty`, `test_valid_token_grammar` (a Workday `adobe.wd5/External_Experienced`
  and an Oracle `eeho.fa.us2.oraclecloud.com/CX_1` pass; a slug with `%` or upper case fails; a Workday token with no
  `.wd<N>` fails).
  `tests/test_net.py`: `test_urllib_client_returns_304_as_a_response`, `..._does_not_follow_a_redirect_by_default`,
  `..._raises_too_large_over_max_bytes`, `..._raises_fetch_error_when_nothing_listens` (against a stdlib `http.server` on
  127.0.0.1 in a thread, no external network); `test_declared_profile_sends_the_one_user_agent_and_no_at_sign`;
  `test_browser_profile_sends_sec_fetch_headers` (builds the request with `_request`, does not send it);
  `test_download_leaves_no_file_on_failure`; `test_spaced_client_spaces_requests_per_host` (fake clock and sleep: two
  gets to `a.com` sleep 1.5 in total, a get to `b.com` sleeps 0); `test_spaced_client_sec_hosts_use_short_interval`;
  `test_response_text_replaces_bad_bytes`; `test_fake_client_unknown_url_fails_the_test`;
  `test_fake_client_exception_route_raises`; `test_fake_client_consumes_a_list_in_order`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_contracts -v`. Expected: FAIL (module not found).
- [ ] **Step 3: Implement** the dataclasses and Protocols above.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Contracts: sources, board adapters and the one HTTP layer (#30)`.

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
  - `body_sha256(text: str) -> str`: sha256 hex (64 characters) of `text` after collapsing every run of whitespace to
    one space and stripping the ends, encoded UTF-8; case is kept. The only definition: #21 stores it in
    `posting_body.body_sha256`, #32 in `read_queue` and `posting_reading`; both import it from here.
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
    revision (raises `LookupError` on a mismatch); raises `ValueError` when the touch is undone; returns the new revision; appends an event.
  - `undo_touch(conn, touch_id: int, *, at: str, revision: int) -> int`: sets `undone_at` (the touch is kept, never
    deleted); raises `ValueError` when an outcome date is already set; checks the revision; returns the new revision;
    appends an event (`outreach_touch`, kind `undone`). Every count of touches (#15 capacity and contact state, #11
    progress and outcome report) filters `undone_at IS NULL`.

- [ ] **Step 1: Write the failing tests** `test_add_alias_refuses_other_company`, `test_add_alias_fein_kept_raw`,
  `test_shared_key_round_trip`, `test_log_touch_is_idempotent` (same key twice returns the same id and one row),
  `test_record_outcome_checks_revision`, `test_record_outcome_refuses_before_sent`, `test_body_sha256_collapses_whitespace_and_keeps_case` (`"A  b\n"` and `"A b"` hash equal, `"a b"` differs, 64 hex), `test_undo_touch_sets_undone_at_and_blocks_outcomes`, `test_undo_touch_refuses_after_an_outcome`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_store_writes -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the helpers with single statements, as the rest of `writes.py` does.
- [ ] **Step 4: Run** the full suite. Expected: all OK.
- [ ] **Step 5: Commit** `Store: alias, shared-key and touch helpers (#30)`.

## Task 5: Hand-off notes for the streams

**Files:**
- Modify: `docs/superpowers/plans/2026-10-19-store-and-rules.md` ("For downstream issues": one paragraph pointing at this plan's tables and the rule that further schema changes are new numbered migrations, one owner each, renumbered on rebase)
- Modify: `docs/README.md` (rows for this plan and the stream plans)

- [ ] **Step 1: Edit both docs.** No em or en dashes.
- [ ] **Step 2: Run** `grep -c -P "[\x{2014}\x{2013}]" docs/README.md docs/superpowers/plans/2026-10-19-store-and-rules.md`. Expected: `0` for each.
- [ ] **Step 3: Commit** `Docs: foundation hand-off for the pipeline streams (#30)`.

## Task 6: Contract checks and the pipeline flow test

Every stream proves its parts against the same checks, and one flow test runs the whole pipeline on fixtures, so a
break between streams fails at the PR that causes it, not at the switch.

**Files:**
- Create: `tests/contracts.py`, `tests/pipeline/__init__.py`, `tests/pipeline/harness.py`, `tests/pipeline/stages_rules.py`, `tests/pipeline/test_flow.py`
- Create: `tests/fixtures/pipeline/` (a tiny LCA extract of 3 employers, one board list response, one posting body)

**Interfaces:**
- Consumes: Tasks 1 to 4.
- Produces:
  - `check_source(tc: unittest.TestCase, source: Source, since: str | None = None) -> list[Candidate]`: asserts
    `name` and `version` are non-empty, every item is a `Candidate`, and a second `discover` over the same input
    yields equal candidates (deterministic).
  - `check_adapter(tc, adapter: Adapter, token: str) -> None`: given an adapter wired to a fake HTTP client, asserts
    a first read is `ok` or `empty`, a read with the returned validators is `unchanged` when the fake (`tests/fakes.py::FakeClient`) returns 304 or
    the same ids, and a 500 is `error` with no jobs.
  - `check_signal(tc, signal: Signal, facts: CompanyFacts) -> SignalValue`: asserts the value is within -1.0 to 1.0
    and the reason is non-empty and has no em or en dash.
  - `PipelineHarness` in `tests/pipeline/harness.py`: `.conn` (a scratch store), `.fixture(name) -> Path`, and
    `.stages`: an ordered list of `(name, callable)` that each stream's module appends to. Stage names, in order:
    `discover`, `resolve`, `find_boards`, `read_boards`, `read_postings`, `rules`, `rank`, `plan`.
  - **Registration convention (stated once; every stream follows it).** Each stream owns one module
    `tests/pipeline/stages_<stream>.py` with a module-level `register(harness: PipelineHarness) -> None` that appends
    its stage or stages: `stages_sources.py` (#31: `discover`, `resolve`, `find_boards`), `stages_crawl.py` (#21:
    `read_boards`), `stages_reader.py` (#32: `read_postings`), `stages_rules.py` (this plan: `rules`),
    `stages_ranking.py` (#6: `rank`), `stages_plan.py` (#15: `plan`). `harness.py` imports each module that exists and
    calls its `register`, in stage order (an import of a missing module is skipped), so a stream's PR adds its one
    file and one import line and edits nothing else in `tests/pipeline/`. A flow test runs every registered stage up
    to and including its own, in order, on a fresh harness, and its invariants count only the rows its own stage made
    or its own fixture companies, never a global total.
  - **The `rules` stage (this plan owns it):** `stages_rules.py::register` appends `rules`, which calls
    `rebuild(conn, combiner=reader_combiner(conn))` when `outreach.reader.combine` imports (else `rebuild(conn)`),
    then `apply_all(conn)` when it imports. A rebuild alone resets every `provisional` flag to 1; the pair keeps every
    decision a verified reading has made.
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
its stage through its `tests/pipeline/stages_<stream>.py` (convention in Task 6) and adds one invariant to the flow
test; the flow test must stay green on every PR. A rules-stage test in this task: `test_rules_stage_keeps_reader_decisions`
(skipped until `outreach.reader.combine` exists, then: a decision made final by a stored verified reading is still
final after the stage).
