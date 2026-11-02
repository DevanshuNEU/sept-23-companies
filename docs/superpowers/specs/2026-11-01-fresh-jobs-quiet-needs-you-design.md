# Fresh jobs, a quiet Needs you, fast pages and smarter sending

**The one rule (Devanshu, 2026-11-01): find the right job, at the right company, at the right time.** Right job:
a tech role at his level. Right company: one he can take on OPT, small ones first, big ones still shown. Right time:
newest first by the real posted date. Anything that does not serve one of the three is out of scope. When a detail
below is unclear, decide it by this rule.

Status: draft for Devanshu's review, 2026-11-01. Nothing here is built. When approved, #9 is edited first (section
10), then each part gets its own issue and plan.

## 1. Why

On 2026-11-01 Devanshu said the app was not helping him get a job:

- **Needs you had 20 open items and none mattered.** 10 were notes left by the import ("row 90 matched no touch"),
  3 were duplicate guesses from the same import, and 7 were "missing email domain" for companies he emailed months
  ago (Microsoft, Slalom, Wolters Kluwer, World Wide Technology). None of them blocks an email or a pick today.
- **Browse companies gave him nothing he could act on.** Of 1,112 companies, only 228 had a role at his level, 127
  showed senior roles, and a 297 day old posting sat at rank 31. Freshness was worth at most 12 points, the same as
  "E-Verify enrolled". Only 8 companies had a fit score. The list was full of non-tech roles (supply chain, thermal,
  optical) and long "why it ranked" paragraphs.
- **Pages felt laggy.** Cold loads measured 1.8 s for Today, 0.5 s for the bench and Browse, 0.4 s for Needs you.
  A cached answer lives 15 s and any commit anywhere (the sender writes every 2 minutes) throws it away, so most
  visits compute from scratch. Needs you sends 633 KB of hidden data gaps on every load to show 20 items.

How he works (his words, 2026-11-01): he opens and judges every posting himself. He does not care about rank or fit
scores. What he looks at: how long the role has been open, the title, the level (new grad, early career, SDE 1;
SDE 2 and up usually wants 2 or more US years), and company size (small is easier to email; big is still worth a
try). He wants tech roles only, the newest first, the last 30 days by default, wider when that runs out. He finds
the people himself and runs LinkedIn himself.

