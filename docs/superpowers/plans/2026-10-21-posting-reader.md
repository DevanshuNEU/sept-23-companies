# Posting reader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read each new engineering posting once through Devanshu's Claude Code seat, store facts only when code has checked every quote against the stored text, and combine those facts with the #4 phrase rules so a hide needs both.

**Architecture:** A queue (`read_queue`) hands out postings in a fixed priority order with leases; `submit` validates each reading with the foundation's `PostingReading.from_json` and `quote_in_body`, refuses what fails, re-queues up to a limit and stores the rest in `posting_reading`. A pure `combine()` turns the rules' `Decision` plus a stored reading into the decision row the store keeps (`provisional`, `reader_version`). The night job is a Python loop that calls `claude -p` as a pure text-in, text-out function (no tools, no store access for the model) and submits what comes back; the side session uses the same `next_batch` and `submit` functions through #13's tools. A gold set in `tests/gold/` and `eval` decide whether a reader version may become current.

**Tech Stack:** Python 3.14 (Homebrew), standard library only in `outreach/`, SQLite STRICT tables, `unittest`, the `claude` CLI (2.1.x) for the night job only.

**Spec:** `docs/superpowers/specs/2026-10-20-opportunity-pipeline-design.md` (sections 3, 4, 7, 8, 12, 14, 17); evidence in `docs/research/2026-10-20-llm-posting-extraction.md` (sections 5.1 to 5.6 and 3.3). Issue: #32. Depends on the foundation plan `docs/superpowers/plans/2026-10-21-pipeline-foundation.md` (issue #30): its `outreach/reader/types.py` (`READING_SCHEMA_VERSION`, `FIELDS`, `ALLOWED`, `FieldReading`, `PostingReading`, `quote_in_body`, `ReadRequest`), its tables `read_queue` and `posting_reading`, `decision.provisional` and `decision.reader_version`, review kind `reader_disagrees` with `raised_by 'reader'`, `job_run.job 'read_postings'` and `event.entity 'reading'`; and its Task 6 harness (`tests/contracts.py`, `tests/pipeline/harness.py::PipelineHarness` with ordered stages, `tests/pipeline/test_flow.py`). This plan imports them and never redefines them.

**Branch:** `DevanshuNEU/posting-reader`, from the foundation branch once it has merged (rebase on master after). One PR, body says it closes #32.

## Global Constraints

