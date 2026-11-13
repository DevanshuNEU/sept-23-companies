# Multi-sender Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Knock sends cold email from several of Devanshu's accounts (a second Gmail through the Gmail API, his Northeastern address through Mail.app), each ramped from 15 a day, inside 08:00 to 15:00 recipient time, one account per company, one follow-up.

**Architecture:** One sender tick keeps running every 2 minutes and loops over accounts. A new `sender_account` table holds each account's state (auth, cursor, spacing, pause); `send_day` is keyed by account; `touch.account_id` and `company_sender` pin every thread and company to one account. Recipient rules (window, company bands, stops) stay global; account rules (daily limit, spacing, auth, proof, pause) become per account. Mail.app is a second `MailClient` behind the same contract.

**Tech Stack:** Python 3.14 standard library + SQLite (STRICT tables, SQL migrations), unittest, FakeGmail; `osascript` for Mail.app; React web app for the Outbox chip.

**Spec:** `docs/superpowers/specs/2026-11-12-multi-sender-design.md`. The file:line references in each task come from a read of the sender on 2026-11-12 (`origin/master` e007183).

## Global Constraints

- #9 is edited first (the multi-sender ruling); code follows it. One branch and PR per phase, base `master`, never stacked.
- Claude never sends, approves, skips or pauses an email, and never runs `python -m outreach.sending run` or `auth`. The Mail.app spike and every sign-in are run by Devanshu.
- Every test runs on scratch settings (`tests/__init__.py`); no test calls Gmail, Mail.app or `osascript`. Mail.app is reached only through an injected runner that tests replace.
- Accounts: `devanshurajesh@gmail.com` (id 1, kind `gmail`, lane none, reads only), `devanshuchi9@gmail.com` (kind `gmail`, lane `general`, start 15, step 3, floor 15, ceiling 40), `chicholikar.d@northeastern.edu` (kind `mailapp`, Mail.app account `Exchange`, lane `sponsor`, start 15, step 3, floor 15, ceiling 60). Display name on every account: `Devanshu Chicholikar`.
- Window: weekdays 08:00 to 15:00 recipient local time. Holidays: 2026-11-26, 2026-11-27, 2026-12-24, 2026-12-25, 2026-12-31, 2027-01-01.
- Spread gap: `minutes_left / (sends_left + 1) * U(0.6, 1.4)`, clipped to `[2, 45]` minutes; breaks and the pick from the first 5 stay.
- Follow-ups: `followup_business_days = [7]`; follow-ups count toward the account's day.
- Hard pause of one account at 2% or more of its last 100 first emails bounced, once 30 are sent.
- `send_day.send_limit` CHECK stays `BETWEEN 0 AND 200`; `HARD_CEILING = 200` stays.
- No em or en dashes in any text a person reads (UI copy, notices, docs, commit messages).
- Tests: `uv run python -m unittest discover -s tests -t .` must pass at the end of every task.

## Review Focus

1. **A send in doubt on one account must never be retried through another account's client.** Every `unclear()` row is settled only by the client of its touch's account. Test in Task A8.
2. **A reply in the resting account (devanshurajesh) must stop mail to that company from the new account.** Company stop reads every account's replies; a failed read of any account that has touches at a company holds that company. Test in Task A9.
3. **A company must never get first emails from two accounts on the same day**, including the moment its pin moves from the resting account. Test in Task A5.
4. **Mail.app not running, or slow, must never block the Gmail account and must never cause a double send.** A Mail.app timeout is a send in doubt settled from Sent Items and Outbox; Gmail keeps sending. Tests in Tasks B2 and B5.
5. **The 15 a day must spread across the window, not go out in the first half hour.** Simulation test in Task A6.

---

# Phase A: accounts on the Gmail API (branch 1, PR 1)

Live result: devanshuchi9@gmail.com sends the general lane from 15 a day; devanshurajesh only reads; sponsor lane emails wait with the verdict `lane_waits` until Phase B.

### Task A0: #9 ruling and docs

**Files:**
- Modify: GitHub issue #9 body (append `.context/issue9-multi-sender-ruling.md` after the job board ruling)
- Modify: `docs/README.md` (list the spec and this plan as current)
- Modify: `CLAUDE.md` live system table only if a job label changes (none in Phase A)