Evidence behind this design (all in the `sydney` workspace's `.context/research/`):

- `own-history-analysis-2026-11-01.md`: his own store. Cold May and June batch: 10 replies from 150 emails (6.7%).
  Managers replied best (3 of 11). All 5 referrals came from warm LinkedIn contacts at big companies. 9 of the 11
  companies that interviewed him were early stage or under 300 people.
- `arkenos-round1-results.md`, `arkenos-round2-results.md`, `arkenos-round3-results.md`: three rounds of outside
  research. The facts this design uses are quoted where they matter. Most cold-email numbers in those files are B2B
  sales data and are used only as direction.

## 2. Goals, non-goals, anti-goals

**Goals**

1. A job list he can work through in one sitting: tech roles at his level, newest first by the real posted date,
   with the facts he checks on each row and two keys, go ahead and pass.
2. Needs you is empty on a day when nothing is waiting on him.
3. Every page answers from a warm cache; no page load waits on a heavy computation after the first one of the day.
4. Sending paces itself without a fixed daily number, backs off on its own when a warning shows up, and tells him
   when his mail may be landing in spam.
5. More small companies hiring at his level, from sources that give real dates.

**Non-goals**

- Ranking postings for him. Sorting is by date; there is no fit score on the job list.
- Anything on LinkedIn. He runs LinkedIn completely. Knock keeps recording what he logs and nothing more.
- Finding people. He finds them himself.
- H-1B. He wants a job on F-1 OPT and later STEM OPT.

**Anti-goals** (would make this a failure even if it works)

- Hiding a role he could take. Only roles he can never take on OPT are hidden.
- Showing an old posting as fresh. A posting whose real date is unknown never appears as "new".
- A Needs you that fills up again with items that block nothing.
- Breaking the drafting harness (#135), the approve-by-his-click rule, or any of the safety rules in `CLAUDE.md`.

## 3. Decisions Devanshu made (2026-11-01)

1. **Visa:** hide only roles he can never take on OPT: "US citizens only", a security clearance, and ITAR or EAR
   "US person" roles. Keep "no sponsorship", "no H-1B" and "must not need sponsorship now or in the future" as a
   flag on the row. E-Verify is a badge, not a hide (it only matters for STEM OPT, and a company can enroll in
   about 15 minutes).
2. **Big companies stay visible** with a tag. Small companies are easier, but he still wants to see big ones.
3. **Tech only.** No supply chain, mechanical, thermal, optical or other non-software engineering.
4. **Freshness matters "100%".** Default window 30 days, then wider when he asks.
5. **Wave size:** up to 4 people per company stays.
6. **LinkedIn:** he handles it completely. How many requests he sends is his call.
7. **Email:** no fixed daily limit. The scheduler should be smart enough to send nicely.
8. **Follow-ups:** 3 (down from 4).

## 4. Constraints

Everything in `CLAUDE.md` holds. The ones this work touches most:

- Claude never approves or sends email and never calls the API's write routes on any port. Tests use the scratch
  settings (`OUTREACH_DB`, `OUTREACH_MAIL_HOME`, `OUTREACH_GMAIL_DIR`, `OUTREACH_SOURCES_DIR`).
- Crawls, source fetches and anything that opens the live store run only in the live checkout. Tests never reach
  the network (saved responses and fakes only).
- Test first: every behavior below starts as a failing test.
- One issue per branch and PR, every PR to `master`, no stacked PRs.
- No em dashes or en dashes in UI copy, docs, commits or PR bodies.
- UI work goes through the `craft` skill and finishes with its gate. The page reuses the app's existing visual
  system (`docs/design/`).
- #9 is edited before code whenever a rule changes (section 10).

## 5. The pipeline comes first

Devanshu, 2026-11-01: "most time I want you to spend on this pipeline itself". The pipeline is everything between
the outside world and the job list: finding companies and boards (sources), reading boards (crawler), deciding what
a posting is (rules), and dating it. Parts C, F and the scorecard below are the core of this design. Parts A, B and E
are small side jobs.

### 5.0 The pipeline scorecard

The pipeline is judged by four numbers, measured every day by a read-only command and shown as one line in the app.
SimplifyJobs' public new grad list is the outside yardstick: it is curated for new grads and dated by discovery.

Baseline, measured read-only on 2026-11-01 (`.context/scratch/coverage.py`, `pipeline1.sql` in the `sydney`
workspace):

| Number | What it means | Today | Target |
|---|---|---|---|
| **Coverage** | Of SimplifyJobs' active US software and AI/ML/data new grad listings posted in the last 30 days, the share whose exact posting is in Knock | 135 of 422 (32%). Knock knows 61% of the companies but has the board for only 36%. Where it has the board it has the posting 88% of the time | 80% of the software listings |
| **Detection time** | Hours from a posting's real posted date to Knock first seeing it, on boards Knock already watches | Greenhouse, Ashby, Lever: 72% the same day, 99.5% within a day. But 248 boards are read only every 1 to 7 days, so a quiet small company's new role can wait up to a week | 95% of new his-level postings on fit boards within 1 hour |
| **Lead over SimplifyJobs** | For listings both have, the share Knock saw first (the SimplifyJobs source records when each listing first appears in its file) | Not measured yet | 90% |
| **Level recall** | Of SimplifyJobs new grad listings Knock has, the share Knock counts as his level | 69 of 135 (51%). Some rejections are right (analyst, data annotation), some are wrong ("Junior AI Engineer" read as no level, "Applied AI New Grad" rejected for having no engineering word, plain "Software Engineer") | 85% of the software listings, with his passes as the check on precision |
| **Date truth** | Share of listed postings whose date is the ATS's own first-posted date or a real watch date | Not measured yet (Part C adds the date source) | 90% |

Where coverage is lost today:

- **No board for the company** (64% of the gap). 100 of the 422 listings are on companies' own careers sites, not a
  known ATS; others are on Workday tenants, Workable, Oracle, iCIMS and Rippling boards Knock does not have.
- **Dark readers.** BambooHR (48 of 48 boards erroring), JazzHR (54 of 56 erroring); iCIMS, Gem, Eightfold and
  join.com boards are never read (no reader); SmartRecruiters is off by its robots.txt.

### 5.1 How the pipeline gets there (round 4 research, 2026-11-01)

Four research passes on 2026-11-01, in the `sydney` workspace's `.context/research/`: `r4-speed-and-discovery.md`,
`r4-repos-and-datasets.md`, `r4-apis-and-feeds.md`, `r4-signals-and-classification.md`. The gap file they tested
against is `coverage_gap_2026-11-01.json` (the 287 SimplifyJobs listings Knock lacked).

**Speed: seeing a job before SimplifyJobs does.**

- Measured from SimplifyJobs' git history: a listing appears a median of about 7 hours after the ATS posts it
  (Greenhouse alone: 2.8 hours), with a slow tail (p90 about 6.5 days), mostly in an evening batch.
- Greenhouse, Ashby, Lever and Teamtailor answer an unchanged board cheaply (checked live); Workday and Oracle can be
  read newest first. One polite pass over Knock's 2,000 boards at today's per-host spacing takes about 21 minutes.
- So: **fit boards are read every 30 minutes**, which means a new posting is seen in 15 minutes on average. A fit
  board: a company that is not no-go and not hidden, and that posted a role at his level in the last 12 months, came
  from a new grad feed below, or has a fresh Form D or YC signal. The strongest predictor found: 66.5% of companies
  posting a new grad role in October had posted one between March and September. Other boards keep today's
  cadence. The 6 hour floor (`floor_s`) is what holds Knock back today.

**Coverage: finding the boards.** 268 of the 287 misses were at companies whose board Knock did not have.

1. **stapply's company lists, used directly.** Knock already downloads them (80,390 companies across 64 ATSs, MIT)
   but only looks a company up after another source names it. They hold 115 of the 168 missed ATS boards (Workday
   51 of 62, Workable 21 of 34). Add their boards for US companies at a slow cadence; a board that shows a role at
   his level becomes a fit board. This is a large first crawl (section 12 asks before it runs).
2. **New grad lists as board feeds.** Take only the board link from each listing and let Knock read and date the
   job itself:
   - SimplifyJobs `listings.json` (no license on the repo, so board names only, never its postings). 72% of the
     misses had appeared there more than a day earlier.
   - ambicuity/New-Grad-Jobs (`jobs.json`, every 30 minutes, MIT code, its terms allow automated access): 8,398
     live roles, about 680 not in Knock.
   - speedyapply 2027 SWE list (daily): about 200 not in Knock.
3. **The ATS behind custom careers sites.** A `gh_jid=` link is a Greenhouse board; an `applytojob.com` link is
   JazzHR; iCIMS sites have a public JSON feed that robots.txt allows. 54 misses.
4. **Readers.** Fix BambooHR (it has a JSON job list) and JazzHR (both broke on page format changes). Port iCIMS and
   SuccessFactors readers, and the stapply and ambicuity Workday approach (one keyword search per new grad term,
   because a big tenant's plain list is ordered by relevance and buries new grad roles), from their MIT code.
5. **Workable discovery** through `jobs.workable.com` searches for US software and data titles, if its terms allow
   (34 misses).
6. **Optional paid check:** Fantastic.jobs (from about $0 to $45 a month) crawls 58 ATSs hourly with an entry level
   filter. One 30 day trial pull is scored against the gap file and Knock's own dates before any money is spent
   past the free tier (section 12 asks).

**Early signals: watching before the job exists.**

- A watch list of every company that ever posted a role at his level; its boards are fit boards.
- yc-oss daily changes (a company turns hiring or grows), Form D first sale dates (Knock measures its own lag from
  raise to first posting), Hacker News job ads (free, exact times; Knock's HN source reads only the monthly thread).
- H-1B LCA filings at wage level I or II become a "has sponsored entry level" fact on the row, not a timing signal.
- The Northeastern career fair employer list, copied by hand twice a year.

**Classification: reading a posting like an expert.**

- **Rule bugs first** (measured on the live store): 294 open postings are tagged senior only because the title says
  "III" while asking 3 years or fewer (mostly Google "Software Engineer III": 2 years, or 1 with an advanced
  degree); "up to 5 years" is read as asking 5; the largest number wins in postings with several levels or degree
  tiers; about 1,073 tech titles a week are dropped for having no engineering word.
- **Then the reader on what rules cannot settle.** 48% of new grad listings have no level word in the title. The
  nightly reader (#32, his Claude Code seat, no API cost) reads only the postings the rules mark uncertain and
  answers two questions: can an MS graduate with no full time experience get this role, and how many years does it
  ask of an MS holder. The closest published study found a language model at 86.9% precision and 97% recall on
  "suitable for a graduate", ahead of a fine tuned model and keyword rules. The nightly cap rises from 400 to 600.
- **Measured, not assumed:** a labeled set of about 300 postings (at least 100 at his level), built from the weekly
  accuracy check (5.2), with ambicuity's visa flags and other public labels as extra test sets.

**Not doing:** JobSpy or anything that scrapes LinkedIn, Indeed or Glassdoor (their terms forbid it; JobSpy offers
proxies to get around blocks); mining emails from GitHub commits (GitHub's terms forbid using them for outreach);
X, Reddit and Discord feeds; copying SimplifyJobs' postings.

**Done when**

- A fit board is due again 30 minutes after its last read; a non-fit board keeps today's cadence.
- Each feed adds boards and companies through the identity rule, never a posting or a posted date, and a second run
  adds nothing.
- A `gh_jid=` link resolves to its Greenhouse board; an `applytojob.com` link to JazzHR.
- "Software Engineer III" asking "2 years, or 1 with an advanced degree" is his level; "up to 5 years" asks 0 to 5
  and counts as 0.
- The scorecard shows Knock's lead over SimplifyJobs (below).

### 5.1b Round 5: the red team (ArkenOS, 2026-11-02), checked against the code

Source: `.context/research/arkenos-round5-results.md`. ArkenOS read the code and the store read-only. Claude checked
its main code claims on 2026-11-02 and they hold: `rules/decide.py` `_level` ends by rejecting a plain title that
names no years; `rules/title.py` treats "III" and "3" as senior before reading the body; `crawl/polite.py` waits
with a blocking sleep in one thread, so a Workday pause stalls every other host; the reader queue holds 62,956
postings against 1,868 read. **Where this section and 5.1 differ, this section wins.**

**Changes it brings, each accepted unless marked otherwise:**

1. **Two yardsticks, small companies first.** SimplifyJobs leans to big companies (about half its listings are on
   Workday) and lists 1.4% of active YC companies under 50 people, while 9 of his 11 interview companies were under
   300. The scorecard gets a second coverage number for small companies, built from VC portfolio lists (Consider,
   Getro), yc-oss and HN, and that one is the headline. SimplifyJobs coverage stays as the second number.
2. **Rules give three answers: fit, reject, or uncertain.** A plain title with no years is uncertain, never rejected.
   "III" or "3" in a title is ambiguous: fit only when the body asks 2 years or fewer (or 2 for a master's) or has
   entry wording. Years are read per degree tier (the master's figure counts) and a range counts as its low end.
   Title nouns widen to AI, ML, data, applied scientist, quant, platform, SRE and similar (subject to his answer on
   data and ML titles). ArkenOS measured on 33 real postings: today's rules 94% precision and 54% recall; rule fixes
   alone 79% and 88% (they let senior roles described only in prose through); rules plus a model on the uncertain
   ones 96% and 92%. 33 postings is a small sample, so these are leads until the labeled set exists.
3. **The model reads uncertain postings within the tick, not at night.** Fresh uncertain postings at fit companies
   (open, US, tech, posted in the last 30 days) are read in small batches through his Claude Code seat during the
   crawl, so an alert never waits for the night. The reader queue is cut to that slice; the 62,956 backlog is not
   worked. A published study's 86.9% precision came from a set that was about half graduate roles; on raw boards
   (4 to 8% at his level) a model alone would drop to roughly 28% precision, which is why the rules run first.
4. **A crawler that does not wait on itself.** One worker per host bucket, each keeping today's spacing, so hosts
   are read in parallel and each host sees exactly the same load. Greenhouse list polls drop `content=true` (much
   smaller) and fetch a body only for a new job id.
5. **Tiers and time of day.** Fit boards every 15 minutes in US business hours (08:00 to 19:00 New York, weekdays)
   and every 90 minutes otherwise; active tech boards every 2 hours (8 at night); quiet boards daily; dead boards
   weekly. ArkenOS measured 87% of postings published 07:00 to 20:00 Eastern on weekdays. A board that opens 3 or more
   engineering roles in 14 days, or that ever posted an internship or a role at his level, is a fit board (ArkenOS
   measured 3.4 times and 4.8 times the usual chance of an early career posting following).
6. **Workday done right.** A fast poll reads only the first page with an empty search (ArkenOS reports it is newest
   first; a test on saved responses confirms before relying on it) and stops at the first job already seen. A board
   over 2,000 rows splits by family and then by a second facet instead of failing. `myworkdaysite.com` URLs are read.
   Requisition numbers spot old jobs with new dates. 16 of the 19 misses on boards Knock already watches were Workday.
7. **stapply boards imported with a gate.** Each new board is read once; it enters the crawl only if it has at least
   one open US engineering role (as a tier 2 board), else it waits as quiet or dead. This keeps 80,000 unchecked
   boards and their non-tech postings out of the store.
8. **Identity from the board itself.** The company's website domain is taken from the ATS board (Workable, Lever,
   Greenhouse and others publish it) and from the employer contact email in DOL LCA rows (ignoring law firms), so
   names like "Loop" or "Front" stop colliding and LCA companies get domains.
9. **More discovery, terms checked first:** Workable's cross-company search (`jobs.workable.com`), the Wayback
   Machine's URL index for board slugs, VC portfolio feeds, and the sitemaps iCIMS, Teamtailor and Recruitee publish.
   Passive DNS services (RapidDNS, AlienVault OTX, urlscan, CertSpotter) are used only where their terms allow it.
10. **Application form visa questions.** Greenhouse (`questions=true`), Ashby (application form) and Rippling
    (knockout questions) carry "will you need sponsorship now or in the future" as a form question. It becomes a flag
    on the row, like the other future sponsorship wording; it does not hide.
11. **Big company portals:** TikTok and ByteDance, Amazon, Tesla and Microsoft readers ported from stapply's MIT code
    (34 of the 100 custom site misses), and `gh_jid=`, `ashby_jid=` and `applytojob.com` detection (21 more).

**Dropped or not taken:**

- ambicuity/New-Grad-Jobs: refreshes every 6 hours, not 30 minutes, and draws from SimplifyJobs and Jobright; it added
  4 boards beyond stapply and SimplifyJobs on the gap file. Dropped.
- Fantastic.jobs: its date can be the ATS update time or its own crawl time, and direct reads do better for free.
  Not trialed unless the scorecard later shows a gap only it fills.
- GitHub commit emails to find managers: GitHub's terms forbid using them for outreach, and he finds people himself.
  Not taken.
- One person per company first, and 2 follow-ups: his own numbers lean that way (18% replies emailing one person vs 7
  to 8% for more), but he chose up to 4 people and 3 follow-ups. His decisions stand; the scorecard's outcome split
  will keep showing this.
- Bluesky search for founder hiring posts: allowed by its public API, but low priority; parked.

**Offered, his call:** reading his own LinkedIn data export (a file he downloads; Knock never touches LinkedIn) so a
job row can say "you know 2 people here". All 5 of his referrals came from people he already knew on LinkedIn.

### 5.2 How we know it stays the best

There is no guarantee that Knock finds every job; some are never posted, some live only on LinkedIn, some sites
block readers. What this design guarantees instead: no silent failure, numbers instead of opinions, and every miss
turned into a fix.

1. **Two outside yardsticks, not one.** SimplifyJobs, plus a second source the pipeline does not ingest for postings
   (my call: Hacker News "Who is hiring" new grad posts, and a weekly sample of VC board jobs checked against Knock),
   so Knock cannot look good just by copying SimplifyJobs.
2. **A miss log.** Any job he finds somewhere else (LinkedIn, a friend, a newsletter) that Knock did not show, he
   pastes in one place (an MCP tool and a field in the app). Each miss records why Knock missed it: no company, no
   board, board not read yet, read but judged wrong level, wrong tech call, hidden by visa, or wrong date. Each cause
   that repeats becomes an issue, and each fix starts with a test built from that miss.
3. **Alarms.** A reader whose boards error on most reads for 2 days, a scorecard number that drops by a quarter
   week over week, or a source that fails twice in a row raises one Needs you item. (Today every BambooHR read has
   been failing and nothing said so.)
4. **A weekly accuracy check.** 20 postings from the job list, chosen at random, that he marks right or wrong on
   level and tech in under 5 minutes. Together with his passes this measures precision; the yardsticks measure
   recall.
5. **The final test is outcomes.** The monthly findings (`outreach/insights`) split replies and interviews by where a
   job came from and how fresh it was when he reached out, so the pipeline is judged by what gets him interviews.

**Done when:** the miss log accepts a pasted URL and stores its cause; the scorecard shows both yardsticks and the lead over SimplifyJobs; a seeded
reader failure raises one alarm and a second run does not raise a second.

## 6. The parts

Six parts, each its own issue and PR. Section 11 gives the order.

### Part A: Fast pages

**What changes**

1. **Serve the last answer at once, refresh behind it.** A heavy view (`views.serve`) answers from its stored bytes
   whenever it has any for the same path, query, config and New York day, and starts a background recompute when
   the store version has moved. The page shows "updated N seconds ago" as it does now. Two exceptions keep the
   page honest:
   - After one of his own writes through the API (approve, skip, swap, pass, go ahead), the next read of the views
     that write touches waits for a fresh answer, so he never sees his click undone.
   - An answer older than a ceiling (my call: 5 minutes) is recomputed before it is served.
2. **Warm the cache after jobs.** When the sender, crawler, plan, sources or reader job finishes a run that changed
   something, the API recomputes the heavy views in the background so the next page load is a hit. How the API
   learns of it (polling the store version, a touch file, a local signal) is the builder's call.
3. **Send less.** Needs you's data gaps move to their own paged read, loaded only when he opens that section. The
   No-go list pages. The job list returns only the fields a row shows. The search index is cached by the browser
   with a version tag.
4. **Find out why Today takes 1.8 s cold** and fix the hot spots (profile first; no guessing).

**Done when**

- After the first load of the day, every heavy page answers in under 150 ms on his laptop, measured from the
  browser, including right after a sender run.
- A cold compute of Today takes under 600 ms.
- Needs you's first response is under 100 KB with 565 data gaps in the store.
- After he approves an email, the Outbox he sees next already shows it approved.

### Part B: A quiet Needs you

**Rule:** Needs you shows an item only when it holds up something he is about to do: an email in the Outbox
(drafted or approved), a pick on today's list, or a reply thread. Everything else lives in Data gaps.

The company scope stays as `review/scope.attention` defines it today (in play, being emailed, held by a reply, and
items he raised himself on Today). This part narrows by item kind inside that scope; it does not replace the scope.

**What changes**

1. **Import leftovers never ask.** Items raised by the import (`raised_by` contains `import`) go to Data gaps,
   whatever company they belong to. That moves the 10 open import notes today.
2. **Duplicate guesses never ask** unless both companies are in play (on today's list or in the Outbox). The 3
   open ones move to Data gaps.
3. **Missing domain fills itself first.** Before raising `domain_missing`, Knock takes the domain from, in order:
   the domain of an email address of a person on record at the company (ignoring free mail like gmail.com), the
   company's website fact, then a careers page host that is not an ATS. It asks him only when none exists and an
   email to that company is waiting.
4. **Existing items are not deleted.** Items that no longer qualify move to Data gaps with a short note (the
   pattern `scope.clear_out_of_scope` already uses).

**Done when**

- With the store as it is today, Needs you shows 0 items (all 20 move to Data gaps or are filled in), and Data gaps
  shows them.
- An Outbox draft to a company with no domain and no source for one raises exactly one Needs you item.
- A new duplicate guess between two companies that are not in play never reaches Needs you.

### Part C: Postings Knock can trust

This part fixes the data under both the job list and Today. It changes no page by itself.

**C1. The real posted date.** The research (round 1, question 1) checked each ATS against live data:

| ATS | Use | Never use |
|---|---|---|
| Greenhouse | `first_published` | `updated_at` (bulk edits reset it on hundreds of jobs at once) |
| Ashby | `publishedAt` | `updatedAt` |
| Lever | `createdAt` | |
| Workday | "Posted N days ago" while N is under 30; "30+ days ago" means at least 30 days; the requisition number to spot an old job with a new date | `startDate` |
| Rippling | `createdOn` from the job detail read (one read per new job) | |
| Workable | `created_at` from the v1 widget | `published_on` (resets on republish) |
| SmartRecruiters | `releasedDate`, marked "may reset" | |

Rules:

- Each posting stores where its date came from: the ATS's own first-posted field, a lower bound ("at least 30
  days"), Knock's own watch, or unknown.
- **Knock's own watch counts only when it is real.** If a board was already being read before the posting first
  appeared, the day Knock first saw the posting is a good posted date. If the posting was there on the board's very
  first read, its date is unknown. Today's fallback (any `first_seen_at` counts) is what makes old Workday jobs look
  new: 562 of 1,112 companies show "seen in the last 7 days".
- A posting with an unknown date never counts as fresh, never earns a freshness bonus in ranking, and shows "date
  unknown".
- When two dates disagree, the older one wins. A posting new to a board Knock has watched that the ATS itself calls
  "30+ days ago" is at least 30 days old and is flagged "reposted" (C5).
- A lower bound ("at least 30 days") is left out of the 7 and 30 day windows. In the 60, 90 and any windows it sits
  after the dated postings, labeled "posted 30+ days ago".
- Existing postings get their date source filled on their board's next read; no mass re-crawl.

**C2. Level.** Today's years parser takes the biggest number and counts "3+ years of Python" as work experience.

- Count a years figure only when it is about professional, industry, full time, work or post-graduation experience,
  or plain "N years of experience" with no technology named. "3+ years of experience with Python" does not count.
- A range counts as its low end ("2 to 4 years" is 2).
- Degree alternatives use the master's branch: "BS and 2 years, or MS and 0 years" is 0 for him.
- Title tiers stay as they are: new grad words, early career words, I or 1 is SDE 1, II or 2 is SDE 2, senior words
  are senior.

**C3. Tech roles only.**

- Add the missing non-tech words to the title filter: supply chain, procurement, logistics, sourcing, buyer, planner,
  thermal, optical, photonics, laser, process engineer, civil, structural, HVAC, chemical, materials, packaging and
  the rest of round 1's list (question 2, "Hard Reject Title Regex"), keeping site reliability, SDET and test
  automation.
- For titles that can be either (Systems, Test, Design, Product, Application, Project, Research Engineer, Member of
  Technical Staff), read the body: count software words (Python, TypeScript, React, Kubernetes, SQL, PyTorch and so
  on) against physical engineering words (SolidWorks, CAD, FEA, PCB, thermodynamics, BOM and so on), as round 1
  describes. Software wins when it leads by 2 or more.

**C4. Visa.** Following decision 1:

- Hard hide, the posting only (as `rules.decide.passes` does for citizenship today): US citizenship required,
  security clearance (active or "able to obtain"), ITAR or EAR "US person". One such posting never hides the
  company's other postings.
- Flag only (shown on the row, never hides, never writes a no-go row): "no sponsorship", "no H-1B", "must not need
  sponsorship now or in the future", unclear wording.
- "Must be currently authorized to work in the US" is neither: he is authorized on OPT.
- The company-level sponsorship hide (`ranking/hide.py`, the refusal rule) is removed everywhere, Today included:
  such a company is ranked and shown with its flag.
- Ranking stops writing `no_sponsorship` no-go rows. Rows it already wrote stay (a no-go is his to lift); the No-go
  page marks them "written by the ranker" so he can lift them in one place.
- Every other hide stays as it is (graduation window, current-student only, non-US, and the rest of `passes`).

**C5. Reposts.**

- The same company and the same normalized title coming back within 120 days of closing is flagged "reposted" and
  keeps the first posting's first-seen date.
- A Workday posting whose requisition number is far below the board's newest is flagged "old requisition".

**Done when** (all on saved responses and scratch stores)

- A Greenhouse job with an old `first_published` and a new `updated_at` is old.
- A Workday "30+ days ago" job on a board's first read is "date unknown", never fresh; the same title appearing on a
  board Knock has watched for a week is dated by the watch.
- "3+ years of experience with Python" asks 0 years; "BS and 2 years or MS and 0" asks 0; "2 to 4 years of
  professional experience" asks 2.
- "Associate Supply Chain Engineer", "Thermal Systems Engineer" and "Optical Engineer Co-Op" are not tech; "Systems
  Engineer" with a Python and Kubernetes body is.
- A posting that says "no H-1B sponsorship" is listed with a flag and its company is not hidden; one that requires
  a clearance is hidden.

### Part D: The job list

The Browse page becomes a list of postings he checks one by one. It is built from open postings directly, not from
the ranked list, so the ranker's size gate never hides a job here.

**What is listed**

- Open US postings that pass C3 (tech) and C4 (no hard visa hide), at companies that are not no-go.
- Never listed: senior roles, postings he passed on, postings at companies he is in touch with right now (waiting or
  engaged), and the rows the hard visa rule hides.

**Two level views**

- **Your level (default):** new grad, early career and SDE 1 titles; any title asking 2 years or fewer after C2;
  and, at companies of 300 people or fewer or of unknown size, plain titles ("Software Engineer", "Member of
  Technical Staff") that state no years, tagged "no years stated". Round 2 found that at YC companies under 50
  people, only 3% of engineering postings say "new grad" and 52% are plain titles.
- **Higher levels:** everything above his level that is not a senior title: SDE 2, any title asking 3 or more
  years, and plain titles with no years stated at companies over 300. One click away, never mixed into the default.
  The `open` tier (Member of Technical Staff, Forward Deployed Engineer) is treated as a plain title.
- Internships and co-ops are not listed: they are not the right job for him now. SimplifyJobs is read from the
  New-Grad repo only.
- Every listed role lands in exactly one view; a test checks that no tech, US, non-senior, non-intern posting falls
  in neither.

**Order and window**

- Newest first by the real posted date. Within the same day, smaller companies first.
- Window chips: 7 days, 30 days (default), 60, 90, any. When the 30 day list has fewer than 20 postings he has not
  checked, the page says so and offers 60 days.
- Postings with an unknown date sit in their own collapsed group at the bottom, "Date unknown (N)", never mixed in.
- A count line at the top: "23 at your level in the last 30 days, 9 checked, 14 left".

**One row**

Title with a level tag; company; days since posted (or "date unknown"); size with a "big company" tag over 300;
location or remote; the visa line (flags from C4, plus "E-Verify enrolled" when known); and small flags when they
apply: "reposted", "old requisition", "3 new tech roles in 30 days", "H-1B entry level filings up this year", "raised
$X on DATE", YC batch, and "you contacted this company" with the date. One link opens the posting. No fit score and
no "why it ranked" paragraph.

A company with several postings in view shows the newest one, with "2 more at this company" that opens in place.

**Filters** (chips with counts): role type from the title (backend, full stack, frontend, AI and ML, data,
infrastructure and platform, forward deployed, mobile, security), size (300 or fewer, over 300, unknown), remote,
not contacted yet, and a company name search.

**Actions**

- **Pass:** the posting never shows again. A new posting at the same company still can. It is a posting pass only:
  it never counts as a Not now for the company and puts nothing on Today's cooldown (`plan/fresh.load_passes` must
  not read it). It joins the set `load_passed_postings` reads, so the plan never offers that role either.
- **Go ahead:** the company joins today's list as his own pick, ready for him to find people. It always works
  except for a real duplicate: the company is no-go, already on today's list, or someone there is already contacted
  and waiting (the #131 intake rule). The size gate, the 7 day room and a Not now cooldown never stop it. Before the
  08:00 plan is built, the pick is stored and the plan keeps it. This is a new write next to the bench add, not
  `picks.add_from_bench`.
- Keys: next and previous row, open the posting, go ahead, pass. The exact keys follow `docs/design/` section 16.6
  and the craft gate.

**New job alerts.** Seeing a job 15 minutes after it posts only helps if he hears about it. When a new posting
lands in the default view (his level, tech, not hidden, real date today), Knock shows a macOS notification within
minutes: company, title, size, "posted N minutes ago", and a click opens the row. Quiet hours (my call: 10 pm to 8 am
New York) and a cap of one grouped notification per 15 minutes keep it calm. On by default.

**Just funded** (a second tab): companies with a recent raise, with or without an open role. A tech Form D (round 1:
industry groups Other Technology, Computers, Telecommunications; pooled funds and 3C exemptions excluded) whose first
sale was in the last 90 days, or a YC company that turned hiring or grew in the last 30 days (Part F). Newest first.
Each row: amount, stage, size, open tech roles at Knock, and the same go ahead and pass (pass hides the company from
this tab).

**Done when**

- With today's store, the default view shows only tech roles at his level whose real posted date is within 30 days,
  newest first; no senior role; no supply chain, thermal or optical role; the 297 day old posting is not there.
- A posting with an unknown date appears only in "Date unknown".
- A big company's new grad role is listed with the "big company" tag.
- Pass hides that posting at once and after a reload; go ahead puts the company on today's list.
- The page loads 50 rows in one response under 80 KB.

### Part E: Smarter sending

**E1. Pacing without a daily number.** Following decision 7, the ramp (20 a day, +10 per clean day, up to 100)
retires.

- The pace comes from what already exists: the window (08:30 to 16:30 recipient local time, weekdays), the random
  gap between any two sends (4 to 9 minutes) and the 60 minute gap per company. Approved email goes out as fast as
  those allow.
- Warnings slow it down on their own instead of capping it: a recipient server rejecting mail as spam ("policy"
  bounce), a bounce rate of 2% or more over 3 days, or a "stop" reply widens the random gap (my call: doubles it)
  for the next 2 sending days, then it eases back.
- Hard stops stay exactly as they are: a Gmail limit or block pauses sending, and so do two "stop" replies in 7 days.
- Gmail's own consumer limit (about 500 a day) is a ceiling no setting can pass; the sender stops well before it (my
  call: 400).

**E2. Three follow-ups,** at 3, 7 and 14 business days after the first email (my call: the 21 day one goes). The
last is the polite close. A thread with no reply closes as `no_reply` 7 quiet business days after the last one, as
today. Threads already planned under the 4 follow-up schedule: a 4th follow-up he already approved still sends (his
click stands); a planned or drafted one is skipped with a note. The `message.seq` check stays 0 to 4.

**E3. Knowing when mail may be landing in spam.**

- The Outbox's sending strip shows policy bounces ("rejected as unsolicited") by company domain for the last 14 days.
- A reply rate watch: of first emails sent 7 to 28 days ago, when at least 40 were sent and the reply rate is under
  half of his baseline, Knock shows one plain warning that mail may be going to spam, with what to check. The
  baseline is his reply rate on first emails sent 29 to 180 days ago when there are at least 40 of them, else a
  configured 6.7% (his cold May and June batch).

**E4. Fewer bounces.** Round 3 found 7 to 22% of Apollo addresses are bad, and a batch of bounces can lock his Gmail
for 24 hours.

- The Outbox marks an address "check first" when it has no source page: an address he supplied (usually Apollo,
  per the #131 intake ruling) or a guessed pattern (`person_source.email_basis` = pattern). A published or verified
  address is not marked. The tip is one line: paste it into Gmail's To field; a photo or name means the mailbox is
  real. No new intake field is needed.
- After a hard bounce at a domain, other unsent addresses at that domain with the same pattern are marked "check
  first" too.
- The existing mail server check (`mx_check`) stays a block.

**Done when** (on `FakeGmail` and scratch settings)

- 150 approved emails across several time zones go out inside each recipient's window with the random gap, with no
  daily cap reached, and never more than 400 in a New York day.
- A policy bounce doubles the gap for the next 2 sending days, then it returns to normal.
- A new first email plans 3 follow-ups at 3, 7 and 14 business days.
- The reply rate warning shows on a seeded store whose recent reply rate halves, and not on one with fewer than 40
  sends.

### Part F: More small companies, with real dates

Each source follows `outreach/sources/` (fetch, discover, resolve, find boards) and the identity rule (one company
per domain). Dates come from the ATS read in Part C, never from the source. Feeds give boards and companies,
never postings (5.1).

1. **SimplifyJobs.** Read `.github/scripts/listings.json` from the public GitHub repo (not `README.md`, which hides
   81% of listings). Poll with an ETag so an unchanged file costs nothing. Use only listings whose category is
   Software or AI/ML/Data and whose location is in the US. Ignore its `sponsorship` field (99.7% say "Other"). Use
   each listing's ATS URL to find the company's board. Round 2 confirmed reading the raw file for personal use is
   allowed under GitHub's terms.
2. **YC company list** (`yc-oss.github.io/api`): the daily change file. A company that turns hiring or grows its
   team feeds Just funded and gets its board found.
3. **VC portfolio boards** (Consider: Sequoia, Kleiner Perkins, First Round and others; Getro: General Catalyst,
   Techstars and others): weekly, companies only, for their domain, size, stage and ATS link. Their job dates are not
   trusted (round 3: Consider copies Greenhouse's `updated_at`, Getro uses its own crawl time).
4. **Form D:** use the first sale date (`dateOfFirstSale`) instead of the filing date, and keep the amount.

**Done when**

- On saved files, each source adds companies and boards through the existing identity rule, a second run adds
  nothing, and none of them ever sets a posted date.
- Just funded lists a seeded company with a Form D first sale 20 days ago and none from 120 days ago.

## 7. What Knock does not change

- Today's company queue, the wave of up to 4, the 14 day rule and the plan job. Today still prefers small
  companies; the size gate keeps hiding companies over 300 from Today's picks (but never from the job list). The
  one change Today sees comes from C4: a company that says "no sponsorship" is no longer hidden from it.
- The drafting harness, the cold-email skill and every approve check.
- LinkedIn: the week counter stays as a count he can look at and never blocks him. Nothing new is built for LinkedIn.

## 8. Data and schema (for the builder)

Likely needs, each the builder's call on shape:

- Per posting: the date's source (section C1), a reposted flag and the original first-seen date, an old requisition
  flag, Workday's requisition number, and the visa flags from C4 if they are not already readable from the stored
  decision.
- Per touch address: where the address came from (E4).
- Sending: the ramp's columns and the `send_day` ceiling `CHECK` change with E1; one migration.
- The job list may want an index on open postings by posted date.

## 9. Success, in his terms

- He opens the job list and every row on the first screen is a tech role at his level posted this month.
- Needs you is empty most days.
- No page makes him wait.
- He never wonders whether a posting date is real: it is a date, a lower bound, or "date unknown".
- When his mail starts landing in spam, Knock tells him before a week of sends is wasted.

## 10. Edits to #9 before any code

1. **Hidden:** "a posting that says we don't sponsor" leaves the hidden list; it, "no H-1B" and future sponsorship
   wording become flags. ITAR or EAR "US person" joins citizenship and clearance, each hiding the posting only.
   "Size over 300 with evidence" hides a company from Today's picks only, never from the job list. Every other hide
   is unchanged.
2. **Level:** at companies of 300 people or fewer or of unknown size, a plain title that states no years, or asks 2
   years or fewer, counts as his level (shown with a tag).
3. **Adaptive ramp:** replaced by E1 (pace from the window and gaps, warnings widen the gap, Gmail's limit and the
   existing hard stops unchanged, a 400 a day ceiling).
4. **Follow-ups:** 3, at 3, 7 and 14 business days (replaces the 2026-10-31 ruling's 4).
5. **LinkedIn:** he runs LinkedIn completely; Knock's LinkedIn limits are counts that never block.
6. **Needs you:** only items that hold up an Outbox email, a pick today or a reply thread; import leftovers and
   duplicate guesses live in Data gaps.

## 11. Order

| Order | Work | Depends on | Why this order |
|---|---|---|---|
| 1 | The scorecard with both yardsticks, the miss log and alarms (5.0, 5.1b item 1, 5.2) | nothing | Every later change is judged by them |
| 1b | The crawler that does not wait on itself, and tiers (5.1b items 4 and 5) | nothing | Every coverage gain needs read capacity |
| 2 | C, postings Knock can trust, with the level recall fixes (5.1 item 4) | #9 edits 1 and 2 | Right job and right time rest on true levels and dates |
| 3 | F and 5.1 items 1 to 3: SimplifyJobs, dark readers, faster reads of fit boards, YC, VC boards, Form D date | C (dates) | Coverage is the biggest loss today (32%) |
| 4 | D, the job list | C | Where he uses all of it |
| side | A, fast pages; B, quiet Needs you; E, smarter sending | E needs #9 edits 3 and 4 | Small; built beside the pipeline work, never ahead of it |

Pipeline work gets most of the time. Each row is one or more issues, each one branch from `origin/master` and one
PR; F and 5.1 split into one PR per source or reader.

## 12. Calls I made (please check)

1. Go ahead always puts the company on today's list as his own pick, stopped only by a real duplicate (D).
   Internships and co-ops are left out of the job list (D).
2. An answer older than 5 minutes is recomputed before it is served (A1).
3. A warning doubles the send gap for 2 sending days (E1); the daily ceiling is 400 (E1).
4. The follow-ups are at 3, 7 and 14 business days (E2).
5. The reply rate watch: at least 40 first emails, sent 7 to 28 days ago, under half of the prior 60 day rate (E3).
6. Within the same posted day, smaller companies first (D).
7. The 30 day view offers 60 days when fewer than 20 unchecked postings are left (D).
8. A company with several postings in view shows the newest with "N more" (D).
9. Plain titles with no years stated count as his level only at companies of 300 or fewer or of unknown size (D).
10. Today keeps hiding companies over 300 from its picks; only the job list shows them (section 7).
11. No-go rows the ranker already wrote for "no sponsorship" stay until he lifts them (C4).
12. Reposts: same company and normalized title within 120 days (C5).
13. When two dates disagree the older wins; "30+ days" postings stay out of the 7 and 30 day windows (C1).
14. Today stops hiding companies that say "no sponsorship", like the job list (C4, section 7).
15. A 4th follow-up he already approved still sends; planned or drafted ones are skipped (E2).
16. The reply rate baseline is his own 29 to 180 day rate, else 6.7% (E3).
17. "Check first" marks every address without a source page; no new intake field (E4).
18. Plain titles with no years at companies over 300, and any title asking 3 or more years, go to Higher levels (D).
19. Coverage target 80% of SimplifyJobs software listings; detection target 95% within 12 hours; level recall
    target 85% (5.0).
21. Feeds (SimplifyJobs, ambicuity, speedyapply) give board links only; Knock reads and dates every job itself (5.1).
23. Fit boards are read every 30 minutes; a fit board is one whose company posted a role at his level in the last
    12 months, came from a new grad feed, or has a fresh Form D or YC signal (5.1).
22. The second yardstick is HN new grad posts plus a weekly VC board sample; alarms fire at 2 days of reader errors or a
    25% week-over-week drop; the accuracy check is 20 postings a week (5.2).

**Open, his call:**

1. The first crawl of stapply's boards for US companies (a large run on his laptop, spread over days at polite
   spacing).
2. Reading his LinkedIn data export to show people he already knows at a company (5.1b, offered).
3. Which data and ML titles count as the right job.
   Claude's recommendation: count ML Engineer, AI Engineer, Applied AI, Data Engineer and ML or AI Research
   Engineer titles; count Data Scientist and AI or ML Researcher only when the posting asks for software or ML
   engineering work and no PhD (the reader decides); leave out Data Analyst, BI and annotation roles. Today "scientist" is off lane, so Data
Scientist, ML Research Scientist and "AI/ML Researcher" never show, and titles with no engineering word ("Applied AI
New Grad", "Programmer Analyst", "Data Analyst") are dropped.

## 13. Left to the builder

- How the API learns a job finished and how background recomputes are scheduled (A2).
- Profiling and fixing Today's compute (A4).
- The exact word lists and regexes for C2 and C3, as long as the "Done when" cases pass.
- Schema shapes and migrations (section 8).
- The job list's layout, within `docs/design/` and the craft gate.
- How each new source module is laid out inside `outreach/sources/`.
