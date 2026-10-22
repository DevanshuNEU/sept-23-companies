# Integration review of plans wave 2

Date: 2026-10-22. Review only: no plan, issue or code was changed. Each finding names both sides with file and line,
and the smallest fix.

Abbreviations (plans in `docs/superpowers/plans/` of the plans-wave-2 worktree unless noted):

| Key | File | Issue |
|---|---|---|
| SE | `2026-10-22-sending-engine.md` | #14 |
| IS | `2026-10-22-import-and-switch-over.md` | #17 (and the runbook) |
| RJ | `2026-10-22-retire-job-radar.md` | #5 |
| NY | `2026-10-22-needs-you.md` | #16 |
| MCP | `2026-10-22-mcp-server.md` | #13 |
| F, S, C, R, K, P, A, W | the 2026-10-21 plans: foundation #30, sources #31, crawler #21, reader #32, ranking #6, people plan #15, API #11, web #33 | |
| IR1 | `2026-10-21-integration-review.md` | first review |
| SQL | `outreach/store/migrations/0001_initial.sql` on master c84f526 | |
| ID | `outreach/identity.py` on the #31 branch (`DevanshuNEU/sources-and-identity`, head 9e94713) | |
| LEDGER | `.superpowers/sdd/2026-10-21-sources-and-identity/progress.md` on the #31 branch | |

Counts: Blocking 9, Important 17, Minor 13.

---

## 1. Blocking

### B1. Migration 0002 is claimed twice

- SE:35, SE:76, SE:121 (`0002_sending.sql`, test `test_migration_is_version_2_and_reinstall_changes_nothing`).
- IS:44, IS:87, IS:134 (`0002_import_history.sql`, test "migrate to 2"), IS:831 (S6 check "store at version 2").
- Also: F:9 to F:11 say that after #30 merges every schema change is a new numbered migration, but LEDGER (Task 7
  ruling) edited `company_name_key` into 0001 in place. Harmless before the switch (no live store), but it is a second
  rule.

Fix: #14 is 0002, #17 is 0003 (IS Part A needs SE Task 2 on master, IS:27 to IS:31, so #14 lands its migration
first). IS Task 1: file `0003_import_history.sql`, test "migrate a version 2 store to 3"; IS S6 check "store at
version 3 (or the latest)". The controller records one line: "#31's `company_name_key` is the last in-place edit of
0001; from now on numbered migrations only, renumbered on rebase."

### B2. A planned follow-up can never be skipped: the message CHECK refuses it

- SQL:451 `CHECK (state = 'planned' OR (length(trim(subject)) > 0 AND length(trim(body)) > 0))`; SQL:862 allows the
  transition `planned>skipped`, but the row CHECK still fails for an empty planned row.
- SE:383 `plan_followups` inserts planned rows with "Empty subject and body". Then SE:385 (`lapse_followups`,
  planned to skipped), SE:362 (a human reply skips the replier's `planned` messages), SE:387 (`sweep_stopped` skips
  `planned` messages) all raise `IntegrityError` inside the tick's one transaction (SE:427), so one reply or one lapse
  makes every tick fail from then on: no reads recorded, no sends.
- IS:457 to IS:459 (importer plans follow-ups for imported sent contacts) hits the same wall at the first lapse.

Fix (pick one; the reviewer recommends A):
- A. No schema change: a planned row that will never be drafted is deleted (it holds no text and nothing references
  `message_id`), with an event `followup_lapsed` or `skipped_by_sender` on the touch carrying `{seq, due_on, reason}`.
  SE Tasks 10 and 11 change "planned to skipped" to "delete planned with an event"; their tests assert the row is gone
  and the event exists. `skipped -> drafted` revival of a lapsed follow-up is dropped (a re-plan is a new planned
  row).
- B. Schema: SE's 0002 rebuilds `message` with `CHECK (state IN ('planned','skipped') OR (...))`, recreating its 5
  indexes and 10 triggers, with a test that the trigger and index names in `sqlite_master` are unchanged.

### B3. Three names for one helper: raising a review item

- MCP:56, MCP:104, MCP:274, MCP:280: creates `outreach/store/review.py::raise_review_item` "only if absent", event
  actor = `raised_by`.
- NY:105, NY:170: creates `outreach/review/items.py::raise_item`, which appends its `raised` event with actor
  `system` and reopens a `cleared` row with an UPDATE.
- IS:563: consumes `outreach.store.review.raise_review_item` "when #13 or #16 has merged it, else one INSERT".
- MCP:204: the MCP guard's temp triggers allow an `event` insert only with actor `claude` or `reader`, and a
  `review_item` update only to `resolution = 'cleared'`. So if #13 adopts #16's `raise_item`, `flag_review` is refused
  by #13's own guard (actor `system`; and a reopen is an UPDATE to `open`).
- MCP:449 also asks #21 and #6 to import it; both already insert inline (C ruling 8, K Task 10), which IR1 left as is.