- [ ] **Step 1:** After Devanshu approves the ruling text, append it to #9 with `gh issue edit 9 -R DevanshuNEU/sept-23-companies --body-file <new body>`. Verify with `gh issue view 9 ... | grep "Multi-sender ruling"`.
- [ ] **Step 2:** Open one issue "Multi-sender: accounts, ramp, window, one follow-up (#9 multi-sender ruling)" with this plan's Phase A Done-when lines; open a second for Phase B and a third for Phase C.
- [ ] **Step 3:** Commit `docs/README.md`, the spec and this plan: `docs: multi-sender design and plan`.

### Task A1: Schema migration 0017 (accounts, pins, per-account days, bounced addresses)

**Files:**
- Create: `outreach/store/migrations/0017_sender_accounts.sql`
- Modify: `outreach/mcp/guard.py:44-58,133-138` (new tables in `NEVER_TABLES`; `touch.account_id` in the protected touch columns)
- Test: `tests/test_sender_accounts_migration.py`, update `tests/test_sending_schema.py`

Schema (exact):

```sql
CREATE TABLE sender_account (
  account_id      INTEGER PRIMARY KEY,
  address         TEXT NOT NULL UNIQUE COLLATE NOCASE,
  kind            TEXT NOT NULL CHECK (kind IN ('gmail','mailapp')),
  paused          INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0,1)),
  pause_kind      TEXT CHECK (pause_kind IN ('hard_block')),
  paused_reason   TEXT NOT NULL DEFAULT '',
  paused_at       TEXT,  -- same strftime CHECKs as sender
  history_id      TEXT NOT NULL DEFAULT '',
  next_send_at    TEXT,
  auth_state      TEXT NOT NULL DEFAULT 'unknown' CHECK (auth_state IN ('unknown','ok','signed_out','wrong_account','app_closed')),
  auth_checked_at TEXT,
  auth_error      TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
  CHECK ((paused = 1) = (paused_at IS NOT NULL)), CHECK ((paused = 1) = (pause_kind IS NOT NULL))
) STRICT;
CREATE TABLE company_sender (company_id INTEGER PRIMARY KEY REFERENCES company(company_id),
  account_id INTEGER NOT NULL REFERENCES sender_account(account_id),
  pinned_by TEXT NOT NULL CHECK (pinned_by IN ('rule','you','import')), pinned_at TEXT NOT NULL) STRICT;
CREATE TABLE bounced_address (address TEXT PRIMARY KEY COLLATE NOCASE, first_seen_at TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('bounce','marked','backfill')), source_ref TEXT NOT NULL DEFAULT '') STRICT;
ALTER TABLE touch ADD COLUMN account_id INTEGER REFERENCES sender_account(account_id);
```

`send_day` is rebuilt (the 0012 pattern) with `account_id INTEGER NOT NULL REFERENCES sender_account`, `PRIMARY KEY (account_id, day)`, rows copied with `account_id = 1`.

Backfill: account 1 from `sender` row 1 (`account`, `history_id`, `next_send_at`, `auth_*`); `kind='gmail'`. `touch.account_id = 1` for every touch that sent something: `first_sent_at` set, a non-empty `gmail_thread_id`, or a message in `sent`, `sending` or `send_unknown` (an imported touch that never sent keeps no account and routes by lane; final review, 2026-11-13). `company_sender` from those touches' companies, `pinned_by='import'`, account 1. `bounced_address` from every bounced touch's person email (`source='backfill'`) and from every `email_bounced` event's old address (`$.email.old` as `record_edit` writes it; read one in the test fixture to confirm the path). `sender` row 1 stays as the global pause (his button).

- [ ] **Step 1: Write the failing tests** in `tests/test_sender_accounts_migration.py`:
  - `test_account_one_copies_sender_row`: seed `sender` row 1 with `account='devanshurajesh@gmail.com', history_id='268958'`; after migrate, `sender_account` row 1 has that address, history id and `kind='gmail'`.
  - `test_send_day_keyed_by_account`: a `send_day` row for `2026-11-12` becomes `(1, '2026-11-12')`; inserting `(2, '2026-11-12')` succeeds; a second `(1, '2026-11-12')` raises `IntegrityError`.
  - `test_sent_touches_and_companies_pinned_to_one`: a sent touch gets `account_id=1`; its company gets a `company_sender` row `pinned_by='import'`; an unsent touch keeps `account_id IS NULL`.
  - `test_bounced_addresses_backfilled`: a bounced touch whose person has `a@x.com` and an `email_bounced` event with old `b@y.com` give two rows, sources `backfill`.
  - `test_guard_refuses_new_tables`: the MCP guard refuses an `INSERT INTO company_sender` and an `UPDATE touch SET account_id`.
