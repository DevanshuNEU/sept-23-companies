# Sending engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new sender in `outreach/sending/` that sends approved emails from the new store inside #9's rules (adaptive ramp, per-company spacing, recipient window, one follow-up, holds and stop rules), never sends twice, does nothing outside the live checkout, and is proven by a simulated two weeks with a fake Gmail and a fake clock.

**Architecture:** One tick (launchd, every 2 minutes) does: prove the token, settle unclear sends against Sent by Message-ID, read new mail through the Gmail history cursor, apply replies, bounces and signals to the store, open the day's ramp row, then send at most one message. Gmail is reached only through a `MailClient` protocol: the real client (ported from `mail/gmail.py`) refuses outside the live checkout, and every test and the simulator use `FakeGmail`. Gmail is never called inside a store transaction; a send is claimed with its Message-ID in a committed, fully synced transaction before the Gmail call. The old mailer (`mail/`) is not changed; the two never run at once.

**Tech Stack:** Python 3.14 (Homebrew), standard library only (`sqlite3`, `zoneinfo`, `tomllib`, `urllib`, `email`, `fcntl`, `random`), SQLite STRICT store from #4 and #30, `unittest`.

**Spec:** issue #14 and its "Done when"; issue #9 (Sending, the 2026-10-19 decisions and "Changes, 2026-10-21", which win over #14's text); `docs/superpowers/specs/2026-10-18-outreach-platform-design.md` section 8; `docs/superpowers/specs/2026-10-20-opportunity-pipeline-design.md` sections 3, 10, 11, 13; `docs/research/2026-10-18-cold-email-deliverability.md` (Gmail facts and stop signals only); `docs/superpowers/plans/2026-10-19-store-and-rules.md` ("The mail state machine, old to new", "For downstream issues", the #14 paragraph and the Sent-folder note); `docs/superpowers/plans/2026-10-21-pipeline-foundation.md` (`outreach_touch`, `log_touch`, `record_outcome`, the harness); `docs/superpowers/plans/2026-10-21-api.md` (Hand-off row for #14, Tasks 9, 17, 18); `docs/superpowers/plans/2026-10-21-daily-people-plan.md` (Task 4 `current_send_limit`, Hand-off row for #14).

**Branch and PRs:** issue #14, two PRs (merge order of the integration review, section 9). **PR 1** (`DevanshuNEU/sending-engine`, "Part of #14"): Tasks 1 to 7, which need only master as it is (#30 merged); it merges early because #17 Part A needs Tasks 1 and 2 and #13 Task 6 needs Task 7's `due_followups`. **PR 2** (`DevanshuNEU/sending-engine-2`, "Closes #14"): Tasks 8 to 18. Task 8 needs #11's `outreach/outcomes/context.py` (#11 Task 9) and Task 12 needs #11's `outreach/drafts/checks.py` with `load_banned` (#11 Task 17) on master; branch PR 2 from master once they land.

## The #9 numbers this plan uses

Quoted from #9 (the 2026-10-21 changes win where they replace an older line):

- "One sender: devanshurajesh@gmail.com via the Gmail API. No second account or domain."
- "Adaptive ramp: 20 a day to start; +10 each clean sending day up to 100. Clean = bounces under 2% over the last 3 days, no Gmail limit or block errors, no spam or 'stop' replies. Any warning steps down one level; a hard block pauses sending and tells him."
- "Window: weekdays, 08:30 to 16:30 recipient local time, random spacing."
- "People per company: ... at most 3 a day per company, at least 60 minutes apart."
- "Follow-ups: 1, at about 7 business days, adding something new (replaces '3, at 3, 7 and 15'). Configurable." And from the original line, still standing: "in the same thread", "Any reply from anyone at the company stops all queued email there."
- "Recruiters: only when no engineer, manager or founder is found; ranked last (replaces 'in scope on day 2')."
- "Content: plain text; 2 links in the first email ...; no tracking pixel; no attachments; each subject unique per person."
- 2026-10-19: "'Asked to stop' makes the day not clean." "Reply tags 'Said no' and 'Asked to stop' commit immediately."
- From #14 (stop conditions not overridden by #9): "Gmail 429 or limit message, policy rejections, bounce rate over 3%, spam or stop replies, said-no list."

## Global Constraints