- No Claude API and no network in tests. The `claude -p` call goes through an injected `Runner`; tests pass a fake. Nothing in `outreach/` imports an HTTP library or the Anthropic SDK.
- No live store: tests use `tests/storetest.fresh_store()`. Never open `~/Library/Application Support/outreach/outreach.db`. The night job refuses to run outside the live checkout (`live_checkout.require_live`). Do not install the launchd job: the plan ships a template and Devanshu runs the install.
- Writers use `INSERT ... ON CONFLICT DO NOTHING` (or `DO UPDATE` where a row must change), never `INSERT OR IGNORE` or `INSERT OR REPLACE`. Timestamps are canonical UTC from `outreach.store.utcnow()`.
- Every derived row records its version: `posting_reading.reader_version` is `rv-` plus 12 hex of sha256 over prompt file bytes, model name and `READING_SCHEMA_VERSION`; `decision.reader_version` is NULL while provisional.
- Hidden only with evidence (#9): a hide needs the reader's verified quote and the rules to agree. Everything uncertain is shown and flagged. A refused, missing or disagreeing reading never hides a posting.
- A reading whose quote is not in the stored text never reaches `posting_reading` as a fact: it is refused or blanked and listed in `refused_fields`.
- The reader never approves or sends anything, and nothing here writes `touch`, `message` or `person` rows.
- Python only, in the `outreach/` package. Tests: `uv run python -m unittest discover -s tests -t .`.
- No em dashes or en dashes in code, comments, prompt text, docs or commit messages.
- Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A real quote that does not support its value** (a flipped negation: the quote "we will sponsor visas" returned with `wont_sponsor`). The quote check passes, so the rules are the guard: reader `wont_sponsor` with rules `silent` must not hide, and raises `reader_disagrees`. Tested in Task 4.
2. **A body too short to read** (Workday and Oracle stubs under 500 characters, an empty body). Never enqueued, never hidden for lack of a reading, no crash. Tested in Task 2.
3. **Two readers at once** (the night job and a side session, or a double submit). The same key is stored once; the second `submit` returns `duplicate` and changes nothing; a lease is never handed to two callers. Tested in Tasks 2 and 3.
4. **A poison posting** (one body that always makes the model fail, or a Mac that sleeps mid-batch). Lease expiry hands it out again with `attempts` up, and at the limit it ends `refused` and fails open (shown, flagged), instead of looping every night. Tested in Tasks 2 and 3.
5. **Unexpected `claude -p` output** (code fences around the JSON, extra prose, a session-limit message in stdout, a non-zero exit). Never a crash and never a half-written batch: the leases go back to `queued` without counting an attempt, and a session limit or auth error stops the night cleanly with the job marked `partial` or `failed`. Tested in Task 6.

---

## Decisions this plan makes (the spec leaves them to the builder)

- **Reader vocabulary to decision columns.** Each reading field maps onto a rules field as below. Fields marked "hide" can change `passes()`; the others only flag.

| Reading field | Compared with (rules) | Kind |
|---|---|---|
| `sponsorship` | `decision.sponsorship`: `wont_sponsor`=`no`, `will_sponsor`=`yes`, `opt_only`=`opt_only`, `silent`=`silent`; reader `unknown` agrees with `unclear` and `silent` | hide (when `no`) |
| `citizenship_or_clearance` | `decision.citizenship`: reader `yes` agrees with true; reader `no` or `unknown` agrees with false | hide |
| `student_or_grad_window` | `decision.eligibility`: reader `yes` agrees with `excluded`; `no` or `unknown` agrees with `ok` | hide |
| `location` | `decision.us`: the reader's location text goes through the existing `outreach.rules.location.us_status(text, None, cfg)`, and the result is compared with `decision.us` | hide (when `no`) |
| `engineering` | `decision.lane`: `yes` agrees with lane `engineering` or `intern`; `no` agrees with the other lanes | flag only |
| `level` | `decision.level_tier` and lane: `intern`=lane `intern`; `new_grad`=`new_grad`; `early_career`=`early_career` or `swe1`; `mid`=`swe2` or `open`; `senior_plus`=`senior`; `unknown` agrees with `not_stated` | flag only |
| `years_low` | `decision.years_asked` (both `None` agree) | flag only |
| `start_date` | `outreach.rules.eligibility.starts_2027("", text, cfg)` against `decision.start_2027` | flag only |
| `years_high`, `remote`, `pay` | no rules counterpart | reader fact stands, read from `posting_reading` |

- **Per-field state** is one of `agree`, `disagree`, `missing` (reader `unknown`, refused or blanked where the rules claim something), `reader_only` (no rules counterpart). A hide survives only when the field state is `agree`. A rules hide whose state is `disagree` or `missing` is relaxed in the stored row (`sponsorship no -> unclear`, `citizenship 1 -> 0`, `eligibility excluded -> ok` with reason NULL, `us no -> unknown`; the rules' quote stays) so `passes()` shows the posting again, and a `reader_disagrees` review item is raised. A reader hide the rules do not claim (reader `wont_sponsor`, rules `silent`) never hides, and also raises the item.
- **Stored decision rows are the combined facts** (spec section 4: "decide() from reader facts plus phrase rules"). What the rules alone said is recomputable by `decide()` from the body, so `explain()` and `eval` lose nothing. `provisional = 0` and `reader_version = <version>` once a reading at the promoted version exists for the posting's current body hash, or the queue row for that key has ended `refused` (fail open); otherwise `provisional = 1` and `reader_version` is NULL.
- **Promoted version.** The queue and the night job always work with the version computed from the files (the candidate). The combination uses readings of the version named in `outreach/reader/config.toml` `[reader] promoted_version`. `python -m outreach.reader promote` writes that line only when the eval gate passes. A new version therefore runs in shadow (stored, compared, deciding nothing) until promoted, as the research (section 5.7) recommends.
- **Quote haystack.** `quote_in_body` runs against the stored body. Two fields also accept their header: `level` may quote the posting title, `location` may quote the structured location the reader was shown. All other fields: body only. Spec 7.1 says "stored body"; the header exceptions are listed in Open questions.
- **Who is read.** Open postings whose current decision lane is `engineering` or `intern` and whose stored body has at least `min_body_chars` (config, 500) characters. Rules-only hides from the title lane (not engineering) are never read; they are lane filters, not evidence hides.
- **The model never touches the store.** The night job sends the pinned prompt as the system prompt and the batch as the user message, with all tools off and a neutral working directory, and parses the reply. This keeps the session free of the repo's CLAUDE.md (which would load Devanshu's email voice) and of any ability to write.

## File Structure

| File | Responsibility |
|---|---|
| `outreach/reader/config.toml` | Create: model, promoted version, queue and night limits, failure patterns |
| `outreach/reader/config.py` | Create: `ReaderConfig`, `load_reader_config` |
| `outreach/reader/version.py` | Create: `reader_version(cfg)`, `body_sha256(text)` |
| `outreach/reader/prompts/read_posting.md` | Create: the pinned prompt |
| `outreach/reader/queue.py` | Create: `enqueue`, `enqueue_missing`, `refresh_priorities`, `next_batch`, `release`, `batch_payload` |
| `outreach/reader/submit.py` | Create: `SubmitResult`, `submit` |
| `outreach/reader/combine.py` | Create: `FieldVerdict`, `Combined`, `compare`, `combine`, `apply_reading`, `apply_all`, `explain`, `reading_state` |
| `outreach/reader/night.py` | Create: `Runner`, `RunResult`, `classify_run`, `claude_runner`, `run_night` |
| `outreach/reader/gold.py` | Create: gold item format, `load_gold`, `sample_gold`, `confirm` |
| `outreach/reader/evaluate.py` | Create: `rules_view`, `score`, `not_worse`, `promote` |
| `outreach/reader/__main__.py` | Create: CLI `next`, `submit`, `apply`, `night`, `gold`, `eval`, `promote` |
| `outreach/rules/rebuild.py` | Modify: public `stored_decision`, `combiner` hook, upsert writes `provisional` and `reader_version` |
| `launchd/com.devanshu.outreach-reader-night.plist.template` | Create: the night job template (not installed) |
| `tests/gold/` | Create: `README.md` is not written; the format lives in `gold.py`. Holds `items.jsonl` and `readings/<version>.jsonl` |
| `tests/test_reader_*.py` | Create: one per module above |
| `tests/pipeline/harness.py`, `tests/pipeline/test_flow.py` | Modify (Task 9): register the `read_postings` stage and add the reader flow invariants |
| `tests/fixtures/pipeline/` | Modify (Task 9): one more posting body for the reader flow |
| `docs/README.md`, `docs/superpowers/plans/2026-10-21-pipeline-foundation.md` | Modify in Task 10: map rows only |

---

## Task 1: Config, version and the pinned prompt

**Files:**
- Create: `outreach/reader/config.toml`, `outreach/reader/config.py`, `outreach/reader/version.py`, `outreach/reader/prompts/read_posting.md`
- Test: `tests/test_reader_version.py`

**Interfaces:**
- Consumes: `READING_SCHEMA_VERSION`, `FIELDS`, `ALLOWED` from `outreach/reader/types.py`.
- Produces:
  - `config.toml` keys (exact): `[reader] model = "claude-haiku-5-5"`, `promoted_version = ""`, `min_body_chars = 500`, `batch_size = 20`, `lease_minutes = 30`, `max_attempts = 3`; `[priority] top_companies = 200`; `[night] max_postings_per_night = 400`, `max_batches = 25`, `max_consecutive_failures = 2`, `call_timeout_s = 900`, `session_limit_patterns = ["usage limit", "session limit", "limit reached", "resets at"]`, `auth_patterns = ["not logged in", "please run /login", "authentication", "invalid api key", "oauth token"]`.
  - `@dataclass(frozen=True) class ReaderConfig`: one attribute per key above (`model: str`, `promoted_version: str`, `min_body_chars: int`, `batch_size: int`, `lease_minutes: int`, `max_attempts: int`, `top_companies: int`, `max_postings_per_night: int`, `max_batches: int`, `max_consecutive_failures: int`, `call_timeout_s: int`, `session_limit_patterns: tuple[str, ...]`, `auth_patterns: tuple[str, ...]`, `prompt_path: Path`).
  - `load_reader_config(path: Path = CONFIG_PATH) -> ReaderConfig`: a missing section or key raises `KeyError` naming it (same style as `outreach/rules/config.py`).
  - `reader_version(cfg: ReaderConfig | None = None) -> str`: `"rv-" + sha256(prompt_bytes + b"\0" + model.encode() + b"\0" + str(READING_SCHEMA_VERSION).encode()).hexdigest()[:12]`.
  - `body_sha256(text: str) -> str`: sha256 hex of the UTF-8 body, 64 characters, matching the `read_queue.body_sha256` CHECK.

- [ ] **Step 1: Write the failing tests** in `tests/test_reader_version.py`:
  - `test_version_shape`: `reader_version()` matches `^rv-[0-9a-f]{12}$`.
  - `test_version_changes_with_prompt`: two temp prompt files differing by one character give different versions (`load_reader_config` takes a `prompt_path` override argument through `dataclasses.replace`).
  - `test_version_changes_with_model`: `replace(cfg, model="claude-sonnet-5-5")` changes it.
  - `test_version_changes_with_schema_version`: patch `outreach.reader.version.READING_SCHEMA_VERSION` to 2; changes it.
  - `test_body_sha256_is_64_hex_and_stable`.
  - `test_missing_config_key_names_it`: a config file without `max_attempts` raises `KeyError` whose text contains `max_attempts`.
  - `test_prompt_names_every_field_and_allowed_value`: the shipped prompt text contains every name in `FIELDS` and every allowed string in `ALLOWED` for the enum fields. A schema change that skips the prompt fails here.
  - `test_prompt_has_no_dashes`: the prompt has no em dash (code point 0x2014) and no en dash (code point 0x2013).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_reader_version -v`. Expected: FAIL (module not found).
- [ ] **Step 3: Implement** the three modules and the prompt file. The prompt (Markdown, plain words) must contain: the task (read each posting, return one object per posting); the output shape `{"readings": [{"posting_id": int, "body_sha256": str, "fields": {<FIELDS>: {"value": ..., "quote": ...}}}]}` with nothing outside the JSON; each field's allowed values (from the foundation's `ALLOWED`) and one line of meaning; the rule "copy the quote exactly from the posting text, at most 300 characters; if no sentence states it, use `unknown` (or `silent` for sponsorship) and an empty quote"; the sponsorship and authorization edge cases from research section 5.1 ("authorized to work in the US" alone is `unknown`, `wont_sponsor` only for text that refuses, a quote must contain the deciding words); `years_low` and `years_high` quote the sentence and never a company-age or preferred number; level comes from title or body wording, not from years; and 4 to 6 short worked examples taken from the misreads listed in research section 3.2 (the "yes sponsor" false positives and the student-exclusion false positives). Nothing volatile (no dates, ids, counts) in the prompt.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Reader: config, version hash and the pinned prompt (#32)`.

## Task 2: The queue, priorities and leases

**Files:**
- Create: `outreach/reader/queue.py`
- Modify: `outreach/rules/rebuild.py` (add public `stored_decision`, see below)
- Test: `tests/test_reader_queue.py`

**Interfaces:**
- Consumes: `ReaderConfig`, `reader_version`, `body_sha256` (Task 1); `ReadRequest` (foundation); `passes` from `outreach/rules/decide.py`; `current_decision` view; tables `read_queue`, `company_score`, `posting_body`.
- Produces:
  - `rebuild.stored_decision(row: sqlite3.Row) -> Decision`: the existing private `_stored`, renamed public (keep `_stored = stored_decision` so nothing else breaks).
  - `enqueue(conn, posting_id: int, *, cfg: ReaderConfig | None = None, version: str | None = None) -> bool`: reads the posting's stored body, returns False (writes nothing) when there is no body or it is shorter than `min_body_chars` or the posting is closed or its current decision lane is not `engineering` or `intern`; otherwise inserts `(posting_id, body_sha256(body), version, priority, 'queued')` with `ON CONFLICT DO NOTHING` and returns True when a row was added. A changed body has a new hash and so a new key: the old key's rows stay as history.
  - `enqueue_missing(conn, *, cfg=None, version=None, limit: int | None = None) -> int`: enqueues every eligible open posting that has no `read_queue` row for its current body hash under `version` (the backlog, a skipped night, a new reader version). Returns the count added.
  - `refresh_priorities(conn, *, cfg=None) -> int`: recomputes `priority` for every `queued` row, returns how many changed. `priority = tier * 1_000_000 + points`. Tier 3: the posting's current decision has `provisional = 1` and `not passes(stored_decision(row))` (provisional hides first). Tier 2: lane 1 (`lane = 'engineering'` and `level_ok = 1`) at a company in the top `top_companies` by `company_score.score` at the most recently scored `ranker_version` (`SELECT ranker_version FROM company_score ORDER BY scored_at DESC LIMIT 1`); when `company_score` has no rows, every lane 1 posting is tier 2 with 0 points; `points = clamp(int(score * 1000), 0, 999_999)`. Tier 1: everything else. Within one priority, order is newest first (`posting_id DESC`).
  - `next_batch(conn, limit: int, *, cfg=None, version=None, now: str | None = None) -> list[ReadRequest]`: calls `refresh_priorities`, then in one transaction takes up to `limit` rows for `version` that are `queued`, or `leased` with `lease_until < now`, ordered `priority DESC, posting_id DESC`; sets `state='leased'`, `lease_until = now + lease_minutes`, `attempts = attempts + 1`; a row whose `attempts` is already `>= max_attempts` is set `refused` with `last_error = 'gave up after N leases'` instead of being handed out. Returns `ReadRequest(posting_id, body_sha256, reader_version, title, body)` per row. Two calls never return the same live lease.
  - `release(conn, requests: Iterable[ReadRequest]) -> None`: sets those rows back to `queued`, clears `lease_until` and gives back the attempt (`attempts = max(attempts - 1, 0)`), for a stop that was not the posting's fault (session limit, auth error, a crashed call).
  - `batch_payload(conn, requests: list[ReadRequest]) -> dict`: `{"reader_version": ..., "postings": [{"posting_id", "body_sha256", "reader_version", "title", "location", "body"}]}`; `location` is the posting's structured location (the reader may quote it).

- [ ] **Step 1: Write the failing tests** in `tests/test_reader_queue.py` using `fresh_store`, `seed_company`, `seed_posting`, plus a `decide_and_store`-free helper that inserts a `ruleset` row and a `decision` row directly (lane, `level_ok`, `provisional`) so priority cases are exact. Names and assertions:
  - `test_enqueue_adds_one_row_per_body_hash`: enqueue twice returns True then False; one `read_queue` row; `state == 'queued'`; `body_sha256` is 64 hex.
  - `test_body_change_requeues`: change the `posting_body.body`, `enqueue` again: True; two rows for the posting with different hashes.
  - `test_short_or_empty_body_is_never_enqueued`: a 499-character body and a posting with no body row both return False; `enqueue_missing` adds 0 (Review Focus 2).
  - `test_closed_or_off_lane_posting_is_not_enqueued`.
  - `test_enqueue_missing_covers_backlog_and_new_version`: three postings, one already queued: returns 2; a different `version` string returns 3.
  - `test_priority_order`: rows for (a) a provisional hide, (b) a lane 1 posting at the top-scored company, (c) a lane 1 posting at a lower company, (d) an older ordinary posting, (e) a newer ordinary posting; `next_batch(conn, 5)` returns them in the order a, b, c, e, d.
  - `test_priority_without_company_score_puts_lane_one_above_the_rest`.
  - `test_next_batch_leases_and_does_not_double_hand_out`: `next_batch(conn, 2)` returns 2; a second call returns the rest, none repeated; leased rows have `lease_until` set and `attempts == 1`.
  - `test_expired_lease_is_handed_out_again`: after `now` moves past `lease_until`, the same posting returns with `attempts == 2`; before that it does not.
  - `test_poison_posting_ends_refused_at_the_attempt_limit`: with `max_attempts=3`, the fourth call returns nothing for it and the row is `refused` with a non-empty `last_error` (Review Focus 4).
  - `test_release_gives_back_the_attempt`.
  - `test_batch_payload_has_location_and_version`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_reader_queue -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `queue.py` and the `stored_decision` rename. Use `BEGIN IMMEDIATE` through `outreach.store.transaction` for the lease so two processes cannot take the same row; no write transaction stays open while Python computes `passes()` in `refresh_priorities` (read rows first, then write in short batches of 500, as `rebuild` does).
- [ ] **Step 4: Run** the same command, then the whole suite. Expected: PASS.
- [ ] **Step 5: Commit** `Reader: read queue with priorities and leases (#32)`.

## Task 3: Submit, quote checks and refusal

**Files:**
- Create: `outreach/reader/submit.py`
- Test: `tests/test_reader_submit.py`

**Interfaces:**
- Consumes: `PostingReading.from_json`, `PostingReading.to_json`, `quote_in_body`, `FIELDS`, `FieldReading` (foundation); `body_sha256`, `reader_version` (Task 1); `read_queue` and `posting_reading` tables; `append_event`.
- Produces:
  - `@dataclass(frozen=True) class SubmitResult: posting_id: int | None; body_sha256: str; status: Literal['stored', 'partial', 'refused', 'requeued', 'stale', 'duplicate', 'unknown']; refused_fields: tuple[str, ...] = (); error: str = ''`.
  - `submit(conn, readings: list[dict] | dict, *, cfg: ReaderConfig | None = None, now: str | None = None) -> list[SubmitResult]`: one result per reading, each in its own transaction so one bad reading never undoes its neighbours. Per reading, in order:
    1. `PostingReading.from_json(obj)` raises `ValueError`: the reading is malformed (extra or missing field, value outside `ALLOWED`, quote missing for a known value). Status `requeued` (attempts left) or `refused` (at the limit); `error` carries the message; nothing stored.
    2. No queue row for `(posting_id, body_sha256, reader_version)`: `unknown`, nothing written. Row `done` or `refused`: `duplicate`, nothing written.
    3. The stored body's hash is no longer `body_sha256`: `stale`; the row ends `done` with `last_error = 'body changed'`, and `enqueue` is called for the new body.
    4. Quote check per field whose value is not `unknown`, `silent`, `None` or `''`: `quote_in_body(quote, haystack)` where the haystack is the body, plus the title for `level` and the structured location for `location`. A failing field is a refused field.
    5. No refused fields: store a `posting_reading` row (`fields = PostingReading.to_json()` as JSON text, `verified = 1`, `refused_fields = '[]'`, `read_at = now`), queue row `done`, one `event` (entity `reading`, kind `stored`, actor `claude`, detail with posting id and version). Status `stored`.
    6. Some refused fields and `attempts < max_attempts`: nothing stored; queue row back to `queued` with `last_error = 'refused: <field list>'`. Status `requeued`.
    7. Some refused fields at the limit: store a partial reading (valid fields kept, each refused field blanked to `FieldReading('unknown', '')`, or `None` for the integer fields and `''` for text fields; `verified = 0`; `refused_fields` the JSON list), queue row `refused`. Status `partial`.
  - `blank_field(name: str) -> FieldReading`: the blank used in step 7.

- [ ] **Step 1: Write the failing tests** in `tests/test_reader_submit.py`. A helper builds a leased posting with a known body (for example `"We do not sponsor visas. Candidates must hold a US work permit."` plus filler to 500 characters) via `enqueue` and `next_batch`, and a helper builds a full valid reading dict where every field is `unknown` and quote `''`, then overrides one field. Tests:
  - `test_valid_reading_is_stored_verified`: `sponsorship = wont_sponsor` with quote `"We do not sponsor visas."`; status `stored`; one `posting_reading` row with `verified = 1`; queue row `done`; one `event` row with entity `reading`.
  - `test_paraphrase_is_refused_and_requeued`: quote `"no visa sponsorship is offered"` (not in the body); status `requeued`; `refused_fields == ('sponsorship',)`; no `posting_reading` row; queue row `queued`, `last_error` mentions `sponsorship`.
  - `test_unquoted_fact_is_refused`: value `wont_sponsor` with quote `''`; no row stored, status `requeued` (the foundation's `from_json` raises first).
  - `test_one_word_quote_is_refused`: quote `"sponsor"`.
  - `test_whitespace_and_case_differences_pass`: quote `"we do  NOT sponsor\nvisas"`; status `stored`.
  - `test_refusal_at_the_limit_stores_a_partial_reading`: with `max_attempts=1` and one bad field plus one good field: status `partial`; the good field is kept with its quote; the bad one is `unknown` with `''`; `verified == 0`; `refused_fields == '["sponsorship"]'`; queue row `refused`.
  - `test_malformed_object_is_refused_without_a_crash`: an extra field name, and a value outside `ALLOWED` (`level = "wizard"`); both return a result, nothing raises, nothing stored.
  - `test_stale_body_is_not_stored_and_the_new_body_is_queued`: change the body between `next_batch` and `submit`; status `stale`; the old row is `done`; a new queue row exists for the new hash.
  - `test_second_submit_is_a_duplicate`: submit the same valid reading twice; second status `duplicate`; still one `posting_reading` row (Review Focus 3).
  - `test_unknown_key_is_refused`: a reading for a posting that was never queued gets `unknown`.
  - `test_level_may_quote_the_title_and_location_may_quote_the_header`: a body without the words, a title `"Software Engineer, New Grad"`, quote `"New Grad"` for `level = new_grad` is accepted; the same quote for `sponsorship` is refused.
  - `test_one_bad_reading_does_not_undo_the_others`: a list of two (one good, one stale) stores the good one.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_reader_submit -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `submit.py`. Normalization is the foundation's `quote_in_body`; do not add a second matcher.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Reader: submit with quote checks, refusal and re-queue (#32)`.

## Task 4: Reader and rules together

**Files:**
- Create: `outreach/reader/combine.py`
- Modify: `outreach/rules/rebuild.py` (hook and upsert columns)
- Test: `tests/test_reader_combine.py`, `tests/test_rules_rebuild.py` (extend)

**Interfaces:**
- Consumes: `Decision`, `PostingInput`, `decide`, `passes` (`outreach/rules/decide.py`); `us_status`; `starts_2027`; `PostingReading`; `posting_reading`, `read_queue`, `decision`, `review_item` tables; the mapping table in "Decisions this plan makes".
- Produces:
  - `HIDE_FIELDS = ('sponsorship', 'citizenship_or_clearance', 'student_or_grad_window', 'location')`.
  - `@dataclass(frozen=True) class FieldVerdict: field: str; state: Literal['agree', 'disagree', 'missing', 'reader_only']; reader_value: str | int | None; rules_value: str | int | bool | None; reader_quote: str; would_hide_rules: bool; would_hide_reader: bool`.
  - `@dataclass(frozen=True) class Combined: decision: Decision; provisional: bool; reader_version: str | None; verdicts: tuple[FieldVerdict, ...]`; `raised: tuple[FieldVerdict, ...]` property: the verdicts in `HIDE_FIELDS` whose state is `disagree` or `missing` where either side would hide.
  - `compare(rules: Decision, reading: PostingReading | None, refused: frozenset[str], cfg: Config) -> tuple[FieldVerdict, ...]`: pure; a field in `refused` counts as `missing`.
  - `combine(rules: Decision, reading: PostingReading | None, *, refused: frozenset[str] = frozenset(), unreadable: bool = False, cfg: Config | None = None) -> Combined`: pure. No reading and not `unreadable`: the rules' decision unchanged, `provisional=True`, `reader_version=None`, no verdicts. Otherwise relax every rules hide whose state is not `agree` (see the decisions above), `provisional=False`, `reader_version=reading.reader_version` (for `unreadable` with no reading: every hide field is `missing`, `reader_version=None`, `provisional=False`).
  - `reading_state(conn, posting_id: int, *, promoted: str) -> tuple[PostingReading | None, frozenset[str], bool]`: the reading stored for the posting's current body hash at version `promoted` (`None` when `promoted` is empty), its `refused_fields`, and whether the queue row for that key is `refused` with no reading (unreadable).
  - `apply_reading(conn, posting_id: int, *, cfg: ReaderConfig | None = None) -> Combined`: decides with the rules (`decide()` on the stored title, body, location, country), combines, upserts the `decision` row under the current ruleset (`provisional`, `reader_version` included), raises one `reader_disagrees` review item per posting when `raised` is non-empty (`raised_by 'reader'`, `evidence_key = f"{version}:{sha[:12]}:{','.join(sorted(fields))}"`, `reason` naming each field with both values and the reader's quote), and clears an open `reader_disagrees` item for the posting with resolution `cleared` when `raised` is empty. Idempotent: a second call changes nothing. Raises `RulesetNotCurrent` as `decide_and_store` does.
  - `apply_all(conn, *, cfg=None) -> int`: `apply_reading` for every open posting that has a reading or an unreadable queue row for its current hash (run after a rebuild or a promotion; no API, no crawl). Returns the count.
  - `explain(conn, posting_id: int, *, cfg=None) -> tuple[FieldVerdict, ...]`: the verdicts for the posting under the promoted version if there is one, else under the candidate version, for the API and UI to show "flagged" fields and both quotes. Read-only.
  - `reader_combiner(conn, cfg: ReaderConfig | None = None) -> Callable[[sqlite3.Connection, int, Decision], Combined]`: the callable the CLI, the crawler (#21) and the flow test pass as `combiner`; it looks up `reading_state` for the posting at `cfg.promoted_version` and calls `combine`.
  - `rebuild.rebuild(..., combiner=None)` and `rebuild.decide_and_store(..., combiner=None)`: `combiner: Callable[[sqlite3.Connection, int, Decision], Combined] | None`; when given, its `decision`, `provisional` and `reader_version` are what get compared (flips) and written. The shared `_UPSERT` gains `provisional` and `reader_version`; with no combiner it writes `1` and NULL, so a re-decided row with changed facts never keeps an old `provisional = 0`. `rebuild.py` does not import `outreach.reader` (the CLI passes `combiner`); it is in `NOT_RULES`, so the ruleset hash does not move.

- [ ] **Step 1: Write the failing tests** in `tests/test_reader_combine.py` (pure ones build `Decision` and `PostingReading` by hand) and extend `tests/test_rules_rebuild.py`:
  - `test_agree_keeps_the_hide_and_turns_final`: rules `sponsorship='no'` with quote, reader `wont_sponsor` with quote: `passes(combined.decision)` is False; `provisional` False; `reader_version` set.
  - `test_reader_unknown_relaxes_a_rules_hide`: rules `no`, reader `unknown`: `sponsorship == 'unclear'`, `passes` True, a `raised` verdict with state `missing`.
  - `test_reader_disagrees_with_rules_hide`: rules `no`, reader `will_sponsor`: shown, state `disagree`.
  - `test_reader_hide_without_rules_never_hides`: rules `silent`, reader `wont_sponsor` with a real quote: `passes` True, `raised` non-empty (Review Focus 1).
  - `test_citizenship_and_student_hides_follow_the_same_table`: one test each for `citizenship_or_clearance` and `student_or_grad_window`.
  - `test_location_goes_through_us_status`: rules `us='no'` (structured country), reader location `"Remote, United States"`: `disagree`, shown; reader location `"Berlin, Germany"`: `agree`, stays hidden.
  - `test_flag_only_fields_never_change_passes`: `level`, `engineering`, `years_low`, `start_date` disagreeing leave `passes` untouched and are not in `raised`.
  - `test_no_reading_is_provisional_and_unchanged`: the rules' decision is returned as is; `provisional` True.
  - `test_unreadable_posting_fails_open`: `unreadable=True`: a rules hide is relaxed and every hide field is `missing`.
  - `test_refused_field_counts_as_missing`.
  - `test_apply_reading_writes_the_row_and_one_review_item`: on a scratch store with a rules hide and a disagreeing stored reading: the `decision` row has `provisional = 0`; one `review_item` of kind `reader_disagrees`, `raised_by 'reader'`; a second `apply_reading` adds no item and changes no row.
  - `test_apply_reading_clears_the_item_when_the_reader_later_agrees`.
  - `test_candidate_version_reading_does_not_decide_until_promoted`: a reading at the candidate version, `promoted_version = ""`: `apply_reading` leaves the decision provisional; `explain` still returns its verdicts.
  - `test_apply_all_restores_combined_rows_after_a_rebuild`: run `rebuild` (rows go provisional), then `apply_all`: the combined rows are back, with no reader call.
  - In `tests/test_rules_rebuild.py`: `test_rebuild_with_a_combiner_writes_provisional_and_reader_version` and `test_rebuild_without_a_combiner_resets_provisional_to_one`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_reader_combine tests.test_rules_rebuild -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** `combine.py` and the rebuild hook. The mapping table above is the whole specification of `compare`; the relaxations are the four listed. Reuse `us_status` and `starts_2027`; do not add vocabulary.
- [ ] **Step 4: Run** the same command, then the whole suite and `uv run python tests/mutation_check.py`. Expected: all OK, mutation `missed 0` (update anchors if `rebuild.py` lines moved).
- [ ] **Step 5: Commit** `Reader: combine reader facts with the phrase rules, hide needs both (#32)`.

## Task 5: The CLI for next, submit and apply

**Files:**
- Create: `outreach/reader/__main__.py`
- Test: `tests/test_reader_cli.py`

**Interfaces:**
- Consumes: Tasks 1 to 4.
- Produces: `main(argv: list[str] | None = None, *, stdin=None, stdout=None) -> int` with subcommands:
  - `next --limit N`: runs `enqueue_missing`, then `next_batch`, prints `batch_payload` as JSON (an empty `postings` list when the queue is empty). Exit 0.
  - `submit`: reads JSON from stdin (one reading object, a list, or `{"readings": [...]}`), calls `submit`, prints a JSON list of `SubmitResult` dicts, then runs `apply_reading` for each `stored` or `partial` result. Exit 0 even when readings are refused (the refusals are the output); exit 2 on input that is not JSON.
  - `apply`: runs `apply_all` with the rebuild combiner and prints the count.
  - `night`, `gold`, `eval`, `promote`: registered here, implemented in Tasks 6 to 8 (the tests for this task cover only `next`, `submit`, `apply`).
  - Wire `python -m outreach.rules.rebuild`'s `main` to pass `combiner=` the reader's combiner when the reader package imports cleanly (lazy import inside `main`).

- [ ] **Step 1: Write the failing tests** in `tests/test_reader_cli.py` with `OUTREACH_DB` already scratch (from `tests/__init__.py`) and a store built by `fresh_store`:
  - `test_next_prints_json_batch_and_leases`: output parses; `len(postings) <= limit`; each entry has `posting_id`, `body_sha256`, `reader_version`, `title`, `location`, `body`; queue rows are `leased`.
  - `test_next_on_empty_queue_prints_empty_list`.
  - `test_submit_round_trip`: feed the `next` output's identifiers plus valid fields through `submit`; the output status is `stored`; the decision row is non-provisional once `promoted_version` equals the current version (patch the config for this test).
  - `test_submit_accepts_a_single_object_and_a_wrapper`.
  - `test_submit_rejects_non_json_with_exit_2`.
  - `test_cli_never_touches_the_live_store`: with `OUTREACH_DB` unset to the default path outside the live checkout, `main(['next'])` raises `StoreError` (the store guard), not a crash from elsewhere.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_reader_cli -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with `argparse`; no business logic in the CLI beyond calling the functions above.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Reader: next, submit and apply on the command line (#32)`.

## Task 6: The night job

**Files:**
- Create: `outreach/reader/night.py`, `launchd/com.devanshu.outreach-reader-night.plist.template`
- Modify: `outreach/reader/__main__.py` (the `night` subcommand)
- Test: `tests/test_reader_night.py`

**Interfaces:**
- Consumes: `next_batch`, `release`, `batch_payload`, `enqueue_missing` (Task 2); `submit` and `apply_reading` (Tasks 3 and 4); `start_job`, `finish_job`; `live_checkout.require_live`.
- Produces:
  - `@dataclass(frozen=True) class RunResult: returncode: int; stdout: str; stderr: str`.
  - `Runner = Callable[[str, str, str], RunResult]`: `(system_prompt, user_message, model)`.
  - `classify_run(result: RunResult, cfg: ReaderConfig) -> Literal['ok', 'session_limit', 'auth', 'failed']`: case-insensitive match of `cfg.session_limit_patterns` and `cfg.auth_patterns` against stdout and stderr (a non-zero exit or an `is_error` JSON result is needed for either; an `ok` reply that merely quotes a posting containing "usage limit" must not stop the night).
  - `parse_reply(stdout: str) -> list[dict]`: takes the `claude -p --output-format json` envelope (`{"result": "<text>", ...}`), strips a Markdown code fence if present, parses the JSON, returns the `readings` list; raises `ValueError` on anything else.
  - `claude_runner(cfg: ReaderConfig) -> Runner`: `subprocess.run` of `claude -p --model <model> --output-format json --tools "" --no-session-persistence --disable-slash-commands --system-prompt <prompt text>` with the user message on stdin, `cwd` a neutral folder `~/Library/Application Support/outreach/reader-cwd/` (created on first use) so the repo's CLAUDE.md is not loaded, and `timeout=cfg.call_timeout_s` (a timeout is a `failed` run). Not `--bare` (that disables the seat login). The builder confirms each flag against `claude --help` for the installed version before relying on it.
  - `@dataclass(frozen=True) class NightSummary: status: Literal['queue_empty', 'cap_reached', 'session_limit', 'auth', 'failed']; batches: int; stored: int; partial: int; requeued: int; stopped_reason: str`.
  - `run_night(conn, runner: Runner, cfg: ReaderConfig | None = None, *, now=utcnow) -> NightSummary`: records a `job_run` with job `read_postings`; runs `enqueue_missing`; loops until the queue is empty, `max_postings_per_night` postings were handed out, or `max_batches` batches ran. Each batch: `next_batch(batch_size)`, one `runner` call, then `classify_run`. `session_limit` or `auth`: `release` the batch and stop (job `partial` for a session limit, `failed` for auth). `failed` or a `parse_reply` error: `release` the batch, count a consecutive failure, stop at `max_consecutive_failures`. `ok`: the model's readings get `reader_version` filled in by code (the model does not copy it), go through `submit`, then `apply_reading` for stored and partial results; postings the model skipped are `release`d. Never raises on a model or CLI problem; always closes the job.
  - `main` subcommand `night [--max-batches N] [--max-postings N]`: calls `live_checkout.require_live("the reader night job")` first, then `run_night(conn, claude_runner(cfg))`, prints the summary line.
  - `launchd/com.devanshu.outreach-reader-night.plist.template`: label `com.devanshu.outreach-reader-night`, modeled on `com.devanshu.outreach-refresh.plist`: `StartCalendarInterval` daily at 02:30 (a run missed during sleep fires on wake), command `cd '{{LIVE_CHECKOUT}}' && PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin /usr/bin/caffeinate -i /opt/homebrew/bin/python3 -m outreach.reader night >> '{{LOG_PATH}}' 2>&1`; the comment block says: install is Devanshu's step, with the exact `launchctl bootstrap gui/$(id -u) <plist>` line, and `launchctl bootout` to remove.

- [ ] **Step 1: Write the failing tests** in `tests/test_reader_night.py` with a fake `Runner` that answers each batch from a script, over a scratch store with 45 eligible postings:
  - `test_night_reads_the_queue_in_batches`: fake runner returns valid readings for every posting it is sent (it parses the posting ids from the user message); `batch_size=20`: three batches, 45 stored, status `queue_empty`, job row `ok`.
  - `test_night_cap_stops_the_run`: `max_postings_per_night=30`, `batch_size=20`: stops after 2 batches with `cap_reached`; the rest stay `queued`.
  - `test_session_limit_stops_cleanly_and_releases`: runner returns non-zero with stdout containing `"usage limit reached"`: status `session_limit`; the batch is back to `queued` with `attempts` unchanged; job row `partial`; no exception.
  - `test_auth_error_stops_with_failed_job`: stderr `"Not logged in. Please run /login"`; status `auth`; job `failed`.
  - `test_a_quoted_phrase_in_a_good_reply_is_not_a_limit`: exit 0, reply readings whose quotes contain "usage limit": classified `ok`.
  - `test_fenced_and_wrapped_json_is_parsed`: reply text wrapped in a Markdown fence inside the envelope parses (Review Focus 5).
  - `test_garbage_reply_releases_and_counts_a_failure`: two garbage replies in a row stop with `failed`; leases released.
  - `test_runner_timeout_is_a_failed_run`: the fake raises `subprocess.TimeoutExpired`; handled as `failed`.
  - `test_model_skipping_a_posting_leaves_it_queued`: the reply covers 19 of 20; the 20th is released and appears in the next batch.
  - `test_refused_quotes_requeue_and_finish_at_the_limit`: a runner that always returns a paraphrase: after `max_attempts` nights the postings end `refused` and their decisions are relaxed (fail open); no infinite loop.
  - `test_skipped_night_is_safe`: run `run_night` with a runner that stops at once, then again later; leases from a dead run (set `lease_until` in the past, no release) are handed out again.
  - `test_night_refuses_outside_the_live_checkout`: `main(['night'])` with `live_checkout.LIVE_ROOT` pointed elsewhere raises `SystemExit` naming "the reader night job" and opens no store.
  - `test_plist_template_is_complete`: the template contains the label, the `-m outreach.reader night` command and only the placeholders `{{LIVE_CHECKOUT}}` and `{{LOG_PATH}}`; `plutil -lint` passes after both are filled (skip the lint if `plutil` is absent).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_reader_night -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `night.py`, the `night` subcommand and the template. The real `claude_runner` is not called by any test.
- [ ] **Step 4: Run** the same command and the whole suite. Expected: PASS.
- [ ] **Step 5: Commit** `Reader: night job over claude -p with a cap and clean stops (#32)`.

## Task 7: The labeled set and the sitting CLI

**Files:**
- Create: `outreach/reader/gold.py`, `tests/gold/items.jsonl` (starts empty), `tests/gold/readings/` (empty folder with a `.gitkeep`)
- Modify: `outreach/reader/__main__.py` (the `gold` subcommand)
- Test: `tests/test_reader_gold.py`

**Interfaces:**
- Consumes: `FIELDS`, `ALLOWED`, `FieldReading`, `quote_in_body` (foundation); `body_sha256`; `rules_view` comes later, not needed here.
- Produces:
  - File format, one JSON object per line in `tests/gold/items.jsonl`: `{"gold_id": "g0001", "stratum": "sponsorship:wont_sponsor", "title": str, "location": str, "body": str, "labels": {<every name in FIELDS>: {"value": ..., "quote": str}}, "status": "prelabeled" | "confirmed", "confirmed_on": "YYYY-MM-DD" | null}`. Bodies are stored in the file (public postings), so tests never need the store. `stratum` is `"<field>:<class>"`, or `"random"` or `"disagree"` for the two extra samples.
  - `@dataclass(frozen=True) class GoldItem` with those attributes and `labels: dict[str, FieldReading]`; `load_gold(path: Path = GOLD_PATH) -> list[GoldItem]` raises `ValueError` naming the line and field for: a missing field, a value outside `ALLOWED`, a quote not found in the body (or title or location for `level` and `location`) by `quote_in_body`, a duplicate `gold_id`, a `confirmed` item without `confirmed_on`.
  - `STRATA`: the classes to cover, one `(field, value)` per rare class: `sponsorship` in `wont_sponsor`, `will_sponsor`, `opt_only`, `silent`; `citizenship_or_clearance` `yes`; `student_or_grad_window` `yes`; `location` class `non_us` (derived through `us_status`); `level` in `intern`, `new_grad`, `early_career`; plus `random` and `disagree`. `TARGET_PER_CLASS = 30` (first set; 150 for a hide class before it may rely on the 2% bound, research section 3.3).
  - `sample_gold(conn, per_class: int, *, seed: int, existing: set[str]) -> list[dict]`: draws postings from the store, stratified by the rules' decision classes (rules are the proxy for "class" when picking; the label is whatever Devanshu confirms), skips postings already in `existing` (matched by body hash), returns item dicts with `status "prelabeled"` and empty `labels` for Claude to fill. Deterministic for a seed.
  - `pending(items: list[GoldItem]) -> list[GoldItem]` and `coverage(items) -> dict[str, tuple[int, int]]`: per stratum, `(confirmed, target)`.
  - `confirm(items_path: Path, gold_id: str, *, set_fields: dict[str, FieldReading] | None = None, today: str) -> GoldItem`: applies any corrections, re-validates through `load_gold` rules, marks `confirmed` with `confirmed_on = today`, rewrites the file atomically (temp file in the same folder, then `os.replace`). Refuses a correction whose quote is not in the text.
  - CLI `gold`: `gold sample --per-class N --seed S` appends prelabeled items; `gold show [--limit N]` prints the next pending items readably for a 30-minute sitting (title, body excerpt around each label quote, then the pre-label per field, a field per line, with quote); `gold confirm <gold_id> [--set field=value --quote "..."]...`; `gold confirm --all-shown` confirms what the last `show` printed untouched; `gold coverage` prints confirmed against target per stratum and, for hide classes, the line "n=N, 0 errors would bound the error rate at 3/N".
  - Claude pre-labels in the side session by editing the `labels` of `prelabeled` items; nothing in this repo calls Claude for that.

- [ ] **Step 1: Write the failing tests** in `tests/test_reader_gold.py` against a temp gold file (a helper writes items; no network, no store except `sample_gold`):
  - `test_load_gold_accepts_a_valid_item`.
  - `test_load_gold_rejects_each_bad_case`: missing field, value outside `ALLOWED`, quote not in the body, duplicate id, confirmed with no date; each raises `ValueError` with the line number in the message.
  - `test_level_quote_may_come_from_the_title`.
  - `test_sample_gold_is_deterministic_and_skips_existing`: same seed twice gives the same ids; a body already present is not drawn again.
  - `test_sample_gold_fills_each_stratum_up_to_availability`.
  - `test_confirm_marks_and_rewrites_atomically`: after `confirm`, `load_gold` shows `confirmed` and the date; a crash between write and replace (simulated by making `os.replace` raise) leaves the original file intact.
  - `test_confirm_refuses_a_correction_with_a_paraphrased_quote`.
  - `test_coverage_counts_confirmed_per_stratum`.
  - `test_show_prints_pending_items_with_prelabels`: output contains each field name and the quote text.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_reader_gold -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `gold.py` and the `gold` subcommand.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `Reader: labeled set format and the sitting commands (#32)`.

## Task 8: Eval and the promotion gate

**Files:**
- Create: `outreach/reader/evaluate.py`
- Modify: `outreach/reader/__main__.py` (`eval` and `promote`)
- Test: `tests/test_reader_eval.py`

**Interfaces:**
- Consumes: `load_gold`, `GoldItem` (Task 7); `decide`, `PostingInput`; the Task 4 mapping (`compare`'s per-field mapping, exposed here as `rules_view`); `PostingReading`; `quote_in_body`; `reader_version`.
- Produces:
  - `rules_view(item: GoldItem, cfg: Config | None = None) -> dict[str, str | int | None]`: runs `decide(PostingInput(item.title, item.body, item.location))` and maps each field into the reader vocabulary (the inverse of the Task 4 table: `sponsorship no` to `wont_sponsor`, `unclear` to `unknown`, citizenship true to `yes` else `unknown`, `excluded` to `yes` else `unknown`, `us` through to a location class, lane to `engineering`, tier to `level`, `years_asked` to `years_low`).
  - `reader_view(reading_fields: dict, item: GoldItem) -> dict`: the stored reader output for the item, with every field that fails the quote check turned to `unknown` (the same refusal `submit` applies).
  - `@dataclass(frozen=True) class ClassScore: field: str; value: str; predictor: Literal['reader', 'rules', 'both']; tp: int; fp: int; fn: int; n_truth: int; precision: float | None; recall: float | None` (`None` when the denominator is 0). `both` predicts a value only when reader and rules give the same one, else `unknown`.
  - `score(items: list[GoldItem], readings: dict[str, dict], cfg=None) -> list[ClassScore]`: over confirmed items only, for every non-`unknown` class of every field in `FIELDS`. `readings` maps `gold_id` to the reader's `fields` JSON.
  - `load_readings(version: str, folder: Path = GOLD_READINGS) -> dict[str, dict]`: reads `tests/gold/readings/<version>.jsonl` (`{"gold_id", "fields"}` per line).
  - `run_reader_on_gold(items, runner, cfg, version) -> dict[str, dict]`: sends confirmed and pending gold items through the same prompt and `parse_reply` as the night job, in batches of `batch_size`, using the injected `Runner`; writes `readings/<version>.jsonl`. Only invoked by `eval --run`; it is the one part that spends seat usage, and never runs in tests with a real runner.
  - `not_worse(new: list[ClassScore], old: list[ClassScore]) -> list[str]`: the regressions, one line each; empty means the new version may be promoted. A regression is any `(field, value, predictor in {'reader', 'both'})` where new precision is below old precision, or new recall is below old recall, where both are defined; a class missing from `old` is not compared.
  - `promote(version: str, items, new_scores, old_scores, config_path) -> None`: raises `ValueError` listing the regressions when `not_worse` is non-empty, else rewrites the single `promoted_version = "<version>"` line in `outreach/reader/config.toml` (atomic replace). Devanshu commits that change; `promote` never commits.
  - CLI `eval [--version V] [--against OLD] [--run] [--json]`: prints a table per field and class with `n`, and precision and recall for reader, rules and both; with `--against`, prints the regression lines and exits 1 if any. `promote --version V --against OLD` runs the gate and writes the line.

- [ ] **Step 1: Write the failing tests** in `tests/test_reader_eval.py` over a small hand-built gold set (6 to 10 items) and fake readings, no runner:
  - `test_rules_view_maps_each_field`: a body with "We do not sponsor visas" gives `sponsorship == 'wont_sponsor'`; "US citizenship required" gives `citizenship_or_clearance == 'yes'`.
  - `test_reader_view_blanks_an_unquoted_fact`: a reading whose quote is a paraphrase scores as `unknown`.
  - `test_score_precision_and_recall_per_class`: three items truth `wont_sponsor`; reader predicts it for two of them and wrongly for one `silent` item: reader precision 2/3, recall 2/3 for that class, exact fractions asserted.
  - `test_both_needs_agreement`: reader says `wont_sponsor`, rules say `silent`: `both` predicts `unknown`, so recall drops and precision stays defined.
  - `test_unconfirmed_items_are_ignored`.
  - `test_output_has_a_row_for_reader_rules_and_both`: the printed table lists all three predictors per class with `n`.
  - `test_not_worse_flags_a_precision_drop_on_a_hide_class`: returns one line naming the field and value; `not_worse(x, x) == []`.
  - `test_not_worse_ignores_classes_missing_from_old`.
  - `test_promote_refuses_on_regression_and_leaves_config_untouched`; `test_promote_rewrites_only_the_version_line` (the other lines byte for byte equal).
  - `test_eval_run_uses_the_injected_runner_and_writes_readings`: a fake runner; the `readings/<version>.jsonl` file appears in a temp folder.
  - `test_eval_against_exits_1_on_regression`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_reader_eval -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `evaluate.py` and the two subcommands.
- [ ] **Step 4: Run** the same command and the whole suite. Expected: PASS.
- [ ] **Step 5: Commit** `Reader: eval per field and class, and the promotion gate (#32)`.

## Task 9: Register the read_postings stage in the pipeline flow test

Foundation Task 6 gives every stream one shared flow test. This task registers the reader's stage and adds the
reader's invariants, so a break between the reader and the crawler, the rules or the ranker fails at this PR.

**Files:**
- Modify: `tests/pipeline/harness.py` (stage registration only), `tests/pipeline/test_flow.py`
- Modify: `tests/fixtures/pipeline/` (add `posting_hide.txt`: a posting body of at least 500 characters that says "We do not sponsor visas." and states nothing else that hides it)
- Test: `tests/pipeline/test_flow.py`

**Interfaces:**
- Consumes: `PipelineHarness` (`.conn`, `.fixture(name)`, `.stages`, stage names in the foundation's order `discover, resolve, find_boards, read_boards, read_postings, rules, rank, plan`); `enqueue`, `next_batch`, `submit`, `run_night`, `Runner`, `RunResult`, `reader_combiner`, `apply_reading`; `rebuild`, `decide_and_store` from `outreach/rules/rebuild.py`.
- Produces: `read_postings_stage(h: PipelineHarness, runner: Runner | None = None) -> None`, appended to `h.stages` under the name `read_postings`. It decides the fixture postings the crawler stage left (`decide_and_store(h.conn, pid, combiner=reader_combiner(h.conn))`, after a `rebuild` if the store has no current ruleset), calls `enqueue` for each as #21 will, then `run_night(h.conn, runner)`. `runner` defaults to a fake that answers every posting from a table of scripted readings keyed by posting title; the real `claude_runner` is never constructed in the flow test. The `rules` stage registered by #4 must call `rebuild(conn, combiner=reader_combiner(conn))` and then `apply_all(conn)` so the reader's decisions survive a rebuild; the reader PR makes that change to the stage if it is already registered.
- Boundaries (do not cross): #21 (crawler) never writes `read_queue` (controller ruling 2026-10-21): it stores the body and its `body_sha256`, and this plan's `enqueue_missing` (run before each night batch and on `next`) queues it; this plan owns leasing, `submit` and re-queue. #6 (ranking) writes `company_score`; the queue order reads it when present and ignores it when absent, so the flow test passes with the `rank` stage unregistered and again with a one-row `company_score` fixture.

- [ ] **Step 1: Write the failing flow tests** in `tests/pipeline/test_flow.py` (each builds the harness, registers the stage with a scripted fake runner, runs it, then asserts on `h.conn`):
  - `test_flow_refused_quote_leaves_the_decision_provisional`: the fake returns `sponsorship = wont_sponsor` with the paraphrase `"no visa sponsorship is offered"`. After the stage: no `posting_reading` row for the posting; its `decision` has `provisional = 1` and `reader_version IS NULL`; `passes()` of the stored decision is still False (still a rules hide, read again next night); the queue row is `queued` with `last_error` naming `sponsorship`.
  - `test_flow_agreeing_verified_reading_makes_the_decision_final`: the fake returns `wont_sponsor` with the quote `"We do not sponsor visas."` and `promoted_version` set to the current version (patched config). After the stage: one `posting_reading` row with `verified = 1`; the decision has `provisional = 0` and `reader_version` equal to the current version; `passes()` is still False; no `review_item`.
  - `test_flow_disagreement_with_a_provisional_hide_shows_the_posting_and_raises_the_review`: the fake returns `sponsorship = unknown` with an empty quote. After the stage: the decision has `provisional = 0`, `passes()` is True (shown again), and exactly one `review_item` with kind `reader_disagrees`, `raised_by = 'reader'`, `posting_id` set.
  - `test_flow_queue_order_ignores_company_score_when_absent_and_reads_it_when_present`: run `next_batch` before and after inserting one `company_score` row; the first call works with the table empty, the second puts that company's lane 1 posting first.
  - `test_flow_a_rebuild_keeps_the_reader_decisions`: after the agreeing case, run the `rules` stage (rebuild with the combiner, then `apply_all`); the decision is still `provisional = 0` with the same `reader_version`, and no reader call is made (the fake runner's call count is unchanged).
- [ ] **Step 2: Run** `uv run python -m unittest tests.pipeline.test_flow -v`. Expected: the new tests FAIL (stage not registered).
- [ ] **Step 3: Implement** `read_postings_stage`, register it in the harness, add the fixture body. No product code changes; if a flow test fails because of a bug in Tasks 2 to 6, fix it there with a unit test first.
- [ ] **Step 4: Run** the full suite. Expected: all OK, and the foundation's `test_flow_runs_every_registered_stage` still passes.
- [ ] **Step 5: Commit** `Tests: reader stage and invariants in the pipeline flow test (#32)`.

## Task 10: Docs and the real run

**Files:**
- Modify: `docs/README.md` (a row for this plan), `docs/superpowers/plans/2026-10-21-pipeline-foundation.md` (nothing if the foundation merged unchanged; otherwise only a note if a name changed)

**Interfaces:**
- Produces: the hand-off list the other streams read, written as a short "Hand-off" section at the end of this plan. Boundaries: #21 (crawler) only calls `enqueue` and `enqueue_missing` and writes nothing else in `read_queue`; this plan owns leasing, `submit`, re-queue and the combination. #6 (ranking) writes `company_score`; the queue order reads it when present and ignores it when absent. Each stream registers its stage in `tests/pipeline/harness.py` (the reader's is `read_postings`, Task 9). #13 wraps `next_batch(conn, limit)` plus `batch_payload` and `submit(conn, readings)` plus `apply_reading` as its two reader tools. #21's crawler calls `enqueue(conn, posting_id)` after it stores a new or changed body, then `decide_and_store(..., combiner=reader_combiner(conn))`. #6 reads `current_decision.provisional` and relies on `passes()` for posting-level hides. #11 and #12 call `explain(conn, posting_id)` for flagged fields and both quotes. After any `rebuild`, run `python -m outreach.reader apply`.

- [ ] **Step 1: Edit the docs.** No em or en dashes.
- [ ] **Step 2: Run** `grep -c -P "[\x{2014}\x{2013}]" docs/README.md docs/superpowers/plans/2026-10-21-posting-reader.md outreach/reader/prompts/read_posting.md`. Expected: `0` for each.
- [ ] **Step 3: Run** the whole suite and the mutation check. Expected: all OK, `missed 0`.
- [ ] **Step 4: Real side-session run (the "Done when" proof, by Devanshu or in his Claude session, against a scratch store).** Set `OUTREACH_DB` to a scratch path, `python -m outreach.install`, load a copy of at least 40 real engineering postings with bodies (the interim spike's data or an import), run `python -m outreach.reader next --limit 20`, have the side session read the printed JSON with the pinned prompt and pipe its answer to `python -m outreach.reader submit`. Record in the PR: 20 readings stored or refused, the refusal count and reasons, the `cache`-free token use if visible, and the first real `claude -p` stdout for a session limit or auth error if one occurs (add it to `session_limit_patterns` or `auth_patterns` and to a test fixture).
- [ ] **Step 5: Run one real `claude -p` batch from the live checkout** (`python -m outreach.reader night --max-batches 1 --max-postings 20`) only after Devanshu says so; confirm the `--model claude-haiku-5-5` and `--tools ""` flags work on his seat, and note any difference in the PR.
- [ ] **Step 6: Commit** `Docs: reader hand-off for the pipeline streams (#32)`.

## Hand-off

What the other streams call and where the boundaries sit.

| Stream | Calls | Boundary |
|---|---|---|
| #21 crawler | `enqueue(conn, posting_id)` after it stores a new or changed body, `enqueue_missing(conn)` for a backlog, then `decide_and_store(conn, posting_id, combiner=reader_combiner(conn))` | The crawler writes nothing else in `read_queue`. This plan owns leasing, `submit` and re-queue. Eligibility (lane, body length) lives in `enqueue` only, so it is not duplicated |
| #6 ranking | Writes `company_score`. Reads `current_decision.provisional` and keeps `passes()` as the posting-level hide policy | The queue order reads `company_score` when present and ignores it when absent. #6 never reads `read_queue` or `posting_reading` |
| #13 MCP | Two reader tools: `next_batch(conn, limit)` with `batch_payload`, and `submit(conn, readings)` followed by `apply_reading` for each stored or partial result | The tools return only what the side session needs (the payload and the `SubmitResult` list) |
| #11 and #12 API and web | `explain(conn, posting_id)` for flagged fields and both quotes; open `review_item` rows of kind `reader_disagrees` for Needs you | Read only |
| #4 rules (`rules` stage) | `rebuild(conn, combiner=reader_combiner(conn))`, then `apply_all(conn)` after every rebuild | Without the combiner a rebuild resets `provisional` to 1 until `apply_all` runs |
| Pipeline flow test | The reader registers `read_postings` in `tests/pipeline/harness.py` and adds the three invariants in Task 9 | The flow test stays green on every PR |

## Self-review (against the spec and issue)

- Section 7.1 facts with quotes and refusal: Tasks 1 and 3. 7.2 queue, `next`, `submit`, night job, side session, version hash, re-read on version change: Tasks 1, 2, 5, 6 (`enqueue_missing` covers a new version, never a crawl). 7.3 combination, provisional hides, queue order, backlog inside the cap: Tasks 2, 4, 6. 7.4 labeled set and `eval`: Tasks 7 and 8. Section 8 hide list: the `HIDE_FIELDS` table. Section 12 `read_queue` and `posting_reading`: used as the foundation defines them, no schema change here. Section 14 step 2 "lanes and the combination in #4's rules and #6": only the combiner hook touches `rebuild.py`; lane hide policy stays `passes()` and #6.
- Issue "Done when": a fake reader tests lease (Task 2), refusal of an unquoted or paraphrased fact (Task 3), re-queue on a body change (Tasks 2 and 3), a skipped night (Task 6), eval output (Task 8); the 20-posting real run is Task 10 step 4. The coordinator's flow-test rule (register the stage, add invariants) is Task 9.
- Names used across tasks: `ReaderConfig`, `reader_version`, `body_sha256`, `enqueue`, `enqueue_missing`, `refresh_priorities`, `next_batch`, `release`, `batch_payload`, `submit`, `SubmitResult`, `combine`, `Combined`, `FieldVerdict`, `apply_reading`, `apply_all`, `explain`, `run_night`, `Runner`, `RunResult`, `load_gold`, `score`, `not_worse`, `promote`. Each is defined once, in the task that lists it under Produces.

## Open questions for Devanshu

1. **Stored decision rows hold the combined facts** (a reader disagreement rewrites the rules' hide to its non-hide value, and the rules' own answer is recomputed from the body when needed). The alternative is a separate table of reader effects, which needs a new migration. Which does #6 prefer?
2. **After the retry limit a posting the reader cannot quote fails open** (shown and flagged), instead of staying a provisional rules hide forever. Spec 7.3 says a refused reading leaves the posting on the phrase rules; the research and "never hide a reachable company" say show it. This plan shows it. Confirm.
3. **Quote haystack.** `level` may quote the title and `location` the structured location header; everything else must be in the stored body. Spec 7.1 says only "the stored body".
4. **Model and CLI facts to verify on his seat:** that `claude -p --model claude-haiku-5-5` works on the Enterprise seat, that `--tools ""` and `--system-prompt` behave as `--help` says in 2.1.306, and the exact text a session limit and an expired login print (the patterns in config are guesses until the real run captures them).
5. **Promotion gate strictness.** `not_worse` blocks any drop in precision or recall on any class, even with a handful of items. With small classes that is noisy. Keep strict, or require a minimum `n` before a class can block?
6. **Starting numbers:** night at 02:30, 20 per batch, a cap of 400 postings and 25 batches a night, 3 attempts, 30 minute leases, top 200 companies for tier 2, 500 characters minimum body. All are config, not code.
7. **Where `promoted_version` lives.** In `config.toml` (a git commit, visible in history) as planned, or in the store so a promotion needs no commit?

## Controller rulings on the open questions (2026-10-21)

0. **This plan owns every `read_queue` write,** including priority (design 7.3). The crawler does not enqueue.
   The flow invariant "a new engineering posting with a body lands in `read_queue` with the right priority" is this
   plan's (Task 9), checked after `enqueue_missing`. `posting_body.body_sha256` comes from #30's Task 1.

1. **Decision rows hold the combined facts** (no new migration). The rules' own verdict can always be recomputed by
   `decide()` from the stored body, which is what `eval` does, and `posting_reading` keeps the reader's side.
2. **After the retry limit, an unquotable posting fails open:** shown, flagged, and a Needs-you item raised. A
   rules-only hide never stays hidden forever just because the reader could not quote. This follows the design's
   first rule (a wrong show is cheap) and section 7.3 (a hide needs both).
3. **Quotes may come from the title and the structured location as well as the body.** `quote_in_body` is called
   on `title + "\n" + location + "\n" + body`.
4. **Checked on his machine:** Claude Code 2.1.306 has `-p`, `--model`, `--tools`, `--system-prompt`,
   `--output-format` and **`--json-schema`** (structured output). Use `--json-schema` with the reading schema, so
   malformed answers are rare and `submit` still validates everything. The session-limit and login-expired texts
   stay config patterns until the first real run (Task 10).
5. **`not_worse` gates only classes with at least 20 labeled examples;** smaller classes are reported, not gated.
6. **Starting numbers as proposed** (02:30 run, 20 per batch, 400 and 25 batches a night, 3 attempts, 30-minute
   leases, top 200 companies, 500-character minimum body), all in config.
7. **`promoted_version` lives in `config.toml`,** so promoting a reader is a reviewed commit.
