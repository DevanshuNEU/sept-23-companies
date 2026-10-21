# Integration review of the eight pipeline plans

Date: 2026-10-21. Reviewer: integration pass over the approved design and the eight plans dated 2026-10-21.
Review only: no plan was changed. Each finding names both sides with plan and line, and the smallest fix.

Abbreviations (all files in `docs/superpowers/plans/` unless noted):

| Key | File | Issue |
|---|---|---|
| D | `../specs/2026-10-20-opportunity-pipeline-design.md` | design |
| F | `2026-10-21-pipeline-foundation.md` | #30 |
| S | `2026-10-21-sources-and-identity.md` | #31 |
| C | `2026-10-21-diff-crawler.md` | #21 |
| R | `2026-10-21-posting-reader.md` | #32 |
| K | `2026-10-21-ranking.md` | #6 |
| P | `2026-10-21-daily-people-plan.md` | #15 |
| A | `2026-10-21-api.md` | #11 |
| W | `2026-10-21-web-experience.md` | #33 |

Controller rulings were applied before judging: task text a ruling overrides is not reported, except where the
overridden text still drives a name, a test or a signature another plan depends on.

Counts: Blocking 7, Important 19, Minor 16.

---

## Blocking

### B1. Two different `body_sha256` functions over one set of columns

- C:86, C:152: `outreach/crawl/text.py::body_sha256(text)` is sha256 of `normalize_body(text)` (whitespace runs
  collapsed). The crawler writes it into `posting_body.body_sha256` (C:279, C:280).
