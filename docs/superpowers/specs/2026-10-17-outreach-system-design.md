> **Superseded on 2026-10-18.** Decisions now live in GitHub issue #9 and the current design is
> `2026-10-18-outreach-platform-design.md`. Kept for history; do not build from this file.

# Outreach system v2: design

Date: 2026-10-17. Owner: Devanshu Chicholikar. Status: draft v2 for his review.

Research behind this document, all in `docs/research/`:

| File | What it settles |
|---|---|
| `2026-10-17-current-system-audit.md` | What works today, what is broken, the real counts |
| `2026-10-17-crawl-and-scheduling.md` | ATS APIs, ETags, tiered polling, launchd vs cloud schedulers |
| `2026-10-17-data-lifecycle-and-reliability.md` | Measured data volumes, SQLite settings, retention, sweep, backup, monitoring |
| `2026-10-17-growth-signals.md` | Form D, YC, HN, hiring velocity, pay laws, H-1B wage levels, thesis tagging |
| `2026-10-17-outreach-and-tracking.md` | CRM options, email finding, Gmail rules, LinkedIn rules, research agents |

## 1. Intent

Devanshu is an F-1 OPT new grad (MS Software Engineering Systems, Northeastern, May 2026). His OPT unemployment
clock is stopped (he volunteers with a professor). He wants a job within about a month and will put in whatever
hours it takes. Beyond this search, he wants to work at fast-growing companies betting on technology he believes in.
He is not looking for stability.

**What gets him in the door is a referral, not an application.** Applying through job boards has not worked. His
last real interview (Retell AI, voice AI, 160k+) came from messaging 10 to 20 employees on LinkedIn until one
engineer referred him; the online assessment and interview followed within days. The system exists to repeat that
at volume, pointed at the right companies:

1. **Find the right companies.** About 200 people or fewer, founding-engineer stage through Series E, growing
   (recent raise, rising engineering hiring, hiring season coming), in or near his theses, with a role at his level.
2. **Hand him a weekly list** of about 50, 2 of them conviction bets with deep research, each saying why it is worth
   it, which role to name, and how to reach it.
3. **Make reaching people cheap.** He finds people on LinkedIn and pastes them in; the system drafts cold emails, he
   approves, the sender sends about 200 a week safely. LinkedIn requests (up to about 200 a week, Premium) he sends
   himself.
4. **Learn from outcomes.** The first reply decides a company. "We don't sponsor" or "no referrals" closes it, and
   that fact is remembered so it never resurfaces.
5. **Watch the future, for years.** Every company is tagged to his theses; a trends view shows which theses heat up
   month by month, so he can see where things move and switch when they do.

## 2. Goals, non-goals, anti-goals

### Goals
- G1. Outreach at volume starts within the first 3 days, on the current system, before the rebuild lands.
- G2. One SQLite database holds everything: boards, postings, companies, signals, theses, people, messages,
  applications, briefs, run history. Nothing in browser localStorage, loose JSON or markdown.
- G3. All crawling runs in the cloud on a schedule, so it never stalls when the laptop sleeps or is off. Boards that
  matter are at most about 30 to 60 minutes stale.
- G4. Company filter: about 200 people or fewer; founding-engineer hires (pre-seed, seed) through Series E; US
  anywhere; roles new grad, early career, SWE I, SWE II, founding engineer, MTS, FDE; postings asking up to 3 years.
- G5. Every company's size and stage carries evidence with a source. Unknown is "unsure", shown and marked, never
  silently "small".
- G6. Ranking is growth first, then thesis fit, then role fit and sponsorship evidence.
- G7. Hiring seasonality per company for all 12 months, backfilled from history and recomputed monthly.
- G8. Company research costs at most about $1 to $2 a month: free sources first, then Claude Code on his university
  Enterprise seat, then Haiku 5.5 over the API for pennies. He also does his own research, so short, sourced
  company cards beat long reports.
- G9. One rules module for every filter, with a regression test suite built from real titles and postings.
- G10. Data keeps each company's full hiring history forever in compact form; bulky and irrelevant data is swept
  monthly under guards.