- [ ] **Step 2:** Run `uv run python -m unittest tests.test_sender_accounts_migration -v`; expect failures on missing tables.
- [ ] **Step 3:** Write the migration and the guard changes; update `tests/test_sending_schema.py` (it pins `sender_id = 1`, which stays true for `sender`).
- [ ] **Step 4:** Run the file, then the whole suite; expect PASS.
- [ ] **Step 5:** Commit `store: sender accounts, per-account send days, company pins, bounced addresses (0017)`.

### Task A2: Migration 0018, one follow-up

**Files:**
- Create: `outreach/store/migrations/0018_one_followup.sql`
- Modify: `outreach/sending/config.toml:72` (`followup_business_days = [7]`)
- Test: `tests/test_one_followup_migration.py`

The migration moves every `message` in `planned`, `drafted` or `approved` with `seq >= 2` to `skipped`, and every such message with `seq >= 1` whose touch has `account_id = 1`, each with an `event` (`kind='skipped'`, `detail={"reason":"rule_changed","ruling":"multi-sender 2026-11-12"}`) and `revision + 1`. It respects the 0005 state trigger (all three moves are legal).

- [ ] **Step 1: Failing tests:** `test_seq2_to_4_skipped`, `test_resting_account_followups_skipped` (a seq 1 on an account-1 touch is skipped, a seq 1 on an account-2 touch stays `planned`), `test_sent_untouched` (a `sent` seq 2 stays `sent`), `test_event_written_per_message`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Write the migration; change the config line.
- [ ] **Step 4:** Run the file and `tests.test_sending_followups`; fix any test that pinned `[3, 7, 14, 21]` to read the config instead.
- [ ] **Step 5:** Commit `sending: one follow-up at 7 business days (0018)`.

### Task A3: Config: accounts, window, holidays, spread pacing

