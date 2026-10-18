# Track A: Outreach Live on Current Code, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cold email sends live Monday Oct 19 08:30 ET on the existing `mail/` and `refresh/` code, with safe ramping, reply handling, address checks, corrected filters and a first ranked list of 50 companies.

**Architecture:** Keep the JSON-file design. One contract commit (Task 1) fixes shared names, paths, clock and fakes. Then four independent streams edit disjoint files: A1a sender (Tasks 2-6), A1b approval (Tasks 7-10), A2 filters (Tasks 11-15), A4 first plan (Tasks 16-17). The lead merges A2, A1b, A1a and deploys (Task 18). Everything is stdlib Python and `unittest`.

**Tech Stack:** Python 3.14 (`/opt/homebrew/bin/python3`), `unittest`, `zoneinfo`, Gmail REST API over `urllib`, Node only where `refresh/*.js` already uses it, vanilla JS in `dashboard/app.js`.

**Spec:** `docs/superpowers/specs/2026-10-17-outreach-system-design.md` (sections 5.6, 5.4, 7 row A1-A4). Priorities and the Track A schedule come from `.context/attachments/3h788r/plan.md`. The v2 rebuild (streams B1 to D2) is out of scope here and needs its own plan per stream.

## Global Constraints

- Python standard library only. Run tests with `python3 -m unittest discover -s tests -t .` from the repo root.
- Gmail scopes stay `gmail.send` and `gmail.metadata` only. `messages.list` with `q=` is not allowed under the metadata scope, so never use it.
- Email is plain text, at most 2 links, no shorteners, no tracking, MX-checked addresses only, one follow-up then stop.
- Ramp by distinct sending days: 20, 30, 40, then 50 a day (test sends excluded). `gapMinutes` `[4, 9]`, `perCompanyDaily` 4, `perCompanyGapMinutes` 60, recipient-local window 08:30 to 16:30 on weekdays, auto-pause above 3% bounces (min 10 sends), `lateReplyDays` 30, sender runs every 5 minutes (`StartInterval 300`).
- A human reply holds every queued touch at that company. Auto-replies (headers `Auto-Submitted` other than `no`, `X-Autoreply`, `X-Autorespond`, `Precedence` of `auto_reply`/`bulk`/`junk`/`list`, `X-Auto-Response-Suppress`, out-of-office subjects) never count as replies.
- No branch edits `dashboard/data.js` or `dashboard/index.html` (the live checkout rewrites them). Rollback is `git revert`, never reset.
- Unknown size is `"unsure"`, never `"small"`.
- Never write em-dashes or en-dashes in code, comments, UI copy or docs. In regexes that must match them in postings, write them as Unicode escapes (code points U+2013 and U+2014), never as the literal characters.
- Fail closed: an ambiguous send, a failed fetch or an unreadable store means do nothing and say so.

## Review Focus

Failure modes the spec implies but a happy-path test would miss. Each has a pinning test in the task named.

1. The sender's own address matching as a substring of someone else's (`dev@gmail.com` inside `xdev@gmail.com`), which would count a stranger's mail as our own and hide a reply. Task 3.
2. A contact with an invalid `tz` string raising inside the window check, which would crash every sender tick and block all sending. Task 4.
3. Two contacts at one company with different spelled names but the same domain (`Retell AI` vs `Retell`), where the pause cascade must still link them. Task 1.
4. Multi-location strings such as `London, UK New York, NY` and ambiguous codes (`Bangalore, IN`, `Toronto, CA`, `Wilmington, DE`). Task 11.
5. A corrupt or half-written `contacts.json`, where the sender must send nothing, report the error and never overwrite the file. Task 6.

## File Structure

| File | Stream | Responsibility |
|---|---|---|
| `mail/store.py` | Task 1 only | Paths, clock, statuses, config defaults, company keys, pause/resume, closed-company lookup |
| `mail/gmail.py` | A1a | Gmail calls: headers for auto-reply detection, caller-supplied Message-ID, recent SENT list, health check |
| `mail/sender.py` | A1a | Reply sync, ramp, spacing, per-company limit, claim-then-send, reconcile, status file |
| `mail/mx.py` (new) | A1b | MX lookup through DoH then `dig`, cached per domain |
| `mail/add.py`, `mail/server.py`, `dashboard/app.js` | A1b | Add guards, approve guard and new actions, Outbox groups and badges |
| `refresh/titles.py`, `refresh/geo.py`, `refresh/size.py` (new) | A2 | `norm_title`, `classify(loc)`, `size_from_lookup` shared by all discovery scripts |
| `refresh/finalcheck.py`, `final_merge.py`, `crawl_boards.py`, `discover_radar.py`, `discover_extra.py`, `apply_seasonal.py`, `refresh.py`, `jsedit.py`, `backfill_unsure.py` (new) | A2 | Filter fixes, failed-fetch handling, report |
| `refresh/enrich_free.py`, `enrich_seat.py`, `rank.py`, `writeback.py`, `enrich_schema.json` (new) | A4 | Evidence gathering, ranking, guarded write-back |
| `tests/helpers.py`, `tests/test_<stream>*.py` | each | One test module per task, no shared edits after Task 1 |

---

### Task 1: Contract commit (lead, Sun 07:30)

**Files:**
- Modify: `mail/store.py`, `mail/gmail.py` (path override only)
- Create: `tests/__init__.py`, `tests/helpers.py`, `tests/test_store.py`