- G11. Every scheduled job reports success or failure to the dashboard and to an external dead-man's switch.
- G12. The Job Radar is merged in and retired; nothing in its history is lost.

### Non-goals
- A postings inbox or a triage queue. He works companies, not postings. (New roles at companies he is already
  working are surfaced as hooks, see 5.6.)
- Scraping or automating LinkedIn in any form (LinkedIn User Agreement 8.2; his account powers his referrals).
- Finding people or email addresses automatically. He supplies them.
- Sending without his approval click.
- Companies over about 200 people, and the Radar's big-employer adapters (Amazon, Oracle, Eightfold, Apple, Google).
- A salary floor. Pay is shown and ranked, never filtered.
- Sending from northeastern.edu. Gmail is the only automated sender; he may use .edu by hand for alumni.
- A hosted CRM (Twenty, NocoDB, Baserow, Teable need Postgres or a schema move).
- Publishing trend write-ups. A later spin-off.

### Anti-goals (failure even if it technically works)
- Silently losing a company or posting: a failed fetch must never look like an empty board (today it does).
- Damaging his Gmail reputation; that inbox is where recruiters answer him.
- Showing stale data as fresh, or hiding a failed job.
- Losing any existing tracking history in migration.
- Personal data (names, emails, notes, replies, verdicts) leaving his machine except in encrypted-at-rest backups
  he controls and API calls he has chosen to make.
- Risking his GitHub account (it is his portfolio): no crawler on GitHub Actions, see 5.3.
- More manual upkeep than today.

## 3. What exists today (from the audit)

- **Outreach dashboard (this repo, live checkout `~/Desktop/Sept 23 Companies`).** `dashboard/data.js` is the
  database (1,010 companies). The twice-weekly refresh reads about 32,400 boards, not 36,000. Scheduled runs stall
  when the Mac sleeps (the main crawl took 2.5 hours on schedule against 79 seconds by hand). A failed fetch looks
  like an empty board, so companies get marked "nothing open". Filters live in seven files in five wordings and
  disagree: discovery drops 27 of 32 "member of technical staff" titles and finds only 34 of 119 "Software Engineer
  II"; "we are unable to provide visa sponsorship" reads as a yes; Indianapolis and Albuquerque read as non-US.
  "Small" is the default when no visa record exists. Hardcoded paths and dates in several files.
- **Real counts.** 355 companies pass size (157 confident, 198 unsure), are not visa-denied or blocked, and have a
  live role posted in the last 60 days. 321 of 1,010 companies have no size evidence; only 24 of the 355 have stage
  evidence. Seasonality covers October and November only.
- **Mail.** The code is good (approval server, careful sender) but has never run: 0 contacts, Gmail never signed in.
  One send per 10-minute run caps out near 32 a day per time zone. Auto-replies would count as replies.
- **Job Radar.** 680 MB SQLite, 101,852 postings over 81 days, 8.4 MB a day of growth, 44% duplicate bodies, auto
  vacuum off. He rarely uses its :8787 page now; it was mostly for applications.

## 4. Decisions made with Devanshu

| Topic | Decision |
|---|---|
| Strategy | Referral first. Applications are logged, not the engine |
| Size | About 200 people or fewer |
| Stage | Founding-engineer hires through Series E |
| Roles | New grad, early career, SWE I, SWE II, founding engineer, MTS, FDE |
| Location | Anywhere in the US |
| Salary | No floor; shown and ranked |
| Volume | About 50 companies a week, 2 conviction bets; as many people per company as he can find |
| LinkedIn | He finds and messages people himself (Premium, up to about 200 requests a week); he prefers people active recently |
| Email | About 200 a week; he supplies names and addresses; drafts need his approval |
| Reply rule | The first reply decides the company; "no sponsorship" or "no referrals" closes it |
| Timeline | A job within about a month; he will give whatever hours are needed |
| Job Radar | Merge and retire |
| Seasonality | All months, updated monthly |
| AI work | Budget about $1 to $2 a month. Free data first, his university Claude Enterprise seat through Claude Code for research, Haiku 5.5 over the API only as a pennies fallback |
| Database | Local, no hosted service (Supabase free projects pause after 7 days of low activity) |
| Deadline | Outreach live this weekend; whole system within about a week (dates in section 7) |
| Email | At least 50 a day, without looking like spam |
| Theses | Voice AI; personal AI assistants (one per person, knows you, friendly, not a therapist); AI and data infrastructure (data growth, storage, cleaning); AI done properly (evals, guardrails) and agent harnesses |