**Files:**
- Modify: `outreach/sending/config.py` (dataclasses and parse), `outreach/sending/config.toml`
- Test: `tests/test_sending_config.py` (create if absent, else extend)

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class AccountConfig:
      address: str; kind: Literal["gmail","mailapp"]; lane: Literal["sponsor","general"] | None
      sends: bool; ramp_start: int; ramp_step: int; ramp_floor: int; ramp_ceiling: int
      mailapp_account: str | None   # Mail.app account name, mailapp only
      token_dir: str | None         # gmail only; None means the default token folder (account 1)
  SendConfig.accounts: tuple[AccountConfig, ...]
  SendConfig.from_name: str           # stays, "Devanshu Chicholikar"
  SendConfig.spread_jitter: tuple[float, float]   # (0.6, 1.4)
  SendConfig.spread_gap_minutes: tuple[int, int]  # (2, 45)
  def SendConfig.account(address: str) -> AccountConfig   # KeyError when unknown
  def SendConfig.lane_account(lane: str) -> AccountConfig | None  # the sending account for a lane
  ```
- `from_address`, `ramp_start`, `ramp_step`, `ramp_ceiling`, `ramp_floor`, `gap_median_minutes`, `gap_spread`, `gap_minutes` go. Every reader is updated in the task that owns it (A4 to A10); in this task `from_address` stays as a property returning the first sending account's address so the suite keeps passing.

config.toml gets three `[[account]]` blocks with the Global Constraints values (`sends = false` and `lane` unset for devanshurajesh; `sends = false` for the Northeastern block until Phase B), `window_start = "08:00"`, `window_end = "15:00"`, the six holidays.

- [ ] **Step 1: Failing tests:** `test_three_accounts_parsed`; `test_two_sending_accounts_in_one_lane_refused` (ValueError naming the lane); `test_mailapp_needs_mailapp_account`; `test_ceiling_over_200_refused`; `test_lane_account_skips_non_sending`; `test_window_and_holidays`.
- [ ] **Step 2:** Run; FAIL.
- [ ] **Step 3:** Implement parse and validation (keys one per line, KeyError naming a missing key, as today).
- [ ] **Step 4:** Run file and suite; PASS.
- [ ] **Step 5:** Commit `sending: accounts, window 08:00 to 15:00, holidays and spread pacing in config`.

### Task A4: Accounts module: sync, lanes, pins

**Files:**
- Create: `outreach/sending/accounts.py`
- Test: `tests/test_sending_accounts.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class AccountRow:  # one sender_account row joined with its AccountConfig
      account_id: int; cfg: AccountConfig; paused: bool; auth_state: str; next_send_at: str | None; revision: int
  def sync(conn, cfg: SendConfig, now: str) -> dict[str, AccountRow]   # inserts missing accounts, never deletes
  def lane_of(conn, company_id: int) -> Literal["sponsor","general"]
  def route(conn, cfg: SendConfig, touch_id: int, accounts: dict[str, AccountRow]) -> tuple[AccountRow | None, str]
  def pin(conn, company_id: int, account_id: int, by: Literal["rule","you"], now: str) -> None
  ```
- `lane_of`: `sponsor` when `company_fact` has `everify = 'enrolled'` or `sponsorship IN ('yes','opt_only')` (also `lca` rows once they exist, `EXISTS (... fact = 'lca')`); else `general`.
- `route` returns, in order: the touch's own `account_id` if set (a follow-up always goes from it; reason `thread`); else the company pin if that account sends (reason `pinned`); else the lane's sending account (reason `lane`); else `(None, "lane_waits")`. A pin to a non-sending account counts as no pin.

- [ ] **Step 1: Failing tests:** `test_sync_inserts_new_account_once`; `test_everify_enrolled_is_sponsor`; `test_opt_only_is_sponsor`; `test_no_facts_is_general`; `test_followup_uses_touch_account_even_if_lane_changed`; `test_pin_to_resting_account_moves_to_lane`; `test_sponsor_lane_without_sending_account_waits` (Northeastern `sends=false` gives `lane_waits`).
- [ ] **Step 2:** Run; FAIL. **Step 3:** Implement. **Step 4:** PASS. **Step 5:** Commit `sending: accounts, lanes and company pins`.

### Task A5: Ramp, signals and counts per account

**Files:**
- Modify: `outreach/sending/ramp.py` (every function takes `account: AccountRow`), `outreach/sending/signals.py:35-95`
- Test: `tests/test_sending_ramp.py`, `tests/test_sending_signals.py` (extend)

**Interfaces:**
- Produces:
  ```python
  ramp.limit_for(conn, account: AccountRow, d: date) -> int
  ramp.effective_limit(conn, account: AccountRow, d: date) -> int
  ramp.open_day(conn, account: AccountRow, now: datetime, cfg: SendConfig) -> DayState | None
  ramp.note_signal(conn, kind: str, *, account: AccountRow, at: str, detail: dict, key: str, cfg: SendConfig, ...) -> None
  signals.sends_on(conn, account_id: int, d: date) -> int          # follow-ups included
  signals.counted_sends_on(conn, account_id: int, d: date) -> int  # bounced emails excluded (as today), follow-ups included
  signals.bounce_rate_3d(conn, account_id: int, now) -> float
  signals.bounce_rate_last_100(conn, account_id: int) -> tuple[int, int]   # (bounced, sent) over the last 100 first emails
  ```
- Signal events use `entity='sender'`, `entity_id=account_id` (account 1 keeps every old event valid). A hard kind pauses that `sender_account` row, never `sender` row 1. New hard kind `bounce_rate_hard`: raised when `sent >= 30` and `bounced / sent >= 0.02`. Notice keys include the account id.
- Step down is `ramp_step` (3), floor `ramp_floor` (15). Clean raises by 3 to `ramp_ceiling`.

- [ ] **Step 1: Failing tests:** `test_new_account_starts_at_15`; `test_clean_day_adds_3`; `test_warning_day_drops_3_not_below_15`; `test_ceiling_40_for_gmail`; `test_accounts_ramp_independently` (account 2 clean, account 3 warning on the same day); `test_followups_count_toward_day`; `test_bounce_rate_hard_pauses_only_that_account` (30 sent, 1 bounce: no pause; 50 sent, 1 bounce: 2% pause; account 3 unaffected); `test_gmail_limit_pauses_only_that_account`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS (update `tests/test_send_limit_200.py` to the per-account world or retire it with a note). **Step 5:** Commit `sending: ramp, signals and hard pauses per account`.

### Task A6: Spread pacing

**Files:**
- Modify: `outreach/sending/human.py`, `outreach/sending/claim.py:260-265` (`_push_gap` writes `sender_account.next_send_at`)
- Test: `tests/test_sending_human.py`, `tests/test_sending_simulation.py`

**Interfaces:**
- Produces: `human.spread_gap(minutes_left: float, sends_left: int, rng: random.Random, cfg: SendConfig) -> float` (minutes). `minutes_left` is computed by the caller (Task A7) as minutes from now to the latest window close, in New York time, among that account's ready recipients today. `sends_left <= 0` returns the high clip.

- [ ] **Step 1: Failing tests:** `test_spread_gap_formula` (minutes_left 420, sends_left 15, rng fixed so factor 1.0: 26.25); `test_spread_gap_clipped` (low 2, high 45); and in the simulation, `test_fifteen_spread_across_window`: one account at 15, recipients split ET and PT, one simulated Monday; assert 15 sent, the first after 08:00 ET, no more than 5 before 10:00 ET, the last after 13:00 ET.
- [ ] **Step 2:** FAIL. **Step 3:** Implement; the break chance and the pick pool are unchanged. **Step 4:** PASS. **Step 5:** Commit `sending: spread each account's day across the window`.