- R:67, R:99: `outreach/reader/version.py::body_sha256(text)` is sha256 of the raw UTF-8 body. The reader writes it
  into `read_queue.body_sha256` and `posting_reading.body_sha256` (R:126, R:164, R:204 "the posting's current body
  hash").
- F:123 says only "sha256 hex of the body", so both readings satisfy #30.

Every body the crawler stores has line breaks (`html_to_text` emits them, C:152), so the two hashes differ for
almost every posting. Any reader or later stream that matches `posting_body.body_sha256` against a queue or
reading row finds nothing, and `submit`'s stale check (R:164) can misfire if it compares against the column.

Fix: F Task 3 defines the one function (`outreach/reader/types.py::body_sha256`, or a small `outreach/store/hashing.py`)
and states the normalization (pick the crawler's: whitespace collapsed, case kept). C Task 3 and R Task 1 import it
and define none. F Task 1 adds the CHECK `body_sha256 = '' OR length(body_sha256) = 64` that C:117 already assumes.

### B2. `work_tier` (foundation) against `company_work_tier` with extra columns (sources)

- F:102 to F:104: table `work_tier(company_id, tier, promoted, reason, set_at)`.
- S:96, S:299 (`table_marks` lists `company_work_tier`), S:354 (`set_work_tier(..., source_version)` upserts a
  `source_version` column), S:583 (hand-off to #21 names `company_work_tier`). S ruling 1 (S:21) moved the table to #30
  but the names and the `source_version` and `updated_at` columns stayed in the tasks.
- K:11, K:64, K:246 read `work_tier`.

`set_work_tier` fails its first insert against #30's table, and `table_marks` fails on a missing table.

Fix: S Tasks 7 and 8 and the hand-off use `work_tier` and `set_at`, and `set_work_tier` drops `source_version`
(the source version is already in `source`). If the version per tier row is wanted, F Task 1 adds
`source_version TEXT NOT NULL DEFAULT ''` instead; one of the two, not both.

### B3. `daily_list_entry.why` keys: the plan writes one shape, the API reads another

- P:355 (Task 9, "exact keys"): `lane`, `reason`, `signals`, `role {posting_id, title, url, age_days, location}`,
  `person {..., found_on {label, url}}`, `channel_reason`, `find_someone {search_url, handoff}`, `draft_id`,
  `carried_from`, `first_proposed`, `rotation`, `score`, `ranker_version`. P:561 maps them for the web.
- A:73 (Decisions) and A:326 (Task 10) read `reason`, `reasonSources`, `applyUrl`, `searchUrl`, `handoff`,
  `foundOn`, and say "#15 and #13 write them". A:587 repeats it as a demand on #15.

Only `reason` matches. Every live plan row would show the lane's default sentence, no source links, no apply link
and the API's fallback search link.

Fix: A Task 10 `parse_plan_why` reads P Task 9's keys (P:561 already gives the mapping: `applyUrl` is
`why.role.url`, `foundOn` is `why.person.found_on`, `findSomeone` is `why.find_someone`, `reasonSources` from
`why.signals` sources or the evidence links). Delete the key list in A:73 and the line in A:587.

### B4. The crawler and its tick re-decide without the reader's combiner, and nobody runs `apply_all`

- R:208 to R:209: after R Task 4, `decide_and_store` and `rebuild` without a `combiner` write `provisional = 1` and
  the rules-only facts. R:416 and R:420 ask #21 to pass `combiner=reader_combiner(conn)` and every rebuild to be
  followed by `apply_all`.
- C:280, C:325, C:327: `decide_postings` calls `decide_and_store` with no combiner for new, changed and
  body-updated postings and for every undecided posting. C:348: `ensure_ruleset_current` runs `rebuild(conn)` with no
  combiner at the start of every tick after a ruleset change. C has no `apply_all` step and R's night job
  (R:275) runs `enqueue_missing` only.

Effect: a posting whose rules hide the reader overturned (R:54) is hidden again after a title edit or any ruleset
change; its body hash is unchanged so the queue row stays `done` and it is never re-read. Ranking then hides the
whole company as "pending reader" (K:59). That is a hide without evidence, against the hide policy.

Fix: C Task 9 `decide_postings` and C Task 12 `ensure_ruleset_current` take a `combiner` and the tick passes
`outreach.reader.combine.reader_combiner(conn)` through a lazy import (none when the reader package is absent).
R Task 6 `run_night` calls `apply_all` first when any decision row for an open posting with a stored reading has
`provisional = 1`, so a missed combiner heals overnight.

### B5. `outreach_touch.context`: the plan counts `context.source = 'plan'`, nobody writes it

- P:27 to P:29 (ruling 3): the 150 a month system quota counts only touches with `context.source = 'plan'`.
- A:302: `build_context` is "the one shape" with keys `lane`, `roleKind`, `roleAgeDays`, `rankerVersion`,
  `signals`, `roleNamed`; no `source`. A:352 and A:356 write it for plan requests.
- F:134 lists the context content without key names or casing.

The quota never moves, so the plan proposes about 7 a day until the 200 hard total binds, overspending the
system's share by up to 50 a month.

Fix: F Task 1 pins the context keys and their casing (one list, used by every writer) and adds `source`
(`plan`, `linkedin_list`, `own`). A Task 9 `build_context` takes `source`; A Task 11 writes `plan`, A Task 16 writes
`linkedin_list`.

### B6. LinkedIn notes: two writers, two checkers, and no way to confirm the reason line

- P:113 (`linkedin_draft` with `reason_line`, `reason_state`, `saved_by`), P:282 (check codes, `note_too_long`,
  banned phrase and dash are blocks), P:285 (`save_linkedin_draft`: a Claude save raises `DraftLocked` only when
  `reason_state` is `confirmed` or `written`), P:286 (`confirm_reason`), P:560 (API should call
  `save_linkedin_draft(saved_by='devanshu')` and `confirm_reason`, `needsYourLine` is `reason_state == 'proposed'`).
- A ruling 1 (A:27 to A:31) maps notes onto `linkedin_draft` but A:326 still sets `needsYourLine` from an empty body,
  A:355 saves by `bump` with A's own `check_note` (A:449: code `note_length`, banned phrase and dash only warn), and A
  has no confirm route.
- W:183 to W:187 and W:253: `NoteDraft` has no `reasonLine` or `reasonState`, and `saveNote` sends only `text`.

Effect: an edit Devanshu saves through the API leaves `reason_state = 'proposed'`, so the next morning's prepare run
(P Task 13) overwrites his note; the `checks` column holds two vocabularies; design 18.2 ("he writes or rewrites the
one line") has no path in the UI.

Fix: A Task 11 `PUT /api/notes/{id}` calls P's `save_linkedin_draft(saved_by='devanshu', reason_line=...)` and A adds
`POST /api/notes/{id}/confirm` calling `confirm_reason`; A drops `outreach/drafts/notes.py`. W Task 2 adds
`reasonLine: string; reasonState: 'proposed' | 'confirmed' | 'written'` to `NoteDraft`, `saveNote` sends
`{text, reasonLine, revision}`, and adds `confirmReason(noteId, revision, o)` with a key in the note block.

### B7. The "board match doubtful" item cannot be raised from the #30 adapter contract

- S:26 to S:29 (ruling 3) and C:31 to C:34 (ruling 8): on the first read, a board whose org name differs from the
  company's names, or a 25-or-fewer LCA filer with a board over 300 jobs, raises a `flagged` item.
- F:184 to F:188: `JobRef` and `ReadResult` carry no board or org name. Neither ruling says how #21 knows the board
  came from a name-only match (S:408 records it only in the `board`/`found` event detail `matched_on`).
- K:58, K:178: ranking matches only items with a `board_id`, `raised_by = 'crawler'` and the reason prefix; C ruling 8
  does not say `board_id` is set.

Without the item, a wrong stapply board's "we do not sponsor" posting writes a permanent `no_go` on the right
company (K:59), a hide on someone else's evidence.

Fix: F Task 2 adds `board_name: str | None = None` to `ReadResult` (adapters fill it where the ATS returns it).
C Task 11 adds a first-read check: the board has no earlier `board_read` row and its `board`/`found` event has
`matched_on` in (`name`, `slug`); raise `flagged` with `board_id` set, `raised_by 'crawler'`, `evidence_key
'board-doubtful:<board_id>'`, reason prefix `board match doubtful`. Until the item is resolved K ignores the board
(already planned).

---

## Important

### I1. Two HTTP layers, two fakes, two User-Agent policies

- S:63, S:123 to S:129: `outreach/net.py` (`Response`, `FetchError`, `HttpClient`, `UrllibClient`, `PoliteClient`),
  `tests/fakes.py::FakeClient`, and a declared UA that contains Devanshu's email (S:126).
- C:84 to C:85, C:135 to C:137: `outreach/crawl/http.py` and `polite.py` with other shapes (`FetchError.kind`,
  `follow_redirects`, `post`, a host breaker), `tests/crawl_fakes.py::FakeClient`; C:25 and C:47: the UA must
  contain no email.
- F:295: `check_adapter` takes "an adapter wired to a fake HTTP client" without naming one.

Fix: F Task 2 owns one `HttpClient` Protocol, `Response`, `FetchError` and one `tests/fakes.py::FakeClient`; C Task 2
keeps `PoliteClient` and the breaker on top of it and S Task 2 drops its copy. One UA policy in F's Shared formats:
no email by default; SEC hosts alone get the contact address SEC's access policy asks for, from
`OUTREACH_USER_AGENT`.

### I2. Token case and character set differ between #30, #31 and #21

- F:34 to F:35: tokens lower case, Workday `<tenant>.wd<N>/<site>`, Oracle `<host>/<siteNumber>`.
- S:406: `normalize_token` lower-cases everything and allows `%` in slugs.
- C:106 example `eeho.fa.us2.oraclecloud.com/CX_1`, C:172 `TOKEN = ^[A-Za-z0-9_.-]+$` (no `%`), C:442 Workday site
  `[A-Za-z0-9_-]+`. Oracle `siteNumber` is likely case sensitive in the finder (`siteNumber=CX_1`).

Fix: F Shared formats keep the host and tenant lower case but keep Workday `site` and Oracle `siteNumber` as the
ATS spells them, and name one slug grammar for both S and C (drop `%`, or add it to C's `TOKEN`). S Task 10 and Task
14 lower-case only the parts F says.

### I3. Ranking expects `median_jr_wage` in `lca` evidence; sources never writes it

- K:19 to K:21 (ruling 3), F:86 to F:87 (optional key).
- S:95 ("exactly the keys"), S:189 (`FyStats` has no wage), S:269 and S:274 (the value and its pinned test).

Fix: S Task 4 adds `median_jr_wage: int | None` to `FyStats` (median of the annualized `WAGE_RATE_OF_PAY_FROM` over
the year's junior software filings, read with the other columns), S Task 6 writes it when known, S Task 1 accepts
the optional key, and the pinned test value gains it.

### I4. `company_post` has no writer and `asks_email` has no rule

- S:25 (ruling 2: HN posts are stored in `company_post`), but S Task 12 (S:452 to S:455) only yields `Candidate`s; no
  task writes `company_post` after identity resolves, and nothing defines `asks_email`.
- Readers: K:63 and K:246 (reach via an HN post), P:248 and P:424 (the email channel and the prepare payload).

Fix: S Task 12 adds `HnSource.posts()` returning `(source_ref, url, posted_at, text, asks_email)`, and S Task 8
`run_discover` writes `company_post` (ON CONFLICT DO NOTHING) for each resolved HN candidate. `asks_email` is 1 when
the post text holds an email address or a phrase from a small list in `hn.toml` ("email me", "send your resume to").

### I5. The flow harness: the `rules` stage has no owner, registration has no convention, and tests disagree on what runs first

- F:300 to F:306 name eight stages including `rules`; no plan registers `rules`. R:381 says "#4 registered" it, but #4
  predates the harness and is not one of these plans.
- Registration: S:543 and S:560 (a `sources_stages.register` called from `test_flow.py`), C:397 to C:402
  (`stage_read_boards.py`, "wire it into the harness setup"), R:375 and K:469 (edit `harness.py`), P:508 to P:513
  (`stage_plan.py`). #31, #21 and #32 edit the same two files in parallel.
- R:384 ("each builds the harness, registers the stage") against P:513 and R:381 (seed "relative to whatever the
  earlier stages produced", "decides the fixture postings the crawler stage left"), and K:477 (the rank stage seeds
  its own 16 companies). Global counts such as K:483 ("the `no_go` table has exactly one rules row") break when a
  run reaches `rank` with R's agreeing reading promoted.

Fix: F Task 6 defines `tests/pipeline/stages/NN_<name>.py`, each exposing `register(h)`, loaded by the harness in
stage order, and the rule "a flow test runs every registered stage up to and including its own, on a fresh harness;
invariants count only rows the stage itself made". F Task 6 also registers the `rules` stage itself:
`rebuild(conn, combiner=reader_combiner(conn) if importable)` then `apply_all` if importable. K:483 counts only its
fixture companies.

### I6. The API seeds its own rows and never reads what the producers write

- A:249: each view test seeds "through SQL helpers in the test (no dependency on another stream's code, only on its
  tables)". F:300 to F:306 has no API stage. B3 and B6 would pass every API test.

Fix: A adds one flow test (Task 10 or 24) that runs the harness through `plan` and asserts `GET /api/plan` returns rows
whose `reason`, `applyUrl`, `findSomeone.searchUrl` and `note` come from the stored `why` and `linkedin_draft`, and
that `GET /api/companies/{id}/score` matches the stored `company_score`.

### I7. LinkedIn capacity and tomorrow's count are computed in two places

- P:64, P:189, P:27 to P:29 (two limits: 150 plan quota by `context.source`, 200 hard total), P:560 (the API should
  call `linkedin_capacity` for `linkedinMonth`).
- A:79 (`[plan] daily_people = 7, linkedin_monthly_limit = 150`), A:326 (`linkedinMonth.used` counts every request
  against 150; `tomorrow.total` is the constant 7), A:587 ("they must agree").

Fix: A Task 10 calls P's `linkedin_capacity` (both limits) and derives `tomorrow.total` from P's capacity for the next
weekday; A drops `[plan]` from its config. W:206 `linkedinMonth` gains `hardLimit` if both numbers are shown.

### I8. Two ways to add a person

- A:286: `POST /api/companies/{id}/people` inserts `person` directly (no `person_source`, no LinkedIn URL
  normalization, no refusal rules).
- P:252: `add_candidate` (refusals, normalization, sources, one event); P:560: "a pasted profile arrives through #11
  as `add_candidate(added_by='devanshu')`".

Fix: A Task 8 calls `add_candidate(added_by='devanshu')` and maps its refusal codes to `bad_request` or `conflict`;
the request body gains `sources`.

### I9. Order: #11 needs #15's functions but runs beside it

- P:559 to P:560 make the API call `save_linkedin_draft`, `confirm_reason`, `linkedin_capacity`, `add_candidate` and
  `build_plan`. Build order runs #15 and #11 in parallel after #6, so A re-implemented them (I7, I8, B6).

Fix: P lands Tasks 2 to 7 (config, outbound, capacity, people, notes and drafts) as its first PR; A Tasks 8, 10 and
11 wait for that PR. Nothing else in A waits.

### I10. Order: `web/src/api/types.ts` is edited by both #11 and #33

- W:134: Task 2 owns `types.ts`. A:253 (Task 6 widens `JobKind`, adds `HealthView`), A:266 (Task 7 `ScoreView` "if #33
  has not landed it"), A:515 (Task 20 `ReviewFix` merge variant), A:566 (Task 24 rewrites every type as a re-export of
  `schema.ts`). A branches from master (A:13); W branches from `web-views` (W:13).

Fix: A Task 24 runs only after #33 has merged into master, and A's other type edits go in as small PRs against the
W branch's owner file (or wait for it). State both in A:13 and W:134.

### I11. `classify_run` signatures differ between the reader and the plan

- R:271: `classify_run(result: RunResult, cfg: ReaderConfig)`.
- P:478: import it from the reader, else define `classify_run(result, patterns)` "with identical signatures".

Fix: R Task 6 makes it `classify_run(result, *, session_limit_patterns, auth_patterns)`; P Task 14 calls it with its
own config lists.

### I12. `weights_changed`: written with one detail, read with another; no actor named

- K:26 to K:27 (ruling 8): detail is old and new version and the changed weights.
- A:77: reads `detail.text` and optional `detail.finding`; A:34.
- F:113: `event.actor` adds only `source` and `reader`; K names no actor.

Fix: K Task 11 writes `entity 'score'`, `actor 'system'`, detail `{old_version, new_version, changed: {name: [old,
new]}, text}` where `text` is one plain line ("E-Verify weight 12 to 40"). A Task 13 reads `text` and the versions.

### I13. Boards on an ATS with no adapter get neither reads nor a watched page

- S:405 (`SUPPORTED_ATS` is the whole schema set, so `gem`, `icims`, `dover`, `eightfold`, `paycom`, `personio`,
  `join_com`, `cornerstone`, `successfactors` boards are created), S:509 (a company with an active board is never
  checked again).
- C:349 and C:518: a board whose ATS has no adapter is never selected. C builds 14 adapters (Tasks 4 to 26).
- D:222 to D:226 (6.3): companies with no readable board get a weekly watched page.

Fix: S Task 15 also gives a `watched_page` (the careers URL, or the board URL) to companies whose only active boards
have no adapter in `outreach.crawl.ats.supported_ats()` when that module imports; C Task 14 reads those pages as any
other.

### I14. Slug probing (design 5.3 step 2) has no owner

- D:189. S:590 defers it to #21 ("verification needs the #21 adapters"); C has only the manual `probe` command (C:366).

Fix: C adds a task after Task 26: `probe_slugs(conn, company_ids, limit)` that tries the candidate slugs from the
company's name aliases on Greenhouse, Lever and Ashby (one request each, through `PoliteClient`), and calls #31's
`add_board(via='probe', matched_on='slug')` only for an `ok` read; run from `find-boards` after stapply.

### I15. A merge cannot be undone

- D:169 (5.2: "A merge is an event and can be undone").
- A:514: the `merge` fix sets `merged_into` and `status 'removed'`; no undo route.

Fix: A Task 20 adds `POST /api/companies/{id}/unmerge {revision}`: clears `merged_into`, restores the status before
the merge from the merge event's detail, appends an event; the moved alias stays with the survivor.

### I16. The monthly report has no reply rate by signal or by channel

- D:333 ("for each signal bucket, touches and replies") and D:389 ("reply rate by signal, lane, role kind and
  channel").
- A:77: findings contrast only role kind, lane and role named.

Fix: A Task 9 `report.findings` also groups by channel and by each signal bucket in `context.signals` (value above
zero, zero, below zero), with the same 20-per-group minimum.

### I17. Hidden companies and reader flags are never shown

- D:478 (16.9: every rank, flag and hide shows its reason and source). K:368 and K:547: `hidden_list` is "for the
  Hidden view and its reasons", including "hidden, pending reader" and over-300 hides that are not in `no_go`. R:207
  and R:419: `explain(conn, posting_id)` for flagged fields and both quotes.
- A has no route that reads `hidden_list` or `explain`; A:270 shows only `hiddenRule` per posting.

Fix: A Task 21 adds the ranking hides to `NoGoView` (kind, reason, provisional), and A Task 7 adds `flags` per posting
from `explain` (field, both values, both quotes). W adds the two lists to the No-go view and the company page.

### I18. One refusing posting hides a company whose other posting says it sponsors

- K:59: any final refusal writes `no_go`; a company with a second visible posting that says `sponsorship = 'yes'` is
  hidden too. D:298 to D:301 and the hide rule: uncertain is shown and flagged.

Fix: K Task 3 `decide_hide` writes no `no_go` and returns no hide when any visible posting at the company has
`sponsorship = 'yes'` or `opt_only`; it adds a flag "postings disagree on sponsorship" and K Task 10 raises a
`flagged` item for it.

### I19. An undone LinkedIn request still counts as a request

- A:431: undoing `request_sent` keeps the touch and sets `quiet_at`.
- P:163: `load_outbound` reads every `outreach_touch` row (only log-only rows check for an undo); P:189 counts them
  against the month and P Task 5 makes the company `waiting`. The outcome report counts a "quiet" request that was
  never sent.

Fix: F Task 1 adds `outreach_touch.undone_at` (nullable; outcomes refused when set) and `log_touch` callers skip
undone rows; A Task 16 sets it instead of `quiet_at`; P Task 3 and A Task 9 filter `undone_at IS NULL`.

---

## Minor

- **M1. `stored_decision` is renamed in three plans and named wrong once.** F:111 says `_stored_decision`; the code's
  name is `_stored` (`outreach/rules/rebuild.py:80`). R:125 and K:100 also do the rename (K ruling 5, K:23, already
  defers). Fix: F Task 1 names `_stored`; R Task 2 drops the rename.
- **M2. `facts.py` is widened by #30 and by #31.** F:115 to F:117 and S:95, S:101 to S:111 (S ruling 1 moves the widening
  but S Task 1 still adds value checks). Fix: F Task 1 owns the tuples and the value checks for the new facts; S Task 1
  keeps only `test_vocab_matches_schema`.
- **M3. Schema text left behind after the rulings.** C:121 tests a hash CHECK #30 lacks (see B1); P:20 (carried-in
  ruling 4) and P:82, P:104 to P:126 still describe a migration against P ruling 1 (P:24); A:70, A:98, A:184 to A:215
  (Task 4 migration and its `linkedin_note` test), A:325 (consumes `linkedin_note`), A:586 (#13 writes
  `linkedin_note`). Fix: each plan marks those steps "moved to #30 Task 1" and points the tests at #30's tables.
- **M4. `company_source` rows for `careers` have no `source` row.** S:529 writes `company_source` with source `careers`;
  only `stapply` is registered (S:509); foreign keys are on (`outreach/store/db.py:78`). Fix: S Task 15 calls
  `register_source(conn, 'careers', 'careers-1', run_id)`.
- **M5. Two `size_check` items for one shared domain.** S:382 (`evidence_key 'shared-domain:<domain>'`) and K:61, K:390
  (weak flag "domain in `shared_key`", key the joined flags). Fix: K Task 5 drops the shared-domain weak flag for size
  checks (keep it for the score).
- **M6. Skip semantics.** A:354 records a `row_skipped` event and leaves the entry state; P:560 expects `backlog`; A:587
  says #15 reads `row_skipped`, P never does (P:354 carries by state). Fix: A Task 11 also sets the entry `backlog` with
  `decided_at`.
- **M7. Stale cross-references in the crawler and reader.** C:366 reads `outreach.reader.version.current_reader_version()`
  (R:98 names `reader_version`); R:401 and R:416 still say the crawler calls `enqueue` (against R ruling 0, R:441).
  Fix: C drops the reader version lookup (ruling 2 made it unused); R edits its hand-off rows.
- **M8. C ruling 6 has no task.** C:27 to C:28 (re-fetch a detail body on a changed title or date; lane 1 monthly) is
  not in C Tasks 9 or 11. Fix: C Task 11 phase 6 adds both conditions to `missing_body_ids`.
- **M9. Health cadences.** A:256 lists `prepare` and `rank` as unscheduled; P:566 schedules prepare weekdays 05:30 and
  P ruling 7 (P:35) runs rank inside `plan`. Fix: A Task 6 adds `prepare` weekdays 05:30 ET; `rank` stays unscheduled.
- **M10. The search index leaves most companies unfindable.** A:78 caps the index at about 3,000 of 18,000. Fix: A Task 7
  adds `GET /api/search/companies?q=` on the server for names and domains outside the index.
- **M11. Commit trailers differ.** F:29 uses `Claude Opus 5.5 (1M context)`; S:45, R:26, K:38, P:49, A:53 use
  `Claude Sonnet 5.5`. Fix: every plan says "the trailer the executing session is given", as C:49 and W:47 do.
- **M12. Email by pattern is accepted without proof.** D:355 to D:356 needs a pattern "already proven at that
  company"; P:112 and P:250 accept `email_basis 'pattern'` alone. Fix: P Task 6 accepts `pattern` only when another
  person at the company has a `published` or `verified` address of the same shape.
- **M13. HN replies and other touches have no logging path.** D:381 to D:383; A:352 and A:431 cover only LinkedIn and
  applications. Fix: A Task 12 adds `POST /api/touches` for `hn_reply` and `other` through `log_touch`.
- **M14. Internal contradictions a builder could trip on.** R:182 asserts a title quote is refused for `sponsorship`,
  but R ruling 3 (R:450) allows title and location for every field; W:694 to W:695 (open questions 7 and 8) contradict
  W rulings 3 and 4 (W:19 to W:23). Fix: edit the test and the two answers to match the rulings.
- **M15. Score signal order.** A:271 returns signals "in stored order", but K:337 stores `values` with sorted keys, so the
  order is alphabetical. Fix: A Task 7 orders by `abs(points)` descending, ties by `build_signals` order.
- **M16. Crawler dependency line.** C:13 says boards come from the #17 part A import; #31 creates them first. Fix: C:13
  names #31.

---

## Checked and consistent

These pairs were checked and need no change: job names (F:142 against S:42, C:349, C:384, R:275, K:416, P:381,
P:484, A:256); review kinds and `raised_by` values (F:144 against S:305, S:382, R:205, K:390, C:384); the lanes
(`level_role`, `intern_junior`, `reason_no_role`, `any_us_role`) across K:55, P:136, A:417 and W:173; the `company_score`
JSON shape (K:65) as read by R:128, P:355 and A:271; `merge_candidate` ids (`company_id` and `int(evidence_key)`,
S:305) as read by A:403 and A:514; `log_touch` and `record_outcome` signatures (F:255 to F:260) as called by P:164,
A:352 and A:372; the reader relaxations (R:54) against the `decision` CHECKs in `0001_initial.sql`; `read_queue` being
written only by #32 (F:38, C ruling 2, R ruling 0).

---

## Resolution (2026-10-21)

Plan names: foundation is #30, sources #31, crawler #21, reader #32, ranking #6, people plan #15, API #11, web #33.
Decisions that overrode a "smallest fix" above are recorded in each plan's "Controller rulings" section.

### Blocking

- B1: fixed in #30: one `body_sha256(text)` in `outreach/store/writes.py` (whitespace collapsed, ends stripped, case kept), CHECKs on the three hash columns; #21 and #32 import it and define none.
- B2: fixed in #30 and #31: `work_tier` is the only table (with `source_version` added to #30's definition); #31 uses `work_tier` and `set_at` everywhere.
- B3: fixed in #15 and #11: the `why` keys are listed once in #15's Hand-off (snake_case, with `reason_sources` added); #11's `parse_plan_why` reads exactly those and maps them to its camelCase fields.
- B4: fixed in #21 and #32: the crawler's decisions, undecided heal and ruleset rebuild all pass the reader's combiner by lazy import; #32's night job runs `apply_all` after its batches; #30's `rules` stage does rebuild plus `apply_all`.
- B5: fixed in #30, #11 and #15: `context` keys are pinned in #30; `build_context` writes `source` (`plan` or `own`); #15's quota counts `source = 'plan'` and returns both limits.
- B6: fixed in #11, #15 and #33: notes save into `linkedin_draft` through #15's `save_linkedin_draft` and `check_note`, `needsYourLine` is `reason_state == 'proposed'`, a confirm route is added, a Claude save never overwrites a draft he saved, and `NoteDraft` gains `reasonLine` and `reasonState`.
- B7: fixed in #30, #31, #21 and #6: `board.found_by` and `ReadResult.board_name` added; #31 sets `found_by`; #21 raises the doubtful item (with `board_id`) before writing postings on a `stapply_name` board's first read; ranking treats an unread `stapply_name` board as doubtful.

### Important

- I1: fixed in #30, #21 and #31: one `outreach/net.py` and `tests/fakes.py` in #30, #21 keeps only `PoliteClient`, #31 drops its copy; one User-Agent with no contact address.
- I2: fixed in #30, #31 and #21: one token grammar and `valid_token`; Workday `site` and Oracle `siteNumber` keep their case; no `%` in a slug.
- I3: fixed in #31: `FyStats.median_jr_wage`, written into the `lca` evidence, with pinned fixture wages and tests.
- I4: fixed in #31: `HnSource.posts()` and `run_discover` write `company_post` with `asks_email` (address or phrase list in `hn.toml`).
- I5: fixed in #30 (and every stream plan): registration convention `tests/pipeline/stages_<stream>.py` with `register(harness)`, #30 owns the `rules` stage, invariants count only a stage's own rows (#6's global count narrowed to fixture companies).
- I6: fixed in #11: Task 24 adds `tests/test_api_flow.py` that runs the harness through `plan` and checks the API reads what the producers wrote.
- I7: fixed in #15, #11 and #33: `linkedin_capacity` and `plan_capacity` are #15's, #11 calls them and drops its `[plan]` config, `linkedinMonth` gains `hardUsed` and `hardLimit`.
- I8: fixed in #11: the add-person route calls #15's `add_candidate(added_by='devanshu')` and maps its refusal codes.
- I9: fixed in #15 and #11: #15 lands Tasks 2 to 7 as PR 1; #11's Tasks 8, 10 and 11 are marked "after #15 PR 1".
- I10: fixed in #11 and #33: #33 owns view-only types, #11 owns API-driven types as additive edits, `schema.ts` resolves overlaps, #11's Task 24 runs after #33 merges.
- I11: fixed in #32 and #15: `classify_run(result, *, session_limit_patterns, auth_patterns)`, #15 calls it with its own lists.
- I12: fixed in #6 and #11: `weights_changed` is written with `actor 'system'` and a pinned detail (versions, changed weights, `text`); #11 reads `text` and the versions.
- I13: fixed in #31 (and #21 note): companies whose only boards have no adapter get a watched page from the careers check or an iCIMS host; #21 reads them like any other.
- I14: fixed in #21: new Task 26a `probe_slugs` (Greenhouse, Lever, Ashby, `ok` reads only, `found_by 'slug_probe'`).
- I15: fixed in #11: Task 20 adds `POST /api/companies/{id}/unmerge`, and the merge event records the previous status.
- I16: fixed in #11: findings also group by channel and by each signal bucket, same 20 per group minimum.
- I17: fixed in #11 and #33: the No-go view gains `hiddenCompanies` from `hidden_list`, company postings gain `flags` from `explain`; #33 ruling 12 renders both.
- I18: fixed in #6: a refusal hides nothing when another open posting reads `yes` or `opt_only`; flag "postings disagree on sponsorship" and a `flagged` Needs-you item (`sync_conflict_items`).
- I19: fixed in #30, #15 and #11: `outreach_touch.undone_at` and `undo_touch`; #15's outbound read and #11's counts filter it; #11's LinkedIn undo uses it.

### Minor

- M1: fixed in #30: names `_stored` and makes it public as `stored_decision`; #32 and #6 drop their renames.
- M2: fixed in #30: owns the `FACTS` tuples and `EvidenceDraft` value checks; #31 keeps only the drift test.
- M3: fixed in #21, #31, #15 and #11: leftover migration and `linkedin_note` text is marked moved to #30 Task 1 or rewritten to the shared tables.
- M4: fixed in #31: Task 15 registers the `careers` source before writing `company_source`.
- M5: fixed in #6: the shared-domain weak flag no longer raises a ranker `size_check` (still lowers `small`).
- M6: fixed in #11: skip also sets the entry to `backlog` with `decided_at`; the plan row state reads it.
- M7: fixed in #21 and #32: the crawler drops the reader version lookup; #32's hand-off rows say the crawler never enqueues.
- M8: deferred: needs a body fetch timestamp column and a `posted_on` compare in `plan_diff`, so it is not a one-line change; #21 ruling 6 stays as written for the builder to settle with #30.
- M9: fixed in #11: `prepare` weekdays 05:30 ET is scheduled in the health cadences; `rank` stays unscheduled.
- M10: fixed in #11: Task 7 adds `GET /api/search/companies?q=`.
- M11: fixed in #30, #31, #32, #6, #15 and #11: every plan says "the trailer the executing session is given".
- M12: fixed in #15: a `pattern` email counts only when another person at the company has a published or verified address of the same shape.
- M13: fixed in #11: Task 12 adds `POST /api/touches` for `hn_reply` and `other`.
- M14: fixed in #32 and #33: the title and location quote test follows #32 ruling 3; #33's answers 7 and 8 follow its rulings 3 and 4.
- M15: fixed in #11: score signals are ordered by `abs(points)` descending, ties by `build_signals` order.
- M16: fixed in #21: the dependency line names #31.

### Left for Devanshu

- The shared User-Agent carries no contact address. SEC's access policy asks for one on `sec.gov` hosts; if SEC refuses the shared agent, that is a decision for him (#31 ruling 8).
- M8 above (monthly lane 1 body re-fetch and the changed-date re-fetch on detail-request ATSs).

Controller, 2026-10-21: M8 resolved. #30 Task 1 adds `posting_body.fetched_at`; #21 re-fetches a detail body when the list shows a changed title or date, and monthly for lane 1 postings, using it.