## 5. Architecture

```
CLOUD (Modal, scheduled; public data only)          LAPTOP (source of truth; all personal data)
  hot tier   every 30 min  (~300-600 boards)          data/outreach.db   SQLite, WAL, single writer
  warm tier  every 6 h     (changed / no-ETag)        config.toml        every knob, versioned rules
  cold tier  twice a week  (~32,000+ boards,          sync job (launchd StartCalendarInterval 30 min):
             discovery and promotion)                    pull deltas since cursor -> merge idempotently
  Form D watcher daily, YC pull daily                 enrich + score + plan + seasonality (after sync)
  state: board ETags, hashes, ID sets (Volume)        enrichment ladder (free -> Claude Code -> Haiku)
  output: delta files, 30-day retention               dashboard :3001 (projection of SQLite, fail-closed)
  HTTPS endpoint (secret token) serves deltas  --->   API server :3002 (all writes: tracking, mail actions)
  pings healthchecks.io per run                       sender.py (Gmail, inside safe limits)
                                                      nightly backup -> Backblaze B2; monthly sweep
                                                      healthchecks.io pings for each job
```

The cloud side holds only public job-board data and the list of boards to watch. Everything personal stays on the
laptop. The laptop can be off for days: deltas wait in the cloud (30 days) and merge on the next wake.

### 5.1 Store

One SQLite file, `data/outreach.db` (gitignored), stdlib `sqlite3`, Homebrew Python (SQLite 3.51). Created with
`auto_vacuum=INCREMENTAL` and WAL before any table exists; per-connection pragmas, STRICT tables, compressed bodies
and FTS5 settings exactly as in the lifecycle research's Recommendation. One writer at a time behind a lock (the
pattern in `mail/store.py`); the dashboard and API server read concurrently.

Logical tables (builder owns exact columns, indexes, migrations; a `schema_migration` table tracks versions):

- `board` (ats, token, company_id, tier, etag, ids_hash, last_checked, last_ok, last_changed, consecutive_errors,
  last_error, active). One row per board, updated in place; per-fetch detail is kept 14 days only.
- `posting` (board, native_id, title, location, url, first_seen, last_seen, closed_at, published_at, pay_min,
  pay_max, pay_currency, family, level, in_lane, body_sha). `posting_version` only when content changes.
- `body` (sha, zlib blob) plus an FTS5 index over in-lane bodies.
- `decision` (posting_id, ruleset_version, route, reasons). Disposable cache, rebuilt when rules change.
- `company` (name, domain, aliases, hq, size_estimate, size_evidence, stage, stage_evidence, sponsorship verdict and
  source, locked fields that automation must not overwrite).
- `signal` (company_id, kind, value, observed_at, source_url): form_d, yc, hn_hiring, velocity, seasonality, lca,
  everify, wage_level, news.
- `thesis`, `company_thesis` (score, reason, ruleset_version).
- `person` (company_id, name, email, linkedin_url, role_kind, hooks, profile_text, last_active_note).
- `touch` (person_id, channel, direction, status, subject, body, at). The email state machine moves here with
  identical behaviour.