**Interfaces:**
- Produces in `store`: `HOME: str`, `use_home(path: str) -> None`, `path(name: str) -> str`, `set_clock(fn: Callable[[], datetime] | None) -> None`, `clock() -> datetime` (aware), `now() -> str` (ISO seconds), `today() -> date`, `STATUSES`, `CLOSE_REASONS`, `HARD_CLOSE`, `PAUSABLE`, `company_keys(c: dict) -> set[str]`, `same_company(a, b) -> bool`, `pause_company(db, c, why: str) -> list[str]`, `resume_company(db, c) -> list[str]`, `company_closed(db, c) -> str | None`, `company_replied(db, c) -> bool`, `is_test(c, cfg) -> bool`.
- Produces in `gmail`: `DIR` honouring `OUTREACH_GMAIL_DIR`, `use_dir(path: str) -> None`.
- Produces in `tests/helpers.py`: `ET`, `at(y, m, d, h=8, mi=30) -> datetime`, `tmp_home()` context manager (points store and gmail at a temp dir, resets clock), `contact(**kw) -> dict`, `FakeGmail` (below).
- Config defaults replace `dailyCaps` and `gapMinutes`: `rampCaps [[1,20],[2,30],[3,40],[9999,50]]` (meaning: cap applies while prior sending days < first number), `gapMinutes [4,9]`, `perCompanyDaily 4`, `perCompanyGapMinutes 60`, `testAddresses []`, `lateReplyDays 30`.
- Constants: `CLOSE_REASONS = ("referral","interview","no_sponsorship","no_referrals","already_hired","not_now","other","no_reply")`, `HARD_CLOSE = ("no_sponsorship","no_referrals","already_hired")`, `PAUSABLE = ("drafted","approved","followup_ready","followup_approved")`, new statuses `paused`, `sending`, `closed` added to `STATUSES`.
- `FakeGmail` surface (used by Tasks 3, 5, 6): `authorized() -> bool`, `check() -> (bool, str)`, `whoami() -> str`, `send(to, subject, body, from_name, from_addr, thread_id=None, in_reply_to=None, message_id=None) -> dict`, `thread(thread_id) -> list[dict]`, `recent_sent(limit=25) -> list[dict]`, `reply(thread_id, from_addr, subject="Re: hi", **headers) -> None`, attributes `sent: list[dict]`, `fail_mode: None | "before" | "after"`. Message dict shape: `{from, subject, date, internalDate, labels, h}` where `h` maps lowercase header name to value.

- [ ] **Step 1: Write failing tests in `tests/test_store.py`.**
  - `test_use_home_redirects_paths`: after `use_home(tmp)`, `save(...)` creates `tmp/contacts.json` and the repo's `mail/contacts.json` is untouched.
  - `test_clock_override`: `set_clock(lambda: at(2026,10,19))` gives `now() == "2026-10-19T08:30:00-04:00"` and `today() == date(2026,10,19)`; `set_clock(None)` restores real time. `OUTREACH_NOW` env sets the clock at import.
  - `test_company_keys`: `company_keys({"companyId":"retell","email":"jane@retellai.com","company":"Retell AI"}) == {"retell","retellai.com","retellai"}`; gmail address and missing `companyId` give `{"acmeinc"}` for company `Acme Inc.`; `a@eng.foo.co.uk` company `Foo` gives `{"foo.co.uk","foo"}`.
  - `test_same_company_by_domain` (Review Focus 3): `Retell AI` and `Retell` with the same email domain are `same_company`.
  - `test_pause_and_resume`: with a replied contact `r` and drafted `a`, approved `b`, followup_approved `c` at the same company and `d` at another, `pause_company(db, r, "reply")` returns the ids of `a`,`b`,`c` and sets `paused`, `pausedFrom`, `pausedWhy`; `resume_company(db, r)` returns them to `drafted`, `drafted`, `followup_ready` (a fresh approval is required, a reply may change the answer); `d` is never touched.
  - `test_company_closed`: a contact closed with `no_sponsorship` makes `company_closed(db, other_at_company) == "no_sponsorship"`; `no_reply` and `other` return `None`; `not_now` returns `"not_now"` until `closedUntil` (90 days after closing) passes.
  - `test_default_config`: asserts every default above.
- [ ] **Step 2:** Run `python3 -m unittest tests.test_store -v`. Expected: errors (missing names).
- [ ] **Step 3:** Implement in `mail/store.py`. `use_home` rebinds `HOME`, `CONTACTS`, `CONFIG`, `LOCK`; at import it calls `use_home(os.environ.get("OUTREACH_MAIL_HOME", HERE))`. `company_keys` takes the registrable domain as the last two labels, or three when the second-last is in `{"co","com","org","net","ac","gov"}` and the last is a two-letter country code; freemail set `{gmail.com, googlemail.com, yahoo.com, outlook.com, hotmail.com, live.com, icloud.com, me.com, aol.com, proton.me, protonmail.com}` yields no domain key; name key is lowercase alphanumerics only. `closedUntil` is set by Task 9, read here.
- [ ] **Step 4:** In `mail/gmail.py` make `DIR` read `OUTREACH_GMAIL_DIR` and add `use_dir` rebinding `DIR`, `CLIENT`, `TOKEN`. No other change in this task.
- [ ] **Step 5:** Write `tests/helpers.py` and `tests/__init__.py` (the latter prepends `mail/` and `refresh/` to `sys.path`). Run `python3 -m unittest discover -s tests -t .`. Expected: all pass.
- [ ] **Step 6:** Commit `contract: store paths, clock, statuses, company keys, test scaffold`. Create the four stream worktrees from this commit.

---

## Stream A1a: sender (files `mail/sender.py`, `mail/gmail.py`)

### Task 2: Gmail client additions