- Claude never approves or sends an email. Approval is Devanshu's click; sending is this sender's job, run by launchd from the live checkout. No code, test or plan step calls the mail API on 127.0.0.1:3002 (`/api/approve`, `/api/save`, `/api/skip`, `/api/unapprove`, `/api/pause`) or the new API's write routes, by any means. Nothing in `outreach/sending` imports `outreach.api`.
- No step sends real mail or calls Gmail. Every test and the simulator use `outreach.sending.fakegmail.FakeGmail` and a fake clock. The real client is constructed only by `python -m outreach.sending run`, `whoami` and `auth`, and refuses outside the live checkout.
- Never open `~/Library/Application Support/outreach/outreach.db` outside the live checkout; tests use `tests/storetest.fresh_store()`, a scratch `OUTREACH_DB`, `OUTREACH_MAIL_HOME` and `OUTREACH_GMAIL_DIR` (`tests/__init__.py`). Gmail secrets (`client_secret.json`, `token.json`) are never printed, logged or put in an event.
- Standard library only in `outreach/sending`. The old mailer (`mail/sender.py`, `mail/gmail.py`, `mail/server.py`, `mail/store.py`) is not edited (#14: "The current mailer is not changed"). `tests/test_mail_sender.py` and `tests/test_live_guards.py` stay green.
- One schema change: **`outreach/store/migrations/0002_sending.sql`** (version 2), owned by this plan (Task 1). #17's `0003_import_history.sql` is version 3 and lands after it. `0001_initial.sql` is frozen: #31's in-place `company_name_key` edit (with its strong column) is the last in-place edit of 0001; every later schema change is a numbered migration, renumbered on rebase.
- The sender opens its connection with `connect(fullfsync=True)`. No connection holds a transaction across a Gmail call; every write is one `db.transaction` (`BEGIN IMMEDIATE`). Writers use `INSERT ... ON CONFLICT DO NOTHING`, never `INSERT OR IGNORE` or `INSERT OR REPLACE`. Timestamps are canonical UTC `%Y-%m-%dT%H:%M:%SZ`; dates are `date(x)`. The sender's day (`send_day.day`, daily counts) is the `America/New_York` date.
- `send_unknown -> failed -> approved` is the only path to a second send attempt, and only after a Sent scan by Message-ID that reaches back past the claim time finds nothing (store plan rule). Reconcile matches by Message-ID only, never by To plus Subject.
- Every read of a company follows `company.merged_into` to its end; holds, pauses, status and per-company counts use the surviving company.
- Error is not empty: a Gmail read that fails is logged and retried next tick, never stored as "no replies"; a sign-in failure is a visible state (`sender.auth_state`), never silence.
- No em dashes or en dashes in code, comments, config, log lines, docs or commit messages. Check: `python3 -c "import sys; [print(p) for p in sys.argv[1:] if any(c in open(p,encoding='utf-8').read() for c in (chr(0x2013), chr(0x2014)))]" <files>`.
- Tests are light: no test sleeps on the real clock, opens a socket to anything but 127.0.0.1, or runs more than a few thousand ticks. The simulator jumps the fake clock between events.
- Tests: `uv run python -m unittest discover -s tests -t .`. Test first; every bug fix starts with a test that reproduces it. Commit steps name their paths (never `git add -A`). Commit trailer: the one the executing session is given.

## Review Focus

1. **Gmail took the email but the record of it was lost** (a crash between the Gmail call and the commit, a timeout after Gmail accepted, a Sent folder with many newer messages, the Mac sleeping mid-send). Expected: exactly one email reaches the person; the message ends `sent` with the Message-ID it was claimed with. Tests: Task 8 `test_crash_after_gmail_accepted_is_recorded_not_resent`, Task 9 `test_not_found_needs_a_scan_back_to_the_claim`, `test_timeout_after_accept_is_found_in_sent`.
2. **A reply while colleagues are queued, at a merged duplicate, or on a closed touch.** Expected: every queued email at the surviving company waits (held), the replier's own planned follow-up is deleted with a touch event (never moved to `skipped`, which the `message` CHECK refuses for an empty row), a late reply on a closed touch still holds the company. Tests: Task 10 `test_reply_holds_every_queued_message_at_the_company`, `test_reply_at_a_merged_away_company_holds_the_survivor`, `test_late_reply_on_a_closed_touch_holds_and_records_the_outcome`.
3. **Calendar edges:** the week of 2026-11-01 (daylight saving ends), a Pacific recipient while New York is past 16:30, a Friday send whose follow-up lands on a Monday, the Mac asleep for two weekdays, a weekend. Expected: windows in the recipient's time, business-day follow-ups, no double ramp after sleep, no ramp change on days with no sends. Tests: Task 2 `test_window_uses_recipient_time_across_the_dst_change`, Task 6 `test_sleeping_mac_does_not_ramp_twice`, Task 11 `test_friday_send_follow_up_lands_seven_business_days_later`.
4. **The wrong token:** a token for another Google account, an expired or revoked token, a token copied into a worktree. Expected: nothing sent, a visible `signed_out` state, one notice a day. Tests: Task 4 `test_real_client_refuses_outside_the_live_checkout`, Task 13 `test_wrong_account_sends_nothing`, `test_revoked_token_is_signed_out_not_a_hard_block`.
5. **Inbound noise:** a Gmail "Delay" notice, an out-of-office, a bounce that is not in the thread, his own manual reply inside the thread, a reply from a colleague's address. Expected: only a real bounce counts as one, only a person counts as a reply, his own mail is ignored. Tests: Task 5 `test_delay_notice_is_not_a_bounce`, `test_unthreaded_dsn_matches_by_failed_recipient`, `test_his_own_message_in_the_thread_is_ignored`, `test_reply_from_another_address_is_a_human_reply`.

---

## Decisions this plan makes (#9 and #14 leave them open)

Each is a default; the ones only Devanshu can settle are listed under "Pending Devanshu" with their question number from the integration review (section 10).

- **Warning or hard block (Pending Devanshu Q1).** Warning kinds (the day is not clean; today's limit drops one level at once; the next day starts from that lower level): `bounce_warning` (a bounce brings the 3-day bounce rate to 2% or more), `policy_rejection` (a recipient server refused the mail as unsolicited or by policy, when the bounce text shows it), `stop_reply` (a touch tagged `asked_to_stop`). Hard-block kinds (sender paused with `pause_kind 'hard_block'`, a notice to him, today's limit one level down, the next day starts there once he resumes): `gmail_limit` (HTTP 429, or 403 with a rate or daily limit reason), `gmail_policy` (403 that says the account may not send), `hard_bounce_rate` (more than 3% of the last 100 first emails bounced, once at least 10 are sent: the old mailer's live `pauseIfBounceRate 0.03`, `minSendsForBounceCheck 10`), `stop_replies_hard` (two `asked_to_stop` tags within 7 days). A sign-in failure is neither: no sends, state `signed_out`.
- **Ramp model.** `send_day.send_limit` is the day's starting limit and is never rewritten. The effective limit today is the start limit, or one level lower (floor 20) when today has a warning or hard-block signal (`ramp.effective_limit`, Task 6, the number #15's plan reads). The next sending day's start: after a `clean` day, `min(prev + 10, 100)`; after `warning` or `hard_block`, `max(prev - 10, 20)`; after a day with no sends, the same. A day is closed (`send_day.closed_at`) on the first tick of a later New York date; only then is its outcome frozen. A signal belongs to the New York date it happened on, or to today if that day is already closed. The first day ever starts at 20. A sending day is a weekday with at least one send (#9 says nothing about how many).
- **Per company and spacing (pace: Pending Devanshu Q9).** At most 3 sends (first emails and follow-ups together) per surviving company per New York date, at least 60 minutes apart. Between any two sends a random gap of 4 to 9 minutes (the research's pacing range) is drawn once and stored in `sender.next_send_at`. One send per tick at most.
- **Recruiters (answered by #9, 2026-10-21).** #9: "only when no engineer, manager or founder is found; ranked last"; there is no day-2 rule. #15 already offers a recruiter only when nobody else is on record, so this rule is the sender's safe fallback: a recruiter counts inside the per-company limit like anyone, and a recruiter's message waits while any non-recruiter message at the same company is approved and sendable.
- **Follow-ups (answered by #9: one, at about 7 business days; Claude drafts, he approves, the sender sends in the thread).** When a first email is sent, the same transaction inserts one `planned` follow-up per entry of `followup_business_days` (default `[7]`, at most 3 entries because `message.seq` is 0 to 3), `due_on` that many business days after the New York send date. The sender never writes recipient-facing text: Claude drafts the follow-up when it is due (#13 `save_followup`, the cold-email skill), Devanshu approves it, and the sender sends it in the thread (`threadId`, `In-Reply-To` the latest sent message's Message-ID, `References` every earlier one) on or after `due_on`. A `planned` row holds no text, and the `message` CHECK refuses an empty subject or body in every state but `planned`, so a planned row that will never be drafted is **deleted, never moved to `skipped`** (nothing references `message_id`, and the delete trigger guards only claimed and sent rows). A follow-up still `planned` 5 business days after `due_on` lapses: the row is deleted and the touch gets event `followup_lapsed` with `{seq, due_on, reason}`. A lapsed follow-up is not revived; a re-plan is a new `planned` row. An open touch with nothing queued closes `no_reply` 7 business days after its last sent message (Pending Devanshu Q20).
- **Holds and stops.** A human reply on any touch holds the surviving company (`company.held_by_touch`, `held_at`; never `paused_at`, which is his) and stops the replier's own queued messages. A held or paused company's messages stay `approved` and wait. A bounce stops that touch's queued messages. A closed or removed (not merged) company, an active `no_go` on the person or the company, or a touch tagged `said_no` or `asked_to_stop` stops the queued messages (touch closed `company_closed` or `no_go` where it is still open). "Stops" always means: a `planned` row is deleted with a `skipped_by_sender` event on the touch (`{seq, due_on, reason}`); a `drafted`, `approved` or `failed` row moves to `skipped` with a `skipped_by_sender` event on the message. The sender never clears a hold; Devanshu releases one with #11 Task 19's release-hold action (Cross-plan effects).
- **Failed sends (Pending Devanshu Q17).** A send that ends `failed` (proven not in Sent) is moved back to `approved` by the sender once, so at most 2 claims per message (#11 left `failed -> approved` to the sender). A failure Gmail gave as 400 (bad request: a bad address or header) is not retried and stays `failed` for him to see.
- **Pre-claim check.** Right before claiming, the sender re-runs #11's `run_checks` on current data; any `block` moves the message `approved -> drafted` with the fresh checks stored and an event `send_blocked`, so it returns to his review. Its own send-time rules (pause, hold, no-go, window, limits) run first.
- **Inbound reading.** The Gmail history cursor (`sender.history_id`, from `users.getProfile`) finds new messages; a message in a tracked thread (any touch with `gmail_thread_id`) is read with format `metadata`; an INBOX message in another thread is read only to catch a bounce that Gmail did not thread (matched by `X-Failed-Recipients` or an exact tracked subject). An empty or expired cursor (HTTP 404) falls back to reading every tracked thread of a touch sent in the last 45 days once, then takes the profile's history id. Scopes stay `gmail.send` and `gmail.metadata` (Pending Devanshu Q7), so reply and bounce text is not readable; the classifier uses headers and, when Gmail returns one, the snippet.
- **The switch.** The new sender reads only the new store and the old one only `mail/contacts.json`; they never both run. The new sender refuses to claim while the old launchd job `com.devanshu.outreach-mail-sender` is loaded (state `old_sender_running`). #17 part B stops the old sender, imports `contacts.json`, then loads the new job from this plan's template. The sender does not backfill follow-ups for imported touches; #17 decides those. Its first runs check each imported sent message against Gmail by its Gmail id (Task 10 `verify_imported`) and report any that Gmail does not have.
- **Dry run** (`run --dry-run`) does every read (store and Gmail), applies every write inside one transaction that is rolled back, never calls `send`, and prints what it would do. It writes nothing at all, `job_run` included. Like `run`, it refuses outside the live checkout.
- **Heartbeat.** Every tick writes one `job_run` (`job 'sender'`) with integer counts in `summary` and `"noop": 1` when nothing changed. Once a New York day, the sender deletes its own `ok` no-op runs older than 14 days.
- **Event kinds** this plan writes (all `actor 'sender'`): entity `message`: `claimed`, `sent`, `send_unknown`, `reconciled_sent`, `claim_failed` (detail: `pending_rfc_id`, `error`, `error_kind`, `retryable`, `attempt`), `requeued`, `send_blocked`, `skipped_by_sender` (detail: reason), `followup_planned`; entity `touch`: `replied`, `auto_reply`, `bounced`, `closed`, `followup_lapsed` and `skipped_by_sender` (both for a deleted `planned` row, detail `{seq, due_on, reason}`); entity `company`: `held`; entity `sender`: the signal kinds above, `hard_block`, `signed_out`, `wrong_account`, `notified`.

## File Structure

| File | Responsibility |
|---|---|
| `outreach/store/migrations/0002_sending.sql` | Create: sender cursor, gap, auth state; `send_day.closed_at`; `inbound_message.dsn_class` |
| `outreach/sending/__init__.py` | Create: package marker |
| `outreach/sending/config.toml`, `outreach/sending/config.py` | Create: every number in this plan, `SendConfig`, `load_config` |
| `outreach/sending/calendar.py` | Create: New York dates, business days, recipient windows |
| `outreach/sending/mail.py` | Create: `MailClient` protocol, value types, `GmailError`, `classify_http` |
| `outreach/sending/gmail.py` | Create: the real client (port of `mail/gmail.py`), MIME builder, `auth` |
| `outreach/sending/fakegmail.py` | Create: `FakeGmail`, `FakeClock` (tests and the simulator) |
| `outreach/sending/classify.py` | Create: inbound message kind and bounce class |
| `outreach/sending/signals.py`, `outreach/sending/ramp.py` | Create: day signals, bounce rates, open and close days, limits, hard block |
| `outreach/sending/queue.py` | Create: who may be sent now, why not, and in what order |
| `outreach/drafts/__init__.py` (if absent), `outreach/drafts/followups.py` | Create: `due_followups`, the one query of due follow-ups that this plan and #13 both import (Task 7) |
| `outreach/sending/claim.py` | Create: claim, record sent or unclear, reconcile, requeue |
| `outreach/sending/inbound.py` | Create: history sync and applying replies, auto-replies and bounces |
| `outreach/sending/followups.py` | Create: plan, lapse, close, sweep stopped messages |
| `outreach/sending/precheck.py` | Create: the pre-claim `run_checks` gate |
| `outreach/sending/tick.py`, `outreach/sending/notify.py` | Create: `run_tick` and the macOS notice |
| `outreach/sending/status.py` | Create: the snapshot and queue schedule #11 and #15 read |
| `outreach/sending/simulate.py`, `outreach/sending/__main__.py` | Create: the two-week simulator; `run`, `status`, `whoami`, `auth`, `simulate` |
| `launchd/com.devanshu.outreach-sender.plist.template` | Create: every 120 s from the live checkout (installed by #17, not here) |
| `tests/sendtest.py`, `tests/test_sending_*.py`, `tests/test_drafts_followups.py` | Create: seed helpers and one test file per module |
| `tests/pipeline/stages_sending.py`, `tests/pipeline/harness.py`, `tests/pipeline/test_flow.py` | Create the `send` stage; `STREAM_STAGES` holds the final wave 2 map (Task 17); one invariant |
| `tests/mutation_check.py` | Modify: sending mutations |
| `.claude/settings.json`, `CLAUDE.md` | Modify: deny agent runs of the sender; add it to "Only in the live checkout" (its own commit, for his review) |
| `docs/README.md` | Modify: a row for this plan |

---

## Task 1: Migration 0002 for the sender's state

**Files:**
- Create: `outreach/store/migrations/0002_sending.sql`
- Test: `tests/test_sending_schema.py`

**Interfaces:**
- Produces (exact DDL, comments in the file's style; `ALTER TABLE ... ADD COLUMN` only):
  - `sender.history_id TEXT NOT NULL DEFAULT ''` (Gmail history cursor; `''` means read tracked threads once).
  - `sender.next_send_at TEXT CHECK (next_send_at IS strftime('%Y-%m-%dT%H:%M:%SZ', next_send_at))` (NULL: no gap pending).
  - `sender.auth_state TEXT NOT NULL DEFAULT 'unknown' CHECK (auth_state IN ('unknown','ok','signed_out','wrong_account'))`, `sender.auth_checked_at TEXT CHECK (... canonical UTC ...)`, `sender.auth_error TEXT NOT NULL DEFAULT ''`, `sender.account TEXT NOT NULL DEFAULT ''` (the address the token signs in as).
  - `send_day.closed_at TEXT CHECK (closed_at IS strftime('%Y-%m-%dT%H:%M:%SZ', closed_at))` (NULL: the day is still open).
  - `inbound_message.dsn_class TEXT CHECK (dsn_class IS NULL OR dsn_class IN ('hard','policy','limit','unknown'))`.

- [ ] **Step 1: Write the failing tests** (each on `fresh_store()`):
  - `test_sender_has_cursor_gap_and_auth_columns`: the row reads `history_id == ''`, `next_send_at is None`, `auth_state == 'unknown'`, `auth_error == ''`, `account == ''`.
  - `test_sender_auth_state_check`: `auth_state = 'bogus'` raises `sqlite3.IntegrityError`; each of the four values updates.
  - `test_send_day_closed_at_is_canonical_utc`: `'2026-11-02T08:00:00Z'` updates; `'2026-11-02 08:00'` raises.
  - `test_inbound_dsn_class_values`: `'policy'` inserts; `'soft'` raises; NULL inserts.
  - `test_migration_is_version_2_and_reinstall_changes_nothing`: `schema_migration` has a row for version 2 and `PRAGMA user_version == latest_version()` (`outreach.store.migrate.latest_version`; a later migration such as #17's 0003 must not break this test); `install()` twice leaves `schema_migration` unchanged.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sending_schema -v`. Expected: FAIL (no such column).
- [ ] **Step 3: Write `0002_sending.sql`** with the DDL above.
- [ ] **Step 4: Run** the whole suite and `uv run python tests/mutation_check.py`. Expected: all OK, `missed 0` (move anchors only if a test file shifted).
- [ ] **Step 5: Commit** `git add outreach/store/migrations/0002_sending.sql tests/test_sending_schema.py` then `Store: sender cursor, gap, sign-in state, day close and bounce class (#14)`.

## Task 2: Config and the sending calendar

**Files:**
- Create: `outreach/sending/__init__.py`, `outreach/sending/config.toml`, `outreach/sending/config.py`, `outreach/sending/calendar.py`
- Test: `tests/test_sending_config.py`, `tests/test_sending_calendar.py`

**Interfaces:**
- Produces:
  - `HARD_CEILING = 100` (#9; the `send_day` CHECK).
  - `@dataclass(frozen=True) class SendConfig`: `from_address: str` (`"devanshurajesh@gmail.com"`), `from_name: str` (`"Devanshu Chicholikar"`), `day_tz: str` (`"America/New_York"`), `ramp_start: int` (20), `ramp_step: int` (10), `ramp_ceiling: int` (100), `ramp_floor: int` (20), `clean_bounce_rate: float` (0.02), `bounce_days: int` (3), `hard_bounce_rate: float` (0.03), `hard_bounce_last_n: int` (100), `hard_bounce_min_sends: int` (10), `stop_replies_hard: int` (2), `stop_replies_days: int` (7), `window_start: time` (08:30), `window_end: time` (16:30), `weekdays: tuple[int, ...]` (0 to 4), `holidays: tuple[date, ...]` (empty), `per_company_daily: int` (3), `per_company_gap_minutes: int` (60), `gap_minutes: tuple[float, float]` ((4, 9)), `followup_business_days: tuple[int, ...]` ((7,)), `followup_lapse_business_days: int` (5), `close_after_business_days: int` (7), `reconcile_after_minutes: int` (10), `max_send_attempts: int` (2), `rethread_days: int` (45), `inbound_meta_cap: int` (200), `old_sender_label: str` (`"com.devanshu.outreach-mail-sender"`), `prune_noop_after_days: int` (14).
  - `load_config(path: Path = DEFAULT_PATH) -> SendConfig`: reads `config.toml`; raises `ValueError` naming the key when `ramp_ceiling > HARD_CEILING`, `ramp_start` or `ramp_floor` is outside `ramp_floor..ramp_ceiling`, `followup_business_days` is empty, longer than 3, or not increasing, `window_start >= window_end`, `gap_minutes` low above high, or a weekday outside 0 to 6.
  - `calendar.py` (pure, every function takes the time or date it needs):
    - `ny_date(at: datetime, cfg) -> date` (`at` is aware UTC).
    - `is_business_day(d: date, cfg) -> bool` (weekday in `cfg.weekdays`, not in `cfg.holidays`).
    - `add_business_days(d: date, n: int, cfg) -> date` (`d` itself never counts).
    - `business_days_between(a: date, b: date, cfg) -> int` (business days after `a` up to and including `b`; 0 when `b <= a`).
    - `recipient_zone(tz_name: str) -> ZoneInfo | None` (None for a name `zoneinfo` cannot load).
    - `in_window(at: datetime, zone: ZoneInfo, cfg) -> bool`: the local date is a business day and `window_start <= local time <= window_end` (both ends inclusive, as the old mailer).
    - `next_window_start(at: datetime, zone: ZoneInfo, cfg) -> datetime` (UTC; `at` itself when inside a window).
    - `ny_day_bounds(d: date, cfg) -> tuple[str, str]` (canonical UTC start and end of a New York date).

- [ ] **Step 1: Write the failing tests:**
  - config: `test_defaults_are_the_nine_numbers` (20, 10, 100, 0.02 over 3 days, 08:30 to 16:30, Monday to Friday, 3 per company, 60 minutes, `(7,)`); `test_ceiling_above_100_is_refused`; `test_four_follow_ups_are_refused` (`[3, 7, 15, 20]`); `test_follow_up_days_must_increase`; `test_inverted_window_is_refused`; `test_from_address_is_the_one_sender`.
  - calendar: `test_add_business_days_examples` (Thursday 2026-10-22 plus 7 is Monday 2026-11-02; Monday 2026-11-02 plus 7 is Wednesday 2026-11-11; Friday 2026-11-06 plus 1 is Monday 2026-11-09); `test_holiday_is_skipped_when_configured` (holidays `(2026-11-26,)`: Wednesday 2026-11-25 plus 1 is Friday 2026-11-27); `test_business_days_between_counts_after_a_up_to_b`; `test_ny_date_of_late_evening_utc` (`2026-11-03T03:30:00Z` is 2026-11-02); `test_window_edges` (08:29 no, 08:30 yes, 16:30 yes, 16:31 no, Saturday 10:00 no, in New York time); `test_window_uses_recipient_time_across_the_dst_change` (2026-11-02T21:00:00Z is 16:00 in New York and 13:00 in Los Angeles: both in; 2026-11-02T22:00:00Z: New York out, Los Angeles in); `test_next_window_start_from_friday_evening_is_monday_0830`; `test_unknown_zone_name_is_none`; `test_ny_day_bounds_on_the_25_hour_day` (2026-11-01 runs `04:00Z` to `05:00Z` next day).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sending_config tests.test_sending_calendar -v`. Expected: FAIL (module not found).
- [ ] **Step 3: Implement** with `tomllib` and `zoneinfo`; no function reads the clock.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add outreach/sending/__init__.py outreach/sending/config.toml outreach/sending/config.py outreach/sending/calendar.py tests/test_sending_config.py tests/test_sending_calendar.py` then `Sending: config with the #9 numbers and the business-day calendar (#14)`.

## Task 3: The mail client contract and the fake Gmail

**Files:**
- Create: `outreach/sending/mail.py`, `outreach/sending/fakegmail.py`
- Test: `tests/test_sending_fakegmail.py`

**Interfaces:**
- Produces in `mail.py`:
  - `@dataclass(frozen=True) class Outgoing: to: str; subject: str; body: str; from_name: str; from_addr: str; message_id: str; thread_id: str | None; in_reply_to: str | None; references: tuple[str, ...]`.
  - `@dataclass(frozen=True) class Sent: gmail_message_id: str; thread_id: str; rfc_message_id: str`.
  - `@dataclass(frozen=True) class Profile: email: str; history_id: str`.
  - `@dataclass(frozen=True) class MessageMeta: gmail_message_id: str; thread_id: str; from_addr: str; to_addr: str; subject: str; internal_ms: int; labels: tuple[str, ...]; headers: Mapping[str, str]; snippet: str` (`headers` lower-case names, only: `auto-submitted`, `x-autoreply`, `x-autorespond`, `precedence`, `x-auto-response-suppress`, `message-id`, `in-reply-to`, `references`, `x-failed-recipients`, `content-type`).
  - `@dataclass(frozen=True) class Added: gmail_message_id: str; thread_id: str; labels: tuple[str, ...]`; `@dataclass(frozen=True) class HistoryPage: added: tuple[Added, ...]; history_id: str`.
  - `class HistoryExpired(Exception)`.
  - `class GmailError(Exception)` with `kind: Literal['auth','rate_limit','daily_limit','account_blocked','bad_request','server','network','timeout']`, `status: int | None`, `detail: str` (at most 300 characters, never a header or token).
  - `classify_http(status: int, body: str) -> str` (the `kind`): 401 `auth`; 403 whose body names `dailyLimitExceeded` or `limit for sending` is `daily_limit`, `userRateLimitExceeded` or `rateLimitExceeded` is `rate_limit`, `insufficientPermissions` or `invalid_grant` is `auth`, any other 403 `account_blocked`; 429 `rate_limit`; other 4xx `bad_request`; 5xx `server`.
  - `RETRYABLE = frozenset({'rate_limit','daily_limit','account_blocked','auth','server','network','timeout'})` (the message never went out; only `bad_request` is the message's own fault).
  - `class MailClient(Protocol)`: `profile() -> Profile`; `send(m: Outgoing) -> Sent`; `history(start_history_id: str) -> HistoryPage` (raises `HistoryExpired`); `message(gmail_message_id: str) -> MessageMeta`; `thread(thread_id: str) -> list[MessageMeta]`; `find_sent(rfc_message_id: str, not_before_ms: int) -> tuple[Sent | None, bool]` (the bool says the scan reached a Sent message older than `not_before_ms` or the end of Sent, so a miss is proof). Any method may raise `GmailError`.
- Produces in `fakegmail.py`:
  - `class FakeClock`: `__init__(start: datetime)`, `__call__() -> datetime`, `advance(**timedelta_kwargs)`, `set(at: datetime)`.
  - `class FakeGmail(MailClient)`: `__init__(*, clock, account='devanshurajesh@gmail.com', probe: Callable[[str], None] | None = None)`; `probe(method_name)` is called at the start of every method (tests use it to assert no open transaction). State: `sent: list[tuple[Outgoing, Sent, datetime]]` (every accepted send, the only count of emails that "went out"), `sent_box` (what `find_sent` scans; an accepted send is added unless scripted otherwise), `calls: list[str]`. Scripting: `fail_next_send(error: Exception, *, accepted: bool = False)` (`accepted=True`: the email goes out and lands in Sent, then the call raises, as a timeout after Gmail took it); `fail_profile(error)`; `deliver_reply(to: Sent, from_addr, *, subject=None, headers=None, snippet='')`, `deliver_auto_reply(to: Sent, from_addr, *, subject='Automatic reply')`, `deliver_bounce(to: Sent, *, dsn: Literal['hard','policy','delay','limit'] = 'hard', threaded: bool = True)` (Gmail's own DSN shape: from `Mail Delivery Subsystem <mailer-daemon@googlemail.com>`, subject `Delivery Status Notification (Failure)` or `(Delay)`, `X-Failed-Recipients`, a snippet with `550 5.1.1`, `550 5.7.1 ... unsolicited`, or `You have reached a limit for sending mail`); `deliver_unrelated(from_addr, subject)` (INBOX noise); `expire_history()` (next `history` raises `HistoryExpired`); `find_sent_page_cap` (default unlimited; a test sets it to make a scan incomplete). Every delivered message gets a history entry; `profile().history_id` is the newest.

- [ ] **Step 1: Write the failing tests:** `test_send_assigns_ids_and_lands_in_sent`; `test_follow_up_with_thread_id_stays_in_the_thread`; `test_failed_send_sends_nothing`; `test_accepted_then_failed_counts_as_sent_and_is_in_sent`; `test_history_lists_only_new_messages_and_expires_on_request`; `test_bounce_shapes` (delay subject says `(Delay)`; unthreaded bounce has a new thread id and `X-Failed-Recipients`); `test_find_sent_by_message_id_reports_a_complete_scan`; `test_find_sent_with_a_page_cap_reports_incomplete`; `test_probe_sees_every_call`; `test_classify_http_table` (one assertion per row above, including 403 `insufficientPermissions` as `auth`).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sending_fakegmail -v`. Expected: FAIL.
- [ ] **Step 3: Implement** both modules. Ids are deterministic (`m1`, `t1` counters) so tests can name them.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add outreach/sending/mail.py outreach/sending/fakegmail.py tests/test_sending_fakegmail.py` then `Sending: the mail client contract and a scriptable fake Gmail (#14)`.

## Task 4: The real Gmail client (a port, never called by tests)

**Files:**
- Create: `outreach/sending/gmail.py`
- Test: `tests/test_sending_gmail.py`

**Interfaces:**
- Consumes: `mail.py` types; `live_checkout`.
- Produces:
  - `DEFAULT_DIR = ~/Library/Application Support/outreach-mail`; `gmail_dir() -> str` (`OUTREACH_GMAIL_DIR` or the default); `SCOPES` exactly as `mail/gmail.py` (`gmail.send gmail.metadata`), so the existing token works unchanged.
  - `build_mime(m: Outgoing) -> bytes`: plain text UTF-8, `To`, `From` (`formataddr`), `Subject`, `Message-ID` from `m.message_id`, `In-Reply-To` and `References` (space-joined) only when set; nothing else (no `Cc`, `Bcc`, attachments, HTML part). `ValueError` when `to` holds more than one address or a comma, or `message_id` is empty.
  - `new_message_id(from_addr: str) -> str` (`email.utils.make_msgid` with the address's domain).
  - `class GmailClient(MailClient)`: `__init__(directory: str | None = None, *, transport: Callable[[str, str, dict | None, dict | None], dict] | None = None, token_provider: Callable[[], str] | None = None)`. The default `token_provider` is `_token`, which raises `SystemExit` naming the live checkout when `live_checkout.is_live()` is false, before opening the file (the same rule as `mail/gmail.py::_token`), and refreshes and rewrites the token with mode 600 as the old code does. The constructor also calls `live_checkout.require_live("the real Gmail client")` when `directory` resolves to `DEFAULT_DIR`. HTTP errors become `GmailError(kind=classify_http(...))`; `socket.timeout` and `TimeoutError` become `timeout`; `URLError` `network`. `history` maps HTTP 404 to `HistoryExpired`.
  - `find_sent`: pages `GET /messages?labelIds=SENT` newest first, reads each with `format=metadata` and `metadataHeaders=Message-ID`, returns the one whose `Message-ID` equals `rfc_message_id` exactly; stops at the first message whose `internalDate` is below `not_before_ms` (complete) or at the end of Sent (complete) or after 10 pages of 50 (incomplete). There is no To-plus-Subject fallback (store plan: follow-ups share a subject).
  - `history`: `GET /history?startHistoryId=...&historyTypes=messageAdded`, all pages; returns `HistoryPage` with the last `historyId`.
  - `python -m outreach.sending.gmail auth` (the old `authorize` flow, PKCE and a loopback redirect, after `check_auth_location()`: `require_live` when the folder is the real one) and `whoami`.
  - Builder note: confirm in the Gmail API reference that `users.history.list` accepts the `gmail.metadata` scope. If it does not, `history` raises `HistoryExpired` every time, so the 45-day thread read in Task 10 becomes the only path; say so in the PR.

- [ ] **Step 1: Write the failing tests** (a fake `transport` records requests and returns canned JSON; `token_provider=lambda: "tok"`; no network):
  - `test_real_client_refuses_outside_the_live_checkout`: with `is_live` patched false and a scratch folder holding a token file, `GmailClient(dir)` then any call through the default token provider raises `SystemExit` with "live checkout", and the token file was never opened (patch `open` to record paths).
  - `test_default_folder_refuses_at_construction_outside_live`.
  - `test_mime_is_plain_text_one_recipient_no_cc` and `test_follow_up_mime_has_in_reply_to_and_references`; `test_two_recipients_are_refused`.
  - `test_send_posts_raw_and_thread_id` (body has `raw` and `threadId` for a follow-up only).
  - `test_http_errors_map_to_kinds` (401, 403 daily limit, 429, 400, 503 through the transport).
  - `test_find_sent_matches_by_message_id_only` (a Sent message with the same To and Subject but another Message-ID is not a hit).
  - `test_find_sent_stops_at_the_claim_time_and_says_complete`; `test_find_sent_after_ten_pages_says_incomplete`.
  - `test_history_404_is_history_expired`; `test_history_reads_every_page`.
  - `test_auth_into_the_real_folder_refuses_outside_live`.
  - `test_logs_and_errors_never_hold_the_token` (a transport that fails with a body echoing the Authorization header: the `GmailError.detail` does not contain `tok`).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sending_gmail -v`. Expected: FAIL.
- [ ] **Step 3: Implement** by porting `mail/gmail.py` (copy, do not import `mail/`).
- [ ] **Step 4: Run** the same command and `tests.test_live_guards`. Expected: PASS.
- [ ] **Step 5: Commit** `git add outreach/sending/gmail.py tests/test_sending_gmail.py` then `Sending: the real Gmail client, live checkout only, Sent checked by Message-ID (#14)`.

## Task 5: Classifying inbound mail

**Files:**
- Create: `outreach/sending/classify.py`
- Test: `tests/test_sending_classify.py`

**Interfaces:**
- Consumes: `MessageMeta`.
- Produces:
  - `@dataclass(frozen=True) class Inbound: kind: Literal['ours','human','auto_reply','bounce','delay','other']; dsn_class: str | None; failed_recipient: str | None`.
  - `classify(meta: MessageMeta, account: str) -> Inbound`. Order: from `account` (any display name, case-insensitive) is `ours`; a bounce sender (`mailer-daemon`, `postmaster`, `mail delivery (sub)system`, ported from `mail/sender.py` `BOUNCE`) or a bounce subject (`BOUNCE_SUBJ`) is `delay` when the subject says `(Delay)` or `delayed`, else `bounce`; an automatic message (the old `is_auto` rules: `Auto-Submitted` not `no`, `X-Autoreply`, `X-Autorespond`, `X-Auto-Response-Suppress`, `Precedence` in `auto_reply|bulk|junk|list`, `AUTO_SUBJ`) is `auto_reply`; anything else is `human`.
  - `dsn_class(meta) -> str`: from the snippet when present: `limit` for "reached a limit for sending"; `policy` for `5.7.1` or `4.7.28` with "unsolicited", "spam" or "policy", or `5.7.511`, or `4.7.5\d\d`; `hard` for `5.1.1`, `5.1.10`, `5.4.1`, "address not found", "does not exist"; else `unknown` (counted as a bounce).
  - `match_unthreaded(meta, tracked: Mapping[str, int], subjects: Mapping[str, int]) -> int | None`: the touch id whose person address equals `X-Failed-Recipients` (case-insensitive), else whose sent subject appears exactly in the DSN subject; None otherwise.

- [ ] **Step 1: Write the failing tests:** `test_out_of_office_header_is_not_a_reply` and `test_automatic_reply_subject_is_not_a_reply` (the old cases, ported); `test_human_reply_is_human`; `test_reply_from_another_address_is_a_human_reply`; `test_his_own_message_in_the_thread_is_ignored` (`"Devanshu C <devanshurajesh@gmail.com>"` is `ours`); `test_delay_notice_is_not_a_bounce` (the old regex would have called it a bounce); `test_gmail_bounce_is_hard`; `test_policy_snippet_is_policy` (one subtest per code above); `test_limit_snippet_is_limit`; `test_bounce_with_no_snippet_is_unknown_and_still_a_bounce`; `test_unthreaded_dsn_matches_by_failed_recipient`; `test_unthreaded_dsn_matches_by_exact_subject_only` (a near subject does not match); `test_newsletter_is_other_only_when_untracked` (an unrelated INBOX message classifies `human` but `match_unthreaded` returns None).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sending_classify -v`. Expected: FAIL.
- [ ] **Step 3: Implement** as pure functions.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add outreach/sending/classify.py tests/test_sending_classify.py` then `Sending: inbound classification, delay notices are not bounces (#14)`.

## Task 6: Signals, the ramp and the hard block

**Files:**
- Create: `outreach/sending/signals.py`, `outreach/sending/ramp.py`, `outreach/sending/notify.py`, `tests/sendtest.py`
- Test: `tests/test_sending_ramp.py`

**Interfaces:**
- Consumes: Task 1 columns, `SendConfig`, `calendar`, `append_event`.
- Produces:
  - `tests/sendtest.py` (every later task adds to it): `MON = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)` (09:00 New York); `seed_email_person(conn, company_id, name, email, *, role_kind='engineer', tz='America/New_York') -> int`; `seed_message(conn, person_id, *, state='approved', seq=0, subject='Hi', body='...', approved_at=None, due_on=None) -> int` (creates the touch when missing; `approved` sets `approved_at`; this is test seeding standing in for #13 and his click, never product code); `seed_sent(conn, person_id, *, at, seq=0, rfc=None, thread=None) -> int`; `bounce(conn, touch_id, at)`.
  - `signals.py`: `WARNING_KINDS = ('bounce_warning','policy_rejection','stop_reply')`; `HARD_KINDS = ('gmail_limit','gmail_policy','hard_bounce_rate','stop_replies_hard')`; `record_signal(conn, kind: str, *, at: str, detail: dict, key: str) -> bool` (an `event` with `entity 'sender'`, `actor 'sender'`, `idempotency_key key`; True when new); `signal_day(conn, at: str, cfg) -> date` (the New York date of `at`, or today when that day's `send_day` is closed); `sends_on(conn, d: date, cfg) -> int` (messages with `sent_at` on that New York date, every `seq`); `bounce_rate_3d(conn, d: date, cfg) -> float` (touches with `bounced_at` on the last `bounce_days` New York dates that had at least one send, up to and including `d`, over messages sent on those dates; 0.0 when none were sent; the same function #11's `bounceRate3d` uses); `hard_bounce_rate(conn, cfg) -> tuple[float, int]` (bounced touches among the touches of the last `hard_bounce_last_n` sent first emails, and that count); `stop_tags_in(conn, now, days) -> int`.
  - `ramp.py`: `@dataclass(frozen=True) class DayState: day: date; start_limit: int; effective_limit: int; sent: int; outcome: str | None; closed: bool`; `limit_for(conn, d: date, cfg) -> int` (read only: the start limit a business day `d` would get from the latest `send_day` row before it, by the rules in Decisions; 20 with no row); `effective_limit(conn, d: date, cfg) -> int` (read only: `d`'s start limit from its `send_day` row, else `limit_for`, one level lower, floor 20, when a warning or hard-block signal belongs to `d`; this is the seam #15's `current_send_limit` swaps to, so the plan never sizes itself on a limit the sender has already lowered); `open_day(conn, now: datetime, cfg) -> DayState | None` (closes every earlier row with `closed_at` NULL: outcome stays `warning` or `hard_block` when set, else `clean` when it had sends, else NULL with `outcome_note 'no sends'`; then inserts today's row with `limit_for` when today is a business day, `ON CONFLICT(day) DO NOTHING`; None on a non-business day); `today_state(conn, now, cfg) -> DayState | None`; `note_signal(conn, kind: str, *, at: str, detail: dict, key: str, cfg, notify) -> None` (records the signal; marks its day's `send_day.outcome` `warning`, or `hard_block` for a hard kind, never downgrading `hard_block`; a hard kind also sets `sender.paused = 1`, `pause_kind 'hard_block'`, `paused_reason` one plain sentence naming the kind, `paused_at`, appends a `hard_block` event, and calls `notify` once per kind per day).
  - `notify.py`: `Notifier = Callable[[str, str], None]`; `mac_notifier(title, text)` runs `osascript -e 'display notification ...'` and does nothing outside the live checkout; `notify_once(conn, notifier, kind, day, title, text)` dedupes with the event key `notify:<kind>:<day>`.

- [ ] **Step 1: Write the failing tests:**
  - `test_first_day_starts_at_20`; `test_clean_days_rise_by_10_to_100_and_stop` (ten clean days with sends: 20, 30, ..., 100, 100: `test_ramp_stops_at_100`).
  - `test_warning_today_lowers_the_effective_limit_at_once` (start 50, a `policy_rejection` at 11:00: effective 40, outcome `warning`).
  - `test_warning_day_does_not_rise` (next business day starts at 40).
  - `test_two_warnings_in_one_day_step_down_once`.
  - `test_floor_is_20` (a warning at 20 stays 20).
  - `test_day_with_no_sends_keeps_the_limit` and `test_weekend_has_no_row`.
  - `test_sleeping_mac_does_not_ramp_twice` (last row Friday clean at 30; the next tick is Wednesday: Monday and Tuesday have no rows, Wednesday starts at 40).
  - `test_a_late_signal_belongs_to_its_own_day_until_that_day_closes` (a bounce warning at 23:50 processed at 00:05 lands on yesterday; one processed after yesterday closed lands on today).
  - `test_bounce_rate_of_exactly_two_percent_is_a_warning` (1 bounce in 50 sends over 3 sending days: `0.02` is not under 2%) and `test_just_under_two_percent_is_clean` (1 in 51).
  - `test_bounce_rate_3d_uses_sending_days_and_is_zero_without_sends`.
  - `test_hard_bounce_rate_needs_ten_sends_and_over_three_percent` (1 of 9: not judged; 1 of 34: 2.9%, no; 2 of 40: 5%, hard).
  - `test_hard_kind_pauses_with_a_reason_and_notifies_once` (two `gmail_limit` signals: one pause row change, one notice).
  - `test_hard_block_over_his_own_pause_becomes_hard_block` (paused `by_you`, then `gmail_policy`: `pause_kind 'hard_block'`).
  - `test_limit_for_matches_open_day` (the read-only seam agrees with the row `open_day` writes).
  - `test_effective_limit_is_one_level_lower_after_a_warning` (start 50, a `policy_rejection` today: `effective_limit` 40 while `limit_for` and `send_limit` stay 50; no signal: 50; it writes nothing).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sending_ramp -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Every function takes `conn` and runs inside the caller's transaction; none opens one.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add outreach/sending/signals.py outreach/sending/ramp.py outreach/sending/notify.py tests/sendtest.py tests/test_sending_ramp.py` then `Sending: clean-day signals, the adaptive ramp and the hard block (#14)`.

## Task 7: The send queue and why a message waits

**Files:**
- Create: `outreach/sending/queue.py`, `outreach/drafts/followups.py`, and `outreach/drafts/__init__.py` only if it is absent (empty; #11 Task 17 puts `checks.py` beside it)
- Test: `tests/test_sending_queue.py`, `tests/test_drafts_followups.py`

**Interfaces:**
- Consumes: Tasks 2 and 6; tables `message`, `touch`, `person`, `company`, `no_go`, `sender`.
- Produces:
  - `outreach/drafts/followups.py` (standard library, imports nothing from `outreach.sending`, so #13's server may import it): `due_followups(conn, today: date, *, days_ahead: int = 0) -> list[int]`: the `message_id`s of `planned` rows with `due_on <= today + days_ahead` on touches in state `open` with `first_sent_at` set, whose surviving company (followed through `merged_into`) is not held, `closed` or `removed`, oldest `due_on` first. Read only. Task 11 and #13's `followups_due` both call it; it is the one copy of this query.
  - `canonical_company(conn, company_id: int) -> int` (follows `merged_into` to its end; a cycle raises `RuntimeError`).
  - `@dataclass(frozen=True) class Queued: message_id: int; touch_id: int; seq: int; revision: int; person_id: int; to: str; tz: str; role_kind: str; company_id: int; approved_at: str; due_on: date | None`.
  - `REASONS`: `ready`, `sender_paused`, `signed_out`, `old_sender_running`, `not_business_day`, `daily_limit`, `spacing`, `window`, `bad_timezone`, `followup_not_due`, `company_paused`, `company_held`, `per_company_limit`, `per_company_gap`, `recruiter_waits`, and the stop reasons `company_closed`, `no_go`, `reply_stop`, `touch_done` (bounced or closed touch).
  - `@dataclass(frozen=True) class Verdict: message_id: int; reason: str; earliest_at: datetime | None` (`earliest_at` set for time-based reasons: next window start, end of the gap, 60 minutes after the company's last send, next business day 08:30 New York for the daily limits, `due_on` at 08:30 recipient time).
  - `load_queue(conn) -> list[Queued]` (messages in `approved`, sorted by `max(approved_at, due_on at window start)` then `message_id`).
  - `verdicts(conn, now: datetime, cfg, *, day: DayState | None, gate: str | None) -> list[Verdict]` (`gate` is `sender_paused`, `signed_out` or `old_sender_running` when the tick knows one; it applies to all). Per message, in this order: stop reasons; `company_paused` (`paused_at` set on the surviving company), `company_held`; `followup_not_due`; `bad_timezone`; `not_business_day` (New York); `daily_limit` (sent today at or above the effective limit); `per_company_limit` (3 sends today at the surviving company); `per_company_gap` (the last send there under 60 minutes ago); `window` (recipient time); `recruiter_waits` (role kind `recruiter` and some non-recruiter message at the same company is approved and not stopped); `spacing` (`now < sender.next_send_at`); else `ready`.
  - `pick(verdicts: list[Verdict], queue: list[Queued]) -> Queued | None` (the first `ready` in queue order).

- [ ] **Step 1: Write the failing tests** (one per rule, plus): `test_queue_is_first_approved_first`; `test_daily_limit_counts_first_emails_and_follow_ups`; `test_second_send_at_one_company_waits_60_minutes` (59 minutes: `per_company_gap`; 60: ready); `test_fourth_send_at_one_company_waits_for_tomorrow`; `test_per_company_counts_use_the_surviving_company` and `test_person_at_merged_company_sends_under_the_survivor` (a person whose company has `merged_into` and status `removed` is not `company_closed`); `test_recruiter_waits_for_the_engineer_at_the_same_company` and `test_recruiter_alone_is_ready`; `test_recruiter_counts_inside_the_company_limit`; `test_follow_up_waits_for_its_due_date`; `test_window_is_per_recipient` (16:45 New York: the New York person waits, the Los Angeles person is ready); `test_held_and_paused_companies_wait_and_say_so`; `test_no_go_person_and_company_are_stop_reasons`; `test_closed_company_is_a_stop_reason`; `test_said_no_tag_is_a_stop_reason`; `test_bad_timezone_waits_and_says_so`; `test_spacing_uses_next_send_at`; `test_gate_applies_to_every_message`; `test_every_wait_has_an_earliest_time_or_is_a_stop`. In `tests/test_drafts_followups.py`: `test_due_followups_lists_planned_rows_due_on_or_before_today` (and `days_ahead` widens it); `test_due_followups_skips_replied_closed_held_removed_and_never_sent_touches` (a held company through a merged duplicate too); `test_due_followups_writes_nothing`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sending_queue tests.test_drafts_followups -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with a few bulk queries (queue, today's sends per company, no-go rows), not one query per message.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add outreach/sending/queue.py outreach/drafts/followups.py tests/test_sending_queue.py tests/test_drafts_followups.py` (and `outreach/drafts/__init__.py` when this task created it) then `Sending: the queue, per-company limits, recruiters last, the reason each message waits, and the due follow-ups query (#14)`.

## Task 8: Claim, send and record (after #11 Task 9 is on master)

**Files:**
- Create: `outreach/sending/claim.py`
- Test: `tests/test_sending_claim.py`

**Interfaces:**
- Consumes: `Queued`, `MailClient`, `gmail.new_message_id`, `outreach.outcomes.context.build_context` (#11), `log_touch`, `append_event`, `followups.plan_followups` and `followups.stop_planned` (Task 11 specifies both; this task calls the first and Task 10 the second, so both land here as working code with Task 11's signatures, and Task 11 adds their tests and the lapse, close and sweep parts).
- Produces:
  - `claim(conn, q: Queued, *, rfc_id: str, now: str) -> bool`: one transaction: `UPDATE message SET state='sending', pending_rfc_id=?, claimed_at=?, updated_at=?, revision=revision+1 WHERE message_id=? AND state='approved' AND revision=?`; appends `claimed`. False when no row changed. An `IntegrityError` from `message_one_sending` is re-raised as `AnotherSendInFlight`.
  - `outgoing_for(conn, q: Queued, cfg, rfc_id: str) -> Outgoing`: subject and body as stored; for `seq >= 1`, `thread_id` is the touch's `gmail_thread_id`, `in_reply_to` the newest sent message's `rfc_message_id` in that touch, `references` all of them oldest first. Message-IDs ending `@outreach.invalid` (the placeholders #17 writes for imported mail with no known id) are left out of both; with none left, the follow-up still goes in the Gmail thread by `thread_id`.
  - `email_context(conn, person_id: int, company_id: int) -> dict`: `build_context(source='plan' if a daily_list_entry with this person_id and channel 'email' exists else 'own', lane=that entry's list_group, else the newest company_score lanes[0], else None (JSON null, which the context CHECK allows; #11 Task 9 types it `lane: str | None`), role_kind=person.role_kind, role_age_days from the entry's posting or None, ranker_version from the newest company_score or None, signals={name: values[name]['value']} from that score or {}, role_named=entry posting is not None)`.
  - `record_sent(conn, message_id: int, sent: Sent, *, at: str, cfg, rng: random.Random, reconciled: bool = False) -> None`: one transaction: `sending` or `send_unknown` to `sent` with `rfc_message_id`, `gmail_message_id`, `sent_at`, `pending_rfc_id = NULL`, `claimed_at = NULL`, `reconciled_at` when reconciled; for `seq 0` also `touch.first_sent_at`, `first_rfc_id`, `gmail_thread_id` together, `log_touch(channel='email', email_touch_id=touch_id, person_id, company_id=surviving company, sent_at=at, context=email_context(...), idempotency_key=f"email:{touch_id}", actor='sender')`, and `plan_followups`; event `sent` or `reconciled_sent`; `sender.next_send_at = at + uniform(gap_minutes)` from `rng`.
  - `record_unclear(conn, message_id: int, error: Exception, *, at: str) -> None`: `sending -> send_unknown`, `error` text (300 characters, no secrets), event `send_unknown` with the error kind.
  - `send_one(conn, client: MailClient, q: Queued, cfg, *, now: str, rng, account: str) -> Literal['sent','unclear','lost_race']`: `rfc = new_message_id(account)`; `claim` (commit, fullfsync connection); then, with no transaction open, `client.send(outgoing_for(...))`; then `record_sent`, or on any exception `record_unclear` and re-raise a `GmailError` so the tick can apply its kind.

- [ ] **Step 1: Write the failing tests** (`FakeGmail` with a `probe` that asserts `not conn.in_transaction` and that a second connection can run `BEGIN IMMEDIATE; ROLLBACK`):
  - `test_send_is_claimed_with_its_message_id_before_the_call` (inside the fake's `send`, a fresh connection reads state `sending` and `pending_rfc_id` equal to the outgoing Message-ID).
  - `test_no_gmail_call_inside_a_transaction` (the probe never fails over a full send).
  - `test_sent_message_records_ids_and_clears_the_claim`.
  - `test_first_send_sets_the_touch_thread_and_logs_one_outreach_touch` (channel `email`, `email_touch_id`, `actor 'sender'` on the event; context keys pinned by #30; a second `record_sent` for the same touch writes no second touch).
  - `test_context_source_is_plan_for_a_plan_row_person_and_own_otherwise`.
  - `test_follow_up_goes_in_the_thread_with_reply_headers`.
  - `test_failed_call_leaves_send_unknown_and_cannot_be_claimed_again`.
  - `test_crash_after_claim_before_the_call_leaves_sending` (the fake raises `SystemExit` at its start: state `sending`, nothing in `FakeGmail.sent`).
  - `test_crash_after_gmail_accepted_is_recorded_not_resent` (the fake accepts, then `record_sent` is made to raise: the row stays `sending`; Task 9's reconcile then records it; `FakeGmail.sent` has one entry after three more ticks' worth of reconcile and pick).
  - `test_second_claim_while_one_is_sending_is_refused` (`AnotherSendInFlight`).
  - `test_lost_race_on_revision_sends_nothing`.
  - `test_next_send_at_is_four_to_nine_minutes_later` (seeded `random.Random(7)`).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sending_claim -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add outreach/sending/claim.py outreach/sending/followups.py tests/test_sending_claim.py` then `Sending: claim with the Message-ID before Gmail, record sent with the touch and its outcome row (#14)`.

## Task 9: Settle unclear sends and retry proven failures

**Files:**
- Modify: `outreach/sending/claim.py`
- Test: `tests/test_sending_reconcile.py`

**Interfaces:**
- Consumes: Task 8.
- Produces:
  - `leftover_claims(conn, now: str) -> int`: one transaction turning every `sending` row into `send_unknown` (a `sending` row at the start of a tick is a crash, since ticks never overlap) with error `the sender stopped mid-send`.
  - `@dataclass(frozen=True) class Unclear: message_id: int; rfc_id: str; claimed_at: str; not_before_ms: int` (`claimed_at` minus 5 minutes, in milliseconds).
  - `unclear(conn) -> list[Unclear]`.
  - `reconcile(conn, found: Mapping[int, tuple[Sent | None, bool]], *, now: str, cfg, rng: random.Random) -> dict[str, int]`: a hit becomes `sent` through `record_sent(..., reconciled=True)`; a complete miss at least `reconcile_after_minutes` (10) after `claimed_at` becomes `failed` (`reconciled_at`, `error` "Not in Sent 10 minutes after the attempt, so it did not go out.", `pending_rfc_id` and `claimed_at` cleared, event `claim_failed` with the attempt number and `retryable` from the error kind of its `send_unknown` event); an incomplete scan or a younger claim stays `send_unknown`.
  - `requeue_failed(conn, *, now: str, cfg) -> int`: `failed -> approved` for each failed message whose last `claim_failed` is `retryable` and whose `claim_failed` count is below `max_send_attempts` (2); event `requeued`.

- [ ] **Step 1: Write the failing tests:** `test_unknown_send_found_in_sent_becomes_sent_without_resending`; `test_timeout_after_accept_is_found_in_sent` (`fail_next_send(TimeoutError(), accepted=True)`: one email, state `sent`); `test_unknown_send_missing_from_sent_becomes_failed_after_ten_minutes`; `test_recent_unknown_send_waits`; `test_not_found_needs_a_scan_back_to_the_claim` (`find_sent_page_cap` set: incomplete scan, stays `send_unknown` at 30 minutes); `test_reconcile_matches_by_message_id_only` (a Sent entry with the same subject and recipient but another Message-ID is a miss); `test_follow_up_reconcile_does_not_take_another_follow_ups_id`; `test_requeue_retries_once_then_stops` (`test_requeue_stops_after_two_attempts`: two claims, two `claim_failed`, state stays `failed`); `test_bad_request_failure_is_not_requeued`; `test_crash_leftover_sending_is_reconciled_not_resent`; `test_failed_attempt_message_id_is_kept_in_an_event`; `test_one_email_total_after_a_crash_before_the_call` (claim, crash, 10 minutes, failed, requeue, send: `FakeGmail.sent` has exactly one entry and the message's `rfc_message_id` is the second Message-ID).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sending_reconcile -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** The `find_sent` calls happen in the tick, outside any transaction; `reconcile` only applies their results.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add outreach/sending/claim.py tests/test_sending_reconcile.py` then `Sending: settle unclear sends by Message-ID and retry a proven failure once (#14)`.

## Task 10: Reading replies, auto-replies and bounces

**Files:**
- Create: `outreach/sending/inbound.py`
- Test: `tests/test_sending_inbound.py`

**Interfaces:**
- Consumes: Tasks 5 and 6; `record_outcome`; `canonical_company`; `followups.stop_planned` (landed in Task 8).
- Produces:
  - `tracked_threads(conn) -> dict[str, int]` (every `touch.gmail_thread_id` to its touch id); `tracked_subjects(conn) -> dict[str, int]`; `known_ids(conn) -> set[str]` (`inbound_message.gmail_message_id` and `message.gmail_message_id`).
  - `read_inbound(client, snapshot, *, cfg, now) -> InboundRead` (no store access; `snapshot` is the three maps plus `sender.history_id`): with a cursor, `history` then `message` for each new id in a tracked thread, and for each new INBOX id elsewhere up to `inbound_meta_cap`; on `HistoryExpired` or an empty cursor, `thread()` for every tracked touch whose `first_sent_at` is within `rethread_days` (45), after taking `profile().history_id` first so nothing in between is missed. `InboundRead(metas: list[MessageMeta], new_history_id: str | None, complete: bool)`; an incomplete read (cap hit, an error mid-way) keeps the old cursor.
  - `apply_inbound(conn, read: InboundRead, *, account: str, now: datetime, cfg, notify) -> dict[str, int]` (one transaction): each meta is classified; `ours`, `delay` and untracked `other` write nothing; the rest write one `inbound_message` row (`ON CONFLICT(gmail_message_id) DO NOTHING`, `received_at` from `internal_ms`, `body_text` the snippet, `dsn_class` for bounces) and:
    - `human`: touch `state 'replied'` (from `open`, `closed` or `bounced`), `replied_at` and `reply_kind 'unsorted'` when unset; `record_outcome(..., kind='replied', at=received_at, revision=<the outreach_touch's current revision, read first in the same transaction>, actor='sender')` on the touch's `outreach_touch` when it has no reply yet (the merged signature requires `revision`, `outreach/store/writes.py:229`); the surviving company held when not already (`held_by_touch`, `held_at`, event `held`); the touch's own queued messages stopped as Decisions says (reason `replied`): its `planned` follow-up deleted with a touch `skipped_by_sender` event, its `drafted`, `approved` and `failed` messages `skipped`.
    - `auto_reply`: `touch.auto_reply_at` when unset; nothing else.
    - `bounce` (threaded or matched by `match_unthreaded`): touch `state 'bounced'` and `bounced_at` when it is `open`; its queued messages stopped the same way (reason `bounced`); then `note_signal('policy_rejection')` when `dsn_class` is `policy`, `note_signal('gmail_limit')` when `limit`, `note_signal('bounce_warning', key=f"bounce_warning:{day}")` when `bounce_rate_3d` of the bounce's day reaches `clean_bounce_rate`, and `note_signal('hard_bounce_rate')` when `hard_bounce_rate` is over `hard_bounce_rate` with at least `hard_bounce_min_sends`.
    - Sets `sender.history_id` to `new_history_id` when the read was complete.
  - `verify_imported(conn, client, *, now, limit: int = 20) -> dict[str, int]` (Gmail reads first, then one transaction; asked for by #17): for up to `limit` `sent` messages on touches with `origin 'import'` that have a `gmail_message_id`, no `@outreach.invalid` Message-ID and no `import_verified` or `import_not_in_sent` event, `client.message(gmail_message_id)`: the `SENT` label and the same `Message-ID` header write event `import_verified`; a 404 or a different header writes `import_not_in_sent` (shown by Task 16's `imported_unverified` count). It never changes a message. Runs every tick until none are left.
  - `observe_stop_tags(conn, *, now, cfg, notify) -> int`: for each email touch whose `touch.reply_kind` or its `outreach_touch.reply_kind` is `asked_to_stop` and has no `stop_reply` event yet: `note_signal('stop_reply', key=f"stop_reply:{touch_id}")`; then `stop_replies_hard` when `stop_tags_in(now, 7) >= 2`.

- [ ] **Step 1: Write the failing tests:** `test_human_reply_marks_the_touch_and_records_the_outcome`; `test_reply_holds_every_queued_message_at_the_company` (two colleagues approved at the same company stay `approved` with verdict `company_held`; the replier's planned follow-up row is gone and the touch has a `skipped_by_sender` event with its `seq` and `due_on`); `test_reply_with_a_planned_follow_up_does_not_fail_the_tick` (the apply transaction commits; no `IntegrityError` from the `message` CHECK); `test_reply_outcome_passes_the_current_revision` (the `outreach_touch` revision rises by exactly one and `replied_at` is set); `test_reply_at_a_merged_away_company_holds_the_survivor`; `test_late_reply_on_a_closed_touch_holds_and_records_the_outcome`; `test_reply_after_quiet_clears_quiet` (#30's `record_outcome` rule); `test_second_reply_writes_inbound_only`; `test_auto_reply_is_recorded_and_holds_nothing`; `test_bounce_closes_the_touch_queue_and_counts`; `test_unthreaded_bounce_is_matched`; `test_policy_bounce_is_a_warning`; `test_bounce_pushing_the_rate_to_two_percent_is_one_warning_for_the_day`; `test_hard_bounce_rate_pauses`; `test_one_stop_tag_is_a_warning_and_two_in_a_week_pause`; `test_stop_tag_seen_twice_counts_once`; `test_expired_cursor_reads_recent_threads_then_moves_on`; `test_incomplete_read_keeps_the_cursor`; `test_inbox_noise_writes_nothing`; `test_read_inbound_runs_with_no_transaction` (probe); `test_paused_sender_still_records_replies` (done through Task 13's tick; written here as a store-level test with `sender.paused = 1`); `test_imported_sent_message_is_verified_by_its_gmail_id`; `test_imported_message_missing_from_sent_is_reported_and_left_alone`; `test_placeholder_message_id_is_never_looked_up`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sending_inbound -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add outreach/sending/inbound.py tests/test_sending_inbound.py` then `Sending: replies hold the company, bounces and stop tags feed the ramp (#14)`.

## Task 11: Follow-ups, lapses, closing quiet touches and stopped messages

**Files:**
- Modify: `outreach/sending/followups.py` (the Task 8 stub)
- Test: `tests/test_sending_followups.py`

**Interfaces:**
- Consumes: Task 7's `outreach.drafts.followups.due_followups`, which stays the only due-list query (it moved there from this task so #13 can import it without importing `outreach.sending`).
- Produces:
  - `plan_followups(conn, touch_id: int, first_sent_on: date, cfg) -> int`: for each `n` in `followup_business_days` (index `i` from 1), `INSERT INTO message(touch_id, seq, state, due_on, saved_by, created_at, updated_at) VALUES (?, i, 'planned', add_business_days(first_sent_on, n), 'sender', ?, ?) ON CONFLICT(touch_id, seq) DO NOTHING`; event `followup_planned`. Empty subject and body (a `planned` row needs none, and the CHECK allows none only in `planned`).
  - `stop_planned(conn, message_id: int, *, reason: str, now: str) -> None`: deletes one `planned` row (refuses any other state with `ValueError`) and appends one touch event `skipped_by_sender` (or `followup_lapsed` when `reason == 'lapsed'`) with `{seq, due_on, reason}` and key `followup-stopped:<touch_id>:<seq>:<due_on>`. Every path that stops a planned row (Task 10's reply and bounce, the lapse, the sweep) calls it.
  - `lapse_followups(conn, today: date, cfg) -> int`: for each `planned` row where `business_days_between(due_on, today) > followup_lapse_business_days` (5), `stop_planned(reason='lapsed')`.
  - `close_quiet_touches(conn, today: date, cfg) -> int`: touches `open` with `first_sent_at` set, no message in `planned`, `drafted`, `approved`, `sending` or `send_unknown`, and `business_days_between(last sent New York date, today) >= close_after_business_days` (7): `state 'closed'`, `close_reason 'no_reply'`, `closed_at`; event `closed`.
  - `sweep_stopped(conn, verdicts: list[Verdict], *, now: str) -> int`: for stop reasons, the message `approved -> skipped`, its touch's other `drafted` and `failed` messages too, and its `planned` ones through `stop_planned`; an `open` touch closes `company_closed` or `no_go`; a `reply_stop` touch keeps `replied`; events `skipped_by_sender` with the reason.

- [ ] **Step 1: Write the failing tests:** `test_followup_due_seven_business_days_later` (sent Monday 2026-11-02 at 09:00 New York: `due_on` 2026-11-11); `test_friday_send_follow_up_lands_seven_business_days_later` (Friday 2026-11-06: 2026-11-17); `test_evening_utc_send_uses_the_new_york_date` (`2026-11-03T02:00:00Z` is a Monday send); `test_three_configured_follow_ups_make_seq_1_to_3` (`(3, 7, 15)`); `test_planning_twice_adds_nothing`; `test_undrafted_follow_up_lapses_after_five_business_days` (and not at four); `test_lapse_deletes_the_planned_row_and_records_it_on_the_touch` (the row is gone; one `followup_lapsed` touch event with `seq` and `due_on`; a second lapse run adds nothing); `test_stop_planned_refuses_a_drafted_row`; `test_a_re_plan_after_a_lapse_is_a_new_planned_row`; `test_touch_closes_seven_business_days_after_the_last_send` (and not with an approved follow-up waiting); `test_sweep_skips_messages_at_a_closed_company_and_closes_the_touch` (its planned follow-up deleted with the touch event, never moved to `skipped`); `test_sweep_on_a_no_go_person`; `test_sweep_leaves_held_and_paused_alone`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sending_followups -v`. Expected: FAIL (the stub has no lapse, close or sweep).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command and `tests.test_sending_claim`. Expected: PASS.
- [ ] **Step 5: Commit** `git add outreach/sending/followups.py tests/test_sending_followups.py` then `Sending: one follow-up at seven business days, lapses that delete the planned row, quiet closes and stop sweeps (#14)`.

## Task 12: The pre-claim check (after #11 Task 17 is on master)

**Files:**
- Create: `outreach/sending/precheck.py`
- Test: `tests/test_sending_precheck.py`

**Interfaces:**
- Consumes: `outreach.drafts.checks.run_checks(conn, DraftInput, *, banned, now)`, `DraftInput` and `load_banned(path: Path | None = None) -> tuple[list[tuple[str, re.Pattern]], bool]` (#11 Task 17; the bool says the list was readable); `mx_check` (cache only: the sender makes no DNS call; a missing or stale row is `unknown`, which only warns).
- Produces: `precheck(conn, q: Queued, *, now: str, banned) -> list[dict] | None`: builds the `DraftInput` from the stored message, person and company; runs `run_checks`; when any check is `block`, in one transaction moves the message `approved -> drafted` with `checks` set to the fresh list, `updated_at`, `revision + 1`, and an event `send_blocked` naming the codes, and returns the blocks; None when it may be claimed. `banned` is the list `load_banned()` returns (an unreadable list only warns, as `run_checks` decides).

- [ ] **Step 1: Write the failing tests:** `test_no_go_added_after_approval_stops_the_send` (approve, add a person `no_go`, tick: no claim, message `drafted`, a `no_go_person` block stored, nothing in `FakeGmail.sent`); `test_colleague_draft_saved_after_approval_blocks_as_near_identical`; `test_warnings_never_stop_a_send` (an em dash in the body still sends); `test_unknown_mail_server_does_not_block`; `test_blocked_message_returns_to_review_and_can_be_approved_again_after_the_fix` (lift the no-go, re-approve through a test seed, it sends).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sending_precheck -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add outreach/sending/precheck.py tests/test_sending_precheck.py` then `Sending: re-run the approve checks on current data before every claim (#14)`.

## Task 13: The tick

**Files:**
- Create: `outreach/sending/tick.py`
- Test: `tests/test_sending_tick.py`

**Interfaces:**
- Consumes: Tasks 6 to 12.
- Produces:
  - `@dataclass(frozen=True) class TickDeps: client: MailClient; clock: Callable[[], datetime]; rng: random.Random; notify: Notifier; old_sender_loaded: Callable[[], bool]; banned: Sequence`.
  - `@dataclass(frozen=True) class TickReport: auth: str; day: DayState | None; reconciled: dict[str, int]; inbound: dict[str, int]; sent: tuple[int, ...]; would_send: int | None; waiting: dict[str, int]; dry_run: bool; lines: tuple[str, ...]`.
  - `run_tick(conn, deps: TickDeps, cfg: SendConfig, *, dry_run: bool = False) -> TickReport`. Steps, in order (Gmail calls only where marked, never inside a transaction):
    1. `job_run` start (`job 'sender'`), skipped in a dry run.
    2. Gmail: `profile()`. A `GmailError` of kind `auth` sets `sender.auth_state 'signed_out'`, `auth_error`, `auth_checked_at`, notifies once a day, and ends the tick (no reads, no sends). An address other than `cfg.from_address` sets `wrong_account` and ends the tick the same way. Else `auth_state 'ok'`, `account`.
    3. `leftover_claims`.
    4. Gmail: `find_sent` for each `unclear`; `read_inbound`; `verify_imported` (its own Gmail reads, then its own short transaction).
    5. One transaction: `reconcile`, `requeue_failed`, `apply_inbound`, `observe_stop_tags`, `open_day`, `lapse_followups`, `close_quiet_touches`, `verdicts` and `sweep_stopped`; once per New York day, delete this job's `ok` runs with `summary.noop = 1` older than `prune_noop_after_days` (14).
    6. Gate: `sender.paused` (by kind), `auth_state`, `old_sender_loaded()`; `verdicts` with the gate; `pick`.
    7. If a message is ready: `precheck`; then `send_one`. A `GmailError` of kind `rate_limit` or `daily_limit` is `note_signal('gmail_limit')`, `account_blocked` is `note_signal('gmail_policy')`, `auth` is `signed_out`; all in one short transaction after the call.
    8. `job_run` finish with integer counts (`sent`, `reconciled`, `replies`, `autoReplies`, `bounces`, `skipped`, `lapsed`, `closed`) and `noop`.
    A dry run does steps 2 and 4 for real, runs step 5 and 6 inside one transaction that is rolled back, stops before step 7, and reports `would_send`.
  - A process lock: `fcntl.flock` on `<store folder>/.sender.lock`, non-blocking, taken by `run_tick` before step 1; held means another tick runs, and this one returns a report with `lines == ("another tick is running",)` having written nothing.
  - `sqlite3.OperationalError` "database is locked" in any step ends the tick with a log line and a `failed` `job_run` when one can be written; the next tick retries.

- [ ] **Step 1: Write the failing tests:** `test_one_tick_sends_at_most_one`; `test_dead_token_sends_nothing_and_is_signed_out` and `test_revoked_token_is_signed_out_not_a_hard_block` (`sender.paused` stays 0); `test_wrong_account_sends_nothing` (profile `someone@else.com`); `test_old_sender_loaded_sends_nothing_but_still_reads_replies`; `test_paused_by_you_sends_nothing_but_records_replies_and_settles_unclear_sends`; `test_hard_block_on_429_pauses_and_the_message_is_retried_after_resume` (the message ends `sent` once after a test-seeded resume; one email total); `test_dry_run_writes_nothing` (`conn.iterdump()` before and after are equal; `FakeGmail.sent` empty; `would_send` names the message); `test_dry_run_reads_gmail` (the fake saw `profile` and `history`); `test_second_tick_while_one_runs_does_nothing` (hold the lock in the test); `test_locked_store_fails_the_tick_without_sending`; `test_quiet_ticks_are_pruned_after_14_days_and_busy_ones_kept`; `test_no_gmail_call_inside_a_transaction_across_a_whole_tick` (probe on every fake method); `test_every_tick_writes_one_job_run_with_integer_counts`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sending_tick -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command and every `tests.test_sending_*`. Expected: PASS.
- [ ] **Step 5: Commit** `git add outreach/sending/tick.py tests/test_sending_tick.py` then `Sending: the tick, with sign-in, account, old-sender and pause gates and a dry run that writes nothing (#14)`.

## Task 14: The command line, the live guard and the launchd template

**Files:**
- Create: `outreach/sending/__main__.py`, `launchd/com.devanshu.outreach-sender.plist.template`
- Modify: `.claude/settings.json`, `CLAUDE.md`
- Test: `tests/test_sending_cli.py`

**Interfaces:**
- Produces:
  - `main(argv: list[str] | None = None) -> int` with subcommands:
    - `run [--dry-run]`: the first statement is `live_checkout.require_live("the sender")`, before any config, store or client is touched; then `load_config()`, `GmailClient()`, `connect(fullfsync=True)`, `TickDeps(..., notify=mac_notifier, old_sender_loaded=launchctl_loaded(cfg.old_sender_label), rng=random.Random(), banned=load_banned()[0])`, `run_tick`, print `report.lines`. There is no flag, variable or file that skips the guard.
    - `status`: reads the store at `OUTREACH_DB` (`connect()` refuses the live store outside the live checkout) and prints Task 16's snapshot in plain lines. No Gmail.
    - `whoami`: `GmailClient().profile().email` (live only through the client).
    - `auth`: delegates to `outreach.sending.gmail` auth (live only for the real folder).
    - `simulate [--days 10] [--seed 7]`: Task 15's simulator on a temp store it creates; never reads `OUTREACH_DB`, never builds a `GmailClient`.
    - No `resume` and no `approve` command exists: resuming after a hard block is his click in the app (#11 Task 18), and approving is only #11's route.
  - `launchctl_loaded(label: str) -> Callable[[], bool]`: `launchctl print gui/<uid>/<label>` exit code 0.
  - The template follows the one launchd convention #17 Task 17 states: file `launchd/<label>.plist.template`, label `com.devanshu.outreach-sender` (the name #17's `NEW_JOBS` expects), `StartInterval 120`, `WorkingDirectory` `{{LIVE_CHECKOUT}}`, `ProgramArguments` `{{LIVE_CHECKOUT}}/.venv/bin/python -m outreach.sending run`, `StandardOutPath` and `StandardErrorPath` `{{LOG_PATH}}` (the only two placeholders #17's deploy renders), no secret and no environment variable that points at the store or the Gmail folder (the defaults are the live ones). It is a template: #17 installs it at the switch; no step here loads it.
  - `.claude/settings.json`: add deny entries `Bash(uv run python -m outreach.sending run:*)`, `Bash(python -m outreach.sending run:*)`, `Bash(.venv/bin/python -m outreach.sending run:*)`, `Bash(python3 -m outreach.sending run:*)` and the same four for `auth`, beside the :3002 denies. `CLAUDE.md` "Only in the live checkout" gains `python -m outreach.sending run` (with or without `--dry-run`) and `auth`, and the line "Claude never runs the new sender in any checkout; launchd does." Both edits are their own commit (Step 6), shown as a separate diff in the PR for Devanshu's review (Pending Devanshu Q12); #17's switch PR rebases on them and keeps these lines.

- [ ] **Step 1: Write the failing tests:** `test_run_refuses_outside_the_live_checkout` (exit through `SystemExit` naming the live checkout; `GmailClient` and `connect` patched to record calls: neither called; no file created under a scratch store folder); `test_dry_run_refuses_outside_the_live_checkout_too`; `test_no_bypass_exists` (no `allow-non-live`, `--force` or `OUTREACH_LIVE` string in `outreach/sending/`); `test_status_on_a_scratch_store_prints_the_state`; `test_simulate_never_opens_outreach_db` (`OUTREACH_DB` set to a path whose folder does not exist: still absent afterwards; output has ten day lines); `test_there_is_no_resume_or_approve_command`; `test_template_runs_every_120_seconds_with_only_the_two_placeholders_and_no_secret`; `test_template_renders_with_the_deploy_tool` (`skipUnless` `outreach.deploy.jobs` imports: `render(text, live_root=<temp>, log_path=<temp>)` parses and its `Label` is the job's); `test_settings_deny_agent_runs_of_the_sender` (reads `.claude/settings.json`).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sending_cli -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the module, the template and the two edits.
- [ ] **Step 4: Run** the same command and `tests.test_live_guards`. Expected: PASS.
- [ ] **Step 5: Commit** `git add outreach/sending/__main__.py launchd/com.devanshu.outreach-sender.plist.template tests/test_sending_cli.py` then `Sending: command line with the live-checkout guard and the launchd template (#14)`.
- [ ] **Step 6: Commit** `git add .claude/settings.json CLAUDE.md` then `Sending: deny agent runs of the new sender; CLAUDE.md lists it as live checkout only (#14)`. The PR body points Devanshu at this commit's diff.

## Task 15: The two-week simulation (issue #14 "Done when")

**Files:**
- Create: `outreach/sending/simulate.py`
- Test: `tests/test_sending_simulation.py`

**Interfaces:**
- Consumes: `run_tick`, `FakeGmail`, `FakeClock`, `install`.
- Produces:
  - `@dataclass class Scenario`: `start: date` (Monday 2026-11-02, the day after daylight saving ends), `days: int` (10 business days, to Friday 2026-11-13), `companies: int`, `people_per_company: int`, timezone mix, and a list of scripted `Event(at: datetime, action: str, args: dict)` (approve a batch, deliver a reply, bounce, policy bounce, a send failure, a stop tag, a resume, a no-go, draft and approve due follow-ups). Approve, resume, tag and draft events stand in for Devanshu and Claude in the simulation only; they write the store directly with test seed SQL.
  - `simulate(scenario, *, seed: int = 7) -> SimResult` builds a temp store, ticks from 07:00 to 20:00 New York each business day, and jumps the clock to the earliest of: the next scripted event, the next `earliest_at` among waiting verdicts, `sender.next_send_at`, 30 minutes on; never less than 2 minutes (the tick interval). `SimResult` has per-day rows (`day, start_limit, effective_limit, sent, outcome`), every send (`at`, company, person, role kind, seq, Message-ID, in-reply-to) and the final store connection.
  - `TWO_WEEKS`: the scenario the CLI prints and the main test asserts.

- [ ] **Step 1: Write the failing tests:**
  - `test_two_weeks_ramp_and_step_downs`: start limits by day are `[20, 30, 40, 50, 40, 50, 60, 70, 60, 70]`; day 4 (Thursday 2026-11-05) has a policy bounce at 11:00, outcome `warning`, effective 40, sent 40; day 8 (Wednesday 2026-11-11) has a Gmail 429 at 13:00, outcome `hard_block`, sender paused with reason, one notice; nothing sent from 13:00 until the scripted resume at 08:00 on day 9; on every other day `sent == start_limit` (the queue is kept fuller than the limit).
  - `test_two_weeks_per_company_spacing`: for every company and New York date, at most 3 sends and every gap at least 60 minutes.
  - `test_two_weeks_recruiter_timing`: at the company with an engineer and a recruiter approved at the same minute, the engineer is sent first and the recruiter at least 60 minutes later.
  - `test_two_weeks_window_and_spacing`: every send is inside 08:30 to 16:30 in the recipient's zone on a weekday, and consecutive sends are at least 4 minutes apart.
  - `test_two_weeks_follow_up_dates`: every first email sent on 2026-11-02 has a follow-up due 2026-11-11; the follow-ups drafted and approved on 2026-11-11 are sent on or after that date, in the first email's thread, with `In-Reply-To` equal to its Message-ID, and count toward that day's limit.
  - `test_two_weeks_company_hold_on_reply`: after the reply at 10:00 on day 7, no other send at that company happens, its approved messages are still `approved` at the end, and the replier's planned follow-up row is gone with a `skipped_by_sender` touch event.
  - `test_two_weeks_no_double_send`: every Message-ID in `FakeGmail.sent` is unique, and no message was accepted by the fake twice, including the one whose send hit the 429.
  - One test per stop condition, each a short scenario on the same simulator: `test_stop_bounce_rate_warning`, `test_stop_hard_bounce_rate_pauses`, `test_stop_policy_rejection_warning`, `test_stop_gmail_429_pauses`, `test_stop_account_blocked_pauses`, `test_stop_one_stop_reply_warns_and_two_pause`, `test_stop_said_no_list_never_sends` (a person on `no_go` with an approved message: never sent, message `skipped`), `test_stop_signed_out_sends_nothing_and_is_not_a_hard_block`.
  - `test_simulation_is_deterministic` (same seed, same sends).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sending_simulation -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `simulate.py`. The whole file of tests must run in under 30 seconds on his Mac; if it does not, jump the clock more, never cut a scenario.
- [ ] **Step 4: Run** the same command, then `uv run python -m outreach.sending simulate` and read the table. Expected: PASS; ten day lines matching the first test.
- [ ] **Step 5: Commit** `git add outreach/sending/simulate.py tests/test_sending_simulation.py` then `Sending: two simulated weeks show the ramp, spacing, recruiters, follow-ups, holds and every stop rule (#14)`.

## Task 16: The status snapshot and the schedule for #11 and #15

**Files:**
- Create: `outreach/sending/status.py`
- Test: `tests/test_sending_status.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class SendingSnapshot`: `state` (`running`, `paused_by_you`, `paused_hard_block`, `outside_window`, `weekend`, `signed_out`: the web `SenderState` values; `wrong_account` and `old_sender_running` show as `signed_out` and `paused_by_you` with a reason), `paused_reason: str`, `paused_at: str | None`, `gmail_message: str | None` (the newest `gmail_limit` or `gmail_policy` event detail today), `sent_today: int`, `limit_today: int` (effective), `start_limit: int`, `ramp_level: int` (1-based position of `start_limit` in `ramp_levels`), `ramp_levels: tuple[int, ...]` (20 to 100 by 10), `next_limit_if_clean: int`, `last_step_down: tuple[str, str] | None` (day and outcome note of the newest `warning` or `hard_block` day), `bounce_rate_3d: float`, `gmail_errors_today: int`, `stop_replies_today: int`, `imported_unverified: int` (`import_not_in_sent` events), `next_send: tuple[int, str, str] | None` (message id, UTC time, zone), `none_reason: str | None`, `revision: int` (`sender.revision`).
  - `sending_snapshot(conn, now: datetime, cfg) -> SendingSnapshot` (read only; `limit_for` when today has no row yet).
  - `queue_schedule(conn, now: datetime, cfg) -> dict[int, Verdict]` (the same `verdicts` the tick uses, so the Outbox's "when it will go" is the sender's own answer).

- [ ] **Step 1: Write the failing tests:** `test_snapshot_of_an_empty_store` (state by clock, `limit_today` 20, `next_send` None, `bounce_rate_3d` 0.0); `test_snapshot_states_per_pause_kind_sign_in_and_clock`; `test_ramp_level_and_next_limit` (start 40 is level 3, next 50; at 100 next stays 100); `test_snapshot_limit_matches_the_ramp_before_the_first_tick` (no row today: `limit_for`); `test_schedule_reason_and_time_for_each_wait` (one per time-based reason); `test_snapshot_never_writes` (`total_changes` unchanged).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_sending_status -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add outreach/sending/status.py tests/test_sending_status.py` then `Sending: the snapshot and queue schedule the API and the plan read (#14)`.

## Task 17: The `send` stage in the pipeline flow test

**Files:**
- Create: `tests/pipeline/stages_sending.py`
- Modify: `tests/pipeline/harness.py` (`STREAM_STAGES` and its docstring table, only if they do not yet hold the final map below), `tests/pipeline/test_flow.py` (one `@invariant("send")`, and the agreed-map test only with the map)

**The final wave 2 map** (integration review, section 5). The first wave 2 PR to merge (#14, #16, #17 or #13) adds all four wave 2 streams, their docstring rows and `test_streams_and_their_stages_are_the_agreed_map` at once, in this order; the harness skips a stream whose `stages_<stream>` module does not exist yet. A later PR finds the map in place and adds only its stage file and invariant, so the four plans never conflict on this dict:

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
- Produces: `stages_sending.register(harness)` appends `("send", fn)`. `fn` seeds its own fixture company "Sendwell Labs" with a founder (`founder@sendwell.example`) and a `daily_list_entry` for 2026-11-10 with `channel 'email'` (creating the `daily_list` row with `ON CONFLICT DO NOTHING` and the next free rank), a second person with a drafted, never approved message, and approves the founder's first email (test seed SQL standing in for his click); then runs `run_tick` on a `FakeGmail` from 09:00 New York on 2026-11-10 for six ticks two minutes apart. It records the ids it made on the harness (`harness.sending_ids`).
- The invariant `send_sends_only_what_was_approved_once(h)`: the founder's message is `sent` and appears once in the fake's `sent`; the drafted message was never sent; the founder's touch has one `outreach_touch` (channel `email`, `context.source == 'plan'`); a follow-up `planned` with `due_on` 2026-11-19; no other message in the store changed state.

- [ ] **Step 1: Write the invariant and the stage module.**
- [ ] **Step 2: Run** `uv run python -m unittest tests.pipeline.test_flow -v` before the harness edit. Expected: FAIL (`send` is not a pipeline stage), or, when the map is already on master, FAIL only on the new invariant until the stage module exists.
- [ ] **Step 3: Edit** `harness.py` to the final map above when master does not have it yet.
- [ ] **Step 4: Run** the same command and the whole suite. Expected: PASS.
- [ ] **Step 5: Commit** `git add tests/pipeline/stages_sending.py tests/pipeline/harness.py tests/pipeline/test_flow.py` then `Tests: the send stage in the pipeline flow test (#14)`.

## Task 18: Mutation check and docs

**Files:**
- Modify: `tests/mutation_check.py` (a `SENDING = ROOT / "outreach" / "sending"` path and the entries below), `docs/README.md` (a Current row for this plan)

**Interfaces:**
- Produces mutations, each a plausible simplification with the test that must catch it (anchors are exact text from the finished code; a missing anchor is SKIP and fails the run):
  1. `claim.py`: the claim moved after `client.send` (`test_send_is_claimed_with_its_message_id_before_the_call`).
  2. `gmail.py`: a To plus Subject fallback in `find_sent` (`test_find_sent_matches_by_message_id_only`).
  3. `claim.py`: a miss counted as proof when the scan was incomplete (`test_not_found_needs_a_scan_back_to_the_claim`).
  4. `claim.py`: the attempt cap removed (`test_requeue_stops_after_two_attempts`).
  5. `mail.py`: `bad_request` added to `RETRYABLE` (`test_bad_request_failure_is_not_requeued`).
  6. `ramp.py`: a warning day rises like a clean one (`test_warning_day_does_not_rise`).
  7. `signals.py` or `inbound.py`: the clean test becomes `<=` 2% (`test_bounce_rate_of_exactly_two_percent_is_a_warning`).
  8. `ramp.py`: `min(..., ramp_ceiling)` removed (`test_ramp_stops_at_100`).
  9. `queue.py`: the per-company gap compared with `<=` or dropped (`test_second_send_at_one_company_waits_60_minutes`).
  10. `queue.py`: the hold check removed (`test_reply_holds_every_queued_message_at_the_company`).
  11. `queue.py`: `canonical_company` replaced by the person's own company (`test_person_at_merged_company_sends_under_the_survivor`).
  12. `classify.py`: the delay check removed (`test_delay_notice_is_not_a_bounce`).
  13. `classify.py`: `Auto-Submitted` ignored (`test_out_of_office_header_is_not_a_reply`).
  14. `tick.py`: the dry-run rollback becomes a commit (`test_dry_run_writes_nothing`).
  15. `__main__.py`: the `require_live` line removed (`test_run_refuses_outside_the_live_checkout`).
  16. `tick.py`: the account check removed (`test_wrong_account_sends_nothing`).
  17. `followups.py`: calendar days instead of business days (`test_followup_due_seven_business_days_later`).
  18. `claim.py`: `send` called inside the claim transaction (`test_no_gmail_call_inside_a_transaction`).
  19. `followups.py`: `stop_planned` moves the row to `skipped` instead of deleting it (`test_lapse_deletes_the_planned_row_and_records_it_on_the_touch`; the `message` CHECK also fails `test_reply_with_a_planned_follow_up_does_not_fail_the_tick`).
  20. `ramp.py`: `effective_limit` returns the start limit on a warning day (`test_effective_limit_is_one_level_lower_after_a_warning`).

- [ ] **Step 1: Add the mutations** with anchors read from the finished code.
- [ ] **Step 2: Run** `uv run python tests/mutation_check.py`. Expected: every sending mutation `CAUGHT`, `missed 0 skipped 0`. A `MISSED` line means a test is missing: add it to the owning task's file.
- [ ] **Step 3: Run** the dash check over `outreach/sending`, `outreach/drafts/followups.py`, `launchd`, `tests/test_sending_*.py`, `tests/test_drafts_followups.py`, `tests/sendtest.py`, `tests/pipeline/stages_sending.py`, `docs/README.md` and this plan. Expected: no output.
- [ ] **Step 4: Edit** `docs/README.md` (a Current row: "`superpowers/plans/2026-10-22-sending-engine.md` | #14: the new sender: ramp, spacing, follow-up, holds, stop rules, the two-week simulation") and run the whole suite. Expected: all OK.
- [ ] **Step 5: Commit** `git add tests/mutation_check.py docs/README.md` then `Sending: mutation guards and the docs row (#14)`.

---

## Out of scope

- Writing any email or follow-up text (Claude in the side session, #13 and the cold-email skill) and approving (Devanshu, #11's one route).
- The Outbox, Sending and shell views and their routes (#11, #33); they read Task 16.
- Importing `mail/contacts.json` and the switch runbook (#17), retiring `mail/` (#17 part B).
- Finding or verifying addresses, mail server lookups (#11's `mx`), and who gets emailed at all (#15).
- Reading reply bodies (needs a wider Gmail scope; Pending Devanshu Q7).
- Any second sending account or domain (#9).

## Cross-plan effects

| Where | What changes or is assumed |
|---|---|
| Schema | Migration `0002_sending.sql` is version 2 (Task 1). #17's `0003_import_history.sql` is version 3 and lands after it. `0001_initial.sql` takes no more in-place edits after #31's `company_name_key`. |
| `tests/pipeline/harness.py` | The final wave 2 `STREAM_STAGES` map (Task 17) is added once, by whichever of #14, #16, #17 and #13 merges first; the others add only their stage file and invariant. |
| #11 API Task 18 | Lands after this plan's Task 16. Builds `SendingStatus` from `outreach.sending.status.sending_snapshot` and each item's `Schedule` from `queue_schedule`, instead of its own copy, mapping `ready` to `next_slot`; `signed_out` comes from `sender.auth_state`; drop `[sending] window` and `ramp` from `outreach/api/config.toml` (read `outreach.sending.config.load_config`). The `types.ts` `Schedule.reason` union becomes this plan's `REASONS` minus `ready`, plus `next_slot`: `recruiter_day_2` goes, and `recruiter_waits`, `company_held`, `company_paused`, `followup_not_due`, `sender_paused`, `signed_out`, `old_sender_running`, `bad_timezone`, `not_business_day`, `per_company_gap` and the stop reasons arrive. #33's fixture data follows. |
| #11 API Task 6 | The sender's cadence is 120 s, not 600 s. |
| #11 API Task 9 | `build_context(lane=...)` is typed `lane: str \| None` with a test `test_context_lane_may_be_null` (Task 8 passes None). The sender writes `source 'plan'` for a person on an email plan row, else `own`. |
| #11 API Task 17 | Exports `load_banned(path: Path \| None = None) -> tuple[list[tuple[str, re.Pattern]], bool]` (Task 12 and #13 use it). `run_checks` gains a block `followup_subject`, named in Task 17's code list, for `seq >= 1` whose subject is not exactly `Re: ` plus the first email's subject, so a follow-up always threads for the recipient (#13's `Re: <first subject>` agrees). The sender calls `run_checks` before every claim (Task 12). |
| #11 API Task 19 and #33 (release hold) | #11 Task 19 adds `POST /api/companies/{id}/release-hold {revision}`: clears `held_by_touch` and `held_at`, appends `company`/`hold_released` with actor `devanshu`. #33 adds the button on the held Outbox group. The sender never clears a hold, so without this one "Not now" reply holds every colleague's email at that company for good. It must exist before the switch. |
| #11 API sweeper | `sweep_quiet` should skip email touches whose `touch.state` is `bounced` (a bounce is not "quiet"). |
| #15 daily plan Task 4 | Swap `current_send_limit` for `outreach.sending.ramp.effective_limit` (Task 6: today's limit, one level lower after a warning), not the start limit. Email `committed` counting is unchanged. |
| #13 MCP | The sender creates one `planned` row per entry of `followup_business_days` (one by default); `save_followup` fills it, with subject `Re: <first subject>`. #13's `followups_due` calls `outreach.drafts.followups.due_followups` (Task 7), the one copy of that query; `outreach/mcp` never imports `outreach.sending`. A lapsed or stopped planned row is deleted, so #13 sees `no_such_followup` for it. The sender never writes recipient-facing text. |
| #17 switch | Runbook order: stop the old sender (`launchctl bootout gui/$(id -u)/com.devanshu.outreach-mail-sender`) after its last reconcile, so no contact is `sending` or `send_unknown`; import sent mail as `message.state 'sent'` with `rfc_message_id`, `gmail_message_id`, `sent_at`, and `touch` (`origin 'import'`) with `gmail_thread_id`; never import `approved`; #17 decides follow-ups for imported touches (only ones due on or after the import date; the sender does not backfill, and never plans one for an imported touch); use `@outreach.invalid` placeholder Message-IDs only where the real one is unknown (the sender skips them in reply headers and never looks them up); leave `sender.history_id` empty so the first tick reads recent threads; install `launchd/com.devanshu.outreach-sender.plist.template`; the new ramp starts at 20. The new sender refuses to send while the old job is loaded, so a missed step fails safe. |
| CLAUDE.md, `.claude/settings.json` | Task 14 Step 6 adds the sender to "Only in the live checkout" and adds agent deny rules, in its own commit for his review; #17's switch PR rebases last and keeps these lines. |
| #9 (when this plan merges) | The controller adds one sentence to #9's Sending line: "the last 3 days" in the clean-day rule means the last 3 New York dates that had at least one send (Task 6's `bounce_rate_3d`). |

## Design questions a builder could not settle alone

These are answered in "Decisions" above with defaults; the builder follows them and flags in the PR if code proves one wrong:

- Whether `users.history.list` accepts the `gmail.metadata` scope (Task 4 checks the reference; the 45-day thread read is the fallback either way).
- Whether Gmail returns `snippet` under the metadata scope. If not, every bounce is `dsn_class 'unknown'` and policy rejections cannot be told apart from bad addresses (they still count as bounces and feed both bounce rules).

## Pending Devanshu

Question numbers are the integration review's (section 10). Each line names the default this plan builds and where it lives, so a later answer is a small edit there.

- **Q1. Warning or hard block.** Builds: a Gmail 429 or daily-limit error, an account-level 403, more than 3% bounces over the last 100 first emails (once 10 are sent), or two "Asked to stop" tags in 7 days pause sending and tell him; one policy rejection, one "Asked to stop", or a 3-day bounce rate of 2% or more steps down one level for the day and blocks the next rise. The 3% rule pauses on the first bounce until about 34 emails have gone out, as the current mailer does. Where: Decisions, `signals.py` kinds (Task 6), `SendConfig`.
- **Q7. Gmail scope.** Builds: keep `gmail.send` and `gmail.metadata` (the current token keeps working; no reply text, and a spam-policy bounce cannot be told from a bad address). Alternative: add `gmail.readonly` and sign in again at the switch. Where: `gmail.py` `SCOPES` (Task 4), `classify.dsn_class` (Task 5).
- **Q9. Pace.** Builds: a tick every 2 minutes and 4 to 9 minutes between sends, so about 70 to 100 a day fit and 100 needs recipients in several time zones. Alternative: `gap_minutes = (3, 6)`. Where: `SendConfig.gap_minutes` (Task 2), the template's `StartInterval` (Task 14).
- **Q12. Deny rules (this plan's part).** Builds: deny agent runs of `outreach.sending run` and `auth` in `.claude/settings.json`. Where: Task 14 Step 6, its own commit.
- **Q17. Automatic retry of a proven failed send.** Builds: one retry when Sent proves it did not go out, except a bad request. Where: `max_send_attempts` and `RETRYABLE` (Tasks 2, 3, 9).
- **Q19. After a hard block.** Builds: when he resumes, sending restarts one level below the day it stopped. Where: `ramp.open_day` and `limit_for` (Task 6).
- **Q20. Closing a quiet email thread.** Builds: closed "no reply" 7 business days after the last email. Where: `close_after_business_days` (Task 2), `close_quiet_touches` (Task 11).

Answered, not open (integration review "Already answered"): recruiters (A3: only when nobody else is found, ranked last, no day 2), follow-up drafting (A8: one at about 7 business days; Claude drafts, he approves, the sender sends in the thread), holidays (A6: weekdays only, a holiday list stays an optional config line), small days still ramp (A7: any weekday with sends and no warning counts).

## Self-review

- **Issue #14 rules:** ramp (Tasks 6, 15), per company 3 a day and 60 minutes (Tasks 7, 15), recruiters (Tasks 7, 15; #9's 2026-10-21 line, answered), window and random spacing (Tasks 2, 7, 8, 15), follow-ups in the thread with real reply headers, drafted and approved (Tasks 7, 8, 11, 15; one follow-up per the newer #9 line, configurable to three; a planned row that will never be drafted is deleted with a touch event, so the `message` CHECK never fails a tick), hold on any reply at the company (Tasks 10, 15), stop conditions (Tasks 6, 10, 13, 15), approve checks re-run at send time (Task 12; the approve itself is #11's). Ported fixes from PR #1: claim before send (8), Sent reconcile by Message-ID (9), auto-reply filter (5), token check (13), no Gmail call under a lock (8, 13).
- **Prompt items:** old list to new store and the switch (Decisions, Cross-plan effects, Task 13's old-sender gate); `is_live` guard (Tasks 4, 14); pause switch (Tasks 7, 13, 16); bounce and reply detection (Tasks 5, 10); Gmail limits (Tasks 2, 6, 13: ceiling 100 against Gmail's 500 a day, 429 and limit errors pause); idempotency (Tasks 8, 9, 15); dry run with a fake Gmail (Tasks 3, 13, 15).
- **Done when:** Task 15 runs ten business days with a fake Gmail and clock and asserts the ramp, step-downs, spacing, recruiter timing, follow-up dates, the hold and each stop condition.
- **Names used across tasks:** `SendConfig`, `load_config`, `HARD_CEILING`, `ny_date`, `is_business_day`, `add_business_days`, `business_days_between`, `recipient_zone`, `in_window`, `next_window_start`, `ny_day_bounds`, `Outgoing`, `Sent`, `Profile`, `MessageMeta`, `Added`, `HistoryPage`, `HistoryExpired`, `GmailError`, `classify_http`, `RETRYABLE`, `MailClient`, `FakeClock`, `FakeGmail`, `GmailClient`, `build_mime`, `new_message_id`, `Inbound`, `classify`, `dsn_class`, `match_unthreaded`, `record_signal`, `signal_day`, `sends_on`, `bounce_rate_3d`, `hard_bounce_rate`, `stop_tags_in`, `DayState`, `limit_for`, `effective_limit`, `open_day`, `today_state`, `note_signal`, `notify_once`, `mac_notifier`, `canonical_company`, `Queued`, `Verdict`, `load_queue`, `verdicts`, `pick`, `claim`, `AnotherSendInFlight`, `outgoing_for`, `email_context`, `record_sent`, `record_unclear`, `send_one`, `leftover_claims`, `Unclear`, `unclear`, `reconcile`, `requeue_failed`, `tracked_threads`, `tracked_subjects`, `known_ids`, `read_inbound`, `InboundRead`, `apply_inbound`, `observe_stop_tags`, `verify_imported`, `plan_followups`, `stop_planned`, `due_followups` (in `outreach/drafts/followups.py`, Task 7), `lapse_followups`, `close_quiet_touches`, `sweep_stopped`, `precheck`, `TickDeps`, `TickReport`, `run_tick`, `launchctl_loaded`, `main`, `Scenario`, `simulate`, `SimResult`, `TWO_WEEKS`, `SendingSnapshot`, `sending_snapshot`, `queue_schedule`. Each is defined once, in the task that lists it under Produces (`plan_followups` and `stop_planned` land in Task 8 with Task 11's signatures and get their tests there; Task 11 finishes the module).
- **Proportion:** tasks carry signatures, values and test names; bodies are left to the builder, except the order of the tick's steps, which no signature fixes.