- `company_status` (pipeline stage, closed_reason, closed_at, notes, next_action).
- `application` (company_id, posting_id, stage, dates).
- `brief` (company_id, depth, body_md, sources, model, cost, created_at).
- `week_plan` (week, company_id, lane, channel_hint, accepted).
- `company_month` rollup and `posting_archive` (35-byte rows) for history after sweep.
- `thesis_month` cache, `job_run` (every job's start, end, status, counts, error), `sweep_run`.

### 5.2 Config and rules

`config.toml` (stdlib `tomllib`), committed: size cap and unsure policy, stage range, role and level patterns,
experience limit, US location rules, tier sizes and intervals, mail caps, windows and ramp, plan size, theses (name,
description, 3 to 5 example sentences), score weights, retention ages, sweep guard thresholds. Rule-bearing sections
hash to `ruleset_version`.

One `rules` module replaces the seven files of regexes, used by the cloud crawler, the laptop pipeline and the
dashboard projection alike (the dashboard stops re-implementing filters in JS). It ships with a regression suite:
labelled fixtures drawn from the real titles, locations and posting texts in today's data and radar.db, including
every misfire the audit found (MTS, SWE II, sponsorship negation, "Bachelor's or 4 years", US cities, Paris TX).
The Radar's label, eval and gate chain is the model for checking that a rule change does not lose good rows.

### 5.3 Crawling and freshness

All crawling moves to **Modal** (scheduled Python functions, about $1 to $2 a month, inside Modal's $30 monthly
free credit). GitHub Actions is ruled out: GitHub's terms bar Actions use "unrelated to the production, testing,
deployment, or publication of the software project associated with the repository", with account suspension as a
possible penalty, and his GitHub is his portfolio. The crawler code may still live in a public repo; it just does not
run there.

- **Tiers.** Hot (every 30 min, about 300 to 600 boards): companies passing size and stage, any board with an in-lane
  posting in the last 60 days, current week-plan and in-pipeline companies, companies whose seasonality says roles
  open within 60 days. Warm (every 6 h): boards changed in the last 30 days and hot-eligible boards on ATSes without
  ETags (Workable, Workday). Cold (twice a week): every known board, discovery of new boards (slug lists from
  `kalil0321/ats-scrapers`, URLs from Simplify's `listings.json`), promotion and demotion.
- **Per request.** `If-None-Match` with the stored ETag where supported (Greenhouse, Lever, Ashby, SmartRecruiters
  answer 304); else hash the sorted posting IDs; on change, diff ID sets into new, closed and edited postings. Per-ATS
  concurrency limits and `Retry-After` backoff.
- **Errors are not emptiness.** A board that errors keeps its last known postings open and increments
  `consecutive_errors`; postings close only on a successful fetch that no longer lists them. Boards with repeated
  errors are reported, then deactivated after a threshold, never treated as "hiring stopped".
- **Output.** Delta files per run (new postings with full text for in-lane only, closed IDs, board health) kept 30
  days on a Modal Volume and served from a token-protected HTTPS endpoint by cursor. The laptop's sync job pulls
  everything after its cursor and merges idempotently (keys are board plus native ID).
- **Board list upload.** After scoring, the laptop pushes the hot and warm board lists to the cloud (tokens and
  company names only).
- **Hooks.** A new role appearing at a company on the week plan or in his pipeline is flagged on the dashboard (and
  optionally as a macOS notification) because it is a fresh reason to message people there.

### 5.4 Enrichment, signals and scoring

**Enrichment, cheapest first.** For each pool company missing evidence:
1. *Free, local.* Pull text we already have or can fetch for free: the company's own job postings (most startup
   postings state the round, investors and team size), the homepage and about page, YC data, Form D, HN posts.
   Rules extract round, amount, investors, team size and HQ with the source sentence.
2. *Free, his seat.* What is still missing goes to headless Claude Code (`claude -p`) logged in to his university
   Claude Enterprise seat, using its built-in web search and fetch, about 10 companies per session to share context,
   returning a fixed JSON shape (headcount with source, last round and date with source, stage, one-line product,
   thesis scores with reasons). Runs while the Mac is idle, in small batches, so it never crowds his own sessions.
3. *Pennies, fallback only.* If the seat is unavailable or rate-limited, Haiku 5.5 over the API reads the step 1
   text (no paid web search) for well under a cent per company. A hard monthly cap in config (default $2) stops it.
Every value stores its source; locked hand values always win.

**Signals** (laptop, after each sync, into `signal`):
- Form D: new filings from the EDGAR daily index or Atom feed (declared User-Agent, under 10 requests a second),
  tech industry groups, not funds, not amendments, fuzzy-joined to companies; quarterly data sets for backfill.
  Absence of a Form D is not evidence of no raise.
- YC: daily yc-oss pull (team size, batch, stage, hiring flag, descriptions; `changes/latest.json` for flips).
- HN Who is Hiring: monthly thread via the Algolia API.
- Hiring velocity: from our own `first_seen`: new engineering postings in 30 days, ratio to the prior three windows,
  and "board went from zero to engineering".
- Seasonality: 12-month calendar per company from our posting history, LCA filings by month (local DOL data), and
  the git history of public new-grad lists for past years. Recomputed on the 1st of each month.
- Wage level: posted pay against OFLC 2026-27 data (SOC 15-1252 by MSA), shown as H-1B lottery weight 1x to 4x.
- Sponsorship: existing LCA, USCIS, E-Verify and hand verdicts; posting wording with correct negation handling; a
  "we don't sponsor" reply writes a hand verdict.

**Score.** Growth first (raise recency, velocity, seasonality due), then thesis fit, then role tier and freshness of
the role to name, sponsorship evidence, wage level, size certainty, plus the existing reach signal (smaller
recruiting apparatus reaches engineers faster). Penalties the current score misses: 2027-only starts, OPT-only,
no-sponsor wording, size estimates above 200. Hard excludes: confidently over the size cap, citizenship or clearance
required, closed by him, sponsorship explicitly refused. Weights in config.

### 5.5 Weekly plan

Every Monday 07:00 (and on demand) the system proposes about 50 companies from the ranked pool: about 2 conviction
candidates (top growth plus strongest thesis fit), about 10 top tier, the rest broad. Each entry: one-line why,
role to name with link, growth evidence, thesis tags, wage level, size and stage with confidence, channel hint
("LinkedIn and email" by default; "email first" for very small founder-led teams; "LinkedIn first" for an alum or
India link). He accepts, swaps or drops; accepted companies get light briefs, the 2 bets get deep briefs. Companies
he does not reach this week roll forward.

### 5.6 Outreach and tracking

- **Email drafting.** He pastes people per company (name, email, LinkedIn text, optional "active recently" note).
  The `cold-email` skill drafts into `person` and `touch` (one draft per person, tailored hook, his strongest
  matching project). A syntax and MX check runs before a draft can be approved.
- **Sending.** `sender.py` keeps every current rule (approval, recipient-local window, weekdays, random gaps, bounce
  pause). Changes: run every 5 minutes with 4 to 9 minute gaps; daily cap ramps 20, 30, 40, then 50 from the fourth
  sending day (follow-ups share the cap), about 250 a week; auto-replies (headers `Auto-Submitted`, `X-Autoreply`,
  out-of-office subjects) do not count as replies; the laptop must be awake during sending hours.
- **Not looking like spam.** Each email is one-to-one and plain text: no tracking pixels, no attachments, no link
  shorteners, at most two links, each draft personalised with a real hook so colleagues never get the same text. At
  most 4 emails to one company per day, spread across the day, so a team does not get the same pitch within an hour.
  Only checked addresses; sending pauses itself above a 3% bounce rate. One follow-up, then stop. Anyone who says
  no is never emailed again. 50 a day is a tenth of Gmail's 500-a-day limit; the real risk is people marking mail as
  spam, which personal, relevant, low-frequency mail avoids.
- **Reply rule.** A real reply from anyone at a company moves every queued touch at that company to paused and puts
  the company at the top of the dashboard. He marks the outcome in one click: referral, interview, no sponsorship,
  no referrals, already hired, not now, other. Facts about the company (no sponsorship) update the company record.
- **LinkedIn.** A quick log of whom he requested (name, profile URL, date, active recently), so the company view shows
  everyone reached on both channels. No caps or note drafting enforced.
- **Applications.** Logged against a posting when he applies after a referral, with stages through offer.
- **Dashboard.** The current UI stays and grows: This week (the plan), Company page (signals, brief, people, touches,
  status, hooks), Outbox (as today), Trends, Health (every job's last success, red when overdue). All writes go
  through :3002 into SQLite; nothing in localStorage. Datasette is optional for raw tables.

### 5.7 Briefs, theses and trends

- **Light brief** (accepted plan companies): what they do, latest raise, why now, who builds what, which of his
  projects fits, outreach angle, sources.
- **Deep brief** (2 conviction bets a week): product, the bet the company is making about the future, competitors,
  growth evidence, team, where he could contribute, a founder-level note angle, sources.
- **Thesis tags** come from enrichment; editing or adding a thesis re-tags only affected companies.
- **Trends** are recomputed from raw history (postings, signals, tags), so a new or edited thesis gets a full past;
  `thesis_month` caches it and a frozen monthly copy is kept for reference.
- **Cost.** Company cards and thesis tags come from the enrichment ladder in 5.4 (free, then his Enterprise seat,
  then Haiku 5.5 for pennies). Deep briefs for the 2 weekly bets run through Claude Code on his seat, interactively
  or headless. Paid API use is capped at $2 a month and every call's cost is recorded.

### 5.8 Evidence bank

`mail/me.md` is thin and drafts can only use what is in it. Before volume email starts, fill it with his strongest
shipped work: OpenCodeIntel (live, 729 commits, 12-tool MCP server), Overhear (voice-call QA with an LLM judge,
macro-F1 0.89 on a labelled set), CallBudget (voice agents), Saar (Chrome Web Store and PyPI), tool-crowding as
in-progress research, plus his past employer and results. Thesis mapping: voice AI (Overhear, CallBudget), agents
and harnesses (OpenCodeIntel, Saar, tool-crowding), evals (Overhear, tool-crowding).

### 5.9 Data lifecycle

Measured basis: a full crawl sees about 729,000 open postings; radar.db grows 8.4 MB a day mostly from per-fetch logs
and duplicate bodies. Projected size under this policy: about 2.4 GB after one year, 3.4 GB after three.

| Data | Rule |
|---|---|
| People, touches, applications, briefs, signals, boards (deactivated, never deleted), rollups, archive, run history | Never deleted |
| Postings he opened, applied to or labelled | Row and body forever |
| In-lane postings | Row forever; body and search entry dropped 90 days after close |
| Out-of-lane postings | Row kept until 90 days after close, then folded into `company_month` (opened, closed, open at month end, median days to close, reposts, by role family and level) plus a 35-byte `posting_archive` row, then deleted |
| Per-board fetch detail | 14 days |

That keeps "how many did this company hire, for what, when" for every company forever.

**Monthly sweep** (first Sunday 03:00): lock, integrity check, `VACUUM INTO` a backup and check it; count
candidates per rule into `sweep_run` (`--dry-run` stops here); abort if any rule would remove over 40% of a table or
over twice last month's count; freeze rollups, write archive rows and verify counts match; delete in guarded
transactions that never touch anything he interacted with; incremental vacuum, checkpoint, optimize; report.

### 5.10 Reliability

- Every job is idempotent and resumable, records itself in `job_run`, and pings its healthchecks.io check (free tier,
  20 checks): cloud crawl (30 min, 90 min grace), laptop sync, nightly backup, monthly sweep, data freshness (pings
  only when the newest delta is under 2 hours old), sender.
- The dashboard shows a Health strip; anything overdue turns red. A failed refresh never publishes; the last good
  projection stays up with a "stale since" label.
- Nightly backup: `VACUUM INTO`, gzip, upload to Backblaze B2 (10 GB free), keep 7 daily and 12 monthly; a monthly
  test restore with integrity check and row counts.
- Secrets (Anthropic key, Gmail token, Modal token, healthchecks URLs, B2 keys) live outside the repo in
  `~/Library/Application Support/outreach/` with 600 permissions, as Gmail's token does today.
- No hardcoded paths or dates: everything resolves from config or the repo root. launchd plists are generated by
  one install script.
- The only laptop-only constraint left: sends happen while the Mac is awake in the sending window. Crawling,
  enrichment batches and backups of cloud state do not depend on it.

### 5.11 Integrity rules

- **Authority.** The laptop database is the only source of truth. Cloud state (ETags, ID sets, board list) is
  disposable and rebuildable. After cloud state is lost or reset, the first run per board is a **baseline**: it
  records what is open without emitting "new" postings (source dates kept where the ATS gives them), so velocity and
  hooks are never faked.
- **Long absence.** Deltas are kept 30 days. If the laptop's cursor is older than that, sync switches to a snapshot
  pull (current open set per board) and records a data gap for that window; postings that opened and closed inside
  the gap are lost and the gap is shown, not hidden.
- **Restores.** The sync cursor lives in the database, so a restored database simply re-pulls deltas; merges are
  idempotent. Before the sender resumes after a restore, it reconciles with Gmail's Sent folder (metadata scope) so
  nothing is sent twice.
- **When a posting closes.** Only when the fetch succeeded, the adapter enumerates the full board (an
  `enumerates_fully` flag per adapter, as radar.db already has; capped adapters such as Workday never close by
  absence), and the response passes sanity checks (a board that suddenly returns zero postings must return zero
  twice in a row). A board that is gone (repeated 404 or redirect to a different ATS) closes its postings with reason
  `board_gone`; those closures are excluded from hiring statistics. A posting reappearing on a company's new board
  with the same normalised title within 30 days is linked as a repost, not counted as new hiring.
- **Cloud keeps bodies loosely, the laptop filters strictly.** The cloud keeps full text for every US
  engineering-family posting (not just ones passing today's rules), so a rule fix can be re-run over history. The
  laptop applies the strict rules. Estimated retained bodies about 1 GB rather than 0.2 GB; acceptable.
- **The scored pool.** Every discovered board gets a cheap `company` row. The scored pool is companies with an
  in-lane posting in the last 60 days, seasonality due within 60 days, or anything in his pipeline. Enrichment runs
  only on pool companies missing evidence, highest-ranked first, through the cost ladder in 5.4. This keeps the hot
  tier near 300 to 600 boards and paid spend inside the $2 cap.
- **Cutover of mail and tracking.** There is only ever one sender. Track A runs on `contacts.json` until the cutover
  window, where the old sender and server are stopped, a one-shot import runs with count checks, and the new ones
  start. No dual writing, no merge conflicts. Crawling and ranking get the side-by-side week; mail does not.
- **Where things live.** The live database is at `~/Library/Application Support/outreach/outreach.db`, outside any
  checkout. The live checkout tracks master; schema migrations run only from the install script, never on open from
  a development worktree. Development uses a copy.
- **Why SQLite and not Postgres.** One user on one machine: SQLite is a file, needs no server, and every job can open
  it without anything else running. Postgres in Docker would need Docker Desktop running for every scheduled job
  (another thing to fail, and memory), with no gain at this scale. Supabase's free plan pauses after 7 days of low
  database activity and would put personal data off the machine. The schema stays plain SQL, so moving to Postgres
  later is a straightforward port if it is ever needed.
- **Backups.** Encrypted on the laptop before upload (`age`), key kept in the macOS Keychain and in his password
  manager; the monthly restore test proves the key works.
- **Alerts.** Cloud checks alert on time. Laptop checks alert on explicit failure pings and only after long grace
  periods (sync and backup: 24 hours on weekdays), so a closed laptop at night or on a weekend is not an alarm, but a
  revoked Gmail token or a failing sync is.
- **Old radar history on out-of-scope companies.** Postings and application events on big-employer and Workday
  boards (154 application events) are imported as frozen, out-of-scope records: kept for history, status unknown,
  never scored or crawled.

## 6. Migration

Read-only on every source, idempotent, with an in/out count report:

1. `dashboard/data.js` companies and hand verdicts (locked fields).
2. localStorage `outreach.v1` (an export button in the current dashboard before cutover).
3. `mail/contacts.json` (empty today, but import whatever exists at cutover).
4. `radar.db`: postings and bodies deduplicated into the new layout, decisions, triage and application events. It
   is the only copy of his triage history; keep the file untouched as an archive afterwards.
5. Old devboard `job-state.json` (36 applied records, 2026-06-24 to 2026-07-24).
6. `contacted.txt` and `DevVault/job-search/outreach/companies/*.md` people where parseable; the rest listed for him.

Then run old and new side by side for one week, compare what each surfaces, and retire `com.devanshu.jobradar` and
the old twice-weekly refresh.

## 7. Plan from A to Z, with dates

Today is Saturday 2026-10-17. Work runs as parallel workstreams, each built by its own agent from the
implementation plan, reviewed and integrated by the lead (me), approved by Devanshu. Dates are targets; anything
that slips moves to the next day without blocking outreach, because outreach runs on the current system first.

| # | Workstream | Delivers | Done by |
|---|---|---|---|
| 0 | Plan | Design approved, implementation plan written | Sat Oct 17, night |
| A1 | Mail on current code | Gmail sign-in, 5-minute runs, ramp to 50, auto-reply filter, pause-on-reply, close reasons, address check | Sun Oct 18 |
| A2 | Filter fixes on current code | Sponsorship negation, MTS, SWE II, US places, "small" default, failed fetch not empty | Sun Oct 18 |
| A3 | Evidence bank | `mail/me.md` filled (he supplies facts) | Sun Oct 18 |
| A4 | Free enrichment pass + first plan | Size, stage, raise for the 355 from free sources and his seat; first 50 companies | Sun Oct 18, night |
| - | **Outreach live** | First emails go out on the ramp | **Mon Oct 19, morning** |
| B1 | Store and rules | SQLite schema and pragmas, config, rules module with regression suite | Tue Oct 20 |
| B2 | Migration and dashboard | Every source imported with counts; dashboard reads SQLite; all writes through :3002 | Wed Oct 21 |
| B3 | Ops | `job_run`, Health strip, encrypted nightly backup to B2, healthchecks | Wed Oct 21 |
| C1 | Cloud crawler | Modal hot, warm, cold tiers, ETags, close rules, delta endpoint, laptop sync | Thu Oct 22 |
| C2 | Signals and score | Form D, YC, HN, velocity, 12-month seasonality, wage level, new score, Monday plan, hooks | Fri Oct 23 |
| C3 | Briefs and trends | Enrichment ladder automated, deep briefs for bets, thesis tags, Trends view | Sat Oct 24 |
| D1 | Cutover | Mail and tracking cutover (Sunday, no sends), old refresh and Radar job retired, sweep dry run | Sun Oct 25 |
| D2 | First live sweep and restore test | Monthly sweep live, backup restore checked | Sun Nov 1 |

**His time each day (my suggestion):** about 1 to 2 hours finding people for the day's companies (LinkedIn activity
check, emails), 30 minutes sending LinkedIn requests, 15 minutes approving drafts, replies as they come. Monday adds
30 minutes to accept the week's plan.

**One-time setup he does (accounts in his name):** Google Cloud OAuth client for Gmail; log Claude Code in to his
university Enterprise seat and confirm Claude Code is enabled for it; Modal account; Backblaze B2 bucket;
healthchecks.io account; the facts for `me.md`.

## 8. Left to the builder

- Exact schema, migrations, indexes, module layout, delta format, endpoint shape.
- Tier sizes, velocity thresholds, size and stage evidence rules, fuzzy-match thresholds, score weights (starting
  values in config, tuned with him).
- Prompt design and output schemas for enrichment and briefs.
- Tests: the rules regression suite and the migration count checks are required; the rest is the builder's call.

## 9. Calls I made (Devanshu: check these)

- SQLite on the laptop as the single source of truth; the cloud holds only public board data.
- Modal instead of GitHub Actions, because of GitHub's Actions terms and the risk to his portfolio account.
- Research runs free first, then on his Enterprise seat, then Haiku 5.5 capped at $2 a month.
- Email ramps 20, 30, 40, then 50 a day from the fourth sending day, follow-ups included, at most 4 per company a
  day, with an address check before approval.
- A reply from anyone at a company pauses all queued email to that company.
- Size unknown means "unsure": shown and marked, never dropped and never defaulted to small.
- Postings asking up to 3 years of experience pass (covers SWE II at most startups).
- A posting that explicitly refuses sponsorship keeps the company out of the plan unless he overrides it.
- Thesis tags come from the enrichment call; trends are recomputed from raw history.
- Retention: in-lane postings forever (bodies 90 days after close), out-of-lane rows rolled up and archived.
- Gmail remains the only automated sender; .edu by hand for alumni.
- The integrity rules in 5.11: laptop is the source of truth, baseline runs after cloud resets, closing only on
  full successful fetches, loose body retention in the cloud, a bounded scored pool, one sender at a time with a
  hard cutover, the database outside any checkout, client-side encrypted backups, quiet laptop alerts.
- A hard paid-API cap of $2 a month.