### Task A7: Queue and pick per account

**Files:**
- Modify: `outreach/sending/queue.py:142-365`
- Test: `tests/test_sending_queue.py`

**Interfaces:**
- Consumes: `accounts.route`, `ramp.effective_limit`, `signals.counted_sends_on`, `human.spread_gap`.
- Produces: `queue.verdicts(conn, now, cfg, accounts: dict[str, AccountRow]) -> list[Verdict]` where `Verdict` gains `account: str | None`; `queue.pick(verdicts, rng, account: str) -> Verdict | None` (one account's ready messages only); `queue.minutes_left(conn, now, cfg, account: str) -> float`.
- Per account: `daily_limit`, `spacing` (`sender_account.next_send_at`, `day_start_offset` seeded by account id), `sender_paused` (row 1 global pause, or the account's own pause), `signed_out`. New verdict `lane_waits`. Global, unchanged: company stops, holds, band limits and gaps, window, weekdays, holidays, recipient tz, `first_only`.

- [ ] **Step 1: Failing tests:** `test_each_message_gets_its_account`; `test_daily_limit_per_account` (account 2 at its 15 does not stop account 3); `test_sponsor_waits_when_edu_not_sending` (verdict `lane_waits`); `test_company_band_counts_every_account` (a company in the 2 a day band with 1 first email sent today by any account may get 1 more, not 2); `test_window_0800_1500_recipient_time` (a PT recipient at 07:59 PT waits, at 08:00 PT ready, at 15:01 PT waits); `test_holiday_2026_11_26_waits`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. **Step 5:** Commit `sending: queue and pick per account`.

### Task A8: Claim, proof and reconcile per account

**Files:**
- Modify: `outreach/sending/claim.py` (claim, `outgoing_for`, `send_one`, `record_sent`, `unclear`, `reconcile`), `outreach/sending/proof.py:44-175`, `outreach/sending/precheck.py`, `outreach/mcp/tools/people.py:127-153` (refuse an address in `bounced_address`), `outreach/mcp/tools/corrections.py:214-233` (`mark_email_bounced` also inserts it, `source='marked'`)
- Test: `tests/test_sending_claim.py`, `tests/test_sending_proof.py`, `tests/test_sending_reconcile.py`

**Interfaces:**
- Produces: `claim.claim(conn, msg, account: AccountRow, now, cfg)` sets, in the same transaction, `touch.account_id` (seq 0) and `accounts.pin(..., by="rule")`; `outgoing_for` uses `account.cfg.address` and `cfg.from_name`; `new_message_id(account.cfg.address)`. `claim.unclear(conn, account_id: int)` lists only that account's rows. `proof.phase(conn, account_id)`, `proof.may_claim(conn, account_id)`, `proof.verify_marker(conn, client, account_id, ...)`: `PROOF_SQL` and `MISSING_SQL` join `message -> touch` on `account_id` (account 1's old events still count for account 1; a new account proves itself with its own first email). `precheck` refuses an address in `bounced_address` (code `bounced_address`).

- [ ] **Step 1: Failing tests:** `test_claim_pins_company_and_sets_touch_account`; `test_from_is_account_address`; `test_new_account_needs_its_own_proof` (account 1 proven, account 2 still sends one email alone); `test_unclear_settled_only_by_own_client` (a `send_unknown` on account 2 is never passed to account 1's `find_sent`); `test_bounced_address_refused_at_precheck`; `test_add_people_refuses_bounced_address` (a new person with an address cleared by `mark_email_bounced` is refused with code `bounced_address`); `test_marker_missing_on_account_2_stops_only_account_2`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. **Step 5:** Commit `sending: claims, proof and reconcile per account`.

### Task A9: Inbound per account

**Files:**
- Modify: `outreach/sending/inbound.py:41-411`, `outreach/sending/classify.py:95-131`
- Test: `tests/test_sending_inbound.py`

**Interfaces:**
- Produces: `inbound.read_inbound(conn, client, account: AccountRow, ...)` reads and writes that account's `sender_account.history_id`; `inbound.apply_inbound(conn, batch, account: AccountRow, ...)`; `tracked_threads(conn, account_id)` only that account's touches; `tracked_message_ids` stays global (Message-IDs are unique). `classify(meta, account_address)` unchanged in shape. Every bounce also inserts the address into `bounced_address` (`source='bounce'`, `source_ref` the message id). `inbound_seen` keys gain the account id prefix for accounts other than 1 (`"{account_id}:{gmail_message_id}"`).
- Hold rule: an account whose read was incomplete or failed holds sending to every company that has a touch from that account (verdict `inbound_unread`), and holds that account itself; other companies keep going.

- [ ] **Step 1: Failing tests:** `test_reply_in_resting_account_stops_company_on_new_account` (company X: touch on account 1 replied; a queued first email on account 2 at X gets `reply_stop`); `test_cursor_per_account`; `test_same_gmail_thread_id_in_two_mailboxes_not_confused`; `test_bounce_adds_bounced_address`; `test_failed_read_on_account_1_holds_only_its_companies`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. **Step 5:** Commit `sending: inbound mail per account`.

### Task A10: Tick loop, CLI and tokens

**Files:**
- Modify: `outreach/sending/tick.py`, `outreach/sending/__main__.py:85,145-156`, `outreach/sending/gmail.py:51-52,85-87,304-313,507-523`, `outreach/sending/fakegmail.py` (an `account` per instance is already there), `tests/sendtest.py`
- Test: `tests/test_sending_tick.py`, `tests/test_sending_cli.py`

**Interfaces:**
- Produces: `TickDeps.clients: Mapping[str, MailClient]` (address to client; replaces `client`). The tick: lock, job_run, `accounts.sync`, then per account in config order: profile/auth (wrong_account compares with `account.cfg.address`; notice keys carry the account), leftover claims (global), reads, reconcile with that account's client; one global transaction (step 5 as today, `open_day` per sending account); then for each sending account in random order: gate, `queue.pick(..., account)`, precheck, `send_one`. At most one send per account per tick.
- Tokens: account 1 keeps `<gmail_dir>/token.json`; others use `<gmail_dir>/<address>/token.json`. `python -m outreach.sending auth --account <address>` and `whoami --account <address>`; both stay live-checkout only for the real folder.

- [ ] **Step 1: Failing tests:** `test_tick_sends_one_per_account`; `test_signed_out_account_2_does_not_stop_account_3`; `test_wrong_account_names_the_account`; `test_resting_account_read_not_sent`; `test_auth_requires_account_flag_for_new_accounts` (CLI); `test_token_dir_per_account`. Update `tests/sendtest.py` helpers to pass `clients={...}`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** Full suite PASS. **Step 5:** Commit `sending: one tick over every account`.

### Task A11: Status, API, Outbox, capacity, simulator

**Files:**
- Modify: `outreach/sending/status.py`, `outreach/api/queries/sending.py:80-174`, `outreach/api/queries/outbox.py:49,363`, `outreach/api/queries/people.py:78-79,178`, `outreach/plan/capacity.py:71-90`, `outreach/sending/simulate.py:365,414,446`, web types (regenerated from OpenAPI) and the sending strip component
- Test: `tests/test_api_sending.py`, `tests/test_api_outbox.py`, `tests/test_sending_status.py`

**Interfaces:**
- `SendingStatus` gains `accounts: list[AccountStatus]` with `address, lane, sends, paused, pause_reason, auth_state, sent_today, limit`; the top-level `sent_today` and `limit` become sums; pause and resume stay on `sender` row 1.
- Outbox and people "from" read `touch.account_id`'s address (a draft not yet sent shows the account `route` would pick, or "waits for the Northeastern sender").
- Gmail thread link: `https://mail.google.com/mail/?authuser={address}#all/{thread_id}`; Mail.app touches get no link (Phase B).

- [ ] **Step 1: Failing tests:** `test_status_lists_accounts`; `test_outbox_from_is_touch_account`; `test_thread_link_names_account`; `test_capacity_sums_sending_accounts`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement; web strip shows one line per sending account ("devanshuchi9@gmail.com 4 of 15 today"). **Step 4:** PASS, `cd web && pnpm build` and `pnpm test` pass. **Step 5:** Commit `api: sending status and Outbox per account`.

### Phase A Done when

- Full suite green; CI `CLEAN`; PR base `master`.
- In the live checkout (Devanshu runs the sign-in): `uv run python -m outreach.install` applies 0017 and 0018; `python -m outreach.sending auth --account devanshuchi9@gmail.com`; `whoami --account devanshuchi9@gmail.com` prints it; the status strip shows devanshuchi9 at 15, devanshurajesh reading, Northeastern waiting.
- On the first weekday after deploy: the first email goes alone (proof), then the rest spread to 15; no sponsor lane email goes; no email goes outside 08:00 to 15:00 recipient time.

---

# Phase B: Northeastern through Mail.app (branch 2, after PR 1 merges)

### Task B0: Spike (Devanshu runs it)

- [ ] `osascript .context/mailapp_spike.applescript send devanshuchi9@gmail.com` prints `sent: Knock spike HHMMSS`.
- [ ] `... find devanshuchi9@gmail.com "Knock spike HHMMSS"` prints `found 1` with a Message-ID ending `@northeastern.edu`, and the time it took to appear (note it).
- [ ] `... reply devanshuchi9@gmail.com "Knock spike HHMMSS"` sends; in Gmail the reply sits in the same thread, without quoted text, from "Devanshu Chicholikar".
- [ ] The same three from the launchd context: a one-off plist runs `find` once and logs output, to raise and grant macOS's "control Mail" prompt for `/opt/homebrew/bin/python3`.
- Results go into the spec. If reply threading fails, follow-ups from Mail.app are sent as a new message with subject "Re: <first subject>" (Outlook and Gmail thread on it) and the spec says so.

### Task B1: AppleScript runner

**Files:** Create `outreach/sending/applescript.py`; Test `tests/test_sending_applescript.py`

**Interfaces:** `Runner = Callable[[str, Sequence[str], float], str]` (script text, args, timeout seconds; returns stdout). `osascript_runner(script, args, timeout) -> str` runs `osascript -` with the script on stdin and args after it; raises `GmailError("timeout")` on timeout, `GmailError("app_closed")` when Mail.app is not running (error -600), `GmailError("server")` otherwise; detail scrubbed and at most 300 characters. Records are tab separated, one per line; fields escaped (`\t`, `\n`, `\\`). Add `app_closed` to `KINDS` and `RETRYABLE`.

- [ ] Failing tests with a fake `subprocess.run`: `test_timeout_maps_to_timeout`, `test_app_not_running_maps_to_app_closed`, `test_fields_unescaped`. Then implement, pass, commit `sending: AppleScript runner`.

### Task B2: MailAppClient and FakeMailApp

**Files:** Create `outreach/sending/mailapp.py`, `outreach/sending/fakemailapp.py`; Test `tests/test_sending_mailapp.py`

**Interfaces:** `MailAppClient(account: AccountConfig, runner: Runner, clock)` implements `MailClient`:
- `profile()`: Mail.app account `account.mailapp_account` exists and its `email addresses` include `account.address`; `history_id` is the stored cursor (ISO time) or now.
- `send(m)`: make, set sender `"Devanshu Chicholikar <address>"`, To, subject, content, send; for a follow-up, `reply` to the Sent Items message whose Message-ID is `m.in_reply_to` (B0 result decides). Then poll Sent Items up to 60 s for To = `m.to` and subject = `m.subject` sent at or after the claim; return `Sent(gmail_message_id="mailapp:" + msgid, thread_id=(m.thread_id or msgid), rfc_message_id=msgid, marker=m.marker)`. Not found in 60 s raises `GmailError("timeout")`.
- `find_sent(marker, not_before_ms, *, send_returned_ms, to, subject)`: the protocol gains keyword `to` and `subject` (Gmail ignores them). Hit = exactly one Sent Items message with that To and subject at or after `not_before_ms`. A message still in Outbox means `complete=False`. Two hits mean `complete=False` and a Needs-you item (never sent again).
- `history(cursor)`: every mailbox of the account except Sent Items, Drafts and Outbox; messages received after `cursor - 10 min`; `Added(gmail_message_id="mailapp:" + msgid, thread_id=<root Message-ID from In-Reply-To/References if Knock sent it, else msgid>, labels=("INBOX",) or ("SPAM",) for Junk Email)`.
- `message(id)`: `MessageMeta` from `all headers` (only `META_HEADERS`) and the first 400 characters of `content` as `snippet`; `labels` includes `SENT` for Sent Items.
- `thread(id)`: raises `HistoryExpired`'s sibling `ThreadUnsupported`; the no-cursor path for mailapp scans the last 45 days with `history`.
- `FakeMailApp` keeps the same scripting hooks as `FakeGmail` (`fail_next_send`, `slow_sent`, `deliver_reply`, `deliver_ndr`, `closed`).

- [ ] Failing tests: `test_send_returns_message_id_from_sent_items`; `test_slow_sent_is_send_in_doubt_not_resend`; `test_in_outbox_is_incomplete`; `test_two_hits_never_resend`; `test_history_skips_sent_and_drafts`; `test_junk_labelled_spam`. Implement, pass, commit `sending: Mail.app client`.

### Task B3: Microsoft bounces and replies

**Files:** Modify `outreach/sending/classify.py`, `outreach/sending/inbound.py`; Test `tests/test_sending_inbound_microsoft.py`

A bounce: From matches `MicrosoftExchange[0-9a-f]+@` or `postmaster@`, or Content-Type `multipart/report; report-type=delivery-status`, with subject starting `Undeliverable:`. The failed address: `Your message to (\S+@\S+) couldn't be delivered` in the snippet. The touch: In-Reply-To (already tried first), else the address against `person.email` of a sent touch on that account. Status `5.x.x` is a hard bounce; `4.x.x` is a delay (ignored, as Gmail delay notices are). `5.7.708`, `5.7.705`, `5.1.8` or "restricted from sending" is a provider block: hard pause of that account (`provider_block`). Fixture: the real NDR read on 2026-11-12 (Sept 16, astronomer.io), with the address replaced.

- [ ] Failing tests: `test_exchange_ndr_is_bounce_with_address`; `test_ndr_matched_by_in_reply_to`; `test_4xx_is_delay`; `test_restricted_sender_pauses_account`. Implement, pass, commit `sending: read Microsoft bounce notices`.

### Task B4: Wire the Northeastern account

**Files:** Modify `outreach/sending/__main__.py` (build `MailAppClient` for `kind="mailapp"`), `outreach/sending/config.toml` (`sends = true` for Northeastern), `outreach/api/queries/outbox.py` (no thread link for mailapp), `CLAUDE.md` (Mail.app must stay open; the Automation grant)
- [ ] Failing test `test_tick_with_gmail_and_mailapp` (FakeGmail + FakeMailApp: each sends its lane; Mail.app closed holds only the sponsor lane and raises one Needs-you a day). Implement, pass, commit `sending: Northeastern sends the sponsor lane through Mail.app`.

### Task B5: Simulation over two weeks

**Files:** Modify `tests/test_sending_simulation.py`
- [ ] `test_two_accounts_two_weeks`: 10 business days, both accounts clean: account 2 reaches 40 on day 10; account 3 reaches 42 on day 10; no company gets two accounts; no send outside the window; one Mail.app timeout on day 3 ends `sent` from Sent Items with no second send. Commit `tests: two-account simulation`.

### Phase B Done when

- Spike results written into the spec; suite green; PR clean on `master`.
- Live: Mail.app open, the Automation prompt granted; the first Northeastern email goes alone and is found in Sent Items, then the sponsor lane ramps from 15.

---

# Phase C: Outbox control and the link rule (branch 3)

### Task C1: Account chip and pin change in the Outbox

**Files:** `outreach/api/queries/outbox.py`, a new write route `POST /api/companies/{id}/sender` (his click only; Claude never calls it), web Outbox row component; Test `tests/test_api_outbox_writes.py`, web unit test
- Rule: allowed only while the company has no sent email from a sending account; pins with `by="you"`.
- [ ] Failing tests: `test_change_pin_before_first_send`, `test_change_pin_refused_after_send`. Implement, pass, commit `outbox: show and change a company's account`.

### Task C2: At most one link in a new first email

**Files:** `outreach/drafts/checks.py` (`too_many_links` threshold for seq 0 becomes 1, counting the signature's portfolio), `.claude/skills/cold-email/SKILL.md` (projects named, not linked); Test `tests/test_drafts_checks.py`
- Drafts already approved are not re-checked for links at precheck.
- [ ] Failing tests: `test_first_email_with_project_link_blocks`, `test_signature_portfolio_only_passes`, `test_approved_before_rule_still_sends`. Implement, pass, commit `drafts: one link in a first email, the portfolio`.