**Files:** Modify `mail/gmail.py`; Test `tests/test_gmail.py`

**Interfaces:**
- Produces: `new_message_id(from_addr: str) -> str`; `send(..., message_id: str | None = None)` uses it when given; `thread(thread_id)` messages gain key `h` with lowercase headers `auto-submitted`, `x-autoreply`, `x-autorespond`, `precedence`, `x-auto-response-suppress` when present; `recent_sent(limit: int = 25) -> list[{id, threadId, rfcMessageId, to, internalDate}]` via `messages.list?labelIds=SENT` then `messages.get?format=metadata` for `Message-ID` and `To`; `check() -> tuple[bool, str]` (True, address) or (False, error text), never raises.

- [ ] **Step 1:** Tests with `gmail.api` monkeypatched: `test_thread_collects_auto_headers` (a fixture response with `Auto-Submitted: auto-replied` yields `h["auto-submitted"] == "auto-replied"` and the request's `metadataHeaders` includes all five); `test_send_uses_given_message_id` (raw MIME decoded from the posted body has the given `Message-ID`); `test_recent_sent_matches_by_header` (list then get calls, no `q` key in any query); `test_check_reports_invalid_grant` (`api` raising `RuntimeError("invalid_grant")` gives `(False, "invalid_grant")`).
- [ ] **Step 2:** Run, see failures. **Step 3:** Implement the four signatures above. **Step 4:** Run, see pass. **Step 5:** Commit `gmail: auto-reply headers, caller Message-ID, recent_sent, check`.

### Task 3: Reply classification and sync

**Files:** Modify `mail/sender.py` (`sync` 44-58, `business_days_since` 30-35); Test `tests/test_sender_sync.py`

**Interfaces:**
- Consumes: `store.same_company`, `store.pause_company`, `store.company_replied`, `store.today`, `FakeGmail`.
- Produces: `classify(msg: dict, me: str) -> "self" | "bounce" | "auto" | "human"`; `threads_to_check(db, cfg, now) -> list[dict]` (every contact with a `threadId` in `SENT_STAGES`, plus `bounced`/`closed` contacts whose `closedAt` is within `lateReplyDays`); `fetch_threads(contacts) -> dict[str, list[dict] | None]` (network only, never takes the store lock, `None` on error); `apply_threads(db, cfg, me, fetched, now) -> list[str]` (pure on `db`, returns log lines).

- [ ] **Step 1:** Tests. `test_classify_table`: `Auto-Submitted: auto-replied` auto; `Auto-Submitted: no` human; `Precedence: bulk` auto; `X-Autoreply: yes` auto; subject `Out of Office: Re: hello` auto; from `MAILER-DAEMON@x` bounce; subject `Delivery Status Notification (Failure)` bounce; plain message human; own address self. `test_self_address_is_not_a_substring_match` (Review Focus 1): `me="dev@gmail.com"`, message from `Bob <xdev@gmail.com>` is `human`. `test_auto_reply_keeps_status_sent`. `test_human_reply_marks_replied_and_pauses_company` (queued drafts at the same company, found by domain, become `paused`). `test_late_reply_reopens_closed` (closed 10 days ago becomes `replied`; closed 31 days ago ignored). `test_followup_after_4_business_days` (sent Monday 2026-10-19, clock Friday 2026-10-23 gives `followup_ready`). `test_no_followup_while_company_replied`. `test_close_after_followup_silence` (7 days gives `closed` with `closeReason == "no_reply"` and `closedAt`). `test_fetch_threads_never_locks` (patch `store.locked` to raise; call succeeds).
- [ ] **Step 2:** Run, see failures. **Step 3:** Implement. Sender identity uses `email.utils.parseaddr`; bounce check runs before auto; `apply_threads` skips a contact whose status changed since it was selected. **Step 4:** Run, see pass. **Step 5:** Commit `sender: classify replies, late replies, pause cascade, no_reply close`.

### Task 4: Sending policy (ramp, spacing, per-company limit, clock flags)

**Files:** Modify `mail/sender.py` (`cap_today` 68-71, `sent_today` 73-75, `in_window` 77-81, `pick` 83-86); Test `tests/test_sender_policy.py`

**Interfaces:**
- Produces (trailing params optional so `server.state()` keeps working): `send_days(db, cfg) -> set[date]`; `cap_today(db, cfg, now=None) -> int`; `sent_today(db, cfg=None, now=None) -> int` (both exclude `store.is_test` contacts); `company_ok(db, cfg, c, now) -> bool`; `due(db, now) -> bool` (reads `db["nextSendAt"]`); `pick(db, cfg, now=None, send_test=False) -> (contact | None, int)`; `parse_args(argv) -> Namespace(dry_run, at, send_test)`; `--at ISO` calls `store.set_clock`.

- [ ] **Step 1:** Tests. `test_ramp`: 0 prior sending days gives 20, 1 gives 30, 2 gives 40, 3 and more gives 50. `test_test_sends_excluded_from_ramp_and_count` (three days of sends to an address in `testAddresses` still gives cap 20 and `sent_today == 0`). `test_window_edges` (Monday 08:29 ET none, 08:30 picked, Saturday none; a Pacific contact at 11:29 ET none, 11:30 ET picked). `test_invalid_tz_falls_back` (Review Focus 2): `tz="America/Foo"` is treated as `America/New_York` and one line is logged. `test_company_limit` (4 sends to a company today in the contact's tz blocks the 5th, a different company is still picked; last company send 59 minutes ago blocks, 60 allows). `test_next_send_at_range` (after `record`, `nextSendAt - now` is between 240 and 540 seconds with a seeded `sender.RNG`; `due` is false before it). `test_send_test_ignores_window` (Saturday, address in `testAddresses`, `send_test=True` picks it; a normal contact is not picked). `test_parse_args`.
- [ ] **Step 2:** Run, see failures. **Step 3:** Implement; `sender.RNG = random.Random()` module global so tests can seed it. **Step 4:** Run, see pass. **Step 5:** Commit `sender: ramp by send days, spacing, per-company limit, --at, --send-test`.

### Task 5: Claim-then-send and SENT reconcile

**Files:** Modify `mail/sender.py` (`main` 88-124); Test `tests/test_sender_send.py`

**Interfaces:**
- Consumes: `gmail.new_message_id`, `gmail.send(message_id=...)`, `gmail.recent_sent`.
- Produces: `claim(db, c, now) -> None` (status `sending`, `claimedAt`, `claimedFrom`, `pendingRfcMessageId`); `record(db, cfg, c, res, now) -> None` (first send sets `rfcMessageId`; follow-up sets `followupRfcMessageId`; both set `nextSendAt`); `reconcile(db, sent_msgs, now) -> list[str]`; `run_once(now=None, dry=False, send_test=False) -> list[str]`; `main(argv=None)`.

- [ ] **Step 1:** Tests on a simulated clock with `FakeGmail`. `test_normal_send_records_id` (the Message-ID given to `send` equals `rfcMessageId`, status `sent`). `test_timeout_after_delivery_never_double_sends` (`fail_mode="after"`: first run leaves `sending`, second run finds it in `recent_sent`, marks `sent`, and `len(fake.sent) == 1`). `test_failure_before_delivery_blocks_then_fails_closed` (`fail_mode="before"`: contact stays `sending` and no other contact sends within 10 minutes; at +10 minutes it becomes `failed` with error `send not confirmed in Sent`, and the next contact then sends). `test_follow_up_threads_reply` (`in_reply_to` equals the first message's id, `thread_id` set).
- [ ] **Step 2:** Run, see failures. **Step 3:** Implement `run_once` as: reconcile (network outside the lock) then, under the lock, load, apply threads, pause pass, bounce check, pick one, claim, save; then send outside the lock; then under the lock reload and record. If any contact is `sending` and unresolved, send nothing. **Step 4:** Run, see pass. **Step 5:** Commit `sender: claim then send with pre-saved Message-ID, SENT reconcile`.

### Task 6: Health status and unreadable store

**Files:** Modify `mail/sender.py`; Test `tests/test_sender_health.py`

**Interfaces:**
- Produces: `sender_status.json` in `store.HOME` written every run as `{"ok": bool, "error": str | None, "checkedAt": iso}`; `store.StoreCorrupt` raised by `store.load()` on unreadable JSON (never overwrites the file).

- [ ] **Step 1:** Tests. `test_auth_failure_sets_status_and_sends_nothing` (`FakeGmail.check` returns `(False, "invalid_grant")`; status file `ok == False`, error contains `invalid_grant`, `fake.sent == []`). `test_corrupt_contacts_json` (Review Focus 5): garbage in `contacts.json` leaves the file byte-identical, sends nothing, status `ok == False`. `test_status_recovers` (next healthy run writes `ok == True`).
- [ ] **Step 2:** Run, see failures. **Step 3:** Implement; log "not signed in" once per day as today (`main` 89-94). **Step 4:** Run, see pass; run the whole suite. **Step 5:** Commit `sender: status file, fail closed on unreadable store`.

---

## Stream A1b: approval and Outbox (files `mail/mx.py`, `mail/add.py`, `mail/server.py`, `dashboard/app.js`)

### Task 7: MX check

**Files:** Create `mail/mx.py`; Test `tests/test_mx.py`

**Interfaces:**
- Produces: `check_domain(domain: str, resolvers: list | None = None) -> "ok" | "none" | "unknown"`. A resolver is `Callable[[str], list[str] | None]` (`None` means the lookup failed, `[]` means no MX). Defaults in order: Cloudflare DoH (`https://cloudflare-dns.com/dns-query`, `Accept: application/dns-json`), Google DoH (`https://dns.google/resolve`), `dig +short MX`. Cache in `mx_cache.json` under `store.HOME`, 24 hour TTL, `unknown` never cached.

- [ ] **Step 1:** Tests. First resolver `None` and second `["mx.x."]` gives `ok`; first `[]` gives `none` without calling the second; all `None` gives `unknown`; a null MX (`"0 ."`) gives `none`; a second call within TTL does not call any resolver; `unknown` is not cached.
- [ ] **Step 2:** Run, see failures. **Step 3:** Implement. **Step 4:** Run, see pass. **Step 5:** Commit `mx: DoH and dig MX check with cache`.

### Task 8: Add guards

**Files:** Modify `mail/add.py` (`main` 30-46, MX check after line 35); Test `tests/test_add.py`

**Interfaces:**
- Consumes: `mx.check_domain`, `store.company_closed`, `store.company_replied`.
- Produces: `add_contact(c: dict, *, check_mx=mx.check_domain, resolve_company=company_id) -> str` (returns the new id) and `class AddError(Exception)`; `main` turns `AddError` into `SystemExit`. Contact gains `mx: {"status", "checkedAt"}`.

- [ ] **Step 1:** Tests. MX `none` raises `AddError` naming the domain. MX `unknown` adds with `mx.status == "unknown"`. A company closed `no_sponsorship` raises `AddError` containing the reason; closed `no_reply` does not. A company with a replied contact adds the new one as `paused` with `pausedFrom == "drafted"`. Duplicate address (case-insensitive) still refused.
- [ ] **Step 2:** Run, see failures. **Step 3:** Implement. **Step 4:** Run, see pass. **Step 5:** Commit `add: MX check, closed-company refusal, paused start`.

### Task 9: Server actions

**Files:** Modify `mail/server.py` (`state` 22-26, `act` 28-60); Test `tests/test_server.py`

**Interfaces:**
- Consumes: `store.pause_company`, `store.resume_company`, `store.company_closed`, `mx.check_domain`, `sender_status.json`.
- Produces: `POST /api/close {id, reason}` (reason in `CLOSE_REASONS` except `no_reply`; sets `status "closed"`, `closeReason`, `closedAt`; `not_now` also sets `closedUntil` 90 days out; any `HARD_CLOSE` reason also closes every unsent contact at the company); `POST /api/resume {id}` (calls `resume_company`); `POST /api/recheck_mx {id}` (lookup outside the lock, then store the result); `/api/state` gains `nextSendAt`, `auth` (the status file contents), per-contact `mx`.

- [ ] **Step 1:** Tests against `act(path, body)`. Approve refused with errors containing `address not verified` (mx `unknown` or `none`), `paused`, and `company closed`; approve succeeds with mx `ok`. Close with `no_sponsorship` closes the contact and the company's unsent contacts; close with `no_reply` is refused; close from `replied` works. Approve, unapprove, save and skip on a `sending` contact are refused (`cannot change while sending`). Resume returns paused contacts to `drafted`. Recheck updates `mx` without holding the lock (patch `store.locked` to assert it is not held during the lookup).
- [ ] **Step 2:** Run, see failures. **Step 3:** Implement; keep the Origin and content-type checks (`do_POST` 80-85) unchanged. **Step 4:** Run, see pass. **Step 5:** Commit `server: approve guard, close, resume, recheck_mx, state fields`.

### Task 10: Outbox UI

**Files:** Modify `dashboard/app.js` (`OB_GROUPS` 355-361, `obCard` 362+, `mailPost` 351-354)

**Interfaces:**
- Consumes: the Task 9 endpoints and `/api/state` fields.
- Produces: groups `Replied: decide` (status `replied`) and `Paused (company replied)` (status `paused`), a close select with the seven manual reasons, an MX badge (`MX ok`, `MX unchecked` with a Recheck button, `No MX`), Approve disabled unless `mx.status == "ok"`, and a header line `Today N of CAP, next send HH:MM` plus a red banner when `auth.ok` is false.

- [ ] **Step 1:** Seed a temp mail home with one contact per status (`OUTREACH_MAIL_HOME=/tmp/outbox-seed`), start `python3 mail/server.py` and the `outreach-dashboard` launch config.
- [ ] **Step 2:** Implement the changes above. **Step 3:** Verify in the browser: each group shows its contacts, Approve is disabled for `mx unknown`, closing a replied contact moves it to the closed group, the auth banner appears when `sender_status.json` has `ok: false`. Save a screenshot to `.context/outbox.png`. **Step 4:** Commit `dashboard: Outbox groups, close select, MX badge, auth banner`.

---

## Stream A2: filters (files under `refresh/`)

### Task 11: Shared modules and importable scripts

**Files:** Create `refresh/titles.py`, `refresh/geo.py`; Modify `refresh/finalcheck.py`, `refresh/final_merge.py`, `refresh/crawl_boards.py`, `refresh/discover_radar.py`, `refresh/discover_extra.py`; Test `tests/test_geo.py`, `tests/test_importable.py`

**Interfaces:**
- Produces: `titles.norm_title(t: str) -> str` (turns `member of (technical|product|engineering) staff` into `MTS`, case-insensitive); `geo.classify(loc: str | None) -> "us" | "non_us" | "unknown"`. Rules: a US state code (upper case, `, ST` or ` ST` at a token boundary) or `United States`/`USA`/`US` means `us`, except `IN`, `CA`, `DE` count only when no non-US word is present; non-US words match on word boundaries and `new mexico` never counts as Mexico; any US marker anywhere in a multi-location string wins; `remote` alone is `us`, `remote` plus a non-US region is `non_us`; a known US city list (extend the existing `USOK` and `_US_WORDS` lists with `indianapolis`, `albuquerque`) means `us`; empty or unrecognised is `unknown`.
- Callers drop on `non_us` only (finalcheck `analyse` 108, discover_radar 129, discover_extra `in_lane` 120); `crawl_boards` keeps only `us` (replaces `US.search`). Delete the local `NONUS`, `USOK`, `_NON_US_PLACE`, `_US` copies. `finalcheck.py` and `final_merge.py` get a `main()` guard so importing them reads no files; keep the `OFFLANE=` assignment text intact because `crawl_boards.py` 62-63 slices it with `exec`.

- [ ] **Step 1:** Tests. `test_classify_table` with expected values: `Indianapolis, IN` us; `Albuquerque, NM` us; `Albuquerque, New Mexico` us; `Paris, TX` us; `Paris, France` non_us; `Bangalore, IN` non_us; `Toronto, CA` non_us; `Berlin, DE` non_us; `San Jose, CA` us; `Wilmington, DE` us; `Lima, OH` us; `London, ON` non_us; `London, KY` us; `Remote - US` us; `Remote - EMEA` non_us; `Remote` us; `Hyderabad, India` non_us; `London, UK New York, NY` us (Review Focus 4); `Anywhere` unknown; empty unknown. `test_norm_title`: `Member of Technical Staff, AI` becomes `MTS, AI`. `test_scripts_import_without_work_files`: `import finalcheck, final_merge, crawl_boards` succeeds in a temp cwd with no `refresh/work`.
- [ ] **Step 2:** Run, see failures. **Step 3:** Implement. **Step 4:** Run the suite, see pass. **Step 5:** Commit `refresh: shared geo and title helpers, importable scripts`.

### Task 12: Sponsorship negation

**Files:** Modify `refresh/finalcheck.py` (`NOSPON` 66-68, `YESSPON` 69-70, `analyse` 130); Test `tests/test_sponsorship.py`

**Interfaces:** Produces `finalcheck.sponsor_flags(text: str) -> tuple[bool, bool]` (no, yes). `yes` is false when the matching sentence also matches a negation. `analyse` uses it for `noSponsor` and `yesSponsor`.

- [ ] **Step 1:** Tests (expected `(no, yes)`): `We are unable to provide visa sponsorship at this time.` gives `(True, False)`; `We do not offer visa sponsorship.` `(True, False)`; `Visa sponsorship is not available for this role.` `(True, False)`; `Candidates must be authorized to work in the US without sponsorship.` `(True, False)`; `We will sponsor H-1B for the right candidate.` `(False, True)`; `We provide visa sponsorship for qualified candidates.` `(False, True)`; text with neither `(False, False)`.
- [ ] **Step 2:** Run, see failures. **Step 3:** Add `unable to (provide|offer)` and `sponsorship (is )?(not available|unavailable)` to `NOSPON` and implement the sentence-level rule. **Step 4:** Run, see pass. **Step 5:** Commit `finalcheck: sponsorship negation read as no, not yes`.

### Task 13: Member of Technical Staff and level patterns

**Files:** Modify `refresh/crawl_boards.py` (`TITLE_ENTRY` 26-27, `BODY_ENTRY` 29-31, `crawl` 123-124), `refresh/finalcheck.py` (`L2_T` 42), `dashboard/app.js` (`tierOf` 94-95); Test `tests/test_levels.py`

**Interfaces:** Produces `crawl_boards.title_ok(title: str, loc: str) -> bool` (in lane, `SOFT`, `BAD`, `OFFLANE`, geo `us`, all evaluated on `titles.norm_title(title)` for `BAD` and `SEN`). `finalcheck.tier` is unchanged in signature.

- [ ] **Step 1:** Tests. `title_ok` true for `Member of Technical Staff`, `Member of Technical Staff - Backend`, `MTS, Platform`, `Founding Member of Technical Staff`, each with location `San Francisco, CA`; false for `Senior Member of Technical Staff`, `Staff Software Engineer`, `Principal MTS`. `entry_shaped(title, "")` is `"title"` for `Software Engineer II`, `Software Engineer (II)`, `Software Engineer - 2`, `SDE 2`, `Software Engineer I`. `entry_shaped("Software Engineer", body)` is `"posting text"` for bodies containing `3 years of professional experience` and `3+ years of experience`, and `None` for `5+ years`. `finalcheck.tier("Member of Technical Staff", "", None) == 4`, `tier("Software Engineer II", "", None) == 4`, `tier("Software Engineer", "", 5) is None`, `tier("Software Engineer, New Grad", "", None) == 1`.
- [ ] **Step 2:** Run, see failures. **Step 3:** Change `TITLE_ENTRY` to accept `(i|1|ii|2)`, `BODY_ENTRY` to accept `[1-3]\+?\s*years` of experience wording, add `MTS|member of technical staff` to `L2_T` and to the `tierOf` level-2 regex. **Step 4:** Run, see pass. **Step 5:** Commit `filters: MTS and SWE II pass, up to 3 years accepted`.

### Task 14: Unsure size default and backfill

**Files:** Create `refresh/size.py`, `refresh/backfill_unsure.py`; Modify `refresh/discover_radar.py` (138, 144), `refresh/discover_extra.py` (193, 199), `refresh/apply_seasonal.py` (59-62), `refresh/jsedit.py`; Test `tests/test_size.py`

**Interfaces:**
- Produces: `size.size_from_lookup(lk: dict | None) -> tuple[str, str] | None` (returns `("unsure", why)` for `None` or a collision, `("small", why)` for verified `n <= 30`, `None` for `n > 30` meaning the caller drops the company); `jsedit.get_field(src: str, cid: str, key: str) -> object | None`; `backfill_unsure.plan(src: str) -> list[str]` (ids whose `sizeClass` is `small` and whose `sizeWhy` starts with `no filing record` or `the visa-data name match`); CLI dry run by default, `--apply` copies `dashboard/data.js` to `refresh/backups/data_pre_unsure.js` first and sets `sizeClass "unsure"` plus a new `sizeWhy` through `set_field`.

- [ ] **Step 1:** Tests. `size_from_lookup(None)[0] == "unsure"`; collision `"unsure"`; `{"verified": True, "n": 5}` gives `("small", "5 lifetime visa filings, consistent with under 500")`; `n=31` gives `None`. `plan` on a three-entry fixture string (default-reason small, verified small, big) returns only the first id; applying twice changes nothing the second time and leaves the other entries byte-identical.
- [ ] **Step 2:** Run, see failures. **Step 3:** Implement and replace the hard-coded `"small"` defaults at the listed lines with `size_from_lookup`. **Step 4:** Run, see pass. **Step 5:** Commit `refresh: unsure size default and backfill script`.

### Task 15: Failed fetch is not an empty board, and the report says so

**Files:** Modify `refresh/finalcheck.py` (`board` 134-162, `work` 174-195), `refresh/final_merge.py` (55-57), `refresh/refresh.py` (`snapshot` 32-40, `main` 42-77, `write_report` 79-110); Test `tests/test_failed_fetch.py`

**Interfaces:**
- Produces: `finalcheck.board(t) -> tuple[list, bool]` (rows, failed; failed on non-200, exception or malformed JSON); `work` sets `fetchFailed` and leaves `boardEmpty` false when failed; `final_merge.merge_all(R, E, T) -> tuple[dict, Counter]` skips any company whose `fetchFailed` is true and whose eligible list is empty, so `write_level.js` leaves its previous level untouched; `refresh.format_report(before, after, failed: list[str], error=None) -> str` adds a section `Fetch failures (N), left as they were` and a reason after each name under `Nothing open for you any more`; `refresh.py` writes `refresh/work/refresh.lock` (pid) at start and removes it in a `finally`, which Task 17 reads.

- [ ] **Step 1:** Tests with `finalcheck.get` patched: a 503 for Ashby gives `([], True)`; a 200 with `{"jobs": []}` gives `([], False)`; garbage JSON gives `([], True)`. `merge_all`: failed with no jobs is absent from the output; empty and not failed gives `noRoleInBand True`. `format_report` contains both new sections for fixture input.
- [ ] **Step 2:** Run, see failures. **Step 3:** Implement; have `snapshot()` also read `c.noRoleWhy`. **Step 4:** Run the suite, see pass. **Step 5:** Commit `refresh: failed fetch kept as-is, report lists failures and reasons`.

---

## Stream A4: first plan (files `refresh/enrich_*.py`, `rank.py`, `writeback.py`)

### Task 16: Evidence extraction and seat runner

**Files:** Create `refresh/enrich_free.py`, `refresh/enrich_seat.py`, `refresh/enrich_schema.json`; Test `tests/test_enrich.py`

**Interfaces:**
- Fact line (append-only JSONL in `refresh/work/enrich/facts.jsonl`): `{id, field, value, quote, source_url, source_kind, at}`; `field` is one of `domain`, `headcount`, `stage`, `raise`, `hq`; `source_kind` is `posting`, `homepage`, `yc`, `seat`.
- Produces: `enrich_free.extract_facts(text: str, url: str, kind: str) -> list[dict]`; `enrich_free.guess_domain(name: str, fetch) -> dict | None` (tries `<name>.com`, `.ai`, `.io`; accepts only when the page title or text contains the company name); `enrich_free.yc_lookup(name: str, index: list[dict]) -> dict | None` (exact normalised-name match against the yc-oss `all.json`); `enrich_seat.pending(companies, facts) -> list[dict]` (companies with no `seat` fact and at least one missing field); `enrich_seat.build_prompt(batch: list[dict]) -> str`; `enrich_seat.validate_card(card: dict, company: dict) -> list[str]` (errors; every value needs a non-empty quote and a `source_url` that appears in `card["sources"]`); CLI flags `--max-calls` (default 8), at most 3 concurrent, 10 companies per call, resumable.
- The `claude -p` flags come from the attached plan and are unverified: Step 4 checks them against `claude -p --help` and the 09:30 `reply ok` run before any batch runs, and adjusts `CLAUDE_CMD`.

- [ ] **Step 1:** Tests. `extract_facts("We just raised a $30M Series B led by Sequoia.", u, "posting")` returns `stage "Series B"` and `raise "$30M"` each with the whole sentence as `quote`; `"We're a team of 18 engineers"` returns `headcount "18"`; `"Join our 45-person team"` returns `"45"`; `"Backed by Y Combinator"` returns `[]`. `guess_domain` accepts a page containing the name and rejects one that does not. `validate_card` returns an error for a value with no quote and for a `source_url` missing from `sources`, and `[]` for a clean card. `pending` skips ids that already have a seat fact. Re-running the writer does not duplicate lines.
- [ ] **Step 2:** Run, see failures. **Step 3:** Implement. **Step 4:** Verify the CLI flags, set `CLAUDE_CMD` to `env -u ANTHROPIC_API_KEY claude -p --output-format json --json-schema <schema> --tools "WebSearch,WebFetch" --permission-mode dontAsk --safe-mode --no-session-persistence --max-turns 60` or its corrected form, and run one batch of 3 companies by hand. **Step 5:** Commit `enrich: free extractors, seat runner with validation`.

### Task 17: Rank and guarded write-back

**Files:** Create `refresh/rank.py`, `refresh/writeback.py`; Test `tests/test_rank.py`, `tests/test_writeback.py`

**Interfaces:**
- Produces: `rank.rank(companies: list[dict], facts: list[dict], today: date, n: int = 50) -> list[dict]`. Eligible: `sizeClass` in `small`/`unsure`, not `noRoleInBand`, not `visaDenied`, no `blockers`. Row: `{id, name, domain, size, stage, role: {title, url}, missing: list[str], growth, why}` where a field counts only when a fact has a quote and a source URL. Sort key: `(bool(missing), -growth, entryTier, start2027, -velocity, name)`; `growth` is 3 when a raise is within 180 days, 2 within 365, 1 older, 0 unknown. CLI writes `refresh/work/first_plan.json` and `first_plan.md`.
- Produces: `writeback.plan_writes(companies, facts) -> list[tuple[str, str, str]]` (`domain` to `domain`, `headcount` to `head`, stage plus raise to `funding`), only where the current field is missing, empty or `"unknown"`; `writeback.apply(src: str, writes) -> str` via `jsedit.set_field`; CLI is a dry run by default, `--apply` required, and it refuses when `refresh/work/refresh.lock` names a live pid.

- [ ] **Step 1:** Tests. A company with a Series A 2 months ago and tier 3 outranks one with no raise and tier 1 when both have full evidence; any company with `missing` empty outranks one with missing evidence; `noRoleInBand`, `visaDenied` and `sizeClass "big"` are excluded; a fact without `source_url` leaves the field in `missing`; `n` caps the output. `plan_writes` never proposes a non-empty field; `apply` on a three-entry fixture changes only the targeted fields (other entries byte-identical); a lock file holding `os.getpid()` makes the CLI exit non-zero without writing.
- [ ] **Step 2:** Run, see failures. **Step 3:** Implement. **Step 4:** Run, see pass. **Step 5:** Commit `rank: first plan of 50 with sourced evidence, guarded write-back`.

---

### Task 18: Integration, deploy and live self-test (lead, with Devanshu's approval)

**Files:** none new; Modify the live launchd plist outside the repo.

- [ ] **Step 1 (12:00):** Merge A2, then A1b, then A1a into master. After each: `python3 -m unittest discover -s tests -t .` passes and `python3 mail/sender.py --dry-run --at 2026-10-19T08:30:00-04:00` runs against a temp `OUTREACH_MAIL_HOME`.
- [ ] **Step 2 (13:30, needs his approval):** `git push origin master`. In `/Users/devanshu/Desktop/Sept 23 Companies`: `git status` shows only `dashboard/data.js` and `dashboard/index.html` modified. Then `launchctl bootout gui/$(id -u)/com.devanshu.outreach-mail-sender`, `git pull --ff-only`, set `StartInterval` to 300 in `~/Library/LaunchAgents/com.devanshu.outreach-mail-sender.plist`, `launchctl bootstrap gui/$(id -u) <plist>`, `launchctl kickstart -k gui/$(id -u)/com.devanshu.outreach-mail-server`, `bash dashboard/publish.sh`. Expected: `launchctl list | grep outreach-mail` shows both labels, `curl -s http://127.0.0.1:3002/api/state` returns JSON with `auth`.
- [ ] **Step 3 (14:15, with him):** Self-test with `testAddresses` set in `mail/config.json` to his .edu and a second address: send to the .edu, reply from it and see the colleague draft turn `paused` after the next tick; send an out-of-office style reply and see no status change; close a contact from the UI with `no_sponsorship` and see the company's drafts close. Use `sender.py --send-test` for weekend sends.
- [ ] **Step 4 (14:45):** Run the manual refresh with the new filters. Gate: at most 10 companies newly under `Nothing open for you any more`, each with a reason; the failed-fetch count is shown in `refresh/LAST_RUN.md`; `data.js` loads. Then `python3 refresh/backfill_unsure.py --apply`.
- [ ] **Step 5 (21:30):** `python3 refresh/writeback.py` dry run, review, then `--apply` before Monday 08:00.
- [ ] **Rollback:** `git revert` the merge commits; restore `StartInterval 600`.

### Devanshu's hands-on steps (not agent work)

1. 08:00-09:30: Google Cloud project, enable Gmail API, External audience, add scopes `gmail.send` and `gmail.metadata`, create a Desktop client, save the JSON to `~/Library/Application Support/outreach-mail/client_secret.json` with `chmod 600`, then **Publish app** (Testing tokens die in 7 days). Run `python3 mail/gmail.py auth`, `python3 mail/gmail.py whoami`, `python3 mail/sender.py --dry-run`.
2. 09:30-11:00: `claude /login` to the university org; `env -u ANTHROPIC_API_KEY claude -p "reply ok" --output-format json --safe-mode`; check `/status` shows the seat, not usage billing. Fill the `TODO` lines in `mail/me.md` (past employer and one result, TA work, one or two more projects with links, resume and LinkedIn URLs, signature).
3. 18:30-22:00: review the plan of 50, find at least 20 addresses across at least 5 companies, draft with the cold-email skill, approve in the Outbox.

## Decisions this plan makes that the source plan left open

- Resuming a paused company returns approved touches to `drafted`, so each needs a fresh approval after a reply.
- A send that cannot be confirmed in the SENT label after 10 minutes becomes `failed` and is never retried automatically.
- `--send-test` lets contacts at `testAddresses` bypass the window, weekday, gap and cap checks, so the Sunday self-test works on a weekend.
- `perCompanyGapMinutes` is a flat 60 (the prose says 60 to 90, the config key says 60).
- The seat runner defaults to 8 calls so the one-time pass cannot use up a day's v2 allowance.
- Reconcile reads the last 25 SENT messages by header because the metadata scope cannot search. This is untested against the live API until the Sunday self-test.

## Self-review

- **Spec coverage:** A1 (Gmail sign-in is manual step 1; 5-minute runs Task 18; ramp Task 4; auto-reply filter Task 3; pause-on-reply Tasks 1, 3; close reasons Tasks 1, 9; address check Tasks 7, 8, 9), A2 (Tasks 11 to 15), A3 (manual step 2), A4 (Tasks 16, 17), writeback (Task 17). The source plan's `Contract commit` list maps to Task 1; the Monday 08:30 acceptance is Task 18 plus his evening steps.
- **Type consistency:** `store.*` names in Tasks 3 to 9 match Task 1; `sender.pick`, `cap_today`, `sent_today` keep their old positional signatures so `server.state()` is unaffected; `refresh.lock` is produced in Task 15 and consumed in Task 17.
- **Gaps left on purpose:** The v2 workstreams (B1 to D2) are not planned here. Each needs its own plan once Track A is live and the schema design is settled.