Fix: #16 owns the one helper, `outreach.review.raise_item(conn, *, kind, company_id, reason, evidence_key,
raised_by, actor='system', reopen=True, board_id=None, posting_id=None, person_id=None, now=None)`. MCP Task 7 drops
`outreach/store/review.py` and calls it with `actor='claude', reopen=False` (a cleared Claude flag on the same reason
stays cleared; a new reason is a new key). IS Task 11 step 7 calls it with `raised_by='import', actor='import'`
and drops the inline fallback. #16 ships Tasks 1 to 12 as its first PR (no dependency), before #13 Task 7 and #17
Task 11. #21 and #6 may keep their inline inserts (same index, same semantics).

### B4. Claude's `mark_no_go` takes effect at once, against a #9 ruling

- #9, Decisions 2026-10-19: "Claude's `mark_no_go` (#13) raises a Needs-you item that Devanshu confirms with one key;
  it never takes effect on its own."
- MCP:62, MCP:285: `mark_no_go` inserts a person `no_go` row (`source 'claude'`) directly; MCP:281
  `add_person_no_go`; MCP Q4 (MCP:476) asks a question #9 already answered.
- A person `no_go` blocks approve and the sender's pre-claim check (SE:65, A Task 19), so a misread reply by Claude
  stops email to that person with no click from him.

Fix: MCP `mark_no_go` keeps its input schema (person, reason, quote or note) but writes one review item through
`raise_item(kind='flagged', raised_by='claude', actor='claude', person_id=..., evidence_key='claude-no-go:<person
id>:<reason>', reason="Claude suggests no-go for <name>: <reason>. <quote>")`. Drop `outreach/store/no_go.py`. NY Task
8: the `no_go` fix on an item that has a `person_id` writes a person-level `no_go` (`source 'devanshu'`, reason from
the item or his choice) instead of a company one; one new test. MCP Q4 is marked answered by #9.

### B5. The deploy tool is used before the PR that carries it merges

- IS:24: Part B branch `DevanshuNEU/switch-over` holds Tasks 17 to 19; IS:784: "It merges on switch day, step S5".
- IS:823 (S3 check `uv run python -m outreach.deploy status`), RJ:130 (`outreach.deploy stop com.devanshu.jobradar`
  at S3): both need Tasks 17 and 18 on master before S5.
- RJ:18: #5 branches "after #17 Tasks 17 and 18 have merged", which cannot happen if they merge at S5.

Fix: Part B is two PRs. PR B1 (Tasks 17 and 18, "Part of #17") merges before the rehearsal; #5 follows it. PR B2
(Task 19, CLAUDE.md, docs and the cold-email skill rewrite of B9, docs only) is the one that merges at S5. IS:24 and
IS:784 say so.

### B6. `raise_merge_candidate` with an unknown basis raises

- IS:561 and IS:909: `raise_merge_candidate(conn, a, b, 'import_near_name', detail)`.
- ID:268 to ID:276 and ID:279: an unknown basis raises `ValueError("no confidence known for basis ...; pass
  confidence=")`; the vocabulary is fixed (`_ITEM_CONFIDENCE`, ID:49).

Fix: IS Task 11 step 7 passes `confidence='weak'` (a near name is never a strong match). No #31 change needed. Add a
test `test_near_name_item_has_weak_confidence_and_basis_import_near_name`.

### B7. Importing `RULED_OUT` as `removed` hides reachable companies on old guesses

- IS:450: "`RULED_OUT` makes the company `removed` with its `why`." IS:170 fixture has one simple entry.
- The real `dashboard/data.js` (read only, live checkout): `RULED_OUT` has 17 entries covering about 30 companies.
  Seven carry several names in one entry ("Browserbase / Braintrust / Arize / Positron / Fluidstack", "Datadog /
  Grafana / Netlify", "RunPod / Hebbia / Rogo", "Replicate / Anyscale / Voltage Park"); Pinecone, Braintrust and
  Fluidstack are also `COMPANIES` rows. The reasons are research judgments ("Sponsorship stopped", "Zero non-senior
  software roles", "Zero H-1B filings ever", "Four-day in-office mandate"), not #9 evidence (a posting that refuses,
  citizenship, size over 300 with evidence).
- #9 2026-10-19: "A removed company is never proposed again automatically." #9 2026-10-21 Lanes: "no role yet but
  money raised", "intern or junior work" count. So `removed` hides them for good, and a slash name either becomes one
  bogus company or fails to link.

Fix: IS Task 10: `RULED_OUT` entries split on ` / ` into one record part per name; each part links like any other
name; the company stays visible with the `why` as a company note plus one review item (`flagged`, `raised_by
'import'`, key `import:ruled_out:<n>:<name_key>`, reason "The old dashboard ruled this out: <why>. Remove it if that
still holds."). No `removed` on import. Test `test_ruled_out_is_a_note_and_an_item_not_a_removal` and
`test_ruled_out_slash_names_split`. Question Q4.

### B8. New companies stop at the switch: nobody schedules #31's jobs

- IS:878 to IS:879 and IS:936 to IS:938: the old refresh's discovery stops at S12; "no plan schedules #31's
  `discover`, `resolve` and `find_boards` jobs".
- S:92 and S:561 (CLI only), A:626 item 9 (`discover`, `resolve`, `find_boards` unscheduled), and #9's 2026-10-18
  ruling "#21: its job keeps running the existing discovery scripts", replaced on 2026-10-21 by "Discovery: source
  modules" (design table row "Discovery: the existing scripts in #21 -> Source modules").
- LEDGER, last notes: "Task 16 adds a launchd template (com.devanshu.outreach-sources, {{LIVE_CHECKOUT}} and
  {{LOG_PATH}} placeholders only, installed by #17's deploy tool) - pending Devanshu's answer".

Fix: #31 Task 16 ships `launchd/com.devanshu.outreach-sources.plist.template` and a `python -m outreach.sources tick`
that runs each source whose `schedule` is due (Form D daily, HN monthly, LCA when a new quarter file is in the cache
folder), then `resolve` and `find_boards`, one `job_run` per job. IS Task 17 adds it to `NEW_JOBS` as required (the
switch must not retire discovery without a replacement). A Task 6 gives `discover`, `resolve`, `find_boards` their
cadences. Question Q3.

### B9. After the switch the cold-email skill writes drafts into the retired mailer

- `.claude/skills/cold-email/SKILL.md` steps 2, 6 and 7: role from `dashboard/data.js`, `python3 mail/check_draft.py`,
  `python3 mail/add.py`; follow-ups through `mail/followup.py`.
- MCP:448 assigns the rewrite to #17; IS has no task for it (IS:880 says the MCP config is "its plan's"). Nobody owns
  it. On Monday after the switch, every draft Claude writes lands in `mail/contacts.json`, which no sender reads: a
  silent loss, against "error is not empty".

Fix: MCP adds Task 16 (text only, no code): the skill's steps become `company` / `person` reads, `save_draft` and
`save_followup` with the returned blocks and warns, and the follow-up section uses `followups_due`. The edit ships in
PR B2 (B5), merged at S5, so the skill flips with the system. The skill keeps #9's Drafts rules unchanged.

---

## 2. Important

### I1. Follow-up count: three in #13, one in #9 and #14

- #9 2026-10-21: "Follow-ups: 1, at about 7 business days, adding something new (replaces '3, at 3, 7 and 15').
  Configurable."
- SE:62, SE:136 (`followup_business_days` default `(7,)`); IS:457 (one per entry of #14's config).
- MCP:59 and MCP:447 ("The sender (#14) creates the three `planned` rows"), MCP:79 (`seq` 1 to 3), MCP:326.

Fix: MCP:59, MCP:326 and MCP:447 read "the sender creates one `planned` row per entry of `followup_business_days`
(one by default)". `seq` may stay 1 to 3 in the schema (the config allows up to 3); `no_such_followup` covers the rest.

### I2. Job cadences and the health view

- A:267 (Task 6): `sender` every 600 s; SE:7 and SE:458: every 120 s.
- NY:77: the Needs-you sweep writes no `job_run` (SQL:719 has no kind for it) and appends its event only when something
  changed (NY:326), so a dead sweep job is invisible in health; IS:855 (S14) checks the health view.
- B8: `discover`, `resolve`, `find_boards` unscheduled.

Fix: A Task 6 sets `sender` to 120 s and adds the sources cadence once #31 Task 16 lands. For the sweep, the smallest
change without a `job_run` rebuild: NY Task 7 writes one heartbeat event per run (entity `review_item`, kind
`sweep_run`, idempotency key `sweep:<YYYY-MM-DDTHH>:<half hour>`) whether or not anything changed, and A Task 6 shows
"Needs-you sweep: last run" from it (the NY invariant then compares `review_item` rows only, plus at most one
`sweep_run` event). Alternative: a `needs_you` job kind in a numbered migration rebuilding `job_run` (heavier, since
`import_record` references it).

### I3. Send schedule reasons: the web types still carry `recruiter_day_2`

- `web/src/api/types.ts:99`: `reason: 'next_slot' | 'per_company_limit' | 'spacing' | 'recruiter_day_2' | 'window' |
  'daily_limit'`; A:91 `[sending] window`, `ramp` in `outreach/api/config.toml`.
- SE:283 (the `REASONS` list), SE:578 (cross-plan: build `SendingStatus` and `Schedule` from `outreach.sending.status`,
  drop `[sending]`).

Fix: A Task 18 reads `sending_snapshot` and `queue_schedule` (SE Task 16) and maps `ready` to `next_slot`; `types.ts`
reason union becomes SE's reasons minus `ready` plus `next_slot`; `recruiter_day_2` goes. A drops `[sending]`. #33
fixture data follows. A Task 18 lands after SE Task 16.

### I4. "Release hold" has no owner

- SE:63 (the sender never clears a hold) and SE:581: "until the action exists a hold lasts until he closes the company
  or tags the reply `auto_reply`". No plan adds the action.
- Effect: one "Not now" reply holds every colleague's approved email at that company with no way out short of closing
  the company.

Fix: A Task 19 adds `POST /api/companies/{id}/release-hold {revision}` (clears `held_by_touch`, `held_at`, appends
`company`/`hold_released`, actor `devanshu`), and W adds the button on the held Outbox group. Small, but it must exist
before the switch.

### I5. Launchd templates: three conventions, and the crawler installs itself

- SE:458: `{{LIVE_CHECKOUT}}/.venv/bin/python -m outreach.sending run`, placeholders `{{LIVE_CHECKOUT}}`,
  `{{LOG_PATH}}`.
- C:115, C:386: `launchd/com.devanshu.outreach-crawl.plist` (no `.template`), hard-coded `/Users/devanshu/Desktop/Sept
  23 Companies` and `/opt/homebrew/bin/python3`; C:573 (Deploy step 4) copies it into `~/Library/LaunchAgents` and
  bootstraps it, while IS:726 and IS:845 install it again at S11.
- A:567 and NY:328: `uv run --no-sync python -m ...`, placeholders not pinned. launchd's PATH has no `uv`
  (`/usr/bin:/bin:/usr/sbin:/sbin`), so these jobs fail at start unless the path is absolute.
- IS:736 to IS:738: `render` refuses any placeholder other than the two.

Fix: one convention, stated in IS Task 17 and applied in A Task 23, NY Task 7, C Task 13, #31 Task 16: file
`launchd/<label>.plist.template`, `WorkingDirectory {{LIVE_CHECKOUT}}`, program `{{LIVE_CHECKOUT}}/.venv/bin/python -m
<module> <command>`, logs `{{LOG_PATH}}`, no other placeholder, no store or Gmail environment variable. C renames its
file and drops Deploy step 4 (S11 installs it; before the switch there is no live store, C Deploy step 2). Each
template has a test that renders it with IS's `render` when that module imports.

### I6. Claude's `domain` at `estimate` becomes a strong identity key

- MCP:283: `record_research` accepts `domain` with `guess` or `estimate`.
- ID:7 and ID:225: identity matches on `domain` evidence with `confidence <> 'guess'`, as a strong key (the first hit
  is the company).
- Effect: a wrong domain from a web page attaches later candidates of the real owner to this company, so that company
  may never exist on its own: a hide on a guess. Also the LEDGER ruling "hosting suffixes are platform domains" is not
  applied by MCP or NY.

Fix: MCP Task 7: `domain` only as `guess`, and refused when `outreach.sources.keys.classify_domain` is not `company`
(platform or hosting host). NY Task 8 `normalize_domain` also refuses a non-`company` class with its own reason.

### I7. #13 transport and registration differ from #9 and #13's own text

- #9 Build: "Claude connection: local stdio MCP server with the official `mcp` Python package." Issue #13: "registered
  at project scope in `.mcp.json`".
- MCP:44 (stdlib loop), MCP:64 and MCP:474 (local scope). MCP:467 says #9 must be edited first if the default wins.

Fix: ask (Q2, Q10). If he accepts the defaults, #9's Build line is edited before #13's PR merges, as CLAUDE.md asks.

### I8. CLAUDE.md and `.claude/settings.json` are edited by three plans

- SE:97, SE:445, SE:459 (deny entries for `outreach.sending run` and `auth`, and an "Only in the live checkout" line).
- MCP:110, MCP:427 to MCP:428 (registration lines, `Bash(sqlite3:*)` and Read, Edit, Write on
  `~/Library/Application Support/outreach/**`).
- IS:109, IS:778 (Live system table rewrite, at S5).

All need Devanshu's review (flagged in section 3). Fix: each edit is its own small commit in its PR, shown as a separate
diff; IS Task 19 rebases last and keeps the SE and MCP lines. The MCP store-folder deny also covers the switch backup
folder (IS:824), which is fine.

### I9. `detect_unsupported` is consumed but not produced

- NY:440 and NY:594 consume `detect_ats`, `detect_unsupported` from `outreach/sources/ats_detect.py`.
- S:416 produces only `detect_ats(text) -> list[BoardRef]`; unsupported hosts live in `CareersResult.unsupported`
  (S:538).

Fix: S Task 10 exports `detect_unsupported(text) -> list[str]` (the unsupported ATS hosts found), or NY Task 11 uses
`detect_ats` alone and says "not a board we can read" for every other host. Reviewer prefers the S export (one place
knows the host list).

### I10. `build_context(lane=None)` is needed by two plans; #11 types it `str`

- A:313: `lane: str`. SE:306 and IS:546 pass `None` (the context CHECK allows JSON null, SQL `$.lane IS NOT NULL`
  only tests presence).

Fix: A Task 9 `lane: str | None`, test `test_context_lane_may_be_null`.

### I11. The banned-list loader has no pinned name

- A:460, A:471: `run_checks(..., banned, now)`; the loader is unnamed. SE:403 and MCP:305 consume "its loader".

Fix: A Task 17 exports `load_banned(path: Path | None = None) -> tuple[list[tuple[str, re.Pattern]], bool]` (the bool
is "list readable"); SE and MCP use it.

### I12. Two merge and unmerge implementations

- A Task 20 (inline SQL merge, unmerge "keeps the alias with the survivor"); NY:398 to NY:405 and NY:587
  (`merge_companies`, `unmerge_company`, "no alias moves").

Fix: the controller assigns NY as owner; A Task 20's handlers call NY's functions when NY PR 1 has merged (NY Task 13
is the reconcile). Note for NY Task 9: identity now matches names through `company_name_key` (LEDGER ruling), and
`resolve_candidate` follows `merged_into`, so a merge needs no name-key move; one test proves a candidate with the
loser's name resolves to the keeper.

### I13. Pace and ceiling: 100 a day is out of reach from one time zone

- #9 Sending: "+10 each clean sending day up to 100"; "Window: weekdays, 08:30 to 16:30 recipient local time".
- SE:60 (4 to 9 minutes between sends, one per 2-minute tick), SE:606 Q9: "about 70 to 100 a day fit".

Fix: ask (Q9). If he wants 100 reachable, `gap_minutes` becomes (3, 6). Not a code conflict.

### I14. Recruiters inside or outside the per-company limit

- #9 original: "Outside the per-company limit, inside the daily limit, emailed on day 2"; 2026-10-21: "only when no
  engineer, manager or founder is found; ranked last (replaces 'in scope on day 2')".
- SE:61 counts recruiters inside the per-company limit and makes them wait for colleagues.

The 2026-10-21 line replaced the whole recruiter rule in the design table ("Recruiters in scope on day 2" row), and
P:617 already offers a recruiter only when nobody else is on record. SE's rule is a safe fallback. Marked answered
(section 7, A3).

### I15. #15's capacity reads the start limit, not today's lowered one

- P:199 `current_send_limit`: today's `send_day.send_limit`; SE:59: `send_limit` is the start and is never rewritten;
  the effective limit is one level lower after a warning. SE:584 swaps in `limit_for` (also a start limit).

Fix: SE Task 6 also exports `effective_limit(conn, d, cfg) -> int` (read only); P Task 4's seam calls it.

### I16. `followups_due` and `due_followups` are two copies of one query

- SE:384 `due_followups(conn, today)`; MCP:75 `followups_due`; MCP:19 forbids `outreach/mcp` importing
  `outreach.sending`.

Fix: move `due_followups` to a stdlib module both may import (`outreach/drafts/followups.py`); SE Task 11 and MCP
Task 6 use it; one test of the query lives there.

### I17. Imported follow-ups due in the past

- IS:457 to IS:459, IS:497 (sent Friday 2026-10-16 gives `due_on` 2026-10-27, before the switch).
- SE:385: lapses 5 business days after `due_on`; MCP `followups_due` would ask Claude to draft a stale follow-up at
  once.

Fix: IS Task 10 plans a follow-up only when `due_on` is on or after the import date; otherwise none, with a count line
"follow-ups not planned: due before the switch". With B2 fixed either way works; this keeps the first Monday calm.

---

## 3. Safety

No plan step sends mail, approves, calls 127.0.0.1:3002, prints Gmail secrets, or touches `dashboard/data.js` or
`dashboard/index.html` (IS reads `data.js` through a Node sandbox, read only, IS:246). No stress or load test (SE:491
caps the simulator file at 30 s; MCP:25, NY:21, IS:46). Every live command refuses outside the live checkout: SE:451
(`run`, `--dry-run`, no bypass, test `test_no_bypass_exists`), SE:196 (real Gmail client before the token file opens),
SE:250 (macOS notice), IS:758 to IS:765 (`install`, `retire-old`, `rollback`), RJ:27, NY:327 (sweep through
`connect()`), MCP:230 (`check_store` first, stdout empty on refusal).

Flagged (need his review or a small change):

| # | Where | What | Fix |
|---|---|---|---|
| S1 | SE:445, SE:459 | Edits `.claude/settings.json` (deny rules) and `CLAUDE.md` | His review in the #14 PR, separate commit |
| S2 | MCP:422, MCP:427 to MCP:428 | Edits `CLAUDE.md` and `.claude/settings.json` (`Bash(sqlite3:*)`, store-folder Read, Edit, Write denies); adds a dependency group to `pyproject.toml` | His review (Q12); separate diff as the plan says |
| S3 | IS:778 | Rewrites the CLAUDE.md Live system table at the switch | His review in PR B2 |
| S4 | IS:816 | Fallback runs `python3 mail/sender.py --dry-run` (his step, live checkout). It reads Gmail and saves contacts, and settles an unclear send only 10 minutes after the attempt (`mail/sender.py` `NOT_SENT_AFTER_MIN`) | Say "wait 10 minutes after the last send attempt, then run it once"; it is his click, keep |
| S5 | IS:826 | `sqlite3 radar.db ".backup ..."` opens the archive read-write (may create `-wal`/`-shm`), against RJ:23 "radar.db is never written" | Use `sqlite3 -readonly ~/Desktop/DevVault/job-search/pipeline/radar.db ".backup '<folder>/radar.db'"` |
| S6 | IS:664 to IS:671 | `rehearse` defaults to his real files and runs anywhere (scratch store), so an agent in a worktree could copy his full history into `~/Desktop/outreach-scratch` | `rehearse` with the default manifest calls `require_live("the rehearsal on real data")`; a fixture manifest runs anywhere |
| S7 | SE:491 Step 4 | Builder runs `python -m outreach.sending simulate` in a worktree | Safe: temp store, no Gmail client, never reads `OUTREACH_DB` (SE:455, test `test_simulate_never_opens_outreach_db`). Keep |
| S8 | MCP:434 | The real side-session run: scratch store in `~/Desktop/outreach-scratch/mcp-run/`, API on the workspace port, local-scope registration from the worktree, removed after | Safe. Keep; the live registration stays in the runbook |
| S9 | `~/Library/LaunchAgents/com.devanshu.outreach-mail-sender.plist` stays on disk between S1 and S12 | A login or restart that day would load the old sender again | Already fails safe: SE:67 refuses to claim while the old label is loaded. Add to S1: "do not log out or restart until S12" |
| S10 | IS:807 to IS:808 (R1 step 5) | Staging API against the rehearsal store "on a workspace port, never 3001" | Also never 3002, 3011, 3013, 5173 or 3100; say "CONDUCTOR_PORT + 1" as A's Q13 |

Hard-coded paths in templates (I5) are not a safety issue but would run the job against the live checkout from any
copy; the placeholder convention removes that.

---

## 4. #9 conformance

| Rule | #9 text (current) | Plan text | Verdict |
|---|---|---|---|
| Ramp | "20 a day to start; +10 each clean sending day up to 100" | SE:136 `ramp_start 20`, `ramp_step 10`, `ramp_ceiling 100`; SE:59 | Matches |
| Clean day | "bounces under 2% over the last 3 days, no Gmail limit or block errors, no spam or 'stop' replies" | SE:248 `bounce_rate_3d`: "the last `bounce_days` New York dates that had at least one send"; SE:261 exactly 2% is a warning | Matches in substance; "last 3 days" read as last 3 sending days. Minor M1 |
| Warning and hard block | "Any warning steps down one level; a hard block pauses sending and tells him" | SE:58 (warnings: 2% bounce, policy rejection, one stop tag; hard: 429 or daily limit, account 403, over 3% of last 100, two stop tags in 7 days) | Matches; the split of kinds is new (Q1) |
| Stop conditions (#14) | "Gmail 429 or limit message, policy rejections, bounce rate over 3%, spam or stop replies, said-no list" | SE:58, SE:488 | Matches |
| Window | "weekdays, 08:30 to 16:30 recipient local time, random spacing" | SE:136, SE:144 (both ends inclusive) | Matches |
| Per company | "at most 3 a day per company, at least 60 minutes apart" | SE:60, SE:286 | Matches |
| Recruiters | 2026-10-21: "only when no engineer, manager or founder is found; ranked last" | SE:61; P:617 | Matches (I14) |
| Follow-ups | 2026-10-21: "1, at about 7 business days ... Configurable"; original: "in the same thread", "Any reply from anyone at the company stops all queued email there" | SE:62, SE:136 `(7,)`, SE:305 threading, SE:362 hold; IS:457 | SE and IS match. MCP:59, MCP:447 say three: conflict (I1) |
| Content | "plain text; 2 links in the first email ...; no tracking pixel; no attachments; each subject unique per person" | SE:194 (`build_mime` plain text, one recipient, no attachments); checks are A's | Matches |
| Approve checks | "safety blocks ... style warns" | SE:65, SE:403 (re-run before claim; warns never stop) | Matches |
| One sender | "devanshurajesh@gmail.com via the Gmail API. No second account" | SE:136 `from_address`, SE:424 wrong account sends nothing | Matches |
| Claude connection | "local stdio MCP server with the official `mcp` Python package. Claude can read, add people, save drafts and flag; only Devanshu approves; only the sender sends" | MCP:44 stdlib loop; MCP:45 adds research, reader and finding tools | Transport conflicts (I7, Q2); tool scope wider than "read, add people, save drafts and flag" (Q11) |
| Claude no-go | 2026-10-19: "`mark_no_go` raises a Needs-you item ... never takes effect on its own" | MCP:62, MCP:285 write a person no-go | Conflict (B4) |
| Needs-you list | "Board unreadable 3 runs in a row; zero jobs only after 2 runs in a row; unclear sponsorship wording; unknown size for a picked company; missing email domain" | NY:36 to NY:42 | Matches; "picked" reinterpreted for a plan of people (Q8) |
| Hidden | "size over 300 with evidence ... Size guesses stay visible and are flagged"; 2026-10-21 "unchanged, with evidence only" | MCP:61 (Claude size only as guess); NY:26; IS:42 to IS:43 | Matches, except IS:450 `RULED_OUT` (B7) |
| Old-dashboard rulings | 2026-10-19: 233 "well over 500" are size evidence and stay hidden; 41 cleared and 1 agency go to no-go; 9 big by filings and 1 SEC go to Needs you | IS:446 to IS:449 (`over_500` estimate `num_low 501`; `defence_contractor`, `agency` no-go; `big_by_filings`, `sec_filings` items) | Matches |
| Export button | Build: "#17 part A adds a read-only 'Export tracking' button to the old dashboard" | IS:45, IS:888: uses the existing "Download a backup of your tracker" | Deviates; the button exists (`dashboard/app.js:546`, export at `:712`). Edit #9 (A1 in section 7) |
| Switch | "Job Radar retires at the switch after its history is imported"; #5: "keep radar.db untouched as a read-only archive" | RJ:5, RJ:23, RJ:161 (not `chmod`) | Matches in substance (Q16) |
| Discovery | 2026-10-21: "Discovery: source modules ..." (replaces the 2026-10-18 "#21's job keeps running the existing discovery scripts") | IS:878 retires old discovery; nothing schedules the new | Gap (B8) |
| Daily cadence | 2026-10-19 "Today proposes up to 5 companies a day, rising to 10 once the daily send limit reaches 60", superseded by 2026-10-21 "Today: a plan of people" | P Task 4 reads the ramp; SE:584 | Matches the newer line |
| Undo window | 2026-10-19: "commit 8 seconds after the click so Z can undo"; "'Said no' and 'Asked to stop' commit immediately" | NY:83 (8 s window, then reopen) | Matches |

---

## 5. Pipeline harness: final `STREAM_STAGES`

Current (master `tests/pipeline/harness.py:35`): sources, crawl, reader, rules, ranking, plan. Wave 2 adds four
streams (IS:683, MCP:406, SE:517, NY:453). Final combined order:

```python
STREAM_STAGES = {
    "importer": ("import_history",),                   # #17, first: history before discovery (IS Task 15)
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

Notes:
- MCP and SE both say "after `plan`"; the order between them is `mcp` then `sending` (draft, approve, send).
- The harness docstring (harness.py:6) says streams edit nothing else in `tests/pipeline/`; all four plans must edit
  the dict and `test_streams_and_their_stages_are_the_agreed_map`. To avoid four rebase conflicts, the first wave 2 PR
  to merge adds all four entries and the docstring rows at once (the harness skips a missing `stages_<stream>`
  module), and the others add only their stage file and invariant.
- Invariants stay scoped to their own rows: SE:521 ("no other message in the store changed state" is fine because the
  MCP stage's message is `drafted` and the sender sends only `approved`); NY:459 checks the contract and a second sweep
  only.

---

## 6. "Never hide a reachable company"

| Step | Risk | Verdict |
|---|---|---|
| IS:450 `RULED_OUT` to `removed` | Hides about 30 companies on research guesses, permanently | Blocking (B7) |
| MCP:283 Claude `domain` at `estimate` | A wrong domain becomes a strong identity key; the real owner's candidates attach elsewhere | Important (I6) |
| IS:485 to IS:486 done set includes interviews that ended in rejection | Closed `interview`, never proposed again | His call (Q5) |
| IS:475, IS:500 frozen radar boards close their postings | Postings closed, company still shown; Workday and Oracle frozen only with size evidence over 300 | Acceptable (Q15) |
| IS:446 `visaDenied` as hand `sponsorship no` plus an item | Ranking hides only on posting refusals (K:61), so the company stays visible | Acceptable (Q6) |
| IS:447 cleared and agency to `no_go`; `over_500` to size evidence | Hides, but ruled by Devanshu on 2026-10-19 | Conforms |
| MCP:285 Claude person no-go | Stops email to a person on Claude's reading | Blocking as a #9 conflict (B4) |
| MCP:61, MCP:289 Claude size only as `guess` | Never hides | Conforms |
| MCP Task 12 `reader_submit` | A side-session reading can make a rules-only refusal final, then ranking writes `no_go` (K:61) | By design (#9 "Reading postings"); every fact needs a checked quote. Q11 |
| NY:82 no-go, NY:356 hand size | Only his click hides | Conforms |
| NY:323 `clear_gone`, NY:324 `clear_stale` | Clear items, never hide; `clear_stale` skips when no recent plan exists | Conforms |
| NY:441 `edit_board` closes the old board's postings `hand` | His click; the company keeps the new board | Conforms |
| SE:63, SE:387 skip queued mail at a closed, removed or no-go company | Messages only; the company is not hidden | Conforms |
| SE:63 company hold with no release | Not a hide, but email at that company stops for good | Important (I4) |
| IS:42 nothing hidden on import, held records block the run | Conforms | |

---

## 7. Minor

- **M1.** SE:248 reads "the last 3 days" as the last 3 sending days. Fine; add the sentence to #9's Sending line when
  #14 merges so the record says it.
- **M2.** SE:121 and IS:134 tests name exact versions; after renumbering (B1) use `latest_version()` in assertions
  where the number does not matter.
- **M3.** SE:307 calls `record_outcome(kind='replied', ...)` without `revision`; the merged signature requires it
  (`outreach/store/writes.py:229`). Read the touch's revision first; say so in SE Task 10.
- **M4.** IS:538: `add_board` always writes its event with actor `crawler` (S:417); imported boards then read
  "found by crawler" in history. Optional `actor=` on `add_board` (S Task 10).
- **M5.** NY:441: `edit_board` must check board ownership before `add_board`, because `add_board` raises a
  `merge_candidate` on conflict (S:417), which NY:441 says it does not.
- **M6.** IS:727: `com.devanshu.outreach-crawl` is listed as required; with I5 its template name changes.
- **M7.** RJ:68: the radar scan covers `outreach/`, `web/src/`, `launchd/`, `.conductor/`; `outreach/deploy/__main__.py`
  will name `com.devanshu.jobradar` (RJ:102); add it to `RADAR_ALLOWED`.
- **M8.** NY:328 runs the sweep every 30 minutes; IS:725 lists it as not required. With B8 and I2 it should be
  required (triggers 3 to 5 have no other producer).
- **M9.** IS:913: `ImportSource` in `types.ts` gains three sources; #33 owns view types, A owns API types (IR1 I10);
  name A Task 21 as the owner.
- **M10.** SE:580 adds a `followup_subject` block to A's `run_checks`; MCP Task 9 derives `Re: <first subject>`, so
  the two agree. Name the block in A Task 17's code list.
- **M11.** IS:563 writes import issues as kind `flagged`; NY's `flag_family` (NY:141) puts them under "Other". Fine;
  optional prefix "Import:" so the Needs-you view can group them.
- **M12.** MCP:445: `--strict-mcp-config` on #32's night job and #15's prepare run is a change to those plans (R Task
  6, P Task 14); record it there.
- **M13.** IS:848 says "If #13 has merged, register the MCP server"; with B9 the switch needs #13 merged, so S11
  should say "Register the MCP server" without the condition.

---

## 8. Proposed migration numbering

| Version | File | Owner | Content |
|---|---|---|---|
| 1 | `0001_initial.sql` | #30, plus #31's in-place `company_name_key` (LEDGER) | Frozen after #31 merges |
| 2 | `0002_sending.sql` | #14 | `sender` cursor, gap, sign-in columns; `send_day.closed_at`; `inbound_message.dsn_class` (ALTER ADD COLUMN only). If B2 takes option B, the `message` rebuild goes here too |
| 3 | `0003_import_history.sql` | #17 | `import_record` rebuild: three new sources, outcome `held` |

#16, #13 and #5 have no migration (NY:23, MCP:464, RJ none). Any later change takes the next number on rebase.

---

## 9. Deploy and merge order

Build and merge (each line waits for the ones above it unless it says otherwise):

1. #31 sources and identity (in flight), including Task 16 `com.devanshu.outreach-sources` template (B8) and the
   `detect_unsupported` export (I9).
2. #14 PR 1 (Tasks 1 to 7: migration 0002, config, calendar, fake and real Gmail, classify, ramp, queue). No other
   dependency; #17 Part A needs its Task 2.
3. #16 PR 1 (Tasks 1 to 12: the review package and `raise_item`, the sweep and its template). No other dependency;
   Task 11 waits for #31 Task 10; Task 12's trigger 1 and 2 tests run once #21 Task 7 is on master.
4. Wave 1 builds in their own order: #21, #32, #6, #15 PR 1 and PR 2, #11 (Tasks 9 and 17 early, since #14, #13 and
   #17 wait on them), #33.
5. #13 (Tasks 1 to 7 any time after step 3; 8 and 9 after #11 Task 17; 10 and 11 after #15 PR 1; 12 after #32; 13
   after #15 PR 2).
6. #14 PR 2 (Tasks 8 to 18) after #11 Tasks 9 and 17.
7. #16 PR 2 (Tasks 13 to 17) after #11 Task 20, #33 Tasks 2 and 6, and his mockup approval (NY Task 14).
8. #11 Task 18 rebuilt on #14 Task 16 (I3), Task 19 release hold (I4).
9. #17 Part A (Tasks 1 to 16, migration 0003) after #31 Tasks 7, 8, 10, #11 Task 9, #14 Task 2, #16 PR 1.
10. #17 PR B1 (Tasks 17 and 18, deploy tool), then #5.
11. R1 rehearsal (Devanshu, Friday before).
12. Switch day (Sunday): IS runbook S1 to S14, with PR B2 (Task 19, CLAUDE.md, the cold-email skill rewrite of B9)
    merged at S5. Order of live jobs: old sender out (S1), old server, refresh and Job Radar out (S3, RJ step 1),
    store installed (S6), import (S8, RJ step 2), web built (S10), new jobs in (S11: api, sender, crawl, sources,
    reader-night, prepare, plan, needs-you; MCP registration), old jobs retired (S12, RJ step 3, Job Radar last), his
    approvals (S13), Monday check (S14).

---

## 10. Questions only Devanshu can answer

Most important first. Each has the default the plans use and why it matters.

1. **Warning or hard block (#14 Q1).** Default: a Gmail 429 or daily limit, an account-level refusal, over 3% of the
   last 100 first emails bounced (once 10 are sent), or two "Asked to stop" tags in 7 days pause sending and tell you;
   one policy bounce, one "Asked to stop" or a 3-day bounce rate of 2% or more steps down one level. Why: it decides
   whether one bad day costs a level or stops all sending.
2. **Official `mcp` package or a standard-library server (#13 Q1).** Default: the standard-library loop, proven
   against the official client in a test-only group; #9's Build line is edited to match. Why: #9 says the official
   package; the package brings many dependencies into a process that can write the store.
3. **Schedule for finding new companies after the switch (#31 ledger, #17 Q2).** Default: a launchd job
   `com.devanshu.outreach-sources` that runs each source when due (Form D daily, HN monthly, LCA when a new quarter
   file is downloaded), then resolve and board finding; installed at the switch. Why: without it, no new company
   arrives after the old refresh retires.
4. **The old dashboard's 17 "ruled out" entries (about 30 companies, new).** Default: import them visible, with the old
   reason as a note and one Needs-you item each; you remove the ones that still hold. Why: as planned they would be
   hidden for good on research guesses such as "sponsorship stopped" or "no non-senior roles".
5. **Done set (#17 Q1).** Default: every company with an interview or later, including ones that ended in rejection,
   closed as done; you can take any out in the overrides file during the rehearsal. Why: a done company is never
   proposed again.
6. **Old "no sponsorship" verdicts, 22 companies (#17 Q3).** Default: recorded as your locked evidence plus one
   Needs-you item each, shown until you confirm a no-go. Why: they are not posting evidence, so #9 would not hide them
   on its own.
7. **Gmail scope (#14 Q3).** Default: keep send and metadata (no new sign-in); the app cannot show reply text or tell
   a spam-policy bounce from a bad address. Alternative: add read-only and sign in again at the switch. Why: it limits
   what the policy-rejection rule can see.
8. **What "a company he picked" means now (#16 Q1).** Default: a company you are working, or one on the latest plan
   (within 4 days) as picked or with a named person; up to about 60 size items a day at first. Why: too narrow and
   unknown sizes go unasked; too wide and Needs you floods.
9. **Pace (#14 Q9).** Default: a tick every 2 minutes and 4 to 9 minutes between sends, so about 70 to 100 a day fit
   and 100 needs recipients in several time zones. Alternative: 3 to 6 minutes. Why: #9's ceiling of 100 is otherwise
   out of reach.
10. **MCP registration (#13 Q2).** Default: local scope, registered once in the live checkout; the command is in
    CLAUDE.md. Alternative: a checked-in `.mcp.json` (issue #13), which every worktree would offer and then refuse.
    Why: decides where the server appears.
11. **What Claude may do through MCP beyond #9's "read, add people, save drafts and flag" (#13, #13 Q7).** Default:
    also record research as guesses, read postings for the reader queue (same quote checks, the night job's version
    label), and find people on public pages. Why: a posting reading can make a refusal final.
12. **Deny rules for the side session (#13 Q6, #14 Task 14).** Default: deny `sqlite3` in Bash and Read, Edit, Write
    on the store folder; deny agent runs of `outreach.sending run` and `auth`. Both are `.claude/settings.json`
    changes for your review. Why: they narrow routes around the server and the sender.
13. **A pasted address with no page (#13 Q3).** Default: #15's `add_candidate` gains `email_given_in`: an address you
    pasted is stored when it appears in your pasted text. Why: today a pasted address is refused.
14. **"Routed" and "LinkedIn referral" replies (#17 Q4).** Default: an interested reply plus a referral. Why: referrals
    are the main win in Insights.
15. **Hand sponsorship "Yes" cancels a posting refusal hide (#16 Q4).** Default: yes, a #6 change. Why: without it
    your fix only silences the item.
16. **radar.db on disk (#5 Q2).** Default: not made read-only; a stored hash catches any change and rollback keeps
    working. Why: #5 says "read-only archive".
17. **Automatic retry of a proven failed send (#14 Q4).** Default: one retry when Sent proves it did not go out,
    except a bad request.
18. **Workday and Oracle radar boards (#17 Q6).** Default: frozen only with size evidence over 300; Amazon, Apple,
    Google and Eightfold always frozen. Why: a small company on Workday keeps being read.
19. **After a hard block (#14 Q6).** Default: resume one level below the day it stopped.
20. **Closing a quiet email thread (#14 Q10).** Default: closed "no reply" 7 business days after the last email.
21. **Hand edits after the 8 second window (#16 Q2).** Default: not retractable; ok, no-go and merge are.
22. **Keep "Edit board" (#16 Q3).** Default: keep.
23. **Sweep cadence (#16 Q6).** Default: every 30 minutes, after the morning plan, and on pick or add-person.
24. **Warn before saving a size over 300 (#16 Q5).** Default: no warning.
25. **Old email touches, April to September (#17 Q7).** Default: closed "no reply", no follow-ups.
26. **Retired note in DevVault's RADAR.md (#5 Q1).** Default: you add it by hand.

### Already answered: do not ask

| Question | Answer | Source |
|---|---|---|
| A1. Export tracking button (#17 Q8, design question 1) | The existing "Download a backup of your tracker" button exports `outreach.v1`; only #9's Build line needs editing to say so | `dashboard/app.js:546`, `:712` |
| A2. Can Claude mark a company no-go (#13 Q4) | No; and a person no-go too is a Needs-you item he confirms with one key | #9, Decisions 2026-10-19 |
| A3. Recruiters (#14 Q2) | Only when no engineer, manager or founder is found, ranked last; no day 2 | #9, Changes 2026-10-21; P:617 |
| A4. Claude's size findings (#13 Q5) | Guesses stay visible and are flagged; only evidence hides | #9 Hidden; 2026-10-21 "Hidden: unchanged, with evidence only" |
| A5. Old approvals (#17 Q5) | Imported as drafts he approves again, because only he approves and the approve checks must run | #9 Drafts and "only Devanshu approves" |
| A6. Holidays (#14 Q7) | Weekdays only; a holiday list stays an optional config line | #9 Sending "Window: weekdays" |
| A7. Small days still ramp (#14 Q8) | Any weekday with sends and no warning counts, as written | #9 "+10 each clean sending day" |
| A8. Follow-up drafting (#14 Q5) | Claude drafts, he approves, the sender sends in the thread; one follow-up at about 7 business days | #9 Drafts; Changes 2026-10-21 |
| A9. Follow-up subject and hook date set by code (#13 Q8) | A builder decision within #9 ("same thread", hook at most 30 days old) | #9 Follow-ups and Drafts |
| A10. Old "well over 500", cleared, agency, big-by-filings verdicts | Size evidence and hidden; no-go; Needs you | #9, Decisions 2026-10-19 |
| A11. Which discovery runs after the switch | Source modules, not the old scripts (who schedules them is Q3) | #9, Changes 2026-10-21 "Discovery" |
