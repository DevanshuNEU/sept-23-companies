# Current system audit (2026-10-17)

Read-only audit of the outreach dashboard, refresh pipeline and mail sender, to decide what v2
(`docs/superpowers/specs/2026-10-17-outreach-system-design.md`) keeps and what it rewrites.

**Method.** Counts come from `dashboard/data.js` loaded with `global.window={};eval(...)`. The worktree copy, the live
checkout and the published copy in `~/Library/Application Support/outreach-dashboard/` are byte-identical: 1,010
companies, `META.refreshedAt` 2026-10-15T20:25. Scores use `dashboard/app.js` lines 13 to 68 evaluated as written,
with "today" fixed at 2026-10-17. Pipeline evidence comes from the live checkout's `refresh/work/` (the 2026-10-15
20:25 manual run). Regex checks run the pattern text copied out of each file against the 11,223 distinct titles in
`refresh/work/research/simplify.json` (a new-grad list, so senior matches there are mostly false positives).

**Size buckets used below.** Evidence is parsed from `head`, `sizeWhy` ("about N people", "around N to M", "source
estimate ...") and `yc.team`. *Confident fit* means a firm (not "guess") estimate with upper bound 220 or less.
*Confident over* means `sizeClass` big, cleared or agency, or a firm estimate with lower bound above 250. Everything
else counts as *unsure*: no estimate, a guess, or a range that spans 200. LCA filing counts (`apparatus`) are not
counted as headcount; they measure sponsorship history.

## 1. Data quality today

| sizeClass | Count | | Size evidence | Count |
|---|---|---|---|---|
| small | 671 | | `apparatus` present (LCA filings) | 283 (278 above 0) |
| big | 249 | | `head` present / numeric | 512 / 102 |
| unsure | 48 | | any numeric headcount estimate | 484 (364 firm) |
| cleared | 41 | | `formD` | 113 |
| agency | 1 | | `yc` (all with team size) | 32 |
| | | | none of the above | 321 |

| Fit for "about 200 or fewer" | Count |
|---|---|
| Confident fit | 291 |
| Unsure: no estimate | 275 |
| Unsure: guess that fits | 56 |
| Unsure: guess above 250 | 53 |
| Unsure: range spans 200 | 29 |
| Confident over (big 249, cleared 41, agency 1, estimate 15) | 306 |

`sizeClass: "small"` is the default whenever the LCA lookup finds nothing ("no filing record: most likely an early
startup", `discover_radar.py:144`, `discover_extra.py:199`), and the drop rule was calibrated for 500 people (more than
30 lifetime filings, `discover_radar.py:141`, `discover_extra.py:196`). So "small" today means "not known to be big",
not "200 or fewer". Only 290 of the 671 "small" companies (and 1 "unsure") carry a firm estimate under the v2 cap.

| Roles, visa and stage | Count |
|---|---|
| Live in-band role (`noRoleInBand` unset and `level.levelOk === true`) | 668 |
| ... of which the named role was posted in the last 60 days | 427 |
| `noRoleInBand` / `levelOk === false` / no `level` record | 151 / 141 / 201 |
| `visaDenied` true / `visaConfirmed` true | 22 / 24 (428 more carry an explicit `false`) |
| `optOnly` / blocked by citizenship or clearance (`BLOCKED`, `app.js:16`) | 48 / 22 |
| E-Verify enrolled / none | 309 / 213 |
| `stageClass` (early 35, mid 18, frequent 7, established 53) = `formD` set | 113 |
| `funding` actually researched (not "not researched" or "Unverified") | 100 |
| Any stage evidence (stageClass, yc or researched funding) | 203 of 1,010 |

**Key number: 355 companies** pass size (157 confident, 198 unsure), are not visa-denied, are not blocked, and have
a live in-band role posted in the last 60 days. Using the dashboard's own `sizeClass` instead of the evidence buckets
gives 363. Of the 355:

- Freshness: 120 posted in the last 14 days, 236 in the last 30; median age 23 days.
- Tier of the role to name: early career 120, level not stated 142, SDE2 65, new grad 21 (1 is a 2027 start), SE1 7.
- Only 24 have any stage evidence, so the v2 stage filter (founding hire through Series E) cannot be applied yet.
- 11 are OPT-only, 15 have a confirmed sponsorship verdict, 25 name a role whose page did not render (`unverified`).
- 7 have a live posting whose text matched the no-sponsorship pattern but no hand verdict, so they still rank:
  Eulerity, Figure Lending, Hypercubic, Interco, Tillster, ProductNow, Neural Concept.
- 16 of the "unsure" ones carry a guess above 250 people.

**Seasonal "ahead" candidates** (`ahead()`, `app.js:28`: seasonal flag and nothing open): 35 pass size, visa and
block checks (36 overall), 2 of them repeat hirers. Size: 2 confident fit, 20 guess fits, 4 span 200, 9 guess over.
The seasonal data is one static file covering October and November only (`apply_seasonal.py:19-21`).

### Top 25 of the 355 by the dashboard's `interviewScore`

| # | Company | Score | Size evidence | Role tier | Days | Flags |
|---|---|---|---|---|---|---|
| 1 | Coram AI | 99 | LCA only | SDE2 (MTS) | 4 | |
| 2 | Perpay | 97 | LCA only | New grad | 12 | 2027 start |
| 3 | AiPrise | 90 | YC team 65 | Early career | 43 | |
| 4 | Telnyx | 90 | guess 400 to 500 | SDE2 | 3 | likely over cap |
| 5 | PointOne | 89 | YC team 35 | SDE2 | 8 | |
| 6 | Latent | 89 | YC team 50 | Not stated | 4 | |
| 7 | Collinear AI | 88 | LCA only | Early career (MTS) | 26 | |
| 8 | matx | 88 | ~100 | Early career | 26 | |
| 9 | Yondu | 88 | 7 | Early career (FDE) | 6 | OPT only |
| 10 | DeepScribe | 88 | Built In 201 to 500 | Early career | 4 | likely over cap |
| 11 | Whop | 88 | 51 to 200 | SDE2 (MTS) | 5 | |
| 12 | Prime Intellect | 88 | 1 to 10 | SDE2 (MTS) | 4 | estimate looks stale |
| 13 | Avathon | 87 | LCA only | Early career | 26 | |
| 14 | RoadRunner | 87 | "around 300 to 700" | New grad (FDE) | 12 | likely over cap |
| 15 | Parallel Systems | 87 | LCA only | SE1 | 25 | |
| 16 | Eulerity | 87 | LCA only | Early career | 18 | no-sponsor wording |
| 17 | LangChain | 85 | LCA only (16 filings) | Early career | 11 | |
| 18 | XBOW | 85 | Built In 51 to 200 | Early career | 10 | |
| 19 | Figure Lending | 85 | LCA only | Early career | 10 | no-sponsor wording |
| 20 | Chai Discovery | 85 | Built In 1 to 10 | Not stated | 6 | |
| 21 | XDOF | 85 | 11 to 50 | SDE2 (MTS) | 9 | |
| 22 | Artera | 85 | LCA only | Early career | 10 | |
| 23 | Valkai | 85 | LCA only | Not stated (founding) | 6 | |
| 24 | Study.com | 84 | LCA only | New grad | 30 | |
| 25 | Oratomic | 82 | LCA only | Not stated | 11 | |

What the score misses: 12 of the 25 have no headcount evidence at all; the `reach` part gives 34 points for 1 to 6
LCA filings and 13 for none (`app.js:46`), so it measures sponsorship history, not reachability; nothing penalises a
2027 start, OPT-only, unreviewed no-sponsor wording or a size guess over the cap (`app.js:59-66`). It also saturates:
35 of the 355 score 80 or more.

## 2. Filter logic

| Rule set | Copies (file:line) | Where they disagree |
|---|---|---|
| ENG (engineering title) | `finalcheck.py:27`, `discover_radar.py:37`; stricter title gate `crawl_boards.py:56` (SOFT) | 664 of 3,626 software and AI titles in the corpus pass ENG, are not senior or off-lane, and still fail SOFT ("Compiler Engineer", "Associate Engineer", "Junior Simulation Engineer") |
| SEN (seniority) | `finalcheck.py:28`, `discover_radar.py:38`, `crawl_boards.py:59` (BAD) | finalcheck alone catches "Engineer 3" (30 titles); finalcheck exempts "staff engineer i"; BAD drops any "staff", "mid-level", "robotics", "consultant", "contract" |
| INTERN | `finalcheck.py:29`, `discover_radar.py:39`, inside BAD | wording differs |
| OFF / OFFLANE | `finalcheck.py:30-36`, `discover_radar.py:40-42`, `crawl_boards.py:62-63` (exec of finalcheck's source text), `crawl_more.py:266`, `final_merge.py:19,49` | 48 titles off-lane only for finalcheck, 16 only for the radar/extra copy (it has bare "quality" and "scientist": "Software Engineer - Agentic AI Harness & Quality" is dropped by `in_lane`) |
| Entry level | `finalcheck.py:38-46` and `tier()` 93-102; `crawl_boards.py:26-32`; `crawl_more.py:35,288-289`; `discover_radar.py:35`; `app.js:13-15` and `tierOf` 89-97 | five vocabularies; `level.entryTierName` is stored in two (39 "new grad", 7 "SDE2 or <=3 yrs" next to "New grad", "SDE2") |
| Graduation eligibility | `finalcheck.py:48-62`, `eligibility.py:32-55`, `final_merge.py:9,18` | year and month rules split across two files plus hand lists |
| Citizenship | `finalcheck.py:63-65`, `crawl_boards.py:127` (narrower), `app.js:16` and `build_targets.js:10` (regex over hand-written blocker text) | three different patterns |
| Sponsorship wording | `finalcheck.py:66-70` | reported in LAST_RUN (`refresh.py:91-100`), never applied |
| US / NONUS | `finalcheck.py:71-84`, `crawl_boards.py:34-48`, `crawl_more.py:261-265` plus structured country, `discover_radar.py:43-45,129`, `discover_extra.py:120`, `app.js:208` | crawl keeps "Indianapolis, IN" (state codes), finalcheck then drops it |
| Company size and type | `discover_radar.py:22-33,141`, `discover_extra.py:21-84,196`, `crawl_more.py:268-273`, `app.js:137-141`, `build_targets.js:14`, COLD_TECH `app.js:171-177` parsed by `build_targets.js:7-9` | hand lists in code; 500-person thresholds |

Misfires, each reproduced against the corpus or `data.js`:

1. **MTS is dropped at discovery.** BAD contains `\bstaff\b` (`crawl_boards.py:59`) and runs before `TITLE_OPEN`
   can see "member of technical staff". 27 of the 32 MTS titles in the corpus are dropped by `crawl_boards` and by
   `crawl_more` (which reuses `crawl()`, `crawl_more.py:278`); finalcheck keeps all 27 because it rewrites MTS first
   (`finalcheck.py:104`). MTS roles reach the dashboard only for companies already tracked.
2. **SWE II is under-discovered.** `TITLE_ENTRY` knows only I and 1 (`crawl_boards.py:26`). An "Engineer II" passes only
   through `BODY_ENTRY`, which accepts "1+ years" and "0/1/2 to 3 years" but not "2+ years" or "3+ years" (`:29-31`).
   Of 119 corpus titles with II or 2, `TITLE_ENTRY` matches 34 and finalcheck's `L2_T` 79. `L2_T` (`finalcheck.py:42`)
   still misses "Member of Technical Staff 2" and "Software Engineer Front-End 2".
3. **Staff.** `staff(?! engineer i)` (`finalcheck.py:28`) also exempts "Staff Engineer II", which `L2_T` then rates
   SDE2. And `data.js` still stores 61 req titles with senior words ("Staff Software Engineer, Agent Platform") plus 2
   sentinel rows ("No entry-level req today (...)") on companies the refresh never re-reads.
4. **False senior.** 21 corpus titles hit SEN while also saying new grad or associate, for example "Machine Learning
   Engineer Graduate - Lead Ads" (a team name), "Forward Deployed Engineer Fellow", "AI Fellow - Member of Technical
   Staff", "Backend Engineer, New Grad to Staff Level".
5. **Years of experience.** `YOE` (`finalcheck.py:43`) takes the minimum number near "years": "Bachelor's degree or 4
   years of equivalent experience" gives 4, so `tier()` returns None and final_merge drops the role as "ask 4+ years";
   "founded 5 years ago, our engineering team" gives 5.
6. **Locations.** `NONUS` has no word boundaries and `USOK` has no state codes (`finalcheck.py:71-84`), so
   "Indianapolis, IN", "Columbus, Indiana", "Albuquerque, New Mexico", "Lima, OH", "Paris, TX" and "Athens, GA" are
   dropped as non-US. The corpus contains 6 such places.
7. **Sponsorship wording.** "We are unable to provide visa sponsorship" matches only YES (via "provide visa
   sponsorship"). "Visa sponsorship is not available", "We will not sponsor H-1B petitions", "not eligible for
   immigration sponsorship" and "We do not sponsor." match neither. "We can sponsor visas to Germany" is a YES.
   The last LAST_RUN.md shows exactly this: Beacon AI and Forage listed as "yes?" over "unable to provide" sentences,
   n8n as "yes?" for Germany.
8. **Dashboard junior regex.** `JUNIOR_T` (`app.js:15`) matches any standalone I, 1, II or 2: 19 distinct req titles
   in `data.js` match only that way ("Scientist I/II", "Cellular Layer-1 Control Software...", "Help Desk Engineer -
   Linux Level II").

**Into one shared rules module (config-driven, versioned):** title classification (engineering, seniority, intern,
off-lane, role family including MTS, FDE and founding), level tier and years extraction, graduation eligibility
(`GRAD_MONTH`), citizenship and clearance, a three-valued sponsorship stance with negation handling, US location
(structured country first, then state codes, then a word-bounded non-US list), and company exclusions (big names,
services firms, `SKIP_LEAD`) as data rows rather than code. The dashboard should read stored decisions and classify
nothing itself; `tierOf`, `JUNIOR_T` and `BLOCKED` go away.

## 3. Reliability gaps

| Gap | Evidence | Effect |
|---|---|---|
| A failed fetch looks like an empty board | `finalcheck.py:5-10` returns status 0 on any exception; `board()` swallows parse errors (`:142,151,161`); `final_merge.py:55-57` then writes `noRoleInBand` "no open software role in your lane" | A rate-limit burst silently "closes" companies and LAST_RUN lists them under "Nothing open for you any more". Last run: 82 of 490 board-read companies came back empty; nothing records how many were errors |
| One-shot board liveness | `slugs_live.json` is written only on `--all` (`crawl_boards.py:133,150`) | 7,891 known boards, 2,314 live since Oct 8; 5,577 never retried |
| Coverage is smaller than "36,000" | `slugs_more.json` by ATS; `refresh.py:53` skips Dover; Workable accounts skipped by default (`crawl_more.py:310`) | About 32,400 boards per run (2,314 + 30,074 + the Workable search) |
| All-or-nothing run | `refresh.py:67-70` restores the backup on any step failure | One bad step discards every result; next attempt is the next Mon or Thu slot; stale `work/` files feed the next run |
| Publish outside the guard | `refresh.py:72-76` | A failed publish leaves `data.js` changed, :3001 stale and LAST_RUN.md not rewritten |
| Thin logging | `run()` keeps the last stdout line and drops stderr on success (`refresh.py:24-31`); timestamps have no date (`:23`) | Warnings vanish; the Oct 5 run crosses midnight and reads as going backwards |
| Four in-place writers to `data.js` | `discover_radar.py:166-175`, `discover_extra.py:235-242`, `apply_seasonal.py:74`, `write_level.js:45` | Validation is only "evaluates and every entry has id and name" (`refresh.py:33-35`) |
| Failure is invisible in the UI | The dashboard shows only `META.refreshedAt` (`app.js:238-243`) and never reads LAST_RUN.md | After the 20:25 run a hand prune at 20:45 cut 1,046 to 1,010 companies with no new stamp |
| Runs hostage to sleep | `crawl_boards` took 2 h 32 min (Oct 12) and 2 h 37 min (Oct 15) in scheduled runs, 79 s in the awake manual run | `caffeinate -i` does not keep a closed laptop awake; subprocess timeouts did not fire either |
| Same board fetched up to four times | `crawl_boards`, `discover_extra.check`, `finalcheck`, `eligibility` | 4x rate-limit exposure; steps can see different board states |
| Hardcoded paths and dates | `publish.sh:3` (Desktop path, so a run from this worktree publishes the Desktop copy); `discover_radar.py:12-14` (`radar.db`, `.research/`, present only in the live checkout); `gmail.py:18`; launchd plists (checkout path, nvm node v20.19.5); `final_merge.py:13` `TODAY = 2026-09-29`; `eligibility.py:32`; `apply_everify.py:33`; hand overrides by name and URL `final_merge.py:5-11`; `build_targets.js:7-9` parses `app.js` source | Moves and renames break silently |

**Last refresh.** LAST_RUN.md is the manual run of Thu Oct 15 20:25: success, 1,046 tracked, 95 new (all "Job
board crawl"), 3 closed (Intuit, Provectus, Coco), 0 reopened, 1 role changed, 26 sponsorship wordings left for hand
review. Its log (`work/refresh_manual.log`): `crawl_boards` 79 s, `crawl_more` 1,005 s, `discover_extra` added 95 and
dropped 539, finalcheck read 788 companies and kept 2,051 in-lane jobs. The tail of `refresh/refresh.log` is the
scheduled Oct 15 run (08:52 to 11:31, success, no `crawl_more`, which was added to `refresh.py` that evening). No run
since. Mon Oct 19 08:00 will be the first scheduled run with `crawl_more` (timeout 3,600 s).

**Mail state machine edge cases.**

- Not set up: `~/Library/Application Support/outreach-mail/` does not exist (no OAuth client, no token). `send.log`
  holds three "not signed in" lines (Oct 15, 16, 17). No `mail/config.json`, so defaults apply.
- Any message in the thread not from him counts as a reply (`sender.py:49-53`), including out-of-office replies. The
  metadata call asks only for From, Subject and Date (`gmail.py:108`), so `Auto-Submitted` cannot be checked. Once
  replies pause a company, one auto-reply would pause all of it.
- Replies in a new thread or from another address (a colleague answering on someone's behalf) are missed. Only
  `SENT_STAGES` are polled (`sender.py:23,46`), so replies after `closed` or `skipped` are lost.
- Bounce rate uses a lifetime denominator and a sticky pause (`sender.py:60-66`); a bounce delivered outside the
  thread never counts.
- The lock is held across every Gmail call in `sync` and the send (`sender.py:95-124`). After a month at 200 a week,
  about 800 threads are polled every 10 minutes; Approve clicks on :3002 wait on the same lock (`server.py:29`) with no
  timeout. Reads stay safe (ThreadingHTTPServer, lock-free reads, atomic `os.replace` in `store.py:38-41`).
- `whoami()` sits outside any try (`sender.py:97`): a revoked token produces a traceback in `sender.out` only, while
  the dashboard still shows "signed in" because `authorized()` checks only that `token.json` exists (`gmail.py:27-28`).
- A `failed` send can be re-approved (`server.py:38`); if Gmail accepted the message but the response timed out, it
  goes out twice.
- `add.py` dedupes by address only (`:40`) and sets `companyId` by exact normalised name, else null (`:22-28`), so
  grouping by company is unreliable.

## 4. Mail readiness for about 200 emails a week

**contacts.json today:** 0 contacts, `firstSendDate` null, so there is nothing to break down by status or company.
(The test fixture `refresh/work/mailtest/contacts.json` holds 3 contacts at 1 company: drafted 1, bounced 1, closed 1.)

**(a) Raise caps.** No code change: create `mail/config.json` with `dailyCaps`, for example
`[[7,10],[14,20],[21,30],[9999,40]]`; `store.config()` merges it (`store.py:48-50`). Throughput is the real limit:
one send per launchd run every 600 s, and the gap is redrawn each run from 6 to 14 minutes (`sender.py:103`), so sends
land 10 or 20 minutes apart, about 15 on average: roughly 32 sends in one recipient's 8-hour window. For 40 a day,
set `gapMinutes` to `[4, 9]` in config, or `StartInterval` 300 in the plist. Follow-ups count against the same cap
(`sender.py:73-75`): 200 first emails a week plus their follow-ups could approach 350 sends a week, so decide whether
"200" includes follow-ups.

**(b) Pause the company on a reply.** In `sender.sync()` right after `status = "replied"` (`sender.py:52-53`), move
every contact with the same company key and status `drafted`, `approved`, `followup_ready` or `followup_approved` to a
new `paused` status, with a history line. In the follow-up branch (`:54`) skip contacts whose company has a reply.
Company key: `companyId`, else normalised `company`, else email domain. Elsewhere: `server.py act()` gets
`/api/resume`; `app.js` adds a "Paused, company replied" group to `OB_GROUPS` (`:355-361`) and a banner in
`renderTop` (`:314`); `add.py` starts new drafts for a paused company as `paused`. Request `Auto-Submitted` in
`gmail.thread()` (`gmail.py:108`) so out-of-office replies do not trigger the pause.

**(c) Record a close reason.** Add `/api/close {id, reason}` to `server.py act()` (`:28-60`) with reasons referral,
interview, no_sponsorship, no_referrals, already_hired, not_now, other, writing `closeReason` and `closedAt`; the
automatic close at `sender.py:57-58` writes `no_reply`. No server-side company record exists (pipeline stage lives in
localStorage, `app.js:112-130`), so the minimal company-level step is a `companies` map in `contacts.json` keyed like
(b), plus `jsedit.set_field(..., "visaDenied", true)` on `data.js` for no_sponsorship. `obCard` (`app.js:362`) gets
the close control.

## 5. Keep or rewrite

| Keep (port with small changes) | Rewrite |
|---|---|
| Mail core: `store.locked` plus atomic writes, sender rules (recipient-local window, weekdays, ramp, random gap, one send per run, bounce pause), stdlib PKCE Gmail client with send plus metadata scopes, the origin check on :3002. Move the state to SQLite with the same behaviour | `data.js` as the database, its four in-place writers and `jsedit.py` |
| `cold-email` skill and `add.py` input checks | The ten-step chain passing state through `refresh/work/*.json` |
| ATS adapters in `crawl_more.py` (11 platforms), `polite_get` spacing, the Workable search sweep, structured-country filtering | Error swallowing in every fetch: return a status, tell "empty" from "failed", add per-platform health |
| `finalcheck.analyse()` and `eligibility.eligibility()` as the seed of the rules module, after the fixes in section 2 | Five regex vocabularies across seven files; the dashboard classifying titles itself |
| `lca_lookup` with its state-collision check, USCIS counts, E-Verify and India results, `seasonal.json` as backfill | "small unless proven big" sizing and the 500-person thresholds (`n > 30`, `LARGE_BOARD = 100`) |
| Backup, validate-then-publish, and the LAST_RUN diff report (new, closed, reopened, role changed, wording to review) | `slugs_live.json` one-shot liveness; per-board state instead |
| `interviewScore` parts as inputs, and freshness keyed to the role he would name | The score itself: reach from LCA counts, no penalty for 2027 starts, OPT-only, no-sponsor wording or size doubt |
| Hand data: 46 visa verdicts, `SKIP_LEAD` reasons, `OVERRIDE` and `HAND_START27`, as table rows | Sponsorship wording as a report only; it should be a stored three-valued signal that keeps the company out of the plan |
| | `publish.sh`, `build_targets.js` reading `app.js`, hardcoded dates, localStorage tracking |
